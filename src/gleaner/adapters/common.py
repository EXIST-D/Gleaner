"""Helpers shared by the platform adapters."""

from __future__ import annotations

import json
import re
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path

IMAGE_FILE = re.compile(r"^\d{3}\.(?:jpe?g|png|webp|gif)$", re.IGNORECASE)
"""Post images are numbered 001.jpg …; covers and videos are named cover.* and video.*."""
_HASHTAG = re.compile(r"#([^\s#]{1,30})")


@dataclass
class ParseReport:
    records: int = 0
    skipped: int = 0
    errors: list[str] = field(default_factory=list)


def read_records(path: Path, report: ParseReport) -> Iterator[tuple[int, dict]]:
    """(line or index number, record) from a MediaCrawler .jsonl or .json export."""
    text = path.read_text(encoding="utf-8", errors="replace")
    if path.suffix == ".json":
        try:
            data = json.loads(text) if text.strip() else []
        except json.JSONDecodeError as exc:
            report.errors.append(f"{path.name} 不是合法的 JSON：{exc}")
            return
        items = data if isinstance(data, list) else []
        yield from ((index, item) for index, item in enumerate(items, start=1) if isinstance(item, dict))
        return
    for number, line in enumerate(text.splitlines(), start=1):
        if not line.strip():
            continue
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            report.skipped += 1
            report.errors.append(f"{path.name}:{number} 不是合法的 JSON 行")
            continue
        if isinstance(item, dict):
            yield number, item


def local_images(directory: Path, workspace: Path) -> list[str]:
    """Numbered images of one post, as paths relative to the workspace."""
    if not directory.is_dir():
        return []
    files = sorted(path for path in directory.iterdir() if IMAGE_FILE.match(path.name))
    return [path.resolve().relative_to(workspace.resolve()).as_posix() for path in files]


def parse_count(value: object) -> int | None:
    """Interaction counts arrive as 12, "12", "1,234", "10+", "1.2万" or "None"."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    text = str(value or "").strip().replace(",", "").rstrip("+")
    if not text:
        return None
    scale = 1
    if text.endswith("万"):
        text, scale = text[:-1], 10_000
    elif text.endswith("亿"):
        text, scale = text[:-1], 100_000_000
    try:
        return int(float(text) * scale)
    except ValueError:
        return None


def metrics(record: dict, names: dict[str, str]) -> dict[str, int]:
    """Gleaner metric name -> count, for the platform fields in names (field -> metric)."""
    found = {}
    for source, target in names.items():
        value = parse_count(record.get(source))
        if value is not None:
            found[target] = value
    return found


def split_list(value: object) -> list[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    return [part.strip() for part in str(value or "").split(",") if part.strip()]


def hashtags(text: str) -> list[str]:
    return list(dict.fromkeys(_HASHTAG.findall(text)))


def first_line(text: str, limit: int = 60) -> str:
    line = next((line.strip() for line in text.splitlines() if line.strip()), "")
    return line[:limit]
