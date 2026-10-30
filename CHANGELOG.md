# 更新记录

## 未发布

- `gleaner bundle` 的题目包带上合并过的问法（采集题库里被并进别的题的不同写法），导入后阅读器里能看到「合并过的问法」。
- 新命令 `gleaner bundle`：向 Interview Bank 新增的 `export-bundle` 要来采集题库的纯文字题目包（题目、公司、岗位、轮次、
  实习或正式、时间、来源链接；没有图片、答案和转写），用来补充线上的个人面经；导入和去重用 Interview Bank 的 `import-bundle`；加 `--fill-event-date month` 让没有面试日期的出现用帖子的发布日期（月）
  顶上，方便按时间归档（近似值，精确的发布日期留在来源上）。
- 整理（GLN-31）：
  - 数据库升到第 3 版：「手工导入」「不导入」不再用 `packed_in` 存特殊值，改放单独的 `disposition` 列（升级时自动搬过去）；
    网页帖子页对它们显示「手工导入」「不导入」，不再显示「已打包」。
  - `mark-imported`、`skip-import` 并进 `gleaner imports`：`--mark`、`--skip`、`--clear`（`--dry-run` 只列出）。
    去掉 `crawl --media`，下载录音只剩 `gleaner media`（和 `auto`、网页按钮）。
  - 手动的 `crawl`、`media`、`pack` 和网页的抓取、打包遇到 `gleaner auto` 正在运行会拒绝（共用 `auto.lock`）。
    `auto` 每次结束写 `workspace/last-auto.json`，网页概览显示「最近一次自动运行」。
  - `doctor` 检查 Interview Bank 有没有 Gleaner 用到的 10 个命令；README 补了云端服务器定时运行要注意的事。
- 转写术语表：新命令 `gleaner glossary` 列出转写录音时常被听错的术语（Lilis→Redis、县城→线程、麦瑟克→MySQL、
  换度→幻读 等 40 多条，取自已导入的录音），`gleaner.toml` 的 `[glossary] extra = ["听成的写法 = 正确写法"]` 追加自己的。
  有录音的素材包的 HANDOFF.md 附上这张表，Agent 改错时对照（转写文本在题库里，不改写）。
- 新命令 `gleaner skip-import <帖子键…>`：记下不打算导入题库的帖子（已打过包的也行），`imports`、`auto` 和网页素材包页
  不再把它们算作「还在等导入」。
- 素材包有没有导入题库不用再手记：新命令 `gleaner imports`（`--dry-run`）向 Interview Bank 新增的只读 `sources` 命令
  要来题库已提交的来源，按帖子链接对照。没经过素材包的手工导入帖子自动记成「手工导入」（不会再打包或下载录音），
  还没进题库的素材包逐个列出；`gleaner auto` 每次运行先对照再下载和打包，最后提醒还有几个素材包在等导入。
  网页素材包页每个包标出「已全部导入」或「待导入 N 篇」。需要装有 `sources` 命令的 Interview Bank，没有时这一步被跳过。
- 小修：网页概览的「待打包面经」不再把还没下载的录音算进去；`doctor` 检查 `[auto] platforms` 里的平台名；
  命令行的自动运行结果文字移到 `auto.py`；去掉只有测试在用的 `SOURCE_XHS`。
- 新命令 `gleaner auto`：一条命令走完 增量抓取各平台 → 下载标成录音的 B 站视频 → 打包，适合交给系统的定时任务
  （README 有 cron 和 Windows 计划任务的写法）。
  - 平台按配置的 `[auto] platforms`（默认全部，牛客最先）逐个抓；刚抓过的平台跳过，降级的平台不影响其余；
    最多下载 `[auto] max_recordings`（默认 2）个录音，两次 B 站运行之间等够最小间隔，一个失败就不再下载后面的。
  - 不等人扫码：MediaCrawler 一问二维码就结束子进程，记为降级并提示手动 `gleaner crawl <平台>` 扫一次；`--login` 才会等。
  - 同一工作区同一时间只能有一次自动运行（`workspace/auto.lock`，12 小时后算崩溃遗留）。
  - `--dry-run` 只列出计划，`--platform`、`--no-media`、`--no-pack`、`--full`、`--force`；退出码 0 全部正常或跳过，3 有降级。
- 打包不再带上还没下载的录音：这种帖子打包后只会导入标题，又因为已经打过包而不会再自动下载。默认选帖时留下它们，
  `pack` 提示还有几个等下载；`gleaner pack --key` 明确指定时照打。
