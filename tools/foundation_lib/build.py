"""Component manifest adapters and build command generation."""

from __future__ import annotations

import json
import platform
import re
from pathlib import Path

from foundation_lib.runtime import (
    VCPKG_COMPONENTS,
    FoundationError,
    WorkspacePaths,
    foundation_python_executable,
    load_foundation_toolchain,
)


def _yaml_string(value: Path | str) -> str:
    return json.dumps(str(value))


def _set_manifest_boolean(
    lines: list[str], section: str, key: str, value: bool
) -> list[str]:
    section_index: int | None = None
    key_index: int | None = None
    active_section: str | None = None
    for index, line in enumerate(lines):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if not line.startswith((" ", "\t")) and stripped.endswith(":"):
            active_section = stripped[:-1]
            if active_section == section:
                section_index = index
            continue
        if active_section == section and re.match(rf"^\s+{re.escape(key)}\s*:", line):
            key_index = index
    rendered = str(value).lower()
    if key_index is not None:
        indentation = lines[key_index][
            : len(lines[key_index]) - len(lines[key_index].lstrip())
        ]
        lines[key_index] = f"{indentation}{key}: {rendered}"
    elif section_index is not None:
        lines.insert(section_index + 1, f"  {key}: {rendered}")
    else:
        if lines and lines[-1] != "":
            lines.append("")
        lines.extend([f"{section}:", f"  {key}: {rendered}"])
    return lines


def _core_foundation_manifest(
    source_manifest: Path, *, callback_assets_shared: bool = False
) -> str:
    if not source_manifest.is_file():
        raise FoundationError(
            f"hakoniwa-core-pro build manifest not found: {source_manifest}"
        )
    lines = source_manifest.read_text(encoding="utf-8").splitlines()
    lines = _set_manifest_boolean(lines, "python", "soabi", True)
    lines = _set_manifest_boolean(
        lines, "features", "callback_assets_shared", callback_assets_shared
    )
    lines = _set_manifest_boolean(lines, "validation", "tests", False)
    return "\n".join(lines) + "\n"


