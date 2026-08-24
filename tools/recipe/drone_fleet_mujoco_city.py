#!/usr/bin/env python3
"""Build one shared MuJoCo City World containing a fleet of drones.

This helper is intentionally owned by the non-ICRA ``drone-fleet-single-host``
path.  It does not change the performance experiment Recipes or their shared
runtime.  The generated MJB is the common model loaded by every MuJoCo drone
instance in the single Drone Service process.
"""

from __future__ import annotations

import argparse
import ctypes
import hashlib
import importlib.util
import json
import math
import os
import re
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

try:
    from tools.mujoco_model_compiler import (
        MujocoCompileError,
        compile_mujoco_xml,
        find_mujoco_library,
    )
    from tools.recipe import drone_shibuya_gamepad as city_drone
except ModuleNotFoundError:
    sys.path.insert(0, str(Path(__file__).absolute().parents[1]))
    from mujoco_model_compiler import (  # type: ignore[no-redef]
        MujocoCompileError,
        compile_mujoco_xml,
        find_mujoco_library,
    )
    import drone_shibuya_gamepad as city_drone  # type: ignore[no-redef]


ROOT = Path(__file__).absolute().parents[2]
DEFAULT_DRONE_ROOT = ROOT.parent / "hakoniwa-drone-pro"
DEFAULT_OUTPUT = (
    ROOT
    / "work"
    / "recipes"
    / "drone-fleet-single-host"
    / "config"
    / "drone"
    / "mujoco-city-fleet"
)
DRONE_COLLISION_MASK = {"contype": "2", "conaffinity": "1"}
MODEL_SIZE = {"nstack": "40000000", "nconmax": "500000"}
DRONE_BODY_PATTERN = re.compile(r"d[1-9][0-9]*_b_drone_base")
LANDING_GEAR_CLEARANCE_M = 0.20
SPAWN_CLEARANCE_RADIUS_M = 0.75
SPAWN_MIN_SEPARATION_M = 2.0
SURFACE_MATCH_TOLERANCE_M = 0.15
MAX_SPAWN_SLOPE_DELTA_M = 0.25


class FleetMujocoError(RuntimeError):
    pass


