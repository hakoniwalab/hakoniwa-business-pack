#!/usr/bin/env python3
"""Drive the proven one-drone Recipe through the remote-operation protocol.

Both protocol peers run on localhost for this smoke.  The worker maps each
enumerated wire command to a fixed local Recipe lifecycle; no received value is
ever interpreted as a command line.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import socket
import subprocess
import sys
import time
import uuid
from pathlib import Path
from typing import Any

from tools.remote_operation import protocol
from tools.remote_operation.pdu_transport import (
    PduJsonTransport,
    TransportError,
    write_tcp_endpoint_config,
)


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_EXPERIMENT = (
    ROOT / "recipes" / "experiments" / "drone-fleet-single-host-ci.yaml"
)
DEFAULT_RUNTIME = ROOT / "work" / "remote-operation" / "single-host-recipe-smoke"
OPERATOR = ROOT / "tools" / "recipe" / "drone_fleet_single_host.py"
SERVER_HOST = "srv-01"
CLIENT_HOST = "cli-01"


class SmokeError(RuntimeError):
    pass


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _available_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


def _append_event(
    path: Path,
    direction: str,
    message: dict[str, Any],
    *,
    display_host: str | None = None,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    record = {
        "recorded_at_unix_sec": time.time(),
        "direction": direction,
        "message": message,
    }
    with path.open("a", encoding="utf-8") as output:
        output.write(json.dumps(record, ensure_ascii=False, separators=(",", ":")))
        output.write("\n")
    if display_host is not None:
        print(
            f"[PDU][{display_host}][{direction.upper()}] "
            f"seq={message['sequence']} attempt={message['attempt']} "
            f"{message['kind']}:{message['type']} "
            f"source={message['source_host']}",
            flush=True,
        )


def _identity(
    *, experiment: Path, session_id: str, source_host: str, sequence: int, kind: str,
    message_type: str, error: dict[str, str] | None = None,
) -> dict[str, Any]:
    return protocol.make_message(
        kind=kind,
        message_type=message_type,
        session_id=session_id,
        sequence=sequence,
        attempt=1,
        source_host=source_host,
        configuration_id="drone-fleet-single-host-ci",
        config_hash=_sha256(experiment),
        error=error,
    )


def _check_peer_message(
    message: dict[str, Any],
    *,
    experiment: Path,
    session_id: str,
    source_host: str,
    expected_sequence: int,
    kind: str,
) -> None:
    expected = {
        "session_id": session_id,
        "attempt": 1,
        "source_host": source_host,
        "configuration_id": "drone-fleet-single-host-ci",
        "config_hash": _sha256(experiment),
        "sequence": expected_sequence,
        "kind": kind,
    }
    for field, value in expected.items():
        if message.get(field) != value:
            raise SmokeError(
                f"received {field}={message.get(field)!r}; expected {value!r}"
            )


def _run_recipe(operation: str, experiment: Path, log_dir: Path) -> None:
    # This mapping is the security boundary: network input selects only a key
    # already validated by the protocol, and every argv is defined locally.
    operations = {
        "configure": ["configure"],
        "doctor": ["doctor"],
        "start": ["start"],
        "smoke": ["smoke", "--timeout-sec", "90"],
        "stop": ["stop"],
    }
    if operation not in operations:
        raise SmokeError(f"unsupported local Recipe operation: {operation}")
    command = [
        sys.executable,
        str(OPERATOR),
        *operations[operation],
        "--experiment",
        str(experiment),
    ]
    result = subprocess.run(
        command,
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    log_dir.mkdir(parents=True, exist_ok=True)
    (log_dir / f"{operation}.stdout.log").write_text(
        result.stdout, encoding="utf-8"
    )
    (log_dir / f"{operation}.stderr.log").write_text(
        result.stderr, encoding="utf-8"
    )
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip()
        raise SmokeError(
            f"Recipe {operation} failed with rc={result.returncode}: {detail}"
        )


def _send_status(
    transport: PduJsonTransport,
    event_log: Path,
    *,
    experiment: Path,
    session_id: str,
    sequence: int,
    message_type: str,
    error: dict[str, str] | None = None,
) -> int:
    message = _identity(
        experiment=experiment,
        session_id=session_id,
        source_host=CLIENT_HOST,
        sequence=sequence,
        kind="status",
        message_type=message_type,
        error=error,
    )
    transport.send(message)
    _append_event(event_log, "send", message)
    return sequence + 1


def worker(args: argparse.Namespace) -> int:
    experiment = args.experiment.resolve()
    event_log = args.runtime_dir / "client-events.jsonl"
    log_dir = args.runtime_dir / "recipe-logs"
    transport = PduJsonTransport(args.endpoint_config)
    status_sequence = 1
    command_sequence = 1
    previous_command: str | None = None
    previous_status: str | None = None
    cleaned = False
    try:
        transport.start()
        transport.wait_connected(args.timeout_sec)
        registered = "REGISTERED"
        protocol.validate_transition("status", previous_status, registered)
        status_sequence = _send_status(
            transport,
            event_log,
            experiment=experiment,
            session_id=args.session_id,
            sequence=status_sequence,
            message_type=registered,
        )
        previous_status = registered

        while True:
            message = transport.receive(args.timeout_sec)
            _append_event(event_log, "receive", message)
            _check_peer_message(
                message,
                experiment=experiment,
                session_id=args.session_id,
                source_host=SERVER_HOST,
                expected_sequence=command_sequence,
                kind="command",
            )
            command_sequence += 1
            message_type = str(message["type"])
            protocol.validate_transition("command", previous_command, message_type)
            previous_command = message_type

            if message_type == "PREPARE":
                protocol.validate_transition("status", previous_status, "PREPARING")
                status_sequence = _send_status(
                    transport,
                    event_log,
                    experiment=experiment,
                    session_id=args.session_id,
                    sequence=status_sequence,
                    message_type="PREPARING",
                )
                previous_status = "PREPARING"
                _run_recipe("configure", experiment, log_dir)
                _run_recipe("doctor", experiment, log_dir)
                next_status = "READY"
            elif message_type == "LAUNCH":
                _run_recipe("start", experiment, log_dir)
                next_status = "LAUNCHED"
            elif message_type == "RUN":
                protocol.validate_transition("status", previous_status, "RUNNING")
                status_sequence = _send_status(
                    transport,
                    event_log,
                    experiment=experiment,
                    session_id=args.session_id,
                    sequence=status_sequence,
                    message_type="RUNNING",
                )
                previous_status = "RUNNING"
                _run_recipe("smoke", experiment, log_dir)
                next_status = "TERMINATED"
            elif message_type in {"CLEANUP", "ABORT"}:
                _run_recipe("stop", experiment, log_dir)
                cleaned = True
                next_status = "CLEANED"
            else:  # Guarded by protocol validation; retained as fail-closed defense.
                raise SmokeError(f"unsupported wire command: {message_type}")

            protocol.validate_transition("status", previous_status, next_status)
            status_sequence = _send_status(
                transport,
                event_log,
                experiment=experiment,
                session_id=args.session_id,
                sequence=status_sequence,
                message_type=next_status,
            )
            previous_status = next_status
            if next_status == "CLEANED":
                return 0
    except Exception as exc:
        try:
            protocol.validate_transition("status", previous_status, "FAILED")
            _send_status(
                transport,
                event_log,
                experiment=experiment,
                session_id=args.session_id,
                sequence=status_sequence,
                message_type="FAILED",
                error={
                    "phase": (previous_command or "connect").lower(),
                    "code": "LOCAL_OPERATION_FAILED",
                    "message": str(exc),
                },
            )
        except Exception:
            pass
        print(f"worker error: {exc}", file=sys.stderr)
        return 1
    finally:
        if not cleaned:
            try:
                _run_recipe("stop", experiment, log_dir)
            except Exception:
                pass
        transport.close()


def _send_command(
    transport: PduJsonTransport,
    event_log: Path,
    *,
    experiment: Path,
    session_id: str,
    sequence: int,
    message_type: str,
) -> int:
    message = _identity(
        experiment=experiment,
        session_id=session_id,
        source_host=SERVER_HOST,
        sequence=sequence,
        kind="command",
        message_type=message_type,
    )
    transport.send(message)
    _append_event(event_log, "send", message, display_host=SERVER_HOST)
    return sequence + 1


def _receive_statuses(
    transport: PduJsonTransport,
    event_log: Path,
    *,
    experiment: Path,
    session_id: str,
    expected_sequence: int,
    previous_status: str | None,
    expected_types: tuple[str, ...],
    timeout_sec: float,
) -> tuple[int, str]:
    current = previous_status
    sequence = expected_sequence
    for expected_type in expected_types:
        message = transport.receive(timeout_sec)
        _append_event(event_log, "receive", message, display_host=SERVER_HOST)
        _check_peer_message(
            message,
            experiment=experiment,
            session_id=session_id,
            source_host=CLIENT_HOST,
            expected_sequence=sequence,
            kind="status",
        )
        message_type = str(message["type"])
        protocol.validate_transition("status", current, message_type)
        if message_type == "FAILED":
            raise SmokeError(f"worker reported failure: {message['error']}")
        if message_type != expected_type:
            raise SmokeError(
                f"received status {message_type}; expected {expected_type}"
            )
        current = message_type
        sequence += 1
    assert current is not None
    return sequence, current


def run(args: argparse.Namespace) -> int:
    experiment = args.experiment.resolve()
    if not experiment.is_file():
        raise SmokeError(f"experiment does not exist: {experiment}")
    session_id = "local-recipe-" + uuid.uuid4().hex[:16]
    runtime_dir = args.runtime_dir.resolve()
    runtime_dir.mkdir(parents=True, exist_ok=True)
    event_log = runtime_dir / "server-events.jsonl"
    for stale in (event_log, runtime_dir / "client-events.jsonl"):
        stale.unlink(missing_ok=True)
    port = _available_port()
    server_config = write_tcp_endpoint_config(
        runtime_dir / "server-endpoint",
        role="server",
        address="127.0.0.1",
        port=port,
    )
    client_config = write_tcp_endpoint_config(
        runtime_dir / "client-endpoint",
        role="client",
        address="127.0.0.1",
        port=port,
    )
    transport = PduJsonTransport(server_config)
    worker_command = [
        sys.executable,
        "-m",
        "tools.remote_operation.single_host_recipe_smoke",
        "worker",
        "--experiment",
        str(experiment),
        "--runtime-dir",
        str(runtime_dir),
        "--endpoint-config",
        str(client_config),
        "--session-id",
        session_id,
        "--timeout-sec",
        str(args.timeout_sec),
    ]
    child: subprocess.Popen[str] | None = None
    command_sequence = 1
    status_sequence = 1
    previous_command: str | None = None
    previous_status: str | None = None
    try:
        transport.start()
        with (
            (runtime_dir / "worker.stdout.log").open("w", encoding="utf-8") as stdout,
            (runtime_dir / "worker.stderr.log").open("w", encoding="utf-8") as stderr,
        ):
            child = subprocess.Popen(
                worker_command,
                cwd=ROOT,
                text=True,
                stdout=stdout,
                stderr=stderr,
            )
        transport.wait_connected(args.timeout_sec)
        status_sequence, previous_status = _receive_statuses(
            transport,
            event_log,
            experiment=experiment,
            session_id=session_id,
            expected_sequence=status_sequence,
            previous_status=previous_status,
            expected_types=("REGISTERED",),
            timeout_sec=args.timeout_sec,
        )

        phases = (
            ("PREPARE", ("PREPARING", "READY")),
            ("LAUNCH", ("LAUNCHED",)),
            ("RUN", ("RUNNING", "TERMINATED")),
            ("CLEANUP", ("CLEANED",)),
        )
        for command_type, statuses in phases:
            protocol.validate_transition("command", previous_command, command_type)
            command_sequence = _send_command(
                transport,
                event_log,
                experiment=experiment,
                session_id=session_id,
                sequence=command_sequence,
                message_type=command_type,
            )
            previous_command = command_type
            status_sequence, previous_status = _receive_statuses(
                transport,
                event_log,
                experiment=experiment,
                session_id=session_id,
                expected_sequence=status_sequence,
                previous_status=previous_status,
                expected_types=statuses,
                timeout_sec=args.timeout_sec,
            )

        return_code = child.wait(timeout=args.timeout_sec)
        if return_code != 0:
            raise SmokeError(f"worker exited with rc={return_code}")
        summary = (
            ROOT
            / "work"
            / "recipes"
            / "drone-fleet-single-host"
            / "validation"
            / "execution-summary.json"
        )
        payload = json.loads(summary.read_text(encoding="utf-8"))
        if payload.get("status") != "done" or payload.get("drone_count") != 1:
            raise SmokeError(f"unexpected Recipe summary: {payload}")
        evidence = {
            "status": "success",
            "session_id": session_id,
            "protocol": "hakoniwa-pdu-endpoint/tcp/json",
            "recipe": "drone-fleet-single-host-ci",
            "drone_count": 1,
            "recipe_summary": str(summary),
            "server_events": str(event_log),
            "client_events": str(runtime_dir / "client-events.jsonl"),
        }
        evidence_path = runtime_dir / "smoke-result.json"
        evidence_path.write_text(
            json.dumps(evidence, indent=2) + "\n", encoding="utf-8"
        )
        print("[OK] remote-operation controlled Recipe completed")
        print(f"Recipe summary : {summary}")
        print(f"Evidence       : {evidence_path}")
        print(f"Server events  : {event_log}")
        print(f"Client events  : {runtime_dir / 'client-events.jsonl'}")
        print(f"Recipe logs    : {runtime_dir / 'recipe-logs'}")
        print(f"Worker stdout  : {runtime_dir / 'worker.stdout.log'}")
        print(f"Worker stderr  : {runtime_dir / 'worker.stderr.log'}")
        return 0
    finally:
        transport.close()
        if child is not None and child.poll() is None:
            child.terminate()
            try:
                child.wait(timeout=5.0)
            except subprocess.TimeoutExpired:
                child.kill()
                child.wait(timeout=5.0)


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(
        description="Control the proven one-drone Recipe over local PDU/TCP"
    )
    subcommands = result.add_subparsers(dest="command", required=True)
    run_parser = subcommands.add_parser("run")
    run_parser.add_argument("--experiment", type=Path, default=DEFAULT_EXPERIMENT)
    run_parser.add_argument("--runtime-dir", type=Path, default=DEFAULT_RUNTIME)
    run_parser.add_argument("--timeout-sec", type=float, default=120.0)

    worker_parser = subcommands.add_parser("worker", help=argparse.SUPPRESS)
    worker_parser.add_argument("--experiment", type=Path, required=True)
    worker_parser.add_argument("--runtime-dir", type=Path, required=True)
    worker_parser.add_argument("--endpoint-config", type=Path, required=True)
    worker_parser.add_argument("--session-id", required=True)
    worker_parser.add_argument("--timeout-sec", type=float, required=True)
    return result


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        if args.command == "worker":
            return worker(args)
        return run(args)
    except (SmokeError, TransportError, OSError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
