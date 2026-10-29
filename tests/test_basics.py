import unittest
from pathlib import Path

from gleaner.config import MAX_NOTES_CAP, parse_config, template_text
from gleaner.errors import GleanerError
from gleaner.timeutil import china_date, from_epoch
from gleaner.urls import canonical_url, nowcoder_url, post_key, zhihu_url
from tests.support import make_config


class CanonicalUrlTest(unittest.TestCase):
    def test_drops_session_and_share_parameters(self):
        url = (
            "https://WWW.xiaohongshu.com/explore/aa01/?xsec_token=SECRET&xsec_source=pc_search&utm_medium=x#c"
        )
        self.assertEqual(canonical_url(url), "https://www.xiaohongshu.com/explore/aa01")

    def test_keeps_content_parameters_in_stable_order(self):
        self.assertEqual(
            canonical_url("https://www.nowcoder.com/discuss/1?b=2&a=1&spm=x"),
            "https://www.nowcoder.com/discuss/1?a=1&b=2",
        )

    def test_leaves_non_http_values_alone(self):
        self.assertEqual(canonical_url("aa01"), "aa01")

    def test_drops_platform_share_parameters(self):
        self.assertEqual(
            canonical_url("https://www.bilibili.com/video/av1/?spm_id_from=333&vd_source=abc&p=2"),
            "https://www.bilibili.com/video/av1?p=2",
        )


class PostKeyTest(unittest.TestCase):
    def test_every_platform_url_maps_to_its_post_key(self):
        cases = {
            "https://www.xiaohongshu.com/explore/6ae0671c?xsec_token=x": "xhs:6ae0671c",
            "https://www.xiaohongshu.com/discovery/item/6ae0671c": "xhs:6ae0671c",
            "https://www.douyin.com/video/7300000000000000001": "dy:7300000000000000001",
            "https://www.douyin.com/note/7300000000000000002": "dy:7300000000000000002",
            "https://www.bilibili.com/video/av1150000001/": "bili:1150000001",
            "https://www.zhihu.com/question/2/answer/3": "zhihu:answer-3",
            "https://www.zhihu.com/answer/3": "zhihu:answer-3",
            "https://zhuanlan.zhihu.com/p/6": "zhihu:article-6",
            "https://www.zhihu.com/zvideo/9": "zhihu:zvideo-9",
            "https://www.nowcoder.com/discuss/800000001?sourceSSR=search": "nowcoder:800000001",
            "https://m.nowcoder.com/feed/main/detail/0123456789ABCDEF0123456789ABCDEF": (
                "nowcoder:0123456789abcdef0123456789abcdef"
            ),
        }
        for url, key in cases.items():
            self.assertEqual(post_key(url), key, url)

    def test_other_pages_have_no_key(self):
        self.assertIsNone(post_key("https://example.org/explore/1"))
        self.assertIsNone(post_key("https://www.bilibili.com/video/BV1xx411c7mD"))
        self.assertIsNone(post_key("https://www.kuaishou.com/short-video/3xexample0001"))
        self.assertIsNone(post_key("aa01"))
        self.assertIsNone(post_key(None))

    def test_builders_match_the_keys(self):
        self.assertEqual(post_key(zhihu_url("answer", "3")), "zhihu:answer-3")
        self.assertEqual(post_key(zhihu_url("article", "6")), "zhihu:article-6")
        self.assertEqual(
            post_key(nowcoder_url("0123456789abcdef0123456789abcdef")),
            "nowcoder:0123456789abcdef0123456789abcdef",
        )


class TimeTest(unittest.TestCase):
    def test_epoch_in_milliseconds_seconds_and_strings(self):
        self.assertEqual(from_epoch(1792540800000), "2026-10-21T00:00:00+00:00")
        self.assertEqual(from_epoch("1792540800"), "2026-10-21T00:00:00+00:00")
        self.assertIsNone(from_epoch(""))
        self.assertIsNone(from_epoch(0))
        self.assertIsNone(from_epoch("soon"))

    def test_china_date_crosses_midnight(self):
        self.assertEqual(china_date("2026-10-20T17:00:00+00:00"), "2026-10-21")
        self.assertIsNone(china_date(None))


class ConfigTest(unittest.TestCase):
    def test_bundled_template_parses(self):
        import tomllib

        config = parse_config(tomllib.loads(template_text()), Path("/tmp/ws"))
        self.assertEqual(config.mediacrawler.max_notes, 20)
        self.assertEqual(config.mediacrawler.command, ("uv", "run", "main.py"))
        self.assertEqual(config.mediacrawler.home, Path("/tmp/MediaCrawler").resolve())
        self.assertEqual(config.handoff.bank, Path("/tmp/collected-bank").resolve())
        self.assertIsNone(config.handoff.ibank)
        self.assertEqual((config.nowcoder.max_notes, config.nowcoder.scan_pages), (20, 5))
        self.assertEqual(config.nowcoder.request_interval_seconds, 3.0)
        self.assertTrue(config.nowcoder.download_images)

    def test_nowcoder_limits(self):
        for values in (
            {"max_notes": 0},
            {"scan_pages": 26},
            {"request_interval_seconds": 0.5},
            {"request_interval_seconds": True},
            {"download_images": "yes"},
        ):
            with self.subTest(values), self.assertRaises(GleanerError):
                make_config(Path("/tmp/ws"), nowcoder=values)
        config = make_config(Path("/tmp/ws"), nowcoder={"timeout_seconds": 5, "request_interval_seconds": 2})
        self.assertEqual(
            (config.nowcoder.timeout_seconds, config.nowcoder.request_interval_seconds), (60, 2.0)
        )

    def test_relative_paths_resolve_against_workspace(self):
        config = parse_config({"handoff": {"bank": "../bank"}}, Path("/tmp/ws"))
        self.assertEqual(config.handoff.bank, Path("/tmp/bank").resolve())

    def test_rejects_large_batches(self):
        with self.assertRaises(GleanerError):
            make_config(Path("/tmp/ws"), mediacrawler={"max_notes": MAX_NOTES_CAP + 1})

    def test_rejects_keywords_with_commas(self):
        with self.assertRaises(GleanerError):
            make_config(Path("/tmp/ws"), keywords=["后端,面经"])

    def test_rejects_unknown_login_type(self):
        with self.assertRaises(GleanerError):
            make_config(Path("/tmp/ws"), mediacrawler={"login_type": "password"})


if __name__ == "__main__":
    unittest.main()
