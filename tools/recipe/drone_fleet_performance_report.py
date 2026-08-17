#!/usr/bin/env python3
"""Render reproducible cross-machine and multi-host performance reports."""

from __future__ import annotations

import argparse
import hashlib
import html
import json
import math
import shutil
import statistics
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:
    from tools import result_layout
except ModuleNotFoundError:  # Direct execution outside the repository cwd.
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from tools import result_layout


ROOT = Path(__file__).resolve().parents[2]
COLORS = ("#2563eb", "#dc2626", "#059669", "#7c3aed")


class ReportError(RuntimeError):
    pass


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _load_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ReportError(f"cannot read report input {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise ReportError(f"report input must be a JSON object: {path}")
    return value


def _finite(value: Any, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ReportError(f"{label} must be numeric")
    result = float(value)
    if not math.isfinite(result):
        raise ReportError(f"{label} must be finite")
    return result


def _valid_performance_rows(payload: dict[str, Any], path: Path) -> list[dict[str, Any]]:
    rows = payload.get("results")
    if not isinstance(rows, list) or not rows:
        raise ReportError(f"summary has no results: {path}")
    selected = []
    for row in rows:
        if not isinstance(row, dict):
            raise ReportError(f"summary result must be an object: {path}")
        if (
            row.get("status") == "success"
            and row.get("validation_passed") is True
            and row.get("preflight_passed") is True
        ):
            selected.append(row)
    if not selected:
        raise ReportError(f"summary has no successful validated results: {path}")
    return selected


def _series_root(layout: dict[str, Any], experiment_id: str, participant: str) -> Path:
    resolved = result_layout.resolve_experiment_paths(layout, experiment_id, participant)
    destination = resolved["destination"]
    if resolved["participant_scope"] == "host":
        # <series>/hosts/<host-id>
        return destination.parent.parent
    return destination


def _single_host_summary_path(
    layout: dict[str, Any], experiment_id: str, participant: str
) -> Path:
    filename = {
        "experiment-a": "experiment-a.json",
        "experiment-b": "experiment-b.json",
        "experiment-b-temporal": "temporal-b.json",
    }.get(experiment_id)
    if filename is None:
        raise ReportError(f"no single-host summary contract for {experiment_id}")
    return _series_root(layout, experiment_id, participant) / "summary" / filename


def _unique_summary(root: Path, pattern: str) -> Path:
    matches = sorted(root.glob(pattern))
    if len(matches) != 1:
        raise ReportError(
            f"expected exactly one summary matching {root / pattern}; found {len(matches)}"
        )
    return matches[0]


def _aggregate_rows(
    rows: list[dict[str, Any]], x_field: str, group_field: str | None = None
) -> dict[Any, dict[Any, dict[str, float]]]:
    grouped: dict[Any, dict[Any, list[dict[str, Any]]]] = {}
    for row in rows:
        group = row.get(group_field) if group_field is not None else "all"
        x = row.get(x_field)
        if isinstance(x, bool) or not isinstance(x, (int, float)):
            raise ReportError(f"invalid {x_field} in performance summary")
        grouped.setdefault(group, {}).setdefault(x, []).append(row)
    result: dict[Any, dict[Any, dict[str, float]]] = {}
    for group, points in grouped.items():
        result[group] = {}
        for x, selected in points.items():
            item: dict[str, float] = {}
            for metric in ("rtf", "cpu_average_percent"):
                values = [_finite(row.get(metric), metric) for row in selected]
                item[metric] = statistics.median(values)
                item[f"{metric}_min"] = min(values)
                item[f"{metric}_max"] = max(values)
            result[group][x] = item
    return result


def _esc(value: Any) -> str:
    return html.escape(str(value), quote=True)


def _svg_text(
    x: float,
    y: float,
    value: Any,
    css: str = "",
    anchor: str = "start",
    transform: str | None = None,
) -> str:
    transformed = f' transform="{_esc(transform)}"' if transform else ""
    return f'<text x="{x:.1f}" y="{y:.1f}" text-anchor="{anchor}" class="{css}"{transformed}>{_esc(value)}</text>'


def _panel(
    *,
    x: float,
    y: float,
    width: float,
    height: float,
    title: str,
    x_values: list[float],
    series: list[dict[str, Any]],
    metric: str,
    y_label: str,
    log_scale: bool,
    reference: float | None = None,
    error_bars: bool = True,
) -> list[str]:
    left = x + 74
    top = y + 42
    plot_width = width - 94
    plot_height = height - 100
    values = [
        float(point[metric])
        for item in series
        for point in item["points"].values()
        if point.get(metric) is not None
    ]
    if not values:
        raise ReportError(f"no values for panel metric {metric}")
    if reference is not None:
        values.append(reference)
    if log_scale:
        if min(values) <= 0:
            raise ReportError(f"log panel {metric} contains a non-positive value")
        low = 10 ** math.floor(math.log10(min(values)))
        high = 10 ** math.ceil(math.log10(max(values)))
        if math.isclose(low, high):
            high = low * 10
        transform_y = lambda value: top + plot_height * (
            math.log10(high) - math.log10(value)
        ) / (math.log10(high) - math.log10(low))
        ticks = [10.0**power for power in range(math.floor(math.log10(low)), math.ceil(math.log10(high)) + 1)]
    else:
        low = 0.0
        maximum = max(values)
        raw_step = maximum * 1.1 / 4 if maximum > 0 else 0.25
        magnitude = 10 ** math.floor(math.log10(raw_step))
        fraction = raw_step / magnitude
        if fraction <= 1:
            nice_fraction = 1
        elif fraction <= 2:
            nice_fraction = 2
        elif fraction <= 3:
            nice_fraction = 2.5
        elif fraction <= 7.5:
            nice_fraction = 5
        else:
            nice_fraction = 10
        step = nice_fraction * magnitude
        high = step * max(4, math.ceil(maximum / step))
        transform_y = lambda value: top + plot_height * (high - value) / (high - low)
        ticks = [step * index for index in range(round(high / step) + 1)]
    x_positions = {
        value: left + index * plot_width / max(1, len(x_values) - 1)
        for index, value in enumerate(x_values)
    }
    parts = [_svg_text(x + width / 2, y + 24, title, "panel-title", "middle")]
    for tick in ticks:
        tick_y = transform_y(tick)
        parts.append(f'<line x1="{left:.1f}" y1="{tick_y:.1f}" x2="{left + plot_width:.1f}" y2="{tick_y:.1f}" class="grid"/>')
        label = f"{tick:g}" if tick >= 0.01 else f"{tick:.1e}"
        parts.append(_svg_text(left - 8, tick_y + 4, label, "tick", "end"))
    if reference is not None and low <= reference <= high:
        ref_y = transform_y(reference)
        parts.append(f'<line x1="{left:.1f}" y1="{ref_y:.1f}" x2="{left + plot_width:.1f}" y2="{ref_y:.1f}" class="reference"/>')
        parts.append(_svg_text(left + plot_width - 4, ref_y - 7, f"{metric.upper()} = {reference:g}", "reference-label", "end"))
    parts.append(f'<line x1="{left:.1f}" y1="{top:.1f}" x2="{left:.1f}" y2="{top + plot_height:.1f}" class="axis"/>')
    parts.append(f'<line x1="{left:.1f}" y1="{top + plot_height:.1f}" x2="{left + plot_width:.1f}" y2="{top + plot_height:.1f}" class="axis"/>')
    for value in x_values:
        point_x = x_positions[value]
        parts.append(f'<line x1="{point_x:.1f}" y1="{top + plot_height:.1f}" x2="{point_x:.1f}" y2="{top + plot_height + 5:.1f}" class="axis"/>')
        parts.append(_svg_text(point_x, top + plot_height + 22, f"{value:g}", "tick", "middle"))
    parts.append(_svg_text(x + 16, top + plot_height / 2, y_label, "axis-label", "middle", f"rotate(-90 {x + 16:.1f} {top + plot_height / 2:.1f})"))
    for index, item in enumerate(series):
        coordinates = []
        point_offset = 0.0
        if len(x_values) == 1 and len(series) > 1:
            point_offset = (index - (len(series) - 1) / 2) * 12
        for value in x_values:
            point = item["points"].get(value)
            if point is None or point.get(metric) is None:
                continue
            point_x = x_positions[value] + point_offset
            point_y = transform_y(float(point[metric]))
            coordinates.append(f"{point_x:.1f},{point_y:.1f}")
            if error_bars:
                minimum = point.get(f"{metric}_min")
                maximum = point.get(f"{metric}_max")
                if minimum is not None and maximum is not None:
                    min_y = transform_y(float(minimum))
                    max_y = transform_y(float(maximum))
                    parts.append(f'<line x1="{point_x:.1f}" y1="{max_y:.1f}" x2="{point_x:.1f}" y2="{min_y:.1f}" stroke="{item["color"]}" class="error"/>')
                    parts.append(f'<line x1="{point_x - 4:.1f}" y1="{max_y:.1f}" x2="{point_x + 4:.1f}" y2="{max_y:.1f}" stroke="{item["color"]}" class="error"/>')
                    parts.append(f'<line x1="{point_x - 4:.1f}" y1="{min_y:.1f}" x2="{point_x + 4:.1f}" y2="{min_y:.1f}" stroke="{item["color"]}" class="error"/>')
            parts.append(f'<circle cx="{point_x:.1f}" cy="{point_y:.1f}" r="5" fill="{item["color"]}" class="mark"><title>{_esc(item["label"])}: x={value:g}, {metric}={float(point[metric]):.4f}</title></circle>')
        dash = ' stroke-dasharray="8 5"' if index % 2 else ""
        parts.append(f'<polyline points="{" ".join(coordinates)}" fill="none" stroke="{item["color"]}" stroke-width="3"{dash}/>' )
    return parts


def _svg_document(width: int, height: int, title: str, subtitle: str, body: list[str], legend: list[tuple[str, str]]) -> str:
    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}" role="img" aria-labelledby="title desc">',
        f'<title id="title">{_esc(title)}</title>',
        f'<desc id="desc">{_esc(subtitle)}</desc>',
        '<rect width="100%" height="100%" fill="#ffffff"/>',
        '<style>text{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;fill:#111827}.title{font-size:25px;font-weight:600}.subtitle{font-size:14px;fill:#4b5563}.panel-title{font-size:18px;font-weight:600}.axis-label{font-size:13px;fill:#374151}.tick{font-size:11px;fill:#4b5563}.legend{font-size:13px}.grid{stroke:#d1d5db;stroke-width:1}.axis{stroke:#4b5563;stroke-width:1.2}.reference{stroke:#111827;stroke-width:1.4;stroke-dasharray:5 4}.reference-label{font-size:11px;font-weight:600}.error{stroke-width:1.2}.mark{stroke:#fff;stroke-width:1.5}</style>',
        _svg_text(width / 2, 34, title, "title", "middle"),
        _svg_text(width / 2, 58, subtitle, "subtitle", "middle"),
    ]
    legend_width = len(legend) * 150
    start = (width - legend_width) / 2
    for index, (label, color) in enumerate(legend):
        item_x = start + index * 150
        parts.append(f'<line x1="{item_x:.1f}" y1="82" x2="{item_x + 30:.1f}" y2="82" stroke="{color}" stroke-width="3"/>')
        parts.append(_svg_text(item_x + 38, 87, label, "legend"))
    parts.extend(body)
    parts.append("</svg>")
    return "\n".join(parts) + "\n"


