"""Declarative profiles consumed by the Windows portable package builder.

Built-in profiles live here. A sibling repository can own its own profile by
shipping ``portable/windows-profile.json``; the builder then drives that
repository's portable tool through a fixed command contract instead of
profile-specific code in Business Pack:

``<tool> collect``  source Workspace, before copying (gather runtime files)
``<tool> doctor``   source Workspace, before copying (fail early)
``<tool> prepare``  package, on first start and in staging (relocate/configure)
``<tool> start|status|stop``  package entrypoints
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path


REPOSITORY_PROFILE = Path("portable") / "windows-profile.json"
REPOSITORY_PROFILE_SCHEMA_VERSION = 1


@dataclass(frozen=True)
class RepositorySpec:
    name: str
    path: Path
    required_artifact: str
    include_paths: tuple[str, ...] = ()


@dataclass(frozen=True)
class RepositoryTool:
    """Repository-owned portable tool contract (kind == "repository")."""

    repository: str
    tool: str
    entrypoint_name: str
    title: str
    readme: str
    validation_imports: tuple[str, ...]
    staging_cleanup: tuple[str, ...]


@dataclass(frozen=True)
class PortableProfile:
    id: str
    package_id: str
    recipe_id: str
    kind: str
    repositories: tuple[RepositorySpec, ...]
    python_paths: tuple[str, ...]
    requirements: tuple[Path, ...]
    repository_tool: RepositoryTool | None = None


class ProfileError(ValueError):
    pass


def _strings(payload: dict, key: str, source: Path, *, required: bool = True) -> tuple[str, ...]:
    value = payload.get(key, None if required else [])
    if not isinstance(value, list) or not all(isinstance(item, str) and item for item in value):
        raise ProfileError(f"{source}: {key} must be a list of non-empty strings")
    return tuple(value)


def _string(payload: dict, key: str, source: Path) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value:
        raise ProfileError(f"{source}: {key} must be a non-empty string")
    return value


def _relative(value: str, source: Path, key: str) -> str:
    path = Path(value)
    if path.is_absolute() or ".." in path.parts or "\\" in value:
        raise ProfileError(f"{source}: {key} must be a relative POSIX path inside the package: {value}")
    return value


def load_repository_profile(path: Path, workspace_root: Path) -> PortableProfile:
    """Parse a repository-owned ``portable/windows-profile.json``."""
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ProfileError(f"invalid portable profile {path}: {exc}") from exc
    if not isinstance(payload, dict) or payload.get("schema_version") != REPOSITORY_PROFILE_SCHEMA_VERSION:
        raise ProfileError(f"{path}: schema_version must be {REPOSITORY_PROFILE_SCHEMA_VERSION}")
    owner = path.parent.parent.name
    repositories = []
    for item in payload.get("repositories", []):
        if not isinstance(item, dict):
            raise ProfileError(f"{path}: repositories entries must be objects")
        name = _string(item, "name", path)
        if Path(name).name != name:
            raise ProfileError(f"{path}: repository name must be a sibling directory name: {name}")
        repositories.append(
            RepositorySpec(
                name,
                workspace_root / name,
                _relative(_string(item, "required_artifact", path), path, "required_artifact"),
                tuple(_relative(value, path, "include_paths") for value in _strings(item, "include_paths", path)),
            )
        )
    if owner not in {item.name for item in repositories}:
        raise ProfileError(f"{path}: repositories must include the owning repository {owner}")
    tool = _relative(_string(payload, "tool", path), path, "tool")
    entrypoint_name = _string(payload, "entrypoint_name", path)
    if not entrypoint_name.replace("-", "").isalnum():
        raise ProfileError(f"{path}: entrypoint_name must contain only letters, digits, and '-'")
    return PortableProfile(
        id=_string(payload, "id", path),
        package_id=_string(payload, "package_id", path),
        recipe_id=_string(payload, "recipe_id", path),
        kind="repository",
        repositories=tuple(repositories),
        python_paths=tuple(_relative(value, path, "python_paths") for value in _strings(payload, "python_paths", path)),
        # Recipe configure installs Python dependencies into the Foundation
        # Python; the builder copies that environment without network access.
        requirements=(),
        repository_tool=RepositoryTool(
            repository=owner,
            tool=tool,
            entrypoint_name=entrypoint_name,
            title=_string(payload, "title", path),
            readme=_relative(_string(payload, "readme", path), path, "readme"),
            validation_imports=_strings(payload, "validation_imports", path),
            staging_cleanup=tuple(
                _relative(value, path, "staging_cleanup")
                for value in _strings(payload, "staging_cleanup", path, required=False)
            ),
        ),
    )


def repository_profiles(workspace_root: Path) -> dict[str, PortableProfile]:
    result: dict[str, PortableProfile] = {}
    for path in sorted(workspace_root.glob(f"*/{REPOSITORY_PROFILE.as_posix()}")):
        profile = load_repository_profile(path, workspace_root)
        if profile.id in result:
            raise ProfileError(f"duplicate portable profile id {profile.id!r}: {path}")
        result[profile.id] = profile
    return result


def profiles(workspace_root: Path) -> dict[str, PortableProfile]:
    result = builtin_profiles(workspace_root)
    for profile_id, profile in repository_profiles(workspace_root).items():
        if profile_id in result:
            raise ProfileError(f"repository portable profile {profile_id!r} collides with a built-in profile")
        result[profile_id] = profile
    return result


def builtin_profiles(workspace_root: Path) -> dict[str, PortableProfile]:
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
