# 素材包格式（schema_version 1）

`gleaner pack` 在 `<工作区>/packs/<pack-id>/` 生成一个素材包。整个包先写进 `.tmp-<pack-id>`，完成后才改名，
所以看到的包要么完整、要么不存在。

```text
packs/<pack-id>/
├── manifest.json
├── HANDOFF.md                 # 给 Agent 的导入说明与命令
└── posts/<platform>-<post_id>/
    ├── body.txt               # 标题、正文、标签（UTF-8）
    ├── images/01.jpg …        # 帖子图片，按原帖顺序；没有图片时不存在
    └── audio.flac             # 只有录音帖有：从下载的视频提取的 16 kHz 单声道音频，做过响度均衡
```

## manifest.json

```json
{
  "schema": "gleaner.pack",
  "schema_version": 1,
  "pack_id": "pack-20261027-164805-49e4",
  "created_at": "2026-10-27T08:48:05+00:00",
  "gleaner_version": "0.1.0",
  "bank": "/Users/you/Gleaner/collected-bank",
  "posts": [
    {
      "key": "xhs:aa01",
      "platform": "xhs",
      "platform_label": "小红书",
      "source_url": "https://www.xiaohongshu.com/explore/aa01",
      "source_date": "2026-10-21",
      "published_at": "2026-10-21T00:00:00+00:00",
      "title": "示例科技后端一面面经",
      "kind": "image",
      "intake": "images",
      "dir": "posts/xhs-aa01",
      "text_file": "posts/xhs-aa01/body.txt",
      "images": ["posts/xhs-aa01/images/01.jpg", "posts/xhs-aa01/images/02.jpg"],
      "media": null,
      "label": "candidate",
      "label_reasons": ["interview:面经", "event:一面"],
      "keywords": ["后端 面经"],
      "tags": ["面经", "后端"],
      "metrics": {"liked_count": 12000, "collected_count": 356},
      "notes": []
    }
  ]
}
```

| 字段 | 说明 |
|---|---|
| `source_url` | 去掉 `xsec_token`、`utm_*` 等参数后的链接，对应 Interview Bank 元数据 `source_url` |
| `source_date` | 北京时间的发布日期，对应 `source_date`；**不是**面试日期 `event_date` |
| `platform` / `platform_label` | `xhs` 小红书、`dy` 抖音、`bili` B站、`zhihu` 知乎、`nowcoder` 牛客；`platform_label` 对应 Interview Bank 元数据 `platform` |
| `kind` | `image` 图文、`video` 视频、`text` 纯文字 |
| `intake` | `images`：用 `ingest images` 导入图片目录；`text`：用 `web-intake` 导入 `body.txt`；`media`：用 `media` 转写 `audio.flac` |
| `media` | 录音帖的音频路径（相对素材包），其他帖子是 `null` |
| `body_questions` | 正文里像题目的行数：带编号或问号、含提问线索词、去掉编号后至少 6 个字 |
| `skipped_images` | 正文已列出题目、改读正文时没有打包的图片张数（原图仍在工作区 `runs/` 里） |
| `label` / `label_reasons` | Gleaner 初筛标签与命中理由，只作参考 |
| `metrics` | 点赞、收藏、评论、分享、播放或浏览数（各平台提供的不同），只用于筛选和排序，不进题库 |
| `notes` | `video-not-included`：视频未打包；`audio-normalized`：录音帖，音频做过响度均衡；`media-missing`：下载过的视频不在了，改读正文；`images-missing`：原帖有图但没有下载到；`images-not-imported`：正文已列出题目，图片未打包 |

`intake` 的选择：下载过音视频的 B 站录音走 `media`；其余帖子正文列出至少 3 道题、并且不少于图片张数时读正文，否则有图就看图。
带 `#牛客AI配图神器#` 的帖子，图片张数先减去牛客按正文生成的那一张。
小红书很多面经把同一份题单排版成几张图，正文里也贴一遍；读正文不用看图，能省下大部分额度。
图片多、正文题少的帖子仍然看图，判断出错时只会多看几张图，不会漏题。

## 与 Interview Bank 的对应

- 图片：`ibank.py ingest images <dir>… --retention copy --bank <采集题库>`，每张图是一个来源；
  Agent 抽题时按图片所在目录在 manifest 里找到对应帖子，填写 `platform`、`source_url`、`source_date`。
- 正文：`ibank.py web-intake --url <source_url> --text <body.txt> --retention copy --bank <采集题库>`，
  来源类型为 `web`，正文按段落编号。
- 录音：每篇一次 `ibank.py media <audio.flac> --retention copy --bank <采集题库>`，记下 `result.id`；
  再用装了 faster-whisper 的 Python（`[handoff] asr_python`）运行
  `media-transcribe --run <id> --model large-v3-turbo --language zh --download-model`，
  转写好后用 `media-task --run <id> --source <来源> --offset … --limit 200` 分页读片段。
  抽取结果的 `reviewed_segment_ids` 要覆盖全部片段，每道题写 `segment_ids`；改正了听错的词时写 `correction`。
  模型缓存在每个题库的 `cache/asr/` 下，所以每个题库第一次转写都要下载一次模型。
- 之后照常 `ingest submit`、`ingest finalize`。相同字节的来源不会重复计数，所以重复导入同一个包不会抬高频次。

兼容性：新增字段不改变版本号；删除或改变字段含义时提高 `schema_version`。
