#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import shutil
import sys
import tempfile
import urllib.error
import urllib.request
import zipfile
from dataclasses import asdict, dataclass
from pathlib import Path


VERSION = "v1.0.0"
SUPPORTED_VERSIONS = {"v1.0.0", "v1.1.0"}
EXPECTED_BINARIES = {
    "bridge_gen",
    "conductor_config_gen",
    "endpoint_container_gen",
    "endpoint_gen",
    "main_asset_eu",
    "main_client",
    "main_client_shell",
    "main_exmonitor",
    "main_server",
    "remote_api_gen",
    "rpc_gen",
}

# v1.1.0 was built from the revisions recorded in its package contract.  The
# revisions below were subsequently audited against those exact sources and
# contain build/doctor/measurement tooling changes only; no C/C++ headers or
# runtime implementation changed.  Keep this an explicit allow-list so an
# arbitrary newer checkout can never bypass the binary ABI provenance guard.
AUDITED_FOUNDATION_REVISION_COMPATIBILITY = {
    (
        "hakoniwa-core-pro",
        "b8818ec47619f6026739f7d71e2d22829dea4752",
    ): frozenset({"945ad77a34b1b86282bf74a04d79451d0eb2ebb8"}),
    (
        "hakoniwa-pdu-endpoint",
        "93a926c520a76f401f52d7ba4e816e5ad54d7c36",
    ): frozenset({"9015a17415fd4a2042de2528b835a265af85b165"}),
}


class ConductorRecipeError(RuntimeError):
    pass


@dataclass(frozen=True)
class ReleaseTarget:
    version: str
    os_name: str
    architecture: str
    suffix: str
    platform_contract: str

    @property
    def package_name(self) -> str:
        return f"hakoniwa-conductor-{self.version}-{self.suffix}"

    @property
    def archive_name(self) -> str:
        return f"{self.package_name}.zip"

    @property
    def checksum_name(self) -> str:
        return f"{self.archive_name}.sha256"


def business_pack_root() -> Path:
    return Path(__file__).resolve().parents[2]


def recipe_id(version: str) -> str:
    if version not in SUPPORTED_VERSIONS:
        raise ConductorRecipeError(f"unsupported Hakoniwa Conductor release: {version}")
    return "hakoniwa-conductor-" + version.replace(".", "-") + "-binary-package"


def recipe_root(version: str = VERSION) -> Path:
    return business_pack_root() / "work" / "recipes" / recipe_id(version)


def release_base(version: str) -> str:
    recipe_id(version)
    return (
        "https://github.com/hakoniwalab/hakoniwa-conductor/releases/download/"
        f"{version}"
    )


def detect_target(
    system: str | None = None,
    machine: str | None = None,
    version: str = VERSION,
) -> ReleaseTarget:
    recipe_id(version)
    system = system or platform.system()
    machine = (machine or platform.machine()).lower()
    normalized = {
        "amd64": "x86_64",
        "x64": "x86_64",
        "aarch64": "arm64",
    }.get(machine, machine)
    if system == "Darwin" and normalized == "arm64":
        return ReleaseTarget(version, "macOS", "arm64", "macos-arm64", "macos/arm64")
    if system == "Linux" and normalized == "x86_64":
        return ReleaseTarget(
            version,
            "Ubuntu 24.04",
            "x86_64",
            "linux-x86_64",
            "linux/x86_64" if version == "v1.1.0" else "linux/amd64",
        )
    raise ConductorRecipeError(
        f"{version} has no verified package for "
        f"system={system!r}, architecture={normalized!r}"
    )


def paths(target: ReleaseTarget) -> dict[str, Path]:
    root = recipe_root(target.version)
    assets = root / "assets"
    runtime = root / "runtime"
    return {
        "root": root,
        "assets": assets,
        "runtime": runtime,
        "archive": assets / target.archive_name,
        "checksum": assets / target.checksum_name,
        "package": runtime / target.package_name,
        "receipt": root / "validation" / "binary-package.json",
    }


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def expected_checksum(path: Path, archive_name: str) -> str:
    fields = path.read_text(encoding="utf-8").strip().split()
    if len(fields) != 2 or fields[1].lstrip("*") != archive_name:
        raise ConductorRecipeError(
            f"invalid checksum contract in {path}; expected {archive_name}"
        )
    value = fields[0].lower()
    if len(value) != 64 or any(char not in "0123456789abcdef" for char in value):
        raise ConductorRecipeError(f"invalid SHA-256 value in {path}")
    return value


def verify_checksum(archive: Path, checksum: Path) -> str:
    expected = expected_checksum(checksum, archive.name)
    actual = sha256(archive)
    if actual != expected:
        raise ConductorRecipeError(
            f"SHA-256 mismatch for {archive}: expected={expected}, actual={actual}"
        )
    return actual


