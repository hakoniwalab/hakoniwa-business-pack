#!/usr/bin/env python3
"""Build a relocatable Windows x64 workspace ZIP from a portable profile."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import urllib.request
import zipfile
from datetime import datetime, timezone
from pathlib import Path

try:
    from tools.portable_package_profiles import PortableProfile, load_profile
except ModuleNotFoundError:  # Direct execution: python tools/package_portable_workspace.py
    from portable_package_profiles import PortableProfile, load_profile


ROOT = Path(__file__).resolve().parents[1]
WORKSPACE_ROOT = ROOT.parent
RECIPE_ID = "city-world-web-ui"
PACKAGE_ID = "hakoniwa-business-pack-city-world-windows-x64"
GENERATION_REQUIREMENTS = (
    ROOT / "recipes" / "requirements" / "plateau-citygml-mujoco-walls.txt"
)
PORTABLE_CITY_WORLD_ENV = "HAKONIWA_PORTABLE_CITY_WORLD"
COMMON_IGNORES = {
    ".git",
    ".idea",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".venv",
    "__pycache__",
}
# pip is only needed to build the Foundation Python environment.  The portable
# package uses the official embeddable interpreter and never installs packages
# at runtime, so carrying pip adds no runtime capability and can exceed
# Windows MAX_PATH while staging the ZIP.
PORTABLE_PYTHON_IGNORES = COMMON_IGNORES | {"pip", "setuptools"}
FOUNDATION_PYTHON_STANDARD_ROOT_ENTRIES = {
    "DLLs",
    "Doc",
    "include",
    "Include",
    "Lib",
    "libs",
    "Scripts",
    "share",
    "tcl",
}
BUSINESS_PACK_IGNORES = COMMON_IGNORES | {"dist", "work"}
SIBLING_IGNORES = COMMON_IGNORES | {"build", "dist", "work"}


class PortablePackageError(RuntimeError):
    pass


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _windows_python(python_root: Path) -> Path:
    portable = python_root / "python.exe"
    if portable.is_file():
        return portable
    return python_root / "Scripts" / "python.exe"


def _site_packages(python_root: Path) -> Path:
    return python_root / "Lib" / "site-packages"


def _python_identity(python: Path) -> dict[str, object]:
    script = (
        "import json,platform,struct,sys;"
        "print(json.dumps({"
        "'version':platform.python_version(),"
        "'major':sys.version_info.major,"
        "'minor':sys.version_info.minor,"
        "'bits':struct.calcsize('P')*8,"
        "'machine':platform.machine()"
        "}))"
    )
    completed = subprocess.run(
        [str(python), "-c", script],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode:
        raise PortablePackageError(
            f"failed to inspect Python runtime {python}: "
            f"{completed.stderr.strip() or completed.stdout.strip()}"
        )
    try:
        return json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise PortablePackageError(
            f"invalid Python identity output from {python}: {completed.stdout!r}"
        ) from exc


def _require_windows_x64(identity: dict[str, object]) -> None:
    if int(identity.get("major", 0)) != 3 or int(identity.get("minor", 0)) != 12:
        raise PortablePackageError(
            f"City World portable package requires CPython 3.12, "
            f"got {identity.get('version')}"
        )
    if int(identity.get("bits", 0)) != 64:
        raise PortablePackageError("City World portable package requires 64-bit Python")
    if str(identity.get("machine", "")).lower() not in {"amd64", "x86_64"}:
        raise PortablePackageError(
            f"City World portable package requires Windows x64, "
            f"got machine={identity.get('machine')!r}"
        )


def _copy_tree(source: Path, destination: Path, ignored_names: set[str]) -> None:
    if not source.is_dir():
        raise PortablePackageError(f"required directory not found: {source}")

    def ignore(_directory: str, names: list[str]) -> set[str]:
        return {name for name in names if name in ignored_names}

    shutil.copytree(source, destination, ignore=ignore, dirs_exist_ok=True)


def _copy_repository(
    source: Path,
    destination: Path,
    include_paths: tuple[str, ...],
) -> None:
    if not include_paths:
        _copy_tree(source, destination, SIBLING_IGNORES)
    else:
        destination.mkdir(parents=True, exist_ok=True)
        for relative in include_paths:
            item = source / relative
            if not item.exists():
                raise PortablePackageError(
                    f"portable profile include path not found: {item}"
                )
            target = destination / relative
            if item.is_dir():
                _copy_tree(item, target, SIBLING_IGNORES)
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(item, target)
    (destination / ".hakoniwa-repository-root").write_text(
        "portable repository root\n", encoding="utf-8"
    )


def _copy_foundation(source: Path, destination: Path) -> None:
    if not source.is_dir():
        raise PortablePackageError(
            f"Foundation workspace is missing; configure it first: {source}"
        )

    def ignore(directory: str, names: list[str]) -> set[str]:
        # CMake build trees are reproducible intermediates, not runtime assets.
        # Excluding them also avoids Windows MAX_PATH failures while staging.
        ignored = {name for name in names if name in COMMON_IGNORES | {"build"}}
        if Path(directory).resolve() == (source / "install").resolve():
            ignored.add("python")
        return ignored

    shutil.copytree(source, destination, ignore=ignore, dirs_exist_ok=True)


def _download_embedded_python(version: str, destination: Path) -> Path:
    url = (
        f"https://www.python.org/ftp/python/{version}/"
        f"python-{version}-embed-amd64.zip"
    )
    destination.parent.mkdir(parents=True, exist_ok=True)
    print(f"> download {url}")
    try:
        with urllib.request.urlopen(url, timeout=60) as response:
            with destination.open("wb") as output:
                shutil.copyfileobj(response, output)
    except OSError as exc:
        raise PortablePackageError(
            f"failed to download official Python embeddable package: {url}: {exc}"
        ) from exc
    return destination


def _embedded_python_archive(
    version: str,
    explicit: Path | None,
    download_dir: Path,
) -> Path:
    if explicit is not None:
        archive = explicit.expanduser().resolve()
        if not archive.is_file():
            raise PortablePackageError(f"Python embeddable ZIP not found: {archive}")
        return archive
    return _download_embedded_python(
        version, download_dir / f"python-{version}-embed-amd64.zip"
    )


def _relative_python_path(_python_root: Path, package_root_entry: str) -> str:
    # The portable interpreter is always located at
    # hakoniwa-business-pack/work/foundation/install/python.  Keep this
    # calculation independent of the temporary staging directory depth.
    normalized = package_root_entry.replace("\\", "/").strip("/")
    if normalized == "hakoniwa-business-pack":
        return r"..\..\..\.."
    return "..\\..\\..\\..\\..\\" + normalized.replace("/", "\\")


def _rewrite_embedded_python_pth(
    python_root: Path,
    package_paths: tuple[str, ...] | None = None,
) -> Path:
    candidates = sorted(python_root.glob("python*._pth"))
    if len(candidates) != 1:
        raise PortablePackageError(
            f"expected exactly one python*._pth in {python_root}"
        )
    path = candidates[0]
    output: list[str] = []
    has_site_packages = False
    if package_paths is None:
        package_paths = (
            "hakoniwa-business-pack",
            "hakoniwa-pdu-python/src",
            "hakoniwa-envsim/tools",
            "hakoniwa-envsim/src/city_pipeline",
        )
    relative_paths = tuple(
        _relative_python_path(python_root, value) for value in package_paths
    )
    has_import_site = False
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line == "import site":
            has_import_site = True
        if line.replace("/", "\\").lower() == r"lib\site-packages":
            has_site_packages = True
        output.append(raw)
    if not has_site_packages:
        output.append(r"Lib\site-packages")
    existing = {line.replace("/", "\\").lower() for line in output}
    for relative in relative_paths:
        normalized = relative.replace("/", "\\")
        if normalized.lower() not in existing:
            output.append(normalized)
    if not has_import_site:
        output.append("import site")
    path.write_text("\n".join(output) + "\n", encoding="utf-8")
    return path


def _looks_absolute_windows_path(value: str) -> bool:
    normalized = value.strip().replace("/", "\\")
    return (
        normalized.startswith("\\\\")
        or (
            len(normalized) >= 3
            and normalized[0].isalpha()
            and normalized[1:3] == ":\\"
        )
    )


def _reject_absolute_pth_entries(site_packages: Path) -> None:
    offenders: list[str] = []
    for path in sorted(site_packages.glob("*.pth")):
        for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or line.startswith("import "):
                continue
            if _looks_absolute_windows_path(line):
                offenders.append(f"{path.name}: {line}")
    if offenders:
        raise PortablePackageError(
            "non-relocatable absolute path found in packaged .pth file: "
            + "; ".join(offenders)
        )


def _copy_site_packages(source_root: Path, destination_root: Path) -> None:
    source = _site_packages(source_root)
    if not source.is_dir():
        raise PortablePackageError(f"Python site-packages not found: {source}")
    destination = _site_packages(destination_root)
    destination.mkdir(parents=True, exist_ok=True)

    def ignore(directory: str, names: list[str]) -> set[str]:
        ignored = {name for name in names if name in PORTABLE_PYTHON_IGNORES}
        # SBOMs belong to installed-wheel provenance, not to the runtime.  They
        # can make the temporary Windows staging path exceed MAX_PATH.
        if Path(directory).name.endswith(".dist-info"):
            ignored.add("sboms")
        return ignored

    for item in source.iterdir():
        # Workspace prepare regenerates this path for the extraction directory.
        if item.name == "hakoniwa_workspace_bootstrap.pth":
            continue
        if item.name in PORTABLE_PYTHON_IGNORES or item.name.startswith(
            ("pip-", "setuptools-")
        ):
            continue
        target = destination / item.name
        if item.is_dir():
            shutil.copytree(
                item,
                target,
                ignore=ignore,
                dirs_exist_ok=True,
            )
        else:
            shutil.copy2(item, target)
    _reject_absolute_pth_entries(destination)


def _copy_foundation_python_runtime_packages(
    source_root: Path, destination_root: Path
) -> None:
    """Copy Foundation-native Python packages installed beside python.exe.

    Foundation-native modules use the ``hakoniwa_*`` namespace and may be
    installed at the Python root rather than under Lib/site-packages.  The
    embeddable interpreter keeps its own executable but must retain every
    such package, not only the currently required Endpoint module.
    """
    found = False
    for source in source_root.iterdir():
        name = source.name
        if name in FOUNDATION_PYTHON_STANDARD_ROOT_ENTRIES or not name.startswith(
            "hakoniwa_"
        ):
            continue
        if source.is_dir():
            shutil.copytree(
                source,
                destination_root / name,
                ignore=lambda _directory, names: {
                    item for item in names if item in PORTABLE_PYTHON_IGNORES
                },
                dirs_exist_ok=True,
            )
            found = True
        elif source.is_file() and source.suffix.lower() == ".pyd":
            shutil.copy2(source, destination_root / name)
            found = True
    if not found:
        raise PortablePackageError(
            f"Foundation-native Python runtime package not found: {source_root}"
        )


def _extract_python_archive(archive_path: Path, destination: Path) -> None:
    try:
        with zipfile.ZipFile(archive_path) as archive:
            destination_root = destination.resolve()
            for member in archive.infolist():
                target = (destination / member.filename).resolve()
                try:
                    target.relative_to(destination_root)
                except ValueError as exc:
                    raise PortablePackageError(
                        f"unsafe path in Python embeddable ZIP: {member.filename}"
                    ) from exc
            archive.extractall(destination)
    except zipfile.BadZipFile as exc:
        raise PortablePackageError(
            f"invalid Python embeddable ZIP: {archive_path}"
        ) from exc


def _materialize_portable_python(
    embedded_zip: Path,
    source_python_root: Path,
    destination: Path,
    package_paths: tuple[str, ...] | None = None,
) -> None:
    if destination.exists():
        shutil.rmtree(destination)
    destination.mkdir(parents=True)
    _extract_python_archive(embedded_zip, destination)
    if not (destination / "python.exe").is_file():
        raise PortablePackageError(
            f"embeddable Python archive did not contain python.exe: {embedded_zip}"
        )
    _rewrite_embedded_python_pth(destination, package_paths)
    _copy_site_packages(source_python_root, destination)
    _copy_foundation_python_runtime_packages(source_python_root, destination)


def _install_portable_requirements(
    source_python: Path,
    destination: Path,
    requirements: tuple[Path, ...],
) -> None:
    missing = [str(path) for path in requirements if not path.is_file()]
    if missing:
        raise PortablePackageError("portable requirements not found: " + ", ".join(missing))
    if not requirements:
        return
    command = [
        str(source_python),
        "-m",
        "pip",
        "install",
        "--disable-pip-version-check",
        "--no-compile",
        "--target",
        str(_site_packages(destination)),
    ]
    for requirement in requirements:
        command.extend(("-r", str(requirement)))
    _run_checked(
        command,
        ROOT,
        "portable Python dependency installation",
    )


def _install_portable_generation_requirements(
    source_python: Path, destination: Path
) -> None:
    """Backward-compatible City World helper retained for callers and tests."""
    _install_portable_requirements(
        source_python, destination, (GENERATION_REQUIREMENTS,)
    )


def _git_revision(repository: Path) -> dict[str, object]:
    try:
        revision = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=repository,
            capture_output=True,
            text=True,
            check=False,
        )
        dirty = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=repository,
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError:
        return {"revision": "unknown", "dirty": None}
    if revision.returncode:
        return {"revision": "unknown", "dirty": None}
    return {
        "revision": revision.stdout.strip(),
        "dirty": bool(dirty.stdout.strip()) if dirty.returncode == 0 else None,
    }


def _render_start_batch() -> str:
    return r"""@echo off
