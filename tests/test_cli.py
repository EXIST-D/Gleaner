import contextlib
import io
import json
import os
import unittest
from unittest import mock

from gleaner.cli import EXIT_DEGRADED, main
from gleaner.config import WORKSPACE_ENV
from tests.support import WorkspaceTestCase, write_export


class CliTest(WorkspaceTestCase):
    def call(self, *argv: str) -> tuple[int, str, str]:
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = main(["--workspace", str(self.workspace), *argv])
        return code, out.getvalue(), err.getvalue()

    def json(self, *argv: str) -> dict:
        code, out, err = self.call(*argv, "--json")
        self.assertEqual(code, 0, out + err)
        return json.loads(out)

    def test_end_to_end_import_list_label_pack(self):
        self.assertTrue(self.json("init")["config_created"])
        self.assertFalse(self.json("init")["config_created"])

        source = write_export(self.workspace.parent / "mediacrawler-data")
        imported = self.json("import", str(source))
        self.assertEqual(imported["new"], 4)

        listed = self.json("list", "--label", "candidate")
        self.assertEqual(len(listed["posts"]), 3)
        self.assertEqual(self.json("show", "xhs:aa01")["post"]["source_date"], "2026-10-21")

        self.json("label", "xhs:dd04", "irrelevant", "--reason", "视频，先不要")
        packed = self.json("pack")
        self.assertEqual(packed["posts"], 2)
        self.assertTrue(os.path.isfile(os.path.join(packed["path"], "HANDOFF.md")))

        stats = self.json("stats")
        self.assertEqual((stats["posts"], stats["packed"]), (4, 2))
        self.assertEqual(self.json("runs")["runs"][0]["mode"], "import")

    def test_text_output(self):
        self.call("init")
        code, out, _ = self.call("stats")
        self.assertEqual(code, 0)
        self.assertIn("帖子 0 篇", out)

    def test_errors_are_one_line_messages(self):
        code, out, err = self.call("stats")
        self.assertEqual(code, 1)
        self.assertIn("还没有初始化", err)
        self.assertEqual(out, "")
        code, out, _ = self.call("stats", "--json")
        self.assertEqual((code, json.loads(out)["ok"]), (1, False))

    def test_crawl_without_mediacrawler_is_degraded(self):
        self.call("init")
        config = self.workspace / "gleaner.toml"
        text = config.read_text(encoding="utf-8").replace('"~/MediaCrawler"', '"no-such-mediacrawler"')
        config.write_text(text, encoding="utf-8")
        code, out, _ = self.call("crawl", "xhs", "--keyword", "后端 面经", "--json")
        self.assertEqual(code, EXIT_DEGRADED)
        self.assertIn("MediaCrawler", json.loads(out)["message"])

    def test_crawl_rejects_bad_arguments(self):
        self.call("init")
        self.assertEqual(self.call("crawl", "xhs", "--keyword", "a,b")[0], 1)
        self.assertEqual(self.call("crawl", "xhs", "--max-notes", "500")[0], 1)

    def test_doctor_reports_missing_pieces(self):
        self.call("init")
        code, out, _ = self.call("doctor", "--json")
        self.assertEqual(code, 1)
        checks = {check["name"]: check["ok"] for check in json.loads(out)["checks"]}
        self.assertFalse(checks["ibank"])
        self.assertFalse(checks["bank"])

    def test_workspace_from_environment(self):
        out = io.StringIO()
        with (
            mock.patch.dict(os.environ, {WORKSPACE_ENV: str(self.workspace)}),
            contextlib.redirect_stdout(out),
        ):
            self.assertEqual(main(["init", "--json"]), 0)
        self.assertEqual(json.loads(out.getvalue())["workspace"], str(self.workspace))

    def test_missing_workspace(self):
        err = io.StringIO()
        with mock.patch.dict(os.environ, {}, clear=True), contextlib.redirect_stderr(err):
            self.assertEqual(main(["stats"]), 1)
        self.assertIn("--workspace", err.getvalue())


if __name__ == "__main__":
    unittest.main()
