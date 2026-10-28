# 架构说明

## 模块

| 模块 | 职责 |
|---|---|
| `cli.py` | 命令行入口；预期内的错误打印一行中文提示，`--json` 输出机器可读结果 |
| `config.py` | 读取并校验 `gleaner.toml`；模板在 `templates/gleaner.toml` |
| `sources/base.py` | `Fetcher` 接口（`problems()`、`run()`）与 `FetchResult` |
| `sources/mediacrawler.py` | 按平台拼装 MediaCrawler 命令（分页大小、是否下载媒体）、运行子进程、记录日志、把失败归为“降级” |
| `sources/nowcoder.py` | 牛客读取器：翻阅最新面经列表、本地匹配关键词、读详情和图片；限速、重试、识别拦截 |
| `adapters/exports.py` | 各平台导出的数据目录与图片目录对照；读取运行目录里的全部帖子；复制外部导出目录 |
| `adapters/common.py` | 适配器共用：读 jsonl/json、找本地图片、解析“1.2万”这类计数、话题标签 |
| `adapters/<平台>.py` | `xhs`、`douyin`、`bilibili`、`kuaishou`、`zhihu`、`nowcoder`：把一条原始记录转成 `Post` |
| `urls.py` | 各平台的稳定链接、去掉会话和分享参数、从链接反查帖子键 |
| `store.py` | SQLite：帖子、标签、运行记录、关键词进度；带 schema 版本 |
| `screening.py` | 可解释的规则初筛 |
| `pipeline.py` | 编排：按平台选抓取器 → 抓取 → 导入 → 初筛；按平台计算计划篇数和抓取间隔 |
| `packer.py` | 生成素材包与 `HANDOFF.md` |
| `bodytext.py` | 识别正文里已经列出的题目，决定读正文还是看图 |
| `checks.py` | 环境检查，`doctor` 和网页共用 |
| `bank.py` | 访问采集题库：调用 Interview Bank 的 `search --format jsonl --detail`、`show`、`taxonomy` 读取（按数据文件变化缓存），用 `export` 生成 Markdown 报告 |
| `web/server.py` | 标准库 HTTP 服务：只监听本机、Host 校验、写操作令牌、CSP；JSON 接口与静态文件 |
| `web/jobs.py` | 后台抓取任务：同一时间只跑一个，日志按行号增量轮询，可停止（MediaCrawler 结束子进程，牛客在下一个请求前停下） |
| `web/progress.py` | 按平台给出步骤，从日志识别当前步骤和需要人操作的提示（扫码、验证、确认框、牛客限流等待） |
| `web/static/` | 原生 HTML/CSS/JS 前端，无构建步骤 |

依赖方向是单向的：`cli / web → pipeline / packer → sources / adapters / screening → store / models`。
网页控制台和命令行调用同一组函数（`crawl`、`fetcher_for`、`build_pack`、`environment_checks`），所以两边的限制和结果一致。
来源相关的细节（命令行参数、接口地址、导出字段）只出现在 `sources/` 和 `adapters/` 里，来源改版时只改这两处
（再加上 `web/progress.py` 里的日志标记）。

## 平台

| 代码 | 平台 | 抓取器 | 运行记录的 `source` | 帖子 ID |
|---|---|---|---|---|
| `xhs` | 小红书 | MediaCrawler | `mediacrawler-xhs` | 笔记 ID |
| `dy` | 抖音 | MediaCrawler | `mediacrawler-dy` | aweme ID |
| `bili` | B站 | MediaCrawler | `mediacrawler-bili` | av 号（数字） |
| `ks` | 快手 | MediaCrawler | `mediacrawler-ks` | 作品 ID |
| `zhihu` | 知乎 | MediaCrawler | `mediacrawler-zhihu` | `answer-…`、`article-…`、`zvideo-…`（三类 ID 各自编号，会重复） |
| `nowcoder` | 牛客 | `sources/nowcoder.py` | `nowcoder` | 讨论帖数字 ID，或动态的 32 位 UUID（小写） |

抓取间隔和关键词进度都按 `source` 分开：刚抓过小红书，不影响马上抓牛客。
导入已有目录时，只含一个平台的记为该平台的 `source`，含多个平台的记为 `mediacrawler`，参数里列出平台。

## 关键决定

**只调用，不内置 MediaCrawler。** 它的许可证是非商业、不可转让的，和本仓库的 MIT 不兼容。
Gleaner 通过子进程调用使用者自己的副本，一次运行一个新的 `--save_data_path`，
这样不用像别的项目那样比较文件修改时间来找出“这次新增的文件”。

**失败是“降级”，不是崩溃。** 登录过期、滑块验证、超时、退出码非 0、没有产出，都记为 `degraded` 并写明原因；
已经产出的数据照常导入。退出码 3 让脚本能区分降级和真正的错误。

