import contextlib
import io
import json
import unittest
from pathlib import Path

from gleaner.cli import main
from gleaner.errors import GleanerError
from gleaner.glossary import BUILTIN, Entry, entries, parse_extra, render_table
from gleaner.packer import build_pack
from gleaner.pipeline import import_export
from gleaner.store import Store
from tests.support import (
    BILI_RECORDING,
    BILI_RECORDING_BVID,
    NOW,
    WorkspaceTestCase,
    fake_extract,
    make_config,
    write_export,
    write_records,
    write_video,
)


class GlossaryTest(unittest.TestCase):
    def test_builtin_entries_are_unique_and_complete(self):
        heard = [item.heard for item in BUILTIN]
        self.assertEqual(len(heard), len(set(heard)))
        self.assertTrue(all(item.heard and item.correct and item.heard != item.correct for item in BUILTIN))
        self.assertIn(("Lilis", "Redis"), {(item.heard, item.correct) for item in BUILTIN})

    def test_extra_entries_are_parsed_and_appended(self):
        extra = parse_extra(["猪肉 = Zookeeper", " 卡夫卡=Kafka "])
        self.assertEqual(extra, (Entry("猪肉", "Zookeeper"), Entry("卡夫卡", "Kafka")))
        self.assertEqual(entries(extra)[-2:], list(extra))
        for bad in ("没有等号", " = Kafka", "卡夫卡 ="):
            with self.subTest(bad), self.assertRaises(GleanerError):
                parse_extra([bad])

    def test_table_has_one_row_per_entry(self):
        lines = render_table([Entry("Lilis", "Redis"), Entry("站", "栈", "看上下文")])
        self.assertEqual(lines[2:], ["| Lilis | Redis |  |", "| 站 | 栈 | 看上下文 |"])


class HandoffGlossaryTest(WorkspaceTestCase):
    def pack(self, *, with_recording: bool):
        config = make_config(
            self.workspace,
            media={"ffmpeg": str(self.workspace / "no-such-ffmpeg")},
            glossary={"extra": ["猪肉 = Zookeeper"]},
        )
        source = write_export(self.workspace / "external")
        if with_recording:
            write_records(source, "bili", [BILI_RECORDING])
            write_video(source, "bili", BILI_RECORDING_BVID)
        with Store.open(self.workspace, create=True) as store:
            import_export(store, config, source, now=NOW)
            keys = ["bili:1150000002"] if with_recording else ["xhs:cc03"]
            result = build_pack(store, config, now=NOW, keys=keys, extract=fake_extract)
        return (result.path / "HANDOFF.md").read_text(encoding="utf-8")

    def test_a_pack_with_recordings_carries_the_table_with_your_own_entries(self):
        handoff = self.pack(with_recording=True)
        self.assertIn("## 转写常见误识别", handoff)
        self.assertIn("| Lilis | Redis |", handoff)
        self.assertIn("| 猪肉 | Zookeeper |", handoff)
        self.assertLess(handoff.index("## 转写常见误识别"), handoff.index("## 命令"))

    def test_other_packs_leave_it_out(self):
        self.assertNotIn("转写常见误识别", self.pack(with_recording=False))


class GlossaryCommandTest(WorkspaceTestCase):
    def test_command_lists_builtin_and_configured_entries(self):
        def call(*argv):
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                self.assertEqual(main(["--workspace", str(self.workspace), *argv]), 0)
            return out.getvalue()

        call("init")
        config = Path(self.workspace) / "gleaner.toml"
        config.write_text(
            config.read_text(encoding="utf-8").replace("extra = []", 'extra = ["猪肉 = Zookeeper"]'),
            encoding="utf-8",
        )
        data = json.loads(call("glossary", "--json"))
        self.assertEqual(len(data["entries"]), len(BUILTIN) + 1)
        self.assertEqual(data["entries"][-1], {"heard": "猪肉", "correct": "Zookeeper", "note": ""})
        self.assertIn("Lilis → Redis", call("glossary"))


if __name__ == "__main__":
    unittest.main()
