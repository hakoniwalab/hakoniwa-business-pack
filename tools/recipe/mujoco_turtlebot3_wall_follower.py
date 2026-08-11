#!/usr/bin/env python3
from __future__ import annotations

import argparse
import importlib.util
import json
import platform
import shutil
import subprocess
import sys
from pathlib import Path


RECIPE_ID = "mujoco-turtlebot3-wall-follower"
RUNTIME_REQUIREMENTS = (
    Path(__file__).resolve().parents[2]
    / "recipes"
    / "requirements"
    / f"{RECIPE_ID}.txt"
)
BASE_SCRIPT = Path(__file__).with_name("mujoco_turtlebot3_mbody.py")
SPEC = importlib.util.spec_from_file_location("tb3_mbody_for_wall_follower", BASE_SCRIPT)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError(f"cannot load TB3 Recipe helper: {BASE_SCRIPT}")
base = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = base
SPEC.loader.exec_module(base)
base.RECIPE_ID = RECIPE_ID


def stage_runtime_inputs(args: argparse.Namespace) -> Path:
    resolved = base.paths()
    source = args.mujoco_root.resolve()
    input_root = resolved["assets"] / "runtime-input"
    shutil.copytree(source / "config", input_root / "config", dirs_exist_ok=True)
    scripts = input_root / "python"
    scripts.mkdir(parents=True, exist_ok=True)
    for name in ("tb3_obstacle_avoider.py", "lidar_visualizer.py"):
        shutil.copy2(source / "python" / name, scripts / name)

    model = base.materialize_assets(args)
    manifest = json.loads(model.read_text(encoding="utf-8"))
    staged_config = input_root / "config"
    manifest["pdu_def"] = str(staged_config / "tb3-pdudef-compact.json")
    manifest["endpoint"] = str(staged_config / "endpoint/tb3_sim_endpoint.json")
    source_assets = source / "config" / "assets"
    for component in manifest["components"]:
        original = Path(component["config"])
        component["config"] = str(staged_config / original.relative_to(source_assets.parent))
    base.write_json(model, manifest)
    return model


def write_launcher(args: argparse.Namespace) -> Path:
    resolved = base.paths()
    prefix = resolved["prefix"]
    python = base.foundation_python(prefix)
    input_root = resolved["assets"] / "runtime-input"
    model = args.model.replace("_", "-")
    manifest = resolved["config"] / f"tb3-{model}-asset.json"
    system = platform.system()
    loader = "PATH" if system == "Windows" else (
        "DYLD_LIBRARY_PATH" if system == "Darwin" else "LD_LIBRARY_PATH"
    )
    prepend = {"PATH": [str(python.parent), str(prefix / "bin")]}
    prepend.setdefault(loader, []).append(str(prefix / "lib"))
    assets = [{
        "name": "tb3_sim_mbody",
        "activation_timing": "before_start",
        "command": str(base.executable_path(resolved["build"], args.model)),
        "args": [],
        "env": {"set": {
            "HAKO_TB3_ENABLE_VIEWER": "0" if args.headless else "1",
            "HAKO_TB3_MANIFEST_PATH": str(manifest),
        }},
        "start_grace_sec": 3,
    }]
    if not args.headless:
        assets.append({
            "name": "lidar_visualizer",
            "activation_timing": "after_start",
            "command": str(python),
            "args": [str(input_root / "python/lidar_visualizer.py"), str(input_root / "config/tb3-pdudef-compact.json"), "TB3"],
            "start_grace_sec": 1,
        })
    assets.append({
        "name": "obstacle_avoider",
        "activation_timing": "after_start",
        "command": str(python),
        "args": [
            str(input_root / "python/tb3_obstacle_avoider.py"),
            "--config-path", str(input_root / "config/tb3-pdudef-compact.json"),
            "--duration-sec", str(args.duration_sec),
        ],
        "start_grace_sec": 1,
    })
    return base.write_json(resolved["config"] / "launcher.json", {
        "version": "0.1",
        "defaults": {
            "cwd": str(input_root),
            "stdout": str(resolved["logs"] / "${asset}.out"),
            "stderr": str(resolved["logs"] / "${asset}.err"),
            "env": {"set": {
                "HAKONIWA_CORE_ROOT": str(prefix),
                "HAKONIWA_PDU_ENDPOINT_ROOT": str(prefix),
                "HAKO_CONFIG_PATH": str(resolved["foundation"] / "config/cpp_core_config.json"),
                "PYTHONUNBUFFERED": "1",
            }, "prepend": prepend},
            "start_grace_sec": 2,
            "delay_sec": 1,
        },
        "assets": assets,
    })


