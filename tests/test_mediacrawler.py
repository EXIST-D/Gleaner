import os
import sys
import time
import unittest
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

from gleaner.config import MediaConfig, MediaCrawlerConfig
from gleaner.pipeline import import_export
from gleaner.sources.mediacrawler import (
    LOG_NAME,
    LoginRequired,
    MediaCrawlerDriver,
    awaiting_download,
    build_command,
    diagnose,
    is_login_prompt,
    make_runner,
    search_notes_per_keyword,
    search_window,
    tool_dirs,
)
from gleaner.store import Store
from gleaner.timeutil import utc_now
from tests.support import (
    BILI_RECORDING,
    BILI_VIDEO,
    DOUYIN_VIDEO,
    NOW,
    WorkspaceTestCase,
    fake_tool,
    make_config,
    write_export,
    write_records,
)


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

    def test_bilibili_details_use_bv_links_and_may_download_the_video(self):
        def pairs(**kwargs):
            command = build_command(
                MediaCrawlerConfig(),
                platform="bili",
                mode="detail",
                output_dir=Path("/w"),
                targets=["https://www.bilibili.com/video/av170001", "BV1xx411c7mD"],
                max_notes=2,
                **kwargs,
            )
            return dict(zip(command[3::2], command[4::2], strict=True))

        self.assertEqual(
            pairs()["--specified_id"], "https://www.bilibili.com/video/BV17x411w7KC,BV1xx411c7mD"
        )
        self.assertEqual(pairs()["--get_media"], "no")
        self.assertEqual(pairs(media=True)["--get_media"], "yes")

    def test_only_bilibili_details_download_recordings(self):
        for platform, mode in (("bili", "search"), ("dy", "detail")):
            with self.subTest(platform=platform, mode=mode), self.assertRaises(ValueError):
                build_command(
                    MediaCrawlerConfig(),
                    platform=platform,
                    mode=mode,
                    output_dir=Path("/w"),
                    keywords=["a"],
                    targets=["1"],
                    max_notes=1,
                    media=True,
                )
        with self.assertRaises(ValueError):
            MediaCrawlerDriver(MediaCrawlerConfig(), platform="xhs", media=MediaConfig())


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
        def runner(command, cwd, timeout, log_path, env):
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

    def test_recordings_need_ffmpeg_and_get_the_longer_timeout(self):
        media = MediaConfig(ffmpeg=str(self.workspace / "bin" / "ffmpeg"), download_timeout_seconds=1800)
        driver = MediaCrawlerDriver(self.config, lambda *args: 0, platform="bili", media=media)
        self.assertIn("找不到 ffmpeg", "".join(driver.problems()))

        ffmpeg = fake_tool(self.workspace / "bin", "ffmpeg")
        media = MediaConfig(ffmpeg=str(ffmpeg), download_timeout_seconds=1800)

        def runner(command, cwd, timeout, log_path, env):
            self.calls.append((list(command), cwd, timeout))
            write_records(self.raw, "bili", [BILI_RECORDING], mode="detail")
            return 0

        driver = MediaCrawlerDriver(self.config, runner, platform="bili", media=media)
        self.assertEqual(driver.problems(), [])
        result = driver.run(
            mode="detail", output_dir=self.raw, log_dir=self.raw.parent, targets=["1150000002"]
        )
        self.assertTrue(result.ok, result.message)
        command, _, timeout = self.calls[0]
        self.assertEqual(timeout, 1800)
        self.assertEqual(command[command.index("--get_media") + 1], "yes")
        self.assertEqual(tool_dirs(str(ffmpeg)), [ffmpeg.parent])

        renamed = fake_tool(self.workspace / "bin", "ffmpeg-7.1")
        driver = MediaCrawlerDriver(
            self.config, runner, platform="bili", media=MediaConfig(ffmpeg=str(renamed))
        )
        self.assertIn("名为 ffmpeg 的链接", "".join(driver.problems()))

    def test_default_runner_puts_tool_dirs_first_on_path(self):
        (self.home / "main.py").write_text(
            "import os\nprint(os.environ['PATH'])\nprint(os.environ['GLEANER_DY_PUBLISH_TIME'])\n",
            encoding="utf-8",
        )
        tools = self.workspace / "tools"
        runner = make_runner(path_dirs=[tools])
        log = self.raw.parent / LOG_NAME
        self.assertEqual(
            runner([sys.executable, "main.py"], self.home, 30, log, {"GLEANER_DY_PUBLISH_TIME": "7"}), 0
        )
        lines = log.read_text(encoding="utf-8").splitlines()
        self.assertTrue(lines[0].startswith(str(tools) + os.pathsep))
        self.assertEqual(lines[1], "7")

    def test_default_runner_enforces_timeout(self):
        (self.home / "main.py").write_text("import time\ntime.sleep(30)\n", encoding="utf-8")
        driver = MediaCrawlerDriver(self.config, echo=False)
        runner = driver.runner
        with self.assertRaises(TimeoutError):
            runner([sys.executable, "main.py"], self.home, 1, self.raw.parent / LOG_NAME, {})

    def test_unattended_run_stops_at_the_qr_code_prompt(self):
        (self.home / "main.py").write_text(
            "import time\n"
            "print('Begin login douyin by qrcode ...', flush=True)\n"
            "print('waiting for scan code login, remaining time is 20s', flush=True)\n"
            "time.sleep(60)\n",
            encoding="utf-8",
        )
        driver = MediaCrawlerDriver(self.config, echo=False, unattended=True, platform="dy")
        started = time.monotonic()
        result = driver.run(mode="search", output_dir=self.raw, log_dir=self.raw.parent, keywords=["k"])
        self.assertLess(time.monotonic() - started, 30, "it must not wait for the scan or the timeout")
        self.assertEqual(result.status, "degraded")
        self.assertIn("登录状态已过期", result.message)
        self.assertIn("gleaner crawl dy", result.message)
        self.assertIn("by qrcode", (self.raw.parent / LOG_NAME).read_text(encoding="utf-8"))

    def test_attended_run_does_not_stop_at_the_prompt(self):
        (self.home / "main.py").write_text(
            "print('waiting for scan code login, remaining time is 20s')\nprint('Login successful')\n",
            encoding="utf-8",
        )
        runner = MediaCrawlerDriver(self.config, echo=False).runner
        self.assertEqual(
            runner([sys.executable, "main.py"], self.home, 30, self.raw.parent / LOG_NAME, {}), 0
        )

    def test_stop_when_is_checked_on_every_line_and_raises_login_required(self):
        (self.home / "main.py").write_text(
            "print('one')\nprint('stop here')\nprint('three')\n", encoding="utf-8"
        )
        runner = make_runner(stop_when=lambda line: "stop" in line)
        with self.assertRaises(LoginRequired):
            runner([sys.executable, "main.py"], self.home, 30, self.raw.parent / LOG_NAME, {})

    def test_incremental_search_passes_the_time_range(self):
        envs = []

        def runner(command, cwd, timeout, log_path, env):
            envs.append(dict(env))
            write_records(self.raw, "douyin", [DOUYIN_VIDEO])
            return 0

        driver = MediaCrawlerDriver(self.config, runner, platform="dy")
        since = utc_now() - timedelta(days=3)
        driver.run(mode="search", output_dir=self.raw, log_dir=self.raw.parent, keywords=["a"], since=since)
        driver.run(mode="search", output_dir=self.raw, log_dir=self.raw.parent, keywords=["a"])
        driver.run(mode="detail", output_dir=self.raw, log_dir=self.raw.parent, targets=["1"], since=since)
        self.assertEqual(envs, [{"GLEANER_DY_PUBLISH_TIME": "7"}, {}, {}])


