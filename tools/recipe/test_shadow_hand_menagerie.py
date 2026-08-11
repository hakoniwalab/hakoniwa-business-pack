from __future__ import annotations

import argparse
import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


SCRIPT = Path(__file__).with_name("shadow_hand_menagerie.py")
SPEC = importlib.util.spec_from_file_location("shadow_hand_menagerie_recipe", SCRIPT)
assert SPEC and SPEC.loader
recipe = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = recipe
SPEC.loader.exec_module(recipe)


class ShadowHandMenagerieRecipeTest(unittest.TestCase):
    def test_recipe_identity_is_applied_to_managed_build_helpers(self):
        self.assertEqual(recipe.base.RECIPE_ID, recipe.RECIPE_ID)

    def test_headless_launcher_uses_staged_inputs_and_foundation_python(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "business"
            python = root / "work/foundation/install/python/bin/python3"
            python.parent.mkdir(parents=True, exist_ok=True)
            python.write_text("", encoding="utf-8")
            staged = {
                "model": root / "work/recipes/x/assets/menagerie/shadow_hand/scene_right.xml",
                "config": root / "work/recipes/x/assets/runtime-input/config",
                "sender": root / "work/recipes/x/assets/runtime-input/python/send_shadow_hand_targets.py",
            }
            args = argparse.Namespace(viewer=False, duration_sec=5.0)
            with mock.patch.object(recipe.base, "business_root", return_value=root):
                launch = recipe.write_launcher(args, staged)
            data = json.loads(launch.read_text(encoding="utf-8"))
            assets = {item["name"]: item for item in data["assets"]}
            self.assertEqual(assets["shadow_hand_sender"]["command"], str(python))
            self.assertEqual(assets["shadow_hand"]["args"][0], "--no-viewer")
            self.assertIn(str(root / "work/recipes" / recipe.RECIPE_ID), json.dumps(data))

    def test_viewer_flag_removes_no_viewer_argument(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "business"
            python = root / "work/foundation/install/python/bin/python3"
            python.parent.mkdir(parents=True, exist_ok=True)
            python.write_text("", encoding="utf-8")
            staged = {"model": root / "model.xml", "config": root / "config", "sender": root / "sender.py"}
            with mock.patch.object(recipe.base, "business_root", return_value=root):
                launch = recipe.write_launcher(argparse.Namespace(viewer=True, duration_sec=5.0), staged)
            asset = json.loads(launch.read_text(encoding="utf-8"))["assets"][0]
            self.assertNotIn("--no-viewer", asset["args"])


if __name__ == "__main__":
    unittest.main()
