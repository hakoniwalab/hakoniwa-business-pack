#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import re
import socket
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
import webbrowser
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import NamedTuple

RECIPE_TOOLS_DIR = Path(__file__).absolute().parent
TOOLS_DIR = RECIPE_TOOLS_DIR.parent
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))
if str(RECIPE_TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(RECIPE_TOOLS_DIR))

import drone_gamepad_exhibition as gamepad
from mujoco_model_compiler import (
    MujocoCompileError,
    compile_mujoco_xml,
    find_mujoco_library,
)
from recipe_portal import (
    PortalCommand,
    PortalEnvironment,
    PortalLink,
    write_recipe_portal,
)

RECIPE_ID = "drone-single-mujoco-shibuya-map-gamepad"
SOURCE_DRONE_CONFIG = Path("config/drone/mujoco-shibuya-api-1")
CITY_WORLD_SOURCE_DRONE_CONFIG = Path("config/drone/mujoco")
GENERATED_DRONE_CONFIG = Path("config/drone/mujoco-shibuya-gamepad-1")
GENERATED_MUJOCO_XML = GENERATED_DRONE_CONFIG / "drone.xml"
GENERATED_MUJOCO_MJB = GENERATED_DRONE_CONFIG / "drone.mjb"
SOURCE_CONTROLLER_PARAM = Path(
    "config/controller/param-api-mixer-mujoco-shibuya.txt"
)
GENERATED_CONTROLLER_PARAM = Path(
    "config/controller/param-gamepad-mixer-mujoco-shibuya.txt"
)
CITY_WORLD_CONTROLLER_PARAM = Path("config/controller/param-api-mixer-mujoco.txt")
DRONE_PDU_CONFIG = "config/pdudef/drone-pdudef-1.json"
GLB_NAME = "13113_shibuya-ku_pref_2023_citygml_2_op.glb"
GLB_RELEASE_URL = (
    "https://github.com/hakoniwalab/hakoniwa-map-viewer/releases/tag/v0.0.1"
)
GLB_DOWNLOAD_URL = (
    "https://github.com/hakoniwalab/hakoniwa-map-viewer/releases/download/"
    f"v0.0.1/{GLB_NAME}"
)
GLB_SHA256 = "2860f6db77f7d39af3320ca4e6650cd0e29082e5dfb5e081df6d810b0b172e9e"
VIEWER_CONFIG_NAME = "viewer-config-shibuya-gamepad.json"
SCENE_CONFIG_NAME = "drone_config-compact-shibuya-gamepad.json"
VIEWER_URL = (
    "http://127.0.0.1:8000/src/client/index.html"
    f"?viewerConfigName={VIEWER_CONFIG_NAME}"
)
ALLOWED_JSON_CHANGES = {
    "components.droneDynamics.mujoco.modelPath",
    "controller.moduleDirectory",
    "controller.moduleName",
    "controller.paramFilePath",
}
MUJOCO_LOCATION = {
    "latitude": 35.6625,
    "longitude": 139.69375,
    "altitude": 15.4,
}
MAP_VIEWER_DEFAULT_ORIGIN = {"latitude": 35.6625, "longitude": 139.70625}
MAP_VIEWER_DEFAULT_CENTER = {"latitude": 35.6812, "longitude": 139.7671}
# Map Viewer converts local ROS/ENU coordinates back to latitude/longitude.
# Its origin must match the PLATEAU local-coordinate origin, not Drone Core's
# simulation.location, which is used by GPS and magnetic-field simulation.
MAP_ORIGIN = dict(MAP_VIEWER_DEFAULT_ORIGIN)

DRONE_COLLISION_MASK = {"contype": "2", "conaffinity": "1"}
DEFAULT_SPAWN_ALTITUDE_M = 20.0
# The City World MJB can contain tens of thousands of collision geoms.  The
# launcher only checks that the Drone Service process is alive; it cannot yet
# observe that MuJoCo loading and PDU registration have completed.  Keep the
# dependent Visual State Publisher behind a conservative registration window.
CITY_WORLD_DRONE_READY_DELAY_SEC = 20

RecipeError = gamepad.RecipeError


class RuntimePaths(NamedTuple):
    system_name: str
    drone_service: Path
    foundation_python: Path
    hako_cmd: Path
    web_bridge: Path


class CityWorldSource(NamedTuple):
    input_path: Path
    job_root: Path
    receipt_path: Path
    mjcf_path: Path
    glb_path: Path
    origin: dict[str, float]
    half_extent_m: dict[str, float]
    receipt: dict[str, object]


def root() -> Path:
    return Path(__file__).absolute().parents[2]


def default_source(name: str) -> Path:
    return root().parent / name


def recipe_file() -> Path:
    return root() / "recipes" / "examples" / f"{RECIPE_ID}.yaml"


def _absolute(path: Path) -> Path:
    return Path(os.path.abspath(path.expanduser()))


def _required(path: Path, label: str) -> Path:
    candidate = _absolute(path)
    if not candidate.exists():
        raise RecipeError(f"{label} not found: {candidate}")
    return candidate.resolve()


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _copytree(source: Path, destination: Path) -> None:
    shutil.copytree(
        source,
        destination,
        dirs_exist_ok=True,
        ignore=shutil.ignore_patterns(
            ".git",
            ".DS_Store",
            "__pycache__",
            "node_modules",
        ),
    )


def _replace_map_origin_assignment(
    source: str,
    name: str,
    expected: float,
    replacement: float,
) -> str:
    pattern = re.compile(
        rf"(?m)^(\s*let\s+{re.escape(name)}\s*=\s*)"
        rf"{re.escape(str(expected))}(\s*;)"
    )
    updated, count = pattern.subn(
        rf"\g<1>{replacement}\g<2>",
        source,
        count=1,
    )
    if count != 1:
        raise RecipeError(
            f"Map Viewer {name} assignment does not match the expected "
            f"PLATEAU origin value {expected}"
        )
    return updated


def _align_map_viewer_origin(
    client: Path,
    map_origin: dict[str, float] | None = None,
) -> Path:
    map_origin = map_origin or MAP_ORIGIN
    ui_path = _required(client / "src" / "ui.js", "Map Viewer UI")
    source = ui_path.read_text(encoding="utf-8")
    default_center = (
        "const map = L.map('map').setView("
        f"[{MAP_VIEWER_DEFAULT_CENTER['latitude']}, "
        f"{MAP_VIEWER_DEFAULT_CENTER['longitude']}], 15);"
    )
    aligned_center = (
        "const map = L.map('map').setView("
        f"[{map_origin['latitude']}, {map_origin['longitude']}], 15);"
    )
    if source.count(default_center) != 1:
        raise RecipeError(
            "Map Viewer initial center does not match the expected default "
            f"{MAP_VIEWER_DEFAULT_CENTER}"
        )
    source = source.replace(default_center, aligned_center, 1)
    source = source.replace(
        "// 東京駅",
        "// Recipeで渋谷PLATEAUのローカル原点へ表示中心を整合",
        1,
    )
    source = _replace_map_origin_assignment(
        source,
        "ORIGIN_LAT",
        MAP_VIEWER_DEFAULT_ORIGIN["latitude"],
        map_origin["latitude"],
    )
    source = _replace_map_origin_assignment(
        source,
        "ORIGIN_LON",
        MAP_VIEWER_DEFAULT_ORIGIN["longitude"],
        map_origin["longitude"],
    )
    source = source.replace(
        "// zone の原点（仮）",
        "// 渋谷PLATEAUのローカル座標原点に対応する地理座標",
        1,
    )
    ui_path.write_text(source, encoding="utf-8")
    return ui_path


