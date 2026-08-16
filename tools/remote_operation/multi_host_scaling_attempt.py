#!/usr/bin/env python3
"""Run a multi-host scaling attempt batch and collect cli-01 evidence."""

from __future__ import annotations

import argparse
import json
import re
import shutil
import statistics
import subprocess
import sys
import time
import zipfile
from pathlib import Path, PurePosixPath
from typing import Any

from tools.recipe import drone_fleet_multi_host as multi_host
from tools.recipe import drone_fleet_multi_host_scaling as scaling
from tools.remote_operation import protocol
from tools.remote_operation.artifact_transfer import (
    ARTIFACT_PDU_CHANNEL_ID,
    ARTIFACT_PDU_ROBOT,
    create_zip,
    receive_file,
    send_file,
)
from tools.remote_operation.artifact_protocol import decode_message as decode_artifact
from tools.remote_operation.artifact_protocol import encode_message as encode_artifact
from tools.remote_operation.pdu_transport import PduJsonTransport, write_tcp_endpoint_config


ROOT = Path(__file__).resolve().parents[2]
OPERATOR = ROOT / "tools" / "recipe" / "drone_fleet_multi_host_scaling.py"
class AttemptError(RuntimeError):
    pass


_IDENTIFIER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


def _mapping(value: Any, label: str, allowed: set[str]) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise AttemptError(f"{label} must be a mapping")
    unknown = sorted(set(value) - allowed)
    if unknown:
        raise AttemptError(f"unknown {label} fields: {', '.join(unknown)}")
    return value


def _repo_path(value: Any, label: str) -> Path:
    if not isinstance(value, str) or not value:
        raise AttemptError(f"{label} must be a non-empty repository-relative path")
    path = Path(value)
    if path.is_absolute() or ".." in path.parts:
        raise AttemptError(f"{label} must stay inside the Business Pack repository")
    return (ROOT / path).resolve()


