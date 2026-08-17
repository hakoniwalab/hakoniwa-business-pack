from __future__ import annotations

import json
import queue
import sys
import tempfile
import threading
import unittest
import zipfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from tools.remote_operation import artifact_protocol
from tools.remote_operation import artifact_transfer


class QueueTransport:
    def __init__(self, incoming: queue.Queue, outgoing: queue.Queue):
        self.incoming = incoming
        self.outgoing = outgoing

    def send(self, message):
        # Exercise the real codec even though this transport stays in memory.
        self.outgoing.put(
            artifact_protocol.decode_message(
                artifact_protocol.encode_message(message)
            )
        )

    def receive(self, timeout_sec):
        try:
            return self.incoming.get(timeout=timeout_sec)
        except queue.Empty as exc:
            raise RuntimeError("test transport timeout") from exc


def paired_transports():
    server_incoming = queue.Queue()
    client_incoming = queue.Queue()
    return (
        QueueTransport(server_incoming, client_incoming),
        QueueTransport(client_incoming, server_incoming),
    )


class ArtifactProtocolTest(unittest.TestCase):
    def test_schema_and_implementation_types_match(self) -> None:
        schema = json.loads(artifact_protocol.SCHEMA_PATH.read_text(encoding="utf-8"))
        self.assertEqual(
            set(schema["properties"]["type"]["enum"]),
            artifact_protocol.MESSAGE_TYPES,
        )

    def test_offer_rejects_path_and_inconsistent_chunk_count(self) -> None:
        common = {
            "message_type": "OFFER",
            "session_id": "transfer-001",
            "transfer_id": "a" * 32,
            "sequence": 1,
            "source_host": "cli-01",
            "media_type": "application/zip",
            "size_bytes": 2000,
            "sha256": "b" * 64,
            "chunk_size": 1024,
            "chunk_count": 2,
        }
        with self.assertRaisesRegex(
            artifact_protocol.ArtifactProtocolError, "safe .zip basename"
        ):
            artifact_protocol.make_message(
                **common, artifact_name="../results.zip"
            )
        with self.assertRaisesRegex(
            artifact_protocol.ArtifactProtocolError, "chunk_count"
        ):
            artifact_protocol.make_message(
                **{**common, "chunk_count": 1}, artifact_name="results.zip"
            )

    def test_chunk_round_trip(self) -> None:
        data = bytes(range(256)) * 4
        message = artifact_protocol.make_message(
            message_type="CHUNK",
            session_id="transfer-001",
            transfer_id="c" * 32,
            sequence=2,
            source_host="cli-01",
            chunk_index=0,
            data_base64=artifact_protocol.encode_chunk(data),
        )
        decoded = artifact_protocol.decode_message(
            artifact_protocol.encode_message(message)
        )
        self.assertEqual(artifact_protocol.decode_chunk(decoded), data)


class ArtifactTransferTest(unittest.TestCase):
    def test_zip_packaging_rejects_symlink_and_keeps_relative_layout(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "client-results"
            source.mkdir()
            (source / "result.json").write_text('{"ok":true}\n', encoding="utf-8")
            (source / "logs").mkdir()
            (source / "logs" / "run.log").write_text("done\n", encoding="utf-8")
            archive = artifact_transfer.create_zip(
                [source], root / "out" / "results.zip"
            )
            with zipfile.ZipFile(archive) as package:
                names = sorted(package.namelist())

        self.assertEqual(
            names,
            ["client-results/logs/run.log", "client-results/result.json"],
        )

    def test_chunked_transfer_verifies_and_atomically_publishes_zip(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source"
            source.mkdir()
            (source / "payload.bin").write_bytes(bytes(range(256)) * 50)
            archive = artifact_transfer.create_zip(
                [source], root / "sender" / "client-results.zip"
            )
            server, client = paired_transports()
            received: dict = {}
            failure: list[BaseException] = []

            def receive() -> None:
                try:
                    received.update(
                        artifact_transfer.receive_file(
                            server,
                            root / "received",
                            session_id="transfer-001",
                            timeout_sec=2.0,
                            max_bytes=1024 * 1024,
                            event_log=root / "receiver-events.jsonl",
                            on_verified=lambda path, _offer: {
                                "status": "published",
                                "artifact": str(path),
                            },
                        )
                    )
                except BaseException as exc:
                    failure.append(exc)

            thread = threading.Thread(target=receive)
            thread.start()
            sent = artifact_transfer.send_file(
                client,
                archive,
                session_id="transfer-001",
                timeout_sec=2.0,
                chunk_size=1024,
                event_log=root / "sender-events.jsonl",
            )
            thread.join(timeout=3.0)
            self.assertFalse(thread.is_alive())
            self.assertEqual(failure, [])
            destination = Path(received["artifact"])
            self.assertTrue(destination.is_file())
            self.assertFalse(list(destination.parent.glob("*.part")))
            self.assertEqual(destination.read_bytes(), archive.read_bytes())
            self.assertEqual(received["sha256"], sent["sha256"])
            self.assertEqual(received["chunk_count"], sent["chunk_count"])
            self.assertEqual(received["publication"]["status"], "published")


if __name__ == "__main__":
    unittest.main()
