"""Server-owned execution and packaging for one inspected City World job."""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import zipfile
from pathlib import Path
from typing import Any, Callable

from .protocol import SCHEMA_VERSION, canonical_sha256, validate_result


class CityWorldGenerationError(RuntimeError):
    pass


Progress = Callable[[str, int, str], None]


def _root() -> Path:
    return Path(__file__).resolve().parents[3]


def _envsim_root() -> Path:
    import os

    configured = os.environ.get("HAKONIWA_ENVSIM_ROOT")
    return (Path(configured).expanduser() if configured else _root().parent / "hakoniwa-envsim").resolve()


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _manifest_text(request: dict[str, Any], job_root: Path, cache_dir: Path) -> str:
    center = request["selection"]["center"]
    extent = request["selection"]["half_extent_m"]
    build_dir = (job_root / "build").resolve()
    install_dir = (job_root / "install").resolve()
    return f"""version: 1
component: hakoniwa-envsim

pipeline:
  type: plateau-citygml-to-assets

source:
  api_base_url: https://api.plateauview.mlit.go.jp
  cache_dir: {cache_dir.resolve()}
  feature_type: bldg
  feature_types:
    bldg: true
    tran: true
    dem: true
    frn: true
    brid: true
  year: latest

selection:
  center:
    latitude: {center['latitude']}
    longitude: {center['longitude']}
  half_extent_m:
    north_south: {extent['north_south']}
    east_west: {extent['east_west']}

geometry:
  base_epsilon_m: 0.2
  waste_threshold: 0.1
  wall_thickness_m: 0.1
  roof_collision_thickness_m: 0.02

mjcf:
  model_name: plateau_city_world
  collision: all
  floor: false

glb:
  enabled: true
  lod_policy: highest_available
  texture_mode: embedded-if-available

city_world:
  enabled: true
  terrain_spacing_m: 2
  marking_vertical_offset_m: 0.055
  bridge_collision_thickness_m: 0.02
  bridge_max_surface_slope_deg: 60

output:
  build_dir: {build_dir}
  install_dir: {install_dir}
  name: city-world
"""


def _artifact_name(job_id: str) -> str:
    candidate = f"city-world-{job_id}.zip"
    if len(candidate) <= 128:
        return candidate
    suffix = hashlib.sha256(job_id.encode("utf-8")).hexdigest()[:12]
    return f"city-world-{job_id[:99]}-{suffix}.zip"


