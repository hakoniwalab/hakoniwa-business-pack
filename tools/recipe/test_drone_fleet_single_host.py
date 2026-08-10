#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path


SCRIPT = Path(__file__).with_name("drone_fleet_single_host.py")
SPEC = importlib.util.spec_from_file_location("drone_fleet_single_host_recipe", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
recipe = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = recipe
SPEC.loader.exec_module(recipe)

FOUNDATION_SCRIPT = Path(__file__).resolve().parents[1] / "foundation.py"
FOUNDATION_SPEC = importlib.util.spec_from_file_location(
    "drone_fleet_foundation_test", FOUNDATION_SCRIPT
)
assert FOUNDATION_SPEC is not None and FOUNDATION_SPEC.loader is not None
foundation = importlib.util.module_from_spec(FOUNDATION_SPEC)
sys.modules[FOUNDATION_SPEC.name] = foundation
FOUNDATION_SPEC.loader.exec_module(foundation)


class DroneFleetSingleHostTest(unittest.TestCase):
    def _experiment(
        self,
        root: Path,
        *,
        drones: int = 100,
        per_process: int = 10,
        visualization: bool = True,
    ) -> Path:
        path = root / "experiment.yaml"
        path.write_text(
            f"""version: 1
experiment:
  id: test-fleet
scale:
  drone_count: {drones}
  drones_per_process: {per_process}
  process_count: auto
runtime:
  mode: native
  visualization: {str(visualization).lower()}
  show_runner_real_time_sync: true
scenario:
  type: hakoniwa-word
  word: HAKONIWA
  letter_width_m: 2.0
  letter_height_m: 4.0
  letter_gap_m: 0.9
  altitude_m: 4.0
  duration_sec: 6.0
  hold_sec: 10.0
  speed_m_s: 3.0
  timeout_sec: 120.0
  land: false
results:
  enabled: false
  directory: results
""",
            encoding="utf-8",
        )
        return path

    def test_auto_process_count_and_build_limits(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            experiment = recipe.resolve_experiment(
                self._experiment(Path(temporary), drones=100, per_process=10)
            )
            self.assertEqual(experiment.process_count, 10)
            self.assertEqual(
                recipe.required_build_limits(experiment),
                {
                    "asset_num": 16,
                    "pdu_channel_max": 4096,
                    "recv_event_max": 2048,
                    "service_client_max": 128,
                    "service_max": 512,
                    "client_name_len_max": 64,
                    "service_name_len_max": 128,
                },
            )

    def test_auto_process_count_uses_ceiling(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            experiment = recipe.resolve_experiment(
                self._experiment(Path(temporary), drones=101, per_process=10)
            )
            self.assertEqual(experiment.process_count, 11)

    def test_explicit_process_count_does_not_require_drones_per_process(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = self._experiment(Path(temporary), drones=100, per_process=10)
            content = path.read_text(encoding="utf-8")
            content = content.replace("  drones_per_process: 10\n", "")
            content = content.replace("  process_count: auto", "  process_count: 3")
            path.write_text(content, encoding="utf-8")
            experiment = recipe.resolve_experiment(path)
            self.assertEqual(experiment.process_count, 3)
            self.assertEqual(experiment.drones_per_process, 34)

    def test_total_drone_count_is_derived_from_process_shape(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = self._experiment(Path(temporary), drones=100, per_process=10)
            content = path.read_text(encoding="utf-8")
            content = content.replace("  drone_count: 100", "  drone_count: auto")
            content = content.replace("  drones_per_process: 10", "  drones_per_process: 26")
            content = content.replace("  process_count: auto", "  process_count: 3")
            path.write_text(content, encoding="utf-8")
            experiment = recipe.resolve_experiment(path)
            self.assertEqual(experiment.drone_count, 78)
            self.assertEqual(experiment.process_count, 3)
            self.assertEqual(experiment.drones_per_process, 26)

    def test_accepts_headless_mode(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            experiment = recipe.resolve_experiment(
                self._experiment(Path(temporary), visualization=False)
            )
            self.assertFalse(experiment.visualization)

    def test_rejects_too_few_drones_for_hakoniwa_strokes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            with self.assertRaisesRegex(recipe.RecipeError, ">= 26"):
                recipe.resolve_experiment(
                    self._experiment(Path(temporary), drones=25, per_process=10)
                )

    def test_rejects_more_than_general_user_binary_profile(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            with self.assertRaisesRegex(
                recipe.RecipeError,
                "general-user limit of 200.*512-drone.*PRO.*license",
            ):
                recipe.resolve_experiment(
                    self._experiment(Path(temporary), drones=201, per_process=10)
                )

    def test_accepts_general_user_limit(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            experiment = recipe.resolve_experiment(
                self._experiment(Path(temporary), drones=200, per_process=10)
            )
            self.assertEqual(experiment.drone_count, 200)

    def test_generated_launcher_uses_one_builtin_conductor_owner(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            paths = foundation.resolve_workspace(root, recipe.RECIPE_ID)
            foundation.prepare_workspace(paths)
            experiment = recipe.resolve_experiment(
                self._experiment(root, drones=100, per_process=10)
            )
            drone_root = root / "hakoniwa-drone-core"
            (drone_root / "lib").mkdir(parents=True)
            (drone_root / "lib" / "mac-main_hako_drone_service").touch()
            (drone_root / "lib" / "mac-drone_visual_state_publisher").touch()
            show_runner = (
                drone_root / "drone_api" / "external_rpc" / "apps" / "show_asset_runner.py"
            )
            show_runner.parent.mkdir(parents=True)
            show_runner.touch()
            python = paths.foundation_python / "bin" / "python3"
            python.parent.mkdir(parents=True)
            python.touch()
            (paths.recipe_config / "scenario").mkdir(parents=True)
            (paths.recipe_validation).mkdir(parents=True, exist_ok=True)
            viewer_root = root / "hakoniwa-threejs-drone"
            viewer_root.mkdir()
            (viewer_root / "index.html").touch()
            launcher = recipe.write_launcher(
                paths,
                drone_root,
                viewer_root,
                experiment,
                "Darwin",
            )
            payload = json.loads(launcher.read_text(encoding="utf-8"))
            serialized = json.dumps(payload)
            services = [a for a in payload["assets"] if a["name"].startswith("drone-service-")]

            self.assertEqual(len(services), 10)
            self.assertNotIn("--disable-conductor", services[0]["args"])
            self.assertNotIn("depends_on", services[0])
            for index, service in enumerate(services[1:], start=1):
                self.assertIn("--disable-conductor", service["args"])
                self.assertNotIn("--real-sleep-msec", service["args"])
                self.assertEqual(
                    service["env"]["set"]["HAKO_CONFIG_PATH"],
                    str(paths.foundation_config / "cpp_core_config.json"),
                )
                self.assertEqual(
                    service["depends_on"], [f"drone-service-{index}"]
                )
            assets = {asset["name"]: asset for asset in payload["assets"]}
            self.assertNotIn("conductor-server", assets)
            self.assertNotIn("conductor-client", assets)
            self.assertEqual(
                payload["defaults"]["env"]["set"]["HAKO_CONFIG_PATH"],
                str(paths.foundation_config / "cpp_core_config.json"),
            )
            self.assertNotIn("/usr/local/hakoniwa", serialized)
            self.assertIn(str(paths.install_prefix), serialized)
            self.assertIn("execution-summary.json", serialized)
            show_runner_asset = assets["show-runner"]
            poll_sleep_index = show_runner_asset["args"].index("--poll-sleep-msec")
            self.assertEqual(show_runner_asset["args"][poll_sleep_index + 1], "0")
            self.assertIn("--real-time-sync", show_runner_asset["args"])
            self.assertIn("visual-state-publisher", serialized)
            self.assertIn("web-bridge-fleets", serialized)
            self.assertIn("threejs-viewer-webserver", serialized)

    def test_headless_launcher_omits_visualization_processes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            paths = foundation.resolve_workspace(root, recipe.RECIPE_ID)
            foundation.prepare_workspace(paths)
            experiment = recipe.resolve_experiment(
                self._experiment(root, drones=26, per_process=26, visualization=False)
            )
            drone_root = root / "hakoniwa-drone-core"
            (drone_root / "lib").mkdir(parents=True)
            (drone_root / "lib" / "mac-main_hako_drone_service").touch()
            show_runner = (
                drone_root / "drone_api" / "external_rpc" / "apps" / "show_asset_runner.py"
            )
            show_runner.parent.mkdir(parents=True)
            show_runner.touch()
            python = paths.foundation_python / "bin" / "python3"
            python.parent.mkdir(parents=True)
            python.touch()
            (paths.recipe_config / "scenario").mkdir(parents=True)
            paths.recipe_validation.mkdir(parents=True, exist_ok=True)
            launcher = recipe.write_launcher(
                paths,
                drone_root,
                root / "viewer-does-not-exist",
                experiment,
                "Darwin",
            )
            payload = json.loads(launcher.read_text(encoding="utf-8"))
            asset_names = {asset["name"] for asset in payload["assets"]}
            self.assertNotIn("conductor-server", asset_names)
            self.assertNotIn("conductor-client", asset_names)
            self.assertIn("drone-service-1", asset_names)
            self.assertIn("show-runner", asset_names)
            self.assertNotIn("visual-state-publisher", asset_names)
            self.assertNotIn("web-bridge-fleets", asset_names)
            self.assertNotIn("threejs-viewer-webserver", asset_names)
            drone_service = next(
                asset
                for asset in payload["assets"]
                if asset["name"] == "drone-service-1"
            )
            self.assertNotIn("--real-sleep-msec", drone_service["args"])

    def test_foundation_requirements_are_parseable_by_resolver(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            experiment = recipe.resolve_experiment(self._experiment(root))
            output = root / "requirements.yaml"
            recipe.write_foundation_requirements(output, experiment)
            requirements = foundation.load_foundation_requirements(output)
            self.assertEqual(
                requirements["hakoniwa-core-pro"]["build_limits"]["service_max"]["min"],
                512,
            )
            self.assertTrue(
                requirements["hakoniwa-pdu-python"]["capabilities"]["external_rpc"]
            )
            self.assertTrue(
                requirements["hakoniwa-pdu-bridge-core"]["capabilities"][
                    "web_bridge_fleets_config_format"
                ]
            )

    def test_headless_requirements_omit_web_bridge(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            experiment = recipe.resolve_experiment(
                self._experiment(root, visualization=False)
            )
            output = root / "requirements.yaml"
            recipe.write_foundation_requirements(output, experiment)
            requirements = foundation.load_foundation_requirements(output)
            self.assertIn("hakoniwa-core-pro", requirements)
            self.assertIn("hakoniwa-pdu-python", requirements)
            self.assertIn("hakoniwa-pdu-endpoint", requirements)
            self.assertNotIn("hakoniwa-pdu-bridge-core", requirements)

    def test_open_viewer_rejects_headless_experiment(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = self._experiment(Path(temporary), visualization=False)
            with self.assertRaisesRegex(recipe.RecipeError, "headless experiment"):
                recipe.open_viewer(path)

    def test_static_recipe_requirements_are_parseable_by_resolver(self) -> None:
        requirements = foundation.load_foundation_requirements(recipe.recipe_file())
        for component in (
            "hakoniwa-core-pro",
            "hakoniwa-pdu-python",
            "hakoniwa-pdu-endpoint",
            "hakoniwa-pdu-bridge-core",
        ):
            minimum = requirements[component]["build_limits"]["asset_num"]["min"]
            self.assertIsInstance(minimum, int, component)

    def test_session_file_uses_recipe_runtime_directory(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            paths = foundation.resolve_workspace(Path(temporary), recipe.RECIPE_ID)
            self.assertEqual(
                recipe.session_file(paths),
                paths.recipe_root / "runtime" / "launcher-session.json",
            )

    def test_viewer_url_enables_dynamic_spawn_for_resolved_drone_count(self) -> None:
        url = recipe.viewer_url(26)
        self.assertIn("dynamicSpawn=true", url)
        self.assertIn("templateDroneIndex=0", url)
        self.assertIn("maxDynamicDrones=26", url)

if __name__ == "__main__":
    unittest.main()
