"""A loopback-only HTTP server for the web console, built on the standard library."""

from __future__ import annotations

import json
import re
import secrets
import webbrowser
from dataclasses import dataclass, field
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib import resources
from pathlib import Path
from urllib.parse import parse_qs, quote, unquote, urlsplit

from gleaner import __version__
from gleaner.bank import (
    ANSWER_STATUSES,
    INTERVIEW_TYPES,
    QUESTION_SUFFIX,
    QUESTION_TYPES,
    ROUNDS,
    BankReader,
    topic_of,
)
from gleaner.bodytext import choose_intake, listed_questions
from gleaner.checks import environment_checks
from gleaner.config import MAX_NOTES_CAP, Config, load_config
from gleaner.devnotes import add_feedback, dev_status
from gleaner.errors import GleanerError
from gleaner.models import LABEL_TITLES, LABELS, PLATFORMS, PostRecord
from gleaner.packer import OUTSIDE_PACKS, build_pack, packable
from gleaner.pipeline import (
    CRAWL_PLATFORMS,
    default_max_notes,
    min_interval_minutes,
    page_size,
    platform_of,
    seconds_until_next_crawl,
)
from gleaner.screening import is_recording
from gleaner.sources import mediacrawler
from gleaner.store import Store
from gleaner.timeutil import china_date, iso, utc_now
from gleaner.urls import post_key
from gleaner.web.jobs import CrawlJobs
from gleaner.web.progress import step_titles, steps_for

HOST = "127.0.0.1"
DEFAULT_PORT = 8765
MAX_BODY_BYTES = 256 * 1024
# mimetypes reads the Windows registry, which may not know .webp or may map .js to text/plain.
CONTENT_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".svg": "image/svg+xml",
    ".webp": "image/webp",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".gif": "image/gif",
}
MEDIA_SUFFIXES = {".webp", ".jpg", ".jpeg", ".png", ".gif"}
PACK_ID = re.compile(r"^pack-\d{8}-\d{6}-[0-9a-f]{4}$")
STATIC_FILES = {"index.html", "app.css", "app.js", "icon.svg"}
TOKEN_PLACEHOLDER = "__GLEANER_TOKEN__"
CSP = (
    "default-src 'self'; img-src 'self' data:; style-src 'self'; script-src 'self'; "
    "connect-src 'self'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'"
)


@dataclass
class Response:
    status: int
    body: bytes
    content_type: str = "application/json; charset=utf-8"
    headers: dict[str, str] = field(default_factory=dict)


def json_response(data: dict, status: int = 200) -> Response:
    return Response(status, json.dumps(data, ensure_ascii=False, default=str).encode("utf-8"))


def error_response(message: str, status: int = 400) -> Response:
    return json_response({"ok": False, "error": message}, status)


