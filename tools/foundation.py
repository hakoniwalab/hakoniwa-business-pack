#!/usr/bin/env python3
from __future__ import annotations

import json
import platform
import subprocess
import sys
from pathlib import Path


TOOLS_DIR = Path(__file__).absolute().parent
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

from workspace_guard import warn_if_workspace_invalid


from foundation_lib.runtime import (
    FOUNDATION_PYTHON_IMPLEMENTATION,
    FOUNDATION_PYTHON_VERSION,
    VCPKG_COMPONENTS,
    FoundationError,
    WorkspacePaths,
    configure_foundation_toolchain,
    ensure_foundation_python,
    foundation_python_executable,
    foundation_toolchain_path,
    inspect_foundation_toolchain,
    load_foundation_toolchain,
    load_foundation_toolchain_file,
    prepare_workspace,
    print_paths,
    probe_python,
    repository_root,
    resolve_workspace,
    validate_foundation_python_probe,
    validate_recipe_id,
)

from foundation_lib.inspection import (
    ARTIFACT_PROBES,
    RECEIPT_REQUIRED_FIELDS,
    _host_contract,
    evaluate_component,
    inspect_core_runtime_config,
    inspect_foundation_impl,
    inspect_foundation_python,
    load_foundation_requirements,
    load_receipt,
)


def inspect_foundation(
    recipe: Path,
    prefix: Path,
    validate_core_config: bool = False,
) -> dict:
    return inspect_foundation_impl(
        recipe,
        prefix,
        validate_core_config,
        inspect_foundation_python_callback=inspect_foundation_python,
        inspect_foundation_toolchain_callback=inspect_foundation_toolchain,
    )


from foundation_lib.plan import (
    create_build_plan_impl,
    dependency_order,
    load_build_catalog,
)


def create_build_plan(
    recipe: Path,
    prefix: Path,
    components: dict[str, dict],
    business_pack_root: Path,
    force: set[str] | None = None,
) -> dict:
    return create_build_plan_impl(
        recipe,
        prefix,
        components,
        business_pack_root,
        force,
        load_foundation_requirements=load_foundation_requirements,
        inspect_foundation=inspect_foundation,
        load_receipt=load_receipt,
        evaluate_component=evaluate_component,
    )


from foundation_lib.build import (
    component_commands,
    normalize_core_config_for_windows,
    write_component_manifest,
)


def execute_build_plan(plan: dict, paths: WorkspacePaths) -> dict:
    if plan["blocked"]:
        raise FoundationError(
            "Foundation plan is blocked by UNKNOWN components: "
            + ", ".join(plan["blocked"])
        )
    validate_build_plan_sources(plan)
    python, python_contract = ensure_foundation_python(
        paths, Path(plan["recipe"])
    )
    print(
        "Foundation Python: "
        f"{python_contract['version']} ({python}) "
        f"SOABI={python_contract['soabi']}"
    )
    prepare_workspace(paths)
    for action in plan["actions"]:
        source = Path(action["source"])
        for command in component_commands(
            action["component"],
            source,
            action["operations"],
            paths,
            action.get("requirements"),
        ):
            if Path(command[0]) != python:
                raise FoundationError(
                    f"{action['component']}: component command does not use "
                    f"Foundation Python: {command[0]}"
                )
            print(f"> {subprocess.list2cmdline(command)}", flush=True)
            result = subprocess.run(command, cwd=source, check=False)
            if result.returncode != 0:
                message = (
                    f"{action['component']} command failed "
                    f"with exit code {result.returncode}"
                )
                toolchain_path = foundation_toolchain_path(paths)
                if platform.system() == "Windows" and not toolchain_path.is_file():
                    message += (
                        f". Foundation toolchain config for install prefix "
                        f"{paths.install_prefix} was not found at {toolchain_path}. "
                        "If this component needs vcpkg, configure this prefix with: "
                        f"python tools/foundation.py toolchain --recipe-id "
                        f"{Path(plan['recipe']).stem} --install-dir "
                        f"{paths.install_prefix} --vcpkg-root <vcpkg-root>"
                    )
                raise FoundationError(message)
            if action["component"] == "hakoniwa-core-pro":
                normalize_core_config_for_windows(
                    paths.foundation_config / "cpp_core_config.json",
                    paths.foundation_mmap,
                )
    final = inspect_foundation(
        Path(plan["recipe"]), paths.install_prefix, validate_core_config=True
    )
    if final["status"] != "SATISFIED":
        raise FoundationError(
            f"Foundation remains {final['status']} after build/install"
        )
    return final


