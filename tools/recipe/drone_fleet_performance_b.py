#!/usr/bin/env python3
"""Run Experiment B: boundary-focused scaling across simulator processes."""

from __future__ import annotations

import argparse
import csv
import json
import math
import os
import platform
import signal
import shutil
import statistics
import subprocess
import sys
import time
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

import drone_fleet_performance_a as common
import drone_fleet_single_host as operator


RECIPE_ID = "drone-fleet-multi-process-scaling"
ROOT = Path(__file__).resolve().parents[2]
OPERATOR = Path(__file__).with_name("drone_fleet_multi_process.py")
DEFAULT_EXPERIMENT = (
    ROOT
    / "recipes"
    / "experiments"
    / "drone-fleet-performance"
    / "multi-process-scaling.yaml"
)
WORKLOAD_GRID = {
    32: [1, 2, 4, 6],
    64: [1, 2, 4, 6, 8],
    128: [1, 2, 4, 6, 8, 12, 15],
}
MAX_SIMULATOR_PROCESSES = 15
SELECTION_THRESHOLD = 0.05
SPREAD_THRESHOLD = 0.05
MINIMUM_STABLE_SUCCESS_COUNT = 2
INITIAL_MEASURED_RUN_COUNT = 3
ESCALATED_MEASURED_RUN_COUNT = 5
DRONE_SERVICE_PROCESS_MARKER = "main_hako_drone_service"
PROCESS_EXIT_TIMEOUT_SEC = 5.0
AGGREGATE_FIELDS = (
    "drone_count",
    "process_count",
    "recorded_count",
    "success_count",
    "failure_count",
    "median_step_wall_clock_sec",
    "min_step_wall_clock_sec",
    "max_step_wall_clock_sec",
    "relative_spread",
    "escalation_required",
    "stable_estimate",
    "performance_equivalent",
    "median_rtf",
    "realtime_recovered",
)


class MatrixError(RuntimeError):
    pass


@dataclass(frozen=True, order=True)
class Workload:
    drone_count: int
    process_count: int


def running_drone_services() -> dict[int, str]:
    """Return native Drone services, including processes orphaned by Launcher."""
    if platform.system() == "Windows":
        completed = subprocess.run(
            ["tasklist", "/FO", "CSV", "/NH"],
            check=False,
            capture_output=True,
            text=True,
        )
        if completed.returncode != 0:
            raise MatrixError("failed to inspect Windows processes with tasklist")
        processes: dict[int, str] = {}
        for row in csv.reader(completed.stdout.splitlines()):
            if len(row) < 2 or DRONE_SERVICE_PROCESS_MARKER not in row[0]:
                continue
            try:
                processes[int(row[1])] = row[0]
            except ValueError:
                continue
        return processes

    completed = subprocess.run(
        ["ps", "-Ao", "pid=,command="],
        check=False,
        capture_output=True,
        text=True,
    )
    if completed.returncode != 0:
        raise MatrixError("failed to inspect native processes with ps")
    processes = {}
    for line in completed.stdout.splitlines():
        stripped = line.strip()
        if not stripped or DRONE_SERVICE_PROCESS_MARKER not in stripped:
            continue
        pid_text, separator, command = stripped.partition(" ")
        if not separator:
            continue
        try:
            processes[int(pid_text)] = command.strip()
        except ValueError:
            continue
    return processes


def wait_for_drone_services_to_exit(
    pids: set[int], timeout_sec: float = PROCESS_EXIT_TIMEOUT_SEC
) -> dict[int, str]:
    deadline = time.monotonic() + timeout_sec
    while True:
        remaining = {
            pid: command
            for pid, command in running_drone_services().items()
            if pid in pids
        }
        if not remaining or time.monotonic() >= deadline:
            return remaining
        time.sleep(0.1)


def require_clean_process_state() -> set[int]:
    existing = running_drone_services()
    if existing:
        details = ", ".join(f"pid={pid} {command}" for pid, command in existing.items())
        raise MatrixError(
            "native Drone service processes already exist before the attempt; "
            "stop or terminate them before measuring: " + details
        )
    return set()


