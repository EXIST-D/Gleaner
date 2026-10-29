"""Turn a downloaded recording into the audio file a pack hands over for transcription.

Remote interviews are recorded on the candidate's side, so the interviewer is often much quieter. Interview
Bank's transcriber drops quiet speech as silence; normalizing loudness first brings most of it back.
"""

from __future__ import annotations

import shutil
import subprocess
from collections.abc import Callable
from pathlib import Path

from gleaner.errors import GleanerError

AUDIO_NAME = "audio.flac"
SAMPLE_RATE = 16000
LOUDNESS_FILTER = "dynaudnorm=f=150:g=15"
TIMEOUT_SECONDS = 1800

Extractor = Callable[[Path, Path], None]
"""Writes the audio of a video (first path) to a file (second path)."""


def find_ffmpeg(command: str) -> str | None:
    return shutil.which(command)


def audio_command(ffmpeg: str, source: Path, target: Path) -> list[str]:
    return [
        ffmpeg,
        "-nostdin",
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-i",
        str(source),
        "-vn",
        "-ac",
        "1",
        "-ar",
        str(SAMPLE_RATE),
        "-af",
        LOUDNESS_FILTER,
        "-sample_fmt",
        "s16",
        "-c:a",
        "flac",
        str(target),
    ]


def ffmpeg_extractor(command: str) -> Extractor:
    ffmpeg = find_ffmpeg(command)
    if ffmpeg is None:
        raise GleanerError(
            f"找不到 ffmpeg（{command}），打包录音需要它提取音频；装好后在 gleaner.toml 的 [media] ffmpeg 写上路径"
        )

    def extract(source: Path, target: Path) -> None:
        try:
            result = subprocess.run(
                audio_command(ffmpeg, source, target),
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=TIMEOUT_SECONDS,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise GleanerError(f"ffmpeg 处理 {source.name} 超过 {TIMEOUT_SECONDS} 秒") from exc
        if result.returncode != 0 or not target.is_file():
            detail = (result.stderr or "").strip().splitlines()[-1:] or ["没有输出"]
            raise GleanerError(f"ffmpeg 提取 {source} 的音频失败：{detail[0]}")

    return extract
