#!/usr/bin/env python3
"""Package and transfer ZIP evidence over a separate PDU Endpoint channel."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import sys
import time
import uuid
import zipfile
from pathlib import Path
from typing import Any, Callable, Iterable

from tools.remote_operation import artifact_protocol
from tools.remote_operation.pdu_transport import (
    PduJsonTransport,
    TransportError,
    write_tcp_endpoint_config,
)


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_RUNTIME = ROOT / "work" / "remote-operation" / "artifacts"
ARTIFACT_PDU_ROBOT = "hako_remote_artifact"
ARTIFACT_PDU_CHANNEL_ID = 2
DEFAULT_PORT = 54201
DEFAULT_CHUNK_SIZE = 32 * 1024
DEFAULT_MAX_BYTES = 1024 * 1024 * 1024


class ArtifactTransferError(RuntimeError):
    pass


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        while chunk := source.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _safe_session(value: str) -> str:
    probe = artifact_protocol.make_message(
        message_type="ACCEPT",
        session_id=value,
        transfer_id="0" * 32,
        sequence=1,
        source_host="srv-01",
    )
    return str(probe["session_id"])


def _archive_members(source: Path) -> Iterable[tuple[Path, Path]]:
    if source.is_symlink():
        raise ArtifactTransferError(f"source must not be a symlink: {source}")
    if source.is_file():
        yield source, Path(source.name)
        return
    if not source.is_dir():
        raise ArtifactTransferError(f"source does not exist: {source}")
    for path in sorted(source.rglob("*")):
        if path.is_symlink():
            raise ArtifactTransferError(f"archive source contains a symlink: {path}")
        if path.is_file():
            yield path, Path(source.name) / path.relative_to(source)


def create_zip(sources: list[Path], output: Path) -> Path:
    if not sources:
        raise ArtifactTransferError("at least one --source is required")
    resolved_sources = [source.resolve() for source in sources]
    names = [source.name for source in resolved_sources]
    if len(set(names)) != len(names):
        raise ArtifactTransferError("archive sources must have distinct basenames")
    output = output.resolve()
    if output.suffix.lower() != ".zip":
        raise ArtifactTransferError("archive output must use the .zip extension")
    for source in resolved_sources:
        if output == source or source in output.parents:
            raise ArtifactTransferError("archive output must be outside every source")
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_suffix(output.suffix + ".part")
    temporary.unlink(missing_ok=True)
    try:
        with zipfile.ZipFile(
            temporary,
            mode="w",
            compression=zipfile.ZIP_DEFLATED,
            compresslevel=6,
        ) as archive:
            for source in resolved_sources:
                for path, archive_name in _archive_members(source):
                    archive.write(path, archive_name.as_posix())
        if temporary.stat().st_size <= 0:
            raise ArtifactTransferError("created ZIP is empty")
        temporary.replace(output)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise
    return output


def _transport(endpoint_config: Path) -> PduJsonTransport:
    return PduJsonTransport(
        endpoint_config,
        encoder=artifact_protocol.encode_message,
        decoder=artifact_protocol.decode_message,
        pdu_robot=ARTIFACT_PDU_ROBOT,
        pdu_channel_id=ARTIFACT_PDU_CHANNEL_ID,
    )


def _event_summary(message: dict[str, Any]) -> dict[str, Any]:
    summary = {key: value for key, value in message.items() if key != "data_base64"}
    if message.get("type") == "CHUNK":
        summary["chunk_bytes"] = len(artifact_protocol.decode_chunk(message))
    return summary


def _log_event(path: Path, direction: str, message: dict[str, Any], host: str) -> None:
    record = {
        "recorded_at_unix_sec": time.time(),
        "direction": direction,
        "message": _event_summary(message),
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as output:
        output.write(json.dumps(record, ensure_ascii=False, separators=(",", ":")))
        output.write("\n")
    detail = ""
    if message["type"] == "CHUNK":
        detail = f" index={message['chunk_index']} bytes={record['message']['chunk_bytes']}"
    print(
        f"[ARTIFACT][{host}][{direction.upper()}] seq={message['sequence']} "
        f"type={message['type']}{detail}",
        flush=True,
    )


def _check_identity(
    message: dict[str, Any],
    *,
    session_id: str,
    transfer_id: str,
    sequence: int,
    source_host: str,
) -> None:
    expected = {
        "session_id": session_id,
        "transfer_id": transfer_id,
        "sequence": sequence,
        "source_host": source_host,
    }
    for field, value in expected.items():
        if message.get(field) != value:
            raise ArtifactTransferError(
                f"received {field}={message.get(field)!r}; expected {value!r}"
            )


def _send(
    transport: PduJsonTransport,
    log: Path,
    host: str,
    message: dict[str, Any],
) -> None:
    transport.send(message)
    _log_event(log, "send", message, host)


def _receive(
    transport: PduJsonTransport,
    log: Path,
    host: str,
    timeout_sec: float,
) -> dict[str, Any]:
    message = transport.receive(timeout_sec)
    _log_event(log, "receive", message, host)
    return message


def send_file(
    transport: PduJsonTransport,
    path: Path,
    *,
    session_id: str,
    timeout_sec: float,
    chunk_size: int,
    event_log: Path,
) -> dict[str, Any]:
    path = path.resolve()
    if not path.is_file() or path.suffix.lower() != ".zip":
        raise ArtifactTransferError(f"artifact must be an existing ZIP: {path}")
    if not 1024 <= chunk_size <= artifact_protocol.MAX_CHUNK_SIZE:
        raise ArtifactTransferError(
            f"chunk size must be from 1024 through {artifact_protocol.MAX_CHUNK_SIZE}"
        )
    size = path.stat().st_size
    if size <= 0:
        raise ArtifactTransferError("artifact ZIP must not be empty")
    digest = _sha256(path)
    chunk_count = math.ceil(size / chunk_size)
    transfer_id = uuid.uuid4().hex
    sender_sequence = 1
    receiver_sequence = 1
    offer = artifact_protocol.make_message(
        message_type="OFFER",
        session_id=session_id,
        transfer_id=transfer_id,
        sequence=sender_sequence,
        source_host="cli-01",
        artifact_name=path.name,
        media_type="application/zip",
        size_bytes=size,
        sha256=digest,
        chunk_size=chunk_size,
        chunk_count=chunk_count,
    )
    _send(transport, event_log, "cli-01", offer)
    sender_sequence += 1
    response = _receive(transport, event_log, "cli-01", timeout_sec)
    _check_identity(
        response,
        session_id=session_id,
        transfer_id=transfer_id,
        sequence=receiver_sequence,
        source_host="srv-01",
    )
    if response["type"] == "REJECTED":
        raise ArtifactTransferError(f"receiver rejected artifact: {response['error']}")
    if response["type"] != "ACCEPT":
        raise ArtifactTransferError(f"expected ACCEPT, received {response['type']}")
    receiver_sequence += 1

    with path.open("rb") as source:
        for index in range(chunk_count):
            data = source.read(chunk_size)
            if not data:
                raise ArtifactTransferError("artifact ended before declared chunk_count")
            chunk = artifact_protocol.make_message(
                message_type="CHUNK",
                session_id=session_id,
                transfer_id=transfer_id,
                sequence=sender_sequence,
                source_host="cli-01",
                chunk_index=index,
                data_base64=artifact_protocol.encode_chunk(data),
            )
            _send(transport, event_log, "cli-01", chunk)
            sender_sequence += 1
        if source.read(1):
            raise ArtifactTransferError("artifact contains more data than declared")
    complete = artifact_protocol.make_message(
        message_type="COMPLETE",
        session_id=session_id,
        transfer_id=transfer_id,
        sequence=sender_sequence,
        source_host="cli-01",
        size_bytes=size,
        sha256=digest,
    )
    _send(transport, event_log, "cli-01", complete)
    response = _receive(transport, event_log, "cli-01", timeout_sec)
    _check_identity(
        response,
        session_id=session_id,
        transfer_id=transfer_id,
        sequence=receiver_sequence,
        source_host="srv-01",
    )
    if response["type"] == "REJECTED":
        raise ArtifactTransferError(f"receiver rejected artifact: {response['error']}")
    if response["type"] != "VERIFIED":
        raise ArtifactTransferError(f"expected VERIFIED, received {response['type']}")
    if response["size_bytes"] != size or response["sha256"] != digest:
        raise ArtifactTransferError("VERIFIED identity does not match the sent artifact")
    return {
        "status": "success",
        "session_id": session_id,
        "transfer_id": transfer_id,
        "artifact": str(path),
        "artifact_name": path.name,
        "size_bytes": size,
        "sha256": digest,
        "chunk_size": chunk_size,
        "chunk_count": chunk_count,
    }


def receive_file(
    transport: PduJsonTransport,
    output_dir: Path,
    *,
    session_id: str,
    timeout_sec: float,
    max_bytes: int,
    event_log: Path,
    on_verified: Callable[[Path, dict[str, Any]], dict[str, Any] | None]
    | None = None,
) -> dict[str, Any]:
    output_dir = output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    offer = _receive(transport, event_log, "srv-01", timeout_sec)
    if offer["type"] != "OFFER":
        raise ArtifactTransferError(f"expected OFFER, received {offer['type']}")
    transfer_id = str(offer["transfer_id"])
    _check_identity(
        offer,
        session_id=session_id,
        transfer_id=transfer_id,
        sequence=1,
        source_host="cli-01",
    )
    receiver_sequence = 1
    sender_sequence = 2
    destination = output_dir / offer["artifact_name"]
    temporary = destination.with_suffix(destination.suffix + f".{transfer_id}.part")

    def reply(message_type: str, **fields: Any) -> None:
        nonlocal receiver_sequence
        message = artifact_protocol.make_message(
            message_type=message_type,
            session_id=session_id,
            transfer_id=transfer_id,
            sequence=receiver_sequence,
            source_host="srv-01",
            **fields,
        )
        _send(transport, event_log, "srv-01", message)
        receiver_sequence += 1

    if offer["size_bytes"] > max_bytes:
        reply(
            "REJECTED",
            error={"code": "ARTIFACT_TOO_LARGE", "message": "offered ZIP exceeds receiver limit"},
        )
        raise ArtifactTransferError("offered ZIP exceeds receiver limit")
    if destination.exists():
        reply(
            "REJECTED",
            error={"code": "DESTINATION_EXISTS", "message": "destination ZIP already exists"},
        )
        raise ArtifactTransferError(f"destination already exists: {destination}")
    temporary.unlink(missing_ok=True)
    reply("ACCEPT")
    digest = hashlib.sha256()
    received_size = 0
    try:
        with temporary.open("xb") as output:
            for expected_index in range(offer["chunk_count"]):
                chunk = _receive(transport, event_log, "srv-01", timeout_sec)
                _check_identity(
                    chunk,
                    session_id=session_id,
                    transfer_id=transfer_id,
                    sequence=sender_sequence,
                    source_host="cli-01",
                )
                sender_sequence += 1
                if chunk["type"] != "CHUNK":
                    raise ArtifactTransferError(f"expected CHUNK, received {chunk['type']}")
                if chunk["chunk_index"] != expected_index:
                    raise ArtifactTransferError(
                        f"received chunk {chunk['chunk_index']}; expected {expected_index}"
                    )
                data = artifact_protocol.decode_chunk(chunk)
                is_last = expected_index == offer["chunk_count"] - 1
                if not is_last and len(data) != offer["chunk_size"]:
                    raise ArtifactTransferError("non-final chunk has the wrong size")
                if received_size + len(data) > offer["size_bytes"]:
                    raise ArtifactTransferError("received bytes exceed the offered size")
                output.write(data)
                digest.update(data)
                received_size += len(data)
        complete = _receive(transport, event_log, "srv-01", timeout_sec)
        _check_identity(
            complete,
            session_id=session_id,
            transfer_id=transfer_id,
            sequence=sender_sequence,
            source_host="cli-01",
        )
        if complete["type"] != "COMPLETE":
            raise ArtifactTransferError(f"expected COMPLETE, received {complete['type']}")
        actual_hash = digest.hexdigest()
        if (
            received_size != offer["size_bytes"]
            or complete["size_bytes"] != received_size
            or actual_hash != offer["sha256"]
            or complete["sha256"] != actual_hash
        ):
            raise ArtifactTransferError("received ZIP size or SHA-256 does not match the offer")
        temporary.replace(destination)
        publication = None
        if on_verified is not None:
            try:
                publication = on_verified(destination, offer)
            except Exception:
                destination.unlink(missing_ok=True)
                raise
        reply("VERIFIED", size_bytes=received_size, sha256=actual_hash)
        result = {
            "status": "success",
            "session_id": session_id,
            "transfer_id": transfer_id,
            "artifact": str(destination),
            "artifact_name": destination.name,
            "size_bytes": received_size,
            "sha256": actual_hash,
            "chunk_size": offer["chunk_size"],
            "chunk_count": offer["chunk_count"],
        }
        if publication is not None:
            result["publication"] = publication
        return result
    except Exception as exc:
        temporary.unlink(missing_ok=True)
        if receiver_sequence == 2:
            try:
                reply(
                    "REJECTED",
                    error={"code": "TRANSFER_FAILED", "message": str(exc)[:2048]},
                )
            except Exception:
                pass
        raise


def _write_result(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def receive_command(args: argparse.Namespace) -> int:
    session_id = _safe_session(args.session_id)
    runtime = args.runtime_dir.resolve()
    runtime.mkdir(parents=True, exist_ok=True)
    event_log = runtime / "receiver-events.jsonl"
    event_log.unlink(missing_ok=True)
    config = write_tcp_endpoint_config(
        runtime / "receiver-endpoint",
        role="server",
        address=args.listen_address,
        port=args.port,
    )
    transport = _transport(config)
    try:
        transport.start()
        print(f"Waiting for artifact on {args.listen_address}:{args.port}", flush=True)
        transport.wait_connected(args.timeout_sec)
        result = receive_file(
            transport,
            args.output_dir,
            session_id=session_id,
            timeout_sec=args.timeout_sec,
            max_bytes=args.max_bytes,
            event_log=event_log,
        )
        result["events"] = str(event_log)
        result_path = runtime / "receiver-result.json"
        _write_result(result_path, result)
        print(f"[OK] verified artifact: {result['artifact']}")
        print(f"SHA-256: {result['sha256']}")
        print(f"Evidence: {result_path}")
        return 0
    finally:
        transport.close()


def send_command(args: argparse.Namespace, artifact: Path) -> int:
    session_id = _safe_session(args.session_id)
    runtime = args.runtime_dir.resolve()
    runtime.mkdir(parents=True, exist_ok=True)
    event_log = runtime / "sender-events.jsonl"
    event_log.unlink(missing_ok=True)
    config = write_tcp_endpoint_config(
        runtime / "sender-endpoint",
        role="client",
        address=args.server_address,
        port=args.port,
    )
    transport = _transport(config)
    try:
        transport.start()
        print(f"Connecting artifact sender to {args.server_address}:{args.port}", flush=True)
        transport.wait_connected(args.timeout_sec)
        result = send_file(
            transport,
            artifact,
            session_id=session_id,
            timeout_sec=args.timeout_sec,
            chunk_size=args.chunk_size,
            event_log=event_log,
        )
        result["events"] = str(event_log)
        result_path = runtime / "sender-result.json"
        _write_result(result_path, result)
        print(f"[OK] receiver verified artifact: {artifact}")
        print(f"SHA-256: {result['sha256']}")
        print(f"Evidence: {result_path}")
        return 0
    finally:
        transport.close()


def pack_command(args: argparse.Namespace) -> int:
    archive = create_zip(args.source, args.output)
    print(f"[OK] ZIP created: {archive}")
    print(f"Size: {archive.stat().st_size} bytes")
    print(f"SHA-256: {_sha256(archive)}")
    return 0


def pack_send_command(args: argparse.Namespace) -> int:
    session_id = _safe_session(args.session_id)
    output = args.archive
    if output is None:
        output = args.runtime_dir / f"{session_id}-client-results.zip"
    archive = create_zip(args.source, output)
    print(f"[OK] ZIP created: {archive}")
    return send_command(args, archive)


def _add_connection_arguments(parser: argparse.ArgumentParser, *, sender: bool) -> None:
    if sender:
        parser.add_argument("--server-address", default="192.168.2.100")
    else:
        parser.add_argument("--listen-address", default="192.168.2.100")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--session-id", required=True)
    parser.add_argument("--runtime-dir", type=Path, default=DEFAULT_RUNTIME)
    parser.add_argument("--timeout-sec", type=float, default=300.0)


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description="Package and transfer Recipe ZIP evidence")
    commands = result.add_subparsers(dest="command", required=True)
    pack = commands.add_parser("pack")
    pack.add_argument("--source", type=Path, action="append", required=True)
    pack.add_argument("--output", type=Path, required=True)

    receive = commands.add_parser("receive")
    _add_connection_arguments(receive, sender=False)
    receive.add_argument("--output-dir", type=Path, required=True)
    receive.add_argument("--max-bytes", type=int, default=DEFAULT_MAX_BYTES)

    send = commands.add_parser("send")
    _add_connection_arguments(send, sender=True)
    send.add_argument("--file", type=Path, required=True)
    send.add_argument("--chunk-size", type=int, default=DEFAULT_CHUNK_SIZE)

    pack_send = commands.add_parser("pack-send")
    _add_connection_arguments(pack_send, sender=True)
    pack_send.add_argument("--source", type=Path, action="append", required=True)
    pack_send.add_argument("--archive", type=Path, default=None)
    pack_send.add_argument("--chunk-size", type=int, default=DEFAULT_CHUNK_SIZE)
    return result


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        if args.command == "pack":
            return pack_command(args)
        if args.command == "receive":
            return receive_command(args)
        if args.command == "send":
            return send_command(args, args.file)
        return pack_send_command(args)
    except (
        ArtifactTransferError,
        artifact_protocol.ArtifactProtocolError,
        TransportError,
        OSError,
        ValueError,
        zipfile.BadZipFile,
    ) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
