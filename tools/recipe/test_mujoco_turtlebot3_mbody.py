from __future__ import annotations

import argparse
import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


SCRIPT = Path(__file__).with_name("mujoco_turtlebot3_mbody.py")
SPEC = importlib.util.spec_from_file_location("mujoco_turtlebot3_mbody", SCRIPT)
assert SPEC and SPEC.loader
recipe = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = recipe
SPEC.loader.exec_module(recipe)


class TurtleBot3MBodyRecipeTest(unittest.TestCase):
    def args(self, root: Path) -> argparse.Namespace:
        return argparse.Namespace(
            mbody_root=root.parent / "hakoniwa-mbody-registry",
            mujoco_root=root.parent / "hakoniwa-mujoco-robots",
            headless=False,
        )

    def test_workspace_paths_stay_under_business_pack_work(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "business-pack"
            with mock.patch.object(recipe, "business_root", return_value=root):
                resolved = recipe.paths()
            for value in resolved.values():
                self.assertTrue(value.is_relative_to(root / "work"))

    def test_mbody_tool_requirements_are_shared_by_repository(self) -> None:
        self.assertEqual(recipe.MBODY_TOOL_REQUIREMENTS.name, "hakoniwa-mbody-registry.txt")
        self.assertIn("PyYAML", recipe.MBODY_TOOL_REQUIREMENTS.read_text(encoding="utf-8"))

    def test_posix_configure_uses_managed_paths_without_windows_toolchain(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "business-pack"
            args = self.args(root)
            state = {"vcpkg_root": None}
            with mock.patch.object(recipe, "business_root", return_value=root), mock.patch.object(
                recipe.platform, "system", return_value="Darwin"
            ):
                command = recipe.cmake_configure_command(args, state)
            dependency_path = root / "work" / "recipes" / recipe.RECIPE_ID / "deps"
            self.assertIn(f"-DFETCHCONTENT_BASE_DIR={dependency_path}", command)
            self.assertIn("-DCMAKE_BUILD_TYPE=Release", command)
            self.assertFalse(any("VCPKG_TARGET_TRIPLET" in item for item in command))
            self.assertEqual(command.count(str(root / "work" / "recipes" / recipe.RECIPE_ID / "build" / "hakoniwa-mujoco-robots")), 1)

    def test_windows_configure_uses_selected_foundation_toolchain(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "business-pack"
            args = self.args(root)
            vcpkg = root / "work" / "foundation" / "tools" / "vcpkg"
            with mock.patch.object(recipe, "business_root", return_value=root), mock.patch.object(
                recipe.platform, "system", return_value="Windows"
            ):
                command = recipe.cmake_configure_command(args, {"vcpkg_root": str(vcpkg)})
            self.assertIn(f"-DCMAKE_TOOLCHAIN_FILE={vcpkg / 'scripts' / 'buildsystems' / 'vcpkg.cmake'}", command)
            self.assertIn("-DVCPKG_TARGET_TRIPLET=x64-windows", command)

    def test_executable_layout_is_platform_internal(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            build = Path(temporary)
            windows = build / "main_for_sample" / "tb3" / "Release" / "tb3_sim_burger.exe"
            windows.parent.mkdir(parents=True)
            windows.touch()
            with mock.patch.object(recipe.platform, "system", return_value="Windows"):
                self.assertEqual(recipe.executable_path(build), windows)
            windows.unlink()
            posix = build / "main_for_sample" / "tb3" / "tb3_sim_burger"
            posix.touch()
            with mock.patch.object(recipe.platform, "system", return_value="Darwin"):
                self.assertEqual(recipe.executable_path(build), posix)

    def test_launcher_command_contract_is_same_on_both_platforms(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "business-pack"
            prefix = root / "work" / "foundation" / "install"
            for relative in ("python/Scripts/python.exe", "python/bin/python3"):
                path = prefix / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.touch()
            with mock.patch.object(recipe, "business_root", return_value=root):
                command = recipe.control_command("status")
                expected_session = recipe.session_file()
            self.assertEqual(command[2:5], ["hakoniwa_pdu.apps.launcher.hako_launcher_ctl", "status", str(expected_session)])


if __name__ == "__main__":
    unittest.main()
