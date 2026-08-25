#!/usr/bin/env python3
"""Configure and operate the native single-host multi-drone Recipe.

The MVP intentionally accepts a small, dependency-free YAML subset consisting
of nested mappings, scalar values, and inline scalar lists.  This lets
``configure`` run before the Foundation Python environment exists.  A matrix
section is ignored by the single-condition operator and consumed by its
dedicated experiment runner.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import math
import os
import platform
import re
import shutil
import socket
import subprocess
import sys
import tarfile
import tempfile
import time
import urllib.error
import urllib.request
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

try:
    from tools.recipe import drone_fleet_runtime as fleet_runtime
except ModuleNotFoundError:
    import drone_fleet_runtime as fleet_runtime


RECIPE_ID = "drone-fleet-single-host"
OPERATOR_NAME = "drone_fleet_single_host.py"
TOOLS_DIR = Path(__file__).absolute().parents[1]
ROOT = Path(__file__).absolute().parents[2]
DEFAULT_EXPERIMENT = (
    ROOT / "recipes" / "experiments" / "drone-fleet-single-host-mvp.yaml"
)
VIEWER_URL_BASE = (
    "http://127.0.0.1:8000/index.html"
    "?viewerConfigPath=/config/viewer-config-fleets.json"
    "&wsUri=ws://127.0.0.1:8765&wireVersion=v2"
)
MAP_VIEWER_URL_BASE = (
    "http://127.0.0.1:8000/src/client/index.html"
    "?threejsRoot=/thirdparty/hakoniwa-threejs-drone"
    "&viewerConfigName=viewer-config-fleets.json"
)
HAKONIWA_STROKE_COUNT = 26
RECOMMENDED_DRONES_PER_STROKE = 2
# The public Drone Core distribution and the default Foundation build limits
# form the verified general-user capacity profile.  A larger experiment must
# use a separately built and verified 512-drone profile; accepting only a
# larger Foundation receipt is insufficient because the native Drone/VSP
# artifacts belong to the same compile-time contract.
GENERAL_USER_MAX_DRONES = 200
PUBLIC_DRONE_RELEASE = "v4.0.0"
PUBLIC_DRONE_REPOSITORY = "https://github.com/toppers/hakoniwa-drone-core.git"
PUBLIC_DRONE_REPOSITORY_ID = "toppers/hakoniwa-drone-core"
DRONE_COMPONENT_ID = "hakoniwa-drone-core"
DRONE_CATALOG = ROOT / "catalog" / "components" / f"{DRONE_COMPONENT_ID}.yaml"
THREEJS_VIEWER_REPOSITORY = "https://github.com/hakoniwalab/hakoniwa-threejs-drone.git"
PUBLIC_DRONE_ARCHIVES = {
    "Darwin": (
        "mac.zip",
        "c8f81a7aa0dc85d335c6568676dd4e958e30cf19d23668c1b96d2e4cebddbd3f",
    ),
    "Linux": (
        "lnx.zip",
        "d8ef1418e8754dcb4048d808a700568f21dd9b328966ae2806f70285e273fc60",
    ),
}
SUPPORTED_NATIVE_SYSTEMS = ("Darwin", "Linux")
MUJOCO_RELEASE_BASE = "https://github.com/google-deepmind/mujoco/releases/download"


class RecipeError(RuntimeError):
    pass


@dataclass(frozen=True)
class PerformanceMeasurement:
    mode: str
    series: str
    configuration_id: str
    attempt: int
    protocol_status: str
    sampling_interval_sec: float
    temporal_sampling_interval_usec: int | None
    preflight_duration_sec: float
    preflight_settle_timeout_sec: float
    preflight_max_cpu_average_percent: float
    preflight_max_memory_used_percent: float
    minimum_virtual_time_sec: float
    minimum_cpu_sample_count: int
    minimum_machine_sample_count: int
    maximum_virtual_time_sec: float
    maximum_wall_time_sec: float
    warmup_virtual_time_sec: float
    drone_delta_time_usec: int
    fleet_delta_time_usec: int
    conductor_delta_time_usec: int
    conductor_max_delay_time_usec: int
    conductor_real_sleep_msec: int
    simtime_publish_mode: str
    conductor_implementation: str


@dataclass(frozen=True)
class Experiment:
    experiment_id: str
    drone_count: int
    drones_per_process: int
    process_count: int
    runtime_mode: str
    visualization: bool
    show_runner_real_time_sync: bool
    scenario_type: str
    word: str
    letter_width_m: float
    letter_height_m: float
    letter_gap_m: float
    altitude_m: float
    duration_sec: float
    hold_sec: float
    speed_m_s: float
    timeout_sec: float
    land: bool
    results_enabled: bool
    results_directory: str
    measurement: PerformanceMeasurement | None = None


def operator_command(command: str) -> str:
    return f"python tools/recipe/{OPERATOR_NAME} {command}"


def load_foundation_module():
    script = TOOLS_DIR / "foundation.py"
    spec = importlib.util.spec_from_file_location(
        "business_pack_drone_fleet_foundation", script
    )
    if spec is None or spec.loader is None:
        raise RecipeError(f"cannot load Foundation helper: {script}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def load_native_runtime_module():
    if str(TOOLS_DIR) not in sys.path:
        sys.path.insert(0, str(TOOLS_DIR))
    import native_runtime

    return native_runtime


def default_source(name: str) -> Path:
    return ROOT.parent / name


def recipe_file() -> Path:
    return ROOT / "recipes" / "examples" / f"{RECIPE_ID}.yaml"


def _safe_extract(archive: Path, destination: Path) -> None:
    destination = destination.resolve()
    with zipfile.ZipFile(archive) as package:
        for member in package.infolist():
            target = (destination / member.filename).resolve()
            if target != destination and destination not in target.parents:
                raise RecipeError(
                    f"native Drone archive contains an unsafe path: {member.filename}"
                )
        package.extractall(destination)


def _atomic_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        while chunk := source.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _download(url: str, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(destination.suffix + ".tmp")
    try:
        with urllib.request.urlopen(url) as response:
            with temporary.open("wb") as output:
                while chunk := response.read(1024 * 1024):
                    output.write(chunk)
        temporary.replace(destination)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise


def _verified_download(url: str, destination: Path, expected_sha256: str) -> str:
    if destination.is_file() and _sha256(destination) == expected_sha256:
        return "verified-cache"
    print(f"Downloading: {url}")
    _download(url, destination)
    actual_sha256 = _sha256(destination)
    if actual_sha256 != expected_sha256:
        destination.unlink(missing_ok=True)
        raise RecipeError(
            f"download SHA-256 mismatch for {url}: expected "
            f"{expected_sha256}, got {actual_sha256}"
        )
    return "downloaded"


def _git_output(drone_root: Path, *arguments: str) -> str:
    command = ["git", *arguments]
    result = subprocess.run(
        command,
        cwd=drone_root,
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip()
        suffix = f": {detail}" if detail else ""
        raise RecipeError(f"command failed: {subprocess.list2cmdline(command)}{suffix}")
    return result.stdout.strip()


def _git_is_ancestor(drone_root: Path, older: str, newer: str) -> bool:
    return subprocess.run(
        ["git", "merge-base", "--is-ancestor", older, newer],
        cwd=drone_root,
        check=False,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    ).returncode == 0


def _repository_id(remote_url: str) -> str | None:
    value = remote_url.strip().removesuffix(".git").rstrip("/")
    match = re.search(r"github\.com(?::|/)([^/]+/[^/]+)$", value)
    return match.group(1).lower() if match else None


def prepare_drone_workspace(drone_root: Path) -> dict[str, Any]:
    mode = "reused"
    if not drone_root.exists():
        drone_root.parent.mkdir(parents=True, exist_ok=True)
        _run_checked(
            [
                "git",
                "clone",
                "--recurse-submodules",
                "--branch",
                "main",
                "--depth",
                "1",
                PUBLIC_DRONE_REPOSITORY,
                str(drone_root),
            ],
            cwd=drone_root.parent,
        )
        mode = "cloned"
    elif not (drone_root / ".git").exists():
        raise RecipeError(
            f"existing Drone workspace is not a Git checkout: {drone_root}"
        )
    else:
        remote_url = _git_output(drone_root, "remote", "get-url", "origin")
        actual_repository = _repository_id(remote_url)
        if actual_repository != PUBLIC_DRONE_REPOSITORY_ID:
            raise RecipeError(
                "existing Drone workspace has an unexpected origin: expected "
                f"{PUBLIC_DRONE_REPOSITORY_ID}, got {remote_url}"
            )
        branch = _git_output(drone_root, "branch", "--show-current")
        if branch != "main":
            raise RecipeError(
                f"existing Drone workspace must be on main, got {branch or 'detached HEAD'}: "
                f"{drone_root}"
            )
        _run_checked(["git", "fetch", "origin", "main"], cwd=drone_root)
        current = _git_output(drone_root, "rev-parse", "HEAD")
        upstream = _git_output(drone_root, "rev-parse", "origin/main")
        if current != upstream:
            if not _git_is_ancestor(drone_root, current, upstream):
                raise RecipeError(
                    "existing Drone workspace is not a fast-forward ancestor of "
                    f"origin/main: HEAD={current}, origin/main={upstream}"
                )
            _run_checked(["git", "merge", "--ff-only", "origin/main"], cwd=drone_root)
            mode = "updated"

    _run_checked(
        ["git", "submodule", "update", "--init", "--recursive"], cwd=drone_root
    )
    remote_url = _git_output(drone_root, "remote", "get-url", "origin")
    actual_repository = _repository_id(remote_url)
    if actual_repository != PUBLIC_DRONE_REPOSITORY_ID:
        raise RecipeError(
            "cloned Drone workspace has an unexpected origin: expected "
            f"{PUBLIC_DRONE_REPOSITORY_ID}, got {remote_url}"
        )
    revision = _git_output(drone_root, "rev-parse", "HEAD")
    dirty_paths = [
        line for line in _git_output(drone_root, "status", "--short").splitlines() if line
    ]
    if not (drone_root / "tools" / "gen_fleet_scale_config.py").is_file():
        raise RecipeError(
            f"Hakoniwa Drone workspace is incomplete: {drone_root}; "
            "tools/gen_fleet_scale_config.py is missing"
        )
    return {
        "mode": mode,
        "repository": PUBLIC_DRONE_REPOSITORY_ID,
        "requested_ref": "main",
        "resolved_revision": revision,
        "dirty": bool(dirty_paths),
        "dirty_path_count": len(dirty_paths),
    }


def _mujoco_asset(version: str, system_name: str, machine: str) -> str:
    normalized_machine = machine.lower()
    if system_name == "Darwin":
        return f"mujoco-{version}-macos-universal2.dmg"
    if system_name == "Linux":
        architectures = {
            "x86_64": "x86_64",
            "amd64": "x86_64",
            "aarch64": "aarch64",
            "arm64": "aarch64",
        }
        architecture = architectures.get(normalized_machine)
        if architecture is None:
            raise RecipeError(f"unsupported Linux architecture for MuJoCo: {machine}")
        return f"mujoco-{version}-linux-{architecture}.tar.gz"
    raise RecipeError(
        f"unsupported native operating system: {system_name}; "
        "drone-fleet-single-host supports macOS and Linux"
    )


def _read_checksum(path: Path, asset_name: str) -> str:
    try:
        fields = path.read_text(encoding="utf-8").strip().split()
    except OSError as exc:
        raise RecipeError(f"cannot read MuJoCo checksum: {path}") from exc
    if not fields or not re.fullmatch(r"[0-9a-fA-F]{64}", fields[0]):
        raise RecipeError(f"invalid MuJoCo checksum file: {path}")
    if len(fields) > 1 and Path(fields[-1].lstrip("*")).name != asset_name:
        raise RecipeError(
            f"MuJoCo checksum names an unexpected asset: {fields[-1]}"
        )
    return fields[0].lower()


def _install_mujoco_linux(archive: Path, drone_root: Path, version: str) -> Path:
    with tempfile.TemporaryDirectory(prefix="hakoniwa-mujoco-extract-") as temporary:
        extraction_root = Path(temporary)
        try:
            with tarfile.open(archive, "r:gz") as package:
                package.extractall(extraction_root, filter="data")
        except (OSError, tarfile.TarError) as exc:
            raise RecipeError(f"failed to extract MuJoCo archive: {exc}") from exc
        source = extraction_root / f"mujoco-{version}"
        if not source.is_dir():
            directories = [path for path in extraction_root.iterdir() if path.is_dir()]
            if len(directories) != 1:
                raise RecipeError(
                    f"MuJoCo archive has an unexpected layout: {archive}"
                )
            source = directories[0]
        destination = drone_root / "vendor" / "mujoco"
        shutil.copytree(source, destination, dirs_exist_ok=True)
    library = destination / "lib" / f"libmujoco.so.{version}"
    if not library.is_file():
        raise RecipeError(f"MuJoCo runtime library is missing after install: {library}")
    return library


def materialize_mujoco_runtime(
    drone_root: Path, system_name: str, cache_root: Path
) -> dict[str, Any]:
    native_runtime = load_native_runtime_module()
    try:
        _requirement, contract, _adapter = native_runtime.resolve_contract(
            DRONE_CATALOG,
            recipe_file(),
            DRONE_COMPONENT_ID,
            drone_root,
            system_name,
        )
    except native_runtime.NativeRuntimeError as exc:
        raise RecipeError(str(exc)) from exc
    if contract.release != PUBLIC_DRONE_RELEASE:
        raise RecipeError(
            "Recipe native distribution and Catalog profile disagree: "
            f"expected {PUBLIC_DRONE_RELEASE}, got {contract.release}"
        )
    mujoco = next(
        (runtime for runtime in contract.managed_runtimes if runtime.name == "mujoco"),
        None,
    )
    if mujoco is None:
        raise RecipeError("Catalog native runtime profile does not require MuJoCo")
    version = mujoco.version
    asset_name = _mujoco_asset(version, system_name, platform.machine())
    release_url = f"{MUJOCO_RELEASE_BASE}/{version}"
    checksum_url = f"{release_url}/{asset_name}.sha256"
    cache = cache_root / "mujoco" / version
    checksum_path = cache / f"{asset_name}.sha256"
    if not checksum_path.is_file():
        print(f"Downloading MuJoCo checksum: {checksum_url}")
        try:
            _download(checksum_url, checksum_path)
        except (OSError, urllib.error.URLError) as exc:
            raise RecipeError(f"failed to download MuJoCo checksum: {exc}") from exc
    expected_sha256 = _read_checksum(checksum_path, asset_name)
    archive = cache / asset_name
    try:
        mode = _verified_download(
            f"{release_url}/{asset_name}", archive, expected_sha256
        )
    except (OSError, urllib.error.URLError) as exc:
        raise RecipeError(f"failed to download MuJoCo runtime: {exc}") from exc

    if system_name == "Darwin":
        installer = drone_root / "tools" / "install-mujoco-mac.bash"
        linker = drone_root / "tools" / "link-mujoco-mac.bash"
        if not installer.is_file() or not linker.is_file():
            raise RecipeError(
                "Drone workspace does not provide the required macOS MuJoCo "
                "install/link scripts"
            )
        with tempfile.TemporaryDirectory(prefix="hakoniwa-mujoco-install-") as temporary:
            staging = Path(temporary)
            shutil.copy2(archive, staging / asset_name)
            (staging / "MUJOCO_VERSION.txt").write_text(
                version + "\n", encoding="utf-8"
            )
            _run_checked(["bash", str(installer), str(drone_root)], cwd=staging)
        library = drone_root / "vendor" / "mujoco" / "lib" / f"libmujoco.{version}.dylib"
        if not library.is_file():
            raise RecipeError(
                f"MuJoCo runtime library is missing after install: {library}"
            )
        target = drone_root / "mac"
        _run_checked(
            [
                "bash",
                str(linker),
                str(target),
                "--lib-dir",
                str(library.parent),
            ],
            cwd=drone_root,
        )
        link_mode = "macos-install-name-and-rpath"
    else:
        library = _install_mujoco_linux(archive, drone_root, version)
        link_mode = "runtime-library-path"

    return {
        "mode": mode,
        "requirements": str(contract.path),
        "version_authority": str(mujoco.version_file),
        "version": version,
        "asset": asset_name,
        "sha256": expected_sha256,
        "library": str(library.absolute()),
        "link_mode": link_mode,
    }


def prepare_native_distribution(
    drone_root: Path,
    system_name: str,
    *,
    cache_root: Path | None = None,
    evidence_path: Path | None = None,
) -> int:
    """Materialize the current Drone workspace and its verified native runtime."""
    if system_name not in SUPPORTED_NATIVE_SYSTEMS:
        raise RecipeError(
            f"unsupported native operating system: {system_name}; "
            "drone-fleet-single-host supports macOS and Linux"
        )
    profile = PUBLIC_DRONE_ARCHIVES.get(system_name)
    if profile is None:
        raise RecipeError(f"unsupported native operating system: {system_name}")
    workspace_evidence = prepare_drone_workspace(drone_root)

    archive_name, expected_sha256 = profile
    url = (
        "https://github.com/toppers/hakoniwa-drone-core/releases/download/"
        f"{PUBLIC_DRONE_RELEASE}/{archive_name}"
    )
    resolved_cache_root = cache_root or ROOT / "work" / "downloads"
    archive = resolved_cache_root / "hakoniwa-drone-core" / PUBLIC_DRONE_RELEASE / archive_name
    try:
        native_mode = _verified_download(url, archive, expected_sha256)
        _safe_extract(archive, drone_root)
    except (OSError, urllib.error.URLError, zipfile.BadZipFile) as exc:
        raise RecipeError(f"failed to prepare native Drone distribution: {exc}") from exc

    for candidate in (
        *binary_candidates(drone_root, system_name),
        *visual_state_publisher_candidates(drone_root, system_name),
    ):
        if candidate.is_file():
            candidate.chmod(candidate.stat().st_mode | 0o111)
    service = resolve_drone_binary(drone_root, system_name)
    vsp = resolve_visual_state_publisher(drone_root, system_name)
    mujoco_evidence = materialize_mujoco_runtime(
        drone_root, system_name, resolved_cache_root
    )
    evidence = {
        "schema_version": 1,
        "recipe": RECIPE_ID,
        "platform": system_name,
        "drone_workspace": workspace_evidence,
        "native_distribution": {
            "mode": native_mode,
            "release": PUBLIC_DRONE_RELEASE,
            "platform": system_name,
            "archive": archive_name,
            "sha256": expected_sha256,
            "service": {
                "path": str(service),
                "sha256": _sha256(service),
            },
            "visual_state_publisher": {
                "path": str(vsp),
                "sha256": _sha256(vsp),
            },
        },
        "mujoco_runtime": mujoco_evidence,
    }
    if evidence_path is not None:
        _atomic_json(evidence_path, evidence)
        print(f"[OK] provenance evidence: {evidence_path}")
    print("Drone workspace:")
    print(f"  mode: {workspace_evidence['mode']}")
    print(f"  repository: {workspace_evidence['repository']}")
    print(f"  requested ref: {workspace_evidence['requested_ref']}")
    print(f"  resolved revision: {workspace_evidence['resolved_revision']}")
    print("Native distribution:")
    print(f"  mode: {native_mode}")
    print(f"  release: {PUBLIC_DRONE_RELEASE}")
    print(f"  platform: {system_name}")
    print(f"  sha256: {expected_sha256}")
    print("MuJoCo runtime:")
    print(f"  mode: {mujoco_evidence['mode']}")
    print(f"  version: {mujoco_evidence['version']}")
    print(f"  authority: {mujoco_evidence['version_authority']}")
    print(f"[OK] native drone service: {service}")
    print(f"[OK] visual-state publisher: {vsp}")
    return 0


def viewer_required_files(viewer_root: Path) -> tuple[Path, ...]:
    pdu_root = viewer_root / "thirdparty" / "hakoniwa-pdu-javascript" / "src" / "pdu_msgs"
    return (
        viewer_root / "index.html",
        pdu_root / "hako_msgs" / "pdu_jstype_Disturbance.js",
        pdu_root / "hako_msgs" / "pdu_jstype_DisturbanceUserCustom.js",
        pdu_root / "geometry_msgs" / "pdu_conv_Twist.js",
        pdu_root / "hako_mavlink_msgs" / "pdu_conv_HakoHilActuatorControls.js",
    )


def prepare_viewer(viewer_root: Path) -> int:
    if not viewer_root.exists():
        viewer_root.parent.mkdir(parents=True, exist_ok=True)
        _run_checked(
            [
                "git",
                "clone",
                "--recurse-submodules",
                THREEJS_VIEWER_REPOSITORY,
                str(viewer_root),
            ],
            cwd=viewer_root.parent,
        )
    elif (viewer_root / ".git").exists():
        _run_checked(
            ["git", "submodule", "update", "--init", "--recursive"],
            cwd=viewer_root,
        )
    missing = [path for path in viewer_required_files(viewer_root) if not path.is_file()]
    if missing:
        raise RecipeError(
            "Three.js viewer is incomplete after submodule preparation; missing: "
            + ", ".join(str(path) for path in missing)
        )
    print(f"[OK] Three.js viewer and PDU JavaScript: {viewer_root}")
    return 0


def _parse_scalar(value: str) -> Any:
    value = value.strip()
    if value == "":
        return {}
    if value in {"true", "false"}:
        return value == "true"
    if value in {"null", "~"}:
        return None
    if (value.startswith('"') and value.endswith('"')) or (
        value.startswith("'") and value.endswith("'")
    ):
        return value[1:-1]
    if value.startswith("[") and value.endswith("]"):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError as exc:
            raise RecipeError(f"invalid inline list: {value}") from exc
        if not isinstance(parsed, list):
            raise RecipeError(f"inline value must be a list: {value}")
        return parsed
    try:
        return int(value)
    except ValueError:
        pass
    try:
        return float(value)
    except ValueError:
        return value


def load_simple_yaml(path: Path) -> dict[str, Any]:
    """Load the dependency-free YAML subset used by Recipe-owned contracts."""
    if not path.is_file():
        raise RecipeError(f"YAML file not found: {path}")
    root: dict[str, Any] = {}
    stack: list[tuple[int, dict[str, Any]]] = [(-1, root)]
    for line_number, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        indent = len(raw) - len(raw.lstrip(" "))
        if "\t" in raw[:indent] or indent % 2:
            raise RecipeError(f"{path}:{line_number}: indentation must use two spaces")
        text = raw.strip()
        if text.startswith("-") or ":" not in text:
            raise RecipeError(
                f"{path}:{line_number}: YAML supports mappings, scalars, and inline lists only"
            )
        key, value = text.split(":", 1)
        key = key.strip()
        if not key:
            raise RecipeError(f"{path}:{line_number}: empty key")
        while stack and indent <= stack[-1][0]:
            stack.pop()
        if not stack:
            raise RecipeError(f"{path}:{line_number}: invalid indentation")
        parent = stack[-1][1]
        if key in parent:
            raise RecipeError(f"{path}:{line_number}: duplicate key: {key}")
        parsed = _parse_scalar(value)
        parent[key] = parsed
        if isinstance(parsed, dict):
            stack.append((indent, parsed))
    return root


def _mapping(root: dict[str, Any], key: str) -> dict[str, Any]:
    value = root.get(key)
    if not isinstance(value, dict):
        raise RecipeError(f"experiment.{key} must be a mapping")
    return value


def _require_fields(section: dict[str, Any], label: str, allowed: set[str]) -> None:
    unknown = sorted(set(section) - allowed)
    if unknown:
        raise RecipeError(f"unknown {label} fields: {', '.join(unknown)}")


def resolve_experiment(
    path: Path,
    *,
    drone_count_override: int | None = None,
    process_count_override: int | None = None,
    formation_scale_override: float | None = None,
) -> Experiment:
    root = load_simple_yaml(path)
    _require_fields(
        root,
        "root",
        {
            "version", "experiment", "scale", "runtime", "scenario", "results",
            "measurement", "matrix", "resolved",
        },
    )
    if root.get("version") != 1:
        raise RecipeError("experiment version must be 1")
    identity = _mapping(root, "experiment")
    scale = _mapping(root, "scale")
    runtime = _mapping(root, "runtime")
    scenario = _mapping(root, "scenario")
    results = _mapping(root, "results")
    measurement_raw = root.get("measurement")
    _require_fields(identity, "experiment", {"id"})
    _require_fields(
        scale, "scale", {"drone_count", "drones_per_process", "process_count"}
    )
    _require_fields(
        runtime,
        "runtime",
        {
            "mode",
            "visualization",
            "show_runner_real_time_sync",
        },
    )
    _require_fields(
        scenario,
        "scenario",
        {
            "type",
            "word",
            "letter_width_m",
            "letter_height_m",
            "letter_gap_m",
            "altitude_m",
            "duration_sec",
            "hold_sec",
            "speed_m_s",
            "timeout_sec",
            "land",
        },
    )
    _require_fields(results, "results", {"enabled", "directory"})

    configured_drone_count = scale.get("drone_count")
    drones_per_process = scale.get("drones_per_process")
    configured_process_count = scale.get("process_count")
    if configured_drone_count == "auto":
        if (
            not isinstance(drones_per_process, int)
            or isinstance(drones_per_process, bool)
            or drones_per_process < 1
        ):
            raise RecipeError(
                "scale.drones_per_process must be an integer >= 1 when drone_count=auto"
            )
        if (
            not isinstance(configured_process_count, int)
            or isinstance(configured_process_count, bool)
            or configured_process_count < 1
        ):
            raise RecipeError(
                "scale.process_count must be an integer >= 1 when drone_count=auto"
            )
        process_count = configured_process_count
        drone_count = drones_per_process * process_count
    elif (
        isinstance(configured_drone_count, int)
        and not isinstance(configured_drone_count, bool)
    ):
        drone_count = configured_drone_count
        if drone_count < 1:
            raise RecipeError("scale.drone_count must be >= 1")
        if configured_process_count == "auto":
            if (
                not isinstance(drones_per_process, int)
                or isinstance(drones_per_process, bool)
                or drones_per_process < 1
            ):
                raise RecipeError(
                    "scale.drones_per_process must be an integer >= 1 when process_count=auto"
                )
            process_count = math.ceil(drone_count / drones_per_process)
        elif (
            isinstance(configured_process_count, int)
            and not isinstance(configured_process_count, bool)
            and 1 <= configured_process_count <= max(1, drone_count)
        ):
            process_count = configured_process_count
        else:
            raise RecipeError(
                "scale.process_count must be auto or an integer in [1, drone_count]"
            )
        if drones_per_process is None or drones_per_process == "auto":
            drones_per_process = math.ceil(drone_count / process_count)
        elif (
            not isinstance(drones_per_process, int)
            or isinstance(drones_per_process, bool)
            or drones_per_process < 1
        ):
            raise RecipeError(
                "scale.drones_per_process must be auto or an integer >= 1"
            )
    else:
        raise RecipeError(
            "scale.drone_count must be auto or an integer"
        )
    if drone_count < 1:
        raise RecipeError("resolved scale.drone_count must be >= 1")
    if drone_count > GENERAL_USER_MAX_DRONES:
        raise RecipeError(
            "resolved scale.drone_count exceeds the general-user limit of "
            f"{GENERAL_USER_MAX_DRONES}; use a separately built and verified "
            "512-drone Core/Drone/VSP/Foundation profile and its Hakoniwa "
            "Drone PRO research Recipe instead of the public default binaries; "
            "a PRO license and PRO source access are required"
        )
    if drone_count_override is not None:
        if not 1 <= drone_count_override <= GENERAL_USER_MAX_DRONES:
            raise RecipeError(
                "--drone-count must be in "
                f"[1, {GENERAL_USER_MAX_DRONES}]"
            )
        drone_count = drone_count_override
    if process_count_override is not None:
        if not 1 <= process_count_override <= drone_count:
            raise RecipeError("--process-count must be in [1, drone_count]")
        process_count = process_count_override
    drones_per_process = math.ceil(drone_count / process_count)

    experiment_id = identity.get("id")
    if not isinstance(experiment_id, str) or not experiment_id:
        raise RecipeError("experiment.id must be a non-empty string")
    runtime_mode = runtime.get("mode")
    if runtime_mode != "native":
        raise RecipeError("runtime.mode must be native")
    visualization = runtime.get("visualization")
    if not isinstance(visualization, bool):
        raise RecipeError("runtime.visualization must be boolean")
    show_runner_real_time_sync = runtime.get("show_runner_real_time_sync")
    if not isinstance(show_runner_real_time_sync, bool):
        raise RecipeError("runtime.show_runner_real_time_sync must be boolean")
    if scenario.get("type") != "hakoniwa-word":
        raise RecipeError("scenario.type must be hakoniwa-word")
    word = scenario.get("word")
    if word != "HAKONIWA":
        raise RecipeError("scenario.word must be HAKONIWA for this MVP Recipe")

    def number(name: str, *, minimum: float) -> float:
        value = scenario.get(name)
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise RecipeError(f"scenario.{name} must be a number")
        result = float(value)
        if result < minimum:
            raise RecipeError(f"scenario.{name} must be >= {minimum}")
        return result

    if formation_scale_override is not None:
        if not 0.25 <= formation_scale_override <= 10.0:
            raise RecipeError("--formation-scale must be in [0.25, 10.0]")
        formation_scale = float(formation_scale_override)
    else:
        formation_scale = 1.0

    land = scenario.get("land")
    if not isinstance(land, bool):
        raise RecipeError("scenario.land must be boolean")
    results_enabled = results.get("enabled")
    if not isinstance(results_enabled, bool):
        raise RecipeError("results.enabled must be boolean")
    results_directory = results.get("directory")
    if not isinstance(results_directory, str) or not results_directory:
        raise RecipeError("results.directory must be a non-empty relative path")
    if Path(results_directory).is_absolute() or ".." in Path(results_directory).parts:
        raise RecipeError("results.directory must stay inside the Recipe workspace")

    measurement: PerformanceMeasurement | None = None
    if measurement_raw is not None:
        if not isinstance(measurement_raw, dict):
            raise RecipeError("experiment.measurement must be a mapping")
        _require_fields(
            measurement_raw,
            "measurement",
            {
                "enabled", "mode", "series", "configuration_id", "attempt",
                "protocol_status", "sampling_interval_sec", "warmup_virtual_time_sec",
                "temporal_sampling_interval_usec",
                "preflight_duration_sec", "preflight_settle_timeout_sec",
                "stop_conditions",
                "invalid_conditions", "time_coordination",
            },
        )
        enabled = measurement_raw.get("enabled")
        if not isinstance(enabled, bool):
            raise RecipeError("measurement.enabled must be boolean")
        if enabled:
            coordination = _mapping(measurement_raw, "time_coordination")
            stop_conditions = _mapping(measurement_raw, "stop_conditions")
            invalid_conditions = _mapping(measurement_raw, "invalid_conditions")
            _require_fields(
                coordination,
                "measurement.time_coordination",
                {
                    "drone_delta_time_usec", "fleet_delta_time_usec",
                    "conductor_delta_time_usec", "conductor_max_delay_time_usec",
                    "conductor_real_sleep_msec", "simtime_publish_mode",
                    "conductor_implementation",
                },
            )
            _require_fields(
                stop_conditions,
                "measurement.stop_conditions",
                {
                    "minimum_virtual_time_sec", "minimum_cpu_sample_count",
                    "minimum_machine_sample_count",
                },
            )
            _require_fields(
                invalid_conditions,
                "measurement.invalid_conditions",
                {
                    "maximum_virtual_time_sec", "maximum_wall_time_sec",
                    "preflight_max_cpu_average_percent",
                    "preflight_max_memory_used_percent",
                },
            )
            def positive_int(mapping: dict[str, Any], key: str, *, allow_zero: bool = False) -> int:
                value = mapping.get(key)
                minimum = 0 if allow_zero else 1
                if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
                    raise RecipeError(f"measurement.time_coordination.{key} must be an integer >= {minimum}")
                return value

            def nonempty(key: str) -> str:
                value = measurement_raw.get(key)
                if not isinstance(value, str) or not value:
                    raise RecipeError(f"measurement.{key} must be a non-empty string")
                return value

            attempt = measurement_raw.get("attempt")
            if isinstance(attempt, bool) or not isinstance(attempt, int) or attempt < 1:
                raise RecipeError("measurement.attempt must be an integer >= 1")
            sampling = measurement_raw.get("sampling_interval_sec")
            preflight_duration = measurement_raw.get("preflight_duration_sec")
            preflight_settle_timeout = measurement_raw.get(
                "preflight_settle_timeout_sec", preflight_duration
            )
            preflight_max_cpu = invalid_conditions.get("preflight_max_cpu_average_percent")
            preflight_max_memory = invalid_conditions.get("preflight_max_memory_used_percent")
            minimum_virtual_time = stop_conditions.get("minimum_virtual_time_sec")
            minimum_cpu_samples = stop_conditions.get("minimum_cpu_sample_count")
            minimum_machine_samples = stop_conditions.get("minimum_machine_sample_count")
            maximum_virtual_time = invalid_conditions.get("maximum_virtual_time_sec")
            maximum_wall_time = invalid_conditions.get("maximum_wall_time_sec")
            warmup = measurement_raw.get("warmup_virtual_time_sec")
            if isinstance(sampling, bool) or not isinstance(sampling, (int, float)) or sampling <= 0:
                raise RecipeError("measurement.sampling_interval_sec must be > 0")
            if (
                isinstance(preflight_duration, bool)
                or not isinstance(preflight_duration, (int, float))
                or preflight_duration <= 0
            ):
                raise RecipeError("measurement.preflight_duration_sec must be > 0")
            if (
                isinstance(preflight_settle_timeout, bool)
                or not isinstance(preflight_settle_timeout, (int, float))
                or preflight_settle_timeout < preflight_duration
            ):
                raise RecipeError(
                    "measurement.preflight_settle_timeout_sec must be >= "
                    "preflight_duration_sec"
                )
            for key, value in (
                ("preflight_max_cpu_average_percent", preflight_max_cpu),
                ("preflight_max_memory_used_percent", preflight_max_memory),
            ):
                if (
                    isinstance(value, bool)
                    or not isinstance(value, (int, float))
                    or not 0 <= value <= 100
                ):
                    raise RecipeError(f"measurement.{key} must be between 0 and 100")
            if (
                isinstance(minimum_virtual_time, bool)
                or not isinstance(minimum_virtual_time, (int, float))
                or minimum_virtual_time <= 0
            ):
                raise RecipeError("measurement.stop_conditions.minimum_virtual_time_sec must be > 0")
            for key, value in (
                ("minimum_cpu_sample_count", minimum_cpu_samples),
                ("minimum_machine_sample_count", minimum_machine_samples),
            ):
                if isinstance(value, bool) or not isinstance(value, int) or value < 1:
                    raise RecipeError(
                        f"measurement.stop_conditions.{key} must be an integer >= 1"
                    )
            for key, value in (
                ("maximum_virtual_time_sec", maximum_virtual_time),
                ("maximum_wall_time_sec", maximum_wall_time),
            ):
                if (
                    isinstance(value, bool)
                    or not isinstance(value, (int, float))
                    or value <= 0
                ):
                    raise RecipeError(f"measurement.invalid_conditions.{key} must be > 0")
            if maximum_virtual_time <= minimum_virtual_time:
                raise RecipeError(
                    "measurement.invalid_conditions.maximum_virtual_time_sec must be greater than minimum_virtual_time_sec"
                )
            if isinstance(warmup, bool) or not isinstance(warmup, (int, float)) or warmup < 0:
                raise RecipeError("measurement.warmup_virtual_time_sec must be >= 0")
            fleet_delta = positive_int(coordination, "fleet_delta_time_usec")
            warmup_usec = int(round(float(warmup) * 1_000_000))
            if warmup_usec % fleet_delta:
                raise RecipeError("measurement warmup must align with fleet_delta_time_usec")
            mode = measurement_raw.get("mode")
            if mode not in {"performance", "temporal"}:
                raise RecipeError(
                    "single-host measurement.mode must be performance or temporal"
                )
            temporal_sampling = measurement_raw.get(
                "temporal_sampling_interval_usec"
            )
            if mode == "temporal":
                if (
                    isinstance(temporal_sampling, bool)
                    or not isinstance(temporal_sampling, int)
                    or temporal_sampling < fleet_delta
                    or temporal_sampling % fleet_delta
                ):
                    raise RecipeError(
                        "temporal measurement requires "
                        "temporal_sampling_interval_usec to be a positive multiple "
                        "of fleet_delta_time_usec"
                    )
            elif temporal_sampling is not None:
                raise RecipeError(
                    "performance measurement must not set "
                    "temporal_sampling_interval_usec"
                )
            if visualization or show_runner_real_time_sync or land or not results_enabled:
                raise RecipeError(
                    "performance measurement requires visualization=false, "
                    "show_runner_real_time_sync=false, land=false, and results.enabled=true"
                )
            if fleet_delta != 20_000:
                raise RecipeError("the current ShowRunner contract requires fleet_delta_time_usec=20000")
            if coordination.get("conductor_implementation") != "embedded":
                raise RecipeError("single-host measurement requires conductor_implementation=embedded")
            if coordination.get("simtime_publish_mode") != "not_applicable":
                raise RecipeError("embedded single-host measurement requires simtime_publish_mode=not_applicable")
            configuration_id = nonempty("configuration_id")
            if configuration_id == "auto":
                configuration_id = (
                    f"uav-{drone_count:03d}-proc-{process_count:02d}"
                )
            measurement = PerformanceMeasurement(
                mode=str(mode),
                series=nonempty("series"),
                configuration_id=configuration_id,
                attempt=attempt,
                protocol_status=nonempty("protocol_status"),
                sampling_interval_sec=float(sampling),
                temporal_sampling_interval_usec=temporal_sampling,
                preflight_duration_sec=float(preflight_duration),
                preflight_settle_timeout_sec=float(preflight_settle_timeout),
                preflight_max_cpu_average_percent=float(preflight_max_cpu),
                preflight_max_memory_used_percent=float(preflight_max_memory),
                minimum_virtual_time_sec=float(minimum_virtual_time),
                minimum_cpu_sample_count=minimum_cpu_samples,
                minimum_machine_sample_count=minimum_machine_samples,
                maximum_virtual_time_sec=float(maximum_virtual_time),
                maximum_wall_time_sec=float(maximum_wall_time),
                warmup_virtual_time_sec=float(warmup),
                drone_delta_time_usec=positive_int(coordination, "drone_delta_time_usec"),
                fleet_delta_time_usec=fleet_delta,
                conductor_delta_time_usec=positive_int(coordination, "conductor_delta_time_usec"),
                conductor_max_delay_time_usec=positive_int(coordination, "conductor_max_delay_time_usec", allow_zero=True),
                conductor_real_sleep_msec=positive_int(coordination, "conductor_real_sleep_msec", allow_zero=True),
                simtime_publish_mode=str(coordination["simtime_publish_mode"]),
                conductor_implementation=str(coordination["conductor_implementation"]),
            )

    return Experiment(
        experiment_id=experiment_id,
        drone_count=drone_count,
        drones_per_process=drones_per_process,
        process_count=process_count,
        runtime_mode=runtime_mode,
        visualization=visualization,
        show_runner_real_time_sync=show_runner_real_time_sync,
        scenario_type=str(scenario["type"]),
        word=word,
        letter_width_m=number("letter_width_m", minimum=0.001) * formation_scale,
        letter_height_m=number("letter_height_m", minimum=0.001) * formation_scale,
        letter_gap_m=number("letter_gap_m", minimum=0.0) * formation_scale,
        altitude_m=number("altitude_m", minimum=0.5),
        duration_sec=number("duration_sec", minimum=0.001),
        hold_sec=number("hold_sec", minimum=0.0),
        speed_m_s=number("speed_m_s", minimum=0.001),
        timeout_sec=number("timeout_sec", minimum=1.0),
        land=land,
        results_enabled=results_enabled,
        results_directory=results_directory,
        measurement=measurement,
    )


def next_pow2(value: int) -> int:
    return 1 if value <= 1 else 1 << (value - 1).bit_length()


def required_build_limits(experiment: Experiment) -> dict[str, int]:
    service_total = 5 * experiment.drone_count
    channel_total = 19 * experiment.drone_count + 2 * service_total + 4
    # Drone services + ShowRunner, plus VSP + WebBridge when visualization is
    # enabled. The first Drone service owns the built-in Conductor; this
    # single-host topology launches no separate Conductor Client asset. The
    # after-start HTTP server is not a Hakoniwa asset.
    runtime_assets = experiment.process_count + 1 + (2 if experiment.visualization else 0)
    return {
        "asset_num": max(16, next_pow2(runtime_assets)),
        "pdu_channel_max": next_pow2(channel_total),
        "recv_event_max": max(1024, next_pow2(next_pow2(service_total) * 4)),
        "service_client_max": max(128, next_pow2(experiment.drone_count)),
        "service_max": next_pow2(service_total),
        "client_name_len_max": 64,
        "service_name_len_max": 128,
    }


def _yaml_scalar(value: Any) -> str:
    if value is True:
        return "true"
    if value is False:
        return "false"
    if value is None:
        return "null"
    if isinstance(value, list):
        return json.dumps(value, ensure_ascii=False)
    if isinstance(value, str):
        return json.dumps(value, ensure_ascii=False)
    return str(value)


def write_simple_yaml(path: Path, value: dict[str, Any]) -> None:
    lines: list[str] = []

    def emit(mapping: dict[str, Any], indent: int) -> None:
        for key, child in mapping.items():
            prefix = " " * indent + f"{key}:"
            if isinstance(child, dict):
                lines.append(prefix)
                emit(child, indent + 2)
            else:
                lines.append(prefix + " " + _yaml_scalar(child))

    emit(value, 0)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def resolved_experiment_dict(experiment: Experiment) -> dict[str, Any]:
    resolved = {
        "version": 1,
        "experiment": {"id": experiment.experiment_id},
        "scale": {
            "drone_count": experiment.drone_count,
            "drones_per_process": experiment.drones_per_process,
            "process_count": experiment.process_count,
        },
        "runtime": {
            "mode": experiment.runtime_mode,
            "visualization": experiment.visualization,
            "show_runner_real_time_sync": experiment.show_runner_real_time_sync,
        },
        "scenario": {
            "type": experiment.scenario_type,
            "word": experiment.word,
            "letter_width_m": experiment.letter_width_m,
            "letter_height_m": experiment.letter_height_m,
            "letter_gap_m": experiment.letter_gap_m,
            "altitude_m": experiment.altitude_m,
            "duration_sec": experiment.duration_sec,
            "hold_sec": experiment.hold_sec,
            "speed_m_s": experiment.speed_m_s,
            "timeout_sec": experiment.timeout_sec,
            "land": experiment.land,
        },
        "results": {
            "enabled": experiment.results_enabled,
            "directory": experiment.results_directory,
        },
        "resolved": {"foundation_build_limits": required_build_limits(experiment)},
    }
    if experiment.measurement is not None:
        measurement = experiment.measurement
        resolved["measurement"] = {
            "enabled": True,
            "mode": measurement.mode,
            "series": measurement.series,
            "configuration_id": measurement.configuration_id,
            "attempt": measurement.attempt,
            "protocol_status": measurement.protocol_status,
            "sampling_interval_sec": measurement.sampling_interval_sec,
            "preflight_duration_sec": measurement.preflight_duration_sec,
            "preflight_settle_timeout_sec": (
                measurement.preflight_settle_timeout_sec
            ),
            "stop_conditions": {
                "minimum_virtual_time_sec": measurement.minimum_virtual_time_sec,
                "minimum_cpu_sample_count": measurement.minimum_cpu_sample_count,
                "minimum_machine_sample_count": measurement.minimum_machine_sample_count,
            },
            "invalid_conditions": {
                "maximum_virtual_time_sec": measurement.maximum_virtual_time_sec,
                "maximum_wall_time_sec": measurement.maximum_wall_time_sec,
                "preflight_max_cpu_average_percent": measurement.preflight_max_cpu_average_percent,
                "preflight_max_memory_used_percent": measurement.preflight_max_memory_used_percent,
            },
            "warmup_virtual_time_sec": measurement.warmup_virtual_time_sec,
            "time_coordination": {
                "drone_delta_time_usec": measurement.drone_delta_time_usec,
                "fleet_delta_time_usec": measurement.fleet_delta_time_usec,
                "conductor_delta_time_usec": measurement.conductor_delta_time_usec,
                "conductor_max_delay_time_usec": measurement.conductor_max_delay_time_usec,
                "conductor_real_sleep_msec": measurement.conductor_real_sleep_msec,
                "simtime_publish_mode": measurement.simtime_publish_mode,
                "conductor_implementation": measurement.conductor_implementation,
            },
        }
        if measurement.temporal_sampling_interval_usec is not None:
            resolved["measurement"]["temporal_sampling_interval_usec"] = (
                measurement.temporal_sampling_interval_usec
            )
    return resolved


def measurement_trial_dir(paths, experiment: Experiment) -> Path | None:
    measurement = experiment.measurement
    if measurement is None:
        return None
    return (
        paths.recipe_root
        / experiment.results_directory
        / measurement.series
        / measurement.configuration_id
        / f"attempt-{measurement.attempt:02d}"
    )


def write_measurement_config(paths, experiment: Experiment) -> Path | None:
    trial = measurement_trial_dir(paths, experiment)
    if trial is None:
        return None
    payload = resolved_experiment_dict(experiment)["measurement"] | {
        "experiment_id": experiment.experiment_id,
        "drone_count": experiment.drone_count,
        "process_count": experiment.process_count,
        "trial_directory": str(trial),
    }
    output = paths.recipe_config / "measurement.json"
    output.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return output


def expected_partition_counts(drone_count: int, process_count: int) -> list[int]:
    try:
        return fleet_runtime.expected_partition_counts(drone_count, process_count)
    except ValueError as exc:
        raise RecipeError(str(exc)) from exc


def validate_materialized_experiment(paths, experiment: Experiment) -> list[str]:
    """Return actionable errors for stale or incomplete generated Recipe state."""
    errors: list[str] = []
    resolved_path = paths.recipe_config / "resolved-experiment.yaml"
    if resolved_path.is_file():
        try:
            actual_resolved = load_simple_yaml(resolved_path)
        except RecipeError as exc:
            errors.append(f"invalid {resolved_path}: {exc}")
        else:
            if actual_resolved != resolved_experiment_dict(experiment):
                errors.append(
                    "experiment YAML differs from the configured Recipe workspace; "
                    "run configure after changing scale or runtime settings"
                )

    errors.extend(
        fleet_runtime.validate_partitions(
            paths.recipe_config,
            fleet_runtime.single_host_spec(experiment),
        )
    )
    return errors


def write_foundation_requirements(path: Path, experiment: Experiment) -> None:
    limits = required_build_limits(experiment)
    requirements: dict[str, Any] = {"foundation_requirements": {}}
    components = [
        (
            "hakoniwa-core-pro",
            {
                "shared_memory": True,
                "hako_cmd": True,
                "python_binding": True,
                **({"measurement_library": True} if experiment.measurement else {}),
            },
        ),
        (
            "hakoniwa-pdu-python",
            {
                "hako_launcher": True,
                "launcher_background_lifecycle": True,
                "shm_backend": True,
                "external_rpc": True,
            },
        ),
        (
            "hakoniwa-pdu-endpoint",
            {"hakoniwa_core": True, "core_callback": True},
        ),
    ]
    if experiment.visualization:
        components.append(
            (
                "hakoniwa-pdu-bridge-core",
                {
                    "hakoniwa_app": True,
                    "web_bridge": True,
                    "web_bridge_fleets_config_format": True,
                },
            )
        )
    for component, capabilities in components:
        body: dict[str, Any] = {"capabilities": capabilities, "build_limits": {}}
        for key, minimum in limits.items():
            body["build_limits"][key] = {"min": minimum}
        requirements["foundation_requirements"][component] = body
    requirements["foundation_requirements"]["hakoniwa-pdu-python"]["version"] = {
        "min": "1.6.5"
    }
    write_simple_yaml(path, requirements)


def _run(command: list[str], *, cwd: Path | None = None, env=None) -> int:
    print("+", subprocess.list2cmdline(command))
    return subprocess.run(command, cwd=cwd, env=env, check=False).returncode


def _run_checked(command: list[str], *, cwd: Path | None = None) -> None:
    if _run(command, cwd=cwd) != 0:
        raise RecipeError(f"command failed: {subprocess.list2cmdline(command)}")


def prepare_config(paths, drone_root: Path, experiment: Experiment) -> None:
    try:
        fleet_runtime.prepare_config(
            paths,
            drone_root,
            fleet_runtime.single_host_spec(experiment),
            run_checked=_run_checked,
            scenario_writer=lambda: write_generated_scenario(
                paths, drone_root, experiment
            ),
        )
    except (FileNotFoundError, ValueError) as exc:
        raise RecipeError(str(exc)) from exc


def write_generated_scenario(paths, drone_root: Path, experiment: Experiment) -> Path:
    if experiment.drone_count < HAKONIWA_STROKE_COUNT:
        print(
            "[WARN] HAKONIWA has 26 stroke segments; with fewer than 26 "
            "drones, evenly sampled strokes are used and the complete word "
            "will not be visible."
        )
    elif experiment.drone_count < (
        HAKONIWA_STROKE_COUNT * RECOMMENDED_DRONES_PER_STROKE
    ):
        print(
            "[WARN] HAKONIWA formation uses fewer than two drones per stroke; "
            "52 or more drones are recommended for readability."
        )
    # Keep the established warning text at the single-host UI boundary while
    # delegating the actual scenario materialization to the shared runtime.
    try:
        return fleet_runtime.prepare_scenario(
            paths,
            drone_root,
            fleet_runtime.ScenarioRuntimeSpec(
                experiment_id=experiment.experiment_id,
                local_drone_count=experiment.drone_count,
                word=experiment.word,
                letter_width_m=experiment.letter_width_m,
                letter_height_m=experiment.letter_height_m,
                letter_gap_m=experiment.letter_gap_m,
                altitude_m=experiment.altitude_m,
                duration_sec=experiment.duration_sec,
                hold_sec=experiment.hold_sec,
                speed_m_s=experiment.speed_m_s,
                stroke_count=HAKONIWA_STROKE_COUNT,
                recommended_drones_per_stroke=RECOMMENDED_DRONES_PER_STROKE,
            ),
            run_checked=_run_checked,
        )
    except (FileNotFoundError, ValueError) as exc:
        raise RecipeError(str(exc)) from exc


def binary_candidates(drone_root: Path, system_name: str) -> tuple[Path, ...]:
    if system_name == "Darwin":
        name, folder = "mac-main_hako_drone_service", "mac"
    elif system_name == "Linux":
        name, folder = "linux-main_hako_drone_service", "lnx"
    elif system_name == "Windows":
        name, folder = "win-main_hako_drone_service.exe", "win"
    else:
        raise RecipeError(f"unsupported native operating system: {system_name}")
    # prepare-native extracts the verified public archive into its OS folder.
    # Prefer that materialized artifact over an unrelated pre-existing lib copy.
    return (
        drone_root / folder / name,
        drone_root / "lib" / name,
        drone_root / ".hako" / "install" / "bin" / name,
    )


def visual_state_publisher_candidates(
    drone_root: Path, system_name: str
) -> tuple[Path, ...]:
    if system_name == "Darwin":
        name, folder = "mac-drone_visual_state_publisher", "mac"
    elif system_name == "Linux":
        name, folder = "linux-drone_visual_state_publisher", "lnx"
    elif system_name == "Windows":
        name, folder = "win-drone_visual_state_publisher.exe", "win"
    else:
        raise RecipeError(f"unsupported native operating system: {system_name}")
    return (
        drone_root / folder / name,
        drone_root / "lib" / name,
        drone_root / ".hako" / "install" / "bin" / name,
    )


def resolve_visual_state_publisher(drone_root: Path, system_name: str) -> Path:
    for candidate in visual_state_publisher_candidates(drone_root, system_name):
        if candidate.is_file():
            return candidate.absolute()
    name = visual_state_publisher_candidates(drone_root, system_name)[0].name
    discovered = shutil.which(name)
    if discovered:
        return Path(discovered).absolute()
    raise RecipeError(
        "native visual-state publisher not found; checked: "
        + ", ".join(
            str(path) for path in visual_state_publisher_candidates(drone_root, system_name)
        )
        + "; run 'python tools/recipe/drone_fleet_single_host.py prepare-native' "
        + "to install the pinned public distribution"
    )


def web_bridge_path(paths, system_name: str) -> Path:
    suffix = ".exe" if system_name == "Windows" else ""
    return paths.install_prefix / "bin" / f"hakoniwa-pdu-web-bridge{suffix}"


def bridge_config_root(paths) -> Path:
    return (
        paths.install_prefix
        / "share"
        / "hakoniwa-pdu-bridge"
        / "config"
        / "web_bridge_fleets"
    )


def _port_available(port: int) -> bool | None:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind(("127.0.0.1", port))
        except PermissionError:
            return None
        except OSError:
            return False
    return True


def resolve_drone_binary(drone_root: Path, system_name: str) -> Path:
    for candidate in binary_candidates(drone_root, system_name):
        if candidate.is_file():
            return candidate.absolute()
    executable_name = binary_candidates(drone_root, system_name)[0].name
    discovered = shutil.which(executable_name)
    if discovered:
        return Path(discovered).absolute()
    raise RecipeError(
        "native Drone service binary not found; checked: "
        + ", ".join(str(path) for path in binary_candidates(drone_root, system_name))
        + "; run 'python tools/recipe/drone_fleet_single_host.py prepare-native' "
        + "to install the pinned public distribution"
    )


def resolve_foundation_python(paths, system_name: str) -> Path:
    if system_name == "Windows":
        candidates = (
            paths.foundation_python / "Scripts" / "python.exe",
            paths.foundation_python / "python.exe",
        )
    else:
        candidates = (
            paths.foundation_python / "bin" / "python3",
            paths.foundation_python / "bin" / "python",
        )
    for candidate in candidates:
        if candidate.is_file():
            return Path(os.path.abspath(candidate))
    raise RecipeError("Foundation Python not found: " + ", ".join(map(str, candidates)))


def materialize_mujoco_city_viewer(paths, viewer_root: Path) -> Path:
    """Create a Recipe-local Map Viewer with a City-backed Three.js pane."""
    marker_path = paths.recipe_config / "mujoco-city-fleet.json"
    try:
        marker = json.loads(marker_path.read_text(encoding="utf-8"))
        city = marker["city_world"]
        city_glb = Path(str(city["glb"])).resolve()
    except (OSError, json.JSONDecodeError, KeyError, TypeError) as exc:
        raise RecipeError(f"invalid MuJoCo City viewer contract {marker_path}: {exc}") from exc
    if not city_glb.is_file():
        raise RecipeError(f"MuJoCo City GLB not found: {city_glb}")

    map_viewer_root = viewer_root.parent / "hakoniwa-map-viewer"
    map_client = map_viewer_root / "src" / "client"
    map_images = map_viewer_root / "images"
    if not map_client.is_dir() or not map_images.is_dir():
        raise RecipeError(
            "Hakoniwa Map Viewer is required by the MuJoCo City fleet viewer: "
            f"{map_viewer_root}"
        )

    web_root = paths.recipe_root / "web" / "map-viewer"
    web_root.mkdir(parents=True, exist_ok=True)
    shutil.copytree(map_client, web_root / "src" / "client", dirs_exist_ok=True)
    shutil.copytree(map_images, web_root / "images", dirs_exist_ok=True)

    embedded = web_root / "thirdparty" / "hakoniwa-threejs-drone"
    embedded.mkdir(parents=True, exist_ok=True)
    shutil.copy2(viewer_root / "index.html", embedded / "index.html")
    for dirname in ("src", "config", "assets", "thirdparty"):
        source = viewer_root / dirname
        if not source.exists():
            raise RecipeError(f"Three.js viewer resource not found: {source}")
        shutil.copytree(source, embedded / dirname, dirs_exist_ok=True)

    glb_destination = embedded / "assets" / "local_models" / "city-world.glb"
    glb_destination.parent.mkdir(parents=True, exist_ok=True)
    glb_destination.unlink(missing_ok=True)
    try:
        os.link(city_glb, glb_destination)
    except OSError:
        shutil.copy2(city_glb, glb_destination)

    scene_path = embedded / "config" / "drone_config-city-fleet.json"
    source_scene = viewer_root / "config" / "drone_config-compact-1.json"
    try:
        scene = json.loads(source_scene.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RecipeError(f"invalid Three.js scene {source_scene}: {exc}") from exc
    scene["environments"] = [
        {
            "name": "city-world",
            "model": "../assets/local_models/city-world.glb",
            "pos": [0, 0, 0],
            "hpr": [0, 0, 0],
        }
    ]
    half_extent = city.get("half_extent_m", {})
    camera_distance = max(
        30.0,
        float(half_extent.get("north_south", 100.0)),
        float(half_extent.get("east_west", 100.0)),
    )
    scene["main_camera"].update(
        {
            "initialMode": "fixed",
            "position": [-0.65 * camera_distance, -0.65 * camera_distance, 0.45 * camera_distance],
            "target": "Drone",
            "followDistance": 8.0,
        }
    )
    scene_path.write_text(json.dumps(scene, indent=2) + "\n", encoding="utf-8")

    viewer_config_path = embedded / "config" / "viewer-config-fleets.json"
    try:
        viewer_config = json.loads(
            (viewer_root / "config" / "viewer-config-fleets.json").read_text(
                encoding="utf-8"
            )
        )
    except (OSError, json.JSONDecodeError) as exc:
        raise RecipeError(f"invalid Three.js fleet viewer config: {exc}") from exc
    viewer_config["three"]["sceneConfigPath"] = "./drone_config-city-fleet.json"
    fleet_options = viewer_config.setdefault("stateInput", {}).setdefault(
        "fleets", {}
    )
    fleet_options.update(
        {
            "dynamicSpawn": True,
            "templateDroneIndex": 0,
            "maxDynamicDrones": int(marker["drone_count"]),
        }
    )
    viewer_config_path.write_text(
        json.dumps(viewer_config, indent=2) + "\n", encoding="utf-8"
    )

    # The Map Viewer is intentionally generic. Its Recipe-local copy only gets
    # the selected City World origin. DroneViewer owns PDU polling; adding a
    # second consumer here would race the consuming PDU buffer.
    map_ui_path = web_root / "src" / "client" / "src" / "ui.js"
    map_ui = map_ui_path.read_text(encoding="utf-8")
    origin = city.get("origin", {})
    origin_lat = float(origin["latitude"])
    origin_lon = float(origin["longitude"])
    map_ui = map_ui.replace(
        "const map = L.map('map').setView([35.6812, 139.7671], 15); // 東京駅",
        f"const map = L.map('map').setView([{origin_lat}, {origin_lon}], 17);",
    )
    map_ui = map_ui.replace(
        "let ORIGIN_LAT = 35.6625;   // zone の原点（仮）",
        f"let ORIGIN_LAT = {origin_lat};",
    )
    map_ui = map_ui.replace(
        "let ORIGIN_LON = 139.70625;",
        f"let ORIGIN_LON = {origin_lon};",
    )
    map_ui_path.write_text(map_ui, encoding="utf-8")
    return web_root


def write_launcher(
    paths,
    drone_root: Path,
    viewer_root: Path,
    experiment: Experiment,
    system_name: str,
) -> Path:
    drone_binary = resolve_drone_binary(drone_root, system_name)
    python = resolve_foundation_python(paths, system_name)
    trial = measurement_trial_dir(paths, experiment)
    summary = (
        trial / "execution-summary.json"
        if trial is not None
        else paths.recipe_validation / "execution-summary.json"
    )
    show_runner = (
        ROOT / "tools" / "recipe" / "assets" / "drone_fleet_performance_runner.py"
        if experiment.measurement is not None
        else drone_root / "drone_api" / "external_rpc" / "apps" / "show_asset_runner.py"
    )
    if not show_runner.is_file():
        raise RecipeError(f"Drone show runner not found: {show_runner}")
    visual_state_publisher = (
        resolve_visual_state_publisher(drone_root, system_name)
        if experiment.visualization
        else None
    )
    mujoco_city_mode = (paths.recipe_config / "mujoco-city-fleet.json").is_file()
    runtime_viewer_root = (
        materialize_mujoco_city_viewer(paths, viewer_root)
        if mujoco_city_mode and experiment.visualization
        else viewer_root
    )
    try:
        return fleet_runtime.prepare_launcher(
            paths,
            drone_root,
            runtime_viewer_root,
            fleet_runtime.LauncherRuntimeSpec(
                local_drone_count=experiment.drone_count,
                process_count=experiment.process_count,
                visualization=experiment.visualization,
                external_conductor=False,
                web_bridge=experiment.visualization,
                viewer=experiment.visualization,
                show_runner_real_time_sync=experiment.show_runner_real_time_sync,
                land=experiment.land,
                speed_m_s=experiment.speed_m_s,
                timeout_sec=experiment.timeout_sec,
                # A normal show-runner exit terminates every Launcher asset.
                # Keep the non-ICRA City demo at its final formation so its
                # browser viewer remains available until an explicit stop.
                final_hold_extra_sec=86400.0 if mujoco_city_mode else 0.0,
            ),
            drone_binary=drone_binary,
            python=python,
            show_runner=show_runner,
            summary=summary,
            visual_state_publisher=visual_state_publisher,
            web_bridge_binary=(
                web_bridge_path(paths, system_name)
                if experiment.visualization
                else None
            ),
            web_bridge_config_root=(
                bridge_config_root(paths) if experiment.visualization else None
            ),
            performance_config=(
                paths.recipe_config / "measurement.json"
                if experiment.measurement is not None
                else None
            ),
        )
    except ValueError as exc:
        raise RecipeError(str(exc)) from exc
def session_file(paths) -> Path:
    return paths.recipe_root / "runtime" / "launcher-session.json"


def runtime_environment(paths, drone_root: Path, system_name: str) -> dict[str, str]:
    env = native_library_environment(paths, drone_root, system_name)
    python = resolve_foundation_python(paths, system_name)
    env["HAKO_CONFIG_PATH"] = str(paths.foundation_config / "cpp_core_config.json")
    env["PATH"] = os.pathsep.join(
        [str(python.parent), str(paths.install_prefix / "bin"), env.get("PATH", "")]
    )

    return env


def native_library_environment(
    paths, drone_root: Path, system_name: str
) -> dict[str, str]:
    env = os.environ.copy()
    key = "PATH" if system_name == "Windows" else (
        "DYLD_LIBRARY_PATH" if system_name == "Darwin" else "LD_LIBRARY_PATH"
    )
    env[key] = os.pathsep.join(
        [
            str(paths.install_prefix / "lib"),
            str(drone_root / "lib"),
            str(drone_root / "vendor" / "mujoco" / "lib"),
            env.get(key, ""),
        ]
    )
    return env


def configure(
    experiment_path: Path,
    drone_root: Path,
    *,
    mujoco_city_world: Path | None = None,
    spawn_altitude_m: float = 0.20,
    spawn_spacing_m: float = 1.0,
    drone_count_override: int | None = None,
    process_count_override: int | None = None,
    formation_scale_override: float | None = None,
    altitude_mode: str = "route-clearance",
    above_city_clearance_m: float = 10.0,
    formation_rotation_deg: float = 90.0,
    formation_tilt_deg: float = 15.0,
) -> int:
    experiment = resolve_experiment(
        experiment_path,
        drone_count_override=drone_count_override,
        process_count_override=process_count_override,
        formation_scale_override=formation_scale_override,
    )
    foundation = load_foundation_module()
    paths = foundation.resolve_workspace(ROOT, RECIPE_ID)
    foundation.prepare_workspace(paths)
    paths.recipe_validation.mkdir(parents=True, exist_ok=True)
    prepare_config(paths, drone_root, experiment)
    mujoco_marker = paths.recipe_config / "mujoco-city-fleet.json"
    if mujoco_city_world is not None:
        try:
            from tools.recipe import drone_fleet_mujoco_city
        except ModuleNotFoundError:
            # Direct script execution adds tools/recipe, not the repository
            # root, to sys.path. Keep that supported because configure is also
            # run with the Foundation Python executable.
            import drone_fleet_mujoco_city  # type: ignore[no-redef]

        try:
            marker = drone_fleet_mujoco_city.configure_single_host_fleet(
                drone_root=drone_root,
                city_world_path=mujoco_city_world,
                drone_count=experiment.drone_count,
                recipe_config=paths.recipe_config,
                spawn_altitude_m=spawn_altitude_m,
                spawn_spacing_m=spawn_spacing_m,
                altitude_mode=altitude_mode,
                above_city_clearance_m=above_city_clearance_m,
                process_count=experiment.process_count,
                formation_rotation_deg=formation_rotation_deg,
                formation_tilt_deg=formation_tilt_deg,
            )
        except drone_fleet_mujoco_city.FleetMujocoError as exc:
            raise RecipeError(str(exc)) from exc
    else:
        marker = None
        mujoco_marker.unlink(missing_ok=True)
    # Remove artifacts from the superseded external-Conductor topology.  The
    # single-host Recipe uses one Foundation Core domain and the first Drone
    # process owns its built-in Conductor.
    stale_conductor = paths.recipe_config / "conductor"
    if stale_conductor.exists():
        shutil.rmtree(stale_conductor)
    stale_core_domains = paths.recipe_root / "runtime" / "core"
    if stale_core_domains.exists():
        shutil.rmtree(stale_core_domains)
    resolved = paths.recipe_config / "resolved-experiment.yaml"
    requirements = paths.recipe_config / "foundation-requirements.yaml"
    write_simple_yaml(resolved, resolved_experiment_dict(experiment))
    write_foundation_requirements(requirements, experiment)
    measurement_config = write_measurement_config(paths, experiment)
    # Launcher paths depend on the installed Foundation and Drone package,
    # so doctor/start materializes it after validating those artifacts. Never
    # leave a runnable-looking Launcher generated from an older experiment.
    launcher = paths.recipe_config / "launcher.json"
    if launcher.exists():
        launcher.unlink()
    _run_checked(
        [
            sys.executable,
            str(ROOT / "tools" / "recipe.py"),
            "guide",
            "--recipe",
            str(recipe_file()),
            "--foundation-requirements",
            str(requirements),
        ],
        cwd=ROOT,
    )
    print(f"Recipe workspace       : {paths.recipe_root}")
    print(f"Resolved experiment    : {resolved}")
    print(f"Foundation requirements: {requirements}")
    print(f"Recipe portal          : {paths.recipe_root / 'index.html'}")
    print("Launcher               : pending (generated by doctor/start)")
    print(f"Drone count            : {experiment.drone_count}")
    print(f"Process count          : {experiment.process_count}")
    if marker is not None:
        print("Physics backend        : MuJoCo shared City World")
        print(f"MuJoCo process models  : {len(marker['process_models'])}")
        print(f"Process 1 MJB          : {marker['shared_mjb']}")
        plan = marker["flight_plan"]
        print(
            "Flight altitude        : "
            f"{plan['resolved_flight_altitude_m']:.3f} m local Z "
            f"({plan.get('requested_clearance_m', plan['requested_agl_m']):.3f} m "
            f"clearance, {plan.get('altitude_mode', 'route-clearance')})"
        )
        print(f"Safe launch points     : {len(plan['spawn_points'])}")
    print("Conductor topology     : built-in owner in drone-service-1")
    print(
        "Visualization         : "
        + ("VSP + WebBridge + Three.js" if experiment.visualization else "disabled (headless)")
    )
    if marker is not None:
        phases = marker["flight_plan"].get("show_phases", ["HAKONIWA"])
        print(
            "Scenario               : takeoff -> "
            + " -> ".join(phases)
            + " -> final hold"
        )
    else:
        print("Scenario               : takeoff -> HAKONIWA -> hold -> finish")
    print(
        "Formation dimensions    : "
        f"letter={experiment.letter_width_m:.3f} x "
        f"{experiment.letter_height_m:.3f} m, gap={experiment.letter_gap_m:.3f} m"
    )
    if measurement_config is not None:
        print(f"Measurement config     : {measurement_config}")
        print(f"Measurement trial      : {measurement_trial_dir(paths, experiment)}")
    print("Next:")
    print(f"  python tools/foundation.py doctor --recipe {requirements}")
    print(f"  python tools/foundation.py plan --recipe {requirements}")
    print(
        f"  {operator_command('doctor')} "
        f"--experiment {experiment_path}"
    )
    return 0


def _load_workspace(
    experiment_path: Path, *, drone_count_override: int | None = None
):
    experiment = resolve_experiment(
        experiment_path, drone_count_override=drone_count_override
    )
    foundation = load_foundation_module()
    paths = foundation.resolve_workspace(ROOT, RECIPE_ID)
    requirements = paths.recipe_config / "foundation-requirements.yaml"
    if not requirements.is_file():
        raise RecipeError("Recipe is not configured; run configure first")
    return experiment, foundation, paths, requirements


def _mujoco_city_runtime_checks(
    paths, drone_root: Path, experiment: Experiment, system_name: str
) -> list[tuple[str, bool, str]] | None:
    marker_path = paths.recipe_config / "mujoco-city-fleet.json"
    if not marker_path.is_file():
        return None
    checks: list[tuple[str, bool, str]] = []
    try:
        marker = json.loads(marker_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return [("MuJoCo City fleet contract", False, f"invalid {marker_path}: {exc}")]
    expected_root = Path(str(marker.get("drone_root", ""))).resolve()
    checks.append(
        (
            "MuJoCo City Drone PRO workspace",
            expected_root == drone_root.resolve(),
            str(drone_root)
            if expected_root == drone_root.resolve()
            else f"configured={expected_root}, selected={drone_root.resolve()}",
        )
    )
    checks.append(
        (
            "MuJoCo City process-model contract",
            marker.get("process_count") == experiment.process_count,
            f"configured={experiment.process_count}, model={marker.get('process_count')}",
        )
    )
    checks.append(
        (
            "MuJoCo City fleet size",
            marker.get("drone_count") == experiment.drone_count,
            f"configured={experiment.drone_count}, model={marker.get('drone_count')}",
        )
    )
    flight_plan = marker.get("flight_plan")
    safety_ok = False
    safety_detail = "flight_plan is missing"
    if isinstance(flight_plan, dict):
        try:
            altitude_mode = str(
                flight_plan.get("altitude_mode", "route-clearance")
            )
            requested_agl = float(flight_plan["requested_agl_m"])
            requested_clearance = float(
                flight_plan.get("requested_clearance_m", requested_agl)
            )
            route_maximum = float(flight_plan["route_maximum_surface_height_m"])
            altitude_reference = float(
                flight_plan.get("altitude_reference_height_m", route_maximum)
            )
            resolved_altitude = float(flight_plan["resolved_flight_altitude_m"])
            spawn_points = flight_plan["spawn_points"]
            mode_ok = altitude_mode in {
                "route-clearance",
                "city-max-clearance",
            }
            scenario_clearance_ok = (
                altitude_mode == "city-max-clearance"
                or abs(requested_agl - experiment.altitude_m) < 1e-6
            )
            safety_ok = (
                len(spawn_points) == experiment.drone_count
                and mode_ok
                and scenario_clearance_ok
                and altitude_reference >= route_maximum - 1e-6
                and resolved_altitude
                >= altitude_reference + requested_clearance - 1e-6
                and all(
                    float(point["surface_height_m"])
                    <= float(point["terrain_height_m"]) + 0.15
                    for point in spawn_points
                )
            )
            safety_detail = (
                f"mode={altitude_mode}, launch_points={len(spawn_points)}, "
                f"route_max={route_maximum:.3f} m, "
                f"reference={altitude_reference:.3f} m, "
                f"flight={resolved_altitude:.3f} m, "
                f"clearance={requested_clearance:.3f} m"
            )
        except (KeyError, TypeError, ValueError) as exc:
            safety_detail = f"invalid flight_plan: {exc}"
    checks.append(("MuJoCo City terrain/wall clearance", safety_ok, safety_detail))
    shared_mjb = Path(str(marker.get("shared_mjb", "")))
    process_models = marker.get("process_models")
    if not isinstance(process_models, list):
        process_models = [
            {
                "process_index": 1,
                "drone_ids": list(range(1, experiment.drone_count + 1)),
                "mjb": str(shared_mjb),
                "receipt": marker.get("shared_model_receipt", ""),
            }
        ]
    observed_ids: list[int] = []
    process_model_files_ok = len(process_models) == experiment.process_count
    process_model_detail: list[str] = []
    for process_model in process_models:
        index = process_model.get("process_index")
        ids = process_model.get("drone_ids")
        mjb = Path(str(process_model.get("mjb", "")))
        receipt = Path(str(process_model.get("receipt", "")))
        if isinstance(ids, list):
            observed_ids.extend(int(value) for value in ids)
        else:
            process_model_files_ok = False
            ids = []
        model_ok = mjb.is_file() and receipt.is_file()
        process_model_files_ok = process_model_files_ok and model_ok
        process_model_detail.append(
            f"p{index}={len(ids)} drones, mjb={'OK' if mjb.is_file() else 'NG'}"
        )
    coverage_ok = observed_ids == list(range(1, experiment.drone_count + 1))
    checks.append(
        (
            "MuJoCo City process models",
            process_model_files_ok and coverage_ok,
            "; ".join(process_model_detail),
        )
    )
    type_config = Path(str(marker.get("type_config", "")))
    checks.append(
        ("MuJoCo City Drone type config", type_config.is_file(), str(type_config))
    )
    receipt_path = Path(str(marker.get("shared_model_receipt", "")))
    receipt_ok = False
    receipt_detail = str(receipt_path)
    if receipt_path.is_file() and shared_mjb.is_file():
        try:
            receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
            compiled = receipt["compiled_model"]
            receipt_ok = (
                compiled.get("reload_validation") == "passed"
                and compiled.get("output_mjb_sha256") == _sha256(shared_mjb)
            )
            receipt_detail += (
                f" (MuJoCo {compiled.get('mujoco_version')}, reload="
                f"{compiled.get('reload_validation')})"
            )
        except (OSError, json.JSONDecodeError, KeyError, TypeError) as exc:
            receipt_detail += f": {exc}"
    checks.append(("Process 1 MJB compile receipt", receipt_ok, receipt_detail))
    try:
        drone_binary = resolve_drone_binary(drone_root, system_name)
        checks.append(("Drone PRO service", True, str(drone_binary)))
    except RecipeError as exc:
        checks.append(("Drone PRO service", False, str(exc)))
    if experiment.visualization:
        try:
            vsp = resolve_visual_state_publisher(drone_root, system_name)
            checks.append(("Drone PRO visual-state publisher", True, str(vsp)))
        except RecipeError as exc:
            checks.append(("Drone PRO visual-state publisher", False, str(exc)))
    return checks


def doctor(
    experiment_path: Path,
    drone_root: Path,
    viewer_root: Path,
    *,
    drone_count_override: int | None = None,
) -> int:
    experiment, foundation, paths, requirements = _load_workspace(
        experiment_path, drone_count_override=drone_count_override
    )
    inspection = foundation.inspect_foundation(requirements, paths.install_prefix)
    foundation.print_inspection(inspection, False)
    system_name = platform.system()
    checks: list[tuple[str, bool, str]] = []
    mujoco_city_checks = _mujoco_city_runtime_checks(
        paths, drone_root, experiment, system_name
    )
    if mujoco_city_checks is not None:
        checks.extend(mujoco_city_checks)
    else:
        native_runtime = load_native_runtime_module()
        try:
            _contract, native_checks = native_runtime.validate_requirement(
                DRONE_CATALOG,
                recipe_file(),
                DRONE_COMPONENT_ID,
                drone_root,
                native_library_environment(paths, drone_root, system_name),
                active_optional_roles=(
                    ("visual_state_publisher",) if experiment.visualization else ()
                ),
            )
            checks.extend(
                (check.label, check.ok, check.detail) for check in native_checks
            )
        except native_runtime.NativeRuntimeError as exc:
            checks.append(("native runtime contract", False, str(exc)))

    if experiment.visualization:
        bridge = web_bridge_path(paths, system_name)
        checks.append(("WebBridge", bridge.is_file(), str(bridge)))
        bridge_config = bridge_config_root(paths)
        checks.append(("WebBridge config", bridge_config.is_dir(), str(bridge_config)))
        missing_viewer_files = [
            path for path in viewer_required_files(viewer_root) if not path.is_file()
        ]
        checks.append(
            (
                "Three.js viewer and PDU JavaScript",
                not missing_viewer_files,
                str(viewer_root)
                if not missing_viewer_files
                else "missing: "
                + ", ".join(str(path) for path in missing_viewer_files)
                + f"; run '{operator_command('prepare-viewer')}'",
            )
        )
    for port in ((8000, 8765, 54111) if experiment.visualization else (54111,)):
        available = _port_available(port)
        if available is None:
            print(f"[WARN] port {port}: unavailable in this execution environment")
        else:
            checks.append((f"port {port}", available, "available" if available else "in use"))
    try:
        python = resolve_foundation_python(paths, system_name)
        probe = subprocess.run(
            [
                str(python),
                "-c",
                (
                    "import sys; assert sys.version_info[:2] == (3, 12), sys.version; "
                    "import hakopy, hakoniwa_pdu; "
                    + ("import hakoniwa_measurement; " if experiment.measurement else "")
                    + "import hakoniwa_pdu.apps.launcher.hako_launcher"
                ),
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        checks.append(
            (
                "Foundation Python imports",
                probe.returncode == 0,
                str(python) if probe.returncode == 0 else probe.stderr.strip(),
            )
        )
    except RecipeError as exc:
        checks.append(("Foundation Python imports", False, str(exc)))
    for relative in (
        "config/resolved-experiment.yaml",
        "config/foundation-requirements.yaml",
        "config/scenario/show.json",
        "config/drone/fleets/api-current.json",
        "config/drone/fleets/services/api-current-service.json",
        "config/pdudef/drone-pdudef-current.json",
        *(("config/measurement.json",) if experiment.measurement else ()),
    ):
        path = paths.recipe_root / relative
        checks.append((relative, path.is_file(), str(path)))
    materialization_errors = validate_materialized_experiment(paths, experiment)
    checks.append(
        (
            f"process partitions ({experiment.process_count})",
            not materialization_errors,
            "configured experiment matches all process partitions"
            if not materialization_errors
            else "; ".join(materialization_errors)
            + f"; run '{operator_command('configure')}'",
        )
    )
    failed = inspection["status"] != "SATISFIED"
    for label, ok, detail in checks:
        print(f"[{'OK' if ok else 'NG'}] {label}: {detail}")
        failed = failed or not ok
    if not failed:
        launcher = write_launcher(
            paths,
            drone_root,
            viewer_root,
            experiment,
            system_name,
        )
        print(f"[OK] launcher: {launcher}")
    return 1 if failed else 0


def _launcher_command(paths, system_name: str, operation: str) -> list[str]:
    python = resolve_foundation_python(paths, system_name)
    session = session_file(paths)
    if operation == "start":
        return [
            str(python),
            "-m",
            "hakoniwa_pdu.apps.launcher.hako_launcher",
            str(paths.recipe_config / "launcher.json"),
            "--background",
            str(session),
        ]
    if operation in {"status", "terminate"}:
        return [
            str(python),
            "-m",
            "hakoniwa_pdu.apps.launcher.hako_launcher_ctl",
            operation,
            str(session),
        ]
    raise RecipeError(f"unsupported Launcher operation: {operation}")


def start(
    experiment_path: Path,
    drone_root: Path,
    viewer_root: Path,
    *,
    drone_count_override: int | None = None,
) -> int:
    if doctor(
        experiment_path,
        drone_root,
        viewer_root,
        drone_count_override=drone_count_override,
    ) != 0:
        return 1
    experiment, _foundation, paths, _requirements = _load_workspace(
        experiment_path, drone_count_override=drone_count_override
    )
    system_name = platform.system()
    trial = measurement_trial_dir(paths, experiment)
    if trial is not None:
        results_root = paths.recipe_root / experiment.results_directory
        if results_root not in trial.parents:
            raise RecipeError(f"measurement trial escaped results root: {trial}")
        if trial.exists():
            shutil.rmtree(trial)
        trial.mkdir(parents=True)
    summary = (
        trial / "execution-summary.json"
        if trial is not None
        else paths.recipe_validation / "execution-summary.json"
    )
    if summary.exists():
        summary.unlink()
    command = _launcher_command(paths, system_name, "start")
    print(
        "Starting all native assets. The command returns only after every asset "
        "is activated and the Launcher control endpoint is ready."
    )
    rc = _run(command, env=runtime_environment(paths, drone_root, system_name))
    if rc == 0:
        print("The experiment continues in the background.")
        print("Next:")
        print(f"  {operator_command('status')}")
        print(f"  {operator_command('smoke')}")
        if experiment.visualization:
            print(f"  {operator_command('open-viewer')}")
        print(f"  {operator_command('stop')}")
        print(f"Session: {session_file(paths)}")
        print(f"Logs   : {paths.recipe_logs}")
    return rc


def control(
    experiment_path: Path,
    drone_root: Path,
    operation: str,
    *,
    drone_count_override: int | None = None,
) -> int:
    _experiment, _foundation, paths, _requirements = _load_workspace(
        experiment_path, drone_count_override=drone_count_override
    )
    system_name = platform.system()
    command = _launcher_command(paths, system_name, operation)
    return _run(command, env=runtime_environment(paths, drone_root, system_name))


def smoke(
    experiment_path: Path,
    timeout_sec: float,
    *,
    drone_count_override: int | None = None,
) -> int:
    experiment, _foundation, paths, _requirements = _load_workspace(
        experiment_path, drone_count_override=drone_count_override
    )
    trial = measurement_trial_dir(paths, experiment)
    if trial is not None:
        result = trial / "result.json"
        print(f"Measurement result: {result}")
        deadline = time.monotonic() + timeout_sec
        while time.monotonic() < deadline:
            if result.is_file():
                try:
                    payload = json.loads(result.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError):
                    time.sleep(0.2)
                    continue
                print(json.dumps(payload, indent=2))
                return 0 if payload.get("status") == "success" else 1
            time.sleep(0.2)
        print(f"[NG] measurement result was not produced within {timeout_sec}s: {result}")
        return 1
    summary = paths.recipe_validation / "execution-summary.json"
    print(
        "Verifying the workload already started by 'start'; smoke does not start "
        "another flight."
    )
    print(
        f"Waiting up to {timeout_sec:g} wall-clock seconds for all "
        f"{experiment.drone_count} drones to finish takeoff, HAKONIWA placement "
        f"(configured move={experiment.duration_sec:g}s), and hold "
        f"({experiment.hold_sec:g}s)."
    )
    print(f"Execution summary: {summary}")
    deadline = time.monotonic() + timeout_sec
    while time.monotonic() < deadline:
        if summary.is_file():
            try:
                payload = json.loads(summary.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                time.sleep(0.2)
                continue
            print(json.dumps(payload, indent=2))
            phase_times = payload.get("phase_times_sec")
            if isinstance(phase_times, dict):
                print("Observed phase times (wall-clock seconds):")
                for name, seconds in phase_times.items():
                    print(f"  {name}: {seconds}")
            phase_simulation_times = payload.get("phase_simulation_times_sec")
            if isinstance(phase_simulation_times, dict):
                print("Observed phase times (Hakoniwa Core seconds):")
                for name, seconds in phase_simulation_times.items():
                    print(f"  {name}: {seconds}")
            simulation_time = payload.get("simulation_time")
            if isinstance(simulation_time, dict):
                print(
                    "Simulation elapsed (Hakoniwa Core seconds): "
                    f"{simulation_time.get('elapsed_sec')}"
                )
            if "wall_elapsed_sec" in payload:
                print(f"Aligned wall elapsed (seconds): {payload.get('wall_elapsed_sec')}")
            if "real_time_factor" in payload:
                print(f"Real Time Factor: {payload.get('real_time_factor')}")
            if payload.get("real_time_sync"):
                print(
                    "Real-time pacing sleep: "
                    f"count={payload.get('real_time_sync_sleep_count')} "
                    f"sec={payload.get('real_time_sync_sleep_sec')}"
                )
            return 0 if payload.get("status") == "done" else 1
        time.sleep(0.2)
    print(f"[NG] execution summary was not produced within {timeout_sec}s: {summary}")
    return 1


def viewer_url(drone_count: int, *, map_viewer: bool = False) -> str:
    base = MAP_VIEWER_URL_BASE if map_viewer else VIEWER_URL_BASE
    return (
        f"{base}&dynamicSpawn=true"
        f"&templateDroneIndex=0&maxDynamicDrones={drone_count}"
    )


def is_wsl() -> bool:
    if os.environ.get("WSL_DISTRO_NAME") or os.environ.get("WSL_INTEROP"):
        return True
    try:
        return "microsoft" in Path("/proc/sys/kernel/osrelease").read_text(
            encoding="utf-8"
        ).lower()
    except OSError:
        return False


def open_browser(url: str) -> bool:
    print(f"Open this URL in a browser: {url}")
    if is_wsl():
        print(
            "WSL2: open the URL in a Windows browser. WSL localhost forwarding "
            "exposes HTTP port 8000 and WebSocket port 8765 to the host."
        )
        return True
    if platform.system() == "Darwin":
        try:
            completed = subprocess.run(["open", url], check=False)
        except OSError as exc:
            print(f"[WARN] could not open the macOS browser: {exc}")
            return False
        if completed.returncode != 0:
            print(
                "[WARN] macOS 'open' failed; open the URL printed above manually"
            )
            return False
    return True


def open_viewer(
    experiment_path: Path, *, drone_count_override: int | None = None
) -> int:
    # Headless is an experiment-level contract and should be rejected before
    # consulting generated workspace state. This keeps diagnostics stable in a
    # clean checkout where the Recipe has not been configured yet.
    requested_experiment = resolve_experiment(
        experiment_path, drone_count_override=drone_count_override
    )
    if not requested_experiment.visualization:
        raise RecipeError(
            "runtime.visualization=false; this headless experiment does not start "
            "VSP, WebBridge, or the Three.js viewer"
        )
    experiment, _foundation, paths, _requirements = _load_workspace(
        experiment_path, drone_count_override=drone_count_override
    )
    url = viewer_url(
        experiment.drone_count,
        map_viewer=(paths.recipe_config / "mujoco-city-fleet.json").is_file(),
    )
    return 0 if open_browser(url) else 1


def configured_experiment_path() -> Path:
    return (
        ROOT
        / "work"
        / "recipes"
        / RECIPE_ID
        / "config"
        / "resolved-experiment.yaml"
    )


def configured_drone_root() -> Path | None:
    marker = configured_experiment_path().with_name("mujoco-city-fleet.json")
    if not marker.is_file():
        return None
    try:
        payload = json.loads(marker.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    value = payload.get("drone_root")
    if not isinstance(value, str) or not value:
        return None
    return Path(value).expanduser().absolute()


def command_experiment_path(command: str, requested: Path | None) -> Path:
    if requested is not None:
        return requested.absolute()
    configured = configured_experiment_path()
    if command != "configure" and configured.is_file():
        return configured
    return DEFAULT_EXPERIMENT.absolute()


def command_drone_root(
    command: str,
    requested: Path | None,
    *,
    mujoco_city_world: Path | None = None,
) -> Path:
    if requested is not None:
        return requested.absolute()
    if command == "configure" and mujoco_city_world is not None:
        # The MuJoCo City path compiles one shared MJB.  Loading that binary
        # model is a Drone PRO contract; silently selecting Drone Core here
        # produces a launcher that configures successfully and then dies before
        # any dependent viewer asset can start.
        return default_source("hakoniwa-drone-pro").absolute()
    if command != "configure":
        configured = configured_drone_root()
        if configured is not None:
            return configured
    return default_source("hakoniwa-drone-core").absolute()


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(
        description="Native single-host multi-drone Recipe operator"
    )
    result.add_argument(
        "command",
        choices=[
            "prepare-native",
            "prepare-viewer",
            "configure",
            "doctor",
            "start",
            "status",
            "smoke",
            "open-viewer",
            "stop",
        ],
    )
    result.add_argument(
        "--experiment",
        type=Path,
        help=(
            "experiment YAML; after configure, omitted commands reuse the "
            "workspace resolved experiment"
        ),
    )
    result.add_argument(
        "--drone-root",
        type=Path,
        help=(
            "Drone workspace; after MuJoCo City configure, omitted commands "
            "reuse the configured Drone PRO workspace"
        ),
    )
    result.add_argument(
        "--viewer-root", type=Path, default=default_source("hakoniwa-threejs-drone")
    )
    result.add_argument("--timeout-sec", type=float, default=300.0)
    result.add_argument(
        "--drone-count",
        type=int,
        help=(
            "override scale.drone_count for a local run; configure "
            "persists the resolved value for subsequent omitted commands"
        ),
    )
    result.add_argument(
        "--process-count",
        type=int,
        help=(
            "configure only: split MuJoCo City drones across this many local "
            "Drone Service processes; each gets an independent City model"
        ),
    )
    result.add_argument(
        "--mujoco-city-world",
        type=Path,
        help=(
            "configure only: City World receipt/XML used to generate one "
            "MuJoCo model per local Drone Service process (non-ICRA)"
        ),
    )
    result.add_argument(
        "--spawn-altitude-m",
        type=float,
        default=0.20,
        help=(
            "initial drone body-origin clearance above the sampled DEM terrain "
            "(default: 0.20 m; City launch points are selected automatically)"
        ),
    )
    result.add_argument(
        "--spawn-spacing-m",
        type=float,
        default=1.0,
        help=(
            "configure only: minimum center-to-center spacing of the compact "
            "MuJoCo City launch formation (default: 1.0 m; 1.0..5.0)"
        ),
    )
    result.add_argument(
        "--formation-scale",
        type=float,
        help=(
            "configure only: multiply HAKONIWA letter width, height, and gap "
            "by this factor (0.25..10.0); the resolved dimensions are persisted"
        ),
    )
    result.add_argument(
        "--formation-rotation-deg",
        type=float,
        default=90.0,
        help=(
            "configure only: clockwise rotation of the City show formation "
            "on the ROS XY plane (default: 90 degrees for map readability)"
        ),
    )
    result.add_argument(
        "--formation-tilt-deg",
        type=float,
        default=15.0,
        help=(
            "configure only: tilt the non-ICRA City show plane toward an "
            "audience looking upward (default: 15 degrees; 0..35)"
        ),
    )
    result.add_argument(
        "--altitude-mode",
        choices=["route-clearance", "city-max-clearance"],
        default="route-clearance",
        help=(
            "configure only: fly above planned-route colliders (default) or "
            "above the highest building in the generated city"
        ),
    )
    result.add_argument(
        "--above-city-clearance-m",
        type=float,
        default=10.0,
        help=(
            "configure only: clearance above the highest city building when "
            "--altitude-mode=city-max-clearance (default: 10 m)"
        ),
    )
    return result


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        system_name = platform.system()
        if system_name not in SUPPORTED_NATIVE_SYSTEMS:
            raise RecipeError(
                f"unsupported native operating system: {system_name}; "
                "drone-fleet-single-host supports macOS and Linux"
            )
        experiment_path = command_experiment_path(args.command, args.experiment)
        drone_root = command_drone_root(
            args.command,
            args.drone_root,
            mujoco_city_world=args.mujoco_city_world,
        )
        viewer_root = args.viewer_root.absolute()
        if args.command == "prepare-native":
            foundation = load_foundation_module()
            paths = foundation.resolve_workspace(ROOT, RECIPE_ID)
            return prepare_native_distribution(
                drone_root,
                system_name,
                cache_root=paths.work_root / "downloads",
                evidence_path=paths.recipe_validation / "native-distribution.json",
            )
        if args.command == "prepare-viewer":
            return prepare_viewer(viewer_root)
        if args.command == "configure":
            return configure(
                experiment_path,
                drone_root,
                mujoco_city_world=(
                    args.mujoco_city_world.absolute()
                    if args.mujoco_city_world is not None
                    else None
                ),
                spawn_altitude_m=args.spawn_altitude_m,
                spawn_spacing_m=args.spawn_spacing_m,
                drone_count_override=args.drone_count,
                process_count_override=args.process_count,
                formation_scale_override=args.formation_scale,
                altitude_mode=args.altitude_mode,
                above_city_clearance_m=args.above_city_clearance_m,
                formation_rotation_deg=args.formation_rotation_deg,
                formation_tilt_deg=args.formation_tilt_deg,
            )
        if args.command == "doctor":
            return doctor(
                experiment_path,
                drone_root,
                viewer_root,
                drone_count_override=args.drone_count,
            )
        if args.command == "start":
            return start(
                experiment_path,
                drone_root,
                viewer_root,
                drone_count_override=args.drone_count,
            )
        if args.command == "status":
            return control(
                experiment_path,
                drone_root,
                "status",
                drone_count_override=args.drone_count,
            )
        if args.command == "stop":
            return control(
                experiment_path,
                drone_root,
                "terminate",
                drone_count_override=args.drone_count,
            )
        if args.command == "open-viewer":
            return open_viewer(
                experiment_path, drone_count_override=args.drone_count
            )
        return smoke(
            experiment_path,
            args.timeout_sec,
            drone_count_override=args.drone_count,
        )
    except RecipeError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
