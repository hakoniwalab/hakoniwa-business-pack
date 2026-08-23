from __future__ import annotations

import json
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from tools.remote_operation.city_world import generation, inspection, protocol, web_smoke, worker


class FakePlateauClient:
    dataset_catalog_requests = 0
    allow_not_found_requests: list[tuple[str, bool]] = []

    @staticmethod
    def bounding_box(latitude, longitude, ns_m, ew_m):
        return longitude - 0.01, latitude - 0.01, longitude + 0.01, latitude + 0.01

    @staticmethod
    def third_mesh_codes(_bbox):
        return ["52385618", "52385628"]

    @staticmethod
    def third_mesh_bounds(code):
        index = 0 if code == "52385618" else 1
        return 138.85 + index * 0.0125, 35.0916666667, 138.8625 + index * 0.0125, 35.1

    @staticmethod
    def request_dataset_catalog(_api_base_url):
        FakePlateauClient.dataset_catalog_requests += 1
        return {"citygml": [{
            "city_code": "22203", "city": "沼津市", "year": 2023,
            "spec": "3.4", "feature_types": ["bldg", "dem", "tran", "frn", "brid"],
        }]}

    @staticmethod
    def search_url(_api_base_url, feature_type, _bbox, *, mesh_level=3):
        return f"https://api.example/{feature_type}?mesh={mesh_level}"

    @staticmethod
    def request_catalog(url, *, allow_not_found=False):
        feature_type = url.split("/")[-1].split("?")[0]
        FakePlateauClient.allow_not_found_requests.append((feature_type, allow_not_found))
        return {"cities": [], "feature_type": feature_type, "optional": allow_not_found}

    @staticmethod
    def select_files(payload, feature_type, _year, *, allow_empty=False, min_lod=1):
        assert allow_empty
        if feature_type == "brid":
            return []
        lod = 2 if feature_type == "bldg" else 3
        return [{
            "city_code": "22203", "city_name": "沼津市", "year": 2023,
            "registration_year": 2024, "spec": "3.4", "code": feature_type,
            "max_lod": max(lod, min_lod), "file_size": 100,
            "url": f"https://assets.example/{feature_type}.gml",
        }]


def request() -> dict:
    return {
        "schema_version": 1,
        "selection": {
            "center": {"latitude": 35.103, "longitude": 138.86},
            "half_extent_m": {"north_south": 100, "east_west": 100},
        },
        "profile": "visual-physics-v1",
        "year": "latest",
    }


def command() -> dict:
    req = request()
    return {
        "schema_version": 1,
        "protocol": "hakoniwa.city-world-job",
        "kind": "command",
        "type": "INSPECT_SELECTION",
        "job_id": "numazu-smoke-001",
        "sequence": 1,
        "source_host": "city-world-browser",
        "request_sha256": protocol.canonical_sha256(req),
        "request": req,
    }


def generate_command(inspected: dict) -> dict:
    req = request()
    return {
        "schema_version": 1,
        "protocol": "hakoniwa.city-world-job",
        "kind": "command",
        "type": "GENERATE",
        "job_id": "numazu-smoke-001",
        "sequence": 2,
        "source_host": "city-world-browser",
        "request_sha256": protocol.canonical_sha256(req),
        "inspection_sha256": protocol.canonical_sha256(inspected),
        "request": req,
    }


def result_manifest(command_value: dict) -> dict:
    return {
        "schema_version": 1,
        "job_id": command_value["job_id"],
        "request_sha256": command_value["request_sha256"],
        "inspection_sha256": command_value["inspection_sha256"],
        "artifact_name": "city-world-numazu-smoke-001.zip",
        "media_type": "application/zip",
        "size_bytes": 1,
        "sha256": "b" * 64,
        "entries": {
            "visual_world": "visual/city-world.glb",
            "physics_world": "physics/city-world.xml",
            "dataset_validation": "validation/dataset-validation.json",
            "world_receipt": "receipt/city-world-receipt.json",
        },
    }


