"""The Foundation's Hakoniwa runtime directory across a Core upgrade.

Hakoniwa Core keeps its shared memory and master lock in files under
``core_mmap_path`` (work/foundation/runtime/mmap). Core with fail-stop master
locks (hakoniwa-core-cpp#62, hako-cmd 1.1.0) refuses files left by an older Core:
a 4096-byte legacy ``flock.bin`` stops every master start. Mixed Core versions on
one runtime directory are unsupported.

The directory belongs to this Foundation alone, and the Foundation installs every
Core component together, so the Workspace entry (``workspace.py enter`` / ``run``)
is where an upgrade is completed. The directory carries a stamp of the Core that
made it; when the installed Core differs (or a legacy file is there without a
stamp) and no Launcher session of this Workspace is running, the old files are
moved to ``runtime/mmap-upgrade-<time>/`` (kept as evidence, the newest few) and
the directory is stamped for the installed Core. While a session runs nothing is
moved: the user is told to stop it and enter again.
"""

from __future__ import annotations

import datetime
import json
import os
import re
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

STAMP_NAME = ".hakoniwa-runtime.json"
BACKUP_PREFIX = "mmap-upgrade-"
BACKUPS_KEPT = 3
LEGACY_FLOCK_SIZE = 4096
# The files Core writes there (the former Launcher cleanup and `hako-cmd recover` remove the same).
RUNTIME_FILE = re.compile(r"^(mmap-0x[0-9a-fA-F]+\.bin|flock\.bin|pdu_init\.lock|pro_init\.lock)$")
TERMINAL_STATES = {"TERMINATED", "FAILED"}


@dataclass
class RuntimeCheck:
    directory: Path | None
    core: dict | None                       # {"hako_cmd": "1.1.0", "layout": "2"} of the installed Core
    stamp: dict | None                      # the stamp found in the directory
    files: list[Path] = field(default_factory=list)
    reason: str = ""                         # why the files do not belong to the installed Core ("" when they do)
    active_sessions: list[Path] = field(default_factory=list)

    @property
    def stale(self) -> bool:
        return bool(self.reason) and bool(self.files)


