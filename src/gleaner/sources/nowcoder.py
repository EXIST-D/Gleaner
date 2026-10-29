"""Read Nowcoder (牛客) interview posts through the JSON endpoints its own pages use, with the standard library.

Checked against nowcoder.com as of 2026-10; no login is needed:

- Discovery: `POST gw-c.nowcoder.com/api/sparta/job-experience/experience/job/list` returns the newest interview
  posts, 20 per page, with title and a cut-off summary. Nowcoder's robots.txt disallows `/search`, so Gleaner
  never uses site search: it reads the newest pages and keeps posts whose title or summary has every term of
  a keyword. `contentType` 250 is a discussion (讨论帖, numeric ID), 74 a moment (动态, 32-hex UUID).
- Detail: `GET …/detail/content-data/detail/<id>` (HTML in `richText`) and `…/detail/moment-data/detail/<uuid>`.
- Pictures come from `uploadfiles.nowcoder.com` and similar hosts; only `*.nowcoder.com` URLs are fetched.

One request at a time with a fixed interval plus jitter; 429 and 5xx are retried, three 429 in a row, a 403 or a
verification page end the run as degraded. Records are written as soon as they are read, so a stopped run keeps
what it has. The layout matches MediaCrawler's: `<out>/nowcoder/jsonl/…` and `<out>/nowcoder/media/<id>/001.jpg`.
"""

from __future__ import annotations

import hashlib
import json
import random
import sys
import time
import urllib.error
import urllib.request
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit

from gleaner.adapters.nowcoder import post_images
from gleaner.config import NowcoderConfig
from gleaner.sources.base import FetchResult
from gleaner.timeutil import CHINA_TZ, utc_now
from gleaner.urls import nowcoder_url, post_key

LOG_NAME = "nowcoder.log"
MODES = ("search", "detail")
LIST_URL = "https://gw-c.nowcoder.com/api/sparta/job-experience/experience/job/list"
DISCUSS_URL = "https://gw-c.nowcoder.com/api/sparta/detail/content-data/detail/{}"
MOMENT_URL = "https://gw-c.nowcoder.com/api/sparta/detail/moment-data/detail/{}"
LIST_PAGE_SIZE = 20
MAX_ATTEMPTS = 3
REQUEST_TIMEOUT = 20
IMAGE_INTERVAL_SECONDS = 1.0
MAX_IMAGES = 30
MAX_IMAGE_BYTES = 15 * 1024 * 1024
MAX_RETRY_AFTER = 60
IMAGE_TYPES = {
    "image/jpeg": ".jpg",
    "image/jpg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
    "image/gif": ".gif",
}
BLOCKED_MARKERS = ("访问过于频繁", "请登录后继续访问", "verifycenter", "请完成验证")
HEADERS = {
    "Accept": "application/json, text/plain, */*",
    "Referer": "https://www.nowcoder.com/",
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/131.0.0.0 Safari/537.36"
    ),
}
_PRIVATE_FIELDS = ("userBrief", "ip4", "ip4Location")

Fetch = Callable[[str, str, bytes | None, dict[str, str], float], tuple[int, dict[str, str], bytes]]
"""(method, url, body, headers, timeout) -> (status, lower-cased headers, body); raises OSError on network errors."""


class Halt(Exception):
    """The run cannot continue: blocked, network gone, stopped or out of time."""


class Skip(Exception):
    """One post cannot be read (deleted, private, broken response); the run goes on."""


def urllib_fetch(
    method: str, url: str, body: bytes | None, headers: dict[str, str], timeout: float
) -> tuple[int, dict[str, str], bytes]:
    request = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.status, _lower(response.headers), response.read(MAX_IMAGE_BYTES + 1)
    except urllib.error.HTTPError as error:
        return error.code, _lower(error.headers), error.read(64 * 1024)


@dataclass
class Pick:
    kind: str
    post_id: str
    keywords: list[str] = field(default_factory=list)


def parse_target(value: str) -> Pick | None:
    """A discussion or moment from a Nowcoder link, a numeric discussion ID or a 32-hex moment UUID."""
    text = value.strip()
    key = post_key(text)
    post_id = key.removeprefix("nowcoder:") if key and key.startswith("nowcoder:") else text.lower()
    if post_id.isdigit():
        return Pick("discuss", post_id)
    if len(post_id) == 32 and all(c in "0123456789abcdef" for c in post_id):
        return Pick("moment", post_id)
    return None


