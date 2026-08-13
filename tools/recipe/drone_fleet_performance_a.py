#!/usr/bin/env python3
"""Run Experiment A: single-process scaling across a UAV-count matrix."""

from __future__ import annotations

import argparse
import csv
import json
import subprocess
import sys
from dataclasses import replace
from pathlib import Path
from typing import Any

import drone_fleet_single_host as operator


RECIPE_ID = "drone-fleet-single-process-scaling"
ROOT = Path(__file__).resolve().parents[2]
OPERATOR = Path(__file__).with_name("drone_fleet_performance.py")
DEFAULT_EXPERIMENT = (
    ROOT
    / "recipes"
    / "experiments"
    / "drone-fleet-performance"
    / "single-process-scaling.yaml"
)
SUMMARY_FIELDS = (
    "run_id",
    "drone_count",
    "process_count",
    "attempt",
    "status",
    "failure_type",
    "wall_clock_sec",
    "world_elapsed_usec",
    "step_count",
    "average_step_wall_clock_sec",
    "rtf",
    "preflight_cpu_average_percent",
    "preflight_memory_used_average_percent",
    "cpu_average_percent",
    "cpu_max_percent",
    "memory_used_average_percent",
    "memory_used_max_percent",
    "preflight_passed",
    "validation_passed",
    "protocol_status",
    "result_path",
)


class MatrixError(RuntimeError):
    pass


