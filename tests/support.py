"""Synthetic MediaCrawler output. Every post, company and person here is invented for tests."""

from __future__ import annotations

import json
import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path

from gleaner.config import Config, parse_config

FAKE_JPEG = b"\xff\xd8\xff\xe0" + b"gleaner-test-image" + b"\xff\xd9"
NOW = datetime(2026, 10, 27, 8, 0, tzinfo=UTC)

INTERVIEW_NOTE = {
    "note_id": "aa01",
    "type": "normal",
    "title": "示例科技后端一面面经",
    "desc": "一面问了 Redis 为什么快、MySQL 索引失效的场景，手撕了 LRU。#面经[话题]# #后端[话题]#",
    "time": 1792540800000,
    "liked_count": "1.2万",
    "collected_count": "356",
    "comment_count": "10+",
    "share_count": 3,
    "image_list": "https://img.example.com/1.jpg,https://img.example.com/2.jpg",
    "tag_list": "面经,后端",
    "note_url": "https://www.xiaohongshu.com/explore/aa01?xsec_token=SECRET&xsec_source=pc_search",
    "source_keyword": "后端 面经",
    "creator_hash": "0123456789abcdef",
}
PROMO_NOTE = {
    "note_id": "bb02",
    "type": "normal",
    "title": "大厂面经资料包免费领取",
    "desc": "整理了上百篇面经，私信我领取，训练营还有名额，学员上岸率很高",
    "time": 1792627200000,
    "image_list": "",
    "tag_list": "",
    "note_url": "https://www.xiaohongshu.com/explore/bb02?xsec_token=SECRET",
    "source_keyword": "后端 面经",
}
TEXT_NOTE = {
    "note_id": "cc03",
    "type": "normal",
    "title": "虚构公司 Java 二面复盘",
    "desc": "二面面试官问了线程池参数怎么设、ConcurrentHashMap 扩容过程，最后问了项目里最难的地方。",
    "time": "1792713600",
    "image_list": "",
    "tag_list": "Java",
    "note_url": "https://www.xiaohongshu.com/explore/cc03",
    "source_keyword": "Java 一面",
}
VIDEO_NOTE = {
    "note_id": "dd04",
    "type": "video",
    "title": "模拟面试录屏：系统设计一面",
    "desc": "一面系统设计题：设计一个短链服务",
    "time": 1792800000000,
    "image_list": "https://img.example.com/cover.jpg",
    "note_url": "https://www.xiaohongshu.com/explore/dd04",
    "source_keyword": "后端 面经",
}
ALL_NOTES = [INTERVIEW_NOTE, PROMO_NOTE, TEXT_NOTE, VIDEO_NOTE]


def write_export(
    raw_dir: Path, notes: list[dict] | None = None, *, images: dict[str, int] | None = None
) -> Path:
    """Lay out notes the way MediaCrawler writes them with --save_data_option jsonl --get_media yes."""
    notes = ALL_NOTES if notes is None else notes
    images = {"aa01": 2} if images is None else images
    jsonl = raw_dir / "xhs" / "jsonl" / "search_contents_2026-10-27.jsonl"
    jsonl.parent.mkdir(parents=True, exist_ok=True)
    jsonl.write_text("".join(json.dumps(note, ensure_ascii=False) + "\n" for note in notes), encoding="utf-8")
    for note_id, count in images.items():
        media = raw_dir / "xhs" / "media" / note_id
        media.mkdir(parents=True, exist_ok=True)
        for index in range(1, count + 1):
            (media / f"{index:03d}.jpg").write_bytes(FAKE_JPEG + bytes([index]))
    video_dir = raw_dir / "xhs" / "media" / "dd04"
    video_dir.mkdir(parents=True, exist_ok=True)
    (video_dir / "cover.jpg").write_bytes(FAKE_JPEG)
    (video_dir / "video.mp4").write_bytes(b"not-a-real-video")
    return raw_dir


def make_config(workspace: Path, **overrides) -> Config:
    data = {
        "keywords": ["后端 面经"],
        "mediacrawler": {"home": str(workspace / "MediaCrawler"), "min_interval_minutes": 5},
        "handoff": {"bank": str(workspace / "collected-bank"), "ibank": str(workspace / "ibank.py")},
    }
    for section, values in overrides.items():
        if isinstance(values, dict):
            data.setdefault(section, {}).update(values)
        else:
            data[section] = values
    return parse_config(data, workspace)


class WorkspaceTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.workspace = Path(self._tmp.name).resolve() / "ws"
        self.workspace.mkdir()

    def tearDown(self) -> None:
        self._tmp.cleanup()