- 新命令 `gleaner mark-imported <帖子键…>`：记下已经手工导入题库的帖子，以后不会再打包，也不会再自动下载它的录音。
- 新命令 `gleaner setup [mediacrawler|bank|media] [--no-model]`，一条命令装好其余依赖（可重复运行，装好的跳过）：
  - `mediacrawler`：从官方仓库下载 MediaCrawler 并固定到核对过的 `68d9ef1`，打上 Gleaner 的补丁，`uv sync`；
    没有 Chrome 或 Edge 时装 Playwright 的 Chromium。补丁和本机改动冲突时一个都不打，提示先 `git stash`。
  - `bank`：下载 Interview Bank、新建采集题库；配置的 Python 低于 3.10（例如 macOS 自带的 3.9）时改用运行 Gleaner 的 Python。
  - `media`：在 `workspace/asr-venv/` 装 `faster-whisper==1.2.1`；没有 ffmpeg 时装 `imageio-ffmpeg` 并链接成 `workspace/bin/ffmpeg`；
    下载转写模型到采集题库的缓存。
  - 装到的路径写回 `gleaner.toml`（相对路径，保留注释），能用的配置不改。`init` 的提示改为指向 `gleaner setup`。
- MediaCrawler 补丁随包分发（`src/gleaner/patches/mediacrawler/`，附 `NOTICE`，按 MediaCrawler 的许可证）：
  独立窗口登录和小红书按时间排序（原来要手改配置）、抖音首页等页面稳定、B 站等二维码，以及新的搜索发布时间范围。
  `docs/mediacrawler.md` 里手工改代码的说明换成了 `gleaner setup mediacrawler`。
- `doctor` 新增补丁检查，导入用 Python 的检查会确认版本是 3.10+（之前只看找不找得到）。
- 网页题库：出现记录标出平台和来源类型（正文、截图、录音转写、字幕）；Interview Bank 记为 `unknown` 的公司不再显示成公司标签。
  概览的「关键词进度」标出平台，同一关键词在不同平台上不再显示成几行一样的。
- README 新增「效果展示」（真实的题库截图，放在 `docs/images/`）和「从抓取到题库：哪些是自动的」。
- 增量抓取：按关键词搜索时只找上次成功搜索之后（往前多留 1 天）发布的帖子。牛客跳过已保存的帖子，翻到整页都是旧帖就停；
  B 站按发布时间精确搜索；抖音、知乎取能覆盖的最小固定范围；小红书照常按最新排序。新关键词、降级的上次搜索不算数。
  抓取开始时打印这次的范围，运行参数记 `incremental` 和 `since`。`crawl --full` 和网页抓取页「高级」里的「不做增量」关掉它。
  抖音的 `PUBLISH_TIME_TYPE` 不再需要手改（之前文档建议改成 `7`）。
- B 站面试录音转写：
  - 初筛在视频的标题和简介里找“录音”“实录”“面试全程”“真实面试”等词（`[screening] recording_terms` 可补充），
    命中记 `recording:<词>` 并算作面试证据。已有标签需要 `gleaner screen --all` 才会重判。
  - 新命令 `gleaner media [帖子键…]`（`--dry-run` 只列出）下载标成录音、还没打包的 B 站视频；
    `crawl bili --url … --media` 下载指定视频。网页帖子详情有“下载音视频”按钮，抓取页指定链接时可以勾选下载音视频。
    下载通过 MediaCrawler 的 detail 模式和 `--get_media yes`，ffmpeg 所在目录放进子进程 PATH，超时用新的
    `[media] download_timeout_seconds`（默认 1 小时）；没拿到全部视频时记为降级。
  - 打包：下载过的录音走新的 `media` 入口，用 ffmpeg 提取 16 kHz 单声道、动态响度均衡的 `audio.flac`
    （远程面试里面试官的声音常常很小）；HANDOFF.md 给出 Interview Bank 的 `media`、`media-transcribe`
    （`[handoff] asr_model`，默认 `large-v3-turbo`，用 `[handoff] asr_python` 运行）和 `media-task` 命令，
    并要求改正听错的专业词、推断出的题降低置信度、不收自我介绍和反问。Interview Bank 每个题库各自缓存模型，
    第一次转写要下载约 1.5 GB。
  - `doctor` 新增 ffmpeg 和 `asr_python` 两项检查；数据库升到第 2 版（帖子记录视频路径），打开旧工作区时自动升级。
- B 站：`crawl bili --url` 可以用 av 链接或 av 号了。MediaCrawler 只认 BV 号，之前会报 `Unable to parse video ID`；
  BV 链接也能反查到帖子键。
