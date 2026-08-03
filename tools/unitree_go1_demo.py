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

TOOLS_DIR = Path(__file__).absolute().parent
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

from recipe_portal import PortalCommand, PortalEnvironment, PortalLink, write_recipe_portal


RECIPE_ID = "unitree-go1-menagerie-mjcf-to-hakoniwa"
JOINT_ORDER = (
    "FR_hip", "FR_thigh", "FR_calf",
    "FL_hip", "FL_thigh", "FL_calf",
    "RR_hip", "RR_thigh", "RR_calf",
    "RL_hip", "RL_thigh", "RL_calf",
)
PROFILES = ("pose-bounce", "perturbation", "creep")


class RecipeError(RuntimeError):
    pass


@dataclass(frozen=True)
class RuntimePaths:
    system_name: str
    foundation_python: Path
    hako_cmd: Path
    endpoint_callback_library: Path
    step1_binary: Path
    plant_binary: Path
    pose_sender: Path
    perturbation_sender: Path
    creep_sender: Path


def root() -> Path:
    return Path(__file__).absolute().parents[1]


def default_source(name: str) -> Path:
    return root().parent / name


def recipe_file() -> Path:
    return root() / "recipes/examples" / f"{RECIPE_ID}.yaml"


def load_foundation_module():
    script = Path(__file__).with_name("foundation.py")
    spec = importlib.util.spec_from_file_location("business_pack_foundation_go1", script)
    if spec is None or spec.loader is None:
        raise RecipeError(f"cannot load Foundation helper: {script}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _absolute(path: Path) -> Path:
    return Path(os.path.abspath(path.expanduser()))


def _required(path: Path, label: str) -> Path:
    candidate = _absolute(path)
    if not candidate.exists():
        raise RecipeError(f"{label} not found: {candidate}")
    return candidate


def resolve_foundation_python(paths) -> Path:
    for candidate in (
        paths.foundation_python / "Scripts/python.exe",
        paths.foundation_python / "bin/python3",
        paths.foundation_python / "bin/python",
    ):
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
    return _absolute(next((item for item in candidates if item.is_file()), candidates[0]))


def resolve_runtime(paths, mujoco_root: Path) -> RuntimePaths:
    system_name = platform.system()
    suffix = ".exe" if system_name == "Windows" else ""
    binary_root = paths.recipe_root / "build/mujoco/examples/actuators/unitree_go1"
    example_root = mujoco_root / "examples/actuators/unitree_go1"
    return RuntimePaths(
        system_name=system_name,
        foundation_python=resolve_foundation_python(paths),
        hako_cmd=_absolute(paths.install_prefix / "bin" / f"hako-cmd{suffix}"),
        endpoint_callback_library=_callback_library(paths.install_prefix, system_name),
        step1_binary=_absolute(binary_root / f"unitree-go1-joint-io-example{suffix}"),
        plant_binary=_absolute(binary_root / f"unitree-go1-joint-hakoniwa-asset{suffix}"),
        pose_sender=_absolute(example_root / "pose_bounce_go1.py"),
        perturbation_sender=_absolute(example_root / "send_go1_joint_targets.py"),
        creep_sender=_absolute(example_root / "walk_go1.py"),
    )


def preflight(mujoco_root: Path):
    foundation = load_foundation_module()
    paths = foundation.resolve_workspace(root(), RECIPE_ID)
    inspection = foundation.inspect_foundation(recipe_file(), paths.install_prefix)
    if inspection["status"] != "SATISFIED":
        foundation.print_inspection(inspection, False)
        raise RecipeError("Foundation is not reusable; run tools/foundation.py plan/build first")
    return foundation, paths, resolve_runtime(paths, mujoco_root)


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def _stage_inputs(paths, mujoco_root: Path) -> None:
    source_model = _required(
        mujoco_root / "thirdparty/mujoco_menagerie/unitree_go1",
        "Vendored Unitree Go1 model",
    )
    shutil.copytree(
        source_model,
        paths.recipe_root / "assets/models/unitree_go1",
        dirs_exist_ok=True,
        ignore=shutil.ignore_patterns(".DS_Store"),
    )
    for relative in (
        "go1-joint-pdudef-compact.json",
        "go1-joint-pdutypes.json",
        "endpoint",
        "sensors/joint_state/go1-joint-states.json",
    ):
        source = _required(mujoco_root / "config" / relative, f"Go1 config {relative}")
        destination = paths.recipe_config / relative
        if source.is_dir():
            shutil.copytree(source, destination, dirs_exist_ok=True)
        else:
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, destination)


