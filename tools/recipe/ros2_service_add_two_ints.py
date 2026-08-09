#!/usr/bin/env python3
"""ROS 2 AddTwoInts Recipe entry point with platform-safe process liveness checks."""

from __future__ import annotations

import ros2_service_add_two_ints_impl as impl
from process_liveness import pid_alive


# Preserve the historical module API because tests and sibling Recipe helpers
# import this file directly and access its constants/functions.
for _name in dir(impl):
    if not _name.startswith("__"):
        globals()[_name] = getattr(impl, _name)

# Override only the liveness primitive. The implementation resolves pid_alive
# through its module globals, so status/stop use the platform-safe helper while
# every other public symbol remains unchanged.
impl.pid_alive = pid_alive
globals()["pid_alive"] = pid_alive


if __name__ == "__main__":
    raise SystemExit(impl.main())
