#!/usr/bin/env python3
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import webbrowser
from pathlib import Path

TOOLS_DIR = Path(__file__).absolute().parent
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

from recipe_portal import (
    PortalCommand,
    PortalEnvironment,
    PortalLink,
    write_recipe_portal,
)
from workspace_guard import warn_if_workspace_invalid


class RecipeGuideError(RuntimeError):
    pass


LOCAL_REQUIREMENTS_SCHEMA_VERSION = 1
LOCAL_REQUIREMENT_FIELDS = {"root", "source", "required_artifacts"}
LOCAL_ROOT_FIELDS = {"default_path", "override_env", "relative_to"}
LOCAL_SOURCE_FIELDS = {"type", "url", "revision"}
LOCAL_ARTIFACT_FIELDS = {"path", "kind"}
LOCAL_ARTIFACT_KINDS = {"file", "directory", "executable"}
RECIPE_RUNTIME_SCHEMA_VERSION = 1
RECIPE_RUNTIME_FIELDS = {"environment", "launcher"}
RECIPE_LAUNCHER_FIELDS = {"template", "output", "mode"}
RECIPE_LAUNCHER_MODES = {"immediate", "activate-only", "serve"}
SECRET_ENV_MARKERS = ("TOKEN", "SECRET", "PASSWORD", "CREDENTIAL", "API_KEY")
RUNTIME_PLACEHOLDER = re.compile(r"\$\{([^{}]+)\}")


def root() -> Path:
    return Path(__file__).absolute().parents[1]


