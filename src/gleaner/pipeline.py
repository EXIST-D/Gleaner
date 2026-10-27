from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path

from gleaner.adapters.xhs import ParseReport, copy_export, iter_posts
from gleaner.config import Config
from gleaner.errors import GleanerError
from gleaner.screening import Screener
from gleaner.sources.mediacrawler import MediaCrawlerDriver, search_notes_per_keyword
from gleaner.store import Store
from gleaner.timeutil import iso, new_id, parse_iso

SOURCE_XHS = "mediacrawler-xhs"


@dataclass
class ImportSummary:
    run_id: str
    records: int = 0
    new: int = 0
    updated: int = 0
    unchanged: int = 0
    skipped: int = 0
    labels: Counter = field(default_factory=Counter)
    errors: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "run_id": self.run_id,
            "records": self.records,
            "new": self.new,
            "updated": self.updated,
            "unchanged": self.unchanged,
            "skipped": self.skipped,
            "labels": dict(self.labels),
            "errors": self.errors[:20],
        }


@dataclass
class CrawlOutcome:
    run_id: str
    status: str
    message: str
    summary: ImportSummary

    def as_dict(self) -> dict:
        return {
            "run_id": self.run_id,
            "status": self.status,
            "message": self.message,
            **self.summary.as_dict(),
        }


def planned_notes(config: Config, *, keywords: list[str], targets: list[str], max_notes: int | None) -> int:
    """Upper bound of notes one crawl requests from the platform."""
    if targets:
        return len(targets)
    return search_notes_per_keyword(max_notes or config.mediacrawler.max_notes) * len(keywords)


def run_dirs(config: Config, run_id: str) -> tuple[Path, Path]:
    run_dir = config.workspace / "runs" / run_id
    return run_dir, run_dir / "raw"


def crawl_xhs(
    store: Store,
    config: Config,
    driver: MediaCrawlerDriver,
    *,
    keywords: list[str],
    targets: list[str],
    max_notes: int | None,
    force: bool,
    now: datetime,
) -> CrawlOutcome:
    mode = "detail" if targets else "search"
    if mode == "search" and not keywords:
        raise GleanerError("没有关键词：用 --keyword 指定，或在 gleaner.toml 的 keywords 里配置")
    _check_interval(store, config, now, force)

    run_id = new_id("run", now)
    run_dir, raw_dir = run_dirs(config, run_id)
    params = {
        "keywords": keywords,
        "targets": targets,
        "max_notes": max_notes or config.mediacrawler.max_notes,
        "planned_notes": planned_notes(config, keywords=keywords, targets=targets, max_notes=max_notes),
    }
    store.begin_run(
        run_id, source=SOURCE_XHS, mode=mode, params=params, output_dir=_rel(raw_dir, config), now=iso(now)
    )
    result = driver.run(
        mode=mode,
        output_dir=raw_dir,
        log_dir=run_dir,
        keywords=keywords,
        targets=targets,
        max_notes=max_notes,
    )
    summary = import_raw(store, config, run_id=run_id, raw_dir=raw_dir, now=now)
    store.finish_run(
        run_id, status=result.status, message=result.message, counts=summary.as_dict(), now=iso(now)
    )
    return CrawlOutcome(run_id, result.status, result.message, summary)


def import_export(store: Store, config: Config, source: Path, *, now: datetime) -> CrawlOutcome:
    run_id = new_id("run", now)
    _, raw_dir = run_dirs(config, run_id)
    try:
        copy_export(source, raw_dir)
    except FileNotFoundError as exc:
        raise GleanerError(str(exc)) from exc
    store.begin_run(
        run_id,
        source=SOURCE_XHS,
        mode="import",
        params={"from": str(source)},
        output_dir=_rel(raw_dir, config),
        now=iso(now),
    )
    summary = import_raw(store, config, run_id=run_id, raw_dir=raw_dir, now=now)
    status = "ok" if summary.records else "degraded"
    message = "" if summary.records else f"{source} 里没有可导入的帖子记录"
    store.finish_run(run_id, status=status, message=message, counts=summary.as_dict(), now=iso(now))
    return CrawlOutcome(run_id, status, message, summary)


def import_raw(store: Store, config: Config, *, run_id: str, raw_dir: Path, now: datetime) -> ImportSummary:
    summary = ImportSummary(run_id)
    report = ParseReport()
    stamp = iso(now)
    for post in iter_posts(raw_dir, config.workspace, report):
        outcome = store.upsert_post(post, run_id=run_id, now=stamp)
        setattr(summary, outcome, getattr(summary, outcome) + 1)
        if post.published_at:
            for keyword in post.keywords:
                store.advance_checkpoint(SOURCE_XHS, keyword, post.published_at, now=stamp)
    summary.records, summary.skipped, summary.errors = report.records, report.skipped, report.errors
    summary.labels = screen(store, config, now=now)
    return summary


def screen(store: Store, config: Config, *, now: datetime, rescreen: bool = False) -> Counter:
    """Label unlabeled posts; with rescreen, also relabel earlier rule labels. Manual labels are kept."""
    screener = Screener.from_config(config.screening)
    records = store.posts(unlabeled=True)
    if rescreen:
        records += [record for record in store.posts() if record.label_source == "rule"]
    labels: Counter = Counter()
    for record in records:
        verdict = screener.screen(record.post)
        store.set_label(
            record.post.key, verdict.label, source="rule", reasons=list(verdict.reasons), now=iso(now)
        )
        labels[verdict.label] += 1
    return labels


def _check_interval(store: Store, config: Config, now: datetime, force: bool) -> None:
    minutes = config.mediacrawler.min_interval_minutes
    if force or minutes <= 0:
        return
    crawls = [run for mode in ("search", "detail") if (run := store.last_run(SOURCE_XHS, mode))]
    if not crawls:
        return
    last_started = max(parse_iso(run.started_at) for run in crawls)
    wait = last_started + timedelta(minutes=minutes) - now
    if wait.total_seconds() > 0:
        raise GleanerError(
            f"距离上次抓取不到 {minutes} 分钟，请 {int(wait.total_seconds() // 60) + 1} 分钟后再试（或加 --force）"
        )


def _rel(path: Path, config: Config) -> str:
    return path.relative_to(config.workspace).as_posix()
