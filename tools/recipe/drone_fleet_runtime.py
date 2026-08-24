#!/usr/bin/env python3
"""Shared Drone Fleet runtime configuration materializer.

This module contains topology-neutral generation that is used by both the
single-host operator and the multi-host adapter.  It deliberately does not own
Foundation setup, process lifecycle, or Conductor topology.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import shutil
import sys
from typing import Any, Callable


RunChecked = Callable[..., None]
ScenarioWriter = Callable[[], Path]


@dataclass(frozen=True)
class FleetRuntimeSpec:
    local_drone_count: int
    process_count: int
    visualization: bool
    global_drone_count: int
    global_start_index: int = 0
    local_start_index: int = 0
    output_chunk_base_index: int = 0
    max_drones_per_packet: int = 512

    def __post_init__(self) -> None:
        if self.local_drone_count < 1:
            raise ValueError("local_drone_count must be >= 1")
        if not 1 <= self.process_count <= self.local_drone_count:
            raise ValueError("process_count must be in [1, local_drone_count]")
        if self.global_drone_count < self.local_drone_count:
            raise ValueError("global_drone_count must be >= local_drone_count")
        if self.global_start_index < 0 or self.local_start_index < 0:
            raise ValueError("drone start indices must be >= 0")
        if self.global_start_index + self.local_drone_count > self.global_drone_count:
            raise ValueError("local global range exceeds global_drone_count")
        if self.output_chunk_base_index < 0:
            raise ValueError("output_chunk_base_index must be >= 0")
        if self.max_drones_per_packet < 1:
            raise ValueError("max_drones_per_packet must be >= 1")


@dataclass(frozen=True)
class ScenarioRuntimeSpec:
    experiment_id: str
    local_drone_count: int
    word: str
    letter_width_m: float
    letter_height_m: float
    letter_gap_m: float
    altitude_m: float
    duration_sec: float
    hold_sec: float
    speed_m_s: float
    stroke_count: int = 26
    recommended_drones_per_stroke: int = 2

    def __post_init__(self) -> None:
        if self.local_drone_count < 1:
            raise ValueError("local_drone_count must be >= 1")
        if not self.experiment_id or not self.word:
            raise ValueError("experiment_id and word must be non-empty")
        if self.stroke_count < 1 or self.recommended_drones_per_stroke < 1:
            raise ValueError("scenario stroke limits must be positive")


@dataclass(frozen=True)
class LauncherRuntimeSpec:
    local_drone_count: int
    process_count: int
    visualization: bool
    external_conductor: bool
    web_bridge: bool
    viewer: bool
    show_runner_real_time_sync: bool
    land: bool
    speed_m_s: float
    timeout_sec: float
    delta_time_msec: int = 20
    z_offset_m: float = 0.0
    viewer_activation_timing: str = "after_start"
    final_hold_extra_sec: float = 0.0

    def __post_init__(self) -> None:
        if self.local_drone_count < 1:
            raise ValueError("local_drone_count must be >= 1")
        if not 1 <= self.process_count <= self.local_drone_count:
            raise ValueError("process_count must be in [1, local_drone_count]")
        if self.web_bridge and not self.visualization:
            raise ValueError("web_bridge requires visualization")
        if self.viewer and not self.web_bridge:
            raise ValueError("viewer requires web_bridge")
        if self.viewer_activation_timing not in {"before_start", "after_start"}:
            raise ValueError(
                "viewer_activation_timing must be before_start or after_start"
            )
        if self.delta_time_msec < 1:
            raise ValueError("delta_time_msec must be positive")
        if self.final_hold_extra_sec < 0:
            raise ValueError("final_hold_extra_sec must be >= 0")


def prepare_launcher(
    paths: Any,
    drone_root: Path,
    viewer_root: Path,
    spec: LauncherRuntimeSpec,
    *,
    drone_binary: Path,
    python: Path,
    show_runner: Path,
    summary: Path,
    visual_state_publisher: Path | None = None,
    web_bridge_binary: Path | None = None,
    web_bridge_config_root: Path | None = None,
    performance_config: Path | None = None,
    leading_assets: list[dict[str, Any]] | None = None,
) -> Path:
    """Materialize one host-local Launcher while preserving proven semantics."""
    shared_env = {
        "set": {
            "HAKO_CONFIG_PATH": str(paths.foundation_config / "cpp_core_config.json"),
            "HAKO_PROFILE_SERVICE_CLIENT": "0",
        }
    }
    service_assets: list[dict[str, Any]] = []
    for index in range(1, spec.process_count + 1):
        fleet = (
            "config/drone/fleets/api-current.json"
            if spec.process_count == 1
            else f"config/drone/fleets/api-current-part{index}.json"
        )
        args = [fleet, "config/pdudef/drone-pdudef-current.json"]
        if spec.process_count > 1:
            args += ["--asset-name", f"drone-{index}"]
        if spec.external_conductor or index >= 2:
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
        if index == 1 and leading_assets:
            asset["depends_on"] = [leading_assets[-1]["name"]]
        elif index >= 2:
            asset["depends_on"] = [f"drone-service-{index - 1}"]
        service_assets.append(asset)

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
        str(spec.local_drone_count),
        "--asset-name",
        "ShowRunnerAsset",
        "--proc-count",
        str(spec.process_count),
        "--summary-json",
        str(summary),
        "--assign-mode",
        "index",
        "--speed",
        str(spec.speed_m_s),
        "--timeout-sec",
        str(spec.timeout_sec),
        "--delta-time-msec",
        str(spec.delta_time_msec),
        "--poll-sleep-msec",
        "0",
        "--final-hold-extra-sec",
        str(spec.final_hold_extra_sec),
    ]
    if spec.show_runner_real_time_sync:
        show_args.append("--real-time-sync")
    if spec.land:
        show_args.append("--land")
    if spec.z_offset_m:
        show_args.extend(["--z-offset-m", str(spec.z_offset_m)])
    show_env = shared_env
    if performance_config is not None:
        show_env = {
            "set": {
                **shared_env["set"],
                "HAKO_DRONE_ROOT": str(drone_root),
                "HAKO_PERFORMANCE_CONFIG": str(performance_config),
            }
        }
    assets: list[dict[str, Any]] = list(leading_assets or []) + service_assets + [
        {
            "name": "show-runner",
            "activation_timing": "before_start",
            "command": str(python),
            "args": show_args,
            "cwd": str(drone_root),
            "env": show_env,
            "depends_on": [service_assets[-1]["name"]],
            "delay_sec": 1,
        }
    ]
    if spec.visualization:
        if visual_state_publisher is None:
            raise ValueError("visualization requires visual_state_publisher")
        assets.append(
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
            }
        )
    if spec.web_bridge:
        if web_bridge_binary is None or web_bridge_config_root is None:
            raise ValueError("web_bridge requires its binary and config root")
        assets.append(
            {
                "name": "web-bridge-fleets",
                "activation_timing": "before_start",
                "command": str(web_bridge_binary),
                "args": [
                    "--config-root",
                    str(web_bridge_config_root),
                    "--node-name",
                    "web_bridge_fleets_node1",
                    "--delta-time-step-usec",
                    "20000",
                    "--enable-ondemand",
                ],
                "cwd": str(paths.recipe_root),
                "env": shared_env,
                "depends_on": ["visual-state-publisher"],
            }
        )
    if spec.viewer:
        assets.append(
            {
                "name": "threejs-viewer-webserver",
                "activation_timing": spec.viewer_activation_timing,
                "command": str(python),
                "args": ["-m", "http.server", "8000"],
                "cwd": str(viewer_root),
                "depends_on": ["web-bridge-fleets"],
            }
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
                "set": shared_env["set"],
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


def prepare_scenario(
    paths: Any,
    drone_root: Path,
    spec: ScenarioRuntimeSpec,
    *,
    run_checked: RunChecked,
) -> Path:
    """Materialize the proven word-show scenario for one host-local fleet."""
    formation = (
        paths.recipe_config
        / "scenario"
        / "formations"
        / f"formation-{spec.word}.json"
    )
    minimum_points = (
        spec.recommended_drones_per_stroke
        if spec.local_drone_count
        >= spec.stroke_count * spec.recommended_drones_per_stroke
        else 1
    )
    generator = drone_root / "tools" / "drone-show" / "gen_word_formation.py"
    if not generator.is_file():
        raise FileNotFoundError(f"word formation generator not found: {generator}")
    run_checked(
        [
            sys.executable,
            str(generator),
            "--word",
            spec.word,
            "--count",
            str(spec.local_drone_count),
            "--out",
            str(formation),
            "--id",
            spec.word,
            "--letter-width",
            str(spec.letter_width_m),
            "--letter-height",
            str(spec.letter_height_m),
            "--gap",
            str(spec.letter_gap_m),
            "--scale",
            "1.0",
            "--min-seg-points",
            str(minimum_points),
        ],
        cwd=paths.recipe_root,
    )
    show = {
        "meta": {
            "name": spec.experiment_id,
            "version": "1.0",
            "drone_count": spec.local_drone_count,
        },
        "options": {
            "center": [0.0, 0.0, 0.0],
            "scale": 1.0,
            "base_alt": spec.altitude_m,
            "min_distance": 0.0,
            "max_speed": spec.speed_m_s,
            "failure_policy": "hold",
        },
        "formation_files": [
            {"id": spec.word, "path": f"formations/formation-{spec.word}.json"}
        ],
        "timeline": [
            {
                "formation": spec.word,
                "duration_sec": spec.duration_sec,
                "hold_sec": spec.hold_sec,
            }
        ],
    }
    output = paths.recipe_config / "scenario" / "show.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(show, indent=2) + "\n", encoding="utf-8")
    return output


def single_host_spec(experiment: Any) -> FleetRuntimeSpec:
    """Preserve the established single-host materialization defaults."""
    return FleetRuntimeSpec(
        local_drone_count=experiment.drone_count,
        process_count=experiment.process_count,
        visualization=experiment.visualization,
        global_drone_count=experiment.drone_count,
    )


def expected_partition_counts(drone_count: int, process_count: int) -> list[int]:
    """Distribute drones evenly, assigning remainders to final processes."""
    if process_count < 1 or process_count > drone_count:
        raise ValueError("process_count must be in [1, drone_count]")
    base = drone_count // process_count
    remainder = drone_count % process_count
    counts = [base] * process_count
    for index in range(process_count - remainder, process_count):
        counts[index] += 1
    return counts


def prepare_config(
    paths: Any,
    drone_root: Path,
    spec: FleetRuntimeSpec,
    *,
    run_checked: RunChecked,
    scenario_writer: ScenarioWriter,
) -> None:
    """Materialize host-local Fleet, partition, PDU, and VSP configuration."""
    config = paths.recipe_config
    fleet = config / "drone" / "fleets" / "api-current.json"
    service = config / "drone" / "fleets" / "services" / "api-current-service.json"
    pdudef = config / "pdudef" / "drone-pdudef-current.json"
    shared_service_path = "config/drone/fleets/services/api-current-service.json"

    for pattern_root, pattern in (
        (config / "drone" / "fleets", "api-current-part*.json"),
        (config / "drone" / "fleets" / "services", "api-current-service-part*.json"),
    ):
        if pattern_root.is_dir():
            for stale_partition in pattern_root.glob(pattern):
                stale_partition.unlink()

    run_checked(
        [
            sys.executable,
            str(drone_root / "tools" / "gen_fleet_scale_config.py"),
            "--drone-count",
            str(spec.local_drone_count),
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
    if spec.process_count > 1:
        run_checked(
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
                str(config / "drone" / "fleets" / "services" / "api-current-service-part{part}.json"),
                "--shared-service-config-path",
                shared_service_path,
                "--parts",
                str(spec.process_count),
            ],
            cwd=paths.recipe_root,
        )

    for relative in (Path("config/drone/fleets/types"), Path("config/controller")):
        source = drone_root / relative
        if not source.is_dir():
            raise FileNotFoundError(f"Drone Core configuration not found: {source}")
        shutil.copytree(
            source,
            paths.recipe_root / relative,
            dirs_exist_ok=True,
            ignore=shutil.ignore_patterns(".DS_Store", "logs"),
        )

    source_pdutypes = drone_root / "config" / "pdudef" / "drone-pdutypes.json"
    if not source_pdutypes.is_file():
        raise FileNotFoundError(f"Drone PDU types not found: {source_pdutypes}")
    shutil.copy2(source_pdutypes, config / "pdudef" / "drone-pdutypes.json")

    visual_output = config / "assets" / "visual_state_publisher"
    visual_pdudef_names = (
        "drone-visual-state.json",
        "drone-visual-state-pdutypes.json",
        "pdutypes_time.json",
    )
    if spec.visualization:
        for name in visual_pdudef_names:
            source = drone_root / "config" / "pdudef" / name
            if not source.is_file():
                raise FileNotFoundError(
                    f"Drone visual-state PDU definition not found: {source}"
                )
            shutil.copy2(source, config / "pdudef" / name)
        visual_source = drone_root / "config" / "assets" / "visual_state_publisher"
        if not visual_source.is_dir():
            raise FileNotFoundError(
                f"Visual-state publisher configuration not found: {visual_source}"
            )
        shutil.copytree(
            visual_source,
            visual_output,
            dirs_exist_ok=True,
            ignore=shutil.ignore_patterns(".DS_Store", "logs"),
        )
        command = [
            sys.executable,
            str(drone_root / "tools" / "gen_visual_state_publisher_config.py"),
            "--base-config",
            str(visual_output / "visual_state_publisher.json"),
            "--out",
            str(visual_output / "visual_state_publisher.runtime.json"),
            "--global-drone-count",
            str(spec.global_drone_count),
            "--local-drone-count",
            str(spec.local_drone_count),
            "--max-drones-per-packet",
            str(spec.max_drones_per_packet),
        ]
        # Preserve the exact established single-host command while exposing
        # only non-default placement values needed by multi-host callers.
        if spec.global_start_index:
            command.extend(["--global-start-index", str(spec.global_start_index)])
        if spec.local_start_index:
            command.extend(["--local-start-index", str(spec.local_start_index)])
        if spec.output_chunk_base_index:
            command.extend(
                ["--output-chunk-base-index", str(spec.output_chunk_base_index)]
            )
        run_checked(command, cwd=paths.recipe_root)
    else:
        if visual_output.exists():
            shutil.rmtree(visual_output)
        for name in visual_pdudef_names:
            stale = config / "pdudef" / name
            if stale.exists():
                stale.unlink()

    scenario_writer()


def validate_partitions(config: Path, spec: FleetRuntimeSpec) -> list[str]:
    """Return actionable errors for stale or incomplete Fleet partitions."""
    errors: list[str] = []
    fleet_root = config / "drone" / "fleets"
    service_root = fleet_root / "services"
    if spec.process_count == 1:
        fleet_paths = [fleet_root / "api-current.json"]
        service_paths = [service_root / "api-current-service.json"]
    else:
        fleet_paths = [
            fleet_root / f"api-current-part{index}.json"
            for index in range(1, spec.process_count + 1)
        ]
        service_paths = [
            service_root / f"api-current-service-part{index}.json"
            for index in range(1, spec.process_count + 1)
        ]

    counts = expected_partition_counts(spec.local_drone_count, spec.process_count)
    observed_names: list[str] = []
    for index, (fleet_path, service_path, expected_count) in enumerate(
        zip(fleet_paths, service_paths, counts), start=1
    ):
        if not fleet_path.is_file():
            errors.append(f"missing process {index} fleet partition: {fleet_path}")
            continue
        if not service_path.is_file():
            errors.append(f"missing process {index} service partition: {service_path}")
            continue
        try:
            fleet_payload = json.loads(fleet_path.read_text(encoding="utf-8"))
            service_payload = json.loads(service_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            errors.append(f"invalid process {index} partition: {exc}")
            continue
        drones = fleet_payload.get("drones")
        services = service_payload.get("services")
        if not isinstance(drones, list):
            errors.append(f"process {index} fleet partition has no drones list")
            continue
        if len(drones) != expected_count:
            errors.append(
                f"process {index} fleet partition has {len(drones)} drones; "
                f"expected {expected_count}"
            )
        if not isinstance(services, list) or len(services) != expected_count * 5:
            actual = len(services) if isinstance(services, list) else 0
            errors.append(
                f"process {index} service partition has {actual} services; "
                f"expected {expected_count * 5}"
            )
        for drone in drones:
            name = drone.get("name") if isinstance(drone, dict) else None
            if not isinstance(name, str) or not name:
                errors.append(f"process {index} fleet partition has an invalid drone name")
                continue
            observed_names.append(name)

    if len(observed_names) != len(set(observed_names)):
        errors.append("fleet partitions contain duplicate drone names")
    expected_names = {
        f"Drone-{index}" for index in range(1, spec.local_drone_count + 1)
    }
    observed = set(observed_names)
    if observed != expected_names:
        missing = sorted(expected_names - observed)
        unexpected = sorted(observed - expected_names)
        detail: list[str] = []
        if missing:
            detail.append("missing=" + ",".join(missing[:5]))
        if unexpected:
            detail.append("unexpected=" + ",".join(unexpected[:5]))
        errors.append(
            "fleet partition coverage mismatch"
            + (": " + " ".join(detail) if detail else "")
        )
    return errors
