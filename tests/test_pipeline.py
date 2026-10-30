import unittest
from datetime import timedelta

from gleaner.errors import GleanerError
from gleaner.pipeline import (
    SEARCH_OVERLAP,
    SearchPlan,
    crawl,
    fetcher_for,
    import_export,
    planned_notes,
    recordings_to_download,
    screen,
    search_plan,
    seconds_until_next_crawl,
    source_name,
)
from gleaner.sources.base import FetchResult
from gleaner.sources.mediacrawler import MediaCrawlerDriver
from gleaner.sources.nowcoder import NowcoderFetcher
from gleaner.store import Store
from gleaner.timeutil import iso
from tests.support import (
    BILI_RECORDING,
    BILI_RECORDING_BVID,
    BILI_VIDEO,
    NOW,
    NOWCODER_DISCUSS,
    PROMO_NOTE,
    WorkspaceTestCase,
    make_config,
    write_export,
    write_records,
    write_video,
)


class FakeDriver:
    def __init__(self, *, produce=True, status="ok", write=write_export):
        self.produce = produce
        self.status = status
        self.write = write
        self.calls = []

    def problems(self):
        return []

    def run(
        self, *, mode, output_dir, log_dir, keywords=(), targets=(), max_notes=None, since=None, known=()
    ):
        self.calls.append(
            {
                "mode": mode,
                "keywords": list(keywords),
                "targets": list(targets),
                "since": since,
                "known": set(known),
            }
        )
        if self.produce:
            self.write(output_dir)
        return FetchResult(self.status, output_dir, "" if self.status == "ok" else "登录过期")


