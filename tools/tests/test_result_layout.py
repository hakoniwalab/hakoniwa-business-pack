from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from tools import result_layout


class ResultLayoutTest(unittest.TestCase):
    def setUp(self) -> None:
        self.layout = result_layout.load_layout()

    def test_current_experiment_paths_are_centralized(self) -> None:
        expected = {
            ("experiment-a", "wsl2"): (
                "work/recipes/drone-fleet-single-process-scaling/results/single-process-scaling",
                "exp-results/wsl2/single-process-scaling",
            ),
            ("experiment-b", "wsl2"): (
                "work/recipes/drone-fleet-multi-process-scaling/results/multi-process-scaling",
                "exp-results/wsl2/multi-process-scaling",
            ),
            ("experiment-b-temporal", "wsl2"): (
                "work/recipes/drone-fleet-multi-process-scaling/results/single-host-temporal-validation",
                "exp-results/wsl2/single-host-temporal-validation",
            ),
            ("experiment-c", "cli-01"): (
                "work/recipes/drone-fleet-multi-host-attempt-extension-smoke/results/multi-host-scaling-preflight/hosts/cli-01",
                "work/recipes/drone-fleet-multi-host-attempt-extension-smoke/results/multi-host-scaling-preflight/hosts/cli-01",
            ),
            ("experiment-c-temporal", "cli-01"): (
                "work/recipes/drone-fleet-multi-host-temporal-smoke/results/multi-host-temporal-validation/hosts/cli-01",
                "work/recipes/drone-fleet-multi-host-temporal-smoke/results/multi-host-temporal-validation/hosts/cli-01",
            ),
        }
        for identity, paths in expected.items():
            resolved = result_layout.resolve_experiment_paths(self.layout, *identity)
            self.assertEqual(
                resolved["source"].relative_to(result_layout.ROOT).as_posix(),
                paths[0],
            )
            self.assertEqual(
                resolved["destination"].relative_to(result_layout.ROOT).as_posix(),
                paths[1],
            )

    def test_every_declared_producer_resolves_inside_repository(self) -> None:
        for experiment_id, experiment in self.layout["experiments"].items():
            for participant in experiment["producers"]:
                resolved = result_layout.resolve_experiment_paths(
                    self.layout, experiment_id, participant
                )
                for field in ("workspace", "source", "destination"):
                    resolved[field].relative_to(result_layout.ROOT)

    def test_every_analysis_output_is_centrally_resolved_inside_repository(self) -> None:
        for analysis in self.layout["analysis"].values():
            output = analysis["output_directory"].format(**self.layout["roots"])
            (result_layout.ROOT / output).resolve().relative_to(result_layout.ROOT)

    def test_transfer_groups_pair_performance_and_temporal_series(self) -> None:
        self.assertEqual(
            self.layout["transfer_groups"]["experiment-b"]["experiments"],
            ["experiment-b", "experiment-b-temporal"],
        )
        self.assertEqual(
            self.layout["transfer_groups"]["experiment-c"]["experiments"],
            ["experiment-c", "experiment-c-temporal"],
        )

    def test_unknown_or_wrong_producer_is_rejected(self) -> None:
        with self.assertRaisesRegex(result_layout.ResultLayoutError, "not a producer"):
            result_layout.resolve_experiment_paths(
                self.layout, "experiment-a", "cli-01"
            )

    def test_series_drift_from_experiment_is_rejected(self) -> None:
        source = result_layout.DEFAULT_LAYOUT.read_text(encoding="utf-8")
        changed = source.replace(
            "    series: multi-process-scaling\n",
            "    series: wrong-series\n",
            1,
        )
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "layout.yaml"
            path.write_text(changed, encoding="utf-8")
            with self.assertRaisesRegex(
                result_layout.ResultLayoutError, "series disagrees"
            ):
                result_layout.load_layout(path)


if __name__ == "__main__":
    unittest.main()
