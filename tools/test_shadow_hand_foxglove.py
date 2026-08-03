#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock


SCRIPT = Path(__file__).with_name("shadow_hand_foxglove.py")
SPEC = importlib.util.spec_from_file_location("shadow_hand_foxglove_recipe", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
recipe = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = recipe
SPEC.loader.exec_module(recipe)


class ShadowHandFoxgloveTest(unittest.TestCase):
    def _paths(self, root: Path):
        foundation_root = root / "work/foundation"
        install_prefix = foundation_root / "install"
        recipe_root = root / "work/recipes" / recipe.RECIPE_ID
        for directory in (
            recipe_root / "config",
            recipe_root / "logs",
            foundation_root / "config",
        ):
            directory.mkdir(parents=True, exist_ok=True)
        return SimpleNamespace(
            recipe_root=recipe_root,
            recipe_config=recipe_root / "config",
            recipe_logs=recipe_root / "logs",
            foundation_config=foundation_root / "config",
            install_prefix=install_prefix,
            foundation_python=install_prefix / "python",
        )

    def _runtime(self, root: Path, paths):
        mujoco = root / "hakoniwa-mujoco-robots"
        foxglove = root / "hakoniwa-pdu-foxglove"
        runtime = recipe.RuntimePaths(
            system_name="Darwin",
            foundation_python=paths.foundation_python / "bin/python3",
            hako_cmd=paths.install_prefix / "bin/hako-cmd",
            endpoint_callback_library=paths.install_prefix
            / "lib/libhakoniwa_pdu_endpoint_core_callback.dylib",
            web_bridge=paths.install_prefix / "bin/hakoniwa-pdu-web-bridge",
            hand_asset=mujoco
            / "src/cmake-build/examples/actuators/shadow_hand/shadow-hand-hakoniwa-asset",
            cdr_publisher=foxglove / "build/cdr_stdin_publisher",
            converter=foxglove
            / "examples/shadow-hand-jointstate-to-foxglove/shadow_hand_jointstate_to_foxglove.py",
            sender=mujoco
            / "examples/actuators/shadow_hand/send_shadow_hand_targets.py",
            cors_server=foxglove / "tools/serve_static_cors.py",
        )
        return mujoco, foxglove, runtime

    def test_generated_launcher_uses_foundation_and_workspace(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            paths = self._paths(root)
            mujoco, _foxglove, runtime = self._runtime(root, paths)
            launcher = recipe.write_launcher(paths, mujoco, runtime)
            data = json.loads(launcher.read_text(encoding="utf-8"))
            assets = {item["name"]: item for item in data["assets"]}

            self.assertEqual(
                list(assets),
                [
                    "shadow_hand",
                    "shadow_hand_urdf_server",
                    "foxglove_jointstate_publisher",
                    "shadow_hand_bridge",
                    "shadow_hand_sender",
                ],
            )
            self.assertEqual(
                assets["shadow_hand_bridge"]["command"], str(runtime.web_bridge)
            )
            self.assertEqual(
                assets["foxglove_jointstate_publisher"]["command"],
                str(runtime.foundation_python),
            )
            self.assertIn(str(paths.recipe_root), json.dumps(data))
            self.assertIn(str(paths.install_prefix), json.dumps(data))
            self.assertNotIn("/usr/local/hakoniwa", json.dumps(data))
            self.assertNotIn("endpoint-core-free", json.dumps(data))

    def test_operator_uses_background_session_contract(self) -> None:
        python = Path("/foundation/python/bin/python3")
        launcher = Path("/workspace/config/launcher.json")
        session = Path("/workspace/runtime/launcher-session.json")

        start = recipe.launcher_start_command(python, launcher, session)
        status = recipe.launcher_control_command(python, "status", session)
        stop = recipe.launcher_control_command(python, "terminate", session)

        self.assertIn("--background", start)
        self.assertEqual(start[-1], str(session))
        self.assertIn("hakoniwa_pdu.apps.launcher.hako_launcher_ctl", status)
        self.assertEqual(status[-2], "status")
        self.assertEqual(stop[-2], "terminate")

    def test_portal_contains_complete_operator_handoff(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            paths = self._paths(root)
            mujoco, _foxglove, runtime = self._runtime(root, paths)
            launcher = recipe.write_launcher(paths, mujoco, runtime)
            portal = recipe.write_portal(paths, runtime, launcher)
            content = portal.read_text(encoding="utf-8")

            for action in (
                "configure",
                "doctor",
                "start",
                "open-viewer",
                "status",
                "stop",
            ):
                self.assertIn(
                    f"python tools/shadow_hand_foxglove.py {action}", content
                )
            self.assertIn(recipe.FOXGLOVE_WS_URL, content)
            self.assertIn(recipe.URDF_URL, content)
            self.assertIn("python tools/workspace.py enter", content)
            self.assertLess(
                content.index("python tools/shadow_hand_foxglove.py stop"),
                content.index('data-copy="exit"'),
            )

    def test_staged_schema_path_is_workspace_relative(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            paths = self._paths(root)
            _mujoco, foxglove, _runtime = self._runtime(root, paths)
            (foxglove / "config/shadow_hand").mkdir(parents=True)
            (foxglove / "config/shadow_hand_bridge").mkdir(parents=True)
            (foxglove / "work/urdf/shadow_hand").mkdir(parents=True)
            schema = (
                foxglove
                / "work/schemas/ros2_jazzy/sensor_msgs/msg/JointState.bundle.msg"
            )
            schema.parent.mkdir(parents=True)
            schema.write_text("schema", encoding="utf-8")
            (foxglove / "config/shadow_hand/comm_foxglove_jointstate.json").write_text(
                json.dumps({"channels": [{"schema": {"file": "old"}}]}),
                encoding="utf-8",
            )

            recipe._copy_runtime_inputs(paths, foxglove)
            staged = json.loads(
                (paths.recipe_config / "shadow_hand/comm_foxglove_jointstate.json").read_text(
                    encoding="utf-8"
                )
            )
            self.assertEqual(
                staged["channels"][0]["schema"]["file"],
                "../../assets/schemas/ros2_jazzy/sensor_msgs/msg/JointState.bundle.msg",
            )
            self.assertTrue(
                (
                    paths.recipe_root
                    / "assets/schemas/ros2_jazzy/sensor_msgs/msg/JointState.bundle.msg"
                ).is_file()
            )

    def test_start_waits_for_session_and_both_listeners(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            paths = self._paths(root)
            mujoco, foxglove, runtime = self._runtime(root, paths)
            launcher = paths.recipe_config / "launcher.json"
            launcher.write_text("{}", encoding="utf-8")
            completed = SimpleNamespace(
                returncode=0,
                stdout='{"ok": true, "state": "RUNNING"}\n',
                stderr="",
            )

            with (
                mock.patch.object(
                    recipe,
                    "preflight",
                    return_value=(SimpleNamespace(), paths, runtime),
                ),
                mock.patch.object(recipe, "_run", return_value=0) as run,
                mock.patch.object(recipe.subprocess, "run", return_value=completed),
                mock.patch.object(recipe, "_port_listening", return_value=True) as port,
            ):
                rc = recipe.start(mujoco, foxglove)

            self.assertEqual(rc, 0)
            run.assert_called_once_with(
                recipe.launcher_start_command(
                    runtime.foundation_python,
                    launcher,
                    recipe.session_file(paths),
                ),
                recipe.runtime_environment(paths, runtime),
            )
            self.assertEqual([call.args[0] for call in port.call_args_list], [8766, 8767])

    def test_stop_uses_session_control_and_verifies_listener_cleanup(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            paths = self._paths(root)
            python = paths.foundation_python / "bin/python3"
            python.parent.mkdir(parents=True, exist_ok=True)
            python.touch()
            foundation = SimpleNamespace(
                resolve_workspace=mock.Mock(return_value=paths)
            )
            with (
                mock.patch.object(recipe, "load_foundation_module", return_value=foundation),
                mock.patch.object(recipe, "_run", return_value=0) as run,
                mock.patch.object(recipe, "_port_listening", return_value=False) as port,
            ):
                rc = recipe.stop()

            self.assertEqual(rc, 0)
            run.assert_called_once_with(
                recipe.launcher_control_command(
                    python, "terminate", recipe.session_file(paths)
                )
            )
            self.assertEqual([call.args[0] for call in port.call_args_list], [8766, 8767])


if __name__ == "__main__":
    unittest.main()
