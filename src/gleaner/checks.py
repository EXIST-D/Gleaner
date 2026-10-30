"""Environment checks shared by `gleaner doctor` and the web console."""

from __future__ import annotations

import shutil
from dataclasses import dataclass

from gleaner.audio import find_ffmpeg
from gleaner.config import Config
from gleaner.installer import APPLIED, CONFLICT, MODERN_PYTHON, make_run, patch_files, patch_states
from gleaner.pipeline import CRAWL_PLATFORMS
from gleaner.sources.mediacrawler import MediaCrawlerDriver


@dataclass(frozen=True)
class Check:
    name: str
    title: str
    ok: bool
    detail: str

    def as_dict(self) -> dict:
        return {"name": self.name, "title": self.title, "ok": self.ok, "detail": self.detail}


def environment_checks(config: Config) -> list[Check]:
    problems = MediaCrawlerDriver(config.mediacrawler).problems()
    ffmpeg = find_ffmpeg(config.media.ffmpeg)
    asr_python = config.handoff.transcribe_python
    ibank = config.handoff.ibank
    bank = config.handoff.bank
    bank_ok = bool(bank and (bank / "manifest.json").is_file())
    bank_detail = str(bank) if bank else "没有配置 handoff.bank"
    if bank and not bank_ok:
        bank_detail += "（还不是题库，先运行 ibank.py init --bank）"
    return [
        Check(
            "mediacrawler",
            "MediaCrawler（牛客以外的平台需要）",
            not problems,
            "；".join(problems) or str(config.mediacrawler.home),
        ),
        patch_check(config),
        Check(
            "keywords", "默认关键词", bool(config.keywords), "、".join(config.keywords) or "没有默认关键词"
        ),
        auto_check(config),
        Check(
            "ibank",
            "Interview Bank",
            bool(ibank and ibank.is_file()),
            str(ibank) if ibank else "没有配置 handoff.ibank",
        ),
        Check("bank", "采集题库", bank_ok, bank_detail),
        python_check(config.handoff.python),
        Check(
            "ffmpeg",
            "ffmpeg（下载和打包 B 站面试录音需要）",
            ffmpeg is not None,
            ffmpeg or f"找不到 {config.media.ffmpeg}；在 [media] ffmpeg 写上路径",
        ),
        Check(
            "asr_python",
            "转写用 Python（需要装 faster-whisper）",
            shutil.which(asr_python) is not None,
            asr_python,
        ),
    ]


def auto_check(config: Config) -> Check:
    title = "自动运行的平台（[auto] platforms）"
    unknown = [code for code in config.auto.platforms if code not in CRAWL_PLATFORMS]
    if unknown:
        return Check(
            "auto", title, False, f"不认识 {'、'.join(unknown)}，可选：{' / '.join(CRAWL_PLATFORMS)}"
        )
    return Check("auto", title, True, "、".join(config.auto.platforms) or "全部平台")


def python_check(python: str) -> Check:
    title = "导入用 Python（Interview Bank 需要 3.10+）"
    found = shutil.which(python)
    if found is None:
        return Check("python", title, False, f"找不到 {python}")
    try:
        code, _ = make_run(echo=False)([found, "-c", MODERN_PYTHON], None, None)
    except OSError as exc:
        return Check("python", title, False, f"{python} 运行不了：{exc}")
    if code != 0:
        return Check(
            "python", title, False, f"{python} 版本低于 3.10；运行 gleaner setup bank 换成可用的 Python"
        )
    return Check("python", title, True, python)


def patch_check(config: Config) -> Check:
    title = "MediaCrawler 补丁（gleaner setup mediacrawler 会打上）"
    home = config.mediacrawler.home
    git = shutil.which("git")
    if home is None or not (home / "main.py").is_file():
        return Check("patches", title, False, "MediaCrawler 还没装好")
    if git is None or not (home / ".git").exists():
        return Check("patches", title, False, "没法检查：需要 git，MediaCrawler 也要是 git 仓库")
    with patch_files() as patches:
        states = patch_states(home, patches, git=git, run=make_run(echo=False))
    missing = [name for name, state in states.items() if state != APPLIED]
    if not missing:
        return Check("patches", title, True, f"{len(states)} 个都已打上")
    conflicts = [name for name, state in states.items() if state == CONFLICT]
    detail = f"没打上：{'、'.join(missing)}；运行 gleaner setup mediacrawler"
    if conflicts:
        detail += f"（{'、'.join(conflicts)} 和本机改动冲突，先在 MediaCrawler 里 git stash）"
    return Check("patches", title, False, detail)