def load_run_profile(path: Path) -> dict[str, Any]:
    raw = scaling.yaml_support.load_simple_yaml(path)
    root = _mapping(
        raw,
        "profile",
        {"version", "operation", "selection", "workspace", "lifecycle", "transport", "timeout_sec"},
    )
    if root.get("version") != 1:
        raise AttemptError("profile.version must be 1")
    operation = _mapping(root.get("operation"), "operation", {"id", "experiment"})
    session_id = operation.get("id")
    if (
        not isinstance(session_id, str)
        or len(session_id) > 128
        or _IDENTIFIER_RE.fullmatch(session_id) is None
    ):
        raise AttemptError("operation.id must be a valid remote-operation identifier")
    experiment = _repo_path(operation.get("experiment"), "operation.experiment")
    if not experiment.is_file():
        raise AttemptError(f"operation.experiment does not exist: {experiment}")
    selection = _mapping(
        root.get("selection"),
        "selection",
        {"drone_counts", "attempt_set"},
    )
    selected_counts = selection.get("drone_counts")
    attempt_set = selection.get("attempt_set")
    if attempt_set not in {
        "baseline",
        "extension",
        "baseline_with_conditional_extension",
    }:
        raise AttemptError(
            "selection.attempt_set must be baseline, extension, or "
            "baseline_with_conditional_extension"
        )
    workspace = _mapping(root.get("workspace"), "workspace", {"output_root"})
    output_root = _repo_path(workspace.get("output_root"), "workspace.output_root")
    lifecycle = _mapping(root.get("lifecycle"), "lifecycle", {"clean_before_run"})
    clean = lifecycle.get("clean_before_run")
    if not isinstance(clean, bool):
        raise AttemptError("lifecycle.clean_before_run must be boolean")
    transport = _mapping(root.get("transport"), "transport", {"control_port", "artifact_port"})
    ports: dict[str, int] = {}
    for key in ("control_port", "artifact_port"):
        value = transport.get(key)
        if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 65535:
            raise AttemptError(f"transport.{key} must be an integer from 1 to 65535")
        ports[key] = value
    if ports["control_port"] == ports["artifact_port"]:
        raise AttemptError("control and artifact ports must differ")
    timeout = root.get("timeout_sec")
    if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or timeout <= 0:
        raise AttemptError("timeout_sec must be a positive number")

    experiment_raw, counts, _attempts = scaling.load_scaling(experiment)
    if selected_counts == "all":
        drone_counts = list(counts)
    elif (
        isinstance(selected_counts, list)
        and selected_counts
        and all(
            isinstance(value, int) and not isinstance(value, bool) and value > 0
            for value in selected_counts
        )
        and selected_counts == sorted(set(selected_counts))
    ):
        drone_counts = list(selected_counts)
    else:
        raise AttemptError(
            "selection.drone_counts must be all or a unique ascending positive "
            "integer list"
        )
    unknown_counts = [value for value in drone_counts if value not in counts]
    if unknown_counts:
        raise AttemptError(
            "selection.drone_counts are outside the Experiment matrix: "
            + ", ".join(map(str, unknown_counts))
        )
    policy = scaling.attempt_policy(experiment_raw["matrix"])
    declared_attempts = policy["baseline"] + policy["extension"]
    highest_artifact_port = (
        ports["artifact_port"]
        + (len(drone_counts) - 1) * len(declared_attempts)
        + max(declared_attempts)
        - 1
    )
    if highest_artifact_port > 65_535:
        raise AttemptError(
            "artifact port range exceeds 65535 for the selected matrix"
        )
    longest_session = (
        f"{session_id}-uav-{max(drone_counts):03d}-attempt-"
        f"{max(declared_attempts):02d}"
    )
    if len(longest_session) > 128:
        raise AttemptError("operation.id is too long for matrix session identities")
    deployment = experiment_raw.get("deployment", {})
    server_host = deployment.get("server_host")
    hosts = deployment.get("hosts", {})
    server = hosts.get(server_host) if isinstance(hosts, dict) else None
    server_address = server.get("address") if isinstance(server, dict) else None
    if not isinstance(server_address, str) or not server_address:
        raise AttemptError("Experiment server host must declare a reachable address")
    return {
        "session_id": session_id,
        "experiment": experiment,
        "drone_counts": drone_counts,
        "attempt_set": attempt_set,
        "output_root": output_root,
        "clean": clean,
        "control_port": ports["control_port"],
        "artifact_port": ports["artifact_port"],
        "timeout_sec": float(timeout),
        "server_address": server_address,
    }


def resolve_arguments(args: argparse.Namespace) -> argparse.Namespace:
    if args.profile is not None:
        override_fields = (
            "experiment",
            "output_root",
            "runtime_dir",
            "drone_count",
            "session_id",
            "clean",
            "timeout_sec",
            "listen_address",
            "server_address",
            "control_port",
            "artifact_port",
        )
        supplied = [
            field
            for field in override_fields
            if getattr(args, field, None) is not None
        ]
        if supplied:
            raise AttemptError(
                "--profile cannot be combined with CLI overrides: "
                + ", ".join(supplied)
            )
        profile = load_run_profile(args.profile.resolve())
        args.session_id = profile["session_id"]
        args.experiment = profile["experiment"]
        args.drone_counts = profile["drone_counts"]
        args.drone_count = args.drone_counts[0]
        args.attempt_set = profile["attempt_set"]
        args.output_root = profile["output_root"]
        args.clean = profile["clean"]
        args.timeout_sec = profile["timeout_sec"]
        args.control_port = profile["control_port"]
        args.artifact_port = profile["artifact_port"]
        if args.role == "server":
            args.listen_address = profile["server_address"]
        else:
            args.server_address = profile["server_address"]
    else:
        if args.session_id is None:
            raise AttemptError("--session-id is required when --profile is not used")
        args.experiment = (args.experiment or scaling.DEFAULT_EXPERIMENT).resolve()
        args.output_root = (args.output_root or scaling.WORK_ROOT).resolve()
        args.drone_count = args.drone_count or 256
        args.drone_counts = [args.drone_count]
        args.attempt_set = "baseline"
        args.clean = bool(args.clean)
        args.timeout_sec = args.timeout_sec or 600.0
        args.control_port = args.control_port or 54200
        args.artifact_port = args.artifact_port or 54201
        if args.role == "server":
            args.listen_address = args.listen_address or "192.168.2.100"
        else:
            args.server_address = args.server_address or "192.168.2.100"
    args.runtime_dir = (
        args.runtime_dir.resolve()
        if args.runtime_dir is not None
        else args.output_root / "runtime" / "remote-operation"
    )
    return args