class WebApp:
    """Request handling without sockets, so tests can call it directly or through a real server."""

    def __init__(
        self, workspace: Path, *, jobs: CrawlJobs | None = None, token: str | None = None, port: int = 0
    ):
        self.workspace = workspace
        self.jobs = jobs or CrawlJobs(workspace)
        self.token = token or secrets.token_urlsafe(24)
        self.port = port
        self._bank: BankReader | None = None
        load_config(workspace)

    def allowed_hosts(self) -> set[str]:
        return {f"{HOST}:{self.port}", f"localhost:{self.port}"}

    def handle(self, method: str, target: str, headers: dict[str, str], body: bytes) -> Response:
        if headers.get("host", "").lower() not in self.allowed_hosts():
            return error_response("只接受来自本机地址的访问", HTTPStatus.FORBIDDEN)
        parts = urlsplit(target)
        path, query = parts.path, {key: values[-1] for key, values in parse_qs(parts.query).items()}
        try:
            if method == "GET":
                return self._get(path, query)
            if method == "POST":
                if not secrets.compare_digest(headers.get("x-gleaner-token", ""), self.token):
                    return error_response("页面令牌无效，请刷新页面", HTTPStatus.FORBIDDEN)
                payload = json.loads(body.decode("utf-8") or "{}") if body else {}
                if not isinstance(payload, dict):
                    return error_response("请求内容必须是 JSON 对象")
                return self._post(path, payload)
            return error_response("不支持的请求方法", HTTPStatus.METHOD_NOT_ALLOWED)
        except GleanerError as exc:
            return error_response(str(exc))
        except (ValueError, UnicodeDecodeError) as exc:
            return error_response(f"请求格式不正确：{exc}")

    # GET

    def _get(self, path: str, query: dict[str, str]) -> Response:
        if path in ("/", "/index.html"):
            html = _static("index.html").decode("utf-8").replace(TOKEN_PLACEHOLDER, self.token)
            return Response(200, html.encode("utf-8"), "text/html; charset=utf-8")
        if path.startswith("/static/"):
            name = path.removeprefix("/static/")
            if name not in STATIC_FILES:
                return error_response("找不到文件", HTTPStatus.NOT_FOUND)
            return Response(200, _static(name), _content_type(name))
        if path.startswith("/media/"):
            return self._media(path.removeprefix("/media/"))
        if path.startswith("/bank-media/"):
            return self._bank_media(path.removeprefix("/bank-media/"))
        if path.startswith("/bank-export/"):
            return self._bank_export_download(unquote(path.removeprefix("/bank-export/")))
        routes = {
            "/api/overview": self.overview,
            "/api/posts": self.posts,
            "/api/post": self.post,
            "/api/crawl": self.crawl_status,
            "/api/packs": self.packs,
            "/api/pack": self.pack,
            "/api/bank": self.bank_questions,
            "/api/bank/question": self.bank_question,
            "/api/bank/exports": self.bank_exports,
            "/api/bank/export-file": self.bank_export_file,
            "/api/dev": self.dev,
        }
        handler = routes.get(path)
        if handler is None:
            return error_response("找不到接口", HTTPStatus.NOT_FOUND)
        return json_response({"ok": True, **handler(query)})

    def _media(self, relative: str) -> Response:
        target = (self.workspace / relative).resolve()
        allowed = [(self.workspace / name).resolve() for name in ("runs", "packs")]
        if (
            target.suffix.lower() not in MEDIA_SUFFIXES
            or not any(target.is_relative_to(root) for root in allowed)
            or not target.is_file()
        ):
            return error_response("找不到图片", HTTPStatus.NOT_FOUND)
        return Response(
            200, target.read_bytes(), _content_type(target.name), {"Cache-Control": "private, max-age=86400"}
        )

    def _bank_media(self, name: str) -> Response:
        path = self._reader().media_path(name)
        if path is None:
            return error_response("找不到图片", HTTPStatus.NOT_FOUND)
        return Response(
            200, path.read_bytes(), _content_type(path.name), {"Cache-Control": "private, max-age=86400"}
        )

    def _bank_export_download(self, name: str) -> Response:
        path = self._reader().export_path(name)
        if path is None:
            return error_response("找不到报告", HTTPStatus.NOT_FOUND)
        disposition = f"attachment; filename*=UTF-8''{quote(path.name)}"
        return Response(
            200, path.read_bytes(), "text/markdown; charset=utf-8", {"Content-Disposition": disposition}
        )

    def bank_exports(self, _query: dict) -> dict:
        reader = self._reader()
        return {"exports": reader.exports(), "directory": str(reader.exports_dir) if reader.bank else ""}

    def bank_export_file(self, query: dict) -> dict:
        reader = self._reader()
        name = query.get("name", "")
        path = reader.export_path(name)
        if path is None:
            raise GleanerError(f"找不到报告 {name}")
        if path.stem.endswith(QUESTION_SUFFIX):
            pair = path.stem.removesuffix(QUESTION_SUFFIX) + ".md"
        else:
            pair = path.stem + QUESTION_SUFFIX + ".md"
        return {
            "name": path.name,
            "path": str(path),
            "edition": "题目版" if path.stem.endswith(QUESTION_SUFFIX) else "答案版",
            "pair": pair if reader.export_path(pair) else None,
            "markdown": path.read_text(encoding="utf-8"),
        }

    def _reader(self) -> BankReader:
        handoff = load_config(self.workspace).handoff
        if self._bank is None or self._bank.handoff != handoff:
            self._bank = BankReader(handoff)
        return self._bank

    def _held(self) -> dict | None:
        """What the bank holds by source URL, or None when it cannot be asked (not set up, too old)."""
        reader = self._reader()
        if reader.problem():
            return None
        try:
            return reader.held()
        except GleanerError:
            return None

    def bank_questions(self, _query: dict) -> dict:
        reader = self._reader()
        problem = reader.problem()
        if problem:
            return {"available": False, "reason": problem, "bank": str(reader.bank or "")}
        labels = reader.domain_labels()
        items = [_question_brief(row, labels) for row in reader.questions()]
        active = [item for item in items if not item["excluded"]]
        topics: dict[str, dict] = {}
        companies: dict[str, int] = {}
        for item in active:
            topic = topics.setdefault(
                item["topic"], {"id": item["topic"], "title": item["topic_title"], "count": 0}
            )
            topic["count"] += 1
            for name in item["companies"]:
                companies[name] = companies.get(name, 0) + 1
        return {
            "available": True,
            "bank": str(reader.bank),
            "summary": {
                "questions": len(active),
                "excluded": len(items) - len(active),
                "occurrences": sum(item["frequency"] for item in active),
                "companies": len(companies),
                "answered": sum(item["answer_status"] in ("source_backed", "reviewed") for item in active),
                "repeated": sum(item["frequency"] > 1 for item in active),
            },
            "topics": sorted(topics.values(), key=lambda topic: (-topic["count"], topic["title"])),
            "companies": [
                {"name": name, "count": count}
                for name, count in sorted(companies.items(), key=lambda pair: (-pair[1], pair[0]))
            ],
            "questions": items,
        }

    def bank_question(self, query: dict) -> dict:
        reader = self._reader()
        result = reader.question(query.get("id", ""))
        labels = reader.domain_labels()
        rows = {row["id"]: row for row in reader.questions()}
        occurrence_owner = {
            occurrence["id"]: (row["id"], occurrence.get("original_text", ""))
            for row in rows.values()
            for occurrence in row.get("occurrences", [])
        }
        question = result["question"]
        sources = {source["id"]: source for source in result.get("sources", [])}
        companies = {company["id"]: company["name"] for company in question.get("companies", [])}

        with Store.open(self.workspace) as store:
            known = {
                key
                for source in sources.values()
                if (key := post_key(source.get("source_url") or "")) and store.get(key) is not None
            }

        occurrences = []
        for occurrence in question.get("occurrences", []):
            source = sources.get(occurrence.get("source_id"), {})
            parent = occurrence_owner.get(occurrence.get("parent_occurrence_id") or "")
            media = Path(source.get("path") or "").name
            key = post_key(source.get("source_url") or "")
            occurrences.append(
                {
                    "original_text": occurrence.get("original_text", ""),
                    "company": companies.get(occurrence.get("company_id") or ""),
                    "round": ROUNDS.get(occurrence.get("round") or "unknown", occurrence.get("round")),
                    "interview_type": INTERVIEW_TYPES.get(occurrence.get("interview_type") or "unknown", ""),
                    "event_date": occurrence.get("event_date"),
                    "source_date": source.get("source_date"),
                    "platform": source.get("platform"),
                    "source_url": source.get("source_url"),
                    "source_type": source.get("type"),
                    "image": f"/bank-media/{media}"
                    if source.get("type") == "image" and reader.media_path(media)
                    else None,
                    "post_key": key if key in known else None,
                    "follow_up_of": {"id": parent[0], "text": parent[1]} if parent else None,
                }
            )

        relations = []
        for relation in result.get("relations", []):
            other = (
                relation["to_question_id"]
                if relation["from_question_id"] == question["id"]
                else relation["from_question_id"]
            )
            if other in rows:
                relations.append(
                    {"id": other, "canonical": rows[other]["canonical"], "type": relation.get("type")}
                )

        answer = question.get("answer") if isinstance(question.get("answer"), dict) else None
        return {
            "question": {
                **_question_brief(question, labels),
                "role_tracks": question.get("role_tracks", []),
                "occurrences": occurrences,
                "relations": relations,
                "merged_variants": [
                    variant.get("canonical", "") for variant in result.get("merged_variants", [])
                ],
                "answer": _answer(answer) if answer else None,
            }
        }

    def overview(self, _query: dict) -> dict:
        config = load_config(self.workspace)
        now = utc_now()
        with Store.open(self.workspace) as store:
            stats = store.stats()
            runs = store.runs(limit=8)
            checkpoints = store.checkpoints()
            unpacked = len(packable(store, limit=None)[0])
            platforms = [_platform_option(store, config, code, now) for code in CRAWL_PLATFORMS]
        job = self.jobs.current
        return {
            "version": __version__,
            "workspace": str(self.workspace),
            "stats": stats,
            "unpacked_candidates": unpacked,
            "labels": [{"id": label, "title": LABEL_TITLES[label]} for label in LABELS],
            "checkpoints": [
                {
                    "keyword": item["keyword"],
                    "platform": _platform_label(platform_of(item["source"]) or ""),
                    "latest": china_date(item["latest_published_at"]),
                }
                for item in checkpoints
            ],
            "runs": [
                {
                    "id": run.id,
                    "platform": _platform_label(run.params.get("platform") or platform_of(run.source) or ""),
                    "mode": run.mode,
                    "status": run.status,
                    "message": _first_line(run.message),
                    "started_at": run.started_at,
                    "keywords": run.params.get("keywords", []),
                    "targets": len(run.params.get("targets", [])),
                    "new": run.counts.get("new", 0),
                    "records": run.counts.get("records", 0),
                }
                for run in runs
            ],
            "checks": [check.as_dict() for check in environment_checks(config)],
            "config": {
                "keywords": list(config.keywords),
                "max_notes_cap": MAX_NOTES_CAP,
            },
            "platforms": platforms,
            "job": {"id": job.id, "state": job.state} if job else None,
        }

    def posts(self, query: dict) -> dict:
        labels = [label for label in query.get("label", "").split(",") if label in LABELS] or None
        platform = query.get("platform", "")
        packed = {"yes": True, "no": False}.get(query.get("packed", ""))
        text = query.get("q", "").strip().lower()
        with Store.open(self.workspace) as store:
            everything = store.posts(packed=packed)
        counts = {label: 0 for label in LABELS}
        platforms: dict[str, int] = {}
        for record in everything:
            platforms[record.post.platform] = platforms.get(record.post.platform, 0) + 1
            if platform and record.post.platform != platform:
                continue
            if record.label in counts:
                counts[record.label] += 1
        selected = [
            record
            for record in everything
            if (labels is None or record.label in labels)
            and (not platform or record.post.platform == platform)
            and (
                not text
                or text in f"{record.post.title}\n{record.post.body}\n{' '.join(record.post.tags)}".lower()
            )
        ]
        return {
            "total": len(everything),
            "counts": counts,
            "platforms": [
                {"id": code, "title": _platform_label(code), "count": count}
                for code, count in sorted(platforms.items(), key=lambda pair: _platform_order(pair[0]))
            ],
            "posts": [self._brief(record) for record in selected],
        }

    def post(self, query: dict) -> dict:
        key = query.get("key", "")
        with Store.open(self.workspace) as store:
            record = store.get(key)
        if record is None:
            raise GleanerError(f"找不到帖子 {key}")
        post = record.post
        return {
            "post": {
                **self._brief(record),
                "body": post.body,
                "tags": post.tags,
                "keywords": post.keywords,
                "metrics": post.metrics,
                "images": [f"/media/{path}" for path in post.images],
                "remote_image_count": post.remote_image_count,
                "open_url": post.open_url,
                "media_path": post.media,
                "label_reasons": record.label_reasons,
                "label_source": record.label_source,
                "first_seen_at": record.first_seen_at,
                "changed_after_pack": record.changed_after_pack,
            }
        }

    def dev(self, _query: dict) -> dict:
        return dev_status(self.workspace)

    def crawl_status(self, query: dict) -> dict:
        job = self.jobs.current
        since = int(query.get("since", "0") or 0)
        return {"job": job.snapshot(since) if job else None}

    def packs(self, _query: dict) -> dict:
        held = self._held()
        with Store.open(self.workspace) as store:
            settled = {r.post.key for r in store.posts(packed=True) if r.packed_in in OUTSIDE_PACKS}
        found = []
        for manifest_path in sorted((self.workspace / "packs").glob("pack-*/manifest.json"), reverse=True):
            try:
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            entries = manifest.get("posts", [])
            found.append(
                {
                    "id": manifest.get("pack_id", manifest_path.parent.name),
                    "created_at": manifest.get("created_at"),
                    "posts": len(entries),
                    "image_posts": sum(entry.get("intake") == "images" for entry in entries),
                    "text_posts": sum(entry.get("intake") == "text" for entry in entries),
                    "media_posts": sum(entry.get("intake") == "media" for entry in entries),
                    "imported": None
                    if held is None
                    else sum(
                        entry.get("source_url") in held or entry.get("key") in settled for entry in entries
                    ),
                    "titles": [entry.get("title", "") for entry in entries[:4]],
                }
            )
        return {"packs": found}

    def pack(self, query: dict) -> dict:
        pack_id = query.get("id", "")
        if not PACK_ID.match(pack_id):
            raise GleanerError("素材包编号不正确")
        directory = self.workspace / "packs" / pack_id
        try:
            manifest = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
            handoff = (directory / "HANDOFF.md").read_text(encoding="utf-8")
        except OSError as exc:
            raise GleanerError(f"找不到素材包 {pack_id}") from exc
        return {
            "pack": {
                "id": pack_id,
                "path": str(directory),
                "created_at": manifest.get("created_at"),
                "bank": manifest.get("bank"),
                "handoff": handoff,
                "posts": [
                    {
                        "key": entry.get("key"),
                        "title": entry.get("title"),
                        "intake": entry.get("intake"),
                        "source_date": entry.get("source_date"),
                    }
                    for entry in manifest.get("posts", [])
                ],
            }
        }

    # POST

    def _post(self, path: str, payload: dict) -> Response:
        if path == "/api/crawl":
            job = self.jobs.start(
                platform=str(payload.get("platform") or "xhs"),
                keywords=_strings(payload.get("keywords")),
                targets=_strings(payload.get("urls")),
                max_notes=_optional_int(payload.get("max_notes")),
                force=bool(payload.get("force")),
                media=bool(payload.get("media")),
                full=bool(payload.get("full")),
            )
            return json_response({"ok": True, "job": job.snapshot()})
        if path == "/api/crawl/stop":
            return json_response({"ok": True, "stopping": self.jobs.stop()})
        if path == "/api/label":
            key, label = str(payload.get("key", "")), str(payload.get("label", ""))
            reason = str(payload.get("reason", "")).strip()
            with Store.open(self.workspace) as store:
                store.set_label(
                    key,
                    label,
                    source="manual",
                    reasons=[f"manual:{reason}" if reason else "manual"],
                    now=iso(utc_now()),
                )
                record = store.get(key)
            return json_response({"ok": True, "post": self._brief(record)})
        if path == "/api/bank/export":
            reader = self._reader()
            problem = reader.problem()
            if problem:
                raise GleanerError(problem)
            company = str(payload.get("company") or "").strip() or None
            domain = str(payload.get("topic") or "").strip() or None
            title = reader.domain_labels().get(domain or "", {}).get("label", domain or "")
            result = reader.export_markdown(company=company, domain=domain, title=title)
            return json_response({"ok": True, "export": result})
        if path == "/api/pack":
            keys = _strings(payload.get("keys"))
            if not keys:
                raise GleanerError("请先选择要打包的帖子")
            config = load_config(self.workspace)
            with Store.open(self.workspace) as store:
                result = build_pack(store, config, now=utc_now(), keys=keys)
            return json_response({"ok": True, "pack": result.as_dict()})
        if path == "/api/feedback":
            entry = add_feedback(
                self.workspace, str(payload.get("text", "")), str(payload.get("topic", "")), now=utc_now()
            )
            return json_response({"ok": True, "entry": entry})
        return error_response("找不到接口", HTTPStatus.NOT_FOUND)

    # Helpers

    def _brief(self, record: PostRecord) -> dict:
        post = record.post
        local = [path for path in post.images if (self.workspace / path).is_file()]
        media = bool(post.media and (self.workspace / post.media).is_file())
        spec = mediacrawler.PLATFORMS.get(post.platform)
        return {
            "key": post.key,
            "platform": post.platform,
            "platform_title": _platform_label(post.platform),
            "title": post.title,
            "kind": post.kind,
            "source_date": china_date(post.published_at),
            "url": post.url,
            "label": record.label,
            "label_title": LABEL_TITLES.get(record.label or "", "未初筛"),
            "images": len(local),
            "thumb": f"/media/{local[0]}" if local else None,
            "intake": "media" if media else choose_intake(post.body, len(local)),
            "body_questions": len(listed_questions(post.body)),
            "media": media,
            "recording": is_recording(record.label_reasons),
            "can_download_media": bool(spec and spec.recordings and post.kind == "video"),
            "likes": post.metrics.get("liked_count"),
            "packed_in": record.packed_in,
            "excerpt": post.body[:90],
        }