- 初筛：正文长度不算话题标签，抖音“一句标题 + 一串 #面经 #后端面试”的单题视频没有面试轮次时判为「拿不准」，不再算疑似真实面经；
  “fa你”（谐音躲“发你”）判为强广告词。已有标签需要 `gleaner screen --all` 才会重判。抖音第一次真实试抓的结果写进开发手册。
- 不再支持快手：试抓两个关键词的 40 个视频全是无关内容或引流，搜不到互联网方向的真实面经。
  `crawl ks` 和快手链接识别一并去掉，`import` 会跳过 MediaCrawler data 目录里的 `kuaishou/`。
- 网页抓取页的扫码倒计时统一按 10 分钟：MediaCrawler 每个平台都每秒检查一次登录、共 600 次，
  日志里的“remaining time is 20s / 120s”只是写死的文字（之前 B 站显示 20 秒倒计时，倒数完其实还能扫码）；
  抖音的扫码提示也有了倒计时。B 站第一次真实试抓的结果和 B 站登录框的 MediaCrawler 补丁写进 `docs/mediacrawler.md`。
- 知乎（第一次真实试抓后修正）：标题和正文里的 HTML 实体（`&#34;` 等）解码后再保存；
  “无偿领取”“免费领取”“下方小卡片”判为强广告词，知乎卖课文章结尾的“点击打开下方小卡片无偿领取……全套资料”
  一次命中就判为「广告/卖课/引流」。已有帖子需要重新 `import` 那次的 raw 目录和 `gleaner screen --all`。
- 抓取失败的提示能认出 Playwright 不带前缀的 `Error: …` 了（之前退回成日志最后 300 个字符，常从半个路径开始）；
  抖音启动时的 `Execution context was destroyed` 会提示给 MediaCrawler 打补丁，补丁见 `docs/mediacrawler.md`。
- 打包：带 `#牛客AI配图神器#` 的牛客帖子，图片张数先减去牛客按正文生成的那张思维导图，
  只有这一张图的帖子改为读正文导入（之前会被当成看图帖，图里却没有正文以外的内容）。
- 初筛：“评论区扣「面试」”这类让读者留言领资料的说法判为「广告/卖课/引流」。已有标签需要 `gleaner screen --all` 才会重判。
- 网页控制台新增「进度」页：显示本机开发记录（工作区的 `开发进度.md`，没有就显示开发手册的「当前状态」）、
  CHANGELOG 里还没发布的改动、最近的提交和未提交/未推送的数量；可以按页面写意见，追加保存到工作区的 `意见.md`。
  概览和进度页每 30 秒自动刷新。Markdown 预览把列表项的缩进续行接回同一项（HANDOFF.md 预览同样受益）。
- 牛客：讨论帖正文里直接写的换行（`\n`，页面按原样显示）不再被压成空格。此前这类帖子的正文成了一整行，
  列出的题目数不出来（`body_questions` 为 0），交给题库的正文也不分行。
- 牛客：动态正文里的话题链接（`<a href="/creation/subject/…">#秋招面试记录#</a>`）只保留文字；
  翻阅日志的“累计命中”改为去重后的篇数，并列出每个关键词各命中几篇。
- 初筛：“AI 面”（AI 面试官主持的一轮）算作面试事件；“AI面试题”这类标题不算。
- Windows：调用 Interview Bank 时强制 UTF-8 输出，题库页不再因为中文输出报 `UnicodeEncodeError`；
  网页控制台的静态文件和图片按固定的后缀表给出类型，不再依赖系统注册表（`.webp` 会被当成二进制下载）。
- 开发手册 `docs/development.md`：在新电脑上搭环境、迁移工作区和采集题库、提交流程与编号、代码约定、调试方法、
  当前状态与待办；根目录 `AGENTS.md` 给 Agent 指路。提交信息检查随仓库提供（`.githooks/commit-msg`），
  CI 的 lint 也检查前端脚本语法。
- 初筛：正文堆了 5 个以上不同外部域名的帖子判为「广告/卖课/引流」，理由里写出域名数和前几个域名；
  github、leetcode、各平台自身等常见域名和邮箱地址不计数。已有标签需要 `gleaner screen --all` 才会重判。
- 新平台：`crawl dy|bili|zhihu` 通过 MediaCrawler 抓取抖音、B 站、知乎。按平台设置搜索分页
  （抖音每页 10 篇，其余 20 篇）；只有小红书和抖音下载媒体（图文帖的图片），B 站、知乎只导入文字。
  `import` 一次导入 MediaCrawler data 目录下所有平台的数据，并处理抖音数据目录和图片目录不同名的情况。
