#!/usr/bin/env python3
"""Generate lightweight PLATEAU MJCF/GLB City Worlds for six regions."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any


RECIPE_ID = "plateau-city-world-six-regions"
CITY_ID_PATTERN = re.compile(r"^[a-z][a-z0-9-]*$")


class MatrixError(RuntimeError):
    pass


@dataclass(frozen=True)
class Region:
    id: str
    name: str
    latitude: float
    longitude: float
    focus: str
    known_limitations: tuple[str, ...]
    feature_type_overrides: tuple[tuple[str, bool], ...]


@dataclass(frozen=True)
class RegionPaths:
    root: Path
    config: Path
    build: Path
    artifacts: Path
    validation: Path


def repository_root() -> Path:
    return Path(__file__).resolve().parents[2]


def recipe_path() -> Path:
    return repository_root() / "recipes" / "examples" / f"{RECIPE_ID}.yaml"


def work_root() -> Path:
    return repository_root() / "work" / "recipes" / RECIPE_ID


def envsim_root() -> Path:
    raw = os.environ.get("HAKONIWA_ENVSIM_ROOT")
    candidate = Path(raw).expanduser() if raw else repository_root().parent / "hakoniwa-envsim"
    return candidate.resolve()


def requirements_path() -> Path:
    return (
        repository_root() / "recipes" / "requirements"
        / "plateau-citygml-mujoco-walls.txt"
    )


def managed_python() -> Path:
    relative = Path("Scripts/python.exe") if os.name == "nt" else Path("bin/python")
    return work_root() / "python" / relative


def region_paths(region_id: str) -> RegionPaths:
    base = work_root() / "cities" / region_id
    return RegionPaths(
        root=base,
        config=base / "config",
        build=base / "build",
        artifacts=base / "artifacts",
        validation=base / "validation",
    )


def _load_recipe(path: Path | None = None) -> dict[str, Any]:
    target = path or recipe_path()
    exporter = repository_root() / "recipes" / "tools" / "export_recipe_json.rb"
    completed = subprocess.run(
        ["ruby", str(exporter), str(target)],
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode:
        raise MatrixError(completed.stderr.strip() or f"failed to load Recipe: {target}")
    data = json.loads(completed.stdout)
    if data.get("id") != RECIPE_ID:
        raise MatrixError(f"unexpected Recipe id: {data.get('id')!r}")
    return data


def load_matrix(path: Path | None = None) -> tuple[dict[str, Any], list[Region]]:
    raw = _load_recipe(path).get("plateau_city_world_matrix")
    if not isinstance(raw, dict):
        raise MatrixError("plateau_city_world_matrix is missing")
    if raw.get("schema_version") != 1:
        raise MatrixError("plateau_city_world_matrix.schema_version must be 1")
    selection = raw.get("selection")
    if not isinstance(selection, dict):
        raise MatrixError("matrix selection is missing")
    extent = selection.get("half_extent_m")
    if not isinstance(extent, dict):
        raise MatrixError("selection.half_extent_m is missing")
    for axis in ("north_south", "east_west"):
        value = extent.get(axis)
        if not isinstance(value, (int, float)) or isinstance(value, bool) or value <= 0:
            raise MatrixError(f"selection.half_extent_m.{axis} must be positive")
    raw_regions = raw.get("regions")
    if not isinstance(raw_regions, list) or len(raw_regions) != 6:
        raise MatrixError("the smoke matrix must contain exactly six regions")
    regions: list[Region] = []
    seen: set[str] = set()
    for item in raw_regions:
        if not isinstance(item, dict):
            raise MatrixError("every region must be a mapping")
        region_id = item.get("id")
        if not isinstance(region_id, str) or not CITY_ID_PATTERN.fullmatch(region_id):
            raise MatrixError(f"invalid region id: {region_id!r}")
        if region_id in seen:
            raise MatrixError(f"duplicate region id: {region_id}")
        seen.add(region_id)
        center = item.get("center")
        if not isinstance(center, dict):
            raise MatrixError(f"region {region_id} center is missing")
        latitude = center.get("latitude")
        longitude = center.get("longitude")
        if not isinstance(latitude, (int, float)) or not -90 <= latitude <= 90:
            raise MatrixError(f"region {region_id} latitude is invalid")
        if not isinstance(longitude, (int, float)) or not -180 <= longitude <= 180:
            raise MatrixError(f"region {region_id} longitude is invalid")
        limitations = item.get("known_limitations", [])
        if not isinstance(limitations, list) or not all(
            isinstance(value, str) and value for value in limitations
        ):
            raise MatrixError(f"region {region_id} known_limitations must be strings")
        overrides = item.get("feature_types", {})
        if not isinstance(overrides, dict):
            raise MatrixError(f"region {region_id} feature_types must be a mapping")
        unknown_features = sorted(set(overrides) - set(raw["source"]["feature_types"]))
        if unknown_features:
            raise MatrixError(
                f"region {region_id} has unknown feature types: {', '.join(unknown_features)}"
            )
        if not all(isinstance(value, bool) for value in overrides.values()):
            raise MatrixError(f"region {region_id} feature_types values must be boolean")
        regions.append(Region(
            id=region_id,
            name=str(item.get("name", region_id)),
            latitude=float(latitude),
            longitude=float(longitude),
            focus=str(item.get("focus", "generic-city-world-smoke")),
            known_limitations=tuple(limitations),
            feature_type_overrides=tuple(sorted(overrides.items())),
        ))
    return raw, regions


def select_regions(regions: list[Region], region_id: str | None) -> list[Region]:
    if region_id is None:
        return regions
    selected = [region for region in regions if region.id == region_id]
    if not selected:
        raise MatrixError(f"unknown region: {region_id}")
    return selected


def install_requirements() -> Path:
    python = managed_python()
    if not python.is_file():
        environment = python.parent.parent
        environment.parent.mkdir(parents=True, exist_ok=True)
        command = [sys.executable, "-m", "venv", str(environment)]
        print(">", subprocess.list2cmdline(command), flush=True)
        completed = subprocess.run(command, cwd=repository_root(), check=False)
        if completed.returncode:
            raise MatrixError(
                f"Recipe Python environment creation failed with rc={completed.returncode}"
            )
    command = [str(python), "-m", "pip", "install", "-r", str(requirements_path())]
    print(">", subprocess.list2cmdline(command), flush=True)
    completed = subprocess.run(command, cwd=repository_root(), check=False)
    if completed.returncode:
        raise MatrixError(
            f"Recipe Python dependency installation failed with rc={completed.returncode}"
        )
    return python


def output_name(region: Region) -> str:
    return f"plateau-{region.id}-city-world-smoke"


def manifest_text(matrix: dict[str, Any], region: Region, paths: RegionPaths) -> str:
    source = matrix["source"]
    extent = matrix["selection"]["half_extent_m"]
    geometry = matrix["geometry"]
    mjcf = matrix["mjcf"]
    glb = matrix["glb"]
    city_world = matrix["city_world"]
    feature_types = {**source["feature_types"], **dict(region.feature_type_overrides)}
    return f"""version: 1
