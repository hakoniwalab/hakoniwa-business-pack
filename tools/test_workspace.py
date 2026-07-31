#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

WORKSPACE_SCRIPT = Path(__file__).with_name("workspace.py")
SPEC = importlib.util.spec_from_file_location("business_pack_workspace", WORKSPACE_SCRIPT)
assert SPEC is not None and SPEC.loader is not None
workspace = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = workspace
SPEC.loader.exec_module(workspace)


class WorkspaceEnvironmentTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name) / "business-pack"
        self.root.mkdir(parents=True)
        self.paths = workspace.resolve_workspace(self.root)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_layout_stays_under_business_pack_work(self) -> None:
        self.assertEqual(
            self.paths.foundation_root,
            self.root.resolve() / "work" / "foundation",
        )
        for path in (
            self.paths.install_prefix,
            self.paths.foundation_python_root,
            self.paths.foundation_config,
            self.paths.activate_posix,
            self.paths.activate_powershell,
        ):
            self.assertTrue(path.is_relative_to(self.root.resolve() / "work"))

    def test_environment_removes_ambient_python_discovery(self) -> None:
        base = {
            "PATH": os.pathsep.join(("/legacy/bin", "/another/bin")),
            "PYTHONPATH": "/legacy/hakopy",
            "PYTHONHOME": "/legacy/python",
            "HOME": "/home/test",
        }

        env = workspace.build_environment(self.paths, base)

        self.assertNotIn("PYTHONPATH", env)
        self.assertNotIn("PYTHONHOME", env)
        self.assertEqual(env["PYTHONNOUSERSITE"], "1")
        self.assertEqual(env["HAKONIWA_WORKSPACE_ACTIVE"], "1")
        self.assertEqual(env["HAKONIWA_WORKSPACE_ROOT"], str(self.root.resolve()))
        self.assertEqual(env["HAKONIWA_HOME"], str(self.paths.install_prefix))
        self.assertEqual(env["VIRTUAL_ENV"], str(self.paths.foundation_python_root))
        path_entries = env["PATH"].split(os.pathsep)
        self.assertEqual(path_entries[0], str(self.paths.foundation_python_bin))
        self.assertEqual(path_entries[1], str(self.paths.foundation_bin))
        self.assertEqual(path_entries.count(str(self.paths.foundation_bin)), 1)
        self.assertEqual(env["HOME"], "/home/test")

    def test_prepare_generates_both_activation_surfaces(self) -> None:
        posix, powershell = workspace.prepare(self.paths)

        self.assertTrue(posix.is_file())
        self.assertTrue(powershell.is_file())
        posix_text = posix.read_text(encoding="utf-8")
        powershell_text = powershell.read_text(encoding="utf-8")
        self.assertIn("unset PYTHONPATH", posix_text)
        self.assertIn("unset PYTHONHOME", posix_text)
        self.assertIn("deactivate_hakoniwa", posix_text)
        self.assertIn(str(self.paths.foundation_python_bin), posix_text)
        self.assertIn("Remove-Item Env:\\PYTHONPATH", powershell_text)
        self.assertIn("Remove-Item Env:\\PYTHONHOME", powershell_text)
        self.assertIn("Exit-HakoniwaWorkspace", powershell_text)
        self.assertIn(str(self.paths.foundation_python_bin), powershell_text)

    @unittest.skipUnless(shutil.which("bash"), "bash is required")
    def test_posix_activation_restores_previous_environment(self) -> None:
        workspace.prepare(self.paths)
        command = f"""
set -eu
export PYTHONPATH=/legacy/hakopy
export PYTHONHOME=/legacy/python
export HAKO_CONFIG_PATH=/legacy/config.json
. {self.paths.activate_posix}
[ -z "${{PYTHONPATH+x}}" ]
[ -z "${{PYTHONHOME+x}}" ]
[ "$HAKONIWA_WORKSPACE_ACTIVE" = 1 ]
[ "$HAKO_CONFIG_PATH" = "{self.paths.foundation_config}" ]
deactivate_hakoniwa
[ "$PYTHONPATH" = /legacy/hakopy ]
[ "$PYTHONHOME" = /legacy/python ]
[ "$HAKO_CONFIG_PATH" = /legacy/config.json ]
[ -z "${{HAKONIWA_WORKSPACE_ACTIVE+x}}" ]
"""
        result = subprocess.run(
            ["bash", "-c", command],
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_snapshot_validation_accepts_workspace_owned_modules(self) -> None:
        snapshot = {
            "executable": str(self.paths.foundation_python),
            "prefix": str(self.paths.foundation_python_root),
            "modules": {
                "hakopy": str(self.paths.foundation_core_python / "hakopy.so"),
                "hakoniwa_pdu": str(
                    self.paths.foundation_python_root
                    / "lib"
                    / "python3.12"
                    / "site-packages"
                    / "hakoniwa_pdu"
                    / "__init__.py"
                ),
                "hakoniwa_pdu_endpoint": str(
                    self.paths.foundation_python_root
                    / "lib"
                    / "python3.12"
                    / "site-packages"
                    / "hakoniwa_pdu_endpoint"
                    / "__init__.py"
                ),
            },
            "errors": {},
        }

        self.assertEqual(workspace.validate_snapshot(snapshot, self.paths), [])

    def test_snapshot_validation_rejects_shadowed_hakopy(self) -> None:
        snapshot = {
            "executable": str(self.paths.foundation_python),
            "prefix": str(self.paths.foundation_python_root),
            "modules": {
                "hakopy": str(self.root.parent / "legacy" / "hakopy.pyd"),
                "hakoniwa_pdu": str(
                    self.paths.foundation_python_root / "site-packages" / "hakoniwa_pdu.py"
                ),
                "hakoniwa_pdu_endpoint": str(
                    self.paths.foundation_python_root
                    / "site-packages"
                    / "hakoniwa_pdu_endpoint.py"
                ),
            },
            "errors": {},
        }

        errors = workspace.validate_snapshot(snapshot, self.paths)

        self.assertTrue(any("hakopy resolved outside" in error for error in errors))

    def test_run_command_does_not_inherit_pythonpath(self) -> None:
        script = "import os, sys; sys.exit(0 if 'PYTHONPATH' not in os.environ else 9)"
        with mock.patch.dict(os.environ, {"PYTHONPATH": "/legacy"}, clear=False):
            result = workspace.run_command(
                self.paths,
                [sys.executable, "-c", script],
            )
        self.assertEqual(result, 0)


if __name__ == "__main__":
    unittest.main()
