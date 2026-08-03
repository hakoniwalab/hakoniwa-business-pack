#!/usr/bin/env python3
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import platform
import shutil
import signal
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

TOOLS_DIR = Path(__file__).absolute().parent
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

from recipe_portal import PortalCommand, PortalEnvironment, PortalLink, write_recipe_portal


RECIPE_ID = "mujoco-turtlebot3-godot"
RUNTIME_REQUIREMENTS = (
    Path(__file__).absolute().parents[1]
    / "recipes"
    / "requirements"
    / f"{RECIPE_ID}.txt"
)
PROFILES = ("gamepad", "route")
CODEC_PACKAGES = "geometry_msgs;sensor_msgs"
AXIS_COUNT = 6
BUTTON_COUNT = 15


class RecipeError(RuntimeError):
    pass


@dataclass(frozen=True)
class RuntimePaths:
    system_name: str
    foundation_python: Path
    hako_cmd: Path
    endpoint_callback_library: Path
    endpoint_polling_library: Path
    godot_binary: Path
    tb3_binary: Path
    route_script: Path


def root() -> Path:
    return Path(__file__).absolute().parents[1]


def default_source(name: str) -> Path:
    return root().parent / name


def recipe_file() -> Path:
    return root() / "recipes/examples" / f"{RECIPE_ID}.yaml"


def load_foundation_module():
    script = Path(__file__).with_name("foundation.py")
    spec = importlib.util.spec_from_file_location("business_pack_foundation_tb3_godot", script)
    if spec is None or spec.loader is None:
        raise RecipeError(f"cannot load Foundation helper: {script}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def absolute(path: Path) -> Path:
    return Path(os.path.abspath(path.expanduser()))


def required(path: Path, label: str) -> Path:
    value = absolute(path)
    if not value.exists():
        raise RecipeError(f"{label} not found: {value}")
    return value


def resolve_foundation_python(paths) -> Path:
    for candidate in (
        paths.foundation_python / "Scripts/python.exe",
        paths.foundation_python / "bin/python3",
        paths.foundation_python / "bin/python",
    ):
        if candidate.is_file():
            return absolute(candidate)
    raise RecipeError(f"Foundation Python not found under {paths.foundation_python}")


def endpoint_library(prefix: Path, system_name: str, profile: str) -> Path:
    if system_name == "Darwin":
        name = f"libhakoniwa_pdu_endpoint_core_{profile}.dylib"
    elif system_name == "Windows":
        name = f"hakoniwa_pdu_endpoint_core_{profile}.dll"
    else:
        name = f"libhakoniwa_pdu_endpoint_core_{profile}.so"
    candidates = (prefix / "lib" / name, prefix / "bin" / name)
    return absolute(next((item for item in candidates if item.is_file()), candidates[0]))


def discover_godot(explicit: Path | None = None) -> Path:
    candidates: list[Path] = []
    if explicit is not None:
        candidates.append(explicit)
    configured = os.getenv("GODOT_BIN")
    if configured:
        candidates.append(Path(configured))
    for name in ("godot-mono", "godot"):
        found = shutil.which(name)
        if found:
            candidates.append(Path(found))
    if platform.system() == "Darwin":
        candidates.append(Path("/Applications/Godot_mono.app/Contents/MacOS/Godot"))
    for candidate in candidates:
        value = absolute(candidate)
        if value.is_file():
            return value
    fallback = explicit or Path("godot")
    return absolute(fallback)


def resolve_runtime(paths, mujoco_root: Path, godot_binary: Path | None = None) -> RuntimePaths:
    system_name = platform.system()
    suffix = ".exe" if system_name == "Windows" else ""
    tb3 = paths.recipe_root / "build/mujoco/main_for_sample/tb3" / f"tb3_sim_burger{suffix}"
    return RuntimePaths(
        system_name=system_name,
        foundation_python=resolve_foundation_python(paths),
        hako_cmd=absolute(paths.install_prefix / "bin" / f"hako-cmd{suffix}"),
        endpoint_callback_library=endpoint_library(paths.install_prefix, system_name, "callback"),
        endpoint_polling_library=endpoint_library(paths.install_prefix, system_name, "polling"),
        godot_binary=discover_godot(godot_binary),
        tb3_binary=absolute(tb3),
        route_script=absolute(mujoco_root / "python/tb3_route_demo.py"),
    )


def preflight(mujoco_root: Path, godot_binary: Path | None = None):
    foundation = load_foundation_module()
    paths = foundation.resolve_workspace(root(), RECIPE_ID)
    inspection = foundation.inspect_foundation(recipe_file(), paths.install_prefix)
    if inspection["status"] != "SATISFIED":
        foundation.print_inspection(inspection, False)
        raise RecipeError("Foundation is not reusable; run tools/foundation.py plan/build first")
    return foundation, paths, resolve_runtime(paths, mujoco_root, godot_binary)


def write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def copy_tree(source: Path, destination: Path) -> None:
    shutil.copytree(source, destination, dirs_exist_ok=True, ignore=shutil.ignore_patterns(".DS_Store"))


def source_revision(repository: Path) -> str:
    completed = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repository, text=True, capture_output=True, check=False
    )
    if completed.returncode != 0:
        raise RecipeError(f"cannot resolve source revision: {repository}")
    return completed.stdout.strip()


