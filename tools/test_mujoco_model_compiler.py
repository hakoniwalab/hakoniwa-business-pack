#!/usr/bin/env python3
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
import mujoco_model_compiler as compiler


class MujocoModelCompilerTest(unittest.TestCase):
    def test_rejects_missing_runtime_library(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            with self.assertRaisesRegex(
                compiler.MujocoCompileError, "shared library was not found"
            ):
                compiler.find_mujoco_library(Path(temporary))

    def test_compiles_and_reload_validates_mjb_with_adjacent_drone_runtime(self) -> None:
        workspace = Path(__file__).resolve().parents[2]
        drone_root = workspace / "hakoniwa-drone-pro"
        if not drone_root.is_dir():
            drone_root = workspace / "hakoniwa-drone-core"
        try:
            library = compiler.find_mujoco_library(drone_root)
        except compiler.MujocoCompileError:
            self.skipTest("adjacent MuJoCo runtime is not installed")

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            xml = root / "model.xml"
            mjb = root / "model.mjb"
            xml.write_text(
                '<mujoco model="compiler-test"><worldbody>'
                '<body name="drone"><freejoint/>'
                '<geom type="box" size="0.1 0.2 0.3" mass="1"/>'
                '</body></worldbody></mujoco>\n',
                encoding="utf-8",
            )
            receipt = compiler.compile_mujoco_xml(xml, mjb, library)

            self.assertTrue(mjb.is_file())
            self.assertGreater(mjb.stat().st_size, 0)
            self.assertEqual(receipt["reload_validation"], "passed")
            self.assertEqual(receipt["mujoco_version"], "3.9.0")
            self.assertEqual(receipt["source_xml_sha256"], compiler._sha256(xml))
            self.assertEqual(receipt["output_mjb_sha256"], compiler._sha256(mjb))


if __name__ == "__main__":
    unittest.main()
