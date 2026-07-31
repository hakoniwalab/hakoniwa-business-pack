#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import platform
import re
import subprocess
import sys
from dataclasses import asdict, dataclass
from pathlib import Path


RECIPE_ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]*$")
RECEIPT_REQUIRED_FIELDS = {
    "schema_version",
    "component",
    "platform",
    "install",
    "capabilities",
    "build_limits",
    "dependencies",
    "artifacts",
    "resolved_manifest",
}
ARTIFACT_PROBES = {
    "hakoniwa-core-pro": ("bin/hako-cmd", "bin/hako-cmd.exe"),
    "hakoniwa-pdu-endpoint": ("lib/cmake/hakoniwa_pdu_endpoint",),
    "hakoniwa-pdu-rpc": ("lib/cmake/hakoniwa_pdu_rpc",),
    "hakoniwa-pdu-bridge-core": (
        "bin/hakoniwa-pdu-web-bridge",
        "bin/hakoniwa-pdu-web-bridge.exe",
    ),
    "hakoniwa-pdu-python": ("python",),
}


class FoundationError(RuntimeError):
    pass


@dataclass(frozen=True)
class WorkspacePaths:
    business_pack_root: Path
    work_root: Path
    foundation_root: Path
    install_prefix: Path
    foundation_python: Path
    foundation_config: Path
    foundation_runtime: Path
    foundation_mmap: Path
    foundation_build: Path
    recipe_root: Path
    recipe_config: Path
    recipe_assets: Path
    recipe_missions: Path
    recipe_logs: Path
    recipe_validation: Path

    def directories(self) -> tuple[Path, ...]:
        return (
            self.install_prefix,
            self.foundation_config,
            self.foundation_mmap,
            self.foundation_build,
            self.recipe_config,
            self.recipe_assets,
            self.recipe_missions,
            self.recipe_logs,
            self.recipe_validation,
        )

    def serializable(self) -> dict[str, str]:
        return {key: str(value) for key, value in asdict(self).items()}


def repository_root() -> Path:
    return Path(__file__).resolve().parents[1]


def validate_recipe_id(recipe_id: str) -> str:
    if not RECIPE_ID_PATTERN.fullmatch(recipe_id):
        raise FoundationError(
            "recipe id must use lowercase letters, digits, and hyphens"
        )
    return recipe_id


def resolve_workspace(
    root: Path,
    recipe_id: str,
    foundation_root_override: Path | None = None,
) -> WorkspacePaths:
    recipe_id = validate_recipe_id(recipe_id)
    business_pack_root = root.resolve()
    work_root = business_pack_root / "work"
    foundation_root = (
        foundation_root_override.resolve()
        if foundation_root_override is not None
        else work_root / "foundation"
    )
    if not foundation_root.is_relative_to(work_root):
        raise FoundationError(
            "Foundation root must stay under the Business Pack work directory"
        )
    recipe_root = work_root / "recipes" / recipe_id
    return WorkspacePaths(
        business_pack_root=business_pack_root,
        work_root=work_root,
        foundation_root=foundation_root,
        install_prefix=foundation_root / "install",
        foundation_python=foundation_root / "install" / "python",
        foundation_config=foundation_root / "config",
        foundation_runtime=foundation_root / "runtime",
        foundation_mmap=foundation_root / "runtime" / "mmap",
        foundation_build=foundation_root / "build",
        recipe_root=recipe_root,
        recipe_config=recipe_root / "config",
        recipe_assets=recipe_root / "assets",
        recipe_missions=recipe_root / "missions",
        recipe_logs=recipe_root / "logs",
        recipe_validation=recipe_root / "validation",
    )


def prepare_workspace(paths: WorkspacePaths) -> None:
    for directory in paths.directories():
        directory.mkdir(parents=True, exist_ok=True)


def print_paths(paths: WorkspacePaths, json_output: bool) -> None:
    data = paths.serializable()
    if json_output:
        print(json.dumps(data, ensure_ascii=False, indent=2))
        return
    for key, value in data.items():
        print(f"{key}: {value}")


def _scalar(value: str):
    value = value.strip()
    if value == "{}":
        return {}
    if value.lower() == "true":
        return True
    if value.lower() == "false":
        return False
    if value.startswith(('"', "'")) and len(value) >= 2 and value[-1] == value[0]:
        if value[0] == '"':
            return json.loads(value)
        return value[1:-1]
    try:
        return int(value)
    except ValueError:
        return value


