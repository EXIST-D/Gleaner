"""Environment checks shared by `gleaner doctor` and the web console."""

from __future__ import annotations

import shutil
from dataclasses import dataclass

from gleaner.config import Config
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
        Check(
            "keywords", "默认关键词", bool(config.keywords), "、".join(config.keywords) or "没有默认关键词"
        ),
        Check(
            "ibank",
            "Interview Bank",
            bool(ibank and ibank.is_file()),
            str(ibank) if ibank else "没有配置 handoff.ibank",
        ),
        Check("bank", "采集题库", bank_ok, bank_detail),
        Check(
            "python", "导入用 Python", shutil.which(config.handoff.python) is not None, config.handoff.python
        ),
    ]
