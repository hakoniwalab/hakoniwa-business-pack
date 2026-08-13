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


RECIPE_ID = "mujoco-turtlebot3-dual-mirror"
TOOLS_DIR = Path(__file__).resolve().parents[1]
MBODY_TOOL_REQUIREMENTS = TOOLS_DIR.parent / "recipes" / "requirements" / "hakoniwa-mbody-registry.txt"


class RecipeError(RuntimeError):
    pass


def business_root() -> Path:
    return Path(__file__).resolve().parents[2]


def default_mujoco_root() -> Path:
    return business_root().parent / "hakoniwa-mujoco-robots"


def default_mbody_root() -> Path:
    return business_root().parent / "hakoniwa-mbody-registry"


def recipe_file() -> Path:
    return business_root() / "recipes" / "examples" / f"{RECIPE_ID}.yaml"


def paths() -> dict[str, Path]:
    recipe = business_root() / "work" / "recipes" / RECIPE_ID
    foundation = business_root() / "work" / "foundation"
    return {
        "recipe": recipe,
        "build": recipe / "build" / "hakoniwa-mujoco-robots",
        "deps": recipe / "deps",
        "input": recipe / "assets" / "runtime-input",
        "config": recipe / "config",
        "logs": recipe / "logs",
        "runtime": recipe / "runtime",
        "prefix": foundation / "install",
        "foundation": foundation,
        "toolchain": foundation / "config" / "toolchain.json",
    }


def load_foundation_module():
    script = TOOLS_DIR / "foundation.py"
    spec = importlib.util.spec_from_file_location("business_pack_foundation_tb3_dual", script)
    if spec is None or spec.loader is None:
        raise RecipeError(f"cannot load Foundation helper: {script}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def foundation_python(prefix: Path) -> Path:
    candidates = (
        prefix / "python" / "Scripts" / "python.exe",
        prefix / "python" / "bin" / "python3",
        prefix / "python" / "bin" / "python",
    )
    return next((candidate for candidate in candidates if candidate.is_file()), candidates[0])


def executable(build: Path, model: str) -> Path:
    suffix = ".exe" if platform.system() == "Windows" else ""
    base = build / "main_for_sample" / "tb3"
    candidates = (base / "Release" / f"tb3_sim_{model}{suffix}", base / f"tb3_sim_{model}{suffix}")
    return next((candidate for candidate in candidates if candidate.is_file()), candidates[-1])


def write_json(path: Path, value: object) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)
    return path


def inspect(args: argparse.Namespace) -> dict:
    resolved = paths()
    source = args.mujoco_root.resolve()
    errors: list[str] = []
    foundation = load_foundation_module()
    inspection = foundation.inspect_foundation(recipe_file(), resolved["prefix"])
    if inspection["status"] != "SATISFIED":
        errors.append(f"Foundation is {inspection['status']}; run tools/foundation.py plan/build")
    required = (
        (foundation_python(resolved["prefix"]), "Foundation Python"),
        (source / "src" / "CMakeLists.txt", "MuJoCo Robots source"),
        (source / "models" / "tb3" / "tb3_burger_real_waffle_mirror.xml", "Burger mirror world"),
        (source / "models" / "tb3" / "tb3_waffle_real_burger_mirror.xml", "Waffle mirror world"),
        (source / "config" / "tb3-dual-pdudef-compact.json", "dual PDU definition"),
        (source / "python" / "tb3_route_demo.py", "route controller"),
        (args.mbody_root.resolve() / "tools/fetch.py", "MBody fetch tool"),
        (args.mbody_root.resolve() / "sources/turtlebot3_burger.yaml", "pinned MBody Burger source definition"),
        (MBODY_TOOL_REQUIREMENTS, "MBody tool requirements"),
    )
    for path, label in required:
        if not path.is_file():
            errors.append(f"{label} is missing: {path}")
    # inspect_foundation() owns the cross-platform Python ABI contract, including
    # the EXT_SUFFIX fallback required when Windows reports SOABI as null.
    runtime_python = inspection.get("runtime", {}).get("python", {})
    toolchain = {}
    if resolved["toolchain"].is_file():
        toolchain = json.loads(resolved["toolchain"].read_text(encoding="utf-8"))
    vcpkg = Path(str(toolchain["vcpkg_root"])) if toolchain.get("vcpkg_root") else None
    if platform.system() == "Windows":
        if vcpkg is None:
            errors.append("Windows Foundation toolchain has no vcpkg_root")
        else:
            cmake_toolchain = vcpkg / "scripts" / "buildsystems" / "vcpkg.cmake"
            if not cmake_toolchain.is_file():
                errors.append(f"vcpkg toolchain is missing: {cmake_toolchain}")
        for variant in ("core_callback", "core_polling"):
            library = resolved["prefix"] / "lib" / f"hakoniwa_pdu_endpoint_{variant}.lib"
            if not library.is_file():
                errors.append(f"PDU Endpoint {variant} import library is missing: {library}")
    return {
        "status": "READY" if not errors else "BLOCKED",
        "errors": errors,
        "vcpkg_root": str(vcpkg) if vcpkg else None,
        "python": str(foundation_python(resolved["prefix"])),
        "python_contract": runtime_python,
    }