def cleanup_spawned_drone_services(before: set[int]) -> None:
    spawned = set(running_drone_services()) - before
    if not spawned:
        return
    print(
        "[WARN] Launcher termination left native Drone services; "
        "terminating Recipe-owned processes: " + ", ".join(map(str, sorted(spawned))),
        file=sys.stderr,
    )
    if platform.system() == "Windows":
        for pid in sorted(spawned):
            subprocess.run(
                ["taskkill", "/PID", str(pid), "/T"],
                check=False,
                capture_output=True,
                text=True,
            )
    else:
        for pid in spawned:
            try:
                os.kill(pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
    remaining = wait_for_drone_services_to_exit(spawned)
    if remaining:
        details = ", ".join(f"pid={pid} {command}" for pid, command in remaining.items())
        raise MatrixError(
            "native Drone services remained after Launcher termination and SIGTERM: "
            + details
        )
    print("[CLEANUP] all Recipe-owned native Drone services exited")


def _positive_int(value: Any, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise MatrixError(f"{label} must be an integer >= 1")
    return value


def load_matrix(path: Path) -> tuple[operator.Experiment, list[Workload], int]:
    raw = operator.load_simple_yaml(path)
    matrix = raw.get("matrix")
    if not isinstance(matrix, dict):
        raise MatrixError("matrix must be a mapping")
    unknown = sorted(set(matrix) - {"workloads", "attempts"})
    if unknown:
        raise MatrixError(f"unknown matrix fields: {', '.join(unknown)}")
    values = matrix.get("workloads")
    if not isinstance(values, dict) or not values:
        raise MatrixError("matrix.workloads must be a non-empty mapping")
    workload_grid: dict[int, list[int]] = {}
    for name, value in values.items():
        label = f"matrix.workloads.{name}"
        if not isinstance(value, dict):
            raise MatrixError(f"{label} must be a mapping")
        unknown_workload = sorted(set(value) - {"drone_count", "process_count"})
        if unknown_workload:
            raise MatrixError(f"unknown {label} fields: {', '.join(unknown_workload)}")
        drone_count = _positive_int(value.get("drone_count"), f"{label}.drone_count")
        process_values = value.get("process_count")
        if not isinstance(process_values, list) or not process_values:
            raise MatrixError(f"{label}.process_count must be a non-empty list")
        process_counts = [
            _positive_int(item, f"{label}.process_count[]")
            for item in process_values
        ]
        if process_counts != sorted(process_counts):
            raise MatrixError(f"{label}.process_count must be in ascending order")
        if len(set(process_counts)) != len(process_counts):
            raise MatrixError(f"{label}.process_count must not contain duplicates")
        if drone_count in workload_grid:
            raise MatrixError("matrix.workloads must not repeat drone_count")
        workload_grid[drone_count] = process_counts
    if list(workload_grid) != sorted(workload_grid):
        raise MatrixError("matrix.workloads must be in ascending drone_count order")
    if workload_grid != WORKLOAD_GRID:
        raise MatrixError(f"Experiment B workload grid must be {WORKLOAD_GRID}")
    workloads = [
        Workload(drone_count, process_count)
        for drone_count, process_counts in workload_grid.items()
        for process_count in process_counts
    ]
    attempts = _positive_int(matrix.get("attempts"), "matrix.attempts")
    base = operator.resolve_experiment(path)
    if max(workload.process_count for workload in workloads) > MAX_SIMULATOR_PROCESSES:
        raise MatrixError(
            "Experiment B requires one Fleet Asset plus each simulator process; "
            f"maximum process count is {MAX_SIMULATOR_PROCESSES}"
        )
    if base.measurement is None:
        raise MatrixError("Experiment B requires measurement.enabled=true")
    if base.measurement.conductor_implementation != "embedded":
        raise MatrixError("single-host Experiment B requires the embedded Conductor")
    return base, workloads, attempts


def workspace_root() -> Path:
    return ROOT / "work" / "recipes" / RECIPE_ID


def configuration_id(drone_count: int, process_count: int) -> str:
    return f"uav-{drone_count:03d}-proc-{process_count:02d}"


def result_path(base: operator.Experiment, workload: Workload, attempt: int) -> Path:
    assert base.measurement is not None
    return (
        workspace_root()
        / base.results_directory
        / base.measurement.series
        / configuration_id(workload.drone_count, workload.process_count)
        / f"attempt-{attempt:02d}"
        / "result.json"
    )


def generated_experiment_path(workload: Workload, attempt: int) -> Path:
    return (
        workspace_root()
        / "matrix"
        / "experiment-b"
        / configuration_id(workload.drone_count, workload.process_count)
        / f"attempt-{attempt:02d}.yaml"
    )


def host_preflight_paths(workload: Workload, attempt: int) -> tuple[Path, Path]:
    directory = workspace_root() / "runtime" / "host-preflight"
    identity = configuration_id(workload.drone_count, workload.process_count)
    stem = f"{identity}-attempt-{attempt:02d}"
    return directory / f"{stem}.json", directory / f"{stem}-samples.jsonl"


def collect_host_preflight(base: operator.Experiment, workload: Workload, attempt: int) -> None:
    assert base.measurement is not None
    foundation = operator.load_foundation_module()
    paths = foundation.resolve_workspace(ROOT, RECIPE_ID)
    python = operator.resolve_foundation_python(paths, platform.system())
    output, samples = host_preflight_paths(workload, attempt)
    command = [
        str(python),
        str(Path(__file__).with_name("assets") / "collect_machine_preflight.py"),
        "--duration-sec",
        str(base.measurement.preflight_duration_sec),
        "--sampling-interval-sec",
        str(base.measurement.sampling_interval_sec),
        "--settle-timeout-sec",
        str(base.measurement.preflight_settle_timeout_sec),
        "--cpu-limit-percent",
        str(base.measurement.preflight_max_cpu_average_percent),
        "--memory-limit-percent",
        str(base.measurement.preflight_max_memory_used_percent),
        "--output",
        str(output),
        "--samples",
        str(samples),
    ]
    print("+ " + " ".join(command), flush=True)
    if subprocess.run(command, cwd=ROOT, check=False).returncode != 0:
        raise MatrixError(
            "host machine preflight failed before workload activation; "
            f"restore a clean measurement environment: {output}"
        )
    config = workspace_root() / "config" / "measurement.json"
    payload = json.loads(config.read_text(encoding="utf-8"))
    payload["host_preflight_result_path"] = str(output.resolve())
    config.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def reusable_result(path: Path, base: operator.Experiment | None = None) -> bool:
    try:
        payload = common.load_result(path)
    except common.MatrixError:
        return False
    if payload.get("status") != "success" or not common.preflight_passed(payload):
        return False
    if base is None or base.measurement is None:
        return True
    preflight = payload.get("machine_preflight")
    if not isinstance(preflight, dict):
        return False
    cpu = preflight.get("cpu_average_percent")
    memory = preflight.get("memory_used_max_percent")
    return (
        isinstance(cpu, (int, float))
        and cpu <= base.measurement.preflight_max_cpu_average_percent
        and isinstance(memory, (int, float))
        and memory <= base.measurement.preflight_max_memory_used_percent
    )


def archive_non_reusable_result(path: Path) -> Path:
    attempt_dir = path.parent
    configuration_dir = attempt_dir.parent
    timestamp = time.strftime("%Y%m%d-%H%M%S")
    destination = (
        configuration_dir
        / "rejected"
        / f"{attempt_dir.name}-{timestamp}"
    )
    suffix = 1
    while destination.exists():
        destination = destination.with_name(f"{destination.name}-{suffix}")
        suffix += 1
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(attempt_dir), str(destination))
    print(f"[ARCHIVE] non-reusable result: {attempt_dir} -> {destination}")
    return destination


def archive_active_series(base: operator.Experiment) -> Path | None:
    assert base.measurement is not None
    series = (
        workspace_root()
        / base.results_directory
        / base.measurement.series
    )
    if not series.exists():
        return None
    timestamp = time.strftime("%Y%m%d-%H%M%S")
    destination = series.parent / "archive" / f"{series.name}-{timestamp}"
    suffix = 1
    while destination.exists():
        destination = destination.with_name(f"{destination.name}-{suffix}")
        suffix += 1
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(series), str(destination))
    print(f"[ARCHIVE] active series: {series} -> {destination}")
    return destination


