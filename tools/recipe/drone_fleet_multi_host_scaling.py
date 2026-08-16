#!/usr/bin/env python3
"""Operate the attempt-1 ICRA multi-host scaling preflight."""

from __future__ import annotations

import argparse
import copy
import csv
import json
import statistics
from pathlib import Path
from typing import Any

try:
    from tools.recipe import drone_fleet_multi_host as multi_host
    from tools.recipe import drone_fleet_single_host as yaml_support
except ModuleNotFoundError:
    import drone_fleet_multi_host as multi_host
    import drone_fleet_single_host as yaml_support


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_EXPERIMENT = (
    ROOT
    / "recipes"
    / "experiments"
    / "drone-fleet-performance"
    / "multi-host-scaling.yaml"
)
WORK_ROOT = ROOT / "work" / "recipes" / multi_host.RECIPE_ID
SUMMARY_FIELDS = (
    "configuration_id",
    "drone_count",
    "real_sleep_msec",
    "attempt",
    "paired",
    "status",
    "rtf",
    "srv-01_rtf",
    "cli-01_rtf",
    "srv-01_cpu_average_percent",
    "cli-01_cpu_average_percent",
    "srv-01_cpu_max_percent",
    "cli-01_cpu_max_percent",
    "config_hash",
)


class ScalingError(RuntimeError):
    pass


def _positive(value: Any, label: str, *, allow_zero: bool = False) -> int:
    minimum = 0 if allow_zero else 1
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise ScalingError(f"{label} must be an integer >= {minimum}")
    return value


def load_scaling(path: Path) -> tuple[dict[str, Any], list[int], int]:
    raw = yaml_support.load_simple_yaml(path)
    matrix = raw.get("matrix")
    if not isinstance(matrix, dict):
        raise ScalingError("matrix must be a mapping")
    unknown = sorted(set(matrix) - {"drone_count", "attempts"})
    if unknown:
        raise ScalingError("unknown matrix fields: " + ", ".join(unknown))
    counts = matrix.get("drone_count")
    if not isinstance(counts, list) or not counts:
        raise ScalingError("matrix.drone_count must be a non-empty list")
    drone_counts = [_positive(value, "matrix.drone_count[]") for value in counts]
    if drone_counts != sorted(drone_counts) or len(set(drone_counts)) != len(
        drone_counts
    ):
        raise ScalingError("matrix.drone_count must be unique and ascending")
    attempts = _positive(matrix.get("attempts"), "matrix.attempts")
    conductor = raw.get("runtime", {}).get("conductor", {})
    if conductor.get("profile") != "icra-target-delta-boundary":
        raise ScalingError(
            "runtime.conductor.profile must be icra-target-delta-boundary"
        )
    _positive(
        conductor.get("real_sleep_msec"),
        "runtime.conductor.real_sleep_msec",
        allow_zero=True,
    )
    results = raw.get("results")
    if not isinstance(results, dict) or results.get("enabled") is not True:
        raise ScalingError("results.enabled must be true")
    results_directory = results.get("directory")
    if not isinstance(results_directory, str) or not results_directory:
        raise ScalingError("results.directory must be a non-empty relative path")
    results_path = Path(results_directory)
    if results_path.is_absolute() or ".." in results_path.parts:
        raise ScalingError("results.directory must stay inside the Recipe workspace")
    measurement = raw.get("measurement")
    if not isinstance(measurement, dict) or measurement.get("enabled") is not True:
        raise ScalingError("measurement.enabled must be true")
    if measurement.get("configuration_id") != "auto":
        raise ScalingError("measurement.configuration_id must be auto")
    if measurement.get("attempt") != 1:
        raise ScalingError("measurement.attempt must be the template value 1")
    return raw, drone_counts, attempts


def configuration_id(drone_count: int, real_sleep_msec: int) -> str:
    return f"uav-{drone_count:03d}-sleep-{real_sleep_msec:03d}ms"