def stage_godot_source(paths, godot_root: Path) -> Path:
    destination = paths.recipe_root / "src/hakoniwa-godot"
    marker = destination / ".business-pack-source-revision"
    revision = source_revision(godot_root)
    if marker.is_file() and marker.read_text(encoding="utf-8").strip() == revision:
        return destination
    if destination.exists():
        shutil.rmtree(destination)
    shutil.copytree(
        godot_root,
        destination,
        ignore=shutil.ignore_patterns(".git", ".godot", "build", "build-*", "*.user"),
    )
    marker.write_text(revision + "\n", encoding="utf-8")
    return destination


def stage_runtime_inputs(paths, mujoco_root: Path, mbody_root: Path, godot_root: Path) -> None:
    copy_tree(required(mujoco_root / "config", "MuJoCo Robots config"), paths.recipe_config / "mujoco")
    copy_tree(
        required(mbody_root / "bodies/turtlebot3_burger", "MBody Burger artifacts"),
        paths.recipe_root / "assets/mbody/turtlebot3_burger",
    )
    reference = required(mbody_root / "bodies/turtlebot3", "MBody Godot reference")
    copy_tree(reference / "generated/parts", paths.recipe_root / "project/assets/parts")
    shutil.copy2(
        reference / "godot_tb3_reference/TurtleBot3.generated.tscn",
        paths.recipe_root / "project/assets/TurtleBot3.generated.tscn",
    )
    shutil.copy2(
        godot_root / "examples/mujoco/assets/tb3_reference_sync.gd",
        paths.recipe_root / "project/assets/tb3_reference_sync.gd",
    )
    copy_tree(godot_root / "examples/mujoco/config", paths.recipe_root / "project/config")
    copy_tree(godot_root / "addons/hakoniwa", paths.recipe_root / "project/addons/hakoniwa")
    copy_tree(godot_root / "addons/hakoniwa_robot_sync", paths.recipe_root / "project/addons/hakoniwa_robot_sync")


def write_tb3_manifest(paths) -> Path:
    body = paths.recipe_root / "assets/mbody/turtlebot3_burger"
    config = paths.recipe_config / "mujoco"
    components = [
        ("left_wheel_actuator", "actuator", "joint_actuator", "actuator/joint/tb3_mbody_left_wheel.json", None),
        ("right_wheel_actuator", "actuator", "joint_actuator", "actuator/joint/tb3_mbody_right_wheel.json", None),
        ("wheel_joint_states", "state_output", "joint_state", "sensors/joint_state/tb3-wheel-joint-states.json", "TB3"),
        ("lidar", "sensor", "lidar_2d", "sensors/lidar/lds-02.json", "TB3"),
        ("imu", "sensor", "imu", "sensors/imu/tb3-imu.json", "TB3"),
        ("odometry", "state_output", "odometry", "sensors/odometry/tb3-ground-truth-odom.json", "TB3"),
        ("tf", "state_output", "tf", "sensors/tf/tb3-basic-tf.json", "TB3"),
    ]
    payload = {
        "name": "tb3_business_pack_burger",
        "description": "Recipe-owned TurtleBot3 Burger exhibition manifest.",
        "model": str(absolute(body / "generated/turtlebot3_burger.minimal_world.xml")),
        "pdu_def": str(absolute(config / "tb3-pdudef-compact.json")),
        "endpoint": str(absolute(config / "endpoint/tb3_sim_endpoint.json")),
        "components": [],
    }
    for component_id, kind, component_type, relative, robot in components:
        item = {"id": component_id, "kind": kind, "type": component_type, "config": str(absolute(config / relative))}
        if robot:
            item["pdu_robot"] = robot
        payload["components"].append(item)
    output = paths.recipe_config / "tb3-burger-asset.json"
    write_json(output, payload)
    return output


