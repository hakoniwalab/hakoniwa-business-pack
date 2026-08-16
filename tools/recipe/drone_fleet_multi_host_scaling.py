#!/usr/bin/env python3
"""Operate ICRA multi-host performance and Temporal Validation matrices."""

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
TEMPORAL_SUMMARY_FIELDS = (
    "configuration_id",
    "drone_count",
    "real_sleep_msec",
    "attempt",
    "paired",
    "status",
    "world_time_start_difference_usec",
    "world_time_end_difference_usec",
    "srv-01_lag_median_usec",
    "srv-01_lag_p95_usec",
    "srv-01_lag_max_usec",
    "srv-01_accepted_sample_count",
    "srv-01_rejected_sample_count",
    "srv-01_acceptance_ratio",
    "cli-01_lag_median_usec",
    "cli-01_lag_p95_usec",
    "cli-01_lag_max_usec",
    "cli-01_accepted_sample_count",
    "cli-01_rejected_sample_count",
    "cli-01_acceptance_ratio",
    "config_hash",
)


class ScalingError(RuntimeError):
    pass


def _positive(value: Any, label: str, *, allow_zero: bool = False) -> int:
    minimum = 0 if allow_zero else 1
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise ScalingError(f"{label} must be an integer >= {minimum}")
    return value


def attempt_policy(matrix: dict[str, Any]) -> dict[str, Any]:
    value = matrix.get("attempts")
    if isinstance(value, int) and not isinstance(value, bool) and value >= 1:
        return {
            "baseline": list(range(1, value + 1)),
            "extension": [],
            "triggers": None,
        }
    if not isinstance(value, dict):
        raise ScalingError("matrix.attempts must be a positive integer or policy")
    unknown = sorted(set(value) - {"baseline", "extension"})
    if unknown:
        raise ScalingError("unknown matrix.attempts fields: " + ", ".join(unknown))
    baseline = value.get("baseline")
    if (
        not isinstance(baseline, list)
        or not baseline
        or any(isinstance(item, bool) or not isinstance(item, int) for item in baseline)
        or baseline != list(range(1, len(baseline) + 1))
    ):
        raise ScalingError("matrix.attempts.baseline must be consecutive from 1")
    extension = value.get("extension")
    if not isinstance(extension, dict):
        raise ScalingError("matrix.attempts.extension must be a mapping")
    extension_unknown = sorted(set(extension) - {"attempts", "triggers"})
    if extension_unknown:
        raise ScalingError(
            "unknown matrix.attempts.extension fields: "
            + ", ".join(extension_unknown)
        )
    additional = extension.get("attempts")
    expected = list(
        range(len(baseline) + 1, len(baseline) + 1 + len(additional or []))
    )
    if (
        not isinstance(additional, list)
        or not additional
        or any(isinstance(item, bool) or not isinstance(item, int) for item in additional)
        or additional != expected
    ):
        raise ScalingError(
            "matrix.attempts.extension.attempts must continue after baseline"
        )
    triggers = extension.get("triggers")
    if not isinstance(triggers, dict) or set(triggers) != {
        "any_failure",
        "relative_spread",
    }:
        raise ScalingError(
            "matrix.attempts.extension.triggers must define any_failure and "
            "relative_spread"
        )
    if not isinstance(triggers["any_failure"], bool):
        raise ScalingError(
            "matrix.attempts.extension.triggers.any_failure must be boolean"
        )
    spread = triggers["relative_spread"]
    if not isinstance(spread, dict) or set(spread) != {"metric", "greater_than"}:
        raise ScalingError(
            "matrix.attempts.extension.triggers.relative_spread must define "
            "metric and greater_than"
        )
    if spread["metric"] != "rtf":
        raise ScalingError("attempt extension relative_spread.metric must be rtf")
    threshold = spread["greater_than"]
    if (
        isinstance(threshold, bool)
        or not isinstance(threshold, (int, float))
        or threshold <= 0
    ):
        raise ScalingError(
            "attempt extension relative_spread.greater_than must be positive"
        )
    return {
        "baseline": baseline,
        "extension": additional,
        "triggers": triggers,
    }


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
    policy = attempt_policy(matrix)
    attempts = len(policy["baseline"])
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
    mode = measurement.get("mode")
    if mode not in {"performance", "temporal"}:
        raise ScalingError("measurement.mode must be performance or temporal")
    temporal_interval = measurement.get("temporal_sampling_interval_usec")
    if mode == "temporal":
        _positive(
            temporal_interval,
            "measurement.temporal_sampling_interval_usec",
        )
    elif temporal_interval is not None:
        raise ScalingError(
            "performance measurement must not set temporal_sampling_interval_usec"
        )
    return raw, drone_counts, attempts


