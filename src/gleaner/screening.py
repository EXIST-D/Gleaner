"""Explainable rule-based screening. Labels only sort posts for review; nothing is ever deleted."""

from __future__ import annotations

from dataclasses import dataclass

from gleaner.config import ScreeningConfig
from gleaner.models import Post

INTERVIEW_TERMS = (
    "面经",
    "面试",
    "凉经",
    "笔试",
    "八股",
    "手撕",
    "拷打",
    "被问",
    "问了",
    "面试官",
    "意向",
)
EVENT_TERMS = (
    "一面",
    "二面",
    "三面",
    "四面",
    "终面",
    "hr面",
    "主管面",
    "交叉面",
    "加面",
    "技术面",
    "笔试",
    "面试官",
)
PROMO_TERMS = (
    "私信",
    "私我",
    "滴滴",
    "戳我",
    "加v",
    "加微",
    "vx",
    "v信",
    "威信",
    "辅导",
    "陪跑",
    "训练营",
    "课程",
    "学员",
    "报名",
    "改简历",
    "简历修改",
    "简历优化",
    "内推码",
    "1v1",
    "一对一",
    "上岸率",
    "包过",
    "咨询",
    "资料包",
    "领取",
    "扣1",
)
STRONG_PROMO_TERMS = ("陪跑", "训练营", "学员", "包过", "上岸率", "加v", "vx", "1v1", "一对一", "扣1")
COLLECTION_TERMS = ("合集", "汇总", "大全", "题库", "必背", "整理了", "高频题", "100道", "面试题分享")

MIN_BODY_CHARS = 40


@dataclass(frozen=True)
class Verdict:
    label: str
    reasons: tuple[str, ...]


@dataclass(frozen=True)
class Screener:
    interview: tuple[str, ...]
    event: tuple[str, ...]
    promo: tuple[str, ...]
    strong_promo: tuple[str, ...]
    collection: tuple[str, ...]

    @classmethod
    def from_config(cls, config: ScreeningConfig) -> Screener:
        return cls(
            interview=_merge(INTERVIEW_TERMS, config.interview_terms),
            event=_merge(EVENT_TERMS, config.event_terms),
            promo=_merge(PROMO_TERMS, config.promo_terms),
            strong_promo=_merge(STRONG_PROMO_TERMS, config.strong_promo_terms),
            collection=_merge(COLLECTION_TERMS, config.collection_terms),
        )

    def screen(self, post: Post) -> Verdict:
        text = "\n".join([post.title, post.body, *post.tags]).lower()
        interview = _hits(text, self.interview)
        event = _hits(text, self.event)
        promo = _hits(text, self.promo)
        strong = _hits(text, self.strong_promo)
        collection = _hits(text, self.collection)
        evidence = [f"interview:{t}" for t in interview] + [f"event:{t}" for t in event]

        if strong or len(promo) >= 2:
            return Verdict("promo", tuple(f"promo:{t}" for t in dict.fromkeys([*strong, *promo])))
        if not interview and not event:
            return Verdict("irrelevant", ("no-interview-terms",))
        if collection and not event:
            return Verdict("collection", tuple([*(f"collection:{t}" for t in collection), *evidence]))
        has_material = bool(post.images) or post.remote_image_count > 0 or len(post.body) >= MIN_BODY_CHARS
        if event or has_material:
            return Verdict("candidate", tuple(evidence + [f"promo:{t}" for t in promo]))
        return Verdict("unclear", tuple([*evidence, "too-little-content"]))


def _merge(defaults: tuple[str, ...], extra: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(term.lower() for term in (*defaults, *extra) if term.strip()))


def _hits(text: str, terms: tuple[str, ...]) -> list[str]:
    return [term for term in terms if term in text]