def core_mmap_path(config_path: Path) -> Path | None:
    try:
        config = json.loads(config_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    value = config.get("core_mmap_path")
    if not value:
        return None
    path = Path(value)
    return path if path.is_absolute() else (config_path.parent / path)


def installed_core(hako_cmd: Path) -> dict | None:
    """The installed Core's hako-cmd version and shared memory layout (`hako-cmd --version`)."""
    if not hako_cmd.is_file():
        return None
    try:
        result = subprocess.run([str(hako_cmd), "--version"], capture_output=True, text=True,
                                encoding="utf-8", errors="replace", timeout=10, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return None
    text = result.stdout or ""
    version = re.search(r"version\s+(\d+(?:\.\d+)*)", text)
    layout = re.search(r"HAKO_SHM_LAYOUT_VERSION=(\S+)", text)
    if not version:
        return None
    return {"hako_cmd": version.group(1), "layout": layout.group(1) if layout else ""}


def _version(text: str) -> tuple[int, ...]:
    return tuple(int(part) for part in re.findall(r"\d+", text))


def _pid_alive(pid: object) -> bool:
    try:
        pid = int(pid)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return False
    if pid <= 0:
        return False
    if os.name == "nt":
        result = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/NH"], capture_output=True,
                                text=True, errors="replace", check=False)
        return str(pid) in (result.stdout or "")
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def active_sessions(work_root: Path) -> list[Path]:
    """Launcher sessions of this Workspace that are not finished and whose Launcher lives."""
    found = []
    for pattern in ("recipes/*/runtime/launcher-session.json", "recipes/*/*/runtime/launcher-session.json",
                    "*/runtime/launcher-session.json"):
        for path in work_root.glob(pattern):
            try:
                session = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if session.get("state") not in TERMINAL_STATES and _pid_alive(session.get("pid")):
                found.append(path)
    return sorted(set(found))


def inspect(config_path: Path, hako_cmd: Path, work_root: Path) -> RuntimeCheck:
    directory = core_mmap_path(config_path)
    core = installed_core(hako_cmd)
    check = RuntimeCheck(directory, core, None)
    if directory is None or not directory.is_dir() or core is None:
        return check
    stamp_path = directory / STAMP_NAME
    try:
        check.stamp = json.loads(stamp_path.read_text(encoding="utf-8")) if stamp_path.is_file() else None
    except (OSError, ValueError):
        check.stamp = {}
    check.files = sorted(p for p in directory.iterdir()
                         if RUNTIME_FILE.match(p.name) and p.is_file() and not p.is_symlink())
    if check.stamp is not None:
        if (check.stamp.get("hako_cmd"), check.stamp.get("layout")) != (core["hako_cmd"], core["layout"]):
            check.reason = (f"made by Core hako-cmd {check.stamp.get('hako_cmd')} "
                            f"(layout {check.stamp.get('layout')}); installed {core['hako_cmd']} "
                            f"(layout {core['layout']})")
    else:
        flock = directory / "flock.bin"
        if (_version(core["hako_cmd"]) >= (1, 1, 0) and flock.is_file()
                and flock.stat().st_size == LEGACY_FLOCK_SIZE):
            check.reason = (f"a legacy flock.bin ({LEGACY_FLOCK_SIZE} bytes) from a Core before "
                            f"hako-cmd 1.1.0; installed {core['hako_cmd']}")
    if check.stale:
        check.active_sessions = active_sessions(work_root)
    return check


def _write_stamp(directory: Path, core: dict) -> None:
    stamp = dict(core, stamped_at=datetime.datetime.now().isoformat(timespec="seconds"))
    (directory / STAMP_NAME).write_text(json.dumps(stamp, indent=2) + "\n", encoding="utf-8")


def _prune_backups(parent: Path) -> None:
    backups = sorted(p for p in parent.glob(BACKUP_PREFIX + "*") if p.is_dir())
    for old in backups[:-BACKUPS_KEPT]:
        shutil.rmtree(old, ignore_errors=True)


def reconcile(check: RuntimeCheck, *, report=print) -> str:
    """Bring the runtime directory to the installed Core. Returns "ok", "stamped", "moved",
    "in-use" or "skipped" (no Foundation Core or no runtime directory yet)."""
    directory, core = check.directory, check.core
    if directory is None or core is None or not directory.is_dir():
        return "skipped"
    if not check.stale:
        if check.stamp is None or (check.stamp.get("hako_cmd"), check.stamp.get("layout")) != (
                core["hako_cmd"], core["layout"]):
            _write_stamp(directory, core)  # empty, or files of the installed Core
            return "stamped"
        return "ok"
    if check.active_sessions:
        report("warning: the Hakoniwa runtime directory holds files of another Core "
               f"({check.reason}), but a Launcher session is running:\n  "
               + "\n  ".join(str(p) for p in check.active_sessions)
               + "\n  Stop it, then enter the Workspace again to finish the Core upgrade.")
        return "in-use"
    backup = directory.parent / (BACKUP_PREFIX + datetime.datetime.now().strftime("%Y%m%d-%H%M%S"))
    backup.mkdir(parents=True, exist_ok=True)
    # The lock last: a failure on the way leaves the shared memory files guarded.
    for path in sorted(check.files, key=lambda p: (p.name == "flock.bin", p.name)):
        shutil.move(str(path), str(backup / path.name))
    if (directory / STAMP_NAME).is_file():
        shutil.move(str(directory / STAMP_NAME), str(backup / STAMP_NAME))
    _write_stamp(directory, core)
    _prune_backups(directory.parent)
    report(f"Hakoniwa Core upgrade: moved {len(check.files)} runtime file(s) of the previous Core "
           f"({check.reason}) to {backup}")
    return "moved"
