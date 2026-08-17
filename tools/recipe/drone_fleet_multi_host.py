#!/usr/bin/env python3
"""Plan and materialize deterministic Drone Fleet multi-host bundles."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import shutil
import subprocess
import sys
import urllib.error
import urllib.request
import webbrowser
from pathlib import Path
from types import SimpleNamespace
from typing import Any

try:
    from tools.recipe import hakoniwa_conductor as conductor_package
    from tools.recipe import drone_fleet_runtime as fleet_runtime
    from tools.recipe import drone_fleet_single_host as yaml_support
except ModuleNotFoundError:
    import hakoniwa_conductor as conductor_package
    import drone_fleet_runtime as fleet_runtime
    import drone_fleet_single_host as yaml_support


RECIPE_ID = "drone-fleet-multi-host"
ROOT = Path(__file__).absolute().parents[2]
DEFAULT_EXPERIMENT = (
    ROOT
    / "recipes"
    / "experiments"
    / "drone-fleet-performance"
    / "multi-host-legacy-256.yaml"
)
DEFAULT_CONDUCTOR_ROOT = ROOT.parent / "hakoniwa-conductor-pro"
CONDUCTOR_PACKAGE_VERSION = "v1.1.0"
CONDUCTOR_IMPLEMENTATION = f"hakoniwa-conductor-{CONDUCTOR_PACKAGE_VERSION}"
DEFAULT_DRONE_ROOT = ROOT.parent / "hakoniwa-drone-core"
DEFAULT_VIEWER_ROOT = ROOT.parent / "hakoniwa-threejs-drone"
DEFAULT_CONDUCTOR_SCHEMA = (
    ROOT.parent / "hakoniwa-conductor" / "schemas" / "eu-input-v1.schema.json"
)
WORK_ROOT = ROOT / "work" / "recipes" / RECIPE_ID
LOCAL_SELECTION = ROOT / ".hako" / "recipes" / RECIPE_ID / "local-selection.json"


class RecipeError(RuntimeError):
    pass


def normalize_host_id(resolved: dict[str, Any], value: str) -> str:
    hosts = resolved["deployment"]["hosts"]
    if value in hosts:
        return value
    matches = [host_id for host_id, host in hosts.items() if host["role"] == value]
    if len(matches) == 1:
        return matches[0]
    raise RecipeError(
        f"unknown host {value!r}; choose one of: " + ", ".join(hosts)
    )


def validate_local_platform(host_id: str, host: dict[str, Any]) -> None:
    expected = {"macos": "Darwin", "linux": "Linux", "windows": "Windows"}[
        host["platform"]
    ]
    actual = platform.system()
    if actual != expected:
        raise RecipeError(
            f"host {host_id} requires {expected}, but this machine reports {actual}"
        )


def write_local_selection(
    resolved: dict[str, Any], index: dict[str, Any], host_value: str
) -> Path:
    host_id = normalize_host_id(resolved, host_value)
    host = resolved["deployment"]["hosts"][host_id]
    validate_local_platform(host_id, host)
    atomic_json(
        LOCAL_SELECTION,
        {
            "schema_version": 1,
            "recipe_id": RECIPE_ID,
            "host_id": host_id,
            "role": host["role"],
            "experiment_id": resolved["experiment_id"],
            "config_hash": index["config_hash"],
        },
    )
    return LOCAL_SELECTION


def load_local_selection(output_root: Path = WORK_ROOT) -> dict[str, Any]:
    if not LOCAL_SELECTION.is_file():
        raise RecipeError("local host is not selected; run configure --host <host-id>")
    try:
        selection = json.loads(LOCAL_SELECTION.read_text(encoding="utf-8"))
        index = json.loads((output_root / "bundle-index.json").read_text(encoding="utf-8"))
        resolved = json.loads(
            (output_root / "config" / "resolved-experiment.json").read_text(
                encoding="utf-8"
            )
        )
    except (OSError, json.JSONDecodeError) as exc:
        raise RecipeError(f"cannot load configured local host state: {exc}") from exc
    if selection.get("schema_version") != 1:
        raise RecipeError("unsupported local host selection schema; rerun configure --host")
    if selection.get("recipe_id") != RECIPE_ID:
        raise RecipeError("local host selection belongs to another Recipe")
    if selection.get("experiment_id") != resolved.get("experiment_id"):
        raise RecipeError("local host selection belongs to another experiment")
    if selection.get("config_hash") != index.get("config_hash"):
        raise RecipeError("local host selection is stale; rerun configure --host")
    host_id = selection.get("host_id")
    if host_id not in resolved["deployment"]["hosts"]:
        raise RecipeError("selected host is absent from the configured experiment")
    host = resolved["deployment"]["hosts"][host_id]
    if selection.get("role") != host["role"]:
        raise RecipeError("selected host role does not match the configured experiment")
    validate_local_platform(host_id, host)
    return {"selection": selection, "index": index, "resolved": resolved}


def _mapping(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise RecipeError(f"{label} must be a mapping")
    return value


def _positive(value: Any, label: str, *, allow_zero: bool = False) -> int:
    minimum = 0 if allow_zero else 1
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        qualifier = "non-negative" if allow_zero else "positive"
        raise RecipeError(f"{label} must be a {qualifier} integer")
    return value


def _required_text(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value:
        raise RecipeError(f"{label} must be a non-empty string")
    return value


def canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")


def digest(value: Any) -> str:
    return hashlib.sha256(canonical_bytes(value)).hexdigest()


def atomic_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def validate_experiment(raw: dict[str, Any]) -> dict[str, Any]:
    if raw.get("version") != 1:
        raise RecipeError("experiment version must be 1")
    identity = _mapping(raw.get("experiment"), "experiment")
    experiment_id = _required_text(identity.get("id"), "experiment.id")
    scale = _mapping(raw.get("scale"), "scale")
    total_drones = _positive(scale.get("drone_count"), "scale.drone_count")
    total_processes = _positive(scale.get("process_count"), "scale.process_count")
    if scale.get("drones_per_process") != "auto":
        raise RecipeError("scale.drones_per_process must be auto")

    runtime = _mapping(raw.get("runtime"), "runtime")
    if runtime.get("mode") != "native":
        raise RecipeError("runtime.mode must be native")
    visualization_enabled = runtime.get("visualization")
    if not isinstance(visualization_enabled, bool):
        raise RecipeError("runtime.visualization must be boolean")
    if not isinstance(runtime.get("show_runner_real_time_sync"), bool):
        raise RecipeError("runtime.show_runner_real_time_sync must be boolean")
    conductor = _mapping(runtime.get("conductor"), "runtime.conductor")
    profile = conductor.get("profile")
    expected_conductor = {
        "legacy-distributed-10ms": {
            "implementation": CONDUCTOR_IMPLEMENTATION,
            "delta_time_usec": 10000,
            "max_delay_time_usec": 20000,
            "real_sleep_msec": "unspecified",
            "simtime_publish_mode": "legacy_simple",
        },
        "icra-target-delta-boundary": {
            "implementation": CONDUCTOR_IMPLEMENTATION,
            "delta_time_usec": 1000,
            "max_delay_time_usec": 20000,
            "simtime_publish_interval_usec": 10000,
            "simtime_publish_mode": "delta_boundary",
        },
    }.get(profile)
    if expected_conductor is None:
        raise RecipeError(f"unsupported runtime.conductor.profile: {profile!r}")
    for field, expected in expected_conductor.items():
        if conductor.get(field) != expected:
            raise RecipeError(
                f"runtime.conductor.{field} must be {expected!r} for "
                f"{profile}"
            )
    if profile == "icra-target-delta-boundary":
        _positive(
            conductor.get("real_sleep_msec"),
            "runtime.conductor.real_sleep_msec",
            allow_zero=True,
        )

    deployment = _mapping(raw.get("deployment"), "deployment")
    if deployment.get("mode") != "multi_host":
        raise RecipeError("deployment.mode must be multi_host")
    server_host = _required_text(deployment.get("server_host"), "deployment.server_host")
    transport = _mapping(deployment.get("transport"), "deployment.transport")
    if transport.get("type") != "tcp" or transport.get("connection_initiator") != "client":
        raise RecipeError("deployment transport must be client-initiated tcp")
    base_port = _positive(transport.get("base_port"), "deployment.transport.base_port")
    if base_port > 65535:
        raise RecipeError("deployment.transport.base_port must be <= 65535")

    hosts = _mapping(deployment.get("hosts"), "deployment.hosts")
    if set(hosts) != {"srv-01", "cli-01"}:
        raise RecipeError(
            "this 1-by-1 Recipe requires exactly the stable host ids "
            "srv-01 and cli-01"
        )
    expected_roles = {"srv-01": "server", "cli-01": "client"}
    for host_id, expected_role in expected_roles.items():
        host = _mapping(hosts[host_id], f"deployment.hosts.{host_id}")
        if host.get("role") != expected_role:
            raise RecipeError(
                f"deployment.hosts.{host_id}.role must be {expected_role}"
            )
    servers = [host_id for host_id, host in hosts.items() if _mapping(host, f"host {host_id}").get("role") == "server"]
    if servers != [server_host]:
        raise RecipeError("deployment must contain exactly its declared server_host")

    ranges: list[tuple[int, int, str]] = []
    process_sum = 0
    drone_sum = 0
    for host_id, host_value in hosts.items():
        host = _mapping(host_value, f"deployment.hosts.{host_id}")
        role = host.get("role")
        if role not in {"server", "client"}:
            raise RecipeError(f"deployment.hosts.{host_id}.role is invalid")
        expected_mode = "activate-only"
        if host.get("launcher_mode") != expected_mode:
            raise RecipeError(
                f"deployment.hosts.{host_id}.launcher_mode must be {expected_mode}"
            )
        if role == "server":
            _required_text(host.get("address"), f"deployment.hosts.{host_id}.address")
            if "connect_to" in host:
                raise RecipeError(f"server host {host_id} must not declare connect_to")
        else:
            if "address" in host:
                raise RecipeError(f"client host {host_id} must not declare address")
            if host.get("connect_to") != server_host:
                raise RecipeError(f"client host {host_id} must connect_to {server_host}")
        count = _positive(host.get("drone_count"), f"deployment.hosts.{host_id}.drone_count")
        processes = _positive(host.get("process_count"), f"deployment.hosts.{host_id}.process_count")
        if processes > count:
            raise RecipeError(
                f"deployment.hosts.{host_id}.process_count must not exceed drone_count"
            )
        start = _positive(
            host.get("global_start_index"),
            f"deployment.hosts.{host_id}.global_start_index",
            allow_zero=True,
        )
        ranges.append((start, count, host_id))
        drone_sum += count
        process_sum += processes
    if drone_sum != total_drones:
        raise RecipeError("host drone counts must sum to scale.drone_count")
    if process_sum != total_processes:
        raise RecipeError("host process counts must sum to scale.process_count")
    expected_start = 0
    for start, count, host_id in sorted(ranges):
        if start != expected_start:
            raise RecipeError(
                f"host drone ranges are not contiguous: {host_id} starts at "
                f"{start}, expected {expected_start}"
            )
        expected_start += count

    visualization = None
    packet_size = 512
    if visualization_enabled:
        visualization = _mapping(raw.get("visualization"), "visualization")
        for owner in ("bridge_host", "viewer_host"):
            if visualization.get(owner) not in hosts:
                raise RecipeError(f"visualization.{owner} must name a declared host")
        packet_size = _positive(
            visualization.get("max_drones_per_packet"),
            "visualization.max_drones_per_packet",
        )
        publishers = _mapping(
            visualization.get("publishers"), "visualization.publishers"
        )
        if set(publishers) != set(hosts):
            raise RecipeError(
                "visualization.publishers must cover every host exactly once"
            )
        chunks: list[int] = []
        for host_id, publisher_value in publishers.items():
            publisher = _mapping(
                publisher_value, f"visualization.publishers.{host_id}"
            )
            chunk = _positive(
                publisher.get("chunk_index"),
                f"visualization.publishers.{host_id}.chunk_index",
                allow_zero=True,
            )
            if publisher.get("pdu_name") != f"drone_visual_state_array_{chunk}":
                raise RecipeError(f"publisher PDU name does not match chunk {chunk}")
            if publisher.get("transfer_policy") != "immediate-atomic":
                raise RecipeError("publisher transfer policy must be immediate-atomic")
            chunks.append(chunk)
        if len(chunks) != len(set(chunks)):
            raise RecipeError("publisher chunk indices must be unique")
        subscriptions = visualization.get("bridge_subscriptions")
        if not isinstance(subscriptions, list) or sorted(subscriptions) != sorted(chunks):
            raise RecipeError("bridge subscriptions must match publisher chunks")
    elif raw.get("visualization") is not None:
        raise RecipeError(
            "visualization section must be omitted when runtime.visualization=false"
        )

    scenario = _mapping(raw.get("scenario"), "scenario")
    if scenario.get("type") != "hakoniwa-word" or scenario.get("word") != "HAKONIWA":
        raise RecipeError("scenario must select the HAKONIWA word workload")
    for field in (
        "letter_width_m",
        "letter_height_m",
        "letter_gap_m",
        "altitude_m",
        "duration_sec",
        "hold_sec",
        "speed_m_s",
        "timeout_sec",
    ):
        value = scenario.get(field)
        if isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0:
            raise RecipeError(f"scenario.{field} must be a non-negative number")
    overrides = scenario.get("host_overrides", {})
    if not isinstance(overrides, dict) or not set(overrides).issubset(hosts):
        raise RecipeError("scenario.host_overrides must reference declared hosts")
    for host_id, override_value in overrides.items():
        override = _mapping(override_value, f"scenario.host_overrides.{host_id}")
        if set(override) != {"z_offset_m"}:
            raise RecipeError(
                f"scenario.host_overrides.{host_id} only supports z_offset_m"
            )
        z_offset = override["z_offset_m"]
        if isinstance(z_offset, bool) or not isinstance(z_offset, (int, float)):
            raise RecipeError(
                f"scenario.host_overrides.{host_id}.z_offset_m must be a number"
            )

    return {
        "schema_version": 1,
        "recipe_id": RECIPE_ID,
        "experiment_id": experiment_id,
        "scale": scale,
        "runtime": runtime,
        "deployment": deployment,
        "visualization": visualization,
        "scenario": scenario,
        "results": _mapping(raw.get("results"), "results"),
        "measurement": _mapping(raw.get("measurement"), "measurement"),
        "derived": {
            "server_host": server_host,
            "host_ids": list(hosts),
            "global_drone_range": [0, total_drones - 1],
            "max_drones_per_packet": packet_size,
        },
    }


def _run_checked(command: list[str], *, cwd: Path | None = None) -> None:
    result = subprocess.run(command, cwd=cwd, check=False)
    if result.returncode != 0:
        raise RecipeError(
            "command failed: " + subprocess.list2cmdline(command)
        )


def materialize_host_runtimes(
    resolved: dict[str, Any],
    output_root: Path,
    drone_root: Path = DEFAULT_DRONE_ROOT,
) -> dict[str, Path]:
    """Generate both host-local Drone runtime trees from the shared materializer."""
    drone_root = drone_root.resolve()
    if not (drone_root / "tools" / "gen_fleet_scale_config.py").is_file():
        raise RecipeError(f"hakoniwa-drone-core checkout is incomplete: {drone_root}")
    generated: dict[str, Path] = {}
    scenario = resolved["scenario"]
    total_drones = resolved["scale"]["drone_count"]
    visualization_enabled = bool(resolved["runtime"]["visualization"])
    visual = resolved["visualization"]
    for host_id, host in resolved["deployment"]["hosts"].items():
        bundle_root = output_root / "bundles" / host_id
        config = bundle_root / "config"
        (config / "pdudef").mkdir(parents=True, exist_ok=True)
        paths = SimpleNamespace(recipe_root=bundle_root, recipe_config=config)
        publisher = visual["publishers"][host_id] if visual is not None else None
        fleet_spec = fleet_runtime.FleetRuntimeSpec(
            local_drone_count=host["drone_count"],
            process_count=host["process_count"],
            visualization=visualization_enabled,
            global_drone_count=total_drones,
            global_start_index=host["global_start_index"],
            output_chunk_base_index=(publisher["chunk_index"] if publisher else 0),
            max_drones_per_packet=(
                visual["max_drones_per_packet"] if visual is not None else 512
            ),
        )
        scenario_spec = fleet_runtime.ScenarioRuntimeSpec(
            experiment_id=resolved["experiment_id"],
            local_drone_count=host["drone_count"],
            word=scenario["word"],
            letter_width_m=float(scenario["letter_width_m"]),
            letter_height_m=float(scenario["letter_height_m"]),
            letter_gap_m=float(scenario["letter_gap_m"]),
            altitude_m=float(scenario["altitude_m"]),
            duration_sec=float(scenario["duration_sec"]),
            hold_sec=float(scenario["hold_sec"]),
            speed_m_s=float(scenario["speed_m_s"]),
        )
        try:
            fleet_runtime.prepare_config(
                paths,
                drone_root,
                fleet_spec,
                run_checked=_run_checked,
                scenario_writer=lambda p=paths, s=scenario_spec: (
                    fleet_runtime.prepare_scenario(
                        p, drone_root, s, run_checked=_run_checked
                    )
                ),
            )
        except (FileNotFoundError, ValueError) as exc:
            raise RecipeError(str(exc)) from exc
        errors = fleet_runtime.validate_partitions(config, fleet_spec)
        if errors:
            raise RecipeError(f"{host_id} runtime validation failed: " + "; ".join(errors))
        generated[host_id] = config
    return generated


def host_launcher_spec(
    resolved: dict[str, Any], host_id: str
) -> fleet_runtime.LauncherRuntimeSpec:
    """Resolve portable launcher topology while keeping paths host-local."""
    hosts = resolved["deployment"]["hosts"]
    if host_id not in hosts:
        raise RecipeError(f"unknown host id: {host_id}")
    host = hosts[host_id]
    scenario = resolved["scenario"]
    visualization_enabled = bool(resolved["runtime"]["visualization"])
    visual = resolved["visualization"]
    override = scenario.get("host_overrides", {}).get(host_id, {})
    return fleet_runtime.LauncherRuntimeSpec(
        local_drone_count=host["drone_count"],
        process_count=host["process_count"],
        visualization=visualization_enabled,
        external_conductor=True,
        web_bridge=(visual is not None and visual["bridge_host"] == host_id),
        viewer=(visual is not None and visual["viewer_host"] == host_id),
        show_runner_real_time_sync=bool(
            resolved["runtime"]["show_runner_real_time_sync"]
        ),
        land=bool(scenario["land"]),
        speed_m_s=float(scenario["speed_m_s"]),
        timeout_sec=float(scenario["timeout_sec"]),
        delta_time_msec=int(scenario["delta_time_msec"]),
        z_offset_m=float(override.get("z_offset_m", 0.0)),
        viewer_activation_timing=(
            "before_start"
            if visual is not None and visual["viewer_host"] == host_id
            else "after_start"
        ),
    )


def conductor_launcher_asset(
    resolved: dict[str, Any],
    host_id: str,
    conductor_package_root: Path,
    generated_root: Path,
) -> dict[str, Any]:
    """Pin the Launcher to the verified public Conductor package."""
    hosts = resolved["deployment"]["hosts"]
    if host_id not in hosts:
        raise RecipeError(f"unknown host id: {host_id}")
    host = hosts[host_id]
    role = host["role"]
    binary = conductor_binary(conductor_package_root, role)
    if binary is None:
        raise RecipeError(
            f"Conductor {role} binary is missing under {conductor_package_root}; "
            "prepare the public v1.1.0 binary package"
        )
    config_name = host_id if role == "server" else host["node_id"]
    config = generated_root / "conductor" / f"{config_name}.json"
    args = ["--config", str(config)]
    if role == "server":
        args.extend(["--server-node-id", host["node_id"], "--enable-conductor"])
    return {
        "name": f"conductor-{role}",
        "activation_timing": "before_start",
        "command": str(binary),
        "args": args,
        "cwd": str(conductor_package_root),
        "delay_sec": 1,
    }


def host_runtime_paths(output_root: Path, host_id: str) -> SimpleNamespace:
    foundation = yaml_support.load_foundation_module()
    shared = foundation.resolve_workspace(ROOT, RECIPE_ID)
    bundle_root = output_root / "bundles" / host_id
    local_root = output_root / "local" / host_id
    runtime_root = output_root / "runtime" / host_id
    return SimpleNamespace(
        recipe_root=bundle_root,
        recipe_config=bundle_root / "config",
        recipe_logs=local_root / "logs",
        recipe_validation=local_root / "validation",
        runtime_root=runtime_root,
        install_prefix=shared.install_prefix,
        foundation_python=shared.foundation_python,
        foundation_config=shared.foundation_config,
    )


def measurement_trial_path(
    resolved: dict[str, Any], output_root: Path, host_id: str
) -> Path:
    measurement = resolved["measurement"]
    if measurement.get("enabled") is not True:
        raise RecipeError("measurement is not enabled")
    configuration_id = _required_text(
        measurement.get("configuration_id"), "measurement.configuration_id"
    )
    attempt = _positive(measurement.get("attempt"), "measurement.attempt")
    series = _required_text(measurement.get("series"), "measurement.series")
    results = resolved["results"]
    if results.get("enabled") is not True:
        raise RecipeError("measurement requires results.enabled=true")
    directory = _required_text(results.get("directory"), "results.directory")
    result_root = Path(directory)
    if result_root.is_absolute() or ".." in result_root.parts:
        raise RecipeError("results.directory must stay inside the Recipe workspace")
    return (
        output_root
        / result_root
        / series
        / "hosts"
        / host_id
        / configuration_id
        / f"attempt-{attempt:02d}"
    )


def prepare_host_measurement(
    resolved: dict[str, Any], output_root: Path, host_id: str, config_hash: str
) -> tuple[Path, Path] | None:
    measurement = resolved["measurement"]
    if measurement.get("enabled") is not True:
        return None
    configuration_id = _required_text(
        measurement.get("configuration_id"), "measurement.configuration_id"
    )
    attempt = _positive(measurement.get("attempt"), "measurement.attempt")
    trial = measurement_trial_path(resolved, output_root, host_id)
    trial.mkdir(parents=True, exist_ok=True)
    coordination = dict(
        _mapping(
            measurement.get("time_coordination"),
            "measurement.time_coordination",
        )
    )
    conductor = resolved["runtime"]["conductor"]
    coordination.update(
        {
            "conductor_delta_time_usec": conductor["delta_time_usec"],
            "conductor_max_delay_time_usec": conductor["max_delay_time_usec"],
            "conductor_real_sleep_msec": conductor["real_sleep_msec"],
            "simtime_publish_interval_usec": conductor.get(
                "simtime_publish_interval_usec"
            ),
            "simtime_publish_mode": conductor["simtime_publish_mode"],
            "conductor_implementation": conductor["implementation"],
        }
    )
    payload = {
        **measurement,
        "configuration_id": configuration_id,
        "attempt": attempt,
        "trial_directory": str(trial.resolve()),
        "host_id": host_id,
        "config_hash": config_hash,
        "time_coordination": coordination,
    }
    config_path = host_runtime_paths(output_root, host_id).recipe_config / "measurement.json"
    atomic_json(config_path, payload)
    atomic_json(trial / "resolved-measurement.json", payload)
    return config_path, trial


def conductor_binary(conductor_package_root: Path, role: str) -> Path | None:
    name = "main_server" if role == "server" else "main_client"
    candidate = conductor_package_root / "bin" / name
    if candidate.is_file() and os.access(candidate, os.X_OK):
        return candidate
    return None


CONDUCTOR_TIMING_FIELDS = (
    "delta_time_usec",
    "max_delay_time_usec",
    "real_sleep_msec",
    "simtime_publish_mode",
    "simtime_publish_interval_usec",
)


def generated_conductor_timing_errors(
    resolved: dict[str, Any], generated_root: Path
) -> list[str]:
    expected = build_conductor_input(resolved)["conductor_defaults"]
    errors: list[str] = []
    for host_id, host in resolved["deployment"]["hosts"].items():
        role = host["role"]
        config_name = host_id if role == "server" else host["node_id"]
        path = generated_root / "conductor" / f"{config_name}.json"
        if not path.is_file():
            errors.append(f"{host_id}: missing {path}")
            continue
        try:
            config = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            errors.append(f"{host_id}: cannot read {path}: {exc}")
            continue
        if not isinstance(config, dict):
            errors.append(f"{host_id}: generated config must be a JSON object")
            continue
        for field in CONDUCTOR_TIMING_FIELDS:
            if field not in expected:
                continue
            actual = config.get(field, "<omitted>")
            wanted = expected[field]
            if actual != wanted:
                errors.append(
                    f"{host_id}.{field}={actual!r}, expected {wanted!r}"
                )
    return errors


def conductor_binary_contract(binary: Path) -> tuple[bool, str]:
    try:
        result = subprocess.run(
            [str(binary), "--help"],
            check=False,
            capture_output=True,
            text=True,
        )
    except OSError as exc:
        return False, f"cannot execute {binary}: {exc}"
    help_text = result.stdout + result.stderr
    if "--real-sleep-msec" not in help_text:
        return False, f"stale binary without timing-profile support: {binary}"
    return True, str(binary)


def launcher_supports_manual_run(python: Path) -> tuple[bool, str]:
    probe = (
        "from hakoniwa_pdu.apps.launcher.hako_launcher import BACKGROUND_MODES; "
        "from hakoniwa_pdu.apps.launcher.hako_launcher_control import CONTROL_COMMANDS; "
        "assert 'activate-only' in BACKGROUND_MODES and 'start' in CONTROL_COMMANDS"
    )
    result = subprocess.run(
        [str(python), "-c", probe],
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode == 0:
        return True, "background activate-only and control start"
    return False, "Foundation Launcher is stale; rebuild hakoniwa-pdu-python"


def open_viewer_local(output_root: Path) -> int:
    state = load_local_selection(output_root)
    if state["selection"]["role"] != "server":
        raise RecipeError("open-viewer is server-only")
    if not state["resolved"]["runtime"]["visualization"]:
        raise RecipeError("open-viewer is unavailable for a headless experiment")
    health_url = "http://127.0.0.1:8000/index.html"
    try:
        with urllib.request.urlopen(health_url, timeout=2.0) as response:
            if response.status >= 400:
                raise RecipeError(
                    f"Three.js viewer returned HTTP {response.status}: {health_url}"
                )
    except (OSError, urllib.error.URLError) as exc:
        raise RecipeError(
            "Three.js viewer is not ready; run start on srv-01 first"
        ) from exc
    drone_count = int(state["resolved"]["scale"]["drone_count"])
    url = yaml_support.viewer_url(drone_count)
    print(f"Opening {url}")
    if not webbrowser.open(url):
        print(f"Open this URL in a browser: {url}")
    return 0


def prepare_host_launcher(
    state: dict[str, Any],
    output_root: Path,
    drone_root: Path,
    conductor_package_root: Path,
    viewer_root: Path,
) -> Path:
    host_id = state["selection"]["host_id"]
    role = state["selection"]["role"]
    paths = host_runtime_paths(output_root, host_id)
    for directory in (paths.recipe_logs, paths.recipe_validation, paths.runtime_root):
        directory.mkdir(parents=True, exist_ok=True)
    system_name = platform.system()
    spec = host_launcher_spec(state["resolved"], host_id)
    drone_binary = yaml_support.resolve_drone_binary(drone_root, system_name)
    python = yaml_support.resolve_foundation_python(paths, system_name)
    visual = (
        yaml_support.resolve_visual_state_publisher(drone_root, system_name)
        if spec.visualization
        else None
    )
    measurement = prepare_host_measurement(
        state["resolved"], output_root, host_id, state["index"]["config_hash"]
    )
    show_runner = (
        ROOT / "tools" / "recipe" / "assets" / "drone_fleet_performance_runner.py"
        if measurement is not None
        else drone_root
        / "drone_api"
        / "external_rpc"
        / "apps"
        / "show_asset_runner.py"
    )
    if not show_runner.is_file():
        raise RecipeError(f"Drone show runner not found: {show_runner}")
    leading = conductor_launcher_asset(
        state["resolved"],
        host_id,
        conductor_package_root,
        output_root / "config" / "conductor" / "generated",
    )
    try:
        return fleet_runtime.prepare_launcher(
            paths,
            drone_root,
            viewer_root,
            spec,
            drone_binary=drone_binary,
            python=python,
            show_runner=show_runner,
            summary=(
                measurement[1] / "execution-summary.json"
                if measurement is not None
                else paths.recipe_validation / "execution-summary.json"
            ),
            visual_state_publisher=visual,
            web_bridge_binary=(
                yaml_support.web_bridge_path(paths, system_name)
                if spec.web_bridge
                else None
            ),
            web_bridge_config_root=(
                yaml_support.bridge_config_root(paths) if spec.web_bridge else None
            ),
            leading_assets=[leading],
            performance_config=(measurement[0] if measurement is not None else None),
        )
    except ValueError as exc:
        raise RecipeError(str(exc)) from exc


def doctor_local(
    output_root: Path,
    drone_root: Path,
    conductor_package_root: Path,
    viewer_root: Path,
) -> int:
    state = load_local_selection(output_root)
    host_id = state["selection"]["host_id"]
    role = state["selection"]["role"]
    paths = host_runtime_paths(output_root, host_id)
    system_name = platform.system()
    checks: list[tuple[str, bool, str]] = []
    try:
        checks.append(
            (
                "Drone service",
                True,
                str(yaml_support.resolve_drone_binary(drone_root, system_name)),
            )
        )
        if state["resolved"]["runtime"]["visualization"]:
            checks.append(
                (
                    "Visual State Publisher",
                    True,
                    str(
                        yaml_support.resolve_visual_state_publisher(
                            drone_root, system_name
                        )
                    ),
                )
            )
        checks.append(
            (
                "Foundation Python",
                True,
                str(yaml_support.resolve_foundation_python(paths, system_name)),
            )
        )
    except yaml_support.RecipeError as exc:
        checks.append(("native runtime", False, str(exc)))
    else:
        launcher_ok, launcher_detail = launcher_supports_manual_run(
            yaml_support.resolve_foundation_python(paths, system_name)
        )
        checks.append(("Launcher manual-run contract", launcher_ok, launcher_detail))
    binary = conductor_binary(conductor_package_root, role)
    binary_ok, binary_detail = (
        conductor_binary_contract(binary)
        if binary is not None
        else (
            False,
            f"missing under {conductor_package_root}; prepare public {CONDUCTOR_PACKAGE_VERSION}",
        )
    )
    checks.append(
        (
            f"Conductor {role}",
            binary_ok,
            binary_detail,
        )
    )
    try:
        installed = conductor_package.validate_foundation_contract(
            conductor_package_root / "metadata" / "build-contract.txt",
            paths.install_prefix,
        )
        checks.append(
            (
                "Conductor Foundation build contract",
                True,
                ", ".join(f"{name}={value}" for name, value in installed.items()),
            )
        )
    except (conductor_package.ConductorRecipeError, OSError) as exc:
        checks.append(("Conductor Foundation build contract", False, str(exc)))
    conductor_config = (
        output_root
        / "config"
        / "conductor"
        / "generated"
        / "conductor"
        / f"{host_id if role == 'server' else state['resolved']['deployment']['hosts'][host_id]['node_id']}.json"
    )
    checks.append(("Conductor config", conductor_config.is_file(), str(conductor_config)))
    generated_root = output_root / "config" / "conductor" / "generated"
    timing_errors = generated_conductor_timing_errors(
        state["resolved"], generated_root
    )
    checks.append(
        (
            "Conductor timing contract",
            not timing_errors,
            "server/client generated configs match the Recipe"
            if not timing_errors
            else "; ".join(timing_errors),
        )
    )
    host = state["resolved"]["deployment"]["hosts"][host_id]
    visualization = state["resolved"]["visualization"]
    publisher = (
        visualization["publishers"][host_id]
        if visualization is not None
        else None
    )
    spec = fleet_runtime.FleetRuntimeSpec(
        local_drone_count=host["drone_count"],
        process_count=host["process_count"],
        visualization=bool(state["resolved"]["runtime"]["visualization"]),
        global_drone_count=state["resolved"]["scale"]["drone_count"],
        global_start_index=host["global_start_index"],
        output_chunk_base_index=(publisher["chunk_index"] if publisher else 0),
        max_drones_per_packet=(
            visualization["max_drones_per_packet"]
            if visualization is not None
            else 512
        ),
    )
    errors = fleet_runtime.validate_partitions(paths.recipe_config, spec)
    checks.append(("Drone partitions", not errors, "; ".join(errors) if errors else "configured"))
    if role == "server" and state["resolved"]["runtime"]["visualization"]:
        bridge = yaml_support.web_bridge_path(paths, system_name)
        checks.append(("WebBridge", bridge.is_file(), str(bridge)))
        missing_viewer = [
            path
            for path in yaml_support.viewer_required_files(viewer_root)
            if not path.is_file()
        ]
        checks.append(
            (
                "Three.js viewer",
                not missing_viewer,
                (
                    str(viewer_root)
                    if not missing_viewer
                    else "missing: " + ", ".join(map(str, missing_viewer))
                ),
            )
        )
    failed = False
    for label, ok, detail in checks:
        print(f"[{'OK' if ok else 'NG'}] {label}: {detail}")
        failed = failed or not ok
    if failed:
        return 1
    launcher = prepare_host_launcher(
        state, output_root, drone_root, conductor_package_root, viewer_root
    )
    print(f"[OK] launcher: {launcher}")
    print(f"[OK] local host: {host_id} ({role})")
    return 0


def launcher_control(
    command: str,
    output_root: Path,
    drone_root: Path,
    conductor_package_root: Path,
    viewer_root: Path,
) -> int:
    if command == "start" and doctor_local(
        output_root, drone_root, conductor_package_root, viewer_root
    ) != 0:
        return 1
    state = load_local_selection(output_root)
    host_id = state["selection"]["host_id"]
    role = state["selection"]["role"]
    if command == "run" and role != "server":
        raise RecipeError("run is server-only; the client starts from the remote event")
    paths = host_runtime_paths(output_root, host_id)
    if command == "run":
        ensure_conductor_clients_joined(output_root, paths.recipe_logs)
    python = yaml_support.resolve_foundation_python(paths, platform.system())
    session = paths.runtime_root / "launcher-session.json"
    if command == "start":
        argv = [
            str(python),
            "-m",
            "hakoniwa_pdu.apps.launcher.hako_launcher",
            str(paths.recipe_config / "launcher.json"),
            "--mode",
            "activate-only",
            "--background",
            str(session),
        ]
    else:
        operation = {"run": "start", "stop": "terminate", "status": "status"}[
            command
        ]
        argv = [
            str(python),
            "-m",
            "hakoniwa_pdu.apps.launcher.hako_launcher_ctl",
            operation,
            str(session),
        ]
    env = yaml_support.runtime_environment(paths, drone_root, platform.system())
    result = subprocess.run(argv, cwd=ROOT, env=env, check=False)
    return result.returncode


def expected_conductor_participants(output_root: Path) -> list[str]:
    path = output_root / "config" / "conductor" / "generated" / "remote-api.json"
    try:
        config = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RecipeError(
            f"cannot read generated Conductor participants: {path}: {exc}"
        ) from exc
    participants = config.get("participants")
    if not isinstance(participants, list):
        raise RecipeError(f"generated Conductor participants must be a list: {path}")
    names: list[str] = []
    for index, participant in enumerate(participants):
        if not isinstance(participant, dict):
            raise RecipeError(
                f"generated participant[{index}] must be an object: {path}"
            )
        name = participant.get("name")
        if not isinstance(name, str) or not name:
            raise RecipeError(
                f"generated participant[{index}].name must be non-empty: {path}"
            )
        if name in names:
            raise RecipeError(f"duplicate generated participant name {name!r}: {path}")
        names.append(name)
    return names


def ensure_conductor_clients_joined(output_root: Path, logs_root: Path) -> None:
    expected = expected_conductor_participants(output_root)
    log = logs_root / "conductor-server.out"
    try:
        content = log.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        raise RecipeError(
            f"cannot read Conductor server readiness log: {log}: {exc}"
        ) from exc
    marker = "Server run loop started."
    current_session_at = content.rfind(marker)
    if current_session_at < 0:
        raise RecipeError(
            f"Conductor server is not ready; current session marker is absent: {log}"
        )
    current_session = content[current_session_at:]
    joined = [
        name
        for name in expected
        if f"Handling join request from client: {name}" in current_session
    ]
    missing = [name for name in expected if name not in joined]
    if missing:
        joined_text = ", ".join(joined) if joined else "none"
        raise RecipeError(
            "Conductor clients are not ready; "
            f"joined: {joined_text}; missing: {', '.join(missing)}; log: {log}"
        )
    print(
        "[OK] Conductor clients joined: "
        + (", ".join(joined) if joined else "no remote participants")
    )


def _validate_managed_clean_path(path: Path, output_root: Path) -> None:
    if path.is_symlink():
        raise RecipeError(f"refusing to clean symlinked path: {path}")
    root = output_root.resolve()
    target = path.resolve()
    if target == root or root not in target.parents:
        raise RecipeError(f"refusing to clean path outside Recipe work: {path}")


def _remove_managed_path(path: Path) -> None:
    if not path.exists():
        print(f"[SKIP] absent: {path}")
        return
    if path.is_dir():
        shutil.rmtree(path)
    else:
        path.unlink()
    print(f"[CLEAN] {path}")


def clean_local(output_root: Path) -> int:
    state = load_local_selection(output_root)
    host_id = state["selection"]["host_id"]
    paths = host_runtime_paths(output_root, host_id)
    session = paths.runtime_root / "launcher-session.json"
    if session.is_file():
        try:
            session_state = json.loads(session.read_text(encoding="utf-8")).get(
                "state"
            )
        except (OSError, json.JSONDecodeError) as exc:
            raise RecipeError(
                f"cannot inspect Launcher session before clean: {exc}"
            ) from exc
        if session_state not in {"TERMINATED", "FAILED"}:
            raise RecipeError(
                f"Launcher session is {session_state!r}; run stop before clean"
            )
    targets = [
        paths.recipe_logs,
        paths.recipe_validation,
        paths.runtime_root,
    ]
    if state["resolved"]["measurement"].get("enabled") is True:
        targets.extend(
            measurement_trial_path(state["resolved"], output_root, result_host_id)
            for result_host_id in state["resolved"]["deployment"]["hosts"]
        )
    for target in targets:
        _validate_managed_clean_path(target, output_root)
    for target in targets:
        _remove_managed_path(target)
    print(
        f"[OK] cleaned local run artifacts: {host_id}; "
        "configuration and host selection were preserved"
    )
    return 0


def collect_local_evidence(output_root: Path) -> int:
    """Snapshot host-local logs beside the existing per-attempt result."""

    state = load_local_selection(output_root)
    host_id = state["selection"]["host_id"]
    paths = host_runtime_paths(output_root, host_id)
    trial = measurement_trial_path(state["resolved"], output_root, host_id)
    result = trial / "result.json"
    if not result.is_file():
        raise RecipeError(f"measurement result is missing: {result}")
    evidence = trial / "evidence"
    _validate_managed_clean_path(evidence, output_root)
    if evidence.exists():
        _remove_managed_path(evidence)
    evidence.mkdir(parents=True)
    copied: list[str] = []
    for label, source in (
        ("logs", paths.recipe_logs),
        ("validation", paths.recipe_validation),
        ("runtime", paths.runtime_root),
    ):
        if not source.is_dir():
            continue
        if any(path.is_symlink() for path in source.rglob("*")):
            raise RecipeError(f"refusing to collect symlinked evidence: {source}")
        destination = evidence / label
        shutil.copytree(source, destination)
        copied.append(label)
    payload = json.loads(result.read_text(encoding="utf-8"))
    metadata = _mapping(payload.get("metadata"), "result.metadata")
    manifest = {
        "version": 1,
        "host_id": host_id,
        "configuration_id": metadata.get("configuration_id"),
        "attempt": metadata.get("attempt"),
        "run_id": payload.get("run_id"),
        "config_hash": metadata.get("config_hash"),
        "directories": copied,
    }
    atomic_json(evidence / "manifest.json", manifest)
    print(f"[OK] collected attempt evidence: {trial}")
    print("Included: result files, " + (", ".join(copied) if copied else "manifest"))
    return 0


def git_identity(path: Path) -> dict[str, Any]:
    def output(*args: str) -> str:
        result = subprocess.run(
            ["git", *args], cwd=path, check=False, capture_output=True, text=True
        )
        if result.returncode != 0:
            return "unknown"
        return result.stdout.strip()

    return {
        "revision": output("rev-parse", "HEAD"),
        "dirty": bool(output("status", "--short")),
    }


def resolve_conductor_root(argument: Path | None = None) -> Path:
    configured = os.environ.get("HAKO_CONDUCTOR_PRO_ROOT", "").strip()
    root = argument or (Path(configured).expanduser() if configured else DEFAULT_CONDUCTOR_ROOT)
    root = root.resolve()
    required = [root / "tools" / "hako.py", root / "eu-config", root / "CMakeLists.txt"]
    missing = [str(path) for path in required if not path.exists()]
    if missing:
        raise RecipeError(
            "explicit hakoniwa-conductor-pro checkout is incomplete: "
            + ", ".join(missing)
            + "; public Conductor fallback is forbidden"
        )
    return root


def resolve_conductor_package(argument: Path | None = None) -> Path:
    """Resolve and verify the public Conductor runtime package for this Recipe."""

    configured = os.environ.get("HAKO_CONDUCTOR_PACKAGE_ROOT", "").strip()
    explicit = argument or (Path(configured).expanduser() if configured else None)
    try:
        target = conductor_package.detect_target(version=CONDUCTOR_PACKAGE_VERSION)
        if explicit is not None:
            package = explicit.resolve()
            conductor_package.validate_package(package, target)
            return package
        result = conductor_package.doctor(CONDUCTOR_PACKAGE_VERSION)
        return Path(str(result["package"])).resolve()
    except (conductor_package.ConductorRecipeError, OSError) as exc:
        raise RecipeError(
            f"public Hakoniwa Conductor {CONDUCTOR_PACKAGE_VERSION} package is not "
            "ready: "
            f"{exc}; run `python3 tools/recipe/hakoniwa_conductor.py configure "
            f"--version {CONDUCTOR_PACKAGE_VERSION} --accept-license`"
        ) from exc


def conductor_runtime_identity(package: Path) -> dict[str, Any]:
    """Record the executable identity actually selected for a generated bundle."""

    return {
        "revision": CONDUCTOR_PACKAGE_VERSION,
        "dirty": False,
        "distribution": "public-binary",
        "package": package.name,
        "build_contract_sha256": sha256_file(
            package / "metadata" / "build-contract.txt"
        ),
        "binaries": {
            name: sha256_file(package / "bin" / name)
            for name in ("main_server", "main_client")
        },
    }


def resolve_conductor_schema(argument: Path | None = None) -> Path:
    configured = os.environ.get("HAKO_CONDUCTOR_EU_INPUT_SCHEMA", "").strip()
    path = argument or (
        Path(configured).expanduser() if configured else DEFAULT_CONDUCTOR_SCHEMA
    )
    path = path.resolve()
    if not path.is_file():
        raise RecipeError(
            "public Hakoniwa Conductor eu-input schema is missing: " + str(path)
        )
    schema = json.loads(path.read_text(encoding="utf-8"))
    if schema.get("$id") != (
        "https://github.com/hakoniwalab/hakoniwa-conductor/"
        "schemas/eu-input-v1.schema.json"
    ):
        raise RecipeError(f"unexpected Conductor eu-input schema identity: {path}")
    return path


def build_conductor_input(resolved: dict[str, Any]) -> dict[str, Any]:
    """Translate the Recipe contract into Conductor PRO's canonical eu-input."""
    deployment = resolved["deployment"]
    hosts = deployment["hosts"]
    server_host_id = resolved["derived"]["server_host"]
    server = hosts[server_host_id]
    clients = [host for host in hosts.values() if host["role"] == "client"]
    if len(clients) != 1:
        raise RecipeError(
            "the current Drone Fleet runtime generator requires exactly one client; "
            "additional clients need explicit server-side node placement"
        )
    client = clients[0]
    client_node = client["node_id"]
    server_node = server["node_id"]
    visualization = resolved["visualization"]
    client_host_id = next(
        host_id for host_id, host in hosts.items() if host["role"] == "client"
    )
    conductor = resolved["runtime"]["conductor"]
    transport = deployment["transport"]

    conductor_defaults = {
        "delta_time_usec": conductor["delta_time_usec"],
        "max_delay_time_usec": conductor["max_delay_time_usec"],
    }
    if conductor["profile"] != "legacy-distributed-10ms":
        conductor_defaults.update(
            {
                "real_sleep_msec": conductor["real_sleep_msec"],
                "simtime_publish_mode": conductor["simtime_publish_mode"],
                "simtime_publish_interval_usec": conductor[
                    "simtime_publish_interval_usec"
                ],
            }
        )

    result = {
        "mode": "simple",
        "execution_nodes": [client_node],
        "connection_pairs": [
            {
                "client_node_id": client_node,
                "server_node_id": server_node,
            }
        ],
        "comm_defaults": {
            "tcp": {
                "base_port": transport["base_port"],
                "connection_initiator": transport["connection_initiator"],
            }
        },
        "conductor_defaults": conductor_defaults,
    }
    if visualization is None:
        result.update(
            {
                "execution_units": [],
                "pdu_groups": [],
                "eu_pdu_bindings": [],
            }
        )
        return result

    publisher = visualization["publishers"]
    client_publisher = publisher[client_host_id]
    visual_group = f"visual-state-{client_node}"
    visual_eu_type = "VisualStatePublisherEU"
    visual_eu = f"vsp-{client_node}"
    result.update(
        {
            "pdudef": "pdudef_visual_state.json",
            "robot_types": {
                "VisualStatePublisher": {
                    "pdutypes": "pdutypes_visual_state.json",
                }
            },
            "robots": [
                {
                    "name": "DroneVisualStatePublisher",
                    "type": "VisualStatePublisher",
                }
            ],
            "pdu_type_groups": [
                {
                    "id": visual_group,
                    "robot_types": [
                        {
                            "robot_type": "VisualStatePublisher",
                            "pdu_names": [client_publisher["pdu_name"]],
                        }
                    ],
                    "transfer_policy_id": client_publisher["transfer_policy"],
                }
            ],
            "eu_types": {
                visual_eu_type: {
                    "writes": [visual_group],
                    "reads": [],
                }
            },
            "execution_units": [
                {
                    "name": visual_eu,
                    "eu_type": visual_eu_type,
                    "robot_bindings": [
                        {
                            "robot_name": "DroneVisualStatePublisher",
                            "robot_type": "VisualStatePublisher",
                        }
                    ],
                }
            ],
            "unit_placement": {
                "mode": "manual",
                "nodes": {client_node: [visual_eu]},
            },
        }
    )
    return result


