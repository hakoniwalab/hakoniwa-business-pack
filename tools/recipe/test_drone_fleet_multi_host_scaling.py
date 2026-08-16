from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

import drone_fleet_multi_host as multi_host
import drone_fleet_multi_host_scaling as scaling
import drone_fleet_single_host as yaml_support


TEMPORAL_EXPERIMENT = (
    Path(__file__).resolve().parents[2]
    / "recipes"
    / "experiments"
    / "drone-fleet-performance"
    / "multi-host-temporal-validation.yaml"
)


class DroneFleetMultiHostScalingTest(unittest.TestCase):
    def test_clean_is_a_host_local_lifecycle_command(self) -> None:
        parsed = scaling.parser().parse_args(["clean"])
        self.assertEqual(parsed.command, "clean")

    def test_collect_is_a_host_local_lifecycle_command(self) -> None:
        parsed = scaling.parser().parse_args(["collect"])
        self.assertEqual(parsed.command, "collect")

    def test_scaling_recipe_uses_scalar_sleep_and_three_attempts(self) -> None:
        raw, counts, attempts = scaling.load_scaling(scaling.DEFAULT_EXPERIMENT)

        self.assertEqual(counts, [64, 128, 256])
        self.assertEqual(attempts, 3)
        self.assertEqual(raw["runtime"]["conductor"]["real_sleep_msec"], 1)
        self.assertNotIn("conductor_real_sleep_msec", raw["matrix"])
        self.assertEqual(
            raw["measurement"]["invalid_conditions"][
                "preflight_max_cpu_average_percent"
            ],
            100.0,
        )

    def test_temporal_recipe_is_one_separate_worst_case_attempt(self) -> None:
        raw, counts, attempts = scaling.load_scaling(TEMPORAL_EXPERIMENT)

        self.assertEqual(counts, [256])
        self.assertEqual(attempts, 1)
        self.assertEqual(raw["measurement"]["mode"], "temporal")
        self.assertEqual(
            raw["measurement"]["series"], "multi-host-temporal-validation"
        )
        self.assertEqual(
            raw["measurement"]["temporal_sampling_interval_usec"], 20_000
        )
        condition = scaling.resolve_condition(raw, 256)
        self.assertEqual(
            condition["measurement"]["configuration_id"],
            "temporal-uav-256-sleep-001ms",
        )

    def test_equal_allocation_keeps_process_policy_fixed(self) -> None:
        raw, _counts, _attempts = scaling.load_scaling(
            scaling.DEFAULT_EXPERIMENT
        )

        expected = {
            64: (32, 32, 32),
            128: (64, 64, 64),
            256: (128, 128, 128),
        }
        for drone_count, (server_count, client_count, client_start) in expected.items():
            condition = scaling.resolve_condition(raw, drone_count)
            hosts = condition["deployment"]["hosts"]
            self.assertEqual(hosts["srv-01"]["drone_count"], server_count)
            self.assertEqual(hosts["srv-01"]["process_count"], 6)
            self.assertEqual(hosts["cli-01"]["drone_count"], client_count)
            self.assertEqual(hosts["cli-01"]["process_count"], 12)
            self.assertEqual(hosts["cli-01"]["global_start_index"], client_start)
            self.assertEqual(condition["scale"]["process_count"], 18)

    def test_resolved_condition_builds_headless_target_conductor_input(self) -> None:
        raw, _counts, _attempts = scaling.load_scaling(
            scaling.DEFAULT_EXPERIMENT
        )
        resolved = multi_host.validate_experiment(
            scaling.resolve_condition(raw, 256)
        )

        self.assertFalse(resolved["runtime"]["visualization"])
        self.assertIsNone(resolved["visualization"])
        conductor = multi_host.build_conductor_input(resolved)
        self.assertEqual(conductor["pdu_groups"], [])
        self.assertEqual(conductor["execution_units"], [])
        self.assertEqual(conductor["eu_pdu_bindings"], [])
        self.assertEqual(
            conductor["conductor_defaults"],
            {
                "delta_time_usec": 1000,
                "max_delay_time_usec": 20000,
                "real_sleep_msec": 1,
                "simtime_publish_mode": "delta_boundary",
                "simtime_publish_interval_usec": 10000,
            },
        )

    def test_generated_condition_round_trips_through_dependency_free_yaml(self) -> None:
        raw, _counts, _attempts = scaling.load_scaling(
            scaling.DEFAULT_EXPERIMENT
        )
        condition = scaling.resolve_condition(raw, 64)
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "condition.yaml"
            yaml_support.write_simple_yaml(path, condition)
            loaded = yaml_support.load_simple_yaml(path)
        self.assertEqual(loaded, condition)

    def test_generated_timing_guard_checks_both_roles(self) -> None:
        raw, _counts, _attempts = scaling.load_scaling(
            scaling.DEFAULT_EXPERIMENT
        )
        resolved = multi_host.validate_experiment(
            scaling.resolve_condition(raw, 64)
        )
        expected = multi_host.build_conductor_input(resolved)[
            "conductor_defaults"
        ]
        with tempfile.TemporaryDirectory() as temporary:
            generated = Path(temporary)
            conductor = generated / "conductor"
            conductor.mkdir()
            for name in ("srv-01.json", "cli-01.json"):
                (conductor / name).write_text(
                    json.dumps(expected), encoding="utf-8"
                )
            self.assertEqual(
                multi_host.generated_conductor_timing_errors(
                    resolved, generated
                ),
                [],
            )
            broken = dict(expected)
            broken.pop("simtime_publish_interval_usec")
            (conductor / "cli-01.json").write_text(
                json.dumps(broken), encoding="utf-8"
            )
            errors = multi_host.generated_conductor_timing_errors(
                resolved, generated
            )

        self.assertEqual(len(errors), 1)
        self.assertIn("cli-01.simtime_publish_interval_usec", errors[0])

    def test_summary_pairs_host_results_and_uses_server_rtf(self) -> None:
        raw, _counts, _attempts = scaling.load_scaling(
            scaling.DEFAULT_EXPERIMENT
        )
        sleep = raw["runtime"]["conductor"]["real_sleep_msec"]
        series = raw["measurement"]["series"]
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary)
            for drone_count in (64, 128, 256):
                config_id = scaling.configuration_id(drone_count, sleep)
                for attempt in range(1, 4):
                    for host_id, rtf, cpu in (
                        ("srv-01", 1.5 + attempt / 10, 72.0),
                        ("cli-01", 1.4 + attempt / 10, 83.0),
                    ):
                        path = scaling.result_path(output, raw["results"]["directory"],
                            series, host_id, config_id, attempt)
                        path.parent.mkdir(parents=True)
                        path.write_text(
                        json.dumps(
                            {
                                "status": "success",
                                "mode": "performance",
                                "run_id": f"{config_id}-attempt-{attempt:02d}",
                                "performance": {"rtf": rtf},
                                "machine": {
                                    "cpu_average_percent": cpu,
                                    "cpu_max_percent": cpu + 5,
                                },
                                "metadata": {
                                    "host_id": host_id,
                                    "configuration_id": config_id,
                                    "attempt": attempt,
                                    "config_hash": "shared-hash",
                                    "temporal_observer_enabled": False,
                                    "time_coordination": {
                                        "conductor_real_sleep_msec": sleep
                                    },
                                },
                            }
                        ),
                            encoding="utf-8",
                        )

            self.assertEqual(
                scaling.summarize(scaling.DEFAULT_EXPERIMENT, output), 0
            )
            report = json.loads(
                (
                    output
                    / "results"
                    / series
                    / "summary"
                    / "multi-host-scaling-sleep-001ms.json"
                ).read_text(encoding="utf-8")
            )

        self.assertTrue(report["complete"])
        self.assertEqual(report["real_sleep_msec"], 1)
        self.assertEqual(report["results"][0]["rtf"], 1.6)
        self.assertEqual(
            report["results"][0]["cli-01_cpu_average_percent"], 83.0
        )
        self.assertEqual(report["statistics"][0]["rtf"]["mean"], 1.7)
        self.assertEqual(report["statistics"][0]["success_count"], 3)

    def test_summary_rejects_a_result_from_another_host(self) -> None:
        raw, _counts, _attempts = scaling.load_scaling(
            scaling.DEFAULT_EXPERIMENT
        )
        sleep = raw["runtime"]["conductor"]["real_sleep_msec"]
        series = raw["measurement"]["series"]
        config_id = scaling.configuration_id(256, sleep)
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary)
            path = scaling.result_path(
                output,
                raw["results"]["directory"],
                series,
                "srv-01",
                config_id,
            )
            path.parent.mkdir(parents=True)
            path.write_text(
                json.dumps(
                    {
                        "mode": "performance",
                        "run_id": f"{config_id}-attempt-01",
                        "metadata": {
                            "host_id": "cli-01",
                            "configuration_id": config_id,
                            "attempt": 1,
                            "config_hash": "shared-hash",
                            "temporal_observer_enabled": False,
                            "time_coordination": {
                                "conductor_real_sleep_msec": sleep
                            },
                        },
                    }
                ),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(scaling.ScalingError, "host_id"):
                scaling.summarize(
                    scaling.DEFAULT_EXPERIMENT,
                    output,
                    selected_drone_count=256,
                )

    def test_temporal_summary_pairs_host_lag_and_world_time_boundaries(self) -> None:
        raw, _counts, _attempts = scaling.load_scaling(TEMPORAL_EXPERIMENT)
        sleep = raw["runtime"]["conductor"]["real_sleep_msec"]
        series = raw["measurement"]["series"]
        config_id = scaling.configuration_id(256, sleep, "temporal")
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary)
            for host_id, world_start, world_end, lag in (
                ("srv-01", 1_060_000, 11_760_000, 2_000),
                ("cli-01", 1_040_000, 11_740_000, 3_000),
            ):
                path = scaling.result_path(
                    output,
                    raw["results"]["directory"],
                    series,
                    host_id,
                    config_id,
                )
                path.parent.mkdir(parents=True)
                path.write_text(
                    json.dumps(
                        {
                            "status": "success",
                            "mode": "temporal",
                            "run_id": f"{config_id}-attempt-01",
                            "performance": {
                                "world_time_start_usec": world_start,
                                "world_time_end_usec": world_end,
                            },
                            "temporal": {
                                "sample_count": 500,
                                "lag_median_usec": lag,
                                "lag_p95_usec": lag + 1_000,
                                "lag_max_usec": lag + 2_000,
                                "accepted_sample_count": 490,
                                "rejected_sample_count": 10,
                                "acceptance_ratio": 0.98,
                            },
                            "metadata": {
                                "host_id": host_id,
                                "configuration_id": config_id,
                                "attempt": 1,
                                "config_hash": "shared-hash",
                                "temporal_observer_enabled": True,
                                "temporal_sampling_interval_usec": 20_000,
                                "time_coordination": {
                                    "conductor_real_sleep_msec": sleep
                                },
                            },
                        }
                    ),
                    encoding="utf-8",
                )

            self.assertEqual(
                scaling.summarize(
                    TEMPORAL_EXPERIMENT,
                    output,
                    selected_drone_count=256,
                ),
                0,
            )
            report = json.loads(
                (
                    output
                    / "results"
                    / series
                    / "summary"
                    / "multi-host-temporal-sleep-001ms-uav-256.json"
                ).read_text(encoding="utf-8")
            )

        self.assertTrue(report["complete"])
        self.assertEqual(report["validation"], "multi-host-temporal")
        row = report["results"][0]
        self.assertEqual(row["world_time_start_difference_usec"], 20_000)
        self.assertEqual(row["world_time_end_difference_usec"], 20_000)
        self.assertEqual(row["srv-01_lag_median_usec"], 2_000)
        self.assertEqual(row["cli-01_lag_p95_usec"], 4_000)
        self.assertEqual(row["cli-01_acceptance_ratio"], 0.98)

    def test_temporal_summary_rejects_disabled_observer(self) -> None:
        raw, _counts, _attempts = scaling.load_scaling(TEMPORAL_EXPERIMENT)
        sleep = raw["runtime"]["conductor"]["real_sleep_msec"]
        series = raw["measurement"]["series"]
        config_id = scaling.configuration_id(256, sleep, "temporal")
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary)
            path = scaling.result_path(
                output,
                raw["results"]["directory"],
                series,
                "srv-01",
                config_id,
            )
            path.parent.mkdir(parents=True)
            path.write_text(
                json.dumps(
                    {
                        "status": "success",
                        "mode": "temporal",
                        "run_id": f"{config_id}-attempt-01",
                        "metadata": {
                            "host_id": "srv-01",
                            "configuration_id": config_id,
                            "attempt": 1,
                            "config_hash": "shared-hash",
                            "temporal_observer_enabled": False,
                            "time_coordination": {
                                "conductor_real_sleep_msec": sleep
                            },
                        },
                    }
                ),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(
                scaling.ScalingError, "temporal_observer_enabled"
            ):
                scaling.summarize(
                    TEMPORAL_EXPERIMENT,
                    output,
                    selected_drone_count=256,
                )

    def test_temporal_summary_rejects_inconsistent_sample_accounting(self) -> None:
        raw, _counts, _attempts = scaling.load_scaling(TEMPORAL_EXPERIMENT)
        sleep = raw["runtime"]["conductor"]["real_sleep_msec"]
        series = raw["measurement"]["series"]
        config_id = scaling.configuration_id(256, sleep, "temporal")
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary)
            path = scaling.result_path(
                output,
                raw["results"]["directory"],
                series,
                "srv-01",
                config_id,
            )
            path.parent.mkdir(parents=True)
            path.write_text(
                json.dumps(
                    {
                        "status": "success",
                        "mode": "temporal",
                        "run_id": f"{config_id}-attempt-01",
                        "performance": {
                            "world_time_start_usec": 1_000_000,
                            "world_time_end_usec": 11_000_000,
                        },
                        "temporal": {
                            "sample_count": 499,
                            "lag_median_usec": 2_000,
                            "lag_p95_usec": 3_000,
                            "lag_max_usec": 4_000,
                            "accepted_sample_count": 490,
                            "rejected_sample_count": 10,
                            "acceptance_ratio": 0.98,
                        },
                        "metadata": {
                            "host_id": "srv-01",
                            "configuration_id": config_id,
                            "attempt": 1,
                            "config_hash": "shared-hash",
                            "temporal_observer_enabled": True,
                            "temporal_sampling_interval_usec": 20_000,
                            "time_coordination": {
                                "conductor_real_sleep_msec": sleep
                            },
                        },
                    }
                ),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(
                scaling.ScalingError, "sample accounting"
            ):
                scaling.summarize(
                    TEMPORAL_EXPERIMENT,
                    output,
                    selected_drone_count=256,
                )


if __name__ == "__main__":
    unittest.main()
