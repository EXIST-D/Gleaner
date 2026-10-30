import json
import sys
import unittest

from gleaner.auto import outcome_lines, run_auto
from gleaner.bank import BankReader, Held
from gleaner.cli import main
from gleaner.errors import GleanerError
from gleaner.imports import sync_imports
from gleaner.packer import MANUAL_IMPORT, NOT_IMPORTING, build_pack
from gleaner.pipeline import import_export
from gleaner.store import Store
from tests.support import (
    BILI_RECORDING,
    INTERVIEW_NOTE,
    NOW,
    TEXT_NOTE,
    WorkspaceTestCase,
    fake_extract,
    make_config,
    write_export,
    write_records,
)
from tests.test_auto import Clock, FakeDriver

AA01 = "https://www.xiaohongshu.com/explore/aa01"
CC03 = "https://www.xiaohongshu.com/explore/cc03"
RECORDING = "https://www.bilibili.com/video/av1150000002"

FAKE_IBANK = r"""
import json, sys
from pathlib import Path
here = Path(__file__).parent
args = sys.argv[1:]
if args[0] != "sources":
    print("invalid choice: " + args[0], file=sys.stderr)
    sys.exit(2)
option = lambda name: int(args[args.index(name) + 1])
rows = json.loads((here / "sources.json").read_text(encoding="utf-8"))
with (here / "calls.log").open("a", encoding="utf-8") as log:
    log.write(f"{option('--offset')}\n")
offset, limit = option("--offset"), option("--limit")
print(json.dumps({"ok": True, "result": {"total": len(rows), "offset": offset, "sources": rows[offset:offset + limit]}}))
"""


def source(url, *, occurrences=1, questions=1):
    return {
        "id": "src_" + url[-6:],
        "type": "image",
        "source_url": url,
        "occurrences": occurrences,
        "questions": questions,
    }


class BankWithSources(WorkspaceTestCase):
    """A workspace whose bank (a fake ibank.py with a `sources` command) holds the given sources."""

    def setUp(self):
        super().setUp()
        self.tools = self.workspace / "ibank"
        self.tools.mkdir()
        self.bank_dir = self.workspace / "bank"
        (self.bank_dir / "data").mkdir(parents=True)
        (self.bank_dir / "manifest.json").write_text("{}", encoding="utf-8")
        (self.tools / "ibank.py").write_text(FAKE_IBANK, encoding="utf-8")
        (self.workspace / "gleaner.toml").write_text(
            f'keywords = ["后端 面经"]\n[handoff]\nbank = {json.dumps(str(self.bank_dir))}\n'
            f"ibank = {json.dumps(str(self.tools / 'ibank.py'))}\npython = {json.dumps(sys.executable)}\n",
            encoding="utf-8",
        )
        self.config = make_config(
            self.workspace,
            handoff={
                "bank": str(self.bank_dir),
                "ibank": str(self.tools / "ibank.py"),
                "python": sys.executable,
            },
        )

    def hold(self, *rows):
        (self.tools / "sources.json").write_text(json.dumps(rows), encoding="utf-8")
        (self.bank_dir / "data" / "sources.jsonl").write_text(str(len(rows)), encoding="utf-8")

    def calls(self):
        return (self.tools / "calls.log").read_text(encoding="utf-8").splitlines()


class HeldTest(BankWithSources):
    def test_sources_are_grouped_by_url_across_pages_and_cached_until_the_bank_changes(self):
        rows = [source(AA01, questions=2), source(AA01, occurrences=3, questions=1), source(CC03)]
        rows += [source(f"https://example.org/p/{n}") for n in range(200)]
        self.hold(*rows)
        reader = BankReader(self.config.handoff)
        held = reader.held()
        self.assertEqual(held[AA01], Held(sources=2, occurrences=4, questions=3))
        self.assertEqual(held[CC03], Held(1, 1, 1))
        self.assertEqual(len(held), 202)
        self.assertEqual(self.calls(), ["0", "200"], "pages of 200 until the total is read")
        reader.held()
        self.assertEqual(len(self.calls()), 2, "cached")
        self.hold(source(CC03))
        self.assertEqual(set(reader.held()), {CC03})
        self.assertEqual(len(self.calls()), 3)

    def test_an_interview_bank_without_the_command_is_reported(self):
        (self.tools / "ibank.py").write_text(
            "import sys\nsys.stderr.write('invalid choice')\nsys.exit(2)\n", encoding="utf-8"
        )
        with self.assertRaisesRegex(GleanerError, "invalid choice"):
            BankReader(self.config.handoff).held()


