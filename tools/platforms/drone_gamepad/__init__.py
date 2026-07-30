from __future__ import annotations

import platform


def current_adapter(system_name: str | None = None):
    name = system_name or platform.system()
    if name == "Windows":
        from . import windows

        return windows
    if name in {"Darwin", "Linux"}:
        from . import posix

        return posix
    raise RuntimeError(f"unsupported operating system: {name}")
