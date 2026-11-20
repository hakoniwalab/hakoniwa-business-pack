#!/usr/bin/env python3
"""Operate the Athrill Studio PX4 motor-fault Recipe (recipes/examples/athrill-studio-px4-motor-fault.yaml).

    python tools/recipe/athrill_studio_px4.py configure   # Foundation and the pinned repositories
    python tools/recipe/athrill_studio_px4.py fetch       # Studio's release files, Hakoniwa Drone's mac.zip
    python tools/recipe/athrill_studio_px4.py build       # the in-process sensor device with the motor status
    python tools/recipe/athrill_studio_px4.py prepare     # PX4's first boot with the DShot parameters, Studio's links
    python tools/recipe/athrill_studio_px4.py scenario    # the flight, then the motor failure, through Studio's API
    python tools/recipe/athrill_studio_px4.py start       # Studio in the background: open the printed URL
    python tools/recipe/athrill_studio_px4.py status | stop | url

Everything it makes stays under work/recipes/athrill-studio-px4-motor-fault/ and in the
Studio checkout's work/ directory. It needs no sudo and installs nothing outside the
Workspace.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import shutil
import socket
import subprocess
import sys
import urllib.request

ROOT = Path(__file__).resolve().parents[2]
RECIPE_ID = "athrill-studio-px4-motor-fault"
RECIPE = ROOT / "recipes/examples" / f"{RECIPE_ID}.yaml"
WORK = ROOT / "work/recipes" / RECIPE_ID
STUDIO_PORT = 8765
STUDIO_URL = f"http://127.0.0.1:{STUDIO_PORT}/"


class RecipeError(RuntimeError):
    pass


def enter_workspace_if_needed() -> int | None:
    """Re-run this command through the managed non-interactive Workspace."""
    if os.environ.get("HAKONIWA_WORKSPACE_ACTIVE") == "1":
        return None
    command = [sys.executable, str(ROOT / "tools/workspace.py"), "run", "--",
               sys.executable, str(Path(__file__).resolve()), *sys.argv[1:]]
    return run(command)


def run(command: list[str], *, env: dict[str, str] | None = None, cwd: Path = ROOT) -> int:
    print(">", subprocess.list2cmdline([str(c) for c in command]), flush=True)
    return subprocess.run([str(c) for c in command], cwd=cwd, env=env, check=False).returncode


def must(rc: int, what: str) -> None:
    if rc != 0:
        raise RecipeError(f"{what} failed (exit {rc})")


def recipe_tool(command: str) -> int:
    return run([sys.executable, ROOT / "tools/recipe.py", command, "--recipe", RECIPE])


def variables() -> dict[str, str]:
    path = WORK / "environment.json"
    if not path.is_file():
        raise RecipeError(f"Recipe environment is missing: {path}; run configure first")
    payload = json.loads(path.read_text(encoding="utf-8"))
    return payload["variables"]


def var_path(name: str) -> Path:
    return Path(variables()[name])


def host() -> str:
    key = (platform.system(), platform.machine())
    if key == ("Darwin", "arm64"):
        return "mac"
    raise RecipeError("this Recipe is verified on macOS arm64 only (the in-process sensor device is macOS-only for now)")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def download(url: str, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(destination.name + ".download")
    print(f"> download {url}", flush=True)
    urllib.request.urlretrieve(url, temporary)
    os.replace(temporary, destination)


def drone_dir() -> Path:
    """Hakoniwa Drone as athrill-device and Studio see it: the checkout's include/, config/ and
    thirdparty/ with the release's platform directory, without writing into the checkout."""
    return WORK / "drone"


def fetch() -> int:
    host()
    studio = var_path("ATHRILL_STUDIO_ROOT")
    must(run(["sh", studio / "tools/fetch.sh"], cwd=studio), "Studio tools/fetch.sh")
    drone = var_path("HAKO_DRONE_CHECKOUT")
    release = variables()["HAKO_DRONE_RELEASE"]
    base = f"https://github.com/toppers/hakoniwa-drone-core/releases/download/{release}"
    inputs = WORK / "inputs" / f"hakoniwa-drone-core-{release}"
    zip_path = inputs / "mac.zip"
    sum_path = inputs / "mac.zip.sha256"
    if not zip_path.is_file():
        download(f"{base}/mac.zip.sha256", sum_path)
        download(f"{base}/mac.zip", zip_path)
    expected = sum_path.read_text(encoding="utf-8").split()[0]
    actual = sha256(zip_path)
    if actual != expected:
        zip_path.unlink()
        raise RecipeError(f"mac.zip SHA-256 mismatch: {actual} != {expected}")
    print(f"[OK] mac.zip {release}: {actual}")
    target = drone_dir()
    if target.exists():
        shutil.rmtree(target)
    target.mkdir(parents=True)
    must(run(["unzip", "-q", zip_path, "-d", target]), "unzip mac.zip")
    for name in ("include", "thirdparty"):
        (target / name).symlink_to(drone / name)
    # a copy: the in-process aircraft writes its log next to the airframe
    shutil.copytree(drone / "config", target / "config")
    print(f"[OK] Hakoniwa Drone {release} for the Recipe: {target}")
    return 0


