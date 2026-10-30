from __future__ import annotations

from collections.abc import Collection, Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Literal, Protocol


@dataclass(frozen=True)
class FetchResult:
    """Outcome of one crawler run. A degraded run may still have produced usable output."""

    status: Literal["ok", "degraded"]
    output_dir: Path
    message: str = ""
    returncode: int | None = None

    @property
    def ok(self) -> bool:
        return self.status == "ok"


class Fetcher(Protocol):
    """One source. `run` writes raw output under output_dir in the layout `adapters/exports.py` reads.

    For an incremental search, since is when these keywords were last searched successfully (less some
    overlap) and known holds the post IDs already saved; a source uses what it can of them.
    """

    def problems(self) -> list[str]: ...

    def run(
        self,
        *,
        mode: str,
        output_dir: Path,
        log_dir: Path,
        keywords: Sequence[str] = (),
        targets: Sequence[str] = (),
        max_notes: int | None = None,
        since: datetime | None = None,
        known: Collection[str] = (),
    ) -> FetchResult: ...
