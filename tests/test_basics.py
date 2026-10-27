import unittest
from pathlib import Path

from gleaner.config import MAX_NOTES_CAP, parse_config, template_text
from gleaner.errors import GleanerError
from gleaner.timeutil import china_date, from_epoch
from gleaner.urls import canonical_url
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
        self.assertEqual(config.mediacrawler.max_notes, 15)
        self.assertEqual(config.mediacrawler.command, ("uv", "run", "main.py"))
        self.assertIsNone(config.handoff.bank)

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