def _positive_int(value: Any, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise MatrixError(f"{label} must be an integer >= 1")
    return value


def load_matrix(path: Path) -> tuple[operator.Experiment, list[int], int]:
    raw = operator.load_simple_yaml(path)
    matrix = raw.get("matrix")
    if not isinstance(matrix, dict):
        raise MatrixError("matrix must be a mapping")
    unknown = sorted(set(matrix) - {"drone_count", "attempts"})
    if unknown:
        raise MatrixError(f"unknown matrix fields: {', '.join(unknown)}")
    counts = matrix.get("drone_count")
    if not isinstance(counts, list) or not counts:
        raise MatrixError("matrix.drone_count must be a non-empty inline list")
    normalized = [_positive_int(value, "matrix.drone_count[]") for value in counts]
    too_large = [
        value for value in normalized if value > operator.GENERAL_USER_MAX_DRONES
    ]
    if too_large:
        raise MatrixError(
            "matrix.drone_count exceeds the public binary limit of "
            f"{operator.GENERAL_USER_MAX_DRONES}: {too_large}"
        )
    if len(set(normalized)) != len(normalized):
        raise MatrixError("matrix.drone_count must not contain duplicates")
    if normalized != sorted(normalized):
        raise MatrixError("matrix.drone_count must be in ascending order")
    attempts = _positive_int(matrix.get("attempts"), "matrix.attempts")
    base = operator.resolve_experiment(path)
    if base.process_count != 1:
        raise MatrixError("Experiment A requires scale.process_count=1")
    if base.measurement is None:
        raise MatrixError("Experiment A requires measurement.enabled=true")
    return base, normalized, attempts


def workspace_root() -> Path:
    return ROOT / "work" / "recipes" / RECIPE_ID


def configuration_id(drone_count: int) -> str:
    return f"uav-{drone_count:03d}-proc-01"


def result_path(base: operator.Experiment, drone_count: int, attempt: int) -> Path:
    assert base.measurement is not None
    return (
        workspace_root()
        / base.results_directory
        / base.measurement.series
        / configuration_id(drone_count)
        / f"attempt-{attempt:02d}"
        / "result.json"
    )


def generated_experiment_path(drone_count: int, attempt: int) -> Path:
    return (
        workspace_root()
        / "matrix"
        / "experiment-a"
        / configuration_id(drone_count)
        / f"attempt-{attempt:02d}.yaml"
    )


def materialize_experiment(
    base: operator.Experiment, drone_count: int, attempt: int
) -> Path:
    assert base.measurement is not None
    measurement = replace(
        base.measurement,
        configuration_id=configuration_id(drone_count),
        attempt=attempt,
    )
    condition = replace(
        base,
        drone_count=drone_count,
        drones_per_process=drone_count,
        process_count=1,
        measurement=measurement,
    )
    payload = operator.resolved_experiment_dict(condition)
    payload.pop("resolved", None)
    output = generated_experiment_path(drone_count, attempt)
    operator.write_simple_yaml(output, payload)
    # Validate the generated contract before it can alter the Recipe workspace.
    operator.resolve_experiment(output)
    return output


def _operator_command(command: str, experiment: Path, *extra: str) -> list[str]:
    return [
        sys.executable,
        str(OPERATOR),
        command,
        "--experiment",
        str(experiment),
        *extra,
    ]


def run_operator(command: str, experiment: Path, *extra: str) -> int:
    invocation = _operator_command(command, experiment, *extra)
    print("+ " + " ".join(invocation), flush=True)
    return subprocess.run(invocation, cwd=ROOT, check=False).returncode


def load_result(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise MatrixError(f"cannot read measurement result: {path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise MatrixError(f"measurement result must be a JSON object: {path}")
    return payload


def preflight_passed(payload: dict[str, Any]) -> bool:
    metadata = payload.get("metadata")
    if not isinstance(metadata, dict):
        return False
    boundary = metadata.get("preflight_boundary")
    return isinstance(boundary, dict) and boundary.get("passed") is True


def _nested(payload: dict[str, Any], section: str, key: str) -> Any:
    value = payload.get(section)
    return value.get(key) if isinstance(value, dict) else None


def summary_row(
    payload: dict[str, Any], path: Path, drone_count: int, attempt: int
) -> dict[str, Any]:
    validation = payload.get("validation")
    return {
        "run_id": payload.get("run_id"),
        "drone_count": drone_count,
        "process_count": 1,
        "attempt": attempt,
        "status": payload.get("status"),
        "failure_type": payload.get("failure_type"),
        "wall_clock_sec": _nested(payload, "performance", "wall_clock_sec"),
        "world_elapsed_usec": _nested(payload, "performance", "world_elapsed_usec"),
        "step_count": _nested(payload, "performance", "step_count"),
        "average_step_wall_clock_sec": _nested(
            payload, "performance", "average_step_wall_clock_sec"
        ),
        "rtf": _nested(payload, "performance", "rtf"),
        "preflight_cpu_average_percent": _nested(
            payload, "machine_preflight", "cpu_average_percent"
        ),
        "preflight_memory_used_average_percent": _nested(
            payload, "machine_preflight", "memory_used_average_percent"
        ),
        "cpu_average_percent": _nested(payload, "machine", "cpu_average_percent"),
        "cpu_max_percent": _nested(payload, "machine", "cpu_max_percent"),
        "memory_used_average_percent": _nested(
            payload, "machine", "memory_used_average_percent"
        ),
        "memory_used_max_percent": _nested(
            payload, "machine", "memory_used_max_percent"
        ),
        "preflight_passed": preflight_passed(payload),
        "validation_passed": (
            validation.get("passed") if isinstance(validation, dict) else None
        ),
        "protocol_status": (
            payload.get("metadata", {}).get("protocol_status")
            if isinstance(payload.get("metadata"), dict)
            else None
        ),
        "result_path": str(path),
    }


def summary_paths(base: operator.Experiment) -> tuple[Path, Path]:
    assert base.measurement is not None
    directory = (
        workspace_root()
        / base.results_directory
        / base.measurement.series
        / "summary"
    )
    return directory / "experiment-a.json", directory / "experiment-a.csv"


def summarize(base: operator.Experiment, counts: list[int], attempts: int) -> int:
    rows: list[dict[str, Any]] = []
    missing: list[str] = []
    for drone_count in counts:
        for attempt in range(1, attempts + 1):
            path = result_path(base, drone_count, attempt)
            if not path.is_file():
                missing.append(str(path))
                continue
            rows.append(summary_row(load_result(path), path, drone_count, attempt))
    json_path, csv_path = summary_paths(base)
    json_path.parent.mkdir(parents=True, exist_ok=True)
    report = {
        "experiment": "single-process-scaling",
        "expected_result_count": len(counts) * attempts,
        "recorded_result_count": len(rows),
        "complete": not missing,
        "missing_results": missing,
        "results": rows,
    }
    json_path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    with csv_path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=SUMMARY_FIELDS)
        writer.writeheader()
        writer.writerows(rows)
    print(f"Summary JSON: {json_path}")
    print(f"Summary CSV : {csv_path}")
    print(f"Recorded    : {len(rows)}/{len(counts) * attempts}")
    return 0 if not missing else 1


def plan(base: operator.Experiment, counts: list[int], attempts: int) -> int:
    print("Experiment A: single-process scaling")
    print(f"Conditions : {len(counts)} UAV counts x {attempts} attempt(s)")
    for drone_count in counts:
        for attempt in range(1, attempts + 1):
            path = result_path(base, drone_count, attempt)
            state = "RECORDED" if path.is_file() else "PENDING"
            print(
                f"  [{state}] UAV={drone_count:3d} process=1 attempt={attempt:02d} "
                f"result={path}"
            )
    return 0


def run_matrix(
    base: operator.Experiment,
    counts: list[int],
    attempts: int,
    *,
    resume: bool,
) -> int:
    assert base.measurement is not None
    existing = [
        result_path(base, count, attempt)
        for count in counts
        for attempt in range(1, attempts + 1)
        if result_path(base, count, attempt).is_file()
    ]
    if existing and not resume:
        raise MatrixError(
            "recorded results already exist; use --resume to preserve and skip them: "
            + ", ".join(str(path) for path in existing)
        )

    failed = False
    for drone_count in counts:
        for attempt in range(1, attempts + 1):
            result = result_path(base, drone_count, attempt)
            if result.is_file():
                print(f"[SKIP] recorded result: {result}")
                continue
            generated = materialize_experiment(base, drone_count, attempt)
            print(
                f"\n=== Experiment A: UAV={drone_count} process=1 "
                f"attempt={attempt:02d} ===",
                flush=True,
            )
            start_attempted = False
            try:
                for command in ("configure", "doctor"):
                    if run_operator(command, generated) != 0:
                        raise MatrixError(
                            f"{command} failed for UAV={drone_count}, attempt={attempt}"
                        )
                start_attempted = True
                if run_operator("start", generated) != 0:
                    raise MatrixError(
                        f"start failed for UAV={drone_count}, attempt={attempt}"
                    )
                timeout = base.measurement.maximum_wall_time_sec + 30.0
                smoke_rc = run_operator(
                    "smoke", generated, "--timeout-sec", str(timeout)
                )
            finally:
                if start_attempted:
                    stop_rc = run_operator("stop", generated)
                    if stop_rc != 0:
                        print(
                            f"[WARN] stop failed for UAV={drone_count}, attempt={attempt}",
                            file=sys.stderr,
                        )
            if not result.is_file():
                raise MatrixError(
                    f"measurement result is missing after smoke: {result}"
                )
            payload = load_result(result)
            if not preflight_passed(payload):
                summarize(base, counts, attempts)
                raise MatrixError(
                    "machine preflight failed; stop the series and restore a clean "
                    f"measurement environment: {result}"
                )
            if smoke_rc != 0 or payload.get("status") != "success":
                failed = True
                print(
                    "[FAIL] condition result was recorded; continuing with the next "
                    f"condition: {result}",
                    file=sys.stderr,
                )
            else:
                print(f"[PASS] {result}")
    summary_rc = summarize(base, counts, attempts)
    return 1 if failed or summary_rc != 0 else 0


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(
        description="Experiment A single-process Drone Fleet matrix runner"
    )
    result.add_argument("command", choices=["plan", "run", "summarize"])
    result.add_argument("--experiment", type=Path, default=DEFAULT_EXPERIMENT)
    result.add_argument(
        "--resume",
        action="store_true",
        help="preserve and skip attempts that already contain result.json",
    )
    return result


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        base, counts, attempts = load_matrix(args.experiment.resolve())
        if args.command == "plan":
            return plan(base, counts, attempts)
        if args.command == "summarize":
            return summarize(base, counts, attempts)
        return run_matrix(base, counts, attempts, resume=args.resume)
    except (MatrixError, operator.RecipeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("interrupted", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
