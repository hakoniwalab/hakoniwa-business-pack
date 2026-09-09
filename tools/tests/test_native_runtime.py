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


def write_pe_image(
    path: Path, imports: tuple[str, ...], magic: int = 0x20B
) -> None:
    """Write the smallest PE image that carries a readable import directory.

    Only the fields the adapter reads are filled in, so this is a fixture
    rather than a loadable executable. Building it here keeps the adapter's
    tests running on every runner instead of only the Windows one.
    """
    optional_size = 240 if magic == 0x20B else 224
    directories_at = 112 if magic == 0x20B else 96
    section_rva = 0x1000
    section_offset = 0x400

    descriptors = bytearray()
    names = bytearray()
    names_rva = section_rva + (len(imports) + 1) * 20
    for index, name in enumerate(imports):
        name_rva = names_rva + len(names)
        names += name.encode("ascii") + b"\0"
        descriptor = bytearray(20)
        descriptor[0:4] = (section_rva + 0x800 + index * 4).to_bytes(4, "little")
        descriptor[12:16] = name_rva.to_bytes(4, "little")
        descriptor[16:20] = (section_rva + 0x900 + index * 4).to_bytes(4, "little")
        descriptors += descriptor
    descriptors += bytes(20)
    section = bytes(descriptors + names)

    dos = bytearray(0x40)
    dos[0:2] = b"MZ"
    dos[0x3C:0x40] = (0x40).to_bytes(4, "little")

    coff = bytearray(20)
    coff[0:2] = (0x8664 if magic == 0x20B else 0x14C).to_bytes(2, "little")
    coff[2:4] = (1).to_bytes(2, "little")
    coff[16:18] = optional_size.to_bytes(2, "little")

    optional = bytearray(optional_size)
    optional[0:2] = magic.to_bytes(2, "little")
    optional[directories_at - 4 : directories_at] = (16).to_bytes(4, "little")
    # Data directory entry 1 is the import table; entry 0 is the export table.
    imports_at = directories_at + 8
    optional[imports_at : imports_at + 4] = section_rva.to_bytes(4, "little")
    optional[imports_at + 4 : imports_at + 8] = len(section).to_bytes(4, "little")

    header = bytearray(40)
    header[0:8] = b".idata\0\0"
    header[8:12] = len(section).to_bytes(4, "little")
    header[12:16] = section_rva.to_bytes(4, "little")
    header[16:20] = len(section).to_bytes(4, "little")
    header[20:24] = section_offset.to_bytes(4, "little")

    image = bytearray(section_offset)
    image[0:0x40] = dos
    image[0x40:0x44] = b"PE\0\0"
    image[0x44 : 0x44 + 20] = coff
    image[0x58 : 0x58 + optional_size] = optional
    table = 0x58 + optional_size
    image[table : table + 40] = header
    path.write_bytes(bytes(image) + section)


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

    def test_pe_adapter_resolves_imports_by_policy_not_by_loader(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            beside = root / "bin"
            beside.mkdir()
            on_path = root / "runtime"
            on_path.mkdir()
            working_directory = root / "cwd"
            working_directory.mkdir()

            binary = beside / "win-main_hako_drone_service.exe"
            write_pe_image(
                binary,
                (
                    "KERNEL32.dll",
                    "api-ms-win-crt-runtime-l1-1-0.dll",
                    "mujoco.dll",
                    "VCRUNTIME140.dll",
                    "glfw3.dll",
                    "delayed_only.dll",
                ),
            )
            (beside / "mujoco.dll").touch()
            (on_path / "VCRUNTIME140.dll").touch()
            # The loader would find this; this adapter deliberately does not.
            (working_directory / "glfw3.dll").touch()

            inspection = native_runtime_platforms.PeDependencyAdapter().inspect(
                binary, {"PATH": str(on_path)}
            )

        self.assertEqual(
            inspection.dependencies,
            (
                "KERNEL32.dll",
                "api-ms-win-crt-runtime-l1-1-0.dll",
                "mujoco.dll",
                "VCRUNTIME140.dll",
                "glfw3.dll",
                "delayed_only.dll",
            ),
        )
        # KERNEL32 by the system approximation, api-ms-win-* by prefix,
        # mujoco.dll from the binary's own directory, VCRUNTIME140.dll from
        # PATH. glfw3.dll sits only in the working directory, which is not
        # searched, and nothing supplies delayed_only.dll at all.
        self.assertEqual(inspection.missing, ("glfw3.dll", "delayed_only.dll"))

    def test_pe_adapter_reads_a_32_bit_optional_header(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            binary = Path(temporary) / "win-32bit.exe"
            write_pe_image(binary, ("mujoco.dll",), magic=0x10B)
            inspection = native_runtime_platforms.PeDependencyAdapter().inspect(
                binary, {"PATH": ""}
            )
        self.assertEqual(inspection.dependencies, ("mujoco.dll",))
        self.assertEqual(inspection.missing, ("mujoco.dll",))

    def test_pe_adapter_refuses_a_file_that_is_not_a_pe_image(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            binary = Path(temporary) / "not-a-pe.exe"
            binary.write_bytes(b"#!/bin/sh\necho hello\n")
            with self.assertRaises(
                native_runtime_platforms.DependencyInspectionError
            ):
                native_runtime_platforms.PeDependencyAdapter().inspect(binary, {})

    def test_platform_adapter_selection_is_outside_recipe_code(self) -> None:
        self.assertEqual(
            native_runtime_platforms.adapter_for("Linux").inspector_id, "elf"
        )
        self.assertEqual(
            native_runtime_platforms.adapter_for("Darwin").inspector_id, "macho"
        )
        self.assertEqual(
            native_runtime_platforms.adapter_for("Windows").inspector_id, "pe"
        )


if __name__ == "__main__":
    unittest.main()
