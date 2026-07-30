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

TOOLS_DIR = Path(__file__).absolute().parent
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

from platforms.drone_gamepad import current_adapter

RECIPE_ID = "drone-single-mujoco-threejs-gamepad"
VIEWER_URL = (
    "http://127.0.0.1:8000/index.html"
    "?viewerConfigPath=/config/viewer-config-fleets.json"
    "&wsUri=ws://127.0.0.1:8765&wireVersion=v2"
)


class RecipeError(RuntimeError):
    pass


@dataclass(frozen=True)
class RuntimePaths:
    system_name: str
    drone_service: Path
    visual_state_publisher: Path
    foundation_python: Path
    hako_cmd: Path
    web_bridge: Path


def load_foundation_module():
    script = Path(__file__).with_name("foundation.py")
    spec = importlib.util.spec_from_file_location(
        "business_pack_foundation_gamepad_runtime", script
    )
    if spec is None or spec.loader is None:
        raise RecipeError(f"cannot load Foundation helper: {script}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def root() -> Path:
    return Path(__file__).absolute().parents[1]


def default_source(name: str) -> Path:
    return root().parent / name


def recipe_file() -> Path:
    return root() / "recipes" / "examples" / f"{RECIPE_ID}.yaml"


def _absolute_without_resolving(path: Path) -> Path:
    return Path(os.path.abspath(path.expanduser()))


def _required(path: Path, label: str, *, preserve_symlink: bool = False) -> Path:
    candidate = _absolute_without_resolving(path)
    if not candidate.exists():
        raise RecipeError(f"{label} not found: {candidate}")
    return candidate if preserve_symlink else candidate.resolve()


def _resolve_candidate(
    candidates: tuple[Path, ...],
    label: str,
    override: Path | None = None,
    *,
    preserve_symlink: bool = False,
) -> Path:
    if override is not None:
        return _required(override, label, preserve_symlink=preserve_symlink)
    for candidate in candidates:
        if candidate.is_file():
            return _required(candidate, label, preserve_symlink=preserve_symlink)
    rendered = ", ".join(str(candidate) for candidate in candidates)
    raise RecipeError(f"{label} not found; checked: {rendered}")


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


def resolve_foundation_python(paths) -> Path:
    adapter = current_adapter()
    python = _resolve_candidate(
        adapter.foundation_python_candidates(paths.foundation_python),
        "Foundation Python",
        preserve_symlink=True,
    )
    venv_root = _absolute_without_resolving(paths.foundation_python)
    try:
        python.relative_to(venv_root)
    except ValueError as exc:
        raise RecipeError(
            f"Foundation Python must stay under the shared venv: {venv_root}"
        ) from exc
    return python


def resolve_runtime(
    paths,
    drone_root: Path,
    *,
    drone_service_bin: Path | None = None,
    visual_state_publisher_bin: Path | None = None,
    hako_cmd_bin: Path | None = None,
    web_bridge_bin: Path | None = None,
) -> RuntimePaths:
    system_name = platform.system()
    adapter = current_adapter(system_name)
    return RuntimePaths(
        system_name=system_name,
        drone_service=_resolve_candidate(
            adapter.drone_service_candidates(drone_root, system_name),
            "Drone service",
            drone_service_bin,
        ),
        visual_state_publisher=_resolve_candidate(
            adapter.visual_state_publisher_candidates(drone_root, system_name),
            "Visual-state publisher",
            visual_state_publisher_bin,
        ),
        foundation_python=resolve_foundation_python(paths),
        hako_cmd=_resolve_candidate(
            adapter.hako_cmd_candidates(paths.install_prefix),
            "hako-cmd",
            hako_cmd_bin,
        ),
        web_bridge=_resolve_candidate(
            adapter.web_bridge_candidates(paths.install_prefix),
            "Web bridge",
            web_bridge_bin,
        ),
    )


def preflight(drone_root: Path, viewer_root: Path, overrides: dict[str, Path | None]):
    foundation = load_foundation_module()
    paths = foundation.resolve_workspace(root(), RECIPE_ID)
    result = foundation.inspect_foundation(recipe_file(), paths.install_prefix)
    if result["status"] != "SATISFIED":
        foundation.print_inspection(result, False)
        raise RecipeError(
            "Foundation is not reusable; run tools/foundation.py plan/build first"
        )

    required = (
        (drone_root / "config", "Drone config"),
        (drone_root / "drone_api" / "rc" / "rc-custom.py", "RC application"),
        (
            drone_root / "drone_api" / "rc" / "rc_config" / "ps4-control.json",
            "PS4/PS5 controller mapping",
        ),
        (viewer_root / "index.html", "Three.js viewer"),
        (
            paths.foundation_config / "cpp_core_config.json",
            "Foundation Core config",
        ),
    )
    for path, label in required:
        _required(path, label)
    runtime = resolve_runtime(paths, drone_root, **overrides)
    return foundation, paths, runtime


def copy_recipe_config(drone_root: Path, recipe_config: Path) -> None:
    mappings = (
        ("drone", recipe_config / "drone"),
        ("pdudef", recipe_config / "pdudef"),
        ("controller", recipe_config / "controller"),
        (
            "assets/visual_state_publisher",
            recipe_config / "assets" / "visual_state_publisher",
        ),
        (
            "assets/web_bridge_fleets",
            recipe_config / "assets" / "web_bridge_fleets",
        ),
    )
    source_config = drone_root / "config"
    for relative, destination in mappings:
        source = _required(source_config / relative, f"Recipe config {relative}")
        shutil.copytree(
            source,
            destination,
            dirs_exist_ok=True,
            ignore=shutil.ignore_patterns(".DS_Store", "logs"),
        )


def write_launcher(paths, drone_root: Path, viewer_root: Path, runtime: RuntimePaths) -> Path:
    rc_root = drone_root / "drone_api" / "rc"
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
                    "config/drone/fleets/api-current.json",
                    "config/pdudef/drone-pdudef-current.json",
                    "--mujoco-viewer",
                ],
                "cwd": str(paths.recipe_root),
                "delay_sec": 2,
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
                        / "visual_state_publisher.runtime.json"
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
                    str(paths.recipe_config / "pdudef" / "drone-pdudef-current.json"),
                    str(rc_root / "rc_config" / "ps4-control.json"),
                ],
                "cwd": str(rc_root),
                "depends_on": ["drone-service-1"],
            },
            {
                "name": "threejs-viewer-webserver",
                "activation_timing": "after_start",
                "command": str(runtime.foundation_python),
                "args": ["-m", "http.server", "8000"],
                "cwd": str(viewer_root),
                "depends_on": ["web-bridge-fleets"],
            },
        ],
    }
    output = paths.recipe_config / "launcher.json"
    output.write_text(
        json.dumps(launcher, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return output


def session_file(paths) -> Path:
    return paths.recipe_root / "runtime" / "launcher-session.json"


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


def reset_commands(hako_cmd: Path) -> list[list[str]]:
    return [
        [str(hako_cmd), "stop"],
        [str(hako_cmd), "reset"],
        [str(hako_cmd), "start"],
    ]


def runtime_environment(paths, runtime: RuntimePaths) -> dict[str, str]:
    env = os.environ.copy()
    env["HAKO_CONFIG_PATH"] = str(paths.foundation_config / "cpp_core_config.json")
    path_entries = [
        str(runtime.foundation_python.parent),
        str(paths.install_prefix / "bin"),
    ]
    env["PATH"] = os.pathsep.join(path_entries + [env.get("PATH", "")])
    library_entries = [str(paths.install_prefix / "lib")]
    if runtime.system_name == "Darwin":
        key = "DYLD_LIBRARY_PATH"
    elif runtime.system_name == "Windows":
        key = "PATH"
    else:
        key = "LD_LIBRARY_PATH"
    env[key] = os.pathsep.join(library_entries + [env.get(key, "")])
    return env


def _run(command: list[str], env: dict[str, str] | None = None) -> int:
    print("+", subprocess.list2cmdline(command))
    return subprocess.run(command, env=env, check=False).returncode


def configure(drone_root: Path, viewer_root: Path, overrides: dict[str, Path | None]) -> int:
    foundation, paths, runtime = preflight(drone_root, viewer_root, overrides)
    foundation.prepare_workspace(paths)
    (paths.recipe_root / "runtime").mkdir(parents=True, exist_ok=True)
    copy_recipe_config(drone_root, paths.recipe_config)
    launcher = write_launcher(paths, drone_root, viewer_root, runtime)
    print(f"Recipe workspace : {paths.recipe_root}")
    print(f"Launcher         : {launcher}")
    print(f"Session          : {session_file(paths)}")
    print(f"Foundation Python: {runtime.foundation_python}")
    print(f"Viewer           : {VIEWER_URL}")
    print("Operator command : python tools/drone_gamepad_exhibition.py start")
    return 0


def _probe_python_runtime(python: Path, venv_root: Path) -> tuple[bool, str]:
    code = (
        "import json, pathlib, sys; "
        "import hakopy; "
        "import hakoniwa_pdu; "
        "import hakoniwa_pdu.apps.launcher.hako_launcher; "
        "print(json.dumps({'prefix': str(pathlib.Path(sys.prefix).absolute()), "
        "'executable': str(pathlib.Path(sys.executable).absolute())}))"
    )
    result = subprocess.run(
        [str(python), "-c", code],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        return False, result.stderr.strip() or "Foundation imports failed"
    try:
        payload = json.loads(result.stdout.strip().splitlines()[-1])
        prefix = _absolute_without_resolving(Path(payload["prefix"]))
        prefix.relative_to(_absolute_without_resolving(venv_root))
    except (KeyError, ValueError, json.JSONDecodeError, IndexError) as exc:
        return False, f"Python is not running inside the Foundation venv: {exc}"
    return True, f"venv={prefix}"


def _probe_controller(python: Path) -> tuple[bool, str]:
    code = (
        "import pygame; "
        "pygame.init(); pygame.joystick.init(); "
        "print(pygame.joystick.get_count()); "
        "pygame.joystick.quit(); pygame.quit()"
    )
    result = subprocess.run(
        [str(python), "-c", code],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        return False, result.stderr.strip() or "pygame import/probe failed"
    try:
        count = int(result.stdout.strip().splitlines()[-1])
    except (ValueError, IndexError):
        return False, f"unexpected pygame output: {result.stdout.strip()}"
    return count > 0, f"joysticks={count}"


def doctor(drone_root: Path, viewer_root: Path, overrides: dict[str, Path | None]) -> int:
    _foundation, paths, runtime = preflight(drone_root, viewer_root, overrides)
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
        ("port 8000", _port_available(8000), "available"),
        ("port 8765", _port_available(8765), "available"),
    ]
    python_ok, python_detail = _probe_python_runtime(
        runtime.foundation_python, paths.foundation_python
    )
    checks.append(("Foundation Python imports", python_ok, python_detail))
    controller_ok, controller_detail = _probe_controller(runtime.foundation_python)
    checks.append(("gamepad", controller_ok, controller_detail))
    launcher = paths.recipe_config / "launcher.json"
    checks.append(("generated Launcher", launcher.is_file(), str(launcher)))

    failed = False
    for name, ok, detail in checks:
        if ok is None:
            print(f"[WARN] {name}: unavailable in this execution environment")
            continue
        print(f"[{'OK' if ok else 'NG'}] {name}: {detail}")
        failed = failed or not ok
    return 1 if failed else 0


def start(drone_root: Path, viewer_root: Path, overrides: dict[str, Path | None]) -> int:
    _foundation, paths, runtime = preflight(drone_root, viewer_root, overrides)
    launcher = _required(paths.recipe_config / "launcher.json", "Generated Launcher")
    session = session_file(paths)
    session.parent.mkdir(parents=True, exist_ok=True)
    return _run(
        launcher_start_command(runtime.foundation_python, launcher, session),
        runtime_environment(paths, runtime),
    )


def status() -> int:
    foundation = load_foundation_module()
    paths = foundation.resolve_workspace(root(), RECIPE_ID)
    python = resolve_foundation_python(paths)
    return _run(launcher_control_command(python, "status", session_file(paths)))


def stop() -> int:
    foundation = load_foundation_module()
    paths = foundation.resolve_workspace(root(), RECIPE_ID)
    python = resolve_foundation_python(paths)
    return _run(launcher_control_command(python, "terminate", session_file(paths)))


def reset(drone_root: Path, viewer_root: Path, overrides: dict[str, Path | None]) -> int:
    _foundation, paths, runtime = preflight(drone_root, viewer_root, overrides)
    env = runtime_environment(paths, runtime)
    for command in reset_commands(runtime.hako_cmd):
        rc = _run(command, env)
        if rc != 0:
            return rc
        time.sleep(0.5)
    return 0


def open_viewer() -> int:
    print(f"Opening {VIEWER_URL}")
    return 0 if webbrowser.open(VIEWER_URL) else 1


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(
        description="Configure and operate the Hakoniwa Drone gamepad exhibition Recipe"
    )
    result.add_argument(
        "command",
        choices=[
            "configure",
            "doctor",
            "start",
            "status",
            "reset",
            "stop",
            "open-viewer",
        ],
    )
    result.add_argument(
        "--drone-root", type=Path, default=default_source("hakoniwa-drone-core")
    )
    result.add_argument(
        "--viewer-root", type=Path, default=default_source("hakoniwa-threejs-drone")
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
        drone_root = _absolute_without_resolving(args.drone_root)
        viewer_root = _absolute_without_resolving(args.viewer_root)
        if args.command == "configure":
            return configure(drone_root, viewer_root, overrides)
        if args.command == "doctor":
            return doctor(drone_root, viewer_root, overrides)
        if args.command == "start":
            return start(drone_root, viewer_root, overrides)
        if args.command == "status":
            return status()
        if args.command == "reset":
            return reset(drone_root, viewer_root, overrides)
        if args.command == "stop":
            return stop()
        return open_viewer()
    except RecipeError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
