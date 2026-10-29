from __future__ import annotations

import re
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

_DROPPED_QUERY_KEYS = {
    "xsec_token",
    "xsec_source",
    "spm",
    "spm_id_from",
    "vd_source",
    "sourcessr",
    "from",
    "ref",
    "source",
    "share_id",
    "share_source",
    "share_medium",
    "share_plat",
    "share_session_id",
    "share_from_user_hidden",
    "author_share",
    "app_platform",
    "app_version",
    "apptime",
    "sharerediid",
    "shareredid",
}


def canonical_url(url: str) -> str:
    """Drop session tokens, share and tracking parameters so one post has one stable URL."""
    value = url.strip()
    parts = urlsplit(value)
    if parts.scheme.lower() not in {"http", "https"} or not parts.netloc:
        return value
    query = sorted(
        (key, item)
        for key, item in parse_qsl(parts.query, keep_blank_values=True)
        if key.lower() not in _DROPPED_QUERY_KEYS and not key.lower().startswith("utm_")
    )
    path = parts.path.rstrip("/") or "/"
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), path, urlencode(query), ""))


def xhs_note_url(note_id: str) -> str:
    return f"https://www.xiaohongshu.com/explore/{note_id}"


def douyin_url(aweme_id: str) -> str:
    return f"https://www.douyin.com/video/{aweme_id}"


def bilibili_url(aid: str) -> str:
    return f"https://www.bilibili.com/video/av{aid}"


# Bilibili's public av/BV mapping: an XOR, a base-58 encoding and two swapped digit pairs.
_BV_ALPHABET = "FcwAPNKTMug3GV5Lj7EJnHpWsx4tb8haYeviqBz6rkCy12mUSDQX9RdoZf"
_BV_XOR = 23442827791579
_BV_MAX_AID = 1 << 51
_BV_SWAPS = ((3, 9), (4, 7))
_BV = re.compile(r"^BV1[0-9A-Za-z]{9}$")


def bilibili_bvid(aid: str) -> str:
    """The BV ID of an av number; MediaCrawler's Bilibili detail mode only parses BV links."""
    value = (_BV_MAX_AID | int(aid)) ^ _BV_XOR
    digits = ["B", "V", "1", *"0" * 9]
    for index in range(len(digits) - 1, 2, -1):
        value, digit = divmod(value, len(_BV_ALPHABET))
        digits[index] = _BV_ALPHABET[digit]
    for left, right in _BV_SWAPS:
        digits[left], digits[right] = digits[right], digits[left]
    return "".join(digits)


def bilibili_aid(bvid: str) -> str:
    if not _BV.match(bvid):
        raise ValueError(f"not a BV ID: {bvid}")
    digits = list(bvid)
    for left, right in _BV_SWAPS:
        digits[left], digits[right] = digits[right], digits[left]
    value = 0
    for char in digits[3:]:
        value = value * len(_BV_ALPHABET) + _BV_ALPHABET.index(char)
    return str((value & (_BV_MAX_AID - 1)) ^ _BV_XOR)


def bilibili_detail_target(target: str) -> str:
    """A link MediaCrawler can open: av links and bare av numbers become BV links, others pass through."""
    value = target.strip()
    match = re.fullmatch(r"(?:https?://(?:www\.|m\.)?bilibili\.com/video/)?av(\d+)/?(?:\?.*)?", value, re.I)
    if match or value.isdigit():
        aid = match.group(1) if match else value
        return f"https://www.bilibili.com/video/{bilibili_bvid(aid)}"
    return value


def zhihu_url(content_type: str, content_id: str, question_id: str = "") -> str:
    if content_type == "answer":
        if question_id:
            return f"https://www.zhihu.com/question/{question_id}/answer/{content_id}"
        return f"https://www.zhihu.com/answer/{content_id}"
    if content_type == "article":
        return f"https://zhuanlan.zhihu.com/p/{content_id}"
    return f"https://www.zhihu.com/zvideo/{content_id}"


def nowcoder_url(post_id: str) -> str:
    """Discussions have numeric IDs; moments (动态) have 32-character hex UUIDs."""
    if post_id.isdigit():
        return f"https://www.nowcoder.com/discuss/{post_id}"
    return f"https://www.nowcoder.com/feed/main/detail/{post_id}"


_POST_URLS = (
    (re.compile(r"^(?:www\.)?xiaohongshu\.com/(?:explore|discovery/item)/([0-9a-zA-Z]+)$"), "xhs", "{}"),
    (re.compile(r"^(?:www\.)?douyin\.com/(?:video|note)/(\d+)$"), "dy", "{}"),
    (re.compile(r"^(?:www\.)?bilibili\.com/video/av(\d+)$"), "bili", "{}"),
    (re.compile(r"^(?:www\.)?zhihu\.com/(?:question/\d+/)?answer/(\d+)$"), "zhihu", "answer-{}"),
    (re.compile(r"^zhuanlan\.zhihu\.com/p/(\d+)$"), "zhihu", "article-{}"),
    (re.compile(r"^(?:www\.)?zhihu\.com/zvideo/(\d+)$"), "zhihu", "zvideo-{}"),
    (re.compile(r"^(?:www\.|m\.)?nowcoder\.com/discuss/(\d+)$"), "nowcoder", "{}"),
    (re.compile(r"^(?:www\.|m\.)?nowcoder\.com/feed/main/detail/([0-9a-fA-F]{32})$"), "nowcoder", "{}"),
)


def post_key(url: str | None) -> str | None:
    """The Gleaner post key (`platform:post_id`) behind a post URL, or None for other pages."""
    parts = urlsplit((url or "").strip())
    if parts.scheme.lower() not in {"http", "https"}:
        return None
    location = f"{parts.netloc.lower()}{parts.path.rstrip('/')}"
    for pattern, platform, template in _POST_URLS:
        if match := pattern.match(location):
            post_id = match.group(1).lower() if platform == "nowcoder" else match.group(1)
            return f"{platform}:{template.format(post_id)}"
    if match := re.match(r"^(?:www\.|m\.)?bilibili\.com/video/(BV1[0-9A-Za-z]{9})$", location):
        return f"bili:{bilibili_aid(match.group(1))}"
    return None
