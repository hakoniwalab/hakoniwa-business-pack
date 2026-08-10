#!/usr/bin/env python3
from __future__ import annotations

import argparse
import importlib.util
import json
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


class RecipeGuideError(RuntimeError):
    pass


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
            ("Foundation doctor", "doctor", "現在の共通FoundationがRecipe要求を満たすか確認します。"),
            ("Foundation plan", "plan", "Foundationが未充足の場合に、必要な構築・再利用計画を確認します。"),
            ("Foundation build", "build", "計画されたFoundation componentを構築・インストールします。"),
        ):
            command = f"python tools/foundation.py {action} --recipe {rendered_recipe}"
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
    result.add_argument("command", choices=("guide",))
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
    try:
        recipe_path = args.recipe.expanduser().absolute()
        data = load_recipe(recipe_path)
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
    except (OSError, RecipeGuideError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