def list_entry(record: dict) -> tuple[Pick, str] | None:
    """(post, text to match keywords against) for one record of the interview list."""
    if record.get("contentType") == 250 and record.get("contentId"):
        data = record.get("contentData") or {}
        pick = Pick("discuss", str(record["contentId"]))
    elif record.get("contentType") == 74:
        data = record.get("momentData") or {}
        if not data.get("uuid"):
            return None
        pick = Pick("moment", str(data["uuid"]).lower())
    else:
        return None
    return pick, f"{data.get('title') or ''}\n{data.get('content') or ''}"


def matches(keyword: str, text: str) -> bool:
    lowered = text.lower()
    return all(term in lowered for term in keyword.lower().split())


class NowcoderClient:
    def __init__(
        self,
        fetch: Fetch,
        *,
        interval: float,
        log: Callable[[str], None],
        should_stop: Callable[[], bool],
        deadline: float,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
        jitter: Callable[[], float] = random.random,
    ):
        self.fetch = fetch
        self.interval = interval
        self.log = log
        self.should_stop = should_stop
        self.deadline = deadline
        self.sleep = sleep
        self.clock = clock
        self.jitter = jitter
        self._last: dict[str, float] = {}

    def request_json(self, method: str, url: str, payload: dict | None = None) -> dict:
        body = json.dumps(payload).encode("utf-8") if payload is not None else None
        headers = {**HEADERS, **({"Content-Type": "application/json"} if body else {})}
        limited = 0
        for attempt in range(1, MAX_ATTEMPTS + 1):
            self._pace("api", self.interval + self.jitter())
            try:
                status, response_headers, content = self.fetch(
                    method, _stamped(url), body, headers, REQUEST_TIMEOUT
                )
            except OSError as exc:
                if attempt == MAX_ATTEMPTS:
                    raise Halt(f"连不上牛客：{exc}") from exc
                self._wait(2 * attempt, f"网络错误（{exc}），稍后重试")
                continue
            text = content.decode("utf-8", errors="replace")
            if status == 429:
                limited += 1
                if limited == MAX_ATTEMPTS:
                    raise Halt(
                        "牛客连续返回 429（请求过于频繁），已停止；请隔一段时间再试，或调大 request_interval_seconds"
                    )
                self._wait(_retry_after(response_headers) or 5 * attempt, "牛客要求放慢请求（HTTP 429）")
                continue
            if status == 403 or any(marker in text for marker in BLOCKED_MARKERS):
                raise Halt(f"牛客拒绝了请求（HTTP {status}，可能需要验证），已停止；请隔一段时间再试")
            if status >= 500 or status == 408:
                if attempt == MAX_ATTEMPTS:
                    raise Halt(f"牛客服务暂时不可用（HTTP {status}）")
                self._wait(2 * attempt, f"牛客返回 HTTP {status}，稍后重试")
                continue
            if status != 200:
                raise Skip(f"HTTP {status}")
            try:
                result = json.loads(text)
            except json.JSONDecodeError as exc:
                raise Skip("返回的不是 JSON") from exc
            if not isinstance(result, dict) or not result.get("success"):
                message = str(result.get("msg") if isinstance(result, dict) else "") or "接口返回失败"
                if any(marker in message for marker in BLOCKED_MARKERS):
                    raise Halt(f"牛客拒绝了请求：{message}")
                raise Skip(message)
            data = result.get("data")
            if not isinstance(data, dict):
                raise Skip("接口没有返回数据")
            return data
        raise AssertionError("unreachable")

    def download(self, url: str) -> tuple[bytes, str]:
        self._pace("image", IMAGE_INTERVAL_SECONDS)
        headers = {**HEADERS, "Accept": "image/avif,image/webp,image/*,*/*;q=0.8"}
        try:
            status, response_headers, content = self.fetch("GET", url, None, headers, REQUEST_TIMEOUT)
        except OSError as exc:
            raise Skip(f"网络错误：{exc}") from exc
        if status != 200:
            raise Skip(f"HTTP {status}")
        if len(content) > MAX_IMAGE_BYTES:
            raise Skip("图片超过 15 MB")
        return content, response_headers.get("content-type", "").split(";")[0].strip().lower()

    def check(self) -> None:
        if self.should_stop():
            raise Halt("已停止")
        if self.clock() > self.deadline:
            raise Halt("超过时限，已停止")

    def _pace(self, lane: str, gap: float) -> None:
        self.check()
        last = self._last.get(lane)
        if last is not None:
            remaining = last + gap - self.clock()
            if remaining > 0:
                self.sleep(remaining)
        self.check()
        self._last[lane] = self.clock()

    def _wait(self, seconds: float, reason: str) -> None:
        seconds = min(float(seconds), MAX_RETRY_AFTER)
        self.log(f"[nowcoder] {reason}，等待 {seconds:.0f} 秒")
        self.check()
        self.sleep(seconds)


