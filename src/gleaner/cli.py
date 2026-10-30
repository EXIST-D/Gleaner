from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path

from gleaner import __version__
from gleaner.auto import chosen_platforms, ensure_not_running, outcome_lines, run_auto
from gleaner.bank import BankReader
from gleaner.checks import environment_checks
from gleaner.config import (
    CONFIG_NAME,
    MAX_NOTES_CAP,
    load_config,
    resolve_workspace,
    template_text,
    validate_keywords,
)
from gleaner.errors import GleanerError
from gleaner.glossary import entries as glossary_entries
from gleaner.imports import sync_imports
from gleaner.installer import PARTS, Installer, make_run
from gleaner.models import DISPOSITIONS, LABEL_TITLES, LABELS, MANUAL, PLATFORMS, SKIPPED, PostRecord
from gleaner.packer import DEFAULT_LIMIT, build_pack, packable
from gleaner.pipeline import (
    CRAWL_PLATFORMS,
    crawl,
    fetcher_for,
    import_export,
    page_size,
    planned_notes,
    recordings_to_download,
    screen,
    search_plan,
    seconds_until_next_crawl,
)
from gleaner.store import Store
from gleaner.timeutil import CHINA_TZ, china_date, iso, utc_now

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
    setup = command(
        "setup", cmd_setup, "安装 MediaCrawler（含 Gleaner 的补丁）、Interview Bank 和转写工具，并写进配置"
    )
    setup.add_argument("part", nargs="*", help=f"只装这几项：{' / '.join(PARTS)}；默认全部")
    setup.add_argument(
        "--no-model", action="store_true", help="不下载转写模型（约 1.5 GB；第一次转写时也会下载）"
    )
    command("doctor", cmd_doctor, "检查配置、MediaCrawler 和 Interview Bank 路径")

    platforms_help = "、".join(f"{code} {PLATFORMS[code].label}" for code in CRAWL_PLATFORMS)
    crawling = command("crawl", cmd_crawl, "抓取并导入（牛客由 Gleaner 直接读取，其余平台调用 MediaCrawler）")
    crawling.add_argument("platform", choices=CRAWL_PLATFORMS, help=f"平台：{platforms_help}")
    crawling.add_argument("--keyword", action="append", default=[], help="搜索关键词，可重复；默认用配置里的")
    crawling.add_argument("--url", action="append", default=[], help="只抓这些帖子（链接或 ID），可重复")
    crawling.add_argument(
        "--max-notes",
        type=int,
        help=f"搜索时每个关键词抓多少篇（1–{MAX_NOTES_CAP}；MediaCrawler 平台按整页取整，最少一页）",
    )
    crawling.add_argument("--force", action="store_true", help="忽略同一平台两次抓取之间的最小间隔")
    crawling.add_argument(
        "--full", action="store_true", help="不做增量：不限发布时间，以前保存过的帖子也重新读取"
    )
    crawling.add_argument("--quiet", action="store_true", help="不在终端显示抓取过程的输出")

    auto = command(
        "auto",
        cmd_auto,
        "一条命令走完：增量抓取各平台 → 下载 B 站面试录音 → 打包；适合交给定时任务，遇到要扫码的平台会跳过",
    )
    auto.add_argument(
        "--platform",
        action="append",
        choices=CRAWL_PLATFORMS,
        default=[],
        help=f"只处理这些平台，可重复；默认用配置的 [auto] platforms，没配就是全部（{platforms_help}）",
    )
    auto.add_argument("--no-media", action="store_true", help="不下载 B 站面试录音")
    auto.add_argument("--no-pack", action="store_true", help="只抓取和下载，不打包")
    auto.add_argument(
        "--login", action="store_true", help="登录过期时等你扫码（手动运行时用；默认遇到扫码就跳过这个平台）"
    )
    auto.add_argument(
        "--full", action="store_true", help="不做增量：不限发布时间，以前保存过的帖子也重新读取"
    )
    auto.add_argument("--force", action="store_true", help="忽略同一平台两次抓取之间的最小间隔")
    auto.add_argument("--dry-run", action="store_true", help="只列出这次会做什么，不抓取")
    auto.add_argument("--quiet", action="store_true", help="不在终端显示抓取过程的输出")

    media = command("media", cmd_media, "下载 B 站面试录音的音视频，打包时会转成音频交给 Interview Bank 转写")
    media.add_argument("key", nargs="*", help="帖子键；不写就下载初筛标成录音、还没下载也没打包的视频")
    media.add_argument("--dry-run", action="store_true", help="只列出要下载的视频")
    media.add_argument("--force", action="store_true", help="忽略同一平台两次抓取之间的最小间隔")
    media.add_argument("--quiet", action="store_true", help="不在终端显示抓取过程的输出")

    imported = command(
        "import",
        cmd_import,
        "导入已有的抓取输出目录（含 xhs/、douyin/、bili/、zhihu/ 或 nowcoder/）",
    )
    imported.add_argument("source", type=Path)

    screening = command("screen", cmd_screen, "对未打标签的帖子做规则初筛")
    screening.add_argument("--all", action="store_true", help="同时重新判断以前的规则标签（人工标签不动）")

    listing = command("list", cmd_list, "列出帖子")
    listing.add_argument("--label", action="append", choices=LABELS, help="按标签筛选，可重复")
    listing.add_argument("--platform", action="append", choices=list(PLATFORMS), help="按平台筛选，可重复")
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

    imports = command(
        "imports",
        cmd_imports,
        "对照采集题库：哪些帖子已经导入、哪些素材包还在等导入；也用来记下手工导入或不打算导入的帖子",
    )
    imports.add_argument(
        "--mark", nargs="+", metavar="KEY", help="记下这些帖子是手工导入题库的（不再打包和下载）"
    )
    imports.add_argument("--skip", nargs="+", metavar="KEY", help="记下不打算导入这些帖子（打过包的也行）")
    imports.add_argument("--clear", nargs="+", metavar="KEY", help="去掉上面两种记录")
    imports.add_argument(
        "--dry-run", action="store_true", help="只列出，不记录（对 --mark、--skip、--clear 也一样）"
    )

    bundle = command(
        "bundle",
        cmd_bundle,
        "把采集题库里的题目（不含图片和答案）导成一个文件，交给另一个 Interview Bank（比如线上的个人题库）去重导入",
    )
    bundle.add_argument(
        "--output", type=Path, help="输出文件，默认 workspace/bundles/questions-<日期时间>.json"
    )

    command(
        "glossary",
        cmd_glossary,
        "列出转写录音时常被听错的术语（内置的加上 gleaner.toml 里 [glossary] extra 的）",
    )

    web = command("web", cmd_web, "打开本机网页控制台：概览、可视化抓取、看帖改标签、打包")
    web.add_argument("--port", type=int, default=8765, help="端口，默认 8765；只监听 127.0.0.1")
    web.add_argument("--no-open", action="store_true", help="不自动打开浏览器")

    command("stats", cmd_stats, "统计帖子、标签和抓取记录")
    runs = command("runs", cmd_runs, "最近的抓取记录")
    runs.add_argument("--limit", type=int, default=10)
    return parser


