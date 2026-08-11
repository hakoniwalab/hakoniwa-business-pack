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
import time
from dataclasses import dataclass
from pathlib import Path


RECIPE_ID = "agilex-tracer-hakoniwa-runtime"
TOOLS_DIR = Path(__file__).resolve().parents[1]


class RecipeError(RuntimeError):
    pass


@dataclass(frozen=True)
class Runtime:
    system_name: str
    python: Path
    hako_cmd: Path
    endpoint_library: Path
    check_binary: Path
    plant_binary: Path
    sender: Path


def root() -> Path:
    return Path(__file__).resolve().parents[2]


def default_source(name: str) -> Path:
    return root().parent / name


def recipe_file() -> Path:
    return root() / "recipes" / "examples" / f"{RECIPE_ID}.yaml"


def load_foundation_module():
    script = TOOLS_DIR / "foundation.py"
    spec = importlib.util.spec_from_file_location("business_pack_foundation_agilex", script)
    if spec is None or spec.loader is None:
        raise RecipeError(f"cannot load Foundation helper: {script}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def absolute(path: Path) -> Path:
    return Path(os.path.abspath(path.expanduser()))


def required(path: Path, label: str) -> Path:
    candidate = absolute(path)
    if not candidate.exists():
        raise RecipeError(f"{label} not found: {candidate}")
    return candidate


def foundation_python(workspace) -> Path:
    for candidate in (
        workspace.foundation_python / "Scripts" / "python.exe",
        workspace.foundation_python / "bin" / "python3",
        workspace.foundation_python / "bin" / "python",
    ):
        if candidate.is_file():
            return absolute(candidate)
    raise RecipeError(f"Foundation Python not found under {workspace.foundation_python}")


def endpoint_library(prefix: Path, system_name: str) -> Path:
    name = {
        "Darwin": "libhakoniwa_pdu_endpoint_core_callback.dylib",
        "Windows": "hakoniwa_pdu_endpoint_core_callback.dll",
    }.get(system_name, "libhakoniwa_pdu_endpoint_core_callback.so")
    candidates = (prefix / "lib" / name, prefix / "bin" / name)
    return absolute(next((path for path in candidates if path.is_file()), candidates[0]))


def resolve_runtime(workspace, mujoco_root: Path) -> Runtime:
    system_name = platform.system()
    suffix = ".exe" if system_name == "Windows" else ""
    binary_root = workspace.recipe_root / "build" / "mujoco" / "examples" / "actuators" / "agilex_tracer"
    return Runtime(
        system_name=system_name,
        python=foundation_python(workspace),
        hako_cmd=absolute(workspace.install_prefix / "bin" / f"hako-cmd{suffix}"),
        endpoint_library=endpoint_library(workspace.install_prefix, system_name),
        check_binary=absolute(binary_root / f"agilex-tracer-rover-example{suffix}"),
        plant_binary=absolute(binary_root / f"rover-twist-hakoniwa-asset{suffix}"),
        sender=absolute(workspace.recipe_root / "assets" / "scripts" / "send_rover_twist.py"),
    )


def preflight(mujoco_root: Path):
    foundation = load_foundation_module()
    workspace = foundation.resolve_workspace(root(), RECIPE_ID)
    inspection = foundation.inspect_foundation(recipe_file(), workspace.install_prefix)
    if inspection["status"] != "SATISFIED":
        foundation.print_inspection(inspection, False)
        raise RecipeError("Foundation is not reusable; run tools/foundation.py plan/build first")
    return foundation, workspace, resolve_runtime(workspace, mujoco_root)


def copy_path(source: Path, destination: Path) -> None:
    source = required(source, destination.name)
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, destination)


def stage_inputs(workspace, mbody_root: Path, mujoco_root: Path) -> None:
    generated = required(
        mbody_root / "bodies" / "agilex_tracer" / "generated",
        "AgileX generated MBody artifacts",
    )
    shutil.copytree(
        generated,
        workspace.recipe_root / "assets" / "models" / "agilex_tracer" / "generated",
        dirs_exist_ok=True,
    )
    copy_path(
        mujoco_root / "examples" / "actuators" / "agilex_tracer" / "send_rover_twist.py",
        workspace.recipe_root / "assets" / "scripts" / "send_rover_twist.py",
    )
    for relative in (
        "actuator/joint/agilex_tracer_left_wheel.json",
        "actuator/joint/agilex_tracer_right_wheel.json",
        "rover-twist-pdudef-compact.json",
        "rover-twist-pdutypes.json",
        "endpoint/rover_twist_endpoint.json",
        "endpoint/cache/buffer.json",
        "endpoint/comm/shm_rover_twist_comm.json",
    ):
        copy_path(mujoco_root / "config" / relative, workspace.recipe_config / relative)


