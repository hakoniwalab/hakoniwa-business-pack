#!/usr/bin/env python3
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import platform
import shutil
import socket
import subprocess
import sys
import time
import webbrowser
from dataclasses import dataclass
from pathlib import Path

TOOLS_DIR = Path(__file__).absolute().parents[1]
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

from recipe_portal import PortalCommand, PortalEnvironment, PortalLink, write_recipe_portal


RECIPE_ID = "shadow-hand-hakoniwa-to-foxglove"
FOXGLOVE_URL = "https://app.foxglove.dev"
FOXGLOVE_WS_URL = "ws://127.0.0.1:8766"
URDF_URL = "http://127.0.0.1:8767/shadow_hand_right.urdf"


class RecipeError(RuntimeError):
    pass


@dataclass(frozen=True)
class RuntimePaths:
    system_name: str
    foundation_python: Path
    hako_cmd: Path
    endpoint_callback_library: Path
    web_bridge: Path
    hand_asset: Path
    cdr_publisher: Path
    converter: Path
    sender: Path
    cors_server: Path


def root() -> Path:
    return Path(__file__).absolute().parents[2]


def default_source(name: str) -> Path:
    return root().parent / name


def recipe_file() -> Path:
    return root() / "recipes" / "examples" / f"{RECIPE_ID}.yaml"


def load_foundation_module():
    script = TOOLS_DIR / "foundation.py"
    spec = importlib.util.spec_from_file_location(
        "business_pack_foundation_shadow_hand", script
    )
    if spec is None or spec.loader is None:
        raise RecipeError(f"cannot load Foundation helper: {script}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _absolute(path: Path) -> Path:
    return Path(os.path.abspath(path.expanduser()))


def _required(path: Path, label: str, *, executable: bool = False) -> Path:
    candidate = _absolute(path)
    if not candidate.exists():
        raise RecipeError(f"{label} not found: {candidate}")
    if executable and not os.access(candidate, os.X_OK):
        raise RecipeError(f"{label} is not executable: {candidate}")
    return candidate


def _port_available(port: int) -> bool | None:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind(("127.0.0.1", port))
        except PermissionError:
            return None
        except OSError:
            return False
    return True


def _port_listening(port: int) -> bool | None:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=0.25):
            return True
    except PermissionError:
        return None
    except OSError:
        return False


def resolve_foundation_python(paths) -> Path:
    candidates = (
        paths.foundation_python / "Scripts" / "python.exe",
        paths.foundation_python / "bin" / "python3",
        paths.foundation_python / "bin" / "python",
    )
    for candidate in candidates:
        if candidate.is_file():
            return _absolute(candidate)
    raise RecipeError(f"Foundation Python not found under {paths.foundation_python}")


def _callback_library(prefix: Path, system_name: str) -> Path:
    if system_name == "Darwin":
        name = "libhakoniwa_pdu_endpoint_core_callback.dylib"
    elif system_name == "Windows":
        name = "hakoniwa_pdu_endpoint_core_callback.dll"
    else:
        name = "libhakoniwa_pdu_endpoint_core_callback.so"
    candidates = (prefix / "lib" / name, prefix / "bin" / name)
    for candidate in candidates:
        if candidate.is_file():
            return _absolute(candidate)
    return _absolute(candidates[0])


def resolve_runtime(paths, mujoco_root: Path, foxglove_root: Path) -> RuntimePaths:
    system_name = platform.system()
    prefix = paths.install_prefix
    return RuntimePaths(
        system_name=system_name,
        foundation_python=resolve_foundation_python(paths),
        hako_cmd=_absolute(prefix / "bin" / ("hako-cmd.exe" if system_name == "Windows" else "hako-cmd")),
        endpoint_callback_library=_callback_library(prefix, system_name),
        web_bridge=_absolute(prefix / "bin" / ("hakoniwa-pdu-web-bridge.exe" if system_name == "Windows" else "hakoniwa-pdu-web-bridge")),
        hand_asset=_absolute(
            mujoco_root
            / "src/cmake-build/examples/actuators/shadow_hand"
            / ("shadow-hand-hakoniwa-asset.exe" if system_name == "Windows" else "shadow-hand-hakoniwa-asset")
        ),
        cdr_publisher=_absolute(
            foxglove_root / "build" / ("cdr_stdin_publisher.exe" if system_name == "Windows" else "cdr_stdin_publisher")
        ),
        converter=_absolute(
            foxglove_root
            / "examples/shadow-hand-jointstate-to-foxglove"
            / "shadow_hand_jointstate_to_foxglove.py"
        ),
        sender=_absolute(
            mujoco_root / "examples/actuators/shadow_hand/send_shadow_hand_targets.py"
        ),
        cors_server=_absolute(foxglove_root / "tools/serve_static_cors.py"),
    )


