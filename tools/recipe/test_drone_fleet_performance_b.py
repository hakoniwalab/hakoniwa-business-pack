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
SCRIPT = SCRIPT_DIR / "drone_fleet_performance_b.py"
SPEC = importlib.util.spec_from_file_location("drone_fleet_performance_b_test", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
matrix = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = matrix
SPEC.loader.exec_module(matrix)

EXPERIMENT = (
    Path(__file__).resolve().parents[2]
    / "recipes"
    / "experiments"
    / "drone-fleet-performance"
    / "multi-process-scaling.yaml"
)


def result_payload(
    process_count: int, attempt: int, step_sec: float, drone_count: int = 128
) -> dict:
    configuration = matrix.configuration_id(drone_count, process_count)
    return {
        "run_id": f"{configuration}-attempt-{attempt:02d}",
        "status": "success",
        "failure_type": None,
        "performance": {
            "rtf": 1.0 / step_sec,
            "step_count": 10_000,
            "average_step_wall_clock_sec": step_sec,
        },
        "machine_preflight": {
            "cpu_average_percent": 10.0,
            "memory_used_max_percent": 20.0,
        },
        "machine": {"cpu_average_percent": 20.0},
        "metadata": {
            "configuration_id": configuration,
            "drone_count": drone_count,
            "process_count": process_count,
            "attempt": attempt,
            "preflight_boundary": {"passed": True},
            "fleet_phase_results": [
                {"phase": "takeoff", "total": drone_count, "status": "PASS"},
                {"phase": "goto:HAKONIWA", "total": drone_count, "status": "PASS"},
            ],
        },
        "validation": {"passed": True},
    }


class DroneFleetPerformanceBTest(unittest.TestCase):
    def test_matrix_contract_is_sparse_workloads_with_embedded_conductor(self) -> None:
        base, workloads, attempts = matrix.load_matrix(EXPERIMENT)
        self.assertEqual(base.drone_count, 128)
        self.assertEqual(
            matrix.workload_groups(workloads),
            [
                (32, [1, 2, 4, 6, 8, 12, 15]),
                (64, [1, 2, 4, 6, 8, 12, 15]),
                (128, [1, 2, 4, 6, 8, 12, 15]),
            ],
        )
        self.assertEqual(len(workloads), 21)
        self.assertEqual(attempts, 3)
        raw = matrix.operator.load_simple_yaml(EXPERIMENT)
        self.assertEqual(
            raw["matrix"]["attempts"]["extension"]["triggers"],
            {
                "any_failure": True,
                "relative_spread": {
                    "metric": "average_step_wall_clock_sec",
                    "greater_than": 0.05,
                },
            },
        )
        assert base.measurement is not None
        self.assertEqual(base.measurement.conductor_implementation, "embedded")
        self.assertEqual(base.measurement.conductor_delta_time_usec, 1_000)
        self.assertEqual(base.measurement.conductor_max_delay_time_usec, 20_000)
        self.assertEqual(base.measurement.simtime_publish_mode, "not_applicable")
        self.assertEqual(base.measurement.preflight_settle_timeout_sec, 60.0)
        self.assertEqual(base.measurement.preflight_max_cpu_average_percent, 50.0)
        self.assertEqual(base.measurement.preflight_max_memory_used_percent, 90.0)

    def test_near_even_partitions_cover_all_128_uavs(self) -> None:
        self.assertEqual(
            matrix.operator.expected_partition_counts(128, 6),
            [21, 21, 21, 21, 22, 22],
        )
        partitions = matrix.operator.expected_partition_counts(128, 15)
        self.assertEqual(partitions, [8] * 7 + [9] * 8)
        self.assertEqual(sum(partitions), 128)

    def test_materialized_condition_has_process_specific_identity(self) -> None:
        base, _workloads, _attempts = matrix.load_matrix(EXPERIMENT)
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "condition.yaml"
            with mock.patch.object(matrix, "generated_experiment_path", return_value=output):
                generated = matrix.materialize_experiment(
                    base, matrix.Workload(64, 6), 2
                )
            resolved = matrix.operator.resolve_experiment(generated)
        self.assertEqual(resolved.drone_count, 64)
        self.assertEqual(resolved.process_count, 6)
        self.assertEqual(resolved.drones_per_process, 11)
        assert resolved.measurement is not None
        self.assertEqual(resolved.measurement.configuration_id, "uav-064-proc-06")
        self.assertEqual(resolved.measurement.attempt, 2)

    def test_result_identity_rejects_wrong_process_or_fleet_total(self) -> None:
        good = result_payload(6, 1, 0.001)
        matrix.validate_identity(good, 128, 6, 1)
        wrong_process = result_payload(6, 1, 0.001)
        wrong_process["metadata"]["process_count"] = 8
        with self.assertRaises(matrix.MatrixError):
            matrix.validate_identity(wrong_process, 128, 6, 1)
        wrong_total = result_payload(6, 1, 0.001)
        wrong_total["metadata"]["fleet_phase_results"][0]["total"] = 127
        with self.assertRaises(matrix.MatrixError):
            matrix.validate_identity(wrong_total, 128, 6, 1)

    def test_only_successful_preflight_result_is_reusable(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            good = root / "good.json"
            invalid = root / "invalid.json"
            good.write_text(json.dumps(result_payload(1, 1, 0.001)), encoding="utf-8")
            bad_payload = result_payload(2, 1, 0.001)
            bad_payload["status"] = "invalid"
            bad_payload["metadata"]["preflight_boundary"]["passed"] = False
            invalid.write_text(json.dumps(bad_payload), encoding="utf-8")
            self.assertTrue(matrix.reusable_result(good))
            self.assertFalse(matrix.reusable_result(invalid))

    def test_reuse_rechecks_current_preflight_thresholds(self) -> None:
        base, _workloads, _attempts = matrix.load_matrix(EXPERIMENT)
        with tempfile.TemporaryDirectory() as temporary:
            result = Path(temporary) / "result.json"
            payload = result_payload(1, 1, 0.001)
            payload["machine_preflight"]["cpu_average_percent"] = 50.1
            result.write_text(json.dumps(payload), encoding="utf-8")
            self.assertFalse(matrix.reusable_result(result, base))

    def test_non_reusable_attempt_is_archived_recoverably(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            attempt = Path(temporary) / "configuration" / "attempt-01"
            attempt.mkdir(parents=True)
            result = attempt / "result.json"
            result.write_text("{}", encoding="utf-8")
            with mock.patch.object(matrix.time, "strftime", return_value="20260813-120000"):
                archived = matrix.archive_non_reusable_result(result)
            self.assertFalse(attempt.exists())
            self.assertTrue((archived / "result.json").is_file())
            self.assertIn("rejected", archived.parts)

    def test_one_attempt_is_preflight_and_does_not_select_process_count(self) -> None:
        rows = []
        for process_count in (1, 2):
            payload = result_payload(process_count, 1, 0.001)
            rows.append(
                matrix.summary_row(
                    payload, Path(f"result-{process_count}.json"), 128, process_count, 1
                )
            )
        aggregates, selected, status = matrix.aggregate(rows, 128, [1, 2])
        self.assertIsNone(selected)
        self.assertEqual(status, "additional_runs_required")
        self.assertTrue(all(not row["stable_estimate"] for row in aggregates))

    def test_smallest_process_within_five_percent_of_best_is_selected(self) -> None:
        rows = []
        values = {
            1: (0.00103, 0.00104, 0.00105),
            2: (0.00100, 0.00100, 0.00100),
        }
        for process_count, attempts in values.items():
            for attempt, step_sec in enumerate(attempts, 1):
                rows.append(
                    matrix.summary_row(
                        result_payload(process_count, attempt, step_sec),
                        Path(f"result-{process_count}-{attempt}.json"),
                        128,
                        process_count,
                        attempt,
                    )
                )
        aggregates, selected, status = matrix.aggregate(rows, 128, [1, 2])
        self.assertEqual(status, "selected")
        self.assertEqual(selected, 1)
        self.assertTrue(aggregates[0]["performance_equivalent"])

    def test_aggregate_keeps_same_process_count_separate_by_workload(self) -> None:
        rows = []
        for drone_count, step_sec in ((32, 0.0005), (64, 0.002)):
            for attempt in range(1, 4):
                rows.append(
                    matrix.summary_row(
                        result_payload(2, attempt, step_sec, drone_count),
                        Path(f"uav-{drone_count}-attempt-{attempt}.json"),
                        drone_count,
                        2,
                        attempt,
                    )
                )
        aggregates, selected, status = matrix.aggregate(rows, 32, [2])
        self.assertEqual(status, "selected")
        self.assertEqual(selected, 2)
        self.assertEqual(aggregates[0]["drone_count"], 32)
        self.assertEqual(aggregates[0]["recorded_count"], 3)
        self.assertTrue(aggregates[0]["realtime_recovered"])

    def test_large_three_attempt_spread_blocks_selection_and_requests_escalation(self) -> None:
        rows = []
        for attempt, step_sec in enumerate((0.0010, 0.0010, 0.0012), 1):
            rows.append(
                matrix.summary_row(
                    result_payload(1, attempt, step_sec),
                    Path(f"result-{attempt}.json"),
                    128,
                    1,
                    attempt,
                )
            )
        aggregates, selected, status = matrix.aggregate(rows, 128, [1])
        self.assertEqual(status, "additional_runs_required")
        self.assertIsNone(selected)
        self.assertTrue(aggregates[0]["escalation_required"])

    def test_five_attempts_complete_escalation_without_hiding_variation(self) -> None:
        rows = []
        for attempt, step_sec in enumerate(
            (0.0010, 0.0010, 0.0012, 0.0011, 0.0010), 1
        ):
            rows.append(
                matrix.summary_row(
                    result_payload(1, attempt, step_sec),
                    Path(f"result-{attempt}.json"),
                    128,
                    1,
                    attempt,
                )
            )
        aggregates, selected, status = matrix.aggregate(rows, 128, [1])
        self.assertEqual(status, "selected")
        self.assertEqual(selected, 1)
        self.assertTrue(aggregates[0]["escalation_required"])
        self.assertTrue(aggregates[0]["stable_estimate"])

    def test_posix_process_inspection_finds_native_drone_services(self) -> None:
        completed = mock.Mock(
            returncode=0,
            stdout=(
                "  100 /usr/bin/unrelated\n"
                "  321 /tmp/linux-main_hako_drone_service config.json\n"
            ),
        )
        with mock.patch.object(matrix.platform, "system", return_value="Linux"), mock.patch.object(
            matrix.subprocess, "run", return_value=completed
        ):
            processes = matrix.running_drone_services()
        self.assertEqual(
            processes,
            {321: "/tmp/linux-main_hako_drone_service config.json"},
        )

    def test_pre_attempt_guard_rejects_orphan_drone_service(self) -> None:
        with mock.patch.object(
            matrix,
            "running_drone_services",
            return_value={321: "/tmp/linux-main_hako_drone_service config.json"},
        ):
            with self.assertRaisesRegex(matrix.MatrixError, "already exist"):
                matrix.require_clean_process_state()

    def test_post_attempt_cleanup_terminates_only_new_drone_services(self) -> None:
        with mock.patch.object(
            matrix,
            "running_drone_services",
            return_value={100: "existing", 321: "spawned"},
        ), mock.patch.object(
            matrix, "wait_for_drone_services_to_exit", return_value={}
        ), mock.patch.object(matrix.platform, "system", return_value="Linux"), mock.patch.object(
            matrix.os, "kill"
        ) as kill:
            matrix.cleanup_spawned_drone_services({100})
        kill.assert_called_once_with(321, matrix.signal.SIGTERM)

    def test_extend_runs_attempts_four_and_five_only_for_escalated_conditions(self) -> None:
        base, workloads, attempts = matrix.load_matrix(EXPERIMENT)
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)

            def fake_result_path(_base, workload, attempt: int) -> Path:
                return (
                    root
                    / f"uav-{workload.drone_count}-process-{workload.process_count}"
                    / f"attempt-{attempt}"
                    / "result.json"
                )

            for workload in workloads:
                values = (
                    (0.0010, 0.0010, 0.0012)
                    if workload == matrix.Workload(64, 6)
                    else (0.001, 0.001, 0.001)
                )
                for attempt, step_sec in enumerate(values, 1):
                    path = fake_result_path(base, workload, attempt)
                    path.parent.mkdir(parents=True)
                    path.write_text(
                        json.dumps(
                            result_payload(
                                workload.process_count,
                                attempt,
                                step_sec,
                                workload.drone_count,
                            )
                        ),
                        encoding="utf-8",
                    )
            with mock.patch.object(matrix, "result_path", side_effect=fake_result_path), mock.patch.object(
                matrix, "run_matrix", return_value=0
            ) as run_matrix:
                rc = matrix.extend_matrix(
                    base,
                    workloads,
                    attempts,
                    rerun_invalid=False,
                )
        self.assertEqual(rc, 0)
        self.assertEqual(
            run_matrix.call_args.kwargs["attempt_plan"],
            {matrix.Workload(64, 6): [4, 5]},
        )
        self.assertTrue(run_matrix.call_args.kwargs["resume"])

    def test_run_automatically_follows_baseline_with_extension_decision(self) -> None:
        base, workloads, attempts = matrix.load_matrix(EXPERIMENT)
        with mock.patch.object(matrix, "run_matrix", return_value=0) as run_matrix, mock.patch.object(
            matrix, "extend_matrix", return_value=0
        ) as extend_matrix:
            rc = matrix.run_with_automatic_extension(
                base,
                workloads,
                attempts,
                resume=False,
                rerun_invalid=False,
                restart_series=False,
            )
        self.assertEqual(rc, 0)
        self.assertEqual(run_matrix.call_count, 1)
        extend_matrix.assert_called_once_with(
            base,
            workloads,
            attempts,
            rerun_invalid=False,
        )

    def test_failed_measurement_with_valid_preflight_is_preserved_for_extension(self) -> None:
        base, _workloads, _attempts = matrix.load_matrix(EXPERIMENT)
        with tempfile.TemporaryDirectory() as temporary:
            result = Path(temporary) / "result.json"
            payload = result_payload(1, 1, 0.001)
            payload["status"] = "invalid"
            payload["validation"] = {"passed": False}
            result.write_text(json.dumps(payload), encoding="utf-8")
            self.assertTrue(matrix.reusable_result(result, base))


if __name__ == "__main__":
    unittest.main()
