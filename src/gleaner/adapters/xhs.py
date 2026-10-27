"""Read MediaCrawler's Xiaohongshu export (`store/xhs/__init__.py: update_xhs_note`) into `Post` objects."""

from __future__ import annotations

import json
import re
import shutil
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path

from gleaner.models import Post
from gleaner.timeutil import from_epoch
from gleaner.urls import canonical_url, xhs_note_url

PLATFORM_DIR = "xhs"
IMAGE_FILE = re.compile(r"^\d{3}\.(?:jpe?g|png|webp|gif)$", re.IGNORECASE)
_METRICS = ("liked_count", "collected_count", "comment_count", "share_count")


@dataclass
class ParseReport:
    records: int = 0
    skipped: int = 0
    errors: list[str] = field(default_factory=list)


def content_files(raw_dir: Path) -> list[Path]:
    base = raw_dir / PLATFORM_DIR
    return sorted([*base.glob("jsonl/*_contents_*.jsonl"), *base.glob("json/*_contents_*.json")])


def iter_posts(raw_dir: Path, workspace: Path, report: ParseReport) -> Iterator[Post]:
    for path in content_files(raw_dir):
        for number, record in _records(path, report):
            report.records += 1
            post = to_post(record, raw_dir=raw_dir, workspace=workspace)
            if post is None:
                report.skipped += 1
                report.errors.append(f"{path.name}:{number} 没有 note_id")
                continue
            yield post


def to_post(record: dict, *, raw_dir: Path, workspace: Path) -> Post | None:
    note_id = str(record.get("note_id") or "").strip()
    if not note_id:
        return None
    open_url = str(record.get("note_url") or xhs_note_url(note_id))
    body = _clean_topics(str(record.get("desc") or ""))
    title = _clean_topics(str(record.get("title") or "")).strip() or body[:60]
    images = _local_images(raw_dir / PLATFORM_DIR / "media" / note_id, workspace)
    remote_images = _split(record.get("image_list"))
    is_video = str(record.get("type") or "") == "video"
    keyword = str(record.get("source_keyword") or "").strip()
    return Post(
        platform="xhs",
        post_id=note_id,
        url=canonical_url(xhs_note_url(note_id)),
        open_url=open_url,
        title=title,
        body=body.strip(),
        kind="video" if is_video else ("image" if images or remote_images else "text"),
        published_at=from_epoch(record.get("time")),
        tags=_split(record.get("tag_list")),
        keywords=[keyword] if keyword else [],
        images=[] if is_video else images,
        remote_image_count=0 if is_video else len(remote_images),
        metrics={name: value for name in _METRICS if (value := parse_count(record.get(name))) is not None},
        author_hash=str(record.get("creator_hash") or ""),
    )


def copy_export(source: Path, raw_dir: Path) -> Path:
    """Copy an existing MediaCrawler data directory into a run, keeping note records and images only."""
    base = source / PLATFORM_DIR if (source / PLATFORM_DIR).is_dir() else source
    if base.name != PLATFORM_DIR or not base.is_dir():
        raise FileNotFoundError(f"{source} 里没有 MediaCrawler 的 xhs 导出目录")
    target = raw_dir / PLATFORM_DIR
    for path in [*base.glob("jsonl/*_contents_*.jsonl"), *base.glob("json/*_contents_*.json")]:
        destination = target / path.parent.name / path.name
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, destination)
    for note_dir in sorted((base / "media").glob("*")):
        for image in sorted(note_dir.glob("*")):
            if IMAGE_FILE.match(image.name):
                destination = target / "media" / note_dir.name / image.name
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(image, destination)
    return raw_dir


def parse_count(value: object) -> int | None:
    """Interaction counts arrive as 12, "12", "1,234", "10+" or "1.2万"."""
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


def _records(path: Path, report: ParseReport) -> Iterator[tuple[int, dict]]:
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


def _local_images(directory: Path, workspace: Path) -> list[str]:
    if not directory.is_dir():
        return []
    files = sorted(path for path in directory.iterdir() if IMAGE_FILE.match(path.name))
    return [path.resolve().relative_to(workspace.resolve()).as_posix() for path in files]


def _split(value: object) -> list[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    return [part.strip() for part in str(value or "").split(",") if part.strip()]


def _clean_topics(text: str) -> str:
    return text.replace("[话题]#", "").replace("[话题]", "")
