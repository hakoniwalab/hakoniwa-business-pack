#!/usr/bin/env python3
"""Best-effort validation for the managed Hakoniwa Workspace identity."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from typing import Mapping, Sequence, TextIO


def repository_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _normalized_path(value: str | Path) -> str:
    return os.path.normcase(
        os.path.abspath(os.path.expanduser(os.fspath(value)))
    )


def _same_path(value: str | None, expected: Path) -> bool:
    return bool(value) and _normalized_path(value) == _normalized_path(expected)


def _expected_paths(root: Path) -> dict[str, Path]:
    business_pack_root = root.expanduser().resolve()
    install = business_pack_root / "work" / "foundation" / "install"
    python_root = install / "python"
    python_bin = python_root / ("Scripts" if os.name == "nt" else "bin")
    return {
        "root": business_pack_root,
        "home": install,
        "virtual_env": python_root,
        "python_bin": python_bin,
        "foundation_bin": install / "bin",
        "config": business_pack_root
        / "work"
        / "foundation"
        / "config"
        / "cpp_core_config.json",
    }


def validate_workspace(
    root: Path | None = None,
    environment: Mapping[str, str] | None = None,
) -> list[str]:
    """Return Workspace identity mismatches without inspecting runtime artifacts."""
    selected_root = (root or repository_root()).expanduser().resolve()
    env = os.environ if environment is None else environment
    expected = _expected_paths(selected_root)

    if env.get("HAKONIWA_WORKSPACE_ACTIVE") != "1":
        return [
            "HAKONIWA_WORKSPACE_ACTIVE is not 1; the managed Workspace is not active"
        ]

    errors: list[str] = []
    path_contract = (
        ("HAKONIWA_WORKSPACE_ROOT", expected["root"]),
        ("HAKONIWA_HOME", expected["home"]),
        ("VIRTUAL_ENV", expected["virtual_env"]),
        ("HAKO_CONFIG_PATH", expected["config"]),
        ("HAKO_PDU_ENDPOINT_RUNTIME_DIRS", expected["foundation_bin"]),
    )
    for name, required in path_contract:
        actual = env.get(name)
        if not _same_path(actual, required):
            errors.append(
                f"{name} does not match this repository: "
                f"expected={required}, actual={actual or '<missing>'}"
            )

    path_entries = [value for value in env.get("PATH", "").split(os.pathsep) if value]
    required_prefix = (expected["python_bin"], expected["foundation_bin"])
    for index, required in enumerate(required_prefix):
        actual = path_entries[index] if index < len(path_entries) else None
        if not _same_path(actual, required):
            errors.append(
                f"PATH entry {index + 1} does not select the managed Workspace: "
                f"expected={required}, actual={actual or '<missing>'}"
            )

    if env.get("PYTHONNOUSERSITE") != "1":
        errors.append(
            "PYTHONNOUSERSITE is not 1; ambient user site-packages may be visible"
        )
    if env.get("PYTHONPATH"):
        errors.append("PYTHONPATH is set; ambient Python modules may shadow Foundation")
    if env.get("PYTHONHOME"):
        errors.append("PYTHONHOME is set; ambient Python may override Foundation")
    return errors


def _print_recovery(
    errors: Sequence[str],
    root: Path,
    *,
    heading: str,
    stream: TextIO,
) -> None:
    print(heading, file=stream)
    print(f"  repository: {root.expanduser().resolve()}", file=stream)
    for error in errors:
        print(f"  - {error}", file=stream)
    print("", file=stream)
    print("Continue with caution. Ambient Python or paths may affect this command.", file=stream)
    print("", file=stream)
    print("Interactive:", file=stream)
    print("  python tools/workspace.py enter", file=stream)
    print("", file=stream)
    print("Non-interactive:", file=stream)
    print("  python tools/workspace.py run -- <command>", file=stream)


def warn_if_workspace_invalid(
    root: Path | None = None,
    environment: Mapping[str, str] | None = None,
    *,
    stream: TextIO | None = None,
) -> list[str]:
    """Warn about Workspace mismatches and return them without blocking the caller."""
    selected_root = (root or repository_root()).expanduser().resolve()
    errors = validate_workspace(selected_root, environment)
    if errors:
        _print_recovery(
            errors,
            selected_root,
            heading="[WARNING] Hakoniwa Workspace is not active for this repository.",
            stream=stream or sys.stderr,
        )
    return errors


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(
        description="Check the managed Hakoniwa Workspace identity"
    )
    result.add_argument(
        "--root",
        type=Path,
        default=None,
        help="Business Pack root (default: repository containing this tool)",
    )
    return result


def main(argv: Sequence[str] | None = None) -> int:
    args = parser().parse_args(argv)
    root = (args.root or repository_root()).expanduser().resolve()
    errors = validate_workspace(root)
    if errors:
        _print_recovery(
            errors,
            root,
            heading="[NG] Hakoniwa Workspace identity is invalid.",
            stream=sys.stderr,
        )
        return 1
    print(f"[OK] Hakoniwa Workspace identity: {root}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
