"""`gleaner auto`: crawl every platform, download new interview recordings and pack what is ready.

The steps are the ones `crawl`, `media` and `pack` run by hand, chained so a scheduler can start them.
A step that cannot finish (login expired, rate limit, a failed download) is recorded and the next one
still runs; only the person's QR code scan and the Agent's import are left out.
"""

from __future__ import annotations

import json
import os
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from gleaner.audio import Extractor
from gleaner.bank import BankReader
from gleaner.config import Config
from gleaner.errors import GleanerError
from gleaner.imports import ImportReport, sync_imports
from gleaner.models import PLATFORMS
from gleaner.packer import PackResult, build_pack, packable
from gleaner.pipeline import (
    CRAWL_PLATFORMS,
    crawl,
    fetcher_for,
    min_interval_minutes,
    recordings_to_download,
    seconds_until_next_crawl,
)
from gleaner.sources import mediacrawler
from gleaner.sources.base import Fetcher
from gleaner.store import Store
from gleaner.timeutil import utc_now

LOCK_NAME = "auto.lock"
LAST_NAME = "last-auto.json"
STALE_LOCK_SECONDS = 12 * 3600
"""A lock older than this is left over from a crashed run; a run with a hundred-minute download is far shorter."""
AUTO_PLATFORMS = ("nowcoder", *mediacrawler.PLATFORMS)
"""Default order: Nowcoder first, it needs no login and cannot stop at a prompt."""
FAILED = ("degraded", "failed")

FetcherFactory = Callable[..., Fetcher]


@dataclass
class Step:
    kind: str
    """crawl, media or pack."""
    platform: str
    status: str
    """ok, degraded (ran, with a problem), failed (could not run) or skipped (nothing to do or too soon)."""
    message: str = ""
    run_id: str | None = None
    records: int = 0
    new: int = 0
    media: int = 0
    """Audio and video files a media step downloaded."""
    post: str | None = None
    """The post a media step downloaded."""

    def as_dict(self) -> dict:
        return {key: value for key, value in self.__dict__.items() if value not in (None, "")}


@dataclass
class AutoOutcome:
    steps: list[Step] = field(default_factory=list)
    pack: PackResult | None = None
    held_recordings: int = 0
    """Recordings flagged but not downloaded, so not packed yet."""
    imports: ImportReport | None = None
    """What the bank holds, when it could be asked: packs still waiting for their import are in `waiting`."""

    @property
    def degraded(self) -> bool:
        return any(step.status in FAILED for step in self.steps)

    def as_dict(self) -> dict:
        return {
            "ok": not self.degraded,
            "steps": [step.as_dict() for step in self.steps],
            "pack": self.pack.as_dict() if self.pack else None,
            "held_recordings": self.held_recordings,
            "imports": self.imports.as_dict() if self.imports else None,
        }


def chosen_platforms(config: Config, requested: list[str] | None = None) -> list[str]:
    """The platforms to crawl: the requested ones, else `[auto] platforms`, else all."""
    platforms = list(dict.fromkeys(requested or config.auto.platforms or AUTO_PLATFORMS))
    unknown = [code for code in platforms if code not in CRAWL_PLATFORMS]
    if unknown:
        raise GleanerError(f"不支持抓取 {'、'.join(unknown)}，可选：{' / '.join(CRAWL_PLATFORMS)}")
    return platforms


def ensure_not_running(workspace: Path) -> None:
    """Manual crawls, downloads and packs must not overlap a `gleaner auto` run: they would pack the same posts twice."""
    try:
        age = time.time() - (workspace / LOCK_NAME).stat().st_mtime
    except FileNotFoundError:
        return
    if age < STALE_LOCK_SECONDS:
        raise GleanerError(
            f"gleaner auto 正在运行（{int(age // 60)} 分钟前开始），等它结束再手动抓取、下载或打包；"
            f"确认没有在运行的话可以删掉 {workspace / LOCK_NAME}"
        )