def write_build_manifest(paths, mujoco_root: Path) -> Path:
    build_dir = paths.recipe_root / "build/mujoco"
    relative = os.path.relpath(build_dir, mujoco_root)
    output = paths.recipe_config / "mujoco-build.yaml"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(f"version: 1\n\nbuild:\n  dir: {relative}\n", encoding="utf-8")
    return output


def _profile_sender(runtime: RuntimePaths, profile: str) -> tuple[Path, list[str]]:
    if profile == "pose-bounce":
        # One cycle is about 1.9 seconds. Keep the exhibition bounded while
        # leaving enough time for a human to open and inspect the viewer.
        return runtime.pose_sender, ["--cycles", "300"]
    if profile == "perturbation":
        return runtime.perturbation_sender, ["--duration-sec", "8", "--amplitude", "0.12"]
    if profile == "creep":
        return runtime.creep_sender, ["--profile", "creep", "--duration-sec", "10"]
    raise RecipeError(f"unsupported Go1 profile: {profile}")


def _runtime_inputs(paths) -> tuple[Path, Path, Path, Path]:
    return (
        paths.recipe_root / "assets/models/unitree_go1/scene.xml",
        paths.recipe_config / "sensors/joint_state/go1-joint-states.json",
        paths.recipe_config / "go1-joint-pdudef-compact.json",
        paths.recipe_config / "endpoint/go1_joint_endpoint.json",
    )


def write_launcher(paths, mujoco_root: Path, runtime: RuntimePaths, profile: str) -> Path:
    model, joint_state, pdu_def, endpoint = _runtime_inputs(paths)
    sender, sender_args = _profile_sender(runtime, profile)
    sender_args = [
        str(sender),
        "--endpoint-config", str(endpoint),
        "--pdu-def", str(pdu_def),
        *sender_args,
    ]
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
                    "HAKO_PDU_ENDPOINT_SHARED_LIB": str(runtime.endpoint_callback_library),
                    "HAKO_PROFILE_SERVICE_CLIENT": "0",
                    "PYTHONUNBUFFERED": "1",
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
                "name": "go1-plant",
                "activation_timing": "before_start",
                "command": str(runtime.plant_binary),
                "args": [str(model), str(joint_state), str(pdu_def), str(endpoint)],
                "cwd": str(paths.recipe_root),
                "delay_sec": 2,
            },
            {
                "name": f"go1-{profile}-sender",
                "activation_timing": "before_start",
                "command": str(runtime.foundation_python),
                "args": sender_args,
                "cwd": str(paths.recipe_root),
                "depends_on": ["go1-plant"],
                "delay_sec": 1,
            },
        ],
    }
    output = paths.recipe_config / f"launcher-{profile}.json"
    _write_json(output, launcher)
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
    env.update(
        {
            "HAKONIWA_CORE_ROOT": str(paths.install_prefix),
            "HAKONIWA_PDU_ENDPOINT_ROOT": str(paths.install_prefix),
            "HAKO_CONFIG_PATH": str(paths.foundation_config / "cpp_core_config.json"),
            "HAKO_PDU_ENDPOINT_SHARED_LIB": str(runtime.endpoint_callback_library),
            "PYTHON_CMD": str(runtime.foundation_python),
            "PYTHONUNBUFFERED": "1",
        }
    )
    env["PATH"] = os.pathsep.join([str(runtime.foundation_python.parent), str(paths.install_prefix / "bin"), env.get("PATH", "")])
    key = "PATH" if runtime.system_name == "Windows" else ("DYLD_LIBRARY_PATH" if runtime.system_name == "Darwin" else "LD_LIBRARY_PATH")
    env[key] = os.pathsep.join([str(paths.install_prefix / "lib"), env.get(key, "")])
    return env


