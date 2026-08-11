#!/usr/bin/env python3
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path


RECIPE_ID = "mujoco-turtlebot3-mbody"
TOOLS_DIR = Path(__file__).resolve().parents[1]
MODELS = ("burger", "waffle", "waffle_pi")
MBODY_TOOL_REQUIREMENTS = (
    Path(__file__).resolve().parents[2]
    / "recipes"
    / "requirements"
    / "hakoniwa-mbody-registry.txt"
)


class RecipeError(RuntimeError):
    pass


def business_root() -> Path:
    return Path(__file__).resolve().parents[2]


def default_source(name: str) -> Path:
    return business_root().parent / name


def recipe_file() -> Path:
    return business_root() / "recipes" / "examples" / f"{RECIPE_ID}.yaml"


def load_foundation_module():
    script = TOOLS_DIR / "foundation.py"
    spec = importlib.util.spec_from_file_location("business_pack_foundation_tb3_mbody", script)
    if spec is None or spec.loader is None:
        raise RecipeError(f"cannot load Foundation helper: {script}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def paths() -> dict[str, Path]:
    recipe = business_root() / "work" / "recipes" / RECIPE_ID
    foundation = business_root() / "work" / "foundation"
    return {
        "recipe": recipe,
        "build": recipe / "build" / "hakoniwa-mujoco-robots",
        "deps": recipe / "deps",
        "config": recipe / "config",
        "logs": recipe / "logs",
        "assets": recipe / "assets",
        "validation": recipe / "validation",
        "runtime": recipe / "runtime",
        "foundation": foundation,
        "prefix": foundation / "install",
        "toolchain": foundation / "config" / "toolchain.json",
    }


def foundation_python(prefix: Path) -> Path:
    candidates = (
        prefix / "python" / "Scripts" / "python.exe",
        prefix / "python" / "bin" / "python3",
        prefix / "python" / "bin" / "python",
    )
    return next((candidate for candidate in candidates if candidate.is_file()), candidates[0])


def executable_path(build: Path, model: str = "burger") -> Path:
    suffix = ".exe" if platform.system() == "Windows" else ""
    base = build / "main_for_sample" / "tb3"
    candidates = (base / "Release" / f"tb3_sim_{model}{suffix}", base / f"tb3_sim_{model}{suffix}")
    return next((candidate for candidate in candidates if candidate.is_file()), candidates[-1])


def require_file(path: Path, label: str, errors: list[str]) -> None:
    if not path.is_file():
        errors.append(f"{label} is missing: {path}")


def inspect(args: argparse.Namespace) -> dict:
    resolved = paths()
    mbody = args.mbody_root.resolve()
    mujoco = args.mujoco_root.resolve()
    prefix = resolved["prefix"]
    errors: list[str] = []

    foundation = load_foundation_module()
    inspection = foundation.inspect_foundation(recipe_file(), prefix)
    if inspection["status"] != "SATISFIED":
        errors.append(f"Foundation is {inspection['status']}; run tools/foundation.py plan/build")

    python = foundation_python(prefix)
    require_file(python, "Foundation Python", errors)
    require_file(
        mbody / "bodies" / f"turtlebot3_{args.model}" / "generated" / f"turtlebot3_{args.model}.minimal_world.xml",
        f"MBody TurtleBot3 {args.model} world",
        errors,
    )
    require_file(mujoco / "src" / "CMakeLists.txt", "MuJoCo Robots source", errors)
    manifest_name = f"tb3-mbody-{args.model.replace('_', '-')}-asset.json"
    require_file(mujoco / "config" / "assets" / manifest_name, f"TurtleBot3 {args.model} asset manifest", errors)
    require_file(mujoco / "python" / "tb3_route_demo.py", "route demo", errors)

    toolchain: dict[str, object] = {}
    if resolved["toolchain"].is_file():
        toolchain = json.loads(resolved["toolchain"].read_text(encoding="utf-8"))
    vcpkg_root = Path(str(toolchain.get("vcpkg_root", ""))) if toolchain.get("vcpkg_root") else None
    if platform.system() == "Windows":
        if vcpkg_root is None:
            errors.append(
                "Windows Foundation toolchain is not selected; run tools/foundation.py toolchain "
                "--recipe-id mujoco-turtlebot3-mbody --vcpkg-root <path>"
            )
        else:
            require_file(vcpkg_root / "scripts" / "buildsystems" / "vcpkg.cmake", "vcpkg CMake toolchain", errors)
            require_file(
                vcpkg_root / "installed" / "x64-windows" / "share" / "glfw3" / "glfw3Config.cmake",
                "GLFW CMake package required by TB3 sensor renderer linkage",
                errors,
            )
        for variant in ("core_callback", "core_polling"):
            require_file(prefix / "lib" / f"hakoniwa_pdu_endpoint_{variant}.lib", f"PDU Endpoint {variant} import library", errors)

    return {
        "status": "READY" if not errors else "BLOCKED",
        "platform": platform.system(),
        "errors": errors,
        "workspace": {key: str(value) for key, value in resolved.items()},
        "sources": {"mbody": str(mbody), "mujoco": str(mujoco)},
        "python": str(python),
        "vcpkg_root": str(vcpkg_root) if vcpkg_root else None,
    }


def write_json(path: Path, value: object) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)
    return path


