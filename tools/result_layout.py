#!/usr/bin/env python3
"""Load and resolve repository-managed performance result paths."""

from __future__ import annotations

import json
import string
from pathlib import Path
from typing import Any

from tools.recipe import drone_fleet_single_host as yaml_support


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_LAYOUT = (
    ROOT / "configs" / "result-layouts" / "drone-fleet-performance.yaml"
)


class ResultLayoutError(RuntimeError):
    pass


def _mapping(value: Any, label: str, fields: set[str]) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ResultLayoutError(f"{label} must be a mapping")
    unknown = sorted(set(value) - fields)
    missing = sorted(fields - set(value))
    if unknown:
        raise ResultLayoutError(f"{label} has unknown fields: {', '.join(unknown)}")
    if missing:
        raise ResultLayoutError(f"{label} is missing fields: {', '.join(missing)}")
    return value


def _relative_path(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value:
        raise ResultLayoutError(f"{label} must be a non-empty relative path")
    path = Path(value)
    if path.is_absolute() or ".." in path.parts or "\\" in value:
        raise ResultLayoutError(f"{label} must be a portable repository-relative path")
    return value


def _template(value: Any, label: str, allowed: set[str]) -> str:
    result = _relative_path(value, label)
    fields = {
        name
        for _literal, name, _format, _conversion in string.Formatter().parse(result)
        if name is not None
    }
    unknown = sorted(fields - allowed)
    if unknown:
        raise ResultLayoutError(
            f"{label} has unsupported placeholders: {', '.join(unknown)}"
        )
    return result


def _experiment_authority(path: Path) -> tuple[str, str]:
    raw = yaml_support.load_simple_yaml(path)
    results = raw.get("results")
    measurement = raw.get("measurement")
    if not isinstance(results, dict) or not isinstance(measurement, dict):
        raise ResultLayoutError(f"Experiment has no result authority: {path}")
    directory = results.get("directory")
    series = measurement.get("series")
    return (
        _relative_path(directory, f"{path}.results.directory"),
        str(series),
    )


def load_layout(path: Path = DEFAULT_LAYOUT) -> dict[str, Any]:
    path = path.resolve()
    raw = yaml_support.load_simple_yaml(path)
    root = _mapping(
        raw,
        "layout",
        {"version", "schema", "roots", "participants", "experiments", "transfer_groups", "analysis", "semantics"},
    )
    if root["version"] != 1:
        raise ResultLayoutError("layout.version must be 1")
    schema_path = ROOT / _relative_path(root["schema"], "layout.schema")
    if not schema_path.is_file():
        raise ResultLayoutError(f"layout schema does not exist: {schema_path}")
    try:
        json.loads(schema_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ResultLayoutError(f"layout schema is invalid JSON: {exc}") from exc

    roots = _mapping(root["roots"], "layout.roots", {"workspaces", "collection"})
    for key, value in roots.items():
        roots[key] = _relative_path(value, f"layout.roots.{key}")

    participants = root["participants"]
    if not isinstance(participants, dict) or not participants:
        raise ResultLayoutError("layout.participants must be a non-empty mapping")
    for participant_id, value in participants.items():
        participant = _mapping(
            value,
            f"layout.participants.{participant_id}",
            {"scope", "platform", "execution_environment"},
        )
        if participant["scope"] not in {"machine", "host", "series"}:
            raise ResultLayoutError(f"invalid participant scope: {participant_id}")

    experiments = root["experiments"]
    if not isinstance(experiments, dict) or not experiments:
        raise ResultLayoutError("layout.experiments must be a non-empty mapping")
    fields = {"experiment", "workspace", "series", "producers", "source", "destination"}
    for experiment_id, value in experiments.items():
        experiment = _mapping(value, f"layout.experiments.{experiment_id}", fields)
        authority = ROOT / _relative_path(
            experiment["experiment"],
            f"layout.experiments.{experiment_id}.experiment",
        )
        if not authority.is_file():
            raise ResultLayoutError(f"Experiment does not exist: {authority}")
        producers = experiment["producers"]
        if (
            not isinstance(producers, list)
            or not producers
            or len(set(producers)) != len(producers)
            or any(producer not in participants for producer in producers)
        ):
            raise ResultLayoutError(
                f"layout.experiments.{experiment_id}.producers must be unique known participants"
            )
        workspace = _template(
            experiment["workspace"],
            f"layout.experiments.{experiment_id}.workspace",
            {"workspaces"},
        )
        experiment["workspace"] = workspace
        for field in ("source", "destination"):
            experiment[field] = _template(
                experiment[field],
                f"layout.experiments.{experiment_id}.{field}",
                {"workspace", "collection", "series", "participant"},
            )
        results_directory, series = _experiment_authority(authority)
        if experiment["series"] != series:
            raise ResultLayoutError(
                f"layout experiment {experiment_id} series disagrees with {authority}"
            )
        expected_fragment = f"/{results_directory}/{{series}}"
        if expected_fragment not in f"/{experiment['source']}":
            raise ResultLayoutError(
                f"layout experiment {experiment_id} source omits Experiment results.directory"
            )

    transfer_groups = root["transfer_groups"]
    if not isinstance(transfer_groups, dict) or not transfer_groups:
        raise ResultLayoutError("layout.transfer_groups must be a non-empty mapping")
    for group_id, value in transfer_groups.items():
        group = _mapping(
            value,
            f"layout.transfer_groups.{group_id}",
            {"experiments"},
        )
        members = group["experiments"]
        if (
            not isinstance(members, list)
            or not members
            or len(set(members)) != len(members)
            or any(member not in experiments for member in members)
        ):
            raise ResultLayoutError(
                f"layout.transfer_groups.{group_id}.experiments must be unique known experiments"
            )
        common_producers = set(experiments[members[0]]["producers"])
        for member in members[1:]:
            common_producers &= set(experiments[member]["producers"])
        if not common_producers:
            raise ResultLayoutError(
                f"layout.transfer_groups.{group_id} has no common producer"
            )

    analysis = root["analysis"]
    if not isinstance(analysis, dict):
        raise ResultLayoutError("layout.analysis must be a mapping")
    for analysis_id, value in analysis.items():
        item = _mapping(
            value,
            f"layout.analysis.{analysis_id}",
            {"inputs", "output_directory", "output_stem", "formats"},
        )
        if not isinstance(item["inputs"], dict) or not item["inputs"]:
            raise ResultLayoutError(f"layout.analysis.{analysis_id}.inputs must be a mapping")
        for input_id, input_path in item["inputs"].items():
            _template(
                input_path,
                f"layout.analysis.{analysis_id}.inputs.{input_id}",
                {"collection", "workspaces"},
            )
        _template(
            item["output_directory"],
            f"layout.analysis.{analysis_id}.output_directory",
            {"collection", "workspaces"},
        )
    if not isinstance(root["semantics"], list) or not root["semantics"]:
        raise ResultLayoutError("layout.semantics must be a non-empty list")
    return root


def resolve_experiment_paths(
    layout: dict[str, Any], experiment_id: str, participant: str
) -> dict[str, Any]:
    try:
        experiment = layout["experiments"][experiment_id]
        participant_info = layout["participants"][participant]
    except KeyError as exc:
        raise ResultLayoutError(f"unknown result layout identity: {exc.args[0]}") from exc
    if participant not in experiment["producers"]:
        raise ResultLayoutError(
            f"{participant} is not a producer for {experiment_id}"
        )
    values = {
        **layout["roots"],
        "series": experiment["series"],
        "participant": participant,
    }
    workspace = experiment["workspace"].format(**values)
    values["workspace"] = workspace
    source = experiment["source"].format(**values)
    destination = experiment["destination"].format(**values)
    return {
        "experiment": ROOT / experiment["experiment"],
        "workspace": ROOT / workspace,
        "source": ROOT / source,
        "destination": ROOT / destination,
        "series": experiment["series"],
        "participant_scope": participant_info["scope"],
    }
