import json
import sys
import unittest

from gleaner.bank import note_key
from gleaner.config import load_config
from gleaner.pipeline import import_export
from gleaner.store import Store
from gleaner.web.server import WebApp
from tests.support import NOW, WorkspaceTestCase, write_export
from tests.test_web import init_workspace

IMAGE = "a" * 64 + ".webp"

QUESTIONS = [
    {
        "id": "q_" + "1" * 32,
        "canonical": "Agent 记忆模块怎么设计？",
        "question_type": "system-design",
        "domains": ["ai.agent.memory"],
        "technologies": [],
        "frequency": 2,
        "answer_status": "source_backed",
        "companies": [
            {"id": "company_a", "name": "示例科技", "frequency": 1},
            {"id": "company_b", "name": "虚构公司", "frequency": 1},
        ],
        "occurrences": [
            {
                "id": "occ_1",
                "source_id": "src_img",
                "company_id": "company_a",
                "round": "technical-1",
                "interview_type": "campus",
                "original_text": "Agent 记忆模块怎么设计？",
                "parent_occurrence_id": None,
            },
            {
                "id": "occ_2",
                "source_id": "src_web",
                "company_id": "company_b",
                "round": "technical-2",
                "interview_type": "intern",
                "original_text": "记忆怎么存？",
                "parent_occurrence_id": "occ_9",
            },
        ],
    },
    {
        "id": "q_" + "2" * 32,
        "canonical": "合并 N 个有序数组",
        "question_type": "coding",
        "domains": ["computer-science.algorithm"],
        "technologies": [],
        "frequency": 1,
        "answer_status": "missing",
        "companies": [{"id": "company_a", "name": "示例科技", "frequency": 1}],
        "occurrences": [
            {
                "id": "occ_9",
                "source_id": "src_img",
                "company_id": "company_a",
                "round": "technical-1",
                "interview_type": "campus",
                "original_text": "合并 N 个有序数组",
                "parent_occurrence_id": None,
            },
        ],
    },
    {
        "id": "q_" + "3" * 32,
        "canonical": "你平时怎么学习？",
        "question_type": "behavioral",
        "domains": ["career.behavioral"],
        "technologies": [],
        "frequency": 1,
        "answer_status": "missing",
        "companies": [{"id": "company_b", "name": "虚构公司", "frequency": 1}],
        "occurrences": [
            {"id": "occ_5", "source_id": "src_web", "company_id": "company_b", "round": "unknown"}
        ],
        "report_exclusion": "用户要求排除",
    },
]

SHOW = {
    "question": {
        **QUESTIONS[0],
        "role_tracks": ["ai-agent"],
        "answer": {
            "status": "source_backed",
            "short_answer": "分层存储短期与长期记忆。",
            "key_points": ["短期记忆放在上下文里"],
            "sources": [
                {"title": "官方文档", "url": "https://example.org/memory", "publisher": "Example"},
                {"title": "坏链接", "url": "javascript:alert(1)"},
            ],
        },
    },
    "sources": [
        {
            "id": "src_img",
            "type": "image",
            "path": f"media/{IMAGE}",
            "platform": "小红书",
            "source_url": "https://www.xiaohongshu.com/explore/aa01",
            "source_date": "2026-10-21",
        },
        {
            "id": "src_web",
            "type": "web",
            "path": "media/" + "b" * 64 + ".txt",
            "platform": "小红书",
            "source_url": "https://www.xiaohongshu.com/explore/zz99",
            "source_date": "2026-10-22",
        },
    ],
    "relations": [
        {"from_question_id": "q_" + "2" * 32, "to_question_id": "q_" + "1" * 32, "type": "related"}
    ],
    "merged_variants": [{"canonical": "记忆怎么存？"}],
}

TAXONOMY = [
    {"id": "ai.agent.memory", "label": "智能体记忆"},
    {"id": "computer-science.algorithm", "label": "算法与复杂度"},
    {"id": "career.behavioral", "label": "行为面试"},
]