def inspect(args: argparse.Namespace) -> dict:
    state = base.inspect(args)
    required = (
        args.mujoco_root.resolve() / "python/tb3_obstacle_avoider.py",
        args.mujoco_root.resolve() / "python/lidar_visualizer.py",
    )
    for path in required:
        if not path.is_file():
            state["errors"].append(f"runtime input is missing: {path}")
    python = base.foundation_python(base.paths()["prefix"])
    if python.is_file():
        probe = subprocess.run(
            [str(python), "-c", "import numpy, matplotlib, PyQt5"],
            capture_output=True,
            text=True,
            check=False,
        )
        if probe.returncode != 0:
            state["errors"].append(
                "Recipe Python dependencies are missing from Foundation Python; "
                f"run configure ({RUNTIME_REQUIREMENTS})"
            )
    state["status"] = "READY" if not state["errors"] else "BLOCKED"
    return state


def require_ready(args: argparse.Namespace) -> dict:
    state = inspect(args)
    if state["status"] != "READY":
        raise base.RecipeError("\n".join(state["errors"]))
    return state


def install_runtime_dependencies() -> None:
    if not RUNTIME_REQUIREMENTS.is_file():
        raise base.RecipeError(
            f"wall-follower Python requirements not found: {RUNTIME_REQUIREMENTS}"
        )
    base.run([
        str(base.foundation_python(base.paths()["prefix"])),
        "-m", "pip", "install", "--disable-pip-version-check",
        "--requirement", str(RUNTIME_REQUIREMENTS),
    ], check=True)


def configure(args: argparse.Namespace) -> None:
    # Validate Foundation and source inputs before mutating the shared venv.
    state = base.require_ready(args)
    resolved = base.paths()
    for name in ("build", "deps", "config", "logs", "validation", "runtime", "assets"):
        resolved[name].mkdir(parents=True, exist_ok=True)
    base.install_mbody_tool_dependencies()
    install_runtime_dependencies()
    state = require_ready(args)
    stage_runtime_inputs(args)
    base.write_json(resolved["config"] / "resolved.json", state)
    write_launcher(args)
    base.run(base.cmake_configure_command(args, state), check=True)
    print(f"Recipe workspace: {resolved['recipe']}")


def build(args: argparse.Namespace) -> None:
    require_ready(args)
    resolved = base.paths()
    if not (resolved["build"] / "CMakeCache.txt").is_file():
        configure(args)
    command = ["cmake", "--build", str(resolved["build"])]
    if platform.system() == "Windows":
        command.extend(["--config", "Release"])
    command.extend(["--target", f"tb3_sim_{args.model}", "--parallel"])
    base.run(command, check=True)
    if not base.executable_path(resolved["build"], args.model).is_file():
        raise base.RecipeError("TurtleBot3 simulator binary was not produced")
    write_launcher(args)


def start(args: argparse.Namespace) -> None:
    require_ready(args)
    if not base.executable_path(base.paths()["build"], args.model).is_file():
        raise base.RecipeError("TurtleBot3 is not built; run build first")
    launch = write_launcher(args)
    python = base.foundation_python(base.paths()["prefix"])
    base.run([
        str(python), "-m", "hakoniwa_pdu.apps.launcher.hako_launcher",
        str(launch), "--mode", "immediate", "--background", str(base.session_file()),
    ], check=True)
    base.run(base.control_command("status"), check=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Configure and operate the managed TB3 wall-follower Recipe")
    parser.add_argument("command", choices=("configure", "build", "doctor", "start", "status", "stop"))
    parser.add_argument("--mbody-root", type=Path, default=base.default_source("hakoniwa-mbody-registry"))
    parser.add_argument("--mujoco-root", type=Path, default=base.default_source("hakoniwa-mujoco-robots"))
    parser.add_argument("--model", choices=base.MODELS, default="waffle")
    parser.add_argument("--duration-sec", type=float, default=20.0)
    parser.add_argument("--headless", action="store_true")
    args = parser.parse_args(argv)
    try:
        if args.command == "doctor":
            state = inspect(args)
            print(json.dumps(state, ensure_ascii=False, indent=2))
            return 0 if state["status"] == "READY" else 1
        if args.command == "configure":
            configure(args)
        elif args.command == "build":
            build(args)
        elif args.command == "start":
            start(args)
        elif args.command == "status":
            base.control("status")
        else:
            base.control("terminate")
        return 0
    except (base.RecipeError, subprocess.CalledProcessError, OSError, json.JSONDecodeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
