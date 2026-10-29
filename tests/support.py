"""Synthetic MediaCrawler output. Every post, company and person here is invented for tests."""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path

from gleaner.config import Config, parse_config
from gleaner.urls import bilibili_bvid

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


DOUYIN_IMAGES = {
    "aweme_id": "7300000000000000001",
    "aweme_type": "68",
    "title": "示例科技测开一面 #面经 #测开",
    "desc": "示例科技测开一面 #面经 #测开\n题目都在图里",
    "create_time": 1792540800,
    "liked_count": "1.1万",
    "collected_count": "20",
    "comment_count": "5",
    "share_count": "2",
    "aweme_url": "https://www.douyin.com/video/7300000000000000001",
    "note_download_url": "https://img.example.com/d1.jpg,https://img.example.com/d2.jpg",
    "video_download_url": "",
    "source_keyword": "测开 面经",
}
DOUYIN_VIDEO = {
    "aweme_id": "7300000000000000002",
    "aweme_type": "0",
    "desc": "口述虚构公司后端二面",
    "create_time": "1792627200",
    "note_download_url": "",
    "video_download_url": "https://video.example.com/v.mp4",
    "source_keyword": "测开 面经",
}
BILI_VIDEO = {
    "video_id": "1150000001",
    "title": "虚构大厂 Go 后端三面复盘",
    "desc": "-",
    "create_time": 1792540800,
    "liked_count": "88",
    "video_play_count": "1.5万",
    "video_favorite_count": "120",
    "video_share_count": "3",
    "video_comment": "9",
    "video_url": "https://www.bilibili.com/video/av1150000001",
    "source_keyword": "Go 面经",
}
BILI_RECORDING = {
    "video_id": "1150000002",
    "title": "虚构公司 Java 后端一面录音（全程）",
    "desc": "远程面试，声音有点小",
    "create_time": 1792627200,
    "video_url": "https://www.bilibili.com/video/av1150000002",
    "source_keyword": "Java 面经",
}
BILI_RECORDING_BVID = bilibili_bvid("1150000002")
FAKE_VIDEO = b"not-a-real-video"
ZHIHU_ANSWER = {
    "content_id": "300000001",
    "content_type": "answer",
    "content_text": "一面问了 TCP 三次握手和 Redis 持久化，二面问了项目。",
    "content_url": "https://www.zhihu.com/question/200000001/answer/300000001?utm_source=x",
    "question_id": "200000001",
    "title": "虚构公司后端面试都问什么？",
    "created_time": 1792540800,
    "voteup_count": 42,
    "comment_count": 3,
    "source_keyword": "后端 面经",
}
ZHIHU_ARTICLE = {
    "content_id": "600000001",
    "content_type": "article",
    "content_text": "整理了虚构公司 Java 一面的题目：问&#34;为什么用 Redis&#34;。",
    "content_url": "https://zhuanlan.zhihu.com/p/600000001",
    "title": "虚构公司 Java 一面 &amp; 二面",
    "created_time": 1792627200,
    "source_keyword": "后端 面经",
}
NOWCODER_DISCUSS = {
    "kind": "discuss",
    "post_id": "800000001",
    "url": "https://www.nowcoder.com/discuss/800000001",
    "source_keywords": ["后端 面经"],
    "author_hash": "fedcba9876543210",
    "detail": {
        "title": "示例科技后端一面",
        "richText": (
            "<p>一面 50 分钟</p><ol><li>Redis 为什么快</li><li>MySQL &amp; 索引</li></ol>"
            '<p><img src="https://uploadfiles.nowcoder.com/images/a.png"></p>'
            '<p><img data-card-emoji="1" src="https://static.nowcoder.com/emoji/smile.png"></p>'
        ),
        "createTime": 1792540800000,
        "frequencyData": {"likeCnt": 7, "followCnt": 4, "commentCnt": 2, "viewCnt": 300},
    },
}
NOWCODER_MOMENT = {
    "kind": "moment",
    "post_id": "0123456789abcdef0123456789abcdef",
    "url": "https://www.nowcoder.com/feed/main/detail/0123456789abcdef0123456789abcdef",
    "source_keywords": ["Java 一面"],
    "detail": {
        "title": "虚构公司 Java 一面",
        "content": '问了 List&lt;String&gt; 的泛型擦除<br/>还有线程池<img data-card-emoji="1" src="https://x/e.png">'
        '<a href="/creation/subject/0" target="_blank" class="subject">#秋招面试记录#</a>',
        "imgMoment": [{"src": "https://uploadfiles.nowcoder.com/images/m1.jpg"}],
        "createdAt": 1792627200000,
    },
}


def write_records(raw_dir: Path, data_dir: str, records: list[dict], *, mode: str = "search") -> Path:
    """One platform's records laid out as `<raw>/<data dir>/jsonl/<mode>_contents_<date>.jsonl`."""
    jsonl = raw_dir / data_dir / "jsonl" / f"{mode}_contents_2026-10-27.jsonl"
    jsonl.parent.mkdir(parents=True, exist_ok=True)
    with jsonl.open("a", encoding="utf-8") as handle:
        handle.write("".join(json.dumps(record, ensure_ascii=False) + "\n" for record in records))
    return raw_dir


def write_images(raw_dir: Path, media_dir: str, post_id: str, count: int) -> None:
    media = raw_dir / media_dir / "media" / post_id
    media.mkdir(parents=True, exist_ok=True)
    for index in range(1, count + 1):
        (media / f"{index:03d}.jpg").write_bytes(FAKE_JPEG + bytes([index]))


def write_video(raw_dir: Path, media_dir: str, folder: str) -> Path:
    """A downloaded video the way MediaCrawler stores it (Bilibili folders are named by BV ID)."""
    media = raw_dir / media_dir / "media" / folder
    media.mkdir(parents=True, exist_ok=True)
    (media / "cover.jpg").write_bytes(FAKE_JPEG)
    (media / "video.mp4").write_bytes(FAKE_VIDEO)
    return media / "video.mp4"


def fake_tool(directory: Path, name: str) -> Path:
    """An executable that does nothing, so shutil.which finds it on every OS."""
    directory.mkdir(parents=True, exist_ok=True)
    if os.name == "nt":
        path = directory / f"{name}.bat"
        path.write_text("@echo off\r\n", encoding="utf-8")
    else:
        path = directory / name
        path.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        path.chmod(0o755)
    return path


def fake_extract(source: Path, target: Path) -> None:
    target.write_bytes(b"fLaC" + source.read_bytes())


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
