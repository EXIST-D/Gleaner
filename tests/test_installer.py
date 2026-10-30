import os
import sys
import tomllib
import unittest
from pathlib import Path

from gleaner.checks import patch_check
from gleaner.cli import init_workspace
from gleaner.config import CONFIG_NAME, load_config
from gleaner.installer import (
    FASTER_WHISPER,
    IBANK_SCRIPT,
    INTERVIEW_BANK_REPO,
    MEDIACRAWLER_COMMIT,
    MEDIACRAWLER_REPO,
    MODERN_PYTHON,
    Installer,
    patch_files,
    set_values,
)
from gleaner.sources.mediacrawler import search_window
from tests.support import NOW, WorkspaceTestCase

GIT, UV, OLD_PYTHON = "/usr/bin/git", "/usr/bin/uv", "/usr/bin/python3"


class FakeShell:
    """Plays git, uv, pip and Python for the installer and records every command."""

    def __init__(self, *, applied=(), conflicts=(), browsers="1", whisper=False, program=None):
        self.calls: list[list[str]] = []
        self.envs: list[dict] = []
        self.applied = set(applied)
        self.conflicts = set(conflicts)
        self.browsers = browsers
        self.whisper = whisper
        self.program = program

    def __call__(self, command, cwd, env):
        command = [str(part) for part in command]
        self.calls.append(command)
        self.envs.append(dict(env or {}))
        script = command[-3] if "-c" in command[:-2] else ""
        if command[:2] == [GIT, "clone"]:
            return self.clone(Path(command[-1]), command[-2])
        if "rev-parse" in command:
            return 0, MEDIACRAWLER_COMMIT
        if "apply" in command:
            name = Path(command[-1]).name
            if "--reverse" in command:
                return (0 if name in self.applied else 1), ""
            if "--check" in command:
                return (1 if name in self.conflicts else 0), ""
            self.applied.add(name)
            return 0, ""
        if "detect_browser_paths" in command[-1]:
            return 0, self.browsers
        if "init" in command and "--bank" in command:
            bank = Path(command[command.index("--bank") + 1])
            bank.mkdir(parents=True)
            (bank / "manifest.json").write_text("{}", encoding="utf-8")
            return 0, "{}"
        if command[1:3] == ["-m", "venv"]:
            venv = Path(command[3])
            for python in (venv / "bin" / "python", venv / "Scripts" / "python.exe"):
                python.parent.mkdir(parents=True, exist_ok=True)
                python.write_text("", encoding="utf-8")
            return 0, ""
        if command[-1] == MODERN_PYTHON:
            return (1 if command[0] == OLD_PYTHON else 0), ""
        if command[-1] == "import faster_whisper":
            return (0 if self.whisper else 1), ""
        if "download_model" in script:
            return 0, "downloaded"
        if "imageio_ffmpeg" in command[-1]:
            return 0, str(self.program)
        return 0, ""

    def clone(self, target, repo):
        target.mkdir(parents=True)
        (target / ".git").mkdir()
        if repo == MEDIACRAWLER_REPO:
            (target / "main.py").write_text("", encoding="utf-8")
        else:
            script = target.joinpath(*IBANK_SCRIPT)
            script.parent.mkdir(parents=True)
            script.write_text("", encoding="utf-8")
        return 0, ""

    def ran(self, *parts):
        return [call for call in self.calls if all(part in call for part in parts)]


