#!/usr/bin/env python3
"""Run Experiment B: fixed-workload scaling across simulator processes."""

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
from dataclasses import replace
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
PROCESS_GRID = [1, 2, 4, 6, 8, 12, 15]
MAX_SIMULATOR_PROCESSES = 15
SELECTION_THRESHOLD = 0.05
SPREAD_THRESHOLD = 0.05
MINIMUM_STABLE_SUCCESS_COUNT = 2
INITIAL_MEASURED_RUN_COUNT = 3
ESCALATED_MEASURED_RUN_COUNT = 5
DRONE_SERVICE_PROCESS_MARKER = "main_hako_drone_service"
PROCESS_EXIT_TIMEOUT_SEC = 5.0
AGGREGATE_FIELDS = (
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
)


class MatrixError(RuntimeError):
    pass


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


def load_matrix(path: Path) -> tuple[operator.Experiment, list[int], int]:
    raw = operator.load_simple_yaml(path)
    matrix = raw.get("matrix")
    if not isinstance(matrix, dict):
        raise MatrixError("matrix must be a mapping")
    unknown = sorted(set(matrix) - {"process_count", "attempts"})
    if unknown:
        raise MatrixError(f"unknown matrix fields: {', '.join(unknown)}")
    values = matrix.get("process_count")
    if not isinstance(values, list) or not values:
        raise MatrixError("matrix.process_count must be a non-empty inline list")
    process_counts = [_positive_int(value, "matrix.process_count[]") for value in values]
    if process_counts != sorted(process_counts):
        raise MatrixError("matrix.process_count must be in ascending order")
    if len(set(process_counts)) != len(process_counts):
        raise MatrixError("matrix.process_count must not contain duplicates")
    if process_counts != PROCESS_GRID:
        raise MatrixError(f"Experiment B process grid must be {PROCESS_GRID}")
    attempts = _positive_int(matrix.get("attempts"), "matrix.attempts")
    base = operator.resolve_experiment(path)
    if base.drone_count != 128:
        raise MatrixError("Experiment B requires scale.drone_count=128")
    if max(process_counts) > MAX_SIMULATOR_PROCESSES:
        raise MatrixError(
            "Experiment B requires one Fleet Asset plus each simulator process; "
            f"maximum process count is {MAX_SIMULATOR_PROCESSES}"
        )
    if base.measurement is None:
        raise MatrixError("Experiment B requires measurement.enabled=true")
    if base.measurement.conductor_implementation != "embedded":
        raise MatrixError("single-host Experiment B requires the embedded Conductor")
    return base, process_counts, attempts


def workspace_root() -> Path:
    return ROOT / "work" / "recipes" / RECIPE_ID


def configuration_id(drone_count: int, process_count: int) -> str:
    return f"uav-{drone_count:03d}-proc-{process_count:02d}"


def result_path(base: operator.Experiment, process_count: int, attempt: int) -> Path:
    assert base.measurement is not None
    return (
        workspace_root()
        / base.results_directory
        / base.measurement.series
        / configuration_id(base.drone_count, process_count)
        / f"attempt-{attempt:02d}"
        / "result.json"
    )


def generated_experiment_path(process_count: int, attempt: int) -> Path:
    return (
        workspace_root()
        / "matrix"
        / "experiment-b"
        / configuration_id(128, process_count)
        / f"attempt-{attempt:02d}.yaml"
    )


def host_preflight_paths(process_count: int, attempt: int) -> tuple[Path, Path]:
    directory = workspace_root() / "runtime" / "host-preflight"
    stem = f"{configuration_id(128, process_count)}-attempt-{attempt:02d}"
    return directory / f"{stem}.json", directory / f"{stem}-samples.jsonl"


def collect_host_preflight(
    base: operator.Experiment, process_count: int, attempt: int
) -> None:
    assert base.measurement is not None
    foundation = operator.load_foundation_module()
    paths = foundation.resolve_workspace(ROOT, RECIPE_ID)
    python = operator.resolve_foundation_python(paths, platform.system())
    output, samples = host_preflight_paths(process_count, attempt)
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
    base: operator.Experiment, process_count: int, attempt: int
) -> Path:
    assert base.measurement is not None
    measurement = replace(
        base.measurement,
        configuration_id=configuration_id(base.drone_count, process_count),
        attempt=attempt,
    )
    condition = replace(
        base,
        drones_per_process=math.ceil(base.drone_count / process_count),
        process_count=process_count,
        measurement=measurement,
    )
    payload = operator.resolved_experiment_dict(condition)
    payload.pop("resolved", None)
    output = generated_experiment_path(process_count, attempt)
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
    rows: list[dict[str, Any]], process_counts: list[int]
) -> tuple[list[dict[str, Any]], int | None, str]:
    summaries: list[dict[str, Any]] = []
    for process_count in process_counts:
        recorded = [row for row in rows if row["process_count"] == process_count]
        successful = [
            row
            for row in recorded
            if row.get("status") == "success"
            and row.get("validation_passed") is True
            and isinstance(row.get("average_step_wall_clock_sec"), (int, float))
        ]
        values = [float(row["average_step_wall_clock_sec"]) for row in successful]
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