def _run(command: list[str], *, cwd: Path | None = None, env: dict[str, str] | None = None) -> int:
    print("+", subprocess.list2cmdline(command))
    return subprocess.run(command, cwd=cwd, env=env, check=False).returncode


def _display(action: str) -> str:
    return f"python tools/unitree_go1_demo.py {action}"


def write_portal(paths, runtime: RuntimePaths) -> Path:
    return write_recipe_portal(
        paths.recipe_root / "index.html",
        recipe_id=RECIPE_ID,
        title="Hakoniwa Unitree Go1 Pose Bounce Demo",
        summary=(
            "MuJoCo MenagerieのGo1を箱庭時間と12関節PDUで動かします。"
            "メイン展示はjump-likeなPose Bounceであり、空中離脱を確認するまではverified jumpとは呼びません。"
        ),
        topology=("Pose sender", "Float64MultiArray", "Endpoint callback SHM", "Go1 Hakoniwa plant", "MuJoCo viewer", "JointState"),
        commands=(
            PortalCommand("Build", _display("build"), "Go1をRecipe専用build directoryへFoundationに対して構築します。"),
            PortalCommand("Doctor", _display("doctor"), "Foundation、モデル、ライセンス、生成物、Python runtimeを検査します。"),
            PortalCommand("Step 1", _display("step1"), "MuJoCo単体の12関節headless smokeを実行します。"),
            PortalCommand("Start Pose Bounce", _display("start"), "MuJoCo viewer付きPose Bounceをbackground sessionで開始します。"),
            PortalCommand("Status", _display("status"), "Launcher sessionを確認します。"),
            PortalCommand("Stop", _display("stop"), "Launcherの通常終了経路で停止します。"),
        ),
        links=(
            PortalLink("Pose Bounce logs", "logs/", "plantとsenderの観測ログ"),
            PortalLink("Pose Launcher", "config/launcher-pose-bounce.json", "メイン展示トポロジ"),
            PortalLink("Runtime session", "runtime/", "Launcher session state"),
            PortalLink("Vendored model", "assets/models/unitree_go1/", "RecipeへステージしたMJCF・mesh・LICENSE"),
        ),
        environment=(
            PortalEnvironment("Platform", runtime.system_name),
            PortalEnvironment("Recipe workspace", str(paths.recipe_root)),
            PortalEnvironment("Foundation install", str(paths.install_prefix)),
            PortalEnvironment("Foundation Python", str(runtime.foundation_python)),
            PortalEnvironment("Session", str(session_file(paths))),
            PortalEnvironment("Joint order", ", ".join(JOINT_ORDER)),
        ),
        agency_notes=(
            "実ロボットは対象外です。",
            "Pose Bounceは姿勢シーケンスです。全脚の非接触を測定するまでverified jumpとは表現しません。",
            "creepはopen-loop motion demoであり、walking controllerではありません。",
            "Stop後にTERMINATEDを確認してからWorkspaceを終了してください。",
        ),
    )


def configure(mujoco_root: Path) -> int:
    foundation, paths, runtime = preflight(mujoco_root)
    foundation.prepare_workspace(paths)
    (paths.recipe_root / "runtime").mkdir(parents=True, exist_ok=True)
    _stage_inputs(paths, mujoco_root)
    manifest = write_build_manifest(paths, mujoco_root)
    for profile in PROFILES:
        write_launcher(paths, mujoco_root, runtime, profile)
    portal = write_portal(paths, runtime)
    print(f"Recipe workspace: {paths.recipe_root}")
    print(f"Recipe portal   : {portal}")
    print(f"Build manifest  : {manifest}")
    print(f"Operator command: {_display('build')}")
    return 0


