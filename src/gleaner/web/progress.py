"""Turn crawler log lines into a step and a call to action for the person watching.

MediaCrawler markers follow its main branch as of 2026-10 (`media_platform/<platform>/login.py`, `core.py`,
`media_downloader/downloader.py`, `store/<platform>/__init__.py`); the Nowcoder markers are Gleaner's own
(`sources/nowcoder.py`). Unknown lines are simply shown in the log.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from gleaner.models import PLATFORMS

STEP_TITLES = {
    "browser": "启动浏览器",
    "login": "登录",
    "search": "搜索帖子",
    "fetch": "读取帖子",
    "download": "下载图片",
    "import": "导入与初筛",
    "done": "完成",
}
_NOWCODER_TITLES = {"search": "翻阅面经列表"}
_APPS = {"xhs": "小红书", "dy": "抖音", "bili": "哔哩哔哩", "ks": "快手", "zhihu": "知乎"}

_QR_COUNTDOWN = re.compile(r"waiting for scan code login, remaining time is \d+s", re.IGNORECASE)
# Every platform's check_login_state retries 600 times a second apart; the "remaining time" in the log is fixed text.
LOGIN_WAIT_SECONDS = 600
_LOGGED_IN = ("Login successful", "Login status confirmed", "Login state result: True")
_VERIFY = ("请通过验证", "验证码", "captcha", "slider", "滑块")
_SEARCHING = re.compile(r"Begin search|Current (?:search )?keyword|search \w+ keyword", re.IGNORECASE)
_STORED = re.compile(r"update_(?:xhs_note|douyin_aweme|bilibili_video|kuaishou_video|zhihu_content)\]")


def steps_for(platform: str, *, download: bool = True) -> tuple[str, ...]:
    """The steps a crawl of the platform goes through; only image posts' media is downloaded."""
    if platform == "nowcoder":
        return ("search", "fetch", *(("download",) if download else ()), "import", "done")
    with_media = platform in ("xhs", "dy")
    return ("browser", "login", "search", *(("download",) if with_media else ()), "import", "done")


def step_titles(platform: str) -> dict[str, str]:
    return {**STEP_TITLES, **(_NOWCODER_TITLES if platform == "nowcoder" else {})}


def attention_titles(platform: str) -> dict[str, str]:
    app = _APPS.get(platform, PLATFORMS[platform].label if platform in PLATFORMS else platform)
    return {
        "qr": f"请用手机{app}扫描弹出的 Chrome 窗口里的二维码",
        "verify": f"{app}要求验证，请在弹出的 Chrome 窗口里完成",
        "confirm": "Chrome 弹出了连接确认框，请在 60 秒内点“接受”",
        "throttled": "牛客要求放慢请求，正在等待后重试",
    }


@dataclass
class Progress:
    platform: str = "xhs"
    download: bool = True
    steps: tuple[str, ...] = field(init=False)
    step: str = field(init=False)
    attention: str | None = None
    qr_seconds: int | None = None
    notes: int = 0
    media: int = 0

    def __post_init__(self) -> None:
        self.steps = steps_for(self.platform, download=self.download)
        self.step = self.steps[0]

    def feed(self, line: str) -> None:
        if self.platform == "nowcoder":
            self._feed_nowcoder(line)
        else:
            self._feed_mediacrawler(line)

    def _feed_mediacrawler(self, line: str) -> None:
        if _QR_COUNTDOWN.search(line) or ("Begin login" in line and "by qrcode" in line):
            self._advance("login")
            self.attention = "qr"
            self.qr_seconds = self.qr_seconds or LOGIN_WAIT_SECONDS
        elif "Begin login" in line:
            self._advance("login")
        elif any(marker in line for marker in _LOGGED_IN):
            self.attention, self.qr_seconds = None, None
            self._advance("search")
        elif "confirmation dialog" in line:
            self.attention = "confirm"
        elif any(marker in line for marker in _VERIFY):
            self.attention = "verify"
        elif _SEARCHING.search(line):
            self.attention, self.qr_seconds = None, None
            self._advance("search")
        elif _STORED.search(line):
            self.notes += 1
            self._advance("search")
        elif "[download]" in line and "下载完成" in line:
            self.media += 1
            self._advance("download")

    def _feed_nowcoder(self, line: str) -> None:
        if not line.startswith("[nowcoder]"):
            return
        if "等待" in line:
            self.attention = "throttled"
            return
        self.attention = None
        if "已保存帖子" in line:
            self.notes += 1
            self._advance("fetch")
        elif "图片下载完成" in line:
            self.media += 1
            self._advance("download")

    def finish_crawler(self) -> None:
        self.attention, self.qr_seconds = None, None
        self._advance("import")

    def done(self) -> None:
        self.attention, self.qr_seconds = None, None
        self.step = "done"

    def _advance(self, step: str) -> None:
        if step in self.steps and self.steps.index(step) > self.steps.index(self.step):
            self.step = step

    def as_dict(self) -> dict:
        titles = step_titles(self.platform)
        return {
            "platform": self.platform,
            "steps": [{"name": name, "title": titles[name]} for name in self.steps],
            "step": self.step,
            "step_title": titles[self.step],
            "attention": self.attention,
            "attention_title": attention_titles(self.platform).get(self.attention or ""),
            "qr_seconds": self.qr_seconds,
            "notes": self.notes,
            "media": self.media,
        }
