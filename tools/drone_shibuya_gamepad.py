#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import socket
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
import webbrowser
from pathlib import Path

TOOLS_DIR = Path(__file__).absolute().parent
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

import drone_gamepad_exhibition as gamepad
from recipe_portal import (
    PortalCommand,
    PortalEnvironment,
    PortalLink,
    write_recipe_portal,
)

RECIPE_ID = "drone-single-mujoco-shibuya-map-gamepad"
SOURCE_DRONE_CONFIG = Path("config/drone/mujoco-shibuya-api-1")
GENERATED_DRONE_CONFIG = Path("config/drone/mujoco-shibuya-gamepad-1")
SOURCE_CONTROLLER_PARAM = Path(
    "config/controller/param-api-mixer-mujoco-shibuya.txt"
)
GENERATED_CONTROLLER_PARAM = Path(
    "config/controller/param-gamepad-mixer-mujoco-shibuya.txt"
)
DRONE_PDU_CONFIG = "config/pdudef/drone-pdudef-1.json"
VISUAL_STATE_CONFIG = "visual_state_publisher-1.json"
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

RecipeError = gamepad.RecipeError
RuntimePaths = gamepad.RuntimePaths


def root() -> Path:
    return Path(__file__).absolute().parents[1]


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


def _align_map_viewer_origin(client: Path) -> Path:
    ui_path = _required(client / "src" / "ui.js", "Map Viewer UI")
    source = ui_path.read_text(encoding="utf-8")
    default_center = (
        "const map = L.map('map').setView("
        f"[{MAP_VIEWER_DEFAULT_CENTER['latitude']}, "
        f"{MAP_VIEWER_DEFAULT_CENTER['longitude']}], 15);"
    )
    aligned_center = (
        "const map = L.map('map').setView("
        f"[{MAP_ORIGIN['latitude']}, {MAP_ORIGIN['longitude']}], 15);"
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
        MAP_ORIGIN["latitude"],
    )
    source = _replace_map_origin_assignment(
        source,
        "ORIGIN_LON",
        MAP_VIEWER_DEFAULT_ORIGIN["longitude"],
        MAP_ORIGIN["longitude"],
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
    runtime = gamepad.resolve_runtime(paths, drone_root, **overrides)
    return foundation, paths, runtime


def _copy_runtime_config(drone_root: Path, recipe_config: Path) -> None:
    for relative in (
        Path("pdudef"),
        Path("assets/visual_state_publisher"),
        Path("assets/web_bridge_fleets"),
    ):
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
        GENERATED_DRONE_CONFIG / "drone.xml"
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
        "source_controller_param": str(source_param),
        "generated_controller_param": str(generated_param),
        "controller_param_sha256": _sha256(generated_param),
    }


