import json
import os
import shlex
import unittest
from pathlib import Path

from gleaner.audio import audio_command, ffmpeg_extractor
from gleaner.errors import GleanerError
from gleaner.packer import PACK_SCHEMA, build_pack, packable
from gleaner.pipeline import import_export
from gleaner.store import Store
from tests.support import (
    BILI_RECORDING,
    BILI_RECORDING_BVID,
    BILI_VIDEO,
    FAKE_JPEG,
    FAKE_VIDEO,
    INTERVIEW_NOTE,
    NOW,
    WorkspaceTestCase,
    fake_extract,
    make_config,
    write_export,
    write_records,
    write_video,
)


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

    def test_posts_whose_body_lists_the_questions_skip_their_images(self):
        listed = {
            **INTERVIEW_NOTE,
            "note_id": "ee05",
            "title": "示例科技 AI 应用二面",
            "desc": "1. 自我介绍\n2. Agent 采用什么架构？\n3. 如何评估 Skill 的效果？\n4. 手写三数之和",
        }
        caption_only = {**INTERVIEW_NOTE, "note_id": "ff06", "title": "示例科技三面", "desc": "#面经 #Agent"}
        self.source = write_export(
            self.workspace / "external2", [listed, caption_only], images={"ee05": 3, "ff06": 2}
        )
        result, manifest, _ = self.pack()
        entries = {entry["key"]: entry for entry in manifest["posts"]}

        text_entry = entries["xhs:ee05"]
        self.assertEqual(text_entry["intake"], "text")
        self.assertEqual(text_entry["body_questions"], 3)
        self.assertEqual(text_entry["images"], [])
        self.assertEqual(text_entry["skipped_images"], 3)
        self.assertIn("images-not-imported", text_entry["notes"])
        self.assertFalse((result.path / text_entry["dir"] / "images").exists())

        image_entry = entries["xhs:ff06"]
        self.assertEqual(image_entry["intake"], "images")
        self.assertEqual(len(image_entry["images"]), 2)
        self.assertEqual(image_entry["skipped_images"], 0)

        handoff = (result.path / "HANDOFF.md").read_text(encoding="utf-8")
        self.assertIn("看图导入 1 篇，读正文导入 1 篇", handoff)
        self.assertIn("使用心得、学习方式", handoff)

    def test_limit(self):
        result, _, _ = self.pack(limit=1)
        self.assertEqual(result.posts, 1)