def preflight(mujoco_root: Path, foxglove_root: Path):
    foundation = load_foundation_module()
    paths = foundation.resolve_workspace(root(), RECIPE_ID)
    inspection = foundation.inspect_foundation(recipe_file(), paths.install_prefix)
    if inspection["status"] != "SATISFIED":
        foundation.print_inspection(inspection, False)
        raise RecipeError(
            "Foundation is not reusable; run tools/foundation.py plan/build first"
        )
    runtime = resolve_runtime(paths, mujoco_root, foxglove_root)
    return foundation, paths, runtime


def _copy_runtime_inputs(paths, foxglove_root: Path) -> None:
    mappings = (
        (foxglove_root / "config/shadow_hand", paths.recipe_config / "shadow_hand"),
        (
            foxglove_root / "config/shadow_hand_bridge",
            paths.recipe_config / "shadow_hand_bridge",
        ),
        (
            foxglove_root / "work/urdf/shadow_hand",
            paths.recipe_root / "assets/urdf/shadow_hand",
        ),
    )
    for source, destination in mappings:
        _required(source, f"Recipe input {source.name}")
        shutil.copytree(
            source,
            destination,
            dirs_exist_ok=True,
            ignore=shutil.ignore_patterns(".DS_Store"),
        )

    schema_source = _required(
        foxglove_root
        / "work/schemas/ros2_jazzy/sensor_msgs/msg/JointState.bundle.msg",
        "JointState schema bundle",
    )
    schema_destination = (
        paths.recipe_root
        / "assets/schemas/ros2_jazzy/sensor_msgs/msg/JointState.bundle.msg"
    )
    schema_destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(schema_source, schema_destination)

    comm_path = paths.recipe_config / "shadow_hand/comm_foxglove_jointstate.json"
    comm = json.loads(comm_path.read_text(encoding="utf-8"))
    for channel in comm.get("channels", []):
        channel["schema"]["file"] = (
            "../../assets/schemas/ros2_jazzy/sensor_msgs/msg/JointState.bundle.msg"
        )
    _write_json(comm_path, comm)


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    temporary.replace(path)


def write_launcher(paths, mujoco_root: Path, runtime: RuntimePaths) -> Path:
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
                    "HAKO_PDU_ENDPOINT_SHARED_LIB": str(
                        runtime.endpoint_callback_library
                    ),
                    "HAKO_PROFILE_SERVICE_CLIENT": "0",
                },
                "prepend": {
                    "lib_path": [
                        str(paths.install_prefix / "lib"),
                        str(mujoco_root / "vendor/mujoco/lib"),
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
                "name": "shadow_hand",
                "activation_timing": "before_start",
                "command": str(runtime.hand_asset),
                "args": [],
                "cwd": str(mujoco_root),
                "delay_sec": 2,
            },
            {
                "name": "shadow_hand_urdf_server",
                "activation_timing": "before_start",
                "command": str(runtime.foundation_python),
                "args": [
                    str(runtime.cors_server),
                    "--directory",
                    str(paths.recipe_root / "assets/urdf/shadow_hand"),
                    "--port",
                    "8767",
                ],
                "depends_on": ["shadow_hand"],
            },
            {
                "name": "foxglove_jointstate_publisher",
                "activation_timing": "before_start",
                "command": str(runtime.foundation_python),
                "args": [
                    str(runtime.converter),
                    "--endpoint-config",
                    str(
                        paths.recipe_config
                        / "shadow_hand_bridge/endpoint/shadow-hand-tcp-server.json"
                    ),
                    "--foxglove-endpoint-config",
                    str(
                        paths.recipe_config
                        / "shadow_hand/endpoint_foxglove_jointstate.json"
                    ),
                    "--publisher",
                    str(runtime.cdr_publisher),
                    "--samples",
                    "0",
                ],
                "depends_on": ["shadow_hand_urdf_server"],
            },
            {
                "name": "shadow_hand_bridge",
                "activation_timing": "before_start",
                "command": str(runtime.web_bridge),
                "args": [
                    "--config-root",
                    str(paths.recipe_config / "shadow_hand_bridge"),
                    "--asset-name",
                    "ShadowHandBridge",
                    "--node-name",
                    "shadow_hand_foxglove_bridge_node1",
                    "--delta-time-step-usec",
                    "20000",
                ],
                "depends_on": ["shadow_hand", "foxglove_jointstate_publisher"],
            },
            {
                "name": "shadow_hand_sender",
                "activation_timing": "before_start",
                "command": str(runtime.foundation_python),
                "args": [
                    str(runtime.sender),
                    "--duration-sec",
                    "120",
                    "--frequency-hz",
                    "0.25",
                ],
                "cwd": str(mujoco_root),
                "depends_on": ["shadow_hand_bridge"],
            },
        ],
    }
    output = paths.recipe_config / "launcher.json"
    _write_json(output, launcher)
    return output