class InstallerTest(WorkspaceTestCase):
    def setUp(self):
        super().setUp()
        init_workspace(self.workspace)
        self.root = self.workspace.parent
        with patch_files() as patches:
            self.patches = list(patches)
        self.tools = {"git": GIT, "uv": UV}

    def installer(self, shell, **kwargs):
        return Installer(self.workspace, run=shell, which=self.tools.get, windows=os.name == "nt", **kwargs)

    def checkout(self):
        home = self.root / "MediaCrawler"
        (home / ".git").mkdir(parents=True)
        (home / "main.py").write_text("", encoding="utf-8")
        return home

    def test_a_fresh_mediacrawler_is_cloned_at_the_checked_commit_patched_and_synced(self):
        shell = FakeShell()
        (step,) = self.installer(shell).install(["mediacrawler"])
        self.assertTrue(step.ok, step.lines)
        home = str(self.root / "MediaCrawler")
        self.assertEqual(shell.ran("clone")[0][-2:], [MEDIACRAWLER_REPO, home])
        self.assertIn(MEDIACRAWLER_COMMIT, shell.ran("checkout")[0])
        applied = [Path(call[-1]).name for call in shell.calls if "apply" in call and "--check" not in call]
        self.assertEqual(applied, self.patches)
        self.assertTrue(shell.ran(UV, "sync"))
        self.assertFalse(shell.ran("playwright"), "Chrome was found")
        self.assertEqual(load_config(self.workspace).mediacrawler.home, self.root / "MediaCrawler")

        shell = FakeShell(applied=self.patches, browsers="0")
        (step,) = self.installer(shell).install(["mediacrawler"])
        self.assertTrue(step.ok, step.lines)
        self.assertFalse(shell.ran("clone"))
        self.assertFalse([call for call in shell.calls if "apply" in call and "--check" not in call])
        self.assertIn(f"补丁 {len(self.patches)} 个都已打上", step.lines)
        self.assertTrue(shell.ran("playwright", "install", "chromium"), "no Chrome or Edge")

    def test_conflicting_local_changes_stop_before_any_patch_is_applied(self):
        self.checkout()
        shell = FakeShell(conflicts={self.patches[1]})
        (step,) = self.installer(shell).install(["mediacrawler"])
        self.assertFalse(step.ok)
        self.assertIn(self.patches[1], step.lines[-1])
        self.assertIn("git stash", step.lines[-1])
        self.assertFalse([call for call in shell.calls if "apply" in call and "--check" not in call])

    def test_missing_tools_are_explained(self):
        self.checkout()
        del self.tools["uv"]
        (step,) = self.installer(FakeShell(applied=self.patches)).install(["mediacrawler"])
        self.assertFalse(step.ok)
        self.assertIn("astral.sh/uv", step.lines[-1])

        (self.root / "MediaCrawler" / "main.py").unlink()
        del self.tools["git"]
        (step,) = self.installer(FakeShell()).install(["mediacrawler"])
        self.assertIn("找不到 git", step.lines[-1])

    def test_bank_clones_interview_bank_and_creates_the_collected_bank(self):
        shell = FakeShell()
        (step,) = self.installer(shell).install(["bank"])
        self.assertTrue(step.ok, step.lines)
        repo = self.root / "Interview-bank"
        self.assertEqual(shell.ran("clone")[0][-2:], [INTERVIEW_BANK_REPO, str(repo)])
        config = load_config(self.workspace)
        self.assertEqual(config.handoff.ibank, repo.joinpath(*IBANK_SCRIPT))
        self.assertEqual(config.handoff.python, sys.executable, "python3 is not on this fake PATH")
        self.assertTrue((self.root / "collected-bank" / "manifest.json").is_file())
        text = (self.workspace / CONFIG_NAME).read_text(encoding="utf-8")
        self.assertIn('ibank = "../Interview-bank/skills/interview-bank/scripts/ibank.py"', text)

        shell = FakeShell()
        (step,) = self.installer(shell).install(["bank"])
        self.assertTrue(step.ok, step.lines)
        self.assertEqual((shell.ran("clone"), shell.ran("init")), ([], []))

    def test_bank_replaces_a_python_older_than_310(self):
        path = self.workspace / CONFIG_NAME
        path.write_text(set_values(path.read_text(encoding="utf-8"), "handoff", {"python": OLD_PYTHON}))
        self.tools["python3"] = OLD_PYTHON
        (Path(self.root) / "Interview-bank").mkdir()
        script = self.root.joinpath("Interview-bank", *IBANK_SCRIPT)
        script.parent.mkdir(parents=True)
        script.write_text("", encoding="utf-8")
        shell = FakeShell()
        (step,) = self.installer(shell).install(["bank"])
        self.assertTrue(step.ok, step.lines)
        self.assertEqual(load_config(self.workspace).handoff.python, sys.executable)
        self.assertTrue(shell.ran(sys.executable, "init"))
        self.assertIn("3.10+", "\n".join(step.lines))

    def test_media_installs_faster_whisper_an_ffmpeg_link_and_the_model(self):
        program = self.root / "imageio" / "ffmpeg-macos-v7.1"
        program.parent.mkdir()
        program.write_text("", encoding="utf-8")
        program.chmod(0o755)
        bank = self.root / "collected-bank"
        bank.mkdir()
        (bank / "manifest.json").write_text("{}", encoding="utf-8")
        path = self.workspace / CONFIG_NAME
        path.write_text(set_values(path.read_text(encoding="utf-8"), "media", {"ffmpeg": "nowhere/ffmpeg"}))

        shell = FakeShell(program=program)
        (step,) = self.installer(shell).install(["media"])
        self.assertTrue(step.ok, step.lines)
        self.assertTrue(shell.ran(sys.executable, "venv"))
        self.assertTrue(shell.ran("pip", "install", FASTER_WHISPER))
        config = load_config(self.workspace)
        windows = os.name == "nt"
        venv_python = self.workspace / "asr-venv" / ("Scripts/python.exe" if windows else "bin/python")
        self.assertEqual(Path(config.handoff.asr_python), venv_python)
        link = self.workspace / "bin" / ("ffmpeg.exe" if windows else "ffmpeg")
        self.assertEqual(Path(config.media.ffmpeg), link)
        self.assertTrue(link.is_file())
        (download,) = [index for index, call in enumerate(shell.calls) if "large-v3-turbo" in call]
        self.assertIn(str(bank / "cache" / "asr"), shell.calls[download])
        self.assertEqual(shell.envs[download]["HF_HOME"], str(bank / "cache" / "asr" / "hub-home"))

        shell = FakeShell(whisper=True, program=program)
        (step,) = self.installer(shell).install(["media"], model=False)
        self.assertTrue(step.ok, step.lines)
        self.assertEqual((shell.ran("venv"), shell.ran("large-v3-turbo")), ([], []))

    def test_doctor_checks_the_patches_only_for_a_git_checkout(self):
        config = load_config(self.workspace)
        self.assertIn("还没装好", patch_check(config).detail)
        self.checkout()
        (self.root / "MediaCrawler" / ".git").rmdir()
        self.assertIn("git 仓库", patch_check(load_config(self.workspace)).detail)


