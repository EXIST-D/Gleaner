"""Turn MediaCrawler's log lines into a step and a call to action for the person watching.

The markers follow MediaCrawler main as of 2026-10 (`media_platform/xhs/login.py`, `core.py`,
`media_downloader/downloader.py`, `store/xhs/__init__.py`). Unknown lines are simply shown in the log.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

STEPS = ("browser", "login", "search", "download", "import", "done")
STEP_TITLES = {
    "browser": "启动浏览器",
    "login": "登录",
    "search": "搜索帖子",
    "download": "下载图片",
    "import": "导入与初筛",
    "done": "完成",
}
ATTENTION_TITLES = {
    "qr": "请用手机小红书扫描弹出的 Chrome 窗口里的二维码",
    "verify": "小红书要求验证，请在弹出的 Chrome 窗口里完成",
    "confirm": "Chrome 弹出了连接确认框，请在 60 秒内点“接受”",
}

_QR_COUNTDOWN = re.compile(r"waiting for scan code login, remaining time is (\d+)s")
_LOGGED_IN = ("Login successful", "Login status confirmed", "Login state result: True")
_VERIFY = ("请通过验证", "验证码", "captcha", "slider", "滑块")


@dataclass
class Progress:
    step: str = "browser"
    attention: str | None = None
    qr_seconds: int | None = None
    notes: int = 0
    media: int = 0

    def feed(self, line: str) -> None:
        if match := _QR_COUNTDOWN.search(line):
            self._advance("login")
            self.attention, self.qr_seconds = "qr", int(match.group(1))
        elif "Begin login" in line:
            self._advance("login")
        elif any(marker in line for marker in _LOGGED_IN):
            self.attention, self.qr_seconds = None, None
            self._advance("search")
        elif "confirmation dialog" in line:
            self.attention = "confirm"
        elif any(marker in line for marker in _VERIFY):
            self.attention = "verify"
        elif "Begin search" in line or "search Xiaohongshu keyword" in line:
            self.attention = None
            self._advance("search")
        elif "update_xhs_note]" in line:
            self.notes += 1
            self._advance("search")
        elif "[download]" in line and "下载完成" in line:
            self.media += 1
            self._advance("download")

    def finish_crawler(self) -> None:
        self.attention, self.qr_seconds = None, None
        self._advance("import")

    def done(self) -> None:
        self.attention, self.qr_seconds = None, None
        self.step = "done"

    def _advance(self, step: str) -> None:
        if STEPS.index(step) > STEPS.index(self.step):
            self.step = step

    def as_dict(self) -> dict:
        return {
            "step": self.step,
            "step_title": STEP_TITLES[self.step],
            "attention": self.attention,
            "attention_title": ATTENTION_TITLES.get(self.attention or ""),
            "qr_seconds": self.qr_seconds,
            "notes": self.notes,
            "media": self.media,
        }
