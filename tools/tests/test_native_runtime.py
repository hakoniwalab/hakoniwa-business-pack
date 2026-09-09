#!/usr/bin/env python3
"""Tests for the shared Catalog/Recipe native runtime validator."""

from __future__ import annotations

import dataclasses
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Sequence
from unittest import mock


TOOLS = Path(__file__).resolve().parents[1]
ROOT = TOOLS.parent
sys.path.insert(0, str(TOOLS))

import native_runtime  # noqa: E402
import native_runtime_platforms  # noqa: E402


@dataclasses.dataclass(frozen=True)
class PeLayout:
    """Where the interesting fields ended up, so a test can corrupt one."""

    path: Path
    optional_at: int
    optional_size: int
    directories_at: int
    section_table_at: int
    text_offset: int
    idata_rva: int
    idata_offset: int
    idata_size: int
    first_name_rva: int
    header_padding_rva: int


def write_pe_image(
    path: Path,
    imports: Sequence[str],
    magic: int = 0x20B,
    *,
    idata_rva: int = 0x3000,
    idata_offset: int = 0xA00,
    decoys: Sequence[str] = (),
    names_in_header_padding: bool = False,
    virtual_tail: int = 0x400,
) -> PeLayout:
    """Write the smallest PE image that carries a readable import directory.

    Only the fields the adapter reads are filled in, so this is a fixture
    rather than a loadable executable. Building it here keeps the adapter's
    tests running on every runner instead of only the Windows one.

    Deliberately not a fixed layout. There are two sections, the import section
    is neither the first section nor at the conventional RVA, and its file
    offset is not the RVA minus a round constant, so a parser that hard-codes
    the relationship between the two fails these tests. `decoys` writes strings
    that look like DLL names into a `.text` section that nothing points at, so
    a parser that scans the file for such strings fails as well. `virtual_tail`
    makes the import section larger in memory than on disk, which is the region
    an RVA must not be translated into.
    """
    optional_size = 240 if magic == 0x20B else 224
    directories_at = 112 if magic == 0x20B else 96
    section_table_at = 0x58 + optional_size
    size_of_headers = 0x200
    text_rva = 0x1000
    text_offset = 0x200
    text = b"".join(name.encode("ascii") + b"\0" for name in decoys)
    text = text.ljust(0x200, b"\0")

    header_padding_rva = section_table_at + 2 * 40 + 8

    descriptors = bytearray()
    names = bytearray()
    names_rva = idata_rva + (len(imports) + 1) * 20
    first_name_rva = 0
    for index, name in enumerate(imports):
        encoded = name.encode("ascii") + b"\0"
        if names_in_header_padding:
            name_rva = header_padding_rva + len(names)
        else:
            name_rva = names_rva + len(names)
        names += encoded
        if index == 0:
            first_name_rva = name_rva
        descriptor = bytearray(20)
        descriptor[0:4] = (idata_rva + 0x600 + index * 8).to_bytes(4, "little")
        descriptor[12:16] = name_rva.to_bytes(4, "little")
        descriptor[16:20] = (idata_rva + 0x700 + index * 8).to_bytes(4, "little")
        descriptors += descriptor
    descriptors += bytes(20)
    idata = bytes(descriptors) + (b"" if names_in_header_padding else bytes(names))

    dos = bytearray(0x40)
    dos[0:2] = b"MZ"
    dos[0x3C:0x40] = (0x40).to_bytes(4, "little")

    coff = bytearray(20)
    coff[0:2] = (0x8664 if magic == 0x20B else 0x14C).to_bytes(2, "little")
    coff[2:4] = (2).to_bytes(2, "little")
    coff[16:18] = optional_size.to_bytes(2, "little")

    optional = bytearray(optional_size)
    optional[0:2] = magic.to_bytes(2, "little")
    optional[60:64] = size_of_headers.to_bytes(4, "little")
    optional[directories_at - 4 : directories_at] = (16).to_bytes(4, "little")
    # Data directory entry 1 is the import table; entry 0 is the export table.
    imports_at = directories_at + 8
    optional[imports_at : imports_at + 4] = idata_rva.to_bytes(4, "little")
    optional[imports_at + 4 : imports_at + 8] = len(idata).to_bytes(4, "little")

    def section(name: bytes, virtual_size: int, rva: int, raw: int, offset: int) -> bytes:
        header = bytearray(40)
        header[0:8] = name.ljust(8, b"\0")
        header[8:12] = virtual_size.to_bytes(4, "little")
        header[12:16] = rva.to_bytes(4, "little")
        header[16:20] = raw.to_bytes(4, "little")
        header[20:24] = offset.to_bytes(4, "little")
        return bytes(header)

    headers = bytearray(size_of_headers)
    headers[0:0x40] = dos
    headers[0x40:0x44] = b"PE\0\0"
    headers[0x44 : 0x44 + 20] = coff
    headers[0x58 : 0x58 + optional_size] = optional
    headers[section_table_at : section_table_at + 40] = section(
        b".text", len(text), text_rva, len(text), text_offset
    )
    headers[section_table_at + 40 : section_table_at + 80] = section(
        b".idata", len(idata) + virtual_tail, idata_rva, len(idata), idata_offset
    )
    if names_in_header_padding:
        headers[header_padding_rva : header_padding_rva + len(names)] = names

    image = bytearray(idata_offset)
    image[0:size_of_headers] = headers
    image[text_offset : text_offset + len(text)] = text
    path.write_bytes(bytes(image) + idata)
    return PeLayout(
        path=path,
        optional_at=0x58,
        optional_size=optional_size,
        directories_at=0x58 + directories_at,
        section_table_at=section_table_at,
        text_offset=text_offset,
        idata_rva=idata_rva,
        idata_offset=idata_offset,
        idata_size=len(idata),
        first_name_rva=first_name_rva,
        header_padding_rva=header_padding_rva,
    )


