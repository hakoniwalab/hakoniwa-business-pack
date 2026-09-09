#!/usr/bin/env python3
"""OS-specific dependency inspection adapters for native runtime validation."""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Protocol, Sequence


class DependencyInspectionError(RuntimeError):
    pass


@dataclass(frozen=True)
class DependencyInspection:
    dependencies: tuple[str, ...]
    missing: tuple[str, ...]


class DependencyAdapter(Protocol):
    platform_id: str
    inspector_id: str

    def inspect(
        self, binary: Path, environment: Mapping[str, str]
    ) -> DependencyInspection: ...


def _run(command: Sequence[str], environment: Mapping[str, str]) -> str:
    probe = subprocess.run(
        list(command),
        capture_output=True,
        text=True,
        check=False,
        env=dict(environment),
    )
    output = "\n".join(part for part in (probe.stdout, probe.stderr) if part).strip()
    if probe.returncode != 0:
        raise DependencyInspectionError(
            f"dependency inspector failed with rc={probe.returncode}: "
            f"{' '.join(command)}: {output}"
        )
    return output


class ElfDependencyAdapter:
    platform_id = "linux"
    inspector_id = "elf"

    def inspect(
        self, binary: Path, environment: Mapping[str, str]
    ) -> DependencyInspection:
        ldd = shutil.which("ldd", path=environment.get("PATH"))
        if ldd is None:
            raise DependencyInspectionError(
                "cannot inspect native runtime dependencies: ldd not found"
            )
        output = _run([ldd, str(binary)], environment)
        dependencies: list[str] = []
        missing: list[str] = []
        for line in output.splitlines():
            match = re.match(
                r"^\s*(\S+)\s+=>\s+(.+?)(?:\s+\(0x[0-9a-fA-F]+\))?\s*$",
                line,
            )
            if not match:
                continue
            library, target = match.groups()
            dependencies.append(library)
            if target == "not found":
                missing.append(library)
        return DependencyInspection(
            tuple(dict.fromkeys(dependencies)), tuple(dict.fromkeys(missing))
        )


class MachODependencyAdapter:
    platform_id = "macos"
    inspector_id = "macho"

    @staticmethod
    def _rpaths(
        binary: Path, environment: Mapping[str, str], otool: str
    ) -> tuple[Path, ...]:
        output = _run([otool, "-l", str(binary)], environment)
        paths: list[Path] = []
        awaiting_path = False
        for line in output.splitlines():
            stripped = line.strip()
            if stripped == "cmd LC_RPATH":
                awaiting_path = True
                continue
            if awaiting_path and stripped.startswith("path "):
                value = stripped.removeprefix("path ").split(" (offset ", 1)[0]
                value = value.replace("@loader_path", str(binary.parent))
                value = value.replace("@executable_path", str(binary.parent))
                paths.append(Path(value))
                awaiting_path = False
        dynamic_paths = [
            Path(value)
            for value in environment.get("DYLD_LIBRARY_PATH", "").split(os.pathsep)
            if value
        ]
        return tuple(dict.fromkeys([*dynamic_paths, *paths]))

    @staticmethod
    def _resolves(install_name: str, binary: Path, rpaths: Sequence[Path]) -> bool:
        if install_name.startswith(("/System/Library/", "/usr/lib/")):
            return True
        if install_name.startswith("@loader_path/"):
            return (binary.parent / install_name.removeprefix("@loader_path/")).is_file()
        if install_name.startswith("@executable_path/"):
            return (
                binary.parent / install_name.removeprefix("@executable_path/")
            ).is_file()
        if install_name.startswith("@rpath/"):
            relative = install_name.removeprefix("@rpath/")
            return any((root / relative).is_file() for root in rpaths)
        path = Path(install_name)
        if path.is_absolute() and path.is_file():
            return True
        basename = path.name
        return any((root / basename).is_file() for root in rpaths)

    def inspect(
        self, binary: Path, environment: Mapping[str, str]
    ) -> DependencyInspection:
        otool = shutil.which("otool", path=environment.get("PATH"))
        if otool is None:
            raise DependencyInspectionError(
                "cannot inspect native runtime dependencies: otool not found"
            )
        output = _run([otool, "-L", str(binary)], environment)
        dependencies = []
        for line in output.splitlines()[1:]:
            stripped = line.strip()
            if stripped:
                dependencies.append(
                    stripped.split(" (compatibility version", 1)[0]
                )
        rpaths = self._rpaths(binary, environment, otool)
        missing = [
            dependency
            for dependency in dependencies
            if not self._resolves(dependency, binary, rpaths)
        ]
        return DependencyInspection(tuple(dependencies), tuple(missing))