def resolve_condition(raw: dict[str, Any], drone_count: int, attempt: int = 1) -> dict[str, Any]:
    result = copy.deepcopy(raw)
    matrix = result.pop("matrix")
    allowed = matrix["drone_count"]
    if drone_count not in allowed:
        raise ScalingError(
            f"drone_count {drone_count} is outside matrix: "
            + ", ".join(map(str, allowed))
        )
    deployment = result["deployment"]
    allocation = deployment.get("allocation")
    if not isinstance(allocation, dict) or allocation.get("mode") != "equal":
        raise ScalingError("deployment.allocation.mode must be equal")
    order = allocation.get("host_order")
    hosts = deployment["hosts"]
    if not isinstance(order, list) or set(order) != set(hosts):
        raise ScalingError(
            "deployment.allocation.host_order must contain every host exactly once"
        )
    quotient, remainder = divmod(drone_count, len(order))
    start = 0
    for index, host_id in enumerate(order):
        count = quotient + (1 if index < remainder else 0)
        processes = _positive(
            hosts[host_id].get("process_count"),
            f"deployment.hosts.{host_id}.process_count",
        )
        if count < processes:
            raise ScalingError(
                f"{drone_count} UAVs allocate only {count} to {host_id}, "
                f"fewer than its {processes} processes"
            )
        hosts[host_id]["drone_count"] = count
        hosts[host_id]["global_start_index"] = start
        start += count
    result["scale"]["drone_count"] = drone_count
    result["scale"]["process_count"] = sum(
        int(host["process_count"]) for host in hosts.values()
    )
    sleep = int(result["runtime"]["conductor"]["real_sleep_msec"])
    measurement = result["measurement"]
    measurement["configuration_id"] = configuration_id(drone_count, sleep)
    attempts = _positive(matrix.get("attempts"), "matrix.attempts")
    if attempt < 1 or attempt > attempts:
        raise ScalingError(f"attempt {attempt} is outside 1..{attempts}")
    measurement["attempt"] = attempt
    return result


def generated_experiment_path(
    output_root: Path, drone_count: int, real_sleep_msec: int, attempt: int = 1
) -> Path:
    return (
        output_root
        / "matrix"
        / "multi-host-scaling"
        / configuration_id(drone_count, real_sleep_msec)
        / f"attempt-{attempt:02d}.yaml"
    )


def delegate_arguments(args: argparse.Namespace, command: str) -> list[str]:
    result = [
        "--experiment",
        str(args.generated_experiment),
        "--output-root",
        str(args.output_root),
        "--drone-root",
        str(args.drone_root),
        "--viewer-root",
        str(args.viewer_root),
    ]
    if args.conductor_root is not None:
        result.extend(["--conductor-root", str(args.conductor_root)])
    if args.conductor_schema is not None:
        result.extend(["--conductor-schema", str(args.conductor_schema)])
    result.append(command)
    return result


def configure(args: argparse.Namespace) -> int:
    raw, counts, _attempts = load_scaling(args.experiment.resolve())
    if args.drone_count not in counts:
        raise ScalingError(
            f"--drone-count must be one of: {', '.join(map(str, counts))}"
        )
    condition = resolve_condition(raw, args.drone_count, args.attempt)
    sleep = int(condition["runtime"]["conductor"]["real_sleep_msec"])
    generated = generated_experiment_path(args.output_root, args.drone_count, sleep, args.attempt)
    yaml_support.write_simple_yaml(generated, condition)
    args.generated_experiment = generated
    delegated = delegate_arguments(args, "configure")
    delegated.extend(["--host", args.host])
    return multi_host.main(delegated)


def plan(path: Path) -> int:
    raw, counts, attempts = load_scaling(path)
    sleep = int(raw["runtime"]["conductor"]["real_sleep_msec"])
    hosts = raw["deployment"]["hosts"]
    print("Experiment C: multi-host scaling preflight")
    print(f"real_sleep_msec: {sleep} (scalar)")
    print(f"attempts: {attempts}")
    for count in counts:
        resolved = resolve_condition(raw, count)
        placement = ", ".join(
            f"{host_id}={host['drone_count']} UAV/{host['process_count']} proc"
            for host_id, host in resolved["deployment"]["hosts"].items()
        )
        print(f"- {configuration_id(count, sleep)}: {placement}")
    print(
        "Process policy: "
        + ", ".join(
            f"{host_id}={host['process_count']}" for host_id, host in hosts.items()
        )
    )
    return 0


def result_path(
    output_root: Path,
    results_directory: str,
    series: str,
    host_id: str,
    configuration: str,
    attempt: int = 1,
) -> Path:
    return (
        output_root
        / results_directory
        / series
        / "hosts"
        / host_id
        / configuration
        / f"attempt-{attempt:02d}"
        / "result.json"
    )


