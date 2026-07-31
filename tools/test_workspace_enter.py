#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

WORKSPACE_SCRIPT = Path(__file__).with_name("workspace.py")
SPEC = importlib.util.spec_from_file_location(
    "business_pack_workspace_enter",
    WORKSPACE_SCRIPT,
)
assert SPEC is not None and SPEC.loader is not None
workspace = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = workspace
SPEC.loader.exec_module(workspace)


class WorkspaceEnterTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name) / "business-pack"
        self.root.mkdir(parents=True)
        bootstrap = self.root / "foundation" / "python"
        bootstrap.mkdir(parents=True)
        (bootstrap / "hakoniwa_workspace_bootstrap.py").write_text(
            "# test bootstrap\n",
            encoding="utf-8",
        )
        self.paths = workspace.resolve_workspace(self.root)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_enter_prepares_before_building_environment(self) -> None:
        events: list[str] = []
        env = {"PATH": os.environ.get("PATH", ""), "SHELL": "/bin/zsh"}
        result = mock.Mock(returncode=0)

        with (
            mock.patch.object(workspace.os, "name", "posix"),
            mock.patch.object(
                workspace,
                "prepare",
                side_effect=lambda paths: events.append("prepare"),
            ),
            mock.patch.object(
                workspace,
                "build_environment",
                side_effect=lambda paths: events.append("environment") or dict(env),
            ),
            mock.patch.object(workspace.subprocess, "run", return_value=result) as run,
        ):
            self.assertEqual(workspace.enter(self.paths), 0)

        self.assertEqual(events, ["prepare", "environment"])
        child_env = run.call_args.kwargs["env"]
        self.assertEqual(child_env["PROMPT"], "(hako) %n@%m %1~ %# ")
        self.assertEqual(child_env["PS1"], child_env["PROMPT"])

    def test_run_prepares_before_starting_command(self) -> None:
        events: list[str] = []
        result = mock.Mock(returncode=0)

        with (
            mock.patch.object(
                workspace,
                "prepare",
                side_effect=lambda paths: events.append("prepare"),
            ),
            mock.patch.object(
                workspace,
                "build_environment",
                side_effect=lambda paths: events.append("environment") or {},
            ),
            mock.patch.object(workspace.subprocess, "run", return_value=result),
        ):
            self.assertEqual(workspace.run_command(self.paths, ["echo", "ok"]), 0)

        self.assertEqual(events, ["prepare", "environment"])

    def test_shell_commands_expose_hako_prompt(self) -> None:
        powershell = workspace._interactive_shell_command("pwsh.exe")
        cmd = workspace._interactive_shell_command("cmd.exe")
        fish = workspace._interactive_shell_command("fish")

        self.assertIn("(hako)", powershell[-1])
        self.assertEqual(cmd, ["cmd.exe", "/d", "/k", "prompt (hako) $P$G"])
        self.assertIn("(hako)", fish[-2])

    def test_bash_prompt_is_added_to_child_environment(self) -> None:
        env: dict[str, str] = {}
        workspace._apply_prompt_environment("/bin/bash", env)
        self.assertTrue(env["PS1"].startswith("(hako) "))


if __name__ == "__main__":
    unittest.main()
