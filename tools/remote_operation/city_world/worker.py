"""Core-free WebSocket PDU Worker for City World inspection and generation."""

from __future__ import annotations

import argparse
import queue
import re
import socket
import threading
from pathlib import Path
from typing import Any, Callable

from ..pdu_transport import PduJsonTransport, TransportError, write_websocket_endpoint_config
from .inspection import PlateauSelectionInspector, inspect_request
from .generation import CityWorldGenerationCanceled, CityWorldGenerator
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
    inspecting = decode_message(encode_message(
        _status(command, "INSPECTING", command["sequence"] + 1)
    ))
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
        completed = decode_message(encode_message(completed))
    except Exception as exc:
        completed = decode_message(encode_message(_status(
            command,
            "FAILED",
            command["sequence"] + 2,
            error={"phase": "inspection", "code": "INSPECTION_FAILED", "message": str(exc)},
        )))
    return [inspecting, completed]


def handle_generate_command(
    command: dict[str, Any],
    *,
    inspection: dict[str, Any] | None,
    inspector: Callable[[dict[str, Any]], dict[str, Any]],
    generator: Callable[
        [dict[str, Any], dict[str, Any], Callable[..., None]],
        dict[str, Any],
    ],
    emit: Callable[[dict[str, Any]], None] | None = None,
    max_download_bytes: int = DEFAULT_MAX_DOWNLOAD_BYTES,
    cancel_event: threading.Event | None = None,
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
        if cancel_event is not None and cancel_event.is_set():
            raise CityWorldGenerationCanceled("City World generation was canceled")
        current = inspector(command["request"])
        if cancel_event is not None and cancel_event.is_set():
            raise CityWorldGenerationCanceled("City World generation was canceled")
        if current["status"] != "available":
            raise ValueError("PLATEAU coverage changed; inspect the selection again")
        if current["estimated_download_bytes"] > max_download_bytes:
            raise ValueError("current PLATEAU download estimate exceeds the Worker limit")

        def report_progress(
            kind: str, percent: int, message: str, *, phase: str | None = None,
            current: int | None = None, total: int | None = None,
        ) -> None:
            detail: dict[str, Any] = {"percent": percent, "message": message}
            if phase is not None:
                detail["phase"] = phase
            if current is not None and total is not None:
                detail.update({"current": current, "total": total})
            publish(kind, inspection_sha256=inspection_hash, progress=detail)

        if cancel_event is None:
            result = generator(command, inspection, report_progress)
        else:
            result = generator(command, inspection, report_progress, cancel_event)
        publish("READY", inspection_sha256=inspection_hash, result=result)
    except CityWorldGenerationCanceled:
        publish("CANCELED")
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
    parallel_workers: int = 4,
    dem_parallel_workers: int = 2,
    terrain_spacing_m: str = "2",
    ready_file: Path | None = None,
) -> int:
    inspector = PlateauSelectionInspector()
    generator = CityWorldGenerator(
        endpoint_config.parent,
        parallel_workers=parallel_workers,
        dem_parallel_workers=dem_parallel_workers,
        terrain_spacing_m=terrain_spacing_m,
    )
    inspections_by_request: dict[str, dict[str, Any]] = {}
    transport = PduJsonTransport(
        endpoint_config,
        encoder=encode_message,
        decoder=decode_message,
        pdu_robot=PDU_ROBOT,
        pdu_channel_id=PDU_CHANNEL_ID,
    )
    with transport:
        if ready_file is not None:
            ready_file.parent.mkdir(parents=True, exist_ok=True)
            ready_file.write_text("ready\n", encoding="utf-8")
        print(f"City World Worker listening: {endpoint_config}")
        active: dict[str, Any] | None = None
        while True:
            if active is not None:
                status_queue: queue.Queue[dict[str, Any]] = active["statuses"]
                while True:
                    try:
                        status = status_queue.get_nowait()
                    except queue.Empty:
                        break
                    print(f"[PDU][SEND] {status['type']} job_id={status['job_id']}")
                    transport.send(status)
                    if status["type"] in {"READY", "FAILED", "CANCELED"}:
                        active["terminal_sent"] = True
                if active.get("terminal_sent") and not active["thread"].is_alive():
                    active["thread"].join()
                    active = None
                    if once:
                        print("[OK] --once generation completed; stopping City World Worker")
                        return 0
            try:
                command = transport.receive(0.1 if active is not None else 3600.0)
            except TransportError as exc:
                if active is not None and str(exc).startswith("no remote-operation message"):
                    continue
                raise
            if command["type"] == "INSPECT_SELECTION":
                if active is not None:
                    statuses = [_status(
                        command, "FAILED", command["sequence"] + 1,
                        error={
                            "phase": "command", "code": "WORKER_BUSY",
                            "message": "a City World generation is already running",
                        },
                    )]
                else:
                    statuses = handle_inspection_command(command, inspector=inspector)
                    terminal = statuses[-1]
                    if terminal["type"] == "SELECTION_AVAILABLE":
                        inspections_by_request[command["request_sha256"]] = terminal["inspection"]
            elif command["type"] == "GENERATE":
                if active is not None:
                    statuses = [_status(
                        command, "FAILED", command["sequence"] + 1,
                        error={
                            "phase": "command", "code": "WORKER_BUSY",
                            "message": "a City World generation is already running",
                        },
                    )]
                else:
                    generation_command = command
                    cancel_event = threading.Event()
                    status_queue = queue.Queue()

                    def generate_in_background() -> None:
                        handle_generate_command(
                            generation_command,
                            inspection=inspections_by_request.get(
                                generation_command["request_sha256"]
                            ),
                            inspector=inspector,
                            generator=generator,
                            emit=status_queue.put,
                            max_download_bytes=max_download_bytes,
                            cancel_event=cancel_event,
                        )

                    thread = threading.Thread(
                        target=generate_in_background,
                        name=f"city-world-generation-{generation_command['job_id']}",
                        daemon=False,
                    )
                    active = {
                        "command": generation_command, "cancel": cancel_event,
                        "statuses": status_queue, "thread": thread,
                        "terminal_sent": False,
                    }
                    thread.start()
                    statuses = []
            elif command["type"] == "CANCEL":
                if (
                    active is not None
                    and command["job_id"] == active["command"]["job_id"]
                    and command["request_sha256"] == active["command"]["request_sha256"]
                ):
                    active["cancel"].set()
                    print(f"[PDU][RECEIVE] CANCEL job_id={command['job_id']}")
                    statuses = []
                else:
                    statuses = [_status(
                        command, "FAILED", command["sequence"] + 1,
                        error={
                            "phase": "command", "code": "CANCEL_REJECTED",
                            "message": "the requested City World job is not running",
                        },
                    )]
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
            if once and active is None:
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
    parser.add_argument(
        "--parallel-workers", type=int, default=4,
        help="worker limit for Envsim source and component generation (1-16; default: 4)",
    )
    parser.add_argument(
        "--dem-parallel-workers", type=int, default=2,
        help="DEM source extraction process limit (1-4; default: 2)",
    )
    parser.add_argument(
        "--terrain-spacing-m", choices=("2", "5", "10", "auto"), default="2",
        help="terrain grid spacing or automatic sample-budget selection (default: 2)",
    )
    parser.add_argument("--ready-file", type=Path, help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.max_download_gib <= 0:
        parser.error("--max-download-gib must be greater than zero")
    if not 1 <= args.parallel_workers <= 16:
        parser.error("--parallel-workers must be in [1, 16]")
    if not 1 <= args.dem_parallel_workers <= 4:
        parser.error("--dem-parallel-workers must be in [1, 4]")
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
        parallel_workers=args.parallel_workers,
        dem_parallel_workers=args.dem_parallel_workers,
        terrain_spacing_m=args.terrain_spacing_m,
        ready_file=args.ready_file,
    )


if __name__ == "__main__":
    raise SystemExit(main())