component: hakoniwa-envsim

pipeline:
  type: plateau-citygml-to-assets

source:
  api_base_url: {source['api_base_url']}
  feature_type: bldg
  feature_types:
    bldg: {str(bool(feature_types['bldg'])).lower()}
    tran: {str(bool(feature_types['tran'])).lower()}
    dem: {str(bool(feature_types['dem'])).lower()}
    frn: {str(bool(feature_types['frn'])).lower()}
    brid: {str(bool(feature_types['brid'])).lower()}
  year: {source['year']}

selection:
  center:
    latitude: {region.latitude}
    longitude: {region.longitude}
  half_extent_m:
    north_south: {extent['north_south']}
    east_west: {extent['east_west']}

geometry:
  base_epsilon_m: {geometry['base_epsilon_m']}
  waste_threshold: {geometry['waste_threshold']}
  wall_thickness_m: {geometry['wall_thickness_m']}
  roof_collision_thickness_m: {geometry['roof_collision_thickness_m']}

mjcf:
  model_name: {output_name(region)}
  collision: {mjcf['collision']}
  floor: {str(bool(mjcf['floor'])).lower()}

glb:
  enabled: {str(bool(glb['enabled'])).lower()}
  lod_policy: {glb['lod_policy']}
  texture_mode: {glb['texture_mode']}

city_world:
  enabled: {str(bool(city_world['enabled'])).lower()}
  terrain_spacing_m: {city_world['terrain_spacing_m']}
  marking_vertical_offset_m: {city_world['marking_vertical_offset_m']}
  bridge_collision_thickness_m: {city_world['bridge_collision_thickness_m']}
  bridge_max_surface_slope_deg: {city_world['bridge_max_surface_slope_deg']}

output:
  build_dir: {paths.build}
  install_dir: {paths.artifacts / 'install'}
  name: {output_name(region)}