FAKE_IBANK = r"""
import json, sys
from pathlib import Path
here = Path(__file__).parent
with (here / "calls.log").open("a", encoding="utf-8") as log:
    log.write(" ".join(sys.argv[1:3]) + "\n")
fixture = json.loads((here / "fixture.json").read_text(encoding="utf-8"))
command = sys.argv[1]
if command == "search":
    for row in fixture["questions"]:
        print(json.dumps(row, ensure_ascii=False))
elif command == "show":
    if sys.argv[2] != fixture["show"]["question"]["id"]:
        print(json.dumps({"ok": False, "error": "Unknown question"}))
        sys.exit(2)
    print(json.dumps({"ok": True, "result": fixture["show"]}, ensure_ascii=False))
elif command == "taxonomy":
    print(json.dumps({"ok": True, "result": {"items": fixture["taxonomy"]}}, ensure_ascii=False))
elif command == "export":
    args = sys.argv[2:]
    option = lambda name: args[args.index(name) + 1] if name in args else ""
    answer = Path(option("--output"))
    question = answer.with_name(answer.stem + "（题目版）.md")
    scope = f"company={option('--company')} domain={option('--domain')}"
    answer.write_text(f"# 面试题整理报告\\n\\n{scope}\\n\\n| 领域 | 题目数 |\\n|---|---:|\\n| 智能体 | 2 |\\n", encoding="utf-8")
    question.write_text("# 面试题整理报告\\n\\n题目版\\n", encoding="utf-8")
    print(json.dumps({"ok": True, "result": {"answer_output": str(answer), "question_output": str(question),
                      "questions": 2, "answered_questions": 1, "pending_answers": 1, "excluded_questions": 1}}))
"""