class _MujocoRayScene:
    """Query the highest collision surface without depending on mujoco-python."""

    def __init__(self, xml_path: Path, library_path: Path):
        self.xml_path = xml_path.resolve()
        self.library = ctypes.CDLL(str(library_path.resolve()))
        self.model: int | None = None
        self.data: int | None = None

    def __enter__(self) -> "_MujocoRayScene":
        lib = self.library
        lib.mj_loadXML.argtypes = [
            ctypes.c_char_p,
            ctypes.c_void_p,
            ctypes.c_char_p,
            ctypes.c_int,
        ]
        lib.mj_loadXML.restype = ctypes.c_void_p
        lib.mj_makeData.argtypes = [ctypes.c_void_p]
        lib.mj_makeData.restype = ctypes.c_void_p
        lib.mj_forward.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
        lib.mj_ray.argtypes = [
            ctypes.c_void_p,
            ctypes.c_void_p,
            ctypes.POINTER(ctypes.c_double),
            ctypes.POINTER(ctypes.c_double),
            ctypes.c_void_p,
            ctypes.c_ubyte,
            ctypes.c_int,
            ctypes.POINTER(ctypes.c_int),
        ]
        lib.mj_ray.restype = ctypes.c_double
        lib.mj_deleteData.argtypes = [ctypes.c_void_p]
        lib.mj_deleteModel.argtypes = [ctypes.c_void_p]
        error = ctypes.create_string_buffer(4096)
        self.model = lib.mj_loadXML(
            os.fsencode(self.xml_path), None, error, len(error)
        )
        if not self.model:
            detail = error.value.decode("utf-8", errors="replace")
            raise FleetMujocoError(
                f"MuJoCo could not load ray-query model {self.xml_path}: {detail}"
            )
        self.data = lib.mj_makeData(self.model)
        if not self.data:
            lib.mj_deleteModel(self.model)
            self.model = None
            raise FleetMujocoError(
                f"MuJoCo could not allocate ray-query data for {self.xml_path}"
            )
        lib.mj_forward(self.model, self.data)
        return self

    def __exit__(self, *_args: object) -> None:
        if self.data:
            self.library.mj_deleteData(self.data)
            self.data = None
        if self.model:
            self.library.mj_deleteModel(self.model)
            self.model = None

    def height(self, x_m: float, y_m: float) -> float:
        if not self.model or not self.data:
            raise FleetMujocoError("MuJoCo ray-query scene is not open")
        ray_origin_z = 10_000.0
        point = (ctypes.c_double * 3)(x_m, y_m, ray_origin_z)
        direction = (ctypes.c_double * 3)(0.0, 0.0, -1.0)
        geom_id = ctypes.c_int(-1)
        distance = self.library.mj_ray(
            self.model,
            self.data,
            point,
            direction,
            None,
            1,
            -1,
            ctypes.byref(geom_id),
        )
        if distance < 0.0 or geom_id.value < 0:
            raise FleetMujocoError(
                f"no collision surface below local point ({x_m:.3f}, {y_m:.3f})"
            )
        return ray_origin_z - float(distance)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _candidate_spawn_centers(
    half_extent_m: dict[str, Any], *, spacing_m: float = SPAWN_MIN_SEPARATION_M
) -> list[tuple[float, float]]:
    north_south = float(half_extent_m.get("north_south", 0.0))
    east_west = float(half_extent_m.get("east_west", 0.0))
    limit_x = max(0.0, north_south - SPAWN_CLEARANCE_RADIUS_M)
    limit_y = max(0.0, east_west - SPAWN_CLEARANCE_RADIUS_M)
    max_ring = int(min(max(limit_x, limit_y), 40.0) // spacing_m)
    candidates: list[tuple[float, float]] = [(0.0, 0.0)]
    for ring in range(1, max_ring + 1):
        radius = ring * spacing_m
        ring_points: list[tuple[float, float]] = [
            (radius, 0.0),
            (-radius, 0.0),
            (0.0, radius),
            (0.0, -radius),
        ]
        for offset in range(1, ring):
            value = offset * spacing_m
            other = radius - value
            ring_points.extend(
                (
                    (value, other),
                    (-value, other),
                    (value, -other),
                    (-value, -other),
                )
            )
        for x_m, y_m in ring_points:
            if abs(x_m) <= limit_x and abs(y_m) <= limit_y:
                candidates.append((x_m, y_m))
    return candidates


def _clearance_probe_points(x_m: float, y_m: float) -> list[tuple[float, float]]:
    diagonal = SPAWN_CLEARANCE_RADIUS_M / math.sqrt(2.0)
    offsets = (
        (0.0, 0.0),
        (SPAWN_CLEARANCE_RADIUS_M, 0.0),
        (-SPAWN_CLEARANCE_RADIUS_M, 0.0),
        (0.0, SPAWN_CLEARANCE_RADIUS_M),
        (0.0, -SPAWN_CLEARANCE_RADIUS_M),
        (diagonal, diagonal),
        (-diagonal, diagonal),
        (diagonal, -diagonal),
        (-diagonal, -diagonal),
    )
    return [(x_m + dx, y_m + dy) for dx, dy in offsets]


def _select_safe_spawn_points(
    *,
    drone_count: int,
    half_extent_m: dict[str, Any],
    terrain_height: Any,
    city_height: Any,
) -> list[dict[str, float]]:
    selected: list[dict[str, float]] = []
    for x_m, y_m in _candidate_spawn_centers(half_extent_m):
        probes = _clearance_probe_points(x_m, y_m)
        terrain = [float(terrain_height(x, y)) for x, y in probes]
        city = [float(city_height(x, y)) for x, y in probes]
        if any(
            city_z - terrain_z > SURFACE_MATCH_TOLERANCE_M
            for terrain_z, city_z in zip(terrain, city)
        ):
            continue
        if max(terrain) - min(terrain) > MAX_SPAWN_SLOPE_DELTA_M:
            continue
        if any(
            math.hypot(x_m - item["x_m"], y_m - item["y_m"])
            < SPAWN_MIN_SEPARATION_M
            for item in selected
        ):
            continue
        selected.append(
            {
                "x_m": x_m,
                "y_m": y_m,
                "terrain_height_m": terrain[0],
                "surface_height_m": city[0],
            }
        )
        if len(selected) == drone_count:
            return selected
    raise FleetMujocoError(
        f"could not find {drone_count} safe launch points within 40 m of the "
        "City World center; reduce drone_count or select a more open area"
    )


def _formation_targets(
    show: dict[str, Any], *, show_path: Path | None = None
) -> list[tuple[float, float]]:
    options = show.get("options", {})
    center = options.get("center", [0.0, 0.0, 0.0])
    scale = float(options.get("scale", 1.0))
    targets: list[tuple[float, float]] = []
    formations = show.get("formations", {})
    if isinstance(formations, dict):
        values = list(formations.values())
    else:
        values = []
    if show_path is not None:
        for entry in show.get("formation_files", []):
            if not isinstance(entry, dict) or not isinstance(entry.get("path"), str):
                continue
            formation_path = (show_path.parent / entry["path"]).resolve()
            if not formation_path.is_file():
                raise FleetMujocoError(
                    f"formation file referenced by City fleet is missing: {formation_path}"
                )
            values.append(json.loads(formation_path.read_text(encoding="utf-8")))
    for formation in values:
        if not isinstance(formation, dict):
            continue
        for point in formation.get("points", []):
            if isinstance(point, list) and len(point) >= 2:
                targets.append(
                    (
                        float(center[0]) + scale * float(point[0]),
                        float(center[1]) + scale * float(point[1]),
                    )
                )
    return targets


def _path_points(
    start: tuple[float, float], end: tuple[float, float], spacing_m: float = 0.5
) -> list[tuple[float, float]]:
    distance = math.dist(start, end)
    divisions = max(1, math.ceil(distance / spacing_m))
    return [
        (
            start[0] + (end[0] - start[0]) * index / divisions,
            start[1] + (end[1] - start[1]) * index / divisions,
        )
        for index in range(divisions + 1)
    ]


def _load_generator(drone_root: Path):
    script = drone_root / "tools" / "gen_mujoco_multidrone_xml.py"
    if not script.is_file():
        raise FleetMujocoError(f"multi-drone MuJoCo generator not found: {script}")
    spec = importlib.util.spec_from_file_location(
        "hakoniwa_drone_pro_mujoco_fleet_generator", script
    )
    if spec is None or spec.loader is None:
        raise FleetMujocoError(f"cannot load multi-drone generator: {script}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _generate_base_fleet_xml(
    drone_root: Path, drone_count: int, destination: Path
) -> None:
    generator = _load_generator(drone_root)
    template_root = drone_root / "config" / "drone" / "fleets" / "types"
    scene = template_root / "mujoco-scene.xml.template"
    drone = template_root / "mujoco-drone.xml.template"
    if not scene.is_file() or not drone.is_file():
        raise FleetMujocoError(
            f"MuJoCo fleet templates are incomplete under {template_root}"
        )
    xml = generator.generate_xml(
        scene.read_text(encoding="utf-8"),
        drone.read_text(encoding="utf-8").strip(),
        drone_count,
    )
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(xml, encoding="utf-8")


def _remove_demo_landmarks(root: ET.Element) -> list[str]:
    worldbody = root.find("worldbody")
    if worldbody is None:
        raise FleetMujocoError("generated fleet MJCF has no worldbody")
    removed: list[str] = []
    for body in list(worldbody.findall("body")):
        name = body.get("name", "")
        if not DRONE_BODY_PATTERN.fullmatch(name):
            worldbody.remove(body)
            removed.append(name or "<unnamed>")
    return removed


def _prepare_base_fleet_xml(path: Path, expected_count: int) -> dict[str, Any]:
    tree = ET.parse(path)
    root = tree.getroot()
    removed_landmarks = _remove_demo_landmarks(root)
    size = root.find("size")
    if size is None:
        size = ET.Element("size")
        root.insert(1, size)
    size.attrib.update(MODEL_SIZE)
    drone_default = root.find("./default/default[@class='drone']/geom")
    if drone_default is None:
        raise FleetMujocoError("generated fleet MJCF has no drone geom default")
    drone_default.attrib.update(DRONE_COLLISION_MASK)
    names = [
        body.get("name", "")
        for body in root.findall("./worldbody/body")
        if DRONE_BODY_PATTERN.fullmatch(body.get("name", ""))
    ]
    expected = [f"d{index}_b_drone_base" for index in range(1, expected_count + 1)]
    if names != expected:
        raise FleetMujocoError(
            f"generated drone body contract mismatch: expected={expected}, actual={names}"
        )
    ET.indent(tree, space="  ")
    tree.write(path, encoding="utf-8", xml_declaration=True)
    return {"drone_body_names": names, "removed_demo_landmarks": removed_landmarks}


def build_shared_model(
    *, drone_root: Path, city_world_path: Path, drone_count: int, output_dir: Path
) -> dict[str, Any]:
    if not 1 <= drone_count <= 200:
        raise FleetMujocoError("drone_count must be in [1, 200]")
    drone_root = drone_root.expanduser().resolve()
    output_dir = output_dir.expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    try:
        city_world = city_drone._resolve_city_world(city_world_path)
    except city_drone.RecipeError as exc:
        raise FleetMujocoError(str(exc)) from exc
    city_receipt = json.loads(city_world.receipt_path.read_text(encoding="utf-8"))
    terrain_xml_value = city_receipt.get("components", {}).get("terrain_xml")
    terrain_xml = Path(str(terrain_xml_value)).resolve()
    if not terrain_xml.is_file():
        raise FleetMujocoError(
            f"City World receipt does not provide a usable terrain XML: {terrain_xml}"
        )

    base_xml = output_dir / "fleet-base.xml"
    shared_xml = output_dir / "city-fleet.xml"
    shared_mjb = output_dir / "city-fleet.mjb"
    _generate_base_fleet_xml(drone_root, drone_count, base_xml)
    fleet = _prepare_base_fleet_xml(base_xml, drone_count)
    try:
        composition = city_drone._compose_drone_and_city_mjcf(
            base_xml, city_world.mjcf_path, shared_xml
        )
        compiled = compile_mujoco_xml(
            shared_xml, shared_mjb, find_mujoco_library(drone_root)
        )
    except (city_drone.RecipeError, MujocoCompileError, ET.ParseError) as exc:
        raise FleetMujocoError(str(exc)) from exc

    receipt: dict[str, Any] = {
        "schema_version": 1,
        "component": "drone-fleet-mujoco-city",
        "scope": "non-ICRA drone-fleet-single-host",
        "drone_count": drone_count,
        "process_count_contract": 1,
        "city_world": {
            "receipt": str(city_world.receipt_path),
            "mjcf": str(city_world.mjcf_path),
            "mjcf_sha256": _sha256(city_world.mjcf_path),
            "terrain_mjcf": str(terrain_xml),
            "terrain_mjcf_sha256": _sha256(terrain_xml),
            "glb": str(city_world.glb_path),
            "origin": city_world.origin,
            "half_extent_m": city_world.half_extent_m,
        },
        "fleet": fleet,
        "model_size": MODEL_SIZE,
        "collision_contract": {
            "city": {"contype": "1", "conaffinity": "0"},
            "drone": DRONE_COLLISION_MASK,
            "city_to_drone": "enabled",
            "drone_to_drone": "disabled",
        },
        "composition": composition,
        "compiled_model": compiled,
    }
    receipt_path = output_dir / "receipt.json"
    receipt_path.write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8")
    return receipt


def materialize_fleet_config(
    *,
    drone_root: Path,
    recipe_config: Path,
    model_receipt: dict[str, Any],
    spawn_altitude_m: float = LANDING_GEAR_CLEARANCE_M,
) -> dict[str, Any]:
    """Replace a configured non-ICRA fleet with its MuJoCo equivalent."""
    if not 0.18 <= spawn_altitude_m <= 2.0:
        raise FleetMujocoError(
            "spawn_altitude_m is the body-origin clearance above terrain and "
            "must be between 0.18 and 2.0"
        )
    drone_root = drone_root.expanduser().resolve()
    recipe_config = recipe_config.expanduser().resolve()
    drone_count = model_receipt.get("drone_count")
    if not isinstance(drone_count, int) or drone_count < 1:
        raise FleetMujocoError("shared model receipt has an invalid drone_count")
    compiled = model_receipt.get("compiled_model")
    city = model_receipt.get("city_world")
    if not isinstance(compiled, dict) or not isinstance(city, dict):
        raise FleetMujocoError("shared model receipt is incomplete")
    model_path = Path(str(compiled.get("output_mjb", ""))).resolve()
    if not model_path.is_file():
        raise FleetMujocoError(f"compiled shared MJB not found: {model_path}")

    type_source = (
        drone_root / "config" / "drone" / "fleets" / "types" / "api-mujoco.json"
    )
    if not type_source.is_file():
        raise FleetMujocoError(f"MuJoCo Drone type config not found: {type_source}")
    type_relative = Path("config/drone/fleets/types/api-mujoco-city.json")
    type_output = recipe_config / type_relative.relative_to("config")
    type_output.parent.mkdir(parents=True, exist_ok=True)
    type_config = json.loads(type_source.read_text(encoding="utf-8"))
    type_config["name"] = "api-mujoco-city"
    dynamics = type_config["components"]["droneDynamics"]
    dynamics["mujoco"]["modelPath"] = str(model_path)
    origin = city.get("origin")
    if not isinstance(origin, dict):
        raise FleetMujocoError("City World origin is missing from the model receipt")
    location = type_config["simulation"]["location"]
    location["latitude"] = origin["latitude"]
    location["longitude"] = origin["longitude"]
    location["altitude"] = origin["altitude_offset_m"]
    type_output.write_text(json.dumps(type_config, indent=2) + "\n", encoding="utf-8")

    fleet = recipe_config / "drone" / "fleets" / "api-current.json"
    service = (
        recipe_config
        / "drone"
        / "fleets"
        / "services"
        / "api-current-service.json"
    )
    pdudef = recipe_config / "pdudef" / "drone-pdudef-current.json"
    generator = drone_root / "tools" / "gen_fleet_scale_config.py"
    command = [
        sys.executable,
        str(generator),
        "--drone-count",
        str(drone_count),
        "--fleet-path",
        str(fleet),
        "--pdudef-path",
        str(pdudef),
        "--service-config-path",
        "config/drone/fleets/services/api-current-service.json",
        "--service-out-path",
        str(service),
        "--type-name",
        "api-mujoco-city",
        "--type-config-path",
        str(type_relative),
        "--enable-mujoco-overrides",
        "--layout",
        "packed-rings",
        "--center-z",
        str(-spawn_altitude_m),
    ]
    result = subprocess.run(command, cwd=recipe_config.parent, check=False)
    if result.returncode != 0:
        raise FleetMujocoError(
            f"MuJoCo fleet config generation failed with rc={result.returncode}"
        )

    terrain_xml = Path(str(city.get("terrain_mjcf", ""))).resolve()
    city_xml = Path(str(city.get("mjcf", ""))).resolve()
    half_extent = city.get("half_extent_m")
    if not terrain_xml.is_file() or not city_xml.is_file():
        raise FleetMujocoError("City World ray-query MJCF paths are incomplete")
    if not isinstance(half_extent, dict):
        raise FleetMujocoError("City World half_extent_m is missing")
    show_path = recipe_config / "scenario" / "show.json"
    if not show_path.is_file():
        raise FleetMujocoError(f"generated show configuration not found: {show_path}")
    show = json.loads(show_path.read_text(encoding="utf-8"))
    options = show.get("options")
    if not isinstance(options, dict):
        raise FleetMujocoError("generated show options are missing")
    requested_agl_m = float(options.get("base_alt", 0.0))
    if requested_agl_m < 0.5:
        raise FleetMujocoError("scenario altitude must be at least 0.5 m AGL")

    library_path = find_mujoco_library(drone_root)
    with _MujocoRayScene(terrain_xml, library_path) as terrain_scene, _MujocoRayScene(
        city_xml, library_path
    ) as city_scene:
        spawn_points = _select_safe_spawn_points(
            drone_count=drone_count,
            half_extent_m=half_extent,
            terrain_height=terrain_scene.height,
            city_height=city_scene.height,
        )
        targets = _formation_targets(show, show_path=show_path)
        if not targets:
            targets = [(item["x_m"], item["y_m"]) for item in spawn_points]
        route_points: set[tuple[float, float]] = set(targets)
        for spawn in spawn_points:
            start = (spawn["x_m"], spawn["y_m"])
            for target in targets:
                route_points.update(_path_points(start, target))
        route_surface_height_m = max(
            city_scene.height(x_m, y_m) for x_m, y_m in route_points
        )

    flight_altitude_m = route_surface_height_m + requested_agl_m
    fleet_config = json.loads(fleet.read_text(encoding="utf-8"))
    drones = fleet_config.get("drones")
    if not isinstance(drones, list) or len(drones) != drone_count:
        raise FleetMujocoError("generated fleet drone count changed unexpectedly")
    for drone, spawn in zip(drones, spawn_points):
        local_z_m = spawn["terrain_height_m"] + spawn_altitude_m
        drone["position_meter"] = [spawn["x_m"], spawn["y_m"], -local_z_m]
        spawn["body_origin_height_m"] = local_z_m
    fleet.write_text(json.dumps(fleet_config, indent=2) + "\n", encoding="utf-8")
    options["base_alt"] = flight_altitude_m
    show_path.write_text(json.dumps(show, indent=2) + "\n", encoding="utf-8")

    flight_plan = {
        "altitude_contract": "requested AGL above highest collider on planned routes",
        "requested_agl_m": requested_agl_m,
        "route_maximum_surface_height_m": route_surface_height_m,
        "resolved_flight_altitude_m": flight_altitude_m,
        "spawn_body_clearance_m": spawn_altitude_m,
        "spawn_clearance_radius_m": SPAWN_CLEARANCE_RADIUS_M,
        "spawn_minimum_separation_m": SPAWN_MIN_SEPARATION_M,
        "spawn_points": spawn_points,
        "formation_targets": [list(point) for point in targets],
    }
    marker = {
        "schema_version": 1,
        "backend": "mujoco-city",
        "scope": "non-ICRA drone-fleet-single-host",
        "drone_root": str(drone_root),
        "drone_count": drone_count,
        "process_count": 1,
        "flight_plan": flight_plan,
        "fleet_config": str(fleet),
        "type_config": str(type_output),
        "shared_model_receipt": str(
            Path(str(compiled["output_mjb"])).parent / "receipt.json"
        ),
        "shared_mjb": str(model_path),
        "city_world": city,
    }
    marker_path = recipe_config / "mujoco-city-fleet.json"
    marker_path.write_text(json.dumps(marker, indent=2) + "\n", encoding="utf-8")
    return marker


def configure_single_host_fleet(
    *,
    drone_root: Path,
    city_world_path: Path,
    drone_count: int,
    recipe_config: Path,
    spawn_altitude_m: float = LANDING_GEAR_CLEARANCE_M,
) -> dict[str, Any]:
    model = build_shared_model(
        drone_root=drone_root,
        city_world_path=city_world_path,
        drone_count=drone_count,
        output_dir=recipe_config / "drone" / "mujoco-city-fleet",
    )
    return materialize_fleet_config(
        drone_root=drone_root,
        recipe_config=recipe_config,
        model_receipt=model,
        spawn_altitude_m=spawn_altitude_m,
    )


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("command", choices=["build"])
    result.add_argument("--city-world", type=Path, required=True)
    result.add_argument("--drone-count", type=int, default=2)
    result.add_argument("--drone-root", type=Path, default=DEFAULT_DRONE_ROOT)
    result.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    return result


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        receipt = build_shared_model(
            drone_root=args.drone_root,
            city_world_path=args.city_world,
            drone_count=args.drone_count,
            output_dir=args.output_dir,
        )
    except FleetMujocoError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print(f"Shared MuJoCo XML: {receipt['composition']['generated_xml']}")
    print(f"Shared MuJoCo MJB: {receipt['compiled_model']['output_mjb']}")
    print(f"Receipt          : {args.output_dir.expanduser().resolve() / 'receipt.json'}")
    print(f"Drones           : {receipt['drone_count']}")
    print("Collision        : city<->drone enabled; drone<->drone disabled")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
