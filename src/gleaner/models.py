from __future__ import annotations

import hashlib
from dataclasses import dataclass, field

LABELS = ("candidate", "collection", "promo", "irrelevant", "unclear")
LABEL_TITLES = {
    "candidate": "疑似真实面经",
    "collection": "题目合集",
    "promo": "广告/卖课/引流",
    "irrelevant": "无关",
    "unclear": "拿不准",
}
KINDS = ("image", "video", "text")


@dataclass(frozen=True)
class Platform:
    code: str
    label: str
    host: str


PLATFORMS = {
    "xhs": Platform(code="xhs", label="小红书", host="xiaohongshu.com"),
    "dy": Platform(code="dy", label="抖音", host="douyin.com"),
    "bili": Platform(code="bili", label="B站", host="bilibili.com"),
    "zhihu": Platform(code="zhihu", label="知乎", host="zhihu.com"),
    "nowcoder": Platform(code="nowcoder", label="牛客", host="nowcoder.com"),
}


@dataclass
class Post:
    """One crawled post, independent of the crawler that produced it."""

    platform: str
    post_id: str
    url: str
    """Canonical URL without session or tracking parameters; safe to hand over."""
    open_url: str
    """URL exactly as crawled. It may carry session tokens, so it stays in the local database."""
    title: str
    body: str
    kind: str
    published_at: str | None
    """ISO 8601 timestamp in UTC, or None when the platform gave no usable time."""
    tags: list[str] = field(default_factory=list)
    keywords: list[str] = field(default_factory=list)
    images: list[str] = field(default_factory=list)
    """Downloaded images in post order, as paths relative to the workspace."""
    remote_image_count: int = 0
    metrics: dict[str, int] = field(default_factory=dict)
    author_hash: str = ""
    media: str | None = None
    """A downloaded recording (Bilibili video), relative to the workspace; packed as audio for transcription."""

    @property
    def key(self) -> str:
        return f"{self.platform}:{self.post_id}"

    @property
    def content_hash(self) -> str:
        payload = "\x00".join([self.title, self.body, *self.tags])
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()


@dataclass
class PostRecord:
    """A post together with its state in the workspace."""

    post: Post
    first_run: str
    last_run: str
    first_seen_at: str
    last_seen_at: str
    label: str | None = None
    label_source: str | None = None
    label_reasons: list[str] = field(default_factory=list)
    screened_at: str | None = None
    packed_in: str | None = None
    packed_at: str | None = None
    changed_after_pack: bool = False
    disposition: str | None = None
    """Decided outside the packs: `manual` (imported into the bank by hand) or `skipped` (not importing)."""


MANUAL = "manual"
SKIPPED = "skipped"
DISPOSITIONS = {MANUAL: "手工导入", SKIPPED: "不导入"}
