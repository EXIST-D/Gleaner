"""Development notes shown in the web console: progress notes, unreleased changes, recent commits and feedback."""

from __future__ import annotations

import re
import subprocess
from datetime import datetime
from pathlib import Path

from gleaner.errors import GleanerError
from gleaner.timeutil import CHINA_TZ

NOTES_FILE = "开发进度.md"
FEEDBACK_FILE = "意见.md"
MAX_FEEDBACK_CHARS = 4000
FEEDBACK_TOPICS = ("概览", "抓取", "帖子", "素材包", "题库", "进度", "其他")
GIT_TIMEOUT_SECONDS = 5
_ENTRY_HEAD = re.compile(r"^## (\d{4}-\d{2}-\d{2} \d{2}:\d{2})(?: · (.+))?$", re.MULTILINE)
_HEADING_START = re.compile(r"^(#+)", re.MULTILINE)


def dev_status(workspace: Path, *, commits: int = 12) -> dict:
    root = repo_root(workspace)
    return {
        "notes": _notes(workspace, root),
        "unreleased": _section(root / "CHANGELOG.md", "未发布") if root else "",
        "repo": str(root) if root else None,
        "commits": _commits(root, commits) if root else [],
        "uncommitted": _count_lines(_git(root, "status", "--porcelain")) if root else None,
        "unpushed": _unpushed(root) if root else None,
        "feedback": feedback_entries(workspace),
        "feedback_file": str(workspace / FEEDBACK_FILE),
        "feedback_topics": list(FEEDBACK_TOPICS),
    }


def repo_root(workspace: Path) -> Path | None:
    top = _git(workspace, "rev-parse", "--show-toplevel")
    return Path(top.strip()) if top and top.strip() else None


def add_feedback(workspace: Path, text: str, topic: str, *, now: datetime) -> dict:
    text = text.strip()
    if not text:
        raise GleanerError("意见不能为空")
    if len(text) > MAX_FEEDBACK_CHARS:
        raise GleanerError(f"意见太长，最多 {MAX_FEEDBACK_CHARS} 个字")
    if topic not in FEEDBACK_TOPICS:
        raise GleanerError(f"不认识的页面：{topic}")
    # A line starting with "#" would end the entry early when the file is read back.
    body = _HEADING_START.sub(lambda match: "\\" + match.group(1), text.replace("\r\n", "\n"))
    path = workspace / FEEDBACK_FILE
    existing = path.read_text(encoding="utf-8") if path.is_file() else "# 意见\n"
    stamp = now.astimezone(CHINA_TZ).strftime("%Y-%m-%d %H:%M")
    path.write_text(f"{existing.rstrip()}\n\n## {stamp} · {topic}\n\n{body}\n", encoding="utf-8")
    return {"time": stamp, "topic": topic, "text": body}


def feedback_entries(workspace: Path) -> list[dict]:
    path = workspace / FEEDBACK_FILE
    if not path.is_file():
        return []
    text = path.read_text(encoding="utf-8")
    heads = list(_ENTRY_HEAD.finditer(text))
    entries = []
    for index, head in enumerate(heads):
        end = heads[index + 1].start() if index + 1 < len(heads) else len(text)
        entries.append(
            {"time": head.group(1), "topic": head.group(2) or "", "text": text[head.end() : end].strip()}
        )
    return entries[::-1]


def _notes(workspace: Path, root: Path | None) -> dict | None:
    local = workspace / NOTES_FILE
    if local.is_file():
        return {"path": str(local), "markdown": local.read_text(encoding="utf-8")}
    handbook = root / "docs" / "development.md" if root else None
    section = _section(handbook, "当前状态") if handbook else ""
    return {"path": str(handbook), "markdown": section} if section else None


def _section(path: Path, title: str) -> str:
    """The level-2 section whose heading contains `title`, without the heading line."""
    if not path.is_file():
        return ""
    lines = path.read_text(encoding="utf-8").splitlines()
    for start, line in enumerate(lines):
        if line.startswith("## ") and title in line:
            end = next((i for i in range(start + 1, len(lines)) if lines[i].startswith("## ")), len(lines))
            return "\n".join(lines[start + 1 : end]).strip()
    return ""


def _commits(root: Path, limit: int) -> list[dict]:
    out = _git(root, "log", f"-n{limit}", "--pretty=format:%h%x1f%cI%x1f%s")
    commits = []
    for line in (out or "").splitlines():
        parts = line.split("\x1f")
        if len(parts) == 3:
            commits.append({"hash": parts[0], "date": parts[1], "subject": parts[2]})
    return commits


def _unpushed(root: Path) -> int | None:
    out = _git(root, "rev-list", "--count", "@{upstream}..HEAD")
    return int(out.strip()) if out and out.strip().isdigit() else None


def _count_lines(text: str | None) -> int | None:
    return None if text is None else len([line for line in text.splitlines() if line.strip()])


def _git(directory: Path, *args: str) -> str | None:
    try:
        result = subprocess.run(
            ["git", "-C", str(directory), *args],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=GIT_TIMEOUT_SECONDS,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    return result.stdout if result.returncode == 0 else None
