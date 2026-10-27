import sqlite3
import unittest
from dataclasses import replace

from gleaner.errors import GleanerError
from gleaner.models import Post
from gleaner.store import DB_NAME, SCHEMA_VERSION, Store
from tests.support import WorkspaceTestCase

T0, T1 = "2026-10-27T08:00:00+00:00", "2026-10-27T09:00:00+00:00"


def sample(**changes) -> Post:
    base = Post(
        platform="xhs",
        post_id="aa01",
        url="https://www.xiaohongshu.com/explore/aa01",
        open_url="https://www.xiaohongshu.com/explore/aa01?xsec_token=T",
        title="示例科技一面",
        body="问了 Redis",
        kind="image",
        published_at="2026-10-21T00:00:00+00:00",
        keywords=["后端 面经"],
        images=["runs/r1/raw/xhs/media/aa01/001.jpg"],
        remote_image_count=1,
    )
    return replace(base, **changes)


class StoreTest(WorkspaceTestCase):
    def open(self):
        return Store.open(self.workspace, create=True)

    def run_ids(self, store, *ids):
        for run_id in ids:
            store.begin_run(run_id, source="s", mode="search", params={}, output_dir="x", now=T0)

    def test_requires_init_unless_creating(self):
        with self.assertRaises(GleanerError), Store.open(self.workspace):
            pass

    def test_new_unchanged_and_keyword_union(self):
        with self.open() as store:
            self.run_ids(store, "r1", "r2")
            self.assertEqual(store.upsert_post(sample(), run_id="r1", now=T0), "new")
            again = sample(keywords=["Java 一面"])
            self.assertEqual(store.upsert_post(again, run_id="r2", now=T1), "unchanged")
            record = store.get("xhs:aa01")
        self.assertEqual(record.post.keywords, ["后端 面经", "Java 一面"])
        self.assertEqual((record.first_run, record.last_run), ("r1", "r2"))
        self.assertEqual(record.last_seen_at, T1)

    def test_edit_clears_rule_label_but_keeps_manual_label(self):
        with self.open() as store:
            self.run_ids(store, "r1", "r2", "r3")
            store.upsert_post(sample(), run_id="r1", now=T0)
            store.set_label("xhs:aa01", "candidate", source="rule", reasons=["event:一面"], now=T0)
            self.assertEqual(store.upsert_post(sample(body="补充了二面"), run_id="r2", now=T1), "updated")
            self.assertIsNone(store.get("xhs:aa01").label)

            store.set_label("xhs:aa01", "promo", source="manual", reasons=["manual"], now=T1)
            store.upsert_post(sample(body="又改了"), run_id="r3", now=T1)
            self.assertEqual(store.get("xhs:aa01").label, "promo")

    def test_edit_after_packing_is_flagged_not_repacked(self):
        with self.open() as store:
            self.run_ids(store, "r1", "r2")
            store.upsert_post(sample(), run_id="r1", now=T0)
            store.mark_packed(["xhs:aa01"], pack_id="p1", now=T0)
            store.upsert_post(sample(title="改过的标题"), run_id="r2", now=T1)
            record = store.get("xhs:aa01")
            self.assertTrue(record.changed_after_pack)
            self.assertEqual(record.packed_in, "p1")
            self.assertEqual(store.stats()["changed_after_pack"], 1)

    def test_images_arriving_later_count_as_update(self):
        with self.open() as store:
            self.run_ids(store, "r1", "r2")
            store.upsert_post(sample(images=[]), run_id="r1", now=T0)
            self.assertEqual(store.upsert_post(sample(), run_id="r2", now=T1), "updated")
            self.assertEqual(len(store.get("xhs:aa01").post.images), 1)

    def test_filters(self):
        with self.open() as store:
            self.run_ids(store, "r1")
            store.upsert_post(sample(), run_id="r1", now=T0)
            store.upsert_post(sample(post_id="bb02", published_at=None), run_id="r1", now=T0)
            store.set_label("xhs:aa01", "candidate", source="rule", reasons=[], now=T0)
            self.assertEqual([r.post.key for r in store.posts(labels=["candidate"])], ["xhs:aa01"])
            self.assertEqual([r.post.key for r in store.posts(unlabeled=True)], ["xhs:bb02"])
            store.mark_packed(["xhs:aa01"], pack_id="p1", now=T0)
            self.assertEqual([r.post.key for r in store.posts(packed=False)], ["xhs:bb02"])
            with self.assertRaises(GleanerError):
                store.set_label("xhs:missing", "promo", source="manual", reasons=[], now=T0)
            with self.assertRaises(GleanerError):
                store.set_label("xhs:aa01", "spam", source="manual", reasons=[], now=T0)

    def test_checkpoint_only_moves_forward(self):
        with self.open() as store:
            store.advance_checkpoint("s", "后端 面经", "2026-10-21T00:00:00+00:00", now=T0)
            store.advance_checkpoint("s", "后端 面经", "2026-10-01T00:00:00+00:00", now=T1)
            (checkpoint,) = store.checkpoints("s")
        self.assertEqual(checkpoint["latest_published_at"], "2026-10-21T00:00:00+00:00")

    def test_refuses_newer_schema(self):
        with self.open():
            pass
        connection = sqlite3.connect(self.workspace / DB_NAME)
        with connection:
            connection.execute(
                "UPDATE meta SET value = ? WHERE key = 'schema_version'", (str(SCHEMA_VERSION + 1),)
            )
        connection.close()
        with self.assertRaises(GleanerError), Store.open(self.workspace):
            pass


if __name__ == "__main__":
    unittest.main()
