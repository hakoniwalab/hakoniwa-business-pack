#!/usr/bin/env python3
"""Render a dependency-free scaling overview from a performance summary."""

from __future__ import annotations

import argparse
import html
import json
import math
import sys
from collections import defaultdict
from pathlib import Path
from statistics import mean
from typing import Any, Callable


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_SUMMARY = (
    ROOT
    / "work"
    / "recipes"
    / "drone-fleet-single-process-scaling"
    / "results"
    / "single-process-scaling"
    / "summary"
    / "experiment-a.json"
)
WIDTH = 1440
HEIGHT = 940


class PlotError(RuntimeError):
    pass


def finite_number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    result = float(value)
    return result if math.isfinite(result) else None


def load_summary(path: Path) -> list[dict[str, Any]]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise PlotError(f"cannot read summary: {path}: {exc}") from exc
    rows = payload.get("results") if isinstance(payload, dict) else None
    if not isinstance(rows, list) or not rows:
        raise PlotError(f"summary has no results: {path}")
    valid: list[dict[str, Any]] = []
    for index, row in enumerate(rows):
        if not isinstance(row, dict):
            raise PlotError(f"summary result {index} must be an object")
        valid.append(row)
    return valid


def aggregate(
    rows: list[dict[str, Any]], x_field: str
) -> tuple[list[dict[str, Any]], int]:
    groups: dict[float, list[dict[str, Any]]] = defaultdict(list)
    rejected = 0
    for row in rows:
        x = finite_number(row.get(x_field))
        if (
            x is None
            or row.get("status") != "success"
            or row.get("validation_passed") is not True
            or row.get("preflight_passed") is not True
        ):
            rejected += 1
            continue
        groups[x].append(row)
    if not groups:
        raise PlotError("summary contains no successful, validated results")

    fields = (
        "rtf",
        "average_step_wall_clock_sec",
        "preflight_cpu_average_percent",
        "cpu_average_percent",
        "cpu_max_percent",
        "preflight_memory_used_average_percent",
        "memory_used_average_percent",
        "memory_used_max_percent",
    )
    aggregated: list[dict[str, Any]] = []
    for x in sorted(groups):
        protocol_values = sorted(
            {
                str(row.get("protocol_status"))
                for row in groups[x]
                if row.get("protocol_status") is not None
            }
        )
        item: dict[str, Any] = {
            x_field: x,
            "attempt_count": len(groups[x]),
            "protocol_status": ",".join(protocol_values) if protocol_values else None,
        }
        for field in fields:
            values = [
                number
                for row in groups[x]
                if (number := finite_number(row.get(field))) is not None
            ]
            item[field] = mean(values) if values else None
            item[f"{field}_min"] = min(values) if values else None
            item[f"{field}_max"] = max(values) if values else None
        aggregated.append(item)
    return aggregated, rejected


def select_workload(
    rows: list[dict[str, Any]], x_field: str, drone_count: int | None
) -> list[dict[str, Any]]:
    if drone_count is not None:
        selected = [row for row in rows if row.get("drone_count") == drone_count]
        if not selected:
            raise PlotError(f"summary has no results for drone_count={drone_count}")
        return selected
    if x_field == "process_count":
        drone_counts = sorted(
            {
                int(value)
                for row in rows
                if (value := finite_number(row.get("drone_count"))) is not None
            }
        )
        if len(drone_counts) > 1:
            raise PlotError(
                "process-count plots require --drone-count when the summary "
                f"contains multiple UAV workloads: {drone_counts}"
            )
    return rows


def esc(value: Any) -> str:
    return html.escape(str(value), quote=True)


def line(x1: float, y1: float, x2: float, y2: float, **attrs: Any) -> str:
    properties = " ".join(
        f'{key.rstrip("_").replace("_", "-")}="{esc(value)}"'
        for key, value in attrs.items()
    )
    return f'<line x1="{x1:.2f}" y1="{y1:.2f}" x2="{x2:.2f}" y2="{y2:.2f}" {properties}/>'