class CityWorldInspectionTest(unittest.TestCase):
    def test_catalog_inspection_reports_optional_bridge_without_downloading(self) -> None:
        FakePlateauClient.allow_not_found_requests = []
        result = inspection.inspect_request(
            request(),
            plateau_client=FakePlateauClient,
            fetched_at=lambda: datetime(2026, 8, 23, tzinfo=timezone.utc),
        )
        self.assertEqual(result["status"], "available")
        self.assertEqual(result["municipalities"][0]["city_code"], "22203")
        self.assertEqual(result["source_file_count"], 4)
        self.assertEqual(result["estimated_download_bytes"], 400)
        self.assertEqual(result["capabilities"]["building"]["max_lod"], 2)
        self.assertEqual(
            [mesh["code"] for mesh in result["query_meshes"]],
            ["52385618", "52385628"],
        )
        self.assertEqual(result["capabilities"]["bridge"]["dataset_status"], "not_available")
        self.assertEqual(
            FakePlateauClient.allow_not_found_requests,
            [("bldg", True), ("dem", True), ("tran", True), ("frn", True), ("brid", True)],
        )
        self.assertEqual(result["catalog_snapshot"]["fetched_at"], "2026-08-23T00:00:00Z")
        protocol.validate_inspection(result)

    def test_worker_emits_inspecting_then_available(self) -> None:
        inspected = inspection.inspect_request(
            request(), plateau_client=FakePlateauClient,
            fetched_at=lambda: datetime(2026, 8, 23, tzinfo=timezone.utc),
        )
        statuses = worker.handle_inspection_command(command(), inspector=lambda _request: inspected)
        self.assertEqual([item["type"] for item in statuses], [
            "INSPECTING", "SELECTION_AVAILABLE",
        ])
        self.assertEqual(statuses[1]["inspection"], inspected)

    def test_worker_converts_inspection_error_to_failed_status(self) -> None:
        statuses = worker.handle_inspection_command(
            command(), inspector=lambda _request: (_ for _ in ()).throw(RuntimeError("API down")),
        )
        self.assertEqual(statuses[1]["type"], "FAILED")
        self.assertEqual(statuses[1]["error"]["phase"], "inspection")

    def test_worker_rechecks_then_generates_with_live_progress_contract(self) -> None:
        inspected = inspection.inspect_request(
            request(), plateau_client=FakePlateauClient,
            fetched_at=lambda: datetime(2026, 8, 23, tzinfo=timezone.utc),
        )
        command_value = generate_command(inspected)

        def fake_generator(command_arg, inspection_arg, progress):
            self.assertEqual(inspection_arg, inspected)
            progress("DOWNLOADING", 10, "download")
            progress("GENERATING", 80, "generate")
            progress("VALIDATING", 90, "validate")
            return result_manifest(command_arg)

        emitted = []
        statuses = worker.handle_generate_command(
            command_value,
            inspection=inspected,
            inspector=lambda _request: inspected,
            generator=fake_generator,
            emit=emitted.append,
        )
        self.assertEqual([item["type"] for item in statuses], [
            "ACCEPTED", "DOWNLOADING", "GENERATING", "VALIDATING", "READY",
        ])
        self.assertEqual(emitted, statuses)

    def test_default_download_limit_accepts_dense_city_catalog_estimate(self) -> None:
        inspected = inspection.inspect_request(
            request(), plateau_client=FakePlateauClient,
            fetched_at=lambda: datetime(2026, 8, 23, tzinfo=timezone.utc),
        )
        inspected["estimated_download_bytes"] = 3 * 1024 * 1024 * 1024
        command_value = generate_command(inspected)
        generated = []

        def fake_generator(command_arg, _inspection_arg, _progress):
            generated.append(command_arg["job_id"])
            return result_manifest(command_arg)

        statuses = worker.handle_generate_command(
            command_value,
            inspection=inspected,
            inspector=lambda _request: inspected,
            generator=fake_generator,
        )
        self.assertEqual(statuses[-1]["type"], "READY")
        self.assertEqual(generated, ["numazu-smoke-001"])

    def test_worker_rejects_generate_without_prior_inspection(self) -> None:
        inspected = inspection.inspect_request(
            request(), plateau_client=FakePlateauClient,
            fetched_at=lambda: datetime(2026, 8, 23, tzinfo=timezone.utc),
        )
        statuses = worker.handle_generate_command(
            generate_command(inspected), inspection=None,
            inspector=lambda _request: inspected,
            generator=lambda *_args: self.fail("generator must not run"),
        )
        self.assertEqual([item["type"] for item in statuses], ["FAILED"])
        self.assertEqual(statuses[0]["error"]["code"], "GENERATION_FAILED")

    def test_generation_packages_the_fixed_world_layout(self) -> None:
        with tempfile.TemporaryDirectory() as raw_root:
            job_root = Path(raw_root)
            world = job_root / "build" / "world"
            world.mkdir(parents=True)
            for name, value in {
                "city-world.glb": b"glb",
                "city-world.xml": b"<mujoco/>",
                "dataset-validation.json": b"{}\n",
                "city-world-receipt.json": b"{}\n",
            }.items():
                (world / name).write_bytes(value)
            result = generation.package_world(
                job_root=job_root, job_id="numazu-smoke-001",
                request_sha256="a" * 64, inspection_sha256="b" * 64,
            )
            self.assertEqual(result["entries"]["visual_world"], "visual/city-world.glb")
            import zipfile
            with zipfile.ZipFile(job_root / "artifacts" / result["artifact_name"]) as archive:
                self.assertEqual(set(archive.namelist()), set(result["entries"].values()))

    def test_generation_manifest_uses_worker_shared_citygml_cache(self) -> None:
        with tempfile.TemporaryDirectory() as raw_root:
            root = Path(raw_root)
            manifest = generation._manifest_text(
                request(), root / "jobs" / "job-1", root / "cache" / "plateau-citygml",
            )
            self.assertIn(f"cache_dir: {(root / 'cache' / 'plateau-citygml').resolve()}", manifest)
            self.assertIn(f"build_dir: {(root / 'jobs' / 'job-1' / 'build').resolve()}", manifest)
            self.assertIn("texture_mode: embedded-if-available", manifest)

    def test_web_server_lists_and_resolves_only_generated_glb_and_zip(self) -> None:
        with tempfile.TemporaryDirectory() as raw_root:
            runtime = Path(raw_root)
            job_root = runtime / "jobs" / "numazu-smoke-001"
            world = job_root / "build" / "world"
            world.mkdir(parents=True)
            for name, value in {
                "city-world.glb": b"glb",
                "city-world.xml": b"<mujoco/>",
                "dataset-validation.json": b"{}\n",
                "city-world-receipt.json": b"{}\n",
            }.items():
                (world / name).write_bytes(value)
            result = generation.package_world(
                job_root=job_root, job_id="numazu-smoke-001",
                request_sha256="a" * 64, inspection_sha256="b" * 64,
            )
            viewer = job_root / "viewer"
            viewer.mkdir()
            (viewer / "city-world.glb").write_bytes(b"glb")
            (viewer / "city-world-colliders.glb").write_bytes(b"colliders")
            (job_root / "job.json").write_text(json.dumps({
                "request": request(),
            }), encoding="utf-8")

            jobs = web_smoke.list_generated_jobs(runtime)
            self.assertEqual([item["job_id"] for item in jobs], ["numazu-smoke-001"])
            self.assertTrue(jobs[0]["collider_available"])
            self.assertEqual(
                jobs[0]["selection"]["center"],
                request()["selection"]["center"],
            )
            self.assertEqual(
                web_smoke.resolve_generated_asset(runtime, "numazu-smoke-001", "glb"),
                (viewer / "city-world.glb").resolve(),
            )
            self.assertEqual(
                web_smoke.resolve_generated_asset(runtime, "numazu-smoke-001", "zip"),
                (job_root / "artifacts" / result["artifact_name"]).resolve(),
            )
            self.assertEqual(
                web_smoke.resolve_generated_asset(runtime, "numazu-smoke-001", "collider"),
                (viewer / "city-world-colliders.glb").resolve(),
            )
            self.assertIsNone(web_smoke.resolve_generated_asset(runtime, "../outside", "zip"))
            cache_marker = runtime / "cache" / "plateau-citygml" / "keep.txt"
            cache_marker.parent.mkdir(parents=True)
            cache_marker.write_text("keep", encoding="utf-8")
            cache_object = runtime / "cache" / "plateau-citygml" / "objects" / "key" / "source.gml"
            cache_object.parent.mkdir(parents=True)
            cache_object.write_bytes(b"citygml")
            cache_summary = web_smoke.shared_cache_summary(runtime)
            self.assertEqual(cache_summary["object_count"], 1)
            self.assertEqual(cache_summary["size_bytes"], 7)
            self.assertTrue(web_smoke.delete_generated_job(runtime, "numazu-smoke-001"))
            self.assertFalse(job_root.exists())
            self.assertTrue(cache_marker.is_file())
            self.assertFalse(web_smoke.delete_generated_job(runtime, "../outside"))

    def test_reusable_inspector_caches_nationwide_catalog(self) -> None:
        FakePlateauClient.dataset_catalog_requests = 0
        inspector_instance = inspection.PlateauSelectionInspector(FakePlateauClient)
        inspector_instance(request())
        inspector_instance(request())
        self.assertEqual(FakePlateauClient.dataset_catalog_requests, 1)

    def test_browser_pdu_contract_is_variable_length_json_on_wire_v2(self) -> None:
        config_path = ROOT / "tools" / "remote_operation" / "city_world" / "web" / "pdu-config.json"
        config = json.loads(config_path.read_text(encoding="utf-8"))
        pdu = config["robots"][0]["shm_pdu_readers"][0]
        self.assertEqual(config["robots"][0]["name"], worker.PDU_ROBOT)
        self.assertEqual(pdu["channel_id"], worker.PDU_CHANNEL_ID)
        self.assertEqual(pdu["pdu_size"], protocol.MAX_WIRE_BYTES)

    def test_leaflet_smoke_ui_exposes_selection_and_capability_panels(self) -> None:
        web_root = ROOT / "tools" / "remote_operation" / "city_world" / "web"
        html = (web_root / "index.html").read_text(encoding="utf-8")
        script = (web_root / "city-world-ui.js").read_text(encoding="utf-8")
        self.assertIn("leaflet@1.9.4", html)
        self.assertIn('id="map"', html)
        self.assertIn('id="northSouth"', html)
        self.assertIn('id="capabilities"', html)
        self.assertIn("selection-resize-handle", html)
        self.assertIn("L.rectangle", script)
        self.assertIn("resizeHandles", script)
        self.assertIn("selectionRectangle.on('mousedown'", script)
        self.assertIn("map.fitBounds(selectionBounds().pad(0.35)", script)
        self.assertIn("generatedRectangle", script)
        self.assertIn("diagnosticMeshLayer", script)
        self.assertIn("PLATEAU 3次メッシュ", script)
        self.assertIn("applyGeneratedSelection", script)
        self.assertIn("詳細は通信ログを確認してください", script)
        self.assertIn('id="generate"', html)
        self.assertIn("client.generate", script)
        self.assertIn("Generate成功", script)
        self.assertIn('id="artifact-select"', html)
        self.assertIn('id="cache-info"', html)
        self.assertIn('id="view3d"', html)
        self.assertIn('id="delete-artifact"', html)
        self.assertIn('id="viewer-visual"', html)
        self.assertIn('id="viewer-collider"', html)
        self.assertIn("changeViewerLayer", script)
        self.assertIn("0x28a86b", script)
        self.assertIn("three/addons/loaders/GLTFLoader.js", script)
        self.assertIn("city-world-colliders.glb", script)
        self.assertIn("/generated/index.json", script)
        self.assertIn("applyGeneratedSelection", script)
        self.assertIn("deleteSelectedArtifact", script)
        for component in ("building", "terrain", "road", "road_markings", "bridge"):
            self.assertIn(f"{component}:", script)


if __name__ == "__main__":
    unittest.main()
