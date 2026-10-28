# Gleaner · 拾穗

把散落在小红书等平台上的真实面经一穗一穗捡回来：调用你本机的
[MediaCrawler](https://github.com/NanmiCoder/MediaCrawler) 小批量抓取帖子，按帖子去重、规则初筛，
再打成素材包，交给 [Interview Bank](https://github.com/EXIST-D/Interview-bank) 导入一个**独立的采集题库**。

Gleaner 只负责“发现 → 抓取 → 筛选 → 打包”。题目抽取、分类、同义题合并和参考答案仍由 Interview Bank 完成，
所以主项目的保守去重原则（拿不准就交给你裁决）同样适用于采集来的题。

当前版本 **0.1.0**，支持小红书。

## 工作方式

```text
MediaCrawler（你本机、你自己的登录）
        │  jsonl + 图片
        ▼
gleaner crawl / import ──► 工作区 SQLite：帖子按「平台 + 帖子 ID」去重，记录每个关键词的最新发布时间
        │
        ▼
gleaner screen ──► 规则初筛：疑似真实面经 / 题目合集 / 广告卖课 / 无关 / 拿不准（只打标签，不删除）
        │
        ▼
gleaner pack ──► 素材包：manifest.json + 每篇的 body.txt 与图片 + HANDOFF.md（含导入命令）
        │
        ▼
Interview Bank（Agent）──► 采集题库：ingest images / web-intake → 抽题 → 去重 → 提交
```

为什么是独立的采集题库：个人题库的出现次数代表“你自己搜集到的面经”，批量抓来的帖子如果混进去，
就分不清哪些是市场上的高频题、哪些是你亲身遇到的。采集题库单独统计，需要的题再通过 JD 选题或人工挑选放进个人题库。

## 环境要求

- Python **3.11+**，Gleaner 本身只用标准库。
- 你自己下载安装的 MediaCrawler（需要 uv、Node.js 和新版 Chrome），见 [MediaCrawler 接入](docs/mediacrawler.md)。
- 导入素材包时需要 Interview Bank 的 `ibank.py`（Python 3.10+）和一个支持 Agent Skills 的 Agent。

## 快速开始

```bash
# 1. 安装 Gleaner（在本仓库目录；需要 Python 3.11+，用 uv 时它会自动准备）
uv venv --python 3.11 .venv && uv pip install --python .venv/bin/python -e .
export PATH="$PWD/.venv/bin:$PATH"

# 2. 把 MediaCrawler 放进仓库目录（已被 git 忽略），见 docs/mediacrawler.md
git clone https://github.com/NanmiCoder/MediaCrawler.git MediaCrawler && (cd MediaCrawler && uv sync)

# 3. 创建工作区（仓库下的 workspace/，已被 git 忽略）
export GLEANER_HOME="$PWD/workspace"
gleaner init
#    默认配置已指向 ../MediaCrawler 和 ../collected-bank，只需填写 handoff.ibank

# 4. 新建独立的采集题库（只需一次）
python3 -B <Interview-bank>/skills/interview-bank/scripts/ibank.py init --bank collected-bank

# 5. 检查环境
gleaner doctor

# 6. 小批量抓取（搜索按页返回，每个关键词一页 20 篇；两次至少间隔 5 分钟）
gleaner crawl xhs --keyword "后端 面经"
gleaner crawl xhs --url "https://www.xiaohongshu.com/explore/<帖子ID>?xsec_token=..."

# 7. 看初筛结果，必要时人工改标签
gleaner list --label candidate
gleaner show xhs:<帖子ID>
gleaner label xhs:<帖子ID> promo --reason "结尾在卖课"

# 8. 打包，把 HANDOFF.md 交给装了 interview-bank 的 Agent
gleaner pack --limit 20
```

已经用 MediaCrawler 的 WebUI 或命令行抓过的数据，可以直接导入：`gleaner import <MediaCrawler 的 data 目录>`。

## 命令

| 命令 | 作用 |
|---|---|
| `init` | 创建工作区：`gleaner.toml`、`gleaner.db`、`runs/`、`packs/` |
| `doctor` | 检查 MediaCrawler 目录与启动命令、默认关键词、`ibank.py`、采集题库 |
| `crawl xhs` | 调用 MediaCrawler 抓取（`--keyword` 搜索或 `--url` 指定帖子），随后导入并初筛 |
| `import <dir>` | 导入已有的 MediaCrawler 输出目录 |
| `screen [--all]` | 对未打标签的帖子初筛；`--all` 重判以前的规则标签，人工标签不动 |
| `list` / `show` | 查看帖子、标签和初筛理由 |
| `label <key> <label>` | 人工改标签，以后的初筛不会覆盖 |
| `pack` | 打包（默认只打 `candidate` 且没打过包的帖子） |
| `stats` / `runs` | 统计与抓取记录 |

所有命令都支持 `--json`。工作区用 `--workspace` 或环境变量 `GLEANER_HOME` 指定。
退出码：0 成功，1 出错，3 抓取降级（例如登录过期，已产出的部分仍会导入）。

## 目录结构

所有运行数据都放在仓库目录里，并且全部被 git 忽略：

```text
Gleaner/
├── src/ tests/ docs/      # 代码与文档（进 git）
├── MediaCrawler/          # 你自己下载的 MediaCrawler，含 browser_data/ 登录状态
├── collected-bank/        # 独立的采集题库（Interview Bank 格式）
└── workspace/             # Gleaner 工作区（GLEANER_HOME）
    ├── gleaner.toml       # 配置；路径相对本文件，默认指向 ../MediaCrawler 和 ../collected-bank
    ├── gleaner.db         # 帖子、标签、抓取记录、关键词进度
    ├── runs/<run-id>/
    │   ├── mediacrawler.log   # MediaCrawler 的完整输出
    │   └── raw/xhs/           # MediaCrawler 原始导出（jsonl + media/）
    └── packs/<pack-id>/   # 交给 Interview Bank 的素材包，格式见 docs/pack-format.md
```

配置里用相对路径，整个 `Gleaner/` 目录挪到别处也不用改。挪动后要重建两个虚拟环境：
仓库的 `.venv` 和 `MediaCrawler/.venv`，因为里面写着绝对路径；`uv` 会从缓存重装，不用重新下载。

## 使用边界

- **个人学习、非商业**。MediaCrawler 使用“非商业学习许可证”，禁止大规模爬取。小红书搜索按页返回（每页 20 篇），
  Gleaner 默认每个关键词只抓一页、最多两页，并发 1，不抓评论，两次抓取之间至少间隔 5 分钟。
- **MediaCrawler 不随本仓库分发**。它的代码不在这里，每个使用者各自下载安装，各自遵守它的许可证。
- **数据留在本机**。工作区、采集题库、MediaCrawler 目录和登录状态都已加入 `.gitignore`。帖子和图片的版权属于原作者，
  不要公开转载原文；交给题库的是题目、来源链接和必要的短摘录。
- 素材包里的链接已经去掉 `xsec_token` 等会话参数；带参数的原始链接只保存在本机数据库里。

## 开发

```bash
python -B -X utf8 -m unittest discover -s tests -t . -v   # 测试（只用标准库）
pip install ruff==0.16.10 && ruff check src tests && ruff format --check src tests
```

CI 在 Windows、macOS、Linux × Python 3.11–3.13 上运行测试和 lint。测试数据全部是合成的，不包含真实帖子。
设计说明见 [docs/architecture.md](docs/architecture.md)。

## 计划

- 牛客讨论帖来源（MediaCrawler 不支持牛客，需要单独的抓取器）
- 可选的大模型复判：规则拿不准的帖子再交给模型判断
- B 站面试复盘视频、知乎面经回答（MediaCrawler 已支持，接入后走 Interview Bank 的音视频与网页入库）
- 采集题库与个人题库的覆盖对照

## 许可证

MIT，见 [LICENSE](LICENSE)。许可证只覆盖 Gleaner 自己的代码和文档。
