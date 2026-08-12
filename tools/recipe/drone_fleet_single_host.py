#!/usr/bin/env python3
"""Configure and operate the native single-host multi-drone Recipe.

The MVP intentionally accepts a small, dependency-free YAML subset consisting
of nested mappings and scalar values.  This lets ``configure`` run before the
Foundation Python environment exists.  Matrix expansion and measurements are
reserved for the later experiment runner.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import math
import os
import platform
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any


RECIPE_ID = "drone-fleet-single-host"
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
    "Windows": (
        "win.zip",
        "2931cb7844dbe74ec3dd5f4be5bb49f28757d268774010c90ea18e8266e59ac0",
    ),
}


class RecipeError(RuntimeError):
    pass


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


def prepare_native_distribution(drone_root: Path, system_name: str) -> int:
    """Explicitly materialize the public Drone source and native distribution."""
    profile = PUBLIC_DRONE_ARCHIVES.get(system_name)
    if profile is None:
        raise RecipeError(f"unsupported native operating system: {system_name}")

    if not drone_root.exists():
        drone_root.parent.mkdir(parents=True, exist_ok=True)
        _run_checked(
            [
                "git",
                "clone",
                "--recurse-submodules",
                "--branch",
                PUBLIC_DRONE_RELEASE,
                "--depth",
                "1",
                PUBLIC_DRONE_REPOSITORY,
                str(drone_root),
            ],
            cwd=drone_root.parent,
        )
    if not (drone_root / "tools" / "gen_fleet_scale_config.py").is_file():
        raise RecipeError(
            f"Hakoniwa Drone source is incomplete: {drone_root}; "
            "use --drone-root to select a toppers/hakoniwa-drone-core checkout"
        )

    try:
        service = resolve_drone_binary(drone_root, system_name)
        vsp = resolve_visual_state_publisher(drone_root, system_name)
        print(f"Native Drone distribution is already ready: {service}")
        print(f"Visual-state publisher is already ready: {vsp}")
        return 0
    except RecipeError:
        pass

    archive_name, expected_sha256 = profile
    url = (
        "https://github.com/toppers/hakoniwa-drone-core/releases/download/"
        f"{PUBLIC_DRONE_RELEASE}/{archive_name}"
    )
    print(f"Downloading official Hakoniwa Drone {PUBLIC_DRONE_RELEASE}: {url}")
    try:
        with tempfile.TemporaryDirectory(prefix="hakoniwa-drone-download-") as temporary:
            archive = Path(temporary) / archive_name
            with urllib.request.urlopen(url) as response:
                with archive.open("wb") as output:
                    while chunk := response.read(1024 * 1024):
                        output.write(chunk)
            digest = hashlib.sha256()
            with archive.open("rb") as package:
                while chunk := package.read(1024 * 1024):
                    digest.update(chunk)
            actual_sha256 = digest.hexdigest()
            if actual_sha256 != expected_sha256:
                raise RecipeError(
                    f"native Drone archive SHA-256 mismatch: expected "
                    f"{expected_sha256}, got {actual_sha256}"
                )
            _safe_extract(archive, drone_root)
    except (OSError, urllib.error.URLError, zipfile.BadZipFile) as exc:
        raise RecipeError(f"failed to prepare native Drone distribution: {exc}") from exc

    for candidate in (*binary_candidates(drone_root, system_name), *visual_state_publisher_candidates(drone_root, system_name)):
        if candidate.is_file() and system_name != "Windows":
            candidate.chmod(candidate.stat().st_mode | 0o111)
    service = resolve_drone_binary(drone_root, system_name)
    vsp = resolve_visual_state_publisher(drone_root, system_name)
    print(f"[OK] native drone service: {service}")
    print(f"[OK] visual-state publisher: {vsp}")
    print(f"[OK] SHA-256: {expected_sha256}")
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
    try:
        return int(value)
    except ValueError:
        pass
    try:
        return float(value)
    except ValueError:
        return value


def load_simple_yaml(path: Path) -> dict[str, Any]:
    """Load the mapping-only YAML subset used by the MVP experiment file."""
    if not path.is_file():
        raise RecipeError(f"experiment YAML not found: {path}")
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
                f"{path}:{line_number}: MVP experiment YAML supports mappings and scalars only"
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


def resolve_experiment(path: Path) -> Experiment:
    root = load_simple_yaml(path)
    _require_fields(
        root,
        "root",
        {"version", "experiment", "scale", "runtime", "scenario", "results"},
    )
    if root.get("version") != 1:
        raise RecipeError("experiment version must be 1")
    identity = _mapping(root, "experiment")
    scale = _mapping(root, "scale")
    runtime = _mapping(root, "runtime")
    scenario = _mapping(root, "scenario")
    results = _mapping(root, "results")
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
        if drones_per_process is None:
            drones_per_process = math.ceil(drone_count / process_count)
        elif (
            not isinstance(drones_per_process, int)
            or isinstance(drones_per_process, bool)
            or drones_per_process < 1
        ):
            raise RecipeError("scale.drones_per_process must be an integer >= 1")
    else:
        raise RecipeError(
            "scale.drone_count must be auto or an integer"
        )
    if drone_count < HAKONIWA_STROKE_COUNT:
        raise RecipeError(
            "resolved scale.drone_count must be >= 26 for the HAKONIWA formation"
        )
    if drone_count > GENERAL_USER_MAX_DRONES:
        raise RecipeError(
            "resolved scale.drone_count exceeds the general-user limit of "
            f"{GENERAL_USER_MAX_DRONES}; use a separately built and verified "
            "512-drone Core/Drone/VSP/Foundation profile and its Hakoniwa "
            "Drone PRO research Recipe instead of the public default binaries; "
            "a PRO license and PRO source access are required"
        )

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
        letter_width_m=number("letter_width_m", minimum=0.001),
        letter_height_m=number("letter_height_m", minimum=0.001),
        letter_gap_m=number("letter_gap_m", minimum=0.0),
        altitude_m=number("altitude_m", minimum=0.5),
        duration_sec=number("duration_sec", minimum=0.001),
        hold_sec=number("hold_sec", minimum=0.0),
        speed_m_s=number("speed_m_s", minimum=0.001),
        timeout_sec=number("timeout_sec", minimum=1.0),
        land=land,
        results_enabled=results_enabled,
        results_directory=results_directory,
    )


def next_pow2(value: int) -> int:
    return 1 if value <= 1 else 1 << (value - 1).bit_length()


def required_build_limits(experiment: Experiment) -> dict[str, int]:
    service_total = 5 * experiment.drone_count
    channel_total = 19 * experiment.drone_count + 2 * service_total + 4
    # Drone services + Conductor Client + ShowRunner, plus VSP + WebBridge when
    # visualization is enabled. The Server has its own Core domain, and the
    # after-start HTTP server is not a Hakoniwa asset.
    runtime_assets = experiment.process_count + 2 + (2 if experiment.visualization else 0)
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
    return {
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


def write_foundation_requirements(path: Path, experiment: Experiment) -> None:
    limits = required_build_limits(experiment)
    requirements: dict[str, Any] = {"foundation_requirements": {}}
    components = [
        (
            "hakoniwa-core-pro",
            {"shared_memory": True, "hako_cmd": True, "python_binding": True},
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
    config = paths.recipe_config
    fleet = config / "drone" / "fleets" / "api-current.json"
    service = config / "drone" / "fleets" / "services" / "api-current-service.json"
    pdudef = config / "pdudef" / "drone-pdudef-current.json"
    shared_service_path = "config/drone/fleets/services/api-current-service.json"
    # Remove partitions from a previous process_count so the Recipe workspace
    # describes only the currently resolved experiment.
    for pattern_root, pattern in (
        (config / "drone" / "fleets", "api-current-part*.json"),
        (
            config / "drone" / "fleets" / "services",
            "api-current-service-part*.json",
        ),
    ):
        if pattern_root.is_dir():
            for stale_partition in pattern_root.glob(pattern):
                stale_partition.unlink()
    _run_checked(
        [
            sys.executable,
            str(drone_root / "tools" / "gen_fleet_scale_config.py"),
            "--drone-count",
            str(experiment.drone_count),
            "--fleet-path",
            str(fleet),
            "--pdudef-path",
            str(pdudef),
            "--service-config-path",
            shared_service_path,
            "--service-out-path",
            str(service),
            "--layout",
            "packed-rings",
        ],
        cwd=paths.recipe_root,
    )
    if experiment.process_count > 1:
        _run_checked(
            [
                sys.executable,
                str(drone_root / "tools" / "gen_fleet_split_config.py"),
                "--fleet-in",
                str(fleet),
                "--service-in",
                str(service),
                "--fleet-out-template",
                str(config / "drone" / "fleets" / "api-current-part{part}.json"),
                "--service-out-template",
                str(
                    config
                    / "drone"
                    / "fleets"
                    / "services"
                    / "api-current-service-part{part}.json"
                ),
                "--shared-service-config-path",
                shared_service_path,
                "--parts",
                str(experiment.process_count),
            ],
            cwd=paths.recipe_root,
        )
    for relative in (
        Path("config/drone/fleets/types"),
        Path("config/controller"),
    ):
        source = drone_root / relative
        if not source.is_dir():
            raise RecipeError(f"Drone Core configuration not found: {source}")
        shutil.copytree(
            source,
            paths.recipe_root / relative,
            dirs_exist_ok=True,
            ignore=shutil.ignore_patterns(".DS_Store", "logs"),
        )
    source_pdutypes = drone_root / "config" / "pdudef" / "drone-pdutypes.json"
    if not source_pdutypes.is_file():
        raise RecipeError(f"Drone PDU types not found: {source_pdutypes}")
    shutil.copy2(source_pdutypes, config / "pdudef" / "drone-pdutypes.json")
    visual_output = config / "assets" / "visual_state_publisher"
    visual_pdudef_names = (
        "drone-visual-state.json",
        "drone-visual-state-pdutypes.json",
        "pdutypes_time.json",
    )
    if experiment.visualization:
        for name in visual_pdudef_names:
            source = drone_root / "config" / "pdudef" / name
            if not source.is_file():
                raise RecipeError(f"Drone visual-state PDU definition not found: {source}")
            shutil.copy2(source, config / "pdudef" / name)
        visual_source = drone_root / "config" / "assets" / "visual_state_publisher"
        if not visual_source.is_dir():
            raise RecipeError(f"Visual-state publisher configuration not found: {visual_source}")
        shutil.copytree(
            visual_source,
            visual_output,
            dirs_exist_ok=True,
            ignore=shutil.ignore_patterns(".DS_Store", "logs"),
        )
        _run_checked(
            [
                sys.executable,
                str(drone_root / "tools" / "gen_visual_state_publisher_config.py"),
                "--base-config",
                str(visual_output / "visual_state_publisher.json"),
                "--out",
                str(visual_output / "visual_state_publisher.runtime.json"),
                "--global-drone-count",
                str(experiment.drone_count),
                "--local-drone-count",
                str(experiment.drone_count),
                "--max-drones-per-packet",
                "512",
            ],
            cwd=paths.recipe_root,
        )
    else:
        if visual_output.exists():
            shutil.rmtree(visual_output)
        for name in visual_pdudef_names:
            stale = config / "pdudef" / name
            if stale.exists():
                stale.unlink()
    write_generated_scenario(paths, drone_root, experiment)


def write_generated_scenario(paths, drone_root: Path, experiment: Experiment) -> Path:
    formation_dir = paths.recipe_config / "scenario" / "formations"
    formation = formation_dir / "formation-HAKONIWA.json"
    minimum_points = (
        RECOMMENDED_DRONES_PER_STROKE
        if experiment.drone_count
        >= HAKONIWA_STROKE_COUNT * RECOMMENDED_DRONES_PER_STROKE
        else 1
    )
    if minimum_points == 1:
        print(
            "[WARN] HAKONIWA formation uses fewer than two drones per stroke; "
            "52 or more drones are recommended for readability."
        )
    generator = drone_root / "tools" / "drone-show" / "gen_word_formation.py"
    if not generator.is_file():
        raise RecipeError(f"word formation generator not found: {generator}")
    _run_checked(
        [
            sys.executable,
            str(generator),
            "--word",
            experiment.word,
            "--count",
            str(experiment.drone_count),
            "--out",
            str(formation),
            "--id",
            experiment.word,
            "--letter-width",
            str(experiment.letter_width_m),
            "--letter-height",
            str(experiment.letter_height_m),
            "--gap",
            str(experiment.letter_gap_m),
            "--scale",
            "1.0",
            "--min-seg-points",
            str(minimum_points),
        ],
        cwd=paths.recipe_root,
    )
    show = {
        "meta": {
            "name": experiment.experiment_id,
            "version": "1.0",
            "drone_count": experiment.drone_count,
        },
        "options": {
            "center": [0.0, 0.0, 0.0],
            "scale": 1.0,
            "base_alt": experiment.altitude_m,
            "min_distance": 0.0,
            "max_speed": experiment.speed_m_s,
            "failure_policy": "hold",
        },
        "formation_files": [
            {"id": experiment.word, "path": "formations/formation-HAKONIWA.json"}
        ],
        "timeline": [
            {
                "formation": experiment.word,
                "duration_sec": experiment.duration_sec,
                "hold_sec": experiment.hold_sec,
            }
        ],
    }
    output = paths.recipe_config / "scenario" / "show.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(show, indent=2) + "\n", encoding="utf-8")
    return output


def binary_candidates(drone_root: Path, system_name: str) -> tuple[Path, ...]:
    if system_name == "Darwin":
        name, folder = "mac-main_hako_drone_service", "mac"
    elif system_name == "Linux":
        name, folder = "linux-main_hako_drone_service", "lnx"
    elif system_name == "Windows":
        name, folder = "win-main_hako_drone_service.exe", "win"
    else:
        raise RecipeError(f"unsupported native operating system: {system_name}")
    return drone_root / "lib" / name, drone_root / folder / name


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
    return drone_root / "lib" / name, drone_root / folder / name


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


def write_launcher(
    paths,
    drone_root: Path,
    viewer_root: Path,
    experiment: Experiment,
    system_name: str,
) -> Path:
    drone_binary = resolve_drone_binary(drone_root, system_name)
    python = resolve_foundation_python(paths, system_name)
    shared_env = {
        "set": {
            "HAKO_CONFIG_PATH": str(paths.foundation_config / "cpp_core_config.json"),
            "HAKO_PROFILE_SERVICE_CLIENT": "0",
        }
    }
    service_assets: list[dict[str, Any]] = []
    for index in range(1, experiment.process_count + 1):
        fleet = (
            "config/drone/fleets/api-current.json"
            if experiment.process_count == 1
            else f"config/drone/fleets/api-current-part{index}.json"
        )
        args = [fleet, "config/pdudef/drone-pdudef-current.json"]
        if experiment.process_count > 1:
            args += ["--asset-name", f"drone-{index}"]
        # A single host has exactly one Core domain and therefore one
        # Conductor owner.  The first Drone process owns the built-in
        # Conductor; additional workload partitions disable theirs.
        if index >= 2:
            args.append("--disable-conductor")
        asset: dict[str, Any] = {
            "name": f"drone-service-{index}",
            "activation_timing": "before_start",
            "command": str(drone_binary),
            "args": args,
            "cwd": str(paths.recipe_root),
            "env": shared_env,
            "delay_sec": 2 if index == 1 else 1,
        }
        if index >= 2:
            asset["depends_on"] = [f"drone-service-{index - 1}"]
        service_assets.append(asset)

    summary = paths.recipe_validation / "execution-summary.json"
    show_runner = drone_root / "drone_api" / "external_rpc" / "apps" / "show_asset_runner.py"
    if not show_runner.is_file():
        raise RecipeError(f"Drone show runner not found: {show_runner}")
    show_args = [
        str(show_runner),
        "--show-json",
        str(paths.recipe_config / "scenario" / "show.json"),
        "--service-config",
        str(
            paths.recipe_config
            / "drone"
            / "fleets"
            / "services"
            / "api-current-service.json"
        ),
        "--pdu-config-path",
        str(paths.recipe_config / "pdudef" / "drone-pdudef-current.json"),
        "--drone-count",
        str(experiment.drone_count),
        "--asset-name",
        "ShowRunnerAsset",
        "--proc-count",
        str(experiment.process_count),
        "--summary-json",
        str(summary),
        "--assign-mode",
        "index",
        "--speed",
        str(experiment.speed_m_s),
        "--timeout-sec",
        str(experiment.timeout_sec),
        "--delta-time-msec",
        "20",
        "--poll-sleep-msec",
        "0",
        "--final-hold-extra-sec",
        "0",
    ]
    if experiment.show_runner_real_time_sync:
        show_args.append("--real-time-sync")
    if experiment.land:
        show_args.append("--land")
    assets: list[dict[str, Any]] = service_assets + [
        {
            "name": "show-runner",
            "activation_timing": "before_start",
            "command": str(python),
            "args": show_args,
            "cwd": str(drone_root),
            "env": shared_env,
            "depends_on": [service_assets[-1]["name"]],
            "delay_sec": 1,
        },
    ]
    if experiment.visualization:
        visual_state_publisher = resolve_visual_state_publisher(drone_root, system_name)
        web_bridge = web_bridge_path(paths, system_name)
        assets.extend(
            [
                {
                    "name": "visual-state-publisher",
                    "activation_timing": "before_start",
                    "command": str(visual_state_publisher),
                    "args": [
                        str(
                            paths.recipe_config
                            / "assets"
                            / "visual_state_publisher"
                            / "visual_state_publisher.runtime.json"
                        )
                    ],
                    "cwd": str(paths.recipe_root),
                    "env": shared_env,
                    "depends_on": ["show-runner"],
                    "delay_sec": 2,
                },
                {
                    "name": "web-bridge-fleets",
                    "activation_timing": "before_start",
                    "command": str(web_bridge),
                    "args": [
                        "--config-root",
                        str(bridge_config_root(paths)),
                        "--node-name",
                        "web_bridge_fleets_node1",
                        "--delta-time-step-usec",
                        "20000",
                        "--enable-ondemand",
                    ],
                    "cwd": str(paths.recipe_root),
                    "env": shared_env,
                    "depends_on": ["visual-state-publisher"],
                },
                {
                    "name": "threejs-viewer-webserver",
                    "activation_timing": "after_start",
                    "command": str(python),
                    "args": ["-m", "http.server", "8000"],
                    "cwd": str(viewer_root),
                    "depends_on": ["web-bridge-fleets"],
                },
            ]
        )
    library_paths = [
        str(paths.install_prefix / "lib"),
        str(drone_root / "lib"),
        str(drone_root / "vendor" / "mujoco" / "lib"),
    ]
    launcher = {
        "version": "0.1",
        "defaults": {
            "cwd": str(paths.recipe_root),
            "stdout": str(paths.recipe_logs / "${asset}.out"),
            "stderr": str(paths.recipe_logs / "${asset}.err"),
            "start_grace_sec": 1,
            "delay_sec": 1,
            "env": {
                "set": {
                    "HAKO_CONFIG_PATH": str(paths.foundation_config / "cpp_core_config.json"),
                    "HAKO_PROFILE_SERVICE_CLIENT": "0",
                },
                "prepend": {
                    "lib_path": library_paths,
                    "PATH": [
                        str(python.parent),
                        str(paths.install_prefix / "bin"),
                        str(drone_binary.parent),
                    ],
                },
            },
        },
        "assets": assets,
    }
    output = paths.recipe_config / "launcher.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(launcher, indent=2) + "\n", encoding="utf-8")
    return output


def session_file(paths) -> Path:
    return paths.recipe_root / "runtime" / "launcher-session.json"


def runtime_environment(paths, drone_root: Path, system_name: str) -> dict[str, str]:
    env = os.environ.copy()
    python = resolve_foundation_python(paths, system_name)
    env["HAKO_CONFIG_PATH"] = str(paths.foundation_config / "cpp_core_config.json")
    env["PATH"] = os.pathsep.join(
        [str(python.parent), str(paths.install_prefix / "bin"), env.get("PATH", "")]
    )
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


def configure(experiment_path: Path, drone_root: Path) -> int:
    experiment = resolve_experiment(experiment_path)
    foundation = load_foundation_module()
    paths = foundation.resolve_workspace(ROOT, RECIPE_ID)
    foundation.prepare_workspace(paths)
    paths.recipe_validation.mkdir(parents=True, exist_ok=True)
    prepare_config(paths, drone_root, experiment)
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
    print("Conductor topology     : built-in owner in drone-service-1")
    print(
        "Visualization         : "
        + ("VSP + WebBridge + Three.js" if experiment.visualization else "disabled (headless)")
    )
    print("Scenario               : takeoff -> HAKONIWA -> hold -> finish")
    print("Next:")
    print(f"  python tools/foundation.py doctor --recipe {requirements}")
    print(f"  python tools/foundation.py plan --recipe {requirements}")
    print(
        "  python tools/recipe/drone_fleet_single_host.py doctor "
        f"--experiment {experiment_path}"
    )
    return 0


def _load_workspace(experiment_path: Path):
    experiment = resolve_experiment(experiment_path)
    foundation = load_foundation_module()
    paths = foundation.resolve_workspace(ROOT, RECIPE_ID)
    requirements = paths.recipe_config / "foundation-requirements.yaml"
    if not requirements.is_file():
        raise RecipeError("Recipe is not configured; run configure first")
    return experiment, foundation, paths, requirements


def doctor(
    experiment_path: Path,
    drone_root: Path,
    viewer_root: Path,
) -> int:
    experiment, foundation, paths, requirements = _load_workspace(experiment_path)
    inspection = foundation.inspect_foundation(requirements, paths.install_prefix)
    foundation.print_inspection(inspection, False)
    system_name = platform.system()
    checks: list[tuple[str, bool, str]] = []
    try:
        drone_binary = resolve_drone_binary(drone_root, system_name)
        checks.append(("native drone service", True, str(drone_binary)))
    except RecipeError as exc:
        checks.append(("native drone service", False, str(exc)))
    if experiment.visualization:
        try:
            publisher = resolve_visual_state_publisher(drone_root, system_name)
            checks.append(("visual-state publisher", True, str(publisher)))
        except RecipeError as exc:
            checks.append(("visual-state publisher", False, str(exc)))
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
                + "; run 'python tools/recipe/drone_fleet_single_host.py prepare-viewer'",
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
                "import sys; assert sys.version_info[:2] == (3, 12), sys.version; "
                "import hakopy, hakoniwa_pdu; "
                "import hakoniwa_pdu.apps.launcher.hako_launcher",
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
    ):
        path = paths.recipe_root / relative
        checks.append((relative, path.is_file(), str(path)))
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
) -> int:
    if doctor(experiment_path, drone_root, viewer_root) != 0:
        return 1
    experiment, _foundation, paths, _requirements = _load_workspace(experiment_path)
    system_name = platform.system()
    summary = paths.recipe_validation / "execution-summary.json"
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
        print("  python tools/recipe/drone_fleet_single_host.py status")
        print("  python tools/recipe/drone_fleet_single_host.py smoke")
        if experiment.visualization:
            print("  python tools/recipe/drone_fleet_single_host.py open-viewer")
        print("  python tools/recipe/drone_fleet_single_host.py stop")
        print(f"Session: {session_file(paths)}")
        print(f"Logs   : {paths.recipe_logs}")
    return rc


def control(experiment_path: Path, drone_root: Path, operation: str) -> int:
    _experiment, _foundation, paths, _requirements = _load_workspace(experiment_path)
    system_name = platform.system()
    command = _launcher_command(paths, system_name, operation)
    return _run(command, env=runtime_environment(paths, drone_root, system_name))


def smoke(experiment_path: Path, timeout_sec: float) -> int:
    experiment, _foundation, paths, _requirements = _load_workspace(experiment_path)
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


def viewer_url(drone_count: int) -> str:
    return (
        f"{VIEWER_URL_BASE}&dynamicSpawn=true"
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


def open_viewer(experiment_path: Path) -> int:
    experiment = resolve_experiment(experiment_path)
    if not experiment.visualization:
        raise RecipeError(
            "runtime.visualization=false; this headless experiment does not start "
            "VSP, WebBridge, or the Three.js viewer"
        )
    url = viewer_url(experiment.drone_count)
    return 0 if open_browser(url) else 1


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
    result.add_argument("--experiment", type=Path, default=DEFAULT_EXPERIMENT)
    result.add_argument(
        "--drone-root", type=Path, default=default_source("hakoniwa-drone-core")
    )
    result.add_argument(
        "--viewer-root", type=Path, default=default_source("hakoniwa-threejs-drone")
    )
    result.add_argument("--timeout-sec", type=float, default=300.0)
    return result


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        experiment_path = args.experiment.absolute()
        drone_root = args.drone_root.absolute()
        viewer_root = args.viewer_root.absolute()
        if args.command == "prepare-native":
            return prepare_native_distribution(drone_root, platform.system())
        if args.command == "prepare-viewer":
            return prepare_viewer(viewer_root)
        if args.command == "configure":
            return configure(experiment_path, drone_root)
        if args.command == "doctor":
            return doctor(experiment_path, drone_root, viewer_root)
        if args.command == "start":
            return start(experiment_path, drone_root, viewer_root)
        if args.command == "status":
            return control(experiment_path, drone_root, "status")
        if args.command == "stop":
            return control(experiment_path, drone_root, "terminate")
        if args.command == "open-viewer":
            return open_viewer(experiment_path)
        return smoke(experiment_path, args.timeout_sec)
    except RecipeError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