_HINTS = {
    "xhs": "小红书搜索按页返回，每页 {page} 篇；第一次需要扫码登录，帖子图片会下载下来看图导入。",
    "dy": "抖音搜索每页 {page} 篇；图文帖下载图片，视频帖只导入文案（MediaCrawler 会顺带下载封面和整段视频）。",
    "bili": "B 站搜索每页 {page} 个视频，只导入标题和简介。面试录音可以在帖子详情里下载音视频，"
    "打包时转成音频交给 Interview Bank 转写；按链接抓取时也可以勾选下载音视频。",
    "zhihu": "知乎搜索每页 {page} 条（回答、文章、视频）；导入纯文字正文。",
    "nowcoder": "不登录、不使用站内搜索：翻阅最新面经列表（最多 {scan} 页，每页 20 篇），"
    "保存标题或摘要里包含关键词全部词语的帖子，按篇数保存。",
}


def _platform_option(store: Store, config: Config, code: str, now) -> dict:
    size = page_size(code)
    titles = step_titles(code)
    steps = steps_for(code, download=config.nowcoder.download_images)
    return {
        "id": code,
        "title": _platform_label(code),
        "page_size": size,
        "max_notes": default_max_notes(config, code),
        "min_interval_minutes": min_interval_minutes(config, code),
        "next_crawl_in": seconds_until_next_crawl(store, config, now, platform=code),
        "engine": "nowcoder" if code == "nowcoder" else "mediacrawler",
        "steps": [{"name": step, "title": titles[step]} for step in steps],
        "hint": _HINTS[code].format(page=size, scan=config.nowcoder.scan_pages),
        "recordings": bool(code in mediacrawler.PLATFORMS and mediacrawler.PLATFORMS[code].recordings),
    }


