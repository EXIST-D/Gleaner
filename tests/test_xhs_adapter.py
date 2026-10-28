import unittest

from gleaner.adapters.common import ParseReport, parse_count
from gleaner.adapters.exports import content_files, copy_export, iter_posts
from tests.support import INTERVIEW_NOTE, WorkspaceTestCase, write_export


class ParseCountTest(unittest.TestCase):
    def test_platform_count_formats(self):
        self.assertEqual(parse_count(12), 12)
        self.assertEqual(parse_count("1,234"), 1234)
        self.assertEqual(parse_count("10+"), 10)
        self.assertEqual(parse_count("1.2万"), 12000)
        self.assertIsNone(parse_count(""))
        self.assertIsNone(parse_count(True))
        self.assertIsNone(parse_count("很多"))


class AdapterTest(WorkspaceTestCase):
    def posts(self):
        raw = write_export(self.workspace / "runs" / "r1" / "raw")
        report = ParseReport()
        return {post.post_id: post for post in iter_posts(raw, self.workspace, report)}, report

    def test_reads_every_record(self):
        posts, report = self.posts()
        self.assertEqual(sorted(posts), ["aa01", "bb02", "cc03", "dd04"])
        self.assertEqual(report.records, 4)
        self.assertEqual(report.skipped, 0)

    def test_image_note(self):
        post = self.posts()[0]["aa01"]
        self.assertEqual(post.key, "xhs:aa01")
        self.assertEqual(post.kind, "image")
        self.assertEqual(post.url, "https://www.xiaohongshu.com/explore/aa01")
        self.assertIn("xsec_token", post.open_url)
        self.assertEqual(post.published_at, "2026-10-21T00:00:00+00:00")
        self.assertEqual(
            post.images, ["runs/r1/raw/xhs/media/aa01/001.jpg", "runs/r1/raw/xhs/media/aa01/002.jpg"]
        )
        self.assertEqual(post.remote_image_count, 2)
        self.assertEqual(post.tags, ["面经", "后端"])
        self.assertEqual(post.keywords, ["后端 面经"])
        self.assertEqual(
            post.metrics,
            {"liked_count": 12000, "collected_count": 356, "comment_count": 10, "share_count": 3},
        )
        self.assertNotIn("[话题]", post.body)
        self.assertIn("#面经", post.body)

    def test_text_and_video_notes(self):
        posts = self.posts()[0]
        self.assertEqual(posts["cc03"].kind, "text")
        self.assertEqual(posts["cc03"].published_at, "2026-10-23T00:00:00+00:00")
        video = posts["dd04"]
        self.assertEqual(video.kind, "video")
        self.assertEqual(video.images, [], "a video cover is not an interview screenshot")

    def test_skips_broken_lines_and_records_without_id(self):
        raw = write_export(self.workspace / "raw", [INTERVIEW_NOTE, {"title": "没有 ID"}], images={})
        path = content_files(raw)[0]
        path.write_text(path.read_text(encoding="utf-8") + "{broken\n", encoding="utf-8")
        report = ParseReport()
        posts = list(iter_posts(raw, self.workspace, report))
        self.assertEqual([post.post_id for post in posts], ["aa01"])
        self.assertEqual(report.skipped, 2)
        self.assertEqual(len(report.errors), 2)

    def test_copy_export_keeps_records_and_images_only(self):
        source = write_export(self.workspace / "external" / "data")
        target = self.workspace / "runs" / "r2" / "raw"
        self.assertEqual(copy_export(source, target), ["xhs"])
        self.assertEqual(len(content_files(target)), 1)
        self.assertTrue((target / "xhs" / "media" / "aa01" / "002.jpg").is_file())
        self.assertFalse((target / "xhs" / "media" / "dd04" / "video.mp4").exists())
        copy_export(source / "xhs", self.workspace / "runs" / "r3" / "raw")
        with self.assertRaises(FileNotFoundError):
            copy_export(self.workspace, self.workspace / "runs" / "r4" / "raw")


if __name__ == "__main__":
    unittest.main()