**帖子去重和题目去重分开。** Gleaner 只按“平台 + 帖子 ID”去重帖子；题目级的同义合并交给 Interview Bank。
同一篇帖子再次抓到时只合并关键词、刷新互动数；正文变化才算更新。打包后被作者修改的帖子会标记出来，
但不会自动重新打包，否则同一篇的题会在题库里被计两次。

**标签只用来排序和挑选，不删除。** 规则标签（`label_source = rule`）在正文变化或 `screen --all` 时会重判；
人工标签（`manual`）永远不被覆盖。每个标签都带命中理由，例如 `event:一面`、`promo:训练营`。
除了词表，正文里出现 5 个以上不同的外部域名也判为广告（SEO 引流帖会堆一串无关域名）；
github、leetcode、各平台自身等常见域名和邮箱地址不计数。

**一篇帖子只走一个入口，能读正文就不看图。** 两个入口都走会让同一道题在采集题库里出现两次。
正文已经列出题目（至少 3 道且不少于图片张数，规则在 `bodytext.py`）时走 `web-intake`，图片不打包；
否则有图就走 `ingest images`。看图比读正文贵得多，而第一次试导入的 5 篇里有 4 篇的图只是正文的排版。
题数门槛和“不少于图片张数”保证误判只会多看图、不会漏题。视频帖的视频不进素材包，只导入正文。

**发布日期不是面试日期。** 素材包只提供 `source_date`（按北京时间换算的发布日期）。
面试日期、公司、轮次需要 Interview Bank 在原文里找到依据才填写。

**题库页面只通过 Interview Bank 的公开命令读取。** Gleaner 不直接解析题库的 JSONL，这样题库升级格式、
加锁或校验规则变化时，页面不会读到半写入的数据，也不需要跟着改。题目列表按 `data/` 下文件的修改时间缓存；
来源截图只按“64 位哈希 + 图片后缀”的文件名从题库 `media/` 目录提供。
导出同样交给 `ibank.py export`，报告只写进题库的 `exports/`（Interview Bank 自己也只允许这里）；
预览和下载只接受该目录下的 `.md` 文件名，不接受路径。前端的 Markdown 渲染只覆盖导出会用到的语法，
内容先转义再加标签，只认 `<details><summary>` 这一种 HTML。

**会话参数不离开本机。** 小红书链接里的 `xsec_token` 和登录会话有关。`url` 字段去掉了它和其他分享参数，
用于去重和交接；原样的 `open_url` 只保存在本机数据库里，方便自己打开原帖。

**牛客不用站内搜索。** 牛客的 robots.txt 不允许 `/search`。读取器翻阅最新面经列表、在标题和摘要里本地匹配关键词，
只读取命中的帖子详情。代价是只能找到最近的帖子，适合经常小批量抓；旧帖子用 `--url` 指定。
请求一次一个、有固定间隔和抖动，429 按 `Retry-After` 等待，遇到验证页就停下并记为降级，而不是换着法子绕过。
原始记录去掉作者资料和 IP 属地，只留作者 ID 的哈希。

**视频只用文字。** B 站、快手、知乎视频不下载媒体（`--get_media no`），只导入标题、简介或文案；
抖音和小红书需要下载图文帖的图片，MediaCrawler 的同一个开关也会下载视频，Gleaner 不读取它们，打包时也不带。

**输出布局统一成 MediaCrawler 的样子。** 牛客读取器写出 `raw/nowcoder/jsonl/*.jsonl` 和 `raw/nowcoder/media/<ID>/001.jpg`，
所以导入、`gleaner import` 和图片查找对所有平台走同一套代码（`adapters/exports.py`）。

## 数据库

`gleaner.db` 由 `store.py` 管理，`meta.schema_version` 记录版本，打开更新版本创建的工作区会直接拒绝。

| 表 | 内容 |
|---|---|
| `runs` | 每次抓取或导入：来源、模式、参数、状态、降级原因、计数 |
| `posts` | 帖子内容、图片路径（相对工作区）、互动数、标签与理由、打包记录 |
| `checkpoints` | 每个关键词见过的最新发布时间，供以后做增量抓取 |

## 扩展新来源

1. 在 `models.PLATFORMS` 登记平台代码和名称，在 `urls.py` 增加稳定链接的生成和反查。
2. 在 `adapters/` 下把一条原始记录转成 `Post`，`url` 必须是去掉会话参数后的稳定链接；在 `adapters/exports.py` 登记数据目录和图片目录。
3. MediaCrawler 已支持的平台：在 `sources/mediacrawler.PLATFORMS` 登记分页大小和是否下载媒体。
   其他来源：在 `sources/` 下实现 `Fetcher`，按上面的布局写出原始记录，失败时返回 `degraded` 而不是抛异常；
   在 `pipeline.fetcher_for`、`source_name` 里接上。
4. 在 `web/progress.py` 给出步骤和日志标记，在 `web/server.py` 的 `_HINTS` 写一句抓取说明。
5. 测试用合成数据，不提交真实帖子。