def _comparison_report(
    layout: dict[str, Any],
    experiment_id: str,
    include_temporal: bool = False,
) -> tuple[str, list[Path], list[dict[str, Any]]]:
    producers = layout["experiments"][experiment_id]["producers"]
    if len(producers) != 2:
        raise ReportError(f"{experiment_id} comparison requires exactly two producers")
    inputs = [_single_host_summary_path(layout, experiment_id, producer) for producer in producers]
    payloads = [_load_json(path) for path in inputs]
    rows = [_valid_performance_rows(payload, path) for payload, path in zip(payloads, inputs)]
    x_field = "drone_count" if experiment_id == "experiment-a" else "process_count"
    group_field = None if experiment_id == "experiment-a" else "drone_count"
    aggregated = [_aggregate_rows(value, x_field, group_field) for value in rows]
    groups = sorted(set(aggregated[0]) | set(aggregated[1]))
    if set(aggregated[0]) != set(aggregated[1]):
        raise ReportError("producer summaries do not contain the same workload groups")
    body: list[str] = []
    tables: list[dict[str, Any]] = []
    if experiment_id == "experiment-a":
        if include_temporal:
            raise ReportError("Experiment A has no Temporal Validation contract")
        width, height = 1180, 830
        x_values = sorted(set(aggregated[0]["all"]) | set(aggregated[1]["all"]))
        series = [
            {"label": producer.upper(), "color": COLORS[index], "points": aggregated[index]["all"]}
            for index, producer in enumerate(producers)
        ]
        body.extend(_panel(x=45,y=100,width=1090,height=330,title="UAV scale-up: Real Time Factor",x_values=x_values,series=series,metric="rtf",y_label="Real Time Factor (log)",log_scale=True,reference=1.0))
        body.extend(_panel(x=45,y=450,width=1090,height=330,title="UAV scale-up: Whole-machine CPU",x_values=x_values,series=series,metric="cpu_average_percent",y_label="CPU average (%)",log_scale=False))
        title = "Experiment A — Single-process Host Comparison"
    else:
        width, height = 1500, 900
        panel_width = 480
        for group_index, group in enumerate(groups):
            x_values = sorted(set(aggregated[0][group]) | set(aggregated[1][group]))
            series = [
                {"label": producer.upper(), "color": COLORS[index], "points": aggregated[index][group]}
                for index, producer in enumerate(producers)
            ]
            x = 20 + group_index * 490
            body.extend(_panel(x=x,y=100,width=panel_width,height=340,title=f"{int(group)} UAV — RTF",x_values=x_values,series=series,metric="rtf",y_label="Real Time Factor (log)",log_scale=True,reference=1.0))
            body.extend(_panel(x=x,y=455,width=panel_width,height=340,title=f"{int(group)} UAV — CPU",x_values=x_values,series=series,metric="cpu_average_percent",y_label="CPU average (%)",log_scale=False))
            for producer_index, producer in enumerate(producers):
                points = aggregated[producer_index][group]
                peak_x, peak = max(points.items(), key=lambda item: item[1]["rtf"])
                recovery = [value for value in sorted(points) if points[value]["rtf"] >= 1.0]
                tables.append({"workload":int(group),"producer":producer,"peak_process":int(peak_x),"peak_rtf":peak["rtf"],"minimum_realtime_process":int(recovery[0]) if recovery else None})
        title = "Experiment B — Multi-process Host Comparison"
        if include_temporal:
            temporal_series = []
            temporal_inputs = []
            for producer_index, producer in enumerate(producers):
                path = _single_host_summary_path(
                    layout, "experiment-b-temporal", producer
                )
                payload = _load_json(path)
                if payload.get("complete") is not True:
                    raise ReportError(f"Temporal Validation is incomplete: {path}")
                temporal_rows = payload.get("results")
                if not isinstance(temporal_rows, list) or not temporal_rows:
                    raise ReportError(f"Temporal Validation has no results: {path}")
                points = {}
                for row in temporal_rows:
                    if not isinstance(row, dict) or row.get("status") != "success":
                        raise ReportError(
                            f"Temporal Validation contains a non-success result: {path}"
                        )
                    process_count = row.get("process_count")
                    if not isinstance(process_count, int):
                        raise ReportError(
                            f"Temporal Validation process_count is invalid: {path}"
                        )
                    lag_p95 = _finite(row.get("lag_p95_usec"), "lag_p95_usec")
                    points[process_count] = {
                        "lag_p95_usec": lag_p95,
                        "lag_p95_usec_min": lag_p95,
                        "lag_p95_usec_max": lag_p95,
                    }
                    tables.append(
                        {
                            "temporal_producer": producer,
                            "temporal_process_count": process_count,
                            "temporal_lag_median_usec": row.get("lag_median_usec"),
                            "temporal_lag_p95_usec": lag_p95,
                            "temporal_lag_max_usec": row.get("lag_max_usec"),
                            "temporal_acceptance_ratio": row.get("acceptance_ratio"),
                        }
                    )
                temporal_series.append(
                    {
                        "label": producer.upper(),
                        "color": COLORS[producer_index],
                        "points": points,
                    }
                )
                temporal_inputs.append(path)
            temporal_x = sorted(
                set().union(*(set(item["points"]) for item in temporal_series))
            )
            body.extend(
                _panel(
                    x=20,
                    y=810,
                    width=1460,
                    height=320,
                    title="Temporal Validation: lag p95 at process endpoints",
                    x_values=temporal_x,
                    series=temporal_series,
                    metric="lag_p95_usec",
                    y_label="Lag p95 (usec)",
                    log_scale=False,
                    error_bars=False,
                )
            )
            inputs.extend(temporal_inputs)
            height = 1180
    svg = _svg_document(width,height,title,"Median of successful validated attempts; whiskers show observed min/max",body,[(producer.upper(), COLORS[index]) for index, producer in enumerate(producers)])
    return svg, inputs, tables


