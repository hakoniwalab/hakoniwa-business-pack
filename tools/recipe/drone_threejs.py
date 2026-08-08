#!/usr/bin/env python3
from __future__ import annotations

import argparse
import importlib.util
import json
import shlex
import shutil
import socket
import sys
from pathlib import Path


TOOLS_DIR = Path(__file__).resolve().parents[1]
RECIPE_ID = "drone-single-mujoco-threejs-mac"
VIEWER_URL = (
    "http://127.0.0.1:8000/index.html"
    "?viewerConfigPath=/config/viewer-config-fleets.json"
    "&wsUri=ws://127.0.0.1:8765&wireVersion=v2"
)


class RecipeError(RuntimeError):
    pass


def load_foundation_module():
    script = TOOLS_DIR / "foundation.py"
    spec = importlib.util.spec_from_file_location(
        "business_pack_foundation_runtime", script
    )
    if spec is None or spec.loader is None:
        raise RecipeError(f"cannot load Foundation helper: {script}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def root() -> Path:
    return Path(__file__).resolve().parents[2]


def default_source(name: str) -> Path:
    return root().parent / name


def recipe_file() -> Path:
    return root() / "recipes" / "examples" / f"{RECIPE_ID}.yaml"


def _required(path: Path, label: str) -> Path:
    if not path.exists():
        raise RecipeError(f"{label} not found: {path}")
    return path


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


def preflight(drone_root: Path, viewer_root: Path) -> tuple[object, object]:
    foundation = load_foundation_module()
    paths = foundation.resolve_workspace(root(), RECIPE_ID)
    result = foundation.inspect_foundation(
        recipe_file(), paths.install_prefix
    )
    if result["status"] != "SATISFIED":
        foundation.print_inspection(result, False)
        raise RecipeError(
            "Foundation is not reusable; run tools/foundation.py plan/build first"
        )
    required = (
        (drone_root / "lib" / "mac-main_hako_drone_service", "Drone service"),
        (
            drone_root / "lib" / "mac-drone_visual_state_publisher",
            "Visual-state publisher",
        ),
        (drone_root / "config", "Drone config"),
        (viewer_root / "index.html", "Three.js viewer"),
        (paths.install_prefix / "bin" / "hakoniwa-pdu-web-bridge", "Web bridge"),
        (paths.foundation_python / "bin" / "python", "Foundation Python"),
        (
            paths.foundation_config / "cpp_core_config.json",
            "Foundation Core config",
        ),
    )
    for path, label in required:
        _required(path, label)
    return foundation, paths


def copy_recipe_config(drone_root: Path, recipe_config: Path) -> None:
    mappings = (
        ("drone", recipe_config / "drone"),
        ("pdudef", recipe_config / "pdudef"),
        ("controller", recipe_config / "controller"),
        ("assets/visual_state_publisher", recipe_config / "assets" / "visual_state_publisher"),
        ("assets/web_bridge_fleets", recipe_config / "assets" / "web_bridge_fleets"),
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


def write_launcher(
    paths,
    drone_root: Path,
    viewer_root: Path,
) -> Path:
    launch = {
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
                    "lib_path": [str(paths.install_prefix / "lib")],
                    "PATH": [
                        str(paths.foundation_python / "bin"),
                        str(paths.install_prefix / "bin"),
                    ],
                },
            },
        },
        "assets": [
            {
                "name": "drone-service-1",
                "activation_timing": "before_start",
                "command": str(
                    drone_root / "lib" / "mac-main_hako_drone_service"
                ),
                "args": [
                    "config/drone/fleets/api-current.json",
                    "config/pdudef/drone-pdudef-current.json",
                ],
                "cwd": str(paths.recipe_root),
                "delay_sec": 2,
            },
            {
                "name": "visual-state-publisher",
                "activation_timing": "before_start",
                "command": str(
                    drone_root / "lib" / "mac-drone_visual_state_publisher"
                ),
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
                "command": str(
                    paths.install_prefix / "bin" / "hakoniwa-pdu-web-bridge"
                ),
                "args": [
                    "--config-root",
                    str(
                        paths.recipe_config
                        / "assets"
                        / "web_bridge_fleets"
                    ),
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
                "name": "threejs-viewer-webserver",
                "activation_timing": "after_start",
                "command": str(paths.foundation_python / "bin" / "python"),
                "args": ["-m", "http.server", "8000"],
                "cwd": str(viewer_root),
                "depends_on": ["web-bridge-fleets"],
            },
        ],
    }
    output = paths.recipe_config / "launcher.json"
    output.write_text(
        json.dumps(launch, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return output


def write_wrappers(paths, drone_root: Path, launcher: Path) -> tuple[Path, Path]:
    python = paths.foundation_python / "bin" / "python"
    core_config = paths.foundation_config / "cpp_core_config.json"
    launch_script = paths.recipe_root / "launch.bash"
    launch_script.write_text(
        "#!/usr/bin/env bash\n"
        "set -euo pipefail\n"
        f"export HAKO_CONFIG_PATH={shlex.quote(str(core_config))}\n"
        f"export PATH={shlex.quote(str(paths.foundation_python / 'bin'))}:"
        f"{shlex.quote(str(paths.install_prefix / 'bin'))}:\"$PATH\"\n"
        f"export DYLD_LIBRARY_PATH={shlex.quote(str(paths.install_prefix / 'lib'))}:"
        "\"${DYLD_LIBRARY_PATH:-}\"\n"
        f"exec {shlex.quote(str(python))} -m "
        "hakoniwa_pdu.apps.launcher.hako_launcher "
        f"--mode immediate {shlex.quote(str(launcher))}\n",
        encoding="utf-8",
    )
    launch_script.chmod(0o755)

    mission_script = paths.recipe_missions / "run-single-mission.bash"
    mission_script.write_text(
        "#!/usr/bin/env bash\n"
        "set -euo pipefail\n"
        f"export HAKO_CONFIG_PATH={shlex.quote(str(core_config))}\n"
        f"export PATH={shlex.quote(str(paths.foundation_python / 'bin'))}:"
        "\"$PATH\"\n"
        f"exec bash {shlex.quote(str(drone_root / 'drone_api/external_rpc/apps/run_single_mission.bash'))} "
        f"--service-config {shlex.quote(str(paths.recipe_config / 'drone/fleets/services/api-current-service.json'))} "
        '--drone Drone-1 --alt 1.0 --x 1.5 --y 0.5 --z 1.0 '
        '--yaw 30 --speed 1.0 --timeout-sec 20 --land "$@"\n',
        encoding="utf-8",
    )
    mission_script.chmod(0o755)
    return launch_script, mission_script


def configure(drone_root: Path, viewer_root: Path) -> int:
    _foundation, paths = preflight(drone_root, viewer_root)
    for directory in paths.directories():
        directory.mkdir(parents=True, exist_ok=True)
    copy_recipe_config(drone_root, paths.recipe_config)
    launcher = write_launcher(paths, drone_root, viewer_root)
    launch_script, mission_script = write_wrappers(paths, drone_root, launcher)
    print(f"Recipe workspace : {paths.recipe_root}")
    print(f"Launcher         : {launcher}")
    print(f"Start            : {launch_script}")
    print(f"Mission          : {mission_script}")
    print(f"Viewer           : {VIEWER_URL}")
    return 0


def doctor(drone_root: Path, viewer_root: Path) -> int:
    _foundation, paths = preflight(drone_root, viewer_root)
    checks = [
        ("port 8000", _port_available(8000), "available"),
        ("port 8765", _port_available(8765), "available"),
        (
            "Recipe launcher",
            (paths.recipe_config / "launcher.json").is_file(),
            str(paths.recipe_config / "launcher.json"),
        ),
    ]
    failed = False
    for name, ok, detail in checks:
        if ok is None:
            print(f"[WARN] {name}: unavailable in this execution environment")
            continue
        print(f"[{'OK' if ok else 'NG'}] {name}: {detail}")
        failed = failed or not ok
    return 1 if failed else 0


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(
        description="Configure the Drone MuJoCo Three.js Recipe"
    )
    result.add_argument("command", choices=["configure", "doctor"])
    result.add_argument(
        "--drone-root",
        type=Path,
        default=default_source("hakoniwa-drone-core"),
    )
    result.add_argument(
        "--viewer-root",
        type=Path,
        default=default_source("hakoniwa-threejs-drone"),
    )
    return result


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        drone_root = args.drone_root.resolve()
        viewer_root = args.viewer_root.resolve()
        if args.command == "configure":
            return configure(drone_root, viewer_root)
        return doctor(drone_root, viewer_root)
    except RecipeError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