setlocal
set "PACKAGE_ROOT=%~dp0"
set "BUSINESS_PACK=%PACKAGE_ROOT%hakoniwa-business-pack"
set "HAKO_PYTHON=%BUSINESS_PACK%\work\foundation\install\python\python.exe"

if not exist "%HAKO_PYTHON%" (
  echo [ERROR] Portable Hakoniwa Python was not found:
  echo         %HAKO_PYTHON%
  pause
  exit /b 2
)

pushd "%BUSINESS_PACK%"
set "PATH=%BUSINESS_PACK%\work\foundation\install\bin;%PATH%"
set "PYTHONNOUSERSITE=1"
set "HAKONIWA_PORTABLE_CITY_WORLD=1"
"%HAKO_PYTHON%" tools\workspace.py run -- "%HAKO_PYTHON%" -m tools.remote_operation.city_world.launcher start --runtime-dir "%BUSINESS_PACK%\work\recipes\city-world-web-ui\runtime" --launcher-runtime-dir "%BUSINESS_PACK%\work\recipes\city-world-web-ui\launcher" --parallel-workers 8 --terrain-spacing-m auto --open-browser
set "RC=%ERRORLEVEL%"
popd

if not "%RC%"=="0" (
  echo.
  echo [ERROR] City World failed to start. Check:
  echo         hakoniwa-business-pack\work\recipes\city-world-web-ui\launcher\logs
  pause
)
exit /b %RC%
"""


def _render_control_batch(command: str) -> str:
    if command not in {"status", "stop"}:
        raise ValueError(command)
    return rf"""@echo off
