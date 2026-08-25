"""Constrained JSON protocol for remote PLATEAU City World jobs.

The domain protocol intentionally contains no command line, executable path,
environment, or output path.  It can be carried by the shared PduJsonTransport;
large generated outputs remain the responsibility of the shared ZIP artifact
channel.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from pathlib import Path
from typing import Any, Mapping


SCHEMA_VERSION = 1
PROTOCOL_NAME = "hakoniwa.city-world-job"
MAX_WIRE_BYTES = 16 * 1024
ROOT = Path(__file__).resolve().parents[3]
SCHEMA_DIR = ROOT / "schemas" / "remote-operation" / "city-world"
REQUEST_SCHEMA_PATH = SCHEMA_DIR / "request.schema.json"
INSPECTION_SCHEMA_PATH = SCHEMA_DIR / "inspection.schema.json"
MESSAGE_SCHEMA_PATH = SCHEMA_DIR / "message.schema.json"
RESULT_SCHEMA_PATH = SCHEMA_DIR / "result-manifest.schema.json"

COMMAND_TYPES = frozenset({"INSPECT_SELECTION", "GENERATE", "CANCEL"})
STATUS_TYPES = frozenset({
    "INSPECTING",
    "SELECTION_AVAILABLE",
    "SELECTION_UNAVAILABLE",
    "ACCEPTED",
    "DOWNLOADING",
    "GENERATING",
    "VALIDATING",
    "READY",
    "CANCELED",
    "FAILED",
})
MESSAGE_TYPES = COMMAND_TYPES | STATUS_TYPES

STATUS_TRANSITIONS = {
    None: frozenset({"INSPECTING", "FAILED"}),
    "INSPECTING": frozenset({"SELECTION_AVAILABLE", "SELECTION_UNAVAILABLE", "FAILED"}),
    "SELECTION_AVAILABLE": frozenset({"ACCEPTED", "CANCELED", "FAILED"}),
    "SELECTION_UNAVAILABLE": frozenset(),
    "ACCEPTED": frozenset({"DOWNLOADING", "GENERATING", "CANCELED", "FAILED"}),
    "DOWNLOADING": frozenset({"DOWNLOADING", "GENERATING", "CANCELED", "FAILED"}),
    "GENERATING": frozenset({"GENERATING", "VALIDATING", "CANCELED", "FAILED"}),
    "VALIDATING": frozenset({"READY", "CANCELED", "FAILED"}),
    "READY": frozenset(),
    "CANCELED": frozenset(),
    "FAILED": frozenset(),
}

_IDENTIFIER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
_HOST_RE = re.compile(r"^[a-z0-9][a-z0-9-]*$")
_HASH_RE = re.compile(r"^[0-9a-f]{64}$")
_CITY_CODE_RE = re.compile(r"^[0-9]{5}$")
_ARTIFACT_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*\.zip$")
_ERROR_CODE_RE = re.compile(r"^[A-Z][A-Z0-9_]*$")
_PHASE_RE = re.compile(r"^[a-z][a-z0-9_-]*$")


class CityWorldProtocolError(ValueError):
    """Raised when a City World job message violates the protocol contract."""


def _plain_json(value: Any, label: str) -> Any:
    try:
        return json.loads(json.dumps(value, ensure_ascii=False, allow_nan=False))
    except (TypeError, ValueError) as exc:
        raise CityWorldProtocolError(f"{label} is not JSON-compatible: {exc}") from exc


def canonical_sha256(value: Mapping[str, Any]) -> str:
    payload = json.dumps(
        _plain_json(dict(value), "value"),
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _object(value: Any, label: str, required: set[str], optional: set[str] = set()) -> dict:
    if not isinstance(value, dict):
        raise CityWorldProtocolError(f"{label} must be an object")
    missing = required - set(value)
    unknown = set(value) - required - optional
    if missing:
        raise CityWorldProtocolError(f"{label} is missing fields: {', '.join(sorted(missing))}")
    if unknown:
        raise CityWorldProtocolError(f"{label} has unknown fields: {', '.join(sorted(unknown))}")
    return value


def _number(value: Any, label: str, minimum: float, maximum: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise CityWorldProtocolError(f"{label} must be a finite number")
    if not minimum <= value <= maximum:
        raise CityWorldProtocolError(f"{label} must be in [{minimum:g}, {maximum:g}]")
    return float(value)


def _integer(value: Any, label: str, minimum: int, maximum: int | None = None) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise CityWorldProtocolError(f"{label} must be an integer >= {minimum}")
    if maximum is not None and value > maximum:
        raise CityWorldProtocolError(f"{label} must be <= {maximum}")
    return value


def _string(value: Any, label: str, maximum: int, pattern: re.Pattern[str] | None = None) -> str:
    if not isinstance(value, str) or not value or len(value) > maximum:
        raise CityWorldProtocolError(f"{label} must contain 1 through {maximum} characters")
    if pattern is not None and pattern.fullmatch(value) is None:
        raise CityWorldProtocolError(f"{label} has an invalid value")
    return value


def _hash(value: Any, label: str) -> str:
    return _string(value, label, 64, _HASH_RE)


def validate_request(value: Any) -> dict[str, Any]:
    request = _object(
        value, "request", {"schema_version", "selection", "profile", "year"}, {"options"}
    )
    if request["schema_version"] != SCHEMA_VERSION:
        raise CityWorldProtocolError("request.schema_version must be 1")
    if request["profile"] != "visual-physics-v1":
        raise CityWorldProtocolError("request.profile must be visual-physics-v1")
    if request["year"] != "latest":
        raise CityWorldProtocolError("request.year must be latest")
    if "options" in request:
        options = _object(
            request["options"], "request.options", {"building_physics_level"},
            {
                "building_collider_reduction", "terrain_uncovered_policy",
                "terrain_uncovered_elevation_m",
            },
        )
        _integer(
            options["building_physics_level"],
            "request.options.building_physics_level", 0, 3,
        )
        if "building_collider_reduction" in options and options[
            "building_collider_reduction"
        ] not in {"safe", "coplanar-union", "convex-decompose", "tolerant-planar"}:
            raise CityWorldProtocolError(
                "request.options.building_collider_reduction must be safe, "
                "coplanar-union, convex-decompose, or tolerant-planar"
            )
        if options.get("terrain_uncovered_policy", "error") not in {"error", "constant"}:
            raise CityWorldProtocolError(
                "request.options.terrain_uncovered_policy must be error or constant"
            )
        if "terrain_uncovered_elevation_m" in options:
            _number(
                options["terrain_uncovered_elevation_m"],
                "request.options.terrain_uncovered_elevation_m", -1000, 10000,
            )
    selection = _object(request["selection"], "request.selection", {"center", "half_extent_m"})
    center = _object(
        selection["center"], "request.selection.center", {"latitude", "longitude"}
    )
    _number(center["latitude"], "request.selection.center.latitude", -90, 90)
    _number(center["longitude"], "request.selection.center.longitude", -180, 180)
    extent = _object(
        selection["half_extent_m"],
        "request.selection.half_extent_m",
        {"north_south", "east_west"},
    )
    _number(extent["north_south"], "request.selection.half_extent_m.north_south", 10, 1000)
    _number(extent["east_west"], "request.selection.half_extent_m.east_west", 10, 1000)
    return _plain_json(request, "request")


def _validate_capability(value: Any, label: str) -> None:
    cap = _object(
        value,
        label,
        {"dataset_status", "generation_status", "max_lod", "source_file_count", "reason"},
    )
    if cap["dataset_status"] not in {"available", "not_available"}:
        raise CityWorldProtocolError(f"{label}.dataset_status is invalid")
    if cap["generation_status"] not in {"candidate", "limited", "scoped_out"}:
        raise CityWorldProtocolError(f"{label}.generation_status is invalid")
    files = _integer(cap["source_file_count"], f"{label}.source_file_count", 0)
    lod = cap["max_lod"]
    reason = cap["reason"]
    if reason is not None:
        _string(reason, f"{label}.reason", 512)
    if cap["dataset_status"] == "available":
        _integer(lod, f"{label}.max_lod", 0, 4)
        if files == 0:
            raise CityWorldProtocolError(f"{label} is available but has no source files")
    else:
        if lod is not None or files != 0 or cap["generation_status"] != "scoped_out":
            raise CityWorldProtocolError(f"{label} unavailable fields are inconsistent")


def validate_inspection(value: Any) -> dict[str, Any]:
    inspection = _object(
        value,
        "inspection",
        {
            "schema_version", "status", "request_sha256", "bbox", "catalog_snapshot",
            "municipalities", "capabilities", "source_file_count",
            "estimated_download_bytes", "warnings",
        },
        {"reason", "query_meshes"},
    )
    if inspection["schema_version"] != SCHEMA_VERSION:
        raise CityWorldProtocolError("inspection.schema_version must be 1")
    if inspection["status"] not in {"available", "unavailable"}:
        raise CityWorldProtocolError("inspection.status is invalid")
    _hash(inspection["request_sha256"], "inspection.request_sha256")
    bbox = _object(inspection["bbox"], "inspection.bbox", {"west", "south", "east", "north"})
    west = _number(bbox["west"], "inspection.bbox.west", -180, 180)
    east = _number(bbox["east"], "inspection.bbox.east", -180, 180)
    south = _number(bbox["south"], "inspection.bbox.south", -90, 90)
    north = _number(bbox["north"], "inspection.bbox.north", -90, 90)
    if west >= east or south >= north:
        raise CityWorldProtocolError("inspection.bbox must satisfy west < east and south < north")
    query_meshes = inspection.get("query_meshes", [])
    if not isinstance(query_meshes, list) or len(query_meshes) > 64:
        raise CityWorldProtocolError("inspection.query_meshes must contain at most 64 items")
    for index, item in enumerate(query_meshes):
        mesh = _object(item, f"inspection.query_meshes[{index}]", {"code", "bbox"})
        _string(
            mesh["code"], f"inspection.query_meshes[{index}].code", 8,
            re.compile(r"^\d{8}$"),
        )
        mesh_bbox = _object(
            mesh["bbox"], f"inspection.query_meshes[{index}].bbox",
            {"west", "south", "east", "north"},
        )
        mesh_west = _number(mesh_bbox["west"], "mesh west", -180, 180)
        mesh_east = _number(mesh_bbox["east"], "mesh east", -180, 180)
        mesh_south = _number(mesh_bbox["south"], "mesh south", -90, 90)
        mesh_north = _number(mesh_bbox["north"], "mesh north", -90, 90)
        if mesh_west >= mesh_east or mesh_south >= mesh_north:
            raise CityWorldProtocolError("inspection query mesh bbox ordering is invalid")
    snapshot = _object(
        inspection["catalog_snapshot"],
        "inspection.catalog_snapshot",
        {"api_base_url", "fetched_at", "response_sha256"},
    )
    if snapshot["api_base_url"] != "https://api.plateauview.mlit.go.jp":
        raise CityWorldProtocolError("inspection.catalog_snapshot.api_base_url is invalid")
    _string(snapshot["fetched_at"], "inspection.catalog_snapshot.fetched_at", 64)
    _hash(snapshot["response_sha256"], "inspection.catalog_snapshot.response_sha256")
    municipalities = inspection["municipalities"]
    if not isinstance(municipalities, list) or len(municipalities) > 8:
        raise CityWorldProtocolError("inspection.municipalities must contain at most 8 items")
    for index, item in enumerate(municipalities):
        city = _object(item, f"inspection.municipalities[{index}]", {"city_code", "city", "year", "spec"})
        _string(city["city_code"], f"inspection.municipalities[{index}].city_code", 5, _CITY_CODE_RE)
        _string(city["city"], f"inspection.municipalities[{index}].city", 128)
        _integer(city["year"], f"inspection.municipalities[{index}].year", 2000, 2100)
        _string(city["spec"], f"inspection.municipalities[{index}].spec", 32)
    capabilities = _object(
        inspection["capabilities"],
        "inspection.capabilities",
        {"building", "terrain", "road", "road_markings", "bridge"},
    )
    for name, capability in capabilities.items():
        _validate_capability(capability, f"inspection.capabilities.{name}")
    files = _integer(inspection["source_file_count"], "inspection.source_file_count", 0)
    _integer(inspection["estimated_download_bytes"], "inspection.estimated_download_bytes", 0)
    warnings = inspection["warnings"]
    if not isinstance(warnings, list) or len(warnings) > 32:
        raise CityWorldProtocolError("inspection.warnings must contain at most 32 items")
    for index, warning in enumerate(warnings):
        _string(warning, f"inspection.warnings[{index}]", 512)
    reason = inspection.get("reason")
    if reason is not None:
        _string(reason, "inspection.reason", 512)
    if inspection["status"] == "available":
        if not municipalities or files == 0:
            raise CityWorldProtocolError("available inspection requires municipality and source files")
    elif municipalities or files != 0 or reason is None:
        raise CityWorldProtocolError("unavailable inspection fields are inconsistent")
    return _plain_json(inspection, "inspection")


def validate_result(value: Any) -> dict[str, Any]:
    result = _object(
        value,
        "result",
        {
            "schema_version", "job_id", "request_sha256", "inspection_sha256",
            "artifact_name", "media_type", "size_bytes", "sha256", "entries",
        },
        {"building_physics_level", "building_collider_reduction", "colliders"},
    )
    if result["schema_version"] != SCHEMA_VERSION:
        raise CityWorldProtocolError("result.schema_version must be 1")
    _string(result["job_id"], "result.job_id", 128, _IDENTIFIER_RE)
    _hash(result["request_sha256"], "result.request_sha256")
    _hash(result["inspection_sha256"], "result.inspection_sha256")
    if "building_physics_level" in result:
        _integer(
            result["building_physics_level"], "result.building_physics_level", 0, 3
        )
    if "building_collider_reduction" in result and result[
        "building_collider_reduction"
    ] not in {"safe", "coplanar-union", "convex-decompose", "tolerant-planar"}:
        raise CityWorldProtocolError(
            "result.building_collider_reduction must be safe, coplanar-union, "
            "convex-decompose, or tolerant-planar"
        )
    if "colliders" in result:
        colliders = _object(
            result["colliders"], "result.colliders",
            {"total", "by_component", "by_physics_class"},
            {"building_by_geom_type"},
        )
        total = _integer(colliders["total"], "result.colliders.total", 0)
        by_component = colliders["by_component"]
        if not isinstance(by_component, dict):
            raise CityWorldProtocolError("result.colliders.by_component must be an object")
        component_total = 0
        for name, count in by_component.items():
            _string(name, "result collider component name", 64, _PHASE_RE)
            component_total += _integer(count, f"result.colliders.by_component.{name}", 0)
        if total != component_total:
            raise CityWorldProtocolError("result.colliders.total must equal the component sum")
        by_class = _object(
            colliders["by_physics_class"], "result.colliders.by_physics_class",
            {"P0", "P1", "P2", "P3"},
        )
        for class_id in ("P0", "P1", "P2", "P3"):
            _integer(by_class[class_id], f"result.colliders.by_physics_class.{class_id}", 0)
        if "building_by_geom_type" in colliders:
            by_geom_type = _object(
                colliders["building_by_geom_type"],
                "result.colliders.building_by_geom_type",
                {"box", "mesh"},
            )
            for geom_type in ("box", "mesh"):
                _integer(
                    by_geom_type[geom_type],
                    f"result.colliders.building_by_geom_type.{geom_type}", 0,
                )
    name = _string(result["artifact_name"], "result.artifact_name", 128)
    if _ARTIFACT_RE.fullmatch(name) is None:
        raise CityWorldProtocolError("result.artifact_name must be a safe .zip basename")
    if result["media_type"] != "application/zip":
        raise CityWorldProtocolError("result.media_type must be application/zip")
    _integer(result["size_bytes"], "result.size_bytes", 1)
    _hash(result["sha256"], "result.sha256")
    entries = _object(
        result["entries"],
        "result.entries",
        {"visual_world", "physics_world", "dataset_validation", "world_receipt"},
    )
    expected = {
        "visual_world": "visual/city-world.glb",
        "physics_world": "physics/city-world.xml",
        "dataset_validation": "validation/dataset-validation.json",
        "world_receipt": "receipt/city-world-receipt.json",
    }
    if entries != expected:
        raise CityWorldProtocolError("result.entries must use the fixed City World layout")
    return _plain_json(result, "result")


def _validate_error(value: Any) -> None:
    error = _object(value, "error", {"phase", "code", "message"})
    _string(error["phase"], "error.phase", 64, _PHASE_RE)
    _string(error["code"], "error.code", 64, _ERROR_CODE_RE)
    _string(error["message"], "error.message", 2048)


def validate_message(value: Any) -> dict[str, Any]:
    message = _object(
        value,
        "message",
        {"schema_version", "protocol", "kind", "type", "job_id", "sequence", "source_host", "request_sha256"},
        {"inspection_sha256", "request", "inspection", "result", "progress", "error"},
    )
    if message["schema_version"] != SCHEMA_VERSION or message["protocol"] != PROTOCOL_NAME:
        raise CityWorldProtocolError("unsupported City World protocol version or name")
    kind = message["kind"]
    message_type = message["type"]
    allowed = COMMAND_TYPES if kind == "command" else STATUS_TYPES if kind == "status" else frozenset()
    if message_type not in allowed:
        raise CityWorldProtocolError(f"type {message_type!r} is not valid for kind {kind!r}")
    _string(message["job_id"], "message.job_id", 128, _IDENTIFIER_RE)
    _integer(message["sequence"], "message.sequence", 1)
    _string(message["source_host"], "message.source_host", 63, _HOST_RE)
    request_hash = _hash(message["request_sha256"], "message.request_sha256")

    allowed_optional = {
        "INSPECT_SELECTION": {"request"},
        "GENERATE": {"request", "inspection_sha256"},
        "CANCEL": set(),
        "INSPECTING": set(),
        "SELECTION_AVAILABLE": {"inspection"},
        "SELECTION_UNAVAILABLE": {"inspection"},
        "ACCEPTED": {"inspection_sha256", "progress"},
        "DOWNLOADING": {"inspection_sha256", "progress"},
        "GENERATING": {"inspection_sha256", "progress"},
        "VALIDATING": {"inspection_sha256", "progress"},
        "READY": {"inspection_sha256", "result"},
        "CANCELED": set(),
        "FAILED": {"error"},
    }[message_type]
    present_optional = set(message) - {
        "schema_version", "protocol", "kind", "type", "job_id", "sequence", "source_host", "request_sha256"
    }
    if present_optional - allowed_optional:
        raise CityWorldProtocolError(
            f"message type {message_type} has disallowed fields: "
            + ", ".join(sorted(present_optional - allowed_optional))
        )
    required_by_type = {
        "INSPECT_SELECTION": {"request"},
        "GENERATE": {"request", "inspection_sha256"},
        "SELECTION_AVAILABLE": {"inspection"},
        "SELECTION_UNAVAILABLE": {"inspection"},
        "READY": {"inspection_sha256", "result"},
        "FAILED": {"error"},
    }.get(message_type, set())
    missing = required_by_type - set(message)
    if missing:
        raise CityWorldProtocolError(
            f"message type {message_type} is missing fields: {', '.join(sorted(missing))}"
        )

    if "request" in message:
        request = validate_request(message["request"])
        if canonical_sha256(request) != request_hash:
            raise CityWorldProtocolError("message.request_sha256 does not match request")
    if "inspection_sha256" in message:
        _hash(message["inspection_sha256"], "message.inspection_sha256")
    if "inspection" in message:
        inspection = validate_inspection(message["inspection"])
        if inspection["request_sha256"] != request_hash:
            raise CityWorldProtocolError("inspection belongs to another request")
        expected_status = "available" if message_type == "SELECTION_AVAILABLE" else "unavailable"
        if inspection["status"] != expected_status:
            raise CityWorldProtocolError("inspection status does not match message type")
    if "result" in message:
        result = validate_result(message["result"])
        if result["job_id"] != message["job_id"] or result["request_sha256"] != request_hash:
            raise CityWorldProtocolError("result identity does not match message")
        if result["inspection_sha256"] != message["inspection_sha256"]:
            raise CityWorldProtocolError("result inspection identity does not match message")
    if "progress" in message:
        progress = _object(
            message["progress"], "progress", {"percent", "message"},
            {"phase", "current", "total"},
        )
        _integer(progress["percent"], "progress.percent", 0, 100)
        _string(progress["message"], "progress.message", 256)
        if "phase" in progress:
            _string(progress["phase"], "progress.phase", 64, _PHASE_RE)
        if "current" in progress or "total" in progress:
            if set(progress) & {"current", "total"} != {"current", "total"}:
                raise CityWorldProtocolError("progress.current and progress.total must appear together")
            current = _integer(progress["current"], "progress.current", 0)
            total = _integer(progress["total"], "progress.total", 0)
            if current > total:
                raise CityWorldProtocolError("progress.current must not exceed progress.total")
    if "error" in message:
        _validate_error(message["error"])
    return _plain_json(message, "message")


def encode_message(message: Mapping[str, Any]) -> bytes:
    validated = validate_message(dict(message))
    payload = json.dumps(
        validated, ensure_ascii=False, allow_nan=False,
        separators=(",", ":"), sort_keys=True,
    ).encode("utf-8")
    if len(payload) > MAX_WIRE_BYTES:
        raise CityWorldProtocolError(
            f"message exceeds the {MAX_WIRE_BYTES}-byte application limit"
        )
    return payload


def decode_message(payload: bytes) -> dict[str, Any]:
    if len(payload) > MAX_WIRE_BYTES:
        raise CityWorldProtocolError(
            f"message exceeds the {MAX_WIRE_BYTES}-byte application limit"
        )
    try:
        value = json.loads(payload.decode("utf-8"))
    except UnicodeDecodeError as exc:
        raise CityWorldProtocolError("message is not UTF-8") from exc
    except json.JSONDecodeError as exc:
        raise CityWorldProtocolError(f"message is not valid JSON: {exc}") from exc
    return validate_message(value)


def validate_status_transition(previous: str | None, following: str) -> None:
    allowed = STATUS_TRANSITIONS.get(previous)
    if allowed is None or following not in allowed:
        raise CityWorldProtocolError(
            f"invalid City World status transition: {previous!r} -> {following!r}"
        )
