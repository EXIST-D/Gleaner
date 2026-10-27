from __future__ import annotations

import secrets
from datetime import UTC, datetime, timedelta, timezone

CHINA_TZ = timezone(timedelta(hours=8), "Asia/Shanghai")
"""Mainland China has no daylight saving time, so a fixed offset needs no tz database."""


def utc_now() -> datetime:
    return datetime.now(UTC)


def iso(moment: datetime) -> str:
    return moment.astimezone(UTC).isoformat(timespec="seconds")


def parse_iso(value: str) -> datetime:
    moment = datetime.fromisoformat(value)
    return moment if moment.tzinfo else moment.replace(tzinfo=UTC)


def from_epoch(value: object) -> str | None:
    """Platform timestamps come in milliseconds or seconds, as numbers or strings."""
    try:
        number = int(str(value).strip())
    except (TypeError, ValueError):
        return None
    if number <= 0:
        return None
    if number > 100_000_000_000:
        number //= 1000
    try:
        return iso(datetime.fromtimestamp(number, UTC))
    except (OverflowError, OSError, ValueError):
        return None


def china_date(published_at: str | None) -> str | None:
    """The calendar date a reader in China sees on the post."""
    if not published_at:
        return None
    return parse_iso(published_at).astimezone(CHINA_TZ).date().isoformat()


def new_id(prefix: str, moment: datetime) -> str:
    return f"{prefix}-{moment.astimezone(CHINA_TZ):%Y%m%d-%H%M%S}-{secrets.token_hex(2)}"
