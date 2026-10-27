"""Recognise interview questions that a post already lists in its body text."""

from __future__ import annotations

import re

MIN_LISTED_QUESTIONS = 3
"""Below this, a post's own text is too thin to stand in for its images."""
MIN_QUESTION_CHARS = 6

_NUMBER = re.compile(
    r"^(?:\d{1,3}\s*[.．、)）:：](?!\d)|[（(]\d{1,3}[)）]|[①-⑳]|[Qq]\d{1,3}\b[.．、:：]?)\s*"
)
_CUE = re.compile(
    r"[？?]|怎么|怎样|如何|为什么|为何|什么|哪些|哪个|哪里|是否|吗|介绍|讲讲|讲一下|说说|聊聊|谈谈|解释|手写|手撕|算法题|区别|原理"
)
_DECORATION = re.compile(r"^[^\w（(]+")


def listed_questions(body: str) -> list[str]:
    """Numbered or question-marked lines that read like questions; summaries and headings do not count."""
    found = []
    for raw in body.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or line.startswith("标签："):
            continue
        numbered = _NUMBER.match(line)
        if not numbered and not line.endswith(("？", "?")):
            continue
        content = _DECORATION.sub("", line[numbered.end() :] if numbered else line)
        if len(content) >= MIN_QUESTION_CHARS and _CUE.search(content):
            found.append(line)
    return found


def body_replaces_images(body: str, image_count: int) -> bool:
    """Read the text instead of the images only when it lists at least as many questions as there are images.

    A post with many images and few listed lines most likely keeps further questions in its images, so it
    stays with the images: a wrong guess then costs extra viewing, never missing questions.
    """
    listed = len(listed_questions(body))
    return listed >= MIN_LISTED_QUESTIONS and listed >= image_count
