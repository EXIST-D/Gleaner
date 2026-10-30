import json
import unittest
from datetime import UTC, datetime, timedelta

from gleaner.adapters.common import ParseReport
from gleaner.adapters.exports import iter_posts
from gleaner.config import NowcoderConfig
from gleaner.sources.nowcoder import (
    DISCUSS_URL,
    LIST_URL,
    MOMENT_URL,
    NowcoderFetcher,
    list_entry,
    matches,
    parse_target,
)
from tests.support import FAKE_JPEG, WorkspaceTestCase

MOMENT_ID = "0123456789abcdef0123456789abcdef"


def discuss_record(content_id, title, summary="", created=None):
    data = {"title": title, "content": summary}
    if created is not None:
        data["createTime"] = int(created.timestamp() * 1000)
    return {"contentType": 250, "contentId": content_id, "contentData": data}


def page(records):
    return {"success": True, "data": {"records": records, "total": 2000}}


class FakeNowcoder:
    """Routes requests like the real endpoints; every reply is (status, headers, body)."""

    def __init__(self, pages, details=None, images=None):
        self.pages = pages
        self.details = details or {}
        self.images = images or {}
        self.requests = []
        self.queued = []

    def __call__(self, method, url, body, headers, timeout):
        self.requests.append(
            (method, url.split("?_=")[0].split("&_=")[0], json.loads(body) if body else None)
        )
        if self.queued:
            return self.queued.pop(0)
        if url.startswith(LIST_URL):
            number = json.loads(body)["page"]
            records = self.pages[number - 1] if number <= len(self.pages) else []
            return 200, {}, json.dumps(page(records)).encode()
        for template in (DISCUSS_URL, MOMENT_URL):
            prefix = template.format("")
            if url.startswith(prefix):
                post_id = url.removeprefix(prefix).split("?")[0]
                if post_id not in self.details:
                    return 200, {}, json.dumps({"success": False, "msg": "内容不存在"}).encode()
                return 200, {}, json.dumps({"success": True, "data": self.details[post_id]}).encode()
        if url in self.images:
            return 200, {"content-type": self.images[url]}, FAKE_JPEG
        return 404, {}, b""


class Clock:
    def __init__(self):
        self.now = 0.0
        self.sleeps = []

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.now += seconds


