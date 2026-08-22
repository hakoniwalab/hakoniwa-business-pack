from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


SCRIPT = Path(__file__).with_name("plateau_city_world_six_regions.py")
SPEC = importlib.util.spec_from_file_location("plateau_city_world_six_regions", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
matrix = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = matrix
SPEC.loader.exec_module(matrix)


class PlateauCityWorldSixRegionsTest(unittest.TestCase):
    def test_recipe_declares_six_fixed_200_meter_regions(self):
        contract, regions = matrix.load_matrix()
        self.assertEqual(
            [region.id for region in regions],
            [
                "tokyo-shibuya",
                "shizuoka-numazu",
                "hokkaido-sapporo",
                "ishikawa-kanazawa",
                "hiroshima-lod3-bridge",
                "okinawa-naha",
            ],
        )
        self.assertEqual(
            contract["selection"]["half_extent_m"],
            {"north_south": 100, "east_west": 100},
        )

    def test_hiroshima_enables_lod3_bridge_visualization_and_collision_source(self):
        _contract, regions = matrix.load_matrix()
        hiroshima = next(region for region in regions if region.id == "hiroshima-lod3-bridge")
        self.assertEqual(
            (hiroshima.latitude, hiroshima.longitude),
            (34.39870318724743, 132.47669631395575),
        )
        self.assertEqual(dict(hiroshima.feature_type_overrides), {"brid": True})
        self.assertFalse(any("not generated" in value for value in hiroshima.known_limitations))

    def test_manifest_changes_only_region_specific_identity_and_center(self):
        contract, regions = matrix.load_matrix()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            paths = matrix.RegionPaths(
                root, root / "config", root / "build", root / "artifacts", root / "validation"
            )
            text = matrix.manifest_text(contract, regions[0], paths)
        self.assertIn("latitude: 35.6595", text)
        self.assertIn("longitude: 139.7005", text)
        self.assertIn("north_south: 100", text)
        self.assertIn("east_west: 100", text)
        self.assertIn("frn: true", text)
        self.assertIn("brid: false", text)
        self.assertIn("texture_mode: flat", text)
        self.assertIn("roof_collision_thickness_m: 0.02", text)
        self.assertIn("bridge_collision_thickness_m: 0.02", text)
        self.assertIn("bridge_max_surface_slope_deg: 60", text)
        self.assertIn("name: plateau-tokyo-shibuya-city-world-smoke", text)

        hiroshima = next(region for region in regions if region.id == "hiroshima-lod3-bridge")
        bridge_text = matrix.manifest_text(contract, hiroshima, paths)
        self.assertIn("brid: true", bridge_text)

    def test_reusable_result_requires_matching_profile_and_artifact_hashes(self):
        contract, regions = matrix.load_matrix()
        region = regions[0]
        with tempfile.TemporaryDirectory() as temporary, mock.patch.object(
            matrix, "work_root", return_value=Path(temporary)
        ):
            paths = matrix.region_paths(region.id)
            paths.validation.mkdir(parents=True)
            paths.artifacts.mkdir(parents=True)
            artifact = paths.artifacts / "world.glb"
            artifact.write_bytes(b"glTF")
            digest = hashlib.sha256(artifact.read_bytes()).hexdigest()
            (paths.validation / "result.json").write_text(json.dumps({
                "status": "done",
                "profile_identity": matrix.profile_identity(contract, region),
                "artifacts": {
                    "glb": {"path": str(artifact), "bytes": 4, "sha256": digest}
                },
            }), encoding="utf-8")
            self.assertTrue(matrix.reusable_result(contract, region))
            artifact.write_bytes(b"changed")
            self.assertFalse(matrix.reusable_result(contract, region))

    def test_partial_summary_keeps_physics_out_of_scope(self):
        contract, regions = matrix.load_matrix()
        with tempfile.TemporaryDirectory() as temporary, mock.patch.object(
            matrix, "work_root", return_value=Path(temporary)
        ):
            self.assertEqual(matrix.summarize(contract, regions), 2)
            payload = json.loads(
                (Path(temporary) / "summary" / "capability-matrix.json").read_text(
                    encoding="utf-8"
                )
            )
        self.assertEqual(payload["status"], "partial")
        self.assertEqual(payload["scope"]["physics_simulation"], "not_evaluated")
        self.assertEqual(payload["total_regions"], 6)


if __name__ == "__main__":
    unittest.main()
