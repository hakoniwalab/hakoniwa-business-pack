from __future__ import annotations

import hashlib
import tempfile
import unittest
import zipfile
from pathlib import Path

from tools.recipe import hakoniwa_conductor as conductor


class HakoniwaConductorRecipeTest(unittest.TestCase):
    def write_foundation_contract_fixture(
        self,
        root: Path,
        *,
        core_revision: str,
        endpoint_revision: str,
    ) -> tuple[Path, Path]:
        contract = root / "build-contract.txt"
        contract.write_text(
            "hakoniwa_core_pro_ref="
            "b8818ec47619f6026739f7d71e2d22829dea4752\n"
            "hakoniwa_pdu_endpoint_ref="
            "93a926c520a76f401f52d7ba4e816e5ad54d7c36\n"
            "hakoniwa_pdu_rpc_ref="
            "7ed378bacf76aca9ba65625d3c21c65bc83f7051\n"
            "hakoniwa_pdu_bridge_core_ref="
            "e5c567948b42512abc36a8cea2b4d8a151f6c145\n"
            "hakoniwa_pdu_version=1.6.9\n",
            encoding="utf-8",
        )
        foundation = root / "foundation"
        receipts = foundation / "share" / "hakoniwa" / "receipts"
        receipts.mkdir(parents=True)
        revisions = {
            "hakoniwa-core-pro": core_revision,
            "hakoniwa-pdu-endpoint": endpoint_revision,
            "hakoniwa-pdu-rpc": "7ed378bacf76aca9ba65625d3c21c65bc83f7051",
            "hakoniwa-pdu-bridge-core": "e5c567948b42512abc36a8cea2b4d8a151f6c145",
        }
        for component_id, revision in revisions.items():
            capabilities = (
                "capabilities:\n"
                "  hakoniwa_core: true\n"
                "  core_callback: true\n"
                "  core_polling: true\n"
                "  tcp: true\n"
                if component_id == "hakoniwa-pdu-endpoint"
                else ""
            )
            (receipts / f"{component_id}.yaml").write_text(
                "schema_version: 1\n"
                "component:\n"
                f"  id: {component_id}\n"
                f'  source_revision: "{revision}"\n'
                + capabilities,
                encoding="utf-8",
            )
        (receipts / "hakoniwa-pdu-python.yaml").write_text(
            "schema_version: 1\n"
            "component:\n"
            "  id: hakoniwa-pdu-python\n"
            '  version: "1.6.9"\n',
            encoding="utf-8",
        )
        return contract, foundation

    def test_detects_published_targets(self):
        self.assertEqual(
            conductor.detect_target("Darwin", "arm64").suffix,
            "macos-arm64",
        )
        self.assertEqual(
            conductor.detect_target("Linux", "AMD64").suffix,
            "linux-x86_64",
        )
        target = conductor.detect_target("Darwin", "arm64", version="v1.1.0")
        self.assertEqual(target.version, "v1.1.0")
        self.assertEqual(
            target.package_name,
            "hakoniwa-conductor-v1.1.0-macos-arm64",
        )
        self.assertEqual(
            conductor.detect_target(
                "Linux", "x86_64", version="v1.1.0"
            ).platform_contract,
            "linux/x86_64",
        )
        self.assertEqual(
            conductor.detect_target(
                "Linux", "x86_64", version="v1.0.0"
            ).platform_contract,
            "linux/amd64",
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

    def test_foundation_contract_accepts_only_audited_forward_revisions(self):
        with tempfile.TemporaryDirectory() as tmp:
            contract, foundation = self.write_foundation_contract_fixture(
                Path(tmp),
                core_revision="945ad77a34b1b86282bf74a04d79451d0eb2ebb8",
                endpoint_revision="9015a17415fd4a2042de2528b835a265af85b165",
            )
            installed = conductor.validate_foundation_contract(contract, foundation)
            self.assertEqual(
                installed["hakoniwa-core-pro"],
                "945ad77a34b1b86282bf74a04d79451d0eb2ebb8",
            )

    def test_foundation_contract_still_rejects_unknown_revision(self):
        with tempfile.TemporaryDirectory() as tmp:
            contract, foundation = self.write_foundation_contract_fixture(
                Path(tmp),
                core_revision="unreviewed-core-revision",
                endpoint_revision="93a926c520a76f401f52d7ba4e816e5ad54d7c36",
            )
            with self.assertRaisesRegex(
                conductor.ConductorRecipeError, "revision mismatch"
            ):
                conductor.validate_foundation_contract(contract, foundation)


if __name__ == "__main__":
    unittest.main()