def _stage_glb(paths, source: Path | None) -> tuple[Path, str]:
    destination = paths.recipe_assets / GLB_NAME
    destination.parent.mkdir(parents=True, exist_ok=True)
    if source is None and destination.is_file() and _sha256(destination) == GLB_SHA256:
        return destination, GLB_RELEASE_URL

    temporary = destination.with_name(f".{destination.name}.part")
    if temporary.exists():
        temporary.unlink()
    try:
        if source is None:
            print(f"Downloading Shibuya PLATEAU GLB: {GLB_DOWNLOAD_URL}")
            try:
                with urllib.request.urlopen(GLB_DOWNLOAD_URL, timeout=60) as response:
                    with temporary.open("wb") as output:
                        shutil.copyfileobj(response, output, length=1024 * 1024)
            except (OSError, urllib.error.URLError) as exc:
                raise RecipeError(
                    f"failed to download Shibuya PLATEAU GLB: {exc}"
                ) from exc
            provenance = GLB_RELEASE_URL
        else:
            resolved = _required(source, "Shibuya PLATEAU GLB")
            destination_is_source = (
                destination.exists() and resolved == destination.resolve()
            )
            if destination_is_source:
                if _sha256(resolved) != GLB_SHA256:
                    raise RecipeError(
                        f"Shibuya PLATEAU GLB checksum mismatch: {resolved}"
                    )
                return destination, str(resolved)
            shutil.copy2(resolved, temporary)
            provenance = str(resolved)

        actual = _sha256(temporary)
        if actual != GLB_SHA256:
            raise RecipeError(
                "Shibuya PLATEAU GLB checksum mismatch: "
                f"expected={GLB_SHA256}, actual={actual}"
            )
        temporary.replace(destination)
        return destination, provenance
    finally:
        if temporary.exists():
            temporary.unlink()


def _json_changes(before: object, after: object, prefix: str = "") -> set[str]:
    if isinstance(before, dict) and isinstance(after, dict):
        changes: set[str] = set()
        for key in before.keys() | after.keys():
            path = f"{prefix}.{key}" if prefix else key
            if key not in before or key not in after:
                changes.add(path)
            else:
                changes.update(_json_changes(before[key], after[key], path))
        return changes
    if isinstance(before, list) and isinstance(after, list):
        if len(before) != len(after):
            return {prefix}
        changes: set[str] = set()
        for index, (left, right) in enumerate(zip(before, after)):
            changes.update(_json_changes(left, right, f"{prefix}[{index}]"))
        return changes
    return set() if before == after else {prefix}


def _load_json(path: Path) -> object:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RecipeError(f"invalid JSON {path}: {exc}") from exc


