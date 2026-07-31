from __future__ import annotations

from pathlib import Path


def drone_service_candidates(drone_root: Path, system_name: str) -> tuple[Path, ...]:
    del system_name
    name = "win-main_hako_drone_service.exe"
    return (drone_root / "lib" / name, drone_root / "win" / name)


def visual_state_publisher_candidates(
    drone_root: Path, system_name: str
) -> tuple[Path, ...]:
    del system_name
    name = "win-drone_visual_state_publisher.exe"
    return (drone_root / "lib" / name, drone_root / "win" / name)


def foundation_python_candidates(python_root: Path) -> tuple[Path, ...]:
    return (
        python_root / "Scripts" / "python.exe",
        python_root / "python.exe",
    )


def hako_cmd_candidates(install_prefix: Path) -> tuple[Path, ...]:
    return (install_prefix / "bin" / "hako-cmd.exe",)


def web_bridge_candidates(install_prefix: Path) -> tuple[Path, ...]:
    return (install_prefix / "bin" / "hakoniwa-pdu-web-bridge.exe",)