PROJECT_GODOT = """[application]\nconfig/name=\"Hakoniwa TurtleBot3 Godot Exhibition\"\nrun/main_scene=\"res://main.tscn\"\n[display]\nwindow/size/viewport_width=1280\nwindow/size/viewport_height=720\n[rendering]\nrenderer/rendering_method=\"gl_compatibility\"\nrenderer/rendering_method.mobile=\"gl_compatibility\"\n"""

MAIN_SCENE = """[gd_scene load_steps=3 format=3]\n\n[ext_resource type=\"PackedScene\" path=\"res://assets/TurtleBot3.generated.tscn\" id=\"1_tb3\"]\n[ext_resource type=\"Script\" path=\"res://monitor.gd\" id=\"2_monitor\"]\n\n[node name=\"Main\" type=\"Node3D\"]\nscript = ExtResource(\"2_monitor\")\n\n[node name=\"TurtleBot3\" parent=\".\" instance=ExtResource(\"1_tb3\")]\n\n[node name=\"Camera3D\" type=\"Camera3D\" parent=\".\"]\ntransform = Transform3D(0.707107, -0.301511, 0.639602, 0, 0.904534, 0.426401, -0.707107, -0.301511, 0.639602, 2.8, 1.7, 2.8)\ncurrent = true\n"""

MONITOR_GD = """extends Node3D\n\nvar _last_position := Vector3.ZERO\nvar _elapsed := 0.0\n\nfunc _ready() -> void:\n    print(\"TB3_GODOT_PROJECT_READY\")\n    var sim_node := get_node_or_null(\"TurtleBot3/HakoniwaSimNode\")\n    if sim_node == null:\n        push_error(\"TB3_GODOT_SYNC_ERROR HakoniwaSimNode not found\")\n        return\n    sim_node.simulation_ready.connect(_on_simulation_ready)\n\nfunc _on_simulation_ready() -> void:\n    print(\"TB3_GODOT_SYNC_READY\")\n\nfunc _process(delta: float) -> void:\n    _elapsed += delta\n    if _elapsed < 1.0:\n        return\n    _elapsed = 0.0\n    var body := get_node_or_null(\"TurtleBot3/RosToGodot\") as Node3D\n    if body == null:\n        return\n    var pos := body.position\n    if pos.distance_to(_last_position) > 0.0001:\n        print(\"TB3_GODOT_POSE position=\", pos)\n        _last_position = pos\n"""


def write_project_files(paths) -> None:
    project = paths.recipe_root / "project"
    project.mkdir(parents=True, exist_ok=True)
    (project / "project.godot").write_text(PROJECT_GODOT, encoding="utf-8")
    (project / "main.tscn").write_text(MAIN_SCENE, encoding="utf-8")
    (project / "monitor.gd").write_text(MONITOR_GD, encoding="utf-8")
    write_json(
        project / "addons/hakoniwa/codec_manifest.json",
        {
            "message_script_roots": ["res://addons/hakoniwa_msgs/"],
            "extensions": [
                "res://addons/hakoniwa/codecs/geometry_msgs_codec.gdextension",
                "res://addons/hakoniwa/codecs/sensor_msgs_codec.gdextension",
            ],
        },
    )


def write_build_manifest(paths, mujoco_root: Path) -> Path:
    build_dir = paths.recipe_root / "build/mujoco"
    relative = os.path.relpath(build_dir, mujoco_root)
    output = paths.recipe_config / "mujoco-build.yaml"
    output.write_text(f"version: 1\n\nbuild:\n  dir: {relative}\n", encoding="utf-8")
    return output


def session_file(paths) -> Path:
    return paths.recipe_root / "runtime/launcher-session.json"


def launcher_start_command(python: Path, launcher: Path, session: Path) -> list[str]:
    return [str(python), "-m", "hakoniwa_pdu.apps.launcher.hako_launcher", str(launcher), "--background", str(session)]