class ConfigEditTest(unittest.TestCase):
    TEXT = '# top\nkeywords = []\n\n[handoff]\n# the bank\nbank = "../collected-bank"\nibank = ""\n\n[media]\nffmpeg = "ffmpeg"\n'

    def test_keys_are_replaced_or_added_and_comments_kept(self):
        text = set_values(
            self.TEXT, "handoff", {"ibank": "../Interview-bank/ibank.py", "asr_python": "a b/python"}
        )
        self.assertIn("# the bank\n", text)
        self.assertIn('ibank = "../Interview-bank/ibank.py"\nasr_python = "a b/python"\n\n[media]', text)
        text = set_values(text, "mediacrawler", {"home": "C:\\Tools\\MediaCrawler"})
        data = tomllib.loads(text)
        self.assertEqual(data["mediacrawler"]["home"], "C:\\Tools\\MediaCrawler")
        self.assertEqual(data["media"]["ffmpeg"], "ffmpeg")
        self.assertEqual(data["handoff"]["bank"], "../collected-bank")


class PatchFilesTest(unittest.TestCase):
    def test_patches_ship_with_the_package_and_use_gleaners_variables(self):
        with patch_files() as patches:
            self.assertEqual([name[:3] for name in patches], ["01-", "02-", "03-", "04-"])
            texts = {name: path.read_text(encoding="utf-8") for name, path in patches.items()}
        for name, text in texts.items():
            self.assertTrue(text.startswith("diff --git a/"), name)
        (time_range,) = [text for name, text in texts.items() if "time-range" in name]
        variables = set()
        for platform in ("dy", "zhihu", "bili"):
            variables |= set(search_window(platform, NOW, NOW).env)
        for variable in variables:
            self.assertIn(f'"{variable}"', time_range)


if __name__ == "__main__":
    unittest.main()