def validate_build_plan_sources(plan: dict) -> None:
    invalid: list[str] = []
    for action in plan["actions"]:
        source = Path(action["source"])
        if not source.is_dir():
            invalid.append(
                f"{action['component']}: source directory is missing: {source}"
            )
            continue
        hako = source / "tools" / "hako.py"
        if not hako.is_file():
            invalid.append(
                f"{action['component']}: component tool is missing: {hako}"
            )
    if not invalid:
        return
    recipe = Path(plan["recipe"])
    details = "\n".join(f"  - {message}" for message in invalid)
    raise FoundationError(
        "Foundation source is missing or invalid:\n"
        f"{details}\n\n"
        "Materialize the selected Recipe first:\n"
        f"  python tools/recipe.py configure --recipe {recipe}"
    )


def print_build_plan(plan: dict, json_output: bool) -> None:
    if json_output:
        print(json.dumps(plan, ensure_ascii=False, indent=2))
        return
    print(f"Foundation plan: {plan['status']}")
    print(f"Dependency order: {' -> '.join(plan['dependency_order'])}")
    if plan["blocked"]:
        print(f"Blocked by UNKNOWN: {', '.join(plan['blocked'])}")
    if not plan["actions"]:
        if plan["status"] == "NEEDS_BUILD":
            print("Actions: initialize or validate Foundation Python 3.12")
        else:
            print("Actions: none (installed Foundation is reusable)")
        return
    print("Actions:")
    for action in plan["actions"]:
        operations = " -> ".join(action["operations"])
        print(
            f"  - {action['component']}: {action['reason']} "
            f"({operations})"
        )


def print_inspection(result: dict, json_output: bool) -> None:
    if json_output:
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return
    print(f"Foundation: {result['status']}")
    toolchain = result.get("toolchain")
    if isinstance(toolchain, dict):
        detail = (
            f"vcpkg_root={toolchain.get('vcpkg_root')} "
            f"path={toolchain.get('path')}"
            if toolchain.get("status") == "SATISFIED"
            else f"{toolchain.get('reason')} path={toolchain.get('path')}"
        )
        print(f"[{toolchain.get('status')}] Foundation toolchain: {detail}")
        print(f"  - required by: {', '.join(toolchain.get('required_by', []))}")
        if toolchain.get("remediation"):
            print(f"  - remediation: {toolchain['remediation']}")
    runtime_python = result.get("runtime", {}).get("python")
    if isinstance(runtime_python, dict):
        detail = (
            f"{runtime_python.get('version')} "
            f"SOABI={runtime_python.get('soabi')} "
            f"executable={runtime_python.get('executable')}"
            if runtime_python.get("status") == "SATISFIED"
            else (
                f"{runtime_python.get('reason')} "
                f"executable={runtime_python.get('executable')}"
            )
        )
        print(f"[{runtime_python.get('status')}] Foundation Python: {detail}")
    core_config = result.get("runtime", {}).get("core_config")
    if isinstance(core_config, dict):
        detail = (
            f"path={core_config.get('path')} mmap={core_config.get('core_mmap_path')}"
            if core_config.get("status") == "SATISFIED"
            else f"{core_config.get('reason')} path={core_config.get('path')}"
        )
        print(f"[{core_config.get('status')}] Core runtime config: {detail}")
    for component in result["components"]:
        print(f"[{component['status']}] {component['component']}")
        for reason in component["reasons"]:
            print(
                f"  - {reason['field']}: required={reason['required']!r}, "
                f"installed={reason['installed']!r}"
            )


from foundation_lib.cli import build_parser, run as run_cli


def main(argv: list[str] | None = None) -> int:
    return run_cli(argv, sys.modules[__name__])


if __name__ == "__main__":
    raise SystemExit(main())
