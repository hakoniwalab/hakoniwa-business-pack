from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET
from argparse import Namespace
from pathlib import Path
from unittest import mock


SCRIPT = Path(__file__).with_name("drone_fleet_performance_paper.py")
SPEC = importlib.util.spec_from_file_location("drone_fleet_performance_paper_test", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
paper = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = paper
SPEC.loader.exec_module(paper)


def performance_row(uav: int, process: int, step_msec: float) -> dict:
    return {
        "drone_count": uav,
        "process_count": process,
        "status": "success",
        "validation_passed": True,
        "preflight_passed": True,
        "average_step_wall_clock_sec": step_msec / 1000.0,
        "rtf": 1.0 / step_msec,
        "cpu_average_percent": 50.0,
        "memory_used_average_bytes": 4 * 2**30,
        "memory_used_average_percent": 25.0,
    }


class DroneFleetPerformancePaperTest(unittest.TestCase):
    def test_step_point_uses_median_and_observed_range(self) -> None:
        point = paper._step_point(
            [
                performance_row(64, 4, 0.5),
                performance_row(64, 4, 0.7),
                performance_row(64, 4, 2.0),
            ]
        )
        self.assertAlmostEqual(point["step_msec"], 0.7)
        self.assertAlmostEqual(point["step_msec_min"], 0.5)
        self.assertAlmostEqual(point["step_msec_max"], 2.0)
        self.assertEqual(point["attempt_count"], 3)

    def test_multi_host_points_use_median_not_matrix_mean(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "matrix.json"
            path.write_text(
                json.dumps(
                    {
                        "complete": True,
                        "conditions": [
                            {
                                "drone_count": 64,
                                "extension_decision": {"required": True},
                                "statistics": [
                                    {
                                        "rtf": {
                                            "mean": 4.0,
                                            "values": [1.0, 2.0, 9.0],
                                        }
                                    }
                                ],
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            points, rows = paper._c_points(path)
        self.assertAlmostEqual(points[64]["rtf"], 2.0)
        self.assertAlmostEqual(points[64]["step_msec"], 0.5)
        self.assertTrue(rows[0]["extension_required"])

    def test_multi_host_resource_points_use_per_host_medians(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for host, values in (
                ("srv-01", [(60.0, 8), (80.0, 10), (70.0, 9)]),
                ("cli-01", [(40.0, 3), (50.0, 5), (45.0, 4)]),
            ):
                for attempt, (cpu, gib) in enumerate(values, 1):
                    path = (
                        root
                        / "hosts"
                        / host
                        / "uav-064-sleep-001ms"
                        / f"attempt-{attempt:02d}"
                        / "result.json"
                    )
                    path.parent.mkdir(parents=True)
                    path.write_text(
                        json.dumps(
                            {
                                "status": "success",
                                "validation": {"passed": True},
                                "machine": {
                                    "cpu_average_percent": cpu,
                                    "memory_used_average_bytes": gib * 2**30,
                                    "memory_used_average_percent": float(gib),
                                },
                            }
                        ),
                        encoding="utf-8",
                    )
            resources, inputs = paper._c_resource_points(
                root, [{"total_uav": 64, "attempt_count": 3}]
            )
        self.assertEqual(len(inputs), 6)
        self.assertEqual(resources[64]["srv-01"]["cpu_average_percent"], 70.0)
        self.assertEqual(resources[64]["srv-01"]["memory_used_average_bytes"], 9 * 2**30)
        self.assertEqual(resources[64]["cli-01"]["cpu_average_percent"], 45.0)

    def test_template_rejects_unresolved_tokens(self) -> None:
        with self.assertRaises(paper.PaperReportError):
            paper._render_template("{{known}} {{unknown}}", {"known": "value"})

    def test_paper_figures_are_valid_svg(self) -> None:
        a_points = {
            "mac": {1: {"step_msec": 0.1, "step_msec_min": 0.1, "step_msec_max": 0.1}},
            "wsl2": {1: {"step_msec": 0.4, "step_msec_min": 0.4, "step_msec_max": 0.4}},
        }
        b_points = {
            host: {
                workload: {
                    process: {
                        "step_msec": step * factor,
                        "step_msec_min": step * factor * 0.9,
                        "step_msec_max": step * factor * 1.1,
                    }
                    for process, step in ((1, 2.0), (6, 0.5), (12, 0.4))
                }
                for workload, factor in ((32, 1.0), (64, 2.0), (128, 4.0))
            }
            for host in ("mac", "wsl2")
        }
        c_points = {
            total: {"step_msec": step, "step_msec_min": step * 0.9, "step_msec_max": step * 1.1}
            for total, step in ((64, 0.4), (128, 0.8), (256, 1.6))
        }
        for svg in (
            paper._figure_a(["mac", "wsl2"], a_points),
            paper._figure_b(["mac", "wsl2"], b_points),
            paper._figure_c(c_points, b_points),
        ):
            ET.fromstring(svg)


if __name__ == "__main__":
    unittest.main()
