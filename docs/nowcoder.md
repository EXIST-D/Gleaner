# 牛客读取

MediaCrawler 不支持牛客，Gleaner 用标准库直接读取牛客网页自己调用的公开 JSON 接口，不需要浏览器和登录。
以下内容按 2026 年 10 月的牛客网核对。

## 怎么找帖子

牛客的 `robots.txt` 不允许抓取站内搜索 `/search`，所以 Gleaner 不用搜索：

1. 翻阅“面经”频道的最新列表（`gw-c.nowcoder.com/api/sparta/job-experience/experience/job/list`，每页 20 篇，
   最新的在前），最多翻 `scan_pages` 页。
2. 在每篇的标题和摘要里匹配关键词：关键词按空格拆开，**每个词都要出现**（不区分大小写）。
   例如「后端 一面」会命中“示例科技后端一面面经”，不会命中“后端二面”。
3. 每个关键词命中够 `max_notes` 篇就不再翻页；只有命中的帖子才读取详情。

列表里有两种帖子：讨论帖（数字 ID，`/discuss/<ID>`，正文是 HTML）和动态（32 位十六进制 UUID，
`/feed/main/detail/<UUID>`，正文是纯文本，图片单独列出）。两种都会保存；表情图片不算帖子图片。

因为只看最新的几页，牛客适合每天或每隔几天抓一次，而不是一次找全历史帖子。

再次搜索同样的关键词时默认是增量的：起点是上次成功搜索的开始时间往前 1 天，列表里早于它的帖子跳过，
**一整页都早于它就不再往后翻**；已经保存过的帖子直接跳过，不再读详情（日志里是“跳过已保存的 N 篇”）。
没有新帖时这次运行仍记为成功，结果是“没有新帖：翻阅了最新 N 篇面经”。列表记录没有发布时间的帖子照常匹配，不会让翻页提前停下。
`gleaner crawl nowcoder --full` 不做增量，已保存的帖子也重新读取（正文变了会更新）。
想要某篇旧帖子，用 `--url` 指定：

```bash
gleaner crawl nowcoder --url "https://www.nowcoder.com/discuss/<ID>" --url "https://www.nowcoder.com/feed/main/detail/<UUID>"
```

## 请求节奏

- 一次只发一个请求，两次之间至少 `request_interval_seconds` 秒（默认 3），另加最多 1 秒随机抖动；图片之间 1 秒。
- 429 和 5xx 会重试，最多 3 次；429 按 `Retry-After` 等待（最多 60 秒）。连续 3 次 429、遇到 403 或验证页
  （“访问过于频繁”“请登录后继续访问”）就停下，已经保存的帖子照常导入，这次记为降级。
- 单篇帖子被删除或读取失败只跳过这一篇，最后在降级原因里列出。
- 超过 `timeout_seconds` 或在网页上点“停止”，会在下一个请求前停下。

## 保存什么

和 MediaCrawler 相同的目录布局，所以 `gleaner import` 也能导入：

```text
runs/<run-id>/
├── nowcoder.log
└── raw/nowcoder/
    ├── jsonl/search_contents_<日期>.jsonl   # 每篇一行，读到就写入
    └── media/<帖子 ID>/001.png …              # 只下载 *.nowcoder.com 的图片，每篇最多 30 张、每张 15 MB
```

每行是 `{"kind", "post_id", "url", "source_keywords", "author_hash", "detail"}`。`detail` 是详情接口的原始返回，
但去掉了作者资料（`userBrief`）和 IP 属地；`author_hash` 是作者 ID 的哈希，只用来区分作者。

## Gleaner 配置

`gleaner.toml` 的 `[nowcoder]`：

| 配置 | 默认 | 说明 |
|---|---|---|
| `max_notes` | 20 | 每个关键词最多保存多少篇（1–40），按篇数精确保存，不按页取整 |
| `scan_pages` | 5 | 最多翻阅多少页最新面经列表（1–25，每页 20 篇） |
| `request_interval_seconds` | 3 | 两次请求的最小间隔（不小于 1） |
| `download_images` | `true` | 是否下载帖子图片；很多面经的题在截图里 |
| `timeout_seconds` | 600 | 单次运行超时（不小于 60） |
| `min_interval_minutes` | 5 | 两次抓取牛客的最小间隔，`--force` 跳过；和其他平台分开计算 |

## 常见问题

- **“标题和摘要都不包含这些关键词”**：最新的几页里没有匹配的帖子。换更宽的关键词（例如只写公司名），
  或调大 `scan_pages`。
- **“牛客拒绝了请求”**：触发了频率限制或验证。隔一段时间再试，或调大 `request_interval_seconds`。
- **牛客改版**：接口地址和判断逻辑在 `src/gleaner/sources/nowcoder.py`，字段解析在 `src/gleaner/adapters/nowcoder.py`。

## 参考

翻页加限速、按 `Retry-After` 重试、遇到验证页即停的做法参考了
[ytogeo/nowcoder_crawler](https://github.com/ytogeo/nowcoder_crawler)（该仓库没有许可证，只借鉴思路，没有使用其代码）
和 [z0l0y/Crawl4NK](https://github.com/z0l0y/Crawl4NK)（MIT）。
