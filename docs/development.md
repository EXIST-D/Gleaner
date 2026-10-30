# 开发手册

给在另一台电脑上接着开发的人（或 Agent）看：怎么把环境搭起来、怎么搬数据、每次改动的流程和约定，
以及目前做到哪里、接下来做什么。项目是什么、怎么用见 [README](../README.md)，模块和设计决定见
[架构说明](architecture.md)。

## 1. 搭环境

需要：git、Python 3.11+、[uv](https://docs.astral.sh/uv/)、Node.js 18+（检查前端语法）、
Chrome 或 Edge（MediaCrawler 扫码登录用；没有的话 `gleaner setup` 装 Playwright 的 Chromium）、[GitHub CLI](https://cli.github.com/)。

```bash
gh auth login                                  # 用 EXIST-D 账号登录
gh repo clone EXIST-D/Gleaner && cd Gleaner

uv venv --python 3.11 .venv
uv pip install --python .venv/bin/python -e . ruff==0.16.10

git config core.hooksPath .githooks            # 启用提交信息检查（每个 clone 一次）
git config user.name EXIST-D
git config user.email 72126689+EXIST-D@users.noreply.github.com
```

然后跑一遍完整检查（见第 4 节），全部测试通过就说明环境没问题。测试只用合成数据，不需要 MediaCrawler、
Interview Bank 或网络。

Windows 上虚拟环境的程序在 `.venv\Scripts\` 下，把下文的 `.venv/bin/` 换掉即可；提交钩子由 Git for Windows 自带的 sh 执行。

## 2. 外部依赖和工作区

```bash
export GLEANER_HOME="$PWD/workspace"
.venv/bin/gleaner setup       # 创建工作区，装 MediaCrawler（含补丁）、Interview Bank 和采集题库、转写工具
.venv/bin/gleaner doctor      # 每一项都应该是 ✓
```

`setup` 装的东西都不进本仓库，放在仓库目录下（已被 git 忽略），路径写进 `workspace/gleaner.toml`，
每一部分装了什么见 README 的「`gleaner setup` 装了什么」，可以只装用得到的部分：

- **MediaCrawler**（`setup mediacrawler`）：抓小红书、抖音、B 站、知乎时需要；只抓牛客不需要。
  固定在 `installer.MEDIACRAWLER_COMMIT`（`68d9ef1`，2026-10-27），Gleaner 的日志识别和导出字段是按这个版本核对的，
  补丁在 `src/gleaner/patches/mediacrawler/`，见 [MediaCrawler 接入](mediacrawler.md#补丁)。
  它改版后如果进度条不动或字段缺失，先对照 `sources/mediacrawler.py`、`web/progress.py` 和 `adapters/<平台>.py`。
  登录状态在 `MediaCrawler/browser_data/`，和电脑绑定，换电脑要重新扫码。
- **Interview Bank**（`setup bank`）：打包后导入采集题库、网页控制台的「题库」页需要。没配置时下载到 `Interview-bank/`；
  已经有一份（例如 `gh repo clone EXIST-D/Interview-bank ../Interview-bank`）就在 `[handoff] ibank` 写上它的 `ibank.py`，
  `setup` 会沿用。
- **ffmpeg 和 faster-whisper**（`setup media`）：只有转写 B 站面试录音时需要。装在 `workspace/asr-venv/`，
  没有系统的 ffmpeg 时用 `imageio-ffmpeg` 的程序，在 `workspace/bin/ffmpeg` 放一个链接（Windows 是副本）。
  链接必须叫 `ffmpeg`：MediaCrawler 按这个名字在 PATH 里找它。

## 3. 工作区和数据

数据都不进 git：`workspace/`（数据库、原始导出、图片、素材包）、`collected-bank/`（采集题库）、`MediaCrawler/`。
要在新电脑上接着用旧数据，就把它们拷过去：

- **采集题库**：在旧电脑上运行 `python3 <ibank.py> backup create --bank collected-bank`，
  把生成在 `collected-bank/backups/` 里的 ZIP 拷到新电脑，再运行 `backup restore --archive <ZIP> --destination collected-bank`。
- **Gleaner 工作区**：先停掉网页控制台，再把整个 `workspace/` 打包拷过去（`asr-venv/` 和 `bin/` 不用拷）。
  配置里大多是相对路径；拷过去后运行一次 `gleaner setup`，它会补装缺的东西，并把用不了的 `[handoff] python` 换成可用的。

SQLite 数据库和题库都没有合并工具。**同一时间只在一台电脑上抓取和导入**；换电脑时整份拷过去，不要两边各抓一些再合并。
只改代码、跑测试时不需要任何数据。

## 4. 每次改动的流程

```bash
git pull --rebase                         # 先同步另一台电脑推上来的提交
# 再看 workspace/意见.md（网页「进度」页写下的意见），处理完的条目在后面注明“已处理”

# ……改代码、补测试、更新文档……

.venv/bin/ruff format src tests
.venv/bin/ruff check src tests && .venv/bin/ruff format --check src tests \
  && node --check src/gleaner/web/static/app.js \
  && .venv/bin/python -B -X utf8 -m unittest discover -s tests -t .

git add -A && git commit -m "GLN-N: <英文的一句话说明>"
git push
```

- **提交编号**：提交信息以 `GLN-N: ` 开头，N 依次递增，`git log --oneline -1` 能看到上一个编号。
  两台电脑都有未推送的提交时，后推的一方先 `git pull --rebase`，再用 `git rebase -i` 把自己的提交改成接着的编号。
  钩子会拒绝不带编号的提交，不要用 `--no-verify` 绕过。
- **CI**：推到 `main` 后，GitHub Actions 在 Linux、macOS、Windows × Python 3.11–3.13 上跑测试，再单独跑 ruff 和
  `node --check`。用 `gh run list` / `gh run watch` 查看；红了先修再继续。
- **文档**：功能变化同步更新 README、`docs/architecture.md`、`CHANGELOG.md` 的「未发布」，涉及具体平台的再改
  `docs/mediacrawler.md` 或 `docs/nowcoder.md`。

## 5. 代码约定

- 只用 Python 标准库（运行时零依赖）；src 布局；测试用 `unittest`。ruff 行宽 110，忽略 E501，格式以 `ruff format` 为准。
- 前端是原生 HTML/CSS/JS，没有构建步骤。CSP 禁止内联样式：样式写进 `app.css`，宽度这类动态值用 JS 设置。
- 面向用户的提示、日志和网页文字用简体中文；代码、注释和提交信息用英文。
- 预期内的错误抛 `GleanerError`，命令行打印一行中文提示；抓取失败返回 `degraded` 并写明原因，已产出的数据照常导入。
- 测试只用合成数据（`tests/support.py`），不提交真实帖子、图片或账号信息。网络和子进程在测试里一律用假的替身
  （例如 `test_nowcoder.py` 的 `FakeNowcoder`、`test_web_bank.py` 里假的 `ibank.py`）。
- MediaCrawler 只通过子进程调用，不复制它的代码（许可证不兼容）。参考别的项目时注意许可证：
  没有许可证的只借鉴思路。
- 抓取保持小批量、低频率，遵守 robots.txt；遇到验证或限流就停下，不写绕过的代码。
- 改动小而准，先读懂相关代码再改。新增平台的步骤见 [架构说明的「扩展新来源」](architecture.md#扩展新来源)。

## 6. 调试

- **网页控制台**：`.venv/bin/gleaner --workspace workspace web --no-open --port 8765`，打开 <http://127.0.0.1:8765/>。
  前端改完刷新页面即可；改了 Python 要重启服务。
- **抓取日志**：每次运行在 `workspace/runs/<run-id>/` 下有完整日志（`mediacrawler.log` 或 `nowcoder.log`）和原始导出 `raw/`。
  `gleaner runs` 列出运行记录和降级原因；所有命令加 `--json` 输出机器可读结果。退出码 0 成功、1 出错、3 降级。
- **重新导入**：改了适配器后，`gleaner import workspace/runs/<run-id>/raw` 可以用旧的原始数据重新导入，不必重抓；
  改了初筛规则后用 `gleaner screen --all` 重判规则标签（人工标签不动）。
- **截图检查页面**：无头 Chrome 加 `--headless=new --screenshot=… --virtual-time-budget=4000`，文件生成后就结束进程，
  否则会一直挂着。要先点按钮再截图，就用 `--remote-debugging-port` 配合 Node 22 自带的 `WebSocket` 走 CDP；
  路径含中文时用 `fileURLToPath`，不要用 `URL.pathname`（会变成 `%E5…`）。

## 7. 当前状态

版本 0.1.0。提交编号接着 `git log` 里最大的往下排。各次提交做了什么见 `git log` 和 [CHANGELOG](../CHANGELOG.md)。

已经能用：

- 小红书、牛客、知乎（真实抓取导入过），抖音、B 站（真实试抓过，字段和进度识别都对）的抓取、去重、初筛、打包，
  网页控制台的全部页面。
- B 站面试录音：初筛标出录音 → `gleaner media` 下载音视频 → 打包成 `audio.flac` → Interview Bank 转写导入。
  2026-10-29 先手工试了一遍（两段共约 1.5 小时的录音导入 33 道题），再做进 Gleaner。
  2026-10-30 用 `gleaner media` 真实下载了另外两个录音（一次一个，各 2.5 和 4 分钟；CDN 返回 HTTP 567 时 MediaCrawler 会换地址重试），
  打包后按 HANDOFF 的命令转写（`large-v3-turbo`，分别约 3 和 6 分钟，211 和 407 段），面试官的问题都转出来了；
  抽题和提交是 Agent 的事，还没做。已经导入过的录音帖子键记在本机的 `开发进度.md`，不要再下载打包，否则题会重复计数。
- 快手试抓过两个关键词，40 个视频全是无关内容或引流，已经不再支持。
- 抖音：2026-10-29 试抓“后端 面经”一页 12 篇，字段、图片和初筛都对，其中 4 篇图集和正文面经已导入采集题库。
  有一篇的最后一张图是面试陪跑广告，初筛只读文字看不到，是打包看图时人工改成引流的。
  2026-10-30 打补丁后再搜（增量，一周内）：缓存的登录直接可用，22 条里新增 9 条，发布时间都在一周内。
- `gleaner setup`：2026-10-30 在 macOS 的全新目录里从零装过一遍（MediaCrawler 下载、4 个补丁、`uv sync`、Interview Bank、
  采集题库、faster-whisper 和 imageio-ffmpeg），`doctor` 全部通过；那次发现系统自带的 `python3` 是 3.9，已改成检查版本。
  Windows 和 Linux 上只跑过单元测试，没有真实装过。
- 增量抓取：2026-10-30 真实跑过牛客（第二次跳过已保存的帖子，第 5 页整页是旧帖时停下）、抖音和 B 站
  （20 个视频最早的发布于起点之后）。知乎只核对过补丁能导入，还没真实增量搜过。

待办（大致按优先级）：

1. 采集题库里大部分题还没有参考答案（`ibank.py stats` 的 `answer_status.missing`；目前只有智能体领域补过）。做法：Interview Bank 的 `research` 生成任务 → 查一手资料 →
   `page-text` 存原文 → `answer --page-texts --commit`；项目类题只给答题框架，不编经历。
2. 让 Agent 读最新素材包里两个录音的转写，抽题并提交（HANDOFF 里的 `media-task` → `ingest submit`）。
   另有几个 B 站视频从标题看不出是不是录音，可以打开看看，是的话用 `gleaner media <帖子键>` 下载。
3. 在 Windows 上真实跑一遍 `gleaner setup`；知乎真实增量搜一次。
4. 可选的大模型复判：规则拿不准（`unclear`）的帖子再交给模型判断。
5. 采集题库与个人题库的覆盖对照。