def load_foundation_requirements(path: Path) -> dict[str, dict]:
    lines = path.read_text(encoding="utf-8").splitlines()
    start = next(
        (
            index
            for index, line in enumerate(lines)
            if line.strip() == "foundation_requirements:" and not line.startswith(" ")
        ),
        None,
    )
    if start is None:
        raise FoundationError(f"foundation_requirements not found: {path}")

    result: dict[str, dict] = {}
    component: str | None = None
    section: str | None = None
    limit: str | None = None
    for raw in lines[start + 1 :]:
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        indent = len(raw) - len(raw.lstrip(" "))
        if indent == 0:
            break
        text = raw.strip()
        if ":" not in text:
            raise FoundationError(f"invalid foundation requirement line: {raw}")
        key, value = text.split(":", 1)
        if indent == 2:
            component = key
            result[component] = {}
            section = None
            limit = None
        elif indent == 4 and component:
            section = key
            result[component][section] = {}
            limit = None
        elif indent == 6 and component and section == "capabilities":
            result[component][section][key] = _scalar(value)
        elif indent == 6 and component and section == "version":
            result[component][section][key] = _scalar(value)
        elif indent == 6 and component and section == "build_limits":
            limit = key
            result[component][section][limit] = {}
        elif (
            indent == 8
            and component
            and section == "build_limits"
            and limit
        ):
            result[component][section][limit][key] = _scalar(value)
        else:
            raise FoundationError(f"unsupported foundation requirement line: {raw}")
    if not result:
        raise FoundationError(f"foundation_requirements is empty: {path}")
    return result


def _numeric_version(value: object) -> tuple[int, ...] | None:
    if not isinstance(value, str):
        return None
    parts = value.split(".")
    if not parts or any(not part.isdigit() for part in parts):
        return None
    return tuple(int(part) for part in parts)


def _version_at_least(installed: object, minimum: object) -> bool:
    installed_parts = _numeric_version(installed)
    minimum_parts = _numeric_version(minimum)
    if installed_parts is None or minimum_parts is None:
        return False
    width = max(len(installed_parts), len(minimum_parts))
    return installed_parts + (0,) * (width - len(installed_parts)) >= (
        minimum_parts + (0,) * (width - len(minimum_parts))
    )


def load_receipt(path: Path) -> dict:
    receipt: dict = {}
    section: str | None = None
    dependency: str | None = None
    dependency_limits = False
    artifact: dict | None = None
    for raw in path.read_text(encoding="utf-8").splitlines():
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        indent = len(raw) - len(raw.lstrip(" "))
        text = raw.strip()
        if ":" not in text:
            raise FoundationError(f"{path}: invalid receipt line: {raw}")
        key, value = text.split(":", 1)
        if indent == 0:
            section = key
            dependency = None
            dependency_limits = False
            artifact = None
            if value.strip():
                receipt[key] = _scalar(value)
            elif key == "artifacts":
                receipt[key] = []
            else:
                receipt[key] = {}
        elif section == "artifacts" and indent == 2 and key.startswith("- "):
            artifact = {key[2:]: _scalar(value)}
            receipt["artifacts"].append(artifact)
        elif section == "artifacts" and indent == 4 and artifact is not None:
            artifact[key] = _scalar(value)
        elif section == "dependencies" and indent == 2:
            dependency = key
            receipt["dependencies"][dependency] = {}
            dependency_limits = False
        elif section == "dependencies" and indent == 4 and dependency:
            if key == "build_limits" and not value.strip():
                receipt["dependencies"][dependency][key] = {}
                dependency_limits = True
            else:
                receipt["dependencies"][dependency][key] = _scalar(value)
                dependency_limits = False
        elif (
            section == "dependencies"
            and indent == 6
            and dependency
            and dependency_limits
        ):
            receipt["dependencies"][dependency]["build_limits"][key] = _scalar(value)
        elif indent == 2 and isinstance(receipt.get(section), dict):
            receipt[section][key] = _scalar(value)
        else:
            raise FoundationError(f"{path}: unsupported receipt line: {raw}")
    return receipt


def _host_contract() -> tuple[str, str]:
    os_name = {
        "Darwin": "macos",
        "Linux": "linux",
        "Windows": "windows",
    }.get(platform.system(), platform.system().lower())
    machine = platform.machine().lower()
    architecture = {
        "amd64": "x64",
        "x86_64": "x64",
        "aarch64": "arm64",
    }.get(machine, machine)
    return os_name, architecture


