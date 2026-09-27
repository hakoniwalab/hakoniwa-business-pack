from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from tools.remote_operation.city_world import cache_cleanup


def result_manifest(job_id: str) -> dict:
    return {
        "schema_version": 1,
        "job_id": job_id,
        "request_sha256": "a" * 64,
        "inspection_sha256": "b" * 64,
        "artifact_name": f"city-world-{job_id}.zip",
        "media_type": "application/zip",
        "size_bytes": 1,
        "sha256": "c" * 64,
        "entries": {
            "visual_world": "visual/city-world.glb",
            "physics_world": "physics/city-world.xml",
            "dataset_validation": "validation/dataset-validation.json",
            "world_receipt": "receipt/city-world-receipt.json",
        },
    }


class CityWorldCacheCleanupTest(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.runtime = Path(temporary.name) / "runtime"
        self.objects = self.runtime / "cache" / "plateau-citygml" / "objects"
        self.cache_dir = self.objects.parent

    def cache_object(self, key: str, name: str, size: int) -> Path:
        path = self.objects / key / name
        path.parent.mkdir(parents=True)
        path.write_bytes(b"g" * size)
        (path.parent / f"{name}.cache.json").write_text("{}", encoding="utf-8")
        return path

    def job(self, job_id: str, sources: list[Path], *, finished: bool = True,
            hardlink: bool = True) -> Path:
        job_root = self.runtime / "jobs" / job_id
        source = job_root / "build" / "source" / "22203-2023"
        source.mkdir(parents=True)
        for cached in sources:
            if hardlink:
                os.link(cached, source / cached.name)
            else:
                (source / cached.name).write_bytes(cached.read_bytes())
        for kept in ("build/world/city-world.xml", "build/components/roads/roads.xml",
                     "viewer/city-world-colliders.glb", "build/download-manifest.json"):
            (job_root / kept).parent.mkdir(parents=True, exist_ok=True)
            (job_root / kept).write_text("kept", encoding="utf-8")
        if finished:
            (job_root / "artifacts").mkdir()
            (job_root / "artifacts" / "result-manifest.json").write_text(
                json.dumps(result_manifest(job_id)), encoding="utf-8",
            )
        return job_root

    def clean(self, **options) -> dict:
        return cache_cleanup.clean(self.runtime, cache_dir=self.cache_dir, **options)

    def test_hardlinked_sources_are_reclaimed_only_together_with_the_cache(self) -> None:
        gml = self.cache_object("0123456789abcdef0123", "52385618_tran_6697_op.gml", 4096)
        self.cache_object("abcdef0123456789abcd", "unused_bldg_6697_op.gml", 100)
        self.job("shizuoka", [gml])

        report = cache_cleanup.plan(self.runtime, self.cache_dir)

        self.assertEqual(report["busy_reasons"], [])
        self.assertEqual(report["reclaimable_bytes"]["job_sources"], 0)
        self.assertEqual(report["reclaimable_bytes"]["source_cache"], 100 + 2 * len("{}"))
        self.assertEqual(report["reclaimable_bytes"]["both"], 4096 + 100 + 2 * len("{}"))

    def test_copied_sources_are_reclaimable_on_their_own(self) -> None:
        gml = self.cache_object("0123456789abcdef0123", "dem.gml", 2048)
        self.job("hokkaido", [gml], hardlink=False)

        report = cache_cleanup.plan(self.runtime, self.cache_dir)

        self.assertEqual(report["reclaimable_bytes"]["job_sources"], 2048)

    def test_dry_run_is_the_default_and_deletes_nothing(self) -> None:
        gml = self.cache_object("0123456789abcdef0123", "dem.gml", 10)
        job_root = self.job("shizuoka", [gml])

        report = self.clean(job_sources=True, source_cache=True)

        self.assertFalse(report["applied"])
        self.assertEqual(len(report["removal"]), 2)
        self.assertTrue((job_root / "build" / "source").is_dir())
        self.assertTrue(gml.is_file())

    def test_apply_keeps_the_generated_world(self) -> None:
        gml = self.cache_object("0123456789abcdef0123", "dem.gml", 10)
        job_root = self.job("shizuoka", [gml])

        report = self.clean(job_sources=True, source_cache=True, apply=True)

        self.assertTrue(report["applied"])
        self.assertFalse((job_root / "build" / "source").exists())
        self.assertEqual(list(self.objects.iterdir()), [])
        self.assertTrue(self.cache_dir.is_dir())
        for kept in ("build/world/city-world.xml", "build/components/roads/roads.xml",
                     "viewer/city-world-colliders.glb", "build/download-manifest.json",
                     "artifacts/result-manifest.json"):
            self.assertTrue((job_root / kept).is_file(), kept)

    def test_generation_in_progress_blocks_the_cache_and_its_own_sources(self) -> None:
        gml = self.cache_object("0123456789abcdef0123", "dem.gml", 10)
        finished = self.job("finished", [gml])
        running = self.job("running", [gml], finished=False)

        report = self.clean(job_sources=True, source_cache=True, apply=True)

        self.assertFalse((finished / "build" / "source").exists())
        self.assertTrue((running / "build" / "source").is_dir())
        self.assertTrue(gml.is_file())
        self.assertTrue(any("running" in reason for reason in report["busy_reasons"]))
        self.assertTrue(any("shared PLATEAU cache" in reason for reason in report["skipped"]))

    def test_pending_backup_or_partial_download_blocks_the_cache(self) -> None:
        gml = self.cache_object("0123456789abcdef0123", "dem.gml", 10)
        self.job("shizuoka", [gml])
        (self.runtime / ".job-backups" / "shizuoka").mkdir(parents=True)

        report = self.clean(source_cache=True, apply=True)
        self.assertEqual(report["removal"], [])
        self.assertTrue(gml.is_file())

        (self.runtime / ".job-backups" / "shizuoka").rmdir()
        (gml.parent / ".dem.gml.x1.part").write_bytes(b"partial")
        report = self.clean(source_cache=True, apply=True)
        self.assertEqual(report["removal"], [])
        self.assertTrue(any("download in progress" in r for r in report["busy_reasons"]))

    def test_symlinked_source_directory_is_refused(self) -> None:
        outside = self.runtime.parent / "outside"
        outside.mkdir()
        (outside / "keep.gml").write_text("keep", encoding="utf-8")
        job_root = self.runtime / "jobs" / "linked"
        (job_root / "build").mkdir(parents=True)
        (job_root / "build" / "source").symlink_to(outside, target_is_directory=True)
        (job_root / "artifacts").mkdir()
        (job_root / "artifacts" / "result-manifest.json").write_text(
            json.dumps(result_manifest("linked")), encoding="utf-8",
        )

        with self.assertRaises(cache_cleanup.CityWorldCacheError):
            self.clean(job_sources=True, apply=True)
        self.assertTrue((outside / "keep.gml").is_file())

    def test_a_selection_is_required(self) -> None:
        with self.assertRaises(cache_cleanup.CityWorldCacheError):
            self.clean(job_sources=False, source_cache=False)

    def test_format_bytes(self) -> None:
        self.assertEqual(cache_cleanup.format_bytes(605), "605 B")
        self.assertEqual(cache_cleanup.format_bytes(5 * 1024 ** 3), "5.0 GiB")


if __name__ == "__main__":
    unittest.main()
