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


_ADAPTERS: Mapping[str, DependencyAdapter] = {
    "Linux": ElfDependencyAdapter(),
    "Darwin": MachODependencyAdapter(),
}


def adapter_for(system_name: str) -> DependencyAdapter:
    adapter = _ADAPTERS.get(system_name)
    if adapter is None:
        raise DependencyInspectionError(
            f"native dependency inspection is not supported on {system_name}"
        )
    return adapter
