from __future__ import annotations

import argparse
import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


SCRIPT = Path(__file__).with_name("mujoco_turtlebot3_wall_follower.py")
SPEC = importlib.util.spec_from_file_location("mujoco_turtlebot3_wall_follower", SCRIPT)
assert SPEC and SPEC.loader
recipe = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = recipe
SPEC.loader.exec_module(recipe)


class WallFollowerRecipeTest(unittest.TestCase):
    def test_recipe_identity_is_applied_to_shared_tb3_helpers(self):
        self.assertEqual(recipe.RECIPE_ID, "mujoco-turtlebot3-wall-follower")
        self.assertEqual(recipe.base.RECIPE_ID, recipe.RECIPE_ID)

    def test_runtime_requirements_are_recipe_owned_and_pinned(self):
        content = recipe.RUNTIME_REQUIREMENTS.read_text(encoding="utf-8")
        self.assertIn("numpy==2.2.6", content)
        self.assertIn("matplotlib==3.10.5", content)
        self.assertIn("PyQt5==5.15.11", content)

    def test_runtime_dependencies_install_into_foundation_python(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "business"
            python = root / "work/foundation/install/python/bin/python3"
            python.parent.mkdir(parents=True, exist_ok=True)
            python.write_text("", encoding="utf-8")
            with mock.patch.object(recipe.base, "business_root", return_value=root), mock.patch.object(
                recipe.base, "run"
            ) as run:
                recipe.install_runtime_dependencies()
            command = run.call_args.args[0]
            self.assertEqual(command[:4], [str(python), "-m", "pip", "install"])
            self.assertEqual(command[-2:], ["--requirement", str(recipe.RUNTIME_REQUIREMENTS)])
            self.assertTrue(run.call_args.kwargs["check"])

    def test_headless_launcher_uses_foundation_python_and_wall_follower(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "business"
            python = root / "work/foundation/install/python/bin/python3"
            python.parent.mkdir(parents=True, exist_ok=True)
            python.write_text("", encoding="utf-8")
            args = argparse.Namespace(model="waffle", headless=True, duration_sec=20.0)
            with mock.patch.object(recipe.base, "business_root", return_value=root):
                launch = recipe.write_launcher(args)
            data = json.loads(launch.read_text(encoding="utf-8"))
            assets = {asset["name"]: asset for asset in data["assets"]}
            self.assertEqual(assets["obstacle_avoider"]["command"], str(python))
            self.assertNotIn("lidar_visualizer", assets)
            self.assertIn(str(root / "work/recipes" / recipe.RECIPE_ID), json.dumps(data))

    def test_gui_launcher_adds_lidar_visualizer(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "business"
            python = root / "work/foundation/install/python/bin/python3"
            python.parent.mkdir(parents=True, exist_ok=True)
            python.write_text("", encoding="utf-8")
            args = argparse.Namespace(model="waffle", headless=False, duration_sec=30.0)
            with mock.patch.object(recipe.base, "business_root", return_value=root):
                launch = recipe.write_launcher(args)
            names = {asset["name"] for asset in json.loads(launch.read_text())["assets"]}
            self.assertIn("lidar_visualizer", names)


if __name__ == "__main__":
    unittest.main()
