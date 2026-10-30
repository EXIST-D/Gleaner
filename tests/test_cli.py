import contextlib
import io
import json
import os
import unittest
from pathlib import Path
from unittest import mock

from gleaner.auto import AutoOutcome, Step
from gleaner.cli import EXIT_DEGRADED, main
from gleaner.config import WORKSPACE_ENV
from gleaner.packer import PackResult
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
            def run(
                self,
                *,
                mode,
                output_dir,
                log_dir,
                keywords=(),
                targets=(),
                max_notes=None,
                since=None,
                known=(),
            ):
                write_records(output_dir, "nowcoder", [NOWCODER_DISCUSS])
                return FetchResult("ok", output_dir)

        with mock.patch("gleaner.cli.fetcher_for", return_value=FakeFetcher()) as factory:
            code, out, err = self.call("crawl", "nowcoder", "--keyword", "后端 面经", "--max-notes", "5")
        self.assertEqual(code, 0, out + err)
        self.assertEqual(factory.call_args.args[1], "nowcoder")
        self.assertIn("翻阅最新面经列表", err)
        self.assertIn("最多约 5 篇", err)
        self.assertIn("新增 1", out)
        self.assertIn("不限发布时间", err)
        self.assertEqual(self.json("show", "nowcoder:800000001")["post"]["platform"], "nowcoder")

        with mock.patch("gleaner.cli.fetcher_for", return_value=FakeFetcher()):
            _, _, err = self.call("crawl", "nowcoder", "--keyword", "后端 面经", "--force")
            self.assertIn("增量：跳过已保存的帖子", err)
            _, _, err = self.call("crawl", "nowcoder", "--keyword", "后端 面经", "--force", "--full")
            self.assertIn("不限发布时间", err)

    def test_setup_rejects_unknown_parts(self):
        code, _, err = self.call("setup", "chrome")
        self.assertEqual(code, 1)
        self.assertIn("mediacrawler / bank / media", err)

    def test_media_downloads_flagged_recordings(self):
        self.call("init")
        source = write_records(self.workspace.parent / "bili-data", "bili", [BILI_VIDEO, BILI_RECORDING])
        self.json("import", str(source))
        listed = self.json("media", "--dry-run")
        self.assertEqual([post["key"] for post in listed["posts"]], ["bili:1150000002"])

        class FakeDownloader:
            def run(
                self,
                *,
                mode,
                output_dir,
                log_dir,
                keywords=(),
                targets=(),
                max_notes=None,
                since=None,
                known=(),
            ):
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

        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            main(["--workspace", str(self.workspace), "crawl", "bili", "--media"])

    def test_auto_dry_run_lists_what_it_would_do(self):
        self.call("init")
        source = write_records(self.workspace.parent / "bili-data", "bili", [BILI_VIDEO, BILI_RECORDING])
        self.json("import", str(source))
        plan = self.json("auto", "--dry-run", "--platform", "bili")
        self.assertEqual([row["platform"] for row in plan["platforms"]], ["bili"])
        self.assertEqual([post["key"] for post in plan["recordings"]], ["bili:1150000002"])
        self.assertEqual(plan["held_recordings"], 1)
        text = self.call("auto", "--dry-run", "--platform", "bili", "--no-media")[1]
        self.assertIn("抓取B站：不限发布时间", text)
        self.assertIn("下载录音：没有要下载的", text)
        self.assertIn("另有 1 个录音等下载", text)
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            main(["--workspace", str(self.workspace), "auto", "--platform", "weibo"])

    def test_auto_reports_each_step_and_exits_3_when_one_degraded(self):
        self.call("init")
        pack = PackResult("pack-1", Path("/tmp/packs/pack-1"), 3, 1, 1, 1, held_recordings=1)
        outcome = AutoOutcome(
            steps=[
                Step("crawl", "nowcoder", "ok", records=8, new=3),
                Step("crawl", "xhs", "degraded", "登录状态已过期，需要重新扫码"),
                Step("crawl", "dy", "skipped", "距离上次抓取不到 5 分钟，下次再抓"),
                Step("media", "bili", "ok", records=1, media=1, post="bili:1"),
                Step("pack", "", "ok", "pack-1：3 篇"),
            ],
            pack=pack,
            held_recordings=1,
        )
        with mock.patch("gleaner.cli.run_auto", return_value=outcome) as run:
            code, out, _ = self.call("auto", "--login", "--platform", "xhs", "--quiet")
        self.assertEqual(code, EXIT_DEGRADED)
        kwargs = run.call_args.kwargs
        self.assertEqual((kwargs["platforms"], kwargs["attended"], kwargs["pack"]), (["xhs"], True, True))
        for line in (
            "抓取牛客：完成；读到 8 条，新增 3",
            "抓取小红书：降级；读到 0 条，新增 0；登录状态已过期，需要重新扫码",
            "抓取抖音：跳过；距离上次抓取不到 5 分钟，下次再抓",
            "下载录音 bili:1：完成；下载到 1 个音视频",
            "素材包 pack-1：3 篇（看图 1，读正文 1，转写录音 1）",
            "还有 1 个录音没下载",
        ):
            self.assertIn(line, out)
        self.assertIn("HANDOFF.md", out)

        outcome.steps.pop(1)
        with mock.patch("gleaner.cli.run_auto", return_value=outcome) as run:
            code, out, _ = self.call("auto", "--no-pack", "--no-media", "--json")
        self.assertEqual(code, 0)
        self.assertTrue(json.loads(out)["ok"])
        self.assertEqual((run.call_args.kwargs["pack"], run.call_args.kwargs["download"]), (False, False))

    def test_marking_a_post_imported_keeps_it_out_of_packs_and_downloads(self):
        self.call("init")
        source = write_records(self.workspace.parent / "bili-data", "bili", [BILI_VIDEO, BILI_RECORDING])
        self.json("import", str(source))
        marked = self.json("imports", "--mark", "bili:1150000002")
        self.assertEqual(marked["changed"], {"manual": ["bili:1150000002"]})
        self.assertEqual(self.json("media", "--dry-run")["posts"], [])
        shown = self.json("show", "bili:1150000002")["post"]
        self.assertEqual((shown["disposition"], shown["packed_in"]), ("manual", None))
        self.assertNotIn(
            "bili:1150000002", [post["key"] for post in self.json("list", "--unpacked")["posts"]]
        )
        code, _, err = self.call("imports", "--mark", "bili:404")
        self.assertEqual(code, 1)
        self.assertIn("找不到帖子：bili:404", err)

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

    def test_doctor_checks_the_platforms_of_auto(self):
        self.call("init")
        checks = {check["name"]: check for check in json.loads(self.call("doctor", "--json")[1])["checks"]}
        self.assertEqual((checks["auto"]["ok"], checks["auto"]["detail"]), (True, "全部平台"))
        config = self.workspace / "gleaner.toml"
        config.write_text(
            config.read_text(encoding="utf-8").replace("platforms = []", 'platforms = ["bili", "weibo"]'),
            encoding="utf-8",
        )
        checks = {check["name"]: check for check in json.loads(self.call("doctor", "--json")[1])["checks"]}
        self.assertFalse(checks["auto"]["ok"])
        self.assertIn("不认识 weibo", checks["auto"]["detail"])

    def test_doctor_checks_that_ibank_offers_the_commands_gleaner_uses(self):
        import sys

        from gleaner.checks import IBANK_COMMANDS, ibank_commands_check
        from tests.support import make_config

        def check(commands):
            fake = self.workspace / f"ibank-{len(commands)}.py"
            fake.write_text(f"print('usage: ibank.py {{{','.join(commands)}}} ...')\n", encoding="utf-8")
            config = make_config(self.workspace, handoff={"ibank": str(fake), "python": sys.executable})
            return ibank_commands_check(config)

        self.assertTrue(check(list(IBANK_COMMANDS) + ["web"]).ok)
        old = check([name for name in IBANK_COMMANDS if name != "sources"])
        self.assertFalse(old.ok)
        self.assertIn("缺少 sources", old.detail)
        self.assertFalse(ibank_commands_check(make_config(self.workspace)).ok)

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
