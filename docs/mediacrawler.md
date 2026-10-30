# MediaCrawler 接入

Gleaner 调用你本机的 [MediaCrawler](https://github.com/NanmiCoder/MediaCrawler) 抓取小红书、抖音、B 站和知乎，
自己不包含它的代码，只带几个补丁。牛客不经过 MediaCrawler，见 [牛客读取](nowcoder.md)。
以下内容按 2026 年 10 月的 MediaCrawler main 分支（`68d9ef1`）核对。

## 安装

```bash
gleaner setup mediacrawler
```

它做的事（可以重复运行，做过的会跳过）：

1. 没有 `MediaCrawler/` 时用 git 从官方仓库下载到仓库目录（工作区旁边，已被 git 忽略），并切到验证过的版本
   （`src/gleaner/installer.py` 的 `MEDIACRAWLER_COMMIT`）。已经有一份时不动它，版本不同只提示。
2. 打上 `src/gleaner/patches/mediacrawler/` 里的补丁（见下文）。打上之前先逐个检查：已经打过的跳过，
   和你本机的改动冲突时停下，什么都不改，提示先在 MediaCrawler 里 `git stash`。
3. `uv sync` 装依赖（需要先装 [uv](https://docs.astral.sh/uv/)）。
4. 找 Chrome 或 Edge；都没有时 `uv run playwright install chromium`，MediaCrawler 会退回标准模式用它。

`gleaner doctor` 会检查补丁是否都打上了。想自己装也可以：`git clone` 到 `MediaCrawler/`、
在里面 `git apply <补丁>`、`uv sync`，效果一样。

MediaCrawler 用 CDP 模式驱动本机的 Chrome 或 Edge。补丁把它设成**独立窗口**登录：运行时 MediaCrawler 自己打开一个窗口，
显示对应平台的二维码，用手机上的小红书、抖音、哔哩哔哩或知乎扫码；遇到验证码或滑块就在这个窗口里完成。
登录状态按平台保存在 `MediaCrawler/browser_data/`，和你日常用的浏览器完全隔离，方便使用小号；登录没过期时下次不用再扫码。
每个平台第一次抓取都要各扫一次。想改成连接日常 Chrome，把 `config/base_config.py` 的 `CDP_CONNECT_EXISTING` 改回 `True`，
在 Chrome 地址栏打开 `chrome://inspect/#remote-debugging`，勾选 “Allow remote debugging for this browser instance”，
运行时 Chrome 会弹出确认框，60 秒内点“接受”。

## 补丁

Gleaner 不分发 MediaCrawler，只分发对它的几处修改。MediaCrawler 使用“非商业学习许可证 1.1”，
允许非商业目的的修改，要求保留它的版权和许可声明，所以补丁放在单独的目录里，附 `NOTICE` 说明，不按 Gleaner 的 MIT 许可证授权。

| 补丁 | 改了什么 | 为什么 |
|---|---|---|
| `01-config.patch` | `CDP_CONNECT_EXISTING = False`；小红书 `SORT_TYPE = "time_descending"` | 独立窗口登录；小红书默认按热度排序，按时间排序才适合增量抓取 |
| `02-douyin-wait-page.patch` | 抖音打开首页后等页面稳定再读 `navigator.userAgent` | 抖音首页写入 cookie 后会再刷新一次，不等就每次都在登录前报 `Execution context was destroyed` |
| `03-bilibili-qrcode.patch` | B 站点开登录框后等二维码图片填上 | 原来只等 1 秒，读到空图片，日志报 `login failed , have not found qrcode` 并以退出码 0 结束 |
| `04-search-time-range.patch` | 抖音、知乎、B 站的搜索发布时间范围改由环境变量控制 | 增量抓取（见下文）；不设环境变量时和原来一样不限时间 |

`04` 读的环境变量由 Gleaner 在增量搜索时设置，自己运行 MediaCrawler 时不用管：

| 变量 | 平台 | 取值 |
|---|---|---|
| `GLEANER_DY_PUBLISH_TIME` | 抖音 | `0` 不限、`1` 一天内、`7` 一周内、`180` 半年内 |
| `GLEANER_ZHIHU_SEARCH_TIME` | 知乎 | 空（不限）、`a_day`、`a_week`、`a_month`、`three_months`、`half_a_year`、`a_year` |
| `GLEANER_BILI_PUBTIME_BEGIN` / `GLEANER_BILI_PUBTIME_END` | B 站 | 发布时间范围的开始和结束，Unix 秒；`0` 不限 |

MediaCrawler 固定在验证过的版本（不在任何分支上，`git pull` 不起作用）。Gleaner 更新这个版本后，`gleaner setup` 会提示版本不同；
这时在 MediaCrawler 里 `git stash && git fetch && git checkout <新版本>`（`git stash` 收起旧补丁和你的改动），
再运行 `gleaner setup mediacrawler` 打上新补丁。

## 增量搜索

按关键词搜索时，Gleaner 取这些关键词在这个平台上次成功搜索的开始时间，往前多留 1 天，只要之后发布的帖子：

- **B 站**：搜索接口的 `pubtime_begin_s` / `pubtime_end_s`，精确到秒（结束时间多给 1 天，避免时钟差）。
- **抖音**：`PUBLISH_TIME_TYPE` 只有一天内、一周内、半年内，取能覆盖的最小范围；超过半年不限时间。
- **知乎**：`search_time` 有一天内到一年内六档，同样取最小范围；超过一年不限时间。
- **小红书**：搜索没有发布时间参数，只能按最新排序（补丁 `01`）照常抓一页，已有的帖子只更新。

有一个关键词从没成功搜过、或者用了 `--full` 时不限时间。降级的搜索（例如登录过期没抓到）不算“上次成功搜索”。

## 命令行参数

Gleaner 每次运行都会通过命令行强制以下参数，不受你的配置影响：

```text
--platform xhs|dy|bili|zhihu --type search|detail --save_data_option jsonl --save_data_path <工作区>/runs/<run-id>/raw
--get_comment no --get_sub_comment no --get_media yes|no
--crawler_max_notes_count <max_notes> --max_concurrency_num 1
```

`config/` 里另外几项保持 MediaCrawler 的默认就好：`BILI_SEARCH_MODE = "normal"`（其他模式按日期逐天搜索，请求多得多）、
`CRAWLER_MAX_SLEEP_SEC` 2 或更大（请求间隔）、`ENABLE_CDP_MODE = True`。

`--get_media` 只对有图文帖的平台打开：

| 平台 | `--get_media` | 搜索每页 | 数据目录 / 图片目录 | 说明 |
|---|---|---|---|---|
| 小红书 | `yes` | 20 | `xhs/` / `xhs/media/` | 很多面经的题只在图片里；视频帖会下载封面和视频，Gleaner 只用正文 |
| 抖音 | `yes` | 10 | `douyin/` / `dy/media/` | 图集下载全部图片；视频帖 MediaCrawler 会下载封面和整段视频（一条几十 MB），Gleaner 只用文案；MediaCrawler 给图集的链接也是 `/video/<ID>`，Gleaner 把 `/note/<ID>` 认作同一篇 |
| B 站 | `no`（下载录音时 `yes`） | 20 | `bili/` / `bili/media/<BV 号>/` | 只有标题和简介（MediaCrawler 截到 500 字；空简介是 `-`）；真实面试录音的题都在声音里，简介很少列题，见下文「B 站面试录音」 |
| 知乎 | `no` | 20 | `zhihu/` | 回答、文章的纯文字正文（标签已去掉，段落会连在一起；`&#34;` 这类实体由 Gleaner 解码）；视频只有标题和简介，其他类型会跳过；作者昵称被 MediaCrawler 打码 |

MediaCrawler 的搜索按整页抓：小于一页的 `max_notes` 会提高到一页，其余按整页向下取整，
所以抖音选 25 篇实际是 20 篇。`--url` 指定帖子时，各平台接受的格式和 MediaCrawler 的 `config/<平台>_config.py` 一致：
小红书要带 `xsec_token` 的完整链接；抖音是视频或图文链接；B 站是视频链接或 BV 号；知乎是回答、文章或视频链接。
MediaCrawler 的 B 站 detail 模式只认 BV 号（`media_platform/bilibili/help.py`），av 链接会报
`Unable to parse video ID`；Gleaner 交给它之前会把 av 链接和纯 av 号换算成 BV 链接，帖子键仍用 av 号。

### B 站面试录音

`gleaner media`、`gleaner auto` 和网页上的“下载音视频”都是一次 detail 运行，
带 `--get_media yes`。MediaCrawler 把视频存在 `bili/media/<BV 号>/video.mp4`（另有 `cover.jpg`），
记录里只有 av 号，Gleaner 换算成 BV 号去找视频。实际试过的情况：

- B 站的画面和声音分开下载（DASH），MediaCrawler 用 PATH 上名叫 `ffmpeg` 的程序合成，日志里每合成一个视频有一行
  `[download] DASH 合流完成`，网页按这一行计数。找不到 ffmpeg 时它退回一个低清晰度的整段文件，可能不完整。
  Gleaner 会把 `[media] ffmpeg` 所在目录放到子进程 PATH 的最前面；`doctor` 和下载前的检查都会确认文件名叫 `ffmpeg`。
- 下载很慢：35 分钟的录音下了 15 分钟（CDN 多次返回 HTTP 567 后重试）。下载录音时超时用
  `[media] download_timeout_seconds`（默认 3600 秒），不用 `timeout_seconds`。
- 一次给两个长视频时，第一个下载完后第二个报 `Target page, context or browser has been closed`；
  单独再下载第二个就成功了。Gleaner 在这次运行没拿到全部视频时记为降级并提示单独重试。

## Gleaner 配置

`gleaner.toml` 的 `[mediacrawler]`：

| 配置 | 默认 | 说明 |
|---|---|---|
| `home` | `../MediaCrawler` | MediaCrawler 根目录（有 `main.py`），相对 `gleaner.toml` 所在目录 |
| `command` | `["uv", "run", "main.py"]` | 在 `home` 下执行的命令；不用 uv 时改成 `[".venv/bin/python", "main.py"]` |
| `login_type` | `qrcode` | `qrcode` / `phone` / `cookie`；CDP 模式复用浏览器的登录状态，一般保持 `qrcode` |
| `max_notes` | 20 | 搜索时每个关键词抓多少篇，按上表的每页篇数取整；上限 40 |
| `timeout_seconds` | 900 | 单次运行超时 |
| `min_interval_minutes` | 5 | 同一平台两次抓取的最小间隔，`--force` 跳过；不同平台互不影响；下载录音也算一次抓取 |

`[media]`（只有 B 站面试录音用到）：

| 配置 | 默认 | 说明 |
|---|---|---|
| `ffmpeg` | `ffmpeg` | 命令名或路径（相对 `gleaner.toml`）；文件名必须是 `ffmpeg`，可以是指向别的 ffmpeg 程序的链接 |
| `download_timeout_seconds` | 3600 | 下载录音时单次运行的超时，至少 300 |

## 常见问题

- **退出码非 0 或没有产出数据**：通常是登录过期或触发了滑块验证。打开 Chrome 确认该平台仍是登录状态，
  必要时把 MediaCrawler 的 `HEADLESS` 设为 `False`，手动完成验证后再试。完整输出在 `runs/<run-id>/mediacrawler.log`。
  每个平台的 MediaCrawler 都最多等 10 分钟扫码（每秒检查一次登录状态，共 600 次）；日志里的
  “remaining time is 20s / 120s”是写死的文字，网页按 10 分钟倒计时。
- **抖音一启动就报 `Execution context was destroyed`，或 B 站日志里出现 `login failed , have not found qrcode`**：
  补丁 `02` / `03` 没打上。运行 `gleaner doctor` 看哪些没打上，再运行 `gleaner setup mediacrawler`。
  B 站这种情况 MediaCrawler 以退出码 0 结束，Gleaner 只能报“没有产出数据”，要看日志才知道原因。
- **小红书指定帖子**：`--url` 的链接需要带 `xsec_token`，从浏览器地址栏或分享链接复制完整地址。
- **已经用 WebUI 抓过**：`gleaner import MediaCrawler/data` 直接导入 Gleaner 支持的平台的数据（快手等其他平台的目录会跳过），不会重复抓取。
- **MediaCrawler 改版**：先改 `installer.py` 的 `MEDIACRAWLER_COMMIT`，在新版本上重新生成补丁
  （在 MediaCrawler 里改好后 `git diff -- <文件> > src/gleaner/patches/mediacrawler/<补丁>`）。
  命令行参数、每个平台的分页、是否下载媒体和增量搜索的时间范围在 `src/gleaner/sources/mediacrawler.py`，
  目录对应在 `src/gleaner/adapters/exports.py`，导出字段在 `src/gleaner/adapters/<平台>.py`，
  日志里识别进度的标记在 `src/gleaner/web/progress.py`。
