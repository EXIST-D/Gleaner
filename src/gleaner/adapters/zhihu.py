"""MediaCrawler's Zhihu answers, articles and videos (`model/m_zhihu.py: ZhihuContent`).

`content_text` is the HTML body with every tag removed, so paragraphs run together on one line.
Answer and article IDs come from different sequences, so the post ID carries the content type.
"""

from __future__ import annotations

from pathlib import Path

from gleaner.adapters.common import metrics
from gleaner.models import Post
from gleaner.timeutil import from_epoch
from gleaner.urls import canonical_url, zhihu_url

_METRICS = {"voteup_count": "liked_count", "comment_count": "comment_count"}
CONTENT_TYPES = ("answer", "article", "zvideo")


def to_post(record: dict, *, media: Path, workspace: Path) -> Post | None:
    content_id = str(record.get("content_id") or "").strip()
    content_type = str(record.get("content_type") or "").strip()
    if not content_id or content_type not in CONTENT_TYPES:
        return None
    question_id = str(record.get("question_id") or "").strip()
    url = zhihu_url(content_type, content_id, question_id)
    body = str(record.get("content_text") or record.get("desc") or "").strip()
    keyword = str(record.get("source_keyword") or "").strip()
    return Post(
        platform="zhihu",
        post_id=f"{content_type}-{content_id}",
        url=url,
        open_url=canonical_url(str(record.get("content_url") or url)),
        title=str(record.get("title") or "").strip() or body[:60],
        body=body,
        kind="video" if content_type == "zvideo" else "text",
        published_at=from_epoch(record.get("created_time")),
        keywords=[keyword] if keyword else [],
        metrics=metrics(record, _METRICS),
        author_hash=str(record.get("creator_hash") or ""),
    )