def _run(args: argparse.Namespace, host: str, operation: str, extra: list[str] | None = None) -> None:
    command = [sys.executable, str(OPERATOR), "--experiment", str(args.experiment)]
    if args.output_root is not None:
        command.extend(["--output-root", str(args.output_root)])
    command.append(operation)
    if extra:
        command.extend(extra)
    log_dir = _attempt_runtime_dir(args, host) / "operations"
    log_dir.mkdir(parents=True, exist_ok=True)
    result = subprocess.run(command, cwd=ROOT, text=True, capture_output=True, check=False)
    (log_dir / f"{operation}.stdout.log").write_text(result.stdout, encoding="utf-8")
    (log_dir / f"{operation}.stderr.log").write_text(result.stderr, encoding="utf-8")
    print(f"[{host}] {operation}: rc={result.returncode}", flush=True)
    if result.returncode:
        raise AttemptError(result.stderr.strip() or result.stdout.strip() or f"{operation} failed")


def _prepare(args: argparse.Namespace, host: str, attempt: int) -> dict[str, Any]:
    args.current_attempt = attempt
    if args.clean and attempt == 1:
        selection = multi_host.LOCAL_SELECTION
        if selection.is_file() and (args.output_root / "bundle-index.json").is_file():
            _run(args, host, "clean")
    _run(args, host, "configure", ["--host", host, "--drone-count", str(args.drone_count), "--attempt", str(attempt)])
    state = multi_host.load_local_selection(args.output_root)
    if state["selection"]["host_id"] != host:
        raise AttemptError("configured local host identity does not match")
    if args.clean:
        # The pre-configure clean handles the previously selected condition;
        # this clean also removes a stale copy of the newly selected condition.
        _run(args, host, "clean")
    trial = multi_host.measurement_trial_path(state["resolved"], args.output_root, host)
    if (trial / "result.json").exists():
        raise AttemptError(f"result already exists; rerun with --clean: {trial}")
    args.prepared_host = host
    return state


def _clean_deferred_attempts(
    args: argparse.Namespace,
    host: str,
    attempt_numbers: list[int],
) -> None:
    if not args.clean:
        return
    for attempt in attempt_numbers:
        args.current_attempt = attempt
        _run(
            args,
            host,
            "configure",
            [
                "--host",
                host,
                "--drone-count",
                str(args.drone_count),
                "--attempt",
                str(attempt),
            ],
        )
        _run(args, host, "clean")


def _session(
    args: argparse.Namespace,
    drone_count: int,
    attempt: int,
    total: int,
) -> str:
    condition = f"{args.session_id}-uav-{drone_count:03d}"
    return condition if total == 1 else f"{condition}-attempt-{attempt:02d}"


def _artifact_port(
    args: argparse.Namespace,
    condition_index: int,
    attempt: int,
    attempts_per_condition: int,
) -> int:
    return (
        args.artifact_port
        + condition_index * attempts_per_condition
        + attempt
        - 1
    )


def _attempt_runtime_dir(args: argparse.Namespace, host: str) -> Path:
    return (
        args.runtime_dir
        / host
        / f"uav-{args.drone_count:03d}"
        / f"attempt-{args.current_attempt:02d}"
    )


def _attach_operation_evidence(args: argparse.Namespace, trial: Path, host: str) -> None:
    target = trial / "evidence" / "remote-operation"
    target.mkdir(parents=True, exist_ok=True)
    source = _attempt_runtime_dir(args, host)
    operations = source / "operations"
    if operations.is_dir():
        shutil.copytree(operations, target / "operations", dirs_exist_ok=True)
    control = source / "control-events.jsonl"
    if control.is_file():
        shutil.copy2(control, target / "control-events.jsonl")


def _identity(state: dict[str, Any]) -> tuple[str, str, int]:
    measurement = state["resolved"]["measurement"]
    return (
        str(measurement["configuration_id"]),
        str(state["index"]["config_hash"]),
        int(measurement["attempt"]),
    )


