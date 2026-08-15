#!/usr/bin/env python3
"""Validate component-owned native runtime contracts without launching binaries."""

from __future__ import annotations

import json
import platform
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

from native_runtime_platforms import (
    DependencyAdapter,
    DependencyInspectionError,
    adapter_for,
)


class NativeRuntimeError(RuntimeError):
    pass


@dataclass(frozen=True)
class ManagedRuntime:
    name: str
    version: str
    version_file: Path
    library: Path


@dataclass(frozen=True)
class NativeRuntimeContract:
    path: Path
    source_path: Path | None
    release: str
    managed_runtimes: tuple[ManagedRuntime, ...]
    binaries: Mapping[str, Path]
    shared_libraries: tuple[str, ...]
    dependency_inspector: str


@dataclass(frozen=True)
class RuntimeCheck:
    label: str
    ok: bool
    detail: str


@dataclass(frozen=True)
class RecipeNativeRequirement:
    component_id: str
    profile: str
    required_roles: tuple[str, ...]
    optional_roles: tuple[str, ...]


def _parse_scalar(value: str) -> Any:
    value = value.strip()
    if not value:
        return {}
    if value in {"true", "false"}:
        return value == "true"
    if value.startswith("[") and value.endswith("]"):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError as exc:
            raise NativeRuntimeError(f"invalid inline list: {value}") from exc
        if not isinstance(parsed, list):
            raise NativeRuntimeError(f"inline value must be a list: {value}")
        return parsed
    try:
        return int(value)
    except ValueError:
        return value


def _parse_simple_yaml(lines: Sequence[str], path: Path) -> dict[str, Any]:
    root: dict[str, Any] = {}
    stack: list[tuple[int, dict[str, Any]]] = [(-1, root)]
    for line_number, raw in enumerate(lines, 1):
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        indent = len(raw) - len(raw.lstrip(" "))
        if "\t" in raw[:indent] or indent % 2:
            raise NativeRuntimeError(
                f"{path}:{line_number}: indentation must use two spaces"
            )
        text = raw.strip()
        if text.startswith("-") or ":" not in text:
            raise NativeRuntimeError(
                f"{path}:{line_number}: mappings, scalars, and inline lists only"
            )
        key, value = text.split(":", 1)
        key = key.strip()
        while stack and indent <= stack[-1][0]:
            stack.pop()
        if not key or not stack:
            raise NativeRuntimeError(f"{path}:{line_number}: invalid mapping")
        parent = stack[-1][1]
        if key in parent:
            raise NativeRuntimeError(f"{path}:{line_number}: duplicate key: {key}")
        parsed = _parse_scalar(value)
        parent[key] = parsed
        if isinstance(parsed, dict):
            stack.append((indent, parsed))
    return root


