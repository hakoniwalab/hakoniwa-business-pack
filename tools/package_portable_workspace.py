#!/usr/bin/env python3
"""Build a relocatable Windows x64 City World workspace ZIP."""

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


ROOT = Path(__file__).resolve().parents[1]
RECIPE_ID = "plateau-citygml-mujoco-walls"
PACKAGE_ID = "hakoniwa-business-pack-city-world-windows-x64"
COMMON_IGNORES = {
    ".git",
    ".idea",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".venv",
    "__pycache__",
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


def _copy_foundation(source: Path, destination: Path) -> None:
    if not source.is_dir():
        raise PortablePackageError(
            f"Foundation workspace is missing; configure it first: {source}"
        )

    def ignore(directory: str, names: list[str]) -> set[str]:
        ignored = {name for name in names if name in COMMON_IGNORES}
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


def _rewrite_embedded_python_pth(python_root: Path) -> Path:
    candidates = sorted(python_root.glob("python*._pth"))
    if len(candidates) != 1:
        raise PortablePackageError(
            f"expected exactly one python*._pth in {python_root}"
        )
    path = candidates[0]
    output: list[str] = []
    has_site_packages = False
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
    for item in source.iterdir():
        # Workspace prepare regenerates this path for the extraction directory.
        if item.name == "hakoniwa_workspace_bootstrap.pth":
            continue
        target = destination / item.name
        if item.is_dir():
            shutil.copytree(item, target, dirs_exist_ok=True)
        else:
            shutil.copy2(item, target)
    _reject_absolute_pth_entries(destination)


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
) -> None:
    if destination.exists():
        shutil.rmtree(destination)
    destination.mkdir(parents=True)
    _extract_python_archive(embedded_zip, destination)
    if not (destination / "python.exe").is_file():
        raise PortablePackageError(
            f"embeddable Python archive did not contain python.exe: {embedded_zip}"
        )
    _rewrite_embedded_python_pth(destination)
    _copy_site_packages(source_python_root, destination)


def _prepare_recipe_python(foundation_python: Path) -> Path:
    code = (
        "from tools.recipe.plateau_citygml_mujoco_walls "
        "import install_python_requirements;"
        "print(install_python_requirements())"
    )
    completed = subprocess.run(
        [str(foundation_python), "-c", code],
        cwd=ROOT,
        check=False,
    )
    if completed.returncode:
        raise PortablePackageError(
            "failed to prepare the City World Recipe Python environment"
        )
    recipe_root = ROOT / "work" / "recipes" / RECIPE_ID / "python"
    if not _windows_python(recipe_root).is_file():
        raise PortablePackageError(
            f"Recipe Python was not created as expected: {recipe_root}"
        )
    return recipe_root


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
"%HAKO_PYTHON%" tools\workspace.py run -- "%HAKO_PYTHON%" -m tools.remote_operation.city_world.launcher start --terrain-spacing-m auto --open-browser
set "RC=%ERRORLEVEL%"
popd

if not "%RC%"=="0" (
  echo.
  echo [ERROR] City World failed to start. Check:
  echo         hakoniwa-business-pack\work\remote-operation\city-world-launcher\logs
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
"%HAKO_PYTHON%" tools\workspace.py run -- "%HAKO_PYTHON%" -m tools.remote_operation.city_world.launcher {command}
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
            "hakoniwa-business-pack\\work\\remote-operation\\ に保存されます。\n"
        ),
        encoding="utf-8",
    )


def _run_checked(command: list[str], cwd: Path, label: str) -> None:
    print(">", subprocess.list2cmdline(command))
    completed = subprocess.run(command, cwd=cwd, check=False)
    if completed.returncode:
        raise PortablePackageError(
            f"{label} failed with exit={completed.returncode}"
        )


