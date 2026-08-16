from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from tools.remote_operation import protocol


HASH = "a" * 64


def status(message_type: str = "READY", **overrides):
    values = {
        "kind": "status",
        "message_type": message_type,
        "session_id": "multi-host-20260817-001",
        "sequence": 3,
        "attempt": 1,
        "source_host": "cli-01",
        "configuration_id": "uav-128-sleep-001ms",
        "config_hash": HASH,
    }
    values.update(overrides)
    return protocol.make_message(**values)


class RemoteOperationProtocolTest(unittest.TestCase):
    def test_schema_and_implementation_controlled_values_match(self) -> None:
        schema = json.loads(protocol.SCHEMA_PATH.read_text(encoding="utf-8"))
        properties = schema["properties"]
        self.assertEqual(properties["schema_version"]["const"], protocol.SCHEMA_VERSION)
        self.assertEqual(set(properties["type"]["enum"]), protocol.MESSAGE_TYPES)
        self.assertEqual(set(schema["required"]), protocol.REQUIRED_FIELDS)

    def test_utf8_json_round_trip(self) -> None:
        message = protocol.make_message(
            kind="status",
            message_type="FAILED",
            session_id="multi-host-20260817-001",
            sequence=8,
            attempt=2,
            source_host="cli-01",
            configuration_id="uav-256-sleep-001ms",
            config_hash=HASH,
            error={
                "phase": "launch",
                "code": "PROCESS_EXITED",
                "message": "ドローンサービスが終了しました",
            },
        )
        payload = protocol.encode_message(message)
        self.assertIsInstance(payload, bytes)
        self.assertEqual(protocol.decode_message(payload), message)

    def test_arbitrary_remote_command_cannot_be_represented(self) -> None:
        message = status()
        message["shell_command"] = "rm -rf something"
        with self.assertRaisesRegex(protocol.ProtocolError, "unknown fields"):
            protocol.encode_message(message)

        message = status()
        message["kind"] = "command"
        message["type"] = "EXEC"
        with self.assertRaisesRegex(protocol.ProtocolError, "not valid"):
            protocol.encode_message(message)

    def test_command_and_status_types_cannot_be_mixed(self) -> None:
        with self.assertRaisesRegex(protocol.ProtocolError, "not valid"):
            status(message_type="PREPARE")

    def test_failed_requires_structured_error(self) -> None:
        with self.assertRaisesRegex(protocol.ProtocolError, "requires error"):
            status(message_type="FAILED")
        with self.assertRaisesRegex(protocol.ProtocolError, "only for FAILED"):
            status(error={"phase": "run", "code": "X", "message": "bad"})

    def test_decode_rejects_non_utf8_and_oversized_payload(self) -> None:
        with self.assertRaisesRegex(protocol.ProtocolError, "UTF-8"):
            protocol.decode_message(b"\xff")
        with self.assertRaisesRegex(protocol.ProtocolError, "application limit"):
            protocol.decode_message(b"x" * (protocol.MAX_WIRE_BYTES + 1))

    def test_command_happy_path_and_abort_cleanup(self) -> None:
        previous = None
        for next_type in ("PREPARE", "LAUNCH", "RUN", "ABORT", "CLEANUP"):
            protocol.validate_transition("command", previous, next_type)
            previous = next_type

    def test_status_happy_path(self) -> None:
        previous = None
        for next_type in (
            "REGISTERED",
            "PREPARING",
            "READY",
            "LAUNCHED",
            "JOINED",
            "RUNNING",
            "TERMINATED",
            "CLEANED",
        ):
            protocol.validate_transition("status", previous, next_type)
            previous = next_type

    def test_client_may_leave_join_detection_to_server(self) -> None:
        protocol.validate_transition("status", "LAUNCHED", "RUNNING")

    def test_state_machine_rejects_skipped_phase(self) -> None:
        with self.assertRaisesRegex(protocol.ProtocolError, "invalid status transition"):
            protocol.validate_transition("status", "REGISTERED", "RUNNING")


if __name__ == "__main__":
    unittest.main()