# Commands


def init_workspace(workspace: Path) -> tuple[Path, bool]:
    """Config, database and folders of a workspace; returns the config path and whether it was created."""
    workspace.mkdir(parents=True, exist_ok=True)
    config_path = workspace / CONFIG_NAME
    created = not config_path.exists()
    if created:
        config_path.write_text(template_text(), encoding="utf-8")
    for name in ("runs", "packs"):
        (workspace / name).mkdir(exist_ok=True)
    with Store.open(workspace, create=True):
        pass
    return config_path, created


def cmd_init(args) -> int:
    workspace = resolve_workspace(args.workspace)
    config_path, created = init_workspace(workspace)
    data = {"ok": True, "workspace": str(workspace), "config": str(config_path), "config_created": created}
    text = [f"工作区：{workspace}"]
    text.append(
        f"已写入配置 {config_path}。下一步运行 gleaner setup，安装 MediaCrawler、Interview Bank 和转写工具。"
        if created
        else "配置已存在，未改动。"
    )
    return _emit(args, data, text)


def cmd_setup(args) -> int:
    unknown = [part for part in args.part if part not in PARTS]
    if unknown:
        raise GleanerError(f"不认识 {'、'.join(unknown)}，可选：{' / '.join(PARTS)}")
    workspace = resolve_workspace(args.workspace)
    _, created = init_workspace(workspace)
    installer = Installer(workspace, run=make_run(echo=not args.json))
    steps = installer.install(args.part or PARTS, model=not args.no_model)
    checks = environment_checks(installer.config)
    data = {
        "ok": all(step.ok for step in steps),
        "workspace": str(workspace),
        "workspace_created": created,
        "steps": [step.as_dict() for step in steps],
        "checks": [check.as_dict() for check in checks],
    }
    text = [f"已创建工作区 {workspace}"] if created else []
    for step in steps:
        text.append(f"{'✓' if step.ok else '✗'} {step.title}")
        text += [f"    {line}" for line in step.lines]
    text.append("环境检查：")
    text += [f"  {'✓' if check.ok else '✗'} {check.name}: {check.detail}" for check in checks]
    _emit(args, data, text)
    return 0 if data["ok"] else 1


