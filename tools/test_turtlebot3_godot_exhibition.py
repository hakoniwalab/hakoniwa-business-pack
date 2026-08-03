#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace


SCRIPT = Path(__file__).with_name("turtlebot3_godot_exhibition.py")
SPEC = importlib.util.spec_from_file_location("tb3_godot_recipe", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
recipe = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = recipe
SPEC.loader.exec_module(recipe)


class TurtleBot3GodotExhibitionTest(unittest.TestCase):
    def paths(self, root: Path):
        foundation = root / "work/foundation"
        recipe_root = root / "work/recipes" / recipe.RECIPE_ID
        for directory in (recipe_root / "config", recipe_root / "logs", foundation / "config"):
            directory.mkdir(parents=True, exist_ok=True)
        return SimpleNamespace(
            recipe_root=recipe_root,
            recipe_config=recipe_root / "config",
            recipe_logs=recipe_root / "logs",
            foundation_config=foundation / "config",
            install_prefix=foundation / "install",
            foundation_python=foundation / "install/python",
        )

    def runtime(self, paths, mujoco: Path):
        return recipe.RuntimePaths(
            system_name="Darwin",
            foundation_python=paths.foundation_python / "bin/python3",
            hako_cmd=paths.install_prefix / "bin/hako-cmd",
            endpoint_callback_library=paths.install_prefix / "lib/libhakoniwa_pdu_endpoint_core_callback.dylib",
            endpoint_polling_library=paths.install_prefix / "lib/libhakoniwa_pdu_endpoint_core_polling.dylib",
            godot_binary=Path("/Applications/Godot_mono.app/Contents/MacOS/Godot"),
            tb3_binary=paths.recipe_root / "build/mujoco/main_for_sample/tb3/tb3_sim_burger",
            route_script=mujoco / "python/tb3_route_demo.py",
        )

    def test_gamepad_is_default_and_route_is_fallback(self):
        self.assertEqual(recipe.parser().parse_args(["start"]).profile, "gamepad")
        self.assertEqual(recipe.parser().parse_args(["start", "--profile", "route"]).profile, "route")

    def test_deadzone_and_clamp(self):
        self.assertEqual(recipe.apply_deadzone(0.04, 0.08), 0.0)
        self.assertEqual(recipe.apply_deadzone(2.0, 0.08), 1.0)
        self.assertEqual(recipe.apply_deadzone(-2.0, 0.08), -1.0)

    def test_mujoco_build_manifest_is_recipe_owned(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            paths = self.paths(root)
            mujoco = root / "hakoniwa-mujoco-robots"
            mujoco.mkdir()
            manifest = recipe.write_build_manifest(paths, mujoco)
            expected = os.path.relpath(paths.recipe_root / "build/mujoco", mujoco)
            self.assertIn(f"dir: {expected}", manifest.read_text(encoding="utf-8"))

    def test_launcher_keeps_callback_and_polling_profiles_separate(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            paths = self.paths(root)
            mujoco = root / "hakoniwa-mujoco-robots"
            runtime = self.runtime(paths, mujoco)
            launcher = recipe.write_launcher(paths, runtime, mujoco, "gamepad")
            payload = json.loads(launcher.read_text(encoding="utf-8"))
            assets = {item["name"]: item for item in payload["assets"]}
            self.assertIn("core_callback", assets["tb3-mujoco"]["env"]["set"]["HAKO_PDU_ENDPOINT_SHARED_LIB"])
            self.assertIn("core_polling", assets["tb3-godot"]["env"]["set"]["HAKO_PDU_ENDPOINT_SHARED_LIB"])
            self.assertEqual(assets["tb3-gamepad"]["activation_timing"], "after_start")

    def test_route_launcher_preserves_game_controller_contract(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            paths = self.paths(root)
            mujoco = root / "hakoniwa-mujoco-robots"
            runtime = self.runtime(paths, mujoco)
            launcher = recipe.write_launcher(paths, runtime, mujoco, "route")
            payload = json.loads(launcher.read_text(encoding="utf-8"))
            route = payload["assets"][2]
            self.assertEqual(route["command"], str(runtime.foundation_python))
            self.assertIn("tb3_route_demo.py", route["args"][0])
            self.assertIn("figure8", route["args"])

    def test_launcher_session_is_external_contract(self):
        python = Path("/foundation/python")
        session = Path("/work/runtime/session.json")
        start = recipe.launcher_start_command(python, Path("/work/launcher.json"), session)
        stop = recipe.launcher_control_command(python, "terminate", session)
        self.assertIn("--background", start)
        self.assertEqual(start[-1], str(session))
        self.assertEqual(stop[-2], "terminate")


if __name__ == "__main__":
    unittest.main()
