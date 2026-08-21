#!/usr/bin/env python3
"""Run dedicated single-host Temporal Validation for Experiment B endpoints."""

from __future__ import annotations

import argparse
import csv
import json
import math
import platform
import subprocess
import sys
from dataclasses import replace
from pathlib import Path
from typing import Any

import drone_fleet_performance_a as common
import drone_fleet_performance_b as performance_b
import drone_fleet_single_host as operator


ROOT = Path(__file__).resolve().parents[2]
RECIPE_ID = performance_b.RECIPE_ID
OPERATOR = Path(__file__).with_name("drone_fleet_multi_process.py")
DEFAULT_EXPERIMENT = (
    ROOT
    / "recipes"
    / "experiments"
    / "drone-fleet-performance"
    / "single-host-temporal-validation.yaml"
)
ATTEMPT = 1
SUMMARY_FIELDS = (
    "process_count",
    "status",
    "lag_median_usec",
    "lag_p95_usec",
    "lag_max_usec",
    "accepted_sample_count",
    "rejected_sample_count",
    "acceptance_ratio",
    "temporal_sampling_interval_usec",
    "result_path",
)


class TemporalError(RuntimeError):
    pass


def workspace_root() -> Path:
    return ROOT / "work" / "recipes" / RECIPE_ID


def load_experiment(path: Path) -> operator.Experiment:
    base = operator.resolve_experiment(path)
    measurement = base.measurement
    if base.drone_count != 128:
        raise TemporalError("single-host Temporal Validation requires 128 UAVs")
    if measurement is None or measurement.mode != "temporal":
        raise TemporalError("Temporal Validation requires measurement.mode=temporal")
    if measurement.temporal_sampling_interval_usec is None:
        raise TemporalError("Temporal Validation requires a sampling interval")
    if measurement.conductor_implementation != "embedded":
        raise TemporalError("single-host Temporal Validation requires embedded Conductor")
    return base


def load_process_counts(path: Path) -> list[int]:
    raw = operator.load_simple_yaml(path)
    matrix = raw.get("matrix")
    if not isinstance(matrix, dict):
        raise TemporalError("single-host Temporal Validation requires matrix")
    unknown = sorted(set(matrix) - {"process_count", "attempts"})
    if unknown:
        raise TemporalError(f"unknown matrix fields: {', '.join(unknown)}")
    values = matrix.get("process_count")
    if not isinstance(values, list) or not values:
        raise TemporalError("matrix.process_count must be a non-empty list")
    if any(
        isinstance(value, bool) or not isinstance(value, int) or value < 1
        for value in values
    ):
        raise TemporalError("matrix.process_count values must be integers >= 1")
    if values != sorted(set(values)):
        raise TemporalError("matrix.process_count must be sorted without duplicates")
    if matrix.get("attempts") != ATTEMPT:
        raise TemporalError(f"matrix.attempts must be {ATTEMPT}")
    return values


def configuration_id(process_count: int) -> str:
    return f"temporal-uav-128-proc-{process_count:02d}"


def result_path(base: operator.Experiment, process_count: int) -> Path:
    assert base.measurement is not None
    return (
        workspace_root()
        / base.results_directory
        / base.measurement.series
        / configuration_id(process_count)
        / f"attempt-{ATTEMPT:02d}"
        / "result.json"
    )


def generated_experiment_path(process_count: int) -> Path:
    return (
        workspace_root()
        / "matrix"
        / "temporal-b"
        / configuration_id(process_count)
        / f"attempt-{ATTEMPT:02d}.yaml"
    )


def materialize_experiment(base: operator.Experiment, process_count: int) -> Path:
    assert base.measurement is not None
    measurement = replace(
        base.measurement,
        configuration_id=configuration_id(process_count),
        attempt=ATTEMPT,
    )
    condition = replace(
        base,
        drones_per_process=math.ceil(base.drone_count / process_count),
        process_count=process_count,
        measurement=measurement,
    )
    payload = operator.resolved_experiment_dict(condition)
    payload.pop("resolved", None)
    output = generated_experiment_path(process_count)
    operator.write_simple_yaml(output, payload)
    operator.resolve_experiment(output)
    return output


