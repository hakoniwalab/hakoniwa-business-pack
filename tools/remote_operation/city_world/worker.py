"""Core-free WebSocket PDU Worker for City World inspection and generation."""

from __future__ import annotations

import argparse
import re
import socket
from pathlib import Path
from typing import Any, Callable

from ..pdu_transport import PduJsonTransport, write_websocket_endpoint_config
from .inspection import PlateauSelectionInspector, inspect_request
from .generation import CityWorldGenerator
from .protocol import (
    PROTOCOL_NAME,
    SCHEMA_VERSION,
    canonical_sha256,
    decode_message,
    encode_message,
)


PDU_ROBOT = "hako_city_world_job"
PDU_CHANNEL_ID = 1
DEFAULT_MAX_DOWNLOAD_BYTES = 8 * 1024 * 1024 * 1024


def _status(command: dict[str, Any], message_type: str, sequence: int, **payload: Any) -> dict[str, Any]:
    source_host = re.sub(r"[^a-z0-9-]+", "-", socket.gethostname().lower()).strip("-")
    source_host = (source_host or "city-world-worker")[:63]
    return {
        "schema_version": SCHEMA_VERSION,
        "protocol": PROTOCOL_NAME,
        "kind": "status",
        "type": message_type,
        "job_id": command["job_id"],
        "sequence": sequence,
        "source_host": source_host,
        "request_sha256": command["request_sha256"],
        **payload,
    }


def handle_inspection_command(
    command: dict[str, Any],
    inspector: Callable[[dict[str, Any]], dict[str, Any]] = inspect_request,
) -> list[dict[str, Any]]:
    if command["kind"] != "command" or command["type"] != "INSPECT_SELECTION":
        raise ValueError("inspection Worker accepts only INSPECT_SELECTION")
    inspecting = _status(command, "INSPECTING", command["sequence"] + 1)
    try:
        inspection = inspector(command["request"])
        message_type = (
            "SELECTION_AVAILABLE"
            if inspection["status"] == "available"
            else "SELECTION_UNAVAILABLE"
        )
        completed = _status(
            command, message_type, command["sequence"] + 2, inspection=inspection,
        )
    except Exception as exc:
        completed = _status(
            command,
            "FAILED",
            command["sequence"] + 2,
            error={"phase": "inspection", "code": "INSPECTION_FAILED", "message": str(exc)},
        )
    # Validate the exact wire representation before returning it to a transport.
    return [decode_message(encode_message(value)) for value in (inspecting, completed)]


def handle_generate_command(
    command: dict[str, Any],
    *,
    inspection: dict[str, Any] | None,
    inspector: Callable[[dict[str, Any]], dict[str, Any]],
    generator: Callable[
        [dict[str, Any], dict[str, Any], Callable[[str, int, str], None]],
        dict[str, Any],
    ],
    emit: Callable[[dict[str, Any]], None] | None = None,
    max_download_bytes: int = DEFAULT_MAX_DOWNLOAD_BYTES,
) -> list[dict[str, Any]]:
    if command["kind"] != "command" or command["type"] != "GENERATE":
        raise ValueError("generation Worker accepts only GENERATE")
    statuses: list[dict[str, Any]] = []
    sequence = command["sequence"]

    def publish(message_type: str, **payload: Any) -> None:
        nonlocal sequence
        sequence += 1
        status = decode_message(encode_message(_status(command, message_type, sequence, **payload)))
        statuses.append(status)
        if emit is not None:
            emit(status)

    try:
        if inspection is None:
            raise ValueError("run Capability inspection before Generate")
        inspection_hash = canonical_sha256(inspection)
        if inspection_hash != command["inspection_sha256"]:
            raise ValueError("Generate does not match the latest inspected selection")
        if inspection["status"] != "available":
            raise ValueError("the inspected selection is not generation-capable")
        if inspection["estimated_download_bytes"] > max_download_bytes:
            raise ValueError(
                "estimated PLATEAU download exceeds the Worker limit: "
                f"{inspection['estimated_download_bytes']} > {max_download_bytes} bytes"
            )

        publish(
            "ACCEPTED",
            inspection_sha256=inspection_hash,
            progress={"percent": 0, "message": "Generateを受け付けました"},
        )
        current = inspector(command["request"])
        if current["status"] != "available":
            raise ValueError("PLATEAU coverage changed; inspect the selection again")
        if current["estimated_download_bytes"] > max_download_bytes:
            raise ValueError("current PLATEAU download estimate exceeds the Worker limit")

        result = generator(
            command,
            inspection,
            lambda kind, percent, message: publish(
                kind,
                inspection_sha256=inspection_hash,
                progress={"percent": percent, "message": message},
            ),
        )
        publish("READY", inspection_sha256=inspection_hash, result=result)
    except Exception as exc:
        publish(
            "FAILED",
            error={"phase": "generation", "code": "GENERATION_FAILED", "message": str(exc)},
        )
    return statuses