def _reason(field: str, required, installed) -> dict:
    return {"field": field, "required": required, "installed": installed}


def _receipt_path(prefix: Path, component_id: str) -> Path:
    return (
        prefix
        / "share"
        / "hakoniwa"
        / "receipts"
        / f"{component_id}.yaml"
    )


def _probe_artifact(prefix: Path, component_id: str) -> bool:
    return any(
        (prefix / relative).exists()
        for relative in ARTIFACT_PROBES.get(component_id, ())
    )


def evaluate_component(
    prefix: Path,
    component_id: str,
    required: dict,
    all_receipts: dict[str, dict],
) -> dict:
    receipt_file = _receipt_path(prefix, component_id)
    if not receipt_file.is_file():
        status = "UNKNOWN" if _probe_artifact(prefix, component_id) else "MISSING"
        return {
            "component": component_id,
            "status": status,
            "reasons": [
                _reason(
                    "receipt",
                    str(receipt_file.relative_to(prefix)),
                    "missing",
                )
            ],
        }
    try:
        receipt = load_receipt(receipt_file)
    except (FoundationError, ValueError, json.JSONDecodeError) as exc:
        return {
            "component": component_id,
            "status": "UNKNOWN",
            "reasons": [_reason("receipt", "valid schema_version 1", str(exc))],
        }

    reasons: list[dict] = []
    missing_fields = sorted(RECEIPT_REQUIRED_FIELDS - set(receipt))
    mapping_fields = (
        "component",
        "platform",
        "install",
        "capabilities",
        "build_limits",
        "dependencies",
    )
    invalid_types = [
        field for field in mapping_fields if not isinstance(receipt.get(field), dict)
    ]
    if not isinstance(receipt.get("artifacts"), list):
        invalid_types.append("artifacts")
    if not isinstance(receipt.get("resolved_manifest"), str):
        invalid_types.append("resolved_manifest")
    if (
        missing_fields
        or invalid_types
        or receipt.get("schema_version") != 1
    ):
        return {
            "component": component_id,
            "status": "UNKNOWN",
            "reasons": [
                _reason(
                    "receipt.schema",
                    {"version": 1, "fields": sorted(RECEIPT_REQUIRED_FIELDS)},
                    {
                        "version": receipt.get("schema_version"),
                        "missing_fields": missing_fields,
                        "invalid_types": invalid_types,
                    },
                )
            ],
        }
    if receipt.get("component", {}).get("id") != component_id:
        reasons.append(
            _reason(
                "component.id",
                component_id,
                receipt.get("component", {}).get("id"),
            )
        )
    minimum_version = required.get("version", {}).get("min")
    installed_version = receipt.get("component", {}).get("version")
    if minimum_version is not None and not _version_at_least(
        installed_version, minimum_version
    ):
        reasons.append(
            _reason("component.version.min", minimum_version, installed_version)
        )
    os_name, architecture = _host_contract()
    for field, expected in (("os", os_name), ("architecture", architecture)):
        installed = receipt.get("platform", {}).get(field)
        if installed != expected:
            reasons.append(_reason(f"platform.{field}", expected, installed))
    installed_prefix = receipt.get("install", {}).get("prefix")
    if installed_prefix != str(prefix):
        reasons.append(
            _reason("install.prefix", str(prefix), installed_prefix)
        )
    for capability, enabled in required.get("capabilities", {}).items():
        installed = receipt.get("capabilities", {}).get(capability)
        if enabled is True and installed is not True:
            reasons.append(
                _reason(f"capabilities.{capability}", True, installed)
            )
    for limit, constraint in required.get("build_limits", {}).items():
        minimum = constraint.get("min")
        installed = receipt.get("build_limits", {}).get(limit)
        if not isinstance(installed, int) or installed < minimum:
            reasons.append(
                _reason(f"build_limits.{limit}.min", minimum, installed)
            )

    for artifact in receipt.get("artifacts", []):
        relative = artifact.get("path") if isinstance(artifact, dict) else None
        if (
            not isinstance(relative, str)
            or Path(relative).is_absolute()
            or ".." in Path(relative).parts
            or not (prefix / relative).exists()
        ):
            reasons.append(
                _reason(
                    f"artifacts.{relative or '<invalid>'}",
                    "exists under install prefix",
                    "missing",
                )
            )
    resolved = receipt.get("resolved_manifest")
    resolved_path = Path(resolved)
    if (
        resolved_path.is_absolute()
        or ".." in resolved_path.parts
        or not (prefix / resolved_path).is_file()
    ):
        reasons.append(
            _reason("resolved_manifest", "existing file", resolved or "missing")
        )

    for dependency_id, installed_dependency in receipt.get(
        "dependencies", {}
    ).items():
        current = all_receipts.get(dependency_id)
        if current is None:
            reasons.append(
                _reason(
                    f"dependencies.{dependency_id}.receipt",
                    "installed",
                    "missing",
                )
            )
            continue
        for field in ("version", "source_revision"):
            expected = installed_dependency.get(field)
            actual = current.get("component", {}).get(field)
            if expected != actual:
                reasons.append(
                    _reason(
                        f"dependencies.{dependency_id}.{field}",
                        expected,
                        actual,
                    )
                )
        for limit, expected in installed_dependency.get(
            "build_limits", {}
        ).items():
            actual = current.get("build_limits", {}).get(limit)
            if expected != actual:
                reasons.append(
                    _reason(
                        f"dependencies.{dependency_id}.build_limits.{limit}",
                        expected,
                        actual,
                    )
                )
    return {
        "component": component_id,
        "status": "INCOMPATIBLE" if reasons else "SATISFIED",
        "reasons": reasons,
    }


