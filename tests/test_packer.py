import json
import shlex
import unittest

from gleaner.errors import GleanerError
from gleaner.packer import PACK_SCHEMA, build_pack
from gleaner.pipeline import import_export
from gleaner.store import Store
from tests.support import FAKE_JPEG, NOW, WorkspaceTestCase, make_config, write_export


class PackerTest(WorkspaceTestCase):
    def setUp(self):
        super().setUp()
        self.config = make_config(self.workspace)
        self.source = write_export(self.workspace / "external")

    def pack(self, **kwargs):
        with Store.open(self.workspace, create=True) as store:
            import_export(store, self.config, self.source, now=NOW)
            result = build_pack(store, self.config, now=NOW, **kwargs)
            records = {record.post.key: record for record in store.posts()}
        manifest = json.loads((result.path / "manifest.json").read_text(encoding="utf-8"))
        return result, manifest, records

    def test_packs_candidates_only_by_default(self):
        result, manifest, records = self.pack()
        self.assertEqual(manifest["schema"], PACK_SCHEMA)
        keys = [entry["key"] for entry in manifest["posts"]]
        self.assertEqual(sorted(keys), ["xhs:aa01", "xhs:cc03", "xhs:dd04"])
        self.assertEqual((result.image_posts, result.text_posts), (1, 2))
        self.assertTrue(all(records[key].packed_in == result.pack_id for key in keys))
        self.assertIsNone(records["xhs:bb02"].packed_in)
        self.assertFalse(any(path.name.startswith(".tmp") for path in result.path.parent.iterdir()))

    def test_entry_carries_interview_bank_metadata(self):
        result, manifest, _ = self.pack()
        entry = next(entry for entry in manifest["posts"] if entry["key"] == "xhs:aa01")
        self.assertEqual(entry["platform_label"], "小红书")
        self.assertEqual(entry["source_url"], "https://www.xiaohongshu.com/explore/aa01")
        self.assertEqual(entry["source_date"], "2026-10-21")
        self.assertEqual(entry["intake"], "images")
        self.assertEqual(entry["images"], ["posts/xhs-aa01/images/01.jpg", "posts/xhs-aa01/images/02.jpg"])
        self.assertEqual((result.path / entry["images"][0]).read_bytes(), FAKE_JPEG + b"\x01")
        body = (result.path / entry["text_file"]).read_text(encoding="utf-8")
        self.assertTrue(body.startswith("示例科技后端一面面经\n\n"))
        self.assertIn("标签：#面经 #后端", body)
        self.assertNotIn("SECRET", json.dumps(manifest, ensure_ascii=False))

    def test_video_and_text_posts_go_through_text_intake(self):
        _, manifest, _ = self.pack()
        entries = {entry["key"]: entry for entry in manifest["posts"]}
        self.assertEqual(entries["xhs:cc03"]["intake"], "text")
        self.assertEqual(entries["xhs:dd04"]["intake"], "text")
        self.assertEqual(entries["xhs:dd04"]["notes"], ["video-not-included"])

    def test_handoff_commands_target_the_collected_bank(self):
        result, manifest, _ = self.pack()
        handoff = (result.path / "HANDOFF.md").read_text(encoding="utf-8")
        bank = str(self.workspace / "collected-bank")
        self.assertIn("独立的采集题库", handoff)
        commands = [line for line in handoff.splitlines() if "ibank.py" in line and "init" not in line]
        self.assertEqual(len(commands), 3, "one ingest images command plus one web-intake per text post")
        for line in commands:
            parts = shlex.split(line)
            self.assertEqual(parts[parts.index("--bank") + 1], bank)
        image_command = next(shlex.split(line) for line in commands if " images " in line)
        self.assertIn(str(result.path / "posts" / "xhs-aa01" / "images"), image_command)
        self.assertNotIn("SECRET", handoff)

    def test_explicit_keys_and_repacking(self):
        result, manifest, _ = self.pack(keys=["xhs:bb02"])
        self.assertEqual([entry["key"] for entry in manifest["posts"]], ["xhs:bb02"])
        with Store.open(self.workspace) as store:
            with self.assertRaisesRegex(GleanerError, "已经打过包"):
                build_pack(store, self.config, now=NOW, keys=["xhs:bb02"])
            with self.assertRaisesRegex(GleanerError, "找不到"):
                build_pack(store, self.config, now=NOW, keys=["xhs:none"])

    def test_nothing_left_to_pack(self):
        self.pack()
        with Store.open(self.workspace) as store, self.assertRaisesRegex(GleanerError, "没有可以打包"):
            build_pack(store, self.config, now=NOW)

    def test_limit(self):
        result, _, _ = self.pack(limit=1)
        self.assertEqual(result.posts, 1)


if __name__ == "__main__":
    unittest.main()
