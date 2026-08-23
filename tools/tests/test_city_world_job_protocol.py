from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from tools.remote_operation.city_world import protocol


HASH_A = "a" * 64
HASH_B = "b" * 64


def request() -> dict:
    return {
        "schema_version": 1,
        "selection": {
            "center": {"latitude": 35.103, "longitude": 138.86},
            "half_extent_m": {"north_south": 500, "east_west": 500},
        },
        "profile": "visual-physics-v1",
        "year": "latest",
        "options": {"building_physics_level": 3},
    }


def capability(available: bool = True, lod: int = 2) -> dict:
    return {
        "dataset_status": "available" if available else "not_available",
        "generation_status": "candidate" if available else "scoped_out",
        "max_lod": lod if available else None,
        "source_file_count": 1 if available else 0,
        "reason": None if available else "dataset is not available in the selected bbox",
    }


def inspection(*, available: bool = True) -> dict:
    req_hash = protocol.canonical_sha256(request())
    return {
        "schema_version": 1,
        "status": "available" if available else "unavailable",
        "request_sha256": req_hash,
        "bbox": {"west": 138.8545, "south": 35.0985, "east": 138.8655, "north": 35.1075},
        "catalog_snapshot": {
            "api_base_url": "https://api.plateauview.mlit.go.jp",
            "fetched_at": "2026-08-23T08:00:00Z",
            "response_sha256": HASH_A,
        },
        "municipalities": ([{
            "city_code": "22203", "city": "沼津市", "year": 2023, "spec": "3.4",
        }] if available else []),
        "capabilities": {
            "building": capability(available, 2),
            "terrain": capability(available, 1),
            "road": capability(available, 3),
            "road_markings": capability(available, 3),
            "bridge": capability(available, 3),
        },
        "source_file_count": 5 if available else 0,
        "estimated_download_bytes": 123456 if available else 0,
        "reason": None if available else "no PLATEAU CityGML covers the selected bbox",
        "warnings": [],
    }


def message(message_type: str, kind: str, **payload) -> dict:
    req = request()
    return {
        "schema_version": 1,
        "protocol": "hakoniwa.city-world-job",
        "kind": kind,
        "type": message_type,
        "job_id": "numazu-station-001",
        "sequence": 1,
        "source_host": "city-world-worker" if kind == "status" else "city-world-controller",
        "request_sha256": protocol.canonical_sha256(req),
        **payload,
    }