def download(url: str, destination: Path) -> None:
    if destination.exists():
        return
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(f".{destination.name}.download")
    if temporary.exists():
        temporary.unlink()
    request = urllib.request.Request(
        url,
        headers={"User-Agent": "hakoniwa-business-pack-conductor-recipe/1.0"},
    )
    try:
        with urllib.request.urlopen(request, timeout=60) as response:  # noqa: S310
            with temporary.open("wb") as output:
                shutil.copyfileobj(response, output)
        os.replace(temporary, destination)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise


def _safe_members(archive: zipfile.ZipFile, destination: Path) -> list[zipfile.ZipInfo]:
    root = destination.resolve()
    members = archive.infolist()
    for member in members:
        target = (root / member.filename).resolve()
        if not target.is_relative_to(root):
            raise ConductorRecipeError(
                f"archive contains an unsafe path: {member.filename}"
            )
    return members


def validate_package(package: Path, target: ReleaseTarget) -> dict[str, object]:
    if not package.is_dir():
        raise ConductorRecipeError(f"package is not installed: {package}")
    version_file = package / "VERSION"
    build_contract = package / "metadata" / "build-contract.txt"
    bin_dir = package / "bin"
    if not version_file.is_file() or version_file.read_text().strip() != target.version:
        raise ConductorRecipeError(f"VERSION is missing or invalid under {package}")
    if not build_contract.is_file():
        raise ConductorRecipeError(f"build contract is missing: {build_contract}")
    contract_lines = set(build_contract.read_text(encoding="utf-8").splitlines())
    if f"platform={target.platform_contract}" not in contract_lines:
        raise ConductorRecipeError(
            f"package platform does not match {target.platform_contract}"
        )
    actual_binaries = {
        item.name for item in bin_dir.iterdir() if item.is_file()
    } if bin_dir.is_dir() else set()
    if actual_binaries != EXPECTED_BINARIES:
        raise ConductorRecipeError(
            "binary inventory mismatch: "
            f"missing={sorted(EXPECTED_BINARIES - actual_binaries)}, "
            f"unexpected={sorted(actual_binaries - EXPECTED_BINARIES)}"
        )
    for binary in bin_dir.iterdir():
        binary.chmod(binary.stat().st_mode | 0o111)
    return {
        "version": target.version,
        "platform": target.platform_contract,
        "package": str(package),
        "binaries": sorted(actual_binaries),
        "build_contract": str(build_contract),
    }


def read_build_contract(path: Path) -> dict[str, str]:
    contract: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line or line.startswith("#"):
            continue
        key, separator, value = line.partition("=")
        if not separator or not key or not value:
            raise ConductorRecipeError(
                f"invalid Conductor build contract line: {line!r}"
            )
        contract[key] = value
    return contract


def _version_at_least(installed: str, required: str) -> bool:
    try:
        installed_parts = tuple(int(part) for part in installed.split("."))
        required_parts = tuple(int(part) for part in required.split("."))
    except ValueError:
        return False
    width = max(len(installed_parts), len(required_parts))
    return installed_parts + (0,) * (width - len(installed_parts)) >= (
        required_parts + (0,) * (width - len(required_parts))
    )


def validate_foundation_contract(
    build_contract: Path, foundation_root: Path
) -> dict[str, str]:
    """Fail closed when installed Foundation ABI provenance differs from a package."""

    try:
        from tools import foundation
    except ModuleNotFoundError:
        # Direct script entry points place tools/recipe, rather than the
        # repository root, at sys.path[0]. Keep their documented invocation
        # form working when this late import is first reached by doctor.
        root = str(business_pack_root())
        if root not in sys.path:
            sys.path.insert(0, root)
        from tools import foundation

    contract = read_build_contract(build_contract)
    expected = {
        "hakoniwa-core-pro": contract.get("hakoniwa_core_pro_ref"),
        "hakoniwa-pdu-endpoint": contract.get("hakoniwa_pdu_endpoint_ref"),
        "hakoniwa-pdu-rpc": contract.get("hakoniwa_pdu_rpc_ref"),
        "hakoniwa-pdu-bridge-core": contract.get("hakoniwa_pdu_bridge_core_ref"),
    }
    missing = [component for component, revision in expected.items() if not revision]
    if missing or not contract.get("hakoniwa_pdu_version"):
        raise ConductorRecipeError(
            "Conductor build contract is incomplete: "
            + ", ".join(missing or ["hakoniwa_pdu_version"])
        )

    installed: dict[str, str] = {}
    receipt_root = foundation_root / "share" / "hakoniwa" / "receipts"
    for component_id, required_revision in expected.items():
        receipt_path = receipt_root / f"{component_id}.yaml"
        if not receipt_path.is_file():
            raise ConductorRecipeError(f"Foundation Receipt is missing: {receipt_path}")
        receipt = foundation.load_receipt(receipt_path)
        actual = receipt.get("component", {}).get("source_revision")
        compatible_revisions = AUDITED_FOUNDATION_REVISION_COMPATIBILITY.get(
            (component_id, str(required_revision)), frozenset()
        )
        if actual != required_revision and actual not in compatible_revisions:
            raise ConductorRecipeError(
                f"Foundation revision mismatch for {component_id}: "
                f"required={required_revision}, installed={actual}; "
                "rebuild the Recipe Foundation for this Conductor package"
            )
        installed[component_id] = str(actual)

        if component_id == "hakoniwa-pdu-endpoint":
            capabilities = receipt.get("capabilities", {})
            missing_capabilities = [
                name
                for name in ("hakoniwa_core", "core_callback", "core_polling", "tcp")
                if capabilities.get(name) is not True
            ]
            if missing_capabilities:
                raise ConductorRecipeError(
                    "Foundation Endpoint is missing required Conductor capabilities: "
                    + ", ".join(missing_capabilities)
                )

    pdu_receipt_path = receipt_root / "hakoniwa-pdu-python.yaml"
    if not pdu_receipt_path.is_file():
        raise ConductorRecipeError(
            f"Foundation Receipt is missing: {pdu_receipt_path}"
        )
    pdu_receipt = foundation.load_receipt(pdu_receipt_path)
    installed_version = str(pdu_receipt.get("component", {}).get("version"))
    required_version = contract["hakoniwa_pdu_version"]
    if not _version_at_least(installed_version, required_version):
        raise ConductorRecipeError(
            "Foundation hakoniwa-pdu version is too old: "
            f"required>={required_version}, installed={installed_version}"
        )
    installed["hakoniwa-pdu-python"] = installed_version
    return installed


