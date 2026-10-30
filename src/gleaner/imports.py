"""Which posts the collected bank already holds, so a pack is never imported or downloaded twice.

The bank lists its committed sources by URL (`BankReader.held`). A Gleaner post whose URL is there was imported:
if it never went through a pack (someone imported it by hand) it is recorded as such, and a pack whose posts are
not all in the bank is still waiting for its import.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from gleaner.bank import Held
from gleaner.packer import MANUAL_IMPORT, OUTSIDE_PACKS
from gleaner.store import Store
from gleaner.timeutil import iso


@dataclass
class ImportReport:
    holding: int = 0
    """Gleaner posts the bank holds."""
    questions: int = 0
    adopted: list[str] = field(default_factory=list)
    """Unpacked posts the bank holds: imported by hand, now recorded as such."""
    waiting: dict[str, list[str]] = field(default_factory=dict)
    """Pack id -> keys of its posts the bank does not hold yet."""
    pack_sizes: dict[str, int] = field(default_factory=dict)

    def as_dict(self) -> dict:
        return {
            "holding": self.holding,
            "questions": self.questions,
            "adopted": self.adopted,
            "waiting": {
                pack: {"posts": self.pack_sizes[pack], "missing": keys} for pack, keys in self.waiting.items()
            },
        }


def sync_imports(store: Store, held: dict[str, Held], *, now: datetime, adopt: bool = True) -> ImportReport:
    """Compare every post with what the bank holds; with adopt, record unpacked posts it holds as imported by hand."""
    report = ImportReport()
    for record in store.posts():
        known = held.get(record.post.url)
        if known:
            report.holding += 1
            report.questions += known.questions
            if not record.packed_in:
                report.adopted.append(record.post.key)
        if record.packed_in and record.packed_in not in OUTSIDE_PACKS:
            report.pack_sizes[record.packed_in] = report.pack_sizes.get(record.packed_in, 0) + 1
            if not known:
                report.waiting.setdefault(record.packed_in, []).append(record.post.key)
    if adopt and report.adopted:
        store.mark_packed(report.adopted, pack_id=MANUAL_IMPORT, now=iso(now))
    return report
