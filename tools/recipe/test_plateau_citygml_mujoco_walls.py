#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

SCRIPT = Path(__file__).with_name("plateau_citygml_mujoco_walls.py")
SPEC = importlib.util.spec_from_file_location("plateau_citygml_mujoco_walls_recipe", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
recipe = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = recipe
SPEC.loader.exec_module(recipe)


class PlateauCityGmlMujocoWallsTest(unittest.TestCase):
    def test_reads_map_viewer_origin_instead_of_drone_location(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            ui = root / "src" / "client" / "src" / "ui.js"
            ui.parent.mkdir(parents=True)
            ui.write_text(
                "let ORIGIN_LAT = 35.6625;\nlet ORIGIN_LON = 139.70625;\n",
                encoding="utf-8",
            )
            self.assertEqual(
                recipe.read_map_origin(root),
                {"latitude": 35.6625, "longitude": 139.70625},
            )

    def test_building_geom_comparison_ignores_non_building_drone_geoms(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            expected = root / "expected.xml"
            actual = root / "actual.xml"
            expected.write_text(
                '<mujoco><worldbody><geom name="drone" type="sphere" size="1"/>'
                '<geom name="geom_bldg_a_edge0" type="box" size="0.05 2 3" '
                'pos="1 2 3" euler="0 0 45" rgba=".82 .82 .86 1" '
                'contype="1" conaffinity="0"/></worldbody></mujoco>',
                encoding="utf-8",
            )
            actual.write_text(
                '<mujoco><worldbody><geom name="geom_bldg_a_edge0" type="box" '
                'size="0.0500001 2 3" pos="1 2 3" euler="0 0 45" '
                'rgba=".82 .82 .86 1" contype="1" conaffinity="0"/>'
                '</worldbody></mujoco>',
                encoding="utf-8",
            )
            result = recipe.compare_building_geoms(actual, expected, 1e-5)
            self.assertEqual(result["status"], "MATCHED")
            self.assertEqual(result["expected_geom_count"], 1)

    def test_building_geom_comparison_detects_geometry_change(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            expected = root / "expected.xml"
            actual = root / "actual.xml"
            template = (
                '<mujoco><worldbody><geom name="geom_bldg_a" type="box" '
                'size="1 2 3" pos="{pos}" euler="0 0 0" rgba="1 1 1 1" '
                'contype="1" conaffinity="0"/></worldbody></mujoco>'
            )
            expected.write_text(template.format(pos="0 0 0"), encoding="utf-8")
            actual.write_text(template.format(pos="0.1 0 0"), encoding="utf-8")
            result = recipe.compare_building_geoms(actual, expected, 1e-5)
            self.assertEqual(result["status"], "MISMATCHED")
            self.assertEqual(result["mismatch_count"], 1)

    def test_legacy_pipeline_is_materialized_from_one_pinned_revision(self):
        with tempfile.TemporaryDirectory() as temporary:
            destination = Path(temporary)
            with mock.patch.object(recipe, "_git_blob", side_effect=[b"lod1", b"walls", b"mjcf"]) as blob:
                scripts = recipe._materialize_legacy_pipeline(
                    Path("/envsim"),
                    "fixed-revision",
                    {
                        "lod1_extract": "legacy/gml_lod1_extract.py",
                        "wall_convert": "legacy/gml2obb.py",
                        "mjcf_convert": "legacy/obb2mjcf.py",
                    },
                    destination,
                )
            self.assertEqual(scripts["lod1_extract"].read_bytes(), b"lod1")
            self.assertEqual(scripts["wall_convert"].read_bytes(), b"walls")
            self.assertEqual(scripts["mjcf_convert"].read_bytes(), b"mjcf")
            self.assertEqual(
                [call.args[1] for call in blob.call_args_list],
                ["fixed-revision", "fixed-revision", "fixed-revision"],
            )


if __name__ == "__main__":
    unittest.main()
