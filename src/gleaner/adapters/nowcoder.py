"""Nowcoder posts as saved by `sources/nowcoder.py`.

Each JSONL record is {"kind", "post_id", "url", "source_keywords", "author_hash", "detail"}, where detail is the
unchanged response of Nowcoder's detail API without the author profile. Discussions (讨论帖) carry HTML in
`richText`/`content`; moments (动态) carry plain text with inline emoji images and list pictures in `imgMoment`.
"""

from __future__ import annotations

import re
from html import unescape
from html.parser import HTMLParser
from pathlib import Path

from gleaner.adapters.common import local_images, metrics
from gleaner.models import Post
from gleaner.timeutil import from_epoch
from gleaner.urls import nowcoder_url

KINDS = ("discuss", "moment")
_METRICS = {
    "likeCnt": "liked_count",
    "followCnt": "collected_count",
    "commentCnt": "comment_count",
    "shareCnt": "share_count",
    "viewCnt": "view_count",
}
_BLOCKS = {
    "p",
    "div",
    "section",
    "article",
    "blockquote",
    "pre",
    "table",
    "tr",
    "h1",
    "h2",
    "h3",
    "h4",
    "h5",
    "h6",
    "hr",
}
_EMOJI_ATTRS = {"data-card-emoji", "data-card-nowcoder"}
_SPACES = re.compile(r"[ \t\r\n\f\u00a0]+")
_IMAGE_TAG = re.compile(r"<img\b[^>]*>", re.IGNORECASE)
_BREAK = re.compile(r"<br\s*/?>", re.IGNORECASE)


def to_post(record: dict, *, media: Path, workspace: Path) -> Post | None:
    kind = str(record.get("kind") or "")
    post_id = str(record.get("post_id") or "").strip().lower()
    detail = record.get("detail")
    if kind not in KINDS or not post_id or not isinstance(detail, dict):
        return None
    if kind == "discuss":
        body = html_to_text(str(detail.get("richText") or detail.get("content") or ""))
        published = detail.get("createTime") or detail.get("showTime")
    else:
        body = moment_text(str(detail.get("content") or ""))
        published = detail.get("createdAt") or detail.get("showTime")
    remote_images = post_images(record)
    url = nowcoder_url(post_id)
    return Post(
        platform="nowcoder",
        post_id=post_id,
        url=url,
        open_url=url,
        title=str(detail.get("title") or "").strip() or body[:60],
        body=body,
        kind="image" if remote_images else "text",
        published_at=from_epoch(published),
        keywords=[str(item) for item in record.get("source_keywords") or [] if str(item).strip()],
        images=local_images(media / post_id, workspace),
        remote_image_count=len(remote_images),
        metrics=metrics(detail.get("frequencyData") or {}, _METRICS),
        author_hash=str(record.get("author_hash") or ""),
    )


def post_images(record: dict) -> list[str]:
    """Picture URLs of one saved post, in post order; emoji are not pictures."""
    detail = record.get("detail") or {}
    if record.get("kind") == "moment":
        return [
            str(item["src"])
            for item in detail.get("imgMoment") or []
            if isinstance(item, dict) and str(item.get("src", "")).startswith("https://")
        ]
    return content_images(str(detail.get("richText") or detail.get("content") or ""))


def content_images(html: str) -> list[str]:
    parser = _Images()
    parser.feed(html)
    parser.close()
    return list(dict.fromkeys(parser.found))


def moment_text(content: str) -> str:
    """Moments are plain text, so `List<String>` stays as written; only inline images and breaks are tags."""
    text = _BREAK.sub("\n", _IMAGE_TAG.sub("", content))
    lines = [line.rstrip() for line in unescape(text).splitlines()]
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()


def html_to_text(html: str) -> str:
    """Readable text: one line per paragraph, list items numbered like the page shows them."""
    parser = _Text()
    parser.feed(html)
    parser.close()
    return parser.text()


class _Images(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.found: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = dict(attrs)
        source = values.get("src") or ""
        if tag == "img" and source.startswith("https://") and not _EMOJI_ATTRS & values.keys():
            self.found.append(source)


class _Text(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.lines: list[str] = []
        self.line = ""
        self.prefix = ""
        self.lists: list[list] = []
        self.skip = 0
        self.pre = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in ("script", "style"):
            self.skip += 1
        elif tag == "br":
            self.newline(keep_empty=True)
        elif tag in ("ol", "ul"):
            self.newline()
            self.lists.append([tag == "ol", 0])
        elif tag == "li":
            self.newline()
            if self.lists and self.lists[-1][0]:
                self.lists[-1][1] += 1
                self.prefix = f"{self.lists[-1][1]}. "
            else:
                self.prefix = "- "
        elif tag in _BLOCKS:
            self.newline()
            if tag == "pre":
                self.pre += 1
        elif tag in ("td", "th") and self.line:
            self.line += " | "

    def handle_endtag(self, tag: str) -> None:
        if tag in ("script", "style"):
            self.skip = max(0, self.skip - 1)
        elif tag in ("ol", "ul"):
            self.newline()
            if self.lists:
                self.lists.pop()
        elif tag == "li" or tag in _BLOCKS:
            self.newline()
            if tag == "pre":
                self.pre = max(0, self.pre - 1)

    def handle_data(self, data: str) -> None:
        if self.skip:
            return
        if self.pre:
            for number, part in enumerate(data.split("\n")):
                if number:
                    self.newline(keep_empty=True)
                self.write(part)
            return
        text = _SPACES.sub(" ", data)
        if not self.line:
            text = text.lstrip()
        if text:
            self.write(text)

    def write(self, text: str) -> None:
        if not self.line and self.prefix:
            self.line, self.prefix = self.prefix, ""
        self.line += text

    def newline(self, *, keep_empty: bool = False) -> None:
        line = self.line.rstrip()
        if line or (keep_empty and self.lines and self.lines[-1]):
            self.lines.append(line)
        self.line = ""

    def text(self) -> str:
        self.newline()
        return "\n".join(self.lines).strip()
