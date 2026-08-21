from __future__ import annotations

import json
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock

from tools.remote_operation import multi_host_scaling_attempt as attempt


PROFILE = (
    Path(__file__).resolve().parents[2]
    / "configs"
    / "remote-operation"
    / "multi-host-temporal-validation.yaml"
)
SCALING_ATTEMPTS_PROFILE = (
    Path(__file__).resolve().parents[2]
    / "configs"
    / "remote-operation"
    / "multi-host-scaling-attempts.yaml"
)


class MultiHostScalingAttemptTest(unittest.TestCase):
    def _state(self) -> dict:
        return {
            "resolved": {
                "measurement": {
                    "configuration_id": "uav-256-sleep-001ms",
                    "attempt": 1,
                }
            },
            "index": {"config_hash": "a" * 64},
        }

    def test_public_roles_share_ports_and_require_session(self) -> None:
        server = attempt.resolve_arguments(
            attempt.parser().parse_args(["--session-id", "mh-001", "server"])
        )
        client = attempt.resolve_arguments(
            attempt.parser().parse_args(["--session-id", "mh-001", "client"])
        )
        self.assertEqual(server.control_port, client.control_port)
        self.assertEqual(server.artifact_port, client.artifact_port)
        self.assertEqual(server.drone_count, 256)
        self.assertEqual(server.attempt_set, "baseline")
        self.assertEqual(
            server.runtime_dir,
            server.output_root / "runtime" / "remote-operation",
        )

    def test_batch_derives_one_session_per_attempt(self) -> None:
        args = attempt.resolve_arguments(
            attempt.parser().parse_args(
                ["--session-id", "mh-batch-01", "server"]
            )
        )
        self.assertEqual(
            [attempt._session(args, 256, number, 3) for number in range(1, 4)],
            [
                "mh-batch-01-uav-256-attempt-01",
                "mh-batch-01-uav-256-attempt-02",
                "mh-batch-01-uav-256-attempt-03",
            ],
        )

    def test_profile_resolves_every_shared_invocation_argument(self) -> None:
        server = attempt.resolve_arguments(
            attempt.parser().parse_args(["--profile", str(PROFILE), "server"])
        )
        client = attempt.resolve_arguments(
            attempt.parser().parse_args(["--profile", str(PROFILE), "client"])
        )
        self.assertEqual(server.session_id, "mh-temporal-uav256-01")
        self.assertEqual(server.session_id, client.session_id)
        self.assertEqual(server.experiment, client.experiment)
        self.assertEqual(server.output_root, client.output_root)
        self.assertEqual(server.drone_count, 256)
        self.assertEqual(server.drone_counts, [256])
        self.assertTrue(server.clean)
        self.assertEqual(server.control_port, client.control_port)
        self.assertEqual(server.artifact_port, client.artifact_port)
        self.assertEqual(server.listen_address, "192.168.2.100")
        self.assertEqual(client.server_address, "192.168.2.100")

    def test_temporal_batch_does_not_request_performance_matrix_summary(self) -> None:
        resolved = attempt.resolve_arguments(
            attempt.parser().parse_args(["--profile", str(PROFILE), "server"])
        )
        with mock.patch.object(attempt.scaling, "summarize_matrix") as summarize:
            attempt._summarize_completed_batch(resolved, {256: [1]}, {})
        summarize.assert_not_called()

    def test_performance_batch_requests_matrix_summary(self) -> None:
        resolved = attempt.resolve_arguments(
            attempt.parser().parse_args(
                ["--profile", str(SCALING_ATTEMPTS_PROFILE), "server"]
            )
        )
        completed = {64: [1, 2, 3], 128: [1, 2, 3], 256: [1, 2, 3]}
        decisions: dict[int, dict] = {}
        with mock.patch.object(attempt.scaling, "summarize_matrix") as summarize:
            attempt._summarize_completed_batch(resolved, completed, decisions)
        summarize.assert_called_once_with(
            resolved.experiment,
            resolved.output_root,
            completed,
            decisions,
        )

    def test_profile_rejects_cli_override(self) -> None:
        parsed = attempt.parser().parse_args(
            [
                "--profile",
                str(PROFILE),
                "--session-id",
                "typo-prone-override",
                "server",
            ]
        )
        with self.assertRaisesRegex(attempt.AttemptError, "CLI overrides"):
            attempt.resolve_arguments(parsed)

    def test_scaling_attempts_profile_selects_isolated_conditional_extension(self) -> None:
        resolved = attempt.resolve_arguments(
            attempt.parser().parse_args(
                ["--profile", str(SCALING_ATTEMPTS_PROFILE), "server"]
            )
        )
        _raw, counts, baseline_attempts = attempt.scaling.load_scaling(
            resolved.experiment
        )
        self.assertEqual(resolved.session_id, "mh-scaling-matrix-01")
        self.assertEqual(resolved.drone_counts, [64, 128, 256])
        self.assertEqual(resolved.drone_count, 64)
        self.assertEqual(
            resolved.attempt_set,
            "baseline_with_conditional_extension",
        )
        self.assertEqual(resolved.drone_counts, counts)
        self.assertEqual(baseline_attempts, 3)
        self.assertEqual(
            resolved.output_root.name,
            "drone-fleet-multi-host-scaling",
        )

    def test_extension_decision_uses_paired_baseline_rtf_spread(self) -> None:
        resolved = attempt.resolve_arguments(
            attempt.parser().parse_args(
                ["--profile", str(SCALING_ATTEMPTS_PROFILE), "server"]
            )
        )
        raw, _counts, _attempts = attempt.scaling.load_scaling(
            resolved.experiment
        )
        resolved.drone_count = 256
        policy = attempt.scaling.attempt_policy(raw["matrix"])
        config_id = attempt.scaling.configuration_id(256, 1, "performance")
        with tempfile.TemporaryDirectory() as temporary:
            resolved.output_root = Path(temporary)
            for attempt_number, rtf in enumerate((1.0, 1.01, 1.20), start=1):
                for host_id in ("srv-01", "cli-01"):
                    path = attempt.scaling.result_path(
                        resolved.output_root,
                        raw["results"]["directory"],
                        raw["measurement"]["series"],
                        host_id,
                        config_id,
                        attempt_number,
                    )
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_text(
                        json.dumps(
                            {
                                "run_id": (
                                    f"{config_id}-attempt-{attempt_number:02d}"
                                ),
                                "status": "success",
                                "mode": "performance",
                                "performance": {"rtf": rtf},
                                "metadata": {
                                    "host_id": host_id,
                                    "configuration_id": config_id,
                                    "attempt": attempt_number,
                                    "config_hash": "a" * 63 + str(attempt_number),
                                    "temporal_observer_enabled": False,
                                    "time_coordination": {
                                        "conductor_real_sleep_msec": 1
                                    },
                                },
                            }
                        ),
                        encoding="utf-8",
                    )
            decision = attempt.extension_decision(
                resolved,
                policy,
                policy["baseline"],
            )

        self.assertTrue(decision["required"])
        self.assertFalse(decision["failure_triggered"])
        self.assertTrue(decision["spread_triggered"])
        self.assertGreater(decision["relative_spread"], 0.05)

    def test_artifact_ports_are_unique_across_conditions_and_attempts(self) -> None:
        resolved = attempt.resolve_arguments(
            attempt.parser().parse_args(
                ["--profile", str(SCALING_ATTEMPTS_PROFILE), "server"]
            )
        )
        ports = {
            attempt._artifact_port(resolved, condition_index, attempt_number, 5)
            for condition_index in range(3)
            for attempt_number in range(1, 6)
        }
        self.assertEqual(len(ports), 15)
        self.assertEqual(min(ports), resolved.artifact_port)
        self.assertEqual(max(ports), resolved.artifact_port + 14)

    def test_clean_removes_only_deferred_extension_attempts_up_front(self) -> None:
        resolved = attempt.resolve_arguments(
            attempt.parser().parse_args(
                ["--profile", str(SCALING_ATTEMPTS_PROFILE), "server"]
            )
        )
        calls: list[tuple[str, int]] = []

        def record(args, _host, operation, _extra=None):
            calls.append((operation, args.current_attempt))

        with mock.patch.object(attempt, "_run", side_effect=record):
            attempt._clean_deferred_attempts(resolved, "srv-01", [4, 5])

        self.assertEqual(
            calls,
            [("configure", 4), ("clean", 4), ("configure", 5), ("clean", 5)],
        )

    def test_prepare_skips_preclean_for_another_output_root_selection(self) -> None:
        resolved = attempt.resolve_arguments(
            attempt.parser().parse_args(
                ["--profile", str(PROFILE), "server"]
            )
        )
        state = {
            "selection": {"host_id": "srv-01"},
            "resolved": {
                "measurement": {
                    "enabled": True,
                    "configuration_id": "temporal-uav-256-sleep-001ms",
                    "attempt": 1,
                    "series": "multi-host-temporal-validation",
                },
                "results": {"enabled": True, "directory": "results"},
                "deployment": {"hosts": {"srv-01": {}, "cli-01": {}}},
            },
        }
        calls: list[str] = []

        def record(_args, _host, operation, _extra=None):
            calls.append(operation)

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            resolved.output_root = root / "output"
            resolved.output_root.mkdir()
            (resolved.output_root / "bundle-index.json").write_text("{}\n")
            selection = root / "local-selection.json"
            selection.write_text("{}\n")
            with (
                mock.patch.object(attempt.multi_host, "LOCAL_SELECTION", selection),
                mock.patch.object(attempt, "_run", side_effect=record),
                mock.patch.object(
                    attempt.multi_host,
                    "load_local_selection",
                    side_effect=[
                        attempt.multi_host.RecipeError(
                            "local host selection belongs to another experiment"
                        ),
                        state,
                    ],
                ),
                mock.patch.object(
                    attempt.multi_host,
                    "measurement_trial_path",
                    return_value=resolved.output_root / "trial",
                ),
            ):
                attempt._prepare(resolved, "srv-01", 1)

        self.assertEqual(calls, ["configure", "clean"])

    def test_verified_client_attempt_is_published_at_receiver_path(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "attempt-01"
            source.mkdir()
            (source / "result.json").write_text(
                json.dumps(
                    {
                        "status": "success",
                        "run_id": "uav-256-sleep-001ms-attempt-01",
                        "metadata": {
                            "host_id": "cli-01",
                            "configuration_id": "uav-256-sleep-001ms",
                            "attempt": 1,
                            "config_hash": "a" * 64,
                        },
                    }
                ),
                encoding="utf-8",
            )
            archive = attempt.create_zip([source], root / "client.zip")
            destination = root / "published" / "attempt-01"
            attempt._extract_client_archive(
                archive, root / "staging", destination, self._state()
            )
            self.assertTrue((destination / "result.json").is_file())
            self.assertFalse((root / "staging").exists())

    def test_archive_path_traversal_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            archive = root / "bad.zip"
            with zipfile.ZipFile(archive, "w") as output:
                output.writestr("../result.json", "{}")
            with self.assertRaisesRegex(attempt.AttemptError, "unsafe ZIP"):
                attempt._extract_client_archive(
                    archive,
                    root / "staging",
                    root / "published" / "attempt-01",
                    self._state(),
                )


if __name__ == "__main__":
    unittest.main()
