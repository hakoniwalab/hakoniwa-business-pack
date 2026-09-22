from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from tools import package_portable_workspace as portable


class PortableWorkspacePackageTest(unittest.TestCase):
    def test_windows_python_prefers_portable_root_executable(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "Scripts").mkdir()
            (root / "Scripts" / "python.exe").write_bytes(b"venv")
            self.assertEqual(
                portable._windows_python(root),
                root / "Scripts" / "python.exe",
            )
            (root / "python.exe").write_bytes(b"portable")
            self.assertEqual(portable._windows_python(root), root / "python.exe")

    def test_embedded_pth_enables_site_packages_and_site(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            path = root / "python312._pth"
            path.write_text(
                "python312.zip\n.\n# import site\n",
                encoding="utf-8",
            )
            portable._rewrite_embedded_python_pth(root)
            lines = path.read_text(encoding="utf-8").splitlines()
            self.assertIn(r"Lib\site-packages", lines)
            self.assertIn("import site", lines)

    def test_absolute_windows_pth_entry_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            site_packages = Path(temporary)
            (site_packages / "bad.pth").write_text(
                "C:\\dev\\hakoniwa\\python\n",
                encoding="utf-8",
            )
            with self.assertRaises(portable.PortablePackageError):
                portable._reject_absolute_pth_entries(site_packages)

    def test_foundation_copy_excludes_rebuildable_build_tree(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary) / "foundation"
            (source / "build" / "deep" / "intermediate.txt").parent.mkdir(
                parents=True
            )
            (source / "build" / "deep" / "intermediate.txt").write_text("x")
            (source / "install" / "bin").mkdir(parents=True)
            (source / "install" / "bin" / "runtime.dll").write_bytes(b"runtime")
            destination = Path(temporary) / "package" / "foundation"
            portable._copy_foundation(source, destination)
            self.assertFalse((destination / "build").exists())
            self.assertTrue((destination / "install" / "bin" / "runtime.dll").is_file())

    def test_start_script_uses_packaged_python_and_web_ui_recipe(self) -> None:
        script = portable._render_start_batch()
        self.assertIn(
            r"work\foundation\install\python\python.exe",
            script,
        )
        self.assertIn(r"tools\workspace.py run", script)
        self.assertIn(
            r"tools\recipe\city_world_web_ui.py start",
            script,
        )
        self.assertIn("--open-browser", script)

    def test_control_scripts_use_same_portable_runtime(self) -> None:
        for command in ("status", "stop"):
            script = portable._render_control_batch(command)
            self.assertIn(
                rf"tools\recipe\city_world_web_ui.py {command}",
                script,
            )
            self.assertIn(
                r"work\foundation\install\python\python.exe",
                script,
            )


if __name__ == "__main__":
    unittest.main()
