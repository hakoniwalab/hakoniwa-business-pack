#!/usr/bin/env python3
"""ROS 2 AddTwoInts Recipe entry point with platform-safe process liveness checks."""

from __future__ import annotations

import ros2_service_add_two_ints_impl as impl
from process_liveness import pid_alive as safe_pid_alive


# Preserve the historical module API because tests and sibling Recipe helpers
# import this file directly and access its constants/functions. Note that
# impl also defines its own (platform-unsafe) pid_alive, so this loop copies
# that name into globals() too; the override below runs afterward and must
# use a distinct alias (safe_pid_alive) rather than the module-level
# `pid_alive` name, otherwise it would just reassign impl's own value to
# itself instead of installing the platform-safe helper.
for _name in dir(impl):
    if not _name.startswith("__"):
        globals()[_name] = getattr(impl, _name)

# Override only the liveness primitive. The implementation resolves pid_alive
# through its module globals, so status/stop use the platform-safe helper while
# every other public symbol remains unchanged.
impl.pid_alive = safe_pid_alive
globals()["pid_alive"] = safe_pid_alive


def _sync_impl(*names: str) -> None:
    """Reflect monkey-patched public symbols into the implementation module."""
    for name in names:
        setattr(impl, name, globals()[name])


def install_host_python() -> None:
    _sync_impl("foundation_python", "sibling", "run")
    return impl.install_host_python()


def copy_offsets(destination):
    _sync_impl("sibling")
    return impl.copy_offsets(destination)


def managed_host_alive(session, host_session_path):
    _sync_impl("pid_alive")
    return impl.managed_host_alive(session, host_session_path)


def start(args):
    _sync_impl("paths")
    return impl.start(args)


if __name__ == "__main__":
    raise SystemExit(impl.main())
