from __future__ import annotations

import os
import socket
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from tools.remote_operation import protocol
from tools.remote_operation import pdu_transport


def available_tcp_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


@unittest.skipUnless(
    os.environ.get("HAKO_REMOTE_OPERATION_PDU_INTEGRATION") == "1",
    "set HAKO_REMOTE_OPERATION_PDU_INTEGRATION=1 with the Python PDU Endpoint runtime installed",
)
class RemoteOperationPduIntegrationTest(unittest.TestCase):
    def test_coreless_tcp_is_bidirectional(self) -> None:
        port = available_tcp_port()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            server_config = pdu_transport.write_tcp_endpoint_config(
                root / "server", role="server", address="127.0.0.1", port=port
            )
            client_config = pdu_transport.write_tcp_endpoint_config(
                root / "client", role="client", address="127.0.0.1", port=port
            )
            server = pdu_transport.PduJsonTransport(server_config)
            client = pdu_transport.PduJsonTransport(client_config)
            try:
                server.start()
                client.start()
                server.wait_connected(3.0)
                client.wait_connected(3.0)

                command = protocol.make_message(
                    kind="command",
                    message_type="PREPARE",
                    session_id="integration-001",
                    sequence=1,
                    attempt=1,
                    source_host="srv-01",
                    configuration_id="uav-064-sleep-001ms",
                    config_hash="c" * 64,
                )
                server.send(command)
                self.assertEqual(client.receive(3.0), command)

                status = protocol.make_message(
                    kind="status",
                    message_type="REGISTERED",
                    session_id="integration-001",
                    sequence=1,
                    attempt=1,
                    source_host="cli-01",
                    configuration_id="uav-064-sleep-001ms",
                    config_hash="c" * 64,
                )
                client.send(status)
                self.assertEqual(server.receive(3.0), status)
            finally:
                client.close()
                server.close()


if __name__ == "__main__":
    unittest.main()
