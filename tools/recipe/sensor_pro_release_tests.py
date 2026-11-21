#!/usr/bin/env python3
"""Operate the Hakoniwa Sensor PRO release-test Recipe (recipes/examples/sensor-pro-release-tests.yaml).

    python tools/recipe/sensor_pro_release_tests.py configure   # Foundation (Hakoniwa core) and the pinned repositories
    python tools/recipe/sensor_pro_release_tests.py doctor
    python tools/recipe/sensor_pro_release_tests.py fetch       # the v0.2.0 archive of this host and MuJoCo, SHA-256 checked
    python tools/recipe/sensor_pro_release_tests.py check       # the plugin finds every library it needs
    python tools/recipe/sensor_pro_release_tests.py test        # the release tests against the archive

Everything it downloads stays under work/recipes/sensor-pro-release-tests/; the tests build under the
hakoniwa-sensor-pro checkout's build/. macOS arm64 or Linux x86_64. No sudo; the host tools (CMake, a C++
compiler, GoogleTest, GLFW) come from the host's package manager first.
"""

from __future__ import annotations

import argparse
import hashlib
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import tempfile
import urllib.request
import zipfile

ROOT = Path(__file__).resolve().parents[2]
RECIPE_ID = "sensor-pro-release-tests"
RECIPE = ROOT / "recipes/examples" / f"{RECIPE_ID}.yaml"
CATALOG = ROOT / "catalog/components/hakoniwa-sensor-pro.yaml"
WORK = ROOT / "work/recipes" / RECIPE_ID
RUNTIME = WORK / "runtime"            # the native runtime root of the catalog profile: archive/, mujoco/, MUJOCO_VERSION.txt
FOUNDATION = ROOT / "work/foundation/install"
VERSION = "0.2.0"
RELEASE = f"https://github.com/hakoniwalab/hakoniwa-sensor-pro/releases/download/v{VERSION}"
MUJOCO = "3.16.0"
MUJOCO_RELEASE = f"https://github.com/google-deepmind/mujoco/releases/download/{MUJOCO}"
HOSTS = {
    ("Darwin", "arm64"): {
        "platform": "macos-arm64",
        "archive_sha256": "bd212c9b89cdbcb4da93e015c70ea4657d76b9ca4cd4ce708df9046e4bb70367",
        "mujoco": f"mujoco-{MUJOCO}-macos-universal2.dmg",
        "mujoco_sha256": "e31ab79c0a5e4280649329f9812b6a402f3c3e416cee04f2f658e5ee26a4d85f",
    },
    ("Linux", "x86_64"): {
        "platform": "linux-x64",
        "archive_sha256": "ad042871005e7713e1fc97bd414a4c3b2b991d9dcb587b73e2ede377173371ba",
        "mujoco": f"mujoco-{MUJOCO}-linux-x86_64.tar.gz",
        "mujoco_sha256": "139577c541d43716a63992ab698cbc7b09788b1d547fb82b682d59b40ef49b9d",
    },
}


class RecipeError(RuntimeError):
    pass


def enter_workspace_if_needed() -> int | None:
    """Re-run this command through the managed non-interactive Workspace."""
    if os.environ.get("HAKONIWA_WORKSPACE_ACTIVE") == "1":
        return None
    command = [sys.executable, str(ROOT / "tools/workspace.py"), "run", "--",
               sys.executable, str(Path(__file__).resolve()), *sys.argv[1:]]
    return run(command)


def run(command: list, *, env: dict[str, str] | None = None, cwd: Path = ROOT) -> int:
    print(">", subprocess.list2cmdline([str(c) for c in command]), flush=True)
    return subprocess.run([str(c) for c in command], cwd=cwd, env=env, check=False).returncode


def must(rc: int, what: str) -> None:
    if rc != 0:
        raise RecipeError(f"{what} failed (exit {rc})")


def host() -> dict[str, str]:
    key = (platform.system(), platform.machine())
    if key not in HOSTS:
        raise RecipeError(f"no Sensor PRO archive for {key[0]} {key[1]} (macOS arm64 or Linux x86_64)")
    return HOSTS[key]


def dependency(name: str, env_name: str) -> Path:
    root = Path(os.environ.get(env_name, ROOT.parent / name)).resolve()
    if not root.is_dir():
        raise RecipeError(f"{name} is missing at {root}; run configure first")
    return root


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def download_checked(url: str, destination: Path, expected: str) -> Path:
    if not destination.is_file() or sha256(destination) != expected:
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary = destination.with_name(destination.name + ".download")
        print(f"> download {url}", flush=True)
        urllib.request.urlretrieve(url, temporary)
        os.replace(temporary, destination)
    actual = sha256(destination)
    if actual != expected:
        raise RecipeError(f"{destination.name}: SHA-256 {actual} != {expected}")
    print(f"{destination.name}: OK (sha256 {expected[:12]}...)")
    return destination


