#!/usr/bin/env python3
"""Operate the Recipe-owned, Core-free City World Web UI runtime."""

from __future__ import annotations

import argparse
import importlib.util
import os
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools import workspace  # noqa: E402


RECIPE_PATH = ROOT / "recipes" / "examples" / "city-world-web-ui.yaml"


def _load_recipe_tool():
    """Load the top-level Recipe CLI, not the sibling tools.recipe package."""
    specification = importlib.util.spec_from_file_location(
        "business_pack_recipe_cli", ROOT / "tools" / "recipe.py"
    )
    if specification is None or specification.loader is None:
        raise RuntimeError("Business Pack Recipe CLI could not be loaded")
    module = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(module)
    return module


recipe_tool = _load_recipe_tool()


class CityWorldWebUiError(RuntimeError):
    pass


def recipe_data() -> dict:
    return recipe_tool.load_recipe(RECIPE_PATH)


def recipe_environment(data: dict) -> tuple[dict[str, str], workspace.WorkspacePaths]:
    variables, paths = recipe_tool.resolve_recipe_environment(RECIPE_PATH, data)
    environment = workspace.build_environment(workspace.resolve_workspace(ROOT))
    environment.update(variables)
    environment["HAKONIWA_RECIPE_ACTIVE"] = data["id"]
    environment["HAKONIWA_RECIPE_ID"] = data["id"]
    return environment, paths


def _run(command: list[str], *, environment: dict[str, str] | None = None) -> int:
    print(">", subprocess.list2cmdline(command), flush=True)
    return subprocess.run(command, cwd=ROOT, env=environment, check=False).returncode


def configure(data: dict) -> int:
    return recipe_tool.configure_recipe(RECIPE_PATH, data)


def doctor(data: dict) -> int:
    result = recipe_tool.doctor_recipe(RECIPE_PATH, data)
    if result:
        return result
    environment, paths = recipe_environment(data)
    probe = (
        "import hakoniwa_pdu; "
        "from hakoniwa_pdu.apps.launcher import hako_launcher, hako_launcher_ctl; "
        "from hakoniwa_pdu_endpoint.c_endpoint import Endpoint; "
        "print('City World Core-free runtime imports OK')"
    )
    return _run([str(paths.foundation_python), "-c", probe], environment=environment)


def lifecycle(data: dict, command: str, args: argparse.Namespace) -> int:
    environment, paths = recipe_environment(data)
    launcher = [
        str(paths.foundation_python),
        "-m",
        "tools.remote_operation.city_world.launcher",
        command,
        "--runtime-dir",
        str(paths.recipe_root / "runtime"),
        "--launcher-runtime-dir",
        str(paths.recipe_root / "launcher"),
    ]
    if command == "start":
        launcher.extend([
            "--listen-address", args.listen_address,
            "--worker-port", str(args.worker_port),
            "--web-port", str(args.web_port),
            "--max-download-gib", str(args.max_download_gib),
            "--parallel-workers", str(args.parallel_workers),
            "--dem-parallel-workers", str(args.dem_parallel_workers),
            "--terrain-spacing-m", args.terrain_spacing_m,
            "--ready-timeout-sec", str(args.ready_timeout_sec),
        ])
        if args.open_browser:
            launcher.append("--open-browser")
    return _run(launcher, environment=environment)


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(
        description="Configure and operate the Core-free City World Web UI Recipe"
    )
    result.add_argument("command", choices=("configure", "doctor", "start", "status", "stop"))
    result.add_argument("--listen-address", default="127.0.0.1")
    result.add_argument("--worker-port", type=int, default=54210)
    result.add_argument("--web-port", type=int, default=8008)
    result.add_argument("--max-download-gib", type=float, default=8.0)
    result.add_argument("--parallel-workers", type=int, default=4)
    result.add_argument("--dem-parallel-workers", type=int, default=2)
    result.add_argument("--terrain-spacing-m", choices=("2", "5", "10", "auto"), default="auto")
    result.add_argument("--ready-timeout-sec", type=float, default=15.0)
    result.add_argument("--open-browser", action="store_true")
    return result


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    data = recipe_data()
    try:
        if args.command == "configure":
            return configure(data)
        if args.command == "doctor":
            return doctor(data)
        return lifecycle(data, args.command, args)
    except (CityWorldWebUiError, OSError, RuntimeError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