def _number(mapping: Any, key: str) -> float | None:
    if not isinstance(mapping, dict):
        return None
    value = mapping.get(key)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def validate_result_identity(
    payload: dict[str, Any],
    *,
    path: Path,
    host_id: str,
    configuration_id: str,
    attempt: int,
    real_sleep_msec: int,
) -> None:
    metadata = payload.get("metadata")
    if not isinstance(metadata, dict):
        raise ScalingError(f"result metadata is missing: {path}")
    expected_run_id = f"{configuration_id}-attempt-{attempt:02d}"
    expected = {
        "run_id": (payload.get("run_id"), expected_run_id),
        "host_id": (metadata.get("host_id"), host_id),
        "configuration_id": (
            metadata.get("configuration_id"),
            configuration_id,
        ),
        "attempt": (metadata.get("attempt"), attempt),
    }
    mismatches = [
        f"{field}={actual!r} (expected {wanted!r})"
        for field, (actual, wanted) in expected.items()
        if actual != wanted
    ]
    coordination = metadata.get("time_coordination")
    actual_sleep = (
        coordination.get("conductor_real_sleep_msec")
        if isinstance(coordination, dict)
        else None
    )
    if actual_sleep != real_sleep_msec:
        mismatches.append(
            "time_coordination.conductor_real_sleep_msec="
            f"{actual_sleep!r} (expected {real_sleep_msec!r})"
        )
    if mismatches:
        raise ScalingError(f"result identity mismatch in {path}: " + "; ".join(mismatches))


