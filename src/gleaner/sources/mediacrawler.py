"""Drive a separately installed MediaCrawler checkout through its command line.

MediaCrawler is under a non-commercial learning license and is never vendored: Gleaner only runs the user's
own checkout. Verified against NanmiCoder/MediaCrawler main as of 2026-10:

- `main.py --platform xhs --type search|detail` with `--keywords` or `--specified_id` (comma separated).
- `--save_data_option jsonl --save_data_path <dir>` writes `<dir>/xhs/jsonl/<type>_contents_<date>.jsonl`.
- `--get_media yes` downloads note images to `<dir>/xhs/media/<note_id>/001.jpg …` (videos: cover + video).
- Search fetches whole result pages of 20 notes per keyword and raises a smaller
  `--crawler_max_notes_count` to 20 (`media_platform/xhs/core.py: search`).

If MediaCrawler changes its command line or output layout, this module and `adapters/xhs.py` change.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import threading
from collections.abc import Callable, Sequence
from pathlib import Path

from gleaner.adapters.xhs import content_files
from gleaner.config import MediaCrawlerConfig
from gleaner.sources.base import FetchResult

LOG_NAME = "mediacrawler.log"
MODES = ("search", "detail")
SEARCH_PAGE_SIZE = 20


def search_notes_per_keyword(max_notes: int) -> int:
    """How many notes MediaCrawler really fetches per keyword: whole pages, at least one."""
    return max(SEARCH_PAGE_SIZE, max_notes // SEARCH_PAGE_SIZE * SEARCH_PAGE_SIZE)


Runner = Callable[[Sequence[str], Path, int, Path], int]
"""(command, working directory, timeout in seconds, log file) -> exit code; raises TimeoutError."""


def build_command(
    config: MediaCrawlerConfig,
    *,
    mode: str,
    output_dir: Path,
    keywords: Sequence[str] = (),
    targets: Sequence[str] = (),
    max_notes: int,
) -> list[str]:
    if mode not in MODES:
        raise ValueError(f"unsupported mode: {mode}")
    command = [*config.command, "--platform", "xhs", "--lt", config.login_type, "--type", mode]
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
        "yes",
        "--crawler_max_notes_count",
        str(max_notes),
        "--max_concurrency_num",
        "1",
    ]


class MediaCrawlerDriver:
    def __init__(self, config: MediaCrawlerConfig, runner: Runner | None = None, *, echo: bool = True):
        self.config = config
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

        produced = bool(content_files(output_dir))
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


_ERROR_LINE = re.compile(r"\b(\w+(?:Error|Exception)): (.+)$")
_WRAPPER_ERRORS = {"RetryError", "CancelledError"}
_HINTS = (
    (
        "没有权限访问",
        "小红书拒绝了这个账号的请求（平台风控，code -104）。可以先在 MediaCrawler 打开的浏览器窗口里正常浏览、"
        "手动搜索一次，看是否需要验证；或者隔一段时间再试，不要提高频率",
    ),
    ("请通过验证", "触发了平台验证，需要在浏览器窗口里手动完成后再试"),
    ("Missing 'a1'", "浏览器里没有小红书的登录状态，需要重新扫码登录"),
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
