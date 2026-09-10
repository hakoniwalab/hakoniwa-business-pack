"""Validated JSON/base64 chunk protocol for transferring ZIP evidence."""

from __future__ import annotations

import base64
import binascii
import json
import re
from pathlib import Path
from typing import Any, Mapping


SCHEMA_VERSION = 1
MAX_CHUNK_SIZE = 48 * 1024
MAX_WIRE_BYTES = 96 * 1024
SCHEMA_PATH = (
    Path(__file__).resolve().parents[2]
    / "schemas"
    / "remote-operation"
    / "artifact-message.schema.json"
)
MESSAGE_TYPES = frozenset(
    {"OFFER", "ACCEPT", "CHUNK", "COMPLETE", "VERIFIED", "REJECTED"}
)
COMMON_FIELDS = frozenset(
    {"schema_version", "type", "session_id", "transfer_id", "sequence", "source_host"}
)
TYPE_FIELDS = {
    "OFFER": frozenset(
        {"artifact_name", "media_type", "size_bytes", "sha256", "chunk_size", "chunk_count"}
    ),
    "ACCEPT": frozenset(),
    "CHUNK": frozenset({"chunk_index", "data_base64"}),
    "COMPLETE": frozenset({"size_bytes", "sha256"}),
    "VERIFIED": frozenset({"size_bytes", "sha256"}),
    "REJECTED": frozenset({"error"}),
}
_IDENTIFIER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
_HOST_RE = re.compile(r"^[a-z0-9][a-z0-9-]*$")
_TRANSFER_RE = re.compile(r"^[0-9a-f]{32}$")
_HASH_RE = re.compile(r"^[0-9a-f]{64}$")
_ARTIFACT_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*\.zip$")
_ERROR_CODE_RE = re.compile(r"^[A-Z][A-Z0-9_]*$")


class ArtifactProtocolError(ValueError):
    pass


def _positive_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 1


def _text(value: Any, maximum: int, pattern: re.Pattern[str]) -> bool:
    return (
        isinstance(value, str)
        and 1 <= len(value) <= maximum
        and pattern.fullmatch(value) is not None
    )


