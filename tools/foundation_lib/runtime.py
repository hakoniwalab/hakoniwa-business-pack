"""Workspace, toolchain, and managed Python runtime contracts."""

from __future__ import annotations

import json
import platform
import re
import subprocess
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

from workdir import resolve_work_dir


RECIPE_ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]*$")
FOUNDATION_PYTHON_IMPLEMENTATION = "CPython"
FOUNDATION_PYTHON_VERSION = (3, 12)
VCPKG_COMPONENTS = frozenset(
    {
        "hakoniwa-pdu-endpoint",
        "hakoniwa-pdu-rpc",
        "hakoniwa-pdu-bridge-core",
        "athrill-target-v850e2m",
    }
)


class FoundationError(RuntimeError):
    pass


@dataclass(frozen=True)
class WorkspacePaths:
    business_pack_root: Path
    work_root: Path
    foundation_root: Path
    install_prefix: Path
    foundation_python: Path
    foundation_config: Path
    foundation_runtime: Path
    foundation_mmap: Path
    foundation_build: Path
    recipe_root: Path
    recipe_config: Path
    recipe_assets: Path
    recipe_missions: Path
    recipe_logs: Path
    recipe_validation: Path

    def directories(self) -> tuple[Path, ...]:
        return (
            self.install_prefix,
            self.foundation_config,
            self.foundation_mmap,
            self.foundation_build,
            self.recipe_config,
            self.recipe_assets,
            self.recipe_missions,
            self.recipe_logs,
            self.recipe_validation,
        )

    def serializable(self) -> dict[str, str]:
        return {key: str(value) for key, value in asdict(self).items()}


def repository_root() -> Path:
    return Path(__file__).resolve().parents[2]


def validate_recipe_id(recipe_id: str) -> str:
    if not RECIPE_ID_PATTERN.fullmatch(recipe_id):
        raise FoundationError(
            "recipe id must use lowercase letters, digits, and hyphens"
        )
    return recipe_id


def resolve_workspace(
    root: Path,
    recipe_id: str,
    foundation_root_override: Path | None = None,
) -> WorkspacePaths:
    recipe_id = validate_recipe_id(recipe_id)
    business_pack_root = root.resolve()
    work_root = resolve_work_dir(business_pack_root)
    foundation_root = (
        foundation_root_override.resolve()
        if foundation_root_override is not None
        else work_root / "foundation"
    )
    if not foundation_root.is_relative_to(work_root):
        raise FoundationError(
            "Foundation root must stay under the selected work directory"
        )
    recipe_root = work_root / "recipes" / recipe_id
    return WorkspacePaths(
        business_pack_root=business_pack_root,
        work_root=work_root,
        foundation_root=foundation_root,
        install_prefix=foundation_root / "install",
        foundation_python=foundation_root / "install" / "python",
        foundation_config=foundation_root / "config",
        foundation_runtime=foundation_root / "runtime",
        foundation_mmap=foundation_root / "runtime" / "mmap",
        foundation_build=foundation_root / "build",
        recipe_root=recipe_root,
        recipe_config=recipe_root / "config",
        recipe_assets=recipe_root / "assets",
        recipe_missions=recipe_root / "missions",
        recipe_logs=recipe_root / "logs",
        recipe_validation=recipe_root / "validation",
    )


def prepare_workspace(paths: WorkspacePaths) -> None:
    for directory in paths.directories():
        directory.mkdir(parents=True, exist_ok=True)


def foundation_toolchain_path(paths: WorkspacePaths) -> Path:
    return paths.foundation_config / "toolchain.json"


def _vcpkg_required_paths(root: Path) -> tuple[Path, Path]:
    executable = root / (
        "vcpkg.exe" if platform.system() == "Windows" else "vcpkg"
    )
    toolchain = root / "scripts" / "buildsystems" / "vcpkg.cmake"
    return executable, toolchain


