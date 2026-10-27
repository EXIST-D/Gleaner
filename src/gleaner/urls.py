from __future__ import annotations

from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

_DROPPED_QUERY_KEYS = {
    "xsec_token",
    "xsec_source",
    "spm",
    "from",
    "ref",
    "source",
    "share_id",
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