def run_worker(
    endpoint_config: Path,
    *,
    once: bool = False,
    max_download_bytes: int = DEFAULT_MAX_DOWNLOAD_BYTES,
) -> int:
    inspector = PlateauSelectionInspector()
    generator = CityWorldGenerator(endpoint_config.parent)
    inspections: dict[str, dict[str, Any]] = {}
    transport = PduJsonTransport(
        endpoint_config,
        encoder=encode_message,
        decoder=decode_message,
        pdu_robot=PDU_ROBOT,
        pdu_channel_id=PDU_CHANNEL_ID,
    )
    with transport:
        print(f"City World Worker listening: {endpoint_config}")
        while True:
            command = transport.receive(3600.0)
            if command["type"] == "INSPECT_SELECTION":
                statuses = handle_inspection_command(command, inspector=inspector)
                terminal = statuses[-1]
                if terminal["type"] == "SELECTION_AVAILABLE":
                    inspections[command["job_id"]] = terminal["inspection"]
            elif command["type"] == "GENERATE":
                def send_generation_status(status: dict[str, Any]) -> None:
                    print(f"[PDU][SEND] {status['type']} job_id={status['job_id']}")
                    transport.send(status)

                statuses = handle_generate_command(
                    command,
                    inspection=inspections.get(command["job_id"]),
                    inspector=inspector,
                    generator=generator,
                    emit=send_generation_status,
                    max_download_bytes=max_download_bytes,
                )
                # Generation statuses were emitted live while the command ran.
                statuses = []
            else:
                statuses = [_status(
                    command,
                    "FAILED",
                    command["sequence"] + 1,
                    error={
                        "phase": "command", "code": "COMMAND_UNSUPPORTED",
                        "message": f"unsupported command: {command['type']}",
                    },
                )]
            for status in statuses:
                print(f"[PDU][SEND] {status['type']} job_id={status['job_id']}")
                transport.send(status)
            if once:
                print("[OK] --once command completed; stopping City World Worker")
                return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--listen-address", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=54210)
    parser.add_argument(
        "--runtime-dir", type=Path,
        default=Path("work/remote-operation/city-world-worker"),
    )
    parser.add_argument(
        "--once", action="store_true",
        help="stop normally after one command (automation only)",
    )
    parser.add_argument(
        "--max-download-gib", type=float, default=8.0,
        help="reject generation when the catalog estimate exceeds this many GiB (default: 8)",
    )
    args = parser.parse_args()
    if args.max_download_gib <= 0:
        parser.error("--max-download-gib must be greater than zero")
    endpoint_config = write_websocket_endpoint_config(
        args.runtime_dir,
        role="server",
        address=args.listen_address,
        port=args.port,
    )
    return run_worker(
        endpoint_config,
        once=args.once,
        max_download_bytes=int(args.max_download_gib * 1024 * 1024 * 1024),
    )


if __name__ == "__main__":
    raise SystemExit(main())