def _statistics(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    configurations = sorted({str(row["configuration_id"]) for row in rows})
    metrics = (
        "rtf", "srv-01_rtf", "cli-01_rtf",
        "srv-01_cpu_average_percent", "cli-01_cpu_average_percent",
        "srv-01_cpu_max_percent", "cli-01_cpu_max_percent",
    )
    for configuration in configurations:
        selected = [row for row in rows if row["configuration_id"] == configuration]
        entry: dict[str, Any] = {
            "configuration_id": configuration,
            "attempt_count": len(selected),
            "success_count": sum(row["status"] == "success" for row in selected),
            "attempts": [row["attempt"] for row in selected],
        }
        for metric in metrics:
            values = [float(row[metric]) for row in selected if row.get(metric) is not None]
            entry[metric] = ({"count": len(values), "mean": statistics.fmean(values),
                "pstdev": statistics.pstdev(values), "min": min(values), "max": max(values),
                "values": values} if values else None)
        result.append(entry)
    return result


def summarize(
    path: Path, output_root: Path, selected_drone_count: int | None = None
) -> int:
    raw, counts, attempts = load_scaling(path)
    if selected_drone_count is not None:
        if selected_drone_count not in counts:
            raise ScalingError(
                f"--drone-count must be one of: {', '.join(map(str, counts))}"
            )
        counts = [selected_drone_count]
    measurement = raw["measurement"]
    series = str(measurement["series"])
    results_directory = str(raw["results"]["directory"])
    sleep = int(raw["runtime"]["conductor"]["real_sleep_msec"])
    host_ids = list(raw["deployment"]["allocation"]["host_order"])
    server_host = str(raw["deployment"]["server_host"])
    rows: list[dict[str, Any]] = []
    missing: list[str] = []
    for count in counts:
        config_id = configuration_id(count, sleep)
        payloads: dict[str, dict[str, Any]] = {}
        for attempt in range(1, attempts + 1):
            payloads: dict[str, dict[str, Any]] = {}
            for host_id in host_ids:
                result = result_path(output_root, results_directory, series,
                    host_id, config_id, attempt)
                if not result.is_file():
                    missing.append(str(result)); continue
                try: payload = json.loads(result.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError) as exc:
                    raise ScalingError(f"cannot read result {result}: {exc}") from exc
                if not isinstance(payload, dict):
                    raise ScalingError(f"result must be a JSON object: {result}")
                validate_result_identity(payload, path=result, host_id=host_id,
                    configuration_id=config_id, attempt=attempt,
                    real_sleep_msec=sleep)
                payloads[host_id] = payload
            paired = set(payloads) == set(host_ids)
            hashes = {payload.get("metadata", {}).get("config_hash") for payload in payloads.values()}
            if paired and (None in hashes or len(hashes) != 1):
                raise ScalingError(f"config hash mismatch for {config_id} attempt {attempt}: {hashes}")
            statuses = {payload.get("status") for payload in payloads.values()}
            host_rtf = {host_id: _number(payload.get("performance"), "rtf") for host_id,payload in payloads.items()}
            host_cpu_average = {host_id: _number(payload.get("machine"), "cpu_average_percent") for host_id,payload in payloads.items()}
            host_cpu_max = {host_id: _number(payload.get("machine"), "cpu_max_percent") for host_id,payload in payloads.items()}
            rows.append({"configuration_id":config_id,"drone_count":count,
                "real_sleep_msec":sleep,"attempt":attempt,"paired":paired,
                "status":"success" if paired and statuses=={"success"} else "incomplete",
                "rtf":host_rtf.get(server_host),"srv-01_rtf":host_rtf.get("srv-01"),
                "cli-01_rtf":host_rtf.get("cli-01"),
                "srv-01_cpu_average_percent":host_cpu_average.get("srv-01"),
                "cli-01_cpu_average_percent":host_cpu_average.get("cli-01"),
                "srv-01_cpu_max_percent":host_cpu_max.get("srv-01"),
                "cli-01_cpu_max_percent":host_cpu_max.get("cli-01"),
                "config_hash":next(iter(hashes)) if len(hashes)==1 else None})
    summary_root = output_root / results_directory / series / "summary"
    summary_root.mkdir(parents=True, exist_ok=True)
    summary_stem = f"multi-host-scaling-sleep-{sleep:03d}ms"
    if selected_drone_count is not None:
        summary_stem += f"-uav-{selected_drone_count:03d}"
    json_path = summary_root / f"{summary_stem}.json"
    csv_path = summary_root / f"{summary_stem}.csv"
    multi_host.atomic_json(
        json_path,
        {
            "experiment": "multi-host-scaling",
            "protocol_status": measurement["protocol_status"],
            "real_sleep_msec": sleep,
            "attempts": attempts,
            "complete": not missing,
            "missing_results": missing,
            "results": rows,
            "statistics": _statistics(rows),
        },
    )
    with csv_path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=SUMMARY_FIELDS)
        writer.writeheader()
        writer.writerows(rows)
    print(f"Summary JSON: {json_path}")
    print(f"Summary CSV : {csv_path}")
    paired_count = sum(1 for row in rows if row["paired"])
    print(f"Paired      : {paired_count}/{len(rows)}")
    return 0 if not missing else 1


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("--experiment", type=Path, default=DEFAULT_EXPERIMENT)
    result.add_argument("--conductor-root", type=Path)
    result.add_argument("--conductor-schema", type=Path)
    result.add_argument("--drone-root", type=Path, default=multi_host.DEFAULT_DRONE_ROOT)
    result.add_argument("--viewer-root", type=Path, default=multi_host.DEFAULT_VIEWER_ROOT)
    result.add_argument("--output-root", type=Path, default=WORK_ROOT)
    commands = result.add_subparsers(dest="command", required=True)
    commands.add_parser("plan")
    configure_parser = commands.add_parser("configure")
    configure_parser.add_argument("--host", required=True)
    configure_parser.add_argument("--drone-count", type=int, required=True)
    configure_parser.add_argument("--attempt", type=int, default=1)
    for command in ("doctor", "start", "run", "status", "stop", "clean", "collect"):
        commands.add_parser(command)
    summarize_parser = commands.add_parser("summarize")
    summarize_parser.add_argument("--drone-count", type=int)
    return result


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    args.experiment = args.experiment.resolve()
    args.output_root = args.output_root.resolve()
    args.drone_root = args.drone_root.resolve()
    args.viewer_root = args.viewer_root.resolve()
    if args.conductor_root is not None:
        args.conductor_root = args.conductor_root.resolve()
    if args.conductor_schema is not None:
        args.conductor_schema = args.conductor_schema.resolve()
    try:
        if args.command == "plan":
            return plan(args.experiment)
        if args.command == "configure":
            return configure(args)
        if args.command == "summarize":
            return summarize(
                args.experiment, args.output_root, args.drone_count
            )
        configured = multi_host.load_local_selection(args.output_root)
        args.generated_experiment = generated_experiment_path(
            args.output_root,
            int(configured["resolved"]["scale"]["drone_count"]),
            int(configured["resolved"]["runtime"]["conductor"]["real_sleep_msec"]),
            int(configured["resolved"]["measurement"]["attempt"]),
        )
        return multi_host.main(delegate_arguments(args, args.command))
    except (ScalingError, multi_host.RecipeError) as exc:
        print(f"[ERROR] {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