def launcher_control_command(python: Path, command: str, session: Path) -> list[str]:
    if command not in {"status", "terminate"}:
        raise RecipeError(f"unsupported Launcher control command: {command}")
    return [str(python), "-m", "hakoniwa_pdu.apps.launcher.hako_launcher_ctl", command, str(session)]


def runtime_environment(paths, runtime: RuntimePaths) -> dict[str, str]:
    env = os.environ.copy()
    env.update({
        "HAKONIWA_CORE_ROOT": str(paths.install_prefix),
        "HAKONIWA_PDU_ENDPOINT_ROOT": str(paths.install_prefix),
        "HAKO_CONFIG_PATH": str(paths.foundation_config / "cpp_core_config.json"),
        "PYTHON_CMD": str(runtime.foundation_python),
        "PYTHONUNBUFFERED": "1",
    })
    env["PATH"] = os.pathsep.join([str(runtime.foundation_python.parent), str(paths.install_prefix / "bin"), env.get("PATH", "")])
    key = "PATH" if runtime.system_name == "Windows" else ("DYLD_LIBRARY_PATH" if runtime.system_name == "Darwin" else "LD_LIBRARY_PATH")
    env[key] = os.pathsep.join([str(paths.install_prefix / "lib"), env.get(key, "")])
    return env


def write_launcher(paths, runtime: RuntimePaths, mujoco_root: Path, profile: str) -> Path:
    if profile not in PROFILES:
        raise RecipeError(f"unsupported profile: {profile}")
    manifest = paths.recipe_config / "tb3-burger-asset.json"
    pdu_def = paths.recipe_config / "mujoco/tb3-pdudef-compact.json"
    controller: dict[str, object]
    if profile == "gamepad":
        controller = {
            "name": "tb3-gamepad",
            "activation_timing": "after_start",
            "command": str(runtime.foundation_python),
            "args": [str(Path(__file__).absolute()), "gamepad-worker", "--mujoco-root", str(mujoco_root), "--config-path", str(pdu_def)],
            "cwd": str(paths.recipe_root),
        }
    else:
        controller = {
            "name": "tb3-route",
            "activation_timing": "after_start",
            "command": str(runtime.foundation_python),
            "args": [str(runtime.route_script), "--config-path", str(pdu_def), "--pattern", "figure8", "--loops", "3", "--linear-axis", "0.6", "--yaw-axis", "0.75", "--forward-sec", "5", "--hold-sec", "600"],
            "cwd": str(mujoco_root),
        }
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
                    "HAKO_CONFIG_PATH": str(paths.foundation_config / "cpp_core_config.json"),
                    "PYTHONUNBUFFERED": "1",
                },
                "prepend": {
                    "lib_path": [str(paths.install_prefix / "lib")],
                    "PATH": [str(runtime.foundation_python.parent), str(paths.install_prefix / "bin")],
                },
            },
        },
        "assets": [
            {
                "name": "tb3-mujoco",
                "activation_timing": "before_start",
                "command": str(runtime.tb3_binary),
                "args": [],
                "env": {"set": {"HAKO_TB3_MANIFEST_PATH": str(manifest), "HAKO_TB3_ENABLE_VIEWER": "1", "HAKO_PDU_ENDPOINT_SHARED_LIB": str(runtime.endpoint_callback_library)}},
                "delay_sec": 2,
            },
            {
                "name": "tb3-godot",
                "activation_timing": "before_start",
                "command": str(runtime.godot_binary),
                "args": ["--path", str(paths.recipe_root / "project")],
                "env": {"set": {"HAKO_PDU_ENDPOINT_SHARED_LIB": str(runtime.endpoint_polling_library)}},
                "depends_on": ["tb3-mujoco"],
                "delay_sec": 2,
            },
            controller,
        ],
    }
    output = paths.recipe_config / f"launcher-{profile}.json"
    write_json(output, launcher)
    return output


def display(action: str) -> str:
    return f"python tools/turtlebot3_godot_exhibition.py {action}"