def cmd_doctor(args) -> int:
    config = load_config(resolve_workspace(args.workspace))
    checks = environment_checks(config)
    data = {"ok": all(check.ok for check in checks), "checks": [check.as_dict() for check in checks]}
    text = [f"{'✓' if check.ok else '✗'} {check.name}: {check.detail}" for check in checks]
    _emit(args, data, text)
    return 0 if data["ok"] else 1


def cmd_crawl(args) -> int:
    config = load_config(resolve_workspace(args.workspace))
    ensure_not_running(config.workspace)
    if args.max_notes is not None and not 1 <= args.max_notes <= MAX_NOTES_CAP:
        raise GleanerError(f"--max-notes 必须在 1 到 {MAX_NOTES_CAP} 之间")
    keywords = validate_keywords(args.keyword) or list(config.keywords)
    targets = [value.strip() for value in args.url if value.strip()]
    platform = args.platform
    fetcher = fetcher_for(config, platform, echo=not (args.quiet or args.json))
    with Store.open(config.workspace) as store:
        if not args.json and not targets:
            planned = planned_notes(
                config, platform=platform, keywords=keywords, targets=targets, max_notes=args.max_notes
            )
            label = PLATFORMS[platform].label
            size = page_size(platform)
            how = (
                f"{label}搜索按页抓取（每页 {size} 篇）"
                if size
                else f"{label}：翻阅最新面经列表（最多 {config.nowcoder.scan_pages} 页），保存标题或摘要含关键词的帖子"
            )
            plan = search_plan(
                store, platform=platform, keywords=keywords, now=utc_now(), incremental=not args.full
            )
            print(f"{how}：{len(keywords)} 个关键词，本次最多约 {planned} 篇", file=sys.stderr)
            print(plan.describe(platform), file=sys.stderr)
        outcome = crawl(
            store,
            config,
            fetcher,
            platform=platform,
            keywords=keywords,
            targets=targets,
            max_notes=args.max_notes,
            force=args.force,
            now=utc_now(),
            incremental=not args.full,
        )
    _emit(args, outcome.as_dict(), _summary_text(outcome))
    return 0 if outcome.status == "ok" else EXIT_DEGRADED


def cmd_auto(args) -> int:
    config = load_config(resolve_workspace(args.workspace))
    platforms = chosen_platforms(config, args.platform)
    download = not args.no_media and config.auto.max_recordings > 0
    if args.dry_run:
        with Store.open(config.workspace) as store:
            data, text = _auto_plan(
                store, config, platforms, download=download, force=args.force, full=args.full
            )
        return _emit(args, data, text)
    say = (lambda text: None) if args.json else (lambda text: print(text, file=sys.stderr))
    with Store.open(config.workspace) as store:
        outcome = run_auto(
            store,
            config,
            platforms=platforms,
            download=download,
            pack=not args.no_pack,
            force=args.force,
            full=args.full,
            attended=args.login,
            echo=not (args.quiet or args.json),
            say=say,
        )
    _emit(args, outcome.as_dict(), outcome_lines(outcome))
    return EXIT_DEGRADED if outcome.degraded else 0


