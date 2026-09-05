#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

SCRIPT = Path(__file__).resolve().parents[1] / "recipe.py"
SPEC = importlib.util.spec_from_file_location("business_pack_recipe_git_revision", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
guide = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(guide)


class RecipeGitRevisionTest(unittest.TestCase):
    def test_unpinned_clone_keeps_existing_recursive_clone_policy(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            target = Path(temporary) / "dependency"
            completed = SimpleNamespace(returncode=0)
            with mock.patch.object(
                guide.subprocess, "run", return_value=completed
            ) as run:
                guide._clone_repository(
                    "https://example.invalid/dependency.git", target
                )

        run.assert_called_once_with(
            [
                "git",
                "clone",
                "--recurse-submodules",
                "https://example.invalid/dependency.git",
                str(target),
            ],
            cwd=target.parent,
            check=False,
        )

    def test_named_revision_keeps_existing_branch_clone_policy(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            target = Path(temporary) / "dependency"
            completed = SimpleNamespace(returncode=0)
            with mock.patch.object(
                guide.subprocess, "run", return_value=completed
            ) as run:
                guide._clone_repository(
                    "https://example.invalid/dependency.git",
                    target,
                    revision="v1.2.3",
                )

        run.assert_called_once_with(
            [
                "git",
                "clone",
                "--recurse-submodules",
                "--branch",
                "v1.2.3",
                "--single-branch",
                "https://example.invalid/dependency.git",
                str(target),
            ],
            cwd=target.parent,
            check=False,
        )

    def test_full_commit_sha_clones_then_checks_out_detached_and_updates_submodules(self) -> None:
        revision = "52ccb4c5e75739e2312105e83f50d9068e45b584"
        with tempfile.TemporaryDirectory() as temporary:
            target = Path(temporary) / "dependency"
            completed = SimpleNamespace(returncode=0)
            with mock.patch.object(
                guide.subprocess, "run", return_value=completed
            ) as run:
                guide._clone_repository(
                    "https://example.invalid/dependency.git",
                    target,
                    revision=revision,
                )

        self.assertEqual(
            run.call_args_list,
            [
                mock.call(
                    [
                        "git",
                        "clone",
                        "--no-checkout",
                        "https://example.invalid/dependency.git",
                        str(target),
                    ],
                    cwd=target.parent,
                    check=False,
                ),
                mock.call(
                    ["git", "-C", str(target), "checkout", "--detach", revision],
                    cwd=target.parent,
                    check=False,
                ),
                mock.call(
                    [
                        "git",
                        "-C",
                        str(target),
                        "submodule",
                        "update",
                        "--init",
                        "--recursive",
                    ],
                    cwd=target.parent,
                    check=False,
                ),
            ],
        )

    def test_full_commit_sha_stops_before_submodules_when_checkout_fails(self) -> None:
        revision = "52ccb4c5e75739e2312105e83f50d9068e45b584"
        with tempfile.TemporaryDirectory() as temporary:
            target = Path(temporary) / "dependency"
            with mock.patch.object(
                guide.subprocess,
                "run",
                side_effect=[
                    SimpleNamespace(returncode=0),
                    SimpleNamespace(returncode=128),
                ],
            ) as run:
                with self.assertRaisesRegex(
                    guide.RecipeGuideError, "git checkout failed with exit code 128"
                ):
                    guide._clone_repository(
                        "https://example.invalid/dependency.git",
                        target,
                        revision=revision,
                    )

        self.assertEqual(run.call_count, 2)


if __name__ == "__main__":
    unittest.main()
