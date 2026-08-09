#!/usr/bin/env python3
"""ROS 2 AddTwoInts Recipe entry point with platform-safe process liveness checks."""

from __future__ import annotations

import ros2_service_add_two_ints_impl as impl
from process_liveness import pid_alive


# The implementation calls pid_alive() through its module globals. Override the
# legacy POSIX-oriented helper before dispatching any command so Windows status
# and stop never use os.kill(pid, 0).
impl.pid_alive = pid_alive


if __name__ == "__main__":
    raise SystemExit(impl.main())
