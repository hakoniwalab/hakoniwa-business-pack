from __future__ import annotations

import hashlib
import tempfile
import unittest
import zipfile
from pathlib import Path

from tools.recipe import hakoniwa_conductor as conductor


class HakoniwaConductorRecipeTest(unittest.TestCase):
    def test_detects_published_targets(self):
        self.assertEqual(
            conductor.detect_target("Darwin", "arm64").suffix,
            "macos-arm64",
        )
        self.assertEqual(
            conductor.detect_target("Linux", "AMD64").suffix,
            "linux-x86_64",
        )

    def test_rejects_unpublished_target(self):
        with self.assertRaises(conductor.ConductorRecipeError):
            conductor.detect_target("Windows", "AMD64")

    def test_checksum_contract(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            archive = root / "package.zip"
            archive.write_bytes(b"release")
            digest = hashlib.sha256(b"release").hexdigest()
            checksum = root / "package.zip.sha256"
            checksum.write_text(f"{digest}  package.zip\n", encoding="utf-8")
            self.assertEqual(conductor.verify_checksum(archive, checksum), digest)

    def test_extracts_and_validates_package(self):
        target = conductor.detect_target("Darwin", "arm64")
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "source" / target.package_name
            (source / "bin").mkdir(parents=True)
            (source / "metadata").mkdir()
            (source / "VERSION").write_text(f"{conductor.VERSION}\n")
            (source / "metadata" / "build-contract.txt").write_text(
                f"platform={target.platform_contract}\n"
            )
            for binary in conductor.EXPECTED_BINARIES:
                (source / "bin" / binary).write_bytes(b"binary")
            archive_path = root / target.archive_name
            with zipfile.ZipFile(archive_path, "w") as archive:
                for path in source.rglob("*"):
                    if path.is_file():
                        archive.write(path, path.relative_to(root / "source"))
            package = conductor.extract_archive(archive_path, root / "runtime", target)
            result = conductor.validate_package(package, target)
            self.assertEqual(result["platform"], "macos/arm64")
            self.assertEqual(len(result["binaries"]), 11)

    def test_rejects_archive_path_traversal(self):
        target = conductor.detect_target("Darwin", "arm64")
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            archive_path = root / target.archive_name
            with zipfile.ZipFile(archive_path, "w") as archive:
                archive.writestr("../outside", "unsafe")
            with self.assertRaises(conductor.ConductorRecipeError):
                conductor.extract_archive(archive_path, root / "runtime", target)


if __name__ == "__main__":
    unittest.main()