class PeDependencyAdapter:
    """Read a PE image's import directory and resolve each DLL by name.

    Unlike the ELF and Mach-O adapters this one does not shell out. A Windows
    job that installs no toolchain has no ldd equivalent available, and adding
    dumpbin, objdump or llvm-readobj to reach one would be a heavier dependency
    than the parsing it replaces. Parsing also lets the tests for this adapter
    run on every runner rather than only the Windows one.

    Declared policy, so that the gaps are known rather than implied:

    * The search order is the directory containing the binary, then the
      directories in the supplied environment's PATH. The real loader also
      consults the current working directory, side-by-side manifests, the
      KnownDLLs registry key, already-loaded modules and package graphs. None
      of those are modelled here. Omitting the working directory is stricter
      than the loader, which is the right direction for a check that runs
      before launch, but the result is not loader-accurate and must not be
      described as such.
    * api-ms-win-* and ext-ms-win-* are treated as resolved. That also hides a
      minimum-OS-version mismatch against the API set schema, which is a trade
      taken deliberately rather than an oversight.
    * _SYSTEM_DLLS is an approximation. Windows defines KnownDLLs in the
      registry and the set varies with architecture and edition.
    * Delay-load imports (data directory entry 13) are out of scope. No binary
      this validator inspects today populates that directory, so nothing is
      missed now; a future binary that uses delay-loading is a known gap rather
      than a silent one.

    The runtime libraries a contract is expected to declare - vcruntime140,
    vcruntime140_1, msvcp140, mujoco, glfw3 and their kin - are deliberately
    absent from the approximation set, so they surface as missing exactly the
    way libglfw.so.3 and libglfw.3.dylib do on the other two platforms.
    """

    platform_id = "windows"
    inspector_id = "pe"

    # Approximation of the DLLs present on any supported Windows install. See
    # the class docstring: this is not the KnownDLLs list and cannot be.
    _SYSTEM_DLLS = frozenset(
        {
            "advapi32.dll", "bcrypt.dll", "cfgmgr32.dll", "combase.dll",
            "comctl32.dll", "comdlg32.dll", "crypt32.dll", "d3d11.dll",
            "dbghelp.dll", "dwmapi.dll", "dxgi.dll", "gdi32.dll", "hid.dll",
            "imm32.dll", "iphlpapi.dll", "kernel32.dll", "kernelbase.dll",
            "msvcrt.dll", "ntdll.dll", "ole32.dll", "oleaut32.dll",
            "opengl32.dll", "powrprof.dll", "psapi.dll", "rpcrt4.dll",
            "secur32.dll", "setupapi.dll", "shell32.dll", "shlwapi.dll",
            "user32.dll", "userenv.dll", "uxtheme.dll", "version.dll",
            "winmm.dll", "winspool.drv", "wintrust.dll", "ws2_32.dll",
        }
    )

    _DOS_SIGNATURE = b"MZ"
    _PE_SIGNATURE = b"PE\0\0"
    _PE32 = 0x10B
    _PE32_PLUS = 0x20B
    _IMPORT_DIRECTORY_INDEX = 1
    _IMPORT_DESCRIPTOR_SIZE = 20
    _SECTION_HEADER_SIZE = 40

    @staticmethod
    def _u16(data: bytes, offset: int) -> int:
        return int.from_bytes(data[offset : offset + 2], "little")

    @staticmethod
    def _u32(data: bytes, offset: int) -> int:
        return int.from_bytes(data[offset : offset + 4], "little")

    @classmethod
    def _sections(
        cls, data: bytes, table_offset: int, count: int
    ) -> tuple[tuple[int, int, int], ...]:
        sections = []
        for index in range(count):
            base = table_offset + index * cls._SECTION_HEADER_SIZE
            if base + cls._SECTION_HEADER_SIZE > len(data):
                raise DependencyInspectionError("PE section table is truncated")
            virtual_size = cls._u32(data, base + 8)
            virtual_address = cls._u32(data, base + 12)
            raw_size = cls._u32(data, base + 16)
            raw_pointer = cls._u32(data, base + 20)
            # A section can be larger in memory than on disk, so the mapped
            # length is the wider of the two.
            sections.append(
                (virtual_address, max(virtual_size, raw_size), raw_pointer)
            )
        return tuple(sections)

    @staticmethod
    def _offset_for(rva: int, sections: Sequence[tuple[int, int, int]]) -> int:
        for virtual_address, length, raw_pointer in sections:
            if virtual_address <= rva < virtual_address + length:
                return raw_pointer + (rva - virtual_address)
        raise DependencyInspectionError(
            f"PE import table references RVA 0x{rva:x}, which is in no section"
        )

    @staticmethod
    def _string_at(data: bytes, offset: int) -> str:
        end = data.find(b"\0", offset)
        if end == -1:
            raise DependencyInspectionError("PE import name is not terminated")
        return data[offset:end].decode("ascii", errors="replace")

    @classmethod
    def _imports(cls, binary: Path) -> tuple[str, ...]:
        try:
            data = binary.read_bytes()
        except OSError as exc:
            raise DependencyInspectionError(f"cannot read {binary}: {exc}") from exc
        if len(data) < 0x40 or data[:2] != cls._DOS_SIGNATURE:
            raise DependencyInspectionError(f"{binary} is not a PE image")
        pe_offset = cls._u32(data, 0x3C)
        if data[pe_offset : pe_offset + 4] != cls._PE_SIGNATURE:
            raise DependencyInspectionError(f"{binary} has no PE signature")

        coff = pe_offset + 4
        section_count = cls._u16(data, coff + 2)
        optional_size = cls._u16(data, coff + 16)
        optional = coff + 20
        magic = cls._u16(data, optional)
        if magic == cls._PE32:
            directories = optional + 96
        elif magic == cls._PE32_PLUS:
            directories = optional + 112
        else:
            raise DependencyInspectionError(
                f"{binary} has an unknown optional header magic 0x{magic:x}"
            )
        if cls._u32(data, directories - 4) <= cls._IMPORT_DIRECTORY_INDEX:
            return ()
        import_rva = cls._u32(data, directories + cls._IMPORT_DIRECTORY_INDEX * 8)
        if import_rva == 0:
            return ()

        sections = cls._sections(data, optional + optional_size, section_count)
        cursor = cls._offset_for(import_rva, sections)
        names: list[str] = []
        while True:
            descriptor = data[cursor : cursor + cls._IMPORT_DESCRIPTOR_SIZE]
            if len(descriptor) < cls._IMPORT_DESCRIPTOR_SIZE:
                raise DependencyInspectionError("PE import directory is truncated")
            if descriptor == bytes(cls._IMPORT_DESCRIPTOR_SIZE):
                break
            name_rva = cls._u32(descriptor, 12)
            if name_rva == 0:
                break
            names.append(cls._string_at(data, cls._offset_for(name_rva, sections)))
            cursor += cls._IMPORT_DESCRIPTOR_SIZE
        return tuple(dict.fromkeys(names))

    @classmethod
    def _resolves(cls, name: str, binary: Path, search_path: Sequence[Path]) -> bool:
        lowered = name.lower()
        if lowered.startswith(("api-ms-win-", "ext-ms-win-")):
            return True
        if lowered in cls._SYSTEM_DLLS:
            return True
        if (binary.parent / name).is_file():
            return True
        return any((root / name).is_file() for root in search_path)

    def inspect(
        self, binary: Path, environment: Mapping[str, str]
    ) -> DependencyInspection:
        dependencies = self._imports(binary)
        search_path = [
            Path(value)
            for value in environment.get("PATH", "").split(os.pathsep)
            if value
        ]
        missing = [
            dependency
            for dependency in dependencies
            if not self._resolves(dependency, binary, search_path)
        ]
        return DependencyInspection(dependencies, tuple(missing))


_ADAPTERS: Mapping[str, DependencyAdapter] = {
    "Linux": ElfDependencyAdapter(),
    "Darwin": MachODependencyAdapter(),
    "Windows": PeDependencyAdapter(),
}


def adapter_for(system_name: str) -> DependencyAdapter:
    adapter = _ADAPTERS.get(system_name)
    if adapter is None:
        raise DependencyInspectionError(
            f"native dependency inspection is not supported on {system_name}"
        )
    return adapter
