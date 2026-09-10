#!/usr/bin/env python3
"""OS-specific dependency inspection adapters for native runtime validation."""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path, PureWindowsPath
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


@dataclass(frozen=True)
class _MappedRange:
    """A span of the image that is backed by bytes on disk."""

    rva: int
    length: int
    offset: int


class PeDependencyAdapter:
    """Read a PE image's import directory and resolve each DLL by name.

    Unlike the ELF and Mach-O adapters this one does not shell out. A Windows
    job that installs no toolchain has no ldd equivalent available, and adding
    dumpbin, objdump or llvm-readobj to reach one would be a heavier dependency
    than the parsing it replaces. Parsing also lets the tests for this adapter
    run on every runner rather than only the Windows one.

    Declared policy, so that the gaps are known rather than implied.

    Search order is the directory containing the binary, then the directories
    in the supplied environment's PATH. The real loader also consults its own
    system directory, the working directory, side-by-side manifests, the
    KnownDLLs registry key, already-loaded modules and package graphs. None of
    those are modelled. Two consequences follow and are intended:

    * The system directory is not searched unless PATH names it. On a host
      whose PATH omits System32, MSVCP140.dll and the VCRUNTIME140 pair are
      reported missing even though they are installed. A contract that wants
      them treated as a host prerequisite has to say so.
    * There is no implicit working-directory entry, which is stricter than the
      loader. A caller that puts "." on PATH re-introduces one, resolving
      against the inspecting process's directory rather than the launch
      directory. The result is therefore not loader-accurate and must not be
      described as such.

    api-ms-win-* and ext-ms-win-* are treated as resolved. That also hides a
    minimum-OS-version mismatch against the API set schema, taken deliberately.
    _SYSTEM_DLLS is an approximation: Windows defines KnownDLLs in the registry
    and the set varies with architecture and edition. Delay-load imports (data
    directory entry 13) are out of scope, so a future binary that uses them is
    a known gap rather than a silent one. Resolution checks only that a file of
    that name exists; it establishes neither a compatible machine architecture
    nor the transitive closure of the dependency graph.

    The runtime libraries a contract is expected to declare - vcruntime140,
    vcruntime140_1, msvcp140, mujoco, glfw3 and their kin - are deliberately
    absent from the approximation set, so they surface as missing exactly the
    way libglfw.so.3 and libglfw.3.dylib do on the other two platforms.

    Malformed images are rejected rather than summarised. Every read is bounded
    by the region it belongs to, so a truncated header, an import directory
    that runs past its declared size, a name that is not terminated inside its
    own region, or a descriptor that claims a name at RVA 0 all raise
    DependencyInspectionError. The alternative - returning a short list - would
    be reported by the caller as "all dependencies resolved", which is the one
    answer a validator must never invent.
    """

    platform_id = "windows"
    inspector_id = "pe"
    # Windows compares DLL names and environment variable names without regard
    # to case; the caller uses this to fold identities before comparing them
    # against a contract. The other adapters leave it unset and keep byte
    # identity.
    case_insensitive_identity = True

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
    _COFF_SIZE = 20
    _SECTION_HEADER_SIZE = 40
    _DIRECTORY_ENTRY_SIZE = 8
    _IMPORT_DIRECTORY_INDEX = 1
    _IMPORT_DESCRIPTOR_SIZE = 20
    # Offset of SizeOfHeaders inside the optional header. The windows-specific
    # block starts 4 bytes later in PE32+ because ImageBase is 8 bytes wide,
    # but the standard block is 4 bytes shorter, so the field lands at the same
    # place in both.
    _SIZE_OF_HEADERS_AT = 60
    _DIRECTORIES_AT = {_PE32: 96, _PE32_PLUS: 112}
    _MAX_NAME_LENGTH = 260

    @staticmethod
    def _bytes(data: bytes, offset: int, length: int, what: str) -> bytes:
        if offset < 0 or length < 0 or offset + length > len(data):
            raise DependencyInspectionError(
                f"PE image is truncated: cannot read {what}"
            )
        return data[offset : offset + length]

    @classmethod
    def _u16(cls, data: bytes, offset: int, what: str) -> int:
        return int.from_bytes(cls._bytes(data, offset, 2, what), "little")

    @classmethod
    def _u32(cls, data: bytes, offset: int, what: str) -> int:
        return int.from_bytes(cls._bytes(data, offset, 4, what), "little")

    @classmethod
    def _mapped_ranges(
        cls, data: bytes, table: int, count: int, size_of_headers: int
    ) -> tuple[_MappedRange, ...]:
        # The headers are mapped at RVA 0 with a 1:1 file offset, so an import
        # name can legitimately live in header padding. dumpbin reads those;
        # refusing them would be a false negative.
        ranges = [_MappedRange(0, min(size_of_headers, len(data)), 0)]
        for index in range(count):
            base = table + index * cls._SECTION_HEADER_SIZE
            header = cls._bytes(
                data, base, cls._SECTION_HEADER_SIZE, f"section header {index}"
            )
            virtual_size = int.from_bytes(header[8:12], "little")
            virtual_address = int.from_bytes(header[12:16], "little")
            raw_size = int.from_bytes(header[16:20], "little")
            raw_pointer = int.from_bytes(header[20:24], "little")
            if raw_size == 0 or raw_pointer == 0:
                continue
            available = min(raw_size, max(len(data) - raw_pointer, 0))
            # Beyond SizeOfRawData a section is zero-filled in memory, so those
            # RVAs are not backed by file bytes and must not be translated into
            # whatever happens to follow on disk.
            present = available if virtual_size == 0 else min(virtual_size, available)
            if present > 0:
                ranges.append(_MappedRange(virtual_address, present, raw_pointer))
        return tuple(ranges)

    @staticmethod
    def _locate(rva: int, ranges: Sequence[_MappedRange]) -> tuple[int, int]:
        """Return the file offset for an RVA and the bytes left in its range."""
        for mapped in ranges:
            if mapped.rva <= rva < mapped.rva + mapped.length:
                delta = rva - mapped.rva
                return mapped.offset + delta, mapped.length - delta
        raise DependencyInspectionError(
            f"PE import table references RVA 0x{rva:x}, "
            "which is not backed by any mapped range"
        )

    @classmethod
    def _name_at(cls, data: bytes, rva: int, ranges: Sequence[_MappedRange]) -> str:
        offset, remaining = cls._locate(rva, ranges)
        window = cls._bytes(
            data, offset, min(remaining, cls._MAX_NAME_LENGTH), "import name"
        )
        end = window.find(b"\0")
        if end == -1:
            raise DependencyInspectionError(
                f"PE import name at RVA 0x{rva:x} is not terminated inside its range"
            )
        raw = window[:end]
        if not raw:
            raise DependencyInspectionError(
                f"PE import name at RVA 0x{rva:x} is empty"
            )
        if any(byte < 0x20 or byte > 0x7E for byte in raw):
            raise DependencyInspectionError(
                f"PE import name at RVA 0x{rva:x} is not printable ASCII"
            )
        name = raw.decode("ascii")
        # Import names are normally bare. A pathname is legal in the file
        # format, and the contract compares bare names, so reduce it and let
        # resolution use the same identity the caller will compare.
        return PureWindowsPath(name).name or name

    @classmethod
    def _imports(cls, binary: Path) -> tuple[str, ...]:
        try:
            data = binary.read_bytes()
        except OSError as exc:
            raise DependencyInspectionError(f"cannot read {binary}: {exc}") from exc
        if len(data) < 0x40 or data[:2] != cls._DOS_SIGNATURE:
            raise DependencyInspectionError(f"{binary} is not a PE image")
        pe_offset = cls._u32(data, 0x3C, "e_lfanew")
        if cls._bytes(data, pe_offset, 4, "PE signature") != cls._PE_SIGNATURE:
            raise DependencyInspectionError(f"{binary} has no PE signature")

        coff = pe_offset + 4
        cls._bytes(data, coff, cls._COFF_SIZE, "COFF header")
        section_count = cls._u16(data, coff + 2, "NumberOfSections")
        optional_size = cls._u16(data, coff + 16, "SizeOfOptionalHeader")
        optional = coff + cls._COFF_SIZE
        magic = cls._u16(data, optional, "optional header magic")
        directories_at = cls._DIRECTORIES_AT.get(magic)
        if directories_at is None:
            raise DependencyInspectionError(
                f"{binary} has an unknown optional header magic 0x{magic:x}"
            )
        entry_at = directories_at + cls._IMPORT_DIRECTORY_INDEX * cls._DIRECTORY_ENTRY_SIZE
        if optional_size < entry_at + cls._DIRECTORY_ENTRY_SIZE:
            raise DependencyInspectionError(
                f"{binary} declares SizeOfOptionalHeader={optional_size}, "
                "which is too small to contain an import directory entry"
            )
        cls._bytes(data, optional, optional_size, "optional header")
        size_of_headers = cls._u32(
            data, optional + cls._SIZE_OF_HEADERS_AT, "SizeOfHeaders"
        )
        directory_count = cls._u32(
            data, optional + directories_at - 4, "NumberOfRvaAndSizes"
        )
        if directory_count <= cls._IMPORT_DIRECTORY_INDEX:
            return ()
        import_rva = cls._u32(data, optional + entry_at, "import directory RVA")
        import_size = cls._u32(data, optional + entry_at + 4, "import directory size")
        if import_rva == 0 or import_size == 0:
            return ()

        ranges = cls._mapped_ranges(
            data, optional + optional_size, section_count, size_of_headers
        )
        offset, remaining = cls._locate(import_rva, ranges)
        # The directory ends at whichever comes first: its declared size or the
        # end of the range that holds it. Both are bounds the file states about
        # itself, so neither may be exceeded.
        span = min(import_size, remaining)
        names: list[str] = []
        for index in range(span // cls._IMPORT_DESCRIPTOR_SIZE):
            base = offset + index * cls._IMPORT_DESCRIPTOR_SIZE
            descriptor = cls._bytes(
                data, base, cls._IMPORT_DESCRIPTOR_SIZE, f"import descriptor {index}"
            )
            if descriptor == bytes(cls._IMPORT_DESCRIPTOR_SIZE):
                return tuple(dict.fromkeys(names))
            name_rva = int.from_bytes(descriptor[12:16], "little")
            if name_rva == 0:
                raise DependencyInspectionError(
                    f"PE import descriptor {index} is populated but names no library"
                )
            names.append(cls._name_at(data, name_rva, ranges))
        raise DependencyInspectionError(
            "PE import directory is not terminated within its declared size"
        )

    @classmethod
    def _search_path(cls, environment: Mapping[str, str]) -> tuple[Path, ...]:
        # Windows environment names are case-insensitive, and this mapping is
        # whatever the caller passed rather than a normalised os.environ.
        value = ""
        for key in environment:
            if key.upper() == "PATH":
                value = environment[key]
                break
        return tuple(Path(part) for part in value.split(os.pathsep) if part)

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
        search_path = self._search_path(environment)
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
