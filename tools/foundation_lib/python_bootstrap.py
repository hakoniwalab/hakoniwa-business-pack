from __future__ import annotations

import os
import shutil
from pathlib import Path


def foundation_site_package_dirs(
    python_root: Path,
    *,
    windows: bool | None = None,
) -> list[Path]:
    use_windows_layout = os.name == "nt" if windows is None else windows
    if use_windows_layout:
        candidate = python_root / "Lib" / "site-packages"
        return [candidate] if candidate.is_dir() else []
    return sorted(
        path
        for path in (python_root / "lib").glob("python*/site-packages")
        if path.is_dir()
    )


def install_workspace_python_bootstrap(
    business_pack_root: Path,
    python_root: Path,
    *,
    windows: bool | None = None,
) -> list[Path]:
    bootstrap_source = business_pack_root / "foundation" / "python"
    bootstrap_module = bootstrap_source / "hakoniwa_workspace_bootstrap.py"
    if not bootstrap_module.is_file():
        raise FileNotFoundError(
            f"Workspace Python bootstrap is missing: {bootstrap_module}"
        )
    installed: list[Path] = []
    content = "import hakoniwa_workspace_bootstrap\n"
    for site_packages in foundation_site_package_dirs(
        python_root, windows=windows
    ):
        shutil.copy2(bootstrap_module, site_packages / bootstrap_module.name)
        pth_path = site_packages / "hakoniwa_workspace_bootstrap.pth"
        pth_path.write_text(content, encoding="utf-8")
        installed.append(pth_path)
    return installed
