from __future__ import annotations

import json
import tempfile
import unittest
import zipfile
from pathlib import Path

from tools.remote_operation import multi_host_scaling_attempt as attempt


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
        server = attempt.parser().parse_args(
            ["--session-id", "mh-001", "server"]
        )
        client = attempt.parser().parse_args(
            ["--session-id", "mh-001", "client"]
        )
        self.assertEqual(server.control_port, client.control_port)
        self.assertEqual(server.artifact_port, client.artifact_port)
        self.assertEqual(server.drone_count, 256)
        self.assertIsNone(server.runtime_dir)

    def test_batch_derives_one_session_per_attempt(self) -> None:
        args = attempt.parser().parse_args(
            ["--session-id", "mh-batch-01", "server"]
        )
        self.assertEqual(
            [attempt._session(args, number, 3) for number in range(1, 4)],
            [
                "mh-batch-01-attempt-01",
                "mh-batch-01-attempt-02",
                "mh-batch-01-attempt-03",
            ],
        )

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