def write_build_manifest(workspace, mujoco_root: Path) -> Path:
    build_dir = workspace.recipe_root / "build" / "mujoco"
    output = workspace.recipe_config / "mujoco-build.yaml"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        f"version: 1\n\nbuild:\n  dir: {os.path.relpath(build_dir, mujoco_root)}\n",
        encoding="utf-8",
    )
    return output


def native_build_args(workspace, runtime: Runtime) -> list[str]:
    if runtime.system_name != "Windows":
        return ["-DCMAKE_BUILD_TYPE=Release"]

    toolchain_path = workspace.foundation_config / "toolchain.json"
    toolchain = json.loads(required(toolchain_path, "Foundation toolchain").read_text(encoding="utf-8"))
    vcpkg_value = toolchain.get("vcpkg_root")
    if not isinstance(vcpkg_value, str) or not vcpkg_value.strip():
        raise RecipeError(f"Windows Foundation toolchain has no vcpkg_root: {toolchain_path}")
    vcpkg = required(Path(vcpkg_value), "vcpkg root")
    cmake_toolchain = required(vcpkg / "scripts/buildsystems/vcpkg.cmake", "vcpkg CMake toolchain")
    installed = required(vcpkg / "installed/x64-windows", "vcpkg x64-windows prefix")
    return [
        "-HakoniwaCoreRoot", str(workspace.install_prefix),
        "-HakoniwaPduEndpointRoot", str(workspace.install_prefix),
        "-ToolchainFile", str(cmake_toolchain),
        "-ExtraPrefixPaths", str(installed),
        "-Configuration", "Release",
    ]


def runtime_inputs(workspace) -> tuple[Path, Path, Path, Path, Path, Path, Path, Path]:
    return (
        workspace.recipe_root / "assets/models/agilex_tracer/generated/tracer_v1.minimal_world.xml",
        workspace.recipe_config / "actuator/joint/agilex_tracer_left_wheel.json",
        workspace.recipe_config / "actuator/joint/agilex_tracer_right_wheel.json",
        workspace.recipe_config / "rover-twist-pdudef-compact.json",
        workspace.recipe_config / "rover-twist-pdutypes.json",
        workspace.recipe_config / "endpoint/rover_twist_endpoint.json",
        workspace.recipe_config / "endpoint/cache/buffer.json",
        workspace.recipe_config / "endpoint/comm/shm_rover_twist_comm.json",
    )


def write_json(path: Path, payload: object) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)
    return path


def write_launcher(workspace, runtime: Runtime, *, headless: bool) -> Path:
    model, left, right, pdu_def, _pdu_types, endpoint, _cache, _comm = runtime_inputs(workspace)
    launcher = {
        "version": "0.1",
        "defaults": {
            "cwd": str(workspace.recipe_root),
            "stdout": str(workspace.recipe_logs / "${asset}.out"),
            "stderr": str(workspace.recipe_logs / "${asset}.err"),
            "start_grace_sec": 1,
            "delay_sec": 1,
            "env": {
                "set": {
                    "HAKO_CONFIG_PATH": str(workspace.foundation_config / "cpp_core_config.json"),
                    "HAKO_PDU_ENDPOINT_SHARED_LIB": str(runtime.endpoint_library),
                    "PYTHONUNBUFFERED": "1",
                },
                "prepend": {
                    "lib_path": [str(workspace.install_prefix / "lib")],
                    "PATH": [str(runtime.python.parent), str(workspace.install_prefix / "bin")],
                },
            },
        },
        "assets": [
            {
                "name": "rover-twist-plant",
                "activation_timing": "before_start",
                "command": str(runtime.plant_binary),
                "args": [
                    *(["--no-viewer"] if headless else []),
                    str(model), str(left), str(right), str(pdu_def), str(endpoint),
                ],
                "delay_sec": 2,
            },
            {
                "name": "rover-twist-sender",
                "activation_timing": "before_start",
                "command": str(runtime.python),
                "args": [
                    str(runtime.sender),
                    "--endpoint-config", str(endpoint),
                    "--pdu-def", str(pdu_def),
                    "--linear-x", "0.2",
                    "--duration-sec", "3",
                ],
                "depends_on": ["rover-twist-plant"],
                "delay_sec": 1,
            },
        ],
    }
    return write_json(workspace.recipe_config / "launcher.json", launcher)


def session_file(workspace) -> Path:
    return workspace.recipe_root / "runtime" / "launcher-session.json"


