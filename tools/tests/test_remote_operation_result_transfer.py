from __future__ import annotations

import json
import tempfile
import unittest
import zipfile
from contextlib import ExitStack
from pathlib import Path
from unittest import mock

from tools.remote_operation import result_transfer


class ResultTransferTest(unittest.TestCase):
    def fixture(self, root: Path):
        layout_path = root / "configs" / "layout.yaml"
        experiment_path = root / "recipes" / "experiment.yaml"
        source = root / "work" / "results" / "multi-process-scaling"
        destination = root / "exp-results" / "wsl2" / "multi-process-scaling"
        layout_path.parent.mkdir(parents=True)
        experiment_path.parent.mkdir(parents=True)
        layout_path.write_text("version: 1\n", encoding="utf-8")
        experiment_path.write_text("version: 1\n", encoding="utf-8")
        attempt = source / "uav-128-proc-12" / "attempt-01"
        attempt.mkdir(parents=True)
        (attempt / "result.json").write_text(
            json.dumps(
                {
                    "status": "success",
                    "metadata": {
                        "series": "multi-process-scaling",
                        "configuration_id": "uav-128-proc-12",
                        "attempt": 1,
                    },
                }
            ),
            encoding="utf-8",
        )
        summary = source / "summary"
        summary.mkdir()
        (summary / "experiment-b.json").write_text(
            '{"complete":true,"results":[]}\n', encoding="utf-8"
        )
        layout = {
            "experiments": {
                "experiment-b": {"producers": ["mac", "wsl2"]}
            }
        }
        resolved = {
            "experiment": experiment_path,
            "source": source,
            "destination": destination,
            "series": "multi-process-scaling",
            "participant_scope": "machine",
        }
        return layout_path, layout, resolved, source, destination

    def patches(self, root: Path, layout: dict, resolved: dict) -> ExitStack:
        stack = ExitStack()
        stack.enter_context(mock.patch.object(result_transfer, "ROOT", root.resolve()))
        stack.enter_context(
            mock.patch.object(
                result_transfer.result_layout,
                "load_layout",
                return_value=layout,
            )
        )
        stack.enter_context(
            mock.patch.object(
                result_transfer.result_layout,
                "resolve_experiment_paths",
                return_value=resolved,
            )
        )
        return stack

    def test_package_manifest_and_atomic_publication(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            layout_path, layout, resolved, _source, destination = self.fixture(root)
            archive = root / "runtime" / "result.zip"
            with self.patches(root, layout, resolved):
                package, manifest = result_transfer.create_package(
                    layout_path, "experiment-b", "wsl2", archive
                )
                publication = result_transfer.publish_package(
                    package,
                    layout_path=layout_path,
                    experiment_id="experiment-b",
                    producer="wsl2",
                    staging=root / "staging",
                    max_uncompressed_bytes=1024 * 1024,
                )
            self.assertEqual(manifest["producer_id"], "wsl2")
            self.assertEqual(publication["file_count"], 2)
            self.assertTrue((destination / "summary" / "experiment-b.json").is_file())
            self.assertFalse((root / "staging").exists())

    def test_modified_payload_is_rejected_without_destination(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            layout_path, layout, resolved, _source, destination = self.fixture(root)
            archive = root / "runtime" / "result.zip"
            changed = root / "runtime" / "changed.zip"
            with self.patches(root, layout, resolved):
                result_transfer.create_package(
                    layout_path, "experiment-b", "wsl2", archive
                )
                with zipfile.ZipFile(archive) as source, zipfile.ZipFile(
                    changed, "w"
                ) as output:
                    for info in source.infolist():
                        data = source.read(info.filename)
                        if info.filename.endswith("result.json"):
                            data = b'{"tampered":true}\n'
                        output.writestr(info, data)
                with self.assertRaisesRegex(
                    result_transfer.ResultTransferError,
                    "(ZIP member size disagrees|payload (size|hash) mismatch)",
                ):
                    result_transfer.publish_package(
                        changed,
                        layout_path=layout_path,
                        experiment_id="experiment-b",
                        producer="wsl2",
                        staging=root / "staging",
                        max_uncompressed_bytes=1024 * 1024,
                    )
            self.assertFalse(destination.exists())

    def test_path_traversal_and_existing_destination_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            layout_path, layout, resolved, _source, destination = self.fixture(root)
            archive = root / "runtime" / "result.zip"
            bad = root / "runtime" / "bad.zip"
            with self.patches(root, layout, resolved):
                package, _manifest = result_transfer.create_package(
                    layout_path, "experiment-b", "wsl2", archive
                )
                with zipfile.ZipFile(package) as source, zipfile.ZipFile(
                    bad, "w"
                ) as output:
                    for info in source.infolist():
                        output.writestr(info, source.read(info.filename))
                    output.writestr("../escape", "bad")
                with self.assertRaisesRegex(
                    result_transfer.ResultTransferError, "unsafe ZIP member"
                ):
                    result_transfer.publish_package(
                        bad,
                        layout_path=layout_path,
                        experiment_id="experiment-b",
                        producer="wsl2",
                        staging=root / "staging",
                        max_uncompressed_bytes=1024 * 1024,
                    )
                destination.mkdir(parents=True)
                with self.assertRaisesRegex(
                    result_transfer.ResultTransferError, "already exists"
                ):
                    result_transfer.publish_package(
                        package,
                        layout_path=layout_path,
                        experiment_id="experiment-b",
                        producer="wsl2",
                        staging=root / "staging",
                        max_uncompressed_bytes=1024 * 1024,
                    )

    def test_manifest_schema_is_valid_json_and_matches_version(self) -> None:
        schema = json.loads(result_transfer.MANIFEST_SCHEMA.read_text(encoding="utf-8"))
        self.assertEqual(schema["properties"]["schema_version"]["const"], 1)
        self.assertEqual(
            schema["properties"]["kind"]["const"],
            "hakoniwa-performance-result-transfer",
        )


if __name__ == "__main__":
    unittest.main()
