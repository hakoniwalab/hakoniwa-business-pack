#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock


SCRIPT = Path(__file__).with_name("city_world_web_ui.py")
SPEC = importlib.util.spec_from_file_location("city_world_web_ui_recipe", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
recipe = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = recipe
SPEC.loader.exec_module(recipe)


class CityWorldWebUiToolTest(unittest.TestCase):
    def test_recipe_file_is_the_web_ui_recipe(self) -> None:
        self.assertEqual(recipe.RECIPE_PATH.name, "city-world-web-ui.yaml")
        self.assertTrue(hasattr(recipe.recipe_tool, "load_recipe"))

    def test_recipe_exports_its_repository_for_launcher_cwd(self) -> None:
        environment = recipe.recipe_data()["recipe_runtime"]["environment"]
        self.assertEqual(environment["RECIPE_REPOSITORY"], "${RECIPE_REPOSITORY}")

    def test_start_uses_foundation_python_and_recipe_owned_state(self) -> None:
        paths = SimpleNamespace(
            foundation_python=Path("/foundation/python"),
            recipe_root=Path("/selected-work/recipes/city-world-web-ui"),
        )
        arguments = recipe.parser().parse_args([
            "start", "--open-browser", "--terrain-spacing-m", "auto"
        ])
        with (
            mock.patch.object(recipe, "recipe_environment", return_value=({}, paths)),
            mock.patch.object(recipe, "_run", return_value=0) as run,
        ):
            self.assertEqual(recipe.lifecycle({}, "start", arguments), 0)
        command = run.call_args.args[0]
        self.assertEqual(command[:4], [
            "/foundation/python", "-m",
            "tools.remote_operation.city_world.launcher", "start",
        ])
        self.assertIn("/selected-work/recipes/city-world-web-ui/runtime", command)
        self.assertIn("/selected-work/recipes/city-world-web-ui/launcher", command)
        self.assertIn("--open-browser", command)

    def test_status_uses_the_same_recipe_owned_state(self) -> None:
        paths = SimpleNamespace(
            foundation_python=Path("/foundation/python"),
            recipe_root=Path("/selected-work/recipes/city-world-web-ui"),
        )
        arguments = recipe.parser().parse_args(["status"])
        with (
            mock.patch.object(recipe, "recipe_environment", return_value=({}, paths)),
            mock.patch.object(recipe, "_run", return_value=0) as run,
        ):
            self.assertEqual(recipe.lifecycle({}, "status", arguments), 0)
        command = run.call_args.args[0]
        self.assertNotIn("--open-browser", command)
        self.assertIn("/selected-work/recipes/city-world-web-ui/runtime", command)


if __name__ == "__main__":
    unittest.main()
