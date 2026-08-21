from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from tools.recipe.path_test_support import path_endswith


SCRIPT = Path(__file__).with_name("drone_fleet_single_host.py")
SPEC = importlib.util.spec_from_file_location("drone_fleet_performance_base_test", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
recipe = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = recipe
SPEC.loader.exec_module(recipe)

MATRIX_SCRIPT = Path(__file__).with_name("drone_fleet_performance_a.py")
sys.path.insert(0, str(MATRIX_SCRIPT.parent))
MATRIX_SPEC = importlib.util.spec_from_file_location(
    "drone_fleet_performance_a_test", MATRIX_SCRIPT
)
assert MATRIX_SPEC is not None and MATRIX_SPEC.loader is not None
matrix = importlib.util.module_from_spec(MATRIX_SPEC)
sys.modules[MATRIX_SPEC.name] = matrix
MATRIX_SPEC.loader.exec_module(matrix)

EXPERIMENT = (
    Path(__file__).resolve().parents[2]
    / "recipes"
    / "experiments"
    / "drone-fleet-performance"
    / "single-process-scaling.yaml"
)
RECIPE_MANIFEST = (
    Path(__file__).resolve().parents[2]
    / "recipes"
    / "examples"
    / "drone-fleet-single-process-scaling.yaml"
)


class DroneFleetPerformanceTest(unittest.TestCase):
    def test_recipe_declares_drone_native_runtime_contract(self) -> None:
        manifest = RECIPE_MANIFEST.read_text(encoding="utf-8")
        self.assertIn("native_runtime_requirements:\n  schema_version: 1", manifest)
        self.assertIn("    hakoniwa-drone-core:\n      profile: public-v4.0.0", manifest)
        self.assertIn('      required_roles: ["drone_service"]', manifest)
        self.assertIn('      optional_roles: ["visual_state_publisher"]', manifest)

    def test_preflight_contract_resolves_scalable_headless_processes(self) -> None:
        experiment = recipe.resolve_experiment(EXPERIMENT)
        self.assertGreaterEqual(experiment.drone_count, 1)
        self.assertGreaterEqual(experiment.process_count, 1)
        self.assertFalse(experiment.visualization)
        self.assertIsNotNone(experiment.measurement)
        assert experiment.measurement is not None
        self.assertEqual(experiment.measurement.mode, "performance")
        self.assertEqual(
            experiment.measurement.configuration_id,
            f"uav-{experiment.drone_count:03d}-proc-{experiment.process_count:02d}",
        )
        self.assertEqual(experiment.measurement.fleet_delta_time_usec, 20_000)
        self.assertEqual(experiment.measurement.conductor_delta_time_usec, 1_000)
        self.assertEqual(experiment.measurement.simtime_publish_mode, "not_applicable")
        self.assertEqual(experiment.measurement.preflight_duration_sec, 1.0)
        self.assertEqual(experiment.measurement.preflight_max_cpu_average_percent, 50.0)
        self.assertEqual(experiment.measurement.preflight_max_memory_used_percent, 90.0)
        self.assertEqual(experiment.measurement.minimum_virtual_time_sec, 10.0)
        self.assertEqual(experiment.measurement.minimum_cpu_sample_count, 5)
        self.assertEqual(experiment.measurement.minimum_machine_sample_count, 5)
        self.assertEqual(experiment.measurement.maximum_virtual_time_sec, 180.0)
        self.assertEqual(experiment.measurement.maximum_wall_time_sec, 300.0)

    def test_foundation_contract_requires_measurement_library(self) -> None:
        experiment = recipe.resolve_experiment(EXPERIMENT)
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "requirements.yaml"
            recipe.write_foundation_requirements(output, experiment)
            payload = recipe.load_simple_yaml(output)
        self.assertTrue(
            payload["foundation_requirements"]["hakoniwa-core-pro"]
            ["capabilities"]["measurement_library"]
        )

    def test_launcher_uses_measurement_runner_and_trial_summary(self) -> None:
        experiment = recipe.resolve_experiment(EXPERIMENT)
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            paths = SimpleNamespace(
                recipe_root=root / "recipe",
                recipe_config=root / "recipe" / "config",
                recipe_logs=root / "recipe" / "logs",
                recipe_validation=root / "recipe" / "validation",
                install_prefix=root / "install",
                foundation_config=root / "foundation" / "config",
                foundation_python=root / "python",
            )
            paths.recipe_config.mkdir(parents=True)
            binary = root / "linux-main_hako_drone_service"
            python = root / "python3"
            binary.touch()
            python.touch()
            with mock.patch.object(recipe, "resolve_drone_binary", return_value=binary), mock.patch.object(
                recipe, "resolve_foundation_python", return_value=python
            ):
                launcher_path = recipe.write_launcher(
                    paths, root / "drone", root / "viewer", experiment, "Linux"
                )
            launcher = json.loads(launcher_path.read_text(encoding="utf-8"))
            show = next(asset for asset in launcher["assets"] if asset["name"] == "show-runner")
            self.assertTrue(show["args"][0].endswith("drone_fleet_performance_runner.py"))
            self.assertIn("HAKO_PERFORMANCE_CONFIG", show["env"]["set"])
            summary_index = show["args"].index("--summary-json") + 1
            self.assertTrue(
                path_endswith(
                    show["args"][summary_index],
                    "attempt-01",
                    "execution-summary.json",
                )
            )
            service = next(
                asset for asset in launcher["assets"] if asset["name"] == "drone-service-1"
            )
            self.assertEqual(service["command"], str(binary))

    def test_experiment_a_matrix_is_ordered_and_single_process(self) -> None:
        base, counts, attempts = matrix.load_matrix(EXPERIMENT)
        self.assertEqual(counts, [1, 2, 4, 8, 16, 32, 64, 128])
        self.assertEqual(attempts, 1)
        self.assertEqual(base.process_count, 1)

    def test_materialized_matrix_condition_has_unique_result_identity(self) -> None:
        base, _counts, _attempts = matrix.load_matrix(EXPERIMENT)
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "condition.yaml"
            with mock.patch.object(matrix, "generated_experiment_path", return_value=output):
                generated = matrix.materialize_experiment(base, 32, 2)
            resolved = recipe.resolve_experiment(generated)
        self.assertEqual(resolved.drone_count, 32)
        self.assertEqual(resolved.process_count, 1)
        self.assertEqual(resolved.drones_per_process, 32)
        assert resolved.measurement is not None
        self.assertEqual(resolved.measurement.configuration_id, "uav-032-proc-01")
        self.assertEqual(resolved.measurement.attempt, 2)

    def test_summary_preserves_recorded_attempts_and_reports_missing(self) -> None:
        base, _counts, _attempts = matrix.load_matrix(EXPERIMENT)
        payload = {
            "run_id": "uav-001-proc-01-attempt-01",
            "status": "success",
            "failure_type": None,
            "performance": {"rtf": 5.0, "step_count": 10},
            "machine_preflight": {"cpu_average_percent": 10.0},
            "machine": {"cpu_average_percent": 20.0},
            "metadata": {"preflight_boundary": {"passed": True}},
            "validation": {"passed": True},
        }
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            recorded = root / "uav-001.json"
            recorded.write_text(json.dumps(payload), encoding="utf-8")
            json_summary = root / "summary.json"
            csv_summary = root / "summary.csv"

            def fake_result(
                _base: recipe.Experiment, drone_count: int, _attempt: int
            ) -> Path:
                return recorded if drone_count == 1 else root / "missing.json"

            with mock.patch.object(matrix, "result_path", side_effect=fake_result), mock.patch.object(
                matrix, "summary_paths", return_value=(json_summary, csv_summary)
            ):
                rc = matrix.summarize(base, [1, 2], 1)
            report = json.loads(json_summary.read_text(encoding="utf-8"))
        self.assertEqual(rc, 1)
        self.assertFalse(report["complete"])
        self.assertEqual(report["recorded_result_count"], 1)
        self.assertEqual(report["results"][0]["rtf"], 5.0)


if __name__ == "__main__":
    unittest.main()