def text(x: float, y: float, value: Any, **attrs: Any) -> str:
    properties = " ".join(
        f'{key.rstrip("_").replace("_", "-")}="{esc(item)}"'
        for key, item in attrs.items()
    )
    return f'<text x="{x:.2f}" y="{y:.2f}" {properties}>{esc(value)}</text>'


def circle(x: float, y: float, **attrs: Any) -> str:
    properties = " ".join(
        f'{key.rstrip("_").replace("_", "-")}="{esc(item)}"'
        for key, item in attrs.items()
    )
    return f'<circle cx="{x:.2f}" cy="{y:.2f}" {properties}/>'


def nice_linear_bounds(values: list[float], *, floor_zero: bool = False) -> tuple[float, float]:
    low = min(values)
    high = max(values)
    if floor_zero:
        low = 0.0
    if math.isclose(low, high):
        padding = max(abs(low) * 0.1, 1.0)
    else:
        padding = (high - low) * 0.12
    return (max(0.0, low - padding) if floor_zero else low - padding, high + padding)


def linear_ticks(low: float, high: float, count: int = 5) -> list[float]:
    return [low + (high - low) * index / (count - 1) for index in range(count)]


def log_ticks(low: float, high: float) -> list[float]:
    first = math.floor(math.log10(low))
    last = math.ceil(math.log10(high))
    candidates = [10.0**power for power in range(first, last + 1)]
    if len(candidates) < 3:
        middle = math.sqrt(low * high)
        candidates = [low, middle, high]
    return sorted({value for value in candidates if low <= value <= high} | {low, high})


def format_tick(value: float, kind: str) -> str:
    if kind == "rtf":
        return f"{value:.2g}×"
    if kind == "milliseconds":
        return f"{value:.3g}"
    if kind == "percent":
        return f"{value:.0f}%"
    return f"{value:.2f}"


