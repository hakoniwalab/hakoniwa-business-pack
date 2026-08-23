"""Server-owned execution and packaging for one inspected City World job."""

from __future__ import annotations

import hashlib
import json
import os
import queue
import shutil
import subprocess
import threading
import zipfile
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Callable

from .protocol import SCHEMA_VERSION, canonical_sha256, validate_result


class CityWorldGenerationError(RuntimeError):
    pass


Progress = Callable[..., None]


@contextmanager
def _replace_job_directory(runtime_root: Path, job_id: str):
    """Replace one job, restoring its previous good result after a failure."""
    jobs_root = (runtime_root / "jobs").resolve()
    job_root = (jobs_root / job_id).resolve()
    backup_root = (runtime_root / ".job-backups").resolve()
    backup = (backup_root / job_id).resolve()
    if job_root.parent != jobs_root or backup.parent != backup_root:
        raise CityWorldGenerationError(f"unsafe job_id path: {job_id}")

    jobs_root.mkdir(parents=True, exist_ok=True)
    backup_root.mkdir(parents=True, exist_ok=True)
    # A surviving backup indicates that the preceding Worker was interrupted.
    # Restore the last good job before beginning another replacement.
    if backup.exists():
        if job_root.exists():
            shutil.rmtree(job_root)
        backup.rename(job_root)

    had_previous = job_root.exists()
    if had_previous:
        job_root.rename(backup)
    job_root.mkdir(parents=True)
    try:
        yield job_root
    except BaseException:
        shutil.rmtree(job_root, ignore_errors=True)
        if had_previous and backup.exists():
            backup.rename(job_root)
        raise
    else:
        if backup.exists():
            shutil.rmtree(backup)


_BUILD_PHASES = {
    "geometry_extract": (35, "建物形状を抽出しています"),
    "building_collision": (42, "建物Colliderを生成しています"),
    "terrain": (43, "地形生成を開始しています"),
    "building_mjcf": (52, "建物Physicsを生成しています"),
    "building_visual": (56, "建物Visualを生成しています"),
    "building_glb": (72, "建物GLBを書き出しています"),
    "roads": (76, "道路と地形のVisualを生成しています"),
    "road_markings": (79, "LOD3路面標示を生成しています"),
    "bridges_visual": (81, "橋梁Visualを生成しています"),
    "bridges_physics": (83, "橋梁Physicsを生成しています"),
    "compose": (86, "City Worldを統合しています"),
    "dataset_validation": (88, "Dataset Capabilityを検証しています"),
}


def _forward_build_progress(
    line: str,
    progress: Progress,
    state: dict[str, Any] | None = None,
) -> None:
    def emit(kind: str, percent: int, message: str, **detail: Any) -> None:
        if state is not None:
            percent = max(percent, int(state.get("percent", 0)))
            state.update({
                "kind": kind, "percent": percent, "message": message,
                "phase": detail.get("phase"),
            })
        progress(kind, percent, message, **detail)

    marker = "[HAKO_PROGRESS] "
    if not line.startswith(marker):
        return
    try:
        event = json.loads(line[len(marker):])
    except json.JSONDecodeError:
        return
    phase = event.get("phase")
    if phase == "source_download":
        current, total = int(event.get("current", 0)), int(event.get("total", 0))
        feature = str(event.get("feature", "source"))
        mode = event.get("mode")
        action = {
            "cache-reused": "共有キャッシュを再利用しました",
            "offline-reused": "ローカルデータを再利用しました",
            "downloaded": "ダウンロードしました",
            "cache-populated": "ダウンロードして共有キャッシュへ保存しました",
        }.get(mode, "取得またはキャッシュ再利用を確認しています")
        emit(
            "DOWNLOADING", 15,
            f"PLATEAU {feature}ソース: {action}（{current}/{total}）",
            phase="source_download", current=current, total=total,
        )
        return
    if phase == "terrain_extract":
        current, total = int(event.get("current", 0)), int(event.get("total", 0))
        percent = 43 if total == 0 else 43 + int(3 * current / total)
        emit(
            "GENERATING", percent,
            f"DEMソースを並列抽出しています（{current}/{total}）",
            phase=phase, current=current, total=total,
        )
        return
    if phase == "terrain_gap_fill":
        current, total = int(event.get("current", 0)), int(event.get("total", 0))
        percent = 47 if total == 0 else 47 + int(current >= total)
        emit(
            "GENERATING", percent,
            f"DEMの小さな欠損を補間しています（{current}/{total}）",
            phase=phase, current=current, total=total,
        )
        return
    if phase == "texture_download":
        current, total = int(event.get("current", 0)), int(event.get("total", 0))
        percent = 70 if total == 0 else 56 + int(14 * current / total)
        message = (
            "選択範囲に建物テクスチャはありません"
            if total == 0 else f"建物テクスチャを取得・再利用しています（{current}/{total}）"
        )
        emit(
            "GENERATING", percent, message,
            phase="texture_download", current=current, total=total,
        )
        return
    if phase in _BUILD_PHASES:
        percent, message = _BUILD_PHASES[phase]
        emit("GENERATING", percent, message, phase=phase)


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
    physics_level = request.get("options", {}).get("building_physics_level", 3)
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
  building_physics_level: {physics_level}

