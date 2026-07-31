"""Register Foundation-owned DLL directories for native Windows Python modules."""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Callable, Mapping


_DLL_DIRECTORY_HANDLES: list[object] = []


def activate(
    environment: Mapping[str, str] | None = None,
    *,
    platform_name: str | None = None,
    add_directory: Callable[[str], object] | None = None,
) -> list[str]:
    env = os.environ if environment is None else environment
    platform_value = sys.platform if platform_name is None else platform_name
    if platform_value != "win32" or env.get("HAKONIWA_WORKSPACE_ACTIVE") != "1":
        return []

    add_dll_directory = add_directory or getattr(os, "add_dll_directory", None)
    if add_dll_directory is None:
        return []

    raw_dirs = env.get("HAKO_PDU_ENDPOINT_RUNTIME_DIRS", "")
    candidates = [Path(value) for value in raw_dirs.split(os.pathsep) if value]
    hakoniwa_home = env.get("HAKONIWA_HOME")
    if hakoniwa_home:
        candidates.append(Path(hakoniwa_home) / "bin")

    registered: list[str] = []
    seen: set[Path] = set()
    for candidate in candidates:
        try:
            resolved = candidate.expanduser().resolve()
        except OSError:
            continue
        if resolved in seen or not resolved.is_dir():
            continue
        seen.add(resolved)
        try:
            handle = add_dll_directory(str(resolved))
        except (FileNotFoundError, OSError):
            continue
        _DLL_DIRECTORY_HANDLES.append(handle)
        registered.append(str(resolved))
    return registered


activate()