def materialize_experiment(
    base: operator.Experiment, workload: Workload, attempt: int
) -> Path:
    assert base.measurement is not None
    measurement = replace(
        base.measurement,
        configuration_id=configuration_id(workload.drone_count, workload.process_count),
        attempt=attempt,
    )
    condition = replace(
        base,
        drone_count=workload.drone_count,
        drones_per_process=math.ceil(workload.drone_count / workload.process_count),
        process_count=workload.process_count,
        measurement=measurement,
    )
    payload = operator.resolved_experiment_dict(condition)
    payload.pop("resolved", None)
    output = generated_experiment_path(workload, attempt)
    operator.write_simple_yaml(output, payload)
    operator.resolve_experiment(output)
    return output


def run_operator(command: str, experiment: Path, *extra: str) -> int:
    invocation = [
        sys.executable,
        str(OPERATOR),
        command,
        "--experiment",
        str(experiment),
        *extra,
    ]
    print("+ " + " ".join(invocation), flush=True)
    return subprocess.run(invocation, cwd=ROOT, check=False).returncode


def validate_identity(
    payload: dict[str, Any], drone_count: int, process_count: int, attempt: int
) -> None:
    expected_id = configuration_id(drone_count, process_count)
    expected_run_id = f"{expected_id}-attempt-{attempt:02d}"
    metadata = payload.get("metadata")
    if not isinstance(metadata, dict):
        raise MatrixError(f"result metadata is missing for {expected_run_id}")
    actual = {
        "run_id": payload.get("run_id"),
        "configuration_id": metadata.get("configuration_id"),
        "drone_count": metadata.get("drone_count"),
        "process_count": metadata.get("process_count"),
        "attempt": metadata.get("attempt"),
    }
    expected = {
        "run_id": expected_run_id,
        "configuration_id": expected_id,
        "drone_count": drone_count,
        "process_count": process_count,
        "attempt": attempt,
    }
    mismatches = [
        f"{key}: expected={expected[key]!r} actual={actual[key]!r}"
        for key in expected
        if actual[key] != expected[key]
    ]
    phases = metadata.get("fleet_phase_results")
    if not isinstance(phases, list) or not phases:
        mismatches.append("fleet_phase_results: expected a non-empty list")
    else:
        for phase in phases:
            if not isinstance(phase, dict) or phase.get("total") != drone_count:
                mismatches.append(
                    "fleet_phase_results.total: "
                    f"expected={drone_count!r} actual="
                    f"{phase.get('total') if isinstance(phase, dict) else phase!r}"
                )
                break
    if mismatches:
        raise MatrixError("result identity mismatch: " + "; ".join(mismatches))