class NowcoderFetcher:
    def __init__(
        self,
        config: NowcoderConfig,
        *,
        fetch: Fetch | None = None,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
        on_line: Callable[[str], None] | None = None,
        should_stop: Callable[[], bool] | None = None,
        echo: bool = False,
    ):
        self.config = config
        self.fetch = fetch or urllib_fetch
        self.sleep = sleep
        self.clock = clock
        self.on_line = on_line
        self.should_stop = should_stop or (lambda: False)
        self.echo = echo

    def problems(self) -> list[str]:
        return []

    def run(
        self,
        *,
        mode: str,
        output_dir: Path,
        log_dir: Path,
        keywords: Sequence[str] = (),
        targets: Sequence[str] = (),
        max_notes: int | None = None,
    ) -> FetchResult:
        if mode not in MODES:
            raise ValueError(f"unsupported mode: {mode}")
        log_dir.mkdir(parents=True, exist_ok=True)
        log_path = log_dir / LOG_NAME
        with log_path.open("w", encoding="utf-8") as log_file:

            def log(line: str) -> None:
                log_file.write(line + "\n")
                log_file.flush()
                if self.echo:
                    sys.stderr.write(line + "\n")
                if self.on_line:
                    self.on_line(line)

            run = _Run(self, output_dir, mode, log)
            return run.execute(list(keywords), list(targets), max_notes or self.config.max_notes, log_path)


