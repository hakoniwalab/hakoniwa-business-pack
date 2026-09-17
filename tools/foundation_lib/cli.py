"""Argument parsing and command dispatch for tools/foundation.py."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from types import ModuleType

from workdir import resolve_work_dir


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Manage the Business Pack local Hakoniwa Foundation workspace"
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    for command, help_text in (
        ("paths", "resolve workspace paths without creating directories"),
        ("prepare", "create the reusable Foundation and Recipe workspace"),
    ):
        subparser = subparsers.add_parser(command, help=help_text)
        subparser.add_argument("--recipe-id", required=True)
        subparser.add_argument("--json", action="store_true", dest="json_output")

    toolchain = subparsers.add_parser(
        "toolchain",
        help="persist explicit host toolchain selection under work/foundation/config",
    )
    toolchain.add_argument("--recipe-id", required=True)
    toolchain.add_argument("--install-dir", default=None)
    toolchain.add_argument("--vcpkg-root", required=True)

    doctor = subparsers.add_parser(
        "doctor",
        help="inspect Recipe requirements against installed Component Receipts",
    )
    doctor.add_argument("--recipe", required=True)
    doctor.add_argument("--install-dir", default=None)
    doctor.add_argument("--json", action="store_true", dest="json_output")

    plan = subparsers.add_parser(
        "plan",
        help="show dependency-ordered Foundation build/install actions",
    )
    plan.add_argument("--recipe", required=True)
    plan.add_argument("--install-dir", default=None)
    plan.add_argument("--catalog", default=None)
    plan.add_argument("--force", action="append", default=[])
    plan.add_argument("--json", action="store_true", dest="json_output")

    build = subparsers.add_parser(
        "build",
        help="execute the dependency-ordered plan and verify installed receipts",
    )
    build.add_argument("--recipe", required=True)
    build.add_argument("--install-dir", default=None)
    build.add_argument("--catalog", default=None)
    build.add_argument("--force", action="append", default=[])

    return parser


def run(argv: list[str] | None, api: ModuleType) -> int:
    args = build_parser().parse_args(argv)
    if args.command in {"toolchain", "doctor", "plan", "build"}:
        api.warn_if_workspace_invalid(api.repository_root())
    try:
        if args.command in {"doctor", "plan", "build"}:
            root = api.repository_root()
            recipe = Path(args.recipe)
            if not recipe.is_absolute():
                recipe = (Path.cwd() / recipe).resolve()
            work_root = resolve_work_dir(root)
            prefix = (
                Path(args.install_dir).resolve()
                if args.install_dir
                else work_root / "foundation" / "install"
            )
            if args.command in {"plan", "build"}:
                catalog = (
                    Path(args.catalog).resolve()
                    if args.catalog
                    else root / "catalog" / "foundation-components.json"
                )
                components = api.load_build_catalog(catalog)
                result = api.create_build_plan(
                    recipe,
                    prefix,
                    components,
                    root,
                    set(args.force),
                )
                if args.command == "plan":
                    api.validate_build_plan_sources(result)
                api.print_build_plan(
                    result, getattr(args, "json_output", False)
                )
                if args.command == "plan":
                    return 2 if result["blocked"] else 0
                paths = api.resolve_workspace(
                    root,
                    api.validate_recipe_id(recipe.stem),
                    prefix.parent,
                )
                if prefix.name != "install" or prefix != paths.install_prefix:
                    raise api.FoundationError(
                        "build install prefix must be "
                        "<workdir>/<foundation-name>/install"
                    )
                final = api.execute_build_plan(result, paths)
                api.print_inspection(final, False)
                return 0
            result = api.inspect_foundation(
                recipe, prefix, validate_core_config=True
            )
            api.print_inspection(result, args.json_output)
            return 0 if result["status"] == "SATISFIED" else 1
        root = api.repository_root()
        if args.command == "toolchain" and args.install_dir:
            prefix = Path(args.install_dir).resolve()
            paths = api.resolve_workspace(root, args.recipe_id, prefix.parent)
            if prefix.name != "install" or prefix != paths.install_prefix:
                raise api.FoundationError(
                    "toolchain install prefix must be "
                    "<workdir>/<foundation-name>/install"
                )
        else:
            paths = api.resolve_workspace(root, args.recipe_id)
        if args.command == "toolchain":
            output = api.configure_foundation_toolchain(
                paths, Path(args.vcpkg_root)
            )
            print(f"Foundation toolchain: {output}")
            print(
                "vcpkg_root: "
                f"{api.load_foundation_toolchain(paths)['vcpkg_root']}"
            )
            return 0
        if args.command == "prepare":
            api.prepare_workspace(paths)
        api.print_paths(paths, args.json_output)
        return 0
    except api.FoundationError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
