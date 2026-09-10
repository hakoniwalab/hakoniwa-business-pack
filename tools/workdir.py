"""Common resolution of the Hakoniwa working directory."""

from __future__ import annotations

import os
from pathlib import Path

WORK_DIR_ENV = "HAKONIWA_WORK_DIR"


def work_dir(business_pack_root: Path) -> Path:
    return resolve_work_dir(business_pack_root)


def foundation_root(business_pack_root: Path) -> Path:
    return work_dir(business_pack_root) / "foundation"


def foundation_install(business_pack_root: Path) -> Path:
    return foundation_root(business_pack_root) / "install"


def recipe_root(business_pack_root: Path, recipe_id: str) -> Path:
    return work_dir(business_pack_root) / "recipes" / recipe_id


def remote_runtime(business_pack_root: Path, name: str) -> Path:
    return work_dir(business_pack_root) / "remote-operation" / name


def resolve_work_dir(
    business_pack_root: Path,
    explicit: Path | str | None = None,
) -> Path:
    """Resolve CLI value, environment value, then the legacy default."""
    value = explicit if explicit is not None else os.environ.get(WORK_DIR_ENV)
    selected = Path(value) if value else business_pack_root / "work"
    return selected.expanduser().resolve()