def materialize(
    experiment_path: Path,
    output_root: Path,
    conductor_root: Path | None = None,
    conductor_schema: Path | None = None,
    *,
    write: bool,
    conductor_package_root: Path | None = None,
) -> dict[str, Any]:
    raw = yaml_support.load_simple_yaml(experiment_path)
    resolved = validate_experiment(raw)
    conductor = resolve_conductor_root(conductor_root)
    schema = resolve_conductor_schema(conductor_schema)
    identities = {
        "business_pack": git_identity(ROOT),
        "hakoniwa_conductor": git_identity(schema.parents[1]),
        "hakoniwa_conductor_pro": git_identity(conductor),
        "hakoniwa_conductor_binary": (
            conductor_runtime_identity(conductor_package_root)
            if conductor_package_root is not None
            else {
                "revision": CONDUCTOR_PACKAGE_VERSION,
                "dirty": False,
                "distribution": "public-binary-declared",
            }
        ),
    }
    config_hash = digest(resolved)
    hosts = resolved["deployment"]["hosts"]
    conductor_input = build_conductor_input(resolved)
    conductor_input_sha256 = digest(conductor_input)
    bundles: dict[str, Any] = {}
    for host_id, host in hosts.items():
        visual = resolved["visualization"]
        bundle = {
            "schema_version": 1,
            "recipe_id": RECIPE_ID,
            "experiment_id": resolved["experiment_id"],
            "config_hash": config_hash,
            "host_id": host_id,
            "host": host,
            "server_host": resolved["derived"]["server_host"],
            "transport": resolved["deployment"]["transport"],
            "conductor": resolved["runtime"]["conductor"],
            "shared_input_refs": {
                "conductor": {
                    "sha256": conductor_input_sha256,
                }
            },
            "scenario": resolved["scenario"],
            "visualization": (
                {
                    "publisher": visual["publishers"][host_id],
                    "max_drones_per_packet": visual["max_drones_per_packet"],
                    "web_bridge": visual["bridge_host"] == host_id,
                    "viewer": visual["viewer_host"] == host_id,
                    "bridge_subscriptions": (
                        visual["bridge_subscriptions"]
                        if visual["bridge_host"] == host_id
                        else []
                    ),
                }
                if visual is not None
                else {
                    "publisher": None,
                    "web_bridge": False,
                    "viewer": False,
                    "bridge_subscriptions": [],
                }
            ),
            "measurement": resolved["measurement"],
            "source_identities": identities,
        }
        bundles[host_id] = bundle

    index = {
        "schema_version": 1,
        "recipe_id": RECIPE_ID,
        "experiment_id": resolved["experiment_id"],
        "config_hash": config_hash,
        "source_identities": identities,
        "shared_inputs": {
            "conductor": {
                "path": "config/conductor/eu-input.json",
                "sha256": conductor_input_sha256,
                "format": "hakoniwa-conductor/eu-input-v1",
                "schema": {
                    "id": (
                        "https://github.com/hakoniwalab/hakoniwa-conductor/"
                        "schemas/eu-input-v1.schema.json"
                    ),
                    "sha256": sha256_file(schema),
                },
            }
        },
        "generation": {
            "product": "hakoniwa-conductor-pro",
            "operation": "configure",
            "availability": "private",
            "required_for": "regeneration",
            "generated_artifacts_committed_for_publication": True,
        },
        "bundles": {
            host_id: {
                "path": f"bundles/{host_id}/manifest.json",
                "sha256": digest(bundle),
            }
            for host_id, bundle in bundles.items()
        },
    }
    if write:
        atomic_json(output_root / "config" / "resolved-experiment.json", resolved)
        for host_id, bundle in bundles.items():
            atomic_json(output_root / "bundles" / host_id / "manifest.json", bundle)
        atomic_json(
            output_root
            / "config"
            / "conductor"
            / "eu-input.json",
            conductor_input,
        )
        server_host = hosts[resolved["derived"]["server_host"]]
        atomic_json(
            output_root / "config" / "conductor" / "node-ip-map.json",
            {
                "nodes": {
                    server_host["machine_id"]: server_host["address"],
                }
            },
        )
        atomic_json(output_root / "bundle-index.json", index)
    return {
        "resolved": resolved,
        "bundles": bundles,
        "index": index,
        "component_inputs": {"conductor": conductor_input},
    }