def write_component_manifest(
    component_id: str,
    source: Path,
    paths: WorkspacePaths,
    required: dict | None = None,
) -> Path | None:
    build_dir = paths.foundation_build / component_id
    manifest = paths.foundation_build / f"{component_id}.yaml"
    prefix = paths.install_prefix
    toolchain = (
        load_foundation_toolchain(paths)
        if component_id in VCPKG_COMPONENTS
        else {}
    )
    vcpkg_root = toolchain.get("vcpkg_root", "")
    required_capabilities = (required or {}).get("capabilities", {})
    if component_id == "hakoniwa-core-pro":
        content = _core_foundation_manifest(
            source / "hakoniwa-build.yaml",
            callback_assets_shared=(
                required_capabilities.get("callback_assets_shared") is True
            ),
        )
    elif component_id == "zenoh-c":
        content = f"""version: 1

build:
  type: Release
  dir: {_yaml_string(build_dir)}
  parallel: 0
  in_source_tree: true

features:
  unstable_api: true
  shared_memory: false

validation:
  tests: false
"""
    elif component_id == "hakoniwa-pdu-endpoint":
        core_free = (
            required_capabilities.get("core_free_runtime") is True
            or required_capabilities.get("hakoniwa_core") is False
        )
        core_enabled = not core_free
        python_enabled = required_capabilities.get(
            "python_binding", core_enabled
        )
        content = f"""version: 1

build:
  type: Release
  dir: {_yaml_string(build_dir)}
  shared: true
  parallel: 0

bindings:
  python: {str(bool(python_enabled)).lower()}

features:
  hakoniwa_core: {str(core_enabled).lower()}
  zenoh: false
  mqtt: false

validation:
  tests: false
  examples: false
  tools: false
  benchmarks: false
  python_import: {str(bool(python_enabled)).lower()}

paths:
  hakoniwa_core_root: {_yaml_string(prefix) if core_enabled else '""'}
  vcpkg_root: {_yaml_string(vcpkg_root)}
"""
    elif component_id == "hakoniwa-pdu-rpc":
        content = f"""version: 1

build:
  type: Release
  dir: {_yaml_string(build_dir)}
  install_dir: {_yaml_string(prefix)}

paths:
  pdu_endpoint_root: {_yaml_string(prefix)}
  vcpkg_root: {_yaml_string(vcpkg_root)}
"""
    elif component_id == "hakoniwa-pdu-bridge-core":
        hakoniwa_app = required_capabilities.get("hakoniwa_app", True) is True
        standalone_app = required_capabilities.get("standalone_app", False) is True
        monitor = required_capabilities.get("monitor", False) is True
        content = f"""version: 1

build:
  type: Release
  dir: {_yaml_string(build_dir)}
  parallel: 0

components:
  library: true
  standalone_app: {str(standalone_app).lower()}
  hakoniwa_app: {str(hakoniwa_app).lower()}
  monitor: {str(monitor).lower()}

validation:
  tests: false
  examples: false
  integration_tcp: false

paths:
  pdu_endpoint_root: {_yaml_string(prefix)}
  hakoniwa_core_root: {_yaml_string(prefix) if hakoniwa_app else '\"\"'}
  vcpkg_root: {_yaml_string(vcpkg_root)}
"""
    elif component_id == "hakoniwa-pdu-python":
        content = f"""version: 1

build:
  dir: {_yaml_string(build_dir)}
  install_dir: {_yaml_string(prefix)}

paths:
  hakoniwa_core_root: {_yaml_string(prefix)}
"""
    elif component_id == "hakoniwa-zenoh-topology-viewer":
        content = f"""version: 1

build:
  type: Release
  dir: {_yaml_string(build_dir)}
  parallel: 0

components:
  collector: true
  web: true

validation:
  tests: true
  smoke: true

paths:
  zenohc_root: {_yaml_string(prefix)}
  pdu_endpoint_root: {_yaml_string(prefix)}
  pdu_registry_root: {_yaml_string(source.parent / "hakoniwa-pdu-registry")}
  pdu_javascript_root: {_yaml_string(source.parent / "hakoniwa-pdu-javascript")}
"""
    elif component_id == "athrill-target-v850e2m":
        athrill_source = source.parent / "athrill"
        exdev_enabled = required_capabilities.get("exdev", True) is True
        content = f"""version: 1

build:
  type: Release
  dir: {_yaml_string(build_dir)}
  parallel: 0

features:
  exdev: {str(exdev_enabled).lower()}
  mros: false
  vdev: false

validation:
  tests: true

paths:
  athrill_root: {_yaml_string(athrill_source)}
  vcpkg_root: {_yaml_string(vcpkg_root)}
"""
    elif component_id == "athrill-device":
        athrill_source = source.parent / "athrill"
        capabilities = required_capabilities
        hakotime_enabled = capabilities.get("hakotime_shared", True) is True
        hakopdu_enabled = capabilities.get("hakopdu_ev3", True) is True
        content = f"""version: 1

build:
  type: Release
  dir: {_yaml_string(build_dir)}
  parallel: 0

components:
  hakotime: {str(hakotime_enabled).lower()}
  hakopdu_ev3: {str(hakopdu_enabled).lower()}

validation:
  tests: true

paths:
  athrill_root: {_yaml_string(athrill_source)}
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
    required: dict | None = None,
) -> list[list[str]]:
    hako = source / "tools" / "hako.py"
    if not hako.is_file():
        raise FoundationError(f"{component_id}: hako.py not found: {hako}")
    manifest = write_component_manifest(component_id, source, paths, required)
    python = str(foundation_python_executable(paths.foundation_python))
    build_dir = paths.foundation_build / component_id
    commands: list[list[str]] = []
    for operation in operations:
        if component_id == "hakoniwa-core-pro":
            command = [python, str(hako), operation]
            command.extend([
                "--state-dir",
                str(paths.foundation_root / "state" / component_id),
            ])
            if operation in {"doctor", "build", "install"}:
                assert manifest is not None
                command.extend(["--config", str(manifest)])
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
                        paths.foundation_mmap.as_posix(),
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
            if component_id in {
                "zenoh-c",
                "hakoniwa-pdu-endpoint",
                "hakoniwa-pdu-bridge-core",
                "hakoniwa-pdu-python",
                "hakoniwa-pdu-rpc",
                "hakoniwa-zenoh-topology-viewer",
            }:
                command.extend(
                    [
                        "--state-dir",
                        str(paths.foundation_root / "state" / component_id),
                    ]
                )
            if component_id == "hakoniwa-pdu-endpoint":
                capabilities = (required or {}).get("capabilities", {})
                core_free = (
                    capabilities.get("core_free_runtime") is True
                    or capabilities.get("hakoniwa_core") is False
                )
                if capabilities.get("python_binding", not core_free):
                    command.extend(
                        ["--python-venv", str(paths.foundation_python)]
                    )
            if component_id == "hakoniwa-pdu-rpc":
                command.extend(
                    ["--python-venv", str(paths.foundation_python)]
                )
            command.append(operation)
        commands.append(command)
    return commands


def normalize_core_config_for_windows(
    config: Path,
    expected_mmap: Path | None = None,
) -> None:
    """Repair Core's Windows mmap JSON and enforce the Foundation-owned path."""
    if platform.system() != "Windows" or not config.is_file():
        return
    content = config.read_text(encoding="utf-8")
    try:
        data = json.loads(content)
    except json.JSONDecodeError:
        pattern = r'("core_mmap_path"\s*:\s*")([^"]*)(")'
        repaired, replacements = re.subn(
            pattern,
            lambda match: (
                match.group(1)
                + match.group(2).replace("\\", "/")
                + match.group(3)
            ),
            content,
            count=1,
        )
        if replacements != 1:
            raise FoundationError(
                f"core_mmap_path not found in invalid Core config: {config}"
            )
        try:
            data = json.loads(repaired)
        except json.JSONDecodeError as exc:
            raise FoundationError(
                f"Core config remains invalid after Windows path repair: {config}: {exc}"
            ) from exc
    if not isinstance(data, dict) or not isinstance(data.get("core_mmap_path"), str):
        raise FoundationError(f"Core config has no string core_mmap_path: {config}")
    normalized = data["core_mmap_path"].replace("\\", "/")
    if expected_mmap is not None and normalized != expected_mmap.as_posix():
        raise FoundationError(
            "Core mmap path is outside the managed Foundation contract: "
            f"required={expected_mmap.as_posix()}, generated={normalized}"
        )
    data["core_mmap_path"] = normalized
    temporary = config.with_suffix(config.suffix + ".tmp")
    temporary.write_text(
        json.dumps(data, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.replace(config)
