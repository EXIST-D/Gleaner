# Gleaner · 拾穗

把散落在小红书、抖音、B 站、快手、知乎和牛客上的真实面经一穗一穗捡回来：前五个平台调用你本机的
[MediaCrawler](https://github.com/NanmiCoder/MediaCrawler) 小批量抓取，牛客由 Gleaner 直接读取公开的面经帖子；
按帖子去重、规则初筛，再打成素材包，交给 [Interview Bank](https://github.com/EXIST-D/Interview-bank) 导入一个**独立的采集题库**。

Gleaner 只负责“发现 → 抓取 → 筛选 → 打包”。题目抽取、分类、同义题合并和参考答案仍由 Interview Bank 完成，
所以主项目的保守去重原则（拿不准就交给你裁决）同样适用于采集来的题。

当前版本 **0.1.0**（未发布的改动见 [CHANGELOG](CHANGELOG.md)）。

| 平台 | 代码 | 抓取方式 | 导入内容 |
|---|---|---|---|
| 小红书 | `xhs` | MediaCrawler，扫码登录；搜索每页 20 篇 | 正文 + 图文帖的图片 |
| 抖音 | `dy` | MediaCrawler，扫码登录；搜索每页 10 篇 | 文案 + 图集的图片；视频帖只用文案 |
| B 站 | `bili` | MediaCrawler，扫码登录；搜索每页 20 个 | 标题和简介（不下载视频） |
| 快手 | `ks` | MediaCrawler，扫码登录；搜索每页 20 个 | 标题文案（不下载视频） |
| 知乎 | `zhihu` | MediaCrawler，扫码登录；搜索每页 20 条 | 回答、文章的纯文字正文 |
| 牛客 | `nowcoder` | Gleaner 直接读取，不登录、不用站内搜索 | 讨论帖和动态的正文 + 图片 |

## 工作方式

```text
MediaCrawler（你本机、你自己的登录）   Gleaner 牛客读取器（公开的面经列表与帖子）
        │  jsonl + 图片                      │  同样的 jsonl + 图片布局
        └──────────────┬─────────────────────┘
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
  只抓牛客时不需要它，见 [牛客读取](docs/nowcoder.md)。
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

# 6. 小批量抓取（搜索按页返回，每个关键词默认一页；同一平台两次至少间隔 5 分钟）
gleaner crawl xhs --keyword "后端 面经"
gleaner crawl xhs --url "https://www.xiaohongshu.com/explore/<帖子ID>?xsec_token=..."
gleaner crawl bili --keyword "Java 面试 复盘"
gleaner crawl nowcoder --keyword "后端 一面" --max-notes 10   # 在最新面经里找标题或摘要含“后端”和“一面”的
gleaner crawl nowcoder --url "https://www.nowcoder.com/discuss/<帖子ID>"

# 7. 看初筛结果，必要时人工改标签
gleaner list --label candidate --platform nowcoder
gleaner show xhs:<帖子ID>
gleaner label xhs:<帖子ID> promo --reason "结尾在卖课"

# 8. 打包，把 HANDOFF.md 交给装了 interview-bank 的 Agent
gleaner pack --limit 20
```

已经用 MediaCrawler 的 WebUI 或命令行抓过的数据，可以直接导入：`gleaner import <MediaCrawler 的 data 目录>`，
里面的 `xhs/`、`douyin/`、`bili/`、`kuaishou/`、`zhihu/` 会一起导入。

帖子键是 `平台代码:帖子 ID`，例如 `xhs:6650a1b2c3d4`、`dy:7300…`、`bili:115…`（av 号）、
`zhihu:answer-…` / `zhihu:article-…`、`nowcoder:945…`（讨论帖）或 `nowcoder:<32 位 UUID>`（动态）。

## 网页控制台

```bash
gleaner --workspace workspace web          # 打开 http://127.0.0.1:8765/
```

上面这些步骤都可以在浏览器里完成，不用记命令：

- **概览**：帖子数、初筛分布、待打包数量、每个关键词抓到的最新日期、最近几次抓取和环境检查。
- **抓取**：先选平台，再填关键词（回车添加）、选每个关键词抓几页（牛客选篇数），点“开始抓取”。页面按平台显示步骤
  （例如小红书是 启动浏览器 → 登录 → 搜索 → 下载 → 导入与初筛，牛客是 翻阅面经列表 → 读取帖子 → 下载图片 → 导入与初筛），
  需要扫码、输入验证码或点确认框时会醒目提示（扫码按 MediaCrawler 的 10 分钟等待倒计时），牛客要求放慢时会提示正在等待；
  下方是实时日志，可以随时停止。批量上限、同一平台两次抓取的最小间隔、去重和初筛与命令行完全相同。
- **帖子**：按平台、标签、是否打包筛选或搜索；卡片上能看到封面、平台、标签，以及打包时会“读正文”还是“看图”。
  点开一篇可以看全部图片和正文、一键改标签、打开原帖；勾选后点“打包所选”。
- **素材包**：每个包的 HANDOFF.md，命令可以一键复制。
- **题库**：Agent 导入采集题库后整理好的题目。按领域分组、按出现次数排序，可以按公司、领域筛选或搜索；
  点开一道题能看到参考答案（有的话）、每次出现的原文、公司、轮次、来源截图，以及原帖和 Gleaner 帖子的链接，
  还有合并过的问法和相关题目。这一页只读，通过 Interview Bank 的命令行读取，不改动题库。
  “导出 Markdown”按当前选中的公司和领域（含子领域）生成答案版和题目版两份报告，保存在采集题库的 `exports/`；
  “已导出的报告”可以在线预览（目录跳转、两版切换）、下载 `.md`、复制全文或路径。
- **进度**：本机的开发记录（工作区里的 `开发进度.md`，没有就显示开发手册的「当前状态」）、`CHANGELOG.md` 里还没发布的改动、
  最近的提交和未提交/未推送的数量。右边可以按页面写意见，追加保存到工作区的 `意见.md`，供下次开发时处理。

概览和进度页每 30 秒自动刷新，抓取结果和新的提交不用手动刷新也能看到。

控制台只监听 `127.0.0.1`，拒绝其他 Host 的请求，写操作需要页面里的随机令牌，并启用了严格的内容安全策略。
`#posts?open=<帖子键>`、`#packs?open=<包编号>`、`#bank?open=<题目编号>`、`#bank?report=<文件名>`
可以直接打开某篇帖子、素材包、题目或导出的报告。

## 命令

| 命令 | 作用 |
|---|---|
| `init` | 创建工作区：`gleaner.toml`、`gleaner.db`、`runs/`、`packs/` |
| `doctor` | 检查 MediaCrawler 目录与启动命令、默认关键词、`ibank.py`、采集题库 |
| `crawl <平台>` | `xhs` `dy` `bili` `ks` `zhihu` 调用 MediaCrawler，`nowcoder` 直接读取；`--keyword` 搜索或 `--url` 指定帖子，随后导入并初筛 |
| `import <dir>` | 导入已有的 MediaCrawler（或 Gleaner 自己的 `runs/<id>/raw`）输出目录 |
| `screen [--all]` | 对未打标签的帖子初筛；`--all` 重判以前的规则标签，人工标签不动 |
| `list` / `show` | 查看帖子、标签和初筛理由；`list --platform` 按平台筛选 |
| `label <key> <label>` | 人工改标签，以后的初筛不会覆盖 |
| `pack` | 打包（默认只打 `candidate` 且没打过包的帖子） |
| `web` | 打开本机网页控制台（`--port` 换端口，`--no-open` 不自动打开浏览器） |
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
    │   ├── mediacrawler.log   # MediaCrawler 的完整输出（牛客是 nowcoder.log）
    │   └── raw/<平台目录>/     # 原始导出（jsonl + media/），如 xhs/、douyin/ 与 dy/、nowcoder/
    └── packs/<pack-id>/   # 交给 Interview Bank 的素材包，格式见 docs/pack-format.md
```

配置里用相对路径，整个 `Gleaner/` 目录挪到别处也不用改。挪动后要重建两个虚拟环境：
仓库的 `.venv` 和 `MediaCrawler/.venv`，因为里面写着绝对路径；`uv` 会从缓存重装，不用重新下载。

## 使用边界

- **个人学习、非商业**。MediaCrawler 使用“非商业学习许可证”，禁止大规模爬取。搜索按页返回（抖音每页 10 篇，其余 20 篇），
  Gleaner 默认每个关键词只抓一页、最多 40 篇，并发 1，不抓评论，同一平台两次抓取之间至少间隔 5 分钟。
- **牛客按 robots.txt 行事**。牛客的 robots.txt 不允许抓取站内搜索 `/search`，所以 Gleaner 不用搜索，
  而是翻阅最新面经列表（默认最多 5 页、100 篇）在本地匹配关键词；不登录，一次一个请求，间隔至少 3 秒，
  遇到 429 按 `Retry-After` 等待、连续 3 次或遇到验证页就停下。作者昵称和 IP 属地不保存。
- **MediaCrawler 不随本仓库分发**。它的代码不在这里，每个使用者各自下载安装，各自遵守它的许可证。
- **数据留在本机**。工作区、采集题库、MediaCrawler 目录和登录状态都已加入 `.gitignore`。帖子和图片的版权属于原作者，
  不要公开转载原文；交给题库的是题目、来源链接和必要的短摘录。
- 素材包里的链接已经去掉 `xsec_token` 等会话参数；带参数的原始链接只保存在本机数据库里。

## 开发

```bash
git config core.hooksPath .githooks                       # 提交信息需以 GLN-N: 开头
python -B -X utf8 -m unittest discover -s tests -t . -v   # 测试（只用标准库）
pip install ruff==0.16.10 && ruff check src tests && ruff format --check src tests
node --check src/gleaner/web/static/app.js
```

CI 在 Windows、macOS、Linux × Python 3.11–3.13 上运行测试和 lint。测试数据全部是合成的，不包含真实帖子。
搭环境、搬数据、提交流程、代码约定和待办见 [开发手册](docs/development.md)，设计说明见 [架构说明](docs/architecture.md)。

## 计划

- 视频帖的口述内容：B 站、抖音、快手的面试复盘视频目前只导入标题和简介，以后可以交给 Interview Bank 的
  `ingest media` 本地转写
- 可选的大模型复判：规则拿不准的帖子再交给模型判断
- 采集题库与个人题库的覆盖对照

## 许可证

MIT，见 [LICENSE](LICENSE)。许可证只覆盖 Gleaner 自己的代码和文档。
