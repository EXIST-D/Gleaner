import os
import time
from datetime import timedelta

from gleaner.auto import (
    LOCK_NAME,
    STALE_LOCK_SECONDS,
    chosen_platforms,
    ensure_not_running,
    last_auto,
    run_auto,
    run_lock,
)
from gleaner.errors import GleanerError
from gleaner.sources.base import FetchResult
from gleaner.store import Store
from tests.support import (
    BILI_RECORDING,
    BILI_RECORDING_BVID,
    BILI_VIDEO,
    DOUYIN_VIDEO,
    NOW,
    WorkspaceTestCase,
    fake_extract,
    make_config,
    write_export,
    write_records,
    write_video,
)


class FakeDriver:
    """Writes `write` into the run's raw directory, like a crawler that found something."""

    def __init__(self, write=None, status="ok"):
        self.write = write
        self.status = status
        self.calls = []

    def problems(self):
        return []

    def run(self, *, mode, output_dir, log_dir, **kwargs):
        self.calls.append({"mode": mode, **kwargs})
        if self.write and self.status == "ok":
            self.write(output_dir)
        return FetchResult(self.status, output_dir, "" if self.status == "ok" else "登录状态已过期")


class Clock:
    """A clock that only moves when the run sleeps."""

    def __init__(self):
        self.value = NOW
        self.slept = []

    def now(self):
        return self.value

    def sleep(self, seconds):
        self.slept.append(seconds)
        self.value += timedelta(seconds=seconds)


def bili_video_with_audio(raw):
    write_records(raw, "bili", [BILI_RECORDING], mode="detail")
    write_video(raw, "bili", BILI_RECORDING_BVID)


