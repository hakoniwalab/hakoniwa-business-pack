from __future__ import annotations

import importlib.util
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path


SCRIPT = Path(__file__).with_name("drone_fleet_performance_plot.py")
SPEC = importlib.util.spec_from_file_location("drone_fleet_performance_plot_test", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
plot = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = plot
SPEC.loader.exec_module(plot)


def row(drone_count: int, rtf: float, *, valid: bool = True) -> dict:
    return {
        "drone_count": drone_count,
        "process_count": 1,
        "status": "success" if valid else "invalid",
        "validation_passed": valid,
        "preflight_passed": valid,
        "protocol_status": "preflight",
        "rtf": rtf,
        "average_step_wall_clock_sec": 0.001 / rtf,
        "preflight_cpu_average_percent": 10.0,
        "cpu_average_percent": 20.0 + drone_count,
        "cpu_max_percent": 30.0 + drone_count,
        "preflight_memory_used_average_percent": 70.0,
        "memory_used_average_percent": 71.0,
        "memory_used_max_percent": 72.0,
    }


class DroneFleetPerformancePlotTest(unittest.TestCase):
    def test_aggregate_averages_repetitions_and_excludes_invalid_rows(self) -> None:
        rows, rejected = plot.aggregate(
            [row(1, 10.0), row(1, 20.0), row(2, 5.0, valid=False)],
            "drone_count",
        )
        self.assertEqual(rejected, 1)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["attempt_count"], 2)
        self.assertEqual(rows[0]["rtf"], 15.0)
        self.assertEqual(rows[0]["rtf_min"], 10.0)
        self.assertEqual(rows[0]["rtf_max"], 20.0)
        self.assertEqual(rows[0]["protocol_status"], "preflight")

    def test_combined_svg_is_well_formed_and_contains_all_panels(self) -> None:
        rows, rejected = plot.aggregate([row(1, 20.0), row(128, 0.5)], "drone_count")
        svg = plot.render_svg(rows, "drone_count", rejected)
        root = ET.fromstring(svg)
        self.assertTrue(root.tag.endswith("svg"))
        self.assertNotIn("class-=", svg)
        self.assertIn("Real Time Factor", svg)
        self.assertIn("Average Step Execution Time", svg)
        self.assertIn("Whole-machine CPU", svg)
        self.assertIn("Whole-machine Memory", svg)
        self.assertIn("RTF = 1", svg)

    def test_process_count_plot_uses_multi_process_title(self) -> None:
        payload = row(128, 2.0)
        payload["process_count"] = 4
        rows, rejected = plot.aggregate([payload], "process_count")
        svg = plot.render_svg(rows, "process_count", rejected)
        self.assertIn("Drone Fleet Multi-process Scaling", svg)
        self.assertNotIn("Drone Fleet Single-process Scaling", svg)

    def test_multi_workload_process_plot_requires_drone_count_selection(self) -> None:
        rows = [row(32, 2.0), row(64, 1.0)]
        with self.assertRaisesRegex(plot.PlotError, "--drone-count"):
            plot.select_workload(rows, "process_count", None)
        selected = plot.select_workload(rows, "process_count", 64)
        self.assertEqual([item["drone_count"] for item in selected], [64])

    def test_main_writes_default_plot_beside_summary(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            summary = root / "experiment-a.json"
            summary.write_text(
                '{"results": ['
                + __import__("json").dumps(row(1, 3.0))
                + "]}",
                encoding="utf-8",
            )
            rc = plot.main([str(summary)])
            output = root / "plots" / "scaling-overview.svg"
            self.assertTrue(output.is_file())
        self.assertEqual(rc, 0)

    def test_main_rejects_non_svg_output_extension(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            summary = root / "experiment-b.json"
            summary.write_text(
                '{"results": [' + __import__("json").dumps(row(128, 2.0)) + "]}",
                encoding="utf-8",
            )
            rc = plot.main(
                [
                    str(summary),
                    "--x-field",
                    "process_count",
                    "--output",
                    str(root / "experiment-b.png"),
                ]
            )
            self.assertFalse((root / "experiment-b.png").exists())
        self.assertEqual(rc, 2)


if __name__ == "__main__":
    unittest.main()