def load_simple_yaml(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise NativeRuntimeError(f"native runtime contract not found: {path}")
    return _parse_simple_yaml(path.read_text(encoding="utf-8").splitlines(), path)


def load_mapping_section(path: Path, section: str) -> dict[str, Any]:
    if not path.is_file():
        raise NativeRuntimeError(f"YAML file not found: {path}")
    lines = path.read_text(encoding="utf-8").splitlines()
    start = next(
        (
            index
            for index, line in enumerate(lines)
            if line == f"{section}:"
        ),
        None,
    )
    if start is None:
        raise NativeRuntimeError(f"{section} not found: {path}")
    selected = [lines[start]]
    for line in lines[start + 1 :]:
        if line and not line.startswith(" ") and not line.lstrip().startswith("#"):
            break
        selected.append(line)
    parsed = _parse_simple_yaml(selected, path)
    value = parsed.get(section)
    if not isinstance(value, dict):
        raise NativeRuntimeError(f"{section} must be a mapping: {path}")
    return value


def _mapping(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise NativeRuntimeError(f"native runtime contract {label} must be a mapping")
    return value


def _relative_path(root: Path, value: Any, label: str) -> Path:
    if not isinstance(value, str) or not value.strip():
        raise NativeRuntimeError(f"native runtime contract {label} must be a relative path")
    relative = Path(value)
    if relative.is_absolute() or ".." in relative.parts:
        raise NativeRuntimeError(f"native runtime contract {label} is unsafe: {value}")
    return root / relative


def _version(path: Path, label: str) -> str:
    try:
        version = path.read_text(encoding="utf-8").strip()
    except OSError as exc:
        raise NativeRuntimeError(f"cannot read {label} version authority: {path}") from exc
    if not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", version):
        raise NativeRuntimeError(f"invalid {label} version in {path}: {version!r}")
    return version


def load_recipe_requirement(
    recipe_path: Path, component_id: str
) -> RecipeNativeRequirement:
    raw = load_mapping_section(recipe_path, "native_runtime_requirements")
    if raw.get("schema_version") != 1:
        raise NativeRuntimeError(
            f"unsupported native runtime Recipe schema in {recipe_path}: "
            f"{raw.get('schema_version')!r}"
        )
    components = _mapping(raw.get("components"), "Recipe components")
    requirement = _mapping(
        components.get(component_id), f"Recipe components.{component_id}"
    )
    profile_id = requirement.get("profile")
    if not isinstance(profile_id, str) or not profile_id:
        raise NativeRuntimeError(
            f"native runtime Recipe profile must be a non-empty string: {component_id}"
        )
    required = requirement.get("required_roles")
    optional = requirement.get("optional_roles")
    for label, roles in (("required_roles", required), ("optional_roles", optional)):
        if not isinstance(roles, list) or not all(
            isinstance(role, str) and role for role in roles
        ):
            raise NativeRuntimeError(
                f"native runtime Recipe {component_id}.{label} must be a string list"
            )
    return RecipeNativeRequirement(
        component_id, profile_id, tuple(required), tuple(optional)
    )


def load_catalog_contract(
    catalog_path: Path,
    component_root: Path,
    requirement: RecipeNativeRequirement,
    adapter: DependencyAdapter,
) -> NativeRuntimeContract:
    raw = load_mapping_section(catalog_path, "native_runtime")
    if raw.get("schema_version") != 1:
        raise NativeRuntimeError(
            f"unsupported Catalog native runtime schema in {catalog_path}: "
            f"{raw.get('schema_version')!r}"
        )
    profiles = _mapping(raw.get("profiles"), "Catalog profiles")
    profile_raw = _mapping(
        profiles.get(requirement.profile), f"Catalog profiles.{requirement.profile}"
    )
    source_path: Path | None = None
    source_contract = profile_raw.get("source_contract")
    if source_contract is not None:
        source_path = _relative_path(
            component_root, source_contract, "Catalog profile source_contract"
        )
        source_raw = load_simple_yaml(source_path)
        source_profiles = _mapping(source_raw.get("profiles"), "source profiles")
        source_profile = _mapping(
            source_profiles.get(requirement.profile),
            f"source profiles.{requirement.profile}",
        )
        comparable_catalog = {
            key: profile_raw.get(key)
            for key in ("distribution_release", "managed_runtimes", "platforms")
        }
        comparable_source = {
            key: source_profile.get(key)
            for key in ("distribution_release", "managed_runtimes", "platforms")
        }
        if comparable_source != comparable_catalog:
            raise NativeRuntimeError(
                "Catalog native runtime profile differs from its component-owned "
                f"source contract: {source_path}"
            )
    release = profile_raw.get("distribution_release")
    if not isinstance(release, str) or not release:
        raise NativeRuntimeError("Catalog distribution_release must be a string")

    managed: list[ManagedRuntime] = []
    managed_raw = _mapping(
        profile_raw.get("managed_runtimes"), "Catalog managed_runtimes"
    )
    for name, untyped_runtime in managed_raw.items():
        runtime = _mapping(untyped_runtime, f"managed_runtimes.{name}")
        if runtime.get("required") is not True:
            continue
        version_file = _relative_path(
            component_root,
            runtime.get("version_file"),
            f"managed_runtimes.{name}.version_file",
        )
        version = _version(version_file, name)
        runtime_platforms = _mapping(
            runtime.get("platforms"), f"managed_runtimes.{name}.platforms"
        )
        platform_runtime = _mapping(
            runtime_platforms.get(adapter.platform_id),
            f"managed_runtimes.{name}.platforms.{adapter.platform_id}",
        )
        library_template = platform_runtime.get("library")
        if not isinstance(library_template, str) or "{version}" not in library_template:
            raise NativeRuntimeError(
                f"native runtime contract {name} library must contain {{version}}: "
                f"{library_template!r}"
            )
        library = _relative_path(
            component_root,
            library_template.replace("{version}", version),
            f"managed_runtimes.{name}.platforms.{adapter.platform_id}.library",
        )
        managed.append(ManagedRuntime(name, version, version_file, library))

    platforms = _mapping(profile_raw.get("platforms"), "Catalog platforms")
    platform_contract = _mapping(
        platforms.get(adapter.platform_id), f"platforms.{adapter.platform_id}"
    )
    inspector_id = platform_contract.get("dependency_inspector")
    if inspector_id != adapter.inspector_id:
        raise NativeRuntimeError(
            "Catalog dependency inspector does not match the current platform "
            f"adapter: expected {adapter.inspector_id}, got {inspector_id!r}"
        )
    binaries_raw = _mapping(
        platform_contract.get("binary_roles"),
        f"platforms.{adapter.platform_id}.binary_roles",
    )
    binaries = {
        role: _relative_path(
            component_root,
            relative,
            f"platforms.{adapter.platform_id}.binary_roles.{role}",
        )
        for role, relative in binaries_raw.items()
    }
    libraries = platform_contract.get("required_libraries")
    if not isinstance(libraries, list) or not all(
        isinstance(library, str) and library for library in libraries
    ):
        raise NativeRuntimeError(
            f"Catalog platforms.{adapter.platform_id}.required_libraries "
            "must be a list of library names"
        )
    return NativeRuntimeContract(
        catalog_path,
        source_path,
        release,
        tuple(managed),
        binaries,
        tuple(libraries),
        inspector_id,
    )


def resolve_contract(
    catalog_path: Path,
    recipe_path: Path,
    component_id: str,
    component_root: Path,
    system_name: str | None = None,
) -> tuple[RecipeNativeRequirement, NativeRuntimeContract, DependencyAdapter]:
    requirement = load_recipe_requirement(recipe_path, component_id)
    try:
        adapter = adapter_for(system_name or platform.system())
    except DependencyInspectionError as exc:
        raise NativeRuntimeError(str(exc)) from exc
    contract = load_catalog_contract(
        catalog_path, component_root, requirement, adapter
    )
    return requirement, contract, adapter


def validate_requirement(
    catalog_path: Path,
    recipe_path: Path,
    component_id: str,
    component_root: Path,
    environment: Mapping[str, str],
    active_optional_roles: Sequence[str] = (),
    system_name: str | None = None,
) -> tuple[NativeRuntimeContract, tuple[RuntimeCheck, ...]]:
    requirement, contract, adapter = resolve_contract(
        catalog_path,
        recipe_path,
        component_id,
        component_root,
        system_name,
    )
    unknown_optional = set(active_optional_roles) - set(requirement.optional_roles)
    if unknown_optional:
        raise NativeRuntimeError(
            "Recipe activated undeclared optional native roles: "
            + ", ".join(sorted(unknown_optional))
        )
    active_roles = tuple(
        dict.fromkeys([*requirement.required_roles, *active_optional_roles])
    )
    return contract, validate_contract(
        contract, adapter, active_roles, environment
    )


def validate_contract(
    contract: NativeRuntimeContract,
    adapter: DependencyAdapter,
    active_roles: Sequence[str],
    environment: Mapping[str, str],
) -> tuple[RuntimeCheck, ...]:
    checks = [
        RuntimeCheck(
            "native runtime contract",
            True,
            f"{contract.path}"
            + (f" mirrored from {contract.source_path}" if contract.source_path else "")
            + f" (distribution {contract.release})",
        )
    ]
    for runtime in contract.managed_runtimes:
        checks.append(
            RuntimeCheck(
                f"{runtime.name} {runtime.version} runtime",
                runtime.library.is_file(),
                str(runtime.library),
            )
        )
    declared = set(contract.shared_libraries)
    for role in active_roles:
        binary = contract.binaries.get(role)
        label = role.replace("_", " ")
        if binary is None:
            checks.append(RuntimeCheck(label, False, f"binary role is not declared: {role}"))
            continue
        checks.append(RuntimeCheck(label, binary.is_file(), str(binary)))
        if not binary.is_file():
            continue
        try:
            inspection = adapter.inspect(binary, environment)
        except DependencyInspectionError as exc:
            checks.append(RuntimeCheck(f"{label} shared libraries", False, str(exc)))
            continue
        if not inspection.missing:
            checks.append(
                RuntimeCheck(
                    f"{label} shared libraries",
                    True,
                    f"all dependencies resolved for {binary}",
                )
            )
            continue
        details = []
        for install_name in inspection.missing:
            library = Path(install_name).name
            declaration = (
                "declared by native runtime contract"
                if library in declared
                else "not declared by native runtime contract"
            )
            details.append(f"{install_name} ({declaration}; required by {binary})")
        checks.append(
            RuntimeCheck(
                f"{label} shared libraries", False, "missing: " + ", ".join(details)
            )
        )
    return tuple(checks)
