from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path
from unittest import mock


SCRIPT = Path(__file__).with_name("drone_fleet_performance_report.py")
SPEC = importlib.util.spec_from_file_location("drone_fleet_performance_report_test", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
report = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = report
SPEC.loader.exec_module(report)


def performance_row(drone_count: int, process_count: int, rtf: float) -> dict:
    return {
        "drone_count": drone_count,
        "process_count": process_count,
        "status": "success",
        "validation_passed": True,
        "preflight_passed": True,
        "rtf": rtf,
        "cpu_average_percent": 50.0,
    }


class DroneFleetPerformanceReportTest(unittest.TestCase):
    def test_aggregate_uses_median_and_observed_range(self) -> None:
        aggregated = report._aggregate_rows(
            [
                performance_row(64, 4, 1.0),
                performance_row(64, 4, 3.0),
                performance_row(64, 4, 20.0),
            ],
            "process_count",
            "drone_count",
        )
        point = aggregated[64][4]
        self.assertEqual(point["rtf"], 3.0)
        self.assertEqual(point["rtf_min"], 1.0)
        self.assertEqual(point["rtf_max"], 20.0)

    def test_comparison_report_reads_both_declared_producers(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            paths = []
            for producer, factor in (("mac", 2.0), ("wsl2", 1.0)):
                path = root / f"{producer}.json"
                path.write_text(
                    json.dumps(
                        {
                            "results": [
                                performance_row(32, 1, factor),
                                performance_row(32, 2, factor * 1.5),
                                performance_row(64, 1, factor / 2),
                                performance_row(64, 2, factor),
                                performance_row(128, 1, factor / 4),
                                performance_row(128, 2, factor / 2),
                            ]
                        }
                    ),
                    encoding="utf-8",
                )
                paths.append(path)
            layout = {"experiments": {"experiment-b": {"producers": ["mac", "wsl2"]}}}
            with mock.patch.object(
                report, "_single_host_summary_path", side_effect=paths
            ):
                svg, inputs, table = report._comparison_report(layout, "experiment-b")
        ET.fromstring(svg)
        self.assertEqual(inputs, paths)
        self.assertEqual(len(table), 6)
        self.assertIn("32 UAV", svg)
        self.assertIn("MAC", svg)
        self.assertIn("WSL2", svg)

    def test_multi_host_report_includes_separate_temporal_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            scaling = root / "scaling"
            temporal = root / "temporal"
            (scaling / "summary").mkdir(parents=True)
            (temporal / "summary").mkdir(parents=True)
            conditions = []
            for count, rtf in ((64, 2.8), (128, 1.4), (256, 0.7)):
                stats = {
                    "attempt_count": 3,
                    "rtf": {"mean": rtf, "min": rtf * 0.9, "max": rtf * 1.1},
                }
                for host, cpu in (("srv-01", 60.0), ("cli-01", 65.0)):
                    stats[f"{host}_cpu_average_percent"] = {
                        "mean": cpu,
                        "min": cpu - 1,
                        "max": cpu + 1,
                    }
                conditions.append(
                    {
                        "drone_count": count,
                        "statistics": [stats],
                        "extension_decision": {"required": count == 128},
                    }
                )
            matrix = scaling / "summary" / "multi-host-scaling-sleep-001ms-matrix.json"
            matrix.write_text(
                json.dumps({"complete": True, "conditions": conditions}),
                encoding="utf-8",
            )
            temporal_summary = temporal / "summary" / "multi-host-temporal-sleep-001ms-uav-256.json"
            temporal_summary.write_text(
                json.dumps(
                    {
                        "complete": True,
                        "results": [
                            {
                                "srv-01_lag_p95_usec": 20000,
                                "cli-01_lag_p95_usec": 20000,
                                "world_time_start_difference_usec": 0,
                                "world_time_end_difference_usec": 20000,
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            with mock.patch.object(
                report,
                "_series_root",
                side_effect=lambda _layout, experiment, _producer: (
                    scaling if experiment == "experiment-c" else temporal
                ),
            ):
                svg, inputs, table = report._multi_host_report({}, True)
        ET.fromstring(svg)
        self.assertEqual(inputs, [matrix, temporal_summary])
        self.assertEqual(len(table), 4)
        self.assertIn("Temporal Validation", svg)
        self.assertIn("Multi-host scale-out", svg)

    def test_b_report_plots_temporal_endpoints_without_merging_rtf(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            paths = []
            for producer in ("mac", "wsl2"):
                performance = root / f"{producer}-performance.json"
                performance.write_text(
                    json.dumps(
                        {
                            "results": [
                                performance_row(workload, process, rtf)
                                for workload in (32, 64, 128)
                                for process, rtf in ((1, 0.8), (2, 1.2))
                            ]
                        }
                    ),
                    encoding="utf-8",
                )
                paths.append(performance)
            for producer in ("mac", "wsl2"):
                temporal = root / f"{producer}-temporal.json"
                temporal.write_text(
                    json.dumps(
                        {
                            "complete": True,
                            "results": [
                                {
                                    "process_count": process,
                                    "status": "success",
                                    "lag_median_usec": 20000,
                                    "lag_p95_usec": 20000,
                                    "lag_max_usec": 20000,
                                    "acceptance_ratio": 1.0,
                                }
                                for process in (2, 15)
                            ],
                        }
                    ),
                    encoding="utf-8",
                )
                paths.append(temporal)
            layout = {
                "experiments": {
                    "experiment-b": {"producers": ["mac", "wsl2"]}
                }
            }
            with mock.patch.object(
                report, "_single_host_summary_path", side_effect=paths
            ):
                svg, inputs, table = report._comparison_report(
                    layout, "experiment-b", True
                )
        ET.fromstring(svg)
        self.assertEqual(inputs, paths)
        self.assertEqual(len([row for row in table if "temporal_producer" in row]), 4)
        self.assertIn("lag p95 at process endpoints", svg)


if __name__ == "__main__":
    unittest.main()
