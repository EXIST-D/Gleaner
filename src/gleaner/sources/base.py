from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal


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
