#!/usr/bin/env python3
"""Contract tests for the Unitree Go1 Recipe operator."""

from __future__ import annotations

import importlib.util
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from tools.recipe.path_test_support import contains_path


SCRIPT = Path(__file__).with_name("unitree_go1_demo.py")
SPEC = importlib.util.spec_from_file_location("unitree_go1_demo_recipe", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
recipe = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = recipe
SPEC.loader.exec_module(recipe)


class UnitreeGo1DemoTest(unittest.TestCase):
    def _paths(self, root: Path):
        foundation_root = root / "hakoniwa-business-pack/work/foundation"
        recipe_root = root / "hakoniwa-business-pack/work/recipes" / recipe.RECIPE_ID
        for directory in (recipe_root / "config", recipe_root / "logs", foundation_root / "config"):
            directory.mkdir(parents=True, exist_ok=True)
        return SimpleNamespace(
            recipe_root=recipe_root,
            recipe_config=recipe_root / "config",
            recipe_logs=recipe_root / "logs",
            foundation_config=foundation_root / "config",
            install_prefix=foundation_root / "install",
            foundation_python=foundation_root / "install/python",
        )

    def _runtime(self, root: Path, paths):
        mujoco = root / "hakoniwa-mujoco-robots"
        examples = mujoco / "examples/actuators/unitree_go1"
        binary_root = paths.recipe_root / "build/mujoco/examples/actuators/unitree_go1"
        return mujoco, recipe.RuntimePaths(
            system_name="Darwin",
            foundation_python=paths.foundation_python / "bin/python3",
            hako_cmd=paths.install_prefix / "bin/hako-cmd",
            endpoint_callback_library=paths.install_prefix / "lib/libhakoniwa_pdu_endpoint_core_callback.dylib",
            step1_binary=binary_root / "unitree-go1-joint-io-example",
            plant_binary=binary_root / "unitree-go1-joint-hakoniwa-asset",
            pose_sender=examples / "pose_bounce_go1.py",
            perturbation_sender=examples / "send_go1_joint_targets.py",
            creep_sender=examples / "walk_go1.py",
        )

    def test_pose_bounce_is_default_profile(self) -> None:
        args = recipe.parser().parse_args(["start"])
        self.assertEqual(args.profile, "pose-bounce")

    def test_build_manifest_uses_recipe_owned_directory(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            paths = self._paths(root)
            mujoco, _runtime = self._runtime(root, paths)
            mujoco.mkdir(parents=True)
            manifest = recipe.write_build_manifest(paths, mujoco)
            content = manifest.read_text(encoding="utf-8")
            relative = os.path.relpath(paths.recipe_root / "build/mujoco", mujoco)
            self.assertIn(f"dir: {relative}", content)
            self.assertNotIn("/usr/local", content)

    def test_generated_pose_launcher_uses_foundation_and_workspace(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            paths = self._paths(root)
            mujoco, runtime = self._runtime(root, paths)
            launcher = recipe.write_launcher(paths, mujoco, runtime, "pose-bounce")
            data = json.loads(launcher.read_text(encoding="utf-8"))
            assets = {item["name"]: item for item in data["assets"]}
            self.assertEqual(list(assets), ["go1-plant", "go1-pose-bounce-sender"])
            self.assertEqual(assets["go1-pose-bounce-sender"]["command"], str(runtime.foundation_python))
            self.assertEqual(assets["go1-pose-bounce-sender"]["args"][-2:], ["--cycles", "300"])
            self.assertTrue(contains_path(data, paths.recipe_root))
            self.assertTrue(contains_path(data, paths.install_prefix))
            self.assertNotIn("/usr/local/hakoniwa", json.dumps(data))

    def test_all_profiles_keep_the_same_plant_and_pdu_contract(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            paths = self._paths(root)
            mujoco, runtime = self._runtime(root, paths)
            for profile in recipe.PROFILES:
                launcher = recipe.write_launcher(paths, mujoco, runtime, profile)
                data = json.loads(launcher.read_text(encoding="utf-8"))
                plant = data["assets"][0]
                self.assertEqual(plant["command"], str(runtime.plant_binary))
                self.assertTrue(plant["args"][2].endswith("go1-joint-pdudef-compact.json"))
                self.assertTrue(plant["args"][3].endswith("go1_joint_endpoint.json"))

    def test_background_session_is_external_lifecycle_contract(self) -> None:
        python = Path("/foundation/python/bin/python3")
        launcher = Path("/workspace/config/launcher-pose-bounce.json")
        session = Path("/workspace/runtime/launcher-session.json")
        start = recipe.launcher_start_command(python, launcher, session)
        stop = recipe.launcher_control_command(python, "terminate", session)
        self.assertIn("--background", start)
        self.assertEqual(start[-1], str(session))
        self.assertIn("hakoniwa_pdu.apps.launcher.hako_launcher_ctl", stop)
        self.assertEqual(stop[-2], "terminate")

    def test_portal_distinguishes_pose_bounce_from_verified_jump(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            paths = self._paths(root)
            _mujoco, runtime = self._runtime(root, paths)
            portal = recipe.write_portal(paths, runtime)
            content = portal.read_text(encoding="utf-8")
            self.assertIn("Pose Bounce", content)
            self.assertIn("verified jump", content)
            self.assertIn("python tools/recipe/unitree_go1_demo.py start", content)
            self.assertIn("python tools/recipe/unitree_go1_demo.py stop", content)
            self.assertIn("python tools/workspace.py enter", content)


if __name__ == "__main__":
    unittest.main()