def _write_json(path: Path, data: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def _resolve_city_world(source: Path) -> CityWorldSource:
    requested = _required(source, "City World")
    if requested.is_dir():
        if (requested / "build" / "world" / "city-world-receipt.json").is_file():
            job_root = requested
            world_dir = requested / "build" / "world"
        elif (requested / "city-world-receipt.json").is_file():
            world_dir = requested
            job_root = requested.parents[1] if requested.name == "world" else requested
        else:
            raise RecipeError(
                "City World directory must be a worker job or build/world directory: "
                f"{requested}"
            )
        receipt_path = world_dir / "city-world-receipt.json"
    else:
        if requested.name == "city-world-receipt.json":
            receipt_path = requested
            world_dir = requested.parent
        elif requested.name in {"city-world.xml", "city-world.glb"}:
            world_dir = requested.parent
            receipt_path = world_dir / "city-world-receipt.json"
        else:
            raise RecipeError(
                "--city-world must identify a worker job, build/world, "
                "city-world-receipt.json, city-world.xml, or city-world.glb"
            )
        job_root = world_dir.parents[1] if world_dir.name == "world" else world_dir

    receipt_path = _required(receipt_path, "City World receipt")
    receipt = _load_json(receipt_path)
    if not isinstance(receipt, dict):
        raise RecipeError(f"City World receipt must be a JSON object: {receipt_path}")
    coordinate_frame = receipt.get("coordinate_frame")
    if not isinstance(coordinate_frame, dict):
        raise RecipeError("City World receipt has no coordinate_frame")
    coordinate_systems = coordinate_frame.get("coordinate_systems", {})
    if not isinstance(coordinate_systems, dict) or coordinate_systems.get("mjcf") != (
        "X=North,Y=-East,Z=Up"
    ):
        raise RecipeError("City World MJCF coordinate contract is not supported")
    origin_value = coordinate_frame.get("origin")
    if not isinstance(origin_value, dict):
        raise RecipeError("City World receipt has no coordinate_frame.origin")
    try:
        origin = {
            "latitude": float(origin_value["latitude"]),
            "longitude": float(origin_value["longitude"]),
            "altitude_offset_m": float(origin_value.get("altitude_offset_m", 0.0)),
        }
    except (KeyError, TypeError, ValueError) as exc:
        raise RecipeError("City World receipt has an invalid geographic origin") from exc
    half_extent_value = coordinate_frame.get("half_extent_m", {})
    if not isinstance(half_extent_value, dict):
        raise RecipeError("City World receipt has invalid half_extent_m")
    half_extent_m = {
        "north_south": float(half_extent_value.get("north_south", 0.0)),
        "east_west": float(half_extent_value.get("east_west", 0.0)),
    }

    mjcf_path = world_dir / "city-world.xml"
    glb_path = world_dir / "city-world.glb"
    if not mjcf_path.is_file():
        mjcf_entry = receipt.get("mjcf", {})
        if isinstance(mjcf_entry, dict) and mjcf_entry.get("path"):
            mjcf_path = Path(str(mjcf_entry["path"]))
    if not glb_path.is_file():
        glb_entry = receipt.get("glb", {})
        if isinstance(glb_entry, dict) and glb_entry.get("path"):
            glb_path = Path(str(glb_entry["path"]))
    mjcf_path = _required(mjcf_path, "City World MJCF")
    glb_path = _required(glb_path, "City World GLB")
    for key, path in (("mjcf", mjcf_path), ("glb", glb_path)):
        entry = receipt.get(key, {})
        expected = entry.get("sha256") if isinstance(entry, dict) else None
        if expected and _sha256(path) != expected:
            raise RecipeError(f"City World {key} differs from its receipt: {path}")
    return CityWorldSource(
        input_path=requested,
        job_root=job_root.resolve(),
        receipt_path=receipt_path,
        mjcf_path=mjcf_path,
        glb_path=glb_path,
        origin=origin,
        half_extent_m=half_extent_m,
        receipt=receipt,
    )


def _set_drone_collision_mask(root_element: ET.Element) -> int:
    count = 0
    drone_default = root_element.find("./default/default[@class='drone']/geom")
    if drone_default is None:
        raise RecipeError("base Drone MJCF has no default class='drone' geom")
    drone_default.attrib.update(DRONE_COLLISION_MASK)
    count += 1
    for body_name in ("box",):
        for geom in root_element.findall(f".//body[@name='{body_name}']//geom"):
            geom.attrib.update(DRONE_COLLISION_MASK)
            count += 1
    return count


def _compose_drone_and_city_mjcf(
    drone_xml: Path,
    city_xml: Path,
    output: Path,
) -> dict[str, object]:
    try:
        drone_tree = ET.parse(drone_xml)
        city_tree = ET.parse(city_xml)
    except (OSError, ET.ParseError) as exc:
        raise RecipeError(f"invalid MuJoCo XML: {exc}") from exc
    drone_root = drone_tree.getroot()
    city_root = city_tree.getroot()
    drone_asset = drone_root.find("asset")
    drone_worldbody = drone_root.find("worldbody")
    city_asset = city_root.find("asset")
    city_worldbody = city_root.find("worldbody")
    if drone_asset is None or drone_worldbody is None or city_worldbody is None:
        raise RecipeError("Drone or City World MJCF has an incomplete root structure")

    removed_ground = 0
    for geom in list(drone_worldbody.findall("geom")):
        if geom.get("name") == "ground":
            drone_worldbody.remove(geom)
            removed_ground += 1
    if removed_ground != 1:
        raise RecipeError("base Drone MJCF must contain exactly one ground geom")

    rewritten_files: list[dict[str, str]] = []
    city_asset_count = 0
    if city_asset is not None:
        for element in list(city_asset):
            file_value = element.get("file")
            if file_value:
                absolute = (city_xml.parent / file_value).resolve()
                _required(absolute, f"City World asset {file_value}")
                element.set("file", str(absolute))
                rewritten_files.append({"source": file_value, "resolved": str(absolute)})
            drone_asset.append(element)
            city_asset_count += 1
    city_body_count = 0
    for element in list(city_worldbody):
        drone_worldbody.insert(city_body_count, element)
        city_body_count += 1

    mask_count = _set_drone_collision_mask(drone_root)
    output.parent.mkdir(parents=True, exist_ok=True)
    ET.indent(drone_tree, space="  ")
    drone_tree.write(output, encoding="utf-8", xml_declaration=True)
    return {
        "base_drone_xml": str(drone_xml),
        "city_world_xml": str(city_xml),
        "generated_xml": str(output),
        "generated_xml_sha256": _sha256(output),
        "removed_base_ground_geoms": removed_ground,
        "city_asset_count": city_asset_count,
        "city_worldbody_child_count": city_body_count,
        "rewritten_file_assets": rewritten_files,
        "drone_collision_mask": DRONE_COLLISION_MASK,
        "collision_mask_assignment_count": mask_count,
        "collision_contract": {
            "city_to_drone": "enabled",
            "drone_to_drone": "disabled",
        },
    }


def _source_paths(
    drone_root: Path,
    map_viewer_root: Path,
    threejs_root: Path,
    shibuya_glb: Path | None = None,
) -> dict[str, Path]:
    paths = {
        "drone_config_dir": _required(
            drone_root / SOURCE_DRONE_CONFIG, "Shibuya Drone config"
        ),
        "controller_param": _required(
            drone_root / SOURCE_CONTROLLER_PARAM,
            "Shibuya controller parameters",
        ),
        "rc_app": _required(
            drone_root / "drone_api" / "rc" / "rc-custom.py", "RC application"
        ),
        "controller_mapping": _required(
            drone_root
            / "drone_api"
            / "rc"
            / "rc_config"
            / "ps4-control.json",
            "PS4/PS5 controller mapping",
        ),
        "map_client": _required(
            map_viewer_root / "src" / "client", "Map Viewer client"
        ),
        "map_images": _required(map_viewer_root / "images", "Map Viewer images"),
        "threejs_root": _required(threejs_root, "Three.js viewer"),
    }
    if shibuya_glb is not None:
        paths["shibuya_glb"] = _required(shibuya_glb, "Shibuya PLATEAU GLB")
    return paths


def _preflight(
    drone_root: Path,
    map_viewer_root: Path,
    threejs_root: Path,
    overrides: dict[str, Path | None],
):
    foundation = gamepad.load_foundation_module()
    paths = foundation.resolve_workspace(root(), RECIPE_ID)
    inspection = foundation.inspect_foundation(recipe_file(), paths.install_prefix)
    if inspection["status"] != "SATISFIED":
        foundation.print_inspection(inspection, False)
        raise RecipeError(
            "Foundation is not reusable; run tools/foundation.py plan/build first"
        )
    _source_paths(drone_root, map_viewer_root, threejs_root)
    _required(
        paths.foundation_config / "cpp_core_config.json",
        "Foundation Core config",
    )
    system_name = platform.system()
    adapter = gamepad.current_adapter(system_name)
    installed_service_name = {
        "Darwin": "mac-main_hako_drone_service",
        "Linux": "lnx-main_hako_drone_service",
        "Windows": "win-main_hako_drone_service.exe",
    }.get(system_name)
    managed_service_candidates = (
        (drone_root / ".hako" / "install" / "bin" / installed_service_name,)
        if installed_service_name is not None
        else ()
    )
    runtime = RuntimePaths(
        system_name=system_name,
        drone_service=gamepad._resolve_candidate(
            managed_service_candidates
            + adapter.drone_service_candidates(drone_root, system_name),
            "Drone service",
            overrides.get("drone_service_bin"),
        ),
        foundation_python=gamepad.resolve_foundation_python(paths),
        hako_cmd=gamepad._resolve_candidate(
            adapter.hako_cmd_candidates(paths.install_prefix),
            "hako-cmd",
            overrides.get("hako_cmd_bin"),
        ),
        web_bridge=gamepad._resolve_candidate(
            adapter.web_bridge_candidates(paths.install_prefix),
            "Web bridge",
            overrides.get("web_bridge_bin"),
        ),
    )
    return foundation, paths, runtime


def _copy_runtime_config(drone_root: Path, recipe_config: Path) -> None:
    for relative in (Path("pdudef"),):
        _copytree(
            _required(drone_root / "config" / relative, f"Recipe config {relative}"),
            recipe_config / relative,
        )


def _materialize_drone(
    drone_root: Path,
    recipe_config: Path,
) -> dict[str, object]:
    source_dir = _required(
        drone_root / SOURCE_DRONE_CONFIG, "Shibuya Drone config"
    )
    destination = recipe_config / GENERATED_DRONE_CONFIG.relative_to("config")
    _copytree(source_dir, destination)

    source_json_path = source_dir / "drone_config_0.json"
    generated_json_path = destination / "drone_config_0.json"
    source_json = _load_json(source_json_path)
    if not isinstance(source_json, dict):
        raise RecipeError(f"Drone config must be a JSON object: {source_json_path}")
    generated_json = json.loads(json.dumps(source_json))
    generated_json["components"]["droneDynamics"]["mujoco"]["modelPath"] = str(
        GENERATED_MUJOCO_MJB
    )
    generated_json["controller"][
        "moduleDirectory"
    ] = "../drone_control/cmake-build/workspace/RadioController"
    generated_json["controller"]["moduleName"] = "RadioController"
    generated_json["controller"]["paramFilePath"] = str(
        GENERATED_CONTROLLER_PARAM
    )
    _write_json(generated_json_path, generated_json)

    changes = _json_changes(source_json, generated_json)
    if changes != ALLOWED_JSON_CHANGES:
        raise RecipeError(
            "generated Drone config violates the allowlist: "
            f"expected={sorted(ALLOWED_JSON_CHANGES)}, actual={sorted(changes)}"
        )

    source_xml = source_dir / "drone.xml"
    generated_xml = destination / "drone.xml"
    if _sha256(source_xml) != _sha256(generated_xml):
        raise RecipeError("generated drone.xml is not byte-for-byte identical")
    try:
        compiled_model = compile_mujoco_xml(
            generated_xml,
            destination / "drone.mjb",
            find_mujoco_library(drone_root),
        )
    except MujocoCompileError as exc:
        raise RecipeError(str(exc)) from exc

    source_param = _required(
        drone_root / SOURCE_CONTROLLER_PARAM, "Shibuya controller parameters"
    )
    generated_param = recipe_config / GENERATED_CONTROLLER_PARAM.relative_to("config")
    generated_param.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source_param, generated_param)

    return {
        "source_config": str(source_json_path),
        "generated_config": str(generated_json_path),
        "json_changes": sorted(changes),
        "source_config_sha256": _sha256(source_json_path),
        "generated_config_sha256": _sha256(generated_json_path),
        "source_xml": str(source_xml),
        "generated_xml": str(generated_xml),
        "xml_sha256": _sha256(generated_xml),
        "compiled_model": compiled_model,
        "source_controller_param": str(source_param),
        "generated_controller_param": str(generated_param),
        "controller_param_sha256": _sha256(generated_param),
    }


