import sys
import unittest
from pathlib import Path

from gleaner.config import MediaCrawlerConfig
from gleaner.sources.mediacrawler import (
    LOG_NAME,
    MediaCrawlerDriver,
    build_command,
    diagnose,
    search_notes_per_keyword,
)
from tests.support import DOUYIN_VIDEO, WorkspaceTestCase, write_export, write_records


class BuildCommandTest(unittest.TestCase):
    def test_search_is_small_and_skips_comments(self):
        command = build_command(
            MediaCrawlerConfig(),
            mode="search",
            output_dir=Path("/w/raw"),
            keywords=["后端 面经", "Java"],
            max_notes=15,
        )
        self.assertEqual(command[:3], ["uv", "run", "main.py"])
        pairs = dict(zip(command[3::2], command[4::2], strict=True))
        self.assertEqual(pairs["--platform"], "xhs")
        self.assertEqual(pairs["--type"], "search")
        self.assertEqual(pairs["--keywords"], "后端 面经,Java")
        self.assertEqual(pairs["--save_data_option"], "jsonl")
        self.assertEqual(pairs["--save_data_path"], str(Path("/w/raw")))
        self.assertEqual(pairs["--get_comment"], "no")
        self.assertEqual(pairs["--get_sub_comment"], "no")
        self.assertEqual(pairs["--get_media"], "yes")
        self.assertEqual(pairs["--crawler_max_notes_count"], "15")
        self.assertEqual(pairs["--max_concurrency_num"], "1")

    def test_search_fetches_whole_pages(self):
        self.assertEqual(search_notes_per_keyword(5), 20)
        self.assertEqual(search_notes_per_keyword(20), 20)
        self.assertEqual(search_notes_per_keyword(39), 20)
        self.assertEqual(search_notes_per_keyword(40), 40)
        self.assertEqual(search_notes_per_keyword(5, 10), 10)
        self.assertEqual(search_notes_per_keyword(35, 10), 30)

    def test_platforms_only_download_media_for_image_posts(self):
        def pairs(platform):
            command = build_command(
                MediaCrawlerConfig(),
                platform=platform,
                mode="search",
                output_dir=Path("/w"),
                keywords=["a"],
                max_notes=10,
            )
            return dict(zip(command[3::2], command[4::2], strict=True))

        self.assertEqual(pairs("dy")["--platform"], "dy")
        self.assertEqual(pairs("dy")["--get_media"], "yes")
        for platform in ("bili", "zhihu"):
            self.assertEqual(
                (pairs(platform)["--platform"], pairs(platform)["--get_media"]), (platform, "no")
            )
        with self.assertRaises(KeyError):
            pairs("nowcoder")
        with self.assertRaises(ValueError):
            MediaCrawlerDriver(MediaCrawlerConfig(), platform="nowcoder")

    def test_detail_uses_specified_ids(self):
        command = build_command(
            MediaCrawlerConfig(), mode="detail", output_dir=Path("/w"), targets=["aa01", "bb02"], max_notes=5
        )
        self.assertIn("--specified_id", command)
        self.assertEqual(command[command.index("--specified_id") + 1], "aa01,bb02")

    def test_requires_inputs(self):
        with self.assertRaises(ValueError):
            build_command(MediaCrawlerConfig(), mode="search", output_dir=Path("/w"), max_notes=1)
        with self.assertRaises(ValueError):
            build_command(MediaCrawlerConfig(), mode="creator", output_dir=Path("/w"), max_notes=1)


REJECTED_SEARCH_LOG = """\
2026-10-27 17:06:16 MediaCrawler INFO (core.py:156) - search Xiaohongshu keyword: AI Agent 面经, page: 1
Traceback (most recent call last):
  File "media_platform/xhs/client.py", line 196, in request
    raise DataFetchError(err_msg)
media_platform.xhs.exception.DataFetchError: 您当前登录的账号没有权限访问

The above exception was the direct cause of the following exception:

Traceback (most recent call last):
  File "tenacity/__init__.py", line 326, in iter
    raise retry_exc from fut.exception()
tenacity.RetryError: RetryError[<Future at 0x11efb1950 state=finished raised DataFetchError>]
"""


