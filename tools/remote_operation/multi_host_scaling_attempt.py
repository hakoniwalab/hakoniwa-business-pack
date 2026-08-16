#!/usr/bin/env python3
"""Run one multi-host scaling attempt and collect cli-01 evidence on srv-01."""

from __future__ import annotations

import argparse
import json
import shutil
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
DEFAULT_RUNTIME = (
    ROOT / "work" / "recipes" / multi_host.RECIPE_ID / "runtime" / "remote-operation"
)


class AttemptError(RuntimeError):
    pass


def _run(args: argparse.Namespace, host: str, operation: str, extra: list[str] | None = None) -> None:
    command = [sys.executable, str(OPERATOR), "--experiment", str(args.experiment)]
    if args.output_root is not None:
        command.extend(["--output-root", str(args.output_root)])
    command.append(operation)
    if extra:
        command.extend(extra)
    log_dir = args.runtime_dir / host / "operations"
    log_dir.mkdir(parents=True, exist_ok=True)
    result = subprocess.run(command, cwd=ROOT, text=True, capture_output=True, check=False)
    (log_dir / f"{operation}.stdout.log").write_text(result.stdout, encoding="utf-8")
    (log_dir / f"{operation}.stderr.log").write_text(result.stderr, encoding="utf-8")
    print(f"[{host}] {operation}: rc={result.returncode}", flush=True)
    if result.returncode:
        raise AttemptError(result.stderr.strip() or result.stdout.strip() or f"{operation} failed")


def _prepare(args: argparse.Namespace, host: str) -> dict[str, Any]:
    if args.clean:
        selection = multi_host.LOCAL_SELECTION
        if selection.is_file() and (args.output_root / "bundle-index.json").is_file():
            _run(args, host, "clean")
    _run(args, host, "configure", ["--host", host, "--drone-count", str(args.drone_count)])
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
            if payload.get("status") == "success":
                return trial
            if payload.get("status") in {"failed", "invalid"}:
                raise AttemptError(f"measurement failed: {payload}")
        time.sleep(0.2)
    raise AttemptError(f"measurement result timeout: {trial}")


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
    state = _prepare(args, "cli-01")
    log = args.runtime_dir / "cli-01" / "control-events.jsonl"; log.unlink(missing_ok=True)
    config = write_tcp_endpoint_config(args.runtime_dir / "cli-01" / "control-endpoint",
        role="client", address=args.server_address, port=args.control_port)
    transport = PduJsonTransport(config); transport.start(); transport.wait_connected(args.timeout_sec)
    status_seq = 1; command_seq = 1
    _send(transport, log, _message(state, args.session_id, "cli-01", status_seq, "status", "REGISTERED"), "cli-01"); status_seq += 1
    try:
        while True:
            message = _receive(transport, log, state, args.session_id, "srv-01", command_seq, "command", args.timeout_sec, "cli-01"); command_seq += 1
            command = message["type"]
            if command == "PREPARE":
                _send(transport, log, _message(state,args.session_id,"cli-01",status_seq,"status","PREPARING"),"cli-01"); status_seq += 1
                _run(args,"cli-01","doctor"); statuses=["READY"]
            elif command == "LAUNCH": _run(args,"cli-01","start"); statuses=["LAUNCHED"]
            elif command == "RUN":
                _send(transport,log,_message(state,args.session_id,"cli-01",status_seq,"status","RUNNING"),"cli-01"); status_seq += 1
                _wait_result(state,args.output_root,"cli-01",args.timeout_sec); statuses=["TERMINATED"]
            elif command in {"CLEANUP","ABORT"}: _run(args,"cli-01","stop"); statuses=["CLEANED"]
            elif command == "COLLECT":
                _send(transport,log,_message(state,args.session_id,"cli-01",status_seq,"status","COLLECTING"),"cli-01"); status_seq += 1
                _run(args,"cli-01","collect")
                trial=multi_host.measurement_trial_path(state["resolved"],args.output_root,"cli-01")
                archive=create_zip([trial],args.runtime_dir/"cli-01"/f"{args.session_id}-cli-01.zip")
                acfg=write_tcp_endpoint_config(args.runtime_dir/"cli-01"/"artifact-endpoint",role="client",address=args.server_address,port=args.artifact_port)
                at=_artifact_transport(acfg); at.start(); at.wait_connected(args.timeout_sec)
                try: send_file(at,archive,session_id=args.session_id,timeout_sec=args.timeout_sec,chunk_size=32*1024,event_log=args.runtime_dir/"cli-01"/"artifact-events.jsonl")
                finally: at.close()
                statuses=["COLLECTED"]
            else: raise AttemptError(f"unsupported command: {command}")
            for value in statuses:
                _send(transport,log,_message(state,args.session_id,"cli-01",status_seq,"status",value),"cli-01"); status_seq += 1
            if command == "COLLECT": return 0
    finally: transport.close()