def _materialize_city_world_drone(
    drone_root: Path,
    recipe_config: Path,
    city_world: CityWorldSource,
    spawn_altitude_m: float,
) -> dict[str, object]:
    if not 1.0 <= spawn_altitude_m <= 500.0:
        raise RecipeError("--spawn-altitude-m must be between 1 and 500 meters")
    source_dir = _required(
        drone_root / CITY_WORLD_SOURCE_DRONE_CONFIG, "base MuJoCo Drone config"
    )
    destination = recipe_config / GENERATED_DRONE_CONFIG.relative_to("config")
    _copytree(source_dir, destination)
    source_json_path = source_dir / "drone_config_0.json"
    generated_json_path = destination / "drone_config_0.json"
    source_json = _load_json(source_json_path)
    if not isinstance(source_json, dict):
        raise RecipeError(f"Drone config must be a JSON object: {source_json_path}")
    generated_json = json.loads(json.dumps(source_json))
    generated_json["components"]["droneDynamics"]["mujoco"]["modelPath"] = str(
        GENERATED_MUJOCO_MJB
    )
    generated_json["components"]["droneDynamics"]["position_meter"] = [
        0.0,
        0.0,
        -spawn_altitude_m,
    ]
    location = generated_json["simulation"]["location"]
    location["latitude"] = city_world.origin["latitude"]
    location["longitude"] = city_world.origin["longitude"]
    # simulation.location is the geodetic anchor of local NED.  The aircraft
    # height belongs only in position_meter (negative Down); adding it here as
    # well would count the spawn altitude twice in GPS conversion.
    location["altitude"] = city_world.origin["altitude_offset_m"]
    generated_json["controller"]["paramFilePath"] = str(
        GENERATED_CONTROLLER_PARAM
    )
    _write_json(generated_json_path, generated_json)

    generated_xml = destination / "drone.xml"
    composition = _compose_drone_and_city_mjcf(
        source_dir / "drone.xml", city_world.mjcf_path, generated_xml
    )
    try:
        compiled_model = compile_mujoco_xml(
            generated_xml,
            destination / "drone.mjb",
            find_mujoco_library(drone_root),
        )
    except MujocoCompileError as exc:
        raise RecipeError(str(exc)) from exc
    source_param = _required(
        drone_root / CITY_WORLD_CONTROLLER_PARAM,
        "base MuJoCo controller parameters",
    )
    generated_param = recipe_config / GENERATED_CONTROLLER_PARAM.relative_to("config")
    generated_param.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source_param, generated_param)
    return {
        "mode": "city-world",
        "source_config": str(source_json_path),
        "generated_config": str(generated_json_path),
        "json_changes": sorted(_json_changes(source_json, generated_json)),
        "source_config_sha256": _sha256(source_json_path),
        "generated_config_sha256": _sha256(generated_json_path),
        "source_xml": str(source_dir / "drone.xml"),
        "generated_xml": str(generated_xml),
        "xml_sha256": _sha256(generated_xml),
        "compiled_model": compiled_model,
        "source_controller_param": str(source_param),
        "generated_controller_param": str(generated_param),
        "controller_param_sha256": _sha256(generated_param),
        "spawn_altitude_m": spawn_altitude_m,
        "simulation_location": location,
        "composition": composition,
    }


def _materialize_browser(
    map_viewer_root: Path,
    threejs_root: Path,
    world_glb: Path,
    browser_root: Path,
    *,
    map_origin: dict[str, float] | None = None,
    glb_name: str = GLB_NAME,
    generated_city_world: bool = False,
) -> dict[str, object]:
    map_origin = map_origin or MAP_ORIGIN
    client = browser_root / "src" / "client"
    images = browser_root / "images"
    embedded = browser_root / "thirdparty" / "hakoniwa-threejs-drone"
    _copytree(_required(map_viewer_root / "src" / "client", "Map Viewer client"), client)
    _copytree(_required(map_viewer_root / "images", "Map Viewer images"), images)
    map_ui_path = _align_map_viewer_origin(client, map_origin)

    for relative in ("src", "config", "assets", "thirdparty/hakoniwa-pdu-javascript"):
        _copytree(
            _required(threejs_root / relative, f"Three.js {relative}"),
            embedded / relative,
        )

    source_scene_path = _required(
        threejs_root / "config" / "drone_config-compact-dji-1.json",
        "Shibuya Three.js scene reference",
    )
    scene = _load_json(source_scene_path)
    if not isinstance(scene, dict):
        raise RecipeError(f"Three.js scene must be a JSON object: {source_scene_path}")
    scene["droneTypesPath"] = "./drone_types-quadrotor_base.json"
    for drone in scene.get("drones", []):
        drone["type"] = "quadrotor_base"
    environments = scene.get("environments", [])
    if not isinstance(environments, list) or not environments:
        raise RecipeError("Three.js scene has no environment entry")
    environments[0]["model"] = f"../assets/local_models/{glb_name}"
    if generated_city_world:
        environments[0]["pos"] = [0, 0, 0]
        environments[0]["hpr"] = [0, 0, 0]
    scene_path = embedded / "config" / SCENE_CONFIG_NAME
    _write_json(scene_path, scene)

    viewer = _load_json(
        _required(
            threejs_root / "config" / "viewer-config-legacy.json",
            "Three.js single-drone viewer config",
        )
    )
    if not isinstance(viewer, dict):
        raise RecipeError("Three.js single-drone viewer config must be a JSON object")
    viewer["three"]["sceneConfigPath"] = f"./{SCENE_CONFIG_NAME}"
    viewer_path = embedded / "config" / VIEWER_CONFIG_NAME
    _write_json(viewer_path, viewer)

    glb_destination = embedded / "assets" / "local_models" / glb_name
    glb_destination.parent.mkdir(parents=True, exist_ok=True)
    if glb_destination.exists():
        glb_destination.unlink()
    try:
        os.link(world_glb, glb_destination)
    except OSError:
        shutil.copy2(world_glb, glb_destination)
    return {
        "map_client_source": str(map_viewer_root / "src" / "client"),
        "map_ui": str(map_ui_path),
        "map_source_origin": MAP_VIEWER_DEFAULT_ORIGIN,
        "map_origin": map_origin,
        "map_initial_center": map_origin,
        "threejs_source": str(threejs_root),
        "viewer_config": str(viewer_path),
        "scene_config": str(scene_path),
        "glb_destination": str(glb_destination),
        "glb_sha256": _sha256(glb_destination),
        "generated_city_world": generated_city_world,
    }


