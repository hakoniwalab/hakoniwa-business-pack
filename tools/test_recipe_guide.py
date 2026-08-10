#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
import tempfile
import unittest
from pathlib import Path
from unittest import mock

SCRIPT = Path(__file__).with_name("recipe.py")
SPEC = importlib.util.spec_from_file_location("business_pack_recipe_guide", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
guide = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(guide)


class RecipeGuideTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.business_pack_root = SCRIPT.resolve().parents[1]
        cls.recipe_dir = cls.business_pack_root / "recipes" / "examples"

    def test_all_recipes_are_loadable_by_the_generic_guide(self) -> None:
        paths = sorted(self.recipe_dir.glob("*.yaml"))
        self.assertGreater(len(paths), 0)
        for path in paths:
            with self.subTest(recipe=path.name):
                data = guide.load_recipe(path)
                self.assertEqual(data["id"], path.stem)
                self.assertIn("demo", data)

    def test_top_level_tools_are_repository_wide_contracts(self) -> None:
        expected = {
            "catalog_doctor.rb",
            "docker-mac.bash",
            "doctor-mac.bash",
            "doctor.bash",
            "foundation.py",
            "recipe.py",
            "recipe_portal.py",
            "test_foundation.py",
            "test_recipe_guide.py",
            "test_workspace.py",
            "test_workspace_enter.py",
            "workspace.py",
        }
        tools_dir = self.business_pack_root / "tools"
        actual = {path.name for path in tools_dir.iterdir() if path.is_file()}
        self.assertEqual(actual, expected)

    def test_guide_generates_workspace_index_from_recipe_yaml(self) -> None:
        recipe_path = (
            self.recipe_dir / "drone-single-mujoco-shibuya-map-gamepad.yaml"
        )
        data = guide.load_recipe(recipe_path)
        with tempfile.TemporaryDirectory() as temporary:
            temporary_root = Path(temporary)
            with mock.patch.object(guide, "root", return_value=temporary_root):
                output = guide.write_guide(recipe_path, data)

            expected = (
                temporary_root.resolve()
                / "work"
                / "recipes"
                / data["id"]
                / "index.html"
            )
            self.assertEqual(output, expected)
            content = output.read_text(encoding="utf-8")
            self.assertIn(data["title"], content)
            self.assertIn("Foundation MISSING", content)
            self.assertIn("hakoniwa-core-pro", content)
            self.assertIn("drone_shibuya_gamepad.py configure", content)
            self.assertIn("drone_shibuya_gamepad.py start", content)
            self.assertIn("drone_shibuya_gamepad.py stop", content)
            self.assertIn("必要な許可", content)
            self.assertIn("これだけではDemo Readyではありません", content)
            self.assertIn("127.0.0.1:8000", content)
            self.assertIn("startの復帰後もDemoは継続します", content)
            self.assertIn("does not execute local commands", content)
            self.assertIn("python tools/workspace.py enter", content)
            self.assertIn("data-copy=\"exit\"", content)
            self.assertIn("python tools/foundation.py doctor", content)
            self.assertNotIn("python3.12 tools/foundation.py", content)
            self.assertLess(
                content.index("python tools/workspace.py enter"),
                content.index("python tools/recipe/drone_shibuya_gamepad.py configure"),
            )
            self.assertLess(
                content.index("python tools/recipe/drone_shibuya_gamepad.py stop"),
                content.index("data-copy=\"exit\""),
            )

    def test_guide_command_deduplicates_prerequisite_and_demo_operations(self) -> None:
        data = guide.load_recipe(
            self.recipe_dir / "drone-single-mujoco-shibuya-map-gamepad.yaml"
        )
        recipe_path = (
            self.recipe_dir / "drone-single-mujoco-shibuya-map-gamepad.yaml"
        )
        commands = guide._command_items(data, recipe_path)
        values = [item.command for item in commands]
        self.assertEqual(len(values), len(set(values)))
        self.assertEqual(
            values.count("python tools/recipe/drone_shibuya_gamepad.py configure"),
            1,
        )
        self.assertTrue(any("foundation.py doctor" in value for value in values))
        self.assertTrue(any("foundation.py plan" in value for value in values))
        self.assertTrue(any("foundation.py build" in value for value in values))
        self.assertTrue(all(not value.startswith("python3.12 ") for value in values))

    def test_cli_contract_requires_only_recipe_to_generate_a_guide(self) -> None:
        help_text = guide.parser().format_help()
        self.assertIn("guide", help_text)
        self.assertIn("--recipe", help_text)
        self.assertIn("--foundation-requirements", help_text)
        self.assertIn("--open", help_text)
        self.assertNotIn("configure", help_text)
        self.assertNotIn("build", help_text)

    def test_dynamic_experiment_guide_uses_declared_foundation_order(self) -> None:
        recipe_path = self.recipe_dir / "drone-fleet-single-host.yaml"
        data = guide.load_recipe(recipe_path)
        commands = [item.command for item in guide._command_items(data, recipe_path)]
        configure = "python tools/recipe/drone_fleet_single_host.py configure"
        generated_doctor = (
            "python tools/foundation.py doctor --recipe "
            "work/recipes/drone-fleet-single-host/config/foundation-requirements.yaml"
        )
        self.assertIn(configure, commands)
        self.assertIn(generated_doctor, commands)
        self.assertLess(commands.index(configure), commands.index(generated_doctor))
        self.assertNotIn(
            "python tools/foundation.py doctor --recipe "
            "recipes/examples/drone-fleet-single-host.yaml",
            commands,
        )
        configuration = guide._configuration_items(data)
        rendered = "\n".join(f"{item.label}: {item.value}" for item in configuration)
        self.assertIn("drones_per_process", rendered)
        self.assertIn("process_count*drones_per_process", rendered)

    def test_readiness_and_background_handoff_are_rendered_as_notes(self) -> None:
        notes = guide._agency_notes(
            {
                "demo": {
                    "readiness": {
                        "lifecycle_state": {
                            "required": "RUNNING",
                            "sufficient": False,
                        },
                        "checks": [
                            {
                                "id": "http",
                                "target": "127.0.0.1:8000",
                                "expected": "TCP connection succeeds.",
                            }
                        ],
                        "operator_handoff": {
                            "background": True,
                            "next_actions": ["open-viewer", "status", "stop"],
                        },
                    }
                }
            }
        )

        content = "\n".join(notes)
        self.assertIn("これだけではDemo Readyではありません", content)
        self.assertIn("127.0.0.1:8000", content)
        self.assertIn("startの復帰後もDemoは継続します", content)
        self.assertIn("open-viewer, status, stop", content)


if __name__ == "__main__":
    unittest.main()
