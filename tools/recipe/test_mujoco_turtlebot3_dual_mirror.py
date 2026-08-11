from __future__ import annotations

import argparse
import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


SCRIPT = Path(__file__).with_name("mujoco_turtlebot3_dual_mirror.py")
SPEC = importlib.util.spec_from_file_location("mujoco_turtlebot3_dual_mirror", SCRIPT)
assert SPEC and SPEC.loader
recipe = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = recipe
SPEC.loader.exec_module(recipe)


class DualMirrorRecipeTest(unittest.TestCase):
    def test_recipe_identity(self):
        self.assertEqual(recipe.RECIPE_ID, "mujoco-turtlebot3-dual-mirror")

    def test_stage_preserves_runtime_relative_layout(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source"
            destination = root / "business/work/recipes/mujoco-turtlebot3-dual-mirror/assets/runtime-input"
            mbody = root / "mbody"
            files = (
                "config/tb3-dual-pdudef-compact.json",
                "models/tb3/tb3_burger_real_waffle_mirror.xml",
                "models/tb3/tb3_waffle_real_burger_mirror.xml",
                "python/tb3_route_demo.py",
                "python/lidar_visualizer.py",
            )
            for relative in files:
                path = source / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                content = 'file="../../../hakoniwa-mbody-registry/bodies/turtlebot3_burger/source/mesh.stl"\n' if path.suffix == ".xml" else "{}\n"
                path.write_text(content, encoding="utf-8")
            mesh = mbody / "bodies/turtlebot3_burger/source/mesh.stl"
            mesh.parent.mkdir(parents=True, exist_ok=True)
            mesh.write_text("mesh\n", encoding="utf-8")
            with mock.patch.object(recipe, "business_root", return_value=root / "business"):
                recipe.stage_runtime_inputs(source, mbody)
            for relative in files:
                self.assertTrue((destination / relative).is_file())
            staged_model = destination / "models/tb3/tb3_burger_real_waffle_mirror.xml"
            self.assertIn("../../mbody/turtlebot3_burger/source/mesh.stl", staged_model.read_text())
            self.assertTrue((destination / "mbody/turtlebot3_burger/source/mesh.stl").is_file())

    def test_launcher_has_one_conductor_owner_and_foundation_python(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "business"
            args = argparse.Namespace(headless=True)
            expected_python = root / "work/foundation/install/python/bin/python3"
            expected_python.parent.mkdir(parents=True, exist_ok=True)
            expected_python.write_text("", encoding="utf-8")
            with mock.patch.object(recipe, "business_root", return_value=root), mock.patch.object(
                recipe, "platform"
            ) as platform_module:
                platform_module.system.return_value = "Darwin"
                launch = recipe.write_launcher(args)
            data = json.loads(launch.read_text(encoding="utf-8"))
            assets = {asset["name"]: asset for asset in data["assets"]}
            self.assertNotIn("HAKO_TB3_DISABLE_CONDUCTOR_START", assets["tb3_burger_real"]["env"]["set"])
            self.assertEqual(assets["tb3_waffle_real"]["env"]["set"]["HAKO_TB3_DISABLE_CONDUCTOR_START"], "1")
            self.assertEqual(assets["burger_route_demo"]["command"], str(expected_python))

    def test_control_contract_uses_session_file(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "business"
            with mock.patch.object(recipe, "business_root", return_value=root):
                command = recipe.control_command("terminate")
                self.assertEqual(command[-2], "terminate")
                self.assertEqual(Path(command[-1]), recipe.session_file())

    def test_gui_launcher_adds_two_lidar_visualizers(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "business"
            python = root / "work/foundation/install/python/bin/python3"
            python.parent.mkdir(parents=True, exist_ok=True)
            python.write_text("", encoding="utf-8")
            with mock.patch.object(recipe, "business_root", return_value=root):
                launch = recipe.write_launcher(argparse.Namespace(headless=False))
            names = {asset["name"] for asset in json.loads(launch.read_text())["assets"]}
            self.assertIn("burger_lidar_visualizer", names)
            self.assertIn("waffle_lidar_visualizer", names)


if __name__ == "__main__":
    unittest.main()
