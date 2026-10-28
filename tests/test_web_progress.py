import unittest

from gleaner.web.progress import Progress

REAL_RUN = [
    "2026-10-27 17:10:29 MediaCrawler INFO (core.py:83) - [XiaoHongShuCrawler] Launching browser using CDP mode",
    "2026-10-27 17:10:39 MediaCrawler INFO (login.py:89) - [XiaoHongShuLogin.begin] Begin login xiaohongshu ...",
    "2026-10-27 17:10:39 MediaCrawler INFO (login.py:202) - [XiaoHongShuLogin.login_by_qrcode] waiting for scan code login, remaining time is 120s",
]
AFTER_LOGIN = [
    "2026-10-27 17:11:40 MediaCrawler INFO (login.py:82) - [XiaoHongShuLogin.check_login_state] Login status confirmed by Cookie (web_session changed).",
    "2026-10-27 17:11:45 MediaCrawler INFO (core.py:156) - [XiaoHongShuCrawler.search] search Xiaohongshu keyword: AI Agent 面经, page: 1",
    "2026-10-27 17:12:01 MediaCrawler INFO (__init__.py:91) - [store.xhs.update_xhs_note] xhs note: {'note_id': 'aa01'}",
    "2026-10-27 17:12:02 MediaCrawler.media_downloader INFO (downloader.py:315) - [download] 下载完成: /w/raw/xhs/media/aa01/001.webp (1 bytes)",
    "2026-10-27 17:12:03 MediaCrawler.media_downloader INFO (downloader.py:315) - [download] 下载完成: /w/raw/xhs/media/aa01/002.webp (1 bytes)",
]


class ProgressTest(unittest.TestCase):
    def feed(self, lines):
        progress = Progress()
        for line in lines:
            progress.feed(line)
        return progress

    def test_waiting_for_the_qr_code_asks_for_a_scan(self):
        progress = self.feed(REAL_RUN)
        self.assertEqual(progress.step, "login")
        self.assertEqual(progress.attention, "qr")
        self.assertEqual(progress.qr_seconds, 120)
        self.assertIn("二维码", progress.as_dict()["attention_title"])

    def test_login_clears_the_call_to_action_and_counts_work(self):
        progress = self.feed(REAL_RUN + AFTER_LOGIN)
        self.assertIsNone(progress.attention)
        self.assertEqual(progress.step, "download")
        self.assertEqual((progress.notes, progress.media), (1, 2))

    def test_verification_and_confirmation_prompts(self):
        self.assertEqual(self.feed(REAL_RUN + ["页面提示：请通过验证"]).attention, "verify")
        confirm = "[CDPBrowserManager] Please check your browser for a confirmation dialog and accept it"
        self.assertEqual(self.feed([confirm]).attention, "confirm")

    def test_steps_never_go_backwards(self):
        progress = self.feed(AFTER_LOGIN + ["Begin login xiaohongshu ..."])
        self.assertEqual(progress.step, "download")

    def test_import_and_done(self):
        progress = self.feed(REAL_RUN)
        progress.finish_crawler()
        self.assertEqual((progress.step, progress.attention), ("import", None))
        progress.done()
        self.assertEqual(progress.as_dict()["step_title"], "完成")


if __name__ == "__main__":
    unittest.main()
