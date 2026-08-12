#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
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
            self.assertIn("python tools/recipe.py doctor", content)
            self.assertIn("python tools/recipe.py plan", content)
            self.assertIn("python tools/recipe.py configure", content)
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
        self.assertTrue(any("recipe.py doctor" in value for value in values))
        self.assertTrue(any("recipe.py plan" in value for value in values))
        self.assertTrue(any("recipe.py configure" in value for value in values))
        self.assertTrue(all(not value.startswith("python3.12 ") for value in values))

    def test_cli_contract_requires_only_recipe_to_generate_a_guide(self) -> None:
        help_text = guide.parser().format_help()
        self.assertIn("guide", help_text)
        self.assertIn("doctor", help_text)
        self.assertIn("plan", help_text)
        self.assertIn("configure", help_text)
        self.assertIn("--recipe", help_text)
        self.assertIn("--foundation-requirements", help_text)
        self.assertIn("--open", help_text)
        self.assertNotIn("build", help_text)

    def test_unversioned_local_requirements_remain_documentation_only(self) -> None:
        data = {
            "recipe_local_requirements": {
                "legacy-component": {"role": "human-readable provenance"}
            }
        }
        self.assertEqual(guide.validate_local_requirements(data), {})

    def test_versioned_local_requirement_inspects_declared_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            repository = Path(temporary) / "recipe-repository"
            (repository / ".git").mkdir(parents=True)
            (repository / "recipes").mkdir()
            dependency = repository.parent / "viewer"
            dependency.mkdir()
            (dependency / "index.html").write_text("viewer", encoding="utf-8")
            recipe_path = repository / "recipes" / "demo.yaml"
            data = {
                "recipe_local_requirements_schema_version": 1,
                "recipe_local_requirements": {
                    "viewer": {
                        "root": {
                            "default_path": "../viewer",
                            "override_env": "HAKO_TEST_VIEWER_ROOT",
                            "relative_to": "recipe_repository",
                        },
                        "source": {
                            "type": "git",
                            "url": "https://example.invalid/viewer.git",
                        },
                        "required_artifacts": [
                            {"path": "index.html", "kind": "file"}
                        ],
                    }
                },
            }
            result = guide.inspect_local_requirements(recipe_path, data, {})
            self.assertEqual(result[0]["status"], "SATISFIED")

    def test_native_platform_placeholders_resolve_for_local_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            repository = Path(temporary) / "recipe-repository"
            (repository / ".git").mkdir(parents=True)
            (repository / "recipes").mkdir()
            dependency = repository.parent / "runtime"
            binary = dependency / "bin" / "linux-simulator"
            binary.parent.mkdir(parents=True)
            binary.write_text("runtime", encoding="utf-8")
            binary.chmod(0o755)
            recipe_path = repository / "recipes" / "demo.yaml"
            data = {
                "recipe_local_requirements_schema_version": 1,
                "recipe_local_requirements": {
                    "runtime": {
                        "root": {
                            "default_path": "../runtime",
                            "override_env": "HAKO_TEST_RUNTIME_ROOT",
                            "relative_to": "recipe_repository",
                        },
                        "source": {"type": "local"},
                        "required_artifacts": [
                            {
                                "path": "bin/${NATIVE_BIN_PREFIX}-simulator${NATIVE_EXECUTABLE_SUFFIX}",
                                "kind": "executable",
                            }
                        ],
                    }
                },
            }
            with mock.patch.object(guide.sys, "platform", "linux"):
                result = guide.inspect_local_requirements(recipe_path, data, {})
            self.assertEqual(result[0]["status"], "SATISFIED")

    def test_native_platform_placeholders_expand_in_recipe_runtime(self) -> None:
        data = {
            "id": "demo",
            "recipe_runtime_schema_version": 1,
            "recipe_runtime": {
                "environment": {
                    "DEMO_BIN": "${RECIPE_REPOSITORY}/bin/${NATIVE_BIN_PREFIX}-demo${NATIVE_EXECUTABLE_SUFFIX}"
                },
                "launcher": {
                    "template": "recipes/launcher/demo.json",
                    "output": "config/launcher.json",
                    "mode": "immediate",
                },
            },
        }
        paths = SimpleNamespace(recipe_root=Path("/tmp/recipe-runtime"))
        foundation = SimpleNamespace(
            resolve_workspace=lambda _root, _recipe_id: paths,
            foundation_python_executable=lambda _value: Path("/foundation/bin/python"),
        )
        paths.foundation_root = Path("/foundation")
        paths.install_prefix = Path("/foundation/install")
        paths.foundation_python = Path("/foundation/python")
        with mock.patch.object(guide.sys, "platform", "linux"), mock.patch.object(
            guide, "load_foundation_module", return_value=foundation
        ), mock.patch.object(
            guide, "recipe_repository_root", return_value=Path("/recipe")
        ):
            variables, _ = guide.resolve_recipe_environment(Path("demo.yaml"), data)
        self.assertEqual(variables["DEMO_BIN"], "/recipe/bin/linux-demo")

    def test_recipe_plan_clones_only_missing_default_git_dependency(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            repository = Path(temporary) / "recipe-repository"
            (repository / ".git").mkdir(parents=True)
            (repository / "recipes").mkdir()
            recipe_path = repository / "recipes" / "demo.yaml"
            data = {
                "id": "demo",
                "recipe_local_requirements_schema_version": 1,
                "recipe_local_requirements": {
                    "viewer": {
                        "root": {
                            "default_path": "../viewer",
                            "override_env": "HAKO_TEST_VIEWER_ROOT",
                            "relative_to": "recipe_repository",
                        },
                        "source": {
                            "type": "git",
                            "url": "https://example.invalid/viewer.git",
                            "revision": "main",
                        },
                        "required_artifacts": [
                            {"path": "index.html", "kind": "file"}
                        ],
                    }
                },
            }
            with mock.patch.dict(guide.os.environ, {}, clear=True):
                plan = guide.create_recipe_plan(recipe_path, data)
            self.assertEqual(plan["local_sources"][0]["action"], "clone")
            self.assertEqual(plan["local_sources"][0]["revision"], "main")

    def test_recipe_plan_does_not_clone_into_missing_override_path(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            repository = Path(temporary) / "recipe-repository"
            (repository / ".git").mkdir(parents=True)
            (repository / "recipes").mkdir()
            recipe_path = repository / "recipes" / "demo.yaml"
            selected = Path(temporary) / "operator-selected-viewer"
            data = {
                "id": "demo",
                "recipe_local_requirements_schema_version": 1,
                "recipe_local_requirements": {
                    "viewer": {
                        "root": {
                            "default_path": "../viewer",
                            "override_env": "HAKO_TEST_VIEWER_ROOT",
                            "relative_to": "recipe_repository",
                        },
                        "source": {
                            "type": "git",
                            "url": "https://example.invalid/viewer.git",
                        },
                        "required_artifacts": [
                            {"path": "index.html", "kind": "file"}
                        ],
                    }
                },
            }
            with mock.patch.dict(
                guide.os.environ,
                {"HAKO_TEST_VIEWER_ROOT": str(selected)},
                clear=True,
            ):
                plan = guide.create_recipe_plan(recipe_path, data)
            self.assertEqual(
                plan["local_sources"][0]["action"], "provide-overridden-path"
            )

    def test_recipe_runtime_rejects_secret_like_environment_variables(self) -> None:
        data = {
            "recipe_runtime_schema_version": 1,
            "recipe_runtime": {
                "environment": {"SERVICE_TOKEN": "must-not-be-persisted"},
                "launcher": {
                    "template": "recipes/launcher/demo.json",
                    "output": "config/launcher.json",
                    "mode": "immediate",
                },
            },
        }
        with self.assertRaisesRegex(guide.RecipeGuideError, "must not persist"):
            guide.validate_recipe_runtime(data)

    def test_materialize_recipe_runtime_generates_environment_activation_and_launcher(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            repository = Path(temporary) / "recipe-repository"
            (repository / ".git").mkdir(parents=True)
            (repository / "recipes" / "launcher").mkdir(parents=True)
            recipe_path = repository / "recipes" / "demo.yaml"
            template = repository / "recipes" / "launcher" / "demo.json"
            template.write_text('{"version":"0.1","assets":[]}', encoding="utf-8")
            workspace = Path(temporary) / "work" / "recipes" / "demo"
            paths = SimpleNamespace(recipe_root=workspace)
            data = {
                "id": "demo",
                "recipe_runtime_schema_version": 1,
                "recipe_runtime": {
                    "environment": {"DEMO_ROOT": "${RECIPE_REPOSITORY}"},
                    "launcher": {
                        "template": "recipes/launcher/demo.json",
                        "output": "config/launcher.json",
                        "mode": "immediate",
                    },
                },
            }
            with mock.patch.object(
                guide,
                "resolve_recipe_environment",
                return_value=({"DEMO_ROOT": str(repository)}, paths),
            ):
                payload = guide.materialize_recipe_runtime(recipe_path, data)
            self.assertIsNotNone(payload)
            environment = json.loads(
                (workspace / "environment.json").read_text(encoding="utf-8")
            )
            self.assertEqual(environment["variables"]["DEMO_ROOT"], str(repository))
            self.assertTrue((workspace / "activate").is_file())
            self.assertTrue((workspace / "Activate.ps1").is_file())
            self.assertEqual(
                (workspace / "config" / "launcher.json").read_text(encoding="utf-8"),
                template.read_text(encoding="utf-8"),
            )

    def test_launch_recipe_uses_foundation_python_and_composed_environment(self) -> None:
        data = {
            "id": "demo",
            "recipe_runtime_schema_version": 1,
            "recipe_runtime": {
                "environment": {"DEMO_ROOT": "${RECIPE_REPOSITORY}"},
                "launcher": {
                    "template": "recipes/launcher/demo.json",
                    "output": "config/launcher.json",
                    "mode": "immediate",
                },
            },
        }
        paths = SimpleNamespace(
            recipe_root=Path("/tmp/recipe-runtime"),
            foundation_python=Path("/tmp/foundation-python"),
        )
        foundation = SimpleNamespace(
            foundation_python_executable=lambda value: Path("/foundation/bin/python")
        )
        process = mock.Mock()
        process.wait.return_value = 0
        with mock.patch.object(
            guide,
            "inspect_recipe_runtime",
            return_value={"status": "SATISFIED", "reasons": []},
        ), mock.patch.object(
            guide,
            "resolve_recipe_environment",
            return_value=({"DEMO_ROOT": "/demo"}, paths),
        ), mock.patch.object(
            guide, "load_foundation_module", return_value=foundation
        ), mock.patch.object(
            guide,
            "_launcher_environment",
            return_value={"HAKONIWA_WORKSPACE_ACTIVE": "1", "DEMO_ROOT": "/demo"},
        ), mock.patch.object(
            guide, "recipe_repository_root", return_value=Path("/recipe")
        ), mock.patch.object(guide.subprocess, "Popen", return_value=process) as popen:
            self.assertEqual(guide.launch_recipe(Path("demo.yaml"), data), 0)
        command = popen.call_args.args[0]
        self.assertEqual(command[0], str(Path("/foundation/bin/python")))
        self.assertIn("hakoniwa_pdu.apps.launcher.hako_launcher", command)
        self.assertEqual(popen.call_args.kwargs["env"]["DEMO_ROOT"], "/demo")

    def test_launch_recipe_waits_for_launcher_after_keyboard_interrupt(self) -> None:
        data = {
            "id": "demo",
            "recipe_runtime_schema_version": 1,
            "recipe_runtime": {
                "environment": {"DEMO_ROOT": "${RECIPE_REPOSITORY}"},
                "launcher": {
                    "template": "recipes/launcher/demo.json",
                    "output": "config/launcher.json",
                    "mode": "immediate",
                },
            },
        }
        paths = SimpleNamespace(
            recipe_root=Path("/tmp/recipe-runtime"),
            foundation_python=Path("/tmp/foundation-python"),
        )
        foundation = SimpleNamespace(
            foundation_python_executable=lambda value: Path("/foundation/bin/python")
        )
        process = mock.Mock()
        process.wait.side_effect = [KeyboardInterrupt(), 0]
        with mock.patch.object(
            guide,
            "inspect_recipe_runtime",
            return_value={"status": "SATISFIED", "reasons": []},
        ), mock.patch.object(
            guide,
            "resolve_recipe_environment",
            return_value=({"DEMO_ROOT": "/demo"}, paths),
        ), mock.patch.object(
            guide, "load_foundation_module", return_value=foundation
        ), mock.patch.object(
            guide, "_launcher_environment", return_value={}
        ), mock.patch.object(
            guide, "recipe_repository_root", return_value=Path("/recipe")
        ), mock.patch.object(guide.subprocess, "Popen", return_value=process):
            self.assertEqual(guide.launch_recipe(Path("demo.yaml"), data), 0)
        process.wait.assert_has_calls([mock.call(), mock.call(timeout=10)])

    def test_dynamic_experiment_guide_uses_declared_foundation_order(self) -> None:
        recipe_path = self.recipe_dir / "drone-fleet-single-host.yaml"
        data = guide.load_recipe(recipe_path)
        commands = [item.command for item in guide._command_items(data, recipe_path)]
        prepare_native = (
            "python tools/recipe/drone_fleet_single_host.py prepare-native"
        )
        prepare_viewer = (
            "python tools/recipe/drone_fleet_single_host.py prepare-viewer"
        )
        configure = "python tools/recipe/drone_fleet_single_host.py configure"
        generated_doctor = (
            "python tools/foundation.py doctor --recipe "
            "work/recipes/drone-fleet-single-host/config/foundation-requirements.yaml"
        )
        self.assertIn(prepare_native, commands)
        self.assertIn(prepare_viewer, commands)
        self.assertIn(configure, commands)
        self.assertIn(generated_doctor, commands)
        self.assertLess(commands.index(prepare_native), commands.index(configure))
        self.assertLess(commands.index(prepare_viewer), commands.index(configure))
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

    def test_managed_git_clone_is_recursive(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            target = Path(temporary) / "dependency"
            completed = mock.Mock(returncode=0)
            with mock.patch.object(guide.subprocess, "run", return_value=completed) as run:
                guide._clone_repository(
                    "https://example.invalid/dependency.git",
                    target,
                    revision="v1.0.0",
                )
            run.assert_called_once_with(
                [
                    "git",
                    "clone",
                    "--recurse-submodules",
                    "--branch",
                    "v1.0.0",
                    "--single-branch",
                    "https://example.invalid/dependency.git",
                    str(target),
                ],
                cwd=target.parent,
                check=False,
            )

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
