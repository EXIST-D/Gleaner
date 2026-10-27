from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from gleaner.errors import GleanerError
from gleaner.models import LABELS, Post, PostRecord

DB_NAME = "gleaner.db"
SCHEMA_VERSION = 1

_SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS runs (
    id TEXT PRIMARY KEY,
    source TEXT NOT NULL,
    mode TEXT NOT NULL,
    params TEXT NOT NULL,
    output_dir TEXT NOT NULL,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    status TEXT NOT NULL,
    message TEXT NOT NULL DEFAULT '',
    counts TEXT NOT NULL DEFAULT '{}'
);
CREATE TABLE IF NOT EXISTS posts (
    key TEXT PRIMARY KEY,
    platform TEXT NOT NULL,
    post_id TEXT NOT NULL,
    url TEXT NOT NULL,
    open_url TEXT NOT NULL,
    title TEXT NOT NULL,
    body TEXT NOT NULL,
    kind TEXT NOT NULL,
    published_at TEXT,
    tags TEXT NOT NULL,
    keywords TEXT NOT NULL,
    images TEXT NOT NULL,
    remote_image_count INTEGER NOT NULL,
    metrics TEXT NOT NULL,
    author_hash TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    first_run TEXT NOT NULL REFERENCES runs(id),
    last_run TEXT NOT NULL REFERENCES runs(id),
    first_seen_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL,
    label TEXT,
    label_source TEXT,
    label_reasons TEXT NOT NULL DEFAULT '[]',
    screened_at TEXT,
    packed_in TEXT,
    packed_at TEXT,
    changed_after_pack INTEGER NOT NULL DEFAULT 0,
    UNIQUE (platform, post_id)
);
CREATE INDEX IF NOT EXISTS posts_label ON posts (label);
CREATE INDEX IF NOT EXISTS posts_packed ON posts (packed_in);
CREATE TABLE IF NOT EXISTS checkpoints (
    source TEXT NOT NULL,
    keyword TEXT NOT NULL,
    latest_published_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (source, keyword)
);
"""


@dataclass(frozen=True)
class RunRecord:
    id: str
    source: str
    mode: str
    params: dict
    output_dir: str
    started_at: str
    finished_at: str | None
    status: str
    message: str
    counts: dict


class Store:
    def __init__(self, connection: sqlite3.Connection):
        self._db = connection

    @classmethod
    @contextmanager
    def open(cls, workspace: Path, *, create: bool = False) -> Iterator[Store]:
        path = workspace / DB_NAME
        if not create and not path.is_file():
            raise GleanerError(f"找不到 {path}，请先运行 gleaner --workspace {workspace} init")
        connection = sqlite3.connect(path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        try:
            store = cls(connection)
            store._migrate()
            yield store
        finally:
            connection.close()

    def _migrate(self) -> None:
        with self._db:
            self._db.executescript(_SCHEMA)
            row = self._db.execute("SELECT value FROM meta WHERE key = 'schema_version'").fetchone()
            if row is None:
                self._db.execute(
                    "INSERT INTO meta (key, value) VALUES ('schema_version', ?)", (str(SCHEMA_VERSION),)
                )
            elif int(row["value"]) > SCHEMA_VERSION:
                raise GleanerError("工作区由更新版本的 Gleaner 创建，请先升级 Gleaner")

    # Runs

    def begin_run(
        self, run_id: str, *, source: str, mode: str, params: dict, output_dir: str, now: str
    ) -> None:
        with self._db:
            self._db.execute(
                "INSERT INTO runs (id, source, mode, params, output_dir, started_at, status)"
                " VALUES (?, ?, ?, ?, ?, ?, 'running')",
                (run_id, source, mode, _dump(params), output_dir, now),
            )

    def finish_run(self, run_id: str, *, status: str, message: str, counts: dict, now: str) -> None:
        with self._db:
            self._db.execute(
                "UPDATE runs SET status = ?, message = ?, counts = ?, finished_at = ? WHERE id = ?",
                (status, message, _dump(counts), now, run_id),
            )

    def last_run(self, source: str, mode: str | None = None) -> RunRecord | None:
        sql = "SELECT * FROM runs WHERE source = ?"
        args: list = [source]
        if mode:
            sql += " AND mode = ?"
            args.append(mode)
        row = self._db.execute(sql + " ORDER BY started_at DESC LIMIT 1", args).fetchone()
        return _run(row) if row else None

    def runs(self, limit: int = 20) -> list[RunRecord]:
        rows = self._db.execute("SELECT * FROM runs ORDER BY started_at DESC LIMIT ?", (limit,))
        return [_run(row) for row in rows]

    # Posts

    def upsert_post(self, post: Post, *, run_id: str, now: str) -> str:
        """Insert or refresh one post; returns "new", "updated" or "unchanged"."""
        row = self._db.execute("SELECT * FROM posts WHERE key = ?", (post.key,)).fetchone()
        with self._db:
            if row is None:
                self._insert(post, run_id=run_id, now=now)
                return "new"
            old = _record(row)
            keywords = list(dict.fromkeys([*old.post.keywords, *post.keywords]))
            content_changed = old.post.content_hash != post.content_hash
            images_arrived = bool(post.images) and not old.post.images
            self._db.execute(
                "UPDATE posts SET keywords = ?, metrics = ?, open_url = ?, last_run = ?, last_seen_at = ?"
                " WHERE key = ?",
                (
                    _dump(keywords),
                    _dump(post.metrics or old.post.metrics),
                    post.open_url,
                    run_id,
                    now,
                    post.key,
                ),
            )
            if not content_changed and not images_arrived:
                return "unchanged"
            images = post.images if images_arrived else old.post.images
            self._db.execute(
                "UPDATE posts SET title = ?, body = ?, kind = ?, published_at = ?, tags = ?, images = ?,"
                " remote_image_count = ?, content_hash = ? WHERE key = ?",
                (
                    post.title,
                    post.body,
                    post.kind,
                    post.published_at or old.post.published_at,
                    _dump(post.tags),
                    _dump(images),
                    post.remote_image_count,
                    post.content_hash,
                    post.key,
                ),
            )
            if old.label_source == "rule":
                self._db.execute(
                    "UPDATE posts SET label = NULL, label_source = NULL, label_reasons = '[]', screened_at = NULL"
                    " WHERE key = ?",
                    (post.key,),
                )
            if old.packed_in:
                self._db.execute("UPDATE posts SET changed_after_pack = 1 WHERE key = ?", (post.key,))
            return "updated"

    def _insert(self, post: Post, *, run_id: str, now: str) -> None:
        self._db.execute(
            "INSERT INTO posts (key, platform, post_id, url, open_url, title, body, kind, published_at, tags,"
            " keywords, images, remote_image_count, metrics, author_hash, content_hash, first_run, last_run,"
            " first_seen_at, last_seen_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                post.key,
                post.platform,
                post.post_id,
                post.url,
                post.open_url,
                post.title,
                post.body,
                post.kind,
                post.published_at,
                _dump(post.tags),
                _dump(post.keywords),
                _dump(post.images),
                post.remote_image_count,
                _dump(post.metrics),
                post.author_hash,
                post.content_hash,
                run_id,
                run_id,
                now,
                now,
            ),
        )

    def get(self, key: str) -> PostRecord | None:
        row = self._db.execute("SELECT * FROM posts WHERE key = ?", (key,)).fetchone()
        return _record(row) if row else None

    def posts(
        self,
        *,
        labels: Iterable[str] | None = None,
        unlabeled: bool = False,
        packed: bool | None = None,
        keys: Iterable[str] | None = None,
        limit: int | None = None,
    ) -> list[PostRecord]:
        clauses, args = [], []
        if labels is not None:
            labels = list(labels)
            clauses.append(f"label IN ({', '.join('?' for _ in labels)})")
            args.extend(labels)
        if unlabeled:
            clauses.append("label IS NULL")
        if packed is True:
            clauses.append("packed_in IS NOT NULL")
        elif packed is False:
            clauses.append("packed_in IS NULL")
        if keys is not None:
            keys = list(keys)
            clauses.append(f"key IN ({', '.join('?' for _ in keys)})")
            args.extend(keys)
        sql = "SELECT * FROM posts"
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY COALESCE(published_at, first_seen_at) DESC, key"
        if limit is not None:
            sql += " LIMIT ?"
            args.append(limit)
        return [_record(row) for row in self._db.execute(sql, args)]

    def set_label(self, key: str, label: str, *, source: str, reasons: list[str], now: str) -> None:
        if label not in LABELS:
            raise GleanerError(f"未知标签 {label}，可选：{' / '.join(LABELS)}")
        with self._db:
            cursor = self._db.execute(
                "UPDATE posts SET label = ?, label_source = ?, label_reasons = ?, screened_at = ? WHERE key = ?",
                (label, source, _dump(reasons), now, key),
            )
        if cursor.rowcount == 0:
            raise GleanerError(f"找不到帖子 {key}")

    def mark_packed(self, keys: Iterable[str], *, pack_id: str, now: str) -> None:
        with self._db:
            self._db.executemany(
                "UPDATE posts SET packed_in = ?, packed_at = ?, changed_after_pack = 0 WHERE key = ?",
                [(pack_id, now, key) for key in keys],
            )

    # Checkpoints

    def advance_checkpoint(self, source: str, keyword: str, published_at: str, *, now: str) -> None:
        with self._db:
            self._db.execute(
                "INSERT INTO checkpoints (source, keyword, latest_published_at, updated_at) VALUES (?, ?, ?, ?)"
                " ON CONFLICT (source, keyword) DO UPDATE SET"
                " latest_published_at = MAX(latest_published_at, excluded.latest_published_at),"
                " updated_at = excluded.updated_at",
                (source, keyword, published_at, now),
            )

    def checkpoints(self, source: str | None = None) -> list[dict]:
        sql, args = "SELECT * FROM checkpoints", []
        if source:
            sql, args = sql + " WHERE source = ?", [source]
        return [dict(row) for row in self._db.execute(sql + " ORDER BY source, keyword", args)]

    # Summary

    def stats(self) -> dict:
        def count(sql: str) -> int:
            return self._db.execute(sql).fetchone()[0]

        labels = {
            row["label"] or "unscreened": row["n"]
            for row in self._db.execute("SELECT label, COUNT(*) AS n FROM posts GROUP BY label")
        }
        kinds = {
            row["kind"]: row["n"]
            for row in self._db.execute("SELECT kind, COUNT(*) AS n FROM posts GROUP BY kind")
        }
        return {
            "posts": count("SELECT COUNT(*) FROM posts"),
            "labels": labels,
            "kinds": kinds,
            "packed": count("SELECT COUNT(*) FROM posts WHERE packed_in IS NOT NULL"),
            "changed_after_pack": count("SELECT COUNT(*) FROM posts WHERE changed_after_pack = 1"),
            "runs": count("SELECT COUNT(*) FROM runs"),
        }


def _dump(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def _run(row: sqlite3.Row) -> RunRecord:
    return RunRecord(
        id=row["id"],
        source=row["source"],
        mode=row["mode"],
        params=json.loads(row["params"]),
        output_dir=row["output_dir"],
        started_at=row["started_at"],
        finished_at=row["finished_at"],
        status=row["status"],
        message=row["message"],
        counts=json.loads(row["counts"]),
    )


def _record(row: sqlite3.Row) -> PostRecord:
    post = Post(
        platform=row["platform"],
        post_id=row["post_id"],
        url=row["url"],
        open_url=row["open_url"],
        title=row["title"],
        body=row["body"],
        kind=row["kind"],
        published_at=row["published_at"],
        tags=json.loads(row["tags"]),
        keywords=json.loads(row["keywords"]),
        images=json.loads(row["images"]),
        remote_image_count=row["remote_image_count"],
        metrics=json.loads(row["metrics"]),
        author_hash=row["author_hash"],
    )
    return PostRecord(
        post=post,
        first_run=row["first_run"],
        last_run=row["last_run"],
        first_seen_at=row["first_seen_at"],
        last_seen_at=row["last_seen_at"],
        label=row["label"],
        label_source=row["label_source"],
        label_reasons=json.loads(row["label_reasons"]),
        screened_at=row["screened_at"],
        packed_in=row["packed_in"],
        packed_at=row["packed_at"],
        changed_after_pack=bool(row["changed_after_pack"]),
    )
