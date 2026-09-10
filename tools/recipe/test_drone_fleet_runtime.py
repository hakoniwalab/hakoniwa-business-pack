#!/usr/bin/env python3
from __future__ import annotations

import tempfile
import unittest
import json
from pathlib import Path
from types import SimpleNamespace

from tools.recipe import drone_fleet_runtime as runtime


class DroneFleetRuntimeTest(unittest.TestCase):
    def source_tree(self, root: Path) -> Path:
        drone = root / "drone"
        for directory in (
            drone / "config/drone/fleets/types",
            drone / "config/controller",
            drone / "config/assets/visual_state_publisher",
            drone / "config/pdudef",
        ):
            directory.mkdir(parents=True, exist_ok=True)
        (drone / "config/drone/fleets/types/type.json").write_text("{}\n")
        (drone / "config/controller/controller.json").write_text("{}\n")
        (drone / "config/assets/visual_state_publisher/visual_state_publisher.json").write_text("{}\n")
        for name in (
            "drone-pdutypes.json",
            "drone-visual-state.json",
            "drone-visual-state-pdutypes.json",
            "pdutypes_time.json",
        ):
            (drone / "config/pdudef" / name).write_text("{}\n")
        return drone

    def paths(self, root: Path):
        recipe_root = root / "recipe"
        config = recipe_root / "config"
        (config / "pdudef").mkdir(parents=True)
        return SimpleNamespace(recipe_root=recipe_root, recipe_config=config)

    def test_single_host_spec_preserves_legacy_vsp_arguments(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            commands = []
            scenario_calls = []
            runtime.prepare_config(
                self.paths(root),
                self.source_tree(root),
                runtime.FleetRuntimeSpec(
                    local_drone_count=8,
                    process_count=2,
                    visualization=True,
                    global_drone_count=8,
                ),
                run_checked=lambda command, **kwargs: commands.append((command, kwargs)),
                scenario_writer=lambda: scenario_calls.append(True) or Path("show.json"),
            )

            vsp = next(command for command, _ in commands if "gen_visual_state_publisher_config.py" in command[1])
            self.assertEqual(vsp[vsp.index("--global-drone-count") + 1], "8")
            self.assertEqual(vsp[vsp.index("--local-drone-count") + 1], "8")
            self.assertEqual(vsp[vsp.index("--max-drones-per-packet") + 1], "512")
            self.assertNotIn("--global-start-index", vsp)
            self.assertNotIn("--output-chunk-base-index", vsp)
            self.assertEqual(scenario_calls, [True])

    def test_multi_host_spec_only_adds_host_placement_arguments(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            commands = []
            runtime.prepare_config(
                self.paths(root),
                self.source_tree(root),
                runtime.FleetRuntimeSpec(
                    local_drone_count=128,
                    process_count=12,
                    visualization=True,
                    global_drone_count=256,
                    global_start_index=128,
                    output_chunk_base_index=1,
                    max_drones_per_packet=128,
                ),
                run_checked=lambda command, **kwargs: commands.append((command, kwargs)),
                scenario_writer=lambda: Path("show.json"),
            )

            split = next(command for command, _ in commands if "gen_fleet_split_config.py" in command[1])
            vsp = next(command for command, _ in commands if "gen_visual_state_publisher_config.py" in command[1])
            self.assertEqual(split[split.index("--parts") + 1], "12")
            self.assertEqual(vsp[vsp.index("--global-drone-count") + 1], "256")
            self.assertEqual(vsp[vsp.index("--local-drone-count") + 1], "128")
            self.assertEqual(vsp[vsp.index("--global-start-index") + 1], "128")
            self.assertEqual(vsp[vsp.index("--output-chunk-base-index") + 1], "1")
            self.assertEqual(vsp[vsp.index("--max-drones-per-packet") + 1], "128")

    def test_partition_rule_is_shared(self) -> None:
        self.assertEqual(
            runtime.expected_partition_counts(128, 12),
            [10, 10, 10, 10, 11, 11, 11, 11, 11, 11, 11, 11],
        )

    def test_scenario_preserves_single_host_center(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            paths = self.paths(root)
            drone = root / "drone"
            generator = drone / "tools/drone-show/gen_word_formation.py"
            generator.parent.mkdir(parents=True)
            generator.write_text("# fixture\n")
            commands = []
            output = runtime.prepare_scenario(
                paths,
                drone,
                runtime.ScenarioRuntimeSpec(
                    experiment_id="multi-host",
                    local_drone_count=128,
                    word="HAKONIWA",
                    letter_width_m=2.0,
                    letter_height_m=4.0,
                    letter_gap_m=0.9,
                    altitude_m=4.0,
                    duration_sec=6.0,
                    hold_sec=10.0,
                    speed_m_s=14.0,
                ),
                run_checked=lambda command, **kwargs: commands.append(command),
            )
            show = json.loads(output.read_text())
            self.assertEqual(show["options"]["center"], [0.0, 0.0, 0.0])
            self.assertEqual(show["meta"]["drone_count"], 128)
            self.assertEqual(commands[0][commands[0].index("--count") + 1], "128")

    def launcher_paths(self, root: Path):
        recipe_root = root / "recipe"
        return SimpleNamespace(
            recipe_root=recipe_root,
            recipe_config=recipe_root / "config",
            recipe_logs=recipe_root / "logs",
            recipe_validation=recipe_root / "validation",
            foundation_config=root / "foundation/config",
            install_prefix=root / "foundation/install",
        )

    def test_external_conductor_disables_every_drone_process(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            output = runtime.prepare_launcher(
                self.launcher_paths(root),
                root / "drone",
                root / "viewer",
                runtime.LauncherRuntimeSpec(
                    local_drone_count=8,
                    process_count=3,
                    visualization=True,
                    external_conductor=True,
                    web_bridge=False,
                    viewer=False,
                    show_runner_real_time_sync=False,
                    land=False,
                    speed_m_s=14.0,
                    timeout_sec=240.0,
                    z_offset_m=2.0,
                ),
                drone_binary=root / "drone-service",
                python=root / "python",
                show_runner=root / "show.py",
                summary=root / "summary.json",
                visual_state_publisher=root / "vsp",
                leading_assets=[
                    {
                        "name": "external-conductor",
                        "activation_timing": "before_start",
                        "command": str(root / "main_client"),
                        "args": ["--config", str(root / "cli-01.json")],
                    }
                ],
            )
            assets = json.loads(output.read_text())["assets"]
            drones = [asset for asset in assets if asset["name"].startswith("drone-service-")]
            self.assertEqual(len(drones), 3)
            self.assertTrue(all("--disable-conductor" in asset["args"] for asset in drones))
            self.assertEqual(assets[0]["name"], "external-conductor")
            self.assertEqual(drones[0]["depends_on"], ["external-conductor"])
            self.assertEqual(
                [asset["name"] for asset in assets[-2:]],
                ["show-runner", "visual-state-publisher"],
            )
            show = next(asset for asset in assets if asset["name"] == "show-runner")
            self.assertEqual(
                show["args"][show["args"].index("--z-offset-m") + 1], "2.0"
            )

    def test_builtin_conductor_keeps_first_process_as_owner(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            output = runtime.prepare_launcher(
                self.launcher_paths(root),
                root / "drone",
                root / "viewer",
                runtime.LauncherRuntimeSpec(
                    local_drone_count=4,
                    process_count=2,
                    visualization=False,
                    external_conductor=False,
                    web_bridge=False,
                    viewer=False,
                    show_runner_real_time_sync=False,
                    land=False,
                    speed_m_s=1.0,
                    timeout_sec=10.0,
                ),
                drone_binary=root / "drone-service",
                python=root / "python",
                show_runner=root / "show.py",
                summary=root / "summary.json",
            )
            assets = json.loads(output.read_text())["assets"]
            self.assertNotIn("--disable-conductor", assets[0]["args"])
            self.assertIn("--disable-conductor", assets[1]["args"])


if __name__ == "__main__":
    unittest.main()
