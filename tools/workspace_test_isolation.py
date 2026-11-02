"""Keep tool tests away from the caller's managed Workspace.

``workspace.py run`` and ``enter`` export the active Workspace identity.
Tool code honours those variables (``HAKONIWA_WORK_DIR`` in particular), so a
test that builds paths from a temporary Business Pack root would otherwise
read and write the caller's real ``work/`` tree. Every tool test module calls
``isolate_from_active_workspace()`` at import time, before any test runs.
"""

from __future__ import annotations

import os
from typing import MutableMapping

WORKSPACE_ENVIRONMENT = (
    "HAKONIWA_WORKSPACE_ACTIVE",
    "HAKONIWA_WORKSPACE_ROOT",
    "HAKONIWA_WORK_DIR",
    "HAKONIWA_HOME",
    "HAKONIWA_PORTABLE_WORKSPACE",
    "HAKO_CONFIG_PATH",
    "HAKO_PDU_ENDPOINT_RUNTIME_DIRS",
)


def isolate_from_active_workspace(
    environ: MutableMapping[str, str] | None = None,
) -> None:
    env = os.environ if environ is None else environ
    for name in WORKSPACE_ENVIRONMENT:
        env.pop(name, None)