def extract_mujoco(package: Path, target: Path) -> None:
    """MuJoCo's include/ and lib/ (and on macOS its framework, which the plugin names) under target."""
    if target.exists():
        shutil.rmtree(target)
    if package.suffix == ".dmg":
        (target / "include/mujoco").mkdir(parents=True)
        (target / "lib").mkdir()
        with tempfile.TemporaryDirectory() as mount:
            must(run(["hdiutil", "attach", "-nobrowse", "-readonly", "-mountpoint", mount, package]), "attach MuJoCo dmg")
            try:
                shutil.copytree(Path(mount) / "MuJoCo.app/Contents/Frameworks/MuJoCo.framework",
                                target / "MuJoCo.framework", symlinks=True)
            finally:
                must(run(["hdiutil", "detach", mount]), "detach MuJoCo dmg")
        framework = target / "MuJoCo.framework"
        for header in (framework / "Headers").iterdir():
            shutil.copy2(header, target / "include/mujoco" / header.name)
        shutil.copy2(framework / f"Versions/Current/libmujoco.{MUJOCO}.dylib", target / "lib")
    else:
        with tempfile.TemporaryDirectory() as tmp:
            must(run(["tar", "-xzf", package, "-C", tmp]), "extract MuJoCo")
            shutil.move(str(Path(tmp) / f"mujoco-{MUJOCO}"), target)


def fetch() -> int:
    h = host()
    name = f"hakoniwa-sensor-pro-{VERSION}-{h['platform']}"
    downloads = WORK / "downloads"
    archive = download_checked(f"{RELEASE}/{name}.zip", downloads / f"{name}.zip", h["archive_sha256"])
    package = download_checked(f"{MUJOCO_RELEASE}/{h['mujoco']}", downloads / h["mujoco"], h["mujoco_sha256"])
    if RUNTIME.exists():
        shutil.rmtree(RUNTIME)
    RUNTIME.mkdir(parents=True)
    with tempfile.TemporaryDirectory() as tmp:
        with zipfile.ZipFile(archive) as z:
            z.extractall(tmp)
        shutil.move(str(Path(tmp) / name), RUNTIME / "archive")
    for f in (RUNTIME / "archive/lib").iterdir():  # zipfile drops the mode bits
        f.chmod(0o755 if f.suffix == ".so" else 0o644)
    extract_mujoco(package, RUNTIME / "mujoco")
    shutil.copy2(dependency("hakoniwa-sensor-pro", "HAKONIWA_SENSOR_PRO_ROOT") / "MUJOCO_VERSION.txt", RUNTIME)
    print(f"[OK] {RUNTIME}: archive/ ({name}), mujoco/ (MuJoCo {MUJOCO})")
    return 0


def library_environment() -> dict[str, str]:
    """Where a host would find the plugin's libraries: the Foundation core, MuJoCo, and GLFW."""
    env = os.environ.copy()
    dirs = [str(FOUNDATION / "lib"), str(RUNTIME / "mujoco/lib")]
    if platform.system() == "Darwin":
        dirs.append(str(RUNTIME / "mujoco"))  # @rpath/mujoco.framework/...
        brew = shutil.which("brew")
        if brew:
            prefix = subprocess.run([brew, "--prefix"], capture_output=True, text=True).stdout.strip()
            dirs.append(str(Path(prefix) / "lib"))
        key = "DYLD_LIBRARY_PATH"
    else:
        key = "LD_LIBRARY_PATH"
    env[key] = os.pathsep.join(dirs + ([env[key]] if env.get(key) else []))
    return env


def check() -> int:
    if not (RUNTIME / "archive").is_dir():
        raise RecipeError(f"{RUNTIME / 'archive'} is missing; run fetch first")
    sys.path.insert(0, str(ROOT / "tools"))
    import native_runtime

    try:
        _contract, checks = native_runtime.validate_requirement(
            CATALOG, RECIPE, "hakoniwa-sensor-pro", RUNTIME, library_environment())
    except native_runtime.NativeRuntimeError as exc:
        raise RecipeError(str(exc)) from exc
    failed = 0
    for c in checks:
        print(f"[{'OK' if c.ok else 'NG'}] {c.label}: {c.detail}")
        failed += 0 if c.ok else 1
    if failed:
        raise RecipeError(f"{failed} native runtime check(s) failed: install the missing host libraries (GLFW) "
                          "or run configure for the Foundation core")
    return 0


def test() -> int:
    check()
    sensor_pro = dependency("hakoniwa-sensor-pro", "HAKONIWA_SENSOR_PRO_ROOT")
    dependency("hakoniwa-sensor-adapter", "HAKONIWA_SENSOR_ADAPTER_ROOT")
    dependency("hakoniwa-drone-core", "HAKONIWA_DRONE_CORE_ROOT")
    env = os.environ.copy()
    # nlohmann/json for the tests: the Foundation install has it when the host does not.
    env["CMAKE_PREFIX_PATH"] = os.pathsep.join([str(FOUNDATION)] + ([env["CMAKE_PREFIX_PATH"]] if env.get("CMAKE_PREFIX_PATH") else []))
    return run([sys.executable, "tools/run-tests.py", "--dist", RUNTIME / "archive", "--mujoco-root", RUNTIME / "mujoco",
                "--hakoniwa-prefix", FOUNDATION], env=env, cwd=sensor_pro)


def main() -> int:
    parser = argparse.ArgumentParser(description="Operate the Hakoniwa Sensor PRO release-test Recipe")
    parser.add_argument("command", choices=("configure", "doctor", "fetch", "check", "test"))
    args = parser.parse_args()
    if args.command in ("configure", "doctor"):
        return run([sys.executable, ROOT / "tools/recipe.py", args.command, "--recipe", RECIPE])
    return {"fetch": fetch, "check": check, "test": test}[args.command]()


if __name__ == "__main__":
    try:
        workspace_rc = enter_workspace_if_needed()
        if workspace_rc is not None:
            raise SystemExit(workspace_rc)
        raise SystemExit(main())
    except RecipeError as exc:
        print(f"[NG] {exc}", file=sys.stderr)
        raise SystemExit(1)