setlocal
set "PACKAGE_ROOT=%~dp0"
set "BUSINESS_PACK=%PACKAGE_ROOT%hakoniwa-business-pack"
set "HAKO_PYTHON=%BUSINESS_PACK%\work\foundation\install\python\python.exe"

pushd "%BUSINESS_PACK%"
set "PATH=%BUSINESS_PACK%\work\foundation\install\bin;%PATH%"
set "PYTHONNOUSERSITE=1"
set "HAKONIWA_PORTABLE_CITY_WORLD=1"
"%HAKO_PYTHON%" tools\workspace.py run -- "%HAKO_PYTHON%" -m tools.remote_operation.city_world.launcher {command} --launcher-runtime-dir "%BUSINESS_PACK%\work\recipes\city-world-web-ui\launcher"
set "RC=%ERRORLEVEL%"
popd
if not "%RC%"=="0" pause
exit /b %RC%
"""


def _write_entrypoints(package_root: Path) -> None:
    (package_root / "start-city-world.bat").write_text(
        _render_start_batch(), encoding="utf-8", newline="\r\n"
    )
    (package_root / "status-city-world.bat").write_text(
        _render_control_batch("status"), encoding="utf-8", newline="\r\n"
    )
    (package_root / "stop-city-world.bat").write_text(
        _render_control_batch("stop"), encoding="utf-8", newline="\r\n"
    )
    (package_root / "README-WINDOWS.txt").write_text(
        (
            "Hakoniwa Business Pack - City World Windows Portable\n"
            "\n"
            "1. このフォルダを任意の場所へ展開してください。\n"
            "2. start-city-world.bat をダブルクリックしてください。\n"
            "3. ブラウザで地図からPLATEAUの対象範囲を選択します。\n"
            "4. Capabilityを診断 -> City Worldを生成 -> Download ZIP の順に操作します。\n"
            "5. 終了するときは stop-city-world.bat を実行してください。\n"
            "\n"
            "Python / Git / WSL / Docker の追加インストールは不要です。\n"
            "生成データと共有キャッシュは "
            "hakoniwa-business-pack\\work\\recipes\\city-world-web-ui\\runtime\\ に保存されます。\n"
        ),
        encoding="utf-8",
    )


def _render_urban_batch(command: str) -> str:
    if command not in {"start", "status", "stop"}:
        raise ValueError(command)
    prepare = ""
    if command == "start":
        prepare = r'''"%HAKO_PYTHON%" tools\portable_urban_car.py prepare
if not "%ERRORLEVEL%"=="0" (
  echo [ERROR] Urban Car portable workspace preparation failed.
  popd
  pause
  exit /b 2
)
'''
    return rf"""@echo off
setlocal
set "PACKAGE_ROOT=%~dp0"
set "BUSINESS_PACK=%PACKAGE_ROOT%hakoniwa-business-pack"
set "URBAN=%PACKAGE_ROOT%hakoniwa-urban-mobility"
set "HAKO_PYTHON=%BUSINESS_PACK%\work\foundation\install\python\python.exe"

if not exist "%HAKO_PYTHON%" (
  echo [ERROR] Portable Hakoniwa Python was not found:
  echo         %HAKO_PYTHON%
  pause
  exit /b 2
)