def _multi_host_report(layout: dict[str, Any], include_temporal: bool) -> tuple[str, list[Path], list[dict[str, Any]]]:
    series_root = _series_root(layout, "experiment-c", "srv-01")
    matrix_path = _unique_summary(series_root / "summary", "multi-host-scaling-sleep-*ms-matrix.json")
    payload = _load_json(matrix_path)
    if payload.get("complete") is not True:
        raise ReportError(f"multi-host matrix summary is incomplete: {matrix_path}")
    conditions = payload.get("conditions")
    if not isinstance(conditions, list) or not conditions:
        raise ReportError("multi-host matrix summary has no conditions")
    stats_by_count: dict[int, dict[str, Any]] = {}
    table: list[dict[str, Any]] = []
    for condition in conditions:
        if not isinstance(condition, dict):
            raise ReportError("invalid multi-host condition entry")
        count = condition.get("drone_count")
        statistics_rows = condition.get("statistics")
        if not isinstance(count, int) or not isinstance(statistics_rows, list) or len(statistics_rows) != 1:
            raise ReportError("multi-host condition requires one statistics entry")
        stats = statistics_rows[0]
        if not isinstance(stats, dict):
            raise ReportError("invalid multi-host statistics entry")
        stats_by_count[count] = stats
        decision = condition.get("extension_decision")
        table.append({
            "drone_count": count,
            "attempt_count": stats.get("attempt_count"),
            "rtf_mean": stats.get("rtf", {}).get("mean"),
            "rtf_pstdev": stats.get("rtf", {}).get("pstdev"),
            "extension_required": decision.get("required") if isinstance(decision, dict) else None,
        })
    counts = sorted(stats_by_count)
    rtf_points = {
        count: {
            "rtf": _finite(stats_by_count[count]["rtf"]["mean"], "rtf.mean"),
            "rtf_min": _finite(stats_by_count[count]["rtf"]["min"], "rtf.min"),
            "rtf_max": _finite(stats_by_count[count]["rtf"]["max"], "rtf.max"),
        }
        for count in counts
    }
    cpu_series = []
    for index, host in enumerate(("srv-01", "cli-01")):
        metric = f"{host}_cpu_average_percent"
        points = {
            count: {
                "cpu_average_percent": _finite(stats_by_count[count][metric]["mean"], f"{metric}.mean"),
                "cpu_average_percent_min": _finite(stats_by_count[count][metric]["min"], f"{metric}.min"),
                "cpu_average_percent_max": _finite(stats_by_count[count][metric]["max"], f"{metric}.max"),
            }
            for count in counts
        }
        cpu_series.append({"label": host, "color": COLORS[index], "points": points})
    body = []
    body.extend(_panel(x=45,y=100,width=1090,height=330,title="Multi-host scale-out: authoritative server RTF",x_values=counts,series=[{"label":"server RTF","color":COLORS[0],"points":rtf_points}],metric="rtf",y_label="Real Time Factor (log)",log_scale=True,reference=1.0))
    body.extend(_panel(x=45,y=450,width=1090,height=330,title="Host CPU utilization",x_values=counts,series=cpu_series,metric="cpu_average_percent",y_label="CPU average (%)",log_scale=False))
    inputs = [matrix_path]
    height = 830
    if include_temporal:
        temporal_root = _series_root(layout, "experiment-c-temporal", "srv-01")
        temporal_path = _unique_summary(temporal_root / "summary", "multi-host-temporal-sleep-*ms-uav-*.json")
        temporal = _load_json(temporal_path)
        if temporal.get("complete") is not True:
            raise ReportError(f"multi-host Temporal Validation is incomplete: {temporal_path}")
        temporal_rows = temporal.get("results")
        if not isinstance(temporal_rows, list) or len(temporal_rows) != 1:
            raise ReportError("multi-host Temporal Validation requires one paired result")
        row = temporal_rows[0]
        lag_series = []
        for index, host in enumerate(("srv-01", "cli-01")):
            value = _finite(row.get(f"{host}_lag_p95_usec"), f"{host} lag p95")
            lag_series.append({"label":host,"color":COLORS[index],"points":{256:{"lag_p95_usec":value,"lag_p95_usec_min":value,"lag_p95_usec_max":value}}})
        body.extend(_panel(x=45,y=800,width=1090,height=280,title="Temporal Validation: host lag p95",x_values=[256],series=lag_series,metric="lag_p95_usec",y_label="Lag p95 (usec)",log_scale=False,error_bars=False))
        height = 1130
        inputs.append(temporal_path)
        temporal_table = {
            "temporal_world_start_difference_usec": row.get(
                "world_time_start_difference_usec"
            ),
            "temporal_world_end_difference_usec": row.get(
                "world_time_end_difference_usec"
            ),
        }
        for host in ("srv-01", "cli-01"):
            for metric in (
                "lag_median_usec",
                "lag_p95_usec",
                "lag_max_usec",
                "acceptance_ratio",
                "accepted_sample_count",
                "rejected_sample_count",
            ):
                temporal_table[f"temporal_{host}_{metric}"] = row.get(
                    f"{host}_{metric}"
                )
        table.append(temporal_table)
    svg = _svg_document(1180,height,"Experiment C — Multi-host Scaling","Mean of paired successful attempts; whiskers show observed min/max",body,[("server / srv-01",COLORS[0]),("client / cli-01",COLORS[1])])
    return svg, inputs, table