class _Run:
    def __init__(self, fetcher: NowcoderFetcher, output_dir: Path, mode: str, log: Callable[[str], None]):
        self.config = fetcher.config
        self.log = log
        self.mode = mode
        self.output_dir = output_dir
        self.client = NowcoderClient(
            fetcher.fetch,
            interval=self.config.request_interval_seconds,
            log=log,
            should_stop=fetcher.should_stop,
            deadline=fetcher.clock() + self.config.timeout_seconds,
            sleep=fetcher.sleep,
            clock=fetcher.clock,
        )
        self.saved = 0
        self.images = 0
        self.failures: list[str] = []
        self.scanned = 0

    def execute(
        self, keywords: list[str], targets: list[str], per_keyword: int, log_path: Path
    ) -> FetchResult:
        halted = ""
        try:
            if self.mode == "search":
                picks = self.discover(keywords, per_keyword)
            else:
                picks = []
                for value in targets:
                    pick = parse_target(value)
                    if pick is None:
                        self.failures.append(f"{value}：不是牛客讨论帖或动态的链接")
                    else:
                        picks.append(pick)
            for pick in picks:
                self.save(pick)
        except Halt as exc:
            halted = str(exc)
            self.log(f"[nowcoder] {halted}")
        self.log(f"[nowcoder] 结束：保存 {self.saved} 篇，下载图片 {self.images} 张")

        done = f"已保存 {self.saved} 篇。日志：{log_path}"
        if halted:
            return FetchResult("degraded", self.output_dir, f"{halted}；{done}")
        if self.failures:
            listed = "；".join(self.failures[:3]) + ("…" if len(self.failures) > 3 else "")
            return FetchResult(
                "degraded", self.output_dir, f"{len(self.failures)} 篇没有读到（{listed}）；{done}"
            )
        if not self.saved:
            if self.mode == "search":
                return FetchResult(
                    "degraded",
                    self.output_dir,
                    f"翻阅了最新 {self.scanned} 篇面经，标题和摘要都不包含这些关键词；"
                    "可以换更宽的关键词，或调大 nowcoder.scan_pages",
                )
            return FetchResult("degraded", self.output_dir, f"没有读到帖子。日志：{log_path}")
        return FetchResult("ok", self.output_dir)

    def discover(self, keywords: list[str], per_keyword: int) -> list[Pick]:
        found: dict[str, list[str]] = {keyword: [] for keyword in keywords}
        picks: dict[str, Pick] = {}
        for page in range(1, self.config.scan_pages + 1):
            self.log(f"[nowcoder] 翻阅最新面经列表第 {page} 页")
            data = self.client.request_json(
                "POST",
                LIST_URL,
                {"companyList": [], "jobId": -1, "level": 1, "order": 3, "page": page, "isNewJob": True},
            )
            records = data.get("records") if isinstance(data.get("records"), list) else []
            self.scanned += len(records)
            for record in records:
                entry = list_entry(record) if isinstance(record, dict) else None
                if entry is None:
                    continue
                pick, text = entry
                for keyword in keywords:
                    if len(found[keyword]) < per_keyword and matches(keyword, text):
                        found[keyword].append(pick.post_id)
                        picks.setdefault(pick.post_id, pick).keywords.append(keyword)
            counts = "、".join(f"{keyword} {len(ids)}" for keyword, ids in found.items())
            self.log(f"[nowcoder] 第 {page} 页 {len(records)} 篇，累计命中 {len(picks)} 篇（{counts}）")
            if all(len(ids) >= per_keyword for ids in found.values()) or len(records) < LIST_PAGE_SIZE:
                break
        return list(picks.values())

    def save(self, pick: Pick) -> None:
        url = DISCUSS_URL if pick.kind == "discuss" else MOMENT_URL
        try:
            detail = self.client.request_json("GET", url.format(pick.post_id))
        except Skip as exc:
            self.failures.append(f"{nowcoder_url(pick.post_id)}：{exc}")
            self.log(f"[nowcoder] 没有读到 {pick.post_id}：{exc}")
            return
        author = str(detail.get("authorId") or detail.get("userId") or "")
        record = {
            "kind": pick.kind,
            "post_id": pick.post_id,
            "url": nowcoder_url(pick.post_id),
            "source_keywords": pick.keywords,
            "author_hash": hashlib.sha256(f"nowcoder:{author}".encode()).hexdigest()[:16] if author else "",
            "detail": {key: value for key, value in detail.items() if key not in _PRIVATE_FIELDS},
        }
        self.write(record)
        self.saved += 1
        self.log(f"[nowcoder] 已保存帖子 {pick.post_id}：{str(detail.get('title') or '')[:40]}")
        if self.config.download_images:
            self.download_images(record)

    def write(self, record: dict) -> None:
        date = utc_now().astimezone(CHINA_TZ).date().isoformat()
        path = self.output_dir / "nowcoder" / "jsonl" / f"{self.mode}_contents_{date}.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")

    def download_images(self, record: dict) -> None:
        urls = [url for url in post_images(record) if _allowed_image(url)][:MAX_IMAGES]
        directory = self.output_dir / "nowcoder" / "media" / record["post_id"]
        for index, url in enumerate(urls, start=1):
            try:
                content, content_type = self.client.download(url)
            except Skip as exc:
                self.log(f"[nowcoder] 图片下载失败 {url}：{exc}")
                continue
            suffix = IMAGE_TYPES.get(content_type)
            if suffix is None:
                self.log(f"[nowcoder] 跳过不是图片的响应 {url}（{content_type or '无类型'}）")
                continue
            directory.mkdir(parents=True, exist_ok=True)
            (directory / f"{index:03d}{suffix}").write_bytes(content)
            self.images += 1
            self.log(f"[nowcoder] 图片下载完成 {record['post_id']}/{index:03d}{suffix}")


def _allowed_image(url: str) -> bool:
    host = (urlsplit(url).hostname or "").lower()
    return url.startswith("https://") and (host == "nowcoder.com" or host.endswith(".nowcoder.com"))


def _stamped(url: str) -> str:
    return f"{url}{'&' if '?' in url else '?'}_={int(time.time() * 1000)}"


def _retry_after(headers: dict[str, str]) -> float | None:
    try:
        return max(0.0, float(headers.get("retry-after", "")))
    except ValueError:
        return None


def _lower(headers) -> dict[str, str]:
    return {key.lower(): value for key, value in headers.items()} if headers else {}