def _auto_plan(store: Store, config, platforms: list[str], *, download: bool, force: bool, full: bool):
    now = utc_now()
    keywords = list(config.keywords)
    rows, text = [], [f"关键词：{'、'.join(keywords) or '无（先在 gleaner.toml 的 keywords 里配置）'}"]
    for platform in platforms:
        plan = search_plan(store, platform=platform, keywords=keywords, now=now, incremental=not full)
        wait = 0 if force else seconds_until_next_crawl(store, config, now, platform=platform)
        label = PLATFORMS[platform].label
        rows.append({"platform": platform, "plan": plan.describe(platform), "wait_seconds": wait})
        text.append(
            f"抓取{label}：{plan.describe(platform)}"
            + (f"；距离上次抓取太近，这次会跳过（还要等 {wait // 60 + 1} 分钟）" if wait else "")
        )
    recordings = (
        recordings_to_download(store)[: config.auto.max_recordings]
        if download and "bili" in platforms
        else []
    )
    text += [f"下载录音：{_record_line(record)}" for record in recordings] or ["下载录音：没有要下载的"]
    ready, held = packable(store)
    text.append(f"打包：{len(ready)} 篇可以打包" + (f"，另有 {held} 个录音等下载" if held else ""))
    data = {
        "ok": True,
        "platforms": rows,
        "recordings": [_record_brief(record) for record in recordings],
        "packable": len(ready),
        "held_recordings": held,
    }
    return data, text