def write_portal(paths, runtime: RuntimePaths) -> Path:
    return write_recipe_portal(
        paths.recipe_root / "index.html",
        recipe_id=RECIPE_ID,
        title="TurtleBot3 MuJoCo + Godot + PS5 Exhibition",
        summary="PS5またはscripted routeでTB3を操作し、同じPDU状態をMuJoCoとGodotで表示します。",
        topology=("PS5 / route", "GameControllerOperation", "MuJoCo TB3", "Twist + JointState", "Godot robot sync"),
        commands=(
            PortalCommand("Build", display("build"), "MuJoCoとGodot addonをRecipe専用領域へ構築します。"),
            PortalCommand("Doctor", display("doctor"), "Foundation、Godot、生成project、PS5を検査します。"),
            PortalCommand("Start PS5", display("start"), "MuJoCo/GodotとPS5 controllerを開始します。"),
            PortalCommand("Start route", display("start --profile route"), "controller無しのfigure-eight fallbackです。"),
            PortalCommand("Status", display("status"), "Launcher sessionを確認します。"),
            PortalCommand("Stop", display("stop"), "neutral送信後、Launcher通常終了経路で停止します。"),
        ),
        links=(
            PortalLink("Godot project", "project/", "生成済みGodot project"),
            PortalLink("Launchers", "config/", "PS5/route Launcher"),
            PortalLink("Logs", "logs/", "MuJoCo/Godot/controller logs"),
            PortalLink("Session", "runtime/", "Launcher lifecycle state"),
        ),
        environment=(
            PortalEnvironment("Platform", runtime.system_name),
            PortalEnvironment("Foundation", str(paths.install_prefix)),
            PortalEnvironment("Godot", str(runtime.godot_binary)),
            PortalEnvironment("Session", str(session_file(paths))),
        ),
        agency_notes=(
            "PS5のUSB/Bluetooth接続と目視同期確認は人が行います。",
            "実ロボットは制御しません。",
            "停止時はcontrollerがneutralを送信し、Launcher terminateを使用します。",
        ),
    )


def install_runtime_dependencies(python: Path) -> None:
    requirements = required(RUNTIME_REQUIREMENTS, "TurtleBot3 exhibition Python requirements")
    command = [
        str(python),
        "-m",
        "pip",
        "install",
        "--disable-pip-version-check",
        "--requirement",
        str(requirements),
    ]
    rc = run(command)
    if rc:
        raise RecipeError("failed to install TurtleBot3 exhibition dependencies into Foundation Python")


def configure(mujoco_root: Path, mbody_root: Path, godot_root: Path, godot_binary: Path | None) -> int:
    foundation, paths, runtime = preflight(mujoco_root, godot_binary)
    install_runtime_dependencies(runtime.foundation_python)
    foundation.prepare_workspace(paths)
    for directory in (paths.recipe_root / "runtime", paths.recipe_root / "project", paths.recipe_root / "src"):
        directory.mkdir(parents=True, exist_ok=True)
    stage_runtime_inputs(paths, mujoco_root, mbody_root, godot_root)
    stage_godot_source(paths, godot_root)
    write_tb3_manifest(paths)
    write_project_files(paths)
    manifest = write_build_manifest(paths, mujoco_root)
    for profile in PROFILES:
        write_launcher(paths, runtime, mujoco_root, profile)
    portal = write_portal(paths, runtime)
    print(f"Recipe workspace: {paths.recipe_root}")
    print(f"Recipe portal   : {portal}")
    print(f"Build manifest  : {manifest}")
    print(f"Next            : {display('build')}")
    return 0


def run(command: list[str], *, cwd: Path | None = None, env: dict[str, str] | None = None) -> int:
    print("+", subprocess.list2cmdline(command))
    return subprocess.run(command, cwd=cwd, env=env, check=False).returncode


