#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

SCRIPT = Path(__file__).resolve().parents[1] / "foundation.py"
SPEC = importlib.util.spec_from_file_location("business_pack_foundation_toolchain_prefix", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
foundation = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = foundation
SPEC.loader.exec_module(foundation)


class FoundationToolchainPrefixTest(unittest.TestCase):
    def make_vcpkg(self, root: Path) -> Path:
        vcpkg = root / "external" / "vcpkg"
        vcpkg.mkdir(parents=True)
        (vcpkg / ("vcpkg.exe" if sys.platform == "win32" else "vcpkg")).write_text(
            "test\n", encoding="utf-8"
        )
        cmake = vcpkg / "scripts" / "buildsystems" / "vcpkg.cmake"
        cmake.parent.mkdir(parents=True)
        cmake.write_text("# test\n", encoding="utf-8")
        return vcpkg

    def test_relative_and_absolute_install_dir_resolve_to_same_config(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            vcpkg = self.make_vcpkg(root)
            install = root / "work" / "alt-prefix" / "install"
            old_cwd = Path.cwd()
            try:
                os.chdir(root)
                with mock.patch.object(
                    foundation, "repository_root", return_value=root
                ), mock.patch.object(foundation, "warn_if_workspace_invalid"):
                    relative_result = foundation.main(
                        [
                            "toolchain",
                            "--recipe-id",
                            "test-recipe",
                            "--install-dir",
                            "work/alt-prefix/install",
                            "--vcpkg-root",
                            str(vcpkg),
                        ]
                    )
                paths = foundation.resolve_workspace(root, "test-recipe", install.parent)
                config = foundation.foundation_toolchain_path(paths)
                relative_payload = json.loads(config.read_text(encoding="utf-8"))
                config.unlink()
                with mock.patch.object(
                    foundation, "repository_root", return_value=root
                ), mock.patch.object(foundation, "warn_if_workspace_invalid"):
                    absolute_result = foundation.main(
                        [
                            "toolchain",
                            "--recipe-id",
                            "test-recipe",
                            "--install-dir",
                            str(install),
                            "--vcpkg-root",
                            str(vcpkg),
                        ]
                    )
            finally:
                os.chdir(old_cwd)

            self.assertEqual(relative_result, 0)
            self.assertEqual(absolute_result, 0)
            self.assertEqual(
                relative_payload,
                json.loads(config.read_text(encoding="utf-8")),
            )

    def test_failed_windows_component_reports_selected_prefix_and_config(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            paths = foundation.resolve_workspace(root, "test-recipe")
            source = root / "athrill-target-v850e2m"
            hako = source / "tools" / "hako.py"
            hako.parent.mkdir(parents=True)
            hako.write_text("# test\n", encoding="utf-8")
            python = foundation.foundation_python_executable(paths.foundation_python)
            plan = {
                "blocked": [],
                "recipe": str(root / "test-recipe.yaml"),
                "actions": [
                    {
                        "component": "athrill-target-v850e2m",
                        "source": str(source),
                        "operations": ["doctor"],
                        "requirements": {},
                    }
                ],
            }
            contract = {"version": "3.12.0", "soabi": "cpython-312-test"}
            with mock.patch.object(
                foundation,
                "ensure_foundation_python",
                return_value=(python, contract),
            ), mock.patch.object(
                foundation.platform, "system", return_value="Windows"
            ), mock.patch.object(
                foundation.subprocess,
                "run",
                return_value=subprocess.CompletedProcess([], 1),
            ):
                with self.assertRaises(foundation.FoundationError) as raised:
                    foundation.execute_build_plan(plan, paths)

            message = str(raised.exception)
            self.assertIn(str(paths.install_prefix), message)
            self.assertIn(str(foundation.foundation_toolchain_path(paths)), message)
            self.assertIn("foundation.py toolchain", message)


if __name__ == "__main__":
    unittest.main()