def run_conductor_configure(
    conductor_root: Path, conductor_package_root: Path, eu_input_path: Path
) -> None:
    command = [
        sys.executable,
        str(conductor_root / "tools" / "hako.py"),
        "configure",
        "--config",
        str(eu_input_path),
    ]
    env = os.environ.copy()
    env["HAKONIWA_CONDUCTOR_GENERATOR_BIN_DIR"] = str(
        (conductor_package_root / "bin").resolve()
    )
    result = subprocess.run(command, cwd=conductor_root, env=env, check=False)
    if result.returncode != 0:
        raise RecipeError(
            f"hakoniwa-conductor-pro configure failed with rc={result.returncode}"
        )


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("--experiment", type=Path, default=DEFAULT_EXPERIMENT)
    result.add_argument(
        "--conductor-root",
        type=Path,
        help="private hakoniwa-conductor-pro checkout used only for configuration generation",
    )
    result.add_argument(
        "--conductor-package-root",
        type=Path,
        help="verified public Hakoniwa Conductor v1.1.0 package override",
    )
    result.add_argument("--conductor-schema", type=Path)
    result.add_argument("--drone-root", type=Path, default=DEFAULT_DRONE_ROOT)
    result.add_argument("--viewer-root", type=Path, default=DEFAULT_VIEWER_ROOT)
    result.add_argument("--output-root", type=Path, default=WORK_ROOT)
    commands = result.add_subparsers(dest="command", required=True)
    commands.add_parser("plan", help="validate and print the resolved host plan")
    configure_parser = commands.add_parser(
        "configure", help="materialize shared inputs and generated configuration"
    )
    configure_parser.add_argument(
        "--host",
        required=True,
        help="select this machine's host id (srv-01/cli-01) or unique role",
    )
    commands.add_parser("doctor", help="validate the locally selected host")
    commands.add_parser("start", help="activate the locally selected host assets")
    commands.add_parser("open-viewer", help="open the server-side Three.js viewer")
    commands.add_parser("run", help="start the simulation from the selected server")
    commands.add_parser("status", help="show the local Launcher status")
    commands.add_parser("stop", help="stop the locally selected host")
    commands.add_parser(
        "clean", help="remove stopped host-local logs, session, and attempt results"
    )
    commands.add_parser(
        "collect", help="snapshot local stdout/stderr and runtime evidence into the attempt"
    )
    return result


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    output_root = args.output_root.resolve()
    if args.command in {
        "doctor",
        "start",
        "open-viewer",
        "run",
        "status",
        "stop",
        "clean",
        "collect",
    }:
        try:
            if args.command == "clean":
                return clean_local(output_root)
            if args.command == "collect":
                return collect_local_evidence(output_root)
            if args.command == "doctor":
                return doctor_local(
                    output_root,
                    args.drone_root.resolve(),
                    resolve_conductor_package(args.conductor_package_root),
                    args.viewer_root.resolve(),
                )
            if args.command == "open-viewer":
                return open_viewer_local(output_root)
            return launcher_control(
                args.command,
                output_root,
                args.drone_root.resolve(),
                resolve_conductor_package(args.conductor_package_root),
                args.viewer_root.resolve(),
            )
        except (RecipeError, yaml_support.RecipeError) as exc:
            print(f"[ERROR] {exc}", file=sys.stderr)
            return 1
    try:
        runtime_package = (
            resolve_conductor_package(args.conductor_package_root)
            if args.command == "configure"
            else None
        )
        result = materialize(
            args.experiment.resolve(),
            output_root,
            args.conductor_root,
            args.conductor_schema,
            write=args.command == "configure",
            conductor_package_root=runtime_package,
        )
        if args.command == "configure":
            host_configs = materialize_host_runtimes(
                result["resolved"], output_root, args.drone_root
            )
            conductor_root = resolve_conductor_root(args.conductor_root)
            run_conductor_configure(
                conductor_root,
                runtime_package,
                output_root / "config" / "conductor" / "eu-input.json",
            )
            timing_errors = generated_conductor_timing_errors(
                result["resolved"],
                output_root / "config" / "conductor" / "generated",
            )
            if timing_errors:
                raise RecipeError(
                    "generated Conductor timing contract mismatch: "
                    + "; ".join(timing_errors)
                )
            selection = write_local_selection(
                result["resolved"], result["index"], args.host
            )
    except (RecipeError, yaml_support.RecipeError) as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        return 1
    index = result["index"]
    print(f"Experiment: {index['experiment_id']}")
    print(f"Config hash: {index['config_hash']}")
    for host_id, metadata in index["bundles"].items():
        print(f"Host bundle: {host_id} -> {metadata['path']} ({metadata['sha256']})")
    if args.command == "plan":
        print("Plan is read-only; run configure to write the bundles.")
    else:
        print(f"[OK] bundle index: {args.output_root.resolve() / 'bundle-index.json'}")
        print(
            f"[OK] generated Conductor config: "
            f"{args.output_root.resolve() / 'config/conductor/generated'}"
        )
        for host_id, config in host_configs.items():
            print(f"[OK] host runtime: {host_id} -> {config}")
        print(f"[OK] local host selection: {selection}")
        print(f"Local host: {json.loads(selection.read_text())['host_id']}")
    dirty = [name for name, value in index["source_identities"].items() if value["dirty"]]
    if dirty:
        print("[WARN] dirty source identities: " + ", ".join(dirty))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