class NowcoderFetcherTest(WorkspaceTestCase):
    def setUp(self):
        super().setUp()
        self.clock = Clock()
        self.lines = []

    def fetcher(self, fake, **config):
        values = {"scan_pages": 3, "request_interval_seconds": 2.0, **config}
        return NowcoderFetcher(
            NowcoderConfig(**values),
            fetch=fake,
            sleep=self.clock.sleep,
            clock=self.clock,
            on_line=self.lines.append,
        )

    def fetch_into(self, fetcher, **kwargs):
        kwargs.setdefault("mode", "search")
        return fetcher.run(output_dir=self.workspace / "raw", log_dir=self.workspace / "run", **kwargs)

    def saved(self):
        report = ParseReport()
        return list(iter_posts(self.workspace / "raw", self.workspace, report))

    def test_incremental_search_skips_saved_posts_and_stops_at_older_pages(self):
        since = datetime(2026, 10, 28, tzinfo=UTC)
        newer, older = since + timedelta(hours=5), since - timedelta(hours=5)
        fake = FakeNowcoder(
            pages=[
                [discuss_record(1, "后端 一面", created=newer), discuss_record(2, "后端 二面", created=newer)]
                + [discuss_record(10 + i, "无关", created=newer) for i in range(18)],
                [discuss_record(3, "后端 三面", created=older), discuss_record(4, "后端 一面")]
                + [discuss_record(30 + i, "无关", created=older) for i in range(18)],
                [discuss_record(5, "后端 一面", created=older)]
                + [discuss_record(50, "无关", created=older)] * 19,
                [discuss_record(7, "后端 一面", created=newer)] + [{}] * 19,
            ],
            details={"2": {"title": "后端 二面", "richText": "<p>Redis</p>"}, "4": {"title": "后端 一面"}},
        )
        fetcher = self.fetcher(fake, download_images=False, scan_pages=5)
        result = self.fetch_into(fetcher, keywords=["后端"], max_notes=5, since=since, known={"1"})
        self.assertTrue(result.ok, result.message)
        lists = [body["page"] for method, url, body in fake.requests if url == LIST_URL]
        self.assertEqual(lists, [1, 2, 3], "page 2 has a post without a time; page 3 is all older, so stop")
        details = [url for method, url, body in fake.requests if method == "GET"]
        self.assertEqual(
            details, [DISCUSS_URL.format(2), DISCUSS_URL.format(4)], "1 is saved, 3 and 5 are older"
        )
        self.assertTrue(any("跳过已保存的 1 篇" in line for line in self.lines))

        fake = FakeNowcoder(pages=[[discuss_record(6, "后端 一面", created=older)] + [{}] * 19])
        result = self.fetch_into(self.fetcher(fake), keywords=["后端"], since=since)
        self.assertTrue(result.ok, "nothing new since the last search is not a failure")
        self.assertIn("没有新帖", result.message)
        self.assertTrue(any("不再往后翻" in line for line in self.lines))

    def test_search_reads_newest_pages_and_keeps_matching_posts(self):
        fake = FakeNowcoder(
            pages=[
                [discuss_record(1, "示例科技 后端一面", "Redis"), discuss_record(2, "前端二面")] + [{}] * 18,
                [
                    {
                        "contentType": 74,
                        "momentData": {"uuid": MOMENT_ID.upper(), "title": "", "content": "后端 一面 面经"},
                    },
                    discuss_record(3, "示例科技后端三面"),
                ]
                + [{}] * 18,
                [discuss_record(4, "后端 一面")],
            ],
            details={
                "1": {
                    "title": "示例科技 后端一面",
                    "richText": '<p>Redis</p><img src="https://uploadfiles.nowcoder.com/a.png">'
                    '<img src="https://evil.example.com/b.png"><img data-card-emoji="1" src="https://uploadfiles.nowcoder.com/e.png">',
                    "createTime": 1792540800000,
                    "authorId": 99,
                    "userBrief": {"nickname": "someone"},
                    "ip4Location": "某地",
                },
                MOMENT_ID: {
                    "title": "",
                    "content": "后端 一面 面经",
                    "createdAt": 1792627200000,
                    "imgMoment": [],
                },
            },
            images={"https://uploadfiles.nowcoder.com/a.png": "image/png"},
        )
        result = self.fetch_into(self.fetcher(fake), keywords=["后端 一面"], max_notes=2)
        self.assertTrue(result.ok, result.message)
        lists = [body["page"] for method, url, body in fake.requests if url == LIST_URL]
        self.assertEqual(lists, [1, 2], "stops paging once every keyword has enough posts")
        self.assertNotIn(("GET", DISCUSS_URL.format(3), None), fake.requests)

        posts = {post.post_id: post for post in self.saved()}
        self.assertEqual(set(posts), {"1", MOMENT_ID})
        self.assertEqual(posts["1"].keywords, ["后端 一面"])
        self.assertEqual(len(posts["1"].images), 1, "only *.nowcoder.com pictures are fetched")
        self.assertTrue(posts["1"].images[0].endswith("nowcoder/media/1/001.png"))
        self.assertEqual(len(posts["1"].author_hash), 16)

        raw = (self.workspace / "raw" / "nowcoder" / "jsonl").glob("search_contents_*.jsonl")
        text = next(raw).read_text(encoding="utf-8")
        self.assertNotIn("someone", text)
        self.assertNotIn("某地", text)
        self.assertTrue(any("已保存帖子 1" in line for line in self.lines))
        self.assertTrue((self.workspace / "run" / "nowcoder.log").is_file())

    def test_requests_are_spaced_by_the_interval(self):
        fake = FakeNowcoder(
            pages=[[discuss_record(1, "后端")]], details={"1": {"title": "后端", "richText": "x"}}
        )
        self.fetch_into(self.fetcher(fake, download_images=False), keywords=["后端"])
        api_waits = [s for s in self.clock.sleeps if s >= 2.0]
        self.assertEqual(len(api_waits), 1, "the detail request waits after the list request")

    def test_no_match_is_degraded_with_advice(self):
        fake = FakeNowcoder(pages=[[discuss_record(1, "前端")] * 20] * 3)
        result = self.fetch_into(self.fetcher(fake), keywords=["后端"])
        self.assertEqual(result.status, "degraded")
        self.assertIn("翻阅了最新 60 篇", result.message)
        self.assertIn("scan_pages", result.message)

    def test_rate_limit_is_retried_then_halts(self):
        fake = FakeNowcoder(
            pages=[[discuss_record(1, "后端")]], details={"1": {"title": "后端", "richText": "x"}}
        )
        fake.queued = [(429, {"retry-after": "7"}, b"")]
        result = self.fetch_into(self.fetcher(fake, download_images=False), keywords=["后端"])
        self.assertTrue(result.ok, result.message)
        self.assertIn(7.0, self.clock.sleeps)

        fake = FakeNowcoder(pages=[])
        fake.queued = [(429, {}, b"")] * 3
        result = self.fetch_into(self.fetcher(fake), keywords=["后端"])
        self.assertEqual(result.status, "degraded")
        self.assertIn("429", result.message)

    def test_verification_page_halts_and_keeps_saved_posts(self):
        fake = FakeNowcoder(
            pages=[[discuss_record(1, "后端"), discuss_record(2, "后端")]],
            details={"1": {"title": "后端", "richText": "x"}, "2": {"title": "后端", "richText": "y"}},
        )
        original = fake.__call__

        def blocking(method, url, body, headers, timeout):
            if url.startswith(DISCUSS_URL.format(2)):
                return 200, {}, "<html>访问过于频繁，请完成验证 verifycenter</html>".encode()
            return original(method, url, body, headers, timeout)

        fetcher = self.fetcher(blocking, download_images=False)
        result = self.fetch_into(fetcher, keywords=["后端"])
        self.assertEqual(result.status, "degraded")
        self.assertIn("拒绝", result.message)
        self.assertEqual([post.post_id for post in self.saved()], ["1"])

    def test_detail_mode_and_missing_posts(self):
        fake = FakeNowcoder(pages=[], details={"5": {"title": "指定帖", "richText": "<p>正文</p>"}})
        result = self.fetch_into(
            self.fetcher(fake),
            mode="detail",
            targets=["https://www.nowcoder.com/discuss/5?sourceSSR=search", "6", "不是链接"],
        )
        self.assertEqual(result.status, "degraded")
        self.assertIn("2 篇没有读到", result.message)
        self.assertEqual([post.post_id for post in self.saved()], ["5"])
        self.assertFalse(any(url == LIST_URL for _, url, _ in fake.requests))

    def test_stop_and_deadline(self):
        fake = FakeNowcoder(pages=[[discuss_record(1, "后端")]])
        fetcher = self.fetcher(fake)
        fetcher.should_stop = lambda: True
        result = self.fetch_into(fetcher, keywords=["后端"])
        self.assertIn("已停止", result.message)
        self.assertEqual(fake.requests, [])

        fake = FakeNowcoder(pages=[[discuss_record(1, "后端")] + [{}] * 19] * 3)
        fetcher = self.fetcher(fake, timeout_seconds=60, request_interval_seconds=40.0)
        result = self.fetch_into(fetcher, keywords=["前端"])
        self.assertIn("超过时限", result.message)


class NowcoderHelpersTest(unittest.TestCase):
    def test_parse_target(self):
        self.assertEqual(parse_target("https://www.nowcoder.com/discuss/123").kind, "discuss")
        self.assertEqual(parse_target(" 123 ").post_id, "123")
        moment = parse_target(f"https://www.nowcoder.com/feed/main/detail/{MOMENT_ID.upper()}")
        self.assertEqual((moment.kind, moment.post_id), ("moment", MOMENT_ID))
        self.assertIsNone(parse_target("https://www.nowcoder.com/users/1"))

    def test_list_entry_and_matching(self):
        self.assertIsNone(list_entry({"contentType": 999}))
        self.assertIsNone(list_entry({"contentType": 74, "momentData": {}}))
        pick, text = list_entry(discuss_record(9, "Java 一面", "问了 JVM"))
        self.assertEqual((pick.kind, pick.post_id), ("discuss", "9"))
        self.assertTrue(matches("java  jvm", text))
        self.assertFalse(matches("java 二面", text))


if __name__ == "__main__":
    unittest.main()
