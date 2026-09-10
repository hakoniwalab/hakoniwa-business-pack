#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
import tempfile
import unittest
from pathlib import Path
from unittest import mock

SCRIPT = Path(__file__).resolve().parents[1] / "recipe.py"
SPEC = importlib.util.spec_from_file_location("business_pack_recipe_provenance", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
guide = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(guide)


class RecipeSourceReproducibilityTest(unittest.TestCase):
    def _source(self, revision: str | None, *, source_type: str = "git") -> dict:
        with tempfile.TemporaryDirectory() as temporary:
            boundary = Path(temporary)
            return guide._resolve_source_requirement(
                source_id="dependency",
                kind="recipe",
                target=boundary / "dependency",
                source_type=source_type,
                repository=(
                    "https://example.invalid/dependency.git"
                    if source_type == "git"
                    else None
                ),
                revision=revision,
                required_artifacts=[{"path": "README.md", "kind": "file"}],
                clone_boundary=boundary,
            )

    def test_full_commit_sha_is_pinned(self) -> None:
        revision = "a" * 40
        source = self._source(revision)
        self.assertEqual(source["action"], "clone")
        self.assertEqual(source["provenance"]["requested_revision"], revision)
        self.assertEqual(source["provenance"]["reproducibility"], "pinned")

    def test_branch_name_is_not_pinned(self) -> None:
        source = self._source("main")
        self.assertEqual(source["action"], "clone")
        self.assertEqual(source["provenance"]["requested_revision"], "main")
        self.assertEqual(source["provenance"]["reproducibility"], "unpinned")

    def test_tag_name_is_not_pinned(self) -> None:
        source = self._source("v1.0.0")
        self.assertEqual(source["action"], "clone")
        self.assertEqual(source["provenance"]["requested_revision"], "v1.0.0")
        self.assertEqual(source["provenance"]["reproducibility"], "unpinned")

    def test_existing_branch_keeps_resolved_sha_without_becoming_pinned(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            boundary = Path(temporary)
            target = boundary / "dependency"
            target.mkdir()
            (target / "README.md").write_text("ok\n", encoding="utf-8")
            resolved = "b" * 40
            with mock.patch.object(guide, "_checkout_revision", return_value=resolved):
                source = guide._resolve_source_requirement(
                    source_id="dependency",
                    kind="recipe",
                    target=target,
                    source_type="git",
                    repository="https://example.invalid/dependency.git",
                    revision="main",
                    required_artifacts=[{"path": "README.md", "kind": "file"}],
                    clone_boundary=boundary,
                )
        self.assertEqual(source["action"], "reuse")
        self.assertEqual(source["provenance"]["requested_revision"], "main")
        self.assertEqual(source["provenance"]["resolved_revision"], resolved)
        self.assertEqual(source["provenance"]["reproducibility"], "unpinned")

    def test_git_without_revision_is_unpinned(self) -> None:
        source = self._source(None)
        self.assertEqual(source["provenance"]["reproducibility"], "unpinned")

    def test_local_source_remains_local(self) -> None:
        source = self._source(None, source_type="local")
        self.assertEqual(source["action"], "provide-local")
        self.assertEqual(source["provenance"]["reproducibility"], "local")


if __name__ == "__main__":
    unittest.main()