def session_file(paths) -> Path:
    return paths.recipe_root / "runtime/launcher-session.json"


def launcher_start_command(python: Path, launcher: Path, session: Path) -> list[str]:
    return [
        str(python),
        "-m",
        "hakoniwa_pdu.apps.launcher.hako_launcher",
        str(launcher),
        "--background",
        str(session),
    ]


def launcher_control_command(python: Path, command: str, session: Path) -> list[str]:
    if command not in {"status", "terminate"}:
        raise RecipeError(f"unsupported Launcher control command: {command}")
    return [
        str(python),
        "-m",
        "hakoniwa_pdu.apps.launcher.hako_launcher_ctl",
        command,
        str(session),
    ]


def runtime_environment(paths, runtime: RuntimePaths) -> dict[str, str]:
    env = os.environ.copy()
    env["HAKO_CONFIG_PATH"] = str(paths.foundation_config / "cpp_core_config.json")
    env["HAKO_PDU_ENDPOINT_SHARED_LIB"] = str(runtime.endpoint_callback_library)
    env["PATH"] = os.pathsep.join(
        [str(runtime.foundation_python.parent), str(paths.install_prefix / "bin"), env.get("PATH", "")]
    )
    key = "PATH" if runtime.system_name == "Windows" else (
        "DYLD_LIBRARY_PATH" if runtime.system_name == "Darwin" else "LD_LIBRARY_PATH"
    )
    env[key] = os.pathsep.join([str(paths.install_prefix / "lib"), env.get(key, "")])
    return env


def _run(command: list[str], env: dict[str, str] | None = None) -> int:
    print("+", subprocess.list2cmdline(command))
    return subprocess.run(command, env=env, check=False).returncode


def _display(action: str) -> str:
    return f"python tools/recipe/shadow_hand_foxglove.py {action}"


def write_portal(paths, runtime: RuntimePaths, launcher: Path) -> Path:
    return write_recipe_portal(
        paths.recipe_root / "index.html",
        recipe_id=RECIPE_ID,
        title="Hakoniwa Shadow Hand + Foxglove",
        summary=(
            "MuJoCoのShadow Hand関節状態を、ROS 2 runtimeなしでCDRへ変換し、"
            "Foxglove 3Dへ配信するRecipeです。"
        ),
        topology=(
            "MuJoCo Shadow Hand",
            "Hakoniwa SHM",
            "Bridge Core",
            "JointState → CDR",
            "Foxglove WebSocket",
            "Foxglove 3D",
        ),
        commands=(
            PortalCommand("Configure", _display("configure"), "Recipe workspaceとLauncherを生成します。"),
            PortalCommand("Doctor", _display("doctor"), "Foundation、ローカル成果物、schema、URDF、ポートを検査します。"),
            PortalCommand("Start", _display("start"), "所有されたbackground Launcher sessionを開始します。"),
            PortalCommand("Open Foxglove", _display("open-viewer"), "Foxglove Appを開きます。"),
            PortalCommand("Status", _display("status"), "Launcher sessionの状態を確認します。"),
            PortalCommand("Stop", _display("stop"), "Launcherの通常終了経路で全管理プロセスを終了します。"),
        ),
        links=(
            PortalLink("Foxglove App", FOXGLOVE_URL, f"接続先: {FOXGLOVE_WS_URL}"),
            PortalLink("Shadow Hand URDF", URDF_URL, "Foxglove 3Dで読み込むURDF（実行中のみ）"),
            PortalLink("Launcher JSON", "config/launcher.json", "生成された実行トポロジ"),
            PortalLink("Runtime session", "runtime/", "Launcher session state"),
            PortalLink("Logs", "logs/", "アセットごとのログ"),
        ),
        environment=(
            PortalEnvironment("Platform", runtime.system_name),
            PortalEnvironment("Recipe workspace", str(paths.recipe_root)),
            PortalEnvironment("Foundation install", str(paths.install_prefix)),
            PortalEnvironment("Foundation Python", str(runtime.foundation_python)),
            PortalEnvironment("Launcher", str(launcher)),
            PortalEnvironment("Session", str(session_file(paths))),
            PortalEnvironment("Foxglove WebSocket", FOXGLOVE_WS_URL),
            PortalEnvironment("URDF URL", URDF_URL),
        ),
        agency_notes=(
            "FoxgloveでWebSocket接続、URDF読込、Joint states modeの選択を行う操作は人間が担当します。",
            "JointState topicは /hakoniwa/ShadowHandAsset/joint_states です。",
            "Shadow Robot DAEでは Ignore COLLADA <up_axis> を有効にしてください。",
            "Stop後にポート8766/8767が閉じたことを確認してからWorkspaceを終了します。",
        ),
    )


