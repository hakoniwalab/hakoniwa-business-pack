"""Declarative profiles consumed by the Windows portable package builder."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class RepositorySpec:
    name: str
    path: Path
    required_artifact: str
    include_paths: tuple[str, ...] = ()


@dataclass(frozen=True)
class PortableProfile:
    id: str
    package_id: str
    recipe_id: str
    kind: str
    repositories: tuple[RepositorySpec, ...]
    python_paths: tuple[str, ...]
    requirements: tuple[Path, ...]


def profiles(workspace_root: Path) -> dict[str, PortableProfile]:
    business_pack = workspace_root / "hakoniwa-business-pack"
    city_world = PortableProfile(
        id="city-world-web-ui",
        package_id="hakoniwa-business-pack-city-world-windows-x64",
        recipe_id="city-world-web-ui",
        kind="city-world-web-ui",
        repositories=(
            RepositorySpec("hakoniwa-envsim", workspace_root / "hakoniwa-envsim", "tools/hako.py"),
            RepositorySpec(
                "hakoniwa-pdu-javascript",
                workspace_root / "hakoniwa-pdu-javascript",
                "src/index.js",
            ),
            RepositorySpec(
                "hakoniwa-pdu-python",
                workspace_root / "hakoniwa-pdu-python",
                "src/hakoniwa_pdu/apps/launcher/hako_launcher.py",
            ),
        ),
        python_paths=(
            "hakoniwa-business-pack",
            "hakoniwa-pdu-python/src",
            "hakoniwa-envsim/tools",
            "hakoniwa-envsim/src/city_pipeline",
        ),
        requirements=(
            business_pack / "recipes/requirements/plateau-citygml-mujoco-walls.txt",
        ),
    )
    urban_car = PortableProfile(
        id="urban-car-rc",
        package_id="hakoniwa-urban-car-rc-windows-x64",
        recipe_id="urban-car-rc",
        kind="urban-car-rc",
        repositories=(
            RepositorySpec(
                "hakoniwa-urban-mobility",
                workspace_root / "hakoniwa-urban-mobility",
                "tools/urban_mobility.py",
                (
                    "apps",
                    "config",
                    "recipes",
                    "tools",
                    "CMakeLists.txt",
                ),
            ),
            RepositorySpec(
                "hakoniwa-pdu-registry",
                workspace_root / "hakoniwa-pdu-registry",
                "pdu/types/ackermann_msgs/pdu_cpptype_AckermannDrive.hpp",
                ("pdu",),
            ),
            RepositorySpec(
                "hakoniwa-robot-runtime",
                workspace_root / "hakoniwa-robot-runtime",
                "include",
                ("include", "src"),
            ),
            RepositorySpec(
                "hakoniwa-mujoco-robots",
                workspace_root / "hakoniwa-mujoco-robots",
                "MUJOCO_VERSION.txt",
                ("MUJOCO_VERSION.txt", "src"),
            ),
            RepositorySpec(
                "hakoniwa-mbody-registry",
                workspace_root / "hakoniwa-mbody-registry",
                "tools/compose_mujoco_world.py",
                ("tools", "bodies/generic_ackermann_golf_cart"),
            ),
            RepositorySpec(
                "hakoniwa-threejs-drone",
                workspace_root / "hakoniwa-threejs-drone",
                "index.html",
                ("index.html", "config", "src", "assets", "thirdparty"),
            ),
            RepositorySpec(
                "hakoniwa-map-viewer",
                workspace_root / "hakoniwa-map-viewer",
                "src/client/index.html",
                ("src",),
            ),
            RepositorySpec(
                "hakoniwa-pdu-python",
                workspace_root / "hakoniwa-pdu-python",
                "src/hakoniwa_pdu/apps/launcher/hako_launcher.py",
                ("src", "tools"),
            ),
        ),
        python_paths=(
            "hakoniwa-business-pack",
            "hakoniwa-pdu-python/src",
            "hakoniwa-urban-mobility/apps/car",
        ),
        # Recipe configure already installs these into Foundation Python.  The
        # packager copies that resolved environment and validates imports; it
        # must not require network access while assembling the ZIP.
        requirements=(),
    )
    return {profile.id: profile for profile in (city_world, urban_car)}


def load_profile(profile_id: str, workspace_root: Path) -> PortableProfile:
    available = profiles(workspace_root)
    try:
        return available[profile_id]
    except KeyError as exc:
        choices = ", ".join(sorted(available))
        raise ValueError(f"unknown portable profile {profile_id!r}; choose: {choices}") from exc