def launcher_command(runtime: Runtime, command: str, workspace) -> list[str]:
    session = session_file(workspace)
    if command == "start":
        return [
            str(runtime.python), "-m", "hakoniwa_pdu.apps.launcher.hako_launcher",
            str(workspace.recipe_config / "launcher.json"), "--background", str(session),
        ]
    return [
        str(runtime.python), "-m", "hakoniwa_pdu.apps.launcher.hako_launcher_ctl",
        command, str(session),
    ]


def environment(workspace, runtime: Runtime) -> dict[str, str]:
    env = os.environ.copy()
    env.update({
        "HAKONIWA_CORE_ROOT": str(workspace.install_prefix),
        "HAKONIWA_PDU_ENDPOINT_ROOT": str(workspace.install_prefix),
        "HAKO_CONFIG_PATH": str(workspace.foundation_config / "cpp_core_config.json"),
        "HAKO_PDU_ENDPOINT_SHARED_LIB": str(runtime.endpoint_library),
        "PYTHON_CMD": str(runtime.python),
        "PYTHONUNBUFFERED": "1",
    })
    env["PATH"] = os.pathsep.join([str(runtime.python.parent), str(workspace.install_prefix / "bin"), env.get("PATH", "")])
    key = "PATH" if runtime.system_name == "Windows" else ("DYLD_LIBRARY_PATH" if runtime.system_name == "Darwin" else "LD_LIBRARY_PATH")
    env[key] = os.pathsep.join([str(workspace.install_prefix / "lib"), env.get(key, "")])
    return env


def run(command: list[str], *, cwd: Path | None = None, env: dict[str, str] | None = None) -> int:
    print("+", subprocess.list2cmdline(command), flush=True)
    return subprocess.run(command, cwd=cwd, env=env, check=False).returncode


def configure(mbody_root: Path, mujoco_root: Path, *, headless: bool) -> int:
    foundation, workspace, runtime = preflight(mujoco_root)
    foundation.prepare_workspace(workspace)
    stage_inputs(workspace, mbody_root, mujoco_root)
    manifest = write_build_manifest(workspace, mujoco_root)
    launcher = write_launcher(workspace, runtime, headless=headless)
    print(f"Recipe workspace: {workspace.recipe_root}")
    print(f"Build manifest : {manifest}")
    print(f"Launcher       : {launcher}")
    return 0


def build(mujoco_root: Path) -> int:
    _foundation, workspace, runtime = preflight(mujoco_root)
    manifest = required(workspace.recipe_config / "mujoco-build.yaml", "generated build manifest")
    hako = required(mujoco_root / "tools" / "hako.py", "hakoniwa-mujoco-robots hako.py")
    env = environment(workspace, runtime)
    native_args = native_build_args(workspace, runtime)
    for operation in ("doctor", "build"):
        command = [str(runtime.python), str(hako), operation, "--config", str(manifest)]
        if runtime.system_name == "Windows" or operation == "build":
            command.extend(["--", *native_args])
        rc = run(command, cwd=mujoco_root, env=env)
        if rc != 0:
            return rc
    return 0


def probe_python(runtime: Runtime, venv_root: Path) -> tuple[bool, str]:
    code = (
        "import json,pathlib,sys,sysconfig; import hakopy,hakoniwa_pdu,hakoniwa_pdu_endpoint; "
        "print(json.dumps({'prefix':str(pathlib.Path(sys.prefix).absolute()),'soabi':sysconfig.get_config_var('SOABI')}))"
    )
    result = subprocess.run([str(runtime.python), "-c", code], capture_output=True, text=True, check=False)
    if result.returncode != 0:
        return False, result.stderr.strip() or "Foundation imports failed"
    try:
        evidence = json.loads(result.stdout.splitlines()[-1])
        absolute(Path(evidence["prefix"])).relative_to(absolute(venv_root))
        if not evidence.get("soabi"):
            return False, "Foundation Python has no SOABI"
    except (KeyError, ValueError, IndexError, json.JSONDecodeError) as exc:
        return False, f"invalid Foundation Python evidence: {exc}"
    return True, f"venv={evidence['prefix']} SOABI={evidence['soabi']}"


