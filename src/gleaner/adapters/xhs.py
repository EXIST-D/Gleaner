"""MediaCrawler's Xiaohongshu notes (`store/xhs/__init__.py: update_xhs_note`)."""

from __future__ import annotations

from pathlib import Path

from gleaner.adapters.common import local_images, metrics, split_list
from gleaner.models import Post
from gleaner.timeutil import from_epoch
from gleaner.urls import canonical_url, xhs_note_url

_METRICS = {name: name for name in ("liked_count", "collected_count", "comment_count", "share_count")}


def to_post(record: dict, *, media: Path, workspace: Path) -> Post | None:
    note_id = str(record.get("note_id") or "").strip()
    if not note_id:
        return None
    body = _clean_topics(str(record.get("desc") or ""))
    title = _clean_topics(str(record.get("title") or "")).strip() or body[:60]
    images = local_images(media / note_id, workspace)
    remote_images = split_list(record.get("image_list"))
    is_video = str(record.get("type") or "") == "video"
    keyword = str(record.get("source_keyword") or "").strip()
    return Post(
        platform="xhs",
        post_id=note_id,
        url=canonical_url(xhs_note_url(note_id)),
        open_url=str(record.get("note_url") or xhs_note_url(note_id)),
        title=title,
        body=body.strip(),
        kind="video" if is_video else ("image" if images or remote_images else "text"),
        published_at=from_epoch(record.get("time")),
        tags=split_list(record.get("tag_list")),
        keywords=[keyword] if keyword else [],
        images=[] if is_video else images,
        remote_image_count=0 if is_video else len(remote_images),
        metrics=metrics(record, _METRICS),
        author_hash=str(record.get("creator_hash") or ""),
    )


def _clean_topics(text: str) -> str:
    return text.replace("[话题]#", "").replace("[话题]", "")