def require_ready(args: argparse.Namespace) -> dict:
    state = inspect(args)
    if state["status"] != "READY":
        raise RecipeError("\n".join(state["errors"]))
    return state


def install_mbody_tool_dependencies() -> None:
    run([
        str(foundation_python(paths()["prefix"])),
        "-m", "pip", "install", "--disable-pip-version-check",
        "--requirement", str(MBODY_TOOL_REQUIREMENTS),
    ])


def materialize_burger_source(mbody_root: Path) -> Path:
    destination = paths()["input"] / "mbody" / "turtlebot3_burger" / "source"
    marker = destination / "turtlebot3_description" / "meshes" / "bases" / "burger_base.stl"
    if not marker.is_file():
        run([
            str(foundation_python(paths()["prefix"])),
            str(mbody_root / "tools" / "fetch.py"),
            str(mbody_root / "sources" / "turtlebot3_burger.yaml"),
            "--output-dir", str(destination),
        ])
    if not marker.is_file():
        raise RecipeError(f"materialized MBody Burger mesh is missing: {marker}")
    return destination


def stage_runtime_inputs(source: Path, mbody_source: Path) -> None:
    destination = paths()["input"]
    for relative in ("config", "models/tb3"):
        shutil.copytree(source / relative, destination / relative, dirs_exist_ok=True)
    python_dir = destination / "python"
    python_dir.mkdir(parents=True, exist_ok=True)
    for name in ("tb3_route_demo.py", "lidar_visualizer.py"):
        shutil.copy2(source / "python" / name, python_dir / name)
    staged_mesh_source = destination / "mbody" / "turtlebot3_burger" / "source"
    if mbody_source.resolve() != staged_mesh_source.resolve():
        shutil.copytree(mbody_source, staged_mesh_source, dirs_exist_ok=True)
    old_prefix = "../../../hakoniwa-mbody-registry/bodies/turtlebot3_burger/source"
    new_prefix = "../../mbody/turtlebot3_burger/source"
    for model in (
        destination / "models/tb3/tb3_burger_real_waffle_mirror.xml",
        destination / "models/tb3/tb3_waffle_real_burger_mirror.xml",
    ):
        text = model.read_text(encoding="utf-8")
        if old_prefix not in text:
            raise RecipeError(f"expected MBody mesh reference is missing: {model}")
        model.write_text(text.replace(old_prefix, new_prefix), encoding="utf-8")


def cmake_command(args: argparse.Namespace, state: dict) -> list[str]:
    resolved = paths()
    prefix = resolved["prefix"]
    command = [
        "cmake", "-S", str(args.mujoco_root.resolve() / "src"), "-B", str(resolved["build"]),
        # TB3 sensor targets link the renderer even when runtime windows are disabled.
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
            f"-DCMAKE_TOOLCHAIN_FILE={vcpkg / 'scripts/buildsystems/vcpkg.cmake'}",
            "-DVCPKG_TARGET_TRIPLET=x64-windows",
        ])
    else:
        command.extend([f"-DCMAKE_PREFIX_PATH={prefix}", f"-DHAKONIWA_EXTRA_PREFIX_PATH={prefix}", "-DCMAKE_BUILD_TYPE=Release"])
    return command


def loader_prepend(prefix: Path) -> dict[str, list[str]]:
    system = platform.system()
    loader = "PATH" if system == "Windows" else ("DYLD_LIBRARY_PATH" if system == "Darwin" else "LD_LIBRARY_PATH")
    result = {"PATH": [str(foundation_python(prefix).parent), str(prefix / "bin")]}
    result.setdefault(loader, []).append(str(prefix / "lib"))
    return result