def _html_report(title: str, svg: str, table: list[dict[str, Any]], inputs: list[Path]) -> str:
    columns = sorted({key for row in table for key in row})
    header = "".join(f"<th>{_esc(column)}</th>" for column in columns)
    rows = "".join(
        "<tr>" + "".join(f"<td>{_esc(row.get(column, ''))}</td>" for column in columns) + "</tr>"
        for row in table
    )
    sources = "".join(f"<li><code>{_esc(path)}</code></li>" for path in inputs)
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{_esc(title)}</title><style>body{{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;margin:24px;color:#111827}}main{{max-width:1500px;margin:auto}}svg{{max-width:100%;height:auto}}table{{border-collapse:collapse;width:100%;margin-top:24px}}th,td{{border-bottom:1px solid #d1d5db;padding:8px;text-align:right}}th:first-child,td:first-child{{text-align:left}}code{{overflow-wrap:anywhere}}h2{{margin-top:32px}}</style></head>
<body><main>{svg}<h2>Derived values</h2><table><thead><tr>{header}</tr></thead><tbody>{rows}</tbody></table><h2>Inputs</h2><ul>{sources}</ul></main></body></html>
"""


def _render_png(svg_path: Path, png_path: Path) -> str:
    converters = [
        ("rsvg-convert", ["rsvg-convert", str(svg_path), "-o", str(png_path)]),
        ("magick", ["magick", str(svg_path), str(png_path)]),
        ("sips", ["sips", "-s", "format", "png", str(svg_path), "--out", str(png_path)]),
    ]
    for name, command in converters:
        if shutil.which(name) is None:
            continue
        result = subprocess.run(command, text=True, capture_output=True, check=False)
        if result.returncode == 0 and png_path.is_file() and png_path.stat().st_size > 0:
            return name
    raise ReportError("PNG output requires rsvg-convert, ImageMagick, or macOS sips")


def _git_revision() -> str | None:
    result = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True, capture_output=True, check=False)
    return result.stdout.strip() if result.returncode == 0 else None


def render(args: argparse.Namespace) -> int:
    layout = result_layout.load_layout(args.layout)
    if args.experiment in {"experiment-a", "experiment-b"}:
        svg, inputs, table = _comparison_report(
            layout, args.experiment, args.include_temporal
        )
        analysis_id = f"{args.experiment}-host-comparison"
        analysis = layout["analysis"][analysis_id]
        output_directory = ROOT / analysis["output_directory"].format(**layout["roots"])
        stem = analysis["output_stem"]
        default_formats = analysis["formats"]
        title = f"{args.experiment} host comparison"
    else:
        svg, inputs, table = _multi_host_report(layout, args.include_temporal)
        analysis = layout["analysis"]["experiment-c-multi-host-scaling"]
        output_directory = ROOT / analysis["output_directory"].format(**layout["roots"])
        stem = analysis["output_stem"]
        default_formats = analysis["formats"]
        title = "Experiment C multi-host scaling"
    formats = args.formats or default_formats
    if len(set(formats)) != len(formats) or any(value not in {"html", "svg", "png"} for value in formats):
        raise ReportError("formats must be unique html, svg, or png values")
    output_directory.mkdir(parents=True, exist_ok=True)
    svg_path = output_directory / f"{stem}.svg"
    svg_path.write_text(svg, encoding="utf-8")
    output_paths: list[Path] = []
    if "svg" in formats:
        output_paths.append(svg_path)
    html_path = output_directory / f"{stem}.html"
    if "html" in formats:
        html_path.write_text(_html_report(title, svg, table, inputs), encoding="utf-8")
        output_paths.append(html_path)
    converter = None
    if "png" in formats:
        png_path = output_directory / f"{stem}.png"
        converter = _render_png(svg_path, png_path)
        output_paths.append(png_path)
    if "svg" not in formats:
        svg_path.unlink(missing_ok=True)
    experiment_ids = [args.experiment]
    if args.include_temporal:
        experiment_ids.append(
            "experiment-b-temporal"
            if args.experiment == "experiment-b"
            else "experiment-c-temporal"
        )
    experiment_inputs = []
    for experiment_id in experiment_ids:
        experiment_path = result_layout.resolve_experiment_paths(
            layout,
            experiment_id,
            layout["experiments"][experiment_id]["producers"][0],
        )["experiment"]
        experiment_inputs.append(
            {
                "id": experiment_id,
                "path": str(experiment_path),
                "sha256": _sha256(experiment_path),
            }
        )
    manifest_path = output_directory / f"{stem}-manifest.json"
    manifest = {
        "version": 1,
        "experiment": args.experiment,
        "include_temporal": args.include_temporal,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "tool_revision": _git_revision(),
        "layout": {"path": str(args.layout.resolve()), "sha256": _sha256(args.layout.resolve())},
        "experiment_inputs": experiment_inputs,
        "aggregation": {
            "single_host": "median of successful rows passing validation and preflight",
            "multi_host": "matrix summary mean with observed min/max",
            "temporal_is_separate_from_performance": True,
        },
        "inputs": [{"path": str(path), "sha256": _sha256(path)} for path in inputs],
        "outputs": [{"path": str(path), "sha256": _sha256(path)} for path in output_paths],
        "png_converter": converter,
        "derived_values": table,
    }
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    print(f"Report manifest: {manifest_path}")
    for path in output_paths:
        print(f"Report output  : {path}")
    return 0


def parser() -> argparse.ArgumentParser:
    value = argparse.ArgumentParser(description=__doc__)
    value.add_argument("--layout", type=Path, default=result_layout.DEFAULT_LAYOUT)
    commands = value.add_subparsers(dest="command", required=True)
    render_parser = commands.add_parser("render")
    render_parser.add_argument(
        "--experiment",
        choices=["experiment-a", "experiment-b", "experiment-c"],
        required=True,
    )
    render_parser.add_argument("--include-temporal", action="store_true")
    render_parser.add_argument("--formats", nargs="+", choices=["html", "svg", "png"])
    return value


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        if args.command == "render":
            return render(args)
        raise ReportError(f"unsupported command: {args.command}")
    except (ReportError, result_layout.ResultLayoutError, OSError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