def require_ready(args: argparse.Namespace) -> dict:
    state = inspect(args)
    if state["status"] != "READY":
        raise RecipeError("\n".join(state["errors"]))
    return state


def materialize_assets(args: argparse.Namespace) -> Path:
    resolved = paths()
    mbody = args.mbody_root.resolve()
    mujoco = args.mujoco_root.resolve()
    asset_root = resolved["assets"] / f"turtlebot3_{args.model}"
    source_dir = asset_root / "source"
    marker = source_dir / ".materialized"
    if not marker.is_file():
        command = [
            str(foundation_python(resolved["prefix"])),
            str(mbody / "tools" / "fetch.py"),
            str(mbody / "sources" / f"turtlebot3_{args.model}.yaml"),
            "--output-dir",
            str(source_dir),
        ]
        run(command, cwd=mbody, check=True)
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.write_text("materialized by Business Pack Recipe\n", encoding="utf-8")

    generated_dir = asset_root / "generated"
    generated_dir.mkdir(parents=True, exist_ok=True)
    model = generated_dir / f"turtlebot3_{args.model}.minimal_world.xml"
    shutil.copy2(mbody / "bodies" / f"turtlebot3_{args.model}" / "generated" / model.name, model)

    manifest_name = f"tb3-mbody-{args.model.replace('_', '-')}-asset.json"
    manifest = json.loads((mujoco / "config" / "assets" / manifest_name).read_text(encoding="utf-8"))
    manifest["model"] = str(model)
    manifest["pdu_def"] = str((mujoco / "config" / "tb3-pdudef-compact.json").resolve())
    manifest["endpoint"] = str((mujoco / "config" / "endpoint" / "tb3_sim_endpoint.json").resolve())
    asset_dir = mujoco / "config" / "assets"
    for component in manifest["components"]:
        component["config"] = str((asset_dir / component["config"]).resolve())
    return write_json(resolved["config"] / f"tb3-{args.model.replace('_', '-')}-asset.json", manifest)


def write_launch(args: argparse.Namespace) -> Path:
    resolved = paths()
    source = args.mujoco_root.resolve()
    executable = executable_path(resolved["build"], args.model)
    prefix = resolved["prefix"]
    system_name = platform.system()
    loader_variable = "PATH" if system_name == "Windows" else (
        "DYLD_LIBRARY_PATH" if system_name == "Darwin" else "LD_LIBRARY_PATH"
    )
    prepend = {
        "PATH": [str(prefix / "python" / ("Scripts" if system_name == "Windows" else "bin")), str(prefix / "bin"), str(executable.parent)]
    }
    prepend.setdefault(loader_variable, []).append(str(prefix / "lib"))
    launch = {
        "version": "0.1",
        "defaults": {
            "cwd": str(source),
            "stdout": str(resolved["logs"] / "${asset}.out"),
            "stderr": str(resolved["logs"] / "${asset}.err"),
            "env": {
                "set": {
                    "HAKONIWA_CORE_ROOT": str(prefix),
                    "HAKONIWA_PDU_ENDPOINT_ROOT": str(prefix),
                    "HAKO_CONFIG_PATH": str(resolved["foundation"] / "config" / "cpp_core_config.json"),
                    "PYTHON_CMD": str(foundation_python(prefix)),
                    "PYTHONUNBUFFERED": "1",
                },
                "prepend": prepend,
            },
            "start_grace_sec": 2,
            "delay_sec": 1,
        },
        "assets": [
            {
                "name": f"tb3_sim_{args.model}",
                "activation_timing": "before_start",
                "command": str(executable),
                "args": [],
                "env": {"set": {"HAKO_TB3_ENABLE_VIEWER": "0" if args.headless else "1", "HAKO_TB3_MANIFEST_PATH": str(resolved["config"] / f"tb3-{args.model.replace('_', '-')}-asset.json")}},
                "start_grace_sec": 3,
            },
            {
                "name": "route_demo",
                "activation_timing": "after_start",
                "command": str(foundation_python(prefix)),
                "args": [str(source / "python" / "tb3_route_demo.py"), "--pattern", "figure8", "--loops", "1", "--hold-sec", "12"],
                "start_grace_sec": 1,
            },
        ],
    }
    return write_json(resolved["config"] / "launcher.json", launch)