class PipelineTest(WorkspaceTestCase):
    def setUp(self):
        super().setUp()
        self.config = make_config(self.workspace)

    def crawl(
        self,
        store,
        driver,
        *,
        platform="xhs",
        now=NOW,
        force=False,
        keywords=("后端 面经",),
        targets=(),
        incremental=True,
    ):
        return crawl(
            store,
            self.config,
            driver,
            platform=platform,
            keywords=list(keywords),
            targets=list(targets),
            max_notes=None,
            force=force,
            now=now,
            incremental=incremental,
        )

    def test_incremental_search_starts_a_day_before_the_last_successful_search(self):
        later = NOW + timedelta(hours=6)
        with Store.open(self.workspace, create=True) as store:
            first = FakeDriver()
            outcome = self.crawl(store, first)
            self.assertIsNone(first.calls[0]["since"])
            self.assertEqual(outcome.search, "不限发布时间")

            second = FakeDriver()
            outcome = self.crawl(store, second, now=later)
            self.assertEqual(second.calls[0]["since"], NOW - SEARCH_OVERLAP)
            self.assertEqual(second.calls[0]["known"], {"aa01", "bb02", "cc03", "dd04"})
            self.assertIn("小红书不能按发布时间搜索", outcome.search)
            self.assertEqual(store.runs()[0].params["since"], iso(NOW - SEARCH_OVERLAP))

            third = FakeDriver()
            self.crawl(store, third, now=later + timedelta(hours=1), keywords=("后端 面经", "Java 一面"))
            self.assertIsNone(third.calls[0]["since"], "a keyword never searched before: any time")

            full = FakeDriver()
            self.crawl(store, full, now=later + timedelta(hours=2), incremental=False)
            self.assertEqual((full.calls[0]["since"], full.calls[0]["known"]), (None, set()))
            self.assertFalse(store.runs()[0].params["incremental"])

    def test_degraded_searches_do_not_move_the_incremental_start(self):
        with Store.open(self.workspace, create=True) as store:
            self.crawl(store, FakeDriver(status="degraded"))
            driver = FakeDriver()
            self.crawl(store, driver, now=NOW + timedelta(hours=1))
            self.assertIsNone(driver.calls[0]["since"])

    def test_search_plan_asks_the_platform_for_the_narrowest_range(self):
        with Store.open(self.workspace, create=True) as store:
            store.begin_run(
                "r1",
                source="mediacrawler-dy",
                mode="search",
                params={"keywords": ["后端"]},
                output_dir="x",
                now=iso(NOW),
            )
            store.finish_run("r1", status="ok", message="", counts={}, now=iso(NOW))
            plan = search_plan(store, platform="dy", keywords=["后端"], now=NOW + timedelta(hours=2))
            self.assertEqual((plan.since, plan.window), (NOW - SEARCH_OVERLAP, "一周内"))
            self.assertEqual(plan.describe("dy"), "增量：只搜一周内发布的帖子")
            later = search_plan(store, platform="dy", keywords=["后端"], now=NOW + timedelta(days=400))
            self.assertIsNone(later.since, "longer ago than Douyin's widest range: any time")
            nowcoder = search_plan(store, platform="nowcoder", keywords=["后端"], now=NOW)
            self.assertIsNone(nowcoder.since, "runs are kept per platform")
            self.assertEqual(
                search_plan(store, platform="dy", keywords=["后端"], now=NOW, incremental=False), SearchPlan()
            )

    def test_crawl_imports_and_screens_new_posts(self):
        with Store.open(self.workspace, create=True) as store:
            outcome = self.crawl(store, FakeDriver())
            self.assertEqual(outcome.status, "ok")
            self.assertEqual((outcome.summary.records, outcome.summary.new), (4, 4))
            self.assertEqual(dict(outcome.summary.labels), {"candidate": 3, "promo": 1})
            self.assertEqual(store.get("xhs:bb02").label, "promo")
            (run,) = store.runs()
            self.assertEqual((run.status, run.mode), ("ok", "search"))
            self.assertEqual(run.counts["new"], 4)
            checkpoint = {
                c["keyword"]: c["latest_published_at"] for c in store.checkpoints(source_name("xhs"))
            }
            self.assertEqual(checkpoint["后端 面经"], "2026-10-24T00:00:00+00:00")

    def test_run_records_the_real_page_based_plan(self):
        with Store.open(self.workspace, create=True) as store:
            self.crawl(store, FakeDriver(), keywords=("后端 面经", "Java 一面"))
            self.assertEqual(store.runs()[0].params["planned_notes"], 40)
        self.assertEqual(planned_notes(self.config, keywords=[], targets=["a", "b"], max_notes=None), 2)

    def test_second_crawl_counts_known_posts_as_unchanged(self):
        with Store.open(self.workspace, create=True) as store:
            self.crawl(store, FakeDriver())
            outcome = self.crawl(store, FakeDriver(), now=NOW + timedelta(minutes=10))
        self.assertEqual((outcome.summary.new, outcome.summary.unchanged), (0, 4))

    def test_minimum_interval_between_crawls(self):
        with Store.open(self.workspace, create=True) as store:
            self.crawl(store, FakeDriver())
            with self.assertRaisesRegex(GleanerError, "分钟"):
                self.crawl(store, FakeDriver(), now=NOW + timedelta(minutes=1))
            self.crawl(store, FakeDriver(), now=NOW + timedelta(minutes=1), force=True)

    def test_degraded_run_still_imports_partial_output(self):
        with Store.open(self.workspace, create=True) as store:
            outcome = self.crawl(store, FakeDriver(status="degraded"))
            self.assertEqual(outcome.status, "degraded")
            self.assertEqual(outcome.summary.new, 4)
            self.assertEqual(store.runs()[0].message, "登录过期")

    def test_detail_mode_for_explicit_links(self):
        driver = FakeDriver()
        with Store.open(self.workspace, create=True) as store:
            self.crawl(store, driver, keywords=(), targets=("https://www.xiaohongshu.com/explore/aa01",))
            self.assertEqual(store.runs()[0].mode, "detail")
        self.assertEqual(driver.calls[0]["mode"], "detail")

    def test_search_needs_keywords(self):
        with Store.open(self.workspace, create=True) as store, self.assertRaises(GleanerError):
            self.crawl(store, FakeDriver(), keywords=())

    def test_import_existing_export_does_not_count_toward_interval(self):
        source = write_export(self.workspace / "external")
        with Store.open(self.workspace, create=True) as store:
            outcome = import_export(store, self.config, source, now=NOW)
            self.assertEqual(outcome.summary.new, 4)
            self.crawl(store, FakeDriver(), now=NOW + timedelta(seconds=1))
            with self.assertRaises(GleanerError):
                import_export(store, self.config, self.workspace / "missing", now=NOW)

    def test_import_of_empty_export_is_degraded(self):
        source = write_export(self.workspace / "external", [], images={})
        with Store.open(self.workspace, create=True) as store:
            outcome = import_export(store, self.config, source, now=NOW)
        self.assertEqual(outcome.status, "degraded")

    def test_rescreen_keeps_manual_labels(self):
        with Store.open(self.workspace, create=True) as store:
            self.crawl(store, FakeDriver())
            store.set_label("xhs:aa01", "unclear", source="manual", reasons=["manual"], now="t")
            labels = screen(store, self.config, now=NOW, rescreen=True)
            self.assertEqual(sum(labels.values()), 3)
            self.assertEqual(store.get("xhs:aa01").label, "unclear")
            self.assertEqual(store.get(f"xhs:{PROMO_NOTE['note_id']}").label, "promo")

    def test_planned_notes_follow_each_platform(self):
        def plan(platform, n=None):
            return planned_notes(self.config, platform=platform, keywords=["a", "b"], targets=[], max_notes=n)

        self.assertEqual(plan("xhs"), 40)
        self.assertEqual(plan("dy", 25), 40, "Douyin pages hold 10 posts")
        self.assertEqual(plan("nowcoder", 7), 14, "Nowcoder saves exactly the requested number")
        self.assertEqual(plan("nowcoder"), 40)

    def test_each_platform_has_its_own_interval_and_checkpoint(self):
        bili = FakeDriver(write=lambda raw: write_records(raw, "bili", [BILI_VIDEO]))
        nowcoder = FakeDriver(write=lambda raw: write_records(raw, "nowcoder", [NOWCODER_DISCUSS]))
        with Store.open(self.workspace, create=True) as store:
            self.crawl(store, FakeDriver())
            outcome = self.crawl(store, bili, platform="bili", now=NOW + timedelta(minutes=1))
            self.assertEqual(outcome.summary.new, 1)
            self.crawl(store, nowcoder, platform="nowcoder", now=NOW + timedelta(minutes=1))
            with self.assertRaisesRegex(GleanerError, "B站"):
                self.crawl(store, bili, platform="bili", now=NOW + timedelta(minutes=2))
            self.assertGreater(seconds_until_next_crawl(store, self.config, NOW, platform="nowcoder"), 0)
            self.assertEqual(seconds_until_next_crawl(store, self.config, NOW, platform="zhihu"), 0)
            platforms = {run.source: run.params["platform"] for run in store.runs()}
            self.assertEqual(
                platforms, {"mediacrawler-xhs": "xhs", "mediacrawler-bili": "bili", "nowcoder": "nowcoder"}
            )
            self.assertEqual(
                {c["source"] for c in store.checkpoints()},
                {source_name("xhs"), "mediacrawler-bili", "nowcoder"},
            )

    def test_unknown_platform_is_rejected(self):
        with Store.open(self.workspace, create=True) as store, self.assertRaisesRegex(GleanerError, "不支持"):
            self.crawl(store, FakeDriver(), platform="weibo")

    def test_fetcher_for_picks_the_source(self):
        self.assertIsInstance(fetcher_for(self.config, "nowcoder", echo=False), NowcoderFetcher)
        driver = fetcher_for(self.config, "zhihu", runner=lambda *args: 0)
        self.assertIsInstance(driver, MediaCrawlerDriver)
        self.assertEqual(driver.platform, "zhihu")
        self.assertEqual(source_name("dy"), "mediacrawler-dy")

    def test_recordings_flagged_by_screening_are_downloaded_on_request(self):
        found = FakeDriver(write=lambda raw: write_records(raw, "bili", [BILI_VIDEO, BILI_RECORDING]))

        def with_video(raw):
            write_records(raw, "bili", [BILI_RECORDING], mode="detail")
            write_video(raw, "bili", BILI_RECORDING_BVID)

        later = NOW + timedelta(minutes=10)
        with Store.open(self.workspace, create=True) as store:
            self.crawl(store, found, platform="bili")
            (wanted,) = recordings_to_download(store)
            self.assertEqual(wanted.post.key, "bili:1150000002")
            downloader = FakeDriver(write=with_video)
            outcome = crawl(
                store,
                self.config,
                downloader,
                platform="bili",
                keywords=[],
                targets=[wanted.post.url],
                max_notes=None,
                force=False,
                now=later,
                media=True,
            )
            self.assertEqual((outcome.status, outcome.summary.media, outcome.summary.updated), ("ok", 1, 1))
            self.assertEqual(downloader.calls[0]["mode"], "detail")
            self.assertTrue(store.get("bili:1150000002").post.media.endswith("/video.mp4"))
            self.assertTrue(store.runs()[0].params["media"])
            self.assertEqual(recordings_to_download(store), [])
            self.assertEqual(
                recordings_to_download(store, ["bili:1150000001"])[0].post.key, "bili:1150000001"
            )
            with self.assertRaisesRegex(GleanerError, "找不到"):
                recordings_to_download(store, ["bili:404"])

    def test_a_recording_that_did_not_download_is_degraded(self):
        driver = FakeDriver(write=lambda raw: write_records(raw, "bili", [BILI_RECORDING], mode="detail"))
        with Store.open(self.workspace, create=True) as store:
            outcome = crawl(
                store,
                self.config,
                driver,
                platform="bili",
                keywords=[],
                targets=["1150000002"],
                max_notes=None,
                force=False,
                now=NOW,
                media=True,
            )
            self.assertEqual(outcome.status, "degraded")
            self.assertIn("只下载到 0 个", outcome.message)
            with self.assertRaisesRegex(GleanerError, "视频链接"):
                crawl(
                    store,
                    self.config,
                    driver,
                    platform="bili",
                    keywords=["a"],
                    targets=[],
                    max_notes=None,
                    force=True,
                    now=NOW,
                    media=True,
                )

    def test_only_bilibili_downloads_recordings(self):
        driver = fetcher_for(self.config, "bili", runner=lambda *args: 0, media=True)
        self.assertIs(driver.media, self.config.media)
        self.assertIsNone(fetcher_for(self.config, "bili", runner=lambda *args: 0).media)
        for platform in ("xhs", "nowcoder"):
            with self.subTest(platform), self.assertRaisesRegex(GleanerError, "B站"):
                fetcher_for(self.config, platform, runner=lambda *args: 0, media=True)
        with Store.open(self.workspace, create=True) as store:
            self.crawl(store, FakeDriver())
            with self.assertRaisesRegex(GleanerError, "不支持"):
                recordings_to_download(store, ["xhs:aa01"])

    def test_import_of_mixed_export_records_its_platforms(self):
        source = write_records(write_export(self.workspace / "external"), "bili", [BILI_VIDEO])
        with Store.open(self.workspace, create=True) as store:
            outcome = import_export(store, self.config, source, now=NOW)
            self.assertEqual(outcome.summary.new, 5)
            (run,) = store.runs()
            self.assertEqual((run.source, run.params["platforms"]), ("mediacrawler", ["xhs", "bili"]))


if __name__ == "__main__":
    unittest.main()
