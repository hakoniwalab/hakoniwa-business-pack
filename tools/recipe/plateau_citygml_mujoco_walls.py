#!/usr/bin/env python3
"""Operate the PLATEAU CityGML to MuJoCo/GLB City World Recipe."""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import math
import os
import re
import shutil
import subprocess
import sys
import tarfile
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path
from typing import Any

RECIPE_ID = "plateau-citygml-mujoco-walls"
MAP_ORIGIN_PATTERN = {
    "latitude": re.compile(r"(?m)^\s*let\s+ORIGIN_LAT\s*=\s*([-+0-9.eE]+)\s*;"),
    "longitude": re.compile(r"(?m)^\s*let\s+ORIGIN_LON\s*=\s*([-+0-9.eE]+)\s*;"),
}


class RecipeError(RuntimeError):
    pass


@dataclass(frozen=True)
class RecipePaths:
    root: Path
    config: Path
    build: Path
    artifacts: Path
    validation: Path


def root() -> Path:
    return Path(__file__).resolve().parents[2]


def recipe_file() -> Path:
    return root() / "recipes" / "examples" / f"{RECIPE_ID}.yaml"


def python_requirements_file() -> Path:
    return root() / "recipes" / "requirements" / f"{RECIPE_ID}.txt"


def python_environment() -> Path:
    return paths().root / "python"


def recipe_python() -> Path:
    relative = Path("Scripts/python.exe") if os.name == "nt" else Path("bin/python")
    return python_environment() / relative


def paths() -> RecipePaths:
    base = root() / "work" / "recipes" / RECIPE_ID
    return RecipePaths(base, base / "config", base / "build", base / "artifacts", base / "validation")


def _source_root(env_name: str, default_name: str) -> Path:
    raw = os.environ.get(env_name)
    candidate = Path(raw).expanduser() if raw else root().parent / default_name
    return candidate.resolve()


def envsim_root() -> Path:
    return _source_root("HAKONIWA_ENVSIM_ROOT", "hakoniwa-envsim")


def map_viewer_root() -> Path:
    return _source_root("HAKONIWA_MAP_VIEWER_ROOT", "hakoniwa-map-viewer")


def drone_core_root() -> Path:
    return _source_root("HAKONIWA_DRONE_CORE_ROOT", "hakoniwa-drone-core")


def _required(path: Path, label: str) -> Path:
    if not path.exists():
        raise RecipeError(f"{label} not found: {path}")
    return path


def _load_recipe() -> dict[str, Any]:
    exporter = root() / "recipes" / "tools" / "export_recipe_json.rb"
    completed = subprocess.run(
        ["ruby", str(exporter), str(recipe_file())], capture_output=True, text=True, check=False
    )
    if completed.returncode:
        raise RecipeError(completed.stderr.strip() or "failed to load Recipe YAML")
    data = json.loads(completed.stdout)
    if data.get("id") != RECIPE_ID or not isinstance(data.get("plateau_citygml"), dict):
        raise RecipeError("Recipe PLATEAU configuration is missing")
    return data


def read_map_origin(map_root: Path) -> dict[str, float]:
    ui = _required(map_root / "src" / "client" / "src" / "ui.js", "Map Viewer UI")
    source = ui.read_text(encoding="utf-8")
    result: dict[str, float] = {}
    for key, pattern in MAP_ORIGIN_PATTERN.items():
        match = pattern.search(source)
        if match is None:
            raise RecipeError(f"Map Viewer {key} origin assignment was not found: {ui}")
        result[key] = float(match.group(1))
    return result


def read_drone_simulation_location(drone_root: Path) -> dict[str, float]:
    path = _required(
        drone_root / "config" / "drone" / "mujoco-shibuya-api-1" / "drone_config_0.json",
        "Drone Core Shibuya config",
    )
    data = json.loads(path.read_text(encoding="utf-8"))
    location = data["simulation"]["location"]
    return {key: float(location[key]) for key in ("latitude", "longitude", "altitude")}


