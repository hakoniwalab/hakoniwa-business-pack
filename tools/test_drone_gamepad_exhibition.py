#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

SCRIPT = Path(__file__).with_name("drone_gamepad_exhibition.py")
SPEC = importlib.util.spec_from_file_location("drone_gamepad_exhibition_recipe", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
recipe = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = recipe
SPEC.loader.exec_module(recipe)


class DroneGamepadExhibitionTest(unittest.TestCase):
    def _paths(self, root: Path):
        foundation_root = root / "work" / "foundation"
        install_prefix = foundation_root / "install"
        recipe_root = root / "work" / "recipes" / recipe.RECIPE_ID
        recipe_config = recipe_root / "config"
        recipe_logs = recipe_root / "logs"
        foundation_config = foundation_root / "config"
        for directory in (recipe_config, recipe_logs, foundation_config):
            directory.mkdir(parents=True, exist_ok=True)
        return SimpleNamespace(
            recipe_root=recipe_root,
            recipe_config=recipe_config,
            recipe_logs=recipe_logs,
            install_prefix=install_prefix,
            foundation_config=foundation_config,
            foundation_python=install_prefix / "python",
        )

    def _generate(self, root: Path):
        paths = self._paths(root)
        drone_root = (root / "hakoniwa-drone-core").absolute()
        viewer_root = (root / "hakoniwa-threejs-drone").absolute()
        runtime = recipe.RuntimePaths(
            system_name="Darwin",
            drone_service=(drone_root / "lib" / "mac-main_hako_drone_service").absolute(),
            visual_state_publisher=(
                drone_root / "lib" / "mac-drone_visual_state_publisher"
            ).absolute(),
            foundation_python=(
                paths.install_prefix / "python" / "bin" / "python"
            ).absolute(),
            hako_cmd=(paths.install_prefix / "bin" / "hako-cmd").absolute(),
            web_bridge=(
                paths.install_prefix / "bin" / "hakoniwa-pdu-web-bridge"
            ).absolute(),
        )
        launcher = recipe.write_launcher(paths, drone_root, viewer_root, runtime)
        return paths, drone_root, viewer_root, runtime, launcher

    def test_launcher_contains_exhibition_topology(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            paths, drone_root, _viewer_root, runtime, launcher = self._generate(root)
            data = json.loads(launcher.read_text(encoding="utf-8"))
            assets = {asset["name"]: asset for asset in data["assets"]}

            self.assertEqual(
                list(assets),
                [
                    "drone-service-1",
                    "visual-state-publisher",
                    "web-bridge-fleets",
                    "remote-controller",
                    "threejs-viewer-webserver",
                ],
            )
            self.assertEqual(
                assets["drone-service-1"]["args"],
                [
                    recipe.DRONE_CONFIG,
                    recipe.DRONE_PDU_CONFIG,
                    "--mujoco-viewer",
                    "--real-sleep-msec",
                    "1",
                ],
            )
            self.assertEqual(
                assets["remote-controller"]["command"], str(runtime.foundation_python)
            )
            self.assertEqual(
                assets["threejs-viewer-webserver"]["command"],
                str(runtime.foundation_python),
            )
            self.assertEqual(
                assets["remote-controller"]["args"][0],
                str(drone_root / "drone_api" / "rc" / "rc-custom.py"),
            )
            self.assertTrue(
                assets["remote-controller"]["args"][2].endswith(
                    "rc_config/ps4-control.json"
                )
            )
            self.assertTrue(
                assets["remote-controller"]["args"][1].endswith(
                    "config/pdudef/drone-pdudef-1.json"
                )
            )
            self.assertTrue(
                assets["visual-state-publisher"]["args"][0].endswith(
                    "visual_state_publisher-1.json"
                )
            )
            self.assertIn(str(paths.install_prefix), json.dumps(data))
            self.assertNotIn("/usr/local", json.dumps(data))

    def test_generated_portal_is_recipe_workspace_entry_point(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            paths, _drone_root, _viewer_root, runtime, launcher = self._generate(root)

            portal = recipe.write_portal(paths, runtime, launcher)
            content = portal.read_text(encoding="utf-8")

            self.assertEqual(portal, paths.recipe_root / "index.html")
            self.assertIn("Hakoniwa Drone Gamepad Exhibition", content)
            self.assertIn("Runtime topology", content)
            self.assertIn("Operator workflow", content)
            self.assertIn("Agency boundary", content)
            self.assertIn(str(runtime.foundation_python), content)
            self.assertIn("config/launcher.json", content)
            self.assertIn("runtime/", content)
            self.assertIn("logs/", content)
            self.assertIn(recipe.VIEWER_URL.replace("&", "&amp;"), content)
            for action in ("doctor", "start", "open-viewer", "status", "reset", "stop"):
                self.assertIn(action, content)
                self.assertIn(
                    f"python tools/drone_gamepad_exhibition.py {action}",
                    content,
                )
            self.assertIn("python tools/workspace.py enter", content)
            self.assertIn("data-copy=\"exit\"", content)
            self.assertNotIn(
                f'data-copy="{runtime.foundation_python}',
                content,
            )
            self.assertLess(
                content.index("python tools/drone_gamepad_exhibition.py stop"),
                content.index("data-copy=\"exit\""),
            )
            self.assertIn("data-copy=", content)
            self.assertIn("does not execute local commands", content)

    def test_operator_commands_use_foundation_python_entry_points(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            paths, _drone_root, _viewer_root, runtime, launcher = self._generate(root)
            session = recipe.session_file(paths)

            self.assertEqual(
                session,
                paths.recipe_root / "runtime" / "launcher-session.json",
            )
            start = recipe.launcher_start_command(
                runtime.foundation_python, launcher, session
            )
            status = recipe.launcher_control_command(
                runtime.foundation_python, "status", session
            )
            stop = recipe.launcher_control_command(
                runtime.foundation_python, "terminate", session
            )

            for command in (start, status, stop):
                self.assertEqual(command[0], str(runtime.foundation_python))
                self.assertNotIn("bash", command)
                self.assertNotIn("pwsh", command)
            self.assertIn("hakoniwa_pdu.apps.launcher.hako_launcher", start)
            self.assertIn("--background", start)
            self.assertEqual(status[-2], "status")
            self.assertEqual(stop[-2], "terminate")

    def test_background_start_is_verified_through_session_control(self) -> None:
        python = Path("/foundation/python/bin/python3")
        launcher = Path("/workspace/config/launcher.json")
        session = Path("/workspace/runtime/launcher-session.json")
        env = {"PATH": "/foundation/bin"}
        completed = SimpleNamespace(
            returncode=0,
            stdout='{"ok": true, "state": "RUNNING"}\n',
            stderr="",
        )

        with (
            mock.patch.object(recipe, "_run", return_value=0) as run,
            mock.patch.object(
                recipe.subprocess, "run", return_value=completed
            ) as subprocess_run,
            mock.patch.object(recipe.time, "sleep") as sleep,
        ):
            rc = recipe.start_launcher_and_verify(
                python,
                launcher,
                session,
                env,
                stabilization_sec=1.25,
            )

        self.assertEqual(rc, 0)
        run.assert_called_once_with(
            recipe.launcher_start_command(python, launcher, session),
            env,
        )
        self.assertEqual(
            subprocess_run.call_args.args[0],
            recipe.launcher_control_command(python, "status", session),
        )
        sleep.assert_called_once_with(1.25)

    def test_background_start_rejects_terminal_status(self) -> None:
        completed = SimpleNamespace(
            returncode=0,
            stdout='{"ok": true, "state": "TERMINATED"}\n',
            stderr="",
        )
        with (
            mock.patch.object(recipe, "_run", return_value=0),
            mock.patch.object(recipe.subprocess, "run", return_value=completed),
            mock.patch.object(recipe.time, "sleep"),
        ):
            rc = recipe.start_launcher_and_verify(
                Path("/foundation/python"),
                Path("/workspace/launcher.json"),
                Path("/workspace/session.json"),
                {},
            )

        self.assertEqual(rc, 1)

    def test_background_start_failure_skips_status_verification(self) -> None:
        with (
            mock.patch.object(recipe, "_run", return_value=7) as run,
            mock.patch.object(recipe.time, "sleep") as sleep,
        ):
            rc = recipe.start_launcher_and_verify(
                Path("/foundation/python"),
                Path("/workspace/launcher.json"),
                Path("/workspace/session.json"),
                {},
            )

        self.assertEqual(rc, 7)
        self.assertEqual(run.call_count, 1)
        sleep.assert_not_called()

    def test_user_cli_has_no_arbitrary_python_override(self) -> None:
        self.assertNotIn("--python-bin", recipe.parser().format_help())

    def test_foundation_python_symlink_is_not_resolved_to_system_python(self) -> None:
        if os.name == "nt":
            self.skipTest("POSIX venv symlink behavior")
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            venv_root = root / "work" / "foundation" / "install" / "python"
            (venv_root / "bin").mkdir(parents=True)
            system_python = root / "system-python"
            system_python.write_text("", encoding="utf-8")
            venv_python = venv_root / "bin" / "python"
            venv_python.symlink_to(system_python)
            paths = SimpleNamespace(foundation_python=venv_root)

            selected = recipe.resolve_foundation_python(paths)

            self.assertEqual(selected, venv_python)
            self.assertNotEqual(selected, system_python)

    def test_python_runtime_probe_requires_foundation_prefix_and_imports(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            venv = Path(temporary) / "work" / "foundation" / "install" / "python"
            output = json.dumps(
                {
                    "prefix": str(venv),
                    "executable": str(venv / "bin" / "python"),
                }
            )
            completed = SimpleNamespace(returncode=0, stdout=output + "\n", stderr="")
            with mock.patch.object(recipe.subprocess, "run", return_value=completed) as run:
                ok, detail = recipe._probe_python_runtime(venv / "bin" / "python", venv)

            self.assertTrue(ok, detail)
            command = run.call_args.args[0]
            self.assertEqual(command[0], str(venv / "bin" / "python"))
            self.assertIn("import hakopy", command[-1])
            self.assertIn("import hakoniwa_pdu", command[-1])
            self.assertIn("hako_launcher", command[-1])

    def test_runtime_dependencies_install_into_foundation_python(self) -> None:
        foundation_python = Path("/foundation/python/bin/python")
        completed = SimpleNamespace(returncode=0)
        with (
            mock.patch.object(recipe, "_required", return_value=recipe.RUNTIME_REQUIREMENTS),
            mock.patch.object(recipe.subprocess, "run", return_value=completed) as run,
        ):
            recipe.install_runtime_dependencies(foundation_python)

        command = run.call_args.args[0]
        self.assertEqual(command[0], str(foundation_python))
        self.assertIn("--requirement", command)
        self.assertEqual(command[-1], str(recipe.RUNTIME_REQUIREMENTS))

    def test_missing_pygame_tells_operator_to_rerun_configure(self) -> None:
        completed = SimpleNamespace(
            returncode=1,
            stdout="",
            stderr="ModuleNotFoundError: No module named 'pygame'",
        )
        with mock.patch.object(recipe.subprocess, "run", return_value=completed):
            ok, detail = recipe._probe_controller(Path("/foundation/python"))

        self.assertFalse(ok)
        self.assertIn("rerun the Recipe configure command", detail)

    def test_reset_keeps_platform_shells_out_of_user_contract(self) -> None:
        commands = recipe.reset_commands(Path("/foundation/bin/hako-cmd"))
        self.assertEqual(
            commands,
            [
                ["/foundation/bin/hako-cmd", "stop"],
                ["/foundation/bin/hako-cmd", "reset"],
                ["/foundation/bin/hako-cmd", "start"],
            ],
        )

    def test_recipe_records_human_operation_and_background_cleanup(self) -> None:
        content = recipe.recipe_file().read_text(encoding="utf-8")
        self.assertIn("human-operated-gamepad", content)
        self.assertIn("ps4-control.json", content)
        self.assertIn("mujoco-native-viewer", content)
        self.assertIn("mode: background", content)
        self.assertIn("hako_launcher_ctl terminate", content)
        self.assertIn("environment: foundation-venv", content)
        self.assertIn("interpreter_override: forbidden", content)
        self.assertIn("runtime/launcher-session.json", content)
        self.assertIn("index.html", content)
        self.assertIn("human_actions:", content)
        self.assertIn("operate_gamepad", content)
        self.assertIn("recipes/requirements/drone-single-mujoco-threejs-gamepad.txt", content)
        self.assertIn("hakoniwa-pdu-endpoint:", content)
        self.assertIn("core_callback: true", content)


if __name__ == "__main__":
    unittest.main()