def host_preflight_paths(process_count: int) -> tuple[Path, Path]:
    directory = workspace_root() / "runtime" / "host-preflight-temporal"
    stem = f"{configuration_id(process_count)}-attempt-{ATTEMPT:02d}"
    return directory / f"{stem}.json", directory / f"{stem}-samples.jsonl"


def collect_host_preflight(base: operator.Experiment, process_count: int) -> None:
    assert base.measurement is not None
    foundation = operator.load_foundation_module()
    paths = foundation.resolve_workspace(ROOT, RECIPE_ID)
    python = operator.resolve_foundation_python(paths, platform.system())
    output, samples = host_preflight_paths(process_count)
    command = [
        str(python),
        str(Path(__file__).with_name("assets") / "collect_machine_preflight.py"),
        "--duration-sec", str(base.measurement.preflight_duration_sec),
        "--sampling-interval-sec", str(base.measurement.sampling_interval_sec),
        "--settle-timeout-sec", str(base.measurement.preflight_settle_timeout_sec),
        "--cpu-limit-percent", str(base.measurement.preflight_max_cpu_average_percent),
        "--memory-limit-percent", str(base.measurement.preflight_max_memory_used_percent),
        "--output", str(output),
        "--samples", str(samples),
    ]
    print("+ " + " ".join(command), flush=True)
    if subprocess.run(command, cwd=ROOT, check=False).returncode != 0:
        raise TemporalError(f"host preflight failed: {output}")
    config = workspace_root() / "config" / "measurement.json"
    payload = json.loads(config.read_text(encoding="utf-8"))
    payload["host_preflight_result_path"] = str(output.resolve())
    config.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


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


def validate_result(payload: dict[str, Any], process_count: int) -> None:
    metadata = payload.get("metadata")
    temporal = payload.get("temporal")
    mismatches = []
    if payload.get("run_id") != f"{configuration_id(process_count)}-attempt-01":
        mismatches.append("run_id")
    if payload.get("mode") != "temporal":
        mismatches.append("mode")
    if payload.get("status") != "success":
        mismatches.append("status")
    if not isinstance(metadata, dict) or metadata.get("process_count") != process_count:
        mismatches.append("process_count")
    if not isinstance(metadata, dict) or metadata.get("temporal_observer_enabled") is not True:
        mismatches.append("temporal_observer_enabled")
    if not isinstance(temporal, dict) or temporal.get("accepted_sample_count", 0) < 1:
        mismatches.append("accepted_sample_count")
    if mismatches:
        raise TemporalError(
            f"invalid temporal result for process={process_count}: "
            + ", ".join(mismatches)
        )


def summary_paths(base: operator.Experiment) -> tuple[Path, Path]:
    assert base.measurement is not None
    root = (
        workspace_root()
        / base.results_directory
        / base.measurement.series
        / "summary"
    )
    return root / "temporal-b.json", root / "temporal-b.csv"


