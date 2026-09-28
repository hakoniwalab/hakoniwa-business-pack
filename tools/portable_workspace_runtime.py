#!/usr/bin/env python3
"""Portable Workspace relocation helpers shared by packaging and runtime."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Mapping


class PortableRuntimeError(RuntimeError):
    pass


def portable_environment(
    business_pack: Path,
    *,
    base: Mapping[str, str] | None = None,
) -> dict[str, str]:
    """Return an environment isolated to one portable Business Pack tree."""
    root = business_pack.expanduser().resolve()
    work = root / "work"
    install = (work / "foundation" / "install").resolve()
    python_root = install / "python"
    bin_dir = install / "bin"

    env = dict(os.environ if base is None else base)
    for name in ("PYTHONPATH", "PYTHONHOME"):
        env.pop(name, None)
    env.update(
        {
            "PYTHONNOUSERSITE": "1",
            "HAKONIWA_PORTABLE_WORKSPACE": "1",
            "HAKONIWA_WORKSPACE_ACTIVE": "1",
            "HAKONIWA_WORKSPACE_ROOT": str(root),
            "HAKONIWA_WORK_DIR": str(work.resolve()),
            "HAKONIWA_HOME": str(install),
            "HAKO_CONFIG_PATH": str(
                (work / "foundation" / "config" / "cpp_core_config.json").resolve()
            ),
            "VIRTUAL_ENV": str(python_root),
            "HAKO_PDU_ENDPOINT_RUNTIME_DIRS": str(bin_dir),
        }
    )
    env["PATH"] = os.pathsep.join(
        (str(python_root), str(bin_dir), env.get("PATH", ""))
    )
    return env


def _rewrite_receipt_install_prefix(path: Path, prefix: Path) -> None:
    """Rewrite one receipt's install.prefix while preserving the rest verbatim."""
    lines = path.read_text(encoding="utf-8").splitlines()
    output: list[str] = []
    in_install = False
    replaced = False
    encoded_prefix = json.dumps(str(prefix), ensure_ascii=False)

    for line in lines:
        stripped = line.strip()
        indent = len(line) - len(line.lstrip(" "))
        if indent == 0:
            in_install = stripped == "install:"
        if in_install and indent == 2 and stripped.startswith("prefix:"):
            output.append(f"  prefix: {encoded_prefix}")
            replaced = True
        else:
            output.append(line)

    if not replaced:
        raise PortableRuntimeError(f"receipt has no install.prefix: {path}")
    path.write_text("\n".join(output) + "\n", encoding="utf-8")


def relocate_foundation_receipts(business_pack: Path) -> int:
    """Relocate copied Foundation receipts to the current Workspace prefix."""
    root = business_pack.expanduser().resolve()
    prefix = (root / "work" / "foundation" / "install").resolve()
    receipt_dir = prefix / "share" / "hakoniwa" / "receipts"
    if not receipt_dir.is_dir():
        raise PortableRuntimeError(
            f"Foundation receipt directory was not found: {receipt_dir}"
        )

    receipts = sorted(receipt_dir.glob("*.yaml"))
    if not receipts:
        raise PortableRuntimeError(f"Foundation receipts were not found: {receipt_dir}")
    for path in receipts:
        _rewrite_receipt_install_prefix(path, prefix)
    return len(receipts)


def _run(command: list[str], cwd: Path, env: dict[str, str], label: str) -> None:
    completed = subprocess.run(command, cwd=cwd, env=env, check=False)
    if completed.returncode:
        raise PortableRuntimeError(f"{label} failed with exit={completed.returncode}")


def prepare_workspace(
    business_pack: Path,
    *,
    extra_environment: Mapping[str, str] | None = None,
) -> tuple[Path, dict[str, str]]:
    """Relocate one portable Workspace and rebuild its path-dependent state."""
    root = business_pack.expanduser().resolve()
    python = root / "work" / "foundation" / "install" / "python" / "python.exe"
    if not python.is_file():
        raise PortableRuntimeError(f"portable Foundation Python was not found: {python}")

    env = portable_environment(root)
    if extra_environment:
        env.update(extra_environment)

    relocate_foundation_receipts(root)
    _run(
        [str(python), "tools/workspace.py", "prepare"],
        root,
        env,
        "portable Workspace prepare",
    )
    return python, env


def prepare_and_doctor(
    business_pack: Path,
    *,
    configure_command: tuple[str, ...],
    doctor_command: tuple[str, ...],
    extra_environment: Mapping[str, str] | None = None,
    label: str,
) -> tuple[Path, dict[str, str]]:
    """Relocate, regenerate and validate a portable runtime before start."""
    root = business_pack.expanduser().resolve()
    python, env = prepare_workspace(
        root,
        extra_environment=extra_environment,
    )
    _run(
        [str(python), *configure_command],
        root,
        env,
        f"{label} configure",
    )
    _run(
        [str(python), *doctor_command],
        root,
        env,
        f"{label} doctor",
    )
    return python, env


def prepare_city_world(business_pack: Path) -> tuple[Path, dict[str, str]]:
    """Prepare and validate City World for the current extraction path."""
    return prepare_and_doctor(
        business_pack,
        configure_command=("tools/recipe/city_world_web_ui.py", "configure"),
        doctor_command=("tools/recipe/city_world_web_ui.py", "doctor"),
        extra_environment={"HAKONIWA_PORTABLE_CITY_WORLD": "1"},
        label="portable City World Recipe",
    )


def _city_world_runtime(
    business_pack: Path,
) -> tuple[Path, Path, dict[str, str]]:
    root = business_pack.expanduser().resolve()
    python = root / "work" / "foundation" / "install" / "python" / "python.exe"
    if not python.is_file():
        raise PortableRuntimeError(f"portable Foundation Python was not found: {python}")
    env = portable_environment(root)
    env["HAKONIWA_PORTABLE_CITY_WORLD"] = "1"
    return root, python, env


def run_city_world(business_pack: Path, command: str) -> None:
    """Run one City World lifecycle command from the portable Workspace."""
    if command not in {"start", "status", "stop"}:
        raise PortableRuntimeError(f"unsupported City World command: {command}")

    root, python, env = _city_world_runtime(business_pack)
    if command == "start":
        python, env = prepare_city_world(root)

    launcher_runtime = root / "work" / "recipes" / "city-world-web-ui" / "launcher"
    command_line = [
        str(python),
        "-m",
        "tools.remote_operation.city_world.launcher",
        command,
        "--launcher-runtime-dir",
        str(launcher_runtime),
    ]
    if command == "start":
        command_line.extend(
            [
                "--runtime-dir",
                str(root / "work" / "recipes" / "city-world-web-ui" / "runtime"),
                "--parallel-workers",
                "8",
                "--terrain-spacing-m",
                "auto",
                "--open-browser",
            ]
        )
    _run(
        command_line,
        root,
        env,
        f"portable City World {command}",
    )


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(
        description="Prepare a relocated Hakoniwa portable Workspace"
    )
    result.add_argument(
        "command",
        choices=("prepare-city-world", "start-city-world", "status-city-world", "stop-city-world"),
    )
    result.add_argument(
        "--root",
        type=Path,
        default=Path(__file__).resolve().parents[1],
        help="Business Pack root",
    )
    return result


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        if args.command == "prepare-city-world":
            prepare_city_world(args.root)
            return 0
        if args.command in {"start-city-world", "status-city-world", "stop-city-world"}:
            run_city_world(args.root, args.command.removesuffix("-city-world"))
            return 0
        raise PortableRuntimeError(f"unsupported command: {args.command}")
    except (PortableRuntimeError, OSError, subprocess.SubprocessError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
