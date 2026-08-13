from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tools.recipe import hakoniwa_conductor_time_sync as time_sync


class HakoniwaConductorTimeSyncRecipeTest(unittest.TestCase):
    def make_sample(self, root: Path) -> Path:
        sample = root / "sample"
        generated = sample / "config" / "generated"
        generated.mkdir(parents=True)
        (generated / "pdudef.json").write_text("{}\n", encoding="utf-8")
        asset = sample / "asset" / "hello_asset.py"
        asset.parent.mkdir(parents=True)
        asset.write_text("print('hello')\n", encoding="utf-8")
        return sample

    def test_configure_materializes_public_fixture_and_isolated_core_domains(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            sample = self.make_sample(root)
            work = root / "work"
            with mock.patch.object(time_sync, "WORK_ROOT", work):
                result = time_sync.configure(sample)
                self.assertEqual(
                    result["domains"], ["server", "client-a", "client-b"]
                )
                self.assertTrue((work / "config" / "pdudef.json").is_file())
                self.assertTrue((work / "asset" / "hello_asset.py").is_file())
                mmap_paths = set()
                for domain in result["domains"]:
                    config = json.loads(
                        time_sync.core_config(domain).read_text(encoding="utf-8")
                    )
                    self.assertEqual(config["shm_type"], "mmap")
                    mmap_paths.add(config["core_mmap_path"])
                    self.assertEqual(config["asset_timeout_usec"], 600_000_000)
                self.assertEqual(len(mmap_paths), 3)

                stale = work / "runtime" / "core" / "client-a" / "mmap" / "stale.bin"
                stale.write_bytes(b"stale")
                time_sync.configure(sample)
                self.assertFalse(stale.exists())

    def test_configure_rejects_rd_control_artifact(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            sample = self.make_sample(root)
            artifact = sample / "config" / "generated" / "bridge-rd-ctrl.json"
            artifact.write_text("{}\n", encoding="utf-8")
            with mock.patch.object(time_sync, "WORK_ROOT", root / "work"):
                with self.assertRaises(time_sync.TimeSyncError):
                    time_sync.configure(sample)

    def test_decode_tick_reads_exact_required_tick(self):
        with tempfile.TemporaryDirectory() as tmp:
            log = Path(tmp) / "asset.log"
            log.write_text(
                "noise\n"
                '{"event":"TICK","tick":10,"sim_time_usec":100000}\n'
                '{"event":"TICK","tick":20,"sim_time_usec":200000}\n',
                encoding="utf-8",
            )
            self.assertEqual(time_sync.decode_tick(log, 20), 200000)
            self.assertIsNone(time_sync.decode_tick(log, 30))

    def test_parser_defaults_to_bounded_smoke(self):
        args = time_sync.parser().parse_args(["smoke"])
        self.assertEqual(args.required_tick, 20)
        self.assertEqual(args.timeout, 60.0)

    def test_foundation_contract_rejects_revision_mismatch(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            contract = root / "build-contract.txt"
            contract.write_text(
                "hakoniwa_core_pro_ref=core-revision\n"
                "hakoniwa_pdu_endpoint_ref=endpoint-revision\n"
                "hakoniwa_pdu_rpc_ref=rpc-revision\n"
                "hakoniwa_pdu_bridge_core_ref=bridge-revision\n"
                "hakoniwa_pdu_version=1.6.6\n",
                encoding="utf-8",
            )
            receipts = root / "foundation" / "share" / "hakoniwa" / "receipts"
            receipts.mkdir(parents=True)
            revisions = {
                "hakoniwa-core-pro": "wrong-revision",
                "hakoniwa-pdu-endpoint": "endpoint-revision",
                "hakoniwa-pdu-rpc": "rpc-revision",
                "hakoniwa-pdu-bridge-core": "bridge-revision",
            }
            for component_id, revision in revisions.items():
                (receipts / f"{component_id}.yaml").write_text(
                    "schema_version: 1\n"
                    "component:\n"
                    f"  id: {component_id}\n"
                    f'  source_revision: "{revision}"\n',
                    encoding="utf-8",
                )
            (receipts / "hakoniwa-pdu-python.yaml").write_text(
                "schema_version: 1\n"
                "component:\n"
                "  id: hakoniwa-pdu-python\n"
                '  version: "1.6.6"\n',
                encoding="utf-8",
            )
            with mock.patch.object(
                time_sync, "FOUNDATION_ROOT", root / "foundation"
            ):
                with self.assertRaisesRegex(
                    time_sync.TimeSyncError, "revision mismatch"
                ):
                    time_sync.validate_foundation_contract(contract)


if __name__ == "__main__":
    unittest.main()
