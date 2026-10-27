from __future__ import annotations

import argparse
import json
import shutil
import sys
from collections.abc import Sequence
from pathlib import Path

from gleaner import __version__
from gleaner.config import (
    CONFIG_NAME,
    MAX_NOTES_CAP,
    load_config,
    resolve_workspace,
    template_text,
    validate_keywords,
)
from gleaner.errors import GleanerError
from gleaner.models import LABEL_TITLES, LABELS, PostRecord
from gleaner.packer import DEFAULT_LIMIT, build_pack
from gleaner.pipeline import crawl_xhs, import_export, planned_notes, screen
from gleaner.sources.mediacrawler import SEARCH_PAGE_SIZE, MediaCrawlerDriver
from gleaner.store import Store
from gleaner.timeutil import china_date, iso, utc_now

EXIT_DEGRADED = 3


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    args.workspace = getattr(args, "workspace", None)
    args.json = getattr(args, "json", False)
    try:
        return args.handler(args)
    except GleanerError as exc:
        if getattr(args, "json", False):
            _print_json({"ok": False, "error": str(exc)})
        else:
            print(f"错误：{exc}", file=sys.stderr)
        return 1


def build_parser() -> argparse.ArgumentParser:
    # Parent actions are shared objects; keep their defaults SUPPRESS so a subcommand never overwrites a
    # value given before it. main() fills in the defaults afterwards.
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument(
        "--workspace", default=argparse.SUPPRESS, help="工作区目录；也可用环境变量 GLEANER_HOME"
    )
    common.add_argument("--json", action="store_true", default=argparse.SUPPRESS, help="输出 JSON")

    parser = argparse.ArgumentParser(
        prog="gleaner", description="拾穗：采集面经、规则初筛、打包交给 Interview Bank", parents=[common]
    )
    parser.add_argument("--version", action="version", version=f"gleaner {__version__}")
    sub = parser.add_subparsers(dest="command", required=True, metavar="<command>")

    def command(name: str, handler, help_text: str) -> argparse.ArgumentParser:
        child = sub.add_parser(name, help=help_text, description=help_text, parents=[common])
        child.set_defaults(handler=handler)
        return child

    command("init", cmd_init, "创建工作区：配置文件、数据库和目录")
    command("doctor", cmd_doctor, "检查配置、MediaCrawler 和 Interview Bank 路径")

    crawl = command("crawl", cmd_crawl, "调用 MediaCrawler 抓取并导入")
    crawl.add_argument("platform", choices=["xhs"], help="平台（目前支持 xhs 小红书）")
    crawl.add_argument("--keyword", action="append", default=[], help="搜索关键词，可重复；默认用配置里的")
    crawl.add_argument("--url", action="append", default=[], help="只抓这些帖子（链接或 ID），可重复")
    crawl.add_argument(
        "--max-notes",
        type=int,
        help=f"搜索时每个关键词抓多少篇（1–{MAX_NOTES_CAP}，按每页 20 篇取整，最少一页）",
    )
    crawl.add_argument("--force", action="store_true", help="忽略两次抓取之间的最小间隔")
    crawl.add_argument("--quiet", action="store_true", help="不在终端显示 MediaCrawler 的输出")

    imported = command("import", cmd_import, "导入已有的 MediaCrawler 输出目录（含 xhs/ 子目录）")
    imported.add_argument("source", type=Path)

    screening = command("screen", cmd_screen, "对未打标签的帖子做规则初筛")
    screening.add_argument("--all", action="store_true", help="同时重新判断以前的规则标签（人工标签不动）")

    listing = command("list", cmd_list, "列出帖子")
    listing.add_argument("--label", action="append", choices=LABELS, help="按标签筛选，可重复")
    listing.add_argument("--unpacked", action="store_true", help="只看还没打包的")
    listing.add_argument("--limit", type=int, default=30)

    show = command("show", cmd_show, "查看一篇帖子")
    show.add_argument("key", help="帖子键，例如 xhs:6650a1b2c3d4")

    label = command("label", cmd_label, "人工修改标签（以后的初筛不会覆盖）")
    label.add_argument("key")
    label.add_argument("label", choices=LABELS)
    label.add_argument("--reason", default="", help="备注原因")

    pack = command("pack", cmd_pack, "把帖子打成交给 Interview Bank 的素材包")
    pack.add_argument("--label", action="append", choices=LABELS, help="要打包的标签，默认 candidate")
    pack.add_argument("--key", action="append", help="只打包这些帖子，可重复")
    pack.add_argument(
        "--limit", type=int, default=DEFAULT_LIMIT, help=f"最多打包多少篇，默认 {DEFAULT_LIMIT}"
    )
    pack.add_argument("--bank", type=Path, help="采集题库目录；默认用配置里的 handoff.bank")

    command("stats", cmd_stats, "统计帖子、标签和抓取记录")
    runs = command("runs", cmd_runs, "最近的抓取记录")
    runs.add_argument("--limit", type=int, default=10)
    return parser


