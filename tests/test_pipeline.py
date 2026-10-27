import unittest
from datetime import timedelta

from gleaner.errors import GleanerError
from gleaner.pipeline import SOURCE_XHS, crawl_xhs, import_export, screen
from gleaner.sources.base import FetchResult
from gleaner.store import Store
from tests.support import NOW, PROMO_NOTE, WorkspaceTestCase, make_config, write_export


class FakeDriver:
    def __init__(self, *, produce=True, status="ok"):
        self.produce = produce
        self.status = status
        self.calls = []

    def run(self, *, mode, output_dir, log_dir, keywords=(), targets=(), max_notes=None):
        self.calls.append({"mode": mode, "keywords": list(keywords), "targets": list(targets)})
        if self.produce:
            write_export(output_dir)
        return FetchResult(self.status, output_dir, "" if self.status == "ok" else "登录过期")


class PipelineTest(WorkspaceTestCase):
    def setUp(self):
        super().setUp()
        self.config = make_config(self.workspace)

    def crawl(self, store, driver, *, now=NOW, force=False, keywords=("后端 面经",), targets=()):
        return crawl_xhs(
            store,
            self.config,
            driver,
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


if __name__ == "__main__":
    unittest.main()
