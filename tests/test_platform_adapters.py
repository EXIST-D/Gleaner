import unittest

from gleaner.adapters.common import ParseReport
from gleaner.adapters.exports import content_files, copy_export, iter_posts
from gleaner.adapters.nowcoder import html_to_text, moment_text, post_images
from tests.support import (
    BILI_VIDEO,
    DOUYIN_IMAGES,
    DOUYIN_VIDEO,
    KS_VIDEO,
    NOWCODER_DISCUSS,
    NOWCODER_MOMENT,
    ZHIHU_ANSWER,
    ZHIHU_ARTICLE,
    WorkspaceTestCase,
    write_export,
    write_images,
    write_records,
)


class PlatformAdapterTest(WorkspaceTestCase):
    def setUp(self):
        super().setUp()
        self.raw = self.workspace / "runs" / "r1" / "raw"
        write_records(self.raw, "douyin", [DOUYIN_IMAGES, DOUYIN_VIDEO])
        write_images(self.raw, "dy", DOUYIN_IMAGES["aweme_id"], 2)
        write_images(self.raw, "dy", DOUYIN_VIDEO["aweme_id"], 1)
        write_records(self.raw, "bili", [BILI_VIDEO])
        write_records(self.raw, "kuaishou", [KS_VIDEO])
        write_records(
            self.raw, "zhihu", [ZHIHU_ANSWER, ZHIHU_ARTICLE, {"content_id": "1", "content_type": "pin"}]
        )
        write_records(self.raw, "nowcoder", [NOWCODER_DISCUSS, NOWCODER_MOMENT])
        write_images(self.raw, "nowcoder", NOWCODER_DISCUSS["post_id"], 1)

    def posts(self):
        report = ParseReport()
        return {post.key: post for post in iter_posts(self.raw, self.workspace, report)}, report

    def test_reads_every_platform_and_skips_unknown_zhihu_types(self):
        posts, report = self.posts()
        self.assertEqual(report.records, 9)
        self.assertEqual(report.skipped, 1)
        self.assertIn("zhihu/search_contents_2026-10-27.jsonl:3 没有帖子 ID", report.errors)
        self.assertEqual(
            {post.platform for post in posts.values()}, {"dy", "bili", "ks", "zhihu", "nowcoder"}
        )

    def test_douyin_image_set_and_video(self):
        posts, _ = self.posts()
        images = posts["dy:7300000000000000001"]
        self.assertEqual(images.kind, "image")
        self.assertEqual(images.title, "示例科技测开一面 #面经 #测开")
        self.assertEqual(images.tags, ["面经", "测开"])
        self.assertEqual(len(images.images), 2)
        self.assertTrue(images.images[0].startswith("runs/r1/raw/dy/media/7300000000000000001/"))
        self.assertEqual(images.remote_image_count, 2)
        self.assertEqual(images.metrics["liked_count"], 11000)
        self.assertEqual(images.published_at, "2026-10-21T00:00:00+00:00")
        video = posts["dy:7300000000000000002"]
        self.assertEqual(video.kind, "video")
        self.assertEqual(video.images, [], "a video's cover is not a screenshot")

    def test_bilibili_video(self):
        post = self.posts()[0]["bili:1150000001"]
        self.assertEqual(post.url, "https://www.bilibili.com/video/av1150000001")
        self.assertEqual((post.kind, post.body, post.title), ("video", "", "虚构大厂 Go 后端三面复盘"))
        self.assertEqual(
            post.metrics,
            {
                "liked_count": 88,
                "collected_count": 120,
                "comment_count": 9,
                "share_count": 3,
                "view_count": 15000,
            },
        )

    def test_kuaishou_video(self):
        post = self.posts()[0]["ks:3xexample0001"]
        self.assertEqual(post.url, "https://www.kuaishou.com/short-video/3xexample0001")
        self.assertEqual(post.tags, ["面经"])
        self.assertEqual(post.metrics, {"liked_count": 15, "view_count": 2000})
        self.assertEqual(post.published_at, "2026-10-21T00:00:00+00:00")

    def test_zhihu_answer_and_article(self):
        posts, _ = self.posts()
        answer = posts["zhihu:answer-300000001"]
        self.assertEqual(answer.url, "https://www.zhihu.com/question/200000001/answer/300000001")
        self.assertEqual(answer.open_url, answer.url)
        self.assertEqual(answer.kind, "text")
        self.assertEqual(answer.metrics, {"liked_count": 42, "comment_count": 3})
        article = posts["zhihu:article-600000001"]
        self.assertEqual(article.url, "https://zhuanlan.zhihu.com/p/600000001")

    def test_nowcoder_discussion_and_moment(self):
        posts, _ = self.posts()
        discuss = posts["nowcoder:800000001"]
        self.assertEqual(discuss.body, "一面 50 分钟\n1. Redis 为什么快\n2. MySQL & 索引")
        self.assertEqual((discuss.kind, discuss.remote_image_count, len(discuss.images)), ("image", 1, 1))
        self.assertEqual(discuss.keywords, ["后端 面经"])
        self.assertEqual(discuss.author_hash, "fedcba9876543210")
        self.assertEqual(
            discuss.metrics, {"liked_count": 7, "collected_count": 4, "comment_count": 2, "view_count": 300}
        )
        moment = posts["nowcoder:0123456789abcdef0123456789abcdef"]
        self.assertEqual(moment.body, "问了 List<String> 的泛型擦除\n还有线程池")
        self.assertEqual(
            moment.url, "https://www.nowcoder.com/feed/main/detail/0123456789abcdef0123456789abcdef"
        )
        self.assertEqual((moment.kind, moment.images), ("image", []))
        self.assertEqual(moment.published_at, "2026-10-22T00:00:00+00:00")

    def test_copy_export_maps_data_and_media_directories(self):
        target = self.workspace / "runs" / "r2" / "raw"
        found = copy_export(self.raw, target)
        self.assertEqual(found, ["dy", "bili", "ks", "zhihu", "nowcoder"])
        self.assertTrue((target / "dy" / "media" / DOUYIN_IMAGES["aweme_id"] / "002.jpg").is_file())
        self.assertEqual(copy_export(self.raw / "douyin", self.workspace / "r3"), ["dy"])
        self.assertTrue(
            (self.workspace / "r3" / "dy" / "media" / DOUYIN_IMAGES["aweme_id"] / "001.jpg").is_file()
        )
        self.assertEqual(len(content_files(target)), 5)

    def test_mixed_export_keeps_xhs(self):
        write_export(self.raw)
        posts, _ = self.posts()
        self.assertIn("xhs:aa01", posts)


class NowcoderTextTest(unittest.TestCase):
    def test_html_blocks_lists_and_code(self):
        html = (
            "<h3>二面</h3><ul><li>自我介绍</li><li>项目</li></ul>"
            "<pre>for i in range(3):\n    print(i)</pre><table><tr><td>轮次</td><td>时长</td></tr></table>"
            "<script>alert(1)</script>"
        )
        self.assertEqual(
            html_to_text(html), "二面\n- 自我介绍\n- 项目\nfor i in range(3):\n    print(i)\n轮次 | 时长"
        )

    def test_moment_text_is_not_parsed_as_html(self):
        self.assertEqual(
            moment_text("Map&lt;K, V&gt; 和 <List>\n\n\n\n下一段"), "Map<K, V> 和 <List>\n\n下一段"
        )

    def test_post_images_skip_emoji(self):
        self.assertEqual(post_images(NOWCODER_DISCUSS), ["https://uploadfiles.nowcoder.com/images/a.png"])
        self.assertEqual(post_images(NOWCODER_MOMENT), ["https://uploadfiles.nowcoder.com/images/m1.jpg"])


if __name__ == "__main__":
    unittest.main()
