import json
import sys
import threading
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path

from gleaner.config import load_config
from gleaner.errors import GleanerError
from gleaner.pipeline import import_export
from gleaner.sources.base import FetchResult
from gleaner.store import Store
from gleaner.web.jobs import CrawlJobs
from gleaner.web.server import TOKEN_PLACEHOLDER, WebApp, WebServer
from tests.support import NOW, NOWCODER_DISCUSS, WorkspaceTestCase, write_export, write_records

FAKE_MEDIACRAWLER = r"""
import json, sys, time
from pathlib import Path

options = dict(zip(sys.argv[1::2], sys.argv[2::2]))
print("[XiaoHongShuCrawler] Launching browser using CDP mode", flush=True)
print("[XiaoHongShuLogin.login_by_qrcode] waiting for scan code login, remaining time is 120s", flush=True)
if Path("SLOW").exists():
    time.sleep(60)
print("[XiaoHongShuLogin.check_login_state] Login status confirmed by Cookie", flush=True)
out = Path(options["--save_data_path"]) / "xhs"
(out / "jsonl").mkdir(parents=True, exist_ok=True)
note = {"note_id": "web01", "type": "normal", "title": "示例科技后端一面", "desc": "一面问了 Redis 为什么快",
        "time": 1792540800000, "image_list": "https://img.example.com/1.jpg", "source_keyword": options["--keywords"]}
(out / "jsonl" / "search_contents_2026-10-28.jsonl").write_text(json.dumps(note, ensure_ascii=False) + "\n", encoding="utf-8")
media = out / "media" / "web01"
media.mkdir(parents=True, exist_ok=True)
(media / "001.webp").write_bytes(b"RIFF0000WEBPVP8 ")
print("[store.xhs.update_xhs_note] xhs note: web01", flush=True)
print("[download] 下载完成: 001.webp", flush=True)
"""


def init_workspace(root: Path) -> tuple[Path, Path]:
    home = root / "MediaCrawler"
    home.mkdir()
    (home / "main.py").write_text(FAKE_MEDIACRAWLER, encoding="utf-8")
    workspace = root / "workspace"
    workspace.mkdir()
    (workspace / "gleaner.toml").write_text(
        "\n".join(
            [
                'keywords = ["后端 面经"]',
                "[mediacrawler]",
                f"home = {json.dumps(str(home))}",
                f'command = [{json.dumps(sys.executable)}, "main.py"]',
                "timeout_seconds = 60",
                "min_interval_minutes = 5",
                "[handoff]",
                f"bank = {json.dumps(str(root / 'bank'))}",
            ]
        ),
        encoding="utf-8",
    )
    with Store.open(workspace, create=True):
        pass
    return workspace, home


