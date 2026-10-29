import contextlib
import io
import json
import os
import unittest
from unittest import mock

from gleaner.cli import EXIT_DEGRADED, main
from gleaner.config import WORKSPACE_ENV
from gleaner.sources.base import FetchResult
from tests.support import (
    BILI_RECORDING,
    BILI_RECORDING_BVID,
    BILI_VIDEO,
    NOWCODER_DISCUSS,
    WorkspaceTestCase,
    write_export,
    write_records,
    write_video,
)


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
        self.assertEqual(listed["posts"][0]["platform"], "xhs")
        self.assertEqual(self.json("list", "--platform", "bili")["posts"], [])
        self.assertEqual(len(self.json("list", "--platform", "xhs", "--platform", "bili")["posts"]), 4)
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
        text = config.read_text(encoding="utf-8").replace('"../MediaCrawler"', '"no-such-mediacrawler"')
        config.write_text(text, encoding="utf-8")
        code, out, _ = self.call("crawl", "xhs", "--keyword", "后端 面经", "--json")
        self.assertEqual(code, EXIT_DEGRADED)
        self.assertIn("MediaCrawler", json.loads(out)["message"])

    def test_crawl_nowcoder_without_mediacrawler(self):
        self.call("init")

        class FakeFetcher:
            def run(self, *, mode, output_dir, log_dir, keywords=(), targets=(), max_notes=None):
                write_records(output_dir, "nowcoder", [NOWCODER_DISCUSS])
                return FetchResult("ok", output_dir)

        with mock.patch("gleaner.cli.fetcher_for", return_value=FakeFetcher()) as factory:
            code, out, err = self.call("crawl", "nowcoder", "--keyword", "后端 面经", "--max-notes", "5")
        self.assertEqual(code, 0, out + err)
        self.assertEqual(factory.call_args.args[1], "nowcoder")
        self.assertIn("翻阅最新面经列表", err)
        self.assertIn("最多约 5 篇", err)
        self.assertIn("新增 1", out)
        self.assertEqual(self.json("show", "nowcoder:800000001")["post"]["platform"], "nowcoder")

    def test_media_downloads_flagged_recordings(self):
        self.call("init")
        source = write_records(self.workspace.parent / "bili-data", "bili", [BILI_VIDEO, BILI_RECORDING])
        self.json("import", str(source))
        listed = self.json("media", "--dry-run")
        self.assertEqual([post["key"] for post in listed["posts"]], ["bili:1150000002"])

        class FakeDownloader:
            def run(self, *, mode, output_dir, log_dir, keywords=(), targets=(), max_notes=None):
                write_records(output_dir, "bili", [BILI_RECORDING], mode="detail")
                write_video(output_dir, "bili", BILI_RECORDING_BVID)
                return FetchResult("ok", output_dir)

        with mock.patch("gleaner.cli.fetcher_for", return_value=FakeDownloader()) as factory:
            code, out, err = self.call("media", "--quiet")
        self.assertEqual(code, 0, out + err)
        self.assertEqual((factory.call_args.args[1], factory.call_args.kwargs["media"]), ("bili", True))
        self.assertIn("下载到音视频 1 个", out)
        self.assertTrue(self.json("show", "bili:1150000002")["post"]["media"])
        self.assertIn("没有要下载", self.call("media")[1])

        code, _, err = self.call("crawl", "bili", "--media")
        self.assertEqual(code, 1)
        self.assertIn("--url", err)

    def test_crawl_rejects_bad_arguments(self):
        self.call("init")
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            main(["--workspace", str(self.workspace), "crawl", "weibo"])
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