def _message(state: dict[str, Any], session: str, source: str, sequence: int, kind: str, value: str, error: dict[str, str] | None = None) -> dict[str, Any]:
    config_id, config_hash, attempt = _identity(state)
    return protocol.make_message(kind=kind, message_type=value, session_id=session,
        sequence=sequence, attempt=attempt, source_host=source,
        configuration_id=config_id, config_hash=config_hash, error=error)


def _check(message: dict[str, Any], state: dict[str, Any], session: str, source: str, sequence: int, kind: str) -> None:
    config_id, config_hash, attempt = _identity(state)
    expected = {"session_id": session, "source_host": source, "sequence": sequence,
        "kind": kind, "configuration_id": config_id, "config_hash": config_hash,
        "attempt": attempt}
    for key, value in expected.items():
        if message.get(key) != value:
            raise AttemptError(f"peer {key}={message.get(key)!r}; expected {value!r}")


def _event(path: Path, direction: str, message: dict[str, Any], host: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps({"recorded_at_unix_sec": time.time(), "direction": direction, "message": message}, separators=(",", ":")) + "\n")
    print(f"[PDU][{host}][{direction.upper()}] {message['type']}", flush=True)


def _send(transport: PduJsonTransport, log: Path, message: dict[str, Any], host: str) -> None:
    transport.send(message); _event(log, "send", message, host)


def _receive(transport: PduJsonTransport, log: Path, state: dict[str, Any], session: str,
             source: str, sequence: int, kind: str, timeout: float, host: str) -> dict[str, Any]:
    message = transport.receive(timeout); _event(log, "receive", message, host)
    _check(message, state, session, source, sequence, kind)
    if message["type"] == "FAILED":
        raise AttemptError(f"peer failed: {message['error']}")
    return message


def _wait_result(state: dict[str, Any], output_root: Path, host: str, timeout: float) -> Path:
    trial = multi_host.measurement_trial_path(state["resolved"], output_root, host)
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        result = trial / "result.json"
        if result.is_file():
            payload = json.loads(result.read_text(encoding="utf-8"))
            if payload.get("status") in {"success", "failed", "invalid"}:
                return trial
        time.sleep(0.2)
    raise AttemptError(f"measurement result timeout: {trial}")


def _attempt_sets(args: argparse.Namespace) -> tuple[dict[str, Any], list[int], list[int]]:
    raw, _counts, _baseline_count = scaling.load_scaling(args.experiment)
    policy = scaling.attempt_policy(raw["matrix"])
    baseline = list(policy["baseline"])
    extension = list(policy["extension"])
    if args.attempt_set != "baseline" and not extension:
        raise AttemptError("selected attempt_set requires an Experiment extension policy")
    return policy, baseline, extension