def cmd_media(args) -> int:
    config = load_config(resolve_workspace(args.workspace))
    ensure_not_running(config.workspace)
    with Store.open(config.workspace) as store:
        records = recordings_to_download(store, args.key or None)
        if args.dry_run or not records:
            data = {"ok": True, "posts": [_record_brief(record) for record in records]}
            text = [_record_line(record) for record in records] or ["没有要下载的录音视频"]
            return _emit(args, data, text)
        platforms = {record.post.platform for record in records}
        if len(platforms) > 1:
            raise GleanerError("一次只能下载一个平台的视频")
        platform = platforms.pop()
        fetcher = fetcher_for(config, platform, echo=not (args.quiet or args.json), media=True)
        if not args.json:
            print(f"下载 {len(records)} 个视频的音视频，长视频要十几分钟，请耐心等待", file=sys.stderr)
        outcome = crawl(
            store,
            config,
            fetcher,
            platform=platform,
            keywords=[],
            targets=[record.post.url for record in records],
            max_notes=None,
            force=args.force,
            now=utc_now(),
            media=True,
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
        records = store.posts(
            labels=args.label,
            platforms=args.platform,
            packed=False if args.unpacked else None,
            limit=args.limit,
        )
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
        f"音视频：{post.media or '没有下载'}",
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
    ensure_not_running(config.workspace)
    if args.limit < 1:
        raise GleanerError("--limit 至少为 1")
    bank = args.bank.expanduser().resolve() if args.bank else None
    with Store.open(config.workspace) as store:
        result = build_pack(
            store, config, now=utc_now(), labels=args.label, keys=args.key, limit=args.limit, bank=bank
        )
    recordings = f"，转写录音 {result.media_posts}" if result.media_posts else ""
    text = [
        f"素材包 {result.pack_id}：{result.posts} 篇"
        f"（看图导入 {result.image_posts}，读正文导入 {result.text_posts}{recordings}）",
        f"位置：{result.path}",
        f"把 {result.path / 'HANDOFF.md'} 交给装了 interview-bank 的 Agent 导入采集题库。",
    ]
    if result.held_recordings:
        text.append(f"还有 {result.held_recordings} 个录音没下载，所以没打包；先运行 gleaner media，再打包。")
    return _emit(args, {"ok": True, **result.as_dict()}, text)


def cmd_imports(args) -> int:
    config = load_config(resolve_workspace(args.workspace))
    reader = BankReader(config.handoff)
    changes = {MANUAL: args.mark or [], SKIPPED: args.skip or [], None: args.clear or []}
    wanted = [key for keys in changes.values() for key in keys]
    text: list[str] = []
    with Store.open(config.workspace) as store:
        missing = (
            sorted(set(wanted) - {record.post.key for record in store.posts(keys=wanted)}) if wanted else []
        )
        if missing:
            raise GleanerError(f"找不到帖子：{', '.join(missing)}")
        for disposition, keys in changes.items():
            if keys:
                title = DISPOSITIONS[disposition] if disposition else "去掉记录"
                text.append(f"{'会记为' if args.dry_run else '已记为'}{title}：{'、'.join(keys)}")
                if not args.dry_run:
                    store.set_disposition(keys, disposition, now=iso(utc_now()))
        try:
            report = sync_imports(store, reader.held(), now=utc_now(), adopt=not args.dry_run)
        except GleanerError as exc:
            if not wanted:
                raise
            data = {"ok": True, "changed": {str(k): v for k, v in changes.items() if v}, "compared": False}
            return _emit(args, data, [*text, f"没能对照题库：{exc}"])
    text.append(f"题库里已有 {report.holding} 篇 Gleaner 的帖子，共 {report.questions} 道题")
    if report.adopted:
        verb = "会记下" if args.dry_run else "已记下"
        text.append(
            f"{len(report.adopted)} 篇没经过素材包，是手工导入的，{verb}：{'、'.join(report.adopted)}"
        )
    for pack, keys in report.waiting.items():
        text.append(f"素材包 {pack}：{len(keys)}/{report.pack_sizes[pack]} 篇还没进题库：{'、'.join(keys)}")
    if not report.waiting:
        text.append("没有等待导入的素材包")
    data = {"ok": True, **report.as_dict(), "dry_run": args.dry_run, "compared": True}
    return _emit(args, data, text)


def cmd_bundle(args) -> int:
    config = load_config(resolve_workspace(args.workspace))
    output = args.output.expanduser().resolve() if args.output else None
    if output is None:
        stamp = utc_now().astimezone(CHINA_TZ).strftime("%Y%m%d-%H%M%S")
        output = config.workspace / "bundles" / f"questions-{stamp}.json"
    result = BankReader(config.handoff).export_bundle(output)
    counts = result["counts"]
    text = [
        f"题目包 {output}",
        f"{counts['questions']} 道题、{counts['occurrences']} 次出现、{counts['sources']} 个来源链接、{counts['companies']} 家公司"
        "（没有图片和答案）",
        "导入另一个题库：ibank.py import-bundle --bank <目标题库> --input <这个文件> --dry-run，"
        "看过报告再去掉 --dry-run，按 review sheet 做决定后 ingest finalize。",
    ]
    return _emit(args, {"ok": True, "output": str(output), **result}, text)


def cmd_glossary(args) -> int:
    config = load_config(resolve_workspace(args.workspace))
    items = glossary_entries(config.glossary.extra)
    data = {"ok": True, "entries": [item.__dict__ for item in items]}
    text = [f"{item.heard} → {item.correct}" + (f"（{item.note}）" if item.note else "") for item in items]
    return _emit(args, data, text)


def cmd_web(args) -> int:
    from gleaner.web.server import serve

    workspace = resolve_workspace(args.workspace)
    load_config(workspace)
    serve(workspace, port=args.port, open_browser=not args.no_open)
    return 0


def cmd_stats(args) -> int:
    config = load_config(resolve_workspace(args.workspace))
    with Store.open(config.workspace) as store:
        stats = store.stats()
        checkpoints = store.checkpoints()
    text = [
        f"帖子 {stats['posts']} 篇，已打包 {stats['packed']} 篇，抓取/导入 {stats['runs']} 次",
        f"标签：{_labels_text(stats['labels'])}",
        f"平台：{'、'.join(f'{_platform_label(k)} {v}' for k, v in stats['platforms'].items()) or '无'}",
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
    if summary.media:
        lines.insert(1, f"下载到音视频 {summary.media} 个，打包时会转成音频交给 Interview Bank 转写")
    if outcome.status != "ok":
        lines.append(f"降级：{outcome.message}")
    lines += [f"  {error}" for error in summary.errors[:5]]
    return lines


def _labels_text(labels) -> str:
    return "、".join(f"{LABEL_TITLES.get(k, k)} {v}" for k, v in sorted(labels.items())) or "无"


def _platform_label(code: str) -> str:
    return PLATFORMS[code].label if code in PLATFORMS else code


def _record_brief(record: PostRecord) -> dict:
    post = record.post
    return {
        "key": post.key,
        "platform": post.platform,
        "title": post.title,
        "kind": post.kind,
        "source_date": china_date(post.published_at),
        "url": post.url,
        "label": record.label,
        "label_source": record.label_source,
        "label_reasons": record.label_reasons,
        "images": len(post.images),
        "media": post.media,
        "packed_in": record.packed_in,
        "disposition": record.disposition,
    }


def _record_line(record: PostRecord) -> str:
    post = record.post
    date = china_date(post.published_at) or "日期未知"
    flags = (
        ("  [有音视频]" if post.media else "")
        + ("  [已打包]" if record.packed_in else "")
        + (f"  [{DISPOSITIONS[record.disposition]}]" if record.disposition else "")
    )
    return f"{post.key}  {record.label or '未初筛':<10} {date}  {post.kind:<5} {post.title[:40]}{flags}"


if __name__ == "__main__":
    sys.exit(main())