class SyncTest(BankWithSources):
    def setUp(self):
        super().setUp()
        source_dir = write_export(self.workspace / "external", [INTERVIEW_NOTE, TEXT_NOTE], images={})
        with Store.open(self.workspace, create=True) as store:
            import_export(store, self.config, source_dir, now=NOW)

    def test_posts_imported_by_hand_are_recorded_and_packs_that_wait_are_listed(self):
        with Store.open(self.workspace) as store:
            build_pack(store, self.config, now=NOW, keys=["xhs:aa01"])
            report = sync_imports(store, {CC03: Held(1, 2, 2)}, now=NOW, adopt=False)
            self.assertEqual(report.adopted, ["xhs:cc03"])
            self.assertIsNone(store.get("xhs:cc03").packed_in, "dry run changes nothing")
            self.assertEqual(report.waiting, {store.get("xhs:aa01").packed_in: ["xhs:aa01"]})
            self.assertEqual((report.holding, report.questions), (1, 2))

            report = sync_imports(store, {CC03: Held(1, 2, 2)}, now=NOW)
            self.assertEqual(store.get("xhs:cc03").packed_in, MANUAL_IMPORT)
            again = sync_imports(store, {CC03: Held(1, 2, 2), AA01: Held(1, 1, 1)}, now=NOW)
            self.assertEqual((again.adopted, again.waiting, again.holding), ([], {}, 2))
            self.assertEqual(
                report.as_dict()["waiting"][store.get("xhs:aa01").packed_in]["missing"], ["xhs:aa01"]
            )

    def test_cli_imports_lists_and_records(self):
        self.hold(source(CC03), source(AA01))
        with Store.open(self.workspace) as store:
            build_pack(store, self.config, now=NOW, keys=["xhs:aa01"])
        self.assertEqual(len(self.run_cli("imports", "--dry-run")["adopted"]), 1)
        with Store.open(self.workspace) as store:
            self.assertIsNone(store.get("xhs:cc03").packed_in)
        report = self.run_cli("imports")
        self.assertEqual((report["adopted"], report["waiting"], report["holding"]), (["xhs:cc03"], {}, 2))
        with Store.open(self.workspace) as store:
            self.assertEqual(store.get("xhs:cc03").packed_in, MANUAL_IMPORT)

    def test_posts_not_to_be_imported_stop_counting_as_waiting(self):
        with Store.open(self.workspace) as store:
            pack = build_pack(store, self.config, now=NOW).pack_id
            self.assertEqual(len(sync_imports(store, {}, now=NOW, adopt=False).waiting[pack]), 2)
        report = self.run_cli("skip-import", "xhs:aa01")
        self.assertEqual(report["marked"], ["xhs:aa01"])
        self.assertEqual(self.run_cli("skip-import", "xhs:aa01")["marked"], [], "already recorded")
        with Store.open(self.workspace) as store:
            self.assertEqual(store.get("xhs:aa01").packed_in, NOT_IMPORTING)
            after = sync_imports(store, {}, now=NOW, adopt=False)
        self.assertEqual(after.waiting, {pack: ["xhs:cc03"]})
        self.assertEqual(after.pack_sizes, {pack: 1})

    def test_skip_import_needs_known_posts(self):
        import contextlib
        import io

        with contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(main(["--workspace", str(self.workspace), "skip-import", "xhs:nothing"]), 1)

    def run_cli(self, *argv):
        import contextlib
        import io

        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(main(["--workspace", str(self.workspace), *argv, "--json"]), 0)
        return json.loads(out.getvalue())


class AutoImportsTest(BankWithSources):
    def drive(self, store, drivers, **kwargs):
        self.clock = getattr(self, "clock", Clock())
        queue = {platform: list(items) for platform, items in drivers.items()}
        return run_auto(
            store,
            self.config,
            clock=self.clock.now,
            sleep=self.clock.sleep,
            fetcher_factory=lambda config, platform, **options: queue[platform].pop(0),
            extract=fake_extract,
            **kwargs,
        )

    def test_a_recording_the_bank_already_holds_is_neither_downloaded_nor_packed(self):
        self.hold(source(RECORDING, questions=20))
        search = FakeDriver(lambda raw: write_records(raw, "bili", [BILI_RECORDING]))
        with Store.open(self.workspace, create=True) as store:
            outcome = self.drive(
                store, {"bili": [search]}, platforms=["bili"], bank=BankReader(self.config.handoff)
            )
            self.assertEqual(
                [(step.kind, step.status) for step in outcome.steps],
                [("crawl", "ok"), ("imports", "ok"), ("pack", "skipped")],
            )
            self.assertEqual(store.get("bili:1150000002").packed_in, MANUAL_IMPORT)
            self.assertEqual(outcome.held_recordings, 0)
            self.assertEqual(outcome.imports.adopted, ["bili:1150000002"])
            self.assertIn("新记手工导入 1 篇", outcome.steps[1].message)

    def test_packs_still_waiting_are_reported_at_the_end(self):
        self.hold()
        with Store.open(self.workspace, create=True) as store:
            outcome = self.drive(
                store,
                {"xhs": [FakeDriver(write_export)]},
                platforms=["xhs"],
                bank=BankReader(self.config.handoff),
            )
            self.assertEqual(list(outcome.imports.waiting), [outcome.pack.pack_id])
            self.assertEqual(len(outcome.imports.waiting[outcome.pack.pack_id]), outcome.pack.posts)
            self.assertIn("1 个素材包还在等导入", "\n".join(outcome_lines(outcome)))
            self.assertEqual(
                outcome.as_dict()["imports"]["waiting"][outcome.pack.pack_id]["posts"], outcome.pack.posts
            )

    def test_a_bank_that_cannot_be_read_only_skips_the_step(self):
        (self.tools / "ibank.py").write_text("import sys\nsys.exit(2)\n", encoding="utf-8")
        with Store.open(self.workspace, create=True) as store:
            outcome = self.drive(
                store,
                {"xhs": [FakeDriver(write_export)]},
                platforms=["xhs"],
                bank=BankReader(self.config.handoff),
            )
            self.assertEqual([(s.kind, s.status) for s in outcome.steps][1], ("imports", "skipped"))
            self.assertFalse(outcome.degraded)
            self.assertIsNone(outcome.imports)
            self.assertIsNotNone(outcome.pack)


if __name__ == "__main__":
    unittest.main()