def configure(mujoco_root: Path, foxglove_root: Path) -> int:
    foundation, paths, runtime = preflight(mujoco_root, foxglove_root)
    foundation.prepare_workspace(paths)
    (paths.recipe_root / "runtime").mkdir(parents=True, exist_ok=True)
    _copy_runtime_inputs(paths, foxglove_root)
    launcher = write_launcher(paths, mujoco_root, runtime)
    portal = write_portal(paths, runtime, launcher)
    print(f"Recipe workspace : {paths.recipe_root}")
    print(f"Recipe portal    : {portal}")
    print(f"Launcher         : {launcher}")
    print(f"Session          : {session_file(paths)}")
    print(f"Foxglove         : {FOXGLOVE_URL} ({FOXGLOVE_WS_URL})")
    print(f"URDF             : {URDF_URL}")
    return 0


def _probe_python(python: Path, venv_root: Path) -> tuple[bool, str]:
    code = (
        "import json,pathlib,sys; import hakopy,hakoniwa_pdu,hakoniwa_pdu_endpoint; "
        "import hakoniwa_pdu.apps.launcher.hako_launcher; "
        "print(json.dumps({'prefix':str(pathlib.Path(sys.prefix).absolute())}))"
    )
    result = subprocess.run([str(python), "-c", code], capture_output=True, text=True, check=False)
    if result.returncode != 0:
        return False, result.stderr.strip() or "Foundation imports failed"
    try:
        prefix = _absolute(Path(json.loads(result.stdout.splitlines()[-1])["prefix"]))
        prefix.relative_to(_absolute(venv_root))
    except (KeyError, ValueError, json.JSONDecodeError, IndexError) as exc:
        return False, f"Python is not running inside Foundation venv: {exc}"
    return True, f"venv={prefix}"


def doctor(mujoco_root: Path, foxglove_root: Path) -> int:
    _foundation, paths, runtime = preflight(mujoco_root, foxglove_root)
    python_ok, python_detail = _probe_python(runtime.foundation_python, paths.foundation_python)
    checks: list[tuple[str, bool | None, str]] = [
        ("platform", runtime.system_name == "Darwin", f"{runtime.system_name} (full Recipe currently verified on macOS)"),
        ("Foundation Python", python_ok, python_detail),
        ("hako-cmd", runtime.hako_cmd.is_file(), str(runtime.hako_cmd)),
        ("Endpoint core_callback", runtime.endpoint_callback_library.is_file(), str(runtime.endpoint_callback_library)),
        ("Foundation WebBridge", runtime.web_bridge.is_file(), str(runtime.web_bridge)),
        ("Shadow Hand asset", runtime.hand_asset.is_file(), str(runtime.hand_asset)),
        ("CDR publisher", runtime.cdr_publisher.is_file(), str(runtime.cdr_publisher)),
        ("JointState converter", runtime.converter.is_file(), str(runtime.converter)),
        ("Hand sender", runtime.sender.is_file(), str(runtime.sender)),
        ("CORS server", runtime.cors_server.is_file(), str(runtime.cors_server)),
        ("JointState schema", (paths.recipe_root / "assets/schemas/ros2_jazzy/sensor_msgs/msg/JointState.bundle.msg").is_file(), "workspace asset"),
        ("Shadow Hand URDF", (paths.recipe_root / "assets/urdf/shadow_hand/shadow_hand_right.urdf").is_file(), "workspace asset"),
        ("generated Launcher", (paths.recipe_config / "launcher.json").is_file(), str(paths.recipe_config / "launcher.json")),
        ("Recipe portal", (paths.recipe_root / "index.html").is_file(), str(paths.recipe_root / "index.html")),
        ("port 8766", _port_available(8766), "available"),
        ("port 8767", _port_available(8767), "available"),
    ]
    failed = False
    for name, ok, detail in checks:
        if ok is None:
            print(f"[WARN] {name}: unavailable in this execution environment")
            continue
        print(f"[{'OK' if ok else 'NG'}] {name}: {detail}")
        failed = failed or not ok
    return 1 if failed else 0


