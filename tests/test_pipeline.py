import unittest
from datetime import timedelta

from gleaner.errors import GleanerError
from gleaner.pipeline import (
    SOURCE_XHS,
    crawl,
    fetcher_for,
    import_export,
    planned_notes,
    screen,
    seconds_until_next_crawl,
    source_name,
)
from gleaner.sources.base import FetchResult
from gleaner.sources.mediacrawler import MediaCrawlerDriver
from gleaner.sources.nowcoder import NowcoderFetcher
from gleaner.store import Store
from tests.support import (
    BILI_VIDEO,
    NOW,
    NOWCODER_DISCUSS,
    PROMO_NOTE,
    WorkspaceTestCase,
    make_config,
    write_export,
    write_records,
)


class FakeDriver:
    def __init__(self, *, produce=True, status="ok", write=write_export):
        self.produce = produce
        self.status = status
        self.write = write
        self.calls = []

    def problems(self):
        return []

    def run(self, *, mode, output_dir, log_dir, keywords=(), targets=(), max_notes=None):
        self.calls.append({"mode": mode, "keywords": list(keywords), "targets": list(targets)})
        if self.produce:
            self.write(output_dir)
        return FetchResult(self.status, output_dir, "" if self.status == "ok" else "登录过期")


class PipelineTest(WorkspaceTestCase):
    def setUp(self):
        super().setUp()
        self.config = make_config(self.workspace)

    def crawl(
        self, store, driver, *, platform="xhs", now=NOW, force=False, keywords=("后端 面经",), targets=()
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
            checkpoint = {c["keyword"]: c["latest_published_at"] for c in store.checkpoints(SOURCE_XHS)}
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
            self.assertEqual(seconds_until_next_crawl(store, self.config, NOW, platform="ks"), 0)
            platforms = {run.source: run.params["platform"] for run in store.runs()}
            self.assertEqual(
                platforms, {"mediacrawler-xhs": "xhs", "mediacrawler-bili": "bili", "nowcoder": "nowcoder"}
            )
            self.assertEqual(
                {c["source"] for c in store.checkpoints()}, {SOURCE_XHS, "mediacrawler-bili", "nowcoder"}
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

    def test_import_of_mixed_export_records_its_platforms(self):
        source = write_records(write_export(self.workspace / "external"), "bili", [BILI_VIDEO])
        with Store.open(self.workspace, create=True) as store:
            outcome = import_export(store, self.config, source, now=NOW)
            self.assertEqual(outcome.summary.new, 5)
            (run,) = store.runs()
            self.assertEqual((run.source, run.params["platforms"]), ("mediacrawler", ["xhs", "bili"]))


if __name__ == "__main__":
    unittest.main()
