#!/usr/bin/env python3
"""Regression checks for Recipe process liveness probing."""

from __future__ import annotations

import os
import subprocess
import sys
import time
import unittest
from pathlib import Path


RECIPE_TOOLS = Path(__file__).resolve().parents[1] / "recipe"
sys.path.insert(0, str(RECIPE_TOOLS))

from process_liveness import pid_alive  # noqa: E402


class ProcessLivenessTest(unittest.TestCase):
    def test_rejects_invalid_pids(self) -> None:
        for value in (None, 0, -1, "1", True):
            with self.subTest(value=value):
                self.assertFalse(pid_alive(value))

    def test_current_process_is_alive(self) -> None:
        self.assertTrue(pid_alive(os.getpid()))

    def test_dead_child_is_not_alive(self) -> None:
        child = subprocess.Popen([sys.executable, "-c", "pass"])
        pid = child.pid
        child.wait(timeout=10)
        deadline = time.monotonic() + 2
        while pid_alive(pid) and time.monotonic() < deadline:
            time.sleep(0.01)
        self.assertFalse(pid_alive(pid))

    def test_repeated_probe_does_not_terminate_live_child(self) -> None:
        child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
        try:
            for _ in range(20):
                self.assertTrue(pid_alive(child.pid))
                self.assertIsNone(child.poll())
        finally:
            child.terminate()
            child.wait(timeout=10)


if __name__ == "__main__":
    unittest.main()