def _platform_label(code: str) -> str:
    return PLATFORMS[code].label if code in PLATFORMS else code


def _platform_order(code: str) -> int:
    return list(PLATFORMS).index(code) if code in PLATFORMS else len(PLATFORMS)


def _question_brief(row: dict, labels: dict[str, dict]) -> dict:
    topic, topic_title = topic_of(row.get("domains") or [], labels)
    occurrences = row.get("occurrences") or []
    companies = row.get("companies") or []
    status = row.get("answer_status") or "missing"
    return {
        "id": row["id"],
        "canonical": row.get("canonical", ""),
        "frequency": row.get("frequency", len(occurrences)),
        "companies": [
            company["name"]
            for company in companies
            if isinstance(company, dict) and company.get("id") and company.get("name")
        ],
        "topic": topic,
        "topic_title": topic_title,
        "domains": [labels.get(domain, {}).get("label", domain) for domain in row.get("domains") or []],
        "technologies": row.get("technologies") or [],
        "question_type": row.get("question_type"),
        "type_title": QUESTION_TYPES.get(row.get("question_type") or "", row.get("question_type") or ""),
        "rounds": sorted(
            {ROUNDS.get(o.get("round") or "unknown", "") for o in occurrences} - {"", ROUNDS["unknown"]}
        ),
        "interview_types": sorted(
            {INTERVIEW_TYPES.get(o.get("interview_type") or "unknown", "") for o in occurrences} - {""}
        ),
        "answer_status": status,
        "answer_title": ANSWER_STATUSES.get(status, status),
        "excluded": row.get("report_exclusion"),
    }


