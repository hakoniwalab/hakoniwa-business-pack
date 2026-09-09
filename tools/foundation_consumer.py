#!/usr/bin/env python3
from __future__ import annotations

import sys
from pathlib import Path


TOOLS_DIR = Path(__file__).resolve().parent
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

import foundation as _foundation  # noqa: E402


CONSUMER_CMAKE_CONTEXT_SCHEMA_VERSION = 1


def cmake_consumer_context(
    paths: _foundation.WorkspacePaths,
    *,
    platform_name: str | None = None,
    architecture: str | None = None,
) -> dict[str, object]:
    """Return Foundation-owned CMake discovery context for downstream consumers.

    The Foundation is the source of truth for the toolchain used to materialize
    its install tree. Consumers must not rediscover or hard-code the vcpkg root.
    This API exposes only the derived CMake context they need.

    Non-Windows consumers retain the existing behavior: only the Foundation
    install prefix is advertised. On Windows, when the Foundation has a persisted
    vcpkg toolchain selection, the matching installed package prefix, toolchain
    file, and target triplet are exposed as additional consumer context.
    """

    selected_platform = sys.platform if platform_name is None else platform_name
    prefix_paths = [str(paths.install_prefix)]
    context: dict[str, object] = {
        "schema_version": CONSUMER_CMAKE_CONTEXT_SCHEMA_VERSION,
        "prefix_paths": prefix_paths,
        "toolchain_file": None,
        "cache_variables": {},
    }

    if selected_platform != "win32":
        return context

    toolchain = _foundation.load_foundation_toolchain(paths)
    if not toolchain:
        return context

    vcpkg_root = Path(toolchain["vcpkg_root"]).resolve()
    if architecture is None:
        os_name, architecture = _foundation._host_contract()
        if os_name != "windows":
            raise _foundation.FoundationError(
                "Windows consumer context requires a Windows Foundation host contract"
            )
    if not isinstance(architecture, str) or not architecture:
        raise _foundation.FoundationError(
            "Windows consumer context requires a non-empty architecture"
        )

    triplet = f"{architecture}-windows"
    toolchain_file = vcpkg_root / "scripts" / "buildsystems" / "vcpkg.cmake"
    prefix_paths.append(str(vcpkg_root / "installed" / triplet))
    context["toolchain_file"] = str(toolchain_file)
    context["cache_variables"] = {"VCPKG_TARGET_TRIPLET": triplet}
    return context