def patch_u32(path: Path, offset: int, value: int) -> None:
    data = bytearray(path.read_bytes())
    data[offset : offset + 4] = value.to_bytes(4, "little")
    path.write_bytes(bytes(data))


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
                    "unshipped_helper.dll",
                ),
                decoys=("decoy_never_imported.dll", "another_decoy.dll"),
            )
            (beside / "mujoco.dll").touch()
            (on_path / "VCRUNTIME140.dll").touch()
            # The loader would find this one; this adapter deliberately does
            # not, so the test runs from that directory to prove it.
            (working_directory / "glfw3.dll").touch()

            previous = os.getcwd()
            os.chdir(working_directory)
            try:
                inspection = native_runtime_platforms.PeDependencyAdapter().inspect(
                    binary, {"PATH": str(on_path)}
                )
            finally:
                os.chdir(previous)

        self.assertEqual(
            inspection.dependencies,
            (
                "KERNEL32.dll",
                "api-ms-win-crt-runtime-l1-1-0.dll",
                "mujoco.dll",
                "VCRUNTIME140.dll",
                "glfw3.dll",
                "unshipped_helper.dll",
            ),
        )
        # KERNEL32 by the system approximation, api-ms-win-* by prefix,
        # mujoco.dll from the binary's own directory, VCRUNTIME140.dll from
        # PATH. glfw3.dll exists only in the working directory, which is not
        # searched, and nothing supplies unshipped_helper.dll at all.
        self.assertEqual(inspection.missing, ("glfw3.dll", "unshipped_helper.dll"))

    def test_pe_adapter_reads_a_32_bit_optional_header(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            binary = Path(temporary) / "win-32bit.exe"
            write_pe_image(binary, ("mujoco.dll",), magic=0x10B)
            inspection = native_runtime_platforms.PeDependencyAdapter().inspect(
                binary, {"PATH": ""}
            )
        self.assertEqual(inspection.dependencies, ("mujoco.dll",))
        self.assertEqual(inspection.missing, ("mujoco.dll",))

    def test_pe_adapter_reads_a_name_stored_in_header_padding(self) -> None:
        # Headers are mapped at RVA 0, so a name can legally live there.
        # dumpbin reads those, and refusing them would be a false negative.
        with tempfile.TemporaryDirectory() as temporary:
            binary = Path(temporary) / "win-header-names.exe"
            write_pe_image(binary, ("mujoco.dll",), names_in_header_padding=True)
            inspection = native_runtime_platforms.PeDependencyAdapter().inspect(
                binary, {"PATH": ""}
            )
        self.assertEqual(inspection.dependencies, ("mujoco.dll",))

    def test_pe_adapter_reports_a_pathname_import_by_its_bare_name(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            binary = root / "win-pathname.exe"
            write_pe_image(binary, ("C:\\vendor\\mujoco.dll",))
            (root / "mujoco.dll").touch()
            inspection = native_runtime_platforms.PeDependencyAdapter().inspect(
                binary, {"PATH": ""}
            )
        # The contract compares bare names, so resolution has to use the same
        # identity the caller will compare rather than the spelling on disk.
        self.assertEqual(inspection.dependencies, ("mujoco.dll",))
        self.assertEqual(inspection.missing, ())

    def test_pe_adapter_reads_path_from_a_windows_cased_environment_key(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            binary = root / "bin" / "win-cased.exe"
            binary.parent.mkdir()
            on_path = root / "runtime"
            on_path.mkdir()
            write_pe_image(binary, ("mujoco.dll",))
            (on_path / "mujoco.dll").touch()
            inspection = native_runtime_platforms.PeDependencyAdapter().inspect(
                binary, {"Path": str(on_path)}
            )
        self.assertEqual(inspection.missing, ())

    def test_pe_adapter_refuses_malformed_images_rather_than_summarising_them(
        self,
    ) -> None:
        # Every case here would otherwise be reported by the caller as "all
        # dependencies resolved", which is the one answer a validator must
        # never invent.
        adapter = native_runtime_platforms.PeDependencyAdapter()
        error = native_runtime_platforms.DependencyInspectionError

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)

            not_pe = root / "not-a-pe.exe"
            not_pe.write_bytes(b"#!/bin/sh\necho hello\n")
            with self.assertRaises(error):
                adapter.inspect(not_pe, {})

            truncated = root / "truncated.exe"
            layout = write_pe_image(truncated, ("mujoco.dll",))
            truncated.write_bytes(truncated.read_bytes()[: layout.optional_at + 2])
            with self.assertRaises(error):
                adapter.inspect(truncated, {})

            small_optional = root / "small-optional.exe"
            layout = write_pe_image(small_optional, ("mujoco.dll",))
            data = bytearray(small_optional.read_bytes())
            data[0x44 + 16 : 0x44 + 18] = (28).to_bytes(2, "little")
            small_optional.write_bytes(bytes(data))
            with self.assertRaises(error):
                adapter.inspect(small_optional, {})

            virtual_tail = root / "virtual-tail.exe"
            layout = write_pe_image(virtual_tail, ("mujoco.dll",))
            # Point the directory into the part of the section that exists in
            # memory but not on disk. Reading it would return whatever bytes
            # happen to follow in the file.
            patch_u32(
                virtual_tail,
                layout.directories_at + 8,
                layout.idata_rva + layout.idata_size + 0x100,
            )
            with self.assertRaises(error):
                adapter.inspect(virtual_tail, {})

            short_directory = root / "short-directory.exe"
            layout = write_pe_image(short_directory, ("mujoco.dll", "glfw3.dll"))
            patch_u32(short_directory, layout.directories_at + 12, 20)
            with self.assertRaises(error):
                adapter.inspect(short_directory, {})

            nameless = root / "nameless-descriptor.exe"
            layout = write_pe_image(nameless, ("mujoco.dll",))
            patch_u32(nameless, layout.idata_offset + 12, 0)
            with self.assertRaises(error):
                adapter.inspect(nameless, {})

            unterminated = root / "unterminated-name.exe"
            layout = write_pe_image(unterminated, ("mujoco.dll",), virtual_tail=0)
            data = bytearray(unterminated.read_bytes())
            data[-1] = ord("x")
            unterminated.write_bytes(bytes(data))
            with self.assertRaises(error):
                adapter.inspect(unterminated, {})

            non_ascii = root / "non-ascii-name.exe"
            layout = write_pe_image(non_ascii, ("mujoco.dll",))
            data = bytearray(non_ascii.read_bytes())
            first_name_offset = layout.idata_offset + (
                layout.first_name_rva - layout.idata_rva
            )
            data[first_name_offset] = 0x80
            non_ascii.write_bytes(bytes(data))
            with self.assertRaises(error):
                adapter.inspect(non_ascii, {})

    def test_windows_identity_comparison_ignores_case(self) -> None:
        class WindowsFakeAdapter(FakeAdapter):
            platform_id = "windows"
            inspector_id = "pe"
            case_insensitive_identity = True

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            binary = root / "win-main_hako_drone_service.exe"
            binary.touch()
            contract = native_runtime.NativeRuntimeContract(
                path=root / "catalog.yaml",
                source_path=None,
                release="v4.0.0",
                managed_runtimes=(),
                binaries={"drone_service": binary},
                shared_libraries=("vcruntime140.dll",),
                dependency_inspector="pe",
            )

            checks = native_runtime.validate_contract(
                contract,
                WindowsFakeAdapter(("VCRUNTIME140.dll", "glfw3.dll")),
                ("drone_service",),
                {"PATH": ""},
            )

        failure = checks[-1]
        self.assertFalse(failure.ok)
        # The distribution spells it VCRUNTIME140.dll and a contract would
        # reasonably spell it vcruntime140.dll; Windows treats those as one
        # library, so the declared one must not be reported as undeclared.
        self.assertIn("VCRUNTIME140.dll (declared", failure.detail)
        self.assertIn("glfw3.dll (not declared", failure.detail)

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
