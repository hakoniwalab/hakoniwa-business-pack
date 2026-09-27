"""Report and reclaim disk space held by City World Web UI downloads.

A generated City World needs only ``build/world``, ``build/components``,
``viewer/`` and ``artifacts/`` of its job.  The raw PLATEAU CityGML under
``build/source`` and the shared download cache are consumed only while a job is
being generated.  Envsim materializes ``build/source`` as hardlinks to the
shared cache objects when it can, so the two usually share the same disk
blocks: removing one side alone frees almost nothing.  Every size reported
here therefore counts an inode as reclaimable only when all of its links are
inside the removal set.

Envsim takes no locks, and the Worker keeps its active generation in memory.
A job whose result manifest is missing, a ``.job-backups`` entry, or a ``.part``
download inside the shared cache is treated as a generation in progress.  Such
jobs are never touched, and the shared cache is not removed while any exist.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
from pathlib import Path
from typing import Any, Iterable

from tools.workdir import recipe_root

from .generation import _source_cache_dir
from .protocol import validate_result


class CityWorldCacheError(RuntimeError):
    pass


def default_runtime_dir() -> Path:
    root = Path(__file__).resolve().parents[3]
    return recipe_root(root, "city-world-web-ui") / "runtime"


def _files(root: Path) -> Iterable[os.stat_result]:
    if not root.exists():
        return
    if root.is_file() or root.is_symlink():
        yield root.lstat()
        return
    for directory, _, names in os.walk(root):
        for name in names:
            try:
                yield (Path(directory) / name).lstat()
            except OSError:
                continue


def _apparent_bytes(root: Path) -> int:
    return sum(stat.st_size for stat in _files(root))


def reclaimable_bytes(roots: Iterable[Path]) -> int:
    """Return bytes freed by deleting ``roots``, honouring hardlinks outside them."""
    links: dict[tuple[int, int], list[Any]] = {}
    for root in roots:
        for stat in _files(root):
            entry = links.setdefault((stat.st_dev, stat.st_ino), [stat, 0])
            entry[1] += 1
    return sum(
        stat.st_size for stat, seen in links.values() if seen >= max(stat.st_nlink, 1)
    )


def _job_state(job_root: Path, backup_root: Path) -> str:
    if (backup_root / job_root.name).exists():
        return "in_progress"
    try:
        result = validate_result(json.loads(
            (job_root / "artifacts" / "result-manifest.json").read_text(encoding="utf-8")
        ))
    except (OSError, ValueError, json.JSONDecodeError):
        return "in_progress"
    return "finished" if result["job_id"] == job_root.name else "in_progress"


def plan(runtime_root: Path, cache_dir: Path | None = None) -> dict[str, Any]:
    """Inspect the Web UI runtime without modifying it."""
    runtime_root = runtime_root.resolve()
    cache_dir = (cache_dir or _source_cache_dir(runtime_root)).resolve()
    objects_root = cache_dir / "objects"
    backup_root = runtime_root / ".job-backups"

    jobs = []
    jobs_root = runtime_root / "jobs"
    for job_root in sorted(jobs_root.iterdir() if jobs_root.is_dir() else []):
        if not job_root.is_dir():
            continue
        source = job_root / "build" / "source"
        jobs.append({
            "job_id": job_root.name,
            "state": _job_state(job_root, backup_root),
            "source_dir": str(source),
            "source_present": source.is_dir(),
            "source_apparent_bytes": _apparent_bytes(source),
        })

    busy = [
        f"job {job['job_id']} has no completed result" for job in jobs
        if job["state"] != "finished"
    ]
    if backup_root.is_dir():
        busy.extend(
            f"job backup {path.name} is pending restoration"
            for path in sorted(backup_root.iterdir())
        )
    objects = sorted(path for path in objects_root.iterdir()) if objects_root.is_dir() else []
    busy.extend(
        f"download in progress: {path.relative_to(cache_dir)}"
        for path in sorted(objects_root.rglob("*.part")) if objects_root.is_dir()
    )

    job_sources = [
        Path(job["source_dir"]) for job in jobs
        if job["state"] == "finished" and job["source_present"]
    ]
    return {
        "runtime_root": str(runtime_root),
        "cache_dir": str(cache_dir),
        "shared_cache": {
            "object_count": len(objects),
            "apparent_bytes": _apparent_bytes(objects_root),
        },
        "jobs": jobs,
        "busy_reasons": busy,
        "targets": {
            "job_sources": [str(path) for path in job_sources],
            "source_cache": [str(path) for path in objects],
        },
        "reclaimable_bytes": {
            "job_sources": reclaimable_bytes(job_sources),
            "source_cache": reclaimable_bytes(objects),
            "both": reclaimable_bytes([*job_sources, *objects]),
        },
    }


def clean(
    runtime_root: Path,
    *,
    job_sources: bool = False,
    source_cache: bool = False,
    apply: bool = False,
    cache_dir: Path | None = None,
) -> dict[str, Any]:
    """Remove the selected data; without ``apply`` only report what would go."""
    if not (job_sources or source_cache):
        raise CityWorldCacheError("select --job-sources and/or --source-cache")
    report = plan(runtime_root, cache_dir)
    removal: list[Path] = []
    skipped: list[str] = []
    if job_sources:
        removal.extend(Path(path) for path in report["targets"]["job_sources"])
        skipped.extend(
            f"job {job['job_id']}: generation in progress"
            for job in report["jobs"]
            if job["state"] != "finished" and job["source_present"]
        )
    if source_cache:
        if report["busy_reasons"]:
            skipped.append(
                "shared PLATEAU cache: a generation may be running ("
                + "; ".join(report["busy_reasons"]) + ")"
            )
        else:
            removal.extend(Path(path) for path in report["targets"]["source_cache"])

    _ensure_owned(removal, Path(report["runtime_root"]), Path(report["cache_dir"]))
    report.update({
        "applied": apply,
        "selected": {"job_sources": job_sources, "source_cache": source_cache},
        "removal": [str(path) for path in removal],
        "removal_reclaimable_bytes": reclaimable_bytes(removal),
        "skipped": skipped,
    })
    if apply:
        for path in removal:
            shutil.rmtree(path)
    return report


def _ensure_owned(paths: Iterable[Path], runtime_root: Path, cache_dir: Path) -> None:
    jobs_root = runtime_root / "jobs"
    objects_root = cache_dir / "objects"
    for path in paths:
        resolved = path.resolve()
        is_source = (
            resolved.name == "source"
            and resolved.parent.name == "build"
            and resolved.parents[2] == jobs_root
        )
        if not (is_source or resolved.parent == objects_root) or path.is_symlink():
            raise CityWorldCacheError(f"refusing to remove unexpected path: {path}")


def format_bytes(value: int) -> str:
    size = float(value)
    for unit in ("B", "KiB", "MiB", "GiB"):
        if size < 1024 or unit == "GiB":
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024
    raise AssertionError("unreachable")


def render(report: dict[str, Any]) -> str:
    reclaim = report["reclaimable_bytes"]
    shared = report["shared_cache"]
    lines = [
        f"City World runtime : {report['runtime_root']}",
        f"Shared PLATEAU cache: {report['cache_dir']}",
        f"  {shared['object_count']} objects, {format_bytes(shared['apparent_bytes'])}",
        "Jobs:",
    ]
    for job in report["jobs"]:
        source = (
            format_bytes(job["source_apparent_bytes"]) if job["source_present"] else "removed"
        )
        lines.append(f"  {job['job_id']}  {job['state']}  build/source {source}")
    if not report["jobs"]:
        lines.append("  none")
    lines += [
        "Reclaimable disk space (hardlinks shared with the other set are not counted):",
        f"  --job-sources                 : {format_bytes(reclaim['job_sources'])}",
        f"  --source-cache                : {format_bytes(reclaim['source_cache'])}",
        f"  --job-sources --source-cache  : {format_bytes(reclaim['both'])}",
    ]
    for reason in report["busy_reasons"]:
        lines.append(f"[WARN] {reason}")
    if "applied" in report:
        verb = "Removed" if report["applied"] else "Would remove"
        lines.append(
            f"{verb} {len(report['removal'])} path(s), "
            f"{format_bytes(report['removal_reclaimable_bytes'])}:"
        )
        objects_root = str(Path(report["cache_dir"]) / "objects")
        objects = [path for path in report["removal"] if str(Path(path).parent) == objects_root]
        lines.extend(f"  {path}" for path in report["removal"] if path not in objects)
        if objects:
            lines.append(f"  {len(objects)} shared cache object(s) under {objects_root}")
        lines.extend(f"[SKIP] {reason}" for reason in report["skipped"])
        if not report["applied"]:
            lines.append("Dry run only. Re-run with --apply to delete.")
    return "\n".join(lines)


def add_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--job-sources", action="store_true",
        help="remove build/source (raw PLATEAU CityGML) of finished jobs",
    )
    parser.add_argument(
        "--source-cache", action="store_true",
        help="remove the shared PLATEAU download cache (re-downloaded on next generation)",
    )
    parser.add_argument("--apply", action="store_true", help="delete instead of a dry run")
    parser.add_argument("--json", action="store_true", help="print a JSON report")


def run(runtime_root: Path, command: str, args: argparse.Namespace) -> int:
    try:
        if command == "status":
            report = plan(runtime_root)
        else:
            report = clean(
                runtime_root,
                job_sources=args.job_sources,
                source_cache=args.source_cache,
                apply=args.apply,
            )
    except (CityWorldCacheError, OSError) as exc:
        print(f"ERROR: {exc}")
        return 2
    print(json.dumps(report, ensure_ascii=False, indent=2) if args.json else render(report))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("command", choices=("status", "clean"))
    parser.add_argument("--runtime-dir", type=Path, default=default_runtime_dir())
    add_arguments(parser)
    args = parser.parse_args(argv)
    return run(args.runtime_dir, args.command, args)


if __name__ == "__main__":
    raise SystemExit(main())