def configuration_id(
    drone_count: int,
    real_sleep_msec: int,
    measurement_mode: str = "performance",
) -> str:
    prefix = "temporal-" if measurement_mode == "temporal" else ""
    return f"{prefix}uav-{drone_count:03d}-sleep-{real_sleep_msec:03d}ms"


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
    measurement["configuration_id"] = configuration_id(
        drone_count, sleep, str(measurement["mode"])
    )
    policy = attempt_policy(matrix)
    allowed_attempts = policy["baseline"] + policy["extension"]
    if attempt not in allowed_attempts:
        raise ScalingError(
            "attempt " + str(attempt) + " is outside declared attempts: "
            + ", ".join(map(str, allowed_attempts))
        )
    measurement["attempt"] = attempt
    return result


def generated_experiment_path(
    output_root: Path,
    drone_count: int,
    real_sleep_msec: int,
    attempt: int = 1,
    measurement_mode: str = "performance",
) -> Path:
    return (
        output_root
        / "matrix"
        / "multi-host-scaling"
        / configuration_id(drone_count, real_sleep_msec, measurement_mode)
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
    mode = str(condition["measurement"]["mode"])
    generated = generated_experiment_path(
        args.output_root, args.drone_count, sleep, args.attempt, mode
    )
    yaml_support.write_simple_yaml(generated, condition)
    args.generated_experiment = generated
    delegated = delegate_arguments(args, "configure")
    delegated.extend(["--host", args.host])
    return multi_host.main(delegated)


def plan(path: Path) -> int:
    raw, counts, attempts = load_scaling(path)
    policy = attempt_policy(raw["matrix"])
    sleep = int(raw["runtime"]["conductor"]["real_sleep_msec"])
    mode = str(raw["measurement"]["mode"])
    hosts = raw["deployment"]["hosts"]
    print(f"Experiment C: multi-host {mode} preflight")
    print(f"real_sleep_msec: {sleep} (scalar)")
    print(f"attempts: {attempts}")
    if policy["extension"]:
        spread = policy["triggers"]["relative_spread"]
        print(
            "extension: "
            + ",".join(map(str, policy["extension"]))
            + f" when failure={policy['triggers']['any_failure']} or "
            + f"{spread['metric']} spread > {spread['greater_than']:.1%}"
        )
    for count in counts:
        resolved = resolve_condition(raw, count)
        placement = ", ".join(
            f"{host_id}={host['drone_count']} UAV/{host['process_count']} proc"
            for host_id, host in resolved["deployment"]["hosts"].items()
        )
        print(f"- {configuration_id(count, sleep, mode)}: {placement}")
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
    measurement_mode: str,
    temporal_sampling_interval_usec: int | None = None,
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
        "mode": (payload.get("mode"), measurement_mode),
        "temporal_observer_enabled": (
            metadata.get("temporal_observer_enabled"),
            measurement_mode == "temporal",
        ),
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
    actual_temporal_interval = metadata.get("temporal_sampling_interval_usec")
    if actual_temporal_interval != temporal_sampling_interval_usec:
        mismatches.append(
            "temporal_sampling_interval_usec="
            f"{actual_temporal_interval!r} "
            f"(expected {temporal_sampling_interval_usec!r})"
        )
    if mismatches:
        raise ScalingError(f"result identity mismatch in {path}: " + "; ".join(mismatches))


def _required_number(mapping: Any, key: str, path: Path) -> float:
    value = _number(mapping, key)
    if value is None:
        raise ScalingError(f"{key} is missing or not numeric in {path}")
    return value


def _temporal_metrics(payload: dict[str, Any], path: Path) -> dict[str, float | int]:
    temporal = payload.get("temporal")
    if not isinstance(temporal, dict):
        raise ScalingError(f"temporal result is missing: {path}")
    accepted = temporal.get("accepted_sample_count")
    rejected = temporal.get("rejected_sample_count")
    sample_count = temporal.get("sample_count")
    if isinstance(accepted, bool) or not isinstance(accepted, int) or accepted < 1:
        raise ScalingError(f"accepted temporal samples are missing: {path}")
    if isinstance(rejected, bool) or not isinstance(rejected, int) or rejected < 0:
        raise ScalingError(f"rejected temporal sample count is invalid: {path}")
    if (
        isinstance(sample_count, bool)
        or not isinstance(sample_count, int)
        or sample_count != accepted + rejected
    ):
        raise ScalingError(f"temporal sample accounting is inconsistent: {path}")
    acceptance_ratio = _required_number(temporal, "acceptance_ratio", path)
    expected_ratio = accepted / sample_count
    if not 0.0 <= acceptance_ratio <= 1.0 or abs(
        acceptance_ratio - expected_ratio
    ) > 1e-9:
        raise ScalingError(f"temporal acceptance ratio is inconsistent: {path}")
    return {
        "lag_median_usec": _required_number(temporal, "lag_median_usec", path),
        "lag_p95_usec": _required_number(temporal, "lag_p95_usec", path),
        "lag_max_usec": _required_number(temporal, "lag_max_usec", path),
        "accepted_sample_count": accepted,
        "rejected_sample_count": rejected,
        "acceptance_ratio": acceptance_ratio,
    }


def _temporal_summary(
    raw: dict[str, Any],
    counts: list[int],
    attempts: int,
    output_root: Path,
    selected_drone_count: int | None,
    attempt_numbers: list[int],
) -> int:
    measurement = raw["measurement"]
    series = str(measurement["series"])
    results_directory = str(raw["results"]["directory"])
    sleep = int(raw["runtime"]["conductor"]["real_sleep_msec"])
    temporal_interval = int(measurement["temporal_sampling_interval_usec"])
    host_ids = list(raw["deployment"]["allocation"]["host_order"])
    if host_ids != ["srv-01", "cli-01"]:
        raise ScalingError(
            "multi-host Temporal Validation currently requires srv-01 and cli-01"
        )
    rows: list[dict[str, Any]] = []
    missing: list[str] = []
    for count in counts:
        config_id = configuration_id(count, sleep, "temporal")
        for attempt in attempt_numbers:
            payloads: dict[str, dict[str, Any]] = {}
            metrics: dict[str, dict[str, float | int]] = {}
            for host_id in host_ids:
                result = result_path(
                    output_root,
                    results_directory,
                    series,
                    host_id,
                    config_id,
                    attempt,
                )
                if not result.is_file():
                    missing.append(str(result))
                    continue
                try:
                    payload = json.loads(result.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError) as exc:
                    raise ScalingError(f"cannot read result {result}: {exc}") from exc
                if not isinstance(payload, dict):
                    raise ScalingError(f"result must be a JSON object: {result}")
                validate_result_identity(
                    payload,
                    path=result,
                    host_id=host_id,
                    configuration_id=config_id,
                    attempt=attempt,
                    real_sleep_msec=sleep,
                    measurement_mode="temporal",
                    temporal_sampling_interval_usec=temporal_interval,
                )
                payloads[host_id] = payload
                metrics[host_id] = _temporal_metrics(payload, result)
            paired = set(payloads) == set(host_ids)
            hashes = {
                payload.get("metadata", {}).get("config_hash")
                for payload in payloads.values()
            }
            if paired and (None in hashes or len(hashes) != 1):
                raise ScalingError(
                    f"config hash mismatch for {config_id} attempt {attempt}: {hashes}"
                )
            statuses = {payload.get("status") for payload in payloads.values()}
            world_start = {
                host_id: _number(payload.get("performance"), "world_time_start_usec")
                for host_id, payload in payloads.items()
            }
            world_end = {
                host_id: _number(payload.get("performance"), "world_time_end_usec")
                for host_id, payload in payloads.items()
            }
            if paired and (None in world_start.values() or None in world_end.values()):
                raise ScalingError(
                    f"world-time boundary is missing for {config_id} attempt {attempt}"
                )
            row: dict[str, Any] = {
                "configuration_id": config_id,
                "drone_count": count,
                "real_sleep_msec": sleep,
                "attempt": attempt,
                "paired": paired,
                "status": (
                    "success"
                    if paired and statuses == {"success"}
                    else "incomplete"
                ),
                "world_time_start_difference_usec": (
                    abs(world_start["srv-01"] - world_start["cli-01"])
                    if paired
                    else None
                ),
                "world_time_end_difference_usec": (
                    abs(world_end["srv-01"] - world_end["cli-01"])
                    if paired
                    else None
                ),
                "config_hash": next(iter(hashes)) if len(hashes) == 1 else None,
            }
            for host_id in host_ids:
                host_metrics = metrics.get(host_id, {})
                for key in (
                    "lag_median_usec",
                    "lag_p95_usec",
                    "lag_max_usec",
                    "accepted_sample_count",
                    "rejected_sample_count",
                    "acceptance_ratio",
                ):
                    row[f"{host_id}_{key}"] = host_metrics.get(key)
            rows.append(row)

    summary_root = output_root / results_directory / series / "summary"
    summary_root.mkdir(parents=True, exist_ok=True)
    stem = f"multi-host-temporal-sleep-{sleep:03d}ms"
    if selected_drone_count is not None:
        stem += f"-uav-{selected_drone_count:03d}"
    json_path = summary_root / f"{stem}.json"
    csv_path = summary_root / f"{stem}.csv"
    multi_host.atomic_json(
        json_path,
        {
            "validation": "multi-host-temporal",
            "protocol_status": measurement["protocol_status"],
            "real_sleep_msec": sleep,
            "attempts": len(attempt_numbers),
            "attempt_numbers": attempt_numbers,
            "complete": not missing,
            "missing_results": missing,
            "results": rows,
        },
    )
    with csv_path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=TEMPORAL_SUMMARY_FIELDS)
        writer.writeheader()
        writer.writerows(rows)
    print(f"Temporal summary JSON: {json_path}")
    print(f"Temporal summary CSV : {csv_path}")
    paired_count = sum(1 for row in rows if row["paired"])
    print(f"Paired               : {paired_count}/{len(rows)}")
    return 0 if not missing else 1


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
    path: Path,
    output_root: Path,
    selected_drone_count: int | None = None,
    attempt_numbers: list[int] | None = None,
) -> int:
    raw, counts, attempts = load_scaling(path)
    selected_attempts = (
        list(range(1, attempts + 1))
        if attempt_numbers is None
        else list(attempt_numbers)
    )
    declared = attempt_policy(raw["matrix"])
    allowed_attempts = declared["baseline"] + declared["extension"]
    if (
        not selected_attempts
        or len(set(selected_attempts)) != len(selected_attempts)
        or selected_attempts != sorted(selected_attempts)
        or any(attempt not in allowed_attempts for attempt in selected_attempts)
    ):
        raise ScalingError("summary attempt_numbers must be unique declared attempts")
    if selected_drone_count is not None:
        if selected_drone_count not in counts:
            raise ScalingError(
                f"--drone-count must be one of: {', '.join(map(str, counts))}"
            )
        counts = [selected_drone_count]
    measurement = raw["measurement"]
    mode = str(measurement["mode"])
    if mode == "temporal":
        return _temporal_summary(
            raw,
            counts,
            attempts,
            output_root,
            selected_drone_count,
            selected_attempts,
        )
    series = str(measurement["series"])
    results_directory = str(raw["results"]["directory"])
    sleep = int(raw["runtime"]["conductor"]["real_sleep_msec"])
    host_ids = list(raw["deployment"]["allocation"]["host_order"])
    server_host = str(raw["deployment"]["server_host"])
    rows: list[dict[str, Any]] = []
    missing: list[str] = []
    for count in counts:
        config_id = configuration_id(count, sleep, mode)
        payloads: dict[str, dict[str, Any]] = {}
        for attempt in selected_attempts:
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
                    real_sleep_msec=sleep, measurement_mode=mode,
                    temporal_sampling_interval_usec=None)
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
            "attempts": len(selected_attempts),
            "attempt_numbers": selected_attempts,
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
            str(configured["resolved"]["measurement"]["mode"]),
        )
        return multi_host.main(delegate_arguments(args, args.command))
    except (ScalingError, multi_host.RecipeError) as exc:
        print(f"[ERROR] {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
