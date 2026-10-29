from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from importlib import resources
from pathlib import Path

from gleaner.errors import GleanerError

CONFIG_NAME = "gleaner.toml"
WORKSPACE_ENV = "GLEANER_HOME"
MAX_NOTES_CAP = 40
"""Two search pages of 20 posts (four of Douyin's 10) per keyword."""
NOWCODER_MAX_SCAN_PAGES = 25
LOGIN_TYPES = ("qrcode", "phone", "cookie")


@dataclass(frozen=True)
class MediaCrawlerConfig:
    home: Path | None = None
    command: tuple[str, ...] = ("uv", "run", "main.py")
    login_type: str = "qrcode"
    max_notes: int = 20
    timeout_seconds: int = 900
    min_interval_minutes: int = 5


@dataclass(frozen=True)
class NowcoderConfig:
    max_notes: int = 20
    scan_pages: int = 5
    request_interval_seconds: float = 3.0
    download_images: bool = True
    timeout_seconds: int = 600
    min_interval_minutes: int = 5


@dataclass(frozen=True)
class ScreeningConfig:
    interview_terms: tuple[str, ...] = ()
    event_terms: tuple[str, ...] = ()
    promo_terms: tuple[str, ...] = ()
    strong_promo_terms: tuple[str, ...] = ()
    collection_terms: tuple[str, ...] = ()
    recording_terms: tuple[str, ...] = ()


@dataclass(frozen=True)
class MediaConfig:
    """Bilibili recordings: downloading the video and turning it into audio for transcription."""

    ffmpeg: str = "ffmpeg"
    """Command name or path; MediaCrawler needs it on PATH to merge Bilibili's separate audio and video."""
    download_timeout_seconds: int = 3600


@dataclass(frozen=True)
class HandoffConfig:
    bank: Path | None = None
    ibank: Path | None = None
    python: str = "python3"
    asr_python: str = ""
    """Python with faster-whisper installed, for `ibank media-transcribe`; empty means `python`."""
    asr_model: str = "large-v3-turbo"

    @property
    def transcribe_python(self) -> str:
        return self.asr_python or self.python


@dataclass(frozen=True)
class Config:
    workspace: Path
    keywords: tuple[str, ...] = ()
    mediacrawler: MediaCrawlerConfig = field(default_factory=MediaCrawlerConfig)
    nowcoder: NowcoderConfig = field(default_factory=NowcoderConfig)
    screening: ScreeningConfig = field(default_factory=ScreeningConfig)
    media: MediaConfig = field(default_factory=MediaConfig)
    handoff: HandoffConfig = field(default_factory=HandoffConfig)


def resolve_workspace(explicit: str | None) -> Path:
    value = explicit or os.environ.get(WORKSPACE_ENV)
    if not value:
        raise GleanerError(f"请用 --workspace 指定工作区目录，或设置环境变量 {WORKSPACE_ENV}")
    return Path(value).expanduser().resolve()


def template_text() -> str:
    return resources.files("gleaner").joinpath("templates", CONFIG_NAME).read_text(encoding="utf-8")


def load_config(workspace: Path) -> Config:
    path = workspace / CONFIG_NAME
    if not path.is_file():
        raise GleanerError(f"工作区 {workspace} 还没有初始化，请先运行 gleaner --workspace {workspace} init")
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as exc:
        raise GleanerError(f"{path} 不是合法的 TOML：{exc}") from exc
    return parse_config(data, workspace)


