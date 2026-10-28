"""Read the collected bank through Interview Bank's public command line (read-only).

Only `search --format jsonl --detail`, `show` and `taxonomy` are used, so the bank's own locking and format
rules apply and Gleaner never touches its JSONL files.
"""

from __future__ import annotations

import json
import re
import subprocess
import threading
from pathlib import Path

from gleaner.config import HandoffConfig
from gleaner.errors import GleanerError

TIMEOUT_SECONDS = 60
BANK_MEDIA = re.compile(r"^[0-9a-f]{64}\.(?:webp|jpe?g|png|gif)$")
EXPORT_BASENAME = "采集题库报告"
QUESTION_SUFFIX = "（题目版）"
_UNSAFE_FILENAME = re.compile(r'[\\/:*?"<>|\s]+')

QUESTION_TYPES = {
    "concept": "概念",
    "coding": "编码",
    "project": "项目",
    "system-design": "系统设计",
    "scenario": "场景",
    "debugging": "排障",
    "behavioral": "行为",
    "math": "数学",
    "case": "案例",
}
ROUNDS = {
    "written": "笔试",
    "technical-1": "一面",
    "technical-2": "二面",
    "technical-3": "三面",
    "manager": "主管面",
    "hr": "HR 面",
    "cross": "交叉面",
    "unknown": "轮次未知",
}
INTERVIEW_TYPES = {"campus": "校招", "intern": "实习", "experienced": "社招", "unknown": ""}
ANSWER_STATUSES = {
    "missing": "待研究答案",
    "ai_draft": "AI 草稿",
    "source_backed": "有来源答案",
    "reviewed": "已人工审阅",
    "stale": "答案需复核",
}


class BankReader:
    def __init__(self, handoff: HandoffConfig):
        self.handoff = handoff
        self._lock = threading.Lock()
        self._questions: tuple[tuple, list[dict]] | None = None
        self._domains: dict[str, dict] | None = None

    @property
    def bank(self) -> Path | None:
        return self.handoff.bank

    def problem(self) -> str | None:
        if not self.handoff.ibank or not self.handoff.ibank.is_file():
            return "没有配置 handoff.ibank（Interview Bank 的 ibank.py），无法读取题库"
        if not self.bank or not (self.bank / "manifest.json").is_file():
            return "没有配置采集题库，或者它还不是 Interview Bank 题库（handoff.bank）"
        return None

    # Reads

    def questions(self) -> list[dict]:
        """All active questions with occurrences, cached until the bank's data files change."""
        signature = self._signature()
        with self._lock:
            if self._questions and self._questions[0] == signature:
                return self._questions[1]
        output = self._run(["search", "--format", "jsonl", "--detail", "--limit", "100000"])
        rows = [json.loads(line) for line in output.splitlines() if line.strip()]
        with self._lock:
            self._questions = (signature, rows)
        return rows

    def question(self, question_id: str) -> dict:
        if not re.fullmatch(r"q_[0-9a-f]{32}", question_id):
            raise GleanerError("题目编号不正确")
        data = json.loads(self._run(["show", question_id, "--json"]))
        if not data.get("ok"):
            raise GleanerError(data.get("error") or f"找不到题目 {question_id}")
        return data["result"]

    def domain_labels(self) -> dict[str, dict]:
        with self._lock:
            if self._domains is not None:
                return self._domains
        data = json.loads(self._run(["taxonomy", "--dimension", "domains", "--limit", "10000", "--json"]))
        domains = {item["id"]: item for item in data.get("result", {}).get("items", [])}
        with self._lock:
            self._domains = domains
        return domains

    # Exports (the only write: Interview Bank renders reports into its own exports/ directory)

    def export_markdown(
        self, *, company: str | None = None, domain: str | None = None, title: str = ""
    ) -> dict:
        name = export_name(company=company, title=title)
        args = [
            "export",
            "--format",
            "markdown",
            "--answers",
            "both",
            "--output",
            str(self.exports_dir / f"{name}.md"),
        ]
        if company:
            args += ["--company", company]
        if domain:
            args += ["--domain", domain]
        data = json.loads(self._run([*args, "--json"]))
        if not data.get("ok"):
            raise GleanerError(data.get("error") or "导出失败")
        result = data["result"]
        return {
            "answer_file": Path(result["answer_output"]).name,
            "question_file": Path(result["question_output"]).name,
            "questions": result.get("questions", 0),
            "answered": result.get("answered_questions", 0),
            "pending": result.get("pending_answers", 0),
            "excluded": result.get("excluded_questions", 0),
        }

    @property
    def exports_dir(self) -> Path:
        if not self.bank:
            raise GleanerError("没有配置采集题库（handoff.bank）")
        return self.bank / "exports"

    def exports(self) -> list[dict]:
        if not self.bank or not self.exports_dir.is_dir():
            return []
        files = sorted(self.exports_dir.glob("*.md"), key=lambda p: p.stat().st_mtime, reverse=True)
        return [
            {
                "name": path.name,
                "edition": "题目版" if path.stem.endswith(QUESTION_SUFFIX) else "答案版",
                "size": path.stat().st_size,
                "modified": path.stat().st_mtime,
            }
            for path in files
        ]

    def export_path(self, name: str) -> Path | None:
        if not self.bank or Path(name).name != name or not name.endswith(".md"):
            return None
        path = self.exports_dir / name
        return path if path.is_file() else None

    def media_path(self, name: str) -> Path | None:
        if not self.bank or not BANK_MEDIA.match(name):
            return None
        path = self.bank / "media" / name
        return path if path.is_file() else None

    # Helpers

    def _signature(self) -> tuple:
        data = self.bank / "data" if self.bank else None
        if not data or not data.is_dir():
            return ()
        return tuple(
            sorted((p.name, p.stat().st_mtime_ns, p.stat().st_size) for p in data.iterdir() if p.is_file())
        )

    def _run(self, args: list[str]) -> str:
        problem = self.problem()
        if problem:
            raise GleanerError(problem)
        command = [self.handoff.python, "-B", str(self.handoff.ibank), *args, "--bank", str(self.bank)]
        try:
            completed = subprocess.run(
                command,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=TIMEOUT_SECONDS,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise GleanerError(f"无法运行 Interview Bank：{exc}") from exc
        if completed.returncode != 0:
            detail = _error_message(completed.stdout) or completed.stderr.strip()[-300:]
            raise GleanerError(f"Interview Bank 读取失败：{detail or f'退出码 {completed.returncode}'}")
        return completed.stdout


def export_name(*, company: str | None, title: str) -> str:
    """采集题库报告[-公司][-领域], safe as a file name on every platform."""
    parts = [EXPORT_BASENAME, *(part for part in (company, title) if part)]
    return "-".join(_UNSAFE_FILENAME.sub("_", part).strip("_.") or "_" for part in parts)


def topic_of(domains: list[str], labels: dict[str, dict]) -> tuple[str, str]:
    """Group by the first (primary) domain, like Interview Bank's reports do."""
    if not domains:
        return "other", "其他"
    return domains[0], labels.get(domains[0], {}).get("label") or domains[0]


def _error_message(stdout: str) -> str:
    try:
        data = json.loads(stdout)
    except (json.JSONDecodeError, ValueError):
        return ""
    return str(data.get("error") or "")