def extension_decision(
    args: argparse.Namespace,
    policy: dict[str, Any],
    baseline: list[int],
) -> dict[str, Any]:
    raw, _counts, _attempts = scaling.load_scaling(args.experiment)
    sleep = int(raw["runtime"]["conductor"]["real_sleep_msec"])
    mode = str(raw["measurement"]["mode"])
    series = str(raw["measurement"]["series"])
    results_directory = str(raw["results"]["directory"])
    config_id = scaling.configuration_id(args.drone_count, sleep, mode)
    failures: list[dict[str, Any]] = []
    rtf_values: list[float] = []
    for attempt_number in baseline:
        attempt_hashes: set[str] = set()
        for host_id in ("srv-01", "cli-01"):
            path = scaling.result_path(
                args.output_root,
                results_directory,
                series,
                host_id,
                config_id,
                attempt_number,
            )
            if not path.is_file():
                raise AttemptError(f"baseline result is missing: {path}")
            payload = json.loads(path.read_text(encoding="utf-8"))
            scaling.validate_result_identity(
                payload,
                path=path,
                host_id=host_id,
                configuration_id=config_id,
                attempt=attempt_number,
                real_sleep_msec=sleep,
                measurement_mode=mode,
                temporal_sampling_interval_usec=None,
            )
            if payload.get("status") != "success":
                failures.append(
                    {
                        "attempt": attempt_number,
                        "host_id": host_id,
                        "status": payload.get("status"),
                    }
                )
            config_hash = payload.get("metadata", {}).get("config_hash")
            if isinstance(config_hash, str):
                attempt_hashes.add(config_hash)
            if host_id == str(raw["deployment"]["server_host"]):
                rtf = scaling._number(payload.get("performance"), "rtf")
                if rtf is not None:
                    rtf_values.append(rtf)
        if len(attempt_hashes) != 1:
            raise AttemptError(
                f"baseline config hash mismatch for attempt {attempt_number}: "
                f"{attempt_hashes}"
            )
    if not failures and len(rtf_values) != len(baseline):
        raise AttemptError("baseline server RTF values are incomplete")
    median = statistics.median(rtf_values) if rtf_values else None
    spread = (
        (max(rtf_values) - min(rtf_values)) / median
        if median is not None and median > 0 and len(rtf_values) == len(baseline)
        else None
    )
    triggers = policy["triggers"]
    threshold = float(triggers["relative_spread"]["greater_than"])
    failure_triggered = bool(failures) and bool(triggers["any_failure"])
    spread_triggered = spread is not None and spread > threshold
    return {
        "required": failure_triggered or spread_triggered,
        "failure_triggered": failure_triggered,
        "spread_triggered": spread_triggered,
        "failures": failures,
        "rtf_values": rtf_values,
        "relative_spread": spread,
        "relative_spread_threshold": threshold,
    }


def _artifact_transport(config: Path) -> PduJsonTransport:
    return PduJsonTransport(config, encoder=encode_artifact, decoder=decode_artifact,
        pdu_robot=ARTIFACT_PDU_ROBOT, pdu_channel_id=ARTIFACT_PDU_CHANNEL_ID)