def write_launcher(paths, drone_root: Path, runtime: RuntimePaths) -> Path:
    rc_root = drone_root / "drone_api" / "rc"
    browser_root = paths.recipe_root / "web" / "map-viewer"
    launcher = {
        "version": "0.1",
        "defaults": {
            "cwd": str(paths.recipe_root),
            "stdout": str(paths.recipe_logs / "${asset}.out"),
            "stderr": str(paths.recipe_logs / "${asset}.err"),
            "start_grace_sec": 1,
            "delay_sec": 1,
            "env": {
                "set": {
                    "HAKO_CONFIG_PATH": str(
                        paths.foundation_config / "cpp_core_config.json"
                    ),
                    "HAKO_PROFILE_SERVICE_CLIENT": "0",
                },
                "prepend": {
                    "lib_path": [
                        str(paths.install_prefix / "lib"),
                        str(drone_root / "vendor" / "mujoco" / "lib"),
                    ],
                    "PATH": [
                        str(runtime.foundation_python.parent),
                        str(paths.install_prefix / "bin"),
                    ],
                },
            },
        },
        "assets": [
            {
                "name": "drone-service-1",
                "activation_timing": "before_start",
                "command": str(runtime.drone_service),
                "args": [
                    str(GENERATED_DRONE_CONFIG),
                    DRONE_PDU_CONFIG,
                    "--real-sleep-msec",
                    "1",
                ],
                "cwd": str(paths.recipe_root),
                # The Shibuya MJCF is large. Do not start dependent assets
                # while MuJoCo is still compiling the model and before the
                # drone service has created the Hakoniwa master.
                "delay_sec": CITY_WORLD_DRONE_READY_DELAY_SEC,
            },
            {
                "name": "web-bridge-single-drone",
                "activation_timing": "before_start",
                "command": str(runtime.web_bridge),
                "args": [
                    "--config-root",
                    str(
                        paths.install_prefix
                        / "share"
                        / "hakoniwa-pdu-bridge"
                        / "config"
                        / "web_bridge"
                    ),
                    "--node-name",
                    "web_bridge_node1",
                    "--delta-time-step-usec",
                    "20000",
                    "--enable-ondemand",
                ],
                "cwd": str(paths.recipe_root),
                "depends_on": ["drone-service-1"],
            },
            {
                "name": "remote-controller",
                "activation_timing": "after_start",
                "command": str(runtime.foundation_python),
                "args": [
                    "-u",
                    str(rc_root / "rc-custom.py"),
                    str(paths.recipe_root / DRONE_PDU_CONFIG),
                    str(rc_root / "rc_config" / "ps4-control.json"),
                ],
                "cwd": str(rc_root),
                "depends_on": ["drone-service-1"],
            },
            {
                "name": "map-viewer-webserver",
                "activation_timing": "after_start",
                "command": str(runtime.foundation_python),
                "args": ["-m", "http.server", "8000"],
                "cwd": str(browser_root),
                "depends_on": ["web-bridge-single-drone"],
            },
        ],
    }
    output = paths.recipe_config / "launcher.json"
    _write_json(output, launcher)
    return output


def session_file(paths) -> Path:
    return paths.recipe_root / "runtime" / "launcher-session.json"


def _clear_runtime_logs(paths) -> None:
    paths.recipe_logs.mkdir(parents=True, exist_ok=True)
    for path in paths.recipe_logs.iterdir():
        if path.is_file() and path.suffix in {".out", ".err"}:
            path.write_text("", encoding="utf-8")


def _tcp_ready(port: int) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=0.25):
            return True
    except OSError:
        return False