def initial_escalation_targets(
    base: operator.Experiment, process_counts: list[int], attempts: int
) -> tuple[list[int], list[str]]:
    rows: list[dict[str, Any]] = []
    missing: list[str] = []
    for process_count in process_counts:
        for attempt in range(1, attempts + 1):
            path = result_path(base, process_count, attempt)
            if not path.is_file():
                missing.append(str(path))
                continue
            payload = common.load_result(path)
            rows.append(summary_row(payload, path, base.drone_count, process_count, attempt))
    if missing:
        return [], missing
    aggregates, _selected, _status = aggregate(rows, process_counts)
    return [
        int(row["process_count"])
        for row in aggregates
        if row["escalation_required"]
    ], []


def summary_attempt_limits(
    base: operator.Experiment, process_counts: list[int], attempts: int
) -> dict[int, int]:
    limits = {process_count: attempts for process_count in process_counts}
    targets, missing = initial_escalation_targets(base, process_counts, attempts)
    if missing:
        return limits
    additional_started = any(
        result_path(base, process_count, attempt).is_file()
        for process_count in targets
        for attempt in range(attempts + 1, ESCALATED_MEASURED_RUN_COUNT + 1)
    )
    if additional_started:
        for process_count in targets:
            limits[process_count] = ESCALATED_MEASURED_RUN_COUNT
    return limits


