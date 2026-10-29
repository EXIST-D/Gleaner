# MediaCrawler 接入

Gleaner 调用你本机的 [MediaCrawler](https://github.com/NanmiCoder/MediaCrawler) 抓取小红书、抖音、B 站和知乎，
自己不包含它的代码。牛客不经过 MediaCrawler，见 [牛客读取](nowcoder.md)。
以下内容按 2026 年 10 月的 MediaCrawler main 分支核对。

## 安装

在 Gleaner 仓库根目录下执行，MediaCrawler 放在 `MediaCrawler/`（已被 git 忽略，默认配置就指向这里）：

```bash
git clone https://github.com/NanmiCoder/MediaCrawler.git MediaCrawler
cd MediaCrawler
uv sync            # 需要先安装 uv 和 Node.js 16+
```

MediaCrawler 默认用 CDP 模式驱动新版 Chrome（144+），不需要安装 Playwright 浏览器。登录有两种方式：

- **独立窗口（推荐）**：把 `config/base_config.py` 的 `CDP_CONNECT_EXISTING` 改成 `False`。
  运行时 MediaCrawler 会自己打开一个 Chrome 窗口并显示对应平台的二维码，用手机上的小红书、抖音、哔哩哔哩或知乎扫码；
  遇到验证码或滑块就在这个窗口里完成。登录状态按平台保存在 `MediaCrawler/browser_data/`，和你日常用的 Chrome 完全隔离，
  方便使用小号；登录没过期时下次不用再扫码。每个平台第一次抓取都要各扫一次。
- **连接日常 Chrome**：保持 `CDP_CONNECT_EXISTING = True`，在 Chrome 地址栏手动打开 `chrome://inspect/#remote-debugging`，
  勾选 “Allow remote debugging for this browser instance”。运行时 Chrome 会弹出确认框，60 秒内点“接受”。

## 建议修改的本地配置

这些配置在 `MediaCrawler/config/` 里，命令行没有对应参数：

| 文件 | 配置 | 建议 | 原因 |
|---|---|---|---|
| `base_config.py` | `CDP_CONNECT_EXISTING` | `False` | 使用上面推荐的独立窗口登录 |
| `xhs_config.py` | `SORT_TYPE` | `"time_descending"` | 默认按热度排序；按时间排序才适合每天增量抓取 |
| `dy_config.py` | `PUBLISH_TIME_TYPE` | 例如 `7`（一周内；`1` 一天内，`180` 半年内） | 抖音搜索默认 `0` 不限发布时间 |
| `bilibili_config.py` | `BILI_SEARCH_MODE` | 保持 `"normal"` | 其他模式按日期逐天搜索，请求多得多 |
| `base_config.py` | `CRAWLER_MAX_SLEEP_SEC` | 保持 2 或更大 | 控制请求间隔 |
| `base_config.py` | `ENABLE_CDP_MODE` | 保持 `True` | 复用你的浏览器登录状态 |

Gleaner 每次运行都会通过命令行强制以下参数，不受你的配置影响：

```text
--platform xhs|dy|bili|zhihu --type search|detail --save_data_option jsonl --save_data_path <工作区>/runs/<run-id>/raw
--get_comment no --get_sub_comment no --get_media yes|no
--crawler_max_notes_count <max_notes> --max_concurrency_num 1
```

`--get_media` 只对有图文帖的平台打开：

| 平台 | `--get_media` | 搜索每页 | 数据目录 / 图片目录 | 说明 |
|---|---|---|---|---|
| 小红书 | `yes` | 20 | `xhs/` / `xhs/media/` | 很多面经的题只在图片里；视频帖会下载封面和视频，Gleaner 只用正文 |
| 抖音 | `yes` | 10 | `douyin/` / `dy/media/` | 图集下载全部图片；视频帖 MediaCrawler 会下载封面和整段视频，Gleaner 只用文案 |
| B 站 | `no` | 20 | `bili/` | 只有标题和简介（MediaCrawler 截到 500 字；空简介是 `-`）；真实面试录音的题都在声音里，简介很少列题 |
| 知乎 | `no` | 20 | `zhihu/` | 回答、文章的纯文字正文（标签已去掉，段落会连在一起；`&#34;` 这类实体由 Gleaner 解码）；视频只有标题和简介，其他类型会跳过；作者昵称被 MediaCrawler 打码 |

MediaCrawler 的搜索按整页抓：小于一页的 `max_notes` 会提高到一页，其余按整页向下取整，
所以抖音选 25 篇实际是 20 篇。`--url` 指定帖子时，各平台接受的格式和 MediaCrawler 的 `config/<平台>_config.py` 一致：
小红书要带 `xsec_token` 的完整链接；抖音是视频或图文链接；B 站是视频链接或 BV 号；知乎是回答、文章或视频链接。

## Gleaner 配置

`gleaner.toml` 的 `[mediacrawler]`：

| 配置 | 默认 | 说明 |
|---|---|---|
| `home` | `../MediaCrawler` | MediaCrawler 根目录（有 `main.py`），相对 `gleaner.toml` 所在目录 |
| `command` | `["uv", "run", "main.py"]` | 在 `home` 下执行的命令；不用 uv 时改成 `[".venv/bin/python", "main.py"]` |
| `login_type` | `qrcode` | `qrcode` / `phone` / `cookie`；CDP 模式复用浏览器的登录状态，一般保持 `qrcode` |
| `max_notes` | 20 | 搜索时每个关键词抓多少篇，按上表的每页篇数取整；上限 40 |
| `timeout_seconds` | 900 | 单次运行超时 |
| `min_interval_minutes` | 5 | 同一平台两次抓取的最小间隔，`--force` 跳过；不同平台互不影响 |

## 常见问题

- **退出码非 0 或没有产出数据**：通常是登录过期或触发了滑块验证。打开 Chrome 确认该平台仍是登录状态，
  必要时把 MediaCrawler 的 `HEADLESS` 设为 `False`，手动完成验证后再试。完整输出在 `runs/<run-id>/mediacrawler.log`。
  每个平台的 MediaCrawler 都最多等 10 分钟扫码（每秒检查一次登录状态，共 600 次）；日志里的
  “remaining time is 20s / 120s”是写死的文字，网页按 10 分钟倒计时。
- **抖音一启动就报 `Execution context was destroyed`**：抖音首页第一次写入 cookie 后会再刷新一次，
  MediaCrawler（2026-10 的 main 分支）不等页面稳定就去读 `navigator.userAgent`，每次都在登录前退出。
  在 `media_platform/douyin/core.py` 的 `start()` 里，`await self.context_page.goto(self.index_url)` 后面加一段等待：

  ```python
  for _ in range(10):
      try:
          await self.context_page.evaluate("() => navigator.userAgent")
          break
      except Exception:
          try:
              await self.context_page.wait_for_load_state("load", timeout=15000)
          except Exception:
              pass
          await asyncio.sleep(1)
  ```

  这是对你本机 MediaCrawler 的修改，`git pull` 更新 MediaCrawler 前先 `git stash`，更新后再 `git stash pop`。
- **B 站日志里出现 `login failed , have not found qrcode`，没等扫码就退出**：登录框先插入二维码的 `<img>`，
  稍后才填上图片；MediaCrawler 点开登录框后只等 1 秒，读到的是空图片。在 `media_platform/bilibili/login.py`
  的 `login_by_qrcode()` 里，点击登录按钮后的 `await asyncio.sleep(1)` 后面加一段等待（同样是本机修改）：

  ```python
  try:
      await self.context_page.wait_for_function(
          "() => { const img = document.querySelector('.login-scan-box img'); return !!(img && img.src); }",
          timeout=30_000,
      )
  except PlaywrightTimeoutError:
      pass
  ```

  这种情况 MediaCrawler 以退出码 0 结束，Gleaner 只能报“没有产出数据”，要看日志才知道原因。
- **小红书指定帖子**：`--url` 的链接需要带 `xsec_token`，从浏览器地址栏或分享链接复制完整地址。
- **已经用 WebUI 抓过**：`gleaner import MediaCrawler/data` 直接导入 Gleaner 支持的平台的数据（快手等其他平台的目录会跳过），不会重复抓取。
- **MediaCrawler 改版**：命令行参数和每个平台的分页、是否下载媒体在 `src/gleaner/sources/mediacrawler.py`，
  目录对应在 `src/gleaner/adapters/exports.py`，导出字段在 `src/gleaner/adapters/<平台>.py`，
  日志里识别进度的标记在 `src/gleaner/web/progress.py`。
