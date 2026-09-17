"""Foundation requirement, receipt, and runtime inspection."""

from __future__ import annotations

import json
import platform
from pathlib import Path

from foundation_lib.runtime import (
    FOUNDATION_PYTHON_IMPLEMENTATION,
    FOUNDATION_PYTHON_VERSION,
    FoundationError,
    foundation_python_executable,
    probe_python,
    validate_foundation_python_probe,
)


RECEIPT_REQUIRED_FIELDS = {
    "schema_version", "component", "platform", "install",
    "capabilities", "build_limits", "dependencies", "artifacts",
    "resolved_manifest",
}
ARTIFACT_PROBES = {
    "athrill-target-v850e2m": ("bin/athrill2", "bin/athrill2.exe"),
    "athrill-device": ("lib/libhakotime.so", "lib/libhakotime.dylib", "bin/hakotime.dll"),
    "hakoniwa-core-pro": ("bin/hako-cmd", "bin/hako-cmd.exe"),
    "hakoniwa-pdu-endpoint": ("lib/cmake/hakoniwa_pdu_endpoint",),
    "hakoniwa-pdu-rpc": ("lib/cmake/hakoniwa_pdu_rpc",),
    "hakoniwa-pdu-bridge-core": ("bin/hakoniwa-pdu-web-bridge", "bin/hakoniwa-pdu-web-bridge.exe"),
}


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
    if component_id == "hakoniwa-pdu-python":
        site_packages = [prefix / "python" / "Lib" / "site-packages"]
        site_packages.extend((prefix / "python" / "lib").glob("python*/site-packages"))
        return any(
            (site / "hakoniwa_pdu").exists()
            or any(site.glob("hakoniwa_pdu-*.dist-info"))
            for site in site_packages
        )
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
    if component_id == "hakoniwa-core-pro":
        installed_python = receipt.get("python")
        if not isinstance(installed_python, dict):
            reasons.append(
                _reason(
                    "python",
                    "SOABI-tagged Foundation Python binding metadata",
                    installed_python or "missing",
                )
            )
        else:
            expected_python = {
                "binding_mode": "soabi",
                "implementation": FOUNDATION_PYTHON_IMPLEMENTATION,
                "major": FOUNDATION_PYTHON_VERSION[0],
                "minor": FOUNDATION_PYTHON_VERSION[1],
            }
            for field, expected in expected_python.items():
                installed = installed_python.get(field)
                if installed != expected:
                    reasons.append(
                        _reason(f"python.{field}", expected, installed)
                    )
            if not installed_python.get("soabi"):
                reasons.append(
                    _reason("python.soabi", "non-empty SOABI", "missing")
                )
            extension_suffix = installed_python.get("extension_suffix")
            expected_artifact = (
                f"share/hakoniwa/python/hakopy{extension_suffix}"
                if isinstance(extension_suffix, str) and extension_suffix
                else None
            )
            installed_artifact = installed_python.get("artifact")
            if expected_artifact is None or installed_artifact != expected_artifact:
                reasons.append(
                    _reason(
                        "python.artifact",
                        expected_artifact or "SOABI-tagged hakopy artifact",
                        installed_artifact or "missing",
                    )
                )
            artifact_paths = {
                artifact.get("path")
                for artifact in receipt.get("artifacts", [])
                if isinstance(artifact, dict)
            }
            if installed_artifact not in artifact_paths:
                reasons.append(
                    _reason(
                        "python.artifact.receipt",
                        "listed in artifacts",
                        installed_artifact or "missing",
                    )
                )
            python_dir = prefix / "share" / "hakoniwa" / "python"
            installed_bindings = sorted(
                path.name
                for path in python_dir.glob("hakopy*")
                if path.is_file() and path.suffix.lower() in {".so", ".pyd"}
            )
            expected_name = Path(expected_artifact).name if expected_artifact else None
            if installed_bindings != ([expected_name] if expected_name else []):
                reasons.append(
                    _reason(
                        "python.artifacts.unique",
                        [expected_name] if expected_name else ["one SOABI-tagged hakopy"],
                        installed_bindings,
                    )
                )
    for capability, enabled in required.get("capabilities", {}).items():
        installed = receipt.get("capabilities", {}).get(capability)
        if isinstance(enabled, bool) and installed is not enabled:
            reasons.append(
                _reason(f"capabilities.{capability}", enabled, installed)
            )
    for limit, constraint in required.get("build_limits", {}).items():
        minimum = constraint.get("min") if isinstance(constraint, dict) else None
        if not isinstance(minimum, int):
            raise FoundationError(
                f"foundation requirement build_limits.{limit}.min must be an integer; "
                "use the nested YAML form with 'min:' on the following indented line"
            )
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


