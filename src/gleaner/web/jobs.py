"""One crawl at a time, run in a background thread and observed by polling."""

from __future__ import annotations

import subprocess
import threading
import traceback
from collections.abc import Callable, Sequence
from datetime import datetime
from pathlib import Path
from typing import Protocol

from gleaner.config import MAX_NOTES_CAP, Config, load_config, validate_keywords
from gleaner.errors import GleanerError
from gleaner.pipeline import (
    CRAWL_PLATFORMS,
    check_interval,
    check_recordings,
    crawl,
    default_max_notes,
    fetcher_for,
    planned_notes,
)
from gleaner.sources.base import Fetcher, FetchResult
from gleaner.sources.mediacrawler import Runner, make_runner, tool_dirs
from gleaner.store import Store
from gleaner.timeutil import iso, new_id, utc_now
from gleaner.web.progress import Progress

MAX_LINES = 3000
MAX_LINE_CHARS = 400


class FetcherFactory(Protocol):
    def __call__(
        self,
        config: Config,
        platform: str,
        *,
        runner: Runner,
        on_line: Callable[[str], None],
        should_stop: Callable[[], bool],
        media: bool,
    ) -> Fetcher: ...


def default_fetcher(
    config: Config,
    platform: str,
    *,
    runner: Runner,
    on_line: Callable[[str], None],
    should_stop: Callable[[], bool],
    media: bool,
) -> Fetcher:
    return fetcher_for(
        config, platform, runner=runner, on_line=on_line, should_stop=should_stop, echo=False, media=media
    )


class CrawlJob:
    def __init__(self, job_id: str, params: dict, started_at: str, *, download: bool = True):
        self.id = job_id
        self.params = params
        self.started_at = started_at
        self.finished_at: str | None = None
        self.state = "running"
        self.outcome: dict | None = None
        self.error: str | None = None
        self.progress = Progress(
            params.get("platform", "xhs"), download=download, recordings=bool(params.get("media"))
        )
        self._lines: list[str] = []
        self._dropped = 0
        self._process: subprocess.Popen | None = None
        self._stop_requested = False
        self._lock = threading.Lock()

    @property
    def running(self) -> bool:
        return self.state == "running"

    def add_line(self, line: str) -> None:
        with self._lock:
            self.progress.feed(line)
            if len(line) > MAX_LINE_CHARS:
                line = line[:MAX_LINE_CHARS] + " …"
            self._lines.append(line)
            if len(self._lines) > MAX_LINES:
                overflow = len(self._lines) - MAX_LINES
                del self._lines[:overflow]
                self._dropped += overflow

    def attach(self, process: subprocess.Popen) -> None:
        with self._lock:
            self._process = process
            stop_now = self._stop_requested
        if stop_now:
            process.terminate()

    def stop(self) -> bool:
        with self._lock:
            if not self.running:
                return False
            self._stop_requested = True
            process = self._process
        if process and process.poll() is None:
            process.terminate()
        return True

    def stop_requested(self) -> bool:
        with self._lock:
            return self._stop_requested

    def crawler_finished(self) -> None:
        with self._lock:
            self.progress.finish_crawler()

    def finish(self, *, state: str, outcome: dict | None = None, error: str | None = None) -> None:
        with self._lock:
            self.state = "stopped" if self._stop_requested and state != "ok" else state
            self.outcome, self.error = outcome, error
            self.finished_at = iso(utc_now())
            self.progress.done()

    def snapshot(self, since: int = 0) -> dict:
        with self._lock:
            start = max(since - self._dropped, 0)
            return {
                "id": self.id,
                "state": self.state,
                "params": self.params,
                "started_at": self.started_at,
                "finished_at": self.finished_at,
                "progress": self.progress.as_dict(),
                "outcome": self.outcome,
                "error": self.error,
                "lines": self._lines[start:],
                "next": self._dropped + len(self._lines),
            }