def build(mujoco_root: Path) -> int:
    _foundation, paths, runtime = preflight(mujoco_root)
    manifest = _required(paths.recipe_config / "mujoco-build.yaml", "Generated MuJoCo build manifest")
    env = runtime_environment(paths, runtime)
    hako = _required(mujoco_root / "tools/hako.py", "hakoniwa-mujoco-robots hako.py")
    for operation in ("doctor", "build"):
        command = [
            str(runtime.foundation_python),
            str(hako),
            operation,
            "--config",
            str(manifest),
        ]
        if operation == "build":
            # build.bash uses `set -u`; one explicit native CMake argument also
            # avoids the empty-array expansion bug in macOS Bash 3.2.
            command.extend(["--", "-DCMAKE_BUILD_TYPE=Release"])
        rc = _run(command, cwd=mujoco_root, env=env)
        if rc != 0:
            return rc
    return 0


def _probe_python(runtime: RuntimePaths, venv_root: Path) -> tuple[bool, str]:
    code = (
        "import json,pathlib,sys; import hakopy,hakoniwa_pdu,hakoniwa_pdu_endpoint; "
        "import hakoniwa_pdu.apps.launcher.hako_launcher; "
        "print(json.dumps({'prefix':str(pathlib.Path(sys.prefix).absolute())}))"
    )
    result = subprocess.run([str(runtime.foundation_python), "-c", code], capture_output=True, text=True, check=False)
    if result.returncode != 0:
        return False, result.stderr.strip() or "Foundation imports failed"
    try:
        prefix = _absolute(Path(json.loads(result.stdout.splitlines()[-1])["prefix"]))
        prefix.relative_to(_absolute(venv_root))
    except (KeyError, ValueError, IndexError, json.JSONDecodeError) as exc:
        return False, f"Python is not running inside Foundation venv: {exc}"
    return True, f"venv={prefix}"


def doctor(mujoco_root: Path) -> int:
    _foundation, paths, runtime = preflight(mujoco_root)
    python_ok, python_detail = _probe_python(runtime, paths.foundation_python)
    model, joint_state, pdu_def, endpoint = _runtime_inputs(paths)
    checks = (
        ("platform", runtime.system_name == "Darwin", f"{runtime.system_name} (current exhibition validation target is macOS)"),
        ("Foundation Python", python_ok, python_detail),
        ("hako-cmd", runtime.hako_cmd.is_file(), str(runtime.hako_cmd)),
        ("Endpoint core_callback", runtime.endpoint_callback_library.is_file(), str(runtime.endpoint_callback_library)),
        ("Go1 scene", model.is_file(), str(model)),
        ("Go1 LICENSE", (model.parent / "LICENSE").is_file(), str(model.parent / "LICENSE")),
        ("joint-state config", joint_state.is_file(), str(joint_state)),
        ("PDU definition", pdu_def.is_file(), str(pdu_def)),
        ("Endpoint config", endpoint.is_file(), str(endpoint)),
        ("Step 1 binary", runtime.step1_binary.is_file(), str(runtime.step1_binary)),
        ("Go1 plant", runtime.plant_binary.is_file(), str(runtime.plant_binary)),
        ("Pose sender", runtime.pose_sender.is_file(), str(runtime.pose_sender)),
        ("Recipe portal", (paths.recipe_root / "index.html").is_file(), str(paths.recipe_root / "index.html")),
    )
    failed = False
    for name, ok, detail in checks:
        print(f"[{'OK' if ok else 'NG'}] {name}: {detail}")
        failed = failed or not ok
    return 1 if failed else 0