def summary_row(
    payload: dict[str, Any], path: Path, drone_count: int, process_count: int, attempt: int
) -> dict[str, Any]:
    validate_identity(payload, drone_count, process_count, attempt)
    row = common.summary_row(payload, path, drone_count, attempt)
    row["process_count"] = process_count
    return row


def summary_paths(base: operator.Experiment) -> tuple[Path, Path, Path]:
    assert base.measurement is not None
    directory = (
        workspace_root()
        / base.results_directory
        / base.measurement.series
        / "summary"
    )
    return (
        directory / "experiment-b.json",
        directory / "experiment-b.csv",
        directory / "experiment-b-aggregate.csv",
    )


def aggregate(
    rows: list[dict[str, Any]], drone_count: int, process_counts: list[int]
) -> tuple[list[dict[str, Any]], int | None, str]:
    summaries: list[dict[str, Any]] = []
    for process_count in process_counts:
        recorded = [
            row
            for row in rows
            if row["drone_count"] == drone_count
            and row["process_count"] == process_count
        ]
        successful = [
            row
            for row in recorded
            if row.get("status") == "success"
            and row.get("validation_passed") is True
            and isinstance(row.get("average_step_wall_clock_sec"), (int, float))
        ]
        values = [float(row["average_step_wall_clock_sec"]) for row in successful]
        rtf_values = [float(row["rtf"]) for row in successful]
        median = statistics.median(values) if values else None
        minimum = min(values) if values else None
        maximum = max(values) if values else None
        spread = (
            (maximum - minimum) / median
            if median is not None and median > 0 and len(values) >= 3
            else None
        )
        failure_count = len(recorded) - len(successful)
        escalation_required = failure_count > 0 or (
            spread is not None and spread > SPREAD_THRESHOLD
        )
        base_runs_complete = len(recorded) >= INITIAL_MEASURED_RUN_COUNT
        escalation_complete = (
            not escalation_required
            or len(recorded) >= ESCALATED_MEASURED_RUN_COUNT
        )
        summaries.append(
            {
                "drone_count": drone_count,
                "process_count": process_count,
                "recorded_count": len(recorded),
                "success_count": len(successful),
                "failure_count": failure_count,
                "median_step_wall_clock_sec": median,
                "min_step_wall_clock_sec": minimum,
                "max_step_wall_clock_sec": maximum,
                "relative_spread": spread,
                "escalation_required": escalation_required,
                "stable_estimate": (
                    base_runs_complete
                    and escalation_complete
                    and len(successful) >= MINIMUM_STABLE_SUCCESS_COUNT
                ),
                "performance_equivalent": False,
                "median_rtf": statistics.median(rtf_values) if rtf_values else None,
                "realtime_recovered": (
                    statistics.median(rtf_values) >= 1.0 if rtf_values else False
                ),
            }
        )
    stable = [row for row in summaries if row["stable_estimate"]]
    if len(stable) != len(process_counts):
        additional_runs_required = any(
            row["recorded_count"] < INITIAL_MEASURED_RUN_COUNT
            or (
                row["escalation_required"]
                and row["recorded_count"] < ESCALATED_MEASURED_RUN_COUNT
            )
            for row in summaries
        )
        return (
            summaries,
            None,
            "additional_runs_required"
            if additional_runs_required
            else "insufficient_successful_runs",
        )
    best = min(float(row["median_step_wall_clock_sec"]) for row in stable)
    candidates = []
    for row in stable:
        equivalent = float(row["median_step_wall_clock_sec"]) <= (1.0 + SELECTION_THRESHOLD) * best
        row["performance_equivalent"] = equivalent
        if equivalent:
            candidates.append(int(row["process_count"]))
    return summaries, min(candidates), "selected"


