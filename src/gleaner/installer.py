"""`gleaner setup`: install the programs Gleaner drives as separate processes, next to the workspace.

- MediaCrawler: a clone of the commit Gleaner was checked against, Gleaner's patches from
  `patches/mediacrawler/` (they stay under MediaCrawler's license, see the NOTICE there), its dependencies
  through uv, and Playwright's Chromium when there is no Chrome or Edge to open.
- Interview Bank: a clone of its repository and an empty collected bank.
- Transcription: a venv with faster-whisper, an `ffmpeg` (the system one, else imageio-ffmpeg's behind a
  link named ffmpeg) and the model in the bank's cache, where Interview Bank looks for it.

Every step checks first and keeps what already works, so running it again is safe. Paths it installs are
written back to gleaner.toml.
"""

from __future__ import annotations

import collections
import json
import os
import re
import shutil
import subprocess
import sys
import tomllib
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass, field
from importlib import resources
from pathlib import Path

from gleaner.audio import find_ffmpeg
from gleaner.config import CONFIG_NAME, Config, load_config, parse_config

MEDIACRAWLER_REPO = "https://github.com/NanmiCoder/MediaCrawler.git"
MEDIACRAWLER_COMMIT = "68d9ef1c1da22eab75940dfd7cec259907006538"
INTERVIEW_BANK_REPO = "https://github.com/EXIST-D/Interview-bank.git"
IBANK_SCRIPT = ("skills", "interview-bank", "scripts", "ibank.py")
FASTER_WHISPER = "faster-whisper==1.2.1"
MODERN_PYTHON = "import sys; sys.exit(sys.version_info < (3, 10))"
"""The version Interview Bank's transcription is written against."""
PARTS = ("mediacrawler", "bank", "media")
PART_TITLES = {"mediacrawler": "MediaCrawler", "bank": "Interview Bank 和采集题库", "media": "转写录音的工具"}
ASR_VENV = "asr-venv"
TOOLS_DIR = "bin"
APPLIED, PENDING, CONFLICT = "applied", "pending", "conflict"

_BROWSERS = (
    "from tools.browser_launcher import BrowserLauncher; print(len(BrowserLauncher().detect_browser_paths()))"
)
_MODEL = """\
import sys
from faster_whisper.utils import download_model
try:
    download_model(sys.argv[1], cache_dir=sys.argv[2], local_files_only=True)
    print("cached")
except Exception:
    download_model(sys.argv[1], cache_dir=sys.argv[2])
    print("downloaded")
"""
_HEADER = re.compile(r"^\s*\[([^\[\]]+)\]\s*(?:#.*)?$")

Run = Callable[[Sequence[str], Path | None, Mapping[str, str] | None], tuple[int, str]]
"""(command, working directory, extra environment) -> (exit code, last lines of output); raises OSError."""


def make_run(*, echo: bool) -> Run:
    """Run a command, keeping the end of its output; with echo also show it on stderr as it comes."""

    def run(command: Sequence[str], cwd: Path | None, env: Mapping[str, str] | None) -> tuple[int, str]:
        tail: collections.deque[str] = collections.deque(maxlen=20)
        with subprocess.Popen(
            list(command),
            cwd=cwd,
            env={**os.environ, **(env or {})},
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
        ) as process:
            assert process.stdout is not None
            for line in process.stdout:
                tail.append(line.rstrip("\n"))
                if echo:
                    sys.stderr.write(line)
            return process.wait(), "\n".join(tail)

    return run


@dataclass
class StepResult:
    part: str
    ok: bool
    lines: list[str] = field(default_factory=list)

    @property
    def title(self) -> str:
        return PART_TITLES[self.part]

    def as_dict(self) -> dict:
        return {"part": self.part, "title": self.title, "ok": self.ok, "lines": self.lines}


class StepFailed(Exception):
    pass


