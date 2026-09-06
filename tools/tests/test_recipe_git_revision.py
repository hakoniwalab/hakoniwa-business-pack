#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
import subprocess
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


def _git(target: Path, *args: str, capture: bool = False) -> str:
    completed = subprocess.run(
        ["git", "-C", str(target), *args],
        check=True,
        capture_output=capture,
        text=True,
    )
    return completed.stdout.strip() if capture else ""


def _make_two_revision_repository(root: Path) -> tuple[Path, str, str]:
    target = root / "dependency"
    target.mkdir()
    subprocess.run(["git", "init", "-q", str(target)], check=True)
    _git(target, "config", "user.email", "test@example.invalid")
    _git(target, "config", "user.name", "Recipe Test")
    tool = target / "tools" / "hako.py"
    tool.parent.mkdir()
    tool.write_text("first\n", encoding="utf-8")
    _git(target, "add", "tools/hako.py")
    _git(target, "commit", "-q", "-m", "first")
    first = _git(target, "rev-parse", "HEAD", capture=True)
    tool.write_text("second\n", encoding="utf-8")
    _git(target, "commit", "-q", "-am", "second")
    second = _git(target, "rev-parse", "HEAD", capture=True)
    _git(target, "checkout", "-q", "--detach", first)
    return target, first, second


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

    def test_matching_existing_pinned_checkout_is_reused(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            target, _first, second = _make_two_revision_repository(Path(temporary))
            _git(target, "checkout", "-q", "--detach", second)
            source = guide._resolve_source_requirement(
                source_id="dependency",
                kind="recipe",
                target=target,
                source_type="git",
                repository="https://example.invalid/dependency.git",
                revision=second,
                required_artifacts=[{"path": "tools/hako.py", "kind": "file"}],
                clone_boundary=target.parent,
            )

        self.assertEqual(source["action"], "reuse")
        self.assertEqual(source["provenance"]["resolved_revision"], second)

    def test_clean_existing_checkout_moves_to_requested_pinned_sha(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            target, first, second = _make_two_revision_repository(Path(temporary))
            source = guide._resolve_source_requirement(
                source_id="dependency",
                kind="recipe",
                target=target,
                source_type="git",
                repository="https://example.invalid/dependency.git",
                revision=second,
                required_artifacts=[{"path": "tools/hako.py", "kind": "file"}],
                clone_boundary=target.parent,
            )
            self.assertEqual(source["action"], "checkout")
            self.assertEqual(source["provenance"]["resolved_revision"], first)

            guide.materialize_sources([source])

            self.assertEqual(_git(target, "rev-parse", "HEAD", capture=True), second)
            self.assertEqual(_git(target, "status", "--porcelain", capture=True), "")

    def test_dirty_existing_checkout_blocks_pinned_revision_change(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            target, _first, second = _make_two_revision_repository(Path(temporary))
            (target / "tools" / "hako.py").write_text("local change\n", encoding="utf-8")

            with self.assertRaisesRegex(
                guide.RecipeGuideError,
                "refusing to change a dirty git checkout",
            ):
                guide._resolve_source_requirement(
                    source_id="dependency",
                    kind="recipe",
                    target=target,
                    source_type="git",
                    repository="https://example.invalid/dependency.git",
                    revision=second,
                    required_artifacts=[{"path": "tools/hako.py", "kind": "file"}],
                    clone_boundary=target.parent,
                )

    def test_overridden_checkout_revision_mismatch_is_not_mutated(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            target, _first, second = _make_two_revision_repository(Path(temporary))

            with self.assertRaisesRegex(
                guide.RecipeGuideError,
                "overridden recipe source revision does not match Recipe pin",
            ):
                guide._resolve_source_requirement(
                    source_id="dependency",
                    kind="recipe",
                    target=target,
                    source_type="git",
                    repository="https://example.invalid/dependency.git",
                    revision=second,
                    required_artifacts=[{"path": "tools/hako.py", "kind": "file"}],
                    clone_boundary=target.parent,
                    overridden=True,
                    override_env="DEPENDENCY_ROOT",
                )


if __name__ == "__main__":
    unittest.main()