def workload_groups(workloads: list[Workload]) -> list[tuple[int, list[int]]]:
    groups: dict[int, list[int]] = {}
    for workload in workloads:
        groups.setdefault(workload.drone_count, []).append(workload.process_count)
    return list(groups.items())


def initial_escalation_targets(
    base: operator.Experiment, workloads: list[Workload], attempts: int
) -> tuple[list[Workload], list[str]]:
    rows: list[dict[str, Any]] = []
    missing: list[str] = []
    for workload in workloads:
        for attempt in range(1, attempts + 1):
            path = result_path(base, workload, attempt)
            if not path.is_file():
                missing.append(str(path))
                continue
            payload = common.load_result(path)
            rows.append(
                summary_row(
                    payload,
                    path,
                    workload.drone_count,
                    workload.process_count,
                    attempt,
                )
            )
    if missing:
        return [], missing
    targets: list[Workload] = []
    for drone_count, process_counts in workload_groups(workloads):
        aggregates, _selected, _status = aggregate(rows, drone_count, process_counts)
        targets.extend(
            Workload(drone_count, int(row["process_count"]))
            for row in aggregates
            if row["escalation_required"]
        )
    return targets, []


def summary_attempt_limits(
    base: operator.Experiment, workloads: list[Workload], attempts: int
) -> dict[Workload, int]:
    limits = {workload: attempts for workload in workloads}
    targets, missing = initial_escalation_targets(base, workloads, attempts)
    if missing:
        return limits
    additional_started = any(
        result_path(base, workload, attempt).is_file()
        for workload in targets
        for attempt in range(attempts + 1, ESCALATED_MEASURED_RUN_COUNT + 1)
    )
    if additional_started:
        for workload in targets:
            limits[workload] = ESCALATED_MEASURED_RUN_COUNT
    return limits