def cmake_configure_command(args: argparse.Namespace, state: dict) -> list[str]:
    resolved = paths()
    prefix = resolved["prefix"]
    command = [
        "cmake", "-S", str(args.mujoco_root.resolve() / "src"), "-B", str(resolved["build"]),
        # TB3 sensor targets link renderer/GLFW symbols even when no window is opened.
        "-DUSE_VIEWER=ON",
        f"-DFETCHCONTENT_BASE_DIR={resolved['deps']}",
        f"-DHAKONIWA_INSTALL_PREFIX={prefix}",
        f"-DHAKONIWA_PDU_ENDPOINT_PREFIX={prefix}",
    ]
    if platform.system() == "Windows":
        vcpkg = Path(state["vcpkg_root"])
        installed = vcpkg / "installed" / "x64-windows"
        command.extend([
            f"-DCMAKE_PREFIX_PATH={prefix};{installed}",
            f"-DHAKONIWA_EXTRA_PREFIX_PATH={prefix};{installed}",
            f"-DCMAKE_TOOLCHAIN_FILE={vcpkg / 'scripts' / 'buildsystems' / 'vcpkg.cmake'}",
            "-DVCPKG_TARGET_TRIPLET=x64-windows",
        ])
    else:
        command.extend([f"-DCMAKE_PREFIX_PATH={prefix}", f"-DHAKONIWA_EXTRA_PREFIX_PATH={prefix}", "-DCMAKE_BUILD_TYPE=Release"])
    return command


def run(command: list[str], *, cwd: Path | None = None, check: bool = False) -> int:
    print("+", subprocess.list2cmdline(command), flush=True)
    completed = subprocess.run(command, cwd=cwd, check=check)
    return completed.returncode


def install_mbody_tool_dependencies() -> None:
    if not MBODY_TOOL_REQUIREMENTS.is_file():
        raise RecipeError(f"MBody tool requirements not found: {MBODY_TOOL_REQUIREMENTS}")
    command = [
        str(foundation_python(paths()["prefix"])),
        "-m", "pip", "install", "--disable-pip-version-check",
        "--requirement", str(MBODY_TOOL_REQUIREMENTS),
    ]
    run(command, check=True)


def configure(args: argparse.Namespace) -> None:
    state = require_ready(args)
    resolved = paths()
    for name in ("build", "deps", "config", "logs", "validation", "runtime", "assets"):
        resolved[name].mkdir(parents=True, exist_ok=True)
    install_mbody_tool_dependencies()
    materialize_assets(args)
    write_json(resolved["config"] / "resolved.json", state)
    write_launch(args)
    run(cmake_configure_command(args, state), check=True)
    print(f"Recipe workspace: {resolved['recipe']}")


def build(args: argparse.Namespace) -> None:
    require_ready(args)
    resolved = paths()
    if not (resolved["build"] / "CMakeCache.txt").is_file():
        configure(args)
    command = ["cmake", "--build", str(resolved["build"])]
    if platform.system() == "Windows":
        command.extend(["--config", "Release"])
    command.extend(["--target", f"tb3_sim_{args.model}", "--parallel"])
    run(command, check=True)
    executable = executable_path(resolved["build"], args.model)
    if not executable.is_file():
        raise RecipeError(f"TurtleBot3 executable was not produced: {executable}")
    write_launch(args)
    print(f"TurtleBot3 executable: {executable}")


def session_file() -> Path:
    return paths()["runtime"] / "launcher-session.json"


def control_command(command: str) -> list[str]:
    python = foundation_python(paths()["prefix"])
    return [str(python), "-m", "hakoniwa_pdu.apps.launcher.hako_launcher_ctl", command, str(session_file())]


def start(args: argparse.Namespace) -> None:
    require_ready(args)
    resolved = paths()
    if not executable_path(resolved["build"], args.model).is_file():
        raise RecipeError("TurtleBot3 is not built; run the Recipe build command first")
    for name in ("logs", "runtime", "config"):
        resolved[name].mkdir(parents=True, exist_ok=True)
    launch = write_launch(args)
    python = foundation_python(resolved["prefix"])
    command = [str(python), "-m", "hakoniwa_pdu.apps.launcher.hako_launcher", str(launch), "--mode", "immediate", "--background", str(session_file())]
    run(command, cwd=args.mujoco_root.resolve(), check=True)
    run(control_command("status"), check=True)


def control(command: str) -> None:
    if not session_file().is_file():
        raise RecipeError(f"Launcher session does not exist: {session_file()}")
    run(control_command(command), check=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Configure and operate the cross-platform TurtleBot3 MBody Recipe")
    parser.add_argument("command", choices=("configure", "build", "doctor", "start", "status", "stop"))
    parser.add_argument("--mbody-root", type=Path, default=default_source("hakoniwa-mbody-registry"))
    parser.add_argument("--mujoco-root", type=Path, default=default_source("hakoniwa-mujoco-robots"))
    parser.add_argument("--headless", action="store_true", help="build and run without the MuJoCo viewer")
    parser.add_argument("--model", choices=MODELS, default="burger")
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
            control("status")
        else:
            control("terminate")
        return 0
    except (RecipeError, subprocess.CalledProcessError, OSError, json.JSONDecodeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