pushd "%URBAN%"
set "PATH=%BUSINESS_PACK%\work\foundation\install\bin;%URBAN%\build\bin;%PATH%"
set "PYTHONNOUSERSITE=1"
set "HAKONIWA_PORTABLE_WORKSPACE=1"
set "HAKONIWA_WORKSPACE_ROOT=%BUSINESS_PACK%"
set "HAKONIWA_WORK_DIR=%BUSINESS_PACK%\work"
set "HAKONIWA_HOME=%BUSINESS_PACK%\work\foundation\install"
set "HAKO_CONFIG_PATH=%BUSINESS_PACK%\work\foundation\config\cpp_core_config.json"
set "HAKO_PDU_ENDPOINT_RUNTIME_DIRS=%BUSINESS_PACK%\work\foundation\install\bin"
set "VIRTUAL_ENV=%BUSINESS_PACK%\work\foundation\install\python"
{prepare}"%HAKO_PYTHON%" tools\urban_mobility.py {command} --recipe recipes\usecases\urban-car-rc.yaml
set "RC=%ERRORLEVEL%"
popd
if not "%RC%"=="0" pause
exit /b %RC%
"""


def _write_urban_entrypoints(package_root: Path) -> None:
    for command in ("start", "status", "stop"):
        (package_root / f"{command}-urban-car.bat").write_text(
            _render_urban_batch(command), encoding="utf-8", newline="\r\n"
        )
    (package_root / "README-WINDOWS.txt").write_text(
        (
            "Hakoniwa Urban Car RC - Windows Portable\n\n"
            "1. このフォルダを任意の場所へ展開してください。\n"
            "2. DualSense controllerをWindowsへ接続してください。\n"
            "3. start-urban-car.batを実行してください。\n"
            "4. 終了するときはstop-urban-car.batを実行してください。\n\n"
            "Python / Git / WSL / Docker / C++ build toolchainは不要です。\n"
            "初回start時と展開先変更後には、同梱City Worldを基準に設定を再生成します。\n"
        ),
        encoding="utf-8",
    )


REPOSITORY_COMMANDS = ("start", "status", "stop")


def _repository_entrypoints(profile: PortableProfile) -> dict[str, str]:
    tool = _require_repository_tool(profile)
    return {
        command: f"{command}-{tool.entrypoint_name}.bat"
        for command in REPOSITORY_COMMANDS
    }


def _require_repository_tool(profile: PortableProfile):
    if profile.repository_tool is None:
        raise PortablePackageError(
            f"portable profile {profile.id} has no repository tool contract"
        )
    return profile.repository_tool


def _render_repository_batch(profile: PortableProfile, command: str) -> str:
    """Entrypoint for a repository-owned profile.

    The repository tool owns relocation and first-start configuration
    (``start`` runs its own ``prepare``); the batch only selects the packaged
    Foundation and Workspace, overriding any ambient Hakoniwa environment.
    """
    if command not in REPOSITORY_COMMANDS:
        raise ValueError(command)
    tool = _require_repository_tool(profile)
    tool_path = tool.tool.replace("/", "\\")
    return rf"""@echo off
setlocal
set "PACKAGE_ROOT=%~dp0"
set "BUSINESS_PACK=%PACKAGE_ROOT%hakoniwa-business-pack"
set "APP_ROOT=%PACKAGE_ROOT%{tool.repository}"
set "HAKO_PYTHON=%BUSINESS_PACK%\work\foundation\install\python\python.exe"

if not exist "%HAKO_PYTHON%" (
  echo [ERROR] Portable Hakoniwa Python was not found:
  echo         %HAKO_PYTHON%
  pause
  exit /b 2
)

