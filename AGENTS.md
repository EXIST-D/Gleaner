# Gleaner：给 Agent 的说明

开始之前先读 [docs/development.md](docs/development.md)（环境、流程、约定、当前状态和待办），
再读 [docs/architecture.md](docs/architecture.md) 里和任务相关的模块。

- 回答用简体中文；代码、注释和提交信息用英文，面向用户的提示和网页文字用简体中文。
- 提交信息以 `GLN-N: ` 开头，编号接着 `git log` 里最大的；不要用 `--no-verify` 绕过钩子。
- 改完跑完整检查（ruff check、ruff format --check、`node --check`、全部 unittest），并同步更新 README、
  `docs/architecture.md`、`CHANGELOG.md`。
- 只用标准库；测试只用合成数据；MediaCrawler 只通过子进程调用；`workspace/`、`collected-bank/`、`MediaCrawler/`
  里是本机数据，不要提交。
