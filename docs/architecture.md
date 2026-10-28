# 架构说明

## 模块

| 模块 | 职责 |
|---|---|
| `cli.py` | 命令行入口；预期内的错误打印一行中文提示，`--json` 输出机器可读结果 |
| `config.py` | 读取并校验 `gleaner.toml`；模板在 `templates/gleaner.toml` |
| `sources/mediacrawler.py` | 拼装 MediaCrawler 命令、运行子进程、记录日志、把失败归为“降级” |
| `adapters/xhs.py` | 把 MediaCrawler 小红书导出转成 `Post`；复制外部导出目录 |
| `store.py` | SQLite：帖子、标签、运行记录、关键词进度；带 schema 版本 |
| `screening.py` | 可解释的规则初筛 |
| `pipeline.py` | 编排：抓取 → 导入 → 初筛；抓取间隔限制 |
| `packer.py` | 生成素材包与 `HANDOFF.md` |
| `bodytext.py` | 识别正文里已经列出的题目，决定读正文还是看图 |
| `checks.py` | 环境检查，`doctor` 和网页共用 |
| `web/server.py` | 标准库 HTTP 服务：只监听本机、Host 校验、写操作令牌、CSP；JSON 接口与静态文件 |
| `web/jobs.py` | 后台抓取任务：同一时间只跑一个，日志按行号增量轮询，可停止 |
| `web/progress.py` | 从 MediaCrawler 日志识别当前步骤和需要人操作的提示（扫码、验证、确认框） |
| `web/static/` | 原生 HTML/CSS/JS 前端，无构建步骤 |

依赖方向是单向的：`cli / web → pipeline / packer → sources / adapters / screening → store / models`。
网页控制台和命令行调用同一组函数（`crawl_xhs`、`build_pack`、`environment_checks`），所以两边的限制和结果一致。
来源相关的细节（命令行参数、导出字段）只出现在 `sources/` 和 `adapters/` 里，MediaCrawler 改版时只改这两处。

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

**一篇帖子只走一个入口，能读正文就不看图。** 两个入口都走会让同一道题在采集题库里出现两次。
正文已经列出题目（至少 3 道且不少于图片张数，规则在 `bodytext.py`）时走 `web-intake`，图片不打包；
否则有图就走 `ingest images`。看图比读正文贵得多，而第一次试导入的 5 篇里有 4 篇的图只是正文的排版。
题数门槛和“不少于图片张数”保证误判只会多看图、不会漏题。视频帖的视频不进素材包，只导入正文。

**发布日期不是面试日期。** 素材包只提供 `source_date`（按北京时间换算的发布日期）。
面试日期、公司、轮次需要 Interview Bank 在原文里找到依据才填写。

**会话参数不离开本机。** 小红书链接里的 `xsec_token` 和登录会话有关。`url` 字段去掉了它和其他分享参数，
用于去重和交接；原样的 `open_url` 只保存在本机数据库里，方便自己打开原帖。

## 数据库

`gleaner.db` 由 `store.py` 管理，`meta.schema_version` 记录版本，打开更新版本创建的工作区会直接拒绝。

| 表 | 内容 |
|---|---|
| `runs` | 每次抓取或导入：来源、模式、参数、状态、降级原因、计数 |
| `posts` | 帖子内容、图片路径（相对工作区）、互动数、标签与理由、打包记录 |
| `checkpoints` | 每个关键词见过的最新发布时间，供以后做增量抓取 |

## 扩展新来源

1. 在 `sources/` 下实现抓取，返回 `FetchResult`，失败时返回 `degraded` 而不是抛异常。
2. 在 `adapters/` 下把原始输出转成 `Post`，`url` 必须是去掉会话参数后的稳定链接。
3. 在 `models.PLATFORMS` 登记平台名称。
4. 在 `pipeline.py` 增加编排函数，在 `cli.py` 增加子命令；测试用合成数据，不提交真实帖子。