def configured_origin(config: dict[str, Any]) -> dict[str, float]:
    center = config["selection"]["center"]
    return {"latitude": float(center["latitude"]), "longitude": float(center["longitude"])}


def _manifest_text(config: dict[str, Any], origin: dict[str, float], recipe_paths: RecipePaths) -> str:
    source = config["source"]
    extent = config["selection"]["half_extent_m"]
    geometry = config["geometry"]
    mjcf = config["mjcf"]
    glb = config["glb"]
    city_world = config["city_world"]
    feature_types = source["feature_types"]
    return f"""version: 1
component: hakoniwa-envsim

pipeline:
  type: plateau-citygml-to-assets

source:
  api_base_url: {source['api_base_url']}
  feature_type: {source['feature_type']}
  feature_types:
    bldg: {str(bool(feature_types['bldg'])).lower()}
    tran: {str(bool(feature_types['tran'])).lower()}
    dem: {str(bool(feature_types['dem'])).lower()}
    frn: {str(bool(feature_types['frn'])).lower()}
  year: {source['year']}

selection:
  center:
    latitude: {origin['latitude']}
    longitude: {origin['longitude']}
  half_extent_m:
    north_south: {extent['north_south']}
    east_west: {extent['east_west']}

geometry:
  base_epsilon_m: {geometry['base_epsilon_m']}
  waste_threshold: {geometry['waste_threshold']}
  wall_thickness_m: {geometry['wall_thickness_m']}

mjcf:
  model_name: {mjcf['model_name']}
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

output:
  build_dir: {recipe_paths.build}
  install_dir: {recipe_paths.artifacts / 'install'}
  name: plateau-numazu-city-world-200m
"""


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def validate_glb_contract(glb_path: Path, receipt_path: Path, origin: dict[str, float]) -> dict[str, Any]:
    glb = _required(glb_path, "generated PLATEAU GLB")
    receipt = json.loads(_required(receipt_path, "PLATEAU GLB receipt").read_text(encoding="utf-8"))
    coordinate = receipt.get("coordinate_system", {})
    receipt_origin = coordinate.get("origin", {})
    if coordinate.get("glb") != "X=East,Y=Up,Z=-North":
        raise RecipeError("generated GLB has an unexpected display-coordinate contract")
    for key in ("latitude", "longitude"):
        if not math.isclose(float(receipt_origin.get(key, math.nan)), origin[key], abs_tol=1e-10):
            raise RecipeError(f"generated GLB origin mismatch: {key}")
    if int(receipt.get("bytes", -1)) != glb.stat().st_size or receipt.get("sha256") != _sha256(glb):
        raise RecipeError("generated GLB bytes or SHA-256 do not match its receipt")
    if int(receipt.get("triangles", 0)) <= 0:
        raise RecipeError("generated GLB contains no triangles")
    return receipt


def validate_city_world_contract(world_dir: Path, origin: dict[str, float]) -> dict[str, Any]:
    receipt_path = _required(world_dir / "city-world-receipt.json", "City World receipt")
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    frame_path = _required(Path(receipt["world_frame"]), "City World frame")
    frame = json.loads(frame_path.read_text(encoding="utf-8"))
    for key in ("latitude", "longitude"):
        if not math.isclose(
            float(frame["origin"].get(key, math.nan)), origin[key], abs_tol=1e-10
        ):
            raise RecipeError(f"City World origin mismatch: {key}")
    for section in ("mjcf", "glb"):
        artifact = _required(Path(receipt[section]["path"]), f"City World {section}")
        if receipt[section].get("sha256") != _sha256(artifact):
            raise RecipeError(f"City World {section} SHA-256 mismatch")
    validation_path = _required(world_dir / "dataset-validation.json", "dataset validation")
    validation = json.loads(validation_path.read_text(encoding="utf-8"))
    if validation.get("status") != "ready":
        raise RecipeError("City World dataset validation is not ready")
    return validation


