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
        self.assertEqual(progress.qr_seconds, 600)
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


class PlatformProgressTest(unittest.TestCase):
    def feed(self, platform, lines, **kwargs):
        progress = Progress(platform, **kwargs)
        for line in lines:
            progress.feed(line)
        return progress

    def test_douyin_asks_for_a_scan_and_counts_down_the_real_wait(self):
        progress = self.feed("dy", ["[DouYinLogin.login_by_qrcode] Begin login douyin by qrcode..."])
        self.assertEqual((progress.step, progress.attention, progress.qr_seconds), ("login", "qr", 600))
        self.assertIn("抖音", progress.as_dict()["attention_title"])
        progress.feed("[DouYinLogin.check_page_display_slider] slider verify failed, retry ...")
        self.assertEqual(progress.attention, "verify")
        progress.feed("[DouYinCrawler.search] search douyin keyword: 测开, page: 0")
        progress.feed("[store.douyin.update_douyin_aweme] douyin aweme id:1, title:x")
        self.assertEqual((progress.step, progress.notes, progress.attention), ("search", 1, None))

    def test_bilibili_countdown_and_no_download_step(self):
        waiting = self.feed(
            "bili",
            [
                "[BilibiliLogin.login_by_qrcode] Begin login bilibili by qrcode ...",
                "[BilibiliLogin.login_by_qrcode] Waiting for scan code login, remaining time is 20s",
            ],
        )
        self.assertEqual((waiting.attention, waiting.qr_seconds), ("qr", 600))
        progress = self.feed(
            "bili",
            [
                "[BilibiliLogin.login_by_qrcode] Waiting for scan code login, remaining time is 20s",
                "[BilibiliLogin.login_by_qrcode] Login successful then wait for 5 seconds redirect ...",
                "[store.bilibili.update_bilibili_video] bilibili video id:1, title:x",
                "[download] 下载完成: cover.jpg",
            ],
        )
        self.assertEqual((progress.step, progress.notes), ("search", 1))
        self.assertNotIn("download", progress.steps)
        self.assertEqual(
            [step["name"] for step in progress.as_dict()["steps"]],
            ["browser", "login", "search", "import", "done"],
        )

    def test_zhihu_records(self):
        zhihu = self.feed(
            "zhihu",
            [
                "[ZhihuCrawler.search] Begin search zhihu keywords",
                "[store.zhihu.update_zhihu_content] zhihu content: {}",
            ],
        )
        self.assertEqual((zhihu.notes, zhihu.step), (1, "search"))

    def test_nowcoder_steps_and_throttling(self):
        progress = self.feed("nowcoder", ["[nowcoder] 翻阅最新面经列表第 1 页", "Login successful"])
        self.assertEqual((progress.step, progress.as_dict()["step_title"]), ("search", "翻阅面经列表"))
        progress.feed("[nowcoder] 牛客要求放慢请求（HTTP 429），等待 5 秒")
        self.assertEqual(progress.attention, "throttled")
        progress.feed("[nowcoder] 已保存帖子 1：示例")
        progress.feed("[nowcoder] 图片下载完成 1/001.png")
        self.assertEqual(
            (progress.step, progress.notes, progress.media, progress.attention), ("download", 1, 1, None)
        )
        without_images = Progress("nowcoder", download=False)
        self.assertEqual(without_images.steps, ("search", "fetch", "import", "done"))


if __name__ == "__main__":
    unittest.main()