def build(mujoco_root: Path, godot_binary: Path | None) -> int:
    _foundation, paths, runtime = preflight(mujoco_root, godot_binary)
    env = runtime_environment(paths, runtime)
    hako = required(mujoco_root / "tools/hako.py", "MuJoCo Robots hako.py")
    manifest = required(paths.recipe_config / "mujoco-build.yaml", "MuJoCo build manifest")
    for operation in ("doctor", "build"):
        command = [str(runtime.foundation_python), str(hako), operation, "--config", str(manifest)]
        if operation == "build":
            command.extend(["--", "-DCMAKE_BUILD_TYPE=Release"])
        rc = run(command, cwd=mujoco_root, env=env)
        if rc:
            return rc
    source = required(paths.recipe_root / "src/hakoniwa-godot", "Staged Godot source")
    build_dir = source / "build-business-pack"
    configure_command = [
        "cmake", "-S", str(source), "-B", str(build_dir),
        "-DCMAKE_BUILD_TYPE=Release", "-DHAKONIWA_GODOT_BUILD_TESTS=OFF",
        f"-DHAKONIWA_GODOT_CODEC_PACKAGES={CODEC_PACKAGES}",
        f"-DHAKONIWA_GODOT_EXECUTABLE={runtime.godot_binary}",
    ]
    if run(configure_command, env=env):
        return 1
    if run(["cmake", "--build", str(build_dir), "--parallel"], env=env):
        return 1
    project = paths.recipe_root / "project"
    copy_tree(source / "addons/hakoniwa", project / "addons/hakoniwa")
    copy_tree(source / "addons/hakoniwa_robot_sync", project / "addons/hakoniwa_robot_sync")
    rc = run(["bash", str(source / "tools/message_addon_tool.sh"), "sync", "--packages", CODEC_PACKAGES, "--target-dir", str(project / "addons/hakoniwa_msgs")], cwd=source, env=env)
    write_project_files(paths)
    return rc


def probe_python(runtime: RuntimePaths, venv_root: Path) -> tuple[bool, str]:
    code = "import json,pathlib,sys; import hakopy,hakoniwa_pdu,hakoniwa_pdu_endpoint,pygame; print(json.dumps({'prefix':str(pathlib.Path(sys.prefix).absolute()),'pygame':pygame.version.ver}))"
    result = subprocess.run([str(runtime.foundation_python), "-c", code], capture_output=True, text=True, check=False)
    if result.returncode:
        detail = result.stderr.strip() or "Foundation imports failed"
        if "No module named 'pygame'" in detail:
            return False, "pygame is missing; rerun the Recipe configure command"
        return False, detail
    return True, result.stdout.strip().splitlines()[-1]


def gamepad_probe(runtime: RuntimePaths) -> tuple[bool, str]:
    code = "import json,pygame; pygame.init(); pygame.joystick.init(); print(json.dumps({'count':pygame.joystick.get_count(),'names':[pygame.joystick.Joystick(i).get_name() for i in range(pygame.joystick.get_count())]})); pygame.quit()"
    result = subprocess.run([str(runtime.foundation_python), "-c", code], capture_output=True, text=True, check=False)
    if result.returncode:
        return False, result.stderr.strip() or "pygame probe failed"
    payload = json.loads(result.stdout.strip().splitlines()[-1])
    return payload["count"] > 0, json.dumps(payload, ensure_ascii=False)


def doctor(mujoco_root: Path, godot_binary: Path | None, interactive: bool) -> int:
    _foundation, paths, runtime = preflight(mujoco_root, godot_binary)
    python_ok, python_detail = probe_python(runtime, paths.foundation_python)
    gamepad_ok, gamepad_detail = gamepad_probe(runtime) if interactive else (True, "not required for route profile")
    project = paths.recipe_root / "project"
    checks = (
        ("platform", runtime.system_name == "Darwin", runtime.system_name),
        ("Foundation Python", python_ok, python_detail),
        ("hako-cmd", runtime.hako_cmd.is_file(), str(runtime.hako_cmd)),
        ("Endpoint callback", runtime.endpoint_callback_library.is_file(), str(runtime.endpoint_callback_library)),
        ("Endpoint polling", runtime.endpoint_polling_library.is_file(), str(runtime.endpoint_polling_library)),
        ("Godot", runtime.godot_binary.is_file(), str(runtime.godot_binary)),
        ("TB3 simulator", runtime.tb3_binary.is_file(), str(runtime.tb3_binary)),
        ("Godot project", (project / "project.godot").is_file(), str(project)),
        ("Godot native addon", (project / "addons/hakoniwa/bin/libhakoniwa_godot_native.dylib").is_file(), str(project / "addons/hakoniwa/bin")),
        ("geometry_msgs codec", (project / "addons/hakoniwa/codecs/geometry_msgs_codec.dylib").is_file(), str(project / "addons/hakoniwa/codecs")),
        ("sensor_msgs codec", (project / "addons/hakoniwa/codecs/sensor_msgs_codec.dylib").is_file(), str(project / "addons/hakoniwa/codecs")),
        ("robot sync profile", (project / "config/robot_sync.profile.json").is_file(), str(project / "config/robot_sync.profile.json")),
        ("gamepad", gamepad_ok, gamepad_detail),
    )
    failed = False
    for name, ok, detail in checks:
        print(f"[{'OK' if ok else 'NG'}] {name}: {detail}")
        failed = failed or not ok
    return 1 if failed else 0