"""


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def profile_identity(matrix: dict[str, Any], region: Region) -> str:
    contract = {
        "schema_version": 1,
        "region": {
            "id": region.id,
            "latitude": region.latitude,
            "longitude": region.longitude,
            "focus": region.focus,
            "known_limitations": region.known_limitations,
            "feature_type_overrides": region.feature_type_overrides,
        },
        "source": matrix["source"],
        "selection": matrix["selection"],
        "geometry": matrix["geometry"],
        "mjcf": matrix["mjcf"],
        "glb": matrix["glb"],
        "city_world": matrix["city_world"],
    }
    encoded = json.dumps(
        contract, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def write_region_manifest(matrix: dict[str, Any], region: Region) -> Path:
    paths = region_paths(region.id)
    for directory in (paths.config, paths.build, paths.artifacts, paths.validation):
        directory.mkdir(parents=True, exist_ok=True)
    manifest = paths.config / "hakoniwa-envsim-build.yaml"
    manifest.write_text(manifest_text(matrix, region, paths), encoding="utf-8")
    contract = {
        "schema_version": 1,
        "recipe_id": RECIPE_ID,
        "profile_identity": profile_identity(matrix, region),
        "region": {
            "id": region.id,
            "name": region.name,
            "center": {"latitude": region.latitude, "longitude": region.longitude},
            "focus": region.focus,
            "known_limitations": list(region.known_limitations),
            "feature_types": {
                **matrix["source"]["feature_types"],
                **dict(region.feature_type_overrides),
            },
        },
        "scope": {
            "mjcf_generation": "required",
            "glb_generation": "required",
            "physics_simulation": "not_evaluated",
        },
        "manifest": str(manifest),
    }
    (paths.config / "region-contract.json").write_text(
        json.dumps(contract, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return manifest


def configure_region(matrix: dict[str, Any], region: Region, python: Path) -> int:
    manifest = write_region_manifest(matrix, region)
    command = [str(python), str(envsim_root() / "tools" / "hako.py"), "configure", "--config", str(manifest)]
    print(">", subprocess.list2cmdline(command), flush=True)
    return subprocess.run(command, cwd=envsim_root(), check=False).returncode


def doctor_region(matrix: dict[str, Any], region: Region, python: Path) -> int:
    manifest = write_region_manifest(matrix, region)
    command = [str(python), str(envsim_root() / "tools" / "hako.py"), "doctor", "--config", str(manifest)]
    print(">", subprocess.list2cmdline(command), flush=True)
    return subprocess.run(command, cwd=envsim_root(), check=False).returncode


def _require_file(path: Path, label: str) -> Path:
    if not path.is_file():
        raise MatrixError(f"{label} not found: {path}")
    return path


def validate_world(matrix: dict[str, Any], region: Region) -> dict[str, Any]:
    paths = region_paths(region.id)
    world = paths.build / "world"
    receipt_path = _require_file(world / "city-world-receipt.json", "City World receipt")
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    frame_path = _require_file(Path(receipt["world_frame"]), "world frame")
    frame = json.loads(frame_path.read_text(encoding="utf-8"))
    origin = frame.get("origin", {})
    for key, expected in (("latitude", region.latitude), ("longitude", region.longitude)):
        if not math.isclose(float(origin.get(key, math.nan)), expected, abs_tol=1e-10):
            raise MatrixError(f"{region.id} world-frame {key} mismatch")
    artifacts: dict[str, dict[str, Any]] = {}
    for kind, filename in (("mjcf", "city-world.xml"), ("glb", "city-world.glb")):
        artifact = _require_file(world / filename, f"{region.id} {kind}")
        if receipt.get(kind, {}).get("sha256") != _sha256(artifact):
            raise MatrixError(f"{region.id} {kind} receipt SHA-256 mismatch")
        artifacts[kind] = {
            "path": str(artifact),
            "bytes": artifact.stat().st_size,
            "sha256": _sha256(artifact),
        }
    dataset_path = _require_file(world / "dataset-validation.json", "Dataset Validator JSON")
    dataset = json.loads(dataset_path.read_text(encoding="utf-8"))
    if dataset.get("status") != "ready":
        raise MatrixError(f"{region.id} Dataset Validator is not ready")
    download_path = _require_file(paths.build / "download-manifest.json", "download manifest")
    download = json.loads(download_path.read_text(encoding="utf-8"))
    return {
        "schema_version": 1,
        "status": "done",
        "recipe_id": RECIPE_ID,
        "profile_identity": profile_identity(matrix, region),
        "region": {
            "id": region.id,
            "name": region.name,
            "center": {"latitude": region.latitude, "longitude": region.longitude},
            "focus": region.focus,
        },
        "scope": {
            "mjcf_generation": "passed",
            "glb_generation": "passed",
            "physics_simulation": "not_evaluated",
        },
        "known_limitations": list(region.known_limitations),
        "artifacts": artifacts,
        "dataset_validation": dataset,
        "source": {
            "file_count": len(download.get("files", [])),
            "catalog_status": download.get("catalog_status", {}),
            "years": sorted({
                int(item["year"]) for item in download.get("files", [])
                if isinstance(item.get("year"), int)
            }),
        },
    }


def publish_region(region: Region, result: dict[str, Any]) -> Path:
    paths = region_paths(region.id)
    world_source = paths.build / "world"
    world_target = paths.artifacts / "world"
    components_source = paths.build / "components"
    components_target = paths.artifacts / "components"
    for source, target in (
        (world_source, world_target), (components_source, components_target)
    ):
        if target.exists():
            shutil.rmtree(target)
        shutil.copytree(source, target)
    for name in ("download-manifest.json", "build-receipt.json"):
        shutil.copy2(paths.build / name, paths.artifacts / name)
    # Publish artifact paths, not transient build paths, in the durable result.
    for kind, filename in (("mjcf", "city-world.xml"), ("glb", "city-world.glb")):
        result["artifacts"][kind]["path"] = str(world_target / filename)
    result_path = paths.validation / "result.json"
    result_path.write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return result_path


def reusable_result(matrix: dict[str, Any], region: Region) -> bool:
    result_path = region_paths(region.id).validation / "result.json"
    if not result_path.is_file():
        return False
    try:
        result = json.loads(result_path.read_text(encoding="utf-8"))
        if result.get("status") != "done":
            return False
        if result.get("profile_identity") != profile_identity(matrix, region):
            return False
        for artifact in result.get("artifacts", {}).values():
            path = Path(artifact["path"])
            if not path.is_file() or path.stat().st_size != artifact["bytes"]:
                return False
            if _sha256(path) != artifact["sha256"]:
                return False
        return True
    except (KeyError, OSError, ValueError, json.JSONDecodeError):
        return False


def build_region(
    matrix: dict[str, Any], region: Region, python: Path, *, offline: bool, force: bool
) -> int:
    if reusable_result(matrix, region) and not force:
        print(f"[SKIP] {region.id}: existing result matches the current profile")
        return 0
    if configure_region(matrix, region, python) != 0:
        return 1
    manifest = region_paths(region.id).config / "hakoniwa-envsim-build.yaml"
    command = [str(python), str(envsim_root() / "tools" / "hako.py"), "build", "--config", str(manifest)]
    if offline:
        command.append("--offline")
    print(">", subprocess.list2cmdline(command), flush=True)
    completed = subprocess.run(command, cwd=envsim_root(), check=False)
    if completed.returncode:
        return completed.returncode
    result = validate_world(matrix, region)
    result_path = publish_region(region, result)
    print(f"[OK] {region.id}: {result_path}")
    return 0


def plan(matrix: dict[str, Any], regions: list[Region]) -> int:
    extent = matrix["selection"]["half_extent_m"]
    print("PLATEAU six-region City World asset-generation smoke")
    print(
        "Selection: "
        f"{2 * float(extent['east_west']):g}m east-west x "
        f"{2 * float(extent['north_south']):g}m north-south"
    )
    print("Scope    : MJCF + GLB generation; physics simulation is not evaluated")
    for region in regions:
        status = "DONE" if reusable_result(matrix, region) else "PENDING"
        limitations = "; ".join(region.known_limitations) or "none"
        print(
            f"  [{status}] {region.id:<16} "
            f"center=({region.latitude:.6f},{region.longitude:.6f}) "
            f"focus={region.focus} limitations={limitations}"
        )
    return 0


def _component_text(component: dict[str, Any]) -> str:
    if component.get("status") != "available":
        return str(component.get("status", "unknown"))
    resolution = component.get("lod_resolution")
    if isinstance(resolution, dict):
        effective = resolution.get("effective_lod") or "unknown"
        if resolution.get("fallback_used"):
            return f"{effective} (fallback)"
        return str(effective)
    return str(component.get("source_lod") or component.get("status"))


def summary_row(region: Region, result: dict[str, Any] | None) -> dict[str, Any]:
    if result is None:
        return {
            "region_id": region.id, "region_name": region.name, "status": "pending",
            "buildings": "-", "roads": "-", "road_markings": "-", "bridges": "-",
            "mjcf_bytes": "", "glb_bytes": "", "physics_simulation": "not_evaluated",
            "known_limitations": "; ".join(region.known_limitations),
        }
    components = result["dataset_validation"]["components"]
    return {
        "region_id": region.id,
        "region_name": region.name,
        "status": result["status"],
        "buildings": _component_text(components["buildings"]),
        "roads": _component_text(components["road_surfaces"]),
        "road_markings": _component_text(components["road_markings"]),
        "bridges": _component_text(components.get("bridges", {"status": "not_available"})),
        "mjcf_bytes": result["artifacts"]["mjcf"]["bytes"],
        "glb_bytes": result["artifacts"]["glb"]["bytes"],
        "physics_simulation": result["scope"]["physics_simulation"],
        "known_limitations": "; ".join(result.get("known_limitations", [])),
    }


def summarize(matrix: dict[str, Any], regions: list[Region]) -> int:
    output = work_root() / "summary"
    output.mkdir(parents=True, exist_ok=True)
    rows = []
    results: dict[str, Any] = {}
    for region in regions:
        path = region_paths(region.id).validation / "result.json"
        result = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else None
        rows.append(summary_row(region, result))
        results[region.id] = result
    completed = sum(row["status"] == "done" for row in rows)
    payload = {
        "schema_version": 1,
        "recipe_id": RECIPE_ID,
        "status": "done" if completed == len(regions) else "partial",
        "completed_regions": completed,
        "total_regions": len(regions),
        "scope": {
            "mjcf_generation": "evaluated",
            "glb_generation": "evaluated",
            "physics_simulation": "not_evaluated",
        },
        "rows": rows,
        "results": results,
    }
    json_path = output / "capability-matrix.json"
    csv_path = output / "capability-matrix.csv"
    markdown_path = output / "capability-matrix.md"
    json_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    fields = list(rows[0])
    with csv_path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)
    markdown = [
        "# PLATEAU Six-region City World Capability Matrix",
        "",
        "> Scope: MJCF and GLB generation only. Physics simulation is not evaluated.",
        "",
        "| Region | Status | Buildings | Roads | Road markings | Bridges | MJCF bytes | GLB bytes | Known limitations |",
        "|---|---|---|---|---|---|---:|---:|---|",
    ]
    for row in rows:
        markdown.append(
            f"| {row['region_name']} (`{row['region_id']}`) | {row['status']} | "
            f"{row['buildings']} | {row['roads']} | {row['road_markings']} | {row['bridges']} | "
            f"{row['mjcf_bytes']} | {row['glb_bytes']} | {row['known_limitations']} |"
        )
    markdown_path.write_text("\n".join(markdown) + "\n", encoding="utf-8")
    print(f"Summary JSON: {json_path}")
    print(f"Summary CSV : {csv_path}")
    print(f"Summary MD  : {markdown_path}")
    print(f"Completed   : {completed}/{len(regions)}")
    return 0 if completed == len(regions) else 2


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "command", choices=("plan", "configure", "doctor", "build", "build-all", "summarize")
    )
    parser.add_argument("--region", help="operate on one region id")
    parser.add_argument("--offline", action="store_true", help="reuse previously downloaded CityGML")
    parser.add_argument("--force", action="store_true", help="rebuild even when a matching result exists")
    args = parser.parse_args()
    if args.command == "build" and not args.region:
        parser.error("build requires --region; use build-all for the complete matrix")
    if args.command == "build-all" and args.region:
        parser.error("build-all does not accept --region")
    if args.offline and args.command not in {"build", "build-all"}:
        parser.error("--offline is valid only with build or build-all")
    if args.force and args.command not in {"build", "build-all"}:
        parser.error("--force is valid only with build or build-all")
    try:
        matrix, all_regions = load_matrix()
        regions = select_regions(all_regions, args.region)
        if args.command == "plan":
            return plan(matrix, regions)
        if args.command == "summarize":
            return summarize(matrix, all_regions)
        python = install_requirements()
        if args.command == "configure":
            for region in regions:
                if configure_region(matrix, region, python) != 0:
                    return 1
            return 0
        if args.command == "doctor":
            for region in regions:
                if doctor_region(matrix, region, python) != 0:
                    return 1
            return 0
        for region in regions:
            if build_region(
                matrix, region, python, offline=args.offline, force=args.force
            ) != 0:
                return 1
        return summarize(matrix, all_regions) if args.command == "build-all" else 0
    except (MatrixError, OSError, ValueError, KeyError, json.JSONDecodeError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
