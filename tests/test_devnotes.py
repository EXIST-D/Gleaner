import shutil
import subprocess
import unittest
from datetime import timedelta

from gleaner.devnotes import FEEDBACK_FILE, NOTES_FILE, add_feedback, dev_status, feedback_entries
from gleaner.errors import GleanerError
from gleaner.timeutil import CHINA_TZ
from tests.support import NOW, WorkspaceTestCase

CHANGELOG = "# 更新记录\n\n## 未发布\n\n- 新的进度页\n\n## 0.1.0\n\n- 第一版\n"
HANDBOOK = "# 开发手册\n\n## 6. 调试\n\n略\n\n## 7. 当前状态\n\n提交到 GLN-2。\n"


class FeedbackTest(WorkspaceTestCase):
    def test_entries_round_trip_newest_first(self):
        add_feedback(self.workspace, "帖子页想按发布时间排序", "帖子", now=NOW)
        later = NOW + timedelta(minutes=5)
        entry = add_feedback(self.workspace, "## 标题样的行\n第二行", "其他", now=later)
        self.assertEqual(entry["text"], "\\## 标题样的行\n第二行")
        entries = feedback_entries(self.workspace)
        self.assertEqual([e["topic"] for e in entries], ["其他", "帖子"])
        self.assertEqual(entries[1]["text"], "帖子页想按发布时间排序")
        self.assertEqual(entries[0]["text"], "\\## 标题样的行\n第二行")
        self.assertEqual(entries[1]["time"], NOW.astimezone(CHINA_TZ).strftime("%Y-%m-%d %H:%M"))
        self.assertTrue((self.workspace / FEEDBACK_FILE).read_text(encoding="utf-8").startswith("# 意见\n"))

    def test_bad_feedback_is_refused(self):
        for text, topic, message in (
            ("  ", "帖子", "不能为空"),
            ("x" * 4001, "帖子", "太长"),
            ("好", "微博", "不认识"),
        ):
            with self.subTest(message=message), self.assertRaisesRegex(GleanerError, message):
                add_feedback(self.workspace, text, topic, now=NOW)
        self.assertFalse((self.workspace / FEEDBACK_FILE).exists())


class DevStatusTest(WorkspaceTestCase):
    def test_outside_a_repository(self):
        (self.workspace / NOTES_FILE).write_text("# 本机进度\n\n- 导入了第二批\n", encoding="utf-8")
        status = dev_status(self.workspace)
        self.assertIn("导入了第二批", status["notes"]["markdown"])
        self.assertEqual((status["repo"], status["commits"], status["unreleased"]), (None, [], ""))
        self.assertIsNone(status["unpushed"])

    @unittest.skipUnless(shutil.which("git"), "git is not installed")
    def test_inside_a_repository(self):
        root = self.workspace.parent / "repo"
        workspace = root / "workspace"
        (root / "docs").mkdir(parents=True)
        workspace.mkdir()
        (root / "CHANGELOG.md").write_text(CHANGELOG, encoding="utf-8")
        (root / "docs" / "development.md").write_text(HANDBOOK, encoding="utf-8")
        (root / ".gitignore").write_text("workspace/\n", encoding="utf-8")
        git = [
            "git",
            "-C",
            str(root),
            "-c",
            "user.name=t",
            "-c",
            "user.email=t@example.com",
            "-c",
            "core.hooksPath=none",
        ]
        subprocess.run([*git, "init", "-q"], check=True)
        subprocess.run([*git, "add", "-A"], check=True)
        subprocess.run([*git, "commit", "-q", "-m", "GLN-1: start"], check=True)
        (root / "new.txt").write_text("x", encoding="utf-8")

        status = dev_status(workspace)
        self.assertEqual(status["notes"]["markdown"], "提交到 GLN-2。")
        self.assertEqual(status["unreleased"], "- 新的进度页")
        self.assertEqual([c["subject"] for c in status["commits"]], ["GLN-1: start"])
        self.assertEqual(status["uncommitted"], 1)
        self.assertIsNone(status["unpushed"])


if __name__ == "__main__":
    unittest.main()