class WebAppTest(WorkspaceTestCase):
    def setUp(self):
        super().setUp()
        self.ws, _ = init_workspace(self.workspace)
        source = write_export(self.workspace / "export")
        with Store.open(self.ws) as store:
            import_export(store, load_config(self.ws), source, now=NOW)
        self.app = WebApp(self.ws, port=8765)

    def get(self, path, host="127.0.0.1:8765"):
        response = self.app.handle("GET", path, {"host": host}, b"")
        return response, json.loads(response.body) if response.content_type.startswith(
            "application/json"
        ) else None

    def post(self, path, payload, token=None):
        headers = {"host": "localhost:8765", "x-gleaner-token": self.app.token if token is None else token}
        response = self.app.handle("POST", path, headers, json.dumps(payload).encode("utf-8"))
        return response, json.loads(response.body)

    def test_only_loopback_hosts_are_served(self):
        response, data = self.get("/api/overview", host="evil.example:8765")
        self.assertEqual(response.status, 403)
        self.assertFalse(data["ok"])

    def test_index_carries_the_page_token(self):
        response, _ = self.get("/")
        html = response.body.decode("utf-8")
        self.assertIn(self.app.token, html)
        self.assertNotIn(TOKEN_PLACEHOLDER, html)

    def test_static_files_are_whitelisted(self):
        script = self.get("/static/app.js")[0]
        self.assertEqual((script.status, script.content_type), (200, "text/javascript; charset=utf-8"))
        self.assertEqual(self.get("/static/app.css")[0].content_type, "text/css; charset=utf-8")
        self.assertEqual(self.get("/static/icon.svg")[0].content_type, "image/svg+xml")
        self.assertEqual(self.get("/static/../server.py")[0].status, 404)
        self.assertEqual(self.get("/static/missing.js")[0].status, 404)

    def test_overview(self):
        response, data = self.get("/api/overview")
        self.assertEqual(response.status, 200)
        self.assertEqual(data["stats"]["posts"], 4)
        self.assertEqual(data["unpacked_candidates"], 3)
        platforms = {p["id"]: p for p in data["platforms"]}
        self.assertEqual(list(platforms), ["xhs", "dy", "bili", "ks", "zhihu", "nowcoder"])
        self.assertEqual((platforms["xhs"]["page_size"], platforms["dy"]["page_size"]), (20, 10))
        self.assertIsNone(platforms["nowcoder"]["page_size"])
        self.assertEqual(platforms["nowcoder"]["engine"], "nowcoder")
        self.assertEqual(platforms["xhs"]["steps"][0]["name"], "browser")
        self.assertEqual(platforms["nowcoder"]["steps"][0]["title"], "翻阅面经列表")
        self.assertNotIn("download", [step["name"] for step in platforms["bili"]["steps"]])
        self.assertEqual(platforms["xhs"]["next_crawl_in"], 0)
        self.assertEqual(data["stats"]["platforms"], {"xhs": 4})
        self.assertTrue(any(check["name"] == "mediacrawler" and check["ok"] for check in data["checks"]))
        self.assertEqual((data["runs"][0]["mode"], data["runs"][0]["platform"]), ("import", "小红书"))

    def test_posts_filters_counts_and_intake(self):
        _, data = self.get("/api/posts?label=candidate&packed=no")
        self.assertEqual(sorted(p["key"] for p in data["posts"]), ["xhs:aa01", "xhs:cc03", "xhs:dd04"])
        self.assertEqual(data["counts"]["promo"], 1)
        image_post = next(p for p in data["posts"] if p["key"] == "xhs:aa01")
        self.assertEqual((image_post["intake"], image_post["images"]), ("images", 2))
        self.assertTrue(image_post["thumb"].startswith("/media/runs/"))
        _, found = self.get("/api/posts?q=java")
        self.assertEqual([p["key"] for p in found["posts"]], ["xhs:cc03"])
        self.assertEqual(image_post["platform_title"], "小红书")

    def test_posts_filter_by_platform(self):
        source = write_records(self.workspace / "export2", "nowcoder", [NOWCODER_DISCUSS])
        with Store.open(self.ws) as store:
            import_export(store, load_config(self.ws), source, now=NOW)
        _, data = self.get("/api/posts?platform=nowcoder")
        self.assertEqual([p["key"] for p in data["posts"]], ["nowcoder:800000001"])
        self.assertEqual(data["counts"]["candidate"], 1)
        self.assertEqual(
            data["platforms"],
            [{"id": "xhs", "title": "小红书", "count": 4}, {"id": "nowcoder", "title": "牛客", "count": 1}],
        )
        self.assertEqual(len(self.get("/api/posts?platform=")[1]["posts"]), 5)

    def test_post_detail_and_media(self):
        _, data = self.get("/api/post?key=xhs:aa01")
        post = data["post"]
        self.assertEqual(len(post["images"]), 2)
        self.assertIn("xsec_token", post["open_url"])
        media, _ = self.get(post["images"][0])
        self.assertEqual((media.status, media.content_type), (200, "image/jpeg"))
        self.assertEqual(self.get("/media/../workspace/gleaner.toml")[0].status, 404)
        self.assertEqual(self.get("/media/gleaner.db")[0].status, 404)
        self.assertEqual(self.get("/api/post?key=xhs:none")[0].status, 400)

    def test_writes_need_the_page_token(self):
        response, data = self.post("/api/label", {"key": "xhs:aa01", "label": "promo"}, token="wrong")
        self.assertEqual(response.status, 403)
        self.assertIn("令牌", data["error"])

    def test_label_pack_and_read_the_handoff(self):
        _, data = self.post("/api/label", {"key": "xhs:dd04", "label": "irrelevant", "reason": "视频"})
        self.assertEqual(data["post"]["label"], "irrelevant")
        with Store.open(self.ws) as store:
            self.assertEqual(store.get("xhs:dd04").label_source, "manual")

        response, data = self.post("/api/pack", {"keys": ["xhs:aa01", "xhs:cc03"]})
        self.assertEqual(response.status, 200, data)
        pack_id = data["pack"]["pack_id"]
        _, packs = self.get("/api/packs")
        self.assertEqual(packs["packs"][0]["id"], pack_id)
        self.assertEqual(packs["packs"][0]["posts"], 2)
        _, detail = self.get(f"/api/pack?id={pack_id}")
        self.assertIn("ingest images", detail["pack"]["handoff"])
        self.assertEqual(self.get("/api/pack?id=../../etc")[0].status, 400)
        self.assertEqual(self.post("/api/pack", {"keys": []})[0].status, 400)

    def test_progress_page_and_feedback(self):
        (self.ws / "开发进度.md").write_text("# 本机进度\n", encoding="utf-8")
        _, data = self.get("/api/dev")
        self.assertEqual((data["notes"]["markdown"], data["feedback"]), ("# 本机进度\n", []))
        self.assertIn("帖子", data["feedback_topics"])
        self.assertEqual(
            self.post("/api/feedback", {"text": "好", "topic": "帖子"}, token="wrong")[0].status, 403
        )
        response, saved = self.post("/api/feedback", {"text": "帖子卡片想显示公司", "topic": "帖子"})
        self.assertEqual(response.status, 200, saved)
        self.assertEqual(self.get("/api/dev")[1]["feedback"][0]["text"], "帖子卡片想显示公司")
        self.assertIn("不能为空", self.post("/api/feedback", {"text": " ", "topic": "帖子"})[1]["error"])

    def test_crawl_requests_are_validated(self):
        self.assertIn("至少", self.post("/api/crawl", {"keywords": []})[1]["error"])
        self.assertIn("逗号", self.post("/api/crawl", {"keywords": ["后端,面经"]})[1]["error"])
        self.assertIn("整数", self.post("/api/crawl", {"keywords": ["后端"], "max_notes": "20"})[1]["error"])
        self.assertIn(
            "不支持", self.post("/api/crawl", {"platform": "weibo", "keywords": ["后端"]})[1]["error"]
        )
        self.assertIsNone(self.get("/api/crawl")[1]["job"])