def render_panel(
    rows: list[dict[str, Any]],
    *,
    x_field: str,
    x_label: str,
    title_value: str,
    y_label: str,
    series: list[tuple[str, str, str]],
    box: tuple[float, float, float, float],
    scale: str,
    tick_kind: str,
    transform: Callable[[float], float] = lambda value: value,
    reference: float | None = None,
    floor_zero: bool = False,
) -> list[str]:
    left, top, width, height = box
    margin_left, margin_right, margin_top, margin_bottom = 82, 24, 48, 66
    plot_left = left + margin_left
    plot_top = top + margin_top
    plot_width = width - margin_left - margin_right
    plot_height = height - margin_top - margin_bottom
    x_values = [float(row[x_field]) for row in rows]
    x_positions = {
        value: plot_left + index * plot_width / max(1, len(x_values) - 1)
        for index, value in enumerate(x_values)
    }
    numeric: list[float] = []
    for field, _label, _color in series:
        for row in rows:
            value = finite_number(row.get(field))
            if value is not None:
                transformed = transform(value)
                if scale != "log" or transformed > 0:
                    numeric.append(transformed)
    if reference is not None:
        numeric.append(reference)
    if not numeric:
        raise PlotError(f"panel has no numeric data: {title_value}")
    if scale == "log":
        positive = [value for value in numeric if value > 0]
        low = min(positive) / 1.18
        high = max(positive) * 1.18
        to_y = lambda value: plot_top + plot_height * (
            math.log10(high) - math.log10(value)
        ) / (math.log10(high) - math.log10(low))
        ticks = log_ticks(low, high)
    else:
        low, high = nice_linear_bounds(numeric, floor_zero=floor_zero)
        to_y = lambda value: plot_top + plot_height * (high - value) / (high - low)
        ticks = linear_ticks(low, high)

    output = [
        f'<g class="panel" aria-label="{esc(title_value)}">',
        text(left + width / 2, top + 22, title_value, text_anchor="middle", class_="panel-title"),
    ]
    for tick in ticks:
        y = to_y(tick)
        output.append(line(plot_left, y, plot_left + plot_width, y, class_="grid"))
        output.append(text(plot_left - 10, y + 4, format_tick(tick, tick_kind), text_anchor="end", class_="tick"))
    output.extend(
        [
            line(plot_left, plot_top, plot_left, plot_top + plot_height, class_="axis"),
            line(plot_left, plot_top + plot_height, plot_left + plot_width, plot_top + plot_height, class_="axis"),
        ]
    )
    for value in x_values:
        x = x_positions[value]
        output.append(line(x, plot_top + plot_height, x, plot_top + plot_height + 6, class_="axis"))
        label = str(int(value)) if value.is_integer() else f"{value:g}"
        output.append(text(x, plot_top + plot_height + 23, label, text_anchor="middle", class_="tick"))
    output.append(text(plot_left + plot_width / 2, top + height - 10, x_label, text_anchor="middle", class_="axis-label"))
    output.append(
        text(
            left + 16,
            plot_top + plot_height / 2,
            y_label,
            text_anchor="middle",
            class_="axis-label",
            transform=f"rotate(-90 {left + 16:.2f} {plot_top + plot_height / 2:.2f})",
        )
    )
    if reference is not None and low <= reference <= high:
        y = to_y(reference)
        output.append(line(plot_left, y, plot_left + plot_width, y, class_="reference"))
        output.append(text(plot_left + plot_width - 4, y - 6, "RTF = 1", text_anchor="end", class_="reference-label"))

    legend_x = plot_left + 6
    for field, label, color in series:
        points: list[tuple[float, float, float]] = []
        for row in rows:
            value = finite_number(row.get(field))
            if value is None:
                continue
            transformed = transform(value)
            if scale == "log" and transformed <= 0:
                continue
            points.append((x_positions[float(row[x_field])], to_y(transformed), transformed))
        if len(points) >= 2:
            output.append(
                '<polyline fill="none" '
                f'stroke="{color}" class="series-line" points="'
                + " ".join(f"{x:.2f},{y:.2f}" for x, y, _value in points)
                + '"/>'
            )
        for x, y, value in points:
            output.append(circle(x, y, r="4.5", fill=color, class_="point"))
            output.append(f'<title>{esc(label)}: {esc(format_tick(value, tick_kind))}</title>')
        output.append(line(legend_x, plot_top + 10, legend_x + 22, plot_top + 10, stroke=color, class_="series-line"))
        output.append(circle(legend_x + 11, plot_top + 10, r="3.5", fill=color))
        output.append(text(legend_x + 29, plot_top + 14, label, class_="legend"))
        legend_x += 34 + len(label) * 7.2
    output.append("</g>")
    return output