class CrawlJobs:
    def __init__(
        self,
        workspace: Path,
        *,
        fetcher_factory: FetcherFactory = default_fetcher,
        clock: Callable[[], datetime] = utc_now,
    ):
        self.workspace = workspace
        self.fetcher_factory = fetcher_factory
        self.clock = clock
        self.current: CrawlJob | None = None
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None

    def start(
        self,
        *,
        platform: str = "xhs",
        keywords: Sequence[str],
        targets: Sequence[str],
        max_notes: int | None,
        force: bool,
        media: bool = False,
    ) -> CrawlJob:
        """media downloads the videos of the given links too (Bilibili recordings)."""
        if platform not in CRAWL_PLATFORMS:
            raise GleanerError(f"不支持抓取 {platform}")
        config = load_config(self.workspace)
        keywords = validate_keywords(list(keywords))
        targets = [value.strip() for value in targets if value.strip()]
        if not targets and not keywords:
            raise GleanerError("请至少填写一个关键词，或者粘贴要抓取的帖子链接")
        if media:
            check_recordings(platform)
            if not targets:
                raise GleanerError("下载音视频要粘贴视频链接")
        if max_notes is not None and not 1 <= max_notes <= MAX_NOTES_CAP:
            raise GleanerError(f"每个关键词的篇数必须在 1 到 {MAX_NOTES_CAP} 之间")
        now = self.clock()
        with self._lock:
            if self.current and self.current.running:
                raise GleanerError("已经有一个抓取正在进行，请等它结束或先停止")
            with Store.open(self.workspace) as store:
                check_interval(store, config, now, force, platform=platform)
            params = {
                "platform": platform,
                "keywords": keywords,
                "targets": targets,
                "max_notes": max_notes or default_max_notes(config, platform),
                "planned_notes": planned_notes(
                    config, platform=platform, keywords=keywords, targets=targets, max_notes=max_notes
                ),
                "force": force,
                "media": media,
            }
            job = CrawlJob(new_id("job", now), params, iso(now), download=config.nowcoder.download_images)
            self.current = job
            self._thread = threading.Thread(
                target=self._run,
                args=(job, config, platform, keywords, targets, max_notes, force, media),
                daemon=True,
            )
            self._thread.start()
        return job

    def stop(self) -> bool:
        job = self.current
        return bool(job and job.stop())

    def wait(self, timeout: float | None = None) -> None:
        if self._thread:
            self._thread.join(timeout)

    def _run(
        self,
        job: CrawlJob,
        config: Config,
        platform: str,
        keywords: list[str],
        targets: list[str],
        max_notes: int | None,
        force: bool,
        media: bool,
    ) -> None:
        path_dirs = tool_dirs(config.media.ffmpeg) if media else ()
        runner = make_runner(on_line=job.add_line, on_start=job.attach, path_dirs=path_dirs)
        try:
            inner = self.fetcher_factory(
                config,
                platform,
                runner=runner,
                on_line=job.add_line,
                should_stop=job.stop_requested,
                media=media,
            )
            fetcher = _Finishing(inner, job)
            with Store.open(self.workspace) as store:
                outcome = crawl(
                    store,
                    config,
                    fetcher,
                    platform=platform,
                    keywords=keywords,
                    targets=targets,
                    max_notes=max_notes,
                    force=force,
                    now=self.clock(),
                    media=media,
                )
            job.finish(state=outcome.status, outcome=outcome.as_dict(), error=outcome.message or None)
        except GleanerError as exc:
            job.finish(state="failed", error=str(exc))
        except Exception as exc:  # noqa: BLE001 - the page must learn that the job ended
            for line in traceback.format_exc().splitlines():
                job.add_line(line)
            job.finish(state="failed", error=f"意外错误：{exc}")


class _Finishing:
    """Moves the job to the import step as soon as the fetcher returns."""

    def __init__(self, fetcher: Fetcher, job: CrawlJob):
        self.fetcher = fetcher
        self.job = job

    def problems(self) -> list[str]:
        return self.fetcher.problems()

    def run(self, **kwargs) -> FetchResult:
        try:
            return self.fetcher.run(**kwargs)
        finally:
            self.job.crawler_finished()
