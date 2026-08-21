#!/usr/bin/env python3
"""Transfer declared performance results to their canonical repository location."""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import stat
import sys
import tempfile
import time
import zipfile
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any, Iterable

from tools import result_layout
from tools.remote_operation import artifact_protocol, artifact_transfer
from tools.remote_operation.pdu_transport import TransportError, write_tcp_endpoint_config


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_RUNTIME = ROOT / "work" / "remote-operation" / "result-transfer"
MANIFEST_NAME = "result-transfer-manifest.json"
MANIFEST_SCHEMA = (
    ROOT
    / "schemas"
    / "remote-operation"
    / "result-transfer-manifest.schema.json"
)
SUMMARY_NAMES = {
    "experiment-a": "experiment-a.json",
    "experiment-b": "experiment-b.json",
    "experiment-b-temporal": "temporal-b.json",
}


class ResultTransferError(RuntimeError):
    pass


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ResultTransferError(f"cannot read JSON {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise ResultTransferError(f"JSON root must be an object: {path}")
    return value


def _relative(path: Path) -> str:
    try:
        return path.resolve().relative_to(ROOT).as_posix()
    except ValueError as exc:
        raise ResultTransferError(f"path is outside the repository: {path}") from exc


def _files(source: Path) -> Iterable[tuple[Path, str]]:
    if source.is_symlink() or not source.is_dir():
        raise ResultTransferError(f"result source must be a real directory: {source}")
    found = False
    for path in sorted(source.rglob("*")):
        if path.is_symlink():
            raise ResultTransferError(f"result source contains a symlink: {path}")
        if path.is_file():
            found = True
            yield path, path.relative_to(source).as_posix()
    if not found:
        raise ResultTransferError(f"result source contains no files: {source}")


def _validate_result_identities(
    source: Path,
    *,
    series: str,
    producer: str,
    participant_scope: str,
) -> int:
    results = sorted(source.glob("*/attempt-*/result.json"))
    if not results:
        raise ResultTransferError(f"result source contains no attempt result.json: {source}")
    for path in results:
        relative = path.relative_to(source)
        configuration_id = relative.parts[-3]
        attempt_name = relative.parts[-2]
        try:
            attempt = int(attempt_name.removeprefix("attempt-"))
        except ValueError as exc:
            raise ResultTransferError(f"invalid attempt directory: {path}") from exc
        payload = _json(path)
        metadata = payload.get("metadata")
        if not isinstance(metadata, dict):
            raise ResultTransferError(f"result metadata is missing: {path}")
        expected = {
            "series": series,
            "configuration_id": configuration_id,
            "attempt": attempt,
        }
        if participant_scope == "host":
            expected["host_id"] = producer
        for field, value in expected.items():
            if metadata.get(field) != value:
                raise ResultTransferError(
                    f"result {field} mismatch in {path}: "
                    f"{metadata.get(field)!r} != {value!r}"
                )
    return len(results)


def _validate_source(
    source: Path,
    *,
    experiment_id: str,
    series: str,
    producer: str,
    participant_scope: str,
) -> int:
    summary_name = SUMMARY_NAMES.get(experiment_id)
    if summary_name is not None:
        summary = source / "summary" / summary_name
        payload = _json(summary)
        if payload.get("complete") is not True:
            raise ResultTransferError(f"result summary is incomplete: {summary}")
    return _validate_result_identities(
        source,
        series=series,
        producer=producer,
        participant_scope=participant_scope,
    )


def _manifest(
    layout_path: Path,
    layout: dict[str, Any],
    experiment_id: str,
    producer: str,
) -> tuple[dict[str, Any], Path, Path]:
    resolved = result_layout.resolve_experiment_paths(layout, experiment_id, producer)
    source = resolved["source"].resolve()
    destination = resolved["destination"].resolve()
    _validate_source(
        source,
        experiment_id=experiment_id,
        series=resolved["series"],
        producer=producer,
        participant_scope=resolved["participant_scope"],
    )
    entries = [
        {"path": relative, "size_bytes": path.stat().st_size, "sha256": _sha256(path)}
        for path, relative in _files(source)
    ]
    return (
        {
            "schema_version": 1,
            "kind": "hakoniwa-performance-result-transfer",
            "experiment_id": experiment_id,
            "producer_id": producer,
            "participant_scope": resolved["participant_scope"],
            "series": resolved["series"],
            "source": _relative(source),
            "destination": _relative(destination),
            "layout": {
                "path": _relative(layout_path),
                "sha256": _sha256(layout_path),
            },
            "experiment": {
                "path": _relative(resolved["experiment"]),
                "sha256": _sha256(resolved["experiment"]),
            },
            "created_at": datetime.now(timezone.utc).isoformat(),
            "files": entries,
        },
        source,
        destination,
    )


def _validate_manifest_shape(manifest: dict[str, Any]) -> None:
    expected = {
        "schema_version",
        "kind",
        "experiment_id",
        "producer_id",
        "participant_scope",
        "series",
        "source",
        "destination",
        "layout",
        "experiment",
        "created_at",
        "files",
    }
    if set(manifest) != expected:
        raise ResultTransferError("transfer manifest fields do not match the schema")
    if manifest.get("schema_version") != 1 or manifest.get("kind") != "hakoniwa-performance-result-transfer":
        raise ResultTransferError("unsupported transfer manifest")
    files = manifest.get("files")
    if not isinstance(files, list) or not files:
        raise ResultTransferError("transfer manifest files must be non-empty")
    seen = set()
    for entry in files:
        if not isinstance(entry, dict) or set(entry) != {"path", "size_bytes", "sha256"}:
            raise ResultTransferError("invalid transfer manifest file entry")
        member = PurePosixPath(str(entry["path"]))
        if member.is_absolute() or ".." in member.parts or "\\" in str(entry["path"]):
            raise ResultTransferError(f"unsafe manifest path: {entry['path']}")
        if str(member) in seen:
            raise ResultTransferError(f"duplicate manifest path: {member}")
        seen.add(str(member))
        if not isinstance(entry["size_bytes"], int) or entry["size_bytes"] < 0:
            raise ResultTransferError(f"invalid size for {member}")
        digest = entry["sha256"]
        if not isinstance(digest, str) or len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
            raise ResultTransferError(f"invalid SHA-256 for {member}")


def create_package(
    layout_path: Path,
    experiment_id: str,
    producer: str,
    output: Path,
) -> tuple[Path, dict[str, Any]]:
    layout_path = layout_path.resolve()
    layout = result_layout.load_layout(layout_path)
    manifest, source, _destination = _manifest(
        layout_path, layout, experiment_id, producer
    )
    output = output.resolve()
    if output.suffix.lower() != ".zip":
        raise ResultTransferError("result transfer package must use .zip")
    if source == output or source in output.parents:
        raise ResultTransferError("result transfer package must be outside its source")
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_suffix(".zip.part")
    temporary.unlink(missing_ok=True)
    try:
        with zipfile.ZipFile(
            temporary, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6
        ) as archive:
            archive.writestr(
                MANIFEST_NAME,
                json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2)
                + "\n",
            )
            for path, relative in _files(source):
                archive.write(path, f"payload/{relative}")
        temporary.replace(output)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise
    return output, manifest