def inspect_foundation_python(prefix: Path) -> dict:
    python_root = prefix / "python"
    executable = foundation_python_executable(python_root)
    if not executable.is_file():
        return {
            "status": "MISSING",
            "executable": str(executable),
            "version": None,
            "soabi": None,
            "reason": "Foundation Python executable is missing",
        }
    try:
        selected = probe_python(executable)
        validate_foundation_python_probe(selected, python_root)
    except FoundationError as exc:
        return {
            "status": "INCOMPATIBLE",
            "executable": str(executable),
            "version": None,
            "soabi": None,
            "reason": str(exc),
        }
    return {
        "status": "SATISFIED",
        "executable": str(executable),
        "version": selected["version"],
        "soabi": selected["soabi"],
        "reason": None,
    }


def inspect_core_runtime_config(prefix: Path) -> dict:
    config = prefix.parent / "config" / "cpp_core_config.json"
    expected_mmap = (prefix.parent / "runtime" / "mmap").as_posix()
    if not config.is_file():
        return {
            "status": "MISSING",
            "path": str(config),
            "core_mmap_path": None,
            "reason": "Core runtime config is missing",
        }
    try:
        data = json.loads(config.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return {
            "status": "INCOMPATIBLE",
            "path": str(config),
            "core_mmap_path": None,
            "reason": f"Core runtime config is not valid JSON: {exc}",
        }
    mmap_path = data.get("core_mmap_path") if isinstance(data, dict) else None
    normalized = mmap_path.replace("\\", "/") if isinstance(mmap_path, str) else None
    if normalized != expected_mmap:
        return {
            "status": "INCOMPATIBLE",
            "path": str(config),
            "core_mmap_path": normalized,
            "reason": f"Core mmap path mismatch: required={expected_mmap}, installed={normalized}",
        }
    return {
        "status": "SATISFIED",
        "path": str(config),
        "core_mmap_path": normalized,
        "reason": None,
    }


def inspect_foundation_impl(
    recipe: Path,
    prefix: Path,
    validate_core_config: bool = False,
    *,
    inspect_foundation_python_callback,
    inspect_foundation_toolchain_callback,
) -> dict:
    requirements = load_foundation_requirements(recipe)
    toolchain = inspect_foundation_toolchain_callback(recipe, prefix, requirements)
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
    runtime_python = inspect_foundation_python_callback(prefix)
    core_receipt = all_receipts.get("hakoniwa-core-pro")
    if runtime_python["status"] == "SATISFIED" and core_receipt is not None:
        installed_soabi = core_receipt.get("python", {}).get("soabi")
        if installed_soabi != runtime_python["soabi"]:
            for component in components:
                if component["component"] == "hakoniwa-core-pro":
                    component["status"] = "INCOMPATIBLE"
                    component["reasons"].append(
                        _reason(
                            "python.soabi",
                            runtime_python["soabi"],
                            installed_soabi,
                        )
                    )
                    break
    statuses = {component["status"] for component in components}
    statuses.add(runtime_python["status"])
    if toolchain is not None:
        statuses.add(toolchain["status"])
    core_config = None
    if validate_core_config and "hakoniwa-core-pro" in requirements:
        core_config = inspect_core_runtime_config(prefix)
        statuses.add(core_config["status"])
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
        "toolchain": toolchain,
        "runtime": {"python": runtime_python, "core_config": core_config},
    }
