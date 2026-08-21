from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))
SCRIPT = SCRIPT_DIR / "drone_fleet_temporal_b.py"
SPEC = importlib.util.spec_from_file_location("drone_fleet_temporal_b_test", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
temporal_b = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = temporal_b
SPEC.loader.exec_module(temporal_b)

EXPERIMENT = (
    Path(__file__).resolve().parents[2]
    / "recipes"
    / "experiments"
    / "drone-fleet-performance"
    / "single-host-temporal-validation.yaml"
)


def payload(process_count: int) -> dict:
    configuration = temporal_b.configuration_id(process_count)
    return {
        "run_id": f"{configuration}-attempt-01",
        "mode": "temporal",
        "status": "success",
        "temporal": {
            "sample_count": 500,
            "lag_median_usec": 1000.0,
            "lag_p95_usec": 2000.0,
            "lag_max_usec": 3000,
            "accepted_sample_count": 490,
            "rejected_sample_count": 10,
            "acceptance_ratio": 0.98,
        },
        "metadata": {
            "process_count": process_count,
            "temporal_observer_enabled": True,
            "temporal_sampling_interval_usec": 20000,
        },
    }


class DroneFleetTemporalBTest(unittest.TestCase):
    def test_temporal_contract_uses_declared_max_process_count(self) -> None:
        base = temporal_b.load_experiment(EXPERIMENT)
        self.assertEqual(temporal_b.load_process_counts(EXPERIMENT), [15])
        self.assertEqual(base.drone_count, 128)
        assert base.measurement is not None
        self.assertEqual(base.measurement.mode, "temporal")
        self.assertEqual(base.measurement.temporal_sampling_interval_usec, 20_000)
        self.assertEqual(base.measurement.conductor_delta_time_usec, 1_000)
        self.assertEqual(base.measurement.conductor_max_delay_time_usec, 20_000)

    def test_materialized_temporal_condition_has_separate_identity(self) -> None:
        base = temporal_b.load_experiment(EXPERIMENT)
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "temporal.yaml"
            with mock.patch.object(
                temporal_b, "generated_experiment_path", return_value=output
            ):
                generated = temporal_b.materialize_experiment(base, 15)
            resolved = temporal_b.operator.resolve_experiment(generated)
        self.assertEqual(resolved.process_count, 15)
        assert resolved.measurement is not None
        self.assertEqual(
            resolved.measurement.configuration_id, "temporal-uav-128-proc-15"
        )

    def test_validate_result_requires_observer_samples(self) -> None:
        temporal_b.validate_result(payload(2), 2)
        bad = payload(2)
        bad["temporal"]["accepted_sample_count"] = 0
        with self.assertRaises(temporal_b.TemporalError):
            temporal_b.validate_result(bad, 2)

    def test_summarize_keeps_temporal_results_separate(self) -> None:
        base = temporal_b.load_experiment(EXPERIMENT)
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)

            def result_path(_base, process_count):
                path = root / f"proc-{process_count}" / "result.json"
                path.parent.mkdir(parents=True, exist_ok=True)
                if not path.exists():
                    path.write_text(json.dumps(payload(process_count)), encoding="utf-8")
                return path

            json_path = root / "summary.json"
            csv_path = root / "summary.csv"
            with mock.patch.object(
                temporal_b, "result_path", side_effect=result_path
            ), mock.patch.object(
                temporal_b, "summary_paths", return_value=(json_path, csv_path)
            ):
                self.assertEqual(temporal_b.summarize(base, [15]), 0)
            report = json.loads(json_path.read_text(encoding="utf-8"))
        self.assertTrue(report["complete"])
        self.assertEqual(
            [row["process_count"] for row in report["results"]], [15]
        )


if __name__ == "__main__":
    unittest.main()