class RecordingPackTest(WorkspaceTestCase):
    def setUp(self):
        super().setUp()
        self.config = make_config(
            self.workspace,
            media={"ffmpeg": str(self.workspace / "no-such-ffmpeg")},
            handoff={"asr_python": "asr-python"},
        )
        self.source = write_records(self.workspace / "external", "bili", [BILI_RECORDING, BILI_VIDEO])
        write_video(self.source, "bili", BILI_RECORDING_BVID)
        with Store.open(self.workspace, create=True) as store:
            import_export(store, self.config, self.source, now=NOW)

    def test_recording_is_packed_as_normalized_audio(self):
        with Store.open(self.workspace) as store:
            result = build_pack(store, self.config, now=NOW, keys=["bili:1150000002"], extract=fake_extract)
        self.assertEqual((result.posts, result.media_posts, result.text_posts), (1, 1, 0))
        manifest = json.loads((result.path / "manifest.json").read_text(encoding="utf-8"))
        (entry,) = manifest["posts"]
        self.assertEqual((entry["intake"], entry["notes"]), ("media", ["audio-normalized"]))
        self.assertEqual(entry["media"], "posts/bili-1150000002/audio.flac")
        self.assertEqual((result.path / entry["media"]).read_bytes(), b"fLaC" + FAKE_VIDEO)
        self.assertEqual(entry["source_url"], "https://www.bilibili.com/video/av1150000002")

        handoff = (result.path / "HANDOFF.md").read_text(encoding="utf-8")
        self.assertIn("转写录音导入 1 篇", handoff)
        self.assertIn("在 `correction` 里写明改了什么", handoff)
        commands = [
            shlex.split(line) for line in handoff.splitlines() if "ibank.py" in line and "init" not in line
        ]
        by_name = {parts[3]: parts for parts in commands}
        self.assertEqual(set(by_name), {"media", "media-transcribe", "media-task"})
        self.assertTrue(all(parts[0] == "asr-python" for parts in commands))
        self.assertIn(str(result.path / entry["media"]), by_name["media"])
        transcribe = by_name["media-transcribe"]
        self.assertEqual(transcribe[transcribe.index("--model") + 1], "large-v3-turbo")
        self.assertIn("--download-model", transcribe)

    def test_missing_ffmpeg_stops_before_anything_is_packed(self):
        with Store.open(self.workspace) as store:
            with self.assertRaisesRegex(GleanerError, "找不到 ffmpeg"):
                build_pack(store, self.config, now=NOW, keys=["bili:1150000002"])
            self.assertIsNone(store.get("bili:1150000002").packed_in)
            result = build_pack(store, self.config, now=NOW, keys=["bili:1150000001"])
        self.assertEqual((result.text_posts, result.media_posts), (1, 0))
        self.assertEqual(list((self.workspace / "packs").iterdir()), [result.path])

    def test_a_deleted_video_falls_back_to_the_title(self):
        with Store.open(self.workspace) as store:
            (self.workspace / store.get("bili:1150000002").post.media).unlink()
            result = build_pack(store, self.config, now=NOW, keys=["bili:1150000002"])
        manifest = json.loads((result.path / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(
            (manifest["posts"][0]["intake"], manifest["posts"][0]["notes"]), ("text", ["media-missing"])
        )


class HeldRecordingTest(WorkspaceTestCase):
    """A recording packed before its video is downloaded would only import its title, and never be downloaded."""

    def setUp(self):
        super().setUp()
        self.config = make_config(self.workspace)
        source = write_records(self.workspace / "external", "bili", [BILI_RECORDING])
        write_export(source, [INTERVIEW_NOTE])
        with Store.open(self.workspace, create=True) as store:
            import_export(store, self.config, source, now=NOW)

    def test_default_packing_leaves_recordings_that_are_not_downloaded(self):
        with Store.open(self.workspace) as store:
            ready, held = packable(store)
            self.assertEqual(([record.post.key for record in ready], held), (["xhs:aa01"], 1))
            result = build_pack(store, self.config, now=NOW)
            self.assertEqual((result.posts, result.held_recordings), (1, 1))
            self.assertEqual(result.as_dict()["held_recordings"], 1)
            self.assertIsNone(store.get("bili:1150000002").packed_in)

            with self.assertRaisesRegex(GleanerError, "有 1 个录音还没下载，先运行 gleaner media"):
                build_pack(store, self.config, now=NOW)

    def test_a_recording_can_still_be_packed_by_key(self):
        with Store.open(self.workspace) as store:
            result = build_pack(store, self.config, now=NOW, keys=["bili:1150000002"])
        self.assertEqual((result.posts, result.held_recordings), (1, 0))


class AudioTest(WorkspaceTestCase):
    def test_audio_is_mono_16k_with_loudness_normalized(self):
        command = audio_command("ffmpeg", Path("in.mp4"), Path("out.flac"))
        pairs = dict(zip(command[1::2], command[2::2], strict=False))
        self.assertEqual((pairs["-ac"], pairs["-ar"], pairs["-af"]), ("1", "16000", "dynaudnorm=f=150:g=15"))
        self.assertEqual(command[-1], "out.flac")

    @unittest.skipIf(os.name == "nt", "the stand-in ffmpeg is a shell script")
    def test_extractor_runs_ffmpeg_and_checks_its_output(self):
        ffmpeg = self.workspace / "ffmpeg"
        ffmpeg.write_text('#!/bin/sh\nfor last; do :; done\nprintf audio > "$last"\n', encoding="utf-8")
        ffmpeg.chmod(0o755)
        target = self.workspace / "out.flac"
        ffmpeg_extractor(str(ffmpeg))(self.workspace / "in.mp4", target)
        self.assertEqual(target.read_bytes(), b"audio")
        ffmpeg.write_text("#!/bin/sh\necho 'Invalid data found' >&2\nexit 1\n", encoding="utf-8")
        with self.assertRaisesRegex(GleanerError, "Invalid data found"):
            ffmpeg_extractor(str(ffmpeg))(self.workspace / "in.mp4", self.workspace / "other.flac")


if __name__ == "__main__":
    unittest.main()