pushd "%APP_ROOT%"
set "PATH=%BUSINESS_PACK%\work\foundation\install\python;%BUSINESS_PACK%\work\foundation\install\bin;%PATH%"
set "PYTHONNOUSERSITE=1"
set "PYTHONPATH="
set "PYTHONHOME="
rem Deep __pycache__ paths would count against MAX_PATH in the user's folder.
set "PYTHONDONTWRITEBYTECODE=1"
set "HAKONIWA_PORTABLE_WORKSPACE=1"
rem The Workspace bootstrap registers Foundation DLL directories only when active.
set "HAKONIWA_WORKSPACE_ACTIVE=1"
set "HAKONIWA_WORKSPACE_ROOT=%BUSINESS_PACK%"
set "HAKONIWA_WORK_DIR=%BUSINESS_PACK%\work"
set "HAKONIWA_HOME=%BUSINESS_PACK%\work\foundation\install"
set "HAKO_CONFIG_PATH=%BUSINESS_PACK%\work\foundation\config\cpp_core_config.json"
set "HAKO_PDU_ENDPOINT_RUNTIME_DIRS=%BUSINESS_PACK%\work\foundation\install\bin"
set "VIRTUAL_ENV=%BUSINESS_PACK%\work\foundation\install\python"
"%HAKO_PYTHON%" {tool_path} {command}
set "RC=%ERRORLEVEL%"
popd
if not "%RC%"=="0" pause
exit /b %RC%
"""


def _write_repository_entrypoints(package_root: Path, profile: PortableProfile) -> None:
    tool = _require_repository_tool(profile)
    for command, name in _repository_entrypoints(profile).items():
        (package_root / name).write_text(
            _render_repository_batch(profile, command), encoding="utf-8", newline="\r\n"
        )
    readme = package_root / tool.repository / tool.readme
    if not readme.is_file():
        raise PortablePackageError(f"portable README declared by {profile.id} not found: {readme}")
    (package_root / "README-WINDOWS.txt").write_text(
        readme.read_text(encoding="utf-8"), encoding="utf-8", newline="\r\n"
    )


def _write_profile_entrypoints(package_root: Path, profile: PortableProfile) -> None:
    if profile.kind == "city-world-web-ui":
        _write_entrypoints(package_root)
    elif profile.kind == "urban-car-rc":
        _write_urban_entrypoints(package_root)
    elif profile.kind == "repository":
        _write_repository_entrypoints(package_root, profile)
    else:
        raise PortablePackageError(f"unsupported portable profile kind: {profile.kind}")


def _run_checked(
    command: list[str],
    cwd: Path,
    label: str,
    env: dict[str, str] | None = None,
) -> None:
    print(">", subprocess.list2cmdline(command))
    completed = subprocess.run(command, cwd=cwd, env=env, check=False)
    if completed.returncode:
        raise PortablePackageError(
            f"{label} failed with exit={completed.returncode}"
        )


def _materialize_web_ui_recipe(foundation_python: Path, business_pack: Path) -> None:
    """Render portable Recipe paths after the ZIP has its final directory layout."""
    code = (
        "import runpy; from pathlib import Path; "
        "recipe=runpy.run_path('tools/recipe.py'); "
        "path=Path('recipes/examples/city-world-web-ui.yaml').resolve(); "
        "recipe['materialize_recipe_runtime'](path, recipe['load_recipe'](path))"
    )
    _run_checked(
        [str(foundation_python), "-c", code],
        business_pack,
        "portable City World Web UI Recipe materialization",
    )


def _configured_urban_city_receipt(explicit: Path | None) -> Path:
    if explicit is not None:
        receipt = explicit.expanduser().resolve()
    else:
        composition = ROOT / "work/recipes/urban-car-rc/config/urban-composition.json"
        try:
            payload = json.loads(composition.read_text(encoding="utf-8"))
            receipt = Path(
                payload["inputs"]["business_pack_city_receipt"]["path"]
            ).expanduser().resolve()
        except (OSError, KeyError, TypeError, json.JSONDecodeError) as exc:
            raise PortablePackageError(
                "Urban Car profile requires --city-receipt or an existing "
                f"configured composition: {composition}"
            ) from exc
    if not receipt.is_file():
        raise PortablePackageError(f"City World receipt not found: {receipt}")
    return receipt


def _replace_json_path_root(value: object, source: str, replacement: str) -> object:
    if isinstance(value, str):
        source_path = source.rstrip("\\/")
        if value.lower() == source_path.lower():
            return replacement
        for separator in ("\\", "/"):
            prefix = source_path + separator
            if value.lower().startswith(prefix.lower()):
                suffix = value[len(prefix) :].replace("\\", "/")
                return replacement.rstrip("/") + "/" + suffix
        return value
    if isinstance(value, list):
        return [
            _replace_json_path_root(item, source, replacement) for item in value
        ]
    if isinstance(value, dict):
        return {
            str(_replace_json_path_root(key, source, replacement)):
            _replace_json_path_root(item, source, replacement)
            for key, item in value.items()
        }
    return value


def _scrub_bundled_city_paths(
    portable_city_root: Path,
    source_build_root: Path,
    token: str = "__HAKONIWA_CITY_BUILD__",
) -> None:
    for path in portable_city_root.rglob("*.json"):
        payload = json.loads(path.read_text(encoding="utf-8"))
        payload = _replace_json_path_root(
            payload, str(source_build_root), token
        )
        path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )


def _prepare_source_profile(
    profile: PortableProfile,
    foundation_python: Path,
    city_receipt: Path | None,
) -> Path | None:
    _run_checked(
        [str(foundation_python), "tools/workspace.py", "prepare"],
        ROOT,
        "source Workspace prepare",
    )
    if profile.kind == "city-world-web-ui":
        for operation in ("configure", "doctor"):
            _run_checked(
                [
                    str(foundation_python),
                    "tools/recipe/city_world_web_ui.py",
                    operation,
                ],
                ROOT,
                f"source City World Web UI Recipe {operation}",
            )
        return None
    if profile.kind == "urban-car-rc":
        receipt = _configured_urban_city_receipt(city_receipt)
        urban = WORKSPACE_ROOT / "hakoniwa-urban-mobility"
        _run_checked(
            [
                str(foundation_python),
                "tools/urban_mobility.py",
                "doctor",
                "--recipe",
                "recipes/usecases/urban-car-rc.yaml",
            ],
            urban,
            "source Urban Car Recipe doctor",
        )
        return receipt
    if profile.kind == "repository":
        tool = _require_repository_tool(profile)
        owner = WORKSPACE_ROOT / tool.repository
        for operation in ("collect", "doctor"):
            _run_checked(
                [str(foundation_python), tool.tool, operation],
                owner,
                f"source {profile.id} portable {operation}",
            )
        return None
    raise PortablePackageError(f"unsupported portable profile kind: {profile.kind}")


def _bundle_urban_runtime(package_root: Path, receipt: Path) -> None:
    urban_source = WORKSPACE_ROOT / "hakoniwa-urban-mobility"
    source_bin = urban_source / "build/bin"
    executable = source_bin / "urban-car-hakoniwa-asset.exe"
    if not executable.is_file():
        raise PortablePackageError(f"built Urban Car executable not found: {executable}")
    _copy_tree(
        source_bin,
        package_root / "hakoniwa-urban-mobility/build/bin",
        COMMON_IGNORES,
    )

    source_build_root = receipt.parent.parent.resolve()
    if source_build_root.name.lower() != "build":
        raise PortablePackageError(
            "City World receipt must be located under <job>/build/world: "
            f"{receipt}"
        )
    destination = package_root / "portable-data/city-world/build"
    # The raw PLATEAU source can be many gigabytes and is not consumed by the
    # Urban runtime.  Bundle only the generated world and its referenced
    # components, plus the collider viewer artifact beside build/.
    for name in ("world", "components"):
        _copy_tree(
            source_build_root / name,
            destination / name,
            COMMON_IGNORES,
        )
    source_viewer = source_build_root.parent / "viewer"
    collider = source_viewer / "city-world-colliders.glb"
    if not collider.is_file():
        raise PortablePackageError(f"City World collider GLB not found: {collider}")
    packaged_viewer = package_root / "portable-data/city-world/viewer"
    packaged_viewer.mkdir(parents=True, exist_ok=True)
    shutil.copy2(collider, packaged_viewer / collider.name)
    path_token = "__HAKONIWA_CITY_BUILD__"
    _scrub_bundled_city_paths(
        package_root / "portable-data/city-world",
        source_build_root,
        path_token,
    )
    metadata = {
        "schema_version": 1,
        "path_token": path_token,
        "receipt_relative": receipt.relative_to(source_build_root).as_posix(),
    }
    metadata_path = package_root / "portable-data/city-world.json"
    metadata_path.parent.mkdir(parents=True, exist_ok=True)
    metadata_path.write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def _validate_staged_package(package_root: Path) -> None:
    business_pack = package_root / "hakoniwa-business-pack"
    foundation_python = (
        business_pack / "work" / "foundation" / "install" / "python" / "python.exe"
    )
    required = (
        foundation_python,
        package_root / "hakoniwa-envsim" / "tools" / "hako.py",
        package_root / "hakoniwa-pdu-javascript" / "src" / "index.js",
        package_root / "hakoniwa-pdu-python" / "src" / "hakoniwa_pdu" / "apps" / "launcher" / "hako_launcher.py",
    )
    missing = [str(path) for path in required if not path.is_file()]
    if missing:
        raise PortablePackageError(
            "portable package is missing required files: " + ", ".join(missing)
        )
    _run_checked(
        [str(foundation_python), "tools/workspace.py", "prepare"],
        business_pack,
        "portable Workspace prepare",
    )
    _materialize_web_ui_recipe(foundation_python, business_pack)
    _run_checked(
        [
            str(foundation_python),
            "-c",
            "import PIL, mapbox_earcut, numpy, plateau_citygml, pyproj, "
            "shapely, trimesh, city_world_composer; "
            "import tools, hakoniwa_pdu, hakoniwa_pdu_endpoint; "
            "print('portable runtime imports OK')",
        ],
        business_pack,
        "portable runtime import validation",
    )
    _run_checked(
        [str(foundation_python), "tools/recipe/city_world_web_ui.py", "doctor"],
        business_pack,
        "portable City World Web UI Recipe doctor",
    )


def _staged_environment(business_pack: Path) -> dict[str, str]:
    """Process environment matching the package entrypoints, for staging checks."""
    staged_env = os.environ.copy()
    for name in ("PYTHONPATH", "PYTHONHOME"):
        staged_env.pop(name, None)
    staged_env.update(
        {
            "PYTHONNOUSERSITE": "1",
            "HAKONIWA_PORTABLE_WORKSPACE": "1",
            "HAKONIWA_WORKSPACE_ACTIVE": "1",
            "HAKONIWA_WORKSPACE_ROOT": str(business_pack),
            "HAKONIWA_WORK_DIR": str(business_pack / "work"),
            "HAKONIWA_HOME": str(business_pack / "work/foundation/install"),
            "HAKO_CONFIG_PATH": str(
                business_pack / "work/foundation/config/cpp_core_config.json"
            ),
            "VIRTUAL_ENV": str(business_pack / "work/foundation/install/python"),
            "HAKO_PDU_ENDPOINT_RUNTIME_DIRS": str(
                business_pack / "work/foundation/install/bin"
            ),
        }
    )
    staged_env["PATH"] = os.pathsep.join(
        (
            str(business_pack / "work/foundation/install/python"),
            str(business_pack / "work/foundation/install/bin"),
            staged_env.get("PATH", ""),
        )
    )
    return staged_env


def _validate_repository_staged_package(
    package_root: Path, profile: PortableProfile
) -> None:
    tool = _require_repository_tool(profile)
    business_pack = package_root / "hakoniwa-business-pack"
    owner = package_root / tool.repository
    foundation_python = business_pack / "work/foundation/install/python/python.exe"
    required = [foundation_python, owner / tool.tool]
    required.extend(
        package_root / repository.name / repository.required_artifact
        for repository in profile.repositories
    )
    missing = [str(path) for path in required if not path.exists()]
    if missing:
        raise PortablePackageError(
            "portable package is missing required files: " + ", ".join(missing)
        )
    staged_env = _staged_environment(business_pack)
    _run_checked(
        [str(foundation_python), "tools/workspace.py", "prepare"],
        business_pack,
        "portable Workspace prepare",
        staged_env,
    )
    _run_checked(
        [str(foundation_python), tool.tool, "prepare"],
        owner,
        f"portable {profile.id} prepare",
        staged_env,
    )
    _run_checked(
        [
            str(foundation_python),
            "-c",
            "import " + ", ".join(tool.validation_imports)
            + f"; print('portable {profile.id} runtime imports OK')",
        ],
        owner,
        f"portable {profile.id} runtime import validation",
        staged_env,
    )


def _validate_urban_staged_package(package_root: Path) -> None:
    business_pack = package_root / "hakoniwa-business-pack"
    urban = package_root / "hakoniwa-urban-mobility"
    foundation_python = (
        business_pack / "work/foundation/install/python/python.exe"
    )
    required = (
        foundation_python,
        urban / "build/bin/urban-car-hakoniwa-asset.exe",
        urban / "tools/portable_urban_car.py",
        package_root / "portable-data/city-world.json",
        package_root / "hakoniwa-threejs-drone/index.html",
        package_root / "hakoniwa-map-viewer/src/client/index.html",
    )
    missing = [str(path) for path in required if not path.is_file()]
    if missing:
        raise PortablePackageError(
            "portable package is missing required files: " + ", ".join(missing)
        )
    staged_env = os.environ.copy()
    staged_env.update(
        {
            "HAKONIWA_WORKSPACE_ROOT": str(business_pack),
            "HAKONIWA_WORK_DIR": str(business_pack / "work"),
            "HAKONIWA_HOME": str(business_pack / "work/foundation/install"),
            "HAKO_CONFIG_PATH": str(
                business_pack / "work/foundation/config/cpp_core_config.json"
            ),
            "VIRTUAL_ENV": str(
                business_pack / "work/foundation/install/python"
            ),
            "HAKO_PDU_ENDPOINT_RUNTIME_DIRS": str(
                business_pack / "work/foundation/install/bin"
            ),
        }
    )
    staged_env["PATH"] = os.pathsep.join(
        (
            str(business_pack / "work/foundation/install/python"),
            str(business_pack / "work/foundation/install/bin"),
            staged_env.get("PATH", ""),
        )
    )
    _run_checked(
        [str(foundation_python), "tools/workspace.py", "prepare"],
        business_pack,
        "portable Workspace prepare",
        staged_env,
    )
    _run_checked(
        [str(foundation_python), "tools/portable_urban_car.py", "prepare"],
        urban,
        "portable Urban Car materialization",
        staged_env,
    )
    _run_checked(
        [
            str(foundation_python),
            "-c",
            "import mujoco, pygame, yaml; import hakoniwa_pdu, "
            "hakoniwa_pdu_endpoint; print('portable Urban runtime imports OK')",
        ],
        urban,
        "portable Urban runtime import validation",
        staged_env,
    )


def _validate_profile_package(
    package_root: Path, profile: PortableProfile
) -> None:
    if profile.kind == "city-world-web-ui":
        _validate_staged_package(package_root)
    elif profile.kind == "urban-car-rc":
        _validate_urban_staged_package(package_root)
    elif profile.kind == "repository":
        _validate_repository_staged_package(package_root, profile)
    else:
        raise PortablePackageError(f"unsupported portable profile kind: {profile.kind}")


PORTABLE_MMAP_TOKEN = "__HAKONIWA_PORTABLE_MMAP__"


def _replace_core_mmap_path(business_pack: Path) -> None:
    """Drop the staging mmap path; the package tool relocates it on first start."""
    core_config = business_pack / "work/foundation/config/cpp_core_config.json"
    payload = json.loads(core_config.read_text(encoding="utf-8"))
    payload["core_mmap_path"] = PORTABLE_MMAP_TOKEN
    core_config.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def _remove_staging_specific_workspace_files(
    package_root: Path,
    profile: PortableProfile | None = None,
) -> None:
    """Remove files whose contents were generated with the temporary staging path.

    workspace.py run regenerates them for the user's actual extraction path
    before starting any City World process.
    """
    business_pack = package_root / "hakoniwa-business-pack"
    for path in (
        business_pack / "work" / "foundation" / "activate",
        business_pack / "work" / "foundation" / "Activate.ps1",
        business_pack
        / "work"
        / "foundation"
        / "install"
        / "python"
        / "Lib"
        / "site-packages"
        / "hakoniwa_workspace_bootstrap.pth",
    ):
        path.unlink(missing_ok=True)
    if profile is not None and profile.kind == "repository":
        tool = _require_repository_tool(profile)
        for relative in tool.staging_cleanup:
            target = package_root / relative
            if target.is_dir():
                shutil.rmtree(target)
            else:
                target.unlink(missing_ok=True)
        mmap_dir = business_pack / "work/foundation/runtime/mmap"
        if mmap_dir.is_dir():
            shutil.rmtree(mmap_dir)
        _replace_core_mmap_path(business_pack)
    if profile is not None and profile.kind == "urban-car-rc":
        generated = business_pack / "work/recipes/urban-car-rc"
        if generated.is_dir():
            shutil.rmtree(generated)
        core_config = business_pack / "work/foundation/config/cpp_core_config.json"
        payload = json.loads(core_config.read_text(encoding="utf-8"))
        payload["core_mmap_path"] = "__HAKONIWA_PORTABLE_MMAP__"
        core_config.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        portable_city = package_root / "portable-data/city-world"
        current_build = portable_city / "build"
        _scrub_bundled_city_paths(portable_city, current_build.resolve())
        metadata_path = package_root / "portable-data/city-world.json"
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        metadata.pop("source_build_root", None)
        metadata["path_token"] = "__HAKONIWA_CITY_BUILD__"
        metadata_path.write_text(
            json.dumps(metadata, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        viewer = portable_city / "viewer"
        if viewer.is_dir():
            for path in viewer.iterdir():
                if path.name != "city-world-colliders.glb" and path.is_file():
                    path.unlink()


def _write_manifest(
    package_root: Path,
    python_identity: dict[str, object],
    embedded_zip: Path,
    sources: dict[str, Path],
    profile: PortableProfile | None = None,
) -> None:
    profile = profile or load_profile("city-world-web-ui", WORKSPACE_ROOT)
    if profile.kind == "city-world-web-ui":
        entrypoints = {
            "start": "start-city-world.bat",
            "status": "status-city-world.bat",
            "stop": "stop-city-world.bat",
        }
    elif profile.kind == "repository":
        entrypoints = _repository_entrypoints(profile)
    else:
        entrypoints = {
            "start": "start-urban-car.bat",
            "status": "status-urban-car.bat",
            "stop": "stop-urban-car.bat",
        }
    manifest = {
        "schema_version": 1,
        "package_id": profile.package_id,
        "profile": profile.id,
        "recipe_id": profile.recipe_id,
        "target": {"os": "windows", "arch": "x64"},
        "created_at": datetime.now(timezone.utc).isoformat(),
        "python": {
            "version": python_identity["version"],
            "embedded_archive": embedded_zip.name,
            "embedded_archive_sha256": _sha256(embedded_zip),
        },
        "sources": {
            name: _git_revision(path)
            for name, path in sources.items()
        },
        "entrypoints": entrypoints,
        "runtime_policy": {
            "requires_system_python": False,
            "requires_git": False,
            "requires_wsl": False,
            "requires_docker": False,
            "installs_python_packages_at_runtime": False,
            "includes_city_world_generation_dependencies": (
                profile.kind == "city-world-web-ui"
            ),
            "includes_configured_city_world": profile.kind == "urban-car-rc",
            "includes_previous_city_world_jobs": False,
            "includes_previous_plateau_cache": False,
        },
    }
    (package_root / "portable-package.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


# Windows MAX_PATH is 260 including the terminating NUL.
WINDOWS_MAX_PATH_CHARS = 259
# Room a user needs for an extraction folder such as C:\Users\<name>\Downloads\<zip name>\.
MIN_EXTRACTION_BUDGET_CHARS = 60


def _longest_relative_path(
    root: Path, ignored: set[str] | frozenset[str] = frozenset()
) -> tuple[int, str]:
    """Length (Windows separators) and value of the deepest file path under root.

    ``ignored`` mirrors the copy filters so excluded trees such as
    ``__pycache__`` do not count against the budget.
    """
    longest = (0, "")
    for directory, dirs, files in os.walk(root):
        dirs[:] = [
            name
            for name in dirs
            if name not in ignored
            and not (name == "sboms" and Path(directory).name.endswith(".dist-info"))
            and not name.startswith(("pip-", "setuptools-"))
        ]
        for name in files:
            relative = str(Path(directory, name).relative_to(root)).replace("/", "\\")
            if len(relative) > longest[0]:
                longest = (len(relative), relative)
    return longest


def _require_staging_path_budget(source_site_packages: Path, staged_site_packages: Path) -> None:
    """Fail before copying when the deepest Python file would exceed MAX_PATH."""
    length, relative = _longest_relative_path(source_site_packages, PORTABLE_PYTHON_IGNORES)
    total = len(str(staged_site_packages)) + 1 + length
    if total > WINDOWS_MAX_PATH_CHARS:
        raise PortablePackageError(
            f"staging path would be {total} characters (Windows limit {WINDOWS_MAX_PATH_CHARS}): "
            f"{staged_site_packages}\\{relative}. Use a shorter package_id, drop --keep-staging "
            "(a short temporary directory is used), or move the Business Pack checkout to a shorter path."
        )


def _report_extraction_budget(package_root: Path) -> int:
    """Print how long the user's extraction folder path may be; return that budget."""
    length, relative = _longest_relative_path(package_root.parent)
    # The user's folder is followed by a separator before the package folder.
    budget = WINDOWS_MAX_PATH_CHARS - length - 1
    print(f"[OK] Deepest packaged path: {length} characters ({relative})")
    if budget < MIN_EXTRACTION_BUDGET_CHARS:
        print(
            f"[WARN] Users must extract into a folder path of at most {budget} characters; "
            "shorten the package_id or the deepest bundled paths."
        )
    else:
        print(f"[OK] Extraction folder path may be up to {budget} characters")
    return budget


