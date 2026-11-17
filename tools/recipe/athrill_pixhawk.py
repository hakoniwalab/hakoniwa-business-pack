#!/usr/bin/env python3
"""Operate the Business Pack Athrill Pixhawk validation Recipes."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import urllib.request
import urllib.error


ROOT = Path(__file__).resolve().parents[2]
VIEWER_URL = (
    "http://127.0.0.1:8000/index.html?"
    "viewerConfigPath=/config/viewer-config-disturb-x500.json"
)

PROFILES = {
    "ardupilot": {
        "recipe_id": "athrill-pixhawk-ardupilot-dos-viewer",
        "recipe": ROOT / "recipes/examples/athrill-pixhawk-ardupilot-dos-viewer.yaml",
        "run": "examples/ardupilot-hakoniwa/run.sh",
        "firmware": "ARDUPILOT_ELF",
        "firmware_sha256": "cfa1b3f04f326ab91760c53b93503b669d4d62e2d43dd1ffc4d09cb15198eb2c",
        "extra": ROOT / "recipes/config/athrill-pixhawk/ardupilot-dos.txt",
        "scenario": "examples/ardupilot-hakoniwa/fly_and_attack.py",
    },
    "px4": {
        "recipe_id": "athrill-pixhawk-px4-motor-fault-viewer",
        "recipe": ROOT / "recipes/examples/athrill-pixhawk-px4-motor-fault-viewer.yaml",
        "run": "examples/px4-hakoniwa-dshot/run.sh",
        "firmware": "PX4_ELF",
        "firmware_sha256": "f8a78a13f3a545305cce45eeef677e4d436325ee6a6065b1753c387283f3d05e",
        "firmware_url": "https://github.com/hakoniwalab/athrill-target-stm32/releases/download/v0.3.0/px4_fmu-v5_default.elf",
        "extra": ROOT / "recipes/config/athrill-pixhawk/px4-motor-fault.txt",
        "scenario": "examples/px4-hakoniwa-dshot/fly-motor-fault.py",
    },
}


class RecipeError(RuntimeError):
    pass


def enter_workspace_if_needed() -> int | None:
    """Re-run this command through the managed non-interactive Workspace."""
    if os.environ.get("HAKONIWA_WORKSPACE_ACTIVE") == "1":
        return None
    command = [
        sys.executable,
        str(ROOT / "tools/workspace.py"),
        "run",
        "--",
        sys.executable,
        str(Path(__file__).resolve()),
        *sys.argv[1:],
    ]
    return run(command)


def workspace(profile: dict) -> Path:
    return ROOT / "work/recipes" / profile["recipe_id"]


def environment_file(profile: dict) -> Path:
    return workspace(profile) / "environment.json"


def launcher_file(profile: dict) -> Path:
    return workspace(profile) / "config/launcher.json"


def session_file(profile: dict) -> Path:
    return workspace(profile) / "runtime/launcher-session.json"


def run(command: list[str], *, env: dict[str, str] | None = None) -> int:
    print(">", subprocess.list2cmdline(command), flush=True)
    return subprocess.run(command, cwd=ROOT, env=env, check=False).returncode


def generic(command: str, profile: dict) -> int:
    return run(
        [
            sys.executable,
            str(ROOT / "tools/recipe.py"),
            command,
            "--recipe",
            str(profile["recipe"]),
        ]
    )


def load_variables(profile: dict) -> dict[str, str]:
    path = environment_file(profile)
    if not path.is_file():
        raise RecipeError(f"Recipe environment is missing: {path}; run configure first")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RecipeError(f"Recipe environment is invalid: {path}: {exc}") from exc
    variables = payload.get("variables")
    if not isinstance(variables, dict) or not all(
        isinstance(key, str) and isinstance(value, str)
        for key, value in variables.items()
    ):
        raise RecipeError(f"Recipe environment has no string variables: {path}")
    return variables


def foundation_python(variables: dict[str, str]) -> str:
    python = variables.get("HAKO_FOUNDATION_PYTHON")
    if not python or not Path(python).is_file():
        raise RecipeError("Foundation Python is missing; run configure first")
    return python


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def fetch(profile: dict) -> int:
    url = profile.get("firmware_url")
    if not url:
        raise RecipeError(
            "This profile requires separately supplied firmware; set its firmware root before configure"
        )
    destination = ROOT / "work/inputs/px4-v1.17.0/px4_fmu-v5_default.elf"
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.is_file() and sha256(destination) == profile["firmware_sha256"]:
        print(f"[OK] pinned firmware already exists: {destination}")
        return 0
    temporary = destination.with_suffix(".elf.download")
    print(f"> download {url}", flush=True)
    try:
        try:
            urllib.request.urlretrieve(url, temporary)
        except (OSError, urllib.error.URLError) as exc:
            raise RecipeError(f"Firmware download failed: {url}: {exc}") from exc
        actual = sha256(temporary)
        if actual != profile["firmware_sha256"]:
            raise RecipeError(
                f"Downloaded firmware SHA-256 mismatch: {actual} != {profile['firmware_sha256']}"
            )
        os.replace(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)
    print(f"[OK] pinned firmware: {destination}")
    return 0


def prepare(profile: dict) -> int:
    variables = load_variables(profile)
    target = Path(variables["ATHRILL_TARGET_ROOT"])
    device = Path(variables["ATHRILL_DEVICE_ROOT"])
    recipe_work = workspace(profile)
    state = recipe_work / "state"
    state.mkdir(parents=True, exist_ok=True)
    (recipe_work / "logs").mkdir(parents=True, exist_ok=True)
    env = os.environ.copy()
    env.update(variables)
    env.update(
        {
            "ATHRILL_DEVICE_DIR": str(device),
            "ATHRILL_WORK_DIR": str(state),
            "ATHRILL_PREPARE_ONLY": "1",
            "PYTHON": foundation_python(variables),
        }
    )
    # Preparation uses the deterministic bench sensor source and must not join
    # an active Hakoniwa runtime.
    for name in ("HAKO_DRONE_ROOT", "HAKO_AIRCRAFT_CONFIG_PATH", "ATHRILL_OPTIONS"):
        env.pop(name, None)
    firmware = Path(variables[profile["firmware"]])
    actual_sha256 = sha256(firmware)
    if actual_sha256 != profile["firmware_sha256"]:
        raise RecipeError(
            f"Firmware SHA-256 mismatch: {firmware}: {actual_sha256} != "
            f"{profile['firmware_sha256']}"
        )
    command = [
        str(target / profile["run"]),
        str(firmware),
        str(target / "build-hakoniwa/athrill3"),
        str(device / "build-stm32-inproc/libhako_sensor_device_inproc_motor.dylib"),
        str(profile["extra"]),
    ]
    return run(command, env=env)


def start(profile: dict) -> int:
    variables = load_variables(profile)
    state = workspace(profile) / "state"
    missing = [name for name in ("fram.bin", "sd.img") if not (state / name).is_file()]
    if missing:
        raise RecipeError(
            "Prepared firmware state is missing ("
            + ", ".join(missing)
            + "); run prepare before start"
        )
    if generic("doctor", profile) != 0:
        return 1
    for directory in (workspace(profile) / "logs", session_file(profile).parent):
        directory.mkdir(parents=True, exist_ok=True)
    command = [
        foundation_python(variables),
        "-m",
        "hakoniwa_pdu.apps.launcher.hako_launcher",
        str(launcher_file(profile)),
        "--mode",
        "immediate",
        "--background",
        str(session_file(profile)),
    ]
    rc = run(command, env={**os.environ, **variables})
    if rc == 0:
        print(f"Viewer : {VIEWER_URL}")
        print("Press Connect in the browser before running scenario.")
        print(f"Session: {session_file(profile)}")
    return rc


def control(profile: dict, command: str) -> int:
    variables = load_variables(profile)
    session = session_file(profile)
    if not session.is_file():
        raise RecipeError(f"Launcher session is missing: {session}")
    return run(
        [
            foundation_python(variables),
            "-m",
            "hakoniwa_pdu.apps.launcher.hako_launcher_ctl",
            command,
            str(session),
        ],
        env={**os.environ, **variables},
    )


def scenario(profile: dict, extra_args: list[str]) -> int:
    if control(profile, "status") != 0:
        return 1
    variables = load_variables(profile)
    target = Path(variables["ATHRILL_TARGET_ROOT"])
    command = [
        foundation_python(variables),
        str(target / profile["scenario"]),
    ]
    if profile["recipe_id"].endswith("ardupilot-dos-viewer"):
        command.extend(
            ["--watchdog-log", str(workspace(profile) / "logs/athrill.err")]
        )
    command.extend(extra_args)
    return run(command, env={**os.environ, **variables})


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Prepare and operate Athrill Pixhawk validation Recipes"
    )
    parser.add_argument(
        "command",
        choices=("fetch", "configure", "doctor", "prepare", "start", "status", "scenario", "stop", "url"),
    )
    parser.add_argument("profile", choices=tuple(PROFILES))
    parser.add_argument("scenario_args", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    profile = PROFILES[args.profile]
    if args.command == "fetch":
        return fetch(profile)
    if args.command == "configure":
        return generic("configure", profile)
    if args.command == "doctor":
        return generic("doctor", profile)
    if args.command == "prepare":
        return prepare(profile)
    if args.command == "start":
        return start(profile)
    if args.command == "status":
        return control(profile, "status")
    if args.command == "scenario":
        return scenario(profile, args.scenario_args)
    if args.command == "stop":
        return control(profile, "terminate")
    print(VIEWER_URL)
    return 0


if __name__ == "__main__":
    try:
        workspace_rc = enter_workspace_if_needed()
        if workspace_rc is not None:
            raise SystemExit(workspace_rc)
        raise SystemExit(main())
    except RecipeError as exc:
        print(f"[NG] {exc}", file=sys.stderr)
        raise SystemExit(1)
