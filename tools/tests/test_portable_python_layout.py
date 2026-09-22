from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tools import workspace
from tools.foundation_lib import runtime as foundation_runtime
from tools.recipe import plateau_citygml_mujoco_walls as city_world_recipe


class PortablePythonLayoutTest(unittest.TestCase):
    def test_workspace_uses_scripts_for_normal_windows_venv(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            python, python_bin = workspace.foundation_python_layout(
                root,
                windows=True,
            )
            self.assertEqual(python, root / "Scripts" / "python.exe")
            self.assertEqual(python_bin, root / "Scripts")

    def test_workspace_prefers_portable_windows_runtime(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "python.exe").write_bytes(b"portable")
            python, python_bin = workspace.foundation_python_layout(
                root,
                windows=True,
            )
            self.assertEqual(python, root / "python.exe")
            self.assertEqual(python_bin, root)

    def test_foundation_runtime_prefers_portable_windows_runtime(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            with mock.patch.object(foundation_runtime.sys, "platform", "win32"):
                self.assertEqual(
                    foundation_runtime.foundation_python_executable(root),
                    root / "Scripts" / "python.exe",
                )
                (root / "python.exe").write_bytes(b"portable")
                self.assertEqual(
                    foundation_runtime.foundation_python_executable(root),
                    root / "python.exe",
                )

    def test_city_world_recipe_prefers_portable_windows_runtime(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self.assertEqual(
                city_world_recipe._python_executable(root, windows=True),
                root / "Scripts" / "python.exe",
            )
            (root / "python.exe").write_bytes(b"portable")
            self.assertEqual(
                city_world_recipe._python_executable(root, windows=True),
                root / "python.exe",
            )


if __name__ == "__main__":
    unittest.main()
