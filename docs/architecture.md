# 架构说明

## 模块

| 模块 | 职责 |
|---|---|
| `cli.py` | 命令行入口；预期内的错误打印一行中文提示，`--json` 输出机器可读结果 |
| `config.py` | 读取并校验 `gleaner.toml`；模板在 `templates/gleaner.toml` |
| `installer.py` | `gleaner setup`：下载 MediaCrawler 并固定版本、检查和打补丁、`uv sync`；下载 Interview Bank、建采集题库；装 faster-whisper、ffmpeg 链接和转写模型；把路径写回 `gleaner.toml`（逐行改，保留注释） |
| `patches/mediacrawler/` | 对 MediaCrawler 的补丁和 `NOTICE`（按 MediaCrawler 的许可证，不按 MIT），随包分发 |
| `sources/base.py` | `Fetcher` 接口（`problems()`、`run()`）与 `FetchResult`；`run()` 接受增量搜索的 `since` 和已保存的帖子 ID `known` |
| `sources/mediacrawler.py` | 按平台拼装 MediaCrawler 命令（分页大小、是否下载媒体、B 站 av 链接换成 BV 链接）、把增量搜索的发布时间范围换成补丁读的环境变量、运行子进程（可把 ffmpeg 所在目录放进 PATH；无人值守时一问二维码就结束子进程）、记录日志、把失败归为“降级”；`awaiting_download` 判断一个录音帖子是否还没下载 |
| `sources/nowcoder.py` | 牛客读取器：翻阅最新面经列表、本地匹配关键词、读详情和图片；增量时跳过已保存的帖子、翻到旧帖就停；限速、重试、识别拦截 |
| `adapters/exports.py` | 各平台导出的数据目录与图片目录对照；读取运行目录里的全部帖子；复制外部导出目录 |
| `adapters/common.py` | 适配器共用：读 jsonl/json、找本地图片和视频、解析“1.2万”这类计数、话题标签 |
| `adapters/<平台>.py` | `xhs`、`douyin`、`bilibili`、`zhihu`、`nowcoder`：把一条原始记录转成 `Post` |
| `urls.py` | 各平台的稳定链接、去掉会话和分享参数、从链接反查帖子键；B 站 av 号和 BV 号互转 |
| `store.py` | SQLite：帖子、标签、运行记录、关键词进度；带 schema 版本和逐版升级 |
| `screening.py` | 可解释的规则初筛；标出 B 站面试录音 |
| `pipeline.py` | 编排：按平台选抓取器 → 抓取 → 导入 → 初筛；按平台计算计划篇数、抓取间隔和增量搜索的起点；挑出要下载的录音 |
| `auto.py` | `gleaner auto`：按顺序增量抓取各平台 → 下载标成录音的 B 站视频 → 打包，每一步的结果记成 `Step`，一步失败不影响下一步；`auto.lock` 保证同一时间只有一次自动运行（手动的 `crawl`、`media`、`pack` 和网页的抓取、打包也看这把锁）；每次结束写 `last-auto.json` 供网页概览显示 |
| `packer.py` | 生成素材包与 `HANDOFF.md`；默认选帖时把还没下载的录音留下（`packable`） |
| `audio.py` | 用 ffmpeg 把录音视频转成单声道、响度均衡的 `audio.flac`；打包器接受替换的提取函数，测试不需要 ffmpeg |
| `bodytext.py` | 识别正文里已经列出的题目，决定读正文还是看图 |
| `checks.py` | 环境检查，`doctor`、`setup` 和网页共用；补丁用 `git apply --reverse --check` 判断是否已打上 |
| `bank.py` | 访问采集题库：调用 Interview Bank 的 `search --format jsonl --detail`、`show`、`taxonomy`、`sources` 读取（按数据文件变化缓存），用 `export` 生成 Markdown 报告；`held()` 给出每个来源链接在题库里有几个来源、几道题 |
| `bank.py` 的 `export_bundle` | `gleaner bundle`：转交 Interview Bank 的 `export-bundle`，生成纯文字题目包，交给另一个题库用 `import-bundle` 去重导入 |
| `glossary.py` | 转写常被听错的术语表（内置 + `[glossary] extra`），写进有录音的素材包的 HANDOFF.md；转写文本留在题库里不改写，由 Agent 对照表改错并写进 `correction` |
| `imports.py` | 把帖子和题库已有的来源按链接对照：手工导入的帖子记成 `disposition = manual`，列出还在等导入的素材包；`--mark/--skip/--clear` 改记录 |
| `devnotes.py` | 进度页的数据：工作区的 `开发进度.md`（或开发手册的「当前状态」）、CHANGELOG 的「未发布」、`git log` 和未提交/未推送数；意见追加到工作区的 `意见.md` |
| `web/server.py` | 标准库 HTTP 服务：只监听本机、Host 校验、写操作令牌、CSP；JSON 接口与静态文件 |
| `web/jobs.py` | 后台抓取任务：同一时间只跑一个，日志按行号增量轮询，可停止（MediaCrawler 结束子进程，牛客在下一个请求前停下） |
| `web/progress.py` | 按平台给出步骤，从日志识别当前步骤和需要人操作的提示（扫码、验证、确认框、牛客限流等待） |
| `web/static/` | 原生 HTML/CSS/JS 前端，无构建步骤 |