def summarize(base: operator.Experiment, process_counts: list[int], attempts: int) -> int:
    rows: list[dict[str, Any]] = []
    missing: list[str] = []
    attempt_limits = summary_attempt_limits(base, process_counts, attempts)
    for process_count in process_counts:
        for attempt in range(1, attempt_limits[process_count] + 1):
            path = result_path(base, process_count, attempt)
            if not path.is_file():
                missing.append(str(path))
                continue
            payload = common.load_result(path)
            rows.append(summary_row(payload, path, base.drone_count, process_count, attempt))
    aggregates, selected, selection_status = aggregate(rows, process_counts)
    json_path, csv_path, aggregate_path = summary_paths(base)
    json_path.parent.mkdir(parents=True, exist_ok=True)
    report = {
        "experiment": "multi-process-scaling",
        "fixed_drone_count": base.drone_count,
        "expected_result_count": sum(attempt_limits.values()),
        "recorded_result_count": len(rows),
        "complete": not missing,
        "missing_results": missing,
        "selection_threshold": SELECTION_THRESHOLD,
        "selection_status": selection_status,
        "selected_process_count": selected,
        "expected_attempts_by_process": {
            str(process_count): attempt_limits[process_count]
            for process_count in process_counts
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
    print(f"Selection       : {selection_status} ({selected})")
    return 0 if not missing else 1


def plan(base: operator.Experiment, process_counts: list[int], attempts: int) -> int:
    print("Experiment B: multi-process single-host scaling")
    print(f"Fixed workload: {base.drone_count} UAVs")
    print(f"Conditions    : {len(process_counts)} process counts x {attempts} attempt(s)")
    for process_count in process_counts:
        partitions = operator.expected_partition_counts(base.drone_count, process_count)
        for attempt in range(1, attempts + 1):
            path = result_path(base, process_count, attempt)
            state = "RECORDED" if path.is_file() else "PENDING"
            print(
                f"  [{state}] process={process_count:2d} partitions={partitions} "
                f"attempt={attempt:02d} result={path}"
            )
    targets, missing = initial_escalation_targets(base, process_counts, attempts)
    if not missing:
        if targets:
            print(
                "Additional attempts: "
                + ", ".join(
                    f"process={process_count} attempts=04,05"
                    for process_count in targets
                )
            )
            print("Next              : run 'extend' after confirming a clean host")
        else:
            print("Additional attempts: none")
    return 0


def run_matrix(
    base: operator.Experiment,
    process_counts: list[int],
    attempts: int,
    *,
    resume: bool,
    rerun_invalid: bool,
    restart_series: bool,
    attempt_plan: dict[int, list[int]] | None = None,
) -> int:
    assert base.measurement is not None
    if restart_series:
        archive_active_series(base)
    planned = attempt_plan or {
        process_count: list(range(1, attempts + 1))
        for process_count in process_counts
    }
    existing = [
        result_path(base, process_count, attempt)
        for process_count, attempt_numbers in planned.items()
        for attempt in attempt_numbers
        if result_path(base, process_count, attempt).is_file()
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
    for process_count, attempt_numbers in planned.items():
        for attempt in attempt_numbers:
            result = result_path(base, process_count, attempt)
            if result.is_file():
                print(f"[SKIP] recorded result: {result}")
                continue
            before_pids = require_clean_process_state()
            generated = materialize_experiment(base, process_count, attempt)
            print(
                f"\n=== Experiment B: UAV={base.drone_count} "
                f"process={process_count} attempt={attempt:02d} ===",
                flush=True,
            )
            start_attempted = False
            try:
                for command in ("configure", "doctor"):
                    if run_operator(command, generated) != 0:
                        raise MatrixError(
                            f"{command} failed for process={process_count}, attempt={attempt}"
                        )
                collect_host_preflight(base, process_count, attempt)
                start_attempted = True
                if run_operator("start", generated) != 0:
                    raise MatrixError(
                        f"start failed for process={process_count}, attempt={attempt}"
                    )
                timeout = base.measurement.maximum_wall_time_sec + 30.0
                smoke_rc = run_operator("smoke", generated, "--timeout-sec", str(timeout))
            finally:
                if start_attempted:
                    stop_rc = run_operator("stop", generated)
                    if stop_rc != 0:
                        print(
                            f"[WARN] stop failed for process={process_count}, attempt={attempt}",
                            file=sys.stderr,
                        )
                    cleanup_spawned_drone_services(before_pids)
            if not result.is_file():
                raise MatrixError(f"measurement result is missing after smoke: {result}")
            payload = common.load_result(result)
            validate_identity(payload, base.drone_count, process_count, attempt)
            if not common.preflight_passed(payload):
                summarize(base, process_counts, attempts)
                raise MatrixError(
                    "machine preflight failed; stop the series and restore a clean "
                    f"measurement environment: {result}"
                )
            if smoke_rc != 0 or payload.get("status") != "success":
                failed = True
                print(f"[FAIL] condition recorded; continuing: {result}", file=sys.stderr)
            else:
                print(f"[PASS] {result}")
    summary_rc = summarize(base, process_counts, attempts)
    return 1 if failed or summary_rc != 0 else 0


def extend_matrix(
    base: operator.Experiment,
    process_counts: list[int],
    attempts: int,
    *,
    rerun_invalid: bool,
) -> int:
    if attempts != INITIAL_MEASURED_RUN_COUNT:
        raise MatrixError(
            "extend requires matrix.attempts="
            f"{INITIAL_MEASURED_RUN_COUNT}; configured={attempts}"
        )
    targets, missing = initial_escalation_targets(base, process_counts, attempts)
    if missing:
        raise MatrixError(
            "initial three-attempt series is incomplete; run with --resume first: "
            + ", ".join(missing)
        )
    initial_paths = [
        result_path(base, process_count, attempt)
        for process_count in process_counts
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
        return summarize(base, process_counts, attempts)
    print(
        "Experiment B additional attempts: "
        + ", ".join(f"process={process_count} attempts=04,05" for process_count in targets)
    )
    return run_matrix(
        base,
        process_counts,
        attempts,
        resume=True,
        rerun_invalid=rerun_invalid,
        restart_series=False,
        attempt_plan={
            process_count: list(
                range(attempts + 1, ESCALATED_MEASURED_RUN_COUNT + 1)
            )
            for process_count in targets
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
        base, process_counts, attempts = load_matrix(args.experiment.resolve())
        if args.command == "plan":
            return plan(base, process_counts, attempts)
        if args.command == "summarize":
            return summarize(base, process_counts, attempts)
        if args.command == "extend":
            return extend_matrix(
                base,
                process_counts,
                attempts,
                rerun_invalid=args.rerun_invalid,
            )
        if args.command in {"status", "stop"}:
            return run_operator(args.command, args.experiment.resolve())
        return run_matrix(
            base,
            process_counts,
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