def start(mujoco_root: Path, foxglove_root: Path) -> int:
    _foundation, paths, runtime = preflight(mujoco_root, foxglove_root)
    launcher = _required(paths.recipe_config / "launcher.json", "Generated Launcher")
    session = session_file(paths)
    session.parent.mkdir(parents=True, exist_ok=True)
    env = runtime_environment(paths, runtime)
    rc = _run(launcher_start_command(runtime.foundation_python, launcher, session), env)
    if rc != 0:
        return rc
    status_command = launcher_control_command(runtime.foundation_python, "status", session)
    state = None
    for _ in range(20):
        completed = subprocess.run(
            status_command,
            env=env,
            text=True,
            capture_output=True,
            timeout=10,
            check=False,
        )
        if completed.returncode != 0:
            if completed.stderr:
                print(completed.stderr, end="", file=sys.stderr)
            return completed.returncode
        try:
            lines = [line for line in completed.stdout.splitlines() if line.strip()]
            state = json.loads(lines[-1]).get("state")
        except (IndexError, json.JSONDecodeError, AttributeError):
            state = None
        listeners = (_port_listening(8766), _port_listening(8767))
        if state == "RUNNING" and all(value is True for value in listeners):
            break
        if state != "RUNNING":
            break
        if any(value is None for value in listeners):
            print(
                "[WARN] listener readiness cannot be probed in this environment; "
                "Launcher session is RUNNING"
            )
            break
        time.sleep(0.5)
    else:
        listeners = (_port_listening(8766), _port_listening(8767))

    if state != "RUNNING":
        print(
            f"[NG] Launcher state is {state!r}; inspect {session}.log",
            file=sys.stderr,
        )
        return 1
    if any(value is False for value in listeners):
        print(
            "[NG] Launcher is RUNNING but Foxglove/URDF listeners are not ready; "
            f"inspect {paths.recipe_logs}",
            file=sys.stderr,
        )
        return 1

    print("Demo remains running in the background.")
    print(f"Ready: {FOXGLOVE_WS_URL} and {URDF_URL}")
    print(f"Next: {_display('open-viewer')} | {_display('status')} | {_display('stop')}")
    print(f"Session: {session}")
    print(f"Logs   : {paths.recipe_logs}")
    return 0


def control(command: str) -> int:
    foundation = load_foundation_module()
    paths = foundation.resolve_workspace(root(), RECIPE_ID)
    python = resolve_foundation_python(paths)
    return _run(launcher_control_command(python, command, session_file(paths)))


def stop() -> int:
    foundation = load_foundation_module()
    paths = foundation.resolve_workspace(root(), RECIPE_ID)
    python = resolve_foundation_python(paths)
    session = session_file(paths)
    rc = _run(launcher_control_command(python, "terminate", session))
    if rc != 0:
        return rc

    for _ in range(20):
        listeners = (_port_listening(8766), _port_listening(8767))
        if all(value is False for value in listeners):
            print("Launcher session is TERMINATED; ports 8766 and 8767 are released.")
            return 0
        if any(value is None for value in listeners):
            print(
                "[WARN] Launcher terminated, but listener cleanup cannot be "
                "probed in this environment"
            )
            return 0
        time.sleep(0.25)
    print(
        "[NG] Launcher terminated, but port 8766 or 8767 is still listening",
        file=sys.stderr,
    )
    return 1


def open_viewer() -> int:
    print(f"Opening {FOXGLOVE_URL}")
    print(f"Connect: {FOXGLOVE_WS_URL}")
    print(f"URDF   : {URDF_URL}")
    return 0 if webbrowser.open(FOXGLOVE_URL) else 1


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description="Configure and operate the Shadow Hand Foxglove Recipe")
    result.add_argument("command", choices=["configure", "doctor", "start", "status", "stop", "open-viewer"])
    result.add_argument("--mujoco-root", type=Path, default=default_source("hakoniwa-mujoco-robots"))
    result.add_argument("--foxglove-root", type=Path, default=default_source("hakoniwa-pdu-foxglove"))
    return result


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        mujoco_root = _absolute(args.mujoco_root)
        foxglove_root = _absolute(args.foxglove_root)
        if args.command == "configure":
            return configure(mujoco_root, foxglove_root)
        if args.command == "doctor":
            return doctor(mujoco_root, foxglove_root)
        if args.command == "start":
            return start(mujoco_root, foxglove_root)
        if args.command == "status":
            return control("status")
        if args.command == "stop":
            return stop()
        return open_viewer()
    except RecipeError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