def server(args: argparse.Namespace) -> int:
    state = _prepare(args,"srv-01")
    log=args.runtime_dir/"srv-01"/"control-events.jsonl"; log.unlink(missing_ok=True)
    cfg=write_tcp_endpoint_config(args.runtime_dir/"srv-01"/"control-endpoint",role="server",address=args.listen_address,port=args.control_port)
    transport=PduJsonTransport(cfg); transport.start(); print(f"Waiting for cli-01 on {args.listen_address}:{args.control_port}")
    transport.wait_connected(args.timeout_sec); status_seq=1; command_seq=1
    registered=_receive(transport,log,state,args.session_id,"cli-01",status_seq,"status",args.timeout_sec,"srv-01"); status_seq+=1
    if registered["type"]!="REGISTERED": raise AttemptError("expected REGISTERED")
    try:
        def command(value: str, expected: list[str]) -> None:
            nonlocal command_seq,status_seq
            _send(transport,log,_message(state,args.session_id,"srv-01",command_seq,"command",value),"srv-01"); command_seq+=1
            for wanted in expected:
                got=_receive(transport,log,state,args.session_id,"cli-01",status_seq,"status",args.timeout_sec,"srv-01"); status_seq+=1
                if got["type"]!=wanted: raise AttemptError(f"expected {wanted}, received {got['type']}")
        _run(args,"srv-01","doctor"); command("PREPARE",["PREPARING","READY"])
        _run(args,"srv-01","start"); command("LAUNCH",["LAUNCHED"])
        _send(transport,log,_message(state,args.session_id,"srv-01",command_seq,"command","RUN"),"srv-01"); command_seq+=1
        running=_receive(transport,log,state,args.session_id,"cli-01",status_seq,"status",args.timeout_sec,"srv-01"); status_seq+=1
        if running["type"]!="RUNNING": raise AttemptError("expected RUNNING")
        _run(args,"srv-01","run"); _wait_result(state,args.output_root,"srv-01",args.timeout_sec)
        terminated=_receive(transport,log,state,args.session_id,"cli-01",status_seq,"status",args.timeout_sec,"srv-01"); status_seq+=1
        if terminated["type"]!="TERMINATED": raise AttemptError("expected TERMINATED")
        _run(args,"srv-01","stop"); command("CLEANUP",["CLEANED"]); _run(args,"srv-01","collect")
        acfg=write_tcp_endpoint_config(args.runtime_dir/"srv-01"/"artifact-endpoint",role="server",address=args.listen_address,port=args.artifact_port)
        at=_artifact_transport(acfg); at.start()
        _send(transport,log,_message(state,args.session_id,"srv-01",command_seq,"command","COLLECT"),"srv-01"); command_seq+=1
        collecting=_receive(transport,log,state,args.session_id,"cli-01",status_seq,"status",args.timeout_sec,"srv-01"); status_seq+=1
        if collecting["type"]!="COLLECTING": raise AttemptError("expected COLLECTING")
        at.wait_connected(args.timeout_sec)
        incoming=args.runtime_dir/"srv-01"/"incoming"; incoming.mkdir(parents=True,exist_ok=True)
        try: received=receive_file(at,incoming,session_id=args.session_id,timeout_sec=args.timeout_sec,max_bytes=1024**3,event_log=args.runtime_dir/"srv-01"/"artifact-events.jsonl")
        finally: at.close()
        collected=_receive(transport,log,state,args.session_id,"cli-01",status_seq,"status",args.timeout_sec,"srv-01")
        if collected["type"]!="COLLECTED": raise AttemptError("expected COLLECTED")
        destination=multi_host.measurement_trial_path(state["resolved"],args.output_root,"cli-01")
        _extract_client_archive(Path(received["artifact"]),args.runtime_dir/"srv-01"/"staging",destination,state)
        Path(received["artifact"]).unlink()
        rc=scaling.summarize(args.experiment,args.output_root,args.drone_count)
        if rc: raise AttemptError("paired summary is incomplete")
        print("[OK] multi-host attempt completed, collected, and summarized")
        return 0
    finally: transport.close()


def parser() -> argparse.ArgumentParser:
    p=argparse.ArgumentParser(description=__doc__); p.add_argument("--experiment",type=Path,default=scaling.DEFAULT_EXPERIMENT)
    p.add_argument("--output-root",type=Path,default=scaling.WORK_ROOT); p.add_argument("--runtime-dir",type=Path,default=DEFAULT_RUNTIME)
    p.add_argument("--drone-count",type=int,default=256); p.add_argument("--session-id",required=True); p.add_argument("--clean",action="store_true")
    p.add_argument("--timeout-sec",type=float,default=600.0); sub=p.add_subparsers(dest="role",required=True)
    s=sub.add_parser("server"); s.add_argument("--listen-address",default="192.168.2.100"); s.add_argument("--control-port",type=int,default=54200); s.add_argument("--artifact-port",type=int,default=54201)
    c=sub.add_parser("client"); c.add_argument("--server-address",default="192.168.2.100"); c.add_argument("--control-port",type=int,default=54200); c.add_argument("--artifact-port",type=int,default=54201)
    return p


def main(argv: list[str] | None=None) -> int:
    args=parser().parse_args(argv); args.experiment=args.experiment.resolve(); args.output_root=args.output_root.resolve(); args.runtime_dir=args.runtime_dir.resolve()
    try: return server(args) if args.role=="server" else client(args)
    except Exception as exc:
        print(f"[ERROR] {exc}",file=sys.stderr)
        if getattr(args, "prepared_host", None) is not None:
            try:
                _run(args, args.prepared_host, "stop")
            except Exception as cleanup_exc:
                print(f"[WARN] best-effort stop failed: {cleanup_exc}", file=sys.stderr)
        return 1


if __name__ == "__main__": raise SystemExit(main())