def inspect_foundation(recipe: Path, prefix: Path) -> dict:
    requirements = load_foundation_requirements(recipe)
    all_receipts: dict[str, dict] = {}
    receipt_dir = prefix / "share" / "hakoniwa" / "receipts"
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
    components = [
        evaluate_component(prefix, component_id, required, all_receipts)
        for component_id, required in requirements.items()
    ]
    statuses = {component["status"] for component in components}
    if "INCOMPATIBLE" in statuses:
        status = "INCOMPATIBLE"
    elif "UNKNOWN" in statuses:
        status = "UNKNOWN"
    elif "MISSING" in statuses:
        status = "MISSING"
    else:
        status = "SATISFIED"
    return {
        "recipe": str(recipe),
        "install_prefix": str(prefix),
        "status": status,
        "components": components,
    }


def load_build_catalog(path: Path) -> dict[str, dict]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise FoundationError(f"cannot read Foundation catalog {path}: {exc}") from exc
    if data.get("schema_version") != 1 or not isinstance(
        data.get("components"), dict
    ):
        raise FoundationError(f"unsupported Foundation catalog: {path}")
    components = data["components"]
    known_operations = {
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
        dependencies = component.get("dependencies")
        operations = component.get("operations")
        if not isinstance(source, str) or not source:
            raise FoundationError(f"{component_id}: source must be a path")
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
    requested: list[str], components: dict[str, dict]
) -> list[str]:
    unknown = sorted(set(requested) - set(components))
    if unknown:
        raise FoundationError(
            f"components are not Foundation-buildable: {', '.join(unknown)}"
        )
    result: list[str] = []
    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(component_id: str) -> None:
        if component_id in visiting:
            raise FoundationError(
                f"Foundation dependency cycle includes {component_id}"
            )
        if component_id in visited:
            return
        visiting.add(component_id)
        for dependency in components[component_id]["dependencies"]:
            visit(dependency)
        visiting.remove(component_id)
        visited.add(component_id)
        result.append(component_id)

    for component_id in requested:
        visit(component_id)
    return result


def create_build_plan(
    recipe: Path,
    prefix: Path,
    components: dict[str, dict],
    business_pack_root: Path,
    force: set[str] | None = None,
) -> dict:
    force = force or set()
    requirements = load_foundation_requirements(recipe)
    order = dependency_order(list(requirements), components)
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
                for dependency in components[component_id]["dependencies"]
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
            for dependency in entry["dependencies"]
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
            }
        )
    return {
        "recipe": str(recipe),
        "install_prefix": str(prefix),
        "status": (
            "BLOCKED"
            if blocked
            else ("NEEDS_BUILD" if actions else "SATISFIED")
        ),
        "dependency_order": order,
        "blocked": blocked,
        "actions": actions,
        "inspection": {
            "status": inspected["status"],
            "components": [by_id[component_id] for component_id in order],
        },
    }


def _yaml_string(value: Path | str) -> str:
    return json.dumps(str(value))


