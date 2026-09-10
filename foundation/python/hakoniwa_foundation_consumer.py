#!/usr/bin/env python3
from __future__ import annotations

import sys
from pathlib import Path


BUSINESS_PACK_ROOT = Path(__file__).resolve().parents[2]
TOOLS_DIR = BUSINESS_PACK_ROOT / "tools"
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


def cmake_consumer_args(
    paths: _foundation.WorkspacePaths,
    *,
    platform_name: str | None = None,
    architecture: str | None = None,
) -> list[str]:
    """Render the Foundation-owned consumer context as CMake cache arguments."""

    context = cmake_consumer_context(
        paths,
        platform_name=platform_name,
        architecture=architecture,
    )
    prefix_paths = context["prefix_paths"]
    if not isinstance(prefix_paths, list) or not all(
        isinstance(item, str) and item for item in prefix_paths
    ):
        raise _foundation.FoundationError(
            "Foundation consumer context contains invalid prefix_paths"
        )

    args = [f"-DCMAKE_PREFIX_PATH={';'.join(prefix_paths)}"]
    toolchain_file = context["toolchain_file"]
    if toolchain_file is not None:
        if not isinstance(toolchain_file, str) or not toolchain_file:
            raise _foundation.FoundationError(
                "Foundation consumer context contains an invalid toolchain_file"
            )
        args.append(f"-DCMAKE_TOOLCHAIN_FILE={toolchain_file}")

    cache_variables = context["cache_variables"]
    if not isinstance(cache_variables, dict):
        raise _foundation.FoundationError(
            "Foundation consumer context contains invalid cache_variables"
        )
    for name in sorted(cache_variables):
        value = cache_variables[name]
        if not isinstance(name, str) or not name or not isinstance(value, str):
            raise _foundation.FoundationError(
                "Foundation consumer context contains an invalid cache variable"
            )
        args.append(f"-D{name}={value}")
    return args
