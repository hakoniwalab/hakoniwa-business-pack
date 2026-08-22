#!/usr/bin/env python3
"""Finalize all drone-fleet performance results into exp-results."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools import result_layout
from tools.remote_operation import artifact_transfer, result_transfer


DEFAULT_RUNTIME = ROOT / "work" / "remote-operation" / "performance-collect"
DEFAULT_MANIFEST = ROOT / "exp-results" / "collection-manifest.json"

LOCAL_COLLECTIONS = (
    ("experiment", "experiment-a", "mac"),
    ("group", "experiment-b", "mac"),
    ("group", "experiment-c-archive", "multi-host-collector"),
)
CANONICAL_DATASETS = (
    ("experiment-a", "mac"),
    ("experiment-a", "wsl2"),
    ("experiment-b", "mac"),
    ("experiment-b-temporal", "mac"),
    ("experiment-b", "wsl2"),
    ("experiment-b-temporal", "wsl2"),
    ("experiment-c-archive", "multi-host-collector"),
    ("experiment-c-temporal-archive", "multi-host-collector"),
)


class PerformanceCollectError(RuntimeError):
    pass


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _tree_identity(path: Path) -> dict[str, Any]:
    if path.is_symlink() or not path.is_dir():
        raise PerformanceCollectError(f"canonical result directory is missing: {path}")
    entries = []
    for item in sorted(path.rglob("*")):
        if item.is_symlink():
            raise PerformanceCollectError(f"canonical result contains a symlink: {item}")
        if item.is_file():
            entries.append(
                {
                    "path": item.relative_to(path).as_posix(),
                    "size_bytes": item.stat().st_size,
                    "sha256": _sha256(item),
                }
            )
    if not entries:
        raise PerformanceCollectError(f"canonical result is empty: {path}")
    digest = hashlib.sha256(
        json.dumps(entries, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    return {
        "file_count": len(entries),
        "size_bytes": sum(item["size_bytes"] for item in entries),
        "tree_sha256": digest,
    }


def _validate_canonical(
    layout: dict[str, Any], experiment_id: str, producer: str
) -> dict[str, Any]:
    resolved = result_layout.resolve_experiment_paths(layout, experiment_id, producer)
    destination = resolved["destination"].resolve()
    result_count = result_transfer._validate_source(
        destination,
        experiment_id=experiment_id,
        series=resolved["series"],
        producer=producer,
        participant_scope=resolved["participant_scope"],
    )
    identity = _tree_identity(destination)
    return {
        "experiment_id": experiment_id,
        "producer_id": producer,
        "series": resolved["series"],
        "destination": destination.relative_to(ROOT).as_posix(),
        "result_count": result_count,
        **identity,
    }


def _collect_local(
    layout_path: Path,
    kind: str,
    identity: str,
    producer: str,
    temporary: Path,
    max_uncompressed_bytes: int,
) -> dict[str, Any]:
    archive = temporary / f"{identity}-{producer}.zip"
    staging = temporary / f"staging-{identity}-{producer}"
    if kind == "experiment":
        _archive, manifest = result_transfer.create_package(
            layout_path, identity, producer, archive
        )
        publication = result_transfer.publish_package(
            archive,
            layout_path=layout_path,
            experiment_id=identity,
            producer=producer,
            staging=staging,
            max_uncompressed_bytes=max_uncompressed_bytes,
        )
    else:
        _archive, manifest = result_transfer.create_group_package(
            layout_path, identity, producer, archive
        )
        publication = result_transfer.publish_group_package(
            archive,
            layout_path=layout_path,
            group_id=identity,
            producer=producer,
            staging=staging,
            max_uncompressed_bytes=max_uncompressed_bytes,
        )
    return {
        "kind": kind,
        "identity": identity,
        "producer_id": producer,
        "manifest": manifest,
        "publication": publication,
    }


def collect_all(
    *,
    layout_path: Path,
    runtime: Path,
    manifest_path: Path,
    max_uncompressed_bytes: int,
) -> dict[str, Any]:
    layout_path = layout_path.resolve()
    layout = result_layout.load_layout(layout_path)
    runtime.mkdir(parents=True, exist_ok=True)
    operations = []
    with tempfile.TemporaryDirectory(prefix="collect-all-", dir=runtime) as temporary:
        temporary_root = Path(temporary)
        for kind, identity, producer in LOCAL_COLLECTIONS:
            operation = _collect_local(
                layout_path,
                kind,
                identity,
                producer,
                temporary_root,
                max_uncompressed_bytes,
            )
            operations.append(operation)
            publication = operation["publication"]
            if kind == "experiment":
                print(
                    f"[{publication['status'].upper()}] {identity}/{producer}",
                    flush=True,
                )
            else:
                for dataset in publication["datasets"]:
                    print(
                        f"[{dataset['status'].upper()}] "
                        f"{dataset['experiment_id']}/{producer}",
                        flush=True,
                    )

    datasets = []
    for experiment_id, producer in CANONICAL_DATASETS:
        dataset = _validate_canonical(layout, experiment_id, producer)
        datasets.append(dataset)
        print(
            f"[VERIFIED] {experiment_id}/{producer}: "
            f"{dataset['result_count']} results, {dataset['file_count']} files",
            flush=True,
        )

    result = {
        "schema_version": 1,
        "kind": "hakoniwa-drone-fleet-performance-collection",
        "status": "complete",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "layout": {
            "path": layout_path.relative_to(ROOT).as_posix(),
            "sha256": _sha256(layout_path),
        },
        "datasets": datasets,
        "operations": operations,
    }
    manifest_path = manifest_path.resolve()
    try:
        manifest_path.relative_to(ROOT)
    except ValueError as exc:
        raise PerformanceCollectError(
            f"collection manifest must stay inside the repository: {manifest_path}"
        ) from exc
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    evidence = runtime / "collector-result.json"
    evidence.write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(f"Collection manifest: {manifest_path}")
    print(f"Evidence           : {evidence}")
    return result


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("command", choices=("collect",))
    result.add_argument("--layout", type=Path, default=result_layout.DEFAULT_LAYOUT)
    result.add_argument("--runtime-dir", type=Path, default=DEFAULT_RUNTIME)
    result.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    result.add_argument(
        "--max-uncompressed-bytes",
        type=int,
        default=artifact_transfer.DEFAULT_MAX_BYTES,
    )
    return result


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        collect_all(
            layout_path=args.layout,
            runtime=args.runtime_dir.resolve(),
            manifest_path=args.manifest,
            max_uncompressed_bytes=args.max_uncompressed_bytes,
        )
        return 0
    except (
        PerformanceCollectError,
        result_layout.ResultLayoutError,
        result_transfer.ResultTransferError,
        OSError,
        ValueError,
    ) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
