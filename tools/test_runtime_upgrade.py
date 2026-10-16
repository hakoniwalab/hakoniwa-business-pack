"""foundation_lib/runtime_upgrade.py: the runtime directory across a Core upgrade
(hakoniwa-core-cpp#62: a legacy flock.bin stops every master start of the new Core)."""
from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))

from foundation_lib import runtime_upgrade  # noqa: E402

NEW = {"hako_cmd": "1.1.0", "layout": "2"}
OLD = {"hako_cmd": "1.0.1", "layout": "2"}


class RuntimeUpgradeTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.work = root / "work"
        self.mmap = self.work / "foundation/runtime/mmap"
        self.mmap.mkdir(parents=True)
        self.config = self.work / "foundation/config/cpp_core_config.json"
        self.config.parent.mkdir(parents=True)
        self.config.write_text(json.dumps({"core_mmap_path": str(self.mmap)}), encoding="utf-8")
        self.hako_cmd = root / "hako-cmd"
        self.hako_cmd.write_text("", encoding="utf-8")

    def tearDown(self):
        self.tmp.cleanup()

    def files(self, legacy=True):
        for name in ("mmap-0xff.bin", "mmap-0x100.bin", "pdu_init.lock", "pro_init.lock"):
            (self.mmap / name).write_bytes(b"x")
        (self.mmap / "flock.bin").write_bytes(b"\0" * (4096 if legacy else 64))
        (self.mmap / "notes.txt").write_text("keep", encoding="utf-8")

    def run_check(self, core, report=None):
        with mock.patch.object(runtime_upgrade, "installed_core", return_value=core):
            check = runtime_upgrade.inspect(self.config, self.hako_cmd, self.work)
        return check, runtime_upgrade.reconcile(check, report=report or (lambda text: None))

    def session(self, state="RUNNING", pid=None):
        path = self.work / "recipes/urban-car-rc/runtime/launcher-session.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"state": state, "pid": pid or os.getpid()}), encoding="utf-8")

    def test_legacy_files_after_the_upgrade_are_moved_aside_and_the_directory_stamped(self):
        self.files(legacy=True)
        check, outcome = self.run_check(NEW)
        self.assertEqual(outcome, "moved")
        self.assertEqual(sorted(p.name for p in self.mmap.iterdir()), [".hakoniwa-runtime.json", "notes.txt"])
        backups = list(self.mmap.parent.glob("mmap-upgrade-*"))
        self.assertEqual(len(backups), 1)
        self.assertEqual(len(list(backups[0].iterdir())), 5)  # kept as evidence
        self.assertEqual(json.loads((self.mmap / ".hakoniwa-runtime.json").read_text())["hako_cmd"], "1.1.0")

    def test_a_running_session_keeps_the_files_and_warns(self):
        self.files(legacy=True)
        self.session("RUNNING")
        messages = []
        _check, outcome = self.run_check(NEW, report=messages.append)
        self.assertEqual(outcome, "in-use")
        self.assertTrue((self.mmap / "flock.bin").is_file())
        self.assertIn("Stop it", messages[0])

    def test_a_finished_session_does_not_hold_the_upgrade(self):
        self.files(legacy=True)
        self.session("TERMINATED")
        self.assertEqual(self.run_check(NEW)[1], "moved")

    def test_files_of_the_installed_core_are_stamped_not_moved(self):
        self.files(legacy=True)          # the old Core itself: its own 4096-byte flock.bin
        self.assertEqual(self.run_check(OLD)[1], "stamped")
        self.assertEqual(self.run_check(OLD)[1], "ok")
        self.assertTrue((self.mmap / "flock.bin").is_file())

    def test_a_core_change_after_the_stamp_moves_the_files(self):
        self.files(legacy=False)
        self.run_check(NEW)
        self.assertEqual(self.run_check({"hako_cmd": "1.2.0", "layout": "3"})[1], "moved")

    def test_no_foundation_core_yet_is_skipped(self):
        self.files(legacy=True)
        self.assertEqual(self.run_check(None)[1], "skipped")
        self.assertTrue((self.mmap / "flock.bin").is_file())

    def test_only_the_newest_backups_are_kept(self):
        for i in range(5):
            (self.mmap.parent / f"mmap-upgrade-2026010{i}-000000").mkdir()
        self.files(legacy=True)
        self.run_check(NEW)
        self.assertEqual(len(list(self.mmap.parent.glob("mmap-upgrade-*"))), runtime_upgrade.BACKUPS_KEPT)

    def test_hako_cmd_version_is_read(self):
        output = "hako-cmd version 1.1.0\nHAKO_SHM_MAGIC=0x48414b4f\nHAKO_SHM_LAYOUT_VERSION=2\n"
        with mock.patch.object(runtime_upgrade.subprocess, "run",
                               return_value=mock.Mock(stdout=output, returncode=0)):
            self.assertEqual(runtime_upgrade.installed_core(self.hako_cmd), {"hako_cmd": "1.1.0", "layout": "2"})


if __name__ == "__main__":
    unittest.main()