def extract_archive(
    archive_path: Path,
    runtime_dir: Path,
    target: ReleaseTarget,
) -> Path:
    package = runtime_dir / target.package_name
    if package.exists():
        validate_package(package, target)
        return package
    runtime_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".conductor-", dir=runtime_dir) as tmp:
        staging = Path(tmp)
        with zipfile.ZipFile(archive_path) as archive:
            members = _safe_members(archive, staging)
            archive.extractall(staging, members=members)
        staged_package = staging / target.package_name
        validate_package(staged_package, target)
        os.replace(staged_package, package)
    return package


def configure(accept_license: bool, version: str = VERSION) -> dict[str, object]:
    if not accept_license:
        raise ConductorRecipeError(
            "license acceptance is required; read "
            "https://github.com/hakoniwalab/hakoniwa-conductor/blob/"
            f"{version}/LICENSE-NC-ja.md and rerun with --accept-license"
        )
    target = detect_target(version=version)
    resolved = paths(target)
    base = release_base(version)
    download(f"{base}/{target.checksum_name}", resolved["checksum"])
    download(f"{base}/{target.archive_name}", resolved["archive"])
    digest = verify_checksum(resolved["archive"], resolved["checksum"])
    package = extract_archive(resolved["archive"], resolved["runtime"], target)
    result = validate_package(package, target)
    result["sha256"] = digest
    result["license_acknowledged"] = True
    receipt = resolved["receipt"]
    receipt.parent.mkdir(parents=True, exist_ok=True)
    temporary = receipt.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, receipt)
    return result


def doctor(version: str = VERSION) -> dict[str, object]:
    target = detect_target(version=version)
    resolved = paths(target)
    if not resolved["archive"].is_file() or not resolved["checksum"].is_file():
        raise ConductorRecipeError(
            "release assets are missing; run configure after reviewing the license"
        )
    digest = verify_checksum(resolved["archive"], resolved["checksum"])
    result = validate_package(resolved["package"], target)
    result["sha256"] = digest
    return result


def status(version: str = VERSION) -> dict[str, object]:
    target = detect_target(version=version)
    resolved = paths(target)
    result: dict[str, object] = {
        "target": asdict(target),
        "archive": str(resolved["archive"]),
        "package": str(resolved["package"]),
        "configured": resolved["package"].is_dir(),
    }
    if result["configured"]:
        try:
            result["validation"] = doctor(version)
            result["status"] = "READY"
        except ConductorRecipeError as exc:
            result["status"] = "INCOMPATIBLE"
            result["reason"] = str(exc)
    else:
        result["status"] = "MISSING"
    return result


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(description=__doc__)
    commands = root.add_subparsers(dest="command", required=True)
    configure_parser = commands.add_parser(
        "configure", help="download, verify, and extract a selected release package"
    )
    configure_parser.add_argument(
        "--accept-license",
        action="store_true",
        help="confirm that the operator reviewed and accepted the applicable license",
    )
    doctor_parser = commands.add_parser(
        "doctor", help="verify the downloaded package without network access"
    )
    status_parser = commands.add_parser("status", help="show package selection and readiness")
    for command_parser in (configure_parser, doctor_parser, status_parser):
        command_parser.add_argument(
            "--version",
            choices=sorted(SUPPORTED_VERSIONS),
            default=VERSION,
            help=f"release version (default: {VERSION})",
        )
    return root


def main() -> int:
    args = parser().parse_args()
    try:
        if args.command == "configure":
            result = configure(args.accept_license, args.version)
        elif args.command == "doctor":
            result = doctor(args.version)
        else:
            result = status(args.version)
        print(json.dumps(result, indent=2))
        return 0
    except (ConductorRecipeError, OSError, urllib.error.URLError, zipfile.BadZipFile) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
