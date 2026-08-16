"""JSON wire contract for constrained remote Recipe coordination.

This protocol carries only enumerated commands and statuses.  It deliberately
has no field for a shell command, executable path, or arbitrary environment.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Mapping


SCHEMA_VERSION = 1
MAX_WIRE_BYTES = 16 * 1024
SCHEMA_PATH = (
    Path(__file__).resolve().parents[2]
    / "schemas"
    / "remote-operation"
    / "message.schema.json"
)

COMMAND_TYPES = frozenset(
    {
        "PREPARE",
        "LAUNCH",
        "RUN",
        "CLEANUP",
        "COLLECT",
        "NEXT_ATTEMPT",
        "BATCH_COMPLETE",
        "ABORT",
    }
)
STATUS_TYPES = frozenset(
    {
        "REGISTERED",
        "PREPARING",
        "READY",
        "LAUNCHED",
        "JOINED",
        "RUNNING",
        "TERMINATED",
        "CLEANED",
        "COLLECTING",
        "COLLECTED",
        "BATCH_COMPLETED",
        "FAILED",
    }
)
MESSAGE_TYPES = COMMAND_TYPES | STATUS_TYPES

REQUIRED_FIELDS = frozenset(
    {
        "schema_version",
        "kind",
        "type",
        "session_id",
        "sequence",
        "attempt",
        "source_host",
        "configuration_id",
        "config_hash",
    }
)
OPTIONAL_FIELDS = frozenset({"error"})

_IDENTIFIER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
_HOST_RE = re.compile(r"^[a-z0-9][a-z0-9-]*$")
_HASH_RE = re.compile(r"^[0-9a-f]{64}$")
_PHASE_RE = re.compile(r"^[a-z][a-z0-9_-]*$")
_ERROR_CODE_RE = re.compile(r"^[A-Z][A-Z0-9_]*$")

COMMAND_TRANSITIONS = {
    None: frozenset({"PREPARE", "ABORT"}),
    "PREPARE": frozenset({"LAUNCH", "ABORT"}),
    "LAUNCH": frozenset({"RUN", "ABORT"}),
    "RUN": frozenset({"CLEANUP", "ABORT"}),
    "ABORT": frozenset({"CLEANUP"}),
    "CLEANUP": frozenset({"COLLECT"}),
    "COLLECT": frozenset({"NEXT_ATTEMPT", "BATCH_COMPLETE"}),
    "NEXT_ATTEMPT": frozenset(),
    "BATCH_COMPLETE": frozenset(),
}
STATUS_TRANSITIONS = {
    None: frozenset({"REGISTERED", "FAILED"}),
    "REGISTERED": frozenset({"PREPARING", "FAILED"}),
    "PREPARING": frozenset({"READY", "FAILED"}),
    "READY": frozenset({"LAUNCHED", "FAILED"}),
    # The server can observe Conductor JOINED directly. A client may therefore
    # move to RUNNING without claiming that it independently observed JOINED.
    "LAUNCHED": frozenset({"JOINED", "RUNNING", "FAILED"}),
    "JOINED": frozenset({"RUNNING", "FAILED"}),
    "RUNNING": frozenset({"TERMINATED", "FAILED"}),
    "TERMINATED": frozenset({"CLEANED", "FAILED"}),
    "FAILED": frozenset({"CLEANED"}),
    "CLEANED": frozenset({"COLLECTING", "FAILED"}),
    "COLLECTING": frozenset({"COLLECTED", "FAILED"}),
    "COLLECTED": frozenset({"BATCH_COMPLETED"}),
    "BATCH_COMPLETED": frozenset(),
}


class ProtocolError(ValueError):
    """Raised when a remote-operation message violates the wire contract."""


def _integer_at_least(value: Any, minimum: int) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= minimum


def _string_field(
    message: Mapping[str, Any],
    field: str,
    *,
    maximum: int,
    pattern: re.Pattern[str],
) -> None:
    value = message.get(field)
    if (
        not isinstance(value, str)
        or not value
        or len(value) > maximum
        or pattern.fullmatch(value) is None
    ):
        raise ProtocolError(f"{field} has an invalid value")


def _validate_error(error: Any) -> None:
    if not isinstance(error, dict):
        raise ProtocolError("error must be an object")
    required = {"phase", "code", "message"}
    unknown = set(error) - required
    missing = required - set(error)
    if missing:
        raise ProtocolError("error is missing fields: " + ", ".join(sorted(missing)))
    if unknown:
        raise ProtocolError("error has unknown fields: " + ", ".join(sorted(unknown)))
    _string_field(error, "phase", maximum=64, pattern=_PHASE_RE)
    _string_field(error, "code", maximum=64, pattern=_ERROR_CODE_RE)
    detail = error.get("message")
    if not isinstance(detail, str) or not detail or len(detail) > 2048:
        raise ProtocolError("error.message must contain 1 through 2048 characters")


def validate_message(message: Any) -> dict[str, Any]:
    """Validate and return a detached plain-dict representation."""

    if not isinstance(message, dict):
        raise ProtocolError("message must be an object")
    fields = set(message)
    missing = REQUIRED_FIELDS - fields
    unknown = fields - REQUIRED_FIELDS - OPTIONAL_FIELDS
    if missing:
        raise ProtocolError("message is missing fields: " + ", ".join(sorted(missing)))
    if unknown:
        raise ProtocolError("message has unknown fields: " + ", ".join(sorted(unknown)))
    if message.get("schema_version") != SCHEMA_VERSION:
        raise ProtocolError(
            f"unsupported schema_version: {message.get('schema_version')!r}"
        )

    kind = message.get("kind")
    message_type = message.get("type")
    if kind == "command":
        allowed = COMMAND_TYPES
    elif kind == "status":
        allowed = STATUS_TYPES
    else:
        raise ProtocolError(f"invalid kind: {kind!r}")
    if message_type not in allowed:
        raise ProtocolError(f"type {message_type!r} is not valid for kind {kind!r}")

    _string_field(message, "session_id", maximum=128, pattern=_IDENTIFIER_RE)
    _string_field(message, "source_host", maximum=63, pattern=_HOST_RE)
    _string_field(message, "configuration_id", maximum=128, pattern=_IDENTIFIER_RE)
    _string_field(message, "config_hash", maximum=64, pattern=_HASH_RE)
    if not _integer_at_least(message.get("sequence"), 1):
        raise ProtocolError("sequence must be a positive integer")
    if not _integer_at_least(message.get("attempt"), 1):
        raise ProtocolError("attempt must be a positive integer")

    if message_type == "FAILED":
        if "error" not in message:
            raise ProtocolError("FAILED status requires error")
        _validate_error(message["error"])
    elif "error" in message:
        raise ProtocolError("error is allowed only for FAILED status")

    # JSON round-tripping both detaches the value and rejects non-JSON values.
    try:
        return json.loads(json.dumps(message, ensure_ascii=False, allow_nan=False))
    except (TypeError, ValueError) as exc:
        raise ProtocolError(f"message is not JSON-compatible: {exc}") from exc


def make_message(
    *,
    kind: str,
    message_type: str,
    session_id: str,
    sequence: int,
    attempt: int,
    source_host: str,
    configuration_id: str,
    config_hash: str,
    error: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    message: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "kind": kind,
        "type": message_type,
        "session_id": session_id,
        "sequence": sequence,
        "attempt": attempt,
        "source_host": source_host,
        "configuration_id": configuration_id,
        "config_hash": config_hash,
    }
    if error is not None:
        message["error"] = dict(error)
    return validate_message(message)


def encode_message(message: Mapping[str, Any]) -> bytes:
    validated = validate_message(dict(message))
    payload = json.dumps(
        validated,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    if len(payload) > MAX_WIRE_BYTES:
        raise ProtocolError(
            f"encoded message exceeds the {MAX_WIRE_BYTES}-byte application limit"
        )
    return payload


def decode_message(payload: bytes) -> dict[str, Any]:
    if not isinstance(payload, bytes):
        raise ProtocolError("payload must be bytes")
    if not payload:
        raise ProtocolError("payload must not be empty")
    if len(payload) > MAX_WIRE_BYTES:
        raise ProtocolError(
            f"payload exceeds the {MAX_WIRE_BYTES}-byte application limit"
        )
    try:
        decoded = json.loads(payload.decode("utf-8"))
    except UnicodeDecodeError as exc:
        raise ProtocolError("payload is not valid UTF-8") from exc
    except json.JSONDecodeError as exc:
        raise ProtocolError("payload is not valid JSON") from exc
    return validate_message(decoded)


def validate_transition(
    kind: str,
    previous_type: str | None,
    next_type: str,
) -> None:
    """Reject an out-of-order command or status transition."""

    if kind == "command":
        transitions = COMMAND_TRANSITIONS
    elif kind == "status":
        transitions = STATUS_TRANSITIONS
    else:
        raise ProtocolError(f"invalid kind: {kind!r}")
    if previous_type not in transitions:
        raise ProtocolError(f"unknown previous {kind} type: {previous_type!r}")
    if next_type not in transitions[previous_type]:
        raise ProtocolError(
            f"invalid {kind} transition: {previous_type!r} -> {next_type!r}"
        )