def _zip_tree(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        destination.unlink()
    with zipfile.ZipFile(
        destination,
        "w",
        compression=zipfile.ZIP_DEFLATED,
        compresslevel=6,
    ) as archive:
        for path in sorted(source.rglob("*")):
            if path.is_file():
                archive.write(path, path.relative_to(source.parent))


def build_package(
    output: Path | None,
    python_embed_zip: Path | None = None,
    keep_staging: bool = False,
    profile_id: str = "city-world-web-ui",
    city_receipt: Path | None = None,
) -> Path:
    if os.name != "nt":
        raise PortablePackageError(
            "Windows portable package must be built on Windows x64; "
            "Foundation binaries and Python wheels are platform-specific"
        )

    try:
        profile = load_profile(profile_id, WORKSPACE_ROOT)
    except ValueError as exc:
        raise PortablePackageError(str(exc)) from exc

    foundation_root = ROOT / "work" / "foundation"
    foundation_python_root = foundation_root / "install" / "python"
    foundation_python = _windows_python(foundation_python_root)
    if not foundation_python.is_file():
        raise PortablePackageError(
            f"Foundation Python is missing; configure it first: {foundation_python}"
        )
    python_identity = _python_identity(foundation_python)
    _require_windows_x64(python_identity)

    for repository in profile.repositories:
        artifact = repository.path / repository.required_artifact
        if not artifact.exists():
            raise PortablePackageError(
                f"{repository.name} checkout or required artifact is missing: {artifact}"
            )

    selected_receipt = _prepare_source_profile(
        profile, foundation_python, city_receipt
    )

    if output is None:
        output = ROOT / "dist" / f"{profile.package_id}.zip"
    output = output.expanduser().resolve()
    staging_base = ROOT / "work" / "portable-package"
    staging_base.mkdir(parents=True, exist_ok=True)
    temporary: tempfile.TemporaryDirectory[str] | None = None
    if keep_staging:
        package_root = staging_base / profile.package_id
        if package_root.exists():
            shutil.rmtree(package_root)
    else:
        # Keep the staging root short: package paths contain the full
        # Foundation Python site-packages hierarchy and Windows MAX_PATH still
        # applies to several stdlib copy operations.
        temporary = tempfile.TemporaryDirectory(prefix="hako-portable-")
        package_root = Path(temporary.name) / profile.package_id

    try:
        _require_staging_path_budget(
            _site_packages(foundation_python_root),
            _site_packages(
                package_root / "hakoniwa-business-pack" / "work" / "foundation" / "install" / "python"
            ),
        )
        package_root.mkdir(parents=True, exist_ok=True)
        business_pack_destination = package_root / "hakoniwa-business-pack"
        _copy_tree(
            ROOT,
            business_pack_destination,
            BUSINESS_PACK_IGNORES,
        )
        _copy_foundation(
            foundation_root,
            business_pack_destination / "work" / "foundation",
        )
        for repository in profile.repositories:
            _copy_repository(
                repository.path,
                package_root / repository.name,
                repository.include_paths,
            )
        if profile.kind == "urban-car-rc":
            if selected_receipt is None:
                raise PortablePackageError("Urban Car City World receipt was not selected")
            _bundle_urban_runtime(package_root, selected_receipt)

        embedded_zip = _embedded_python_archive(
            str(python_identity["version"]),
            python_embed_zip,
            staging_base / "downloads",
        )
        _materialize_portable_python(
            embedded_zip,
            foundation_python_root,
            business_pack_destination
            / "work"
            / "foundation"
            / "install"
            / "python",
            profile.python_paths,
        )
        _install_portable_requirements(
            foundation_python,
            business_pack_destination
            / "work"
            / "foundation"
            / "install"
            / "python",
            profile.requirements,
        )
        _write_profile_entrypoints(package_root, profile)
        _write_manifest(
            package_root,
            python_identity,
            embedded_zip,
            {"hakoniwa-business-pack": ROOT}
            | {item.name: item.path for item in profile.repositories},
            profile,
        )
        _validate_profile_package(package_root, profile)
        _remove_staging_specific_workspace_files(package_root, profile)
        _report_extraction_budget(package_root)
        _zip_tree(package_root, output)
        print(f"[OK] Windows portable package: {output}")
        print(f"[OK] SHA-256: {_sha256(output)}")
        if keep_staging:
            print(f"[OK] Staging retained: {package_root}")
        return output
    finally:
        if temporary is not None:
            temporary.cleanup()


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(
        description="Build a Windows x64 portable ZIP from a portable profile"
    )
    result.add_argument(
        "--profile",
        default="city-world-web-ui",
        help=(
            "portable distribution profile: city-world-web-ui (default), "
            "urban-car-rc, or a repository-owned profile declared in "
            "<sibling>/portable/windows-profile.json"
        ),
    )
    result.add_argument(
        "--output",
        type=Path,
        default=None,
        help="output ZIP (default: dist/<profile package-id>.zip)",
    )
    result.add_argument(
        "--python-embed-zip",
        type=Path,
        help=(
            "Use a local official python-<version>-embed-amd64.zip instead of "
            "downloading it from python.org"
        ),
    )
    result.add_argument(
        "--keep-staging",
        action="store_true",
        help="Keep assembled files under work/portable-package for inspection",
    )
    result.add_argument(
        "--city-receipt",
        type=Path,
        help=(
            "City World receipt to bundle for the urban-car-rc profile; "
            "defaults to the currently configured Urban composition"
        ),
    )
    return result


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        build_package(
            args.output,
            python_embed_zip=args.python_embed_zip,
            keep_staging=args.keep_staging,
            profile_id=args.profile,
            city_receipt=args.city_receipt,
        )
        return 0
    except (PortablePackageError, OSError, subprocess.SubprocessError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