def doctor(mbody_root: Path, mujoco_root: Path) -> int:
    _foundation, workspace, runtime = preflight(mujoco_root)
    python_ok, python_detail = probe_python(runtime, workspace.foundation_python)
    model, left, right, pdu_def, pdu_types, endpoint, cache, comm = runtime_inputs(workspace)
    checks = (
        ("Foundation Python/SOABI", python_ok, python_detail),
        ("hako-cmd", runtime.hako_cmd.is_file(), str(runtime.hako_cmd)),
        ("Endpoint core_callback", runtime.endpoint_library.is_file(), str(runtime.endpoint_library)),
        ("generated Tracer model", model.is_file(), str(model)),
        ("left wheel config", left.is_file(), str(left)),
        ("right wheel config", right.is_file(), str(right)),
        ("PDU definition", pdu_def.is_file(), str(pdu_def)),
        ("PDU type definition", pdu_types.is_file(), str(pdu_types)),
        ("Endpoint config", endpoint.is_file(), str(endpoint)),
        ("Endpoint cache config", cache.is_file(), str(cache)),
        ("Endpoint communication config", comm.is_file(), str(comm)),
        ("MuJoCo-only check", runtime.check_binary.is_file(), str(runtime.check_binary)),
        ("Hakoniwa plant", runtime.plant_binary.is_file(), str(runtime.plant_binary)),
        ("Twist sender", runtime.sender.is_file(), str(runtime.sender)),
        ("MBody source", (mbody_root / "sources/agilex_tracer.yaml").is_file(), str(mbody_root)),
    )
    failed = False
    for name, ok, detail in checks:
        print(f"[{'OK' if ok else 'NG'}] {name}: {detail}")
        failed = failed or not ok
    return 1 if failed else 0


def step1(mujoco_root: Path) -> int:
    _foundation, workspace, runtime = preflight(mujoco_root)
    model, left, right, _pdu_def, _pdu_types, _endpoint, _cache, _comm = runtime_inputs(workspace)
    command = [str(required(runtime.check_binary, "MuJoCo-only check")), "--check", str(model), str(left), str(right)]
    return run(command, cwd=workspace.recipe_root, env=environment(workspace, runtime))


def parse_state(completed: subprocess.CompletedProcess[str]) -> str | None:
    if completed.returncode != 0:
        return None
    try:
        return json.loads([line for line in completed.stdout.splitlines() if line.strip()][-1]).get("state")
    except (IndexError, json.JSONDecodeError, AttributeError):
        return None


def start(mujoco_root: Path) -> int:
    _foundation, workspace, runtime = preflight(mujoco_root)
    required(workspace.recipe_config / "launcher.json", "generated Launcher")
    required(runtime.plant_binary, "Hakoniwa plant")
    session_file(workspace).parent.mkdir(parents=True, exist_ok=True)
    env = environment(workspace, runtime)
    rc = run(launcher_command(runtime, "start", workspace), env=env)
    if rc != 0:
        return rc
    status = launcher_command(runtime, "status", workspace)
    plant_log = workspace.recipe_logs / "rover-twist-plant.out"
    sender_log = workspace.recipe_logs / "rover-twist-sender.out"
    state = None
    for _ in range(30):
        completed = subprocess.run(status, env=env, text=True, capture_output=True, check=False)
        state = parse_state(completed)
        plant = plant_log.read_text(encoding="utf-8", errors="replace") if plant_log.is_file() else ""
        sender = sender_log.read_text(encoding="utf-8", errors="replace") if sender_log.is_file() else ""
        if state == "RUNNING" and "WAIT RUNNING" in plant and "callback started" in sender:
            print("AgileX Tracer Demo remains running in the background.")
            print("Next: status | inspect viewer/logs | stop")
            print(f"Session: {session_file(workspace)}")
            print(f"Logs   : {workspace.recipe_logs}")
            return 0
        if state != "RUNNING":
            break
        time.sleep(0.5)
    print(f"[NG] AgileX runtime is not ready; state={state}, logs={workspace.recipe_logs}", file=sys.stderr)
    return 1


def control(command: str, mujoco_root: Path) -> int:
    _foundation, workspace, runtime = preflight(mujoco_root)
    return run(launcher_command(runtime, command, workspace), env=environment(workspace, runtime))


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description="Configure and operate the AgileX Tracer Hakoniwa Recipe")
    result.add_argument("command", choices=["configure", "build", "doctor", "step1", "start", "status", "stop"])
    result.add_argument("--headless", action="store_true")
    result.add_argument("--mbody-root", type=Path, default=default_source("hakoniwa-mbody-registry"))
    result.add_argument("--mujoco-root", type=Path, default=default_source("hakoniwa-mujoco-robots"))
    return result


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        mbody_root = absolute(args.mbody_root)
        mujoco_root = absolute(args.mujoco_root)
        if args.command == "configure":
            return configure(mbody_root, mujoco_root, headless=args.headless)
        if args.command == "build":
            return build(mujoco_root)
        if args.command == "doctor":
            return doctor(mbody_root, mujoco_root)
        if args.command == "step1":
            return step1(mujoco_root)
        if args.command == "start":
            return start(mujoco_root)
        return control("status" if args.command == "status" else "terminate", mujoco_root)
    except (RecipeError, OSError, json.JSONDecodeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