def summarize(base: operator.Experiment, workloads: list[Workload], attempts: int) -> int:
    rows: list[dict[str, Any]] = []
    missing: list[str] = []
    attempt_limits = summary_attempt_limits(base, workloads, attempts)
    for workload in workloads:
        for attempt in range(1, attempt_limits[workload] + 1):
            path = result_path(base, workload, attempt)
            if not path.is_file():
                missing.append(str(path))
                continue
            payload = common.load_result(path)
            rows.append(
                summary_row(
                    payload,
                    path,
                    workload.drone_count,
                    workload.process_count,
                    attempt,
                )
            )
    aggregates: list[dict[str, Any]] = []
    workload_summaries: list[dict[str, Any]] = []
    for drone_count, process_counts in workload_groups(workloads):
        group_aggregates, selected, selection_status = aggregate(
            rows, drone_count, process_counts
        )
        aggregates.extend(group_aggregates)
        realtime_processes = [
            int(row["process_count"])
            for row in group_aggregates
            if row["stable_estimate"] and row["realtime_recovered"]
        ]
        workload_summaries.append(
            {
                "drone_count": drone_count,
                "process_counts": process_counts,
                "selection_status": selection_status,
                "selected_process_count": selected,
                "minimum_realtime_process_count": (
                    min(realtime_processes) if realtime_processes else None
                ),
                "configuration_summary": group_aggregates,
            }
        )
    json_path, csv_path, aggregate_path = summary_paths(base)
    json_path.parent.mkdir(parents=True, exist_ok=True)
    report = {
        "experiment": "multi-process-scaling",
        "drone_counts": [drone_count for drone_count, _ in workload_groups(workloads)],
        "expected_result_count": sum(attempt_limits.values()),
        "recorded_result_count": len(rows),
        "complete": not missing,
        "missing_results": missing,
        "selection_threshold": SELECTION_THRESHOLD,
        "workload_summary": workload_summaries,
        "expected_attempts_by_configuration": {
            configuration_id(
                workload.drone_count, workload.process_count
            ): attempt_limits[workload]
            for workload in workloads
        },
        "configuration_summary": aggregates,
        "results": rows,
    }
    json_path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    with csv_path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=common.SUMMARY_FIELDS)
        writer.writeheader()
        writer.writerows(rows)
    with aggregate_path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=AGGREGATE_FIELDS)
        writer.writeheader()
        writer.writerows(aggregates)
    print(f"Summary JSON    : {json_path}")
    print(f"Raw summary CSV : {csv_path}")
    print(f"Aggregate CSV   : {aggregate_path}")
    print(f"Recorded        : {len(rows)}/{sum(attempt_limits.values())}")
    for workload_summary in workload_summaries:
        print(
            f"UAV={workload_summary['drone_count']:3d}       : "
            f"{workload_summary['selection_status']} "
            f"(selected={workload_summary['selected_process_count']}, "
            f"realtime-min={workload_summary['minimum_realtime_process_count']})"
        )
    return 0 if not missing else 1