依赖方向是单向的：`cli / web → pipeline / packer → sources / adapters / screening → store / models`；`auto` 夹在 `cli` 和 `pipeline / packer` 之间，网页不用它。
网页控制台和命令行调用同一组函数（`crawl`、`fetcher_for`、`build_pack`、`environment_checks`），所以两边的限制和结果一致。
来源相关的细节（命令行参数、接口地址、导出字段）只出现在 `sources/` 和 `adapters/` 里，来源改版时只改这两处
（再加上 `web/progress.py` 里的日志标记）。

## 平台

| 代码 | 平台 | 抓取器 | 运行记录的 `source` | 帖子 ID |
|---|---|---|---|---|
| `xhs` | 小红书 | MediaCrawler | `mediacrawler-xhs` | 笔记 ID |
| `dy` | 抖音 | MediaCrawler | `mediacrawler-dy` | aweme ID |
| `bili` | B站 | MediaCrawler | `mediacrawler-bili` | av 号（数字；BV 链接换算成 av 号） |
| `zhihu` | 知乎 | MediaCrawler | `mediacrawler-zhihu` | `answer-…`、`article-…`、`zvideo-…`（三类 ID 各自编号，会重复） |
| `nowcoder` | 牛客 | `sources/nowcoder.py` | `nowcoder` | 讨论帖数字 ID，或动态的 32 位 UUID（小写） |

抓取间隔和关键词进度都按 `source` 分开：刚抓过小红书，不影响马上抓牛客。
导入已有目录时，只含一个平台的记为该平台的 `source`，含多个平台的记为 `mediacrawler`，参数里列出平台。

## 关键决定

**只调用，不内置 MediaCrawler；随包带的是补丁和安装程序。** 它的许可证是非商业、不可转让的，和本仓库的 MIT 不兼容，
挑出有用的部分并进来也一样（那部分仍受它的许可证约束，整个仓库就不能按 MIT 发布），而且它每个平台的签名、登录和反爬应对
一直在改，并进来就得自己跟着维护。所以 Gleaner 通过子进程调用使用者自己的副本，一次运行一个新的 `--save_data_path`，
这样不用像别的项目那样比较文件修改时间来找出“这次新增的文件”。
“装好就能用”靠 `gleaner setup`：从官方仓库下载、固定到核对过的提交（日志标记和导出字段是按这个版本核对的）、打补丁。
补丁是 `git diff` 生成的普通 diff，按许可证要求附 `NOTICE`；打之前先用 `git apply --reverse --check` 看是否已打上、
`git apply --check` 看能否打上，有一个冲突就一个都不打，不会改到一半。MediaCrawler 只读配置文件，没有对应的命令行参数，
所以增量搜索的时间范围由补丁改成读环境变量（`GLEANER_*`），不设时和原版一样。