class BankPageTest(WorkspaceTestCase):
    def setUp(self):
        super().setUp()
        self.ws, _ = init_workspace(self.workspace)
        self.bank = self.workspace / "bank"
        (self.bank / "data").mkdir(parents=True)
        (self.bank / "media").mkdir()
        (self.bank / "manifest.json").write_text("{}", encoding="utf-8")
        (self.bank / "data" / "questions.jsonl").write_text("{}\n", encoding="utf-8")
        (self.bank / "media" / IMAGE).write_bytes(b"RIFF0000WEBP")
        tools = self.workspace / "ibank"
        tools.mkdir()
        self.ibank = tools / "ibank.py"
        self.ibank.write_text(FAKE_IBANK, encoding="utf-8")
        (tools / "fixture.json").write_text(
            json.dumps({"questions": QUESTIONS, "show": SHOW, "taxonomy": TAXONOMY}, ensure_ascii=False),
            encoding="utf-8",
        )
        config = self.ws / "gleaner.toml"
        config.write_text(
            config.read_text(encoding="utf-8")
            + f"\nibank = {json.dumps(str(self.ibank))}\npython = {json.dumps(sys.executable)}\n",
            encoding="utf-8",
        )
        source = write_export(self.workspace / "export")
        with Store.open(self.ws) as store:
            import_export(store, load_config(self.ws), source, now=NOW)
        self.app = WebApp(self.ws, port=8765)

    def get(self, path):
        response = self.app.handle("GET", path, {"host": "127.0.0.1:8765"}, b"")
        return response, json.loads(response.body) if response.content_type.startswith(
            "application/json"
        ) else None

    def calls(self) -> list[str]:
        return (self.ibank.parent / "calls.log").read_text(encoding="utf-8").splitlines()

    def test_list_summary_topics_and_exclusions(self):
        _, data = self.get("/api/bank")
        self.assertTrue(data["available"])
        self.assertEqual(
            data["summary"],
            {"questions": 2, "excluded": 1, "occurrences": 3, "companies": 2, "answered": 1, "repeated": 1},
        )
        self.assertEqual(
            [(t["title"], t["count"]) for t in data["topics"]], [("智能体记忆", 1), ("算法与复杂度", 1)]
        )
        self.assertEqual(data["companies"][0], {"name": "示例科技", "count": 2})
        first = data["questions"][0]
        self.assertEqual(first["companies"], ["示例科技", "虚构公司"])
        self.assertEqual((first["type_title"], first["answer_title"]), ("系统设计", "有来源答案"))
        self.assertEqual(first["rounds"], ["一面", "二面"])
        self.assertEqual(data["questions"][2]["excluded"], "用户要求排除")

    def test_list_is_cached_until_the_bank_changes(self):
        self.get("/api/bank")
        self.get("/api/bank")
        self.assertEqual([call for call in self.calls() if call.startswith("search")], ["search --format"])
        data_file = self.bank / "data" / "questions.jsonl"
        data_file.write_text("{}\n{}\n", encoding="utf-8")
        self.get("/api/bank")
        self.assertEqual(len([call for call in self.calls() if call.startswith("search")]), 2)

    def test_question_detail_links_sources_posts_and_follow_ups(self):
        _, data = self.get("/api/bank/question?id=q_" + "1" * 32)
        q = data["question"]
        image, web = q["occurrences"]
        self.assertEqual(
            (image["company"], image["round"], image["interview_type"]), ("示例科技", "一面", "校招")
        )
        self.assertEqual(image["image"], f"/bank-media/{IMAGE}")
        self.assertEqual(image["post_key"], "xhs:aa01", "the post exists in the Gleaner workspace")
        self.assertIsNone(web["image"])
        self.assertIsNone(web["post_key"], "zz99 was never crawled by Gleaner")
        self.assertEqual(web["follow_up_of"], {"id": "q_" + "2" * 32, "text": "合并 N 个有序数组"})
        self.assertEqual(
            q["relations"], [{"id": "q_" + "2" * 32, "canonical": "合并 N 个有序数组", "type": "related"}]
        )
        self.assertEqual(q["merged_variants"], ["记忆怎么存？"])
        self.assertEqual([s["url"] for s in q["answer"]["sources"]], ["https://example.org/memory"])
        self.assertEqual(q["answer"]["status_title"], "有来源答案")

    def test_bad_question_ids(self):
        self.assertIn("编号", self.get("/api/bank/question?id=../x")[1]["error"])
        self.assertIn("Unknown question", self.get("/api/bank/question?id=q_" + "9" * 32)[1]["error"])

    def test_bank_media_only_serves_hash_named_images(self):
        response, _ = self.get(f"/bank-media/{IMAGE}")
        self.assertEqual((response.status, response.content_type), (200, "image/webp"))
        self.assertEqual(self.get("/bank-media/" + "b" * 64 + ".txt")[0].status, 404)
        self.assertEqual(self.get("/bank-media/../manifest.json")[0].status, 404)

    def test_unavailable_without_interview_bank(self):
        self.ibank.unlink()
        _, data = self.get("/api/bank")
        self.assertFalse(data["available"])
        self.assertIn("ibank", data["reason"])

    def post(self, path, payload):
        headers = {"host": "127.0.0.1:8765", "x-gleaner-token": self.app.token}
        response = self.app.handle("POST", path, headers, json.dumps(payload).encode("utf-8"))
        return response, json.loads(response.body)

    def test_export_with_current_filters_then_preview_and_download(self):
        (self.bank / "exports").mkdir()
        response, data = self.post("/api/bank/export", {"company": "示例科技", "topic": "ai.agent.memory"})
        self.assertEqual(response.status, 200, data)
        export = data["export"]
        self.assertEqual(export["answer_file"], "采集题库报告-示例科技-智能体记忆.md")
        self.assertEqual(export["question_file"], "采集题库报告-示例科技-智能体记忆（题目版）.md")
        self.assertEqual((export["questions"], export["answered"]), (2, 1))

        _, listing = self.get("/api/bank/exports")
        self.assertEqual(
            sorted((item["edition"], item["name"]) for item in listing["exports"]),
            [("答案版", export["answer_file"]), ("题目版", export["question_file"])],
        )
        from urllib.parse import quote

        _, report = self.get("/api/bank/export-file?name=" + quote(export["answer_file"]))
        self.assertIn("company=示例科技 domain=ai.agent.memory", report["markdown"])
        self.assertEqual((report["edition"], report["pair"]), ("答案版", export["question_file"]))

        download, _ = self.get("/bank-export/" + quote(export["answer_file"]))
        self.assertEqual(download.status, 200)
        self.assertIn("attachment", download.headers["Content-Disposition"])
        self.assertEqual(self.get("/bank-export/" + quote("../manifest.json"))[0].status, 404)
        self.assertIn("找不到报告", self.get("/api/bank/export-file?name=x.txt")[1]["error"])

    def test_export_needs_the_page_token(self):
        headers = {"host": "127.0.0.1:8765", "x-gleaner-token": "wrong"}
        response = self.app.handle("POST", "/api/bank/export", headers, b"{}")
        self.assertEqual(response.status, 403)

    def test_export_names_are_safe_file_names(self):
        from gleaner.bank import export_name

        self.assertEqual(export_name(company=None, title=""), "采集题库报告")
        self.assertEqual(
            export_name(company="A/B 公司", title="智能体:记忆"), "采集题库报告-A_B_公司-智能体_记忆"
        )

    def test_note_key(self):
        self.assertEqual(note_key("https://www.xiaohongshu.com/explore/6ae0671c"), "xhs:6ae0671c")
        self.assertIsNone(note_key("https://example.org/explore/1"))
        self.assertIsNone(note_key(None))


if __name__ == "__main__":
    unittest.main()
