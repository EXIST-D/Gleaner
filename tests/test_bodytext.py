import unittest

from gleaner.bodytext import MIN_LISTED_QUESTIONS, body_replaces_images, choose_intake, listed_questions

NUMBERED_LIST = """\
示例科技后端一面

面试官重点拷打了项目，具体问题如下：

1. 自我介绍
2. 讲讲你项目里 Agent 的启动运行流程
3. skill 的基本原理是什么？
12. 算法题：合并 N 个有序数组

#面经 #秋招
"""

SECTIONED_LIST = """\
一、项目与技术

2. 实习项目与Agent框架
（1）介绍一下实习项目，以及你负责的工作。
（2）Agent采用什么架构？
① 了解MySQL的MVCC吗
Q4: 手写最接近的三数之和
"""

PREPARATION_SUMMARY = """\
我整体面试完的总结
1. 岗位定位划重点：示例公司 Agent 后端不等于算法岗，本质是 SaaS 后端工程师。
2. 必补业务背景：四大场景，底层依赖云厂商模型服务。
3. 备考优先级（按重要程度排序）
4. 避雷提醒：不要拿 C 端聊天机器人的思路去面！
5. 投递适配：3-5 年后端、做过平台或 SaaS 的人最适配。
"""

EXPLAINER_HEADINGS = "📌 做了什么？\n说明谁遇到什么问题。\n📌 为什么这样做？\n📌 怎么知道做对了？"


class ListedQuestionsTest(unittest.TestCase):
    def test_numbered_lines_that_read_like_questions(self):
        found = listed_questions(NUMBERED_LIST)
        self.assertEqual(len(found), 3, "“1. 自我介绍” is too short to count")
        self.assertIn("12. 算法题：合并 N 个有序数组", found)

    def test_section_titles_do_not_count(self):
        found = listed_questions(SECTIONED_LIST)
        self.assertEqual(len(found), 4)
        self.assertNotIn("2. 实习项目与Agent框架", found)

    def test_question_marks_without_numbers(self):
        body = "一面\n怎么设计缓存？\nMVCC 解决什么问题?\n为什么用 Redis？"
        self.assertEqual(len(listed_questions(body)), 3)

    def test_numbered_preparation_notes_are_not_questions(self):
        self.assertEqual(listed_questions(PREPARATION_SUMMARY), [])

    def test_short_explainer_headings_are_not_questions(self):
        self.assertLess(len(listed_questions(EXPLAINER_HEADINGS)), MIN_LISTED_QUESTIONS)

    def test_dates_and_hashtags_are_not_questions(self):
        body = "10.26 示例科技 Agent Harness 二面\n2026-10-10 一面\n#面经 #Agent\n标签：#面经"
        self.assertEqual(listed_questions(body), [])


class BodyReplacesImagesTest(unittest.TestCase):
    def test_listed_questions_replace_cover_and_copies(self):
        self.assertTrue(body_replaces_images(NUMBERED_LIST, 1))
        self.assertTrue(body_replaces_images(NUMBERED_LIST, 3))

    def test_more_images_than_listed_questions_keeps_the_images(self):
        self.assertFalse(body_replaces_images(NUMBERED_LIST, 4))

    def test_a_caption_never_replaces_images(self):
        self.assertFalse(body_replaces_images("阿里巴巴大模型Agent三面\n\n#面经", 1))
        self.assertFalse(body_replaces_images(PREPARATION_SUMMARY, 11))


class ChooseIntakeTest(unittest.TestCase):
    def test_a_picture_generated_from_the_text_is_not_read(self):
        body = "示例科技笔试\n编程题是最长递增子序列\n#秋招笔试记录# #牛客AI配图神器#"
        self.assertEqual(choose_intake(body, 1), "text")
        self.assertEqual(choose_intake(body.replace(" #牛客AI配图神器#", ""), 1), "images")

    def test_screenshots_next_to_a_generated_picture_are_still_read(self):
        self.assertEqual(choose_intake("示例科技一面\n题目见图\n#牛客AI配图神器#", 3), "images")
        self.assertEqual(choose_intake(NUMBERED_LIST + "#牛客AI配图神器#", 4), "text")


if __name__ == "__main__":
    unittest.main()