class CrawlJobTest(WorkspaceTestCase):
    def setUp(self):
        super().setUp()
        self.ws, self.home = init_workspace(self.workspace)
        self.jobs = CrawlJobs(self.ws)

    def tearDown(self):
        self.jobs.stop()
        self.jobs.wait(20)
        super().tearDown()

    def wait_for_line(self, text, timeout=15):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if any(text in line for line in self.jobs.current.snapshot()["lines"]):
                return
            time.sleep(0.05)
        self.fail(f"never saw {text!r}")

    def test_a_crawl_runs_imports_and_reports_progress(self):
        job = self.jobs.start(keywords=["后端 面经"], targets=[], max_notes=None, force=False)
        self.assertEqual(job.params["planned_notes"], 20)
        self.jobs.wait(30)
        snapshot = job.snapshot()
        self.assertEqual(snapshot["state"], "ok", snapshot)
        self.assertEqual(snapshot["outcome"]["new"], 1)
        self.assertEqual(snapshot["progress"]["step"], "done")
        self.assertEqual((snapshot["progress"]["notes"], snapshot["progress"]["media"]), (1, 1))
        self.assertIn("Launching browser", snapshot["lines"][0])
        self.assertEqual(job.snapshot(since=snapshot["next"])["lines"], [])
        with Store.open(self.ws) as store:
            self.assertEqual(store.get("xhs:web01").label, "candidate")

        with self.assertRaisesRegex(GleanerError, "分钟"):
            self.jobs.start(keywords=["后端 面经"], targets=[], max_notes=None, force=False)

    def test_one_crawl_at_a_time_and_stop(self):
        (self.home / "SLOW").write_text("", encoding="utf-8")
        job = self.jobs.start(keywords=["后端 面经"], targets=[], max_notes=None, force=False)
        self.wait_for_line("waiting for scan code")
        self.assertEqual(job.snapshot()["progress"]["attention"], "qr")
        with self.assertRaisesRegex(GleanerError, "已经有"):
            self.jobs.start(keywords=["Java"], targets=[], max_notes=None, force=True)
        self.assertTrue(self.jobs.stop())
        self.jobs.wait(20)
        self.assertEqual(job.snapshot()["state"], "stopped")

    def test_nowcoder_crawl_uses_the_job_hooks(self):
        seen = {}

        class FakeFetcher:
            def __init__(self, on_line, should_stop):
                self.on_line, self.should_stop = on_line, should_stop

            def problems(self):
                return []

            def run(self, *, mode, output_dir, log_dir, keywords=(), targets=(), max_notes=None):
                seen["max_notes"] = max_notes
                self.on_line("[nowcoder] 翻阅最新面经列表第 1 页")
                write_records(output_dir, "nowcoder", [NOWCODER_DISCUSS])
                self.on_line("[nowcoder] 已保存帖子 800000001：示例")
                seen["stop"] = self.should_stop()
                return FetchResult("ok", output_dir)

        def factory(config, platform, *, runner, on_line, should_stop):
            seen["platform"] = platform
            return FakeFetcher(on_line, should_stop)

        jobs = CrawlJobs(self.ws, fetcher_factory=factory)
        job = jobs.start(platform="nowcoder", keywords=["后端 面经"], targets=[], max_notes=7, force=False)
        jobs.wait(20)
        snapshot = job.snapshot()
        self.assertEqual(snapshot["state"], "ok", snapshot)
        self.assertEqual(seen, {"platform": "nowcoder", "max_notes": 7, "stop": False})
        self.assertEqual((job.params["platform"], job.params["planned_notes"]), ("nowcoder", 7))
        self.assertEqual(snapshot["progress"]["notes"], 1)
        self.assertEqual(snapshot["progress"]["steps"][0]["name"], "search")
        with Store.open(self.ws) as store:
            self.assertIsNotNone(store.get("nowcoder:800000001"))
            self.assertEqual(store.runs()[0].source, "nowcoder")