def last_auto(workspace: Path) -> dict | None:
    """The summary of the latest automatic run, for the web console; None before the first run."""
    try:
        return json.loads((workspace / LAST_NAME).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _write_last(workspace: Path, started: datetime, finished: datetime, outcome: AutoOutcome) -> None:
    summary = {"started_at": started.isoformat(), "finished_at": finished.isoformat(), **outcome.as_dict()}
    temporary = workspace / f"{LAST_NAME}.tmp"
    temporary.write_text(json.dumps(summary, ensure_ascii=False), encoding="utf-8")
    os.replace(temporary, workspace / LAST_NAME)


@contextmanager
def run_lock(workspace: Path) -> Iterator[None]:
    """One automatic run at a time: two runs would pack the same posts twice and double count their questions."""
    path = workspace / LOCK_NAME
    for _ in range(2):
        try:
            handle = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            try:
                age = time.time() - path.stat().st_mtime
            except FileNotFoundError:
                continue
            if age < STALE_LOCK_SECONDS:
                raise GleanerError(
                    f"上一次 gleaner auto 还在运行（{path.name} 创建于 {int(age // 60)} 分钟前）；"
                    f"确认没有在运行后可以删掉 {path}"
                ) from None
            path.unlink(missing_ok=True)
            continue
        os.close(handle)
        break
    else:
        raise GleanerError(f"无法创建 {path}")
    try:
        yield
    finally:
        path.unlink(missing_ok=True)


def run_auto(
    store: Store,
    config: Config,
    *,
    platforms: list[str] | None = None,
    download: bool = True,
    pack: bool = True,
    force: bool = False,
    full: bool = False,
    attended: bool = False,
    echo: bool = False,
    say: Callable[[str], None] = lambda text: None,
    clock: Callable[[], datetime] = utc_now,
    sleep: Callable[[float], None] = time.sleep,
    fetcher_factory: FetcherFactory = fetcher_for,
    extract: Extractor | None = None,
    bank: BankReader | None = None,
) -> AutoOutcome:
    """Crawl, download recordings, pack. Unattended unless `attended`: a run never waits for a QR code scan.

    `say` receives a line for each step as it starts; `echo` shows MediaCrawler's own output.
    """
    selected = chosen_platforms(config, platforms)
    if not config.keywords:
        raise GleanerError("没有关键词：在 gleaner.toml 的 keywords 里配置")
    outcome = AutoOutcome()
    started = clock()

    def factory(platform: str, **options) -> Fetcher:
        return fetcher_factory(config, platform, echo=echo, unattended=not attended, **options)

    with run_lock(config.workspace):
        for platform in selected:
            say(f"抓取{PLATFORMS[platform].label}…")
            outcome.steps.append(_crawl(store, config, platform, factory, force, full, clock))
        reader = bank or BankReader(config.handoff)
        if step := _imports(store, reader, clock, outcome):
            outcome.steps.append(step)
        if download and "bili" in selected:
            blocked = any(
                step.kind == "crawl" and step.platform == "bili" and step.status in FAILED
                for step in outcome.steps
            )
            outcome.steps += _download(store, config, factory, force, blocked, say, clock, sleep)
        if pack:
            step, outcome.pack, outcome.held_recordings = _pack(store, config, clock, extract)
            outcome.steps.append(step)
        else:
            outcome.held_recordings = packable(store)[1]
        if outcome.imports:
            latest = _imports_report(store, reader, clock, adopt=False)
            if latest:
                latest.adopted = outcome.imports.adopted
                outcome.imports = latest
        _write_last(config.workspace, started, clock(), outcome)
    return outcome


def _imports_report(store: Store, reader: BankReader, clock: Callable, *, adopt: bool) -> ImportReport | None:
    if reader.problem():
        return None
    try:
        return sync_imports(store, reader.held(), now=clock(), adopt=adopt)
    except GleanerError:
        return None


def _imports(store: Store, reader: BankReader, clock: Callable, outcome: AutoOutcome) -> Step | None:
    """Ask the bank what it holds, so posts imported by hand are neither downloaded nor packed again.

    Never makes the run fail: an Interview Bank without the `sources` command only skips this step, and a bank
    that is not configured at all leaves it out.
    """
    if reader.problem():
        return None
    try:
        report = sync_imports(store, reader.held(), now=clock())
    except GleanerError as exc:
        return Step("imports", "", "skipped", f"没能读到题库里的来源，下面按没导入处理：{exc}")
    outcome.imports = report
    return Step(
        "imports", "", "ok", f"题库已有 {report.holding} 篇帖子，新记手工导入 {len(report.adopted)} 篇"
    )


def _crawl(
    store: Store, config: Config, platform: str, factory, force: bool, full: bool, clock: Callable
) -> Step:
    wait = 0 if force else seconds_until_next_crawl(store, config, clock(), platform=platform)
    if wait:
        minutes = min_interval_minutes(config, platform)
        return Step("crawl", platform, "skipped", f"距离上次抓取不到 {minutes} 分钟，下次再抓")
    try:
        result = crawl(
            store,
            config,
            factory(platform),
            platform=platform,
            keywords=list(config.keywords),
            targets=[],
            max_notes=None,
            force=force,
            now=clock(),
            incremental=not full,
        )
    except GleanerError as exc:
        return Step("crawl", platform, "failed", str(exc))
    summary = result.summary
    return Step(
        "crawl",
        platform,
        result.status,
        result.message,
        result.run_id,
        records=summary.records,
        new=summary.new,
    )


def _download(
    store: Store,
    config: Config,
    factory,
    force: bool,
    blocked: bool,
    say: Callable[[str], None],
    clock: Callable,
    sleep: Callable,
) -> list[Step]:
    """Download up to `[auto] max_recordings` flagged recordings, one run each; stop at the first failure.

    The platform's minimum interval applies between runs, so a download after a search waits for it.
    """
    pending = recordings_to_download(store)[: config.auto.max_recordings]
    if not pending:
        return []
    if blocked:
        return [Step("media", "bili", "skipped", f"B 站这次抓取失败，先不下载 {len(pending)} 个录音")]
    steps: list[Step] = []
    for record in pending:
        post = record.post
        wait = 0 if force else seconds_until_next_crawl(store, config, clock(), platform=post.platform)
        if wait:
            say(f"{PLATFORMS[post.platform].label}两次抓取要间隔一会儿，等待 {wait} 秒后下载录音…")
            sleep(wait)
        say(f"下载录音 {post.key}（长视频要十几分钟）…")
        try:
            result = crawl(
                store,
                config,
                factory(post.platform, media=True),
                platform=post.platform,
                keywords=[],
                targets=[post.url],
                max_notes=None,
                force=force,
                now=clock(),
                media=True,
            )
        except GleanerError as exc:
            steps.append(Step("media", post.platform, "failed", str(exc), post=post.key))
            break
        steps.append(
            Step(
                "media",
                post.platform,
                result.status,
                result.message,
                result.run_id,
                records=result.summary.records,
                media=result.summary.media,
                post=post.key,
            )
        )
        if result.status != "ok":
            break
    return steps


def _pack(
    store: Store, config: Config, clock: Callable, extract: Extractor | None
) -> tuple[Step, PackResult | None, int]:
    ready, held = packable(store)
    if not ready:
        return Step("pack", "", "skipped", "没有新的可打包帖子"), None, held
    try:
        result = build_pack(store, config, now=clock(), extract=extract)
    except GleanerError as exc:
        return Step("pack", "", "failed", str(exc)), None, held
    return Step("pack", "", "ok", f"{result.pack_id}：{result.posts} 篇"), result, held


_STATUS_TITLES = {"ok": "完成", "degraded": "降级", "failed": "没能运行", "skipped": "跳过"}


def outcome_lines(outcome: AutoOutcome) -> list[str]:
    """The run for the terminal: one line per step, then the pack to hand over and what still waits."""
    lines = []
    for step in outcome.steps:
        if step.kind == "crawl":
            what = f"抓取{PLATFORMS[step.platform].label}"
            detail = f"读到 {step.records} 条，新增 {step.new}" if step.status in ("ok", "degraded") else ""
        elif step.kind == "media":
            what = f"下载录音 {step.post}"
            detail = f"下载到 {step.media} 个音视频" if step.status in ("ok", "degraded") else ""
        elif step.kind == "imports":
            what, detail = "对照题库", ""
        else:
            what, detail = "打包", ""
        shown = step.message if step.status != "ok" or step.kind in ("pack", "imports") else ""
        lines.append(
            f"{what}：{_STATUS_TITLES[step.status]}"
            + "".join(f"；{part}" for part in (detail, shown) if part)
        )
    if outcome.pack:
        pack = outcome.pack
        recordings = f"，转写录音 {pack.media_posts}" if pack.media_posts else ""
        lines += [
            f"素材包 {pack.pack_id}：{pack.posts} 篇（看图 {pack.image_posts}，读正文 {pack.text_posts}{recordings}）",
            f"把 {pack.path / 'HANDOFF.md'} 交给装了 interview-bank 的 Agent 导入采集题库。",
        ]
    if outcome.held_recordings:
        lines.append(
            f"还有 {outcome.held_recordings} 个录音没下载，所以没打包；下载后再运行 gleaner auto 或 gleaner pack。"
        )
    if outcome.imports and outcome.imports.waiting:
        waiting = outcome.imports.waiting
        lines.append(
            f"{len(waiting)} 个素材包还在等导入："
            + "；".join(
                f"{pack}（{len(keys)}/{outcome.imports.pack_sizes[pack]} 篇不在题库里）"
                for pack, keys in waiting.items()
            )
        )
    return lines