def _answer(answer: dict) -> dict:
    return {
        "status": answer.get("status"),
        "status_title": ANSWER_STATUSES.get(answer.get("status") or "", answer.get("status")),
        "short_answer": answer.get("short_answer") or "",
        "key_points": [str(point) for point in answer.get("key_points") or []],
        "common_mistakes": [str(item) for item in answer.get("common_mistakes") or []],
        "follow_up_questions": [str(item) for item in answer.get("follow_up_questions") or []],
        "interviewer_intent": answer.get("interviewer_intent") or "",
        "spoken_answer": answer.get("spoken_answer") or "",
        "sources": [
            {
                "title": source.get("title") or source.get("url"),
                "url": source.get("url"),
                "publisher": source.get("publisher"),
            }
            for source in answer.get("sources") or []
            if isinstance(source, dict) and str(source.get("url", "")).startswith(("http://", "https://"))
        ],
        "verified_at": answer.get("verified_at"),
    }


def _static(name: str) -> bytes:
    return resources.files("gleaner.web").joinpath("static", name).read_bytes()


def _content_type(name: str) -> str:
    return CONTENT_TYPES.get(Path(name).suffix.lower(), "application/octet-stream")


def _first_line(message: str, limit: int = 160) -> str:
    line = message.strip().splitlines()[0] if message.strip() else ""
    return line if len(line) <= limit else line[:limit] + "…"


