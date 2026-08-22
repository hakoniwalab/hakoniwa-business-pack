#!/usr/bin/env python3
"""Generate paper-oriented figures, tables, and a Markdown results draft."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import statistics
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:
    from tools import result_layout
    from tools.recipe import drone_fleet_performance_report as report
    from tools.recipe import drone_fleet_single_host as yaml_support
except ModuleNotFoundError:  # Direct execution outside the repository cwd.
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from tools import result_layout
    from tools.recipe import drone_fleet_performance_report as report
    from tools.recipe import drone_fleet_single_host as yaml_support


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_TEMPLATE = (
    Path(__file__).resolve().parent
    / "templates"
    / "drone-fleet-performance-paper.md"
)
WORKLOAD_COLORS = {32: "#2563eb", 64: "#059669", 128: "#dc2626"}


class PaperReportError(RuntimeError):
    pass


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _summary_path(layout: dict[str, Any], experiment: str, producer: str) -> Path:
    return report._single_host_summary_path(layout, experiment, producer)


def _load_complete(path: Path) -> dict[str, Any]:
    payload = report._load_json(path)
    if payload.get("complete") is not True:
        raise PaperReportError(f"summary is incomplete: {path}")
    return payload


def _performance_rows(path: Path) -> list[dict[str, Any]]:
    rows = report._valid_performance_rows(_load_complete(path), path)
    enriched: list[dict[str, Any]] = []
    series_root = path.parent.parent
    for source in rows:
        row = dict(source)
        drone_count = row.get("drone_count")
        process_count = row.get("process_count")
        attempt = row.get("attempt")
        if not all(isinstance(value, int) for value in (drone_count, process_count, attempt)):
            raise PaperReportError(f"invalid performance result identity: {path}")
        raw_path = (
            series_root
            / f"uav-{drone_count:03d}-proc-{process_count:02d}"
            / f"attempt-{attempt:02d}"
            / "result.json"
        )
        raw = report._load_json(raw_path)
        machine = raw.get("machine")
        if not isinstance(machine, dict):
            raise PaperReportError(f"performance result has no machine metrics: {raw_path}")
        row["memory_used_average_bytes"] = machine.get("memory_used_average_bytes")
        row["_paper_source_path"] = str(raw_path)
        enriched.append(row)
    return enriched


def _step_point(rows: list[dict[str, Any]]) -> dict[str, float | int]:
    values = [
        report._finite(row.get("average_step_wall_clock_sec"), "average_step_wall_clock_sec")
        * 1000.0
        for row in rows
    ]
    rtf_values = [report._finite(row.get("rtf"), "rtf") for row in rows]
    cpu_values = [
        report._finite(row.get("cpu_average_percent"), "cpu_average_percent")
        for row in rows
    ]
    memory_byte_values = [
        report._finite(row.get("memory_used_average_bytes"), "memory_used_average_bytes")
        for row in rows
    ]
    memory_percent_values = [
        report._finite(row.get("memory_used_average_percent"), "memory_used_average_percent")
        for row in rows
    ]
    return {
        "step_msec": statistics.median(values),
        "step_msec_min": min(values),
        "step_msec_max": max(values),
        "rtf": statistics.median(rtf_values),
        "cpu_average_percent": statistics.median(cpu_values),
        "memory_used_average_bytes": statistics.median(memory_byte_values),
        "memory_used_average_percent": statistics.median(memory_percent_values),
        "attempt_count": len(values),
    }


def _single_host_points(
    rows: list[dict[str, Any]], *, group_field: str, x_field: str
) -> dict[int, dict[int, dict[str, float | int]]]:
    grouped: dict[int, dict[int, list[dict[str, Any]]]] = {}
    for row in rows:
        group = row.get(group_field)
        x_value = row.get(x_field)
        if not isinstance(group, int) or not isinstance(x_value, int):
            raise PaperReportError(f"invalid {group_field}/{x_field} result identity")
        grouped.setdefault(group, {}).setdefault(x_value, []).append(row)
    return {
        group: {x_value: _step_point(selected) for x_value, selected in points.items()}
        for group, points in grouped.items()
    }


def _figure_a(
    producers: list[str], points: dict[str, dict[int, dict[str, float | int]]]
) -> str:
    x_values = sorted(set().union(*(set(points[producer]) for producer in producers)))
    series = [
        {
            "label": producer.upper(),
            "color": report.COLORS[index],
            "points": points[producer],
        }
        for index, producer in enumerate(producers)
    ]
    body = report._panel(
        x=45,
        y=105,
        width=1090,
        height=500,
        title="Single-process scalability boundary",
        x_values=x_values,
        series=series,
        metric="step_msec",
        y_label="Average wall-clock time per simulation step (ms)",
        log_scale=False,
        reference=1.0,
        reference_label="RTF = 1 (T_step = 1 ms)",
        error_bars=False,
    )
    return report._svg_document(
        1180,
        650,
        "Experiment A — Single-process Scalability",
        "One official attempt per condition; UAV workload doubles from 1 to 128",
        body,
        [(producer.upper(), report.COLORS[index]) for index, producer in enumerate(producers)],
    )


def _figure_b(
    producers: list[str], points: dict[str, dict[int, dict[int, dict[str, float | int]]]]
) -> str:
    workloads = sorted(set().union(*(set(points[producer]) for producer in producers)))
    body: list[str] = []
    for index, producer in enumerate(producers):
        x_values = sorted(
            set().union(*(set(points[producer][workload]) for workload in workloads))
        )
        series = [
            {
                "label": f"{workload} UAV",
                "color": WORKLOAD_COLORS[workload],
                "points": points[producer][workload],
            }
            for workload in workloads
        ]
        body.extend(
            report._panel(
                x=45,
                y=105 + index * 390,
                width=1090,
                height=370,
                title=f"{producer.upper()}: static near-even process partitioning",
                x_values=x_values,
                series=series,
                metric="step_msec",
                y_label="Average wall-clock time per simulation step (ms)",
                log_scale=False,
                reference=1.0,
                reference_label="RTF = 1 (T_step = 1 ms)",
            )
        )
    return report._svg_document(
        1180,
        900,
        "Experiment B — Multi-process Performance Recovery",
        "Median of successful validated attempts; whiskers show observed minimum/maximum",
        body,
        [(f"{workload} UAV", WORKLOAD_COLORS[workload]) for workload in workloads],
    )


def _c_points(matrix_path: Path) -> tuple[dict[int, dict[str, float | int]], list[dict[str, Any]]]:
    payload = _load_complete(matrix_path)
    conditions = payload.get("conditions")
    if not isinstance(conditions, list) or not conditions:
        raise PaperReportError(f"multi-host summary has no conditions: {matrix_path}")
    result: dict[int, dict[str, float | int]] = {}
    rows: list[dict[str, Any]] = []
    for condition in conditions:
        if not isinstance(condition, dict) or not isinstance(condition.get("drone_count"), int):
            raise PaperReportError("invalid multi-host condition")
        stats_rows = condition.get("statistics")
        if not isinstance(stats_rows, list) or len(stats_rows) != 1:
            raise PaperReportError("multi-host condition must have exactly one statistics entry")
        stats = stats_rows[0]
        rtf = stats.get("rtf") if isinstance(stats, dict) else None
        values = rtf.get("values") if isinstance(rtf, dict) else None
        if not isinstance(values, list) or not values:
            raise PaperReportError("multi-host condition has no RTF attempt values")
        rtf_values = [report._finite(value, "multi-host rtf") for value in values]
        step_values = [1.0 / value for value in rtf_values]
        count = condition["drone_count"]
        point = {
            "step_msec": statistics.median(step_values),
            "step_msec_min": min(step_values),
            "step_msec_max": max(step_values),
            "rtf": statistics.median(rtf_values),
            "attempt_count": len(values),
        }
        result[count] = point
        rows.append(
            {
                "total_uav": count,
                **point,
                "extension_required": bool(
                    isinstance(condition.get("extension_decision"), dict)
                    and condition["extension_decision"].get("required")
                ),
            }
        )
    return result, rows


def _c_resource_points(
    series_root: Path, c_rows: list[dict[str, Any]]
) -> tuple[dict[int, dict[str, dict[str, float | int]]], list[Path]]:
    resources: dict[int, dict[str, dict[str, float | int]]] = {}
    inputs: list[Path] = []
    for condition in c_rows:
        total_uav = int(condition["total_uav"])
        expected_count = int(condition["attempt_count"])
        resources[total_uav] = {}
        for host in ("srv-01", "cli-01"):
            pattern = (
                series_root
                / "hosts"
                / host
                / f"uav-{total_uav:03d}-sleep-*ms"
                / "attempt-*"
                / "result.json"
            )
            paths = sorted(pattern.parent.parent.parent.glob(
                f"uav-{total_uav:03d}-sleep-*ms/attempt-*/result.json"
            ))
            if len(paths) != expected_count:
                raise PaperReportError(
                    f"expected {expected_count} {host} resource results for {total_uav} UAV; found {len(paths)}"
                )
            cpu_values: list[float] = []
            memory_byte_values: list[float] = []
            memory_percent_values: list[float] = []
            for path in paths:
                payload = report._load_json(path)
                if payload.get("status") != "success":
                    raise PaperReportError(f"resource result is not successful: {path}")
                validation = payload.get("validation")
                if not isinstance(validation, dict) or validation.get("passed") is not True:
                    raise PaperReportError(f"resource result did not pass validation: {path}")
                machine = payload.get("machine")
                if not isinstance(machine, dict):
                    raise PaperReportError(f"resource result has no machine metrics: {path}")
                cpu_values.append(report._finite(machine.get("cpu_average_percent"), "cpu_average_percent"))
                memory_byte_values.append(report._finite(machine.get("memory_used_average_bytes"), "memory_used_average_bytes"))
                memory_percent_values.append(report._finite(machine.get("memory_used_average_percent"), "memory_used_average_percent"))
                inputs.append(path)
            resources[total_uav][host] = {
                "attempt_count": len(paths),
                "cpu_average_percent": statistics.median(cpu_values),
                "cpu_average_percent_min": min(cpu_values),
                "cpu_average_percent_max": max(cpu_values),
                "memory_used_average_bytes": statistics.median(memory_byte_values),
                "memory_used_average_percent": statistics.median(memory_percent_values),
            }
    return resources, inputs


def _figure_c(
    c_points: dict[int, dict[str, float | int]],
    b_points: dict[str, dict[int, dict[int, dict[str, float | int]]]],
) -> str:
    counts = sorted(c_points)
    references: dict[str, dict[int, dict[str, float | int]]] = {}
    for producer, process_count in (("mac", 6), ("wsl2", 12)):
        references[producer] = {}
        for total in counts:
            local_uav = total // 2
            try:
                references[producer][total] = b_points[producer][local_uav][process_count]
            except KeyError as exc:
                raise PaperReportError(
                    f"missing Experiment B reference: {producer}, {local_uav} UAV, {process_count} processes"
                ) from exc
    series = [
        {"label": "Mac, 6 processes", "color": "#059669", "points": references["mac"]},
        {"label": "WSL2, 12 processes", "color": "#dc2626", "points": references["wsl2"]},
        {"label": "Multi-host", "color": "#2563eb", "points": c_points},
    ]
    body = report._panel(
        x=45,
        y=105,
        width=1090,
        height=500,
        title="Fixed deployment: Mac 6 processes + WSL2 12 processes",
        x_values=counts,
        series=series,
        metric="step_msec",
        y_label="Average wall-clock time per simulation step (ms)",
        log_scale=False,
        reference=1.0,
        reference_label="RTF = 1 (T_step = 1 ms)",
    )
    return report._svg_document(
        1180,
        650,
        "Experiment C — Multi-host End-to-end Scaling",
        "Multi-host median/min/max with corresponding half-workload single-host references",
        body,
        [(item["label"], item["color"]) for item in series],
    )


def _markdown_table(headers: list[str], rows: list[list[Any]]) -> str:
    def value(item: Any) -> str:
        if item is None:
            return "—"
        return str(item).replace("|", "\\|").replace("\n", " ")

    lines = [
        "| " + " | ".join(headers) + " |",
        "| " + " | ".join("---" if index == 0 else "---:" for index in range(len(headers))) + " |",
    ]
    lines.extend("| " + " | ".join(value(item) for item in row) + " |" for row in rows)
    return "\n".join(lines)


def _fmt_msec(value: float | int) -> str:
    return f"{float(value):.4f}"


def _fmt_rtf(value: float | int) -> str:
    return f"{float(value):.3f}"


def _authority(layout: dict[str, Any], experiment_id: str) -> tuple[Path, dict[str, Any]]:
    path = ROOT / layout["experiments"][experiment_id]["experiment"]
    return path, yaml_support.load_simple_yaml(path)


def _joined(values: list[Any]) -> str:
    return "/".join(str(value) for value in values)


def _attempt_contract(matrix: dict[str, Any]) -> str:
    attempts = matrix.get("attempts")
    if isinstance(attempts, int):
        return str(attempts)
    if not isinstance(attempts, dict):
        raise PaperReportError("invalid experiment attempt contract")
    baseline = attempts.get("baseline")
    extension = attempts.get("extension")
    if not isinstance(baseline, list) or not isinstance(extension, dict):
        raise PaperReportError("invalid baseline/extension attempt contract")
    extra = extension.get("attempts")
    if not isinstance(extra, list):
        raise PaperReportError("invalid extension attempt list")
    return f"{len(baseline)}; trigger時{len(baseline) + len(extra)}"


def _protocol_table(layout: dict[str, Any]) -> tuple[str, list[Path]]:
    authorities = {
        experiment_id: _authority(layout, experiment_id)
        for experiment_id in (
            "experiment-a",
            "experiment-b",
            "experiment-b-temporal",
            "experiment-c",
            "experiment-c-temporal",
        )
    }
    a = authorities["experiment-a"][1]
    b = authorities["experiment-b"][1]
    bt = authorities["experiment-b-temporal"][1]
    c = authorities["experiment-c"][1]
    ct = authorities["experiment-c-temporal"][1]
    b_workloads = b["matrix"]["workloads"]
    b_uav = [value["drone_count"] for value in b_workloads.values()]
    b_process = sorted(
        set().union(*(set(value["process_count"]) for value in b_workloads.values()))
    )
    c_counts = c["matrix"]["drone_count"]
    c_hosts = c["deployment"]["hosts"]
    c_processes = [c_hosts[host]["process_count"] for host in c["deployment"]["allocation"]["host_order"]]
    ct_count = ct["matrix"]["drone_count"][0]
    table = _markdown_table(
        ["Dataset", "Hosts", "UAV workload", "Simulator processes", "Attempts", "Conductor / transport", "Observer"],
        [
            ["A", "Mac, WSL2 separately", _joined(a["matrix"]["drone_count"]), str(a["scale"]["process_count"]), _attempt_contract(a["matrix"]), "embedded / local", "OFF"],
            ["B", "Mac, WSL2 separately", _joined(b_uav), _joined(b_process), _attempt_contract(b["matrix"]), "embedded / local", "OFF"],
            ["B Temporal", "Mac, WSL2 separately", str(bt["scale"]["drone_count"]), _joined(bt["matrix"]["process_count"]), _attempt_contract(bt["matrix"]), "embedded / local", "ON"],
            ["C", "Mac + WSL2", " / ".join(f"{count // 2}+{count // 2}" for count in c_counts), "+".join(str(value) for value in c_processes) + " fixed", _attempt_contract(c["matrix"]), f'external / {c["deployment"]["transport"]["type"].upper()}', "OFF"],
            ["C Temporal", "Mac + WSL2", f"{ct_count // 2}+{ct_count // 2}", "+".join(str(value) for value in c_processes) + " fixed", _attempt_contract(ct["matrix"]), f'external / {ct["deployment"]["transport"]["type"].upper()}', "ON"],
        ],
    )
    return table, [value[0] for value in authorities.values()]


def _temporal_rows(layout: dict[str, Any]) -> tuple[list[list[Any]], list[Path], list[dict[str, Any]]]:
    table_rows: list[list[Any]] = []
    derived: list[dict[str, Any]] = []
    inputs: list[Path] = []
    for producer in ("mac", "wsl2"):
        path = _summary_path(layout, "experiment-b-temporal", producer)
        payload = _load_complete(path)
        values = payload.get("results")
        if not isinstance(values, list) or len(values) != 1 or values[0].get("status") != "success":
            raise PaperReportError(f"B Temporal requires one successful result: {path}")
        row = values[0]
        table_rows.append([
            "B-max", producer.upper(), row.get("accepted_sample_count"), row.get("rejected_sample_count"),
            f'{float(row.get("lag_median_usec")) / 1000:.1f}', f'{float(row.get("lag_p95_usec")) / 1000:.1f}',
            f'{float(row.get("lag_max_usec")) / 1000:.1f}', "—",
        ])
        derived.append({"condition": "B-max", "host": producer, **row})
        inputs.append(path)
    root = report._series_root(layout, "experiment-c-temporal", "srv-01")
    path = report._unique_summary(root / "summary", "multi-host-temporal-sleep-*ms-uav-*.json")
    payload = _load_complete(path)
    values = payload.get("results")
    if not isinstance(values, list) or len(values) != 1:
        raise PaperReportError(f"C Temporal requires one paired result: {path}")
    row = values[0]
    for host in ("srv-01", "cli-01"):
        table_rows.append([
            "C-max", host, row.get(f"{host}_accepted_sample_count"), row.get(f"{host}_rejected_sample_count"),
            f'{float(row.get(f"{host}_lag_median_usec")) / 1000:.1f}',
            f'{float(row.get(f"{host}_lag_p95_usec")) / 1000:.1f}',
            f'{float(row.get(f"{host}_lag_max_usec")) / 1000:.1f}',
            f'{float(row.get("world_time_start_difference_usec")) / 1000:.1f} / {float(row.get("world_time_end_difference_usec")) / 1000:.1f}',
        ])
        derived.append({"condition": "C-max", "host": host, **row})
    inputs.append(path)
    return table_rows, inputs, derived


def _render_template(template: str, values: dict[str, str]) -> str:
    rendered = template
    for key, value in values.items():
        token = "{{" + key + "}}"
        if token not in rendered:
            raise PaperReportError(f"template is missing token: {token}")
        rendered = rendered.replace(token, value)
    if "{{" in rendered or "}}" in rendered:
        raise PaperReportError("template contains unresolved tokens")
    return rendered


def render_paper(args: argparse.Namespace) -> int:
    layout = result_layout.load_layout(args.layout)
    analysis = layout["analysis"].get("paper-results")
    if not isinstance(analysis, dict):
        raise PaperReportError("layout.analysis.paper-results is missing")
    output_directory = ROOT / analysis["output_directory"].format(**layout["roots"])
    figure_directory = output_directory / "figures"
    figure_directory.mkdir(parents=True, exist_ok=True)

    a_inputs = {producer: _summary_path(layout, "experiment-a", producer) for producer in ("mac", "wsl2")}
    b_inputs = {producer: _summary_path(layout, "experiment-b", producer) for producer in ("mac", "wsl2")}
    a_rows = {producer: _performance_rows(path) for producer, path in a_inputs.items()}
    b_rows = {producer: _performance_rows(path) for producer, path in b_inputs.items()}
    a_points = {
        producer: _single_host_points(rows, group_field="process_count", x_field="drone_count")[1]
        for producer, rows in a_rows.items()
    }
    b_points = {
        producer: _single_host_points(rows, group_field="drone_count", x_field="process_count")
        for producer, rows in b_rows.items()
    }
    c_root = report._series_root(layout, "experiment-c", "srv-01")
    c_input = report._unique_summary(c_root / "summary", "multi-host-scaling-sleep-*ms-matrix.json")
    c_points, c_rows = _c_points(c_input)
    c_resources, c_resource_inputs = _c_resource_points(c_root, c_rows)

    figures = {
        "figure-3-single-process.svg": _figure_a(["mac", "wsl2"], a_points),
        "figure-4-multi-process.svg": _figure_b(["mac", "wsl2"], b_points),
        "figure-5-multi-host.svg": _figure_c(c_points, b_points),
    }
    output_paths: list[Path] = []
    for filename, content in figures.items():
        path = figure_directory / filename
        path.write_text(content, encoding="utf-8")
        output_paths.append(path)
        if args.png:
            png_path = path.with_suffix(".png")
            report._render_png(path, png_path)
            output_paths.append(png_path)

    a_observations = []
    for producer in ("mac", "wsl2"):
        realtime = [count for count, point in a_points[producer].items() if float(point["step_msec"]) <= 1.0]
        slower = [count for count, point in a_points[producer].items() if float(point["step_msec"]) > 1.0]
        a_observations.append(
            f'- **{producer.upper()}**: largest measured real-time workload = {max(realtime)} UAV; first measured non-real-time workload = {min(slower)} UAV.'
        )

    b_table_rows: list[list[Any]] = []
    b_derived: list[dict[str, Any]] = []
    for producer in ("mac", "wsl2"):
        for workload in sorted(b_points[producer]):
            points = b_points[producer][workload]
            base = points[1]
            best_process, best = min(points.items(), key=lambda item: float(item[1]["step_msec"]))
            speedup = float(base["step_msec"]) / float(best["step_msec"])
            b_table_rows.append([
                producer.upper(), workload, best_process, best["attempt_count"], _fmt_msec(base["step_msec"]),
                _fmt_msec(best["step_msec"]), f"{speedup:.2f}×", _fmt_rtf(best["rtf"]),
            ])
            b_derived.append({"host": producer, "uav": workload, "best_process": best_process, "speedup": speedup, **best})

    c_table_rows: list[list[Any]] = []
    c_derived: list[dict[str, Any]] = []
    for row in c_rows:
        total = int(row["total_uav"])
        wsl_reference = b_points["wsl2"][total // 2][12]
        ratio = float(row["step_msec"]) / float(wsl_reference["step_msec"])
        c_table_rows.append([
            total, f"{total // 2}+{total // 2}", "6+12", row["attempt_count"],
            _fmt_msec(row["step_msec"]),
            f'{_fmt_msec(row["step_msec_min"])}–{_fmt_msec(row["step_msec_max"])}',
            _fmt_rtf(row["rtf"]), f"{ratio * 100:.1f}%", "yes" if row["extension_required"] else "no",
        ])
        c_derived.append({**row, "wsl2_reference_step_msec": wsl_reference["step_msec"], "step_ratio_to_wsl2_reference": ratio})

    resource_b_rows: list[list[Any]] = []
    resource_b_derived: list[dict[str, Any]] = []
    for producer in ("mac", "wsl2"):
        for workload in sorted(b_points[producer]):
            points = b_points[producer][workload]
            best_process, best = min(points.items(), key=lambda item: float(item[1]["step_msec"]))
            baseline = points[1]
            resource_b_rows.append([
                producer.upper(), workload, f"1 process → {best_process} processes (smallest median)",
                f'{float(baseline["cpu_average_percent"]):.1f} → {float(best["cpu_average_percent"]):.1f}',
                f'{float(baseline["memory_used_average_bytes"]) / 2**30:.2f} → {float(best["memory_used_average_bytes"]) / 2**30:.2f}',
            ])
            resource_b_derived.append({
                "host": producer,
                "uav": workload,
                "baseline_process": 1,
                "best_process": best_process,
                "baseline_cpu_average_percent": baseline["cpu_average_percent"],
                "best_cpu_average_percent": best["cpu_average_percent"],
                "baseline_memory_used_average_bytes": baseline["memory_used_average_bytes"],
                "best_memory_used_average_bytes": best["memory_used_average_bytes"],
            })

    resource_c_rows: list[list[Any]] = []
    resource_c_derived: list[dict[str, Any]] = []
    for total_uav in sorted(c_resources):
        mac = c_resources[total_uav]["srv-01"]
        wsl2 = c_resources[total_uav]["cli-01"]
        resource_c_rows.append([
            total_uav,
            f'{float(mac["cpu_average_percent"]):.1f}',
            f'{float(wsl2["cpu_average_percent"]):.1f}',
            f'{float(mac["memory_used_average_bytes"]) / 2**30:.2f}',
            f'{float(wsl2["memory_used_average_bytes"]) / 2**30:.2f}',
        ])
        resource_c_derived.append({"total_uav": total_uav, "srv-01": mac, "cli-01": wsl2})

    temporal_rows, temporal_inputs, temporal_derived = _temporal_rows(layout)
    template = args.template.read_text(encoding="utf-8")
    generated_at = datetime.now(timezone.utc).isoformat()
    protocol_table, authority_inputs = _protocol_table(layout)
    markdown = _render_template(
        template,
        {
            "protocol_table": protocol_table,
            "figure_a": "![Experiment A: single-process scalability](figures/figure-3-single-process.svg)",
            "experiment_a_observations": "\n".join(a_observations),
            "figure_b": "![Experiment B: multi-process recovery](figures/figure-4-multi-process.svg)",
            "experiment_b_table": _markdown_table(
                ["Host", "UAV", "Process count with smallest median", "Attempts", "Median step time with 1 process (ms)", "Median step time at selected process count (ms)", "Speedup relative to 1 process", "Median RTF at selected process count"], b_table_rows
            ),
            "experiment_b_observations": "- Each process configuration is represented by the median of its per-attempt average step times. The listed process count has the smallest median among the tested configurations; it does not claim a global optimum.",
            "figure_c": "![Experiment C: multi-host scaling](figures/figure-5-multi-host.svg)",
            "experiment_c_table": _markdown_table(
                ["Total UAV", "Split", "Processes", "n", "Median T_step (ms)", "Observed range (ms)", "Median RTF", "vs. WSL2 reference", "Extended"], c_table_rows
            ),
            "experiment_c_observations": "- The multi-host result is compared with the WSL2 12-process result at the corresponding per-host workload. A ratio near 100% means the global step time closely follows that slower-host reference.",
            "resource_b_table": _markdown_table(
                ["Host", "UAV", "Configurations compared", "Whole-machine CPU average (%)", "Whole-machine used memory (GiB)"], resource_b_rows
            ),
            "resource_c_table": _markdown_table(
                ["Total UAV", "Mac CPU avg (%)", "WSL2 CPU avg (%)", "Mac memory (GiB)", "WSL2 memory (GiB)"], resource_c_rows
            ),
            "resource_observations": "- CPU utilization is a whole-machine metric and is interpreted longitudinally within each host. Memory is reported in GiB so unlike host capacities do not distort the comparison.",
            "temporal_table": _markdown_table(
                ["Condition", "Host", "Accepted", "Rejected", "Median lag (ms)", "p95 (ms)", "Max (ms)", "World-time start/end diff (ms)"], temporal_rows
            ),
            "temporal_observations": "- Temporal values come from dedicated observer-enabled runs and are descriptive rather than performance measurements.",
            "provenance": f"Generated at `{generated_at}` from the result-layout authority and the collected official summaries.",
        },
    )
    markdown_path = output_directory / f'{analysis["output_stem"]}.md'
    markdown_path.write_text(markdown, encoding="utf-8")
    output_paths.append(markdown_path)

    derived_path = output_directory / f'{analysis["output_stem"]}-values.json'
    derived = {
        "version": 1,
        "experiment_a": a_points,
        "experiment_b": b_derived,
        "experiment_c": c_derived,
        "resource_utilization": {"experiment_b": resource_b_derived, "experiment_c": resource_c_derived},
        "temporal": temporal_derived,
    }
    derived_path.write_text(json.dumps(derived, indent=2) + "\n", encoding="utf-8")
    output_paths.append(derived_path)

    raw_performance_inputs = [
        Path(row["_paper_source_path"])
        for rows in (*a_rows.values(), *b_rows.values())
        for row in rows
    ]
    inputs = [
        *authority_inputs,
        *a_inputs.values(),
        *b_inputs.values(),
        *raw_performance_inputs,
        c_input,
        *c_resource_inputs,
        *temporal_inputs,
    ]
    manifest_path = output_directory / f'{analysis["output_stem"]}-manifest.json'
    manifest = {
        "version": 1,
        "generated_at": generated_at,
        "template": {"path": str(args.template.resolve()), "sha256": _sha256(args.template.resolve())},
        "layout": {"path": str(args.layout.resolve()), "sha256": _sha256(args.layout.resolve())},
        "aggregation": {
            "primary_metric": "average_step_wall_clock_sec",
            "experiment_a": "single official attempt",
            "experiment_b": "median with observed minimum/maximum",
            "experiment_c": "median with observed minimum/maximum derived from authoritative server RTF and 1 ms Core step",
            "temporal_is_separate_from_performance": True,
        },
        "inputs": [{"path": str(path), "sha256": _sha256(path)} for path in inputs],
        "outputs": [{"path": str(path), "sha256": _sha256(path)} for path in output_paths],
    }
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    print(f"Paper Markdown : {markdown_path}")
    print(f"Derived values : {derived_path}")
    print(f"Paper manifest : {manifest_path}")
    for path in output_paths:
        if path.suffix in {".svg", ".png"}:
            print(f"Paper figure   : {path}")
    return 0


def parser() -> argparse.ArgumentParser:
    value = argparse.ArgumentParser(description=__doc__)
    value.add_argument("--layout", type=Path, default=result_layout.DEFAULT_LAYOUT)
    value.add_argument("--template", type=Path, default=DEFAULT_TEMPLATE)
    value.add_argument("--png", action="store_true", help="also render PNG figures")
    return value


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        return render_paper(args)
    except (PaperReportError, report.ReportError, result_layout.ResultLayoutError, OSError, ValueError, KeyError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