def write_launcher(args: argparse.Namespace) -> Path:
    resolved = paths()
    prefix = resolved["prefix"]
    input_root = resolved["input"]
    python = foundation_python(prefix)
    common = {
        "set": {
            "HAKONIWA_CORE_ROOT": str(prefix),
            "HAKONIWA_PDU_ENDPOINT_ROOT": str(prefix),
            "HAKO_CONFIG_PATH": str(resolved["foundation"] / "config" / "cpp_core_config.json"),
            "PYTHONUNBUFFERED": "1",
        },
        "prepend": loader_prepend(prefix),
    }
    assets = []
    simulators = (
        ("tb3_burger_real", "burger", "TB3_BURGER", "tb3_dual_burger_endpoint", "tb3-dual-burger-real-waffle-mirror-asset.json", "tb3-dual-burger-real-waffle-mirror-bindings.json", False),
        ("tb3_waffle_real", "waffle", "TB3_WAFFLE", "tb3_dual_waffle_endpoint", "tb3-dual-waffle-real-burger-mirror-asset.json", "tb3-dual-waffle-real-burger-mirror-bindings.json", True),
    )
    for name, model, robot, endpoint, manifest, bindings, disable_conductor in simulators:
        settings = {
            "HAKO_ASSET_NAME": name,
            "HAKO_TB3_PDU_ROBOT_NAME": robot,
            "HAKO_TB3_ENDPOINT_NAME": endpoint,
            "HAKO_TB3_MANIFEST_PATH": str(input_root / "config/assets" / manifest),
            "HAKO_TB3_MIRROR_BINDINGS_PATH": str(input_root / "config" / bindings),
            "HAKO_TB3_ENABLE_VIEWER": "0" if args.headless or disable_conductor else "1",
        }
        if disable_conductor:
            settings.update({"HAKO_TB3_DISABLE_CONDUCTOR_START": "1", "HAKO_TB3_DISABLE_VIEWER": "1"})
        assets.append({
            "name": name, "activation_timing": "before_start", "command": str(executable(resolved["build"], model)),
            "args": [], "env": {"set": settings}, "start_grace_sec": 3, "delay_sec": 1,
        })
    if not args.headless:
        for suffix, robot in (("burger", "TB3_BURGER"), ("waffle", "TB3_WAFFLE")):
            assets.append({
                "name": f"{suffix}_lidar_visualizer",
                "activation_timing": "after_start",
                "command": str(python),
                "args": [
                    str(input_root / "python/lidar_visualizer.py"),
                    str(input_root / "config/tb3-dual-pdudef-compact.json"),
                    robot,
                ],
                "start_grace_sec": 1,
                "delay_sec": 0,
            })
    for suffix, robot, pattern in (("burger", "TB3_BURGER", "figure8"), ("waffle", "TB3_WAFFLE", "dance")):
        assets.append({
            "name": f"{suffix}_route_demo", "activation_timing": "after_start", "command": str(python),
            "args": [str(input_root / "python/tb3_route_demo.py"), "--config-path", str(input_root / "config/tb3-dual-pdudef-compact.json"), "--robot", robot, "--pattern", pattern, "--loops", "1", "--hold-sec", "4"],
            "start_grace_sec": 1, "delay_sec": 0,
        })
    return write_json(resolved["config"] / "launcher.json", {
        "version": "0.1",
        "defaults": {"cwd": str(input_root), "stdout": str(resolved["logs"] / "${asset}.out"), "stderr": str(resolved["logs"] / "${asset}.err"), "env": common, "start_grace_sec": 2, "delay_sec": 1},
        "assets": assets,
    })


def run(command: list[str], *, check: bool = True) -> None:
    print("+", subprocess.list2cmdline(command), flush=True)
    subprocess.run(command, check=check)


def configure(args: argparse.Namespace) -> None:
    state = require_ready(args)
    for name in ("build", "deps", "input", "config", "logs", "runtime"):
        paths()[name].mkdir(parents=True, exist_ok=True)
    install_mbody_tool_dependencies()
    mbody_source = materialize_burger_source(args.mbody_root.resolve())
    stage_runtime_inputs(args.mujoco_root.resolve(), mbody_source)
    write_launcher(args)
    run(cmake_command(args, state))
    print(f"Recipe workspace: {paths()['recipe']}")


def build(args: argparse.Namespace) -> None:
    require_ready(args)
    if not (paths()["build"] / "CMakeCache.txt").is_file():
        configure(args)
    command = ["cmake", "--build", str(paths()["build"])]
    if platform.system() == "Windows":
        command.extend(["--config", "Release"])
    command.extend(["--target", "tb3_sim_burger", "tb3_sim_waffle", "--parallel"])
    run(command)
    for model in ("burger", "waffle"):
        if not executable(paths()["build"], model).is_file():
            raise RecipeError(f"missing simulator binary: {executable(paths()['build'], model)}")
    write_launcher(args)


def session_file() -> Path:
    return paths()["runtime"] / "launcher-session.json"


def control_command(command: str) -> list[str]:
    python = foundation_python(paths()["prefix"])
    return [str(python), "-m", "hakoniwa_pdu.apps.launcher.hako_launcher_ctl", command, str(session_file())]


def start(args: argparse.Namespace) -> None:
    require_ready(args)
    for model in ("burger", "waffle"):
        if not executable(paths()["build"], model).is_file():
            raise RecipeError("simulators are not built; run build first")
    launch = write_launcher(args)
    python = foundation_python(paths()["prefix"])
    run([str(python), "-m", "hakoniwa_pdu.apps.launcher.hako_launcher", str(launch), "--mode", "immediate", "--background", str(session_file())])
    run(control_command("status"))


def control(command: str) -> None:
    if not session_file().is_file():
        raise RecipeError(f"Launcher session does not exist: {session_file()}")
    run(control_command(command))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Configure and operate the managed dual TurtleBot3 mirror Recipe")
    parser.add_argument("command", choices=("configure", "build", "doctor", "start", "status", "stop"))
    parser.add_argument("--mujoco-root", type=Path, default=default_mujoco_root())
    parser.add_argument("--mbody-root", type=Path, default=default_mbody_root())
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
            control("status")
        else:
            control("terminate")
        return 0
    except (RecipeError, subprocess.CalledProcessError, OSError, json.JSONDecodeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
