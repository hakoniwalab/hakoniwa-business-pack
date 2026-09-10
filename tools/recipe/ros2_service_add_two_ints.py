#!/usr/bin/env python3
"""ROS 2 AddTwoInts Recipe entry point with platform-safe process liveness checks."""

from __future__ import annotations

import ros2_service_add_two_ints_impl as impl


# Preserve the historical module API because tests and sibling Recipe helpers
# import this file directly and access its constants/functions. The implementation
# imports process_liveness.pid_alive directly, so the public pid_alive exported
# here is the same platform-safe helper used by start/status/stop.
for _name in dir(impl):
    if not _name.startswith("__"):
        globals()[_name] = getattr(impl, _name)


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
    # Preserve the historical monkey-patchable public API used by tests and
    # sibling helpers while keeping process_liveness.py as the implementation SoT.
    _sync_impl("pid_alive")
    return impl.managed_host_alive(session, host_session_path)


def start(args):
    _sync_impl("paths")
    return impl.start(args)


if __name__ == "__main__":
    raise SystemExit(impl.main())