def write_component_manifest(
    component_id: str, paths: WorkspacePaths
) -> Path | None:
    build_dir = paths.foundation_build / component_id
    manifest = paths.foundation_build / f"{component_id}.yaml"
    prefix = paths.install_prefix
    if component_id == "hakoniwa-core-pro":
        return None
    if component_id == "hakoniwa-pdu-endpoint":
        content = f"""version: 1

build:
  type: Release
  dir: {_yaml_string(build_dir)}
  shared: true
  parallel: 0

bindings:
  python: true

features:
  hakoniwa_core: true
  zenoh: false
  mqtt: false

validation:
  tests: false
  examples: false
  tools: false
  benchmarks: false
  python_import: true

paths:
  hakoniwa_core_root: {_yaml_string(prefix)}
  vcpkg_root: ""
"""
    elif component_id == "hakoniwa-pdu-rpc":
        content = f"""version: 1

build:
  type: Release
  dir: {_yaml_string(build_dir)}
  install_dir: {_yaml_string(prefix)}

paths:
  pdu_endpoint_root: {_yaml_string(prefix)}
  vcpkg_root: ""
"""
    elif component_id == "hakoniwa-pdu-bridge-core":
        content = f"""version: 1

build:
  type: Release
  dir: {_yaml_string(build_dir)}
  parallel: 0

components:
  library: true
  standalone_app: false
  hakoniwa_app: true
  monitor: false

validation:
  tests: false
  examples: false
  integration_tcp: false

paths:
  pdu_endpoint_root: {_yaml_string(prefix)}
  hakoniwa_core_root: {_yaml_string(prefix)}
  vcpkg_root: ""
"""
    elif component_id == "hakoniwa-pdu-python":
        content = f"""version: 1

build:
  dir: {_yaml_string(build_dir)}
  install_dir: {_yaml_string(prefix)}

paths:
  hakoniwa_core_root: {_yaml_string(prefix)}
"""
    else:
        raise FoundationError(
            f"no Foundation manifest adapter for {component_id}"
        )
    manifest.parent.mkdir(parents=True, exist_ok=True)
    manifest.write_text(content, encoding="utf-8")
    return manifest


def component_commands(
    component_id: str,
    source: Path,
    operations: list[str],
    paths: WorkspacePaths,
) -> list[list[str]]:
    hako = source / "tools" / "hako.py"
    if not hako.is_file():
        raise FoundationError(f"{component_id}: hako.py not found: {hako}")
    manifest = write_component_manifest(component_id, paths)
    python = sys.executable
    build_dir = paths.foundation_build / component_id
    commands: list[list[str]] = []
    for operation in operations:
        if component_id == "hakoniwa-core-pro":
            command = [python, str(hako), operation]
            if operation in {"doctor", "build", "install"}:
                command.extend(["--config", str(source / "hakoniwa-build.yaml")])
            if operation == "build":
                command.extend(
                    [
                        "--build-dir",
                        str(build_dir),
                        "--install-dir",
                        str(paths.install_prefix),
                        "--core-config-dir",
                        str(paths.foundation_config),
                        "--python-install-dir",
                        str(paths.install_prefix / "share" / "hakoniwa" / "python"),
                        "--python-executable",
                        python,
                        "--core-mmap-dir",
                        str(paths.foundation_mmap),
                    ]
                )
            elif operation == "install":
                command.extend(
                    [
                        "--build-dir",
                        str(build_dir),
                        "--install-dir",
                        str(paths.install_prefix),
                    ]
                )
        else:
            assert manifest is not None
            command = [
                python,
                str(hako),
                "--config",
                str(manifest),
                "--install-dir",
                str(paths.install_prefix),
            ]
            if component_id == "hakoniwa-pdu-endpoint":
                command.extend(
                    ["--python-venv", str(paths.foundation_python)]
                )
            command.append(operation)
        commands.append(command)
    return commands


def execute_build_plan(plan: dict, paths: WorkspacePaths) -> dict:
    if plan["blocked"]:
        raise FoundationError(
            "Foundation plan is blocked by UNKNOWN components: "
            + ", ".join(plan["blocked"])
        )
    prepare_workspace(paths)
    for action in plan["actions"]:
        source = Path(action["source"])
        for command in component_commands(
            action["component"],
            source,
            action["operations"],
            paths,
        ):
            print(f"> {subprocess.list2cmdline(command)}", flush=True)
            result = subprocess.run(command, cwd=source, check=False)
            if result.returncode != 0:
                raise FoundationError(
                    f"{action['component']} command failed "
                    f"with exit code {result.returncode}"
                )
    final = inspect_foundation(
        Path(plan["recipe"]), paths.install_prefix
    )
    if final["status"] != "SATISFIED":
        raise FoundationError(
            f"Foundation remains {final['status']} after build/install"
        )
    return final