def plan(base: operator.Experiment, workloads: list[Workload], attempts: int) -> int:
    print("Experiment B: multi-process single-host scaling")
    print(f"Conditions    : {len(workloads)} sparse workloads x {attempts} attempt(s)")
    for workload in workloads:
        partitions = operator.expected_partition_counts(
            workload.drone_count, workload.process_count
        )
        for attempt in range(1, attempts + 1):
            path = result_path(base, workload, attempt)
            state = "RECORDED" if path.is_file() else "PENDING"
            print(
                f"  [{state}] UAV={workload.drone_count:3d} "
                f"process={workload.process_count:2d} partitions={partitions} "
                f"attempt={attempt:02d} result={path}"
            )
    targets, missing = initial_escalation_targets(base, workloads, attempts)
    if not missing:
        if targets:
            print(
                "Additional attempts: "
                + ", ".join(
                    f"UAV={workload.drone_count} process={workload.process_count} attempts=04,05"
                    for workload in targets
                )
            )
            print("Next              : run 'extend' after confirming a clean host")
        else:
            print("Additional attempts: none")
    return 0


def run_matrix(
    base: operator.Experiment,
    workloads: list[Workload],
    attempts: int,
    *,
    resume: bool,
    rerun_invalid: bool,
    restart_series: bool,
    attempt_plan: dict[Workload, list[int]] | None = None,
) -> int:
    assert base.measurement is not None
    if restart_series:
        archive_active_series(base)
    planned = attempt_plan or {
        workload: list(range(1, attempts + 1))
        for workload in workloads
    }
    existing = [
        result_path(base, workload, attempt)
        for workload, attempt_numbers in planned.items()
        for attempt in attempt_numbers
        if result_path(base, workload, attempt).is_file()
    ]
    non_reusable = [path for path in existing if not reusable_result(path, base)]
    if non_reusable and rerun_invalid:
        for path in non_reusable:
            archive_non_reusable_result(path)
        existing = [path for path in existing if path not in non_reusable]
    elif non_reusable:
        raise MatrixError(
            "non-reusable invalid/failed results exist; use --resume "
            "--rerun-invalid to archive and rerun them: "
            + ", ".join(str(path) for path in non_reusable)
        )
    if existing and not resume:
        raise MatrixError(
            "recorded results already exist; use --resume to preserve and skip them: "
            + ", ".join(str(path) for path in existing)
        )

    failed = False
    for workload, attempt_numbers in planned.items():
        for attempt in attempt_numbers:
            result = result_path(base, workload, attempt)
            if result.is_file():
                print(f"[SKIP] recorded result: {result}")
                continue
            before_pids = require_clean_process_state()
            generated = materialize_experiment(base, workload, attempt)
            print(
                f"\n=== Experiment B: UAV={workload.drone_count} "
                f"process={workload.process_count} attempt={attempt:02d} ===",
                flush=True,
            )
            start_attempted = False
            try:
                for command in ("configure", "doctor"):
                    if run_operator(command, generated) != 0:
                        raise MatrixError(
                            f"{command} failed for UAV={workload.drone_count}, "
                            f"process={workload.process_count}, attempt={attempt}"
                        )
                collect_host_preflight(base, workload, attempt)
                start_attempted = True
                if run_operator("start", generated) != 0:
                    raise MatrixError(
                        f"start failed for UAV={workload.drone_count}, "
                        f"process={workload.process_count}, attempt={attempt}"
                    )
                timeout = base.measurement.maximum_wall_time_sec + 30.0
                smoke_rc = run_operator("smoke", generated, "--timeout-sec", str(timeout))
            finally:
                if start_attempted:
                    stop_rc = run_operator("stop", generated)
                    if stop_rc != 0:
                        print(
                            f"[WARN] stop failed for UAV={workload.drone_count}, "
                            f"process={workload.process_count}, attempt={attempt}",
                            file=sys.stderr,
                        )
                    cleanup_spawned_drone_services(before_pids)
            if not result.is_file():
                raise MatrixError(f"measurement result is missing after smoke: {result}")
            payload = common.load_result(result)
            validate_identity(
                payload, workload.drone_count, workload.process_count, attempt
            )
            if not common.preflight_passed(payload):
                summarize(base, workloads, attempts)
                raise MatrixError(
                    "machine preflight failed; stop the series and restore a clean "
                    f"measurement environment: {result}"
                )
            if smoke_rc != 0 or payload.get("status") != "success":
                failed = True
                print(f"[FAIL] condition recorded; continuing: {result}", file=sys.stderr)
            else:
                print(f"[PASS] {result}")
    summary_rc = summarize(base, workloads, attempts)
    return 1 if failed or summary_rc != 0 else 0


