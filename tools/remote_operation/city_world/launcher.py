"""Lifecycle wrapper for the core-free City World Worker and Web UI."""

from __future__ import annotations

import argparse
import json
import socket
import subprocess
import sys
import time
import webbrowser
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
DEFAULT_SERVICE_RUNTIME = ROOT / "work" / "remote-operation" / "city-world-worker"
DEFAULT_LAUNCHER_RUNTIME = ROOT / "work" / "remote-operation" / "city-world-launcher"


class CityWorldLauncherError(RuntimeError):
    pass


def _connect_host(address: str) -> str:
    return "127.0.0.1" if address in {"0.0.0.0", "::"} else address


def _port_open(address: str, port: int, timeout: float = 0.2) -> bool:
    try:
        with socket.create_connection((_connect_host(address), port), timeout=timeout):
            return True
    except OSError:
        return False


def _launcher_command(*args: str) -> list[str]:
    return [sys.executable, "-m", "hakoniwa_pdu.apps.launcher.hako_launcher", *args]


def _control_command(*args: str) -> list[str]:
    return [sys.executable, "-m", "hakoniwa_pdu.apps.launcher.hako_launcher_ctl", *args]


def launcher_paths(runtime: Path) -> dict[str, Path]:
    root = runtime.resolve()
    return {
        "root": root,
        "config": root / "city-world.launch.json",
        "session": root / "launcher-session.json",
        "logs": root / "logs",
    }