def validate_selection_coverage(selection_path: Path, contract: dict[str, Any]) -> dict[str, int]:
    selection = json.loads(_required(selection_path, "PLATEAU LOD1 selection").read_text(encoding="utf-8"))
    polygons = selection.get("polygons", [])
    minimum_buildings = int(contract["minimum_buildings"])
    radius = float(contract["center_half_extent_m"])
    minimum_center_buildings = int(contract["minimum_center_buildings"])
    center_count = 0
    for polygon in polygons:
        vertices = polygon.get("vertices", [])
        if not vertices:
            continue
        east = sum(float(point[0]) for point in vertices) / len(vertices)
        north = sum(float(point[1]) for point in vertices) / len(vertices)
        center_count += abs(east) <= radius and abs(north) <= radius
    if len(polygons) < minimum_buildings:
        raise RecipeError(
            f"PLATEAU selection is unexpectedly sparse: {len(polygons)} < {minimum_buildings} buildings"
        )
    if center_count < minimum_center_buildings:
        raise RecipeError(
            "PLATEAU selection does not cover the geographic center: "
            f"{center_count} < {minimum_center_buildings} buildings within +/-{radius}m"
        )
    return {"buildings": len(polygons), "center_buildings": center_count}


def install_python_requirements() -> Path:
    requirements = _required(python_requirements_file(), "Recipe Python requirements")
    python = recipe_python()
    if not python.is_file():
        environment = python_environment()
        environment.parent.mkdir(parents=True, exist_ok=True)
        command = [sys.executable, "-m", "venv", str(environment)]
        print(">", subprocess.list2cmdline(command), flush=True)
        completed = subprocess.run(command, cwd=root(), check=False)
        if completed.returncode:
            raise RecipeError(
                "Recipe Python environment creation failed: "
                f"interpreter={sys.executable}, exit={completed.returncode}"
            )
    command = [str(python), "-m", "pip", "install", "-r", str(requirements)]
    print(">", subprocess.list2cmdline(command), flush=True)
    completed = subprocess.run(command, cwd=root(), check=False)
    if completed.returncode:
        raise RecipeError(
            "Recipe Python dependency installation failed: "
            f"interpreter={python}, exit={completed.returncode}"
        )
    print(f"OK: Recipe Python: {python}")
    return python