class AutoTest(WorkspaceTestCase):
    def setUp(self):
        super().setUp()
        self.config = make_config(self.workspace)
        self.clock = Clock()
        self.drivers = {}
        self.options = []

    def factory(self, config, platform, **options):
        self.options.append((platform, options))
        return self.drivers[platform].pop(0)

    def drive(self, store, drivers, **kwargs):
        self.drivers = {platform: list(items) for platform, items in drivers.items()}
        return run_auto(
            store,
            self.config,
            clock=self.clock.now,
            sleep=self.clock.sleep,
            fetcher_factory=self.factory,
            extract=fake_extract,
            **kwargs,
        )

    def test_default_platforms_start_with_nowcoder(self):
        self.assertEqual(chosen_platforms(self.config), ["nowcoder", "xhs", "dy", "bili", "zhihu"])
        configured = make_config(self.workspace, auto={"platforms": ["zhihu", "xhs", "zhihu"]})
        self.assertEqual(chosen_platforms(configured), ["zhihu", "xhs"])
        self.assertEqual(chosen_platforms(configured, ["dy"]), ["dy"])
        with self.assertRaisesRegex(GleanerError, "不支持抓取 weibo"):
            chosen_platforms(self.config, ["weibo"])

    def test_crawls_each_platform_then_packs_what_is_ready(self):
        with Store.open(self.workspace, create=True) as store:
            outcome = self.drive(
                store,
                {
                    "xhs": [FakeDriver(write_export)],
                    "dy": [FakeDriver(lambda raw: write_records(raw, "douyin", [DOUYIN_VIDEO]))],
                },
                platforms=["xhs", "dy"],
            )
            self.assertFalse(outcome.degraded)
            self.assertEqual(
                [(step.kind, step.platform, step.status) for step in outcome.steps],
                [("crawl", "xhs", "ok"), ("crawl", "dy", "ok"), ("pack", "", "ok")],
            )
            self.assertEqual((outcome.steps[0].records, outcome.steps[0].new), (4, 4))
            self.assertGreaterEqual(outcome.pack.posts, 1)
            self.assertEqual(len(store.posts(packed=True)), outcome.pack.posts)
            self.assertEqual(outcome.as_dict()["pack"]["pack_id"], outcome.pack.pack_id)
            self.assertFalse((self.workspace / LOCK_NAME).exists())

    def test_runs_are_unattended_unless_asked(self):
        with Store.open(self.workspace, create=True) as store:
            self.drive(store, {"xhs": [FakeDriver(write_export)]}, platforms=["xhs"], pack=False)
            self.assertEqual(self.options, [("xhs", {"echo": False, "unattended": True})])
            self.options.clear()
            self.clock.value += timedelta(hours=1)
            self.drive(
                store, {"xhs": [FakeDriver(write_export)]}, platforms=["xhs"], pack=False, attended=True
            )
            self.assertEqual(self.options, [("xhs", {"echo": False, "unattended": False})])

    def test_a_degraded_platform_does_not_stop_the_others(self):
        with Store.open(self.workspace, create=True) as store:
            outcome = self.drive(
                store,
                {
                    "xhs": [FakeDriver(status="degraded")],
                    "dy": [FakeDriver(lambda raw: write_records(raw, "douyin", [DOUYIN_VIDEO]))],
                },
                platforms=["xhs", "dy"],
                pack=False,
            )
            self.assertTrue(outcome.degraded)
            self.assertEqual([step.status for step in outcome.steps], ["degraded", "ok"])
            self.assertEqual(outcome.steps[0].message, "登录状态已过期")
            self.assertFalse(outcome.as_dict()["ok"])
            self.assertEqual(len(store.posts(platforms=["dy"])), 1)

    def test_a_platform_crawled_a_moment_ago_is_skipped_unless_forced(self):
        with Store.open(self.workspace, create=True) as store:
            self.drive(store, {"xhs": [FakeDriver(write_export)]}, platforms=["xhs"], pack=False)
            outcome = self.drive(store, {"xhs": []}, platforms=["xhs"], pack=False)
            self.assertEqual([step.status for step in outcome.steps], ["skipped"])
            self.assertIn("不到 5 分钟", outcome.steps[0].message)
            self.assertFalse(outcome.degraded)

            forced = FakeDriver(write_export)
            outcome = self.drive(store, {"xhs": [forced]}, platforms=["xhs"], pack=False, force=True)
            self.assertEqual([step.status for step in outcome.steps], ["ok"])
            self.assertIsNotNone(forced.calls[0]["since"], "a second search is incremental")
            outcome = self.drive(
                store,
                {"xhs": [FakeDriver(write_export)]},
                platforms=["xhs"],
                pack=False,
                full=True,
                force=True,
            )
            self.assertEqual(outcome.steps[0].status, "ok")

    def test_downloads_flagged_recordings_after_the_search_and_waits_for_the_interval(self):
        bili_search = FakeDriver(lambda raw: write_records(raw, "bili", [BILI_VIDEO, BILI_RECORDING]))
        downloader = FakeDriver(bili_video_with_audio)
        with Store.open(self.workspace, create=True) as store:
            outcome = self.drive(store, {"bili": [bili_search, downloader]}, platforms=["bili"])
            self.assertEqual(
                [(step.kind, step.status) for step in outcome.steps],
                [("crawl", "ok"), ("media", "ok"), ("pack", "ok")],
            )
            media = outcome.steps[1]
            self.assertEqual((media.post, media.media), ("bili:1150000002", 1))
            self.assertEqual(downloader.calls[0]["mode"], "detail")
            self.assertEqual(self.options[1], ("bili", {"echo": False, "unattended": True, "media": True}))
            self.assertEqual(self.clock.slept, [300], "the 5 minute interval between two B 站 runs is kept")
            self.assertEqual(outcome.pack.media_posts, 1)
            self.assertEqual(outcome.held_recordings, 0)

    def test_nothing_is_downloaded_after_a_failed_bili_search_or_when_turned_off(self):
        with Store.open(self.workspace, create=True) as store:
            self.drive(
                store,
                {"bili": [FakeDriver(lambda raw: write_records(raw, "bili", [BILI_RECORDING]))]},
                platforms=["bili"],
                download=False,
            )
            self.assertEqual(self.options, [("bili", {"echo": False, "unattended": True})])

            self.clock.value += timedelta(hours=1)
            outcome = self.drive(
                store, {"bili": [FakeDriver(status="degraded")]}, platforms=["bili"], pack=False
            )
            self.assertEqual(
                [(step.kind, step.status) for step in outcome.steps],
                [("crawl", "degraded"), ("media", "skipped")],
            )
            self.assertEqual(self.clock.slept, [])

    def test_recordings_wait_for_their_download_instead_of_being_packed(self):
        with Store.open(self.workspace, create=True) as store:
            outcome = self.drive(
                store,
                {"bili": [FakeDriver(lambda raw: write_records(raw, "bili", [BILI_RECORDING]))]},
                platforms=["bili"],
                download=False,
            )
            self.assertEqual(
                [(step.kind, step.status) for step in outcome.steps], [("crawl", "ok"), ("pack", "skipped")]
            )
            self.assertIsNone(outcome.pack)
            self.assertEqual(outcome.held_recordings, 1)
            self.assertEqual(store.posts(packed=True), [])

            self.clock.value += timedelta(hours=1)
            outcome = self.drive(
                store, {"bili": [FakeDriver(status="degraded")]}, platforms=["bili"], pack=False
            )
            self.assertEqual(outcome.held_recordings, 1)

    def test_a_failed_download_ends_the_downloads_but_not_the_run(self):
        search = FakeDriver(lambda raw: write_records(raw, "bili", [BILI_RECORDING]))
        with Store.open(self.workspace, create=True) as store:
            outcome = self.drive(store, {"bili": [search, FakeDriver(status="degraded")]}, platforms=["bili"])
            self.assertEqual(
                [(step.kind, step.status) for step in outcome.steps],
                [("crawl", "ok"), ("media", "degraded"), ("pack", "skipped")],
            )
            self.assertTrue(outcome.degraded)
            self.assertEqual(outcome.held_recordings, 1)

    def test_needs_keywords_and_a_free_lock(self):
        with Store.open(self.workspace, create=True) as store:
            empty = make_config(self.workspace, keywords=[])
            with self.assertRaisesRegex(GleanerError, "没有关键词"):
                run_auto(store, empty, platforms=["xhs"], fetcher_factory=self.factory)
            (self.workspace / LOCK_NAME).write_text("", encoding="utf-8")
            with self.assertRaisesRegex(GleanerError, "还在运行"):
                self.drive(store, {"xhs": []}, platforms=["xhs"])

    def test_the_summary_of_the_latest_run_is_kept_for_the_web_console(self):
        with Store.open(self.workspace, create=True) as store:
            self.assertIsNone(last_auto(self.workspace))
            self.drive(store, {"xhs": [FakeDriver(write_export)]}, platforms=["xhs"])
        summary = last_auto(self.workspace)
        self.assertEqual([step["kind"] for step in summary["steps"]], ["crawl", "pack"])
        self.assertTrue(summary["ok"])
        self.assertEqual(summary["pack"]["posts"], 3)
        self.assertLess(summary["started_at"], summary["finished_at"] + "z")