**增量搜索按“上次成功搜索”算起点。** 起点是这些关键词在这个平台上次 `ok` 的搜索运行的开始时间里最早的那个，再往前 1 天：
用运行开始时间而不是帖子发布时间，是因为平台的搜索结果不按时间排完，看到的最新帖子不代表更早的都看过了；
往前 1 天覆盖时钟差、平台的索引延迟，以及抖音、知乎只能选固定范围造成的误差。降级的运行不推进起点，
新加的关键词没有起点，这一次就不限时间。各平台能表达的范围不同（见 [MediaCrawler 接入](mediacrawler.md#增量搜索)），
小红书完全不能按时间搜，只能按最新排序。牛客没有搜索，读取器拿到 `since` 后在列表里跳过旧帖，
一整页都早于 `since` 就停止翻页（列表本身按发布时间倒序）；拿到的 `known` 是已保存的帖子 ID，直接跳过不读详情。
按链接抓取和 `--full` 不做增量。

**自动运行不等人，也不留半截。** `gleaner auto` 是给定时任务用的，所以：
一问二维码就结束 MediaCrawler 子进程（`is_login_prompt` 只认 MediaCrawler 登录代码自己的日志，不会被帖子内容误触），
记为降级并写明怎么补救，而不是占着 15 分钟的超时；平台之间互不影响，失败只记在这一步；
下载录音时 B 站两次运行之间照样等够最小间隔，一个下载失败就不再下载后面的，免得对着同一个问题连续请求。
最要紧的是**不重复**：还没下载的录音不能打包——打包只会导入标题，而打过包的帖子不再自动下载，那段录音就永远转写不了——
所以默认选帖时把它们留下（`packer.packable`），`gleaner pack --key` 明确指定时才打。
两次自动运行重叠会把同一批帖子打两次包、让题目重复计数，所以用 `auto.lock`（`O_EXCL` 创建，12 小时后算崩溃遗留）互斥；
没用进程号判断，因为 Windows 上 `os.kill(pid, 0)` 会结束进程。
题库有没有这篇帖子不靠手记，而是向 Interview Bank 的只读 `sources` 命令要来已提交的来源，按链接（`source_url` 就是帖子的稳定链接）对照（`imports.py`）；`auto` 每次先对照再下载和打包。没提交过的来源（例如录音转写了还没抽题）不在列表里，所以算「没导入」。不经素材包、手工导入题库的帖子用 `imports --mark` 记成 `disposition = manual`，决定不导入的用 `--skip` 记成 `skipped`（已打过包的也行，之后不再算「还在等导入」）。`disposition` 是单独的一列，不和 `packed_in`（它在哪个素材包里）混用：先前用 `packed_in` 存特殊值，网页上就会把这些帖子显示成「已打包」，Gleaner 没有别的办法知道它们已经进了题库。

**失败是“降级”，不是崩溃。** 登录过期、滑块验证、超时、退出码非 0、没有产出，都记为 `degraded` 并写明原因；
已经产出的数据照常导入。退出码 3 让脚本能区分降级和真正的错误。

**帖子去重和题目去重分开。** Gleaner 只按“平台 + 帖子 ID”去重帖子；题目级的同义合并交给 Interview Bank。
同一篇帖子再次抓到时只合并关键词、刷新互动数；正文变化才算更新。打包后被作者修改的帖子会标记出来，
但不会自动重新打包，否则同一篇的题会在题库里被计两次。

**标签只用来排序和挑选，不删除。** 规则标签（`label_source = rule`）在正文变化或 `screen --all` 时会重判；
人工标签（`manual`）永远不被覆盖。每个标签都带命中理由，例如 `event:一面`、`promo:训练营`。
“ai面”用正则识别为面试事件（后面紧跟“试”的不算，免得“AI面试题合集”被当成面经）。
除了词表，正文里出现 5 个以上不同的外部域名也判为广告（SEO 引流帖会堆一串无关域名）；
github、leetcode、各平台自身等常见域名和邮箱地址不计数。
“评论区扣「面试」”这类让读者留言领资料的说法是强广告信号，一次命中就判为广告；只是“在评论区交流”不算。
知乎卖课文章结尾的“点击打开下方小卡片无偿领取……全套资料”同理：“无偿领取”“免费领取”“下方小卡片”都是强广告词；把“发你”写成“fa你”这种躲关键词的写法也是。
正文够不够长（40 字）不算话题标签：抖音文案常是一句标题加一串 `#面经 #后端面试`，标签凑出的长度不算有内容，
这类帖子没有“一面”之类的面试轮次时判为拿不准。

**一篇帖子只走一个入口，能读正文就不看图。** 两个入口都走会让同一道题在采集题库里出现两次。
正文已经列出题目（至少 3 道且不少于图片张数，规则在 `bodytext.py`）时走 `web-intake`，图片不打包；
否则有图就走 `ingest images`。看图比读正文贵得多，而第一次试导入的 5 篇里有 4 篇的图只是正文的排版。
题数门槛和“不少于图片张数”保证误判只会多看图、不会漏题。视频帖的视频不进素材包，只导入正文；
下载过的 B 站录音例外，走第三个入口 `media`（见下面的“只转写面试录音”）。
带 `#牛客AI配图神器#` 的帖子有一张图是牛客按正文生成的思维导图，计算图片张数时减去这一张：
只有这一张图的帖子读正文；另有截图的照常按剩下的张数判断，所以不会因此漏掉截图里的题。

**发布日期不是面试日期。** 素材包只提供 `source_date`（按北京时间换算的发布日期）。
面试日期、公司、轮次需要 Interview Bank 在原文里找到依据才填写。

**题库页面和导入对照只通过 Interview Bank 的公开命令读取。** Gleaner 不直接解析题库的 JSONL，这样题库升级格式、
加锁或校验规则变化时，页面不会读到半写入的数据，也不需要跟着改。题目列表按 `data/` 下文件的修改时间缓存；
来源截图只按“64 位哈希 + 图片后缀”的文件名从题库 `media/` 目录提供。
Interview Bank 认不出公司时记成 `id` 为空的 `unknown`，列表不把它当公司；出现记录按 `source_type` 标出正文、截图或录音转写。
导出同样交给 `ibank.py export`，报告只写进题库的 `exports/`（Interview Bank 自己也只允许这里）；
预览和下载只接受该目录下的 `.md` 文件名，不接受路径。前端的 Markdown 渲染只覆盖导出会用到的语法，
内容先转义再加标签，只认 `<details><summary>` 这一种 HTML。

**会话参数不离开本机。** 小红书链接里的 `xsec_token` 和登录会话有关。`url` 字段去掉了它和其他分享参数，
用于去重和交接；原样的 `open_url` 只保存在本机数据库里，方便自己打开原帖。

**牛客不用站内搜索。** 牛客的 robots.txt 不允许 `/search`。读取器翻阅最新面经列表、在标题和摘要里本地匹配关键词，
只读取命中的帖子详情。代价是只能找到最近的帖子，适合经常小批量抓；旧帖子用 `--url` 指定。
请求一次一个、有固定间隔和抖动，429 按 `Retry-After` 等待，遇到验证页就停下并记为降级，而不是换着法子绕过。
原始记录去掉作者资料和 IP 属地，只留作者 ID 的哈希。

**视频默认只用文字，只转写面试录音。** 搜索时 B 站、知乎视频不下载媒体（`--get_media no`），只导入标题、简介或文案；
抖音和小红书需要下载图文帖的图片，MediaCrawler 的同一个开关也会下载视频，Gleaner 不读取它们，打包时也不带。
按工作区里已有的视频评估，值得转写的是 B 站上候选人自己录的真实面试：抖音的视频是一题一讲加资料引流，
小红书的是广告；B 站 19 个视频里从标题看有 4 个是录音，其余是复盘、讲题或课程。试转的两段录音导入了 33 道题。所以：

- 初筛只在视频的标题和简介里找“录音”“实录”“面试全程”“真实面试”这类词，命中记 `recording:<词>` 并算作面试证据；
  只有这些视频（或者手动指定的 B 站视频）才下载音视频，每次一个 detail 运行，不在搜索时全局打开 `--get_media`。
- 下载走 MediaCrawler 的 detail 模式，链接先换成 BV 号（它不认 av 链接）；B 站的画面和声音分开下载，
  需要 ffmpeg 合成，所以 ffmpeg 所在目录会放到子进程的 PATH 前面；下载比搜索慢得多，超时单独设置（默认 1 小时）。
- 打包时用 ffmpeg 提取 16 kHz 单声道音频并做动态响度均衡（`dynaudnorm`）。远程面试录的是候选人这边，
  面试官的声音常常很小，Interview Bank 的转写开着静音检测（VAD），不均衡的话面试官的问题会被当成静音丢掉。
  视频本身不进素材包，只放 `audio.flac`。
- 转写交给 Interview Bank（`media` → `media-transcribe` → `media-task`），Gleaner 不装 faster-whisper；
  试点里 `large-v3-turbo` 在 CPU 上用录音时长的 0.16–0.37 倍，不比 `small`（约 0.4 倍）慢，专业词明显更准。
  转写仍会听错专业词、漏掉面试官的话，
  HANDOFF.md 要求逐题改正并写 `correction`，从回答推断的题要降低置信度并说明。

**输出布局统一成 MediaCrawler 的样子。** 牛客读取器写出 `raw/nowcoder/jsonl/*.jsonl` 和 `raw/nowcoder/media/<ID>/001.jpg`，
所以导入、`gleaner import` 和图片查找对所有平台走同一套代码（`adapters/exports.py`）。

## 数据库

`gleaner.db` 由 `store.py` 管理，`meta.schema_version` 记录版本，打开更新版本创建的工作区会直接拒绝；
旧版本的工作区打开时按 `_UPGRADES` 逐版升级（第 2 版给 `posts` 加了 `media` 列；第 3 版加了 `disposition` 列，并把 GLN-27/30 用 `packed_in` 的特殊值记的“手工导入”“不导入”挪过去）。

| 表 | 内容 |
|---|---|
| `runs` | 每次抓取或导入：来源、模式、参数（下载录音的运行带 `media: true`；搜索带 `incremental` 和 `since`）、状态、降级原因、计数；增量搜索的起点从这里算 |
| `posts` | 帖子内容、图片和录音视频的路径（相对工作区）、互动数、标签与理由、打包记录（`packed_in`）、`disposition`（手工导入或不导入） |
| `checkpoints` | 每个关键词见过的最新发布时间，供以后做增量抓取 |

## 扩展新来源

1. 在 `models.PLATFORMS` 登记平台代码和名称，在 `urls.py` 增加稳定链接的生成和反查。
2. 在 `adapters/` 下把一条原始记录转成 `Post`，`url` 必须是去掉会话参数后的稳定链接；在 `adapters/exports.py` 登记数据目录和图片目录。
3. MediaCrawler 已支持的平台：在 `sources/mediacrawler.PLATFORMS` 登记分页大小、是否下载媒体、能不能下载录音；
   能按发布时间搜索的，在 `search_window` 里换成环境变量，并在补丁 `04` 里让 MediaCrawler 读它。
   其他来源：在 `sources/` 下实现 `Fetcher`，按上面的布局写出原始记录，失败时返回 `degraded` 而不是抛异常；
   在 `pipeline.fetcher_for`、`source_name` 里接上。
4. 在 `web/progress.py` 给出步骤和日志标记，在 `web/server.py` 的 `_HINTS` 写一句抓取说明。
5. 测试用合成数据，不提交真实帖子。
