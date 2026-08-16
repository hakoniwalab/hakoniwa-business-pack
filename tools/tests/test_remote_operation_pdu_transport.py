from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from tools.remote_operation import protocol
from tools.remote_operation import pdu_transport


class FakeKey:
    def __init__(self, *, robot: str, channel_id: int):
        self.robot = robot
        self.channel_id = channel_id


class FakeEndpoint:
    def __init__(self, name: str, direction: str):
        self.name = name
        self.direction = direction
        self.callback = None
        self.sent = []
        self.running = False
        self.opened_path = None

    def open(self, path: str) -> None:
        self.opened_path = path

    def subscribe_on_recv_callback(self, key, callback) -> None:
        self.callback = callback

    def start(self) -> None:
        self.running = True

    def is_running(self) -> bool:
        return self.running

    def send(self, key, payload: bytes) -> None:
        self.sent.append((key, payload))

    def inject(self, key, payload: bytes) -> None:
        self.callback(key, payload)

    def stop(self) -> None:
        self.running = False

    def close(self) -> None:
        pass


def ready_message():
    return protocol.make_message(
        kind="status",
        message_type="READY",
        session_id="session-001",
        sequence=2,
        attempt=1,
        source_host="cli-01",
        configuration_id="uav-064-sleep-001ms",
        config_hash="b" * 64,
    )


class RemoteOperationPduTransportTest(unittest.TestCase):
    def test_generated_tcp_configs_are_core_independent_endpoint_inputs(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            server = pdu_transport.write_tcp_endpoint_config(
                root / "server",
                role="server",
                address="192.168.2.100",
                port=54100,
            )
            client = pdu_transport.write_tcp_endpoint_config(
                root / "client",
                role="client",
                address="192.168.2.100",
                port=54100,
            )
            server_endpoint = json.loads(server.read_text(encoding="utf-8"))
            server_comm = json.loads((server.parent / "comm.json").read_text(encoding="utf-8"))
            client_comm = json.loads((client.parent / "comm.json").read_text(encoding="utf-8"))

        self.assertEqual(server_endpoint["cache"], "cache.json")
        self.assertEqual(server_comm["protocol"], "tcp")
        self.assertEqual(server_comm["direction"], "inout")
        self.assertEqual(server_comm["local"], {"address": "192.168.2.100", "port": 54100})
        self.assertNotIn("remote", server_comm)
        self.assertEqual(client_comm["remote"], {"address": "192.168.2.100", "port": 54100})
        self.assertNotIn("local", client_comm)
        self.assertNotIn("hakoniwa_core", json.dumps(server_endpoint) + json.dumps(server_comm))

    def test_transport_sends_and_receives_validated_json_bytes(self) -> None:
        fake = FakeEndpoint("unused", "unused")
        transport = pdu_transport.PduJsonTransport(
            Path("endpoint.json"),
            endpoint_factory=lambda _name, _direction: fake,
            key_factory=FakeKey,
        )
        message = ready_message()
        transport.start()
        transport.wait_connected(0.1)
        transport.send(message)
        sent_key, sent_payload = fake.sent[0]
        self.assertEqual(sent_key.robot, pdu_transport.PDU_ROBOT)
        self.assertEqual(protocol.decode_message(sent_payload), message)

        fake.inject(sent_key, sent_payload)
        self.assertEqual(transport.receive(0.1), message)
        transport.close()

    def test_invalid_received_payload_is_reported_on_owner_thread(self) -> None:
        fake = FakeEndpoint("unused", "unused")
        transport = pdu_transport.PduJsonTransport(
            Path("endpoint.json"),
            endpoint_factory=lambda _name, _direction: fake,
            key_factory=FakeKey,
        )
        transport.start()
        fake.inject(FakeKey(robot="x", channel_id=1), b"not-json")
        with self.assertRaisesRegex(pdu_transport.TransportError, "invalid.*payload"):
            transport.receive(0.1)
        transport.close()


if __name__ == "__main__":
    unittest.main()