glb:
  enabled: true
  lod_policy: highest_available
  texture_mode: embedded-if-available

city_world:
  enabled: true
  parallel_workers: 4
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
    building_physics_level: int,
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

    world_receipt = json.loads(sources["receipt/city-world-receipt.json"].read_text(encoding="utf-8"))
    collider_counts = world_receipt.get("components", {}).get("mjcf_geom_counts", {})
    physics_receipt_path = (
        job_root / "build" / "components" / "buildings" /
        "building-physics-application.json"
    )
    physics_receipt = json.loads(physics_receipt_path.read_text(encoding="utf-8"))
    by_class = physics_receipt.get("collider_geom_counts", {}).get("by_class", {})
    result = {
        "schema_version": SCHEMA_VERSION,
        "job_id": job_id,
        "request_sha256": request_sha256,
        "inspection_sha256": inspection_sha256,
        "building_physics_level": building_physics_level,
        "colliders": {
            "total": int(collider_counts.get("total", 0)),
            "by_component": {
                str(key): int(value)
                for key, value in collider_counts.items() if key != "total"
            },
            "by_physics_class": {
                class_id: int(by_class.get(class_id, 0))
                for class_id in ("P0", "P1", "P2", "P3")
            },
        },
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

        with _replace_job_directory(self.runtime_root, command["job_id"]) as job_root:
            return self._generate(command, inspection, inspection_hash, progress, job_root)

    def _generate(
        self,
        command: dict[str, Any],
        inspection: dict[str, Any],
        inspection_hash: str,
        progress: Progress,
        job_root: Path,
    ) -> dict[str, Any]:
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
            phase="source_download",
        )
        progress_state: dict[str, Any] = {
            "kind": "DOWNLOADING", "percent": 10,
            "message": "PLATEAUデータを準備しています（共有キャッシュの検証・再利用を含む）",
            "phase": "source_download",
        }
        with log_path.open("w", encoding="utf-8") as log:
            environment = dict(os.environ)
            environment["PYTHONUNBUFFERED"] = "1"
            process = subprocess.Popen(
                [str(python), "-u", str(hako), "build", "--config", str(manifest)],
                cwd=envsim,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
                env=environment,
            )
            assert process.stdout is not None
            output_queue: queue.Queue[str | None] = queue.Queue()

            def read_output() -> None:
                assert process.stdout is not None
                for output_line in process.stdout:
                    output_queue.put(output_line)
                output_queue.put(None)

            reader = threading.Thread(
                target=read_output, name="city-world-build-output", daemon=True,
            )
            reader.start()
            while True:
                try:
                    line = output_queue.get(timeout=15.0)
                except queue.Empty:
                    progress(
                        str(progress_state["kind"]), int(progress_state["percent"]),
                        f"{progress_state['message']}（処理継続中）",
                        phase=str(progress_state["phase"]), heartbeat=True,
                    )
                    continue
                if line is None:
                    break
                log.write(line)
                log.flush()
                _forward_build_progress(
                    line.rstrip("\n"), progress, progress_state,
                )
            reader.join()
            returncode = process.wait()
        if returncode:
            tail = log_path.read_text(encoding="utf-8", errors="replace").splitlines()[-12:]
            raise CityWorldGenerationError(
                f"hakoniwa-envsim build failed with rc={returncode}: " + " | ".join(tail)
            )

        progress(
            "GENERATING", 89, "Visual WorldとPhysics Worldを生成しました",
            phase="world_generated",
        )
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

        progress(
            "VALIDATING", 94, "生成物、Collider表示、Receiptを検証しています",
            phase="packaging",
        )
        result = package_world(
            job_root=job_root,
            job_id=command["job_id"],
            request_sha256=command["request_sha256"],
            inspection_sha256=inspection_hash,
            building_physics_level=command["request"].get(
                "options", {}
            ).get("building_physics_level", 3),
        )
        return result
