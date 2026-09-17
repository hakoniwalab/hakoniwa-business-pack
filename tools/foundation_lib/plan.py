"""Dependency ordering and Foundation build-plan generation."""

from __future__ import annotations

import json
from pathlib import Path

from foundation_lib.runtime import (
    FOUNDATION_PYTHON_IMPLEMENTATION,
    FOUNDATION_PYTHON_VERSION,
    FoundationError,
)


def load_build_catalog(path: Path) -> dict[str, dict]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise FoundationError(f"cannot read Foundation catalog {path}: {exc}") from exc
    if data.get("schema_version") != 1 or not isinstance(
        data.get("components"), dict
    ):
        raise FoundationError(f"unsupported Foundation catalog: {path}")
    python_contract = data.get("runtime", {}).get("python", {})
    expected_python = {
        "implementation": FOUNDATION_PYTHON_IMPLEMENTATION,
        "major": FOUNDATION_PYTHON_VERSION[0],
        "minor": FOUNDATION_PYTHON_VERSION[1],
    }
    if python_contract != expected_python:
        raise FoundationError(
            "Foundation catalog Python contract does not match the runtime: "
            f"required={expected_python}, catalog={python_contract}"
        )
    components = data["components"]
    known_operations = {
        "prepare",
        "doctor",
        "configure",
        "build",
        "test",
        "install",
        "smoke",
    }
    for component_id, component in components.items():
        if not isinstance(component, dict):
            raise FoundationError(f"{component_id}: catalog entry must be a mapping")
        source = component.get("source")
        repository = component.get("repository")
        revision = component.get("revision")
        dependencies = component.get("dependencies")
        operations = component.get("operations")
        if not isinstance(source, str) or not source:
            raise FoundationError(f"{component_id}: source must be a path")
        if repository is not None and (
            not isinstance(repository, str) or not repository
        ):
            raise FoundationError(f"{component_id}: repository must be a URL")
        if revision is not None and (
            not isinstance(revision, str) or not revision.strip()
        ):
            raise FoundationError(
                f"{component_id}: revision must be a non-empty string"
            )
        if (
            not isinstance(dependencies, list)
            or not all(isinstance(item, str) for item in dependencies)
            or not isinstance(operations, list)
            or not all(item in known_operations for item in operations)
        ):
            raise FoundationError(
                f"{component_id}: invalid dependencies or operations"
            )
        unknown = sorted(set(dependencies) - set(components))
        if unknown:
            raise FoundationError(
                f"{component_id}: unknown dependencies: {', '.join(unknown)}"
            )
        if "build" not in operations or "install" not in operations:
            raise FoundationError(
                f"{component_id}: build and install operations are required"
            )
    return components


def dependency_order(
    requested: list[str],
    components: dict[str, dict],
    requirements: dict[str, dict] | None = None,
) -> list[str]:
    unknown = sorted(set(requested) - set(components))
    if unknown:
        raise FoundationError(
            f"components are not Foundation-buildable: {', '.join(unknown)}"
        )
    result: list[str] = []
    visiting: set[str] = set()
    visited: set[str] = set()
    requirements = requirements or {}

    def dependencies_for(component_id: str) -> list[str]:
        dependencies = list(components[component_id]["dependencies"])
        required_capabilities = requirements.get(component_id, {}).get(
            "capabilities", {}
        )
        # Endpoint's native default is Core-free. The legacy Foundation adapter
        # keeps Core integration for existing Recipes, while an explicit
        # Core-free requirement narrows this optional dependency. A reusable
        # Catalog/Recipe profile resolver is tracked by Business Pack #87.
        if component_id == "hakoniwa-pdu-endpoint" and (
            required_capabilities.get("core_free_runtime") is True
            or required_capabilities.get("hakoniwa_core") is False
        ):
            dependencies = [
                item for item in dependencies if item != "hakoniwa-core-pro"
            ]
        return dependencies

    def visit(component_id: str) -> None:
        if component_id in visiting:
            raise FoundationError(
                f"Foundation dependency cycle includes {component_id}"
            )
        if component_id in visited:
            return
        visiting.add(component_id)
        for dependency in dependencies_for(component_id):
            visit(dependency)
        visiting.remove(component_id)
        visited.add(component_id)
        result.append(component_id)

    for component_id in requested:
        visit(component_id)
    return result


