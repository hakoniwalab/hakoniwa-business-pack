from __future__ import annotations

import json
import shutil
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

    def test_path_traversal_and_different_existing_destination_are_rejected(self) -> None:
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
                (destination / "different.txt").write_text(
                    "different\n", encoding="utf-8"
                )
                with self.assertRaisesRegex(
                    result_transfer.ResultTransferError, "differs from transfer"
                ):
                    result_transfer.publish_package(
                        package,
                        layout_path=layout_path,
                        experiment_id="experiment-b",
                        producer="wsl2",
                        staging=root / "staging",
                        max_uncompressed_bytes=1024 * 1024,
                    )

    def test_collect_publishes_locally_and_is_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            layout_path, layout, resolved, _source, destination = self.fixture(root)
            args = mock.Mock(
                layout=layout_path,
                experiment="experiment-b",
                group=None,
                producer="mac",
                runtime_dir=root / "runtime",
                session_id="collect-b-mac",
                max_uncompressed_bytes=1024 * 1024,
            )
            with self.patches(root, layout, resolved):
                first = result_transfer.collect_command(args)
                first_evidence = json.loads(
                    (
                        root
                        / "runtime"
                        / "collect-b-mac"
                        / "collector-result.json"
                    ).read_text(encoding="utf-8")
                )
                second = result_transfer.collect_command(args)
                second_evidence = json.loads(
                    (
                        root
                        / "runtime"
                        / "collect-b-mac"
                        / "collector-result.json"
                    ).read_text(encoding="utf-8")
                )
                published_summary = (
                    destination / "summary" / "experiment-b.json"
                ).is_file()
        self.assertEqual(first, 0)
        self.assertEqual(second, 0)
        self.assertEqual(first_evidence["publication"]["status"], "published")
        self.assertEqual(
            second_evidence["publication"]["status"],
            "skipped_existing_identical",
        )
        self.assertTrue(published_summary)

    def test_manifest_schema_is_valid_json_and_matches_version(self) -> None:
        schema = json.loads(result_transfer.MANIFEST_SCHEMA.read_text(encoding="utf-8"))
        self.assertEqual(
            schema["$defs"]["groupManifest"]["properties"]["schema_version"]["const"],
            2,
        )
        self.assertEqual(
            schema["$defs"]["groupManifest"]["properties"]["kind"]["const"],
            "hakoniwa-performance-result-transfer-group",
        )

    def test_series_scope_requires_paired_hosts_and_complete_summary(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary) / "multi-host-scaling"
            for host_id in ("srv-01", "cli-01"):
                attempt = source / "hosts" / host_id / "uav-064" / "attempt-01"
                attempt.mkdir(parents=True)
                (attempt / "result.json").write_text(
                    json.dumps(
                        {
                            "status": "success",
                            "metadata": {
                                "series": "multi-host-scaling",
                                "configuration_id": "uav-064",
                                "attempt": 1,
                                "host_id": host_id,
                            },
                        }
                    ),
                    encoding="utf-8",
                )
            summary = source / "summary"
            summary.mkdir()
            (summary / "multi-host-scaling-sleep-001ms-matrix.json").write_text(
                '{"complete":true}\n', encoding="utf-8"
            )
            self.assertEqual(
                result_transfer._validate_source(
                    source,
                    experiment_id="experiment-c-archive",
                    series="multi-host-scaling",
                    producer="multi-host-collector",
                    participant_scope="series",
                ),
                2,
            )
            (
                source
                / "hosts"
                / "cli-01"
                / "uav-064"
                / "attempt-01"
                / "result.json"
            ).unlink()
            with self.assertRaisesRegex(
                result_transfer.ResultTransferError, "fewer than two hosts"
            ):
                result_transfer._validate_source(
                    source,
                    experiment_id="experiment-c-archive",
                    series="multi-host-scaling",
                    producer="multi-host-collector",
                    participant_scope="series",
                )

    def group_fixture(self, root: Path, *, temporal: bool = True):
        layout_path, _layout, performance, source, destination = self.fixture(root)
        temporal_source = root / "work" / "results" / "single-host-temporal-validation"
        temporal_destination = (
            root / "exp-results" / "wsl2" / "single-host-temporal-validation"
        )
        if temporal:
            attempt = temporal_source / "temporal-uav-128-proc-02" / "attempt-01"
            attempt.mkdir(parents=True)
            (attempt / "result.json").write_text(
                json.dumps(
                    {
                        "status": "success",
                        "metadata": {
                            "series": "single-host-temporal-validation",
                            "configuration_id": "temporal-uav-128-proc-02",
                            "attempt": 1,
                        },
                    }
                ),
                encoding="utf-8",
            )
            summary = temporal_source / "summary"
            summary.mkdir()
            (summary / "temporal-b.json").write_text(
                '{"complete":true,"results":[]}\n', encoding="utf-8"
            )
        temporal_experiment = root / "recipes" / "temporal.yaml"
        temporal_experiment.write_text("version: 1\n", encoding="utf-8")
        layout = {
            "experiments": {
                "experiment-b": {"producers": ["mac", "wsl2"]},
                "experiment-b-temporal": {"producers": ["mac", "wsl2"]},
            },
            "transfer_groups": {
                "experiment-b": {
                    "experiments": ["experiment-b", "experiment-b-temporal"]
                }
            },
        }
        resolved = {
            "experiment-b": performance,
            "experiment-b-temporal": {
                "experiment": temporal_experiment,
                "source": temporal_source,
                "destination": temporal_destination,
                "series": "single-host-temporal-validation",
                "participant_scope": "machine",
            },
        }
        return layout_path, layout, resolved, source, destination, temporal_destination

    def group_patches(self, root: Path, layout: dict, resolved: dict) -> ExitStack:
        stack = ExitStack()
        stack.enter_context(mock.patch.object(result_transfer, "ROOT", root.resolve()))
        stack.enter_context(
            mock.patch.object(result_transfer.result_layout, "load_layout", return_value=layout)
        )
        stack.enter_context(
            mock.patch.object(
                result_transfer.result_layout,
                "resolve_experiment_paths",
                side_effect=lambda _layout, experiment, _producer: resolved[experiment],
            )
        )
        return stack

    def test_group_skips_identical_performance_and_publishes_temporal(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            layout_path, layout, resolved, source, destination, temporal_destination = (
                self.group_fixture(root)
            )
            shutil.copytree(source, destination)
            archive = root / "runtime" / "group.zip"
            with self.group_patches(root, layout, resolved):
                result_transfer.create_group_package(
                    layout_path, "experiment-b", "wsl2", archive
                )
                publication = result_transfer.publish_group_package(
                    archive,
                    layout_path=layout_path,
                    group_id="experiment-b",
                    producer="wsl2",
                    staging=root / "staging",
                    max_uncompressed_bytes=1024 * 1024,
                )
            temporal_published = temporal_destination.is_dir()
        statuses = {
            item["experiment_id"]: item["status"]
            for item in publication["datasets"]
        }
        self.assertEqual(statuses["experiment-b"], "skipped_existing_identical")
        self.assertEqual(statuses["experiment-b-temporal"], "published")
        self.assertTrue(temporal_published)

    def test_group_missing_temporal_source_is_recorded_and_skipped(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            layout_path, layout, resolved, _source, _destination, temporal_destination = (
                self.group_fixture(root, temporal=False)
            )
            archive = root / "runtime" / "group.zip"
            with self.group_patches(root, layout, resolved):
                _package, manifest = result_transfer.create_group_package(
                    layout_path, "experiment-b", "wsl2", archive
                )
                publication = result_transfer.publish_group_package(
                    archive,
                    layout_path=layout_path,
                    group_id="experiment-b",
                    producer="wsl2",
                    staging=root / "staging",
                    max_uncompressed_bytes=1024 * 1024,
                )
            temporal_exists = temporal_destination.exists()
        self.assertEqual(
            manifest["skipped_sources"],
            [
                {
                    "experiment_id": "experiment-b-temporal",
                    "status": "skipped_missing_source",
                }
            ],
        )
        self.assertIn(manifest["skipped_sources"][0], publication["datasets"])
        self.assertFalse(temporal_exists)

    def test_collect_group_publishes_available_local_sources(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            layout_path, layout, resolved, _source, destination, _temporal = (
                self.group_fixture(root, temporal=False)
            )
            args = mock.Mock(
                layout=layout_path,
                experiment=None,
                group="experiment-b",
                producer="mac",
                runtime_dir=root / "runtime",
                session_id="collect-group-b-mac",
                max_uncompressed_bytes=1024 * 1024,
            )
            with self.group_patches(root, layout, resolved):
                rc = result_transfer.collect_command(args)
                evidence = json.loads(
                    (
                        root
                        / "runtime"
                        / "collect-group-b-mac"
                        / "collector-result.json"
                    ).read_text(encoding="utf-8")
                )
                published = destination.is_dir()
        statuses = {
            item["experiment_id"]: item["status"]
            for item in evidence["publication"]["datasets"]
        }
        self.assertEqual(rc, 0)
        self.assertTrue(published)
        self.assertEqual(statuses["experiment-b"], "published")
        self.assertEqual(
            statuses["experiment-b-temporal"], "skipped_missing_source"
        )


if __name__ == "__main__":
    unittest.main()
