from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tools.recipe import drone_fleet_performance_collect as collector


class DroneFleetPerformanceCollectTest(unittest.TestCase):
    def test_collect_all_runs_local_publication_and_verifies_eight_datasets(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            layout_path = root / "layout.yaml"
            layout_path.write_text("version: 1\n", encoding="utf-8")
            manifest_path = root / "exp-results" / "collection-manifest.json"

            def publication(
                _layout_path: Path,
                kind: str,
                identity: str,
                producer: str,
                _temporary: Path,
                _max_uncompressed_bytes: int,
            ) -> dict:
                result = {
                    "kind": kind,
                    "identity": identity,
                    "producer_id": producer,
                    "manifest": {"identity": identity},
                }
                if kind == "experiment":
                    result["publication"] = {"status": "published"}
                else:
                    result["publication"] = {
                        "datasets": [
                            {
                                "experiment_id": identity,
                                "status": "published",
                            }
                        ]
                    }
                return result

            def verified(_layout: dict, experiment_id: str, producer: str) -> dict:
                return {
                    "experiment_id": experiment_id,
                    "producer_id": producer,
                    "series": "series",
                    "destination": f"exp-results/{experiment_id}/{producer}",
                    "result_count": 1,
                    "file_count": 1,
                    "size_bytes": 1,
                    "tree_sha256": "0" * 64,
                }

            with (
                mock.patch.object(collector, "ROOT", root),
                mock.patch.object(
                    collector.result_layout, "load_layout", return_value={}
                ),
                mock.patch.object(
                    collector, "_collect_local", side_effect=publication
                ) as collect_local,
                mock.patch.object(
                    collector, "_validate_canonical", side_effect=verified
                ) as validate_canonical,
            ):
                result = collector.collect_all(
                    layout_path=layout_path,
                    runtime=root / "runtime",
                    manifest_path=manifest_path,
                    max_uncompressed_bytes=1024,
                )

            saved = json.loads(manifest_path.read_text(encoding="utf-8"))
            self.assertEqual(result["status"], "complete")
            self.assertEqual(len(result["operations"]), 3)
            self.assertEqual(len(result["datasets"]), 8)
            self.assertEqual(saved["status"], "complete")
            self.assertEqual(collect_local.call_count, 3)
            self.assertEqual(validate_canonical.call_count, 8)

    def test_tree_identity_rejects_symlink(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            source = root / "results"
            source.mkdir()
            target = root / "target.txt"
            target.write_text("data\n", encoding="utf-8")
            (source / "link.txt").symlink_to(target)
            with self.assertRaisesRegex(
                collector.PerformanceCollectError, "symlink"
            ):
                collector._tree_identity(source)


if __name__ == "__main__":
    unittest.main()