def launcher_state(completed: subprocess.CompletedProcess[str]) -> str | None:
    if completed.returncode:
        return None
    try:
        lines = [line for line in completed.stdout.splitlines() if line.strip()]
        return json.loads(lines[-1]).get("state")
    except (IndexError, json.JSONDecodeError, AttributeError):
        return None


def start(mujoco_root: Path, godot_binary: Path | None, profile: str) -> int:
    _foundation, paths, runtime = preflight(mujoco_root, godot_binary)
    launcher = required(paths.recipe_config / f"launcher-{profile}.json", f"{profile} Launcher")
    session = session_file(paths)
    session.parent.mkdir(parents=True, exist_ok=True)
    env = runtime_environment(paths, runtime)
    rc = run(launcher_start_command(runtime.foundation_python, launcher, session), env=env)
    if rc:
        return rc
    status_command = launcher_control_command(runtime.foundation_python, "status", session)
    ready = False
    state = None
    for _ in range(60):
        completed = subprocess.run(status_command, env=env, text=True, capture_output=True, check=False)
        state = launcher_state(completed)
        plant = (paths.recipe_logs / "tb3-mujoco.out").read_text(encoding="utf-8", errors="replace") if (paths.recipe_logs / "tb3-mujoco.out").is_file() else ""
        godot = (paths.recipe_logs / "tb3-godot.out").read_text(encoding="utf-8", errors="replace") if (paths.recipe_logs / "tb3-godot.out").is_file() else ""
        controller_log = paths.recipe_logs / f"tb3-{profile}.out"
        controller = controller_log.read_text(encoding="utf-8", errors="replace") if controller_log.is_file() else ""
        controller_ready = "TB3_GAMEPAD_READY" in controller if profile == "gamepad" else "TB3 route demo start" in controller
        if state == "RUNNING" and "TB3 endpoint started successfully" in plant and "TB3_GODOT_SYNC_READY" in godot and controller_ready:
            ready = True
            break
        if state != "RUNNING":
            break
        time.sleep(0.5)
    if not ready:
        print(f"[NG] TB3 {profile} runtime is not ready; state={state}, logs={paths.recipe_logs}", file=sys.stderr)
        cleanup = launcher_control_command(runtime.foundation_python, "terminate", session)
        cleanup_rc = run(cleanup, env=env)
        if cleanup_rc:
            print(f"[WARN] Launcher cleanup failed with rc={cleanup_rc}; inspect {session}", file=sys.stderr)
        return 1
    print("TurtleBot3 MuJoCo + Godot Demo remains running in the background.")
    print(f"Next: {display('status')} | {display('stop')}")
    print(f"Session: {session}")
    print(f"Logs   : {paths.recipe_logs}")
    return 0


def control(command: str) -> int:
    foundation = load_foundation_module()
    paths = foundation.resolve_workspace(root(), RECIPE_ID)
    python = resolve_foundation_python(paths)
    return run(launcher_control_command(python, command, session_file(paths)))


def apply_deadzone(value: float, deadzone: float) -> float:
    value = max(-1.0, min(1.0, value))
    return 0.0 if abs(value) < deadzone else value