def _validate_staged_package(package_root: Path) -> None:
    business_pack = package_root / "hakoniwa-business-pack"
    foundation_python = (
        business_pack / "work" / "foundation" / "install" / "python" / "python.exe"
    )
    recipe_python = (
        business_pack / "work" / "recipes" / RECIPE_ID / "python" / "python.exe"
    )
    required = (
        foundation_python,
        recipe_python,
        package_root / "hakoniwa-envsim" / "tools" / "hako.py",
        package_root / "hakoniwa-pdu-javascript" / "src" / "index.js",
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
    _run_checked(
        [str(foundation_python), "tools/workspace.py", "doctor"],
        business_pack,
        "portable Workspace doctor",
    )
    _run_checked(
        [str(recipe_python), "-m", "pip", "check"],
        business_pack,
        "portable Recipe Python pip check",
    )


def _write_manifest(
    package_root: Path,
    python_identity: dict[str, object],
    embedded_zip: Path,
    sources: dict[str, Path],
) -> None:
    manifest = {
        "schema_version": 1,
        "package_id": PACKAGE_ID,
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
        "entrypoints": {
            "start": "start-city-world.bat",
            "status": "status-city-world.bat",
            "stop": "stop-city-world.bat",
        },
        "runtime_policy": {
            "requires_system_python": False,
            "requires_git": False,
            "requires_wsl": False,
            "requires_docker": False,
            "includes_previous_city_world_jobs": False,
            "includes_previous_plateau_cache": False,
        },
    }
    (package_root / "portable-package.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


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
    output: Path,
    python_embed_zip: Path | None = None,
    keep_staging: bool = False,
) -> Path:
    if os.name != "nt":
        raise PortablePackageError(
            "Windows portable package must be built on Windows x64; "
            "Foundation binaries and Python wheels are platform-specific"
        )

    foundation_root = ROOT / "work" / "foundation"
    foundation_python_root = foundation_root / "install" / "python"
    foundation_python = _windows_python(foundation_python_root)
    if not foundation_python.is_file():
        raise PortablePackageError(
            f"Foundation Python is missing; configure it first: {foundation_python}"
        )
    python_identity = _python_identity(foundation_python)
    _require_windows_x64(python_identity)

    envsim = ROOT.parent / "hakoniwa-envsim"
    pdu_javascript = ROOT.parent / "hakoniwa-pdu-javascript"
    if not (envsim / "tools" / "hako.py").is_file():
        raise PortablePackageError(
            f"hakoniwa-envsim sibling checkout is missing: {envsim}"
        )
    if not (pdu_javascript / "src" / "index.js").is_file():
        raise PortablePackageError(
            f"hakoniwa-pdu-javascript sibling checkout is missing: {pdu_javascript}"
        )

    _run_checked(
        [str(foundation_python), "tools/workspace.py", "prepare"],
        ROOT,
        "source Workspace prepare",
    )
    _run_checked(
        [str(foundation_python), "tools/workspace.py", "doctor"],
        ROOT,
        "source Workspace doctor",
    )
    recipe_python_root = _prepare_recipe_python(foundation_python)

    output = output.expanduser().resolve()
    staging_base = ROOT / "work" / "portable-package"
    staging_base.mkdir(parents=True, exist_ok=True)
    temporary: tempfile.TemporaryDirectory[str] | None = None
    if keep_staging:
        package_root = staging_base / PACKAGE_ID
        if package_root.exists():
            shutil.rmtree(package_root)
    else:
        temporary = tempfile.TemporaryDirectory(
            prefix="hakoniwa-portable-",
            dir=staging_base,
        )
        package_root = Path(temporary.name) / PACKAGE_ID

    try:
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
        _copy_tree(
            envsim,
            package_root / "hakoniwa-envsim",
            SIBLING_IGNORES,
        )
        _copy_tree(
            pdu_javascript,
            package_root / "hakoniwa-pdu-javascript",
            SIBLING_IGNORES,
        )

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
        )
        _materialize_portable_python(
            embedded_zip,
            recipe_python_root,
            business_pack_destination
            / "work"
            / "recipes"
            / RECIPE_ID
            / "python",
        )

        _write_entrypoints(package_root)
        _write_manifest(
            package_root,
            python_identity,
            embedded_zip,
            {
                "hakoniwa-business-pack": ROOT,
                "hakoniwa-envsim": envsim,
                "hakoniwa-pdu-javascript": pdu_javascript,
            },
        )
        _validate_staged_package(package_root)
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
        description=(
            "Build a Windows x64 portable ZIP for the Business Pack City World Web UI"
        )
    )
    result.add_argument(
        "--output",
        type=Path,
        default=ROOT / "dist" / f"{PACKAGE_ID}.zip",
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
    return result


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        build_package(
            args.output,
            python_embed_zip=args.python_embed_zip,
            keep_staging=args.keep_staging,
        )
        return 0
    except (PortablePackageError, OSError, subprocess.SubprocessError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