def _group_members(
    layout: dict[str, Any], group_id: str, producer: str
) -> list[str]:
    try:
        members = layout["transfer_groups"][group_id]["experiments"]
    except KeyError as exc:
        raise ResultTransferError(f"unknown transfer group: {group_id}") from exc
    for experiment_id in members:
        if producer not in layout["experiments"][experiment_id]["producers"]:
            raise ResultTransferError(
                f"{producer} is not a common producer for transfer group {group_id}"
            )
    return list(members)


def create_group_package(
    layout_path: Path,
    group_id: str,
    producer: str,
    output: Path,
) -> tuple[Path, dict[str, Any]]:
    layout_path = layout_path.resolve()
    layout = result_layout.load_layout(layout_path)
    datasets = []
    skipped = []
    sources: dict[str, Path] = {}
    for experiment_id in _group_members(layout, group_id, producer):
        resolved = result_layout.resolve_experiment_paths(
            layout, experiment_id, producer
        )
        if not resolved["source"].is_dir():
            skipped.append(
                {
                    "experiment_id": experiment_id,
                    "status": "skipped_missing_source",
                }
            )
            continue
        single, source, _destination = _manifest(
            layout_path, layout, experiment_id, producer
        )
        datasets.append(
            {
                key: single[key]
                for key in (
                    "experiment_id",
                    "participant_scope",
                    "series",
                    "source",
                    "destination",
                    "experiment",
                    "files",
                )
            }
        )
        sources[experiment_id] = source
    if not datasets:
        raise ResultTransferError(
            f"all sources are missing for transfer group {group_id}"
        )
    manifest = {
        "schema_version": 2,
        "kind": "hakoniwa-performance-result-transfer-group",
        "group_id": group_id,
        "producer_id": producer,
        "layout": {
            "path": _relative(layout_path),
            "sha256": _sha256(layout_path),
        },
        "created_at": datetime.now(timezone.utc).isoformat(),
        "datasets": datasets,
        "skipped_sources": skipped,
    }
    output = output.resolve()
    if output.suffix.lower() != ".zip":
        raise ResultTransferError("result transfer package must use .zip")
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_suffix(".zip.part")
    temporary.unlink(missing_ok=True)
    try:
        with zipfile.ZipFile(
            temporary, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6
        ) as archive:
            archive.writestr(
                MANIFEST_NAME,
                json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2)
                + "\n",
            )
            for dataset in datasets:
                experiment_id = dataset["experiment_id"]
                for path, relative in _files(sources[experiment_id]):
                    archive.write(path, f"payload/{experiment_id}/{relative}")
        temporary.replace(output)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise
    return output, manifest


