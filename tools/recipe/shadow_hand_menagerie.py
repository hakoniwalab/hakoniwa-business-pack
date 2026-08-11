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


RECIPE_ID = "shadow-hand-menagerie-mjcf-to-hakoniwa"
BASE_SCRIPT = Path(__file__).with_name("mujoco_turtlebot3_mbody.py")
SPEC = importlib.util.spec_from_file_location("managed_mujoco_build_for_shadow_hand", BASE_SCRIPT)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError(f"cannot load managed MuJoCo build helper: {BASE_SCRIPT}")
base = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = base
SPEC.loader.exec_module(base)
base.RECIPE_ID = RECIPE_ID


def paths() -> dict[str, Path]:
    return base.paths()


def executable_path() -> Path:
    suffix = ".exe" if platform.system() == "Windows" else ""
    root = paths()["build"] / "examples/actuators/shadow_hand"
    candidates = (
        root / "Release" / f"shadow-hand-hakoniwa-asset{suffix}",
        root / f"shadow-hand-hakoniwa-asset{suffix}",
    )
    return next((candidate for candidate in candidates if candidate.is_file()), candidates[-1])


def inspect(args: argparse.Namespace) -> dict[str, object]:
    resolved = paths()
    prefix = resolved["prefix"]
    mbody = args.mbody_root.resolve()
    mujoco = args.mujoco_root.resolve()
    errors: list[str] = []
    foundation = base.load_foundation_module()
    inspection = foundation.inspect_foundation(base.recipe_file(), prefix)
    if inspection["status"] != "SATISFIED":
        errors.append(f"Foundation is {inspection['status']}; run tools/foundation.py plan/build")

    required = (
        (base.foundation_python(prefix), "Foundation Python"),
        (mbody / "tools/fetch.py", "MBody fetch tool"),
        (mbody / "sources/shadow_hand.yaml", "pinned Shadow Hand source definition"),
        (mujoco / "src/CMakeLists.txt", "MuJoCo Robots CMake source"),
        (mujoco / "examples/actuators/shadow_hand/send_shadow_hand_targets.py", "Shadow Hand sender"),
        (mujoco / "config/shadow-hand-pdudef-compact.json", "Shadow Hand PDU definition"),
        (mujoco / "config/endpoint/shadow_hand_endpoint.json", "Shadow Hand endpoint config"),
        (mujoco / "config/sensors/joint_state/shadow-hand-joint-states.json", "Shadow Hand JointState config"),
    )
    for path, label in required:
        base.require_file(path, label, errors)

    toolchain: dict[str, object] = {}
    if resolved["toolchain"].is_file():
        toolchain = json.loads(resolved["toolchain"].read_text(encoding="utf-8"))
    vcpkg_root = Path(str(toolchain["vcpkg_root"])) if toolchain.get("vcpkg_root") else None
    if platform.system() == "Windows":
        if vcpkg_root is None:
            errors.append(
                "Windows Foundation toolchain is not selected; run tools/foundation.py toolchain "
                f"--recipe-id {RECIPE_ID} --vcpkg-root <path>"
            )
        else:
            base.require_file(
                vcpkg_root / "scripts/buildsystems/vcpkg.cmake", "vcpkg CMake toolchain", errors
            )
            base.require_file(
                vcpkg_root / "installed/x64-windows/share/glfw3/glfw3Config.cmake",
                "GLFW CMake package required by the Shadow Hand target",
                errors,
            )
        for variant in ("core_callback", "core_polling"):
            base.require_file(
                prefix / "lib" / f"hakoniwa_pdu_endpoint_{variant}.lib",
                f"PDU Endpoint {variant} import library",
                errors,
            )

    return {
        "status": "READY" if not errors else "BLOCKED",
        "platform": platform.system(),
        "errors": errors,
        "workspace": {key: str(value) for key, value in resolved.items()},
        "sources": {"mbody": str(mbody), "mujoco": str(mujoco)},
        "vcpkg_root": str(vcpkg_root) if vcpkg_root else None,
    }


def require_ready(args: argparse.Namespace) -> dict[str, object]:
    state = inspect(args)
    if state["status"] != "READY":
        raise base.RecipeError("\n".join(state["errors"]))
    return state


def stage_inputs(args: argparse.Namespace) -> dict[str, Path]:
    resolved = paths()
    mbody = args.mbody_root.resolve()
    mujoco = args.mujoco_root.resolve()
    source_root = resolved["assets"] / "menagerie"
    model = source_root / "shadow_hand/scene_right.xml"
    license_file = source_root / "shadow_hand/LICENSE"
    if not model.is_file() or not license_file.is_file():
        base.run([
            str(base.foundation_python(resolved["prefix"])),
            str(mbody / "tools/fetch.py"),
            str(mbody / "sources/shadow_hand.yaml"),
            "--output-dir", str(source_root),
        ], cwd=mbody, check=True)
    if not model.is_file():
        raise base.RecipeError(f"materialized Shadow Hand scene is missing: {model}")
    if not license_file.is_file():
        raise base.RecipeError(f"materialized Shadow Hand license is missing: {license_file}")

    runtime_input = resolved["assets"] / "runtime-input"
    shutil.copytree(mujoco / "config", runtime_input / "config", dirs_exist_ok=True)
    python_dir = runtime_input / "python"
    python_dir.mkdir(parents=True, exist_ok=True)
    sender = python_dir / "send_shadow_hand_targets.py"
    shutil.copy2(mujoco / "examples/actuators/shadow_hand/send_shadow_hand_targets.py", sender)
    return {
        "model": model,
        "license": license_file,
        "config": runtime_input / "config",
        "sender": sender,
    }


