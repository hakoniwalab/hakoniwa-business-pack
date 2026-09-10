#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "tools" / "recipe.py"
SPEC = importlib.util.spec_from_file_location("business_pack_tb3_local_requirements", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
guide = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(guide)
RECIPE = ROOT / "recipes" / "examples" / "mujoco-turtlebot3-wall-follower.yaml"

class TurtleBot3RecipeLocalRequirementsTest(unittest.TestCase):
    def setUp(self) -> None:
        self.data = guide.load_recipe(RECIPE)
        self.requirements = guide.validate_local_requirements(self.data)

    def test_recipe_declares_executable_local_dependency_contract(self) -> None:
        self.assertEqual(set(self.requirements), {"hakoniwa-mbody-registry", "hakoniwa-mujoco-robots"})
        self.assertEqual(self.requirements["hakoniwa-mbody-registry"]["source"]["url"], "https://github.com/hakoniwalab/hakoniwa-mbody-registry.git")
        self.assertEqual(self.requirements["hakoniwa-mujoco-robots"]["source"]["url"], "https://github.com/hakoniwalab/hakoniwa-mujoco-robots.git")

    def _resolved(self, dependency_id: str, target: Path, boundary: Path) -> dict:
        requirement = self.requirements[dependency_id]
        source = requirement["source"]
        return guide._resolve_source_requirement(source_id=dependency_id, kind="recipe", target=target, source_type=source["type"], repository=source.get("url"), revision=source.get("revision"), required_artifacts=requirement["required_artifacts"], clone_boundary=boundary)

    def test_missing_dependencies_are_planned_as_clone(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            boundary = Path(temporary)
            for dependency_id in self.requirements:
                source = self._resolved(dependency_id, boundary / dependency_id, boundary)
                self.assertEqual(source["action"], "clone")
                self.assertEqual(source["provenance"]["reproducibility"], "unpinned")

    def test_valid_existing_checkout_is_reused_without_mutation(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            boundary = Path(temporary)
            dependency_id = "hakoniwa-mbody-registry"
            target = boundary / dependency_id
            target.mkdir()
            subprocess.run(["git", "init", "-b", "main", str(target)], check=True, capture_output=True)
            for artifact in self.requirements[dependency_id]["required_artifacts"]:
                path = target / artifact["path"]
                if artifact["kind"] == "directory":
                    path.mkdir(parents=True, exist_ok=True)
                else:
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_text("test\n", encoding="utf-8")
            subprocess.run(["git", "-C", str(target), "add", "."], check=True, capture_output=True)
            subprocess.run(["git", "-C", str(target), "-c", "user.name=Test", "-c", "user.email=test@example.invalid", "commit", "-m", "fixture"], check=True, capture_output=True)
            before = subprocess.run(["git", "-C", str(target), "rev-parse", "HEAD"], check=True, capture_output=True, text=True).stdout.strip()
            source = self._resolved(dependency_id, target, boundary)
            after = subprocess.run(["git", "-C", str(target), "rev-parse", "HEAD"], check=True, capture_output=True, text=True).stdout.strip()
            self.assertEqual(source["action"], "reuse")
            self.assertEqual(before, after)

if __name__ == "__main__":
    unittest.main()