class LoginPromptTest(unittest.TestCase):
    def test_only_the_qr_code_prompts_count(self):
        for line in (
            "waiting for scan code login, remaining time is 20s",
            "2026-10-29 INFO - [DouYinLogin.begin] Begin login douyin by qrcode ...",
        ):
            self.assertTrue(is_login_prompt(line), line)
        for line in (
            "Use cache login state get web interface successfull!",
            "Begin login xiaohongshu by cookie",
            "Login successful",
            "title:面试官问：二维码登录怎么实现",
        ):
            self.assertFalse(is_login_prompt(line), line)


class AwaitingDownloadTest(WorkspaceTestCase):
    def test_only_flagged_bilibili_videos_without_a_download(self):
        config = make_config(self.workspace)
        source = write_records(self.workspace / "external", "bili", [BILI_RECORDING, BILI_VIDEO])
        write_records(source, "douyin", [DOUYIN_VIDEO])
        with Store.open(self.workspace, create=True) as store:
            import_export(store, config, source, now=NOW)
            flagged = {record.post.key: awaiting_download(record) for record in store.posts()}
            self.assertEqual(flagged["bili:1150000002"], True)
            self.assertEqual(flagged["bili:1150000001"], False, "not flagged as a recording")
            self.assertEqual(flagged["dy:7300000000000000002"], False, "Douyin recordings are not downloaded")

            record = store.get("bili:1150000002")
            downloaded = replace(record, post=replace(record.post, media="media/video.mp4"))
            self.assertFalse(awaiting_download(downloaded))


class SearchWindowTest(unittest.TestCase):
    NOW = datetime(2026, 10, 30, 2, 0, tzinfo=UTC)

    def window(self, platform, days):
        return search_window(platform, self.NOW - timedelta(days=days), self.NOW)

    def test_douyin_and_zhihu_pick_the_narrowest_range_that_reaches_back(self):
        self.assertEqual(self.window("dy", 0.5).env, {"GLEANER_DY_PUBLISH_TIME": "1"})
        self.assertEqual(self.window("dy", 1.5).env, {"GLEANER_DY_PUBLISH_TIME": "7"})
        self.assertEqual(self.window("dy", 30).text, "半年内")
        self.assertIsNone(self.window("dy", 200), "longer than half a year: any time")
        self.assertEqual(self.window("zhihu", 20).env, {"GLEANER_ZHIHU_SEARCH_TIME": "a_month"})
        self.assertEqual(self.window("zhihu", 300).env, {"GLEANER_ZHIHU_SEARCH_TIME": "a_year"})
        self.assertIsNone(self.window("zhihu", 400))

    def test_bilibili_takes_exact_timestamps_and_xiaohongshu_has_no_range(self):
        window = self.window("bili", 2)
        self.assertEqual(
            window.env["GLEANER_BILI_PUBTIME_BEGIN"], str(int((self.NOW - timedelta(days=2)).timestamp()))
        )
        self.assertEqual(
            window.env["GLEANER_BILI_PUBTIME_END"], str(int((self.NOW + timedelta(days=1)).timestamp()))
        )
        self.assertEqual(window.text, " 2026-10-28 10:00 之后")
        self.assertIsNone(self.window("xhs", 2))


if __name__ == "__main__":
    unittest.main()
