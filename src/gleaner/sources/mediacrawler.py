"""Drive a separately installed MediaCrawler checkout through its command line.

MediaCrawler is under a non-commercial learning license and is never vendored: Gleaner only runs the user's
own checkout. Verified against NanmiCoder/MediaCrawler main as of 2026-10:

- `main.py --platform xhs|dy|bili|zhihu --type search|detail` with `--keywords` or `--specified_id`
  (comma separated; links or IDs as each platform's `config/*_config.py` shows).
- `--save_data_option jsonl --save_data_path <dir>` writes `<dir>/<data dir>/jsonl/<type>_contents_<date>.jsonl`
  (`adapters/exports.py` lists the data and media directory of each platform).
- `--get_media yes` downloads post images to `<dir>/<media dir>/media/<post id>/001.jpg …`; for videos it
  downloads a cover and the whole video. Bilibili serves audio and video separately and MediaCrawler merges
  them with the `ffmpeg` it finds on PATH (`media_downloader/ffmpeg.py`); without one it falls back to a
  low-quality stream. Bilibili media lands in `media/<BV ID>/`.
- Bilibili detail mode only parses BV links (`media_platform/bilibili/help.py`), so av links are converted.
- Search fetches whole result pages per keyword and raises a smaller `--crawler_max_notes_count` to one page
  (`media_platform/<platform>/core.py: search`): Douyin pages hold 10 posts, the others 20.
- With Gleaner's patch `04-search-time-range.patch`, searches read a publish time range from `GLEANER_*`
  environment variables: Douyin and Zhihu offer fixed ranges (a day, a week, …), Bilibili takes timestamps.
  Xiaohongshu has no range; its search is sorted by time (`01-config.patch`). Unpatched, the variables
  are ignored and searches cover any time.

If MediaCrawler changes its command line or output layout, this module and `adapters/` change.
"""

from __future__ import annotations

import math
import os
import re
import shutil
import subprocess
import sys
import threading
from collections.abc import Callable, Collection, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path

from gleaner.adapters.exports import content_files
from gleaner.audio import find_ffmpeg
from gleaner.config import MediaConfig, MediaCrawlerConfig
from gleaner.models import PostRecord
from gleaner.screening import is_recording
from gleaner.sources.base import FetchResult
from gleaner.timeutil import CHINA_TZ, utc_now
from gleaner.urls import bilibili_detail_target

LOG_NAME = "mediacrawler.log"
MODES = ("search", "detail")
SEARCH_PAGE_SIZE = 20
_QR_COUNTDOWN = re.compile(r"waiting for scan code login, remaining time is \d+s", re.IGNORECASE)


class LoginRequired(Exception):
    """An unattended run stopped because MediaCrawler asked for a QR code scan."""


def is_login_prompt(line: str) -> bool:
    """MediaCrawler logs this only when the saved login is missing or expired and it wants a QR code scan."""
    return bool(_QR_COUNTDOWN.search(line)) or ("Begin login" in line and "by qrcode" in line)


@dataclass(frozen=True)
class CrawlerPlatform:
    code: str
    """Gleaner's platform code, which is also MediaCrawler's `--platform` value."""
    page_size: int
    get_media: bool
    """Only image posts need their media: a video would be downloaded whole and Gleaner reads only its text."""
    recordings: bool = False
    """Whether selected videos may be downloaded on request, to transcribe interview recordings."""


PLATFORMS = {
    "xhs": CrawlerPlatform("xhs", SEARCH_PAGE_SIZE, get_media=True),
    "dy": CrawlerPlatform("dy", 10, get_media=True),
    "bili": CrawlerPlatform("bili", SEARCH_PAGE_SIZE, get_media=False, recordings=True),
    "zhihu": CrawlerPlatform("zhihu", SEARCH_PAGE_SIZE, get_media=False),
}


def awaiting_download(record: PostRecord) -> bool:
    """A video screening flagged as an interview recording whose audio and video are not downloaded yet."""
    spec = PLATFORMS.get(record.post.platform)
    return bool(spec and spec.recordings and not record.post.media and is_recording(record.label_reasons))