def create_build_plan_impl(
    recipe: Path,
    prefix: Path,
    components: dict[str, dict],
    business_pack_root: Path,
    force: set[str] | None = None,
    *,
    load_foundation_requirements,
    inspect_foundation,
    load_receipt,
    evaluate_component,
) -> dict:
    force = force or set()
    requirements = load_foundation_requirements(recipe)
    order = dependency_order(list(requirements), components, requirements)
    resolved_dependencies = {
        component_id: [
            dependency
            for dependency in components[component_id]["dependencies"]
            if dependency in order
        ]
        for component_id in order
    }
    invalid_force = sorted(force - set(order))
    if invalid_force:
        raise FoundationError(
            "forced components are outside the Recipe dependency closure: "
            + ", ".join(invalid_force)
        )
    inspected = inspect_foundation(recipe, prefix)
    by_id = {
        component["component"]: component for component in inspected["components"]
    }

    receipt_dir = prefix / "share" / "hakoniwa" / "receipts"
    all_receipts: dict[str, dict] = {}
    for path in sorted(receipt_dir.glob("*.yaml")) if receipt_dir.is_dir() else []:
        try:
            receipt = load_receipt(path)
            component = receipt.get("component")
            component_id = (
                component.get("id") if isinstance(component, dict) else None
            )
            if isinstance(component_id, str):
                all_receipts[component_id] = receipt
        except (FoundationError, ValueError, json.JSONDecodeError):
            continue
    for component_id in order:
        if component_id not in by_id:
            by_id[component_id] = evaluate_component(
                prefix, component_id, {}, all_receipts
            )

    blocked = [
        component_id
        for component_id in order
        if by_id[component_id]["status"] == "UNKNOWN"
        and component_id not in force
    ]
    rebuild: set[str] = {
        component_id
        for component_id in order
        if by_id[component_id]["status"] in {"MISSING", "INCOMPATIBLE"}
    }
    rebuild.update(force)
    changed = True
    while changed:
        changed = False
        for component_id in order:
            if component_id in rebuild or component_id in blocked:
                continue
            if any(
                dependency in rebuild
                for dependency in resolved_dependencies[component_id]
            ):
                rebuild.add(component_id)
                changed = True

    actions = []
    for component_id in order:
        if component_id not in rebuild:
            continue
        entry = components[component_id]
        source = (business_pack_root / entry["source"]).resolve()
        dependency_rebuilds = [
            dependency
            for dependency in resolved_dependencies[component_id]
            if dependency in rebuild
        ]
        if component_id in force:
            reason = "FORCED"
        elif by_id[component_id]["status"] == "SATISFIED":
            reason = f"dependency rebuild: {', '.join(dependency_rebuilds)}"
        else:
            reason = by_id[component_id]["status"]
        actions.append(
            {
                "component": component_id,
                "reason": reason,
                "source": str(source),
                "operations": entry["operations"],
                "requirements": requirements.get(component_id, {}),
            }
        )
    return {
        "recipe": str(recipe),
        "install_prefix": str(prefix),
        "status": (
            "BLOCKED"
            if blocked
            else (
                "NEEDS_BUILD"
                if actions
                or inspected.get("runtime", {}).get("python", {}).get("status")
                != "SATISFIED"
                else "SATISFIED"
            )
        ),
        "dependency_order": order,
        "resolved_dependencies": resolved_dependencies,
        "blocked": blocked,
        "actions": actions,
        "inspection": {
            "status": inspected["status"],
            "components": [by_id[component_id] for component_id in order],
        },
    }