class DiagnoseTest(WorkspaceTestCase):
    def diagnose(self, text: str) -> str:
        path = self.workspace / LOG_NAME
        path.write_text(text, encoding="utf-8")
        return diagnose(path)

    def test_known_cause_beats_the_retry_wrapper(self):
        message = self.diagnose(REJECTED_SEARCH_LOG)
        self.assertIn("平台风控", message)
        self.assertIn("DataFetchError: 您当前登录的账号没有权限访问", message)
        self.assertNotIn("RetryError", message)

    def test_unknown_error_is_reported_verbatim(self):
        message = self.diagnose("Traceback:\nValueError: unexpected layout\n")
        self.assertEqual(message, "ValueError: unexpected layout")

    def test_bare_playwright_error_is_found(self):
        message = self.diagnose(
            "    raise rewrite_error(error, f\"{parsed_st['apiName']}: {error}\") from None\n"
            "playwright._impl._errors.Error: Page.evaluate: Execution context was destroyed, "
            "most likely because of a navigation\n"
        )
        self.assertIn("等待页面稳定的补丁", message)
        self.assertIn("Error: Page.evaluate: Execution context was destroyed", message)

    def test_falls_back_to_the_tail(self):
        self.assertEqual(self.diagnose("just a line\n"), "just a line")
        self.assertEqual(diagnose(self.workspace / "missing.log"), "没有日志")


class DriverTest(WorkspaceTestCase):
    def setUp(self):
        super().setUp()
        self.home = self.workspace / "MediaCrawler"
        self.home.mkdir()
        (self.home / "main.py").write_text("", encoding="utf-8")
        self.config = MediaCrawlerConfig(
            home=self.home, command=(sys.executable, "main.py"), timeout_seconds=60
        )
        self.raw = self.workspace / "runs" / "r1" / "raw"
        self.calls = []

    def driver(self, behaviour):
        def runner(command, cwd, timeout, log_path):
            self.calls.append((list(command), cwd, timeout))
            log_path.parent.mkdir(parents=True, exist_ok=True)
            log_path.write_text("扫码登录中…\n", encoding="utf-8")
            return behaviour()

        return MediaCrawlerDriver(self.config, runner)

    def run_driver(self, behaviour):
        return self.driver(behaviour).run(
            mode="search", output_dir=self.raw, log_dir=self.raw.parent, keywords=["后端 面经"]
        )

    def test_ok_when_records_are_produced(self):
        result = self.run_driver(lambda: write_export(self.raw) and 0)
        self.assertTrue(result.ok, result.message)
        self.assertEqual(self.calls[0][1], self.home)
        self.assertEqual(self.calls[0][2], 60)

    def test_nonzero_exit_is_degraded_with_log_tail(self):
        result = self.run_driver(lambda: 1)
        self.assertEqual(result.status, "degraded")
        self.assertEqual(result.returncode, 1)
        self.assertIn("扫码登录中", result.message)

    def test_clean_exit_without_output_is_degraded(self):
        result = self.run_driver(lambda: 0)
        self.assertEqual(result.status, "degraded")
        self.assertIn("没有产出数据", result.message)

    def test_output_is_checked_in_the_platform_directory(self):
        driver = MediaCrawlerDriver(self.config, lambda *args: write_export(self.raw) and 0, platform="dy")
        result = driver.run(mode="search", output_dir=self.raw, log_dir=self.raw.parent, keywords=["a"])
        self.assertIn("没有产出数据", result.message)
        driver = MediaCrawlerDriver(
            self.config, lambda *args: write_records(self.raw, "douyin", [DOUYIN_VIDEO]) and 0, platform="dy"
        )
        result = driver.run(mode="search", output_dir=self.raw, log_dir=self.raw.parent, keywords=["a"])
        self.assertTrue(result.ok, result.message)

    def test_timeout_is_degraded(self):
        def timeout():
            raise TimeoutError(60)

        result = self.run_driver(timeout)
        self.assertEqual(result.status, "degraded")
        self.assertIn("60 秒", result.message)

    def test_missing_checkout_never_runs(self):
        self.config = MediaCrawlerConfig(home=self.workspace / "nowhere")
        result = self.run_driver(lambda: 0)
        self.assertEqual(result.status, "degraded")
        self.assertEqual(self.calls, [])
        self.assertTrue(MediaCrawlerDriver(MediaCrawlerConfig()).problems())

    def test_default_runner_logs_output_and_exit_code(self):
        (self.home / "main.py").write_text(
            "import sys\nprint('hello', sys.argv[1:3])\nsys.exit(2)\n", encoding="utf-8"
        )
        driver = MediaCrawlerDriver(self.config, echo=False)
        result = driver.run(mode="search", output_dir=self.raw, log_dir=self.raw.parent, keywords=["k"])
        self.assertEqual(result.returncode, 2)
        self.assertIn("hello", (self.raw.parent / LOG_NAME).read_text(encoding="utf-8"))

    def test_default_runner_enforces_timeout(self):
        (self.home / "main.py").write_text("import time\ntime.sleep(30)\n", encoding="utf-8")
        driver = MediaCrawlerDriver(self.config, echo=False)
        runner = driver.runner
        with self.assertRaises(TimeoutError):
            runner([sys.executable, "main.py"], self.home, 1, self.raw.parent / LOG_NAME)


if __name__ == "__main__":
    unittest.main()
