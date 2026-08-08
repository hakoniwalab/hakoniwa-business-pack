"""POSIX runtime paths for the Drone gamepad Recipe."""

from __future__ import annotations

from pathlib import Path


def drone_service_candidates(drone_root: Path, system_name: str) -> tuple[Path, ...]:
    if system_name == "Darwin":
        name = "mac-main_hako_drone_service"
        directory = "mac"
    elif system_name == "Linux":
        name = "linux-main_hako_drone_service"
        directory = "lnx"
    else:
        raise RuntimeError(f"unsupported POSIX platform: {system_name}")
    return (drone_root / "lib" / name, drone_root / directory / name)


def visual_state_publisher_candidates(
    drone_root: Path, system_name: str
) -> tuple[Path, ...]:
    if system_name == "Darwin":
        name = "mac-drone_visual_state_publisher"
        directory = "mac"
    elif system_name == "Linux":
        name = "linux-drone_visual_state_publisher"
        directory = "lnx"
    else:
        raise RuntimeError(f"unsupported POSIX platform: {system_name}")
    return (drone_root / "lib" / name, drone_root / directory / name)


def foundation_python_candidates(python_root: Path) -> tuple[Path, ...]:
    return (python_root / "bin" / "python3", python_root / "bin" / "python")


def hako_cmd_candidates(install_prefix: Path) -> tuple[Path, ...]:
    return (install_prefix / "bin" / "hako-cmd",)


def web_bridge_candidates(install_prefix: Path) -> tuple[Path, ...]:
    return (install_prefix / "bin" / "hakoniwa-pdu-web-bridge",)