def extend_matrix(
    base: operator.Experiment,
    workloads: list[Workload],
    attempts: int,
    *,
    rerun_invalid: bool,
) -> int:
    if attempts != INITIAL_MEASURED_RUN_COUNT:
        raise MatrixError(
            "extend requires matrix.attempts="
            f"{INITIAL_MEASURED_RUN_COUNT}; configured={attempts}"
        )
    targets, missing = initial_escalation_targets(base, workloads, attempts)
    if missing:
        raise MatrixError(
            "initial three-attempt series is incomplete; run with --resume first: "
            + ", ".join(missing)
        )
    initial_paths = [
        result_path(base, workload, attempt)
        for workload in workloads
        for attempt in range(1, attempts + 1)
    ]
    non_reusable = [path for path in initial_paths if not reusable_result(path, base)]
    if non_reusable:
        raise MatrixError(
            "initial series contains invalid/failed results; repair it with "
            "'run --resume --rerun-invalid' before extend: "
            + ", ".join(str(path) for path in non_reusable)
        )
    if not targets:
        print("Experiment B: no configurations require additional attempts")
        return summarize(base, workloads, attempts)
    print(
        "Experiment B additional attempts: "
        + ", ".join(
            f"UAV={workload.drone_count} process={workload.process_count} attempts=04,05"
            for workload in targets
        )
    )
    return run_matrix(
        base,
        workloads,
        attempts,
        resume=True,
        rerun_invalid=rerun_invalid,
        restart_series=False,
        attempt_plan={
            workload: list(
                range(attempts + 1, ESCALATED_MEASURED_RUN_COUNT + 1)
            )
            for workload in targets
        },
    )


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(
        description="Experiment B multi-process Drone Fleet matrix runner"
    )
    result.add_argument(
        "command", choices=["plan", "run", "extend", "summarize", "status", "stop"]
    )
    result.add_argument(
        "--restart-series",
        action="store_true",
        help="archive the active series before starting every configured attempt",
    )
    result.add_argument("--experiment", type=Path, default=DEFAULT_EXPERIMENT)
    result.add_argument(
        "--resume",
        action="store_true",
        help="preserve and skip attempts that already contain result.json",
    )
    result.add_argument(
        "--rerun-invalid",
        action="store_true",
        help="archive invalid/failed attempts and measure those identities again",
    )
    return result


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        base, workloads, attempts = load_matrix(args.experiment.resolve())
        if args.command == "plan":
            return plan(base, workloads, attempts)
        if args.command == "summarize":
            return summarize(base, workloads, attempts)
        if args.command == "extend":
            return extend_matrix(
                base,
                workloads,
                attempts,
                rerun_invalid=args.rerun_invalid,
            )
        if args.command in {"status", "stop"}:
            return run_operator(args.command, args.experiment.resolve())
        return run_matrix(
            base,
            workloads,
            attempts,
            resume=args.resume,
            rerun_invalid=args.rerun_invalid,
            restart_series=args.restart_series,
        )
    except (MatrixError, common.MatrixError, operator.RecipeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("interrupted", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
