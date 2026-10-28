"""Read crawler output laid out like MediaCrawler's `--save_data_path`.

Records live in `<raw>/<data dir>/jsonl/<type>_contents_<date>.jsonl` (or `json/`), media in
`<raw>/<media dir>/media/<post id>/001.jpg …`. MediaCrawler names the two directories differently for some
platforms (`douyin/` and `dy/media/`); Gleaner's own Nowcoder fetcher writes the same layout.
"""

from __future__ import annotations

import shutil
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from pathlib import Path

from gleaner.adapters import bilibili, douyin, kuaishou, nowcoder, xhs, zhihu
from gleaner.adapters.common import IMAGE_FILE, ParseReport, read_records
from gleaner.models import Post


@dataclass(frozen=True)
class Export:
    platform: str
    data_dir: str
    media_dir: str | None
    to_post: Callable[..., Post | None]


EXPORTS = {
    "xhs": Export("xhs", "xhs", "xhs", xhs.to_post),
    "dy": Export("dy", "douyin", "dy", douyin.to_post),
    "bili": Export("bili", "bili", "bili", bilibili.to_post),
    "ks": Export("ks", "kuaishou", "ks", kuaishou.to_post),
    "zhihu": Export("zhihu", "zhihu", None, zhihu.to_post),
    "nowcoder": Export("nowcoder", "nowcoder", "nowcoder", nowcoder.to_post),
}


def content_files(raw_dir: Path, platform: str | None = None) -> list[Path]:
    exports = [EXPORTS[platform]] if platform else EXPORTS.values()
    found = []
    for export in exports:
        base = raw_dir / export.data_dir
        found += [*base.glob("jsonl/*_contents_*.jsonl"), *base.glob("json/*_contents_*.json")]
    return sorted(found)


def iter_posts(raw_dir: Path, workspace: Path, report: ParseReport) -> Iterator[Post]:
    for export in EXPORTS.values():
        media = raw_dir / (export.media_dir or export.data_dir) / "media"
        for path in content_files(raw_dir, export.platform):
            for number, record in read_records(path, report):
                report.records += 1
                post = export.to_post(record, media=media, workspace=workspace)
                if post is None:
                    report.skipped += 1
                    report.errors.append(f"{export.data_dir}/{path.name}:{number} 没有帖子 ID")
                    continue
                yield post


def copy_export(source: Path, raw_dir: Path) -> list[str]:
    """Copy records and post images of an existing output directory into a run; returns the platforms found.

    `source` is either the whole data directory (with `xhs/`, `douyin/` … inside) or one platform's directory.
    """
    by_dir = {export.data_dir: export for export in EXPORTS.values()}
    if source.name in by_dir and source.is_dir():
        base, candidates = source.parent, [by_dir[source.name]]
    else:
        base, candidates = source, list(EXPORTS.values())
    found = [export for export in candidates if content_files(base, export.platform)]
    if not found:
        raise FileNotFoundError(f"{source} 里没有可识别的 MediaCrawler 导出目录（xhs/、douyin/、bili/ …）")
    for export in found:
        for path in content_files(base, export.platform):
            destination = raw_dir / export.data_dir / path.parent.name / path.name
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, destination)
        if export.media_dir is None:
            continue
        for post_dir in sorted((base / export.media_dir / "media").glob("*")):
            for image in sorted(post_dir.glob("*")):
                if IMAGE_FILE.match(image.name):
                    destination = raw_dir / export.media_dir / "media" / post_dir.name / image.name
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(image, destination)
    return [export.platform for export in found]