def _materialize_browser(
    map_viewer_root: Path,
    threejs_root: Path,
    shibuya_glb: Path,
    browser_root: Path,
) -> dict[str, object]:
    client = browser_root / "src" / "client"
    images = browser_root / "images"
    embedded = browser_root / "thirdparty" / "hakoniwa-threejs-drone"
    _copytree(_required(map_viewer_root / "src" / "client", "Map Viewer client"), client)
    _copytree(_required(map_viewer_root / "images", "Map Viewer images"), images)
    map_ui_path = _align_map_viewer_origin(client)

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
    scene_path = embedded / "config" / SCENE_CONFIG_NAME
    _write_json(scene_path, scene)

    viewer = _load_json(
        _required(
            threejs_root / "config" / "viewer-config-fleets.json",
            "Three.js fleets viewer config",
        )
    )
    if not isinstance(viewer, dict):
        raise RecipeError("Three.js fleets viewer config must be a JSON object")
    viewer["three"]["sceneConfigPath"] = f"./{SCENE_CONFIG_NAME}"
    viewer_path = embedded / "config" / VIEWER_CONFIG_NAME
    _write_json(viewer_path, viewer)

    glb_destination = embedded / "assets" / "local_models" / GLB_NAME
    glb_destination.parent.mkdir(parents=True, exist_ok=True)
    if glb_destination.exists():
        glb_destination.unlink()
    try:
        os.link(shibuya_glb, glb_destination)
    except OSError:
        shutil.copy2(shibuya_glb, glb_destination)
    return {
        "map_client_source": str(map_viewer_root / "src" / "client"),
        "map_ui": str(map_ui_path),
        "map_source_origin": MAP_VIEWER_DEFAULT_ORIGIN,
        "map_origin": MAP_ORIGIN,
        "map_initial_center": MAP_ORIGIN,
        "threejs_source": str(threejs_root),
        "viewer_config": str(viewer_path),
        "scene_config": str(scene_path),
        "glb_destination": str(glb_destination),
        "glb_sha256": _sha256(glb_destination),
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
                    "--mujoco-viewer",
                    "--real-sleep-msec",
                    "1",
                ],
                "cwd": str(paths.recipe_root),
                # The Shibuya MJCF is large. Do not start dependent assets
                # while MuJoCo is still compiling the model and before the
                # drone service has created the Hakoniwa master.
                "delay_sec": 8,
            },
            {
                "name": "visual-state-publisher",
                "activation_timing": "before_start",
                "command": str(runtime.visual_state_publisher),
                "args": [
                    str(
                        paths.recipe_config
                        / "assets"
                        / "visual_state_publisher"
                        / VISUAL_STATE_CONFIG
                    )
                ],
                "cwd": str(paths.recipe_root),
                "depends_on": ["drone-service-1"],
                "delay_sec": 2,
            },
            {
                "name": "web-bridge-fleets",
                "activation_timing": "before_start",
                "command": str(runtime.web_bridge),
                "args": [
                    "--config-root",
                    str(paths.recipe_config / "assets" / "web_bridge_fleets"),
                    "--node-name",
                    "web_bridge_fleets_node1",
                    "--delta-time-step-usec",
                    "20000",
                    "--enable-ondemand",
                ],
                "cwd": str(paths.recipe_root),
                "depends_on": ["visual-state-publisher"],
            },
            {
                "name": "remote-controller",
                "activation_timing": "after_start",
                "command": str(runtime.foundation_python),
                "args": [
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
                "depends_on": ["web-bridge-fleets"],
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
            "MuJoCo Viewer": "Viewer thread started." in drone_log,
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
    print("  1. MuJoCo Viewerウィンドウが表示されている")
    print("  2. 次のcommandでMap Viewerを開ける")
    print(f"     {_display_command(runtime, 'open-viewer')}")
    print("  3. PS5コントローラでDroneを操作できる")
    print("状態確認:")
    print(f"     {_display_command(runtime, 'status')}")
    print("終了:")
    print(f"     {_display_command(runtime, 'stop')}")
    print(f"Session: {session_file(paths)}")
    print(f"Logs   : {paths.recipe_logs}")


def _display_command(_runtime: RuntimePaths, action: str) -> str:
    return f"python tools/drone_shibuya_gamepad.py {action}"


def write_portal(paths, runtime: RuntimePaths, launcher: Path) -> Path:
    return write_recipe_portal(
        paths.recipe_root / "index.html",
        recipe_id=RECIPE_ID,
        title="Hakoniwa Drone Shibuya Gamepad Demo",
        summary=(
            "PS5コントローラで渋谷のMuJoCo衝突ワールドを飛行し、"
            "同じ状態をLeaflet地図とThree.js PLATEAUシーンで確認するRecipeです。"
        ),
        topology=(
            "PS5 controller",
            "RadioController",
            "Hakoniwa Drone + Shibuya MuJoCo",
            "DroneVisualStatePublisher",
            "WebBridge",
            "Leaflet + Three.js",
        ),
        commands=tuple(
            PortalCommand(label, _display_command(runtime, action), description)
            for label, action, description in (
                ("Preflight", "doctor", "Foundation、生成物、ゲームパッド、ポートを確認します。"),
                (
                    "Start",
                    "start",
                    "4つのready条件を確認後に復帰します。復帰後もDemoはbackgroundで継続します。",
                ),
                ("Open viewer", "open-viewer", "渋谷Map Viewerを既定ブラウザで開きます。"),
                ("Status", "status", "Launcherセッションの状態を確認します。"),
                ("Reset", "reset", "シミュレーションを初期状態へ戻します。"),
                ("Stop", "stop", "Launcherの通常終了経路で全アセットを終了します。"),
            )
        ),
        links=(
            PortalLink("Shibuya Map Viewer", VIEWER_URL, "LeafletとThree.jsの統合画面"),
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
            PortalEnvironment("Drone simulation location", "35.6625, 139.69375, 15.4"),
            PortalEnvironment("PLATEAU map origin", "35.6625, 139.70625"),
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
) -> Path:
    sources = _source_paths(
        drone_root, map_viewer_root, threejs_root, shibuya_glb
    )
    _copy_runtime_config(drone_root, paths.recipe_config)
    drone_record = _materialize_drone(drone_root, paths.recipe_config)
    browser_record = _materialize_browser(
        map_viewer_root,
        threejs_root,
        sources["shibuya_glb"],
        paths.recipe_root / "web" / "map-viewer",
    )
    launcher = write_launcher(paths, drone_root, runtime)
    portal = write_portal(paths, runtime, launcher)
    record = {
        "schema_version": 1,
        "recipe_id": RECIPE_ID,
        "source_assets": drone_record,
        "browser_bundle": browser_record,
        "glb": {
            "input": str(sources["shibuya_glb"]),
            "source": glb_source,
            "sha256": browser_record["glb_sha256"],
            "release_url": GLB_RELEASE_URL,
            "download_url": GLB_DOWNLOAD_URL,
            "expected_sha256": GLB_SHA256,
        },
        "coordinate_invariants": {
            "drone_simulation_location": MUJOCO_LOCATION,
            "map_viewer_source_origin": MAP_VIEWER_DEFAULT_ORIGIN,
            "plateau_map_origin": MAP_ORIGIN,
            "map_origin_derived_from_drone_location": False,
        },
        "launcher": str(launcher),
        "portal": str(portal),
        "no_envsim": True,
    }
    output = _validation_record(paths)
    _write_json(output, record)
    validate_materialization(paths, drone_root)
    return output


def _assert(condition: bool, message: str) -> None:
    if not condition:
        raise RecipeError(message)


def validate_materialization(paths, drone_root: Path) -> dict[str, str]:
    source_dir = _required(
        drone_root / SOURCE_DRONE_CONFIG, "Shibuya Drone config"
    )
    generated_dir = _required(
        paths.recipe_config / GENERATED_DRONE_CONFIG.relative_to("config"),
        "Generated Shibuya Drone config",
    )
    source_json = _load_json(source_dir / "drone_config_0.json")
    generated_json = _load_json(generated_dir / "drone_config_0.json")
    _assert(
        _json_changes(source_json, generated_json) == ALLOWED_JSON_CHANGES,
        "generated Drone config differs outside the four allowlisted paths",
    )
    generated_location = generated_json["simulation"]["location"]
    _assert(
        all(generated_location.get(key) == value for key, value in MUJOCO_LOCATION.items()),
        "Drone simulation location changed",
    )
    _assert(
        generated_json["simulation"]["timeStep"] == 0.003,
        "Drone simulation timestep changed",
    )
    _assert(
        _sha256(source_dir / "drone.xml") == _sha256(generated_dir / "drone.xml"),
        "generated drone.xml differs from the Drone Core source",
    )
    _assert(
        'timestep="0.003"' in (generated_dir / "drone.xml").read_text(encoding="utf-8"),
        "MuJoCo XML timestep changed",
    )
    source_param = _required(
        drone_root / SOURCE_CONTROLLER_PARAM, "Shibuya controller parameters"
    )
    generated_param = _required(
        paths.recipe_config / GENERATED_CONTROLLER_PARAM.relative_to("config"),
        "Generated Shibuya controller parameters",
    )
    _assert(
        _sha256(source_param) == _sha256(generated_param),
        "generated controller parameters differ from the Drone Core source",
    )

    record_path = _required(_validation_record(paths), "Materialization record")
    record = _load_json(record_path)
    glb = _required(Path(record["browser_bundle"]["glb_destination"]), "Generated GLB")
    _assert(_sha256(glb) == record["glb"]["sha256"], "generated GLB hash changed")
    coordinates = record["coordinate_invariants"]
    _assert(
        coordinates["drone_simulation_location"] == MUJOCO_LOCATION,
        "recorded Drone simulation location changed",
    )
    _assert(
        coordinates["plateau_map_origin"] == MAP_ORIGIN,
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
    _assert(
        "setView([35.6625, 139.70625], 15)" in map_ui,
        "Map Viewer initial center changed",
    )
    _assert("let ORIGIN_LAT = 35.6625" in map_ui, "Map Viewer latitude changed")
    _assert("let ORIGIN_LON = 139.70625" in map_ui, "Map Viewer longitude changed")

    launcher_path = _required(
        paths.recipe_config / "launcher.json", "Generated Launcher"
    )
    launcher = _load_json(launcher_path)
    asset_names = [asset["name"] for asset in launcher["assets"]]
    _assert(
        asset_names
        == [
            "drone-service-1",
            "visual-state-publisher",
            "web-bridge-fleets",
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
        "allowlisted_json_changes": "OK",
        "drone_xml_hash": "OK",
        "controller": "RadioController",
        "coordinates": "OK",
        "glb_hash": "OK",
        "launcher_topology": "OK",
        "no_envsim": "OK",
    }


def configure(
    drone_root: Path,
    map_viewer_root: Path,
    threejs_root: Path,
    shibuya_glb: Path | None,
    glb_source: str | None,
    overrides: dict[str, Path | None],
) -> int:
    foundation, paths, runtime = _preflight(
        drone_root, map_viewer_root, threejs_root, overrides
    )
    gamepad.install_runtime_dependencies(runtime.foundation_python)
    foundation.prepare_workspace(paths)
    (paths.recipe_root / "runtime").mkdir(parents=True, exist_ok=True)
    staged_glb, default_provenance = _stage_glb(paths, shibuya_glb)
    record = materialize_runtime(
        paths,
        drone_root,
        map_viewer_root,
        threejs_root,
        staged_glb,
        glb_source or default_provenance,
        runtime,
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
        (
            "visual-state publisher",
            runtime.visual_state_publisher.is_file(),
            str(runtime.visual_state_publisher),
        ),
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
        description="Configure and operate the Hakoniwa Drone Shibuya gamepad Recipe"
    )
    result.add_argument(
        "command",
        choices=("configure", "doctor", "start", "status", "reset", "stop", "open-viewer"),
    )
    result.add_argument(
        "--drone-root", type=Path, default=default_source("hakoniwa-drone-core")
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