def build() -> int:
    host()
    v = variables()
    drone = Path(v["HAKO_DRONE_CHECKOUT"])
    must(run(["git", "-C", drone, "submodule", "update", "--init", "thirdparty/glm"]), "glm submodule")
    foundation = Path(v["HAKO_FOUNDATION_INSTALL"])
    # athrill-device takes the PDU types from <dir>/pdu/types; the Foundation installs them in
    # include/hakoniwa/pdu/ (hako_msgs, hako_mavlink_msgs, ...).
    registry = WORK / "pdu-registry"
    (registry / "pdu").mkdir(parents=True, exist_ok=True)
    types = registry / "pdu" / "types"
    if types.is_symlink():
        types.unlink()
    types.symlink_to(foundation / "include/hakoniwa/pdu")
    build_dir = WORK / "build-device"
    must(run([
        "cmake", "-S", Path(v["ATHRILL_DEVICE_ROOT"]) / "board/common", "-B", build_dir,
        "-DCMAKE_BUILD_TYPE=Release", f"-DCMAKE_PREFIX_PATH={foundation}",
        "-DATHRILL_ADDRESS_WIDTH=32", f"-DATHRILL_SOURCE_DIR={v['ATHRILL_ROOT']}",
        "-DHAKO_SENSOR_DEVICE_BUILD_INPROC=ON", "-DHAKO_SENSOR_DEVICE_BUILD_INPROC_MOTOR=ON",
        f"-DHAKO_DRONE_ROOT={drone_dir()}", f"-DHAKO_PDU_REGISTRY_DIR={registry}",
    ]), "cmake configure")
    must(run(["cmake", "--build", build_dir, "--parallel"]), "cmake build")
    library = build_dir / "libhako_sensor_device_inproc_motor.dylib"
    if not library.is_file():
        raise RecipeError(f"missing {library}")
    print(f"[OK] {library}")
    return 0


def prepare() -> int:
    host()
    v = variables()
    studio = Path(v["ATHRILL_STUDIO_ROOT"])
    target = Path(v["ATHRILL_TARGET_ROOT"])
    device = Path(v["ATHRILL_DEVICE_ROOT"])
    athrill3 = studio / "work/athrill3-stm32/athrill3"
    elf = studio / "work/download/px4_fmu-v5_default.elf"
    library = WORK / "build-device/libhako_sensor_device_inproc_motor.dylib"
    for need in (athrill3, elf, library):
        if not need.exists():
            raise RecipeError(f"missing {need}; run fetch and build first")
    seed = WORK / "seed-dshot"
    if not ((seed / "fram.bin").is_file() and (seed / "sd.img").is_file()):
        seed.mkdir(parents=True, exist_ok=True)
        env = os.environ.copy()
        env.update({"ATHRILL_DEVICE_DIR": str(device), "ATHRILL_WORK_DIR": str(seed),
                    "ATHRILL_PREPARE_ONLY": "1", "PYTHON": v["HAKO_FOUNDATION_PYTHON"]})
        for name in ("HAKO_DRONE_ROOT", "HAKO_AIRCRAFT_CONFIG_PATH", "ATHRILL_OPTIONS"):
            env.pop(name, None)
        # PX4's first boot with the x500 and DShot parameters on the test vehicle, no aircraft
        must(run([target / "examples/px4-hakoniwa-dshot/run.sh", elf, athrill3, library], env=env, cwd=target),
             "PX4 first boot")
    print(f"[OK] PX4 first-boot state: {seed}")
    must(run(["sh", studio / "tools/setup-drone-motor.sh", athrill3, studio / "work/athrill-device",
              library.parent, drone_dir(), seed], cwd=studio), "Studio tools/setup-drone-motor.sh")
    return 0


def port_busy(port: int) -> bool:
    with socket.socket() as s:
        return s.connect_ex(("127.0.0.1", port)) == 0


def scenario(extra: list[str]) -> int:
    v = variables()
    if port_busy(5760):
        raise RecipeError("TCP 5760 is in use (another PX4 run?); stop it first")
    studio = Path(v["ATHRILL_STUDIO_ROOT"])
    python = v["HAKO_FOUNDATION_PYTHON"]
    # the normal flight on the DShot ESCs, then the motor failure
    must(run([python, "tests/flight_smoke.py", "--firmware", "px4-x500-dshot"], cwd=studio), "flight (take off, hold, land)")
    return run([python, "tests/motor_stop_smoke.py", *extra], cwd=studio)


def launcher(command: str) -> int:
    v = variables()
    session = WORK / "runtime/launcher-session.json"
    if command == "start":
        (WORK / "logs").mkdir(parents=True, exist_ok=True)
        session.parent.mkdir(parents=True, exist_ok=True)
        rc = run([v["HAKO_FOUNDATION_PYTHON"], "-m", "hakoniwa_pdu.apps.launcher.hako_launcher",
                  WORK / "config/launcher.json", "--mode", "activate-only", "--background", session],
                 env={**os.environ, **v})
        if rc == 0:
            print(f"Studio : {STUDIO_URL}")
            print("Board Holybro Pixhawk 4, firmware 'PX4 v1.17.0 x500, motor 3 stops at 40 s', Real time on, Run.")
        return rc
    if not session.is_file():
        raise RecipeError(f"Launcher session is missing: {session}")
    return run([v["HAKO_FOUNDATION_PYTHON"], "-m", "hakoniwa_pdu.apps.launcher.hako_launcher_ctl",
                command, session], env={**os.environ, **v})


def main() -> int:
    parser = argparse.ArgumentParser(description="Operate the Athrill Studio PX4 motor-fault Recipe")
    parser.add_argument("command", choices=("configure", "doctor", "fetch", "build", "prepare",
                                            "scenario", "start", "status", "stop", "url"))
    parser.add_argument("args", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    if args.command in ("configure", "doctor"):
        return recipe_tool(args.command)
    if args.command == "fetch":
        return fetch()
    if args.command == "build":
        return build()
    if args.command == "prepare":
        return prepare()
    if args.command == "scenario":
        return scenario(args.args)
    if args.command == "start":
        return launcher("start")
    if args.command == "status":
        return launcher("status")
    if args.command == "stop":
        return launcher("terminate")
    print(STUDIO_URL)
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
