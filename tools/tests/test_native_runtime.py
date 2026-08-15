#!/usr/bin/env python3
"""Tests for the shared Catalog/Recipe native runtime validator."""

from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


TOOLS = Path(__file__).resolve().parents[1]
ROOT = TOOLS.parent
sys.path.insert(0, str(TOOLS))

import native_runtime  # noqa: E402
import native_runtime_platforms  # noqa: E402


class FakeAdapter:
    platform_id = "linux"
    inspector_id = "elf"

    def __init__(self, missing: tuple[str, ...] = ()) -> None:
        self.missing = missing

    def inspect(self, binary, environment):
        return native_runtime_platforms.DependencyInspection(
            dependencies=self.missing,
            missing=self.missing,
        )


class NativeRuntimeTest(unittest.TestCase):
    def test_catalog_profile_and_recipe_roles_share_one_schema(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            component_root = Path(temporary) / "hakoniwa-drone-core"
            component_root.mkdir()
            (component_root / "MUJOCO_VERSION.txt").write_text(
                "7.6.5\n", encoding="utf-8"
            )
            (component_root / "NATIVE_RUNTIME_REQUIREMENTS.yaml").write_text(
                """schema_version: 1
profiles:
  public-v4.0.0:
    distribution_release: v4.0.0
    managed_runtimes:
      mujoco:
        required: true
        version_file: MUJOCO_VERSION.txt
        platforms:
          linux:
            library: vendor/mujoco/lib/libmujoco.so.{version}
          macos:
            library: vendor/mujoco/lib/libmujoco.{version}.dylib
    platforms:
      linux:
        dependency_inspector: elf
        binary_roles:
          drone_service: lnx/linux-main_hako_drone_service
          visual_state_publisher: lnx/linux-drone_visual_state_publisher
        required_libraries: [\"libOpenGL.so.0\", \"libglfw.so.3\"]
      macos:
        dependency_inspector: macho
        binary_roles:
          drone_service: mac/mac-main_hako_drone_service
          visual_state_publisher: mac/mac-drone_visual_state_publisher
        required_libraries: [\"libglfw.3.dylib\"]
""",
                encoding="utf-8",
            )
            requirement = native_runtime.load_recipe_requirement(
                ROOT / "recipes/examples/drone-fleet-single-host.yaml",
                "hakoniwa-drone-core",
            )
            contract = native_runtime.load_catalog_contract(
                ROOT / "catalog/components/hakoniwa-drone-core.yaml",
                component_root,
                requirement,
                FakeAdapter(),
            )

            self.assertEqual(requirement.profile, "public-v4.0.0")
            self.assertEqual(requirement.required_roles, ("drone_service",))
            self.assertEqual(contract.release, "v4.0.0")
            self.assertEqual(contract.managed_runtimes[0].version, "7.6.5")
            self.assertEqual(
                contract.shared_libraries, ("libOpenGL.so.0", "libglfw.so.3")
            )
            self.assertEqual(
                contract.binaries["drone_service"],
                component_root / "lnx/linux-main_hako_drone_service",
            )

    def test_common_validator_classifies_declared_and_undeclared_missing_libraries(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            binary = root / "drone"
            binary.touch()
            contract = native_runtime.NativeRuntimeContract(
                path=root / "catalog.yaml",
                source_path=None,
                release="v1",
                managed_runtimes=(),
                binaries={"drone_service": binary},
                shared_libraries=("libOpenGL.so.0",),
                dependency_inspector="elf",
            )
            adapter = FakeAdapter(("libOpenGL.so.0", "libnew.so.1"))

            checks = native_runtime.validate_contract(
                contract, adapter, ("drone_service",), {"PATH": "/usr/bin"}
            )

            failure = checks[-1]
            self.assertFalse(failure.ok)
            self.assertIn("libOpenGL.so.0 (declared", failure.detail)
            self.assertIn("libnew.so.1 (not declared", failure.detail)

    def test_catalog_and_component_owned_contract_drift_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            component_root = Path(temporary)
            (component_root / "MUJOCO_VERSION.txt").write_text(
                "3.9.0\n", encoding="utf-8"
            )
            source = component_root / "NATIVE_RUNTIME_REQUIREMENTS.yaml"
            source.write_text(
                """schema_version: 1
profiles:
  public-v4.0.0:
    distribution_release: stale-release
    managed_runtimes: {}
    platforms: {}
""",
                encoding="utf-8",
            )
            requirement = native_runtime.load_recipe_requirement(
                ROOT / "recipes/examples/drone-fleet-single-host.yaml",
                "hakoniwa-drone-core",
            )

            with self.assertRaisesRegex(
                native_runtime.NativeRuntimeError,
                "differs from its component-owned source contract",
            ):
                native_runtime.load_catalog_contract(
                    ROOT / "catalog/components/hakoniwa-drone-core.yaml",
                    component_root,
                    requirement,
                    FakeAdapter(),
                )

    def test_elf_adapter_reports_ldd_not_found_entries(self) -> None:
        completed = subprocess.CompletedProcess(
            args=["/usr/bin/ldd", "/tmp/drone"],
            returncode=0,
            stdout=(
                "libmujoco.so.3.9.0 => /tmp/libmujoco.so.3.9.0 (0x1)\n"
                "libOpenGL.so.0 => not found\n"
            ),
            stderr="",
        )
        with mock.patch.object(
            native_runtime_platforms.shutil, "which", return_value="/usr/bin/ldd"
        ), mock.patch.object(
            native_runtime_platforms.subprocess, "run", return_value=completed
        ):
            inspection = native_runtime_platforms.ElfDependencyAdapter().inspect(
                Path("/tmp/drone"), {"PATH": "/usr/bin"}
            )

        self.assertEqual(inspection.missing, ("libOpenGL.so.0",))

    def test_macho_adapter_reports_missing_absolute_glfw_before_launch(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            binary = root / "mac-drone"
            binary.touch()
            mujoco = root / "libmujoco.3.9.0.dylib"
            mujoco.touch()
            missing_glfw = root / "missing-homebrew/lib/libglfw.3.dylib"
            linked = subprocess.CompletedProcess(
                args=["otool", "-L", str(binary)],
                returncode=0,
                stdout=(
                    f"{binary}:\n"
                    f"\t{missing_glfw} "
                    "(compatibility version 3.0.0, current version 3.4.0)\n"
                    "\t@rpath/libmujoco.3.9.0.dylib "
                    "(compatibility version 0.0.0, current version 3.9.0)\n"
                    "\t/usr/lib/libSystem.B.dylib "
                    "(compatibility version 1.0.0, current version 1.0.0)\n"
                ),
                stderr="",
            )
            load_commands = subprocess.CompletedProcess(
                args=["otool", "-l", str(binary)],
                returncode=0,
                stdout=f"cmd LC_RPATH\npath {root} (offset 12)\n",
                stderr="",
            )
            with mock.patch.object(
                native_runtime_platforms.shutil, "which", return_value="/usr/bin/otool"
            ), mock.patch.object(
                native_runtime_platforms.subprocess,
                "run",
                side_effect=[linked, load_commands],
            ):
                inspection = native_runtime_platforms.MachODependencyAdapter().inspect(
                    binary, {"PATH": "/usr/bin"}
                )

            self.assertEqual(
                inspection.missing,
                (str(missing_glfw),),
            )

    def test_platform_adapter_selection_is_outside_recipe_code(self) -> None:
        self.assertEqual(
            native_runtime_platforms.adapter_for("Linux").inspector_id, "elf"
        )
        self.assertEqual(
            native_runtime_platforms.adapter_for("Darwin").inspector_id, "macho"
        )


if __name__ == "__main__":
    unittest.main()
