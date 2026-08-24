#!/usr/bin/env python3
"""Compile canonical MuJoCo XML into a version-bound MJB artifact."""

from __future__ import annotations

import argparse
import ctypes
import hashlib
import json
import os
import sys
from pathlib import Path


class MujocoCompileError(RuntimeError):
    pass


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def find_mujoco_library(mujoco_root: Path) -> Path:
    root = mujoco_root.expanduser().resolve()
    version_file = root / "MUJOCO_VERSION.txt"
    version = version_file.read_text(encoding="utf-8").strip() if version_file.is_file() else ""
    candidates: list[Path] = []
    if sys.platform == "darwin":
        if version:
            candidates.append(root / "vendor" / "mujoco" / "lib" / f"libmujoco.{version}.dylib")
        candidates.extend(sorted((root / "vendor" / "mujoco" / "lib").glob("libmujoco*.dylib")))
    elif sys.platform.startswith("linux"):
        if version:
            candidates.extend(
                (
                    root / "vendor" / "mujoco" / "lib" / f"libmujoco.so.{version}",
                    root / "vendor" / "mujoco" / "lib" / "libmujoco.so",
                )
            )
        candidates.extend(sorted((root / "vendor" / "mujoco" / "lib").glob("libmujoco.so*")))
    elif os.name == "nt":
        candidates.extend(
            (
                root / "vendor" / "mujoco" / "bin" / "mujoco.dll",
                root / "vendor" / "mujoco" / "lib" / "mujoco.dll",
            )
        )
    for candidate in candidates:
        if candidate.is_file():
            return candidate.resolve()
    raise MujocoCompileError(
        f"MuJoCo shared library was not found under {root}; "
        "install the runtime declared by the Drone package first"
    )


def compile_mujoco_xml(xml_path: Path, mjb_path: Path, library_path: Path) -> dict[str, object]:
    source = xml_path.expanduser().resolve()
    output = mjb_path.expanduser().resolve()
    library_file = library_path.expanduser().resolve()
    if not source.is_file():
        raise MujocoCompileError(f"MuJoCo XML not found: {source}")
    if source.suffix.lower() != ".xml":
        raise MujocoCompileError(f"MuJoCo source must use the .xml extension: {source}")
    if output.suffix.lower() != ".mjb":
        raise MujocoCompileError(f"MuJoCo binary output must use the .mjb extension: {output}")
    if not library_file.is_file():
        raise MujocoCompileError(f"MuJoCo shared library not found: {library_file}")

    try:
        library = ctypes.CDLL(str(library_file))
    except OSError as exc:
        raise MujocoCompileError(f"failed to load MuJoCo library {library_file}: {exc}") from exc
    library.mj_loadXML.argtypes = [ctypes.c_char_p, ctypes.c_void_p, ctypes.c_char_p, ctypes.c_int]
    library.mj_loadXML.restype = ctypes.c_void_p
    library.mj_loadModel.argtypes = [ctypes.c_char_p, ctypes.c_void_p]
    library.mj_loadModel.restype = ctypes.c_void_p
    library.mj_saveModel.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_void_p, ctypes.c_int]
    library.mj_saveModel.restype = None
    library.mj_deleteModel.argtypes = [ctypes.c_void_p]
    library.mj_deleteModel.restype = None
    library.mj_versionString.argtypes = []
    library.mj_versionString.restype = ctypes.c_char_p

    error = ctypes.create_string_buffer(4096)
    model = library.mj_loadXML(os.fsencode(source), None, error, len(error))
    if not model:
        detail = error.value.decode("utf-8", errors="replace")
        raise MujocoCompileError(f"MuJoCo XML compile failed for {source}: {detail}")

    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_name(f".{output.name}.tmp-{os.getpid()}")
    try:
        library.mj_saveModel(model, os.fsencode(temporary), None, 0)
    finally:
        library.mj_deleteModel(model)
    if not temporary.is_file() or temporary.stat().st_size == 0:
        temporary.unlink(missing_ok=True)
        raise MujocoCompileError(f"MuJoCo did not write a binary model: {temporary}")

    verified = library.mj_loadModel(os.fsencode(temporary), None)
    if not verified:
        temporary.unlink(missing_ok=True)
        raise MujocoCompileError(
            f"MuJoCo could not reload the generated binary model: {temporary}"
        )
    library.mj_deleteModel(verified)
    os.replace(temporary, output)

    version_bytes = library.mj_versionString()
    version = version_bytes.decode("ascii") if version_bytes else "unknown"
    return {
        "format": "mjb",
        "source_xml": str(source),
        "source_xml_sha256": _sha256(source),
        "output_mjb": str(output),
        "output_mjb_sha256": _sha256(output),
        "output_mjb_size_bytes": output.stat().st_size,
        "mujoco_version": version,
        "mujoco_library": str(library_file),
        "mujoco_library_sha256": _sha256(library_file),
        "reload_validation": "passed",
        "compatibility": "MJB must be loaded by the same MuJoCo version",
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--xml", type=Path, required=True)
    parser.add_argument("--mjb", type=Path, required=True)
    parser.add_argument("--mujoco-library", type=Path, required=True)
    parser.add_argument("--receipt", type=Path)
    args = parser.parse_args(argv)
    try:
        receipt = compile_mujoco_xml(args.xml, args.mjb, args.mujoco_library)
    except MujocoCompileError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    if args.receipt:
        args.receipt.parent.mkdir(parents=True, exist_ok=True)
        args.receipt.write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(receipt, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