class Installer:
    def __init__(
        self,
        workspace: Path,
        *,
        run: Run,
        which: Callable[[str], str | None] = shutil.which,
        windows: bool = os.name == "nt",
    ):
        self.workspace = workspace
        self.run = run
        self.which = which
        self.windows = windows
        self.config: Config = load_config(workspace)

    def install(self, parts: Sequence[str], *, model: bool = True) -> list[StepResult]:
        results = []
        for part in PARTS:
            if part not in parts:
                continue
            result = StepResult(part, True)
            try:
                if part == "mediacrawler":
                    self.mediacrawler(result.lines)
                elif part == "bank":
                    self.bank(result.lines)
                else:
                    self.media(result.lines, model=model)
            except StepFailed as exc:
                result.ok = False
                result.lines.append(str(exc))
            results.append(result)
        return results

    # Steps

    def mediacrawler(self, notes: list[str]) -> None:
        git = self._need("git", "下载 MediaCrawler 和打补丁需要 git")
        home = self.config.mediacrawler.home or (self.workspace.parent / "MediaCrawler").resolve()
        if not (home / "main.py").is_file():
            self._empty(home, "MediaCrawler（没有 main.py）")
            self._call(
                [
                    git,
                    "clone",
                    "-c",
                    "core.autocrlf=false",
                    "--filter=blob:none",
                    MEDIACRAWLER_REPO,
                    str(home),
                ],
                what="下载 MediaCrawler",
            )
            self._call(
                [git, "-C", str(home), "-c", "advice.detachedHead=false", "checkout", MEDIACRAWLER_COMMIT],
                what="切换到 Gleaner 核对过的 MediaCrawler 版本",
            )
            notes.append(f"已下载 MediaCrawler {MEDIACRAWLER_COMMIT[:7]} 到 {home}")
        else:
            notes.append(f"MediaCrawler 已在 {home}")
            code, head = self.run([git, "-C", str(home), "rev-parse", "HEAD"], None, None)
            head = head.strip().splitlines()[-1] if code == 0 and head.strip() else ""
            if head and head != MEDIACRAWLER_COMMIT:
                notes.append(
                    f"注意：本机版本是 {head[:7]}，Gleaner 核对的是 {MEDIACRAWLER_COMMIT[:7]}，日志识别和字段可能对不上"
                )
        if self.config.mediacrawler.home != home:
            self._set("mediacrawler", {"home": self._relative(home)})

        if not (home / ".git").exists():
            raise StepFailed(f"{home} 不是 git 仓库，没法打补丁；删掉它后重新运行 gleaner setup mediacrawler")
        with patch_files() as patches:
            states = patch_states(home, patches, git=git, run=self.run)
            conflicts = [name for name, state in states.items() if state == CONFLICT]
            if conflicts:
                raise StepFailed(
                    f"补丁 {'、'.join(conflicts)} 打不上：本机的 MediaCrawler 改过相关文件，或者版本不同。"
                    f"可以在 {home} 里先 git stash 本机改动，再运行 gleaner setup mediacrawler"
                )
            pending = [name for name, state in states.items() if state == PENDING]
            for name in pending:
                self._call([git, "-C", str(home), "apply", str(patches[name])], what=f"打补丁 {name}")
        notes.append(
            f"补丁：新打上 {len(pending)} 个，共 {len(states)} 个"
            if pending
            else f"补丁 {len(states)} 个都已打上"
        )

        command = self.config.mediacrawler.command
        if command[0] != "uv":
            notes.append("mediacrawler.command 不用 uv，依赖请按 MediaCrawler 的说明自己安装")
            return
        uv = self._need(
            "uv",
            "需要 uv 安装 MediaCrawler 的依赖：macOS/Linux 运行 curl -LsSf https://astral.sh/uv/install.sh | sh，"
            'Windows 运行 powershell -c "irm https://astral.sh/uv/install.ps1 | iex"，装好后重新打开终端',
        )
        self._call([uv, "sync"], cwd=home, what="安装 MediaCrawler 的依赖（uv sync）")
        notes.append("依赖已安装")
        code, output = self.run([uv, "run", "python", "-c", _BROWSERS], home, None)
        found = output.strip().splitlines()[-1] if output.strip() else ""
        if code == 0 and found.isdigit() and int(found) > 0:
            notes.append("找到本机的 Chrome 或 Edge，抓取时会单独打开一个窗口扫码登录")
        else:
            self._call(
                [uv, "run", "playwright", "install", "chromium"], cwd=home, what="安装 Playwright 的 Chromium"
            )
            notes.append("没找到 Chrome 或 Edge，已安装 Playwright 的 Chromium")

    def bank(self, notes: list[str]) -> None:
        ibank = self.config.handoff.ibank
        if ibank and ibank.is_file():
            notes.append(f"Interview Bank 已在 {ibank}")
        else:
            repo = (self.workspace.parent / "Interview-bank").resolve()
            ibank = repo.joinpath(*IBANK_SCRIPT)
            if not ibank.is_file():
                git = self._need("git", "下载 Interview Bank 需要 git")
                self._empty(repo, "Interview Bank")
                self._call(
                    [git, "clone", "--depth", "1", INTERVIEW_BANK_REPO, str(repo)], what="下载 Interview Bank"
                )
                notes.append(f"已下载 Interview Bank 到 {repo}")
            self._set("handoff", {"ibank": self._relative(ibank)})
        python = self.config.handoff.python
        if not self._modern(python):
            python = sys.executable
            self._set("handoff", {"python": python})
            notes.append(f"导入用 Python 改为 {python}（Interview Bank 需要 3.10+）")
        bank = self.config.handoff.bank or (self.workspace.parent / "collected-bank").resolve()
        if (bank / "manifest.json").is_file():
            notes.append(f"采集题库已在 {bank}")
        else:
            self._call([python, "-B", str(ibank), "init", "--bank", str(bank), "--json"], what="创建采集题库")
            notes.append(f"已创建采集题库 {bank}")
        if self.config.handoff.bank != bank:
            self._set("handoff", {"bank": self._relative(bank)})

    def media(self, notes: list[str], *, model: bool) -> None:
        asr = self.config.handoff.transcribe_python
        if self._imports(asr, "faster_whisper"):
            notes.append(f"faster-whisper 已装在 {asr}")
        else:
            venv = self.workspace / ASR_VENV
            python = venv / ("Scripts/python.exe" if self.windows else "bin/python")
            if not python.is_file():
                self._call([sys.executable, "-m", "venv", str(venv)], what="创建转写用的虚拟环境")
            self._call([str(python), "-m", "pip", "install", FASTER_WHISPER], what="安装 faster-whisper")
            asr = str(python)
            self._set("handoff", {"asr_python": self._relative(python)})
            notes.append(f"已在 {venv} 装好 {FASTER_WHISPER}")

        ffmpeg = find_ffmpeg(self.config.media.ffmpeg)
        if ffmpeg and Path(ffmpeg).stem.lower() == "ffmpeg":
            notes.append(f"ffmpeg：{ffmpeg}")
        elif self.which("ffmpeg"):
            self._set("media", {"ffmpeg": "ffmpeg"})
            notes.append(f"ffmpeg：用系统里的 {self.which('ffmpeg')}")
        else:
            self._call([asr, "-m", "pip", "install", "imageio-ffmpeg"], what="安装 imageio-ffmpeg")
            code, output = self.run(
                [asr, "-c", "import imageio_ffmpeg; print(imageio_ffmpeg.get_ffmpeg_exe())"], None, None
            )
            program = Path(output.strip().splitlines()[-1]) if code == 0 and output.strip() else None
            if program is None or not program.is_file():
                raise StepFailed(f"imageio-ffmpeg 没有给出 ffmpeg 程序：{output.strip()[-200:]}")
            link = self.workspace / TOOLS_DIR / ("ffmpeg.exe" if self.windows else "ffmpeg")
            link_tool(program, link, windows=self.windows)
            self._set("media", {"ffmpeg": self._relative(link)})
            notes.append(f"ffmpeg：{link} → {program}")

        if not model:
            notes.append("没有下载转写模型（第一次转写时会下载）")
            return
        bank = self.config.handoff.bank
        if bank is None or not (bank / "manifest.json").is_file():
            notes.append(
                "还没有采集题库，没有下载转写模型；先运行 gleaner setup bank，再运行 gleaner setup media"
            )
            return
        cache = bank / "cache" / "asr"
        name = self.config.handoff.asr_model
        output = self._call(
            [asr, "-B", "-c", _MODEL, name, str(cache)],
            env=model_environment(cache),
            what=f"下载转写模型 {name}",
        )
        if output.strip().endswith("cached"):
            notes.append(f"转写模型 {name} 已在 {cache}")
        else:
            notes.append(f"已把转写模型 {name} 下载到 {cache}")

    # Helpers

    def _call(
        self,
        command: Sequence[str],
        *,
        what: str,
        cwd: Path | None = None,
        env: Mapping[str, str] | None = None,
    ) -> str:
        try:
            code, output = self.run(command, cwd, env)
        except OSError as exc:
            raise StepFailed(f"{what}失败：{exc}") from exc
        if code != 0:
            last = output.strip().splitlines()[-1] if output.strip() else "没有输出"
            raise StepFailed(f"{what}失败（退出码 {code}）：{last}")
        return output

    def _need(self, program: str, why: str) -> str:
        found = self.which(program)
        if found is None:
            raise StepFailed(f"找不到 {program}：{why}")
        return found

    def _empty(self, directory: Path, what: str) -> None:
        if directory.exists() and any(directory.iterdir()):
            raise StepFailed(f"{directory} 已存在但不是 {what}；换个位置（改 gleaner.toml）或清空后再试")

    def _runnable(self, command: str) -> bool:
        return bool(command) and (self.which(command) is not None or Path(command).is_file())

    def _imports(self, python: str, module: str) -> bool:
        return self._succeeds(python, f"import {module}")

    def _modern(self, python: str) -> bool:
        return self._succeeds(python, MODERN_PYTHON)

    def _succeeds(self, python: str, script: str) -> bool:
        if not self._runnable(python):
            return False
        try:
            code, _ = self.run([python, "-c", script], None, None)
        except OSError:
            return False
        return code == 0

    def _relative(self, path: Path) -> str:
        try:
            return Path(os.path.relpath(path, self.workspace)).as_posix()
        except ValueError:
            return str(path)

    def _set(self, table: str, values: Mapping[str, object]) -> None:
        path = self.workspace / CONFIG_NAME
        text = set_values(path.read_text(encoding="utf-8"), table, values)
        parse_config(tomllib.loads(text), self.workspace)
        path.write_text(text, encoding="utf-8")
        self.config = load_config(self.workspace)