def tool_dirs(ffmpeg: str) -> list[Path]:
    """Directories to put on MediaCrawler's PATH so it finds the configured ffmpeg."""
    found = find_ffmpeg(ffmpeg)
    return [Path(found).parent] if found else []


def search_notes_per_keyword(max_notes: int, page_size: int = SEARCH_PAGE_SIZE) -> int:
    """How many posts MediaCrawler really fetches per keyword: whole pages, at least one."""
    return max(page_size, max_notes // page_size * page_size)


@dataclass(frozen=True)
class SearchWindow:
    text: str
    """What the platform is asked for, e.g. 一周内."""
    env: dict[str, str] = field(default_factory=dict)


# (longest span in days, value, text), narrowest first: the platforms only offer these ranges.
_DOUYIN_RANGES = ((1, "1", "一天内"), (7, "7", "一周内"), (180, "180", "半年内"))
_ZHIHU_RANGES = (
    (1, "a_day", "一天内"),
    (7, "a_week", "一周内"),
    (31, "a_month", "一个月内"),
    (92, "three_months", "三个月内"),
    (183, "half_a_year", "半年内"),
    (366, "a_year", "一年内"),
)


def search_window(platform: str, since: datetime, now: datetime) -> SearchWindow | None:
    """The narrowest publish time range of the platform's search that still reaches back to since.

    None when the platform cannot narrow its search (Xiaohongshu) or the span is longer than its ranges.
    """
    days = max(1, math.ceil((now - since) / timedelta(days=1)))
    if platform == "bili":
        begin = int(since.timestamp())
        end = int((now + timedelta(days=1)).timestamp())
        return SearchWindow(
            f" {since.astimezone(CHINA_TZ):%Y-%m-%d %H:%M} 之后",
            {"GLEANER_BILI_PUBTIME_BEGIN": str(begin), "GLEANER_BILI_PUBTIME_END": str(end)},
        )
    ranges, variable = {
        "dy": (_DOUYIN_RANGES, "GLEANER_DY_PUBLISH_TIME"),
        "zhihu": (_ZHIHU_RANGES, "GLEANER_ZHIHU_SEARCH_TIME"),
    }.get(platform, ((), ""))
    for span, value, text in ranges:
        if days <= span:
            return SearchWindow(text, {variable: value})
    return None


Runner = Callable[[Sequence[str], Path, int, Path, Mapping[str, str]], int]
"""(command, working directory, timeout in seconds, log file, extra environment) -> exit code;
raises TimeoutError, or LoginRequired when an unattended run was stopped at a login prompt."""


def build_command(
    config: MediaCrawlerConfig,
    *,
    platform: str = "xhs",
    mode: str,
    output_dir: Path,
    keywords: Sequence[str] = (),
    targets: Sequence[str] = (),
    max_notes: int,
    media: bool = False,
) -> list[str]:
    if mode not in MODES:
        raise ValueError(f"unsupported mode: {mode}")
    spec = PLATFORMS[platform]
    if media and not (spec.recordings and mode == "detail"):
        raise ValueError(f"{platform} {mode} does not download recordings")
    command = [*config.command, "--platform", spec.code, "--lt", config.login_type, "--type", mode]
    if mode == "search":
        if not keywords:
            raise ValueError("search mode needs keywords")
        command += ["--keywords", ",".join(keywords)]
    else:
        if not targets:
            raise ValueError("detail mode needs note URLs or IDs")
        if platform == "bili":
            targets = [bilibili_detail_target(target) for target in targets]
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
        "yes" if spec.get_media or media else "no",
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
        media: MediaConfig | None = None,
        unattended: bool = False,
    ):
        """With `media`, detail runs also download the videos (Bilibili recordings only).

        An unattended run stops as soon as MediaCrawler asks for a QR code scan instead of waiting for someone.
        """
        if platform not in PLATFORMS:
            raise ValueError(f"MediaCrawler does not crawl {platform}")
        if media is not None and not PLATFORMS[platform].recordings:
            raise ValueError(f"MediaCrawler recordings are not supported for {platform}")
        self.config = config
        self.platform = platform
        self.media = media
        self.runner = runner or make_runner(
            echo=echo,
            path_dirs=tool_dirs(media.ffmpeg) if media else (),
            stop_when=is_login_prompt if unattended else None,
        )

    @property
    def timeout_seconds(self) -> int:
        if self.media is None:
            return self.config.timeout_seconds
        return max(self.config.timeout_seconds, self.media.download_timeout_seconds)

    def problems(self) -> list[str]:
        found = self._home_problems()
        if self.media is not None:
            ffmpeg = find_ffmpeg(self.media.ffmpeg)
            if ffmpeg is None:
                found.append(
                    f"找不到 ffmpeg（{self.media.ffmpeg}）：B 站的音频和画面分开下载，需要它合成；"
                    "装好后在 gleaner.toml 的 [media] ffmpeg 写上路径"
                )
            elif Path(ffmpeg).stem.lower() != "ffmpeg":
                found.append(
                    f"MediaCrawler 只认名叫 ffmpeg 的程序，请给 {ffmpeg} 建一个名为 ffmpeg 的链接再配置"
                )
        return found

    def _home_problems(self) -> list[str]:
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
        since: datetime | None = None,
        known: Collection[str] = (),
    ) -> FetchResult:
        """since narrows a search to posts published after it where the platform can; MediaCrawler fetches
        every result itself, so known posts cannot be skipped."""
        problems = self.problems()
        if problems:
            return FetchResult("degraded", output_dir, "；".join(problems))
        window = search_window(self.platform, since, utc_now()) if since and mode == "search" else None
        command = build_command(
            self.config,
            platform=self.platform,
            mode=mode,
            output_dir=output_dir,
            keywords=keywords,
            targets=targets,
            max_notes=max_notes or self.config.max_notes,
            media=self.media is not None,
        )
        output_dir.mkdir(parents=True, exist_ok=True)
        log_path = log_dir / LOG_NAME
        timeout = self.timeout_seconds
        try:
            returncode = self.runner(
                command, self.config.home, timeout, log_path, window.env if window else {}
            )
        except TimeoutError:
            cause = "视频下载太慢" if self.media is not None else "可能卡在登录或滑块验证"
            return FetchResult(
                "degraded",
                output_dir,
                f"MediaCrawler 超过 {timeout} 秒没有结束，{cause}；已产出的数据仍会导入。日志：{log_path}",
            )
        except LoginRequired:
            return FetchResult(
                "degraded",
                output_dir,
                f"登录状态已过期，需要重新扫码；自动运行不会等待扫码。先手动运行 gleaner crawl {self.platform} "
                f"扫一次码，登录状态保存在 MediaCrawler 里，下次自动运行就能用。日志：{log_path}",
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
    path_dirs: Sequence[Path] = (),
    stop_when: Callable[[str], bool] | None = None,
) -> Runner:
    """Run MediaCrawler, teeing its output to the log file, the terminal (echo) and an optional callback.

    on_start receives the process so a caller can terminate it; the run then ends like any failed run.
    path_dirs go in front of PATH, so MediaCrawler finds tools such as ffmpeg.
    stop_when terminates the process at the first output line it accepts; the runner then raises LoginRequired.
    """

    def run(command: Sequence[str], cwd: Path, timeout: int, log_path: Path, extra: Mapping[str, str]) -> int:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        env = {**os.environ, **extra, "PYTHONUNBUFFERED": "1", "PYTHONIOENCODING": "utf-8"}
        if path_dirs:
            env["PATH"] = os.pathsep.join([*(str(path) for path in path_dirs), env.get("PATH", "")])
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
            stopped = False

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
                    if stop_when and stop_when(line):
                        # Stop reading too: a browser MediaCrawler started may keep the pipe open after it exits.
                        stopped = True
                        process.terminate()
                        break
                returncode = process.wait()
            finally:
                timer.cancel()
        if timed_out.is_set():
            raise TimeoutError(timeout)
        if stopped:
            raise LoginRequired
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
        "运行 gleaner setup mediacrawler 打上等待页面稳定的补丁后再试",
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