- 新平台：`crawl nowcoder` 由 Gleaner 直接读取牛客，不需要 MediaCrawler 和登录。遵守 robots.txt 不用站内搜索，
  翻阅最新面经列表并在标题和摘要里匹配关键词，读取讨论帖和动态的正文与图片；一次一个请求、间隔至少 3 秒，
  429 按 `Retry-After` 重试，连续限流或遇到验证页就停下记为降级，已保存的帖子照常导入；不保存作者资料和 IP 属地。
  新增配置节 `[nowcoder]`，说明见 `docs/nowcoder.md`。
- 抓取间隔和关键词进度按平台分开计算；运行记录带上平台。`list --platform` 按平台筛选，`stats` 显示各平台帖子数。
- 网页控制台：抓取页可选平台，篇数选项、提示和步骤条随平台变化（牛客是 翻阅面经列表 → 读取帖子 → 下载图片），
  牛客限流等待时会提示；帖子页可按平台筛选，卡片和详情显示平台；概览显示各平台帖子数，最近抓取带平台标记。
- 题库页按来源链接找回 Gleaner 帖子时支持所有平台的链接。
- `gleaner web`：本机网页控制台。概览统计与环境检查；可视化抓取（步骤条、扫码/验证码/确认框提示与倒计时、
  实时日志、停止）；帖子浏览（封面、标签、读正文还是看图、详情抽屉、一键改标签）；勾选打包与查看 HANDOFF.md。
  只监听 127.0.0.1，校验 Host，写操作需要页面令牌，启用严格 CSP；纯标准库，前端无构建步骤。
- 网页控制台新增「题库」：只读浏览采集题库里整理好的题目，按领域分组、按出现次数排序，可按公司和领域筛选；
  题目详情含参考答案、每次出现的原文与公司轮次、来源截图、原帖与 Gleaner 帖子链接、合并问法和相关题目。
- 题库页可按当前筛选导出 Markdown 报告（答案版 + 题目版，写入采集题库 `exports/`），并在线预览：
  表格、折叠块、目录跳转、两版切换、下载与复制。题目详情的参考答案按行显示要点，补充常见追问、口述版和考察意图。
- `pack`：正文已经列出题目（至少 3 道且不少于图片张数）的帖子改为读正文导入，图片不再打包，
  manifest 记录 `body_questions` 与 `skipped_images`。备考总结、讲解小标题这类带编号或问号的非题目行不计入。
- `HANDOFF.md`：明确不收没有技术考点的问题，包括自我介绍、经历确认、实习时长和到岗时间、反问环节，
  以及使用心得、学习方式、自我评价。
- 目录布局：MediaCrawler、采集题库和工作区默认都放在仓库目录下（`MediaCrawler/`、`collected-bank/`、
  `workspace/`，均被 git 忽略）；新工作区的配置用相对路径指向它们，整个目录挪动后不用改配置。
- 文档补充推荐的独立窗口扫码登录方式（`CDP_CONNECT_EXISTING = False`）。

## 0.1.0 — 2026-10-27

第一个版本：小红书面经采集、初筛和打包。

- `crawl xhs`：调用本机 MediaCrawler 按关键词搜索或抓取指定帖子；每次一个独立输出目录，日志同时写入文件和终端；
  超时、登录失效、没有产出都记为降级，部分产出照常导入；降级提示给出日志里真正的错误原因
  （例如小红书风控返回的“账号没有权限访问”），而不是调用栈末尾。搜索按小红书的分页抓取：每个关键词默认一页
  20 篇、上限两页，抓取前提示本次最多篇数；并发 1、不抓评论、两次抓取至少间隔 5 分钟。
- `import`：导入已有的 MediaCrawler 输出目录，只复制帖子记录和图片。
- 工作区 SQLite：帖子按“平台 + 帖子 ID”去重，正文变化才算更新；记录每个关键词的最新发布时间；
  打包后被修改的帖子会标记但不重复打包。
- `screen`：可解释的规则初筛（疑似真实面经、题目合集、广告卖课、无关、拿不准），只打标签不删除；
  人工标签不会被覆盖；词表可在配置里追加。“面试官”不算面试场次的证据，避免“高频考点汇总”被当成面经。
- `pack`：生成交给 Interview Bank 的素材包，链接去掉会话参数；`HANDOFF.md` 带有导入独立采集题库的现成命令。
- `init`、`doctor`、`list`、`show`、`label`、`stats`、`runs`，全部支持 `--json`。