def render_svg(rows: list[dict[str, Any]], x_field: str, rejected: int) -> str:
    x_label = "UAV count" if x_field == "drone_count" else "Simulator process count"
    protocols = sorted(
        {
            str(row.get("protocol_status"))
            for row in rows
            if row.get("protocol_status") is not None
        }
    )
    subtitle = f"Valid configurations: {len(rows)}"
    if protocols:
        subtitle += " · Protocol: " + ", ".join(protocols)
    if rejected:
        subtitle += f" · Excluded invalid results: {rejected}"
    gap = 26
    outer = 32
    top_header = 82
    panel_width = (WIDTH - outer * 2 - gap) / 2
    panel_height = (HEIGHT - top_header - outer - gap) / 2
    boxes = [
        (outer, top_header, panel_width, panel_height),
        (outer + panel_width + gap, top_header, panel_width, panel_height),
        (outer, top_header + panel_height + gap, panel_width, panel_height),
        (outer + panel_width + gap, top_header + panel_height + gap, panel_width, panel_height),
    ]
    main_title = (
        "Drone Fleet Multi-process Scaling"
        if x_field == "process_count"
        else "Drone Fleet Single-process Scaling"
    )
    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{WIDTH}" height="{HEIGHT}" viewBox="0 0 {WIDTH} {HEIGHT}" role="img" aria-labelledby="title desc">',
        '<title id="title">Drone Fleet scaling overview</title>',
        '<desc id="desc">RTF, average simulation step time, whole-machine CPU, and whole-machine memory by scaling condition.</desc>',
        """<style>
        .background { fill: #ffffff; }
        text { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; fill: #1f2937; }
        .main-title { font-size: 24px; font-weight: 600; }
        .subtitle { font-size: 13px; fill: #4b5563; }
        .panel-title { font-size: 17px; font-weight: 600; }
        .axis { stroke: #4b5563; stroke-width: 1; }
        .grid { stroke: #d1d5db; stroke-width: 1; }
        .tick, .legend { font-size: 11px; fill: #374151; }
        .axis-label { font-size: 12px; fill: #374151; }
        .series-line { stroke-width: 2.2; stroke-linejoin: round; stroke-linecap: round; }
        .point { stroke: #ffffff; stroke-width: 1.2; }
        .reference { stroke: #6b7280; stroke-width: 1.4; stroke-dasharray: 6 5; }
        .reference-label { font-size: 11px; fill: #4b5563; }
        </style>""",
        f'<rect class="background" width="{WIDTH}" height="{HEIGHT}"/>',
        text(WIDTH / 2, 32, main_title, text_anchor="middle", class_="main-title"),
        text(WIDTH / 2, 56, subtitle, text_anchor="middle", class_="subtitle"),
    ]
    parts += render_panel(
        rows,
        x_field=x_field,
        x_label=x_label,
        title_value="Real Time Factor",
        y_label="RTF (virtual / wall)",
        series=[("rtf", "RTF", "#2563eb")],
        box=boxes[0],
        scale="log",
        tick_kind="rtf",
        reference=1.0,
    )
    parts += render_panel(
        rows,
        x_field=x_field,
        x_label=x_label,
        title_value="Average Step Execution Time",
        y_label="Wall time per step (ms)",
        series=[("average_step_wall_clock_sec", "Average", "#7c3aed")],
        box=boxes[1],
        scale="log",
        tick_kind="milliseconds",
        transform=lambda value: value * 1000.0,
    )
    parts += render_panel(
        rows,
        x_field=x_field,
        x_label=x_label,
        title_value="Whole-machine CPU",
        y_label="CPU utilization (%)",
        series=[
            ("preflight_cpu_average_percent", "Preflight avg", "#6b7280"),
            ("cpu_average_percent", "Measurement avg", "#059669"),
            ("cpu_max_percent", "Measurement max", "#dc2626"),
        ],
        box=boxes[2],
        scale="linear",
        tick_kind="percent",
        floor_zero=True,
    )
    parts += render_panel(
        rows,
        x_field=x_field,
        x_label=x_label,
        title_value="Whole-machine Memory",
        y_label="Memory used (%)",
        series=[
            ("preflight_memory_used_average_percent", "Preflight avg", "#6b7280"),
            ("memory_used_average_percent", "Measurement avg", "#d97706"),
            ("memory_used_max_percent", "Measurement max", "#dc2626"),
        ],
        box=boxes[3],
        scale="linear",
        tick_kind="percent",
    )
    parts.append("</svg>")
    return "\n".join(parts) + "\n"


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description="Render Drone Fleet performance plots")
    result.add_argument("summary", type=Path, nargs="?", default=DEFAULT_SUMMARY)
    result.add_argument("--output", type=Path)
    result.add_argument(
        "--x-field",
        choices=["drone_count", "process_count"],
        default="drone_count",
    )
    result.add_argument(
        "--drone-count",
        type=int,
        help="select one UAV workload from a multi-workload Experiment B summary",
    )
    return result


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        summary = args.summary.resolve()
        selected_rows = select_workload(
            load_summary(summary), args.x_field, args.drone_count
        )
        rows, rejected = aggregate(selected_rows, args.x_field)
        output = (
            args.output.resolve()
            if args.output is not None
            else summary.parent / "plots" / "scaling-overview.svg"
        )
        if output.suffix.lower() != ".svg":
            raise PlotError(
                f"plot output must use the .svg extension; the renderer writes SVG: {output}"
            )
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(render_svg(rows, args.x_field, rejected), encoding="utf-8")
        print(f"Scaling overview: {output}")
        print(f"Configurations : {len(rows)}")
        if rejected:
            print(f"Excluded invalid results: {rejected}")
        return 0
    except PlotError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