def cmake_command(args: argparse.Namespace, state: dict[str, object]) -> list[str]:
    proxy = argparse.Namespace(mujoco_root=args.mujoco_root)
    return base.cmake_configure_command(proxy, state)


def write_launcher(args: argparse.Namespace, staged: dict[str, Path]) -> Path:
    resolved = paths()
    prefix = resolved["prefix"]
    python = base.foundation_python(prefix)
    config = staged["config"]
    loader = "PATH" if platform.system() == "Windows" else (
        "DYLD_LIBRARY_PATH" if platform.system() == "Darwin" else "LD_LIBRARY_PATH"
    )
    asset_args = [] if args.viewer else ["--no-viewer"]
    asset_args.extend([
        str(staged["model"]),
        str(config / "sensors/joint_state/shadow-hand-joint-states.json"),
        str(config / "shadow-hand-pdudef-compact.json"),
        str(config / "endpoint/shadow_hand_endpoint.json"),
    ])
    launcher = {
        "version": "0.1",
        "defaults": {
            "cwd": str(resolved["assets"] / "runtime-input"),
            "stdout": str(resolved["logs"] / "${asset}.out"),
            "stderr": str(resolved["logs"] / "${asset}.err"),
            "env": {
                "set": {
                    "HAKO_CONFIG_PATH": str(resolved["foundation"] / "config/cpp_core_config.json"),
                    "HAKONIWA_CORE_ROOT": str(prefix),
                    "HAKONIWA_PDU_ENDPOINT_ROOT": str(prefix),
                    "PYTHONUNBUFFERED": "1",
                },
                "prepend": {
                    "PATH": [str(python.parent), str(prefix / "bin")],
                    loader: [str(prefix / "lib")],
                },
            },
            "start_grace_sec": 2,
            "delay_sec": 1,
        },
        "assets": [
            {
                "name": "shadow_hand",
                "activation_timing": "before_start",
                "command": str(executable_path()),
                "args": asset_args,
                "start_grace_sec": 3,
            },
            {
                "name": "shadow_hand_sender",
                "activation_timing": "before_start",
                "command": str(python),
                "args": [
                    str(staged["sender"]),
                    "--endpoint-config", str(config / "endpoint/shadow_hand_endpoint.json"),
                    "--pdu-def", str(config / "shadow-hand-pdudef-compact.json"),
                    "--duration-sec", str(args.duration_sec),
                ],
                "start_grace_sec": 2,
            },
        ],
    }
    return base.write_json(resolved["config"] / "launcher.json", launcher)


def configure(args: argparse.Namespace) -> None:
    state = require_ready(args)
    for name in ("build", "deps", "config", "logs", "validation", "runtime", "assets"):
        paths()[name].mkdir(parents=True, exist_ok=True)
    base.install_mbody_tool_dependencies()
    staged = stage_inputs(args)
    base.write_json(paths()["config"] / "resolved.json", state)
    write_launcher(args, staged)
    base.run(cmake_command(args, state), check=True)
    print(f"Recipe workspace: {paths()['recipe']}")
    print(f"Shadow Hand license: {staged['license']}")


def build(args: argparse.Namespace) -> None:
    require_ready(args)
    if not (paths()["build"] / "CMakeCache.txt").is_file():
        configure(args)
    command = ["cmake", "--build", str(paths()["build"])]
    if platform.system() == "Windows":
        command.extend(["--config", "Release"])
    command.extend(["--target", "shadow-hand-hakoniwa-asset", "--parallel"])
    base.run(command, check=True)
    if not executable_path().is_file():
        raise base.RecipeError(f"Shadow Hand executable was not produced: {executable_path()}")
    write_launcher(args, stage_inputs(args))
    print(f"Shadow Hand executable: {executable_path()}")


def start(args: argparse.Namespace) -> None:
    require_ready(args)
    if not executable_path().is_file():
        raise base.RecipeError("Shadow Hand is not built; run build first")
    staged = stage_inputs(args)
    launcher = write_launcher(args, staged)
    session = base.session_file()
    session.parent.mkdir(parents=True, exist_ok=True)
    python = base.foundation_python(paths()["prefix"])
    base.run([
        str(python), "-m", "hakoniwa_pdu.apps.launcher.hako_launcher",
        str(launcher), "--mode", "immediate", "--background", str(session),
    ], check=True)
    base.run(base.control_command("status"), check=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Operate the managed Shadow Hand Menagerie Recipe")
    parser.add_argument("command", choices=("configure", "build", "doctor", "start", "status", "stop"))
    parser.add_argument("--mbody-root", type=Path, default=base.default_source("hakoniwa-mbody-registry"))
    parser.add_argument("--mujoco-root", type=Path, default=base.default_source("hakoniwa-mujoco-robots"))
    parser.add_argument("--duration-sec", type=float, default=5.0)
    parser.add_argument("--viewer", action="store_true")
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
