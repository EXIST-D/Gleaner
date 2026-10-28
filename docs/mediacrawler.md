# MediaCrawler 接入

Gleaner 调用你本机的 [MediaCrawler](https://github.com/NanmiCoder/MediaCrawler)，自己不包含它的代码。
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
  运行时 MediaCrawler 会自己打开一个 Chrome 窗口并显示小红书二维码，用手机扫码；
  遇到验证码就在这个窗口里输入。登录状态保存在 `MediaCrawler/browser_data/`，和你日常用的 Chrome 完全隔离，
  方便使用小号；登录没过期时下次不用再扫码。
- **连接日常 Chrome**：保持 `CDP_CONNECT_EXISTING = True`，在 Chrome 地址栏手动打开 `chrome://inspect/#remote-debugging`，
  勾选 “Allow remote debugging for this browser instance”。运行时 Chrome 会弹出确认框，60 秒内点“接受”。

## 建议修改的本地配置

这些配置在 `MediaCrawler/config/` 里，命令行没有对应参数：

| 文件 | 配置 | 建议 | 原因 |
|---|---|---|---|
| `base_config.py` | `CDP_CONNECT_EXISTING` | `False` | 使用上面推荐的独立窗口登录 |
| `xhs_config.py` | `SORT_TYPE` | `"time_descending"` | 默认按热度排序；按时间排序才适合每天增量抓取 |
| `base_config.py` | `CRAWLER_MAX_SLEEP_SEC` | 保持 2 或更大 | 控制请求间隔 |
| `base_config.py` | `ENABLE_CDP_MODE` | 保持 `True` | 复用你的浏览器登录状态 |

Gleaner 每次运行都会通过命令行强制以下参数，不受你的配置影响：

```text
--platform xhs --type search|detail --save_data_option jsonl --save_data_path <工作区>/runs/<run-id>/raw
--get_comment no --get_sub_comment no --get_media yes
--crawler_max_notes_count <max_notes> --max_concurrency_num 1
```

`--get_media yes` 会下载图文帖的图片；很多面经的题只在图片里。视频帖会下载封面和视频，
但 Gleaner 目前只使用它的正文。

## Gleaner 配置

`gleaner.toml` 的 `[mediacrawler]`：

| 配置 | 默认 | 说明 |
|---|---|---|
| `home` | `../MediaCrawler` | MediaCrawler 根目录（有 `main.py`），相对 `gleaner.toml` 所在目录 |
| `command` | `["uv", "run", "main.py"]` | 在 `home` 下执行的命令；不用 uv 时改成 `[".venv/bin/python", "main.py"]` |
| `login_type` | `qrcode` | `qrcode` / `phone` / `cookie`；CDP 模式复用浏览器的登录状态，一般保持 `qrcode` |
| `max_notes` | 20 | 搜索时每个关键词抓多少篇。小红书按页返回、每页 20 篇，MediaCrawler 会把小于 20 的值提高到 20，其余按整页向下取整；上限 40（两页） |
| `timeout_seconds` | 900 | 单次运行超时 |
| `min_interval_minutes` | 5 | 两次抓取的最小间隔，`--force` 跳过 |

## 常见问题

- **退出码非 0 或没有产出数据**：通常是登录过期或触发了滑块验证。打开 Chrome 确认小红书仍是登录状态，
  必要时把 MediaCrawler 的 `HEADLESS` 设为 `False`，手动完成验证后再试。完整输出在 `runs/<run-id>/mediacrawler.log`。
- **指定帖子抓取**：`--url` 的链接需要带 `xsec_token`，从浏览器地址栏或分享链接复制完整地址。
- **已经用 WebUI 抓过**：`gleaner import MediaCrawler/data` 直接导入，不会重复抓取。
- **MediaCrawler 改版**：命令行参数在 `src/gleaner/sources/mediacrawler.py`，导出字段在
  `src/gleaner/adapters/xhs.py`，只需要改这两处。
