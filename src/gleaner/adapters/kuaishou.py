"""MediaCrawler's Kuaishou videos (`store/kuaishou/__init__.py: update_kuaishou_video`).

The caption is both `title` and `desc`, cut at 500 characters, with hashtags inline.
"""

from __future__ import annotations

from pathlib import Path

from gleaner.adapters.common import first_line, hashtags, metrics
from gleaner.models import Post
from gleaner.timeutil import from_epoch
from gleaner.urls import kuaishou_url

_METRICS = {"liked_count": "liked_count", "viewd_count": "view_count"}


def to_post(record: dict, *, media: Path, workspace: Path) -> Post | None:
    photo_id = str(record.get("video_id") or "").strip()
    if not photo_id:
        return None
    body = str(record.get("desc") or record.get("title") or "").strip()
    keyword = str(record.get("source_keyword") or "").strip()
    return Post(
        platform="ks",
        post_id=photo_id,
        url=kuaishou_url(photo_id),
        open_url=str(record.get("video_url") or kuaishou_url(photo_id)),
        title=first_line(body) or photo_id,
        body=body,
        kind="video",
        published_at=from_epoch(record.get("create_time")),
        tags=hashtags(body),
        keywords=[keyword] if keyword else [],
        metrics=metrics(record, _METRICS),
        author_hash=str(record.get("creator_hash") or ""),
    )
