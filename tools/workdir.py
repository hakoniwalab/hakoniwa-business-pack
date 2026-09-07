"""Common resolution of the Hakoniwa working directory."""

from __future__ import annotations

import os
from pathlib import Path

WORK_DIR_ENV = "HAKONIWA_WORK_DIR"


def resolve_work_dir(
    business_pack_root: Path,
    explicit: Path | str | None = None,
) -> Path:
    """Resolve CLI value, environment value, then the legacy default."""
    value = explicit if explicit is not None else os.environ.get(WORK_DIR_ENV)
    selected = Path(value) if value else business_pack_root / "work"
    return Path(os.path.abspath(os.path.expanduser(os.fspath(selected))))
