#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
import io
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

GUARD_SCRIPT = Path(__file__).with_name("workspace_guard.py")
SPEC = importlib.util.spec_from_file_location("business_pack_workspace_guard", GUARD_SCRIPT)
assert SPEC is not None and SPEC.loader is not None
guard = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(guard)


def _valid_environment(root: Path) -> dict[str, str]:
    expected = guard._expected_paths(root)
    return {
        "HAKONIWA_WORKSPACE_ACTIVE": "1",
        "HAKONIWA_WORKSPACE_ROOT": str(expected["root"]),
        "HAKONIWA_HOME": str(expected["home"]),
        "VIRTUAL_ENV": str(expected["virtual_env"]),
        "HAKO_CONFIG_PATH": str(expected["config"]),
        "HAKO_PDU_ENDPOINT_RUNTIME_DIRS": str(expected["foundation_bin"]),
        "PATH": os.pathsep.join(
            (str(expected["python_bin"]), str(expected["foundation_bin"]), "/ambient/bin")
        ),
        "PYTHONNOUSERSITE": "1",
    }


class WorkspaceGuardTest(unittest.TestCase):
    def test_accepts_workspace_identity_before_foundation_artifacts_exist(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "business-pack"
            environment = _valid_environment(root)

            self.assertEqual(guard.validate_workspace(root, environment), [])
            self.assertFalse((root / "work" / "foundation" / "install").exists())

    def test_missing_workspace_reports_a_single_primary_diagnostic(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            errors = guard.validate_workspace(Path(temporary), {})

        self.assertEqual(len(errors), 1)
        self.assertIn("HAKONIWA_WORKSPACE_ACTIVE", errors[0])

    def test_detects_workspace_from_another_repository(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            selected = base / "selected"
            environment = _valid_environment(base / "other")

            errors = guard.validate_workspace(selected, environment)

        self.assertTrue(any("HAKONIWA_WORKSPACE_ROOT" in error for error in errors))
        self.assertTrue(any("VIRTUAL_ENV" in error for error in errors))
        self.assertTrue(any("PATH entry 1" in error for error in errors))

    def test_warning_is_advisory_and_includes_recovery_commands(self) -> None:
        output = io.StringIO()
        with tempfile.TemporaryDirectory() as temporary:
            errors = guard.warn_if_workspace_invalid(
                Path(temporary), {}, stream=output
            )

        self.assertTrue(errors)
        text = output.getvalue()
        self.assertIn("[WARNING]", text)
        self.assertIn("workspace.py enter", text)
        self.assertIn("workspace.py run -- <command>", text)

    def test_standalone_check_returns_nonzero_for_invalid_workspace(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            with mock.patch.dict(os.environ, {}, clear=True):
                with mock.patch.object(guard.sys, "stderr", io.StringIO()):
                    result = guard.main(["--root", temporary])

        self.assertEqual(result, 1)


if __name__ == "__main__":
    unittest.main()
