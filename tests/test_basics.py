import unittest
from pathlib import Path

from gleaner.config import MAX_NOTES_CAP, parse_config, template_text
from gleaner.errors import GleanerError
from gleaner.timeutil import china_date, from_epoch
from gleaner.urls import (
    bilibili_aid,
    bilibili_bvid,
    bilibili_detail_target,
    canonical_url,
    nowcoder_url,
    post_key,
    zhihu_url,
)
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
            "https://www.bilibili.com/video/BV1xx411c7mD/?spm_id_from=333": "bili:2",
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
        self.assertIsNone(post_key("https://www.bilibili.com/video/BV1short"))
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


class BilibiliIdTest(unittest.TestCase):
    # Bilibili's published examples of the av/BV mapping.
    KNOWN = {"2": "BV1xx411c7mD", "170001": "BV17x411w7KC"}

    def test_av_and_bv_convert_both_ways(self):
        for aid, bvid in self.KNOWN.items():
            self.assertEqual(bilibili_bvid(aid), bvid)
            self.assertEqual(bilibili_aid(bvid), aid)
        for aid in ("1", "1150000001", "999999999999999"):
            self.assertEqual(bilibili_aid(bilibili_bvid(aid)), aid)
        with self.assertRaises(ValueError):
            bilibili_aid("BV1short")

    def test_detail_targets_become_bv_links(self):
        expected = "https://www.bilibili.com/video/BV17x411w7KC"
        for target in ("https://www.bilibili.com/video/av170001/?p=1", "av170001", "170001", expected):
            self.assertEqual(bilibili_detail_target(target), expected, target)
        self.assertEqual(bilibili_detail_target("BV17x411w7KC"), "BV17x411w7KC")


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
        self.assertEqual((config.media.ffmpeg, config.media.download_timeout_seconds), ("ffmpeg", 3600))
        self.assertEqual(
            (config.handoff.transcribe_python, config.handoff.asr_model), ("python3", "large-v3-turbo")
        )
        self.assertEqual(config.screening.recording_terms, ())
        self.assertEqual((config.auto.platforms, config.auto.max_recordings), ((), 2))

    def test_media_commands_are_names_or_workspace_paths(self):
        workspace = Path("/tmp/ws")
        config = make_config(
            workspace,
            media={"ffmpeg": "bin/ffmpeg", "download_timeout_seconds": 10},
            handoff={"asr_python": "asr-venv/bin/python"},
        )
        self.assertEqual(config.media.ffmpeg, str(workspace / "bin" / "ffmpeg"))
        self.assertEqual(config.media.download_timeout_seconds, 300)
        self.assertEqual(config.handoff.transcribe_python, str(workspace / "asr-venv" / "bin" / "python"))
        self.assertEqual(
            make_config(workspace, handoff={"asr_python": "python3.11"}).handoff.asr_python, "python3.11"
        )

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

    def test_auto_platforms_and_recordings(self):
        workspace = Path("/tmp/ws")
        config = make_config(workspace, auto={"platforms": ["bili", "xhs", "bili"], "max_recordings": 0})
        self.assertEqual((config.auto.platforms, config.auto.max_recordings), (("bili", "xhs"), 0))
        for values in (
            {"max_recordings": 11},
            {"max_recordings": -1},
            {"max_recordings": "2"},
            {"platforms": "xhs"},
        ):
            with self.subTest(values), self.assertRaises(GleanerError):
                make_config(workspace, auto=values)

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
