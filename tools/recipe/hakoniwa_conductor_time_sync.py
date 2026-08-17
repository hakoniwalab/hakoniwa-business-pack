#!/usr/bin/env python3
"""Run the public Hakoniwa Conductor Python time-synchronization smoke."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import signal
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

from tools.recipe import hakoniwa_conductor


RECIPE_ID = "hakoniwa-conductor-python-time-sync"
FOUNDATION_ROOT = hakoniwa_conductor.business_pack_root() / "work" / "foundation" / "install"
WORK_ROOT = hakoniwa_conductor.business_pack_root() / "work" / "recipes" / RECIPE_ID


class TimeSyncError(RuntimeError):
    pass


@dataclass
class ManagedProcess:
    name: str
    process: subprocess.Popen[bytes]
    log_stream: object
    log_path: Path


def default_package_root() -> Path:
    target = hakoniwa_conductor.detect_target()
    return hakoniwa_conductor.paths(target)["package"]


def default_sample_root() -> Path:
    return (
        hakoniwa_conductor.business_pack_root().parent
        / "hakoniwa-conductor"
        / "samples"
        / "python-time-sync"
    )


def runtime_paths() -> dict[str, Path]:
    return {
        "root": WORK_ROOT,
        "config": WORK_ROOT / "config",
        "asset": WORK_ROOT / "asset" / "hello_asset.py",
        "logs": WORK_ROOT / "logs",
        "runtime": WORK_ROOT / "runtime",
        "validation": WORK_ROOT / "validation" / "evidence.json",
    }


def core_config(domain: str) -> Path:
    return runtime_paths()["runtime"] / "core" / domain / "cpp_core_config.json"


def write_json_atomic(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def configure(sample_root: Path) -> dict[str, object]:
    generated = sample_root / "config" / "generated"
    asset = sample_root / "asset" / "hello_asset.py"
    if not generated.is_dir() or not asset.is_file():
        raise TimeSyncError(f"public sample is incomplete: {sample_root}")
    if any("rd-ctrl" in path.as_posix() for path in generated.rglob("*")):
        raise TimeSyncError("public sample unexpectedly contains an RD control artifact")

    paths = runtime_paths()
    if paths["config"].exists():
        shutil.rmtree(paths["config"])
    shutil.copytree(generated, paths["config"])
    paths["asset"].parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(asset, paths["asset"])

    for domain in ("server", "client-a", "client-b"):
        mmap_root = paths["runtime"] / "core" / domain / "mmap"
        if mmap_root.exists():
            shutil.rmtree(mmap_root)
        mmap_root.mkdir(parents=True, exist_ok=True)
        write_json_atomic(
            core_config(domain),
            {
                "shm_type": "mmap",
                "core_mmap_path": str(mmap_root.resolve()),
                "asset_timeout_usec": 600_000_000,
            },
        )
    return {
        "sample_root": str(sample_root.resolve()),
        "work_root": str(WORK_ROOT.resolve()),
        "domains": ["server", "client-a", "client-b"],
    }


def library_environment(domain: str) -> dict[str, str]:
    env = os.environ.copy()
    library_path = str((FOUNDATION_ROOT / "lib").resolve())
    variable = "DYLD_LIBRARY_PATH" if sys.platform == "darwin" else "LD_LIBRARY_PATH"
    current = env.get(variable, "")
    env[variable] = library_path if not current else f"{library_path}{os.pathsep}{current}"
    env["HAKO_CONFIG_PATH"] = str(core_config(domain).resolve())
    return env


def validate_foundation_contract(build_contract: Path) -> dict[str, str]:
    try:
        return hakoniwa_conductor.validate_foundation_contract(
            build_contract, FOUNDATION_ROOT
        )
    except (hakoniwa_conductor.ConductorRecipeError, OSError) as exc:
        raise TimeSyncError(str(exc)) from exc


def doctor(package_root: Path, sample_root: Path) -> dict[str, object]:
    target = hakoniwa_conductor.detect_target()
    package = hakoniwa_conductor.validate_package(package_root, target)
    required = {
        "hako_cmd": FOUNDATION_ROOT / "bin" / "hako-cmd",
        "python": FOUNDATION_ROOT / "python" / "bin" / "python3",
        "hakopy": FOUNDATION_ROOT / "share" / "hakoniwa" / "python" / "hakopy.so",
        "sample_asset": sample_root / "asset" / "hello_asset.py",
        "sample_server_config": sample_root / "config" / "generated" / "conductor" / "srv-01.json",
    }
    missing = [f"{name}={path}" for name, path in required.items() if not path.exists()]
    if missing:
        raise TimeSyncError("required runtime artifacts are missing: " + ", ".join(missing))
    installed_contract = validate_foundation_contract(
        Path(str(package["build_contract"]))
    )
    configure(sample_root)
    import_check = subprocess.run(
        [str(required["python"]), "-c", "import hakopy; print(hakopy.__file__)"],
        env=library_environment("client-a"),
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    if import_check.returncode != 0:
        raise TimeSyncError("Foundation Python cannot import hakopy: " + import_check.stdout)
    return {
        "package": package,
        "sample_root": str(sample_root.resolve()),
        "foundation": str(FOUNDATION_ROOT.resolve()),
        "foundation_contract": installed_contract,
        "hakopy": import_check.stdout.strip(),
    }


def wait_for_log(
    path: Path,
    pattern: str,
    process: ManagedProcess,
    timeout: float,
) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if path.is_file() and pattern in path.read_text(errors="replace"):
            return
        if process.process.poll() is not None:
            raise TimeSyncError(
                f"{process.name} exited with {process.process.returncode} while waiting "
                f"for {pattern!r}; log={path}"
            )
        time.sleep(0.1)
    raise TimeSyncError(f"timeout waiting for {pattern!r}; log={path}")


def start_process(
    name: str,
    command: list[str],
    domain: str,
) -> ManagedProcess:
    log_path = runtime_paths()["logs"] / f"{name}.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    stream = log_path.open("wb")
    process = subprocess.Popen(
        command,
        cwd=WORK_ROOT,
        env=library_environment(domain),
        stdout=stream,
        stderr=subprocess.STDOUT,
        start_new_session=True,
    )
    return ManagedProcess(name, process, stream, log_path)


def stop_process(managed: ManagedProcess) -> None:
    if managed.process.poll() is None:
        try:
            os.killpg(managed.process.pid, signal.SIGINT)
        except ProcessLookupError:
            pass
        try:
            managed.process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            managed.process.terminate()
            try:
                managed.process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                managed.process.kill()
                managed.process.wait(timeout=3)
    managed.log_stream.close()


def decode_tick(path: Path, required_tick: int) -> int | None:
    for line in path.read_text(errors="replace").splitlines():
        if not line.startswith("{"):
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if event.get("event") == "TICK" and event.get("tick") == required_tick:
            value = event.get("sim_time_usec")
            return value if isinstance(value, int) else None
    return None


def command_path(package_root: Path, name: str) -> str:
    path = package_root / "bin" / name
    if not path.is_file():
        raise TimeSyncError(f"Conductor command is missing: {path}")
    return str(path.resolve())


def run_hako_cmd(action: str) -> None:
    result = subprocess.run(
        [str(FOUNDATION_ROOT / "bin" / "hako-cmd"), action],
        env=library_environment("server"),
        cwd=WORK_ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    if result.returncode != 0:
        raise TimeSyncError(f"hako-cmd {action} failed: {result.stdout}")


def smoke(
    package_root: Path,
    sample_root: Path,
    required_tick: int,
    timeout: float,
) -> dict[str, object]:
    preflight = doctor(package_root, sample_root)
    paths = runtime_paths()
    if paths["logs"].exists():
        shutil.rmtree(paths["logs"])
    paths["logs"].mkdir(parents=True)
    config = paths["config"]
    python = str((FOUNDATION_ROOT / "python" / "bin" / "python3").resolve())
    processes: list[ManagedProcess] = []
    started_at = time.monotonic()
    result: dict[str, object] = {
        "status": "FAILED",
        "required_tick": required_tick,
        "package": preflight["package"],
        "foundation_contract": preflight["foundation_contract"],
        "sample_root": preflight["sample_root"],
    }

    try:
        server_01 = start_process(
            "server-01",
            [command_path(package_root, "main_server"), "--config", str(config / "conductor" / "srv-01.json"), "--server-node-id", "srv-01-01", "--enable-conductor"],
            "server",
        )
        processes.append(server_01)
        wait_for_log(
            server_01.log_path,
            "Server run loop started.",
            server_01,
            timeout,
        )

        server_02 = start_process(
            "server-02",
            [command_path(package_root, "main_server"), "--config", str(config / "conductor" / "srv-01.json"), "--server-node-id", "srv-01-02"],
            "server",
        )
        processes.append(server_02)
        wait_for_log(
            server_02.log_path,
            "Server run loop started.",
            server_02,
            timeout,
        )

        processes.extend(
            [
                start_process(
                    "client-a",
                    [command_path(package_root, "main_client"), "--config", str(config / "conductor" / "cli-01.json")],
                    "client-a",
                ),
                start_process(
                    "client-b",
                    [command_path(package_root, "main_client"), "--config", str(config / "conductor" / "cli-02.json")],
                    "client-b",
                ),
            ]
        )
        by_name = {process.name: process for process in processes}
        for name in ("client-a", "client-b"):
            wait_for_log(by_name[name].log_path, "Client run loop started.", by_name[name], timeout)

        processes.extend(
            [
                start_process(
                    "hello-asset-a",
                    [python, str(paths["asset"]), "--asset-name", "hello-asset-a", "--config", str(config / "pdudef.json"), "--log-every", "10"],
                    "client-a",
                ),
                start_process(
                    "hello-asset-b",
                    [python, str(paths["asset"]), "--asset-name", "hello-asset-b", "--config", str(config / "pdudef.json"), "--log-every", "10"],
                    "client-b",
                ),
            ]
        )
        by_name = {process.name: process for process in processes}
        for name in ("hello-asset-a", "hello-asset-b"):
            wait_for_log(by_name[name].log_path, '"event":"REGISTERED"', by_name[name], timeout)
        for name in ("client-a", "client-b"):
            wait_for_log(
                by_name[name].log_path,
                "Client proxy state -> Joined",
                by_name[name],
                timeout,
            )

        run_hako_cmd("start")
        for name in ("hello-asset-a", "hello-asset-b"):
            wait_for_log(by_name[name].log_path, f'"tick":{required_tick}', by_name[name], timeout)
        time_a = decode_tick(by_name["hello-asset-a"].log_path, required_tick)
        time_b = decode_tick(by_name["hello-asset-b"].log_path, required_tick)
        if time_a is None or time_b is None or time_a != time_b:
            raise TimeSyncError(
                f"time mismatch at tick {required_tick}: hello-asset-a={time_a}, hello-asset-b={time_b}"
            )
        if any(process.process.poll() is not None for process in processes):
            raise TimeSyncError("a managed process exited before validation completed")
        result.update(
            status="PASSED",
            sim_time_usec=time_a,
            elapsed_sec=round(time.monotonic() - started_at, 3),
            logs=str(paths["logs"]),
        )
        return result
    except BaseException as exc:
        result["reason"] = str(exc)
        result["elapsed_sec"] = round(time.monotonic() - started_at, 3)
        raise
    finally:
        try:
            run_hako_cmd("stop")
        except TimeSyncError as exc:
            result.setdefault("cleanup_warning", str(exc))
        for process in reversed(processes):
            stop_process(process)
        write_json_atomic(paths["validation"], result)


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(description=__doc__)
    root.add_argument("--package-root", type=Path, default=default_package_root())
    root.add_argument("--sample-root", type=Path, default=default_sample_root())
    commands = root.add_subparsers(dest="command", required=True)
    commands.add_parser("configure")
    commands.add_parser("doctor")
    smoke_parser = commands.add_parser("smoke")
    smoke_parser.add_argument("--required-tick", type=int, default=20)
    smoke_parser.add_argument("--timeout", type=float, default=60.0)
    return root


def main() -> int:
    args = parser().parse_args()
    try:
        if args.command == "configure":
            result = configure(args.sample_root)
        elif args.command == "doctor":
            result = doctor(args.package_root, args.sample_root)
        else:
            result = smoke(args.package_root, args.sample_root, args.required_tick, args.timeout)
        print(json.dumps(result, indent=2))
        return 0
    except (TimeSyncError, hakoniwa_conductor.ConductorRecipeError, OSError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