def write_launcher_config(
    *,
    launcher_runtime: Path,
    service_runtime: Path,
    listen_address: str,
    worker_port: int,
    web_port: int,
    max_download_gib: float,
    parallel_workers: int,
    dem_parallel_workers: int,
    terrain_spacing_m: str,
) -> Path:
    paths = launcher_paths(launcher_runtime)
    paths["logs"].mkdir(parents=True, exist_ok=True)
    ready_dir = paths["root"] / "ready"
    ready_dir.mkdir(parents=True, exist_ok=True)
    worker_ready = ready_dir / "worker.ready"
    web_ready = ready_dir / "web.ready"
    worker_ready.unlink(missing_ok=True)
    web_ready.unlink(missing_ok=True)
    service_runtime = service_runtime.resolve()
    config = {
        "version": "1",
        "defaults": {
            "cwd": str(ROOT),
            "start_grace_sec": 0.5,
            "delay_sec": 0.0,
        },
        "assets": [
            {
                "name": "city-world-worker",
                "command": sys.executable,
                "args": [
                    "-m", "tools.remote_operation.city_world.worker",
                    "--listen-address", listen_address,
                    "--port", str(worker_port),
                    "--runtime-dir", str(service_runtime),
                    "--max-download-gib", str(max_download_gib),
                    "--parallel-workers", str(parallel_workers),
                    "--dem-parallel-workers", str(dem_parallel_workers),
                    "--terrain-spacing-m", terrain_spacing_m,
                    "--ready-file", str(worker_ready),
                ],
                "stdout": str(paths["logs"] / "worker.out"),
                "stderr": str(paths["logs"] / "worker.err"),
                "activation_timing": "before_start",
                "start_grace_sec": 0.5,
                "delay_sec": 0.25,
            },
            {
                "name": "city-world-web",
                "command": sys.executable,
                "args": [
                    "-m", "tools.remote_operation.city_world.web_smoke",
                    "--listen-address", listen_address,
                    "--port", str(web_port),
                    "--worker-runtime-dir", str(service_runtime),
                    "--ready-file", str(web_ready),
                ],
                "stdout": str(paths["logs"] / "web.out"),
                "stderr": str(paths["logs"] / "web.err"),
                "activation_timing": "before_start",
                "depends_on": ["city-world-worker"],
                "start_grace_sec": 0.5,
            },
        ],
    }
    paths["config"].write_text(
        json.dumps(config, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return paths["config"]


def _run_control(command: str, session: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        _control_command(command, str(session)),
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )


def start(args: argparse.Namespace) -> int:
    paths = launcher_paths(args.launcher_runtime_dir)
    if paths["session"].is_file():
        current = _run_control("status", paths["session"])
        try:
            state = json.loads(current.stdout).get("state")
        except (json.JSONDecodeError, AttributeError):
            state = None
        if state not in {None, "TERMINATED", "FAILED"}:
            raise CityWorldLauncherError(
                f"City World Launcher is already active: state={state}"
            )
    occupied = [
        f"Worker {args.listen_address}:{args.worker_port}"
        for _ in [0] if _port_open(args.listen_address, args.worker_port)
    ] + [
        f"Web {args.listen_address}:{args.web_port}"
        for _ in [0] if _port_open(args.listen_address, args.web_port)
    ]
    if occupied:
        raise CityWorldLauncherError("port is already in use: " + ", ".join(occupied))

    config = write_launcher_config(
        launcher_runtime=args.launcher_runtime_dir,
        service_runtime=args.runtime_dir,
        listen_address=args.listen_address,
        worker_port=args.worker_port,
        web_port=args.web_port,
        max_download_gib=args.max_download_gib,
        parallel_workers=args.parallel_workers,
        dem_parallel_workers=args.dem_parallel_workers,
        terrain_spacing_m=args.terrain_spacing_m,
    )
    completed = subprocess.run(
        _launcher_command(
            str(config), "--mode", "activate-only", "--background", str(paths["session"])
        ),
        cwd=ROOT,
        text=True,
        check=False,
    )
    if completed.returncode:
        return completed.returncode

    deadline = time.monotonic() + args.ready_timeout_sec
    ready_dir = paths["root"] / "ready"
    while time.monotonic() < deadline:
        if (
            (ready_dir / "worker.ready").is_file()
            and (ready_dir / "web.ready").is_file()
        ):
            url = f"http://{_connect_host(args.listen_address)}:{args.web_port}/"
            print("[OK] City World services are ready")
            print(f"Web UI : {url}")
            print(f"Session: {paths['session']}")
            print(f"Logs   : {paths['logs']}")
            print(f"Parallel workers: {args.parallel_workers}")
            print(f"DEM parallel workers: {args.dem_parallel_workers}")
            print(f"Terrain spacing: {args.terrain_spacing_m} m")
            if args.open_browser:
                webbrowser.open(url)
            return 0
        time.sleep(0.1)

    stopped = _run_control("terminate", paths["session"])
    if stopped.stdout:
        print(stopped.stdout.strip())
    raise CityWorldLauncherError(
        f"services did not become ready within {args.ready_timeout_sec:g} seconds; "
        f"inspect {paths['logs']}"
    )


def control(command: str, launcher_runtime: Path) -> int:
    session = launcher_paths(launcher_runtime)["session"]
    if not session.is_file():
        raise CityWorldLauncherError(f"Launcher session not found: {session}")
    completed = _run_control("terminate" if command == "stop" else command, session)
    if completed.stdout:
        print(completed.stdout.strip())
    if completed.stderr:
        print(completed.stderr.strip(), file=sys.stderr)
    return completed.returncode


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Core-free City World service Launcher")
    parser.add_argument("command", choices=("start", "status", "stop"))
    parser.add_argument("--listen-address", default="127.0.0.1")
    parser.add_argument("--worker-port", type=int, default=54210)
    parser.add_argument("--web-port", type=int, default=8008)
    parser.add_argument("--runtime-dir", type=Path, default=DEFAULT_SERVICE_RUNTIME)
    parser.add_argument(
        "--launcher-runtime-dir", type=Path, default=DEFAULT_LAUNCHER_RUNTIME
    )
    parser.add_argument("--max-download-gib", type=float, default=8.0)
    parser.add_argument(
        "--parallel-workers", type=int, default=4,
        help="Envsim source/component worker limit (1-16; default: 4)",
    )
    parser.add_argument(
        "--dem-parallel-workers", type=int, default=2,
        help="DEM source extraction process limit (1-4; default: 2)",
    )
    parser.add_argument(
        "--terrain-spacing-m", choices=("2", "5", "10", "auto"), default="2",
        help="terrain grid spacing or automatic sample-budget selection (default: 2)",
    )
    parser.add_argument("--ready-timeout-sec", type=float, default=15.0)
    parser.add_argument("--open-browser", action="store_true")
    args = parser.parse_args(argv)
    if not 1 <= args.worker_port <= 65535 or not 1 <= args.web_port <= 65535:
        parser.error("ports must be in [1, 65535]")
    if args.worker_port == args.web_port:
        parser.error("Worker and Web ports must differ")
    if args.max_download_gib <= 0 or args.ready_timeout_sec <= 0:
        parser.error("download limit and readiness timeout must be positive")
    if not 1 <= args.parallel_workers <= 16:
        parser.error("--parallel-workers must be in [1, 16]")
    if not 1 <= args.dem_parallel_workers <= 4:
        parser.error("--dem-parallel-workers must be in [1, 4]")
    try:
        return start(args) if args.command == "start" else control(
            args.command, args.launcher_runtime_dir
        )
    except CityWorldLauncherError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