def model_environment(cache: Path) -> dict[str, str]:
    """The model cache Interview Bank's `media-transcribe` uses (`ibank_core/media.py`)."""
    return {
        "HF_HOME": str(cache / "hub-home"),
        "HF_XET_CACHE": str(cache / "xet"),
        "HF_HUB_DISABLE_TELEMETRY": "1",
        "HF_HUB_DISABLE_XET": "1",
    }


def link_tool(program: Path, link: Path, *, windows: bool) -> None:
    """A tool under the name callers look for; Windows needs extra rights for symlinks, so it gets a copy."""
    link.parent.mkdir(parents=True, exist_ok=True)
    if link.is_symlink() or link.exists():
        link.unlink()
    if windows:
        shutil.copy2(program, link)
    else:
        link.symlink_to(program)


@contextmanager
def patch_files() -> Iterator[dict[str, Path]]:
    """Gleaner's MediaCrawler patches by name, in the order they apply."""
    folder = resources.files("gleaner").joinpath("patches", "mediacrawler")
    items = sorted(
        (item for item in folder.iterdir() if item.name.endswith(".patch")), key=lambda item: item.name
    )
    with ExitStack() as stack:
        yield {item.name: stack.enter_context(resources.as_file(item)) for item in items}


def patch_states(home: Path, patches: Mapping[str, Path], *, git: str, run: Run) -> dict[str, str]:
    """applied, pending (applies cleanly) or conflict, for each patch against the checkout at home."""
    states = {}
    for name, path in patches.items():
        if run([git, "-C", str(home), "apply", "--check", "--reverse", str(path)], None, None)[0] == 0:
            states[name] = APPLIED
        elif run([git, "-C", str(home), "apply", "--check", str(path)], None, None)[0] == 0:
            states[name] = PENDING
        else:
            states[name] = CONFLICT
    return states


def set_values(text: str, table: str, values: Mapping[str, object]) -> str:
    """Set single-line keys of one TOML table, keeping the rest of the file and its comments."""
    lines = text.splitlines()
    start = next(
        (
            i
            for i, line in enumerate(lines)
            if (match := _HEADER.match(line)) and match.group(1).strip() == table
        ),
        None,
    )
    if start is None:
        if lines and lines[-1].strip():
            lines.append("")
        lines.append(f"[{table}]")
        start = len(lines) - 1
    end = next(
        (i for i in range(start + 1, len(lines)) if lines[i].lstrip().startswith("[")),
        len(lines),
    )
    for key, value in values.items():
        literal = f"{key} = {json.dumps(value, ensure_ascii=False)}"
        pattern = re.compile(rf"^\s*{re.escape(key)}\s*=")
        found = next((i for i in range(start + 1, end) if pattern.match(lines[i])), None)
        if found is not None:
            lines[found] = literal
            continue
        insert = end
        while insert > start + 1 and not lines[insert - 1].strip():
            insert -= 1
        lines.insert(insert, literal)
        end += 1
    return "\n".join(lines) + "\n"