# Commands


def cmd_init(args) -> int:
    workspace = resolve_workspace(args.workspace)
    workspace.mkdir(parents=True, exist_ok=True)
    config_path = workspace / CONFIG_NAME
    created = not config_path.exists()
    if created:
        config_path.write_text(template_text(), encoding="utf-8")
    for name in ("runs", "packs"):
        (workspace / name).mkdir(exist_ok=True)
    with Store.open(workspace, create=True):
        pass
    data = {"ok": True, "workspace": str(workspace), "config": str(config_path), "config_created": created}
    text = [f"工作区：{workspace}"]
    text.append(
        f"已写入配置 {config_path}，请先填写 mediacrawler.home 和 handoff。"
        if created
        else "配置已存在，未改动。"
    )
    return _emit(args, data, text)


def cmd_doctor(args) -> int:
    config = load_config(resolve_workspace(args.workspace))
    checks = []
    problems = MediaCrawlerDriver(config.mediacrawler).problems()
    checks.append(("mediacrawler", not problems, "；".join(problems) or str(config.mediacrawler.home)))
    checks.append(("keywords", bool(config.keywords), "、".join(config.keywords) or "没有默认关键词"))
    ibank = config.handoff.ibank
    checks.append(
        ("ibank", bool(ibank and ibank.is_file()), str(ibank) if ibank else "没有配置 handoff.ibank")
    )
    bank = config.handoff.bank
    bank_ok = bool(bank and (bank / "manifest.json").is_file())
    bank_text = str(bank) if bank else "没有配置 handoff.bank"
    if bank and not bank_ok:
        bank_text += "（还不是题库，先运行 ibank.py init --bank）"
    checks.append(("bank", bank_ok, bank_text))
    python_ok = shutil.which(config.handoff.python) is not None
    checks.append(("python", python_ok, config.handoff.python))

    data = {
        "ok": all(ok for _, ok, _ in checks),
        "checks": [{"name": n, "ok": ok, "detail": d} for n, ok, d in checks],
    }
    text = [f"{'✓' if ok else '✗'} {name}: {detail}" for name, ok, detail in checks]
    _emit(args, data, text)
    return 0 if data["ok"] else 1


def cmd_crawl(args) -> int:
    config = load_config(resolve_workspace(args.workspace))
    if args.max_notes is not None and not 1 <= args.max_notes <= MAX_NOTES_CAP:
        raise GleanerError(f"--max-notes 必须在 1 到 {MAX_NOTES_CAP} 之间")
    keywords = validate_keywords(args.keyword) or list(config.keywords)
    targets = [value.strip() for value in args.url if value.strip()]
    driver = MediaCrawlerDriver(config.mediacrawler, echo=not (args.quiet or args.json))
    if not args.json and not targets:
        planned = planned_notes(config, keywords=keywords, targets=targets, max_notes=args.max_notes)
        print(
            f"小红书搜索按页抓取（每页 {SEARCH_PAGE_SIZE} 篇）：{len(keywords)} 个关键词，本次最多约 {planned} 篇",
            file=sys.stderr,
        )
    with Store.open(config.workspace) as store:
        outcome = crawl_xhs(
            store,
            config,
            driver,
            keywords=keywords,
            targets=targets,
            max_notes=args.max_notes,
            force=args.force,
            now=utc_now(),
        )
    _emit(args, outcome.as_dict(), _summary_text(outcome))
    return 0 if outcome.status == "ok" else EXIT_DEGRADED


def cmd_import(args) -> int:
    config = load_config(resolve_workspace(args.workspace))
    with Store.open(config.workspace) as store:
        outcome = import_export(store, config, args.source.expanduser().resolve(), now=utc_now())
    _emit(args, outcome.as_dict(), _summary_text(outcome))
    return 0 if outcome.status == "ok" else EXIT_DEGRADED


def cmd_screen(args) -> int:
    config = load_config(resolve_workspace(args.workspace))
    with Store.open(config.workspace) as store:
        labels = screen(store, config, now=utc_now(), rescreen=args.all)
    data = {"ok": True, "labels": dict(labels)}
    return _emit(args, data, [f"初筛 {sum(labels.values())} 篇：{_labels_text(labels)}"])


def cmd_list(args) -> int:
    config = load_config(resolve_workspace(args.workspace))
    with Store.open(config.workspace) as store:
        records = store.posts(labels=args.label, packed=False if args.unpacked else None, limit=args.limit)
    data = {"ok": True, "posts": [_record_brief(record) for record in records]}
    text = [_record_line(record) for record in records] or ["没有符合条件的帖子"]
    return _emit(args, data, text)