def step1(mujoco_root: Path) -> int:
    _foundation, paths, runtime = preflight(mujoco_root)
    model = _required(_runtime_inputs(paths)[0], "Staged Go1 scene")
    command = [str(_required(runtime.step1_binary, "Step 1 binary")), "--check", str(model)]
    print("+", subprocess.list2cmdline(command))
    completed = subprocess.run(command, cwd=paths.recipe_root, env=runtime_environment(paths, runtime), text=True, capture_output=True, check=False)
    print(completed.stdout, end="")
    if completed.stderr:
        print(completed.stderr, end="", file=sys.stderr)
    expected = all(name in completed.stdout for name in JOINT_ORDER) and "check ok" in completed.stdout
    return 0 if completed.returncode == 0 and expected else 1


def _launcher_state(completed: subprocess.CompletedProcess[str]) -> str | None:
    if completed.returncode != 0:
        return None
    try:
        lines = [line for line in completed.stdout.splitlines() if line.strip()]
        return json.loads(lines[-1]).get("state")
    except (IndexError, json.JSONDecodeError, AttributeError):
        return None


def start(mujoco_root: Path, profile: str) -> int:
    _foundation, paths, runtime = preflight(mujoco_root)
    launcher = _required(paths.recipe_config / f"launcher-{profile}.json", f"Generated {profile} Launcher")
    session = session_file(paths)
    session.parent.mkdir(parents=True, exist_ok=True)
    env = runtime_environment(paths, runtime)
    rc = _run(launcher_start_command(runtime.foundation_python, launcher, session), env=env)
    if rc != 0:
        return rc
    status_command = launcher_control_command(runtime.foundation_python, "status", session)
    plant_log = paths.recipe_logs / "go1-plant.out"
    sender_log = paths.recipe_logs / f"go1-{profile}-sender.out"
    ready = False
    state = None
    for _ in range(30):
        completed = subprocess.run(status_command, env=env, text=True, capture_output=True, check=False)
        state = _launcher_state(completed)
        plant_text = plant_log.read_text(encoding="utf-8", errors="replace") if plant_log.is_file() else ""
        sender_text = sender_log.read_text(encoding="utf-8", errors="replace") if sender_log.is_file() else ""
        if state == "RUNNING" and "WAIT RUNNING" in plant_text and "callback started" in sender_text:
            ready = True
            break
        if state != "RUNNING":
            break
        time.sleep(0.5)
    if not ready:
        print(f"[NG] Go1 {profile} runtime is not ready; state={state}, logs={paths.recipe_logs}", file=sys.stderr)
        return 1
    print(f"Go1 {profile} Demo remains running in the background.")
    print(f"Next: {_display('status')} | {_display('stop')}")
    print(f"Session: {session}")
    print(f"Logs   : {paths.recipe_logs}")
    return 0


def control(command: str) -> int:
    foundation = load_foundation_module()
    paths = foundation.resolve_workspace(root(), RECIPE_ID)
    python = resolve_foundation_python(paths)
    return _run(launcher_control_command(python, command, session_file(paths)))


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description="Configure and operate the Unitree Go1 Recipe")
    result.add_argument("command", choices=["configure", "build", "doctor", "step1", "start", "status", "stop"])
    result.add_argument("--profile", choices=PROFILES, default="pose-bounce")
    result.add_argument("--mujoco-root", type=Path, default=default_source("hakoniwa-mujoco-robots"))
    return result


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        mujoco_root = _absolute(args.mujoco_root)
        if args.command == "configure":
            return configure(mujoco_root)
        if args.command == "build":
            return build(mujoco_root)
        if args.command == "doctor":
            return doctor(mujoco_root)
        if args.command == "step1":
            return step1(mujoco_root)
        if args.command == "start":
            return start(mujoco_root, args.profile)
        if args.command == "status":
            return control("status")
        return control("terminate")
    except RecipeError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
