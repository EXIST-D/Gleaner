from __future__ import annotations

import math
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path

from gleaner.adapters.common import ParseReport
from gleaner.adapters.exports import copy_export, iter_posts
from gleaner.config import Config
from gleaner.errors import GleanerError
from gleaner.models import PLATFORMS
from gleaner.screening import Screener
from gleaner.sources import mediacrawler
from gleaner.sources.base import Fetcher
from gleaner.sources.mediacrawler import MediaCrawlerDriver, Runner, search_notes_per_keyword
from gleaner.sources.nowcoder import NowcoderFetcher
from gleaner.store import Store
from gleaner.timeutil import iso, new_id, parse_iso

CRAWL_PLATFORMS = (*mediacrawler.PLATFORMS, "nowcoder")
SOURCE_XHS = "mediacrawler-xhs"


def source_name(platform: str) -> str:
    """Run and checkpoint source of a platform."""
    return "nowcoder" if platform == "nowcoder" else f"mediacrawler-{platform}"


def platform_of(source: str) -> str | None:
    platform = source.removeprefix("mediacrawler-")
    return platform if platform in PLATFORMS else None


def page_size(platform: str) -> int | None:
    """Posts per search page, or None when the source saves exactly the requested number."""
    spec = mediacrawler.PLATFORMS.get(platform)
    return spec.page_size if spec else None


def default_max_notes(config: Config, platform: str) -> int:
    return config.nowcoder.max_notes if platform == "nowcoder" else config.mediacrawler.max_notes


def min_interval_minutes(config: Config, platform: str) -> int:
    if platform == "nowcoder":
        return config.nowcoder.min_interval_minutes
    return config.mediacrawler.min_interval_minutes


def _check_platform(platform: str) -> None:
    if platform not in CRAWL_PLATFORMS:
        raise GleanerError(f"不支持抓取 {platform}，可选：{' / '.join(CRAWL_PLATFORMS)}")


def fetcher_for(
    config: Config,
    platform: str,
    *,
    runner: Runner | None = None,
    on_line: Callable[[str], None] | None = None,
    should_stop: Callable[[], bool] | None = None,
    echo: bool = True,
) -> Fetcher:
    """MediaCrawler for the platforms it crawls, Gleaner's own reader for Nowcoder."""
    _check_platform(platform)
    if platform == "nowcoder":
        return NowcoderFetcher(config.nowcoder, on_line=on_line, should_stop=should_stop, echo=echo)
    return MediaCrawlerDriver(config.mediacrawler, runner, platform=platform, echo=echo)


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


def planned_notes(
    config: Config, *, platform: str = "xhs", keywords: list[str], targets: list[str], max_notes: int | None
) -> int:
    """Upper bound of posts one crawl requests from the platform."""
    if targets:
        return len(targets)
    per_keyword = max_notes or default_max_notes(config, platform)
    size = page_size(platform)
    return (search_notes_per_keyword(per_keyword, size) if size else per_keyword) * len(keywords)


def run_dirs(config: Config, run_id: str) -> tuple[Path, Path]:
    run_dir = config.workspace / "runs" / run_id
    return run_dir, run_dir / "raw"


def crawl(
    store: Store,
    config: Config,
    fetcher: Fetcher,
    *,
    platform: str,
    keywords: list[str],
    targets: list[str],
    max_notes: int | None,
    force: bool,
    now: datetime,
) -> CrawlOutcome:
    _check_platform(platform)
    mode = "detail" if targets else "search"
    if mode == "search" and not keywords:
        raise GleanerError("没有关键词：用 --keyword 指定，或在 gleaner.toml 的 keywords 里配置")
    check_interval(store, config, now, force, platform=platform)

    run_id = new_id("run", now)
    run_dir, raw_dir = run_dirs(config, run_id)
    params = {
        "platform": platform,
        "keywords": keywords,
        "targets": targets,
        "max_notes": max_notes or default_max_notes(config, platform),
        "planned_notes": planned_notes(
            config, platform=platform, keywords=keywords, targets=targets, max_notes=max_notes
        ),
    }
    store.begin_run(
        run_id,
        source=source_name(platform),
        mode=mode,
        params=params,
        output_dir=_rel(raw_dir, config),
        now=iso(now),
    )
    result = fetcher.run(
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
        platforms = copy_export(source, raw_dir)
    except FileNotFoundError as exc:
        raise GleanerError(str(exc)) from exc
    store.begin_run(
        run_id,
        source=source_name(platforms[0]) if len(platforms) == 1 else "mediacrawler",
        mode="import",
        params={"from": str(source), "platforms": platforms},
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
                store.advance_checkpoint(source_name(post.platform), keyword, post.published_at, now=stamp)
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


def seconds_until_next_crawl(store: Store, config: Config, now: datetime, *, platform: str = "xhs") -> int:
    """0 when a crawl of the platform may start now, else the seconds left of its minimum interval."""
    minutes = min_interval_minutes(config, platform)
    if minutes <= 0:
        return 0
    source = source_name(platform)
    crawls = [run for mode in ("search", "detail") if (run := store.last_run(source, mode))]
    if not crawls:
        return 0
    last_started = max(parse_iso(run.started_at) for run in crawls)
    wait = (last_started + timedelta(minutes=minutes) - now).total_seconds()
    return max(0, math.ceil(wait))


def check_interval(
    store: Store, config: Config, now: datetime, force: bool, *, platform: str = "xhs"
) -> None:
    if force:
        return
    wait = seconds_until_next_crawl(store, config, now, platform=platform)
    if wait > 0:
        raise GleanerError(
            f"距离上次抓取{PLATFORMS[platform].label}不到 {min_interval_minutes(config, platform)} 分钟，"
            f"请 {wait // 60 + 1} 分钟后再试（或加 --force）"
        )


def _rel(path: Path, config: Config) -> str:
    return path.relative_to(config.workspace).as_posix()