def validate_message(message: Any) -> dict[str, Any]:
    if not isinstance(message, dict):
        raise ArtifactProtocolError("artifact message must be an object")
    message_type = message.get("type")
    if message_type not in MESSAGE_TYPES:
        raise ArtifactProtocolError(f"invalid artifact message type: {message_type!r}")
    required = COMMON_FIELDS | TYPE_FIELDS[message_type]
    missing = required - set(message)
    unknown = set(message) - required
    if missing:
        raise ArtifactProtocolError("message is missing fields: " + ", ".join(sorted(missing)))
    if unknown:
        raise ArtifactProtocolError("message has unknown fields: " + ", ".join(sorted(unknown)))
    if message.get("schema_version") != SCHEMA_VERSION:
        raise ArtifactProtocolError("unsupported artifact schema_version")
    if not _text(message.get("session_id"), 128, _IDENTIFIER_RE):
        raise ArtifactProtocolError("session_id has an invalid value")
    if not _text(message.get("transfer_id"), 32, _TRANSFER_RE):
        raise ArtifactProtocolError("transfer_id has an invalid value")
    if not _positive_int(message.get("sequence")):
        raise ArtifactProtocolError("sequence must be a positive integer")
    if not _text(message.get("source_host"), 63, _HOST_RE):
        raise ArtifactProtocolError("source_host has an invalid value")

    if message_type == "OFFER":
        if not _text(message.get("artifact_name"), 128, _ARTIFACT_RE):
            raise ArtifactProtocolError("artifact_name must be a safe .zip basename")
        if Path(message["artifact_name"]).name != message["artifact_name"]:
            raise ArtifactProtocolError("artifact_name must not contain a path")
        if message.get("media_type") != "application/zip":
            raise ArtifactProtocolError("media_type must be application/zip")
        if not _positive_int(message.get("size_bytes")):
            raise ArtifactProtocolError("size_bytes must be a positive integer")
        if not _text(message.get("sha256"), 64, _HASH_RE):
            raise ArtifactProtocolError("sha256 must be lowercase hexadecimal")
        chunk_size = message.get("chunk_size")
        if not _positive_int(chunk_size) or not 1024 <= chunk_size <= MAX_CHUNK_SIZE:
            raise ArtifactProtocolError("chunk_size is outside the supported range")
        if not _positive_int(message.get("chunk_count")):
            raise ArtifactProtocolError("chunk_count must be a positive integer")
        expected = (message["size_bytes"] + chunk_size - 1) // chunk_size
        if message["chunk_count"] != expected:
            raise ArtifactProtocolError("chunk_count does not match size_bytes")
    elif message_type == "CHUNK":
        index = message.get("chunk_index")
        if not isinstance(index, int) or isinstance(index, bool) or index < 0:
            raise ArtifactProtocolError("chunk_index must be a non-negative integer")
        data = message.get("data_base64")
        if not isinstance(data, str) or not data:
            raise ArtifactProtocolError("data_base64 must be a non-empty string")
        try:
            decoded = base64.b64decode(data, validate=True)
        except (binascii.Error, ValueError) as exc:
            raise ArtifactProtocolError("data_base64 is invalid") from exc
        if not decoded or len(decoded) > MAX_CHUNK_SIZE:
            raise ArtifactProtocolError("decoded chunk is outside the supported range")
    elif message_type in {"COMPLETE", "VERIFIED"}:
        if not _positive_int(message.get("size_bytes")):
            raise ArtifactProtocolError("size_bytes must be a positive integer")
        if not _text(message.get("sha256"), 64, _HASH_RE):
            raise ArtifactProtocolError("sha256 must be lowercase hexadecimal")
    elif message_type == "REJECTED":
        error = message.get("error")
        if not isinstance(error, dict) or set(error) != {"code", "message"}:
            raise ArtifactProtocolError("error must contain exactly code and message")
        if not _text(error.get("code"), 64, _ERROR_CODE_RE):
            raise ArtifactProtocolError("error.code has an invalid value")
        detail = error.get("message")
        if not isinstance(detail, str) or not 1 <= len(detail) <= 2048:
            raise ArtifactProtocolError("error.message has an invalid value")

    try:
        return json.loads(json.dumps(message, ensure_ascii=False, allow_nan=False))
    except (TypeError, ValueError) as exc:
        raise ArtifactProtocolError(f"message is not JSON-compatible: {exc}") from exc


def make_message(
    *,
    message_type: str,
    session_id: str,
    transfer_id: str,
    sequence: int,
    source_host: str,
    **fields: Any,
) -> dict[str, Any]:
    return validate_message(
        {
            "schema_version": SCHEMA_VERSION,
            "type": message_type,
            "session_id": session_id,
            "transfer_id": transfer_id,
            "sequence": sequence,
            "source_host": source_host,
            **fields,
        }
    )


def encode_message(message: Mapping[str, Any]) -> bytes:
    payload = json.dumps(
        validate_message(dict(message)),
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    if len(payload) > MAX_WIRE_BYTES:
        raise ArtifactProtocolError("artifact message exceeds the wire limit")
    return payload


def decode_message(payload: bytes) -> dict[str, Any]:
    if not isinstance(payload, bytes) or not payload:
        raise ArtifactProtocolError("artifact payload must be non-empty bytes")
    if len(payload) > MAX_WIRE_BYTES:
        raise ArtifactProtocolError("artifact payload exceeds the wire limit")
    try:
        value = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ArtifactProtocolError("artifact payload is not valid UTF-8 JSON") from exc
    return validate_message(value)


def encode_chunk(data: bytes) -> str:
    if not data or len(data) > MAX_CHUNK_SIZE:
        raise ArtifactProtocolError("chunk bytes are outside the supported range")
    return base64.b64encode(data).decode("ascii")


def decode_chunk(message: Mapping[str, Any]) -> bytes:
    validated = validate_message(dict(message))
    if validated["type"] != "CHUNK":
        raise ArtifactProtocolError("message is not CHUNK")
    return base64.b64decode(validated["data_base64"], validate=True)
