#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path


SCRIPT = Path(__file__).with_name("drone_threejs.py")
SPEC = importlib.util.spec_from_file_location("drone_threejs_recipe", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
recipe = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = recipe
SPEC.loader.exec_module(recipe)

FOUNDATION_SCRIPT = Path(__file__).with_name("foundation.py")
FOUNDATION_SPEC = importlib.util.spec_from_file_location(
    "drone_threejs_foundation_test", FOUNDATION_SCRIPT
)
assert FOUNDATION_SPEC is not None and FOUNDATION_SPEC.loader is not None
foundation = importlib.util.module_from_spec(FOUNDATION_SPEC)
sys.modules[FOUNDATION_SPEC.name] = foundation
FOUNDATION_SPEC.loader.exec_module(foundation)


class DroneThreejsConfigureTest(unittest.TestCase):
    def test_generated_launcher_uses_only_local_foundation_and_recipe_paths(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            paths = foundation.resolve_workspace(root, recipe.RECIPE_ID)
            foundation.prepare_workspace(paths)
            drone_root = root / "hakoniwa-drone-core"
            viewer_root = root / "hakoniwa-threejs-drone"

            launcher = recipe.write_launcher(paths, drone_root, viewer_root)
            launch, mission = recipe.write_wrappers(
                paths, drone_root, launcher
            )
            data = json.loads(launcher.read_text(encoding="utf-8"))
            serialized = json.dumps(data)

            self.assertNotIn("/usr/local", serialized)
            self.assertNotIn("/etc/hakoniwa", serialized)
            self.assertIn(str(paths.install_prefix), serialized)
            self.assertIn(str(paths.recipe_config), serialized)
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


if __name__ == "__main__":
    unittest.main()