def _extract_client_archive(archive: Path, staging: Path, destination: Path,
                            state: dict[str, Any]) -> None:
    if staging.exists(): shutil.rmtree(staging)
    staging.mkdir(parents=True)
    with zipfile.ZipFile(archive) as source:
        for info in source.infolist():
            member = PurePosixPath(info.filename)
            if member.is_absolute() or ".." in member.parts or "\\" in info.filename:
                raise AttemptError(f"unsafe ZIP member: {info.filename}")
            if (info.external_attr >> 16) & 0o170000 == 0o120000:
                raise AttemptError(f"symlink ZIP member: {info.filename}")
        source.extractall(staging)
    roots = [path for path in staging.iterdir()]
    if len(roots) != 1 or not roots[0].is_dir():
        raise AttemptError("client archive must contain exactly one attempt directory")
    result = json.loads((roots[0] / "result.json").read_text(encoding="utf-8"))
    config_id, config_hash, attempt = _identity(state)
    metadata = result.get("metadata", {})
    expected = {"host_id": "cli-01", "configuration_id": config_id,
                "config_hash": config_hash, "attempt": attempt}
    for key, value in expected.items():
        if metadata.get(key) != value:
            raise AttemptError(f"received result {key} mismatch")
    if destination.exists():
        raise AttemptError(f"client destination already exists: {destination}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    roots[0].replace(destination)
    shutil.rmtree(staging)


def client(args: argparse.Namespace) -> int:
    _policy, baseline, extension = _attempt_sets(args)
    if args.attempt_set == "baseline":
        planned = baseline
    elif args.attempt_set == "extension":
        planned = extension
    else:
        planned = baseline + extension
    session_total = len(baseline + extension)
    config = write_tcp_endpoint_config(args.runtime_dir / "cli-01" / "control-endpoint",
        role="client", address=args.server_address, port=args.control_port)
    transport = PduJsonTransport(config); transport.start(); transport.wait_connected(args.timeout_sec)
    try:
        for condition_index, drone_count in enumerate(args.drone_counts):
            args.drone_count = drone_count
            if args.attempt_set == "baseline_with_conditional_extension":
                _clean_deferred_attempts(args, "cli-01", extension)
            index = 0
            while True:
                attempt = planned[index]
                state = _prepare(args, "cli-01", attempt)
                session = _session(args, drone_count, attempt, session_total)
                attempt_dir = _attempt_runtime_dir(args, "cli-01")
                log = attempt_dir / "control-events.jsonl"
                log.unlink(missing_ok=True)
                status_seq = 1
                command_seq = 1
                _send(transport, log, _message(state, session, "cli-01", status_seq, "status", "REGISTERED"), "cli-01")
                status_seq += 1
                while True:
                    message = _receive(transport, log, state, session, "srv-01", command_seq, "command", args.timeout_sec, "cli-01")
                    command_seq += 1
                    command = message["type"]
                    if command == "PREPARE":
                        _send(transport, log, _message(state, session, "cli-01", status_seq, "status", "PREPARING"), "cli-01")
                        status_seq += 1
                        _run(args, "cli-01", "doctor")
                        statuses = ["READY"]
                    elif command == "LAUNCH":
                        _run(args, "cli-01", "start")
                        statuses = ["LAUNCHED"]
                    elif command == "RUN":
                        _send(transport, log, _message(state, session, "cli-01", status_seq, "status", "RUNNING"), "cli-01")
                        status_seq += 1
                        _wait_result(state, args.output_root, "cli-01", args.timeout_sec)
                        statuses = ["TERMINATED"]
                    elif command in {"CLEANUP", "ABORT"}:
                        _run(args, "cli-01", "stop")
                        statuses = ["CLEANED"]
                    elif command == "COLLECT":
                        _send(transport, log, _message(state, session, "cli-01", status_seq, "status", "COLLECTING"), "cli-01")
                        status_seq += 1
                        _run(args, "cli-01", "collect")
                        trial = multi_host.measurement_trial_path(state["resolved"], args.output_root, "cli-01")
                        _attach_operation_evidence(args, trial, "cli-01")
                        archive = create_zip([trial], attempt_dir / f"{session}-cli-01.zip")
                        artifact_port = _artifact_port(args, condition_index, attempt, session_total)
                        acfg = write_tcp_endpoint_config(attempt_dir / "artifact-endpoint", role="client", address=args.server_address, port=artifact_port)
                        at = _artifact_transport(acfg)
                        at.start()
                        at.wait_connected(args.timeout_sec)
                        try:
                            send_file(at, archive, session_id=session, timeout_sec=args.timeout_sec, chunk_size=32 * 1024, event_log=attempt_dir / "artifact-events.jsonl")
                        finally:
                            at.close()
                        statuses = ["COLLECTED"]
                    else:
                        raise AttemptError(f"unsupported command: {command}")
                    for value in statuses:
                        _send(transport, log, _message(state, session, "cli-01", status_seq, "status", value), "cli-01")
                        status_seq += 1
                    if command == "COLLECT":
                        break
                decision = _receive(transport, log, state, session, "srv-01", command_seq, "command", args.timeout_sec, "cli-01")
                if decision["type"] == "BATCH_COMPLETE":
                    _send(transport, log, _message(state, session, "cli-01", status_seq, "status", "BATCH_COMPLETED"), "cli-01")
                    return 0
                if decision["type"] == "NEXT_CONDITION":
                    if condition_index + 1 >= len(args.drone_counts):
                        raise AttemptError("server requested an undeclared next condition")
                    break
                if decision["type"] != "NEXT_ATTEMPT":
                    raise AttemptError(
                        "expected NEXT_ATTEMPT, NEXT_CONDITION, or BATCH_COMPLETE; "
                        f"received {decision['type']}"
                    )
                index += 1
                if index >= len(planned):
                    raise AttemptError("server requested an undeclared next attempt")
        raise AttemptError("server did not complete the selected condition matrix")
    finally:
        transport.close()


def server(args: argparse.Namespace) -> int:
    policy, baseline, extension = _attempt_sets(args)
    if args.attempt_set == "baseline":
        planned = baseline
    elif args.attempt_set == "extension":
        planned = extension
    else:
        planned = baseline + extension
    session_total = len(baseline + extension)
    cfg = write_tcp_endpoint_config(
        args.runtime_dir / "srv-01" / "control-endpoint",
        role="server",
        address=args.listen_address,
        port=args.control_port,
    )
    transport = PduJsonTransport(cfg)
    transport.start()
    print(f"Waiting for cli-01 on {args.listen_address}:{args.control_port}")
    transport.wait_connected(args.timeout_sec)
    try:
        completed_by_count: dict[int, list[int]] = {}
        decisions_by_count: dict[int, dict[str, Any]] = {}
        for condition_index, drone_count in enumerate(args.drone_counts):
            args.drone_count = drone_count
            if args.attempt_set == "baseline_with_conditional_extension":
                _clean_deferred_attempts(args, "srv-01", extension)
            completed: list[int] = []
            index = 0
            while True:
                attempt = planned[index]
                state = _prepare(args, "srv-01", attempt)
                session = _session(args, drone_count, attempt, session_total)
                attempt_dir = _attempt_runtime_dir(args, "srv-01")
                log = attempt_dir / "control-events.jsonl"
                log.unlink(missing_ok=True)
                status_seq = 1
                command_seq = 1
                registered = _receive(transport, log, state, session, "cli-01", status_seq, "status", args.timeout_sec, "srv-01")
                status_seq += 1
                if registered["type"] != "REGISTERED":
                    raise AttemptError("expected REGISTERED")

                def command(value: str, expected: list[str]) -> None:
                    nonlocal command_seq, status_seq
                    _send(transport, log, _message(state, session, "srv-01", command_seq, "command", value), "srv-01")
                    command_seq += 1
                    for wanted in expected:
                        got = _receive(transport, log, state, session, "cli-01", status_seq, "status", args.timeout_sec, "srv-01")
                        status_seq += 1
                        if got["type"] != wanted:
                            raise AttemptError(f"expected {wanted}, received {got['type']}")

                _run(args, "srv-01", "doctor")
                command("PREPARE", ["PREPARING", "READY"])
                _run(args, "srv-01", "start")
                command("LAUNCH", ["LAUNCHED"])
                _send(transport, log, _message(state, session, "srv-01", command_seq, "command", "RUN"), "srv-01")
                command_seq += 1
                running = _receive(transport, log, state, session, "cli-01", status_seq, "status", args.timeout_sec, "srv-01")
                status_seq += 1
                if running["type"] != "RUNNING":
                    raise AttemptError("expected RUNNING")
                _run(args, "srv-01", "run")
                _wait_result(state, args.output_root, "srv-01", args.timeout_sec)
                terminated = _receive(transport, log, state, session, "cli-01", status_seq, "status", args.timeout_sec, "srv-01")
                status_seq += 1
                if terminated["type"] != "TERMINATED":
                    raise AttemptError("expected TERMINATED")
                _run(args, "srv-01", "stop")
                command("CLEANUP", ["CLEANED"])
                _run(args, "srv-01", "collect")
                trial = multi_host.measurement_trial_path(state["resolved"], args.output_root, "srv-01")
                _attach_operation_evidence(args, trial, "srv-01")

                artifact_port = _artifact_port(args, condition_index, attempt, session_total)
                acfg = write_tcp_endpoint_config(attempt_dir / "artifact-endpoint", role="server", address=args.listen_address, port=artifact_port)
                at = _artifact_transport(acfg)
                at.start()
                _send(transport, log, _message(state, session, "srv-01", command_seq, "command", "COLLECT"), "srv-01")
                command_seq += 1
                collecting = _receive(transport, log, state, session, "cli-01", status_seq, "status", args.timeout_sec, "srv-01")
                status_seq += 1
                if collecting["type"] != "COLLECTING":
                    raise AttemptError("expected COLLECTING")
                at.wait_connected(args.timeout_sec)
                incoming = attempt_dir / "incoming"
                incoming.mkdir(parents=True, exist_ok=True)
                try:
                    received = receive_file(at, incoming, session_id=session, timeout_sec=args.timeout_sec, max_bytes=1024**3, event_log=attempt_dir / "artifact-events.jsonl")
                finally:
                    at.close()
                collected = _receive(transport, log, state, session, "cli-01", status_seq, "status", args.timeout_sec, "srv-01")
                if collected["type"] != "COLLECTED":
                    raise AttemptError("expected COLLECTED")
                destination = multi_host.measurement_trial_path(state["resolved"], args.output_root, "cli-01")
                _extract_client_archive(Path(received["artifact"]), attempt_dir / "staging", destination, state)
                Path(received["artifact"]).unlink()
                completed.append(attempt)

                run_next = index + 1 < len(planned)
                if args.attempt_set == "baseline_with_conditional_extension" and attempt == baseline[-1]:
                    decision = extension_decision(args, policy, baseline)
                    decision_path = args.runtime_dir / "srv-01" / f"uav-{drone_count:03d}" / "extension-decision.json"
                    multi_host.atomic_json(decision_path, decision)
                    decisions_by_count[drone_count] = decision
                    run_next = bool(decision["required"])
                    spread = decision["relative_spread"]
                    spread_text = f"{spread:.6f}" if isinstance(spread, (int, float)) else "unavailable"
                    print(
                        f"[EXTEND][uav-{drone_count:03d}] required={run_next} "
                        f"spread={spread_text} threshold={decision['relative_spread_threshold']:.6f} "
                        f"failure={decision['failure_triggered']}",
                        flush=True,
                    )
                if run_next:
                    decision_type = "NEXT_ATTEMPT"
                elif condition_index + 1 < len(args.drone_counts):
                    decision_type = "NEXT_CONDITION"
                else:
                    decision_type = "BATCH_COMPLETE"
                _send(transport, log, _message(state, session, "srv-01", command_seq, "command", decision_type), "srv-01")
                if decision_type == "BATCH_COMPLETE":
                    completed_status = _receive(transport, log, state, session, "cli-01", status_seq + 1, "status", args.timeout_sec, "srv-01")
                    if completed_status["type"] != "BATCH_COMPLETED":
                        raise AttemptError("expected BATCH_COMPLETED")
                if not run_next:
                    break
                index += 1
                if index >= len(planned):
                    raise AttemptError("attempt plan ended before condition completion")

            completed_by_count[drone_count] = completed
            decision_path = args.runtime_dir / "srv-01" / f"uav-{drone_count:03d}" / "extension-decision.json"
            if decision_path.is_file():
                decisions_by_count[drone_count] = json.loads(decision_path.read_text(encoding="utf-8"))
            rc=scaling.summarize(args.experiment,args.output_root,drone_count,attempt_numbers=completed)
            if rc: raise AttemptError(f"paired summary is incomplete for {drone_count} UAV")
            print(f"[OK] {drone_count} UAV attempts {','.join(map(str, completed))} summarized", flush=True)

        scaling.summarize_matrix(
            args.experiment,
            args.output_root,
            completed_by_count,
            decisions_by_count,
        )
        print("[OK] selected multi-host condition matrix completed", flush=True)
        return 0
    finally:
        transport.close()


def parser() -> argparse.ArgumentParser:
    p=argparse.ArgumentParser(description=__doc__); p.add_argument("--profile",type=Path)
    p.add_argument("--experiment",type=Path); p.add_argument("--output-root",type=Path); p.add_argument("--runtime-dir",type=Path)
    p.add_argument("--drone-count",type=int); p.add_argument("--session-id"); p.add_argument("--clean",action="store_true",default=None)
    p.add_argument("--timeout-sec",type=float); sub=p.add_subparsers(dest="role",required=True)
    s=sub.add_parser("server"); s.add_argument("--listen-address"); s.add_argument("--control-port",type=int); s.add_argument("--artifact-port",type=int)
    c=sub.add_parser("client"); c.add_argument("--server-address"); c.add_argument("--control-port",type=int); c.add_argument("--artifact-port",type=int)
    return p


def main(argv: list[str] | None=None) -> int:
    try:
        args=resolve_arguments(parser().parse_args(argv))
        return server(args) if args.role=="server" else client(args)
    except Exception as exc:
        print(f"[ERROR] {exc}",file=sys.stderr)
        if "args" in locals() and getattr(args, "prepared_host", None) is not None:
            try:
                _run(args, args.prepared_host, "stop")
            except Exception as cleanup_exc:
                print(f"[WARN] best-effort stop failed: {cleanup_exc}", file=sys.stderr)
        return 1


if __name__ == "__main__": raise SystemExit(main())