class ManualCommandsTest(WorkspaceTestCase):
    def test_a_fresh_lock_blocks_manual_crawls_downloads_and_packs_but_a_stale_one_does_not(self):
        import contextlib
        import io

        from gleaner.cli import main

        main(["--workspace", str(self.workspace), "init"])
        lock = self.workspace / LOCK_NAME
        lock.write_text("", encoding="utf-8")
        with self.assertRaisesRegex(GleanerError, "正在运行"):
            ensure_not_running(self.workspace)
        for argv in (["crawl", "nowcoder"], ["media", "--dry-run"], ["pack"]):
            with self.subTest(argv), contextlib.redirect_stderr(io.StringIO()) as err:
                self.assertEqual(main(["--workspace", str(self.workspace), *argv]), 1)
                self.assertIn("gleaner auto 正在运行", err.getvalue())
        old = time.time() - STALE_LOCK_SECONDS - 60
        os.utime(lock, (old, old))
        ensure_not_running(self.workspace)


class RunLockTest(WorkspaceTestCase):
    def test_lock_is_held_during_the_run_and_removed_after_it(self):
        path = self.workspace / LOCK_NAME
        with run_lock(self.workspace):
            self.assertTrue(path.exists())
            with self.assertRaisesRegex(GleanerError, "还在运行"), run_lock(self.workspace):
                pass
            self.assertTrue(path.exists(), "the refused run leaves the first run's lock alone")
        self.assertFalse(path.exists())

    def test_lock_is_removed_when_the_run_fails(self):
        with self.assertRaises(RuntimeError), run_lock(self.workspace):
            raise RuntimeError("boom")
        self.assertFalse((self.workspace / LOCK_NAME).exists())

    def test_a_stale_lock_from_a_crashed_run_is_replaced(self):
        path = self.workspace / LOCK_NAME
        path.write_text("", encoding="utf-8")
        old = time.time() - STALE_LOCK_SECONDS - 60
        os.utime(path, (old, old))
        with run_lock(self.workspace):
            self.assertGreater(path.stat().st_mtime, old + STALE_LOCK_SECONDS)
        self.assertFalse(path.exists())
