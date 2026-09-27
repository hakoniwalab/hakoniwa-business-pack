from __future__ import annotations

import contextlib
import io
import json
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))

import mujoco_model_compiler  # noqa: E402


def progress_events(output: str) -> list[dict]:
    marker = "[HAKO_PROGRESS] "
    return [json.loads(line[len(marker):]) for line in output.splitlines() if line.startswith(marker)]


class CompileProgressTest(unittest.TestCase):
    def test_long_compile_reports_start_heartbeats_and_done(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "city-cars.xml"
            source.write_text("<mujoco/>", encoding="utf-8")
            output = io.StringIO()
            with mock.patch.object(mujoco_model_compiler, "HEARTBEAT_SEC", 0.02), \
                    contextlib.redirect_stdout(output):
                with mujoco_model_compiler._compile_progress(source):
                    time.sleep(0.15)
        events = progress_events(output.getvalue())
        self.assertEqual({event["phase"] for event in events}, {"mujoco_compile"})
        self.assertEqual({event["model"] for event in events}, {"city-cars.xml"})
        self.assertEqual(events[0]["elapsed_sec"], 0)
        self.assertTrue(events[-1]["done"])
        self.assertGreaterEqual(len(events), 3, "start, at least one heartbeat, done")
        self.assertIn("still compiling", output.getvalue())

    def test_short_compile_reports_only_start_and_done(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "small.xml"
            source.write_text("<mujoco/>", encoding="utf-8")
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                with mujoco_model_compiler._compile_progress(source):
                    pass
        events = progress_events(output.getvalue())
        self.assertEqual(len(events), 2)
        self.assertNotIn("done", events[0])
        self.assertTrue(events[1]["done"])


if __name__ == "__main__":
    unittest.main()
