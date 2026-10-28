"""MediaCrawler's Douyin works (`store/douyin/__init__.py: update_douyin_aweme`).

An image set (图集) lists its pictures in `note_download_url`; a video has `video_download_url`.
The caption is both `title` and `desc`, with hashtags inline.
"""

from __future__ import annotations

from pathlib import Path

from gleaner.adapters.common import first_line, hashtags, local_images, metrics, split_list
from gleaner.models import Post
from gleaner.timeutil import from_epoch
from gleaner.urls import douyin_url

_METRICS = {
    "liked_count": "liked_count",
    "collected_count": "collected_count",
    "comment_count": "comment_count",
    "share_count": "share_count",
}


def to_post(record: dict, *, media: Path, workspace: Path) -> Post | None:
    aweme_id = str(record.get("aweme_id") or "").strip()
    if not aweme_id:
        return None
    body = str(record.get("desc") or record.get("title") or "").strip()
    remote_images = split_list(record.get("note_download_url"))
    keyword = str(record.get("source_keyword") or "").strip()
    return Post(
        platform="dy",
        post_id=aweme_id,
        url=douyin_url(aweme_id),
        open_url=str(record.get("aweme_url") or douyin_url(aweme_id)),
        title=first_line(body) or aweme_id,
        body=body,
        kind="image" if remote_images else "video",
        published_at=from_epoch(record.get("create_time")),
        tags=hashtags(body),
        keywords=[keyword] if keyword else [],
        images=local_images(media / aweme_id, workspace) if remote_images else [],
        remote_image_count=len(remote_images),
        metrics=metrics(record, _METRICS),
        author_hash=str(record.get("creator_hash") or ""),
    )