class CityWorldJobProtocolTest(unittest.TestCase):
    def test_schema_files_and_controlled_values_are_present(self) -> None:
        for path in (
            protocol.REQUEST_SCHEMA_PATH,
            protocol.INSPECTION_SCHEMA_PATH,
            protocol.MESSAGE_SCHEMA_PATH,
            protocol.RESULT_SCHEMA_PATH,
        ):
            self.assertIsInstance(json.loads(path.read_text(encoding="utf-8")), dict)
        schema = json.loads(protocol.MESSAGE_SCHEMA_PATH.read_text(encoding="utf-8"))
        self.assertEqual(set(schema["properties"]["type"]["enum"]), protocol.MESSAGE_TYPES)

    def test_inspection_command_round_trip(self) -> None:
        req = request()
        value = message("INSPECT_SELECTION", "command", request=req)
        self.assertEqual(protocol.decode_message(protocol.encode_message(value)), value)

    def test_request_rejects_unbounded_extent_and_execution_fields(self) -> None:
        req = request()
        req["selection"]["half_extent_m"]["east_west"] = 1001
        with self.assertRaisesRegex(protocol.CityWorldProtocolError, "east_west"):
            protocol.validate_request(req)
        req = request()
        req["shell_command"] = "arbitrary command"
        with self.assertRaisesRegex(protocol.CityWorldProtocolError, "unknown fields"):
            protocol.validate_request(req)
        req = request()
        req["options"]["building_physics_level"] = 4
        with self.assertRaisesRegex(protocol.CityWorldProtocolError, "building_physics_level"):
            protocol.validate_request(req)

    def test_message_rejects_command_path_and_hash_mismatch(self) -> None:
        value = message("INSPECT_SELECTION", "command", request=request())
        value["executable_path"] = "/tmp/program"
        with self.assertRaisesRegex(protocol.CityWorldProtocolError, "unknown fields"):
            protocol.validate_message(value)
        value = message("INSPECT_SELECTION", "command", request=request())
        value["request_sha256"] = HASH_B
        with self.assertRaisesRegex(protocol.CityWorldProtocolError, "does not match"):
            protocol.validate_message(value)

    def test_available_and_unavailable_inspections_match_status(self) -> None:
        available = inspection()
        protocol.validate_message(message(
            "SELECTION_AVAILABLE", "status", inspection=available,
        ))
        unavailable = inspection(available=False)
        protocol.validate_message(message(
            "SELECTION_UNAVAILABLE", "status", inspection=unavailable,
        ))
        with self.assertRaisesRegex(protocol.CityWorldProtocolError, "does not match"):
            protocol.validate_message(message(
                "SELECTION_AVAILABLE", "status", inspection=unavailable,
            ))

    def test_generate_requires_matching_inspection_identity(self) -> None:
        req = request()
        inspected = inspection()
        value = message(
            "GENERATE", "command", request=req,
            inspection_sha256=protocol.canonical_sha256(inspected),
        )
        protocol.validate_message(value)
        value.pop("inspection_sha256")
        with self.assertRaisesRegex(protocol.CityWorldProtocolError, "missing fields"):
            protocol.validate_message(value)

    def test_ready_result_uses_fixed_artifact_layout(self) -> None:
        inspected = inspection()
        inspection_hash = protocol.canonical_sha256(inspected)
        result = {
            "schema_version": 1,
            "job_id": "numazu-station-001",
            "request_sha256": protocol.canonical_sha256(request()),
            "inspection_sha256": inspection_hash,
            "building_physics_level": 3,
            "colliders": {
                "total": 12,
                "by_component": {"terrain": 1, "buildings": 11},
                "by_physics_class": {"P0": 3, "P1": 4, "P2": 4, "P3": 0},
            },
            "artifact_name": "city-world-numazu-station-001.zip",
            "media_type": "application/zip",
            "size_bytes": 1234,
            "sha256": HASH_B,
            "entries": {
                "visual_world": "visual/city-world.glb",
                "physics_world": "physics/city-world.xml",
                "dataset_validation": "validation/dataset-validation.json",
                "world_receipt": "receipt/city-world-receipt.json",
            },
        }
        value = message("READY", "status", inspection_sha256=inspection_hash, result=result)
        protocol.validate_message(value)
        result["entries"]["visual_world"] = "../../outside.glb"
        with self.assertRaisesRegex(protocol.CityWorldProtocolError, "fixed City World layout"):
            protocol.validate_message(value)

    def test_status_lifecycle_and_skipped_phase(self) -> None:
        previous = None
        for following in (
            "INSPECTING", "SELECTION_AVAILABLE", "ACCEPTED", "DOWNLOADING",
            "GENERATING", "VALIDATING", "READY",
        ):
            protocol.validate_status_transition(previous, following)
            previous = following
        with self.assertRaisesRegex(protocol.CityWorldProtocolError, "invalid City World"):
            protocol.validate_status_transition("INSPECTING", "GENERATING")
        protocol.validate_status_transition("DOWNLOADING", "DOWNLOADING")
        protocol.validate_status_transition("GENERATING", "GENERATING")

    def test_progress_supports_phase_and_exact_item_count(self) -> None:
        value = message(
            "GENERATING", "status", inspection_sha256=HASH_B,
            progress={
                "percent": 63,
                "phase": "texture_download",
                "current": 25,
                "total": 100,
                "message": "building textures 25/100",
            },
        )
        protocol.validate_message(value)
        value["progress"]["current"] = 101
        with self.assertRaisesRegex(protocol.CityWorldProtocolError, "must not exceed"):
            protocol.validate_message(value)


if __name__ == "__main__":
    unittest.main()
