import unittest
from dataclasses import replace

from gleaner.config import ScreeningConfig
from gleaner.models import Post
from gleaner.screening import Screener, is_recording


def post(title: str, body: str = "", *, images: int = 0) -> Post:
    return Post(
        platform="xhs",
        post_id="x",
        url="",
        open_url="",
        title=title,
        body=body,
        kind="image" if images else "text",
        published_at=None,
        images=[f"{index:03d}.jpg" for index in range(images)],
    )


class ScreenerTest(unittest.TestCase):
    def setUp(self):
        self.screener = Screener.from_config(ScreeningConfig())

    def label(self, *args, **kwargs) -> str:
        return self.screener.screen(post(*args, **kwargs)).label

    def test_interview_event_is_candidate(self):
        verdict = self.screener.screen(post("示例科技后端一面", "问了 Redis"))
        self.assertEqual(verdict.label, "candidate")
        self.assertIn("event:一面", verdict.reasons)

    def test_a_video_of_the_interview_itself_is_a_recording_candidate(self):
        video = replace(post("中小厂26届录音"), platform="bili", kind="video")
        verdict = self.screener.screen(video)
        self.assertEqual(verdict.label, "candidate")
        self.assertEqual(verdict.reasons, ("recording:录音",))
        self.assertTrue(is_recording(verdict.reasons))
        self.assertFalse(is_recording(self.screener.screen(post("中小厂26届录音", "x" * 50)).reasons))
        promo = replace(post("真实面试录音", "训练营学员上岸率"), kind="video")
        self.assertEqual(self.screener.screen(promo).label, "promo")
        custom = Screener.from_config(ScreeningConfig(recording_terms=("Interview Replay",)))
        self.assertTrue(
            is_recording(custom.screen(replace(post("后端 interview replay"), kind="video")).reasons)
        )

    def test_screenshot_post_without_event_words_is_candidate(self):
        self.assertEqual(self.label("今天的面经", images=3), "candidate")

    def test_one_weak_promo_word_does_not_override_a_real_account(self):
        self.assertEqual(self.label("Java 二面面经", "有问题可以私信"), "candidate")

    def test_strong_or_repeated_promo_is_promo(self):
        self.assertEqual(self.label("面经陪跑", "一面二面全程带"), "promo")
        self.assertEqual(self.label("面经资料包", "私信领取"), "promo")

    def test_asking_for_a_comment_to_send_material_is_promo(self):
        verdict = self.screener.screen(
            post("示例公司 Agent 岗：8 道追问", "想要配套面经，评论区扣「面试」，免费分享。")
        )
        self.assertEqual(verdict.label, "promo")
        self.assertIn("promo:评论区扣", verdict.reasons)
        self.assertEqual(self.label("示例科技二面", "面完在评论区交流一下"), "candidate")

    def test_free_material_card_is_promo(self):
        body = "技术面被问了 Redis。【点击打开下方小卡片无偿领取Java后端面试全套资料】"
        verdict = self.screener.screen(post("Java后端面试了8家公司", body))
        self.assertEqual(verdict.label, "promo")
        self.assertIn("promo:无偿领取", verdict.reasons)

    def test_pinyin_dodge_for_comment_bait_is_promo(self):
        body = "后来我问他上下文窗口满了怎么办。想系统梳理，可以在溜个“Agent”，我把学习清单fa你一份。"
        self.assertEqual(self.label("面了个候选人，聊到 Agent", body), "promo")

    def test_topic_tags_are_not_body_text(self):
        caption = "用户密码怎么存到数据库里？ #java #java面试 #后端开发 #面经 #互联网大厂"
        verdict = self.screener.screen(post(caption, caption))
        self.assertEqual(verdict.label, "unclear")
        self.assertIn("too-little-content", verdict.reasons)
        self.assertEqual(self.label("示例公司后端一面：登录凭证怎么设计？", "#Java #后端面试"), "candidate")

    def test_question_collection_without_an_interview_event(self):
        self.assertEqual(self.label("Redis 高频题合集", "八股必背", images=2), "collection")

    def test_interviewer_mentions_are_not_an_interview_event(self):
        self.assertEqual(
            self.label("AI 全栈工程师高频考点汇总", "面试官最爱问的八股", images=4), "collection"
        )
        self.assertEqual(self.label("AI Agent 全套 50 道面试题", "面试必看", images=4), "collection")

    def test_collection_with_an_interview_event_stays_candidate(self):
        self.assertEqual(self.label("三场一面汇总", "示例科技一面问了"), "candidate")

    def test_no_interview_terms_is_irrelevant(self):
        verdict = self.screener.screen(post("周末去爬山", "风景很好"))
        self.assertEqual(verdict.label, "irrelevant")

    def test_too_little_content_is_unclear(self):
        self.assertEqual(self.label("面经", "求"), "unclear")

    def test_an_ai_conducted_round_is_an_interview_event(self):
        verdict = self.screener.screen(
            post("示例科技ai面", "1. 泛型是什么？什么是类型擦除？\n2. JVM 垃圾回收的流程？")
        )
        self.assertEqual(verdict.label, "candidate")
        self.assertIn("event:ai面", verdict.reasons)
        self.assertEqual(self.label("示例科技 AI 面", "问了 Redis"), "candidate")
        self.assertEqual(self.label("AI面试题合集", "高频题", images=2), "collection")

    def test_seo_domain_padding_is_promo(self):
        spam = "\n".join(f"site{index}-demo.com.cn" for index in range(6))
        verdict = self.screener.screen(
            post("示例科技后端一面", f"问了 Redis 和 MySQL\n{spam}\n访问demo-x.top")
        )
        self.assertEqual(verdict.label, "promo")
        self.assertIn(
            "promo:7 个外部域名（site0-demo.com.cn、site1-demo.com.cn、site2-demo.com.cn 等）",
            verdict.reasons,
        )

    def test_a_few_links_do_not_make_a_real_account_promo(self):
        body = (
            "示例科技一面问了 Redis，题目在 https://leetcode.cn/problems/lru-cache 和 www.github.com/demo，"
            "项目文档放在 docs.demo-a.com，邮箱 me@demo-b.net，" + "复盘见 notes.demo-c.cn " * 6
        )
        self.assertEqual(self.label("后端一面面经", body), "candidate")

    def test_extra_terms_from_config_are_case_insensitive(self):
        screener = Screener.from_config(ScreeningConfig(strong_promo_terms=("OfferBox",)))
        self.assertEqual(screener.screen(post("面经 offerbox 推荐")).label, "promo")


if __name__ == "__main__":
    unittest.main()