def _read_text_if_present(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except (FileNotFoundError, OSError):
        return ""


def wait_for_demo_ready(paths, *, timeout_sec: float = 45.0) -> tuple[bool, list[str]]:
    deadline = time.monotonic() + timeout_sec
    missing: list[str] = []
    while True:
        drone_log = _read_text_if_present(paths.recipe_logs / "drone-service-1.out")
        checks = {
            "simulation": "WAIT RUNNING" in drone_log,
            "HTTP port 8000": _tcp_ready(8000),
            "WebSocket port 8765": _tcp_ready(8765),
        }
        missing = [name for name, ready in checks.items() if not ready]
        if not missing:
            return True, []
        if time.monotonic() >= deadline:
            return False, missing
        time.sleep(0.5)


def print_background_handoff(paths, runtime: RuntimePaths) -> None:
    print()
    print("The start command has returned, but the Demo is still running in background.")
    print("確認してください:")
    print("  1. 次のcommandでMap Viewerを開ける")
    print(f"     {_display_command(runtime, 'open-viewer')}")
    print("  2. PS4/PS5コントローラでDroneを操作できる")
    print("状態確認:")
    print(f"     {_display_command(runtime, 'status')}")
    print("終了:")
    print(f"     {_display_command(runtime, 'stop')}")
    print(f"Session: {session_file(paths)}")
    print(f"Logs   : {paths.recipe_logs}")


def _display_command(_runtime: RuntimePaths, action: str) -> str:
    return f"python tools/recipe/drone_shibuya_gamepad.py {action}"


def write_portal(
    paths,
    runtime: RuntimePaths,
    launcher: Path,
    *,
    city_world: CityWorldSource | None = None,
) -> Path:
    world_label = "generated City World" if city_world is not None else "Shibuya"
    map_origin = (
        city_world.origin if city_world is not None else MAP_ORIGIN
    )
    collider_total: object = "legacy Shibuya world"
    extent_text = "legacy Shibuya extent"
    if city_world is not None:
        components = city_world.receipt.get("components", {})
        counts = components.get("mjcf_geom_counts", {}) if isinstance(components, dict) else {}
        collider_total = counts.get("total", "not recorded") if isinstance(counts, dict) else "not recorded"
        extent_text = (
            f"{2 * city_world.half_extent_m['north_south']:.1f} m x "
            f"{2 * city_world.half_extent_m['east_west']:.1f} m"
        )
    return write_recipe_portal(
        paths.recipe_root / "index.html",
        recipe_id=RECIPE_ID,
        title=f"Hakoniwa Drone {world_label} Gamepad Demo",
        summary=(
            f"PS5コントローラで{world_label}のMuJoCo衝突ワールドを飛行し、"
            "同じ状態をLeaflet地図とThree.js PLATEAUシーンで確認するRecipeです。"
        ),
        topology=(
            "PS5 controller",
            "RadioController",
            "Hakoniwa Drone + Shibuya MuJoCo",
            "Single-drone WebBridge",
            "Leaflet + Three.js",
        ),
        commands=tuple(
            PortalCommand(label, _display_command(runtime, action), description)
            for label, action, description in (
                ("Preflight", "doctor", "Foundation、生成物、ゲームパッド、ポートを確認します。"),
                (
                    "Start",
                    "start",
                    "simulationと2つのportを確認後に復帰します。復帰後もDemoはbackgroundで継続します。",
                ),
                ("Open viewer", "open-viewer", "Map Viewerを既定ブラウザで開きます。"),
                ("Status", "status", "Launcherセッションの状態を確認します。"),
                ("Reset", "reset", "シミュレーションを初期状態へ戻します。"),
                ("Stop", "stop", "Launcherの通常終了経路で全アセットを終了します。"),
            )
        ),
        links=(
            PortalLink("City Map Viewer", VIEWER_URL, "LeafletとThree.jsの統合画面"),
            PortalLink("Launcher JSON", "config/launcher.json", "生成された実行構成"),
            PortalLink("Runtime session", "runtime/", "Launcherセッション"),
            PortalLink("Logs", "logs/", "各アセットのログ"),
            PortalLink("Validation", "validation/", "materializationの検証証跡"),
        ),
        environment=(
            PortalEnvironment("Platform", runtime.system_name),
            PortalEnvironment("Recipe workspace", str(paths.recipe_root)),
            PortalEnvironment("Foundation install", str(paths.install_prefix)),
            PortalEnvironment("Foundation Python", str(runtime.foundation_python)),
            PortalEnvironment("Launcher", str(launcher)),
            PortalEnvironment("Session", str(session_file(paths))),
            PortalEnvironment(
                "PLATEAU map origin",
                f"{map_origin['latitude']}, {map_origin['longitude']}",
            ),
            PortalEnvironment(
                "City World source",
                str(city_world.job_root) if city_world is not None else "legacy Shibuya preset",
            ),
            PortalEnvironment("City World extent", extent_text),
            PortalEnvironment("City collider count", str(collider_total)),
            PortalEnvironment("Web ports", "8000 / 8765"),
        ),
        agency_notes=(
            "PLATEAU GLBの利用権と出典はオペレータが確認します。",
            "PS5コントローラの操作、衝突挙動、座標整合性の最終判断は人が行います。",
            "停止にはLauncher session fileを使用し、OS固有の広範なkillは行いません。",
            "Start commandの復帰はDemo終了を意味しません。[OK] Demo readyを確認して次へ進みます。",
            "このHTMLはローカルコマンドを直接実行しません。",
        ),
    )


def _validation_record(paths) -> Path:
    return paths.recipe_validation / "materialization.json"


def materialize_runtime(
    paths,
    drone_root: Path,
    map_viewer_root: Path,
    threejs_root: Path,
    shibuya_glb: Path,
    glb_source: str,
    runtime: RuntimePaths,
    city_world: CityWorldSource | None = None,
    spawn_altitude_m: float = DEFAULT_SPAWN_ALTITUDE_M,
) -> Path:
    sources = _source_paths(
        drone_root,
        map_viewer_root,
        threejs_root,
        None if city_world is not None else shibuya_glb,
    )
    _copy_runtime_config(drone_root, paths.recipe_config)
    drone_record = (
        _materialize_city_world_drone(
            drone_root,
            paths.recipe_config,
            city_world,
            spawn_altitude_m,
        )
        if city_world is not None
        else _materialize_drone(drone_root, paths.recipe_config)
    )
    map_origin = (
        {
            "latitude": city_world.origin["latitude"],
            "longitude": city_world.origin["longitude"],
        }
        if city_world is not None
        else MAP_ORIGIN
    )
    world_glb = (
        city_world.glb_path if city_world is not None else sources["shibuya_glb"]
    )
    glb_name = "city-world.glb" if city_world is not None else GLB_NAME
    browser_record = _materialize_browser(
        map_viewer_root,
        threejs_root,
        world_glb,
        paths.recipe_root / "web" / "map-viewer",
        map_origin=map_origin,
        glb_name=glb_name,
        generated_city_world=city_world is not None,
    )
    launcher = write_launcher(paths, drone_root, runtime)
    portal = write_portal(paths, runtime, launcher, city_world=city_world)
    record = {
        "schema_version": 1,
        "recipe_id": RECIPE_ID,
        "source_assets": drone_record,
        "browser_bundle": browser_record,
        "glb": {
            "input": str(world_glb),
            "source": glb_source,
            "sha256": browser_record["glb_sha256"],
            "release_url": GLB_RELEASE_URL if city_world is None else None,
            "download_url": GLB_DOWNLOAD_URL if city_world is None else None,
            "expected_sha256": GLB_SHA256 if city_world is None else _sha256(world_glb),
        },
        "coordinate_invariants": {
            "drone_simulation_location": (
                drone_record.get("simulation_location", MUJOCO_LOCATION)
            ),
            "map_viewer_source_origin": MAP_VIEWER_DEFAULT_ORIGIN,
            "plateau_map_origin": map_origin,
            "map_origin_source": (
                "city-world-receipt" if city_world is not None else "legacy-shibuya"
            ),
            "map_origin_derived_from_drone_location": False,
        },
        "launcher": str(launcher),
        "portal": str(portal),
        "runtime_launches_envsim": False,
        "mode": "city-world" if city_world is not None else "legacy-shibuya",
    }
    if city_world is not None:
        record["city_world"] = {
            "input": str(city_world.input_path),
            "job_root": str(city_world.job_root),
            "receipt": str(city_world.receipt_path),
            "receipt_sha256": _sha256(city_world.receipt_path),
            "mjcf": str(city_world.mjcf_path),
            "mjcf_sha256": _sha256(city_world.mjcf_path),
            "glb": str(city_world.glb_path),
            "glb_sha256": _sha256(city_world.glb_path),
            "origin": city_world.origin,
            "half_extent_m": city_world.half_extent_m,
        }
    output = _validation_record(paths)
    _write_json(output, record)
    validate_materialization(paths, drone_root)
    return output


def _assert(condition: bool, message: str) -> None:
    if not condition:
        raise RecipeError(message)


def validate_materialization(paths, drone_root: Path) -> dict[str, str]:
    record_path = _required(_validation_record(paths), "Materialization record")
    record = _load_json(record_path)
    if not isinstance(record, dict):
        raise RecipeError("Materialization record must be a JSON object")
    city_mode = record.get("mode") == "city-world"
    source_dir = _required(
        drone_root
        / (CITY_WORLD_SOURCE_DRONE_CONFIG if city_mode else SOURCE_DRONE_CONFIG),
        "base Drone config" if city_mode else "Shibuya Drone config",
    )
    generated_dir = _required(
        paths.recipe_config / GENERATED_DRONE_CONFIG.relative_to("config"),
        "Generated Shibuya Drone config",
    )
    source_json = _load_json(source_dir / "drone_config_0.json")
    generated_json = _load_json(generated_dir / "drone_config_0.json")
    changes = _json_changes(source_json, generated_json)
    if city_mode:
        expected_changes = set(record["source_assets"]["json_changes"])
        _assert(changes == expected_changes, "generated City World Drone config changed")
    else:
        _assert(
            changes == ALLOWED_JSON_CHANGES,
            "generated Drone config differs outside the four allowlisted paths",
        )
    generated_location = generated_json["simulation"]["location"]
    _assert(
        generated_json["components"]["droneDynamics"]["mujoco"]["modelPath"]
        == str(GENERATED_MUJOCO_MJB),
        "Drone config does not select the compiled MuJoCo model",
    )
    expected_location = record["coordinate_invariants"]["drone_simulation_location"]
    _assert(
        all(generated_location.get(key) == value for key, value in expected_location.items()),
        "Drone simulation location changed",
    )
    _assert(
        generated_json["simulation"]["timeStep"] == (0.001 if city_mode else 0.003),
        "Drone simulation timestep changed",
    )
    generated_xml = generated_dir / "drone.xml"
    generated_mjb = _required(generated_dir / "drone.mjb", "Compiled MuJoCo model")
    compiled_model = record["source_assets"].get("compiled_model", {})
    _assert(isinstance(compiled_model, dict), "compiled model receipt is missing")
    _assert(
        compiled_model.get("source_xml_sha256") == _sha256(generated_xml),
        "compiled model source XML hash changed",
    )
    _assert(
        compiled_model.get("output_mjb_sha256") == _sha256(generated_mjb),
        "compiled MuJoCo model hash changed",
    )
    _assert(
        compiled_model.get("reload_validation") == "passed",
        "compiled MuJoCo model was not reload-validated",
    )
    if city_mode:
        try:
            xml_root = ET.parse(generated_xml).getroot()
        except (OSError, ET.ParseError) as exc:
            raise RecipeError(f"invalid generated City World MJCF: {exc}") from exc
        _assert(xml_root.find(".//geom[@name='ground']") is None, "base ground was retained")
        _assert(
            xml_root.find(".//body[@name='drone_base']") is not None,
            "generated MJCF has no drone_base",
        )
        drone_default = xml_root.find("./default/default[@class='drone']/geom")
        _assert(drone_default is not None, "generated MJCF has no Drone geom default")
        _assert(
            all(drone_default.get(key) == value for key, value in DRONE_COLLISION_MASK.items()),
            "Drone collision mask changed",
        )
        _assert(
            len(xml_root.findall("./worldbody/*")) > 1,
            "City World was not composed into the Drone MJCF",
        )
    else:
        _assert(
            _sha256(source_dir / "drone.xml") == _sha256(generated_xml),
            "generated drone.xml differs from the Drone Core source",
        )
        _assert(
            'timestep="0.003"' in generated_xml.read_text(encoding="utf-8"),
            "MuJoCo XML timestep changed",
        )
    source_param = _required(
        drone_root
        / (CITY_WORLD_CONTROLLER_PARAM if city_mode else SOURCE_CONTROLLER_PARAM),
        "MuJoCo controller parameters",
    )
    generated_param = _required(
        paths.recipe_config / GENERATED_CONTROLLER_PARAM.relative_to("config"),
        "Generated Shibuya controller parameters",
    )
    _assert(
        _sha256(source_param) == _sha256(generated_param),
        "generated controller parameters differ from the Drone Core source",
    )

    glb = _required(Path(record["browser_bundle"]["glb_destination"]), "Generated GLB")
    _assert(_sha256(glb) == record["glb"]["sha256"], "generated GLB hash changed")
    coordinates = record["coordinate_invariants"]
    _assert(
        coordinates["drone_simulation_location"] == expected_location,
        "recorded Drone simulation location changed",
    )
    _assert(
        coordinates["plateau_map_origin"] == record["browser_bundle"]["map_origin"],
        "recorded PLATEAU map origin changed",
    )
    _assert(
        coordinates["map_origin_derived_from_drone_location"] is False,
        "Map Viewer origin must not be derived from Drone simulation.location",
    )

    map_ui = _required(
        paths.recipe_root / "web" / "map-viewer" / "src" / "client" / "src" / "ui.js",
        "Generated Map Viewer UI",
    ).read_text(encoding="utf-8")
    map_origin = coordinates["plateau_map_origin"]
    _assert(
        f"setView([{map_origin['latitude']}, {map_origin['longitude']}], 15)" in map_ui,
        "Map Viewer initial center changed",
    )
    _assert(
        f"let ORIGIN_LAT = {map_origin['latitude']}" in map_ui,
        "Map Viewer latitude changed",
    )
    _assert(
        f"let ORIGIN_LON = {map_origin['longitude']}" in map_ui,
        "Map Viewer longitude changed",
    )

    launcher_path = _required(
        paths.recipe_config / "launcher.json", "Generated Launcher"
    )
    launcher = _load_json(launcher_path)
    asset_names = [asset["name"] for asset in launcher["assets"]]
    _assert(
        asset_names
        == [
            "drone-service-1",
            "web-bridge-single-drone",
            "remote-controller",
            "map-viewer-webserver",
        ],
        "Launcher topology is incomplete",
    )
    generated_json_files = sorted(paths.recipe_config.rglob("*.json"))
    generated_json_files.extend(
        sorted(
            (
                paths.recipe_root
                / "web"
                / "map-viewer"
                / "thirdparty"
                / "hakoniwa-threejs-drone"
                / "config"
            ).rglob("*.json")
        )
    )
    for path in generated_json_files:
        _assert(
            "hakoniwa-envsim" not in path.read_text(encoding="utf-8").lower(),
            f"forbidden hakoniwa-envsim reference: {path}",
        )
    return {
        "drone_config_contract": "OK",
        "drone_xml_contract": "OK",
        "drone_mjb_contract": "OK",
        "controller": "RadioController",
        "coordinates": "OK",
        "glb_hash": "OK",
        "launcher_topology": "OK",
        "no_envsim_runtime": "OK",
    }


def configure(
    drone_root: Path,
    map_viewer_root: Path,
    threejs_root: Path,
    shibuya_glb: Path | None,
    glb_source: str | None,
    overrides: dict[str, Path | None],
    city_world_path: Path | None = None,
    spawn_altitude_m: float = DEFAULT_SPAWN_ALTITUDE_M,
) -> int:
    foundation, paths, runtime = _preflight(
        drone_root, map_viewer_root, threejs_root, overrides
    )
    gamepad.install_runtime_dependencies(runtime.foundation_python)
    foundation.prepare_workspace(paths)
    (paths.recipe_root / "runtime").mkdir(parents=True, exist_ok=True)
    if city_world_path is not None and shibuya_glb is not None:
        raise RecipeError("--city-world and --shibuya-glb cannot be used together")
    city_world = _resolve_city_world(city_world_path) if city_world_path else None
    if city_world is not None:
        staged_glb = city_world.glb_path
        default_provenance = f"City World receipt: {city_world.receipt_path}"
    else:
        staged_glb, default_provenance = _stage_glb(paths, shibuya_glb)
    record = materialize_runtime(
        paths,
        drone_root,
        map_viewer_root,
        threejs_root,
        staged_glb,
        glb_source or default_provenance,
        runtime,
        city_world=city_world,
        spawn_altitude_m=spawn_altitude_m,
    )
    print(f"Recipe workspace : {paths.recipe_root}")
    print(f"Recipe portal    : {paths.recipe_root / 'index.html'}")
    print(f"Launcher         : {paths.recipe_config / 'launcher.json'}")
    print(f"Validation       : {record}")
    print(f"Viewer           : {VIEWER_URL}")
    return 0


def doctor(
    drone_root: Path,
    map_viewer_root: Path,
    threejs_root: Path,
    overrides: dict[str, Path | None],
) -> int:
    _foundation, paths, runtime = _preflight(
        drone_root, map_viewer_root, threejs_root, overrides
    )
    checks: list[tuple[str, bool | None, str]] = [
        ("platform", True, runtime.system_name),
        ("drone service", runtime.drone_service.is_file(), str(runtime.drone_service)),
        ("Foundation Python", runtime.foundation_python.is_file(), str(runtime.foundation_python)),
        ("hako-cmd", runtime.hako_cmd.is_file(), str(runtime.hako_cmd)),
        ("WebBridge", runtime.web_bridge.is_file(), str(runtime.web_bridge)),
        ("port 8000", gamepad._port_available(8000), "available"),
        ("port 8765", gamepad._port_available(8765), "available"),
    ]
    python_ok, python_detail = gamepad._probe_python_runtime(
        runtime.foundation_python, paths.foundation_python
    )
    checks.append(("Foundation Python imports", python_ok, python_detail))
    controller_ok, controller_detail = gamepad._probe_controller(
        runtime.foundation_python
    )
    checks.append(("gamepad", controller_ok, controller_detail))
    try:
        details = validate_materialization(paths, drone_root)
        checks.extend((name, True, detail) for name, detail in details.items())
    except RecipeError as exc:
        checks.append(("materialization", False, str(exc)))

    failed = False
    for name, ok, detail in checks:
        if ok is None:
            print(f"[WARN] {name}: unavailable in this execution environment")
            continue
        print(f"[{'OK' if ok else 'NG'}] {name}: {detail}")
        failed = failed or not ok
    return 1 if failed else 0


def start(
    drone_root: Path,
    map_viewer_root: Path,
    threejs_root: Path,
    overrides: dict[str, Path | None],
) -> int:
    _foundation, paths, runtime = _preflight(
        drone_root, map_viewer_root, threejs_root, overrides
    )
    validate_materialization(paths, drone_root)
    launcher = _required(paths.recipe_config / "launcher.json", "Generated Launcher")
    launcher_data = _load_json(launcher)
    launcher_assets = launcher_data.get("assets", []) if isinstance(launcher_data, dict) else []
    drone_asset = next(
        (
            asset
            for asset in launcher_assets
            if isinstance(asset, dict) and asset.get("name") == "drone-service-1"
        ),
        None,
    )
    configured_service = (
        Path(str(drone_asset.get("command"))).resolve()
        if isinstance(drone_asset, dict) and drone_asset.get("command")
        else None
    )
    if configured_service != runtime.drone_service.resolve():
        raise RecipeError(
            "Launcher Drone Service differs from the selected runtime; rerun configure. "
            f"launcher={configured_service} runtime={runtime.drone_service.resolve()}"
        )
    session = session_file(paths)
    session.parent.mkdir(parents=True, exist_ok=True)
    _clear_runtime_logs(paths)
    rc = gamepad.start_launcher_and_verify(
        runtime.foundation_python,
        launcher,
        session,
        gamepad.runtime_environment(paths, runtime),
    )
    if rc != 0:
        return rc

    ready, missing = wait_for_demo_ready(paths)
    if ready:
        print("[OK] Demo ready: MuJoCo, simulation, HTTP 8000, WebSocket 8765")
        print_background_handoff(paths, runtime)
        return 0

    print(
        "[NG] Demo did not become ready: " + ", ".join(missing),
        file=sys.stderr,
    )
    print(f"Inspect logs under {paths.recipe_logs}", file=sys.stderr)
    gamepad._run(
        gamepad.launcher_control_command(
            runtime.foundation_python, "terminate", session
        )
    )
    return 1


def status() -> int:
    foundation = gamepad.load_foundation_module()
    paths = foundation.resolve_workspace(root(), RECIPE_ID)
    python = gamepad.resolve_foundation_python(paths)
    return gamepad._run(
        gamepad.launcher_control_command(python, "status", session_file(paths))
    )


def stop() -> int:
    foundation = gamepad.load_foundation_module()
    paths = foundation.resolve_workspace(root(), RECIPE_ID)
    python = gamepad.resolve_foundation_python(paths)
    return gamepad._run(
        gamepad.launcher_control_command(python, "terminate", session_file(paths))
    )


def reset(
    drone_root: Path,
    map_viewer_root: Path,
    threejs_root: Path,
    overrides: dict[str, Path | None],
) -> int:
    _foundation, paths, runtime = _preflight(
        drone_root, map_viewer_root, threejs_root, overrides
    )
    env = gamepad.runtime_environment(paths, runtime)
    for command in gamepad.reset_commands(runtime.hako_cmd):
        rc = gamepad._run(command, env)
        if rc != 0:
            return rc
    return 0


def open_viewer() -> int:
    if not _tcp_ready(8000):
        print(
            "[NG] Map Viewer is not ready on http://127.0.0.1:8000. "
            "Run start and wait for '[OK] Demo ready' first.",
            file=sys.stderr,
        )
        return 1
    print(f"Opening {VIEWER_URL}")
    return 0 if webbrowser.open(VIEWER_URL) else 1


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(
        description=(
            "Configure and operate the single Hakoniwa Drone gamepad Recipe "
            "with a generated City World or the legacy Shibuya preset"
        )
    )
    result.add_argument(
        "command",
        choices=("configure", "doctor", "start", "status", "reset", "stop", "open-viewer"),
    )
    result.add_argument(
        "--drone-root", type=Path, default=default_source("hakoniwa-drone-pro")
    )
    result.add_argument(
        "--map-viewer-root", type=Path, default=default_source("hakoniwa-map-viewer")
    )
    result.add_argument(
        "--threejs-root", type=Path, default=default_source("hakoniwa-threejs-drone")
    )
    result.add_argument(
        "--shibuya-glb",
        type=Path,
        help=(
            "Offline path to the pinned v0.0.1 GLB; when omitted, configure "
            "downloads the declared Release Asset"
        ),
    )
    result.add_argument(
        "--glb-source",
        help="Human-readable GLB provenance, such as the release URL or source record",
    )
    result.add_argument(
        "--city-world",
        type=Path,
        help=(
            "Generated City World worker job, build/world directory, receipt, MJCF, "
            "or GLB. Used by configure to replace the legacy Shibuya world."
        ),
    )
    result.add_argument(
        "--spawn-altitude-m",
        type=float,
        default=DEFAULT_SPAWN_ALTITUDE_M,
        help="Initial Drone altitude in the City World local frame (default: 20 m)",
    )
    result.add_argument("--drone-service-bin", type=Path)
    result.add_argument("--visual-state-publisher-bin", type=Path)
    result.add_argument("--hako-cmd-bin", type=Path)
    result.add_argument("--web-bridge-bin", type=Path)
    return result


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    overrides = {
        "drone_service_bin": args.drone_service_bin,
        "visual_state_publisher_bin": args.visual_state_publisher_bin,
        "hako_cmd_bin": args.hako_cmd_bin,
        "web_bridge_bin": args.web_bridge_bin,
    }
    try:
        drone_root = _absolute(args.drone_root)
        map_viewer_root = _absolute(args.map_viewer_root)
        threejs_root = _absolute(args.threejs_root)
        if args.command == "configure":
            return configure(
                drone_root,
                map_viewer_root,
                threejs_root,
                args.shibuya_glb,
                args.glb_source,
                overrides,
                args.city_world,
                args.spawn_altitude_m,
            )
        if args.command == "doctor":
            return doctor(drone_root, map_viewer_root, threejs_root, overrides)
        if args.command == "start":
            return start(drone_root, map_viewer_root, threejs_root, overrides)
        if args.command == "status":
            return status()
        if args.command == "reset":
            return reset(drone_root, map_viewer_root, threejs_root, overrides)
        if args.command == "stop":
            return stop()
        return open_viewer()
    except RecipeError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