def _safe_member(info: zipfile.ZipInfo) -> PurePosixPath:
    member = PurePosixPath(info.filename)
    mode = info.external_attr >> 16
    if (
        member.is_absolute()
        or ".." in member.parts
        or "\\" in info.filename
        or stat.S_ISLNK(mode)
    ):
        raise ResultTransferError(f"unsafe ZIP member: {info.filename}")
    return member


def _read_package(archive_path: Path) -> tuple[dict[str, Any], dict[str, zipfile.ZipInfo]]:
    with zipfile.ZipFile(archive_path) as archive:
        infos: dict[str, zipfile.ZipInfo] = {}
        for info in archive.infolist():
            member = _safe_member(info)
            name = str(member)
            if name in infos:
                raise ResultTransferError(f"duplicate ZIP member: {name}")
            if not info.is_dir():
                infos[name] = info
        if MANIFEST_NAME not in infos:
            raise ResultTransferError("result transfer manifest is missing")
        if infos[MANIFEST_NAME].file_size > 1024 * 1024:
            raise ResultTransferError("result transfer manifest is too large")
        try:
            manifest = json.loads(archive.read(MANIFEST_NAME).decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ResultTransferError("result transfer manifest is invalid") from exc
    if not isinstance(manifest, dict):
        raise ResultTransferError("result transfer manifest must be an object")
    _validate_manifest_shape(manifest)
    expected = {f"payload/{entry['path']}" for entry in manifest["files"]}
    actual = set(infos) - {MANIFEST_NAME}
    if actual != expected:
        raise ResultTransferError("ZIP payload does not match the transfer manifest")
    for entry in manifest["files"]:
        if infos[f"payload/{entry['path']}"].file_size != entry["size_bytes"]:
            raise ResultTransferError(
                f"ZIP member size disagrees with manifest: {entry['path']}"
            )
    return manifest, infos


def _validate_expected(
    manifest: dict[str, Any],
    *,
    layout_path: Path,
    layout: dict[str, Any],
    experiment_id: str,
    producer: str,
) -> Path:
    resolved = result_layout.resolve_experiment_paths(layout, experiment_id, producer)
    expected = {
        "experiment_id": experiment_id,
        "producer_id": producer,
        "participant_scope": resolved["participant_scope"],
        "series": resolved["series"],
        "source": _relative(resolved["source"]),
        "destination": _relative(resolved["destination"]),
    }
    for field, value in expected.items():
        if manifest.get(field) != value:
            raise ResultTransferError(
                f"transfer manifest {field} mismatch: {manifest.get(field)!r} != {value!r}"
            )
    hashes = {
        "layout": (_relative(layout_path), _sha256(layout_path)),
        "experiment": (
            _relative(resolved["experiment"]),
            _sha256(resolved["experiment"]),
        ),
    }
    for field, (path, digest) in hashes.items():
        if manifest.get(field) != {"path": path, "sha256": digest}:
            raise ResultTransferError(f"transfer manifest {field} identity mismatch")
    return resolved["destination"].resolve()


def publish_package(
    archive_path: Path,
    *,
    layout_path: Path,
    experiment_id: str,
    producer: str,
    staging: Path,
    max_uncompressed_bytes: int,
) -> dict[str, Any]:
    layout_path = layout_path.resolve()
    layout = result_layout.load_layout(layout_path)
    manifest, infos = _read_package(archive_path)
    destination = _validate_expected(
        manifest,
        layout_path=layout_path,
        layout=layout,
        experiment_id=experiment_id,
        producer=producer,
    )
    total = sum(entry["size_bytes"] for entry in manifest["files"])
    if total > max_uncompressed_bytes:
        raise ResultTransferError("uncompressed result exceeds receiver limit")
    if destination.exists():
        if not _destination_matches(destination, manifest["files"]):
            raise ResultTransferError(
                f"canonical destination differs from transfer: {destination}"
            )
        return {
            "status": "skipped_existing_identical",
            "experiment_id": experiment_id,
            "producer_id": producer,
            "series": manifest["series"],
            "destination": str(destination),
            "file_count": len(manifest["files"]),
            "uncompressed_size_bytes": total,
            "manifest_sha256": hashlib.sha256(
                json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode()
            ).hexdigest(),
        }
    if staging.exists():
        shutil.rmtree(staging)
    payload = staging / "payload"
    payload.mkdir(parents=True)
    try:
        with zipfile.ZipFile(archive_path) as archive:
            for entry in manifest["files"]:
                relative = PurePosixPath(entry["path"])
                info = infos[f"payload/{relative}"]
                target = payload.joinpath(*relative.parts)
                target.parent.mkdir(parents=True, exist_ok=True)
                digest = hashlib.sha256()
                size = 0
                with archive.open(info) as source, target.open("xb") as output:
                    while chunk := source.read(1024 * 1024):
                        size += len(chunk)
                        if size > entry["size_bytes"] or size > max_uncompressed_bytes:
                            raise ResultTransferError(f"payload size mismatch: {relative}")
                        digest.update(chunk)
                        output.write(chunk)
                if size != entry["size_bytes"] or digest.hexdigest() != entry["sha256"]:
                    raise ResultTransferError(f"payload hash mismatch: {relative}")
        _validate_source(
            payload,
            experiment_id=experiment_id,
            series=manifest["series"],
            producer=producer,
            participant_scope=manifest["participant_scope"],
        )
        if destination.exists():
            raise ResultTransferError(
                f"canonical destination appeared during transfer: {destination}"
            )
        destination.parent.mkdir(parents=True, exist_ok=True)
        payload.replace(destination)
        return {
            "status": "published",
            "experiment_id": experiment_id,
            "producer_id": producer,
            "series": manifest["series"],
            "destination": str(destination),
            "file_count": len(manifest["files"]),
            "uncompressed_size_bytes": total,
            "manifest_sha256": hashlib.sha256(
                json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode()
            ).hexdigest(),
        }
    finally:
        if staging.exists():
            shutil.rmtree(staging)


def _read_group_package(
    archive_path: Path,
) -> tuple[dict[str, Any], dict[str, zipfile.ZipInfo]]:
    with zipfile.ZipFile(archive_path) as archive:
        infos: dict[str, zipfile.ZipInfo] = {}
        for info in archive.infolist():
            member = _safe_member(info)
            name = str(member)
            if name in infos:
                raise ResultTransferError(f"duplicate ZIP member: {name}")
            if not info.is_dir():
                infos[name] = info
        manifest_info = infos.get(MANIFEST_NAME)
        if manifest_info is None:
            raise ResultTransferError("result transfer manifest is missing")
        if manifest_info.file_size > 1024 * 1024:
            raise ResultTransferError("result transfer manifest is too large")
        try:
            manifest = json.loads(archive.read(MANIFEST_NAME).decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ResultTransferError("result transfer manifest is invalid") from exc
    if not isinstance(manifest, dict):
        raise ResultTransferError("result transfer manifest must be an object")
    expected_fields = {
        "schema_version",
        "kind",
        "group_id",
        "producer_id",
        "layout",
        "created_at",
        "datasets",
        "skipped_sources",
    }
    if set(manifest) != expected_fields:
        raise ResultTransferError("group transfer manifest fields do not match the schema")
    if (
        manifest.get("schema_version") != 2
        or manifest.get("kind")
        != "hakoniwa-performance-result-transfer-group"
    ):
        raise ResultTransferError("unsupported group transfer manifest")
    datasets = manifest.get("datasets")
    skipped = manifest.get("skipped_sources")
    if not isinstance(datasets, list) or not datasets or not isinstance(skipped, list):
        raise ResultTransferError("group manifest datasets/skipped_sources are invalid")
    expected_members = {MANIFEST_NAME}
    seen_experiments = set()
    for dataset in datasets:
        required = {
            "experiment_id",
            "participant_scope",
            "series",
            "source",
            "destination",
            "experiment",
            "files",
        }
        if not isinstance(dataset, dict) or set(dataset) != required:
            raise ResultTransferError("invalid group dataset entry")
        experiment_id = dataset.get("experiment_id")
        if not isinstance(experiment_id, str) or experiment_id in seen_experiments:
            raise ResultTransferError("duplicate or invalid group experiment identity")
        seen_experiments.add(experiment_id)
        probe = {
            "schema_version": 1,
            "kind": "hakoniwa-performance-result-transfer",
            "producer_id": manifest.get("producer_id"),
            "layout": manifest.get("layout"),
            "created_at": manifest.get("created_at"),
            **dataset,
        }
        _validate_manifest_shape(probe)
        for entry in dataset["files"]:
            name = f"payload/{experiment_id}/{entry['path']}"
            expected_members.add(name)
            info = infos.get(name)
            if info is None or info.file_size != entry["size_bytes"]:
                raise ResultTransferError(
                    f"ZIP member size disagrees with manifest: {name}"
                )
    for item in skipped:
        if (
            not isinstance(item, dict)
            or set(item) != {"experiment_id", "status"}
            or item.get("status") != "skipped_missing_source"
            or not isinstance(item.get("experiment_id"), str)
            or item["experiment_id"] in seen_experiments
        ):
            raise ResultTransferError("invalid skipped source entry")
        seen_experiments.add(item["experiment_id"])
    if set(infos) != expected_members:
        raise ResultTransferError("ZIP payload does not match the group manifest")
    return manifest, infos


def _destination_matches(
    destination: Path, entries: list[dict[str, Any]]
) -> bool:
    if destination.is_symlink() or not destination.is_dir():
        return False
    actual: dict[str, Path] = {}
    for path in destination.rglob("*"):
        if path.is_symlink():
            return False
        if path.is_file():
            actual[path.relative_to(destination).as_posix()] = path
    expected = {entry["path"]: entry for entry in entries}
    if set(actual) != set(expected):
        return False
    return all(
        actual[name].stat().st_size == entry["size_bytes"]
        and _sha256(actual[name]) == entry["sha256"]
        for name, entry in expected.items()
    )


def publish_group_package(
    archive_path: Path,
    *,
    layout_path: Path,
    group_id: str,
    producer: str,
    staging: Path,
    max_uncompressed_bytes: int,
) -> dict[str, Any]:
    layout_path = layout_path.resolve()
    layout = result_layout.load_layout(layout_path)
    members = _group_members(layout, group_id, producer)
    manifest, infos = _read_group_package(archive_path)
    if manifest.get("group_id") != group_id or manifest.get("producer_id") != producer:
        raise ResultTransferError("group or producer identity mismatch")
    expected_layout = {"path": _relative(layout_path), "sha256": _sha256(layout_path)}
    if manifest.get("layout") != expected_layout:
        raise ResultTransferError("group manifest layout identity mismatch")
    declared = [item["experiment_id"] for item in manifest["datasets"]]
    skipped = [item["experiment_id"] for item in manifest["skipped_sources"]]
    if len(set(declared + skipped)) != len(members) or set(declared + skipped) != set(members):
        raise ResultTransferError("group manifest does not account for every member")
    total = sum(
        entry["size_bytes"]
        for dataset in manifest["datasets"]
        for entry in dataset["files"]
    )
    if total > max_uncompressed_bytes:
        raise ResultTransferError("uncompressed result group exceeds receiver limit")
    if staging.exists():
        shutil.rmtree(staging)
    staging.mkdir(parents=True)
    prepared: list[tuple[dict[str, Any], Path, Path, str]] = []
    published: list[tuple[Path, Path]] = []
    try:
        with zipfile.ZipFile(archive_path) as archive:
            for dataset in manifest["datasets"]:
                experiment_id = dataset["experiment_id"]
                probe = {
                    "schema_version": 1,
                    "kind": "hakoniwa-performance-result-transfer",
                    "producer_id": producer,
                    "layout": manifest["layout"],
                    "created_at": manifest["created_at"],
                    **dataset,
                }
                destination = _validate_expected(
                    probe,
                    layout_path=layout_path,
                    layout=layout,
                    experiment_id=experiment_id,
                    producer=producer,
                )
                payload = staging / experiment_id / "payload"
                payload.mkdir(parents=True)
                for entry in dataset["files"]:
                    relative = PurePosixPath(entry["path"])
                    info = infos[f"payload/{experiment_id}/{relative}"]
                    target = payload.joinpath(*relative.parts)
                    target.parent.mkdir(parents=True, exist_ok=True)
                    digest = hashlib.sha256()
                    size = 0
                    with archive.open(info) as source, target.open("xb") as output:
                        while chunk := source.read(1024 * 1024):
                            size += len(chunk)
                            if size > entry["size_bytes"]:
                                raise ResultTransferError(
                                    f"payload size mismatch: {experiment_id}/{relative}"
                                )
                            digest.update(chunk)
                            output.write(chunk)
                    if size != entry["size_bytes"] or digest.hexdigest() != entry["sha256"]:
                        raise ResultTransferError(
                            f"payload hash mismatch: {experiment_id}/{relative}"
                        )
                _validate_source(
                    payload,
                    experiment_id=experiment_id,
                    series=dataset["series"],
                    producer=producer,
                    participant_scope=dataset["participant_scope"],
                )
                if destination.exists():
                    if not _destination_matches(destination, dataset["files"]):
                        raise ResultTransferError(
                            f"canonical destination differs from transfer: {destination}"
                        )
                    status = "skipped_existing_identical"
                else:
                    status = "pending_publish"
                prepared.append((dataset, payload, destination, status))
        results = []
        for dataset, payload, destination, status in prepared:
            if status == "pending_publish":
                if destination.exists():
                    raise ResultTransferError(
                        f"canonical destination appeared during transfer: {destination}"
                    )
                destination.parent.mkdir(parents=True, exist_ok=True)
                rollback = staging / "rollback" / dataset["experiment_id"]
                payload.replace(destination)
                published.append((destination, rollback))
                status = "published"
            results.append(
                {
                    "experiment_id": dataset["experiment_id"],
                    "status": status,
                    "destination": str(destination),
                    "file_count": len(dataset["files"]),
                }
            )
        results.extend(manifest["skipped_sources"])
        return {
            "status": "published",
            "group_id": group_id,
            "producer_id": producer,
            "datasets": results,
            "uncompressed_size_bytes": total,
        }
    except Exception:
        for destination, rollback in reversed(published):
            if destination.exists():
                rollback.parent.mkdir(parents=True, exist_ok=True)
                destination.replace(rollback)
        raise
    finally:
        if staging.exists():
            shutil.rmtree(staging)


def _session(args: argparse.Namespace) -> str:
    identity = args.group or args.experiment
    value = args.session_id or f"result-{identity}-{args.producer}"
    return artifact_transfer._safe_session(value)


def _runtime(args: argparse.Namespace) -> Path:
    return (args.runtime_dir / _session(args)).resolve()


def _write_evidence(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")


def collect_command(args: argparse.Namespace) -> int:
    runtime = _runtime(args)
    runtime.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="collect-", dir=runtime) as temporary:
        temporary_root = Path(temporary)
        archive = temporary_root / f"{args.group or args.experiment}-{args.producer}.zip"
        if args.group:
            _archive, manifest = create_group_package(
                args.layout, args.group, args.producer, archive
            )
            publication = publish_group_package(
                archive,
                layout_path=args.layout,
                group_id=args.group,
                producer=args.producer,
                staging=temporary_root / "staging",
                max_uncompressed_bytes=args.max_uncompressed_bytes,
            )
        else:
            _archive, manifest = create_package(
                args.layout, args.experiment, args.producer, archive
            )
            publication = publish_package(
                archive,
                layout_path=args.layout,
                experiment_id=args.experiment,
                producer=args.producer,
                staging=temporary_root / "staging",
                max_uncompressed_bytes=args.max_uncompressed_bytes,
            )
    evidence = {
        "status": "success",
        "role": "collector",
        "recorded_at_unix_sec": time.time(),
        "manifest": manifest,
        "publication": publication,
    }
    evidence_path = runtime / "collector-result.json"
    _write_evidence(evidence_path, evidence)
    if args.group:
        for item in publication["datasets"]:
            print(
                f"[{item['status'].upper()}] {item['experiment_id']}: "
                f"{item.get('destination', '-')}",
                flush=True,
            )
    else:
        print(
            f"[{publication['status'].upper()}] {args.experiment}: "
            f"{publication['destination']}",
            flush=True,
        )
    print(f"Evidence: {evidence_path}")
    return 0


def send_command(args: argparse.Namespace) -> int:
    session_id = _session(args)
    runtime = _runtime(args)
    runtime.mkdir(parents=True, exist_ok=True)
    archive = runtime / f"{args.experiment}-{args.producer}.zip"
    if archive.exists():
        raise ResultTransferError(f"transfer archive already exists: {archive}")
    archive, manifest = create_package(
        args.layout, args.experiment, args.producer, archive
    )
    event_log = runtime / "sender-events.jsonl"
    event_log.unlink(missing_ok=True)
    config = write_tcp_endpoint_config(
        runtime / "sender-endpoint",
        role="client",
        address=args.server_address,
        port=args.port,
    )
    transport = artifact_transfer._transport(config)
    try:
        transport.start()
        print(f"Connecting result sender to {args.server_address}:{args.port}", flush=True)
        transport.wait_connected(args.timeout_sec)
        transfer = artifact_transfer.send_file(
            transport,
            archive,
            session_id=session_id,
            timeout_sec=args.timeout_sec,
            chunk_size=args.chunk_size,
            event_log=event_log,
        )
    finally:
        transport.close()
    evidence = {
        "status": "success",
        "role": "sender",
        "recorded_at_unix_sec": time.time(),
        "manifest": manifest,
        "transfer": transfer,
        "events": str(event_log),
    }
    evidence_path = runtime / "sender-result.json"
    _write_evidence(evidence_path, evidence)
    print(f"[OK] receiver published {args.experiment}/{args.producer}")
    print(f"Evidence: {evidence_path}")
    return 0


def receive_command(args: argparse.Namespace) -> int:
    session_id = _session(args)
    runtime = _runtime(args)
    runtime.mkdir(parents=True, exist_ok=True)
    incoming = runtime / "incoming"
    event_log = runtime / "receiver-events.jsonl"
    event_log.unlink(missing_ok=True)
    config = write_tcp_endpoint_config(
        runtime / "receiver-endpoint",
        role="server",
        address=args.listen_address,
        port=args.port,
    )
    publication: dict[str, Any] = {}

    def publish(archive: Path, _offer: dict[str, Any]) -> dict[str, Any]:
        result = publish_package(
            archive,
            layout_path=args.layout,
            experiment_id=args.experiment,
            producer=args.producer,
            staging=runtime / "staging",
            max_uncompressed_bytes=args.max_uncompressed_bytes,
        )
        publication.update(result)
        return result

    transport = artifact_transfer._transport(config)
    try:
        transport.start()
        print(f"Waiting for result on {args.listen_address}:{args.port}", flush=True)
        transport.wait_connected(args.timeout_sec)
        transfer = artifact_transfer.receive_file(
            transport,
            incoming,
            session_id=session_id,
            timeout_sec=args.timeout_sec,
            max_bytes=args.max_bytes,
            event_log=event_log,
            on_verified=publish,
        )
    finally:
        transport.close()
    evidence = {
        "status": "success",
        "role": "receiver",
        "recorded_at_unix_sec": time.time(),
        "publication": publication,
        "transfer": transfer,
        "events": str(event_log),
    }
    evidence_path = runtime / "receiver-result.json"
    _write_evidence(evidence_path, evidence)
    print(f"[OK] published result: {publication['destination']}")
    print(f"Evidence: {evidence_path}")
    return 0


def send_group_command(args: argparse.Namespace) -> int:
    session_id = _session(args)
    runtime = _runtime(args)
    runtime.mkdir(parents=True, exist_ok=True)
    archive = runtime / f"{args.group}-{args.producer}.zip"
    if archive.exists():
        raise ResultTransferError(f"transfer archive already exists: {archive}")
    archive, manifest = create_group_package(
        args.layout, args.group, args.producer, archive
    )
    event_log = runtime / "sender-events.jsonl"
    event_log.unlink(missing_ok=True)
    config = write_tcp_endpoint_config(
        runtime / "sender-endpoint",
        role="client",
        address=args.server_address,
        port=args.port,
    )
    transport = artifact_transfer._transport(config)
    try:
        transport.start()
        print(f"Connecting result sender to {args.server_address}:{args.port}", flush=True)
        for item in manifest["skipped_sources"]:
            print(
                f"[SKIP] {item['experiment_id']}: source does not exist",
                flush=True,
            )
        transport.wait_connected(args.timeout_sec)
        transfer = artifact_transfer.send_file(
            transport,
            archive,
            session_id=session_id,
            timeout_sec=args.timeout_sec,
            chunk_size=args.chunk_size,
            event_log=event_log,
        )
    finally:
        transport.close()
    evidence = {
        "status": "success",
        "role": "sender",
        "recorded_at_unix_sec": time.time(),
        "manifest": manifest,
        "transfer": transfer,
        "events": str(event_log),
    }
    evidence_path = runtime / "sender-result.json"
    _write_evidence(evidence_path, evidence)
    print(f"[OK] receiver published group {args.group}/{args.producer}")
    print(f"Evidence: {evidence_path}")
    return 0


def receive_group_command(args: argparse.Namespace) -> int:
    session_id = _session(args)
    runtime = _runtime(args)
    runtime.mkdir(parents=True, exist_ok=True)
    incoming = runtime / "incoming"
    event_log = runtime / "receiver-events.jsonl"
    event_log.unlink(missing_ok=True)
    config = write_tcp_endpoint_config(
        runtime / "receiver-endpoint",
        role="server",
        address=args.listen_address,
        port=args.port,
    )
    publication: dict[str, Any] = {}

    def publish(archive: Path, _offer: dict[str, Any]) -> dict[str, Any]:
        result = publish_group_package(
            archive,
            layout_path=args.layout,
            group_id=args.group,
            producer=args.producer,
            staging=runtime / "staging",
            max_uncompressed_bytes=args.max_uncompressed_bytes,
        )
        publication.update(result)
        return result

    transport = artifact_transfer._transport(config)
    try:
        transport.start()
        print(f"Waiting for result group on {args.listen_address}:{args.port}", flush=True)
        transport.wait_connected(args.timeout_sec)
        transfer = artifact_transfer.receive_file(
            transport,
            incoming,
            session_id=session_id,
            timeout_sec=args.timeout_sec,
            max_bytes=args.max_bytes,
            event_log=event_log,
            on_verified=publish,
        )
    finally:
        transport.close()
    evidence = {
        "status": "success",
        "role": "receiver",
        "recorded_at_unix_sec": time.time(),
        "publication": publication,
        "transfer": transfer,
        "events": str(event_log),
    }
    evidence_path = runtime / "receiver-result.json"
    _write_evidence(evidence_path, evidence)
    for item in publication["datasets"]:
        print(
            f"[{item['status'].upper()}] {item['experiment_id']}: "
            f"{item.get('destination', '-')}",
            flush=True,
        )
    print(f"Evidence: {evidence_path}")
    return 0


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("--layout", type=Path, default=result_layout.DEFAULT_LAYOUT)
    selection = result.add_mutually_exclusive_group(required=True)
    current_layout = result_layout.load_layout()
    selection.add_argument(
        "--experiment", choices=sorted(current_layout["experiments"])
    )
    selection.add_argument(
        "--group", choices=sorted(current_layout["transfer_groups"])
    )
    result.add_argument("--producer", required=True)
    result.add_argument("--session-id")
    result.add_argument("--runtime-dir", type=Path, default=DEFAULT_RUNTIME)
    result.add_argument("--port", type=int, default=artifact_transfer.DEFAULT_PORT)
    result.add_argument("--timeout-sec", type=float, default=300.0)
    commands = result.add_subparsers(dest="command", required=True)
    receive = commands.add_parser("receive")
    receive.add_argument("--listen-address", default="192.168.2.100")
    receive.add_argument("--max-bytes", type=int, default=artifact_transfer.DEFAULT_MAX_BYTES)
    receive.add_argument("--max-uncompressed-bytes", type=int, default=artifact_transfer.DEFAULT_MAX_BYTES)
    send = commands.add_parser("send")
    send.add_argument("--server-address", default="192.168.2.100")
    send.add_argument("--chunk-size", type=int, default=artifact_transfer.DEFAULT_CHUNK_SIZE)
    collect = commands.add_parser("collect")
    collect.add_argument(
        "--max-uncompressed-bytes",
        type=int,
        default=artifact_transfer.DEFAULT_MAX_BYTES,
    )
    return result


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        layout = result_layout.load_layout(args.layout)
        if args.group:
            _group_members(layout, args.group, args.producer)
        else:
            experiment = layout["experiments"].get(args.experiment)
            if experiment is None or args.producer not in experiment["producers"]:
                raise ResultTransferError(
                    f"{args.producer} is not a producer for {args.experiment}"
                )
        if args.command == "collect":
            return collect_command(args)
        if args.command == "receive":
            return (
                receive_group_command(args) if args.group else receive_command(args)
            )
        return send_group_command(args) if args.group else send_command(args)
    except (
        ResultTransferError,
        result_layout.ResultLayoutError,
        artifact_transfer.ArtifactTransferError,
        artifact_protocol.ArtifactProtocolError,
        TransportError,
        OSError,
        ValueError,
        zipfile.BadZipFile,
    ) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