def load_foundation_module():
    script = Path(__file__).with_name("foundation.py")
    spec = importlib.util.spec_from_file_location(
        "business_pack_foundation_recipe_guide", script
    )
    if spec is None or spec.loader is None:
        raise RecipeGuideError(f"cannot load Foundation helper: {script}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def load_workspace_module():
    script = Path(__file__).with_name("workspace.py")
    spec = importlib.util.spec_from_file_location(
        "business_pack_workspace_recipe_runtime", script
    )
    if spec is None or spec.loader is None:
        raise RecipeGuideError(f"cannot load Workspace helper: {script}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def load_recipe(path: Path) -> dict:
    recipe = path.expanduser().absolute()
    exporter = root() / "recipes" / "tools" / "export_recipe_json.rb"
    result = subprocess.run(
        ["ruby", str(exporter), str(recipe)],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise RecipeGuideError(
            result.stderr.strip() or f"failed to load Recipe: {recipe}"
        )
    try:
        data = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise RecipeGuideError(f"invalid Recipe exporter output: {exc}") from exc
    if not isinstance(data, dict):
        raise RecipeGuideError("Recipe root must be a mapping")
    recipe_id = data.get("id")
    if not isinstance(recipe_id, str) or not recipe_id:
        raise RecipeGuideError("Recipe id is missing")
    if recipe.stem != recipe_id:
        raise RecipeGuideError("Recipe file name must match its id")
    return data


def recipe_repository_root(recipe_path: Path) -> Path:
    start = recipe_path.expanduser().resolve().parent
    for candidate in (start, *start.parents):
        if (candidate / ".git").exists():
            return candidate.resolve()
    raise RecipeGuideError(
        f"Recipe repository root was not found from: {recipe_path}"
    )


def _required_string(value: object, path: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise RecipeGuideError(f"{path} must be a non-empty string")
    return value.strip()


def _exact_fields(value: object, expected: set[str], path: str) -> dict:
    if not isinstance(value, dict):
        raise RecipeGuideError(f"{path} must be a mapping")
    actual = set(value)
    missing = sorted(expected - actual)
    unknown = sorted(actual - expected)
    if missing:
        raise RecipeGuideError(f"{path} missing fields: {', '.join(missing)}")
    if unknown:
        raise RecipeGuideError(f"{path} has unknown fields: {', '.join(unknown)}")
    return value


def validate_local_requirements(data: dict) -> dict[str, dict]:
    requirements = data.get("recipe_local_requirements")
    if requirements is None:
        return {}
    schema_version = data.get("recipe_local_requirements_schema_version")
    # Existing Recipes used this field as human-readable provenance.  Only the
    # explicitly versioned form is an executable dependency contract.
    if schema_version is None:
        return {}
    if schema_version != LOCAL_REQUIREMENTS_SCHEMA_VERSION:
        raise RecipeGuideError(
            "recipe_local_requirements_schema_version must be 1 when "
            "recipe_local_requirements is declared"
        )
    if not isinstance(requirements, dict) or not requirements:
        raise RecipeGuideError("recipe_local_requirements must be a non-empty mapping")

    result: dict[str, dict] = {}
    for dependency_id, raw in requirements.items():
        dependency_path = f"recipe_local_requirements.{dependency_id}"
        _required_string(dependency_id, "recipe_local_requirements dependency id")
        requirement = _exact_fields(raw, LOCAL_REQUIREMENT_FIELDS, dependency_path)
        root_spec = _exact_fields(
            requirement["root"], LOCAL_ROOT_FIELDS, f"{dependency_path}.root"
        )
        default_path = _required_string(
            root_spec["default_path"], f"{dependency_path}.root.default_path"
        )
        override_env = _required_string(
            root_spec["override_env"], f"{dependency_path}.root.override_env"
        )
        if not override_env.replace("_", "A").isalnum() or not override_env[0].isalpha():
            raise RecipeGuideError(
                f"{dependency_path}.root.override_env must be an environment variable name"
            )
        if root_spec["relative_to"] != "recipe_repository":
            raise RecipeGuideError(
                f"{dependency_path}.root.relative_to must be recipe_repository"
            )

        source = requirement["source"]
        if not isinstance(source, dict):
            raise RecipeGuideError(f"{dependency_path}.source must be a mapping")
        unknown_source = sorted(set(source) - LOCAL_SOURCE_FIELDS)
        if unknown_source:
            raise RecipeGuideError(
                f"{dependency_path}.source has unknown fields: {', '.join(unknown_source)}"
            )
        source_type = _required_string(
            source.get("type"), f"{dependency_path}.source.type"
        )
        if source_type not in {"local", "git"}:
            raise RecipeGuideError(
                f"{dependency_path}.source.type must be local or git"
            )
        if source_type == "git":
            _required_string(source.get("url"), f"{dependency_path}.source.url")
        elif set(source) != {"type"}:
            raise RecipeGuideError(
                f"{dependency_path}.source type local must contain only type"
            )
        if "revision" in source:
            _required_string(source["revision"], f"{dependency_path}.source.revision")

        artifacts = requirement["required_artifacts"]
        if not isinstance(artifacts, list) or not artifacts:
            raise RecipeGuideError(
                f"{dependency_path}.required_artifacts must be a non-empty list"
            )
        for index, artifact_raw in enumerate(artifacts):
            artifact_path = f"{dependency_path}.required_artifacts[{index}]"
            artifact = _exact_fields(
                artifact_raw, LOCAL_ARTIFACT_FIELDS, artifact_path
            )
            relative = Path(_required_string(artifact["path"], f"{artifact_path}.path"))
            if relative.is_absolute() or ".." in relative.parts:
                raise RecipeGuideError(
                    f"{artifact_path}.path must stay under the dependency root"
                )
            if artifact["kind"] not in LOCAL_ARTIFACT_KINDS:
                raise RecipeGuideError(
                    f"{artifact_path}.kind must be file, directory, or executable"
                )
        result[dependency_id] = requirement
    return result


def resolve_local_requirement_root(
    recipe_path: Path,
    requirement: dict,
    environ: dict[str, str] | None = None,
) -> tuple[Path, bool]:
    environ = os.environ if environ is None else environ
    root_spec = requirement["root"]
    override = environ.get(root_spec["override_env"], "").strip()
    selected = Path(override).expanduser() if override else Path(root_spec["default_path"])
    if not selected.is_absolute():
        selected = recipe_repository_root(recipe_path) / selected
    return selected.resolve(), bool(override)


def native_platform_context() -> dict[str, str]:
    if sys.platform == "darwin":
        prefix = "mac"
    elif sys.platform.startswith("linux"):
        prefix = "linux"
    elif sys.platform == "win32":
        prefix = "win"
    else:
        raise RecipeGuideError(f"unsupported native Recipe platform: {sys.platform}")
    return {
        "NATIVE_BIN_PREFIX": prefix,
        "NATIVE_EXECUTABLE_SUFFIX": ".exe" if sys.platform == "win32" else "",
    }


def validate_recipe_runtime(data: dict) -> dict:
    runtime = data.get("recipe_runtime")
    if runtime is None:
        return {}
    if data.get("recipe_runtime_schema_version") != RECIPE_RUNTIME_SCHEMA_VERSION:
        raise RecipeGuideError(
            "recipe_runtime_schema_version must be 1 when recipe_runtime is declared"
        )
    runtime = _exact_fields(runtime, RECIPE_RUNTIME_FIELDS, "recipe_runtime")
    environment = runtime["environment"]
    if not isinstance(environment, dict) or not environment:
        raise RecipeGuideError("recipe_runtime.environment must be a non-empty mapping")
    for name, value in environment.items():
        _required_string(name, "recipe_runtime.environment variable name")
        if not name.replace("_", "A").isalnum() or not name[0].isalpha():
            raise RecipeGuideError(
                f"recipe_runtime.environment has invalid variable name: {name}"
            )
        if any(marker in name.upper() for marker in SECRET_ENV_MARKERS):
            raise RecipeGuideError(
                f"recipe_runtime.environment must not persist secret-like variable: {name}"
            )
        _required_string(value, f"recipe_runtime.environment.{name}")

    launcher = _exact_fields(
        runtime["launcher"], RECIPE_LAUNCHER_FIELDS, "recipe_runtime.launcher"
    )
    for field in ("template", "output"):
        value = Path(_required_string(launcher[field], f"recipe_runtime.launcher.{field}"))
        if value.is_absolute() or ".." in value.parts:
            raise RecipeGuideError(
                f"recipe_runtime.launcher.{field} must be a safe relative path"
            )
    if launcher["mode"] not in RECIPE_LAUNCHER_MODES:
        raise RecipeGuideError(
            "recipe_runtime.launcher.mode must be immediate, activate-only, or serve"
        )
    return runtime


def _runtime_context(recipe_path: Path, data: dict) -> tuple[dict[str, str], object]:
    foundation = load_foundation_module()
    paths = foundation.resolve_workspace(root(), data["id"])
    context = {
        "RECIPE_REPOSITORY": str(recipe_repository_root(recipe_path)),
        "RECIPE_WORKSPACE": str(paths.recipe_root),
        "FOUNDATION_ROOT": str(paths.foundation_root),
        "FOUNDATION_INSTALL": str(paths.install_prefix),
        "FOUNDATION_PYTHON": str(
            foundation.foundation_python_executable(paths.foundation_python)
        ),
    }
    context.update(native_platform_context())
    for dependency_id, requirement in validate_local_requirements(data).items():
        dependency_root, _ = resolve_local_requirement_root(recipe_path, requirement)
        context[f"DEPENDENCY:{dependency_id}"] = str(dependency_root)
    return context, paths


def _expand_runtime_value(value: str, context: dict[str, str], label: str) -> str:
    unresolved: list[str] = []

    def replace(match: re.Match[str]) -> str:
        key = match.group(1)
        if key not in context:
            unresolved.append(key)
            return match.group(0)
        return context[key]

    expanded = RUNTIME_PLACEHOLDER.sub(replace, value)
    if unresolved:
        raise RecipeGuideError(
            f"{label} has unknown placeholders: {', '.join(sorted(set(unresolved)))}"
        )
    return expanded


def resolve_recipe_environment(recipe_path: Path, data: dict) -> tuple[dict[str, str], object]:
    runtime = validate_recipe_runtime(data)
    context, paths = _runtime_context(recipe_path, data)
    if not runtime:
        return {}, paths
    resolved = {
        name: _expand_runtime_value(
            value, context, f"recipe_runtime.environment.{name}"
        )
        for name, value in runtime["environment"].items()
    }
    return resolved, paths


def _atomic_write_text(path: Path, content: str, *, executable: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(content, encoding="utf-8")
    if executable:
        temporary.chmod(temporary.stat().st_mode | 0o111)
    temporary.replace(path)


def _render_recipe_activate(recipe_id: str, variables: dict[str, str]) -> str:
    managed = ("HAKONIWA_RECIPE_ACTIVE", "HAKONIWA_RECIPE_ID", *variables)
    lines = [
        "# Generated by tools/recipe.py configure. Source this file; do not execute it.",
        'if [ "${HAKONIWA_WORKSPACE_ACTIVE-}" != "1" ]; then',
        '  echo "Enter the Hakoniwa Workspace before activating a Recipe." >&2',
        "  return 1 2>/dev/null || exit 1",
        "fi",
        'if [ -n "${HAKONIWA_RECIPE_ACTIVE-}" ]; then',
        '  echo "A Hakoniwa Recipe environment is already active." >&2',
        "  return 1 2>/dev/null || exit 1",
        "fi",
        "",
    ]
    for name in managed:
        lines.extend(
            [
                f"_HAKONIWA_RECIPE_OLD_{name}_SET=${{{name}+x}}",
                f"_HAKONIWA_RECIPE_OLD_{name}=${{{name}-}}",
            ]
        )
    lines.append("deactivate_hakoniwa_recipe() {")
    for name in managed:
        lines.extend(
            [
                f'  if [ "${{_HAKONIWA_RECIPE_OLD_{name}_SET-}}" = x ]; then',
                f'    export {name}="${{_HAKONIWA_RECIPE_OLD_{name}}}"',
                "  else",
                f"    unset {name}",
                "  fi",
                f"  unset _HAKONIWA_RECIPE_OLD_{name}_SET _HAKONIWA_RECIPE_OLD_{name}",
            ]
        )
    lines.extend(
        [
            "  unset -f deactivate_hakoniwa_recipe 2>/dev/null || true",
            "}",
            f"export HAKONIWA_RECIPE_ACTIVE={shlex.quote(recipe_id)}",
            f"export HAKONIWA_RECIPE_ID={shlex.quote(recipe_id)}",
        ]
    )
    lines.extend(
        f"export {name}={shlex.quote(value)}" for name, value in variables.items()
    )
    lines.extend(
        [
            f'echo "Activated Hakoniwa Recipe: {recipe_id}"',
            'echo "Run deactivate_hakoniwa_recipe to restore the previous Recipe environment."',
            "",
        ]
    )
    return "\n".join(lines)


def _ps_quote(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _render_recipe_activate_powershell(recipe_id: str, variables: dict[str, str]) -> str:
    managed = ("HAKONIWA_RECIPE_ACTIVE", "HAKONIWA_RECIPE_ID", *variables)
    lines = [
        "# Generated by tools/recipe.py configure. Dot-source this file.",
        "if ($env:HAKONIWA_WORKSPACE_ACTIVE -ne '1') { throw 'Enter the Hakoniwa Workspace before activating a Recipe.' }",
        "if ($env:HAKONIWA_RECIPE_ACTIVE) { throw 'A Hakoniwa Recipe environment is already active.' }",
        "$global:HAKONIWA_RECIPE_SAVED_ENV = @{}",
    ]
    for name in managed:
        lines.append(
            f"$global:HAKONIWA_RECIPE_SAVED_ENV[{_ps_quote(name)}] = "
            f"[Environment]::GetEnvironmentVariable({_ps_quote(name)}, 'Process')"
        )
    lines.extend(
        [
            "function global:Exit-HakoniwaRecipe {",
            "  foreach ($entry in $global:HAKONIWA_RECIPE_SAVED_ENV.GetEnumerator()) {",
            "    if ($null -eq $entry.Value) { Remove-Item (\"Env:\\{0}\" -f $entry.Key) -ErrorAction SilentlyContinue }",
            "    else { [Environment]::SetEnvironmentVariable($entry.Key, $entry.Value, 'Process') }",
            "  }",
            "  Remove-Variable HAKONIWA_RECIPE_SAVED_ENV -Scope Global -ErrorAction SilentlyContinue",
            "  Remove-Item Function:\\Exit-HakoniwaRecipe -ErrorAction SilentlyContinue",
            "}",
            f"$env:HAKONIWA_RECIPE_ACTIVE = {_ps_quote(recipe_id)}",
            f"$env:HAKONIWA_RECIPE_ID = {_ps_quote(recipe_id)}",
        ]
    )
    lines.extend(f"$env:{name} = {_ps_quote(value)}" for name, value in variables.items())
    lines.extend(
        [
            f"Write-Host {_ps_quote('Activated Hakoniwa Recipe: ' + recipe_id)}",
            "Write-Host 'Run Exit-HakoniwaRecipe to restore the previous Recipe environment.'",
            "",
        ]
    )
    return "\n".join(lines)


def materialize_recipe_runtime(recipe_path: Path, data: dict) -> dict | None:
    runtime = validate_recipe_runtime(data)
    if not runtime:
        return None
    variables, paths = resolve_recipe_environment(recipe_path, data)
    environment_path = paths.recipe_root / "environment.json"
    activate_path = paths.recipe_root / "activate"
    activate_powershell_path = paths.recipe_root / "Activate.ps1"
    launcher = runtime["launcher"]
    template_path = recipe_repository_root(recipe_path) / launcher["template"]
    if not template_path.is_file():
        raise RecipeGuideError(f"Recipe Launcher template not found: {template_path}")
    launcher_path = paths.recipe_root / launcher["output"]
    payload = {
        "schema_version": RECIPE_RUNTIME_SCHEMA_VERSION,
        "recipe_id": data["id"],
        "variables": variables,
        "launcher": {"path": str(launcher_path), "mode": launcher["mode"]},
    }
    _atomic_write_text(
        environment_path, json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    )
    _atomic_write_text(
        activate_path,
        _render_recipe_activate(data["id"], variables),
        executable=True,
    )
    _atomic_write_text(
        activate_powershell_path,
        _render_recipe_activate_powershell(data["id"], variables),
    )
    _atomic_write_text(launcher_path, template_path.read_text(encoding="utf-8"))
    return payload


def inspect_local_requirements(
    recipe_path: Path,
    data: dict,
    environ: dict[str, str] | None = None,
) -> list[dict]:
    requirements = validate_local_requirements(data)
    platform_context = native_platform_context()
    results: list[dict] = []
    for dependency_id, requirement in requirements.items():
        dependency_root, overridden = resolve_local_requirement_root(
            recipe_path, requirement, environ
        )
        missing: list[str] = []
        if not dependency_root.is_dir():
            missing.append("dependency root")
        else:
            for artifact in requirement["required_artifacts"]:
                artifact_path = _expand_runtime_value(
                    artifact["path"],
                    platform_context,
                    f"recipe_local_requirements.{dependency_id}.required_artifacts.path",
                )
                target = dependency_root / artifact_path
                kind = artifact["kind"]
                satisfied = (
                    target.is_file()
                    if kind == "file"
                    else target.is_dir()
                    if kind == "directory"
                    else target.is_file() and os.access(target, os.X_OK)
                )
                if not satisfied:
                    missing.append(f"{kind}:{artifact_path}")
        results.append(
            {
                "dependency": dependency_id,
                "status": "SATISFIED" if not missing else "MISSING",
                "root": str(dependency_root),
                "overridden": overridden,
                "override_env": requirement["root"]["override_env"],
                "missing": missing,
            }
        )
    return results


def print_local_inspection(results: list[dict]) -> None:
    for result in results:
        print(
            f"[{result['status']}] Recipe dependency {result['dependency']}: "
            f"{result['root']}"
        )
        for missing in result["missing"]:
            print(f"  - missing {missing}")
        if result["status"] != "SATISFIED":
            print(f"  - override with {result['override_env']}")


def recipe_python_requirements(recipe_path: Path, data: dict) -> Path | None:
    runtime_dependencies = data.get("runtime_dependencies")
    if not isinstance(runtime_dependencies, dict):
        return None
    python = runtime_dependencies.get("python")
    if not isinstance(python, dict):
        return None
    value = python.get("requirements")
    if value is None:
        return None
    path = Path(_required_string(value, "runtime_dependencies.python.requirements"))
    if path.is_absolute():
        raise RecipeGuideError(
            "runtime_dependencies.python.requirements must be relative to the Recipe repository"
        )
    resolved = (recipe_repository_root(recipe_path) / path).resolve()
    repository = recipe_repository_root(recipe_path)
    if repository not in resolved.parents:
        raise RecipeGuideError(
            "runtime_dependencies.python.requirements must stay inside the Recipe repository"
        )
    return resolved


def _clone_repository(url: str, target: Path, *, revision: str | None = None) -> None:
    if target.exists():
        raise RecipeGuideError(f"refusing to overwrite clone target: {target}")
    # Recipe dependencies are runtime source trees, not shallow file bundles.
    # Their declared artifacts may live in nested submodules, so every managed
    # clone materializes the complete pinned repository graph by default.
    command = ["git", "clone", "--recurse-submodules"]
    if revision:
        command.extend(["--branch", revision, "--single-branch"])
    command.extend([url, str(target)])
    print(">", subprocess.list2cmdline(command), flush=True)
    completed = subprocess.run(command, cwd=target.parent, check=False)
    if completed.returncode:
        raise RecipeGuideError(
            f"git clone failed with exit code {completed.returncode}: {target}"
        )


def _checkout_revision(target: Path) -> str | None:
    if not (target / ".git").exists():
        return None
    completed = subprocess.run(
        ["git", "-C", str(target), "rev-parse", "HEAD"],
        capture_output=True,
        text=True,
        check=False,
    )
    revision = completed.stdout.strip()
    return revision if completed.returncode == 0 and revision else None


def _missing_source_artifacts(source: dict) -> list[str]:
    target = Path(source["target"])
    if not target.is_dir():
        return ["directory:."]
    missing: list[str] = []
    for artifact in source["required_artifacts"]:
        path = target / artifact["path"]
        kind = artifact["kind"]
        satisfied = (
            path.is_file()
            if kind == "file"
            else path.is_dir()
            if kind == "directory"
            else path.is_file() and os.access(path, os.X_OK)
        )
        if not satisfied:
            missing.append(f"{kind}:{artifact['path']}")
    return missing


def _resolve_source_requirement(
    *,
    source_id: str,
    kind: str,
    target: Path,
    source_type: str,
    repository: str | None,
    revision: str | None,
    required_artifacts: list[dict],
    clone_boundary: Path,
    overridden: bool = False,
    override_env: str | None = None,
) -> dict:
    target = target.expanduser().resolve()
    if source_type == "git" and (
        not isinstance(repository, str) or not repository.strip()
    ):
        raise RecipeGuideError(
            f"{kind} source {source_id} has no repository URL: {target}"
        )
    if revision is not None and (
        not isinstance(revision, str) or not revision.strip()
    ):
        raise RecipeGuideError(
            f"{kind} source {source_id} has an invalid revision"
        )

    if target.exists():
        action = "reuse"
    elif source_type != "git":
        action = "provide-local"
    elif overridden:
        action = "provide-overridden-path"
    else:
        if target.parent != clone_boundary.expanduser().resolve():
            raise RecipeGuideError(
                f"automatic {kind} source is outside the declared sibling "
                f"boundary: target={target}, boundary={clone_boundary}"
            )
        action = "clone"

    result = {
        "id": source_id,
        "kind": kind,
        "action": action,
        "target": str(target),
        "repository": repository,
        "revision": revision,
        "required_artifacts": required_artifacts,
        "override_env": override_env,
        "provenance": {
            "mode": "existing-checkout" if action == "reuse" else action,
            "requested_revision": revision,
            "resolved_revision": (
                _checkout_revision(target) if action == "reuse" else None
            ),
            "reproducibility": (
                "pinned"
                if revision
                else "local"
                if source_type == "local"
                else "unpinned"
            ),
        },
    }
    if action == "reuse":
        missing = _missing_source_artifacts(result)
        if missing:
            raise RecipeGuideError(
                f"existing {kind} source is invalid: {source_id} target={target}; "
                f"missing={', '.join(missing)}"
            )
    return result


def materialize_sources(sources: list[dict]) -> None:
    unresolved = [
        source
        for source in sources
        if source["action"] not in {"clone", "reuse"}
    ]
    if unresolved:
        details = ", ".join(
            f"{source['kind']}:{source['id']} ({source['action']})"
            for source in unresolved
        )
        raise RecipeGuideError(
            "Recipe has source dependencies that cannot be materialized "
            f"automatically: {details}"
        )

    for source in sources:
        if source["action"] != "clone":
            continue
        _clone_repository(
            source["repository"],
            Path(source["target"]),
            revision=source.get("revision"),
        )

    for source in sources:
        missing = _missing_source_artifacts(source)
        if missing:
            raise RecipeGuideError(
                f"materialized {source['kind']} source is invalid: "
                f"{source['id']} target={source['target']}; "
                f"missing={', '.join(missing)}"
            )


def create_recipe_plan(recipe_path: Path, data: dict) -> dict:
    foundation = load_foundation_module()
    business_root = root()
    paths = foundation.resolve_workspace(business_root, data["id"])
    foundation_plan = None
    sources: list[dict] = []
    recipe_repo = recipe_repository_root(recipe_path)
    clone_boundary = recipe_repo.parent
    if isinstance(data.get("foundation_requirements"), dict):
        catalog_path = business_root / "catalog" / "foundation-components.json"
        components = foundation.load_build_catalog(catalog_path)
        foundation_plan = foundation.create_build_plan(
            recipe_path,
            paths.install_prefix,
            components,
            business_root,
        )
        for action in foundation_plan["actions"]:
            source = Path(action["source"])
            component = action["component"]
            repository_url = components[component].get("repository")
            sources.append(
                _resolve_source_requirement(
                    source_id=component,
                    kind="foundation",
                    target=source,
                    source_type="git",
                    repository=repository_url,
                    revision=components[component].get("revision"),
                    required_artifacts=[
                        {"path": "tools/hako.py", "kind": "file"}
                    ],
                    clone_boundary=business_root.parent,
                )
            )

    requirements = validate_local_requirements(data)
    platform_context = native_platform_context() if requirements else {}
    for dependency_id, requirement in requirements.items():
        target, overridden = resolve_local_requirement_root(recipe_path, requirement)
        source = requirement["source"]
        artifacts = [
            {
                "path": _expand_runtime_value(
                    artifact["path"],
                    platform_context,
                    f"recipe_local_requirements.{dependency_id}.required_artifacts.path",
                ),
                "kind": artifact["kind"],
            }
            for artifact in requirement["required_artifacts"]
        ]
        sources.append(
            _resolve_source_requirement(
                source_id=dependency_id,
                kind="recipe",
                target=target,
                source_type=source["type"],
                repository=source.get("url"),
                revision=source.get("revision"),
                required_artifacts=artifacts,
                clone_boundary=clone_boundary,
                overridden=overridden,
                override_env=requirement["root"]["override_env"],
            )
        )

    python_requirements = recipe_python_requirements(recipe_path, data)
    runtime = validate_recipe_runtime(data)
    return {
        "foundation": foundation_plan,
        "sources": sources,
        "python_requirements": str(python_requirements) if python_requirements else None,
        "runtime": (
            {
                "action": "materialize",
                "environment": f"work/recipes/{data['id']}/environment.json",
                "launcher": f"work/recipes/{data['id']}/{runtime['launcher']['output']}",
            }
            if runtime
            else None
        ),
    }


def print_recipe_plan(plan: dict) -> None:
    print("Recipe plan:")
    for source in plan["sources"]:
        label = (
            f"Foundation {source['id']}"
            if source["kind"] == "foundation"
            else f"Recipe dependency {source['id']}"
        )
        if source["action"] == "clone":
            revision = source["revision"] or "unpinned"
            print(
                f"  - clone {label}: {source['repository']} -> "
                f"{source['target']} (revision={revision})"
            )
        elif source["action"] == "reuse":
            resolved = source["provenance"]["resolved_revision"] or "unknown"
            requested = source["revision"] or "unpinned"
            print(
                f"  - reuse {label}: {source['target']} "
                f"(requested={requested}, resolved={resolved})"
            )
        else:
            print(
                f"  - {source['action']} for {label}: {source['target']} "
                f"(override={source['override_env']})"
            )
    foundation_plan = plan["foundation"]
    if foundation_plan is not None:
        print(f"  - Foundation status: {foundation_plan['status']}")
        for action in foundation_plan["actions"]:
            print(
                f"  - Foundation {action['component']}: "
                f"{', '.join(action['operations'])} ({action['reason']})"
            )
    if plan["python_requirements"]:
        print(
            "  - install Recipe Python requirements into Foundation Python: "
            f"{plan['python_requirements']}"
        )
    if plan["runtime"]:
        print(
            "  - materialize Recipe environment and Launcher: "
            f"{plan['runtime']['environment']}, {plan['runtime']['launcher']}"
        )
    if (
        not plan["sources"]
        and (foundation_plan is None or not foundation_plan["actions"])
        and not plan["python_requirements"]
        and not plan["runtime"]
    ):
        print("  - no actions")


def configure_recipe(recipe_path: Path, data: dict) -> int:
    plan = create_recipe_plan(recipe_path, data)
    print_recipe_plan(plan)
    materialize_sources(plan["sources"])

    foundation = load_foundation_module()
    paths = foundation.resolve_workspace(root(), data["id"])
    if isinstance(data.get("foundation_requirements"), dict):
        components = foundation.load_build_catalog(
            root() / "catalog" / "foundation-components.json"
        )
        foundation_plan = foundation.create_build_plan(
            recipe_path,
            paths.install_prefix,
            components,
            root(),
        )
        if foundation_plan["blocked"]:
            raise RecipeGuideError(
                "Foundation plan is blocked: " + ", ".join(foundation_plan["blocked"])
            )
        foundation.execute_build_plan(foundation_plan, paths)

    requirements = recipe_python_requirements(recipe_path, data)
    if requirements is not None:
        if not requirements.is_file():
            raise RecipeGuideError(f"Recipe Python requirements not found: {requirements}")
        python = foundation.foundation_python_executable(paths.foundation_python)
        command = [str(python), "-m", "pip", "install", "-r", str(requirements)]
        print(">", subprocess.list2cmdline(command), flush=True)
        completed = subprocess.run(command, cwd=recipe_repository_root(recipe_path), check=False)
        if completed.returncode:
            raise RecipeGuideError(
                f"Recipe Python dependency installation failed: exit={completed.returncode}"
            )
    materialized = materialize_recipe_runtime(recipe_path, data)
    if materialized is not None:
        print(
            "Recipe environment: "
            f"{paths.recipe_root / 'environment.json'}"
        )
        print(f"Recipe Launcher   : {materialized['launcher']['path']}")
    return doctor_recipe(recipe_path, data)


def _launcher_environment(recipe_path: Path, data: dict, variables: dict[str, str]) -> dict[str, str]:
    workspace = load_workspace_module()
    env = workspace.build_environment(workspace.resolve_workspace(root()))
    env.update(variables)
    env["HAKONIWA_RECIPE_ACTIVE"] = data["id"]
    env["HAKONIWA_RECIPE_ID"] = data["id"]
    return env


def _substitute_launcher_environment(content: str, env: dict[str, str]) -> str:
    pattern = re.compile(r"\$\{([A-Za-z0-9_]+)(?::-?([^}]+))?\}")

    def replace(match: re.Match[str]) -> str:
        name = match.group(1)
        if name in {"asset", "timestamp"}:
            return match.group(0)
        if name in env:
            return env[name]
        default = match.group(2)
        return default if default is not None else match.group(0)

    return pattern.sub(replace, content)


def inspect_recipe_runtime(recipe_path: Path, data: dict) -> dict:
    runtime = validate_recipe_runtime(data)
    if not runtime:
        return {"status": "NOT_DECLARED", "reasons": []}
    variables, paths = resolve_recipe_environment(recipe_path, data)
    environment_path = paths.recipe_root / "environment.json"
    launcher_path = paths.recipe_root / runtime["launcher"]["output"]
    reasons: list[str] = []
    payload: dict = {}
    if not environment_path.is_file():
        reasons.append(f"environment.json is missing: {environment_path}")
    else:
        try:
            payload = json.loads(environment_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            reasons.append(f"environment.json is invalid: {exc}")
        else:
            if payload.get("schema_version") != RECIPE_RUNTIME_SCHEMA_VERSION:
                reasons.append("environment.json schema_version is incompatible")
            if payload.get("recipe_id") != data["id"]:
                reasons.append("environment.json recipe_id does not match")
            if payload.get("variables") != variables:
                reasons.append("environment.json is stale; rerun configure")
    for activation in (paths.recipe_root / "activate", paths.recipe_root / "Activate.ps1"):
        if not activation.is_file():
            reasons.append(f"Recipe activation is missing: {activation}")
    if not launcher_path.is_file():
        reasons.append(f"Recipe Launcher is missing: {launcher_path}")
    else:
        env = _launcher_environment(recipe_path, data, variables)
        try:
            substituted = _substitute_launcher_environment(
                launcher_path.read_text(encoding="utf-8"), env
            )
            launcher = json.loads(substituted)
        except (OSError, json.JSONDecodeError) as exc:
            reasons.append(f"Recipe Launcher is invalid: {exc}")
        else:
            unresolved = sorted(
                {
                    item
                    for item in re.findall(r"\$\{([A-Z][A-Z0-9_]*)[^}]*\}", substituted)
                }
            )
            if unresolved:
                reasons.append(
                    "Recipe Launcher has unresolved environment variables: "
                    + ", ".join(unresolved)
                )
            defaults = launcher.get("defaults", {})
            default_env = defaults.get("env", {}) if isinstance(defaults, dict) else {}
            prepend = default_env.get("prepend", {}) if isinstance(default_env, dict) else {}
            for key in ("lib_path", "PATH"):
                values = prepend.get(key, []) if isinstance(prepend, dict) else []
                for value in values if isinstance(values, list) else []:
                    target = Path(value)
                    if target.is_absolute() and not target.is_dir():
                        reasons.append(f"Launcher {key} directory is missing: {target}")
            for asset in launcher.get("assets", []):
                if not isinstance(asset, dict):
                    continue
                name = asset.get("name", "unknown")
                cwd = Path(str(asset.get("cwd", defaults.get("cwd", "."))))
                if not cwd.is_absolute():
                    cwd = (launcher_path.parent / cwd).resolve()
                if not cwd.is_dir():
                    reasons.append(f"Launcher asset {name} cwd is missing: {cwd}")
                command = str(asset.get("command", ""))
                if not command:
                    reasons.append(f"Launcher asset {name} command is missing")
                elif os.path.sep in command:
                    executable = Path(command)
                    if not executable.is_file() or not os.access(executable, os.X_OK):
                        reasons.append(
                            f"Launcher asset {name} command is not executable: {executable}"
                        )
                elif shutil.which(command, path=env.get("PATH")) is None:
                    reasons.append(
                        f"Launcher asset {name} command is not on PATH: {command}"
                    )
    return {
        "status": "SATISFIED" if not reasons else "MISSING",
        "reasons": reasons,
        "environment": str(environment_path),
        "launcher": str(launcher_path),
    }


def launch_recipe(recipe_path: Path, data: dict) -> int:
    inspection = inspect_recipe_runtime(recipe_path, data)
    if inspection["status"] != "SATISFIED":
        for reason in inspection["reasons"]:
            print(f"[NG] {reason}", file=sys.stderr)
        raise RecipeGuideError("Recipe runtime is not ready; run configure first")
    variables, paths = resolve_recipe_environment(recipe_path, data)
    runtime = validate_recipe_runtime(data)
    launcher_path = paths.recipe_root / runtime["launcher"]["output"]
    foundation = load_foundation_module()
    python = foundation.foundation_python_executable(paths.foundation_python)
    command = [
        str(python),
        "-m",
        "hakoniwa_pdu.apps.launcher.hako_launcher",
        "--mode",
        runtime["launcher"]["mode"],
        str(launcher_path),
    ]
    print(">", subprocess.list2cmdline(command), flush=True)
    process = subprocess.Popen(
        command,
        cwd=recipe_repository_root(recipe_path),
        env=_launcher_environment(recipe_path, data, variables),
    )
    try:
        return process.wait()
    except KeyboardInterrupt:
        # The foreground child receives the same Ctrl+C and owns the normal
        # Launcher termination path.  Do not obscure its clean shutdown with a
        # wrapper traceback; only terminate if it fails to finish promptly.
        try:
            return process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.terminate()
            return process.wait(timeout=10)


def doctor_recipe(recipe_path: Path, data: dict) -> int:
    foundation_status = "SATISFIED"
    if isinstance(data.get("foundation_requirements"), dict):
        foundation = load_foundation_module()
        paths = foundation.resolve_workspace(root(), data["id"])
        result = foundation.inspect_foundation(
            recipe_path, paths.install_prefix, validate_core_config=True
        )
        foundation.print_inspection(result, False)
        foundation_status = result["status"]
    else:
        print("Foundation: not declared")
    local_results = inspect_local_requirements(recipe_path, data)
    print_local_inspection(local_results)
    local_satisfied = all(item["status"] == "SATISFIED" for item in local_results)
    runtime_result = inspect_recipe_runtime(recipe_path, data)
    if runtime_result["status"] != "NOT_DECLARED":
        print(
            f"[{runtime_result['status']}] Recipe runtime: "
            f"{runtime_result['launcher']}"
        )
        for reason in runtime_result["reasons"]:
            print(f"  - {reason}")
    runtime_satisfied = runtime_result["status"] in {"SATISFIED", "NOT_DECLARED"}
    return 0 if foundation_status == "SATISFIED" and local_satisfied and runtime_satisfied else 1


def _text(value: object, fallback: str = "") -> str:
    return value.strip() if isinstance(value, str) and value.strip() else fallback


def _list(value: object) -> list:
    return value if isinstance(value, list) else []


def _mapping(value: object) -> dict:
    return value if isinstance(value, dict) else {}


def _foundation_state(recipe_path: Path, data: dict) -> tuple[str, str]:
    requirements = data.get("foundation_requirements")
    if not isinstance(requirements, dict) or not requirements:
        return "Foundation not declared", "このRecipeはFoundation要求を宣言していません。"
    foundation = load_foundation_module()
    paths = foundation.resolve_workspace(root(), data["id"])
    try:
        result = foundation.inspect_foundation(recipe_path, paths.install_prefix)
    except Exception as exc:  # noqa: BLE001 - render state without blocking the guide
        return "Foundation UNKNOWN", str(exc)
    status = _text(result.get("status"), "UNKNOWN")
    components = ", ".join(
        f"{item.get('component', '?')}={item.get('status', '?')}"
        for item in _list(result.get("components"))
        if isinstance(item, dict)
    )
    return f"Foundation {status}", components or status


def _relative_recipe_path(recipe_path: Path) -> str:
    try:
        return str(recipe_path.relative_to(root()))
    except ValueError:
        return str(recipe_path)


def _workspace_command(command: str) -> str:
    for prefix in ("python3.12 ", "python3 "):
        if command.startswith(prefix):
            return "python " + command[len(prefix) :]
    return command


def _command_items(data: dict, recipe_path: Path) -> tuple[PortalCommand, ...]:
    demo = _mapping(data.get("demo"))
    raw_items = _list(demo.get("prerequisites")) + _list(demo.get("steps"))
    commands: list[PortalCommand] = []
    seen: set[str] = set()
    if (
        isinstance(data.get("foundation_requirements"), dict)
        and demo.get("foundation_workflow") != "declared"
    ):
        rendered_recipe = _relative_recipe_path(recipe_path)
        for label, action, description in (
            ("Recipe doctor", "doctor", "FoundationとRecipe固有依存をまとめて診断します。"),
            ("Recipe plan", "plan", "clone・Foundation構築・Python依存導入の計画を確認します。"),
            ("Recipe configure", "configure", "Recipeの実行環境を計画どおりに構成します。"),
        ):
            command = f"python tools/recipe.py {action} --recipe {rendered_recipe}"
            commands.append(PortalCommand(label, command, description))
            seen.add(command)
    for index, item in enumerate(raw_items, start=1):
        if not isinstance(item, dict):
            continue
        command = _workspace_command(_text(item.get("command")))
        if not command or command in seen:
            continue
        if (
            "tools/foundation.py doctor" in command
            and commands
            and demo.get("foundation_workflow") != "declared"
        ):
            continue
        seen.add(command)
        label = _text(item.get("id"), _text(item.get("action"))).replace("_", " ")
        if not label:
            for operation in (
                "configure",
                "doctor",
                "start",
                "status",
                "reset",
                "stop",
                "open-viewer",
                "build",
                "test",
                "smoke",
            ):
                if operation in command.split():
                    label = operation
                    break
        if not label:
            label = f"Step {index}"
        expected = item.get("expected")
        if isinstance(expected, list):
            description = " / ".join(str(value) for value in expected)
        else:
            description = _text(
                expected,
                _text(
                    item.get("notes"),
                    _text(
                        item.get("description"),
                        _text(item.get("action"), "Recipeで宣言された操作"),
                    ),
                ),
            )
        commands.append(PortalCommand(label.title(), command, description))
    return tuple(commands)


def _configuration_items(data: dict) -> tuple[PortalEnvironment, ...]:
    contract = _mapping(data.get("experiment_contract"))
    if not contract:
        return ()
    items: list[PortalEnvironment] = []
    source = _text(contract.get("source"))
    if source:
        items.append(PortalEnvironment("Experiment YAML", source))
    inputs = [str(value) for value in _list(contract.get("user_inputs"))]
    if inputs:
        items.append(PortalEnvironment("Editable fields", ", ".join(inputs)))
    for index, rule in enumerate(_list(contract.get("resolution")), start=1):
        items.append(PortalEnvironment(f"Resolution rule {index}", str(rule)))
    return tuple(items)


def _agency_notes(data: dict) -> tuple[str, ...]:
    agency = _mapping(data.get("agency_boundary"))
    demo = _mapping(data.get("demo"))
    notes: list[str] = []
    for key, prefix in (
        ("human_decisions", "人の判断"),
        ("human_actions", "人の操作"),
        ("required_permissions", "必要な許可"),
    ):
        for item in _list(agency.get(key)):
            if isinstance(item, dict):
                description = _text(item.get("description"))
                if description:
                    notes.append(f"{prefix}: {description}")
    readiness = _mapping(demo.get("readiness"))
    lifecycle = _mapping(readiness.get("lifecycle_state"))
    if lifecycle:
        required = _text(lifecycle.get("required"), "not specified")
        sufficient = lifecycle.get("sufficient")
        notes.append(
            f"Launcher lifecycle: {required}"
            + (
                "（Demo Readyの十分条件）"
                if sufficient is True
                else "（これだけではDemo Readyではありません）"
            )
        )
    for check in _list(readiness.get("checks")):
        if not isinstance(check, dict):
            continue
        target = _text(check.get("target"), _text(check.get("id"), "readiness"))
        expected = _text(check.get("expected"), "ready")
        notes.append(f"Demo Ready: {target} — {expected}")
    handoff = _mapping(readiness.get("operator_handoff"))
    if handoff.get("background") is True:
        actions = ", ".join(str(item) for item in _list(handoff.get("next_actions")))
        notes.append(
            "Background handoff: startの復帰後もDemoは継続します。"
            + (f" 次の操作: {actions}" if actions else "")
        )

    cleanup = _mapping(demo.get("cleanup"))
    for caution in _list(cleanup.get("cautions")):
        notes.append(f"停止時の注意: {caution}")
    if not notes:
        notes.append("追加の人手・許可境界はRecipeに記載されていません。")
    return tuple(notes)


def _topology(data: dict) -> tuple[str, ...]:
    components = []
    for component in _list(data.get("components")):
        if isinstance(component, dict):
            component_id = _text(component.get("id"))
            if component_id:
                components.append(component_id)
    return tuple(components) or ("Recipe components not declared",)


def _links(paths, recipe_path: Path, data: dict) -> tuple[PortalLink, ...]:
    links = [
        PortalLink("Recipe YAML", recipe_path.as_uri(), "このページの一次情報"),
        PortalLink("Config", "config/", "Recipe固有の生成設定"),
        PortalLink("Assets", "assets/", "Recipe固有の生成・配布Asset"),
        PortalLink("Logs", "logs/", "実行時ログ"),
        PortalLink("Validation", "validation/", "検証証跡"),
    ]
    experiment_source = _text(_mapping(data.get("experiment_contract")).get("source"))
    if experiment_source:
        experiment_path = root() / experiment_source
        links.insert(
            1,
            PortalLink(
                "Experiment YAML",
                experiment_path.as_uri(),
                "機体数、process数、飛行シナリオを変更する入力",
            ),
        )
    launcher = next(
        (
            profile.get("launcher")
            for profile in _list(_mapping(data.get("demo")).get("profiles"))
            if isinstance(profile, dict) and isinstance(profile.get("launcher"), str)
        ),
        None,
    )
    if launcher:
        try:
            relative = Path(launcher).relative_to(paths.recipe_root)
            links.append(PortalLink("Launcher", str(relative), "Recipeが使用するLauncher"))
        except ValueError:
            pass
    return tuple(links)


def write_guide(
    recipe_path: Path,
    data: dict,
    foundation_requirements_path: Path | None = None,
) -> Path:
    foundation = load_foundation_module()
    paths = foundation.resolve_workspace(root(), data["id"])
    paths.recipe_root.mkdir(parents=True, exist_ok=True)
    status_label, foundation_detail = _foundation_state(
        foundation_requirements_path or recipe_path, data
    )
    if status_label.endswith("SATISFIED") or status_label == "Foundation not declared":
        status_tone = "ready"
    elif status_label.endswith("UNKNOWN"):
        status_tone = "warning"
    else:
        status_tone = "error"
    goal = _mapping(data.get("goal"))
    target = _mapping(data.get("target_environment"))
    execution = _mapping(data.get("execution_environment"))
    feasibility = _mapping(data.get("feasibility"))
    validation = _mapping(data.get("validation"))
    missing = " / ".join(str(item) for item in _list(data.get("missing_pieces")))
    environment = (
        PortalEnvironment("Recipe source", str(recipe_path)),
        PortalEnvironment("Recipe workspace", str(paths.recipe_root)),
        PortalEnvironment("Feasibility", _text(feasibility.get("status"), "unknown")),
        PortalEnvironment("Validation", _text(validation.get("status"), "unknown")),
        PortalEnvironment("Foundation", foundation_detail),
        PortalEnvironment("Target OS", _text(target.get("os"), "not specified")),
        PortalEnvironment(
            "Execution mode", _text(execution.get("execution_mode"), "not specified")
        ),
        PortalEnvironment("Missing pieces", missing or "none declared"),
    )
    summary = _text(
        goal.get("description"),
        _text(data.get("expected_result"), _text(goal.get("user_request"), data["id"])),
    )
    output = paths.recipe_root / "index.html"
    return write_recipe_portal(
        output,
        recipe_id=data["id"],
        title=_text(data.get("title"), data["id"]),
        summary=summary,
        commands=_command_items(data, recipe_path),
        links=_links(paths, recipe_path, data),
        environment=environment,
        topology=_topology(data),
        agency_notes=_agency_notes(data),
        status_label=status_label,
        status_tone=status_tone,
        configuration=_configuration_items(data),
    )


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(
        description="Generate a human-facing workspace guide from a Hakoniwa Recipe"
    )
    result.add_argument(
        "command", choices=("guide", "doctor", "plan", "configure", "launch")
    )
    result.add_argument("--recipe", type=Path, required=True)
    result.add_argument(
        "--foundation-requirements",
        type=Path,
        help="Use generated Foundation requirements when rendering current status",
    )
    result.add_argument(
        "--open",
        action="store_true",
        help="Open the generated workspace index.html in the default browser",
    )
    return result


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    if args.command in {"doctor", "plan", "configure", "launch"}:
        warn_if_workspace_invalid(root())
    try:
        recipe_path = args.recipe.expanduser().resolve()
        data = load_recipe(recipe_path)
        if args.command == "doctor":
            return doctor_recipe(recipe_path, data)
        if args.command == "plan":
            print_recipe_plan(create_recipe_plan(recipe_path, data))
            return 0
        if args.command == "configure":
            return configure_recipe(recipe_path, data)
        if args.command == "launch":
            return launch_recipe(recipe_path, data)
        requirements_path = (
            args.foundation_requirements.expanduser().absolute()
            if args.foundation_requirements is not None
            else None
        )
        output = write_guide(recipe_path, data, requirements_path)
        print(f"Recipe guide: {output}")
        if args.open and not webbrowser.open(output.as_uri()):
            raise RecipeGuideError(f"failed to open Recipe guide: {output}")
        return 0
    except (OSError, RuntimeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