def configure_foundation_toolchain(paths: WorkspacePaths, vcpkg_root: Path) -> Path:
    root = vcpkg_root.expanduser().resolve()
    required_paths = _vcpkg_required_paths(root)
    missing = [str(path) for path in required_paths if not path.is_file()]
    if missing:
        raise FoundationError(
            "invalid vcpkg root; missing required files: " + ", ".join(missing)
        )
    output = foundation_toolchain_path(paths)
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_suffix(".json.tmp")
    temporary.write_text(
        json.dumps({"schema_version": 1, "vcpkg_root": str(root)}, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.replace(output)
    return output


def load_foundation_toolchain(paths: WorkspacePaths) -> dict[str, str]:
    return load_foundation_toolchain_file(foundation_toolchain_path(paths))


def load_foundation_toolchain_file(path: Path) -> dict[str, str]:
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise FoundationError(
            f"cannot read Foundation toolchain config {path}: {exc}"
        ) from exc
    if set(data) != {"schema_version", "vcpkg_root"} or data["schema_version"] != 1:
        raise FoundationError(f"unsupported Foundation toolchain config: {path}")
    value = data["vcpkg_root"]
    if not isinstance(value, str) or not value:
        raise FoundationError(f"Foundation toolchain vcpkg_root is invalid: {path}")
    root = Path(value).resolve()
    missing = [
        str(required)
        for required in _vcpkg_required_paths(root)
        if not required.is_file()
    ]
    if missing:
        raise FoundationError(
            "Foundation toolchain vcpkg_root is not usable; "
            "missing required files: " + ", ".join(missing)
        )
    return {"vcpkg_root": str(root)}


def inspect_foundation_toolchain(
    recipe: Path,
    prefix: Path,
    requirements: dict[str, dict],
) -> dict | None:
    required_by = sorted(set(requirements) & VCPKG_COMPONENTS)
    if platform.system() != "Windows" or not required_by:
        return None
    path = prefix.parent / "config" / "toolchain.json"
    try:
        toolchain = load_foundation_toolchain_file(path)
    except FoundationError as exc:
        return {
            "status": "INCOMPATIBLE",
            "path": str(path),
            "vcpkg_root": None,
            "required_by": required_by,
            "reason": str(exc),
            "remediation": (
                "re-register vcpkg with: python tools/foundation.py toolchain "
                f"--recipe-id {recipe.stem} --install-dir {prefix} "
                "--vcpkg-root <vcpkg-root>"
            ),
        }
    if not toolchain:
        return {
            "status": "MISSING",
            "path": str(path),
            "vcpkg_root": None,
            "required_by": required_by,
            "reason": "Foundation vcpkg toolchain is not registered",
            "remediation": (
                "register it before configure/build with: "
                "python tools/foundation.py toolchain "
                f"--recipe-id {recipe.stem} --install-dir {prefix} "
                "--vcpkg-root <vcpkg-root>"
            ),
        }
    return {
        "status": "SATISFIED",
        "path": str(path),
        "vcpkg_root": toolchain["vcpkg_root"],
        "required_by": required_by,
        "reason": None,
        "remediation": None,
    }


def foundation_python_executable(python_root: Path) -> Path:
    # Windows portable packages use the official embeddable distribution,
    # whose interpreter is at the Python root rather than Scripts/python.exe.
    portable_python = python_root / "python.exe"
    if portable_python.is_file():
        return portable_python
    return python_root / (
        "Scripts/python.exe" if sys.platform == "win32" else "bin/python"
    )


def probe_python(executable: Path) -> dict:
    probe = (
        "import json, platform, sys, sysconfig; "
        "print(json.dumps({"
        "'implementation': platform.python_implementation(), "
        "'executable': sys.executable, "
        "'prefix': sys.prefix, "
        "'version': platform.python_version(), "
        "'major': sys.version_info.major, "
        "'minor': sys.version_info.minor, "
        "'soabi': sysconfig.get_config_var('SOABI') or '', "
        "'extension_suffix': sysconfig.get_config_var('EXT_SUFFIX') or ''}))"
    )
    result = subprocess.run(
        [str(executable), "-c", probe],
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise FoundationError(
            f"Foundation Python probe failed for {executable}: "
            f"{result.stderr.strip()}"
        )
    try:
        metadata = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise FoundationError(
            f"Foundation Python returned invalid metadata: {result.stdout.strip()}"
        ) from exc
    if not metadata.get("soabi"):
        suffix = metadata.get("extension_suffix")
        if isinstance(suffix, str):
            name = suffix.lstrip(".")
            for extension in (".pyd", ".so"):
                if name.endswith(extension):
                    name = name[: -len(extension)]
                    break
            if name not in {"so", "pyd"}:
                metadata["soabi"] = name
    return metadata


def validate_foundation_python_probe(probe: dict, python_root: Path) -> None:
    expected_major, expected_minor = FOUNDATION_PYTHON_VERSION
    actual = (probe.get("major"), probe.get("minor"))
    if probe.get("implementation") != FOUNDATION_PYTHON_IMPLEMENTATION:
        raise FoundationError(
            "Foundation Python implementation mismatch: "
            f"required={FOUNDATION_PYTHON_IMPLEMENTATION}, "
            f"selected={probe.get('implementation')}"
        )
    if actual != FOUNDATION_PYTHON_VERSION:
        raise FoundationError(
            "Foundation Python version mismatch: "
            f"required={expected_major}.{expected_minor}, "
            f"selected={probe.get('version')} ({probe.get('executable')})"
        )
    prefix = probe.get("prefix")
    if not isinstance(prefix, str) or Path(prefix).resolve() != python_root.resolve():
        raise FoundationError(
            "Foundation Python prefix mismatch: "
            f"required={python_root}, selected={prefix}"
        )
    if not probe.get("soabi") or not probe.get("extension_suffix"):
        raise FoundationError(
            f"Foundation Python does not expose SOABI metadata: {probe.get('executable')}"
        )


def ensure_foundation_python(paths: WorkspacePaths, recipe: Path) -> tuple[Path, dict]:
    executable = foundation_python_executable(paths.foundation_python)
    rerun = (
        f"{executable} tools/foundation.py build --recipe {recipe}"
        if executable.is_file()
        else f"python3.12 tools/foundation.py build --recipe {recipe}"
    )
    if not executable.is_file():
        if (
            platform.python_implementation() != FOUNDATION_PYTHON_IMPLEMENTATION
            or sys.version_info[:2] != FOUNDATION_PYTHON_VERSION
        ):
            raise FoundationError(
                "Foundation Python is not initialized and must be created with "
                "CPython 3.12 before any Foundation changes. Re-run with: " + rerun
            )
        try:
            subprocess.run(
                [sys.executable, "-m", "venv", str(paths.foundation_python)],
                check=True,
            )
        except subprocess.CalledProcessError as exc:
            raise FoundationError(
                "Foundation Python 3.12 environment creation failed: "
                f"{paths.foundation_python}"
            ) from exc
    try:
        selected = probe_python(executable)
        validate_foundation_python_probe(selected, paths.foundation_python)
    except FoundationError as exc:
        raise FoundationError(f"{exc}. Re-run with: {rerun}") from exc
    return executable, selected


def print_paths(paths: WorkspacePaths, json_output: bool) -> None:
    data = paths.serializable()
    if json_output:
        print(json.dumps(data, ensure_ascii=False, indent=2))
        return
    for key, value in data.items():
        print(f"{key}: {value}")
