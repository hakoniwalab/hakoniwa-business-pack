#!/usr/bin/env python3
"""Contract tests for the Drone Three.js Recipe operator."""

from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path

from tools.recipe.path_test_support import contains_path


SCRIPT = Path(__file__).with_name("drone_threejs.py")
SPEC = importlib.util.spec_from_file_location("drone_threejs_recipe", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
recipe = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = recipe
SPEC.loader.exec_module(recipe)

FOUNDATION_SCRIPT = Path(__file__).resolve().parents[1] / "foundation.py"
FOUNDATION_SPEC = importlib.util.spec_from_file_location(
    "drone_threejs_foundation_test", FOUNDATION_SCRIPT
)
assert FOUNDATION_SPEC is not None and FOUNDATION_SPEC.loader is not None
foundation = importlib.util.module_from_spec(FOUNDATION_SPEC)
sys.modules[FOUNDATION_SPEC.name] = foundation
FOUNDATION_SPEC.loader.exec_module(foundation)


class DroneThreejsConfigureTest(unittest.TestCase):
    def _generate(self, root: Path):
        paths = foundation.resolve_workspace(root, recipe.RECIPE_ID)
        foundation.prepare_workspace(paths)
        drone_root = root / "hakoniwa-drone-core"
        viewer_root = root / "hakoniwa-threejs-drone"
        launcher = recipe.write_launcher(paths, drone_root, viewer_root)
        launch, mission = recipe.write_wrappers(paths, drone_root, launcher)
        return paths, launcher, launch, mission

    def test_generated_launcher_uses_only_local_foundation_and_recipe_paths(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            paths, launcher, launch, mission = self._generate(root)
            data = json.loads(launcher.read_text(encoding="utf-8"))
            serialized = json.dumps(data)

            self.assertNotIn("/usr/local", serialized)
            self.assertNotIn("/etc/hakoniwa", serialized)
            self.assertTrue(contains_path(data, paths.install_prefix))
            self.assertTrue(contains_path(data, paths.recipe_config))
            self.assertEqual(
                data["assets"][2]["command"],
                str(paths.install_prefix / "bin" / "hakoniwa-pdu-web-bridge"),
            )
            self.assertEqual(
                data["assets"][3]["command"],
                str(paths.install_prefix / "python" / "bin" / "python"),
            )
            self.assertIn(str(paths.foundation_config), launch.read_text())
            self.assertIn(
                str(paths.foundation_python / "bin"),
                mission.read_text(),
            )

    def test_generated_wrapper_execs_launcher_for_sigint_cleanup(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            _paths, _launcher, launch, _mission = self._generate(root)
            content = launch.read_text(encoding="utf-8")

            # exec replaces the wrapper shell with hako_launcher, so interactive
            # Ctrl+C/SIGINT reaches the launcher main process and its SIGINT handler.
            self.assertIn("exec ", content)
            self.assertIn("hakoniwa_pdu.apps.launcher.hako_launcher", content)
            self.assertIn("--mode immediate", content)
            self.assertNotIn("kill ", content)
            self.assertNotIn("trap ", content)

    def test_recipe_records_launcher_cleanup_and_post_exit_checks(self) -> None:
        content = recipe.recipe_file().read_text(encoding="utf-8")
        self.assertIn("cleanup:", content)
        self.assertIn("owner: hakoniwa-launcher", content)
        self.assertIn("target: launcher-main-process", content)
        self.assertIn("signal: SIGINT", content)
        self.assertIn("state -> TERMINATED", content)
        self.assertIn("TCP port 8000", content)
        self.assertIn("TCP port 8765", content)


if __name__ == "__main__":
    unittest.main()