def gamepad_worker(args: argparse.Namespace) -> int:
    sys.path.insert(0, str(absolute(args.mujoco_root / "python")))
    import pygame
    import tb3_route_demo as route
    from rc_utils.rc_utils import RcConfig, StickMonitor

    pygame.init()
    pygame.joystick.init()
    if pygame.joystick.get_count() == 0:
        print("TB3_GAMEPAD_ERROR no controller", file=sys.stderr)
        return 2
    joystick = pygame.joystick.Joystick(0)
    joystick.init()
    monitor = StickMonitor(RcConfig(str(args.rc_config)))
    runtime = route.load_runtime()
    manager = runtime["PduManager"]()
    manager.initialize(config_path=str(args.config_path), comm_service=runtime["ShmCommunicationService"]())
    manager.start_service_nowait()
    if not runtime["hakopy"].init_for_external():
        print("TB3_GAMEPAD_ERROR hakopy.init_for_external failed", file=sys.stderr)
        return 3
    stopping = False

    def request_stop(_signum, _frame):
        nonlocal stopping
        stopping = True

    signal.signal(signal.SIGINT, request_stop)
    if hasattr(signal, "SIGTERM"):
        signal.signal(signal.SIGTERM, request_stop)

    def neutral() -> None:
        command = route.build_gamepad(0.0, 0.0)
        for _ in range(5):
            route.send_command(manager, args.robot, args.pdu, command)
            time.sleep(0.02)

    print(f"TB3_GAMEPAD_READY name={joystick.get_name()} axes={joystick.get_numaxes()} buttons={joystick.get_numbuttons()} stop_button={args.stop_button}")
    neutral()
    try:
        while not stopping:
            pygame.event.pump()
            for event in pygame.event.get():
                if event.type == pygame.JOYDEVICEREMOVED:
                    print("TB3_GAMEPAD_LOST")
                    stopping = True
            if not joystick.get_init():
                print("TB3_GAMEPAD_LOST")
                break
            values = [0.0] * 4
            for physical_axis in range(min(joystick.get_numaxes(), 6)):
                op_index = monitor.rc_config.get_op_index(physical_axis)
                if op_index is not None and op_index < len(values):
                    values[op_index] = apply_deadzone(monitor.stick_value(physical_axis, joystick.get_axis(physical_axis)), args.deadzone)
            if 0 <= args.stop_button < joystick.get_numbuttons() and joystick.get_button(args.stop_button):
                print("TB3_GAMEPAD_EMERGENCY_STOP")
                break
            command = route.build_gamepad(-values[3], -values[0])
            route.send_command(manager, args.robot, args.pdu, command)
            time.sleep(1.0 / args.rate_hz)
    finally:
        try:
            neutral()
            print("TB3_GAMEPAD_NEUTRAL_SENT")
        finally:
            pygame.joystick.quit()
            pygame.quit()
    return 0


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description="Configure and operate the TurtleBot3 MuJoCo/Godot exhibition Recipe")
    result.add_argument("command", choices=["configure", "build", "doctor", "start", "status", "open-godot", "stop", "gamepad-worker"])
    result.add_argument("--profile", choices=PROFILES, default="gamepad")
    result.add_argument("--mujoco-root", type=Path, default=default_source("hakoniwa-mujoco-robots"))
    result.add_argument("--mbody-root", type=Path, default=default_source("hakoniwa-mbody-registry"))
    result.add_argument("--godot-root", type=Path, default=default_source("hakoniwa-godot"))
    result.add_argument("--godot-bin", type=Path, default=None)
    result.add_argument("--config-path", type=Path, default=None)
    result.add_argument("--rc-config", type=Path, default=None)
    result.add_argument("--robot", default="TB3")
    result.add_argument("--pdu", default="hako_cmd_game")
    result.add_argument("--deadzone", type=float, default=0.08)
    result.add_argument("--rate-hz", type=float, default=50.0)
    result.add_argument("--stop-button", type=int, default=6)
    return result


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        args.mujoco_root = absolute(args.mujoco_root)
        args.mbody_root = absolute(args.mbody_root)
        args.godot_root = absolute(args.godot_root)
        if args.command == "gamepad-worker":
            args.config_path = absolute(args.config_path or args.mujoco_root / "config/tb3-pdudef-compact.json")
            args.rc_config = absolute(args.rc_config or args.mujoco_root / "python/rc_config/ps4-control.json")
            return gamepad_worker(args)
        if args.command == "configure":
            return configure(args.mujoco_root, args.mbody_root, args.godot_root, args.godot_bin)
        if args.command == "build":
            return build(args.mujoco_root, args.godot_bin)
        if args.command == "doctor":
            return doctor(args.mujoco_root, args.godot_bin, args.profile == "gamepad")
        if args.command == "start":
            return start(args.mujoco_root, args.godot_bin, args.profile)
        if args.command == "open-godot":
            print("Godot is Launcher-managed and opens with start; checking the active session.")
            return control("status")
        if args.command == "status":
            return control("status")
        return control("terminate")
    except (RecipeError, OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
