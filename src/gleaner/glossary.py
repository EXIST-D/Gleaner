"""Terms speech recognition gets wrong in Chinese tech interviews, for the Agent that corrects a transcript.

The list comes from the recordings imported so far (faster-whisper large-v3-turbo; the notes of each entry
say when a correction needs the context). It is rendered into HANDOFF.md of a pack with recordings, and
`[glossary] extra` in gleaner.toml adds more. Transcripts live in the bank and are not rewritten: the Agent
applies the table while it reads, and still writes what it changed into `correction`.
"""

from __future__ import annotations

from dataclasses import dataclass

from gleaner.errors import GleanerError

CONTEXT = "看上下文"


@dataclass(frozen=True)
class Entry:
    heard: str
    correct: str
    note: str = ""


def _e(heard: str, correct: str, note: str = "") -> Entry:
    return Entry(heard, correct, note)


BUILTIN: tuple[Entry, ...] = (
    _e("Lilis", "Redis"),
    _e("Redulog / Redlog", "redo log"),
    _e("Andulog / Android Log / Underlog / 阿努洛格", "undo log"),
    _e("readwell / ReadWheel / ZW", "ReadView", "MVCC 语境"),
    _e("回汶指针", "回滚指针"),
    _e("麦瑟克 / Masko / Mansicle", "MySQL"),
    _e("事物", "事务"),
    _e("一次性", "一致性", f"ACID 里的 C；{CONTEXT}"),
    _e("换度 / 患读 / 换货", "幻读"),
    _e("重机", "从机", "主从复制语境"),
    _e("Blog", "binlog", f"MySQL 语境；{CONTEXT}"),
    _e("KONLO / KANIA / Kanel", "Canal"),
    _e("ROCKET MPO / RockerMQ", "RocketMQ"),
    _e("REDITION", "Redisson"),
    _e("开门口", "看门狗", "Redisson 的锁续期"),
    _e("SETNS", "SETNX"),
    _e("分布师说 / 分布锁 / 分门锁", "分布式锁"),
    _e("属力度", "锁粒度"),
    _e("令牌桶设法", "令牌桶算法"),
    _e("散除 / 三熟", "删除", "Redis 过期删除语境"),
    _e("满存", "缓存"),
    _e("大 K", "大 Key"),
    _e("Folk", "fork", "RDB 持久化"),
    _e("写实复制", "写时复制"),
    _e("县城 / 县层 / 现成", "线程", f"{CONTEXT}；“现成值”是线程池"),
    _e("密等", "幂等"),
    _e("SickleLite", "synchronized"),
    _e("GMM", "JMM", "Java 内存模型"),
    _e("GOC", "JUC"),
    _e("GVM / 虚拟机GVM", "JVM"),
    _e("GDK", "JDK"),
    _e("加瓦", "Java"),
    _e("加上", "Java", f"“加上中的多态”这类；{CONTEXT}"),
    _e("SpinBoot / Smin Boot", "Spring Boot"),
    _e("SpinCloud / Smin Cloud", "Spring Cloud"),
    _e("smeave / Smin", "Spring"),
    _e("4GC / 伏计 / 负极锡", "Full GC"),
    _e("双轻委派", "双亲委派"),
    _e("扩载内加载器", "扩展类加载器"),
    _e("类加改进 / 雷加 / 累加的", "类加载器", CONTEXT),
    _e("原空间 / 语言空间", "元空间", "JDK 1.8 的方法区"),
    _e("副类 / 负类", "父类"),
    _e("质类", "子类"),
    _e("站", "栈", f"JVM 内存结构；{CONTEXT}"),
    _e("速度 / 宿主", "数组", f"和“链表”并列时；{CONTEXT}"),
    _e("练表", "链表"),
)


def parse_extra(values: list[str]) -> tuple[Entry, ...]:
    """`[glossary] extra`: each item is `heard = correct`."""
    found = []
    for value in values:
        heard, sep, correct = value.partition("=")
        if not sep or not heard.strip() or not correct.strip():
            raise GleanerError(f"glossary.extra 的每一项要写成“听成的写法 = 正确写法”：{value}")
        found.append(Entry(heard.strip(), correct.strip()))
    return tuple(found)


def entries(extra: tuple[Entry, ...] = ()) -> list[Entry]:
    return [*BUILTIN, *extra]


def render_table(items: list[Entry]) -> list[str]:
    lines = ["| 转写里常见的写法 | 应该是 | 说明 |", "|---|---|---|"]
    lines += [f"| {item.heard} | {item.correct} | {item.note} |" for item in items]
    return lines
