#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

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
        drone_root = (root / "hakoniwa-drone-core").resolve()
        viewer_root = (root / "hakoniwa-threejs-drone").resolve()
        runtime = recipe.RuntimePaths(
            system_name="Darwin",
            drone_service=(drone_root / "lib" / "mac-main_hako_drone_service").resolve(),
            visual_state_publisher=(
                drone_root / "lib" / "mac-drone_visual_state_publisher"
            ).resolve(),
            foundation_python=(
                paths.install_prefix / "python" / "bin" / "python"
            ).resolve(),
            hako_cmd=(paths.install_prefix / "bin" / "hako-cmd").resolve(),
            web_bridge=(
                paths.install_prefix / "bin" / "hakoniwa-pdu-web-bridge"
            ).resolve(),
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
            self.assertIn("--mujoco-viewer", assets["drone-service-1"]["args"])
            self.assertEqual(
                assets["remote-controller"]["command"],
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
            self.assertIn(str(paths.install_prefix), json.dumps(data))
            self.assertNotIn("/usr/local", json.dumps(data))

    def test_operator_commands_use_python_entry_points(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            paths, _drone_root, _viewer_root, runtime, launcher = self._generate(root)
            session = recipe.session_file(paths)

            start = recipe.launcher_start_command(
                runtime.foundation_python, launcher, session
            )
            status = recipe.launcher_control_command(
                runtime.foundation_python, "status", session
            )
            stop = recipe.launcher_control_command(
                runtime.foundation_python, "terminate", session
            )

            self.assertEqual(start[0], str(runtime.foundation_python))
            self.assertIn("hakoniwa_pdu.apps.launcher.hako_launcher", start)
            self.assertIn("--background", start)
            self.assertIn("hakoniwa_pdu.apps.launcher.hako_launcher_ctl", status)
            self.assertEqual(status[-2], "status")
            self.assertEqual(stop[-2], "terminate")
            for command in (start, status, stop):
                self.assertNotIn("bash", command)
                self.assertNotIn("pwsh", command)

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
        self.assertIn("--mujoco-viewer", content)
        self.assertIn("mode: background", content)
        self.assertIn("hako_launcher_ctl terminate", content)
        self.assertIn("human_actions:", content)
        self.assertIn("operate_gamepad", content)


if __name__ == "__main__":
    unittest.main()
