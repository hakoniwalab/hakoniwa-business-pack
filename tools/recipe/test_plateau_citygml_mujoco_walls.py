#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
import hashlib
import json
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
    def test_configure_creates_recipe_venv_and_installs_requirements(self):
        completed = mock.Mock(returncode=0)
        managed_python = Path("/recipe/python/bin/python")
        with (
            mock.patch.object(recipe, "python_requirements_file", return_value=Path(__file__)),
            mock.patch.object(recipe, "recipe_python", return_value=managed_python),
            mock.patch.object(recipe, "python_environment", return_value=Path("/recipe/python")),
            mock.patch.object(Path, "is_file", return_value=False),
            mock.patch.object(Path, "mkdir"),
            mock.patch.object(recipe.subprocess, "run", return_value=completed) as run,
        ):
            result = recipe.install_python_requirements()
        self.assertEqual(result, managed_python)
        self.assertEqual(
            [call.args[0] for call in run.call_args_list],
            [
                [sys.executable, "-m", "venv", "/recipe/python"],
                [str(managed_python), "-m", "pip", "install", "-r", str(Path(__file__))],
            ],
        )

    def test_python_requirement_install_failure_is_actionable(self):
        completed = mock.Mock(returncode=7)
        with (
            mock.patch.object(recipe, "python_requirements_file", return_value=Path(__file__)),
            mock.patch.object(recipe, "recipe_python", return_value=Path(__file__)),
            mock.patch.object(recipe.subprocess, "run", return_value=completed),
            self.assertRaisesRegex(recipe.RecipeError, "dependency installation failed"),
        ):
            recipe.install_python_requirements()

    def test_selection_coverage_rejects_an_empty_center(self):
        with tempfile.TemporaryDirectory() as temporary:
            selection = Path(temporary) / "selection.json"
            selection.write_text(json.dumps({"polygons": [
                {"vertices": [[490, 490], [495, 490], [495, 495]]},
                {"vertices": [[-490, -490], [-495, -490], [-495, -495]]},
            ]}), encoding="utf-8")
            with self.assertRaisesRegex(recipe.RecipeError, "does not cover"):
                recipe.validate_selection_coverage(selection, {
                    "minimum_buildings": 2,
                    "center_half_extent_m": 100,
                    "minimum_center_buildings": 1,
                })

    def test_selection_coverage_accepts_dense_centered_data(self):
        with tempfile.TemporaryDirectory() as temporary:
            selection = Path(temporary) / "selection.json"
            selection.write_text(json.dumps({"polygons": [
                {"vertices": [[-5, -5], [5, -5], [5, 5]]},
                {"vertices": [[490, 490], [495, 490], [495, 495]]},
            ]}), encoding="utf-8")
            result = recipe.validate_selection_coverage(selection, {
                "minimum_buildings": 2,
                "center_half_extent_m": 100,
                "minimum_center_buildings": 1,
            })
            self.assertEqual(result, {"buildings": 2, "center_buildings": 1})

    def test_glb_contract_checks_hash_origin_axes_and_geometry(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            glb = root / "city.glb"
            glb.write_bytes(b"glTF-test")
            receipt = root / "city-glb-receipt.json"
            receipt.write_text(json.dumps({
                "bytes": glb.stat().st_size,
                "sha256": hashlib.sha256(glb.read_bytes()).hexdigest(),
                "triangles": 2,
                "coordinate_system": {
                    "glb": "X=East,Y=Up,Z=-North",
                    "origin": {"latitude": 35.6625, "longitude": 139.70625},
                },
            }), encoding="utf-8")
            result = recipe.validate_glb_contract(
                glb, receipt, {"latitude": 35.6625, "longitude": 139.70625}
            )
            self.assertEqual(result["triangles"], 2)

    def test_glb_contract_rejects_wrong_origin(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            glb = root / "city.glb"
            glb.write_bytes(b"glTF-test")
            receipt = root / "city-glb-receipt.json"
            receipt.write_text(json.dumps({
                "bytes": glb.stat().st_size,
                "sha256": hashlib.sha256(glb.read_bytes()).hexdigest(),
                "triangles": 2,
                "coordinate_system": {
                    "glb": "X=East,Y=Up,Z=-North",
                    "origin": {"latitude": 35.0, "longitude": 139.70625},
                },
            }), encoding="utf-8")
            with self.assertRaisesRegex(recipe.RecipeError, "origin mismatch"):
                recipe.validate_glb_contract(
                    glb, receipt, {"latitude": 35.6625, "longitude": 139.70625}
                )

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
