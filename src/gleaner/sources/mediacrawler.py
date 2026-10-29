"""Drive a separately installed MediaCrawler checkout through its command line.

MediaCrawler is under a non-commercial learning license and is never vendored: Gleaner only runs the user's
own checkout. Verified against NanmiCoder/MediaCrawler main as of 2026-10:

- `main.py --platform xhs|dy|bili|ks|zhihu --type search|detail` with `--keywords` or `--specified_id`
  (comma separated; links or IDs as each platform's `config/*_config.py` shows).
- `--save_data_option jsonl --save_data_path <dir>` writes `<dir>/<data dir>/jsonl/<type>_contents_<date>.jsonl`
  (`adapters/exports.py` lists the data and media directory of each platform).
- `--get_media yes` downloads post images to `<dir>/<media dir>/media/<post id>/001.jpg …`; for videos it
  downloads a cover and the whole video.
- Search fetches whole result pages per keyword and raises a smaller `--crawler_max_notes_count` to one page
  (`media_platform/<platform>/core.py: search`): Douyin pages hold 10 posts, the others 20.

If MediaCrawler changes its command line or output layout, this module and `adapters/` change.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import threading
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

from gleaner.adapters.exports import content_files
from gleaner.config import MediaCrawlerConfig
from gleaner.sources.base import FetchResult

LOG_NAME = "mediacrawler.log"
MODES = ("search", "detail")
SEARCH_PAGE_SIZE = 20


@dataclass(frozen=True)
class CrawlerPlatform:
    code: str
    """Gleaner's platform code, which is also MediaCrawler's `--platform` value."""
    page_size: int
    get_media: bool
    """Only image posts need their media: a video would be downloaded whole and Gleaner reads only its text."""


PLATFORMS = {
    "xhs": CrawlerPlatform("xhs", SEARCH_PAGE_SIZE, get_media=True),
    "dy": CrawlerPlatform("dy", 10, get_media=True),
    "bili": CrawlerPlatform("bili", SEARCH_PAGE_SIZE, get_media=False),
    "ks": CrawlerPlatform("ks", SEARCH_PAGE_SIZE, get_media=False),
    "zhihu": CrawlerPlatform("zhihu", SEARCH_PAGE_SIZE, get_media=False),
}


def search_notes_per_keyword(max_notes: int, page_size: int = SEARCH_PAGE_SIZE) -> int:
    """How many posts MediaCrawler really fetches per keyword: whole pages, at least one."""
    return max(page_size, max_notes // page_size * page_size)


Runner = Callable[[Sequence[str], Path, int, Path], int]
"""(command, working directory, timeout in seconds, log file) -> exit code; raises TimeoutError."""


def build_command(
    config: MediaCrawlerConfig,
    *,
    platform: str = "xhs",
    mode: str,
    output_dir: Path,
    keywords: Sequence[str] = (),
    targets: Sequence[str] = (),
    max_notes: int,
) -> list[str]:
    if mode not in MODES:
        raise ValueError(f"unsupported mode: {mode}")
    spec = PLATFORMS[platform]
    command = [*config.command, "--platform", spec.code, "--lt", config.login_type, "--type", mode]
    if mode == "search":
        if not keywords:
            raise ValueError("search mode needs keywords")
        command += ["--keywords", ",".join(keywords)]
    else:
        if not targets:
            raise ValueError("detail mode needs note URLs or IDs")
        command += ["--specified_id", ",".join(targets)]
    return command + [
        "--save_data_option",
        "jsonl",
        "--save_data_path",
        str(output_dir),
        "--get_comment",
        "no",
        "--get_sub_comment",
        "no",
        "--get_media",
        "yes" if spec.get_media else "no",
        "--crawler_max_notes_count",
        str(max_notes),
        "--max_concurrency_num",
        "1",
    ]


class MediaCrawlerDriver:
    def __init__(
        self,
        config: MediaCrawlerConfig,
        runner: Runner | None = None,
        *,
        platform: str = "xhs",
        echo: bool = True,
    ):
        if platform not in PLATFORMS:
            raise ValueError(f"MediaCrawler does not crawl {platform}")
        self.config = config
        self.platform = platform
        self.runner = runner or make_runner(echo=echo)

    def problems(self) -> list[str]:
        home = self.config.home
        if home is None:
            return ["没有配置 mediacrawler.home（你本机 MediaCrawler 的目录）"]
        if not home.is_dir():
            return [f"MediaCrawler 目录不存在：{home}"]
        found = []
        if not (home / "main.py").is_file():
            found.append(f"{home} 下没有 main.py，确认这是 MediaCrawler 的根目录")
        executable = self.config.command[0]
        if shutil.which(executable) is None and not (home / executable).is_file():
            found.append(f"找不到启动命令 {executable}（mediacrawler.command 的第一项）")
        return found

    def run(
        self,
        *,
        mode: str,
        output_dir: Path,
        log_dir: Path,
        keywords: Sequence[str] = (),
        targets: Sequence[str] = (),
        max_notes: int | None = None,
    ) -> FetchResult:
        problems = self.problems()
        if problems:
            return FetchResult("degraded", output_dir, "；".join(problems))
        command = build_command(
            self.config,
            platform=self.platform,
            mode=mode,
            output_dir=output_dir,
            keywords=keywords,
            targets=targets,
            max_notes=max_notes or self.config.max_notes,
        )
        output_dir.mkdir(parents=True, exist_ok=True)
        log_path = log_dir / LOG_NAME
        try:
            returncode = self.runner(command, self.config.home, self.config.timeout_seconds, log_path)
        except TimeoutError:
            return FetchResult(
                "degraded",
                output_dir,
                f"MediaCrawler 超过 {self.config.timeout_seconds} 秒没有结束，可能卡在登录或滑块验证；"
                f"已产出的数据仍会导入。日志：{log_path}",
            )
        except OSError as exc:
            return FetchResult("degraded", output_dir, f"无法启动 MediaCrawler：{exc}")

        produced = bool(content_files(output_dir, self.platform))
        if returncode != 0:
            return FetchResult(
                "degraded",
                output_dir,
                f"MediaCrawler 退出码 {returncode}：{diagnose(log_path)}。完整日志：{log_path}",
                returncode,
            )
        if not produced:
            return FetchResult(
                "degraded",
                output_dir,
                f"MediaCrawler 正常退出但没有产出数据：可能没有搜索结果，或登录状态失效。日志：{log_path}",
                returncode,
            )
        return FetchResult("ok", output_dir, "", returncode)


def make_runner(
    *,
    echo: bool = False,
    on_line: Callable[[str], None] | None = None,
    on_start: Callable[[subprocess.Popen], None] | None = None,
) -> Runner:
    """Run MediaCrawler, teeing its output to the log file, the terminal (echo) and an optional callback.

    on_start receives the process so a caller can terminate it; the run then ends like any failed run.
    """

    def run(command: Sequence[str], cwd: Path, timeout: int, log_path: Path) -> int:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        env = {**os.environ, "PYTHONUNBUFFERED": "1", "PYTHONIOENCODING": "utf-8"}
        with (
            log_path.open("w", encoding="utf-8") as log,
            subprocess.Popen(
                list(command),
                cwd=cwd,
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
            ) as process,
        ):
            timed_out = threading.Event()

            def kill() -> None:
                timed_out.set()
                process.kill()

            if on_start:
                on_start(process)
            timer = threading.Timer(timeout, kill)
            timer.start()
            try:
                assert process.stdout is not None
                for line in process.stdout:
                    log.write(line)
                    if echo:
                        sys.stderr.write(line)
                    if on_line:
                        on_line(line.rstrip("\n"))
                returncode = process.wait()
            finally:
                timer.cancel()
        if timed_out.is_set():
            raise TimeoutError(timeout)
        return returncode

    return run


_ERROR_LINE = re.compile(r"\b(\w*(?:Error|Exception)): (.+)$")
_WRAPPER_ERRORS = {"RetryError", "CancelledError"}
_HINTS = (
    (
        "没有权限访问",
        "小红书拒绝了这个账号的请求（平台风控，code -104）。可以先在 MediaCrawler 打开的浏览器窗口里正常浏览、"
        "手动搜索一次，看是否需要验证；或者隔一段时间再试，不要提高频率",
    ),
    ("请通过验证", "触发了平台验证，需要在浏览器窗口里手动完成后再试"),
    ("Missing 'a1'", "浏览器里没有小红书的登录状态，需要重新扫码登录"),
    (
        "Execution context was destroyed",
        "MediaCrawler 打开首页后页面又跳转了一次，读取页面时出错（抖音常见）。"
        "按 docs/mediacrawler.md「常见问题」给 MediaCrawler 加上等待页面稳定的补丁后再试",
    ),
)


def diagnose(path: Path) -> str:
    """The most useful line of a MediaCrawler log: a known cause, else the innermost error, else the tail."""
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return "没有日志"
    errors = [
        f"{match.group(1)}: {match.group(2).strip()}"
        for line in text.splitlines()
        if (match := _ERROR_LINE.search(line)) and match.group(1) not in _WRAPPER_ERRORS
    ]
    last_error = errors[-1] if errors else ""
    for marker, hint in _HINTS:
        if marker in last_error:
            return f"{hint}。原始错误：{last_error}"
    if last_error:
        return last_error
    return text[-300:].strip() or "日志为空"
