#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

sys.path.append(str(Path(__file__).resolve().parents[1]))
from workspace_test_isolation import isolate_from_active_workspace  # noqa: E402

isolate_from_active_workspace()


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
            mock.patch.object(recipe, "recipe_environment", return_value=({
                "HAKO_FOUNDATION_PYTHON": "/foundation/python/bin/python",
            }, paths)),
            mock.patch.object(recipe, "_run", return_value=0) as run,
        ):
            self.assertEqual(recipe.lifecycle({}, "start", arguments), 0)
        command = run.call_args.args[0]
        self.assertEqual(command[:4], [
            "/foundation/python/bin/python", "-m",
            "tools.remote_operation.city_world.launcher", "start",
        ])
        self.assertIn(str(paths.recipe_root / "runtime"), command)
        self.assertIn(str(paths.recipe_root / "launcher"), command)
        self.assertIn("--open-browser", command)

    def test_status_uses_the_same_recipe_owned_state(self) -> None:
        paths = SimpleNamespace(
            foundation_python=Path("/foundation/python"),
            recipe_root=Path("/selected-work/recipes/city-world-web-ui"),
        )
        arguments = recipe.parser().parse_args(["status"])
        with (
            mock.patch.object(recipe, "recipe_environment", return_value=({
                "HAKO_FOUNDATION_PYTHON": "/foundation/python/bin/python",
            }, paths)),
            mock.patch.object(recipe, "_run", return_value=0) as run,
        ):
            self.assertEqual(recipe.lifecycle({}, "status", arguments), 0)
        command = run.call_args.args[0]
        self.assertNotIn("--open-browser", command)
        self.assertIn(str(paths.recipe_root / "runtime"), command)

    def test_cache_clean_is_a_dry_run_on_the_recipe_owned_runtime(self) -> None:
        paths = SimpleNamespace(recipe_root=Path("/selected-work/recipes/city-world-web-ui"))
        arguments = recipe.parser().parse_args(["cache-clean", "--job-sources"])
        self.assertFalse(arguments.apply)
        from tools.remote_operation.city_world import cache_cleanup

        with (
            mock.patch.object(recipe, "recipe_environment", return_value=({}, paths)),
            mock.patch.object(cache_cleanup, "run", return_value=0) as run,
        ):
            self.assertEqual(recipe.cache({}, "cache-clean", arguments), 0)
        runtime, command, forwarded = run.call_args.args
        self.assertEqual(runtime, paths.recipe_root / "runtime")
        self.assertEqual(command, "clean")
        self.assertTrue(forwarded.job_sources)
        self.assertFalse(forwarded.source_cache)


if __name__ == "__main__":
    unittest.main()