def _strings(value: object) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        value = value.splitlines()
    if not isinstance(value, list):
        raise GleanerError("关键词和链接必须是字符串列表")
    return [str(item).strip() for item in value if str(item).strip()]


def _optional_int(value: object) -> int | None:
    if value in (None, ""):
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise GleanerError("篇数必须是整数")
    return value


class _Handler(BaseHTTPRequestHandler):
    server_version = "Gleaner"
    app: WebApp

    def do_GET(self) -> None:  # noqa: N802 - name required by BaseHTTPRequestHandler
        self._dispatch("GET")

    def do_POST(self) -> None:  # noqa: N802
        self._dispatch("POST")

    def _dispatch(self, method: str) -> None:
        length = int(self.headers.get("Content-Length") or 0)
        if length > MAX_BODY_BYTES:
            response = error_response("请求内容过大", HTTPStatus.REQUEST_ENTITY_TOO_LARGE)
        else:
            body = self.rfile.read(length) if length else b""
            headers = {key.lower(): value for key, value in self.headers.items()}
            response = self.server.app.handle(method, self.path, headers, body)  # type: ignore[attr-defined]
        self.send_response(response.status)
        self.send_header("Content-Type", response.content_type)
        self.send_header("Content-Length", str(len(response.body)))
        self.send_header("Content-Security-Policy", CSP)
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Cache-Control", response.headers.pop("Cache-Control", "no-store"))
        for name, value in response.headers.items():
            self.send_header(name, value)
        self.end_headers()
        self.wfile.write(response.body)

    def log_message(self, format: str, *args) -> None:  # noqa: A002 - signature fixed by the base class
        return


class WebServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, workspace: Path, port: int = DEFAULT_PORT, *, jobs: CrawlJobs | None = None):
        super().__init__((HOST, port), _Handler)
        self.app = WebApp(workspace, jobs=jobs, port=self.server_address[1])

    @property
    def url(self) -> str:
        return f"http://{HOST}:{self.server_address[1]}/"


def serve(workspace: Path, *, port: int = DEFAULT_PORT, open_browser: bool = True) -> None:
    try:
        server = WebServer(workspace, port)
    except OSError as exc:
        raise GleanerError(
            f"端口 {port} 无法使用（{exc.strerror}），换一个端口：gleaner web --port 8766"
        ) from exc
    print(f"Gleaner 控制台：{server.url}  （只监听本机，Ctrl+C 停止）", flush=True)
    if open_browser:
        webbrowser.open(server.url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止", flush=True)
    finally:
        server.app.jobs.stop()
        server.server_close()