class ServerTest(WorkspaceTestCase):
    def setUp(self):
        super().setUp()
        self.ws, _ = init_workspace(self.workspace)
        self.server = WebServer(self.ws, port=0)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        super().tearDown()

    def request(self, path, *, data=None, headers=None):
        request = urllib.request.Request(self.server.url.rstrip("/") + path, data=data, headers=headers or {})
        try:
            with urllib.request.urlopen(request, timeout=10) as response:
                return response.status, dict(response.headers), response.read()
        except urllib.error.HTTPError as error:
            return error.code, dict(error.headers), error.read()

    def test_security_headers_and_token_round_trip(self):
        status, headers, body = self.request("/")
        self.assertEqual(status, 200)
        self.assertIn("script-src 'self'", headers["Content-Security-Policy"])
        self.assertEqual(headers["X-Content-Type-Options"], "nosniff")
        token = self.server.app.token
        self.assertIn(token, body.decode("utf-8"))

        status, _, body = self.request(
            "/api/crawl/stop",
            data=b"{}",
            headers={"X-Gleaner-Token": token, "Content-Type": "application/json"},
        )
        self.assertEqual((status, json.loads(body)["stopping"]), (200, False))

    def test_rebinding_hosts_are_refused(self):
        status, _, _ = self.request("/api/overview", headers={"Host": "attacker.example"})
        self.assertEqual(status, 403)


if __name__ == "__main__":
    unittest.main()