def print_build_plan(plan: dict, json_output: bool) -> None:
    if json_output:
        print(json.dumps(plan, ensure_ascii=False, indent=2))
        return
    print(f"Foundation plan: {plan['status']}")
    print(f"Dependency order: {' -> '.join(plan['dependency_order'])}")
    if plan["blocked"]:
        print(f"Blocked by UNKNOWN: {', '.join(plan['blocked'])}")
    if not plan["actions"]:
        print("Actions: none (installed Foundation is reusable)")
        return
    print("Actions:")
    for action in plan["actions"]:
        operations = " -> ".join(action["operations"])
        print(
            f"  - {action['component']}: {action['reason']} "
            f"({operations})"
        )


def print_inspection(result: dict, json_output: bool) -> None:
    if json_output:
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return
    print(f"Foundation: {result['status']}")
    for component in result["components"]:
        print(f"[{component['status']}] {component['component']}")
        for reason in component["reasons"]:
            print(
                f"  - {reason['field']}: required={reason['required']!r}, "
                f"installed={reason['installed']!r}"
            )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Manage the Business Pack local Hakoniwa Foundation workspace"
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    for command, help_text in (
        ("paths", "resolve workspace paths without creating directories"),
        ("prepare", "create the reusable Foundation and Recipe workspace"),
    ):
        subparser = subparsers.add_parser(command, help=help_text)
        subparser.add_argument("--recipe-id", required=True)
        subparser.add_argument("--json", action="store_true", dest="json_output")

    doctor = subparsers.add_parser(
        "doctor",
        help="inspect Recipe requirements against installed Component Receipts",
    )
    doctor.add_argument("--recipe", required=True)
    doctor.add_argument("--install-dir", default=None)
    doctor.add_argument("--json", action="store_true", dest="json_output")

    plan = subparsers.add_parser(
        "plan",
        help="show dependency-ordered Foundation build/install actions",
    )
    plan.add_argument("--recipe", required=True)
    plan.add_argument("--install-dir", default=None)
    plan.add_argument("--catalog", default=None)
    plan.add_argument("--force", action="append", default=[])
    plan.add_argument("--json", action="store_true", dest="json_output")

    build = subparsers.add_parser(
        "build",
        help="execute the dependency-ordered plan and verify installed receipts",
    )
    build.add_argument("--recipe", required=True)
    build.add_argument("--install-dir", default=None)
    build.add_argument("--catalog", default=None)
    build.add_argument("--force", action="append", default=[])

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command in {"doctor", "plan", "build"}:
            root = repository_root()
            recipe = Path(args.recipe)
            if not recipe.is_absolute():
                recipe = (Path.cwd() / recipe).resolve()
            prefix = (
                Path(args.install_dir).resolve()
                if args.install_dir
                else root / "work" / "foundation" / "install"
            )
            if args.command in {"plan", "build"}:
                catalog = (
                    Path(args.catalog).resolve()
                    if args.catalog
                    else root / "catalog" / "foundation-components.json"
                )
                components = load_build_catalog(catalog)
                result = create_build_plan(
                    recipe,
                    prefix,
                    components,
                    root,
                    set(args.force),
                )
                print_build_plan(result, getattr(args, "json_output", False))
                if args.command == "plan":
                    return 2 if result["blocked"] else 0
                paths = resolve_workspace(
                    root,
                    validate_recipe_id(recipe.stem),
                    prefix.parent,
                )
                if prefix.name != "install" or prefix != paths.install_prefix:
                    raise FoundationError(
                        "build install prefix must be "
                        "<business-pack>/work/<foundation-name>/install"
                    )
                final = execute_build_plan(result, paths)
                print_inspection(final, False)
                return 0
            result = inspect_foundation(recipe, prefix)
            print_inspection(result, args.json_output)
            return 0 if result["status"] == "SATISFIED" else 1
        paths = resolve_workspace(repository_root(), args.recipe_id)
        if args.command == "prepare":
            prepare_workspace(paths)
        print_paths(paths, args.json_output)
        return 0
    except FoundationError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