def parse_config(data: dict, workspace: Path) -> Config:
    crawler = _table(data, "mediacrawler")
    nowcoder = _table(data, "nowcoder")
    screening = _table(data, "screening")
    media = _table(data, "media")
    handoff = _table(data, "handoff")

    max_notes = _int(crawler, "max_notes", 20, "mediacrawler.max_notes")
    if not 1 <= max_notes <= MAX_NOTES_CAP:
        raise GleanerError(f"mediacrawler.max_notes 必须在 1 到 {MAX_NOTES_CAP} 之间")
    login_type = str(crawler.get("login_type", "qrcode"))
    if login_type not in LOGIN_TYPES:
        raise GleanerError(f"mediacrawler.login_type 只能是 {' / '.join(LOGIN_TYPES)}")
    command = tuple(_strings(crawler, "command", ["uv", "run", "main.py"], "mediacrawler.command"))
    if not command:
        raise GleanerError("mediacrawler.command 不能为空")

    nowcoder_notes = _int(nowcoder, "max_notes", 20, "nowcoder.max_notes")
    if not 1 <= nowcoder_notes <= MAX_NOTES_CAP:
        raise GleanerError(f"nowcoder.max_notes 必须在 1 到 {MAX_NOTES_CAP} 之间")
    scan_pages = _int(nowcoder, "scan_pages", 5, "nowcoder.scan_pages")
    if not 1 <= scan_pages <= NOWCODER_MAX_SCAN_PAGES:
        raise GleanerError(f"nowcoder.scan_pages 必须在 1 到 {NOWCODER_MAX_SCAN_PAGES} 之间")
    interval = nowcoder.get("request_interval_seconds", 3)
    if isinstance(interval, bool) or not isinstance(interval, int | float) or interval < 1:
        raise GleanerError("nowcoder.request_interval_seconds 必须是不小于 1 的数字")
    download_images = nowcoder.get("download_images", True)
    if not isinstance(download_images, bool):
        raise GleanerError("nowcoder.download_images 必须是 true 或 false")

    return Config(
        workspace=workspace,
        keywords=tuple(_keywords(_strings(data, "keywords", [], "keywords"))),
        mediacrawler=MediaCrawlerConfig(
            home=_path(crawler.get("home"), workspace),
            command=command,
            login_type=login_type,
            max_notes=max_notes,
            timeout_seconds=max(60, _int(crawler, "timeout_seconds", 900, "mediacrawler.timeout_seconds")),
            min_interval_minutes=max(
                0, _int(crawler, "min_interval_minutes", 5, "mediacrawler.min_interval_minutes")
            ),
        ),
        nowcoder=NowcoderConfig(
            max_notes=nowcoder_notes,
            scan_pages=scan_pages,
            request_interval_seconds=float(interval),
            download_images=download_images,
            timeout_seconds=max(60, _int(nowcoder, "timeout_seconds", 600, "nowcoder.timeout_seconds")),
            min_interval_minutes=max(
                0, _int(nowcoder, "min_interval_minutes", 5, "nowcoder.min_interval_minutes")
            ),
        ),
        screening=ScreeningConfig(
            **{
                name: tuple(_strings(screening, name, [], f"screening.{name}"))
                for name in ScreeningConfig.__dataclass_fields__
            }
        ),
        media=MediaConfig(
            ffmpeg=_command(media.get("ffmpeg"), workspace) or "ffmpeg",
            download_timeout_seconds=max(
                300, _int(media, "download_timeout_seconds", 3600, "media.download_timeout_seconds")
            ),
        ),
        handoff=HandoffConfig(
            bank=_path(handoff.get("bank"), workspace),
            ibank=_path(handoff.get("ibank"), workspace),
            python=str(handoff.get("python") or "python3"),
            asr_python=_command(handoff.get("asr_python"), workspace),
            asr_model=str(handoff.get("asr_model") or "large-v3-turbo"),
        ),
    )


def validate_keywords(values: list[str]) -> list[str]:
    return _keywords(values)


def _keywords(values: list[str]) -> list[str]:
    cleaned = [value.strip() for value in values if value.strip()]
    for value in cleaned:
        if "," in value or "，" in value:
            raise GleanerError(f"关键词不能包含逗号（MediaCrawler 用逗号分隔关键词）：{value}")
    return list(dict.fromkeys(cleaned))


def _table(data: dict, name: str) -> dict:
    value = data.get(name, {})
    if not isinstance(value, dict):
        raise GleanerError(f"[{name}] 必须是一个表")
    return value


def _int(table: dict, key: str, default: int, label: str) -> int:
    value = table.get(key, default)
    if isinstance(value, bool) or not isinstance(value, int):
        raise GleanerError(f"{label} 必须是整数")
    return value


def _strings(table: dict, key: str, default: list[str], label: str) -> list[str]:
    value = table.get(key, default)
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise GleanerError(f"{label} 必须是字符串列表")
    return value


def _command(value: object, workspace: Path) -> str:
    """A bare command name stays as is (looked up on PATH); anything with a separator is a path.

    Symlinks are kept: a venv's python and an ffmpeg link only work under their own names.
    """
    text = str(value or "").strip()
    if not text or ("/" not in text and "\\" not in text):
        return text
    path = Path(text).expanduser()
    return os.path.normpath(path if path.is_absolute() else workspace / path)


def _path(value: object, workspace: Path) -> Path | None:
    if not value:
        return None
    path = Path(str(value)).expanduser()
    return path if path.is_absolute() else (workspace / path).resolve()