def configure() -> int:
    python = install_python_requirements()
    data = _load_recipe()["plateau_citygml"]
    origin = configured_origin(data)
    recipe_paths = paths()
    for directory in (recipe_paths.config, recipe_paths.build, recipe_paths.artifacts, recipe_paths.validation):
        directory.mkdir(parents=True, exist_ok=True)
    manifest = recipe_paths.config / "hakoniwa-envsim-build.yaml"
    manifest.write_text(_manifest_text(data, origin, recipe_paths), encoding="utf-8")
    contract = {
        "schema_version": 1,
        "recipe_id": RECIPE_ID,
        "configured_origin": origin,
        "center_contract": data["selection"]["center_contract"],
        "manifest": str(manifest),
        "selection_half_extent_m": data["selection"]["half_extent_m"],
        "coordinate_systems": {
            "mjcf": "X=North,Y=-East,Z=Up",
            "glb": "X=East,Y=Up,Z=-North",
        },
    }
    (recipe_paths.config / "coordinate-contract.json").write_text(
        json.dumps(contract, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    completed = subprocess.run(
        [str(python), str(envsim_root() / "tools" / "hako.py"), "configure", "--config", str(manifest)],
        cwd=envsim_root(), check=False,
    )
    if completed.returncode:
        return completed.returncode
    print(f"OK: Recipe Envsim manifest: {manifest}")
    print(f"OK: configured City World origin: {origin['latitude']}, {origin['longitude']}")
    return 0


def doctor() -> int:
    recipe_paths = paths()
    manifest = recipe_paths.config / "hakoniwa-envsim-build.yaml"
    if (not manifest.is_file() or not recipe_python().is_file()) and configure() != 0:
        return 1
    recipe_config = _load_recipe()["plateau_citygml"]
    origin = configured_origin(recipe_config)
    completed = subprocess.run(
        [str(recipe_python()), str(envsim_root() / "tools" / "hako.py"), "doctor", "--config", str(manifest)],
        cwd=envsim_root(), check=False,
    )
    if completed.returncode:
        return completed.returncode
    print(f"OK: Recipe-configured PLATEAU origin: {origin['latitude']}, {origin['longitude']}")
    validation_candidates = (
        recipe_paths.artifacts / "city-world" / "dataset-validation.json",
        recipe_paths.build / "world" / "dataset-validation.json",
    )
    existing_validation = next(
        (candidate for candidate in validation_candidates if candidate.is_file()), None
    )
    if existing_validation is None:
        print("OK: Dataset Validator will report LOD fallback and unavailable features after build")
    else:
        print(f"Dataset Validator: {existing_validation}")
        displayed = subprocess.run(
            [
                str(recipe_python()),
                str(envsim_root() / "src" / "city_pipeline" / "city_dataset_validator.py"),
                "--input", str(existing_validation),
            ],
            cwd=envsim_root(), check=False,
        )
        if displayed.returncode:
            return displayed.returncode
    return 0


def build(offline: bool = False) -> int:
    if configure() != 0 or doctor() != 0:
        return 1
    recipe_paths = paths()
    manifest = recipe_paths.config / "hakoniwa-envsim-build.yaml"
    command = [str(recipe_python()), str(envsim_root() / "tools" / "hako.py"), "build", "--config", str(manifest)]
    if offline:
        command.append("--offline")
    completed = subprocess.run(
        command,
        cwd=envsim_root(), check=False,
    )
    if completed.returncode:
        return completed.returncode
    recipe_config = _load_recipe()["plateau_citygml"]
    origin = configured_origin(recipe_config)
    coverage = validate_selection_coverage(
        recipe_paths.build / "plateau-numazu-city-world-200m-lod1.json",
        recipe_config["acceptance"],
    )
    dataset_validation = validate_city_world_contract(recipe_paths.build / "world", origin)
    print(
        "OK: PLATEAU selection coverage: "
        f"{coverage['buildings']} buildings, {coverage['center_buildings']} near center"
    )
    city_world_artifacts = recipe_paths.artifacts / "city-world"
    if city_world_artifacts.exists():
        shutil.rmtree(city_world_artifacts)
    shutil.copytree(_required(recipe_paths.build / "world", "City World output"), city_world_artifacts)
    components_artifacts = recipe_paths.artifacts / "components"
    if components_artifacts.exists():
        shutil.rmtree(components_artifacts)
    shutil.copytree(_required(recipe_paths.build / "components", "City World components"), components_artifacts)
    for name in ("download-manifest.json", "build-receipt.json"):
        shutil.copy2(_required(recipe_paths.build / name, name), recipe_paths.artifacts / name)
    marking_status = dataset_validation["components"]["road_markings"]["status"]
    print(f"OK: Dataset Validator road markings: {marking_status}")
    print(f"OK: Recipe artifacts: {recipe_paths.artifacts}")
    return 0


def _git_blob(repository: Path, revision: str, path: str) -> bytes:
    completed = subprocess.run(
        ["git", "show", f"{revision}:{path}"], cwd=repository, capture_output=True, check=False
    )
    if completed.returncode:
        raise RecipeError(
            f"Git object is unavailable: {repository} {revision}:{path}; "
            "use a full clone or fetch the required revision"
        )
    return completed.stdout


def _extract_archive(data: bytes, destination: Path) -> None:
    destination.mkdir(parents=True, exist_ok=True)
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as archive:
        for member in archive.getmembers():
            member_path = Path(member.name)
            if member_path.is_absolute() or ".." in member_path.parts:
                raise RecipeError(f"unsafe path in historical archive: {member.name}")
            if not member.isfile():
                continue
            source = archive.extractfile(member)
            if source is None:
                raise RecipeError(f"failed to read historical archive member: {member.name}")
            target = destination / member_path
            target.parent.mkdir(parents=True, exist_ok=True)
            with source, target.open("wb") as output:
                shutil.copyfileobj(source, output)


def _run(command: list[str], cwd: Path) -> None:
    print("+", subprocess.list2cmdline(command))
    subprocess.run(command, cwd=cwd, check=True)


def _materialize_legacy_pipeline(
    repository: Path,
    revision: str,
    configured_paths: dict[str, str],
    destination: Path,
) -> dict[str, Path]:
    """Materialize the immutable historical converter for regression only."""
    destination.mkdir(parents=True, exist_ok=True)
    scripts: dict[str, Path] = {}
    for role, source_path in configured_paths.items():
        target = destination / f"{role}.py"
        target.write_bytes(_git_blob(repository, revision, source_path))
        scripts[role] = target
    return scripts


def _building_geoms(xml_path: Path) -> dict[str, dict[str, str]]:
    root_element = ET.parse(xml_path).getroot()
    output: dict[str, dict[str, str]] = {}
    for geom in root_element.findall(".//geom"):
        name = geom.get("name", "")
        if not name.startswith("geom_bldg_"):
            continue
        key = name.removeprefix("geom_")
        output[key] = {
            attribute: geom.get(attribute, "")
            for attribute in ("type", "size", "pos", "euler", "rgba", "contype", "conaffinity")
        }
    return output


def compare_building_geoms(actual_xml: Path, expected_xml: Path, tolerance: float) -> dict[str, Any]:
    actual = _building_geoms(actual_xml)
    expected = _building_geoms(expected_xml)
    missing = sorted(set(expected) - set(actual))
    extra = sorted(set(actual) - set(expected))
    mismatches: list[dict[str, Any]] = []
    max_numeric_error = 0.0
    for name in sorted(set(actual) & set(expected)):
        for attribute in ("type", "rgba", "contype", "conaffinity"):
            if actual[name][attribute] != expected[name][attribute]:
                mismatches.append({"geom": name, "attribute": attribute, "expected": expected[name][attribute], "actual": actual[name][attribute]})
        for attribute in ("size", "pos", "euler"):
            left = [float(value) for value in actual[name][attribute].split()]
            right = [float(value) for value in expected[name][attribute].split()]
            if len(left) != len(right):
                mismatches.append({"geom": name, "attribute": attribute, "expected": expected[name][attribute], "actual": actual[name][attribute]})
                continue
            error = max((abs(a - b) for a, b in zip(left, right)), default=0.0)
            max_numeric_error = max(max_numeric_error, error)
            if error > tolerance:
                mismatches.append({"geom": name, "attribute": attribute, "max_error": error, "expected": expected[name][attribute], "actual": actual[name][attribute]})
        if len(mismatches) >= 100:
            break
    return {
        "status": "MATCHED" if not missing and not extra and not mismatches else "MISMATCHED",
        "actual_geom_count": len(actual),
        "expected_geom_count": len(expected),
        "missing_count": len(missing),
        "extra_count": len(extra),
        "mismatch_count": len(mismatches),
        "max_numeric_error": max_numeric_error,
        "tolerance": tolerance,
        "missing_sample": missing[:20],
        "extra_sample": extra[:20],
        "mismatch_sample": mismatches[:20],
    }


def regression() -> int:
    python = install_python_requirements()
    recipe = _load_recipe()["plateau_citygml"]
    config = recipe["regression"]
    origin = read_map_origin(map_viewer_root())
    recipe_paths = paths()
    regression_root = recipe_paths.build / "regression"
    source_root = regression_root / "source"
    regression_root.mkdir(parents=True, exist_ok=True)
    archive = _git_blob(envsim_root(), config["source_archive_revision"], config["source_archive_path"])
    if source_root.exists():
        shutil.rmtree(source_root)
    _extract_archive(archive, source_root)
    extent = config["half_extent_m"]
    (source_root / "query_meta.json").write_text(json.dumps({
        "center_lat": origin["latitude"], "center_lon": origin["longitude"],
        "ns_m": extent["north_south"], "ew_m": extent["east_west"],
    }, indent=2) + "\n", encoding="utf-8")

    legacy_pipeline = _materialize_legacy_pipeline(
        envsim_root(),
        config["final_contract_revision"],
        config["legacy_pipeline"],
        regression_root / "legacy-pipeline",
    )
    lod1 = regression_root / "shibuya-reference-lod1.json"
    walls = regression_root / "shibuya-reference-walls.json"
    generated = regression_root / "shibuya-reference.xml"
    _run([str(python), str(legacy_pipeline["lod1_extract"]), "--in", str(source_root), "--out", str(lod1), "--to-epsg", "6677", "--src-epsg", "4326"], regression_root)
    _run([str(python), str(legacy_pipeline["wall_convert"]), "--in", str(lod1), "--out", str(walls), "--waste-threshold", "1.0", "--wall-thickness", "0.1"], regression_root)
    _run([str(python), str(legacy_pipeline["mjcf_convert"]), "--inp", str(walls), "--out", str(generated)], regression_root)

    reference = _required(drone_core_root() / config["reference_drone_xml"], "Drone Core Shibuya reference XML")
    result = compare_building_geoms(generated, reference, float(config["numeric_tolerance"]))
    result.update({
        "schema_version": 1,
        "recipe_id": RECIPE_ID,
        "map_viewer_origin": origin,
        "historical_selection_half_extent_m": extent,
        "historical_source": {
            "repository": str(envsim_root()),
            "revision": config["source_archive_revision"],
            "path": config["source_archive_path"],
        },
        "legacy_pipeline": {
            "owner": "hakoniwa-business-pack",
            "source_revision": config["final_contract_revision"],
            "scripts": config["legacy_pipeline"],
        },
        "reference_xml": str(reference),
        "generated_xml": str(generated),
    })
    expected_count = int(config["expected_building_geoms"])
    if result["expected_geom_count"] != expected_count:
        result["status"] = "MISMATCHED"
        result["reference_contract_error"] = f"expected {expected_count} reference geoms"
    recipe_paths.validation.mkdir(parents=True, exist_ok=True)
    report = recipe_paths.validation / "shibuya-regression.json"
    report.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Regression report: {report}")
    print(json.dumps({key: result[key] for key in ("status", "actual_geom_count", "expected_geom_count", "mismatch_count", "max_numeric_error", "tolerance")}, indent=2))
    return 0 if result["status"] == "MATCHED" else 1


def smoke() -> int:
    python = install_python_requirements()
    completed = subprocess.run(
        [str(python), str(envsim_root() / "tools" / "hako.py"), "smoke"],
        cwd=envsim_root(), check=False,
    )
    return completed.returncode


def main() -> int:
    parser = argparse.ArgumentParser(description="Operate the PLATEAU CityGML MuJoCo wall Recipe")
    parser.add_argument("command", choices=("configure", "doctor", "build", "regression", "smoke"))
    parser.add_argument(
        "--offline",
        action="store_true",
        help="reuse previously downloaded CityGML (valid only with build)",
    )
    args = parser.parse_args()
    if args.offline and args.command != "build":
        parser.error("--offline is valid only with build")
    try:
        return {
            "configure": configure,
            "doctor": doctor,
            "build": lambda: build(offline=args.offline),
            "regression": regression,
            "smoke": smoke,
        }[args.command]()
    except (RecipeError, OSError, ValueError, KeyError, json.JSONDecodeError, ET.ParseError, subprocess.CalledProcessError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