def summarize(base: operator.Experiment, process_counts: list[int]) -> int:
    rows = []
    missing = []
    for process_count in process_counts:
        path = result_path(base, process_count)
        if not path.is_file():
            missing.append(str(path))
            continue
        payload = common.load_result(path)
        validate_result(payload, process_count)
        temporal = payload["temporal"]
        rows.append(
            {
                "process_count": process_count,
                "status": payload["status"],
                "lag_median_usec": temporal["lag_median_usec"],
                "lag_p95_usec": temporal["lag_p95_usec"],
                "lag_max_usec": temporal["lag_max_usec"],
                "accepted_sample_count": temporal["accepted_sample_count"],
                "rejected_sample_count": temporal["rejected_sample_count"],
                "acceptance_ratio": temporal["acceptance_ratio"],
                "temporal_sampling_interval_usec": payload["metadata"][
                    "temporal_sampling_interval_usec"
                ],
                "result_path": str(path),
            }
        )
    json_path, csv_path = summary_paths(base)
    json_path.parent.mkdir(parents=True, exist_ok=True)
    report = {
        "validation": "single-host-temporal-b",
        "fixed_drone_count": 128,
        "process_counts": process_counts,
        "complete": not missing,
        "missing_results": missing,
        "results": rows,
    }
    json_path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    with csv_path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=SUMMARY_FIELDS)
        writer.writeheader()
        writer.writerows(rows)
    print(f"Temporal summary JSON: {json_path}")
    print(f"Temporal summary CSV : {csv_path}")
    print(f"Recorded             : {len(rows)}/{len(process_counts)}")
    return 0 if not missing else 1


def plan(base: operator.Experiment, process_counts: list[int]) -> int:
    print("Temporal Validation B-max: 128 UAV single-host maximum process count")
    assert base.measurement is not None
    print(
        "Observer interval    : "
        f"{base.measurement.temporal_sampling_interval_usec} usec virtual time"
    )
    for process_count in process_counts:
        path = result_path(base, process_count)
        print(
            f"  [{'RECORDED' if path.is_file() else 'PENDING'}] "
            f"process={process_count:2d} attempt=01 result={path}"
        )
    return 0


def run(base: operator.Experiment, process_counts: list[int], *, resume: bool) -> int:
    existing = [
        result_path(base, process_count)
        for process_count in process_counts
        if result_path(base, process_count).is_file()
    ]
    if existing and not resume:
        raise TemporalError(
            "temporal results already exist; use --resume to skip them: "
            + ", ".join(map(str, existing))
        )
    for process_count in process_counts:
        result = result_path(base, process_count)
        if result.is_file():
            print(f"[SKIP] {result}")
            continue
        before_pids = performance_b.require_clean_process_state()
        generated = materialize_experiment(base, process_count)
        start_attempted = False
        print(f"\n=== Temporal B: UAV=128 process={process_count} attempt=01 ===")
        try:
            for command in ("configure", "doctor"):
                if run_operator(command, generated) != 0:
                    raise TemporalError(f"{command} failed for process={process_count}")
            collect_host_preflight(base, process_count)
            start_attempted = True
            if run_operator("start", generated) != 0:
                raise TemporalError(f"start failed for process={process_count}")
            assert base.measurement is not None
            timeout = base.measurement.maximum_wall_time_sec + 30.0
            if run_operator("smoke", generated, "--timeout-sec", str(timeout)) != 0:
                raise TemporalError(f"smoke failed for process={process_count}")
        finally:
            if start_attempted:
                run_operator("stop", generated)
                performance_b.cleanup_spawned_drone_services(before_pids)
        if not result.is_file():
            raise TemporalError(f"temporal result is missing: {result}")
        validate_result(common.load_result(result), process_count)
        print(f"[PASS] {result}")
    return summarize(base, process_counts)


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description="Experiment B Temporal Validation")
    result.add_argument("command", choices=["plan", "run", "summarize", "status", "stop"])
    result.add_argument("--experiment", type=Path, default=DEFAULT_EXPERIMENT)
    result.add_argument("--resume", action="store_true")
    return result


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        base = load_experiment(args.experiment.resolve())
        process_counts = load_process_counts(args.experiment.resolve())
        if args.command == "plan":
            return plan(base, process_counts)
        if args.command == "summarize":
            return summarize(base, process_counts)
        if args.command in {"status", "stop"}:
            return run_operator(args.command, args.experiment.resolve())
        return run(base, process_counts, resume=args.resume)
    except (TemporalError, common.MatrixError, performance_b.MatrixError, operator.RecipeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("interrupted", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