def package_world(
    *,
    job_root: Path,
    job_id: str,
    request_sha256: str,
    inspection_sha256: str,
) -> dict[str, Any]:
    world = job_root / "build" / "world"
    sources = {
        "visual/city-world.glb": world / "city-world.glb",
        "physics/city-world.xml": world / "city-world.xml",
        "validation/dataset-validation.json": world / "dataset-validation.json",
        "receipt/city-world-receipt.json": world / "city-world-receipt.json",
    }
    missing = [str(path) for path in sources.values() if not path.is_file()]
    if missing:
        raise CityWorldGenerationError(
            "City World build did not produce the required outputs: " + ", ".join(missing)
        )

    artifact_dir = job_root / "artifacts"
    artifact_dir.mkdir(parents=True, exist_ok=True)
    artifact = artifact_dir / _artifact_name(job_id)
    with zipfile.ZipFile(artifact, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for entry, source in sources.items():
            archive.write(source, entry)

    result = {
        "schema_version": SCHEMA_VERSION,
        "job_id": job_id,
        "request_sha256": request_sha256,
        "inspection_sha256": inspection_sha256,
        "artifact_name": artifact.name,
        "media_type": "application/zip",
        "size_bytes": artifact.stat().st_size,
        "sha256": _sha256(artifact),
        "entries": {
            "visual_world": "visual/city-world.glb",
            "physics_world": "physics/city-world.xml",
            "dataset_validation": "validation/dataset-validation.json",
            "world_receipt": "receipt/city-world-receipt.json",
        },
    }
    validate_result(result)
    (artifact_dir / "result-manifest.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return result


class CityWorldGenerator:
    """Generate one job using fixed Business Pack policy and Envsim's public CLI."""

    def __init__(self, runtime_root: Path) -> None:
        self.runtime_root = runtime_root.resolve()
        self._python: Path | None = None

    def _generation_python(self) -> Path:
        if self._python is None:
            # Reuse the Recipe-owned dependency environment. Installation is
            # performed only after the user explicitly requests Generate.
            from tools.recipe import plateau_citygml_mujoco_walls as recipe

            # Keep the venv entry path itself. Resolving its executable symlink
            # would launch the base interpreter and lose the venv site-packages.
            self._python = recipe.install_python_requirements()
        return self._python

    def __call__(
        self,
        command: dict[str, Any],
        inspection: dict[str, Any],
        progress: Progress,
    ) -> dict[str, Any]:
        if inspection["status"] != "available":
            raise CityWorldGenerationError("generation requires an available inspection")
        if inspection["request_sha256"] != command["request_sha256"]:
            raise CityWorldGenerationError("inspection belongs to another request")
        inspection_hash = canonical_sha256(inspection)
        if inspection_hash != command["inspection_sha256"]:
            raise CityWorldGenerationError("inspection identity does not match GENERATE")

        job_root = self.runtime_root / "jobs" / command["job_id"]
        artifact_manifest = job_root / "artifacts" / "result-manifest.json"
        if artifact_manifest.is_file():
            existing = json.loads(artifact_manifest.read_text(encoding="utf-8"))
            if (
                existing.get("request_sha256") == command["request_sha256"]
                and existing.get("inspection_sha256") == inspection_hash
            ):
                return validate_result(existing)
            raise CityWorldGenerationError("job_id is already owned by another request")

        job_root.mkdir(parents=True, exist_ok=True)
        manifest = job_root / "hakoniwa-envsim-build.yaml"
        manifest.write_text(_manifest_text(
            command["request"],
            job_root,
            self.runtime_root / "cache" / "plateau-citygml",
        ), encoding="utf-8")
        (job_root / "job.json").write_text(json.dumps({
            "schema_version": 1,
            "job_id": command["job_id"],
            "request": command["request"],
            "request_sha256": command["request_sha256"],
            "inspection": inspection,
            "inspection_sha256": inspection_hash,
        }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

        envsim = _envsim_root()
        hako = envsim / "tools" / "hako.py"
        if not hako.is_file():
            raise CityWorldGenerationError(f"hakoniwa-envsim CLI not found: {hako}")
        python = self._generation_python()
        log_path = job_root / "generation.log"
        progress(
            "DOWNLOADING", 10,
            "PLATEAUデータを準備しています（共有キャッシュの検証・再利用を含む）",
        )
        with log_path.open("w", encoding="utf-8") as log:
            completed = subprocess.run(
                [str(python), str(hako), "build", "--config", str(manifest)],
                cwd=envsim,
                stdout=log,
                stderr=subprocess.STDOUT,
                text=True,
                check=False,
            )
        if completed.returncode:
            tail = log_path.read_text(encoding="utf-8", errors="replace").splitlines()[-12:]
            raise CityWorldGenerationError(
                f"hakoniwa-envsim build failed with rc={completed.returncode}: " + " | ".join(tail)
            )

        progress("GENERATING", 80, "Visual WorldとPhysics Worldを生成しました")
        viewer = job_root / "viewer"
        viewer.mkdir(parents=True, exist_ok=True)
        shutil.copy2(job_root / "build" / "world" / "city-world.glb", viewer / "city-world.glb")
        collider_converter = envsim / "src" / "city_pipeline" / "mjcf_colliders2glb.py"
        with log_path.open("a", encoding="utf-8") as log:
            collider_completed = subprocess.run(
                [
                    str(python), str(collider_converter),
                    "--in", str(job_root / "build" / "world" / "city-world.xml"),
                    "--out", str(viewer / "city-world-colliders.glb"),
                    "--receipt", str(viewer / "city-world-colliders-receipt.json"),
                ],
                cwd=envsim,
                stdout=log,
                stderr=subprocess.STDOUT,
                text=True,
                check=False,
            )
        if collider_completed.returncode:
            tail = log_path.read_text(encoding="utf-8", errors="replace").splitlines()[-12:]
            raise CityWorldGenerationError(
                "collider debug GLB generation failed with "
                f"rc={collider_completed.returncode}: " + " | ".join(tail)
            )

        progress("VALIDATING", 90, "生成物、Collider表示、Receiptを検証しています")
        result = package_world(
            job_root=job_root,
            job_id=command["job_id"],
            request_sha256=command["request_sha256"],
            inspection_sha256=inspection_hash,
        )
        return result