def cmd_show(args) -> int:
    config = load_config(resolve_workspace(args.workspace))
    with Store.open(config.workspace) as store:
        record = store.get(args.key)
    if record is None:
        raise GleanerError(f"找不到帖子 {args.key}")
    post = record.post
    data = {"ok": True, "post": {**_record_brief(record), "body": post.body, "open_url": post.open_url}}
    text = [
        _record_line(record),
        f"链接：{post.url}",
        f"关键词：{'、'.join(post.keywords) or '无'}　标签：{'、'.join(post.tags) or '无'}",
        f"图片：本地 {len(post.images)} 张 / 原帖 {post.remote_image_count} 张　互动：{post.metrics or '无'}",
        f"初筛理由：{'、'.join(record.label_reasons) or '无'}（{record.label_source or '未初筛'}）",
        "",
        post.body or "（正文为空）",
    ]
    return _emit(args, data, text)


def cmd_label(args) -> int:
    config = load_config(resolve_workspace(args.workspace))
    reasons = [f"manual:{args.reason}"] if args.reason else ["manual"]
    with Store.open(config.workspace) as store:
        store.set_label(args.key, args.label, source="manual", reasons=reasons, now=iso(utc_now()))
    return _emit(args, {"ok": True, "key": args.key, "label": args.label}, [f"{args.key} → {args.label}"])


def cmd_pack(args) -> int:
    config = load_config(resolve_workspace(args.workspace))
    if args.limit < 1:
        raise GleanerError("--limit 至少为 1")
    bank = args.bank.expanduser().resolve() if args.bank else None
    with Store.open(config.workspace) as store:
        result = build_pack(
            store, config, now=utc_now(), labels=args.label, keys=args.key, limit=args.limit, bank=bank
        )
    text = [
        f"素材包 {result.pack_id}：{result.posts} 篇（图文 {result.image_posts}，纯文字 {result.text_posts}）",
        f"位置：{result.path}",
        f"把 {result.path / 'HANDOFF.md'} 交给装了 interview-bank 的 Agent 导入采集题库。",
    ]
    return _emit(args, {"ok": True, **result.as_dict()}, text)


def cmd_stats(args) -> int:
    config = load_config(resolve_workspace(args.workspace))
    with Store.open(config.workspace) as store:
        stats = store.stats()
        checkpoints = store.checkpoints()
    text = [
        f"帖子 {stats['posts']} 篇，已打包 {stats['packed']} 篇，抓取/导入 {stats['runs']} 次",
        f"标签：{_labels_text(stats['labels'])}",
        f"类型：{'、'.join(f'{k} {v}' for k, v in stats['kinds'].items()) or '无'}",
    ]
    if stats["changed_after_pack"]:
        text.append(f"注意：{stats['changed_after_pack']} 篇在打包后被作者修改过")
    for item in checkpoints:
        text.append(f"关键词「{item['keyword']}」最新发布：{china_date(item['latest_published_at'])}")
    return _emit(args, {"ok": True, **stats, "checkpoints": checkpoints}, text)


def cmd_runs(args) -> int:
    config = load_config(resolve_workspace(args.workspace))
    with Store.open(config.workspace) as store:
        runs = store.runs(limit=args.limit)
    data = {"ok": True, "runs": [run.__dict__ for run in runs]}
    text = [
        f"{run.id}  {run.mode:<6} {run.status:<8} 新 {run.counts.get('new', 0)} / 共 {run.counts.get('records', 0)}"
        + (f"  {run.message}" if run.message else "")
        for run in runs
    ] or ["还没有抓取记录"]
    return _emit(args, data, text)


# Output


def _emit(args, data: dict, text: list[str]) -> int:
    if args.json:
        _print_json(data)
    else:
        print("\n".join(text))
    return 0


def _print_json(data: dict) -> None:
    print(json.dumps(data, ensure_ascii=False, indent=2, default=str))


def _summary_text(outcome) -> list[str]:
    summary = outcome.summary
    lines = [
        f"{outcome.run_id}：读到 {summary.records} 条，新增 {summary.new}，更新 {summary.updated}，"
        f"未变 {summary.unchanged}，跳过 {summary.skipped}",
        f"新帖初筛：{_labels_text(summary.labels)}",
    ]
    if outcome.status != "ok":
        lines.append(f"降级：{outcome.message}")
    lines += [f"  {error}" for error in summary.errors[:5]]
    return lines


def _labels_text(labels) -> str:
    return "、".join(f"{LABEL_TITLES.get(k, k)} {v}" for k, v in sorted(labels.items())) or "无"


def _record_brief(record: PostRecord) -> dict:
    post = record.post
    return {
        "key": post.key,
        "title": post.title,
        "kind": post.kind,
        "source_date": china_date(post.published_at),
        "url": post.url,
        "label": record.label,
        "label_source": record.label_source,
        "label_reasons": record.label_reasons,
        "images": len(post.images),
        "packed_in": record.packed_in,
    }


def _record_line(record: PostRecord) -> str:
    post = record.post
    date = china_date(post.published_at) or "日期未知"
    packed = "  [已打包]" if record.packed_in else ""
    return f"{post.key}  {record.label or '未初筛':<10} {date}  {post.kind:<5} {post.title[:40]}{packed}"


if __name__ == "__main__":
    sys.exit(main())
