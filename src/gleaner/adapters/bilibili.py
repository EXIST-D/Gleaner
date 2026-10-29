"""MediaCrawler's Bilibili videos (`store/bilibili/__init__.py: update_bilibili_video`).

Only the title and description are text; MediaCrawler cuts both at 500 characters. With `--get_media yes`
the video lands in `media/<BV ID>/video.mp4`, while the record itself only carries the av number.
"""

from __future__ import annotations

from pathlib import Path

from gleaner.adapters.common import hashtags, local_video, metrics
from gleaner.models import Post
from gleaner.timeutil import from_epoch
from gleaner.urls import bilibili_bvid, bilibili_url

_METRICS = {
    "liked_count": "liked_count",
    "video_favorite_count": "collected_count",
    "video_comment": "comment_count",
    "video_share_count": "share_count",
    "video_play_count": "view_count",
}


def to_post(record: dict, *, media: Path, workspace: Path) -> Post | None:
    aid = str(record.get("video_id") or "").strip()
    if not aid:
        return None
    folders = [media / bilibili_bvid(aid), media / aid] if aid.isdigit() else [media / aid]
    title = str(record.get("title") or "").strip()
    body = str(record.get("desc") or "").strip()
    if body == "-":
        body = ""
    keyword = str(record.get("source_keyword") or "").strip()
    return Post(
        platform="bili",
        post_id=aid,
        url=bilibili_url(aid),
        open_url=str(record.get("video_url") or bilibili_url(aid)),
        title=title or body[:60] or aid,
        body=body,
        kind="video",
        published_at=from_epoch(record.get("create_time")),
        tags=hashtags(body),
        keywords=[keyword] if keyword else [],
        metrics=metrics(record, _METRICS),
        author_hash=str(record.get("creator_hash") or ""),
        media=local_video(folders, workspace),
    )
