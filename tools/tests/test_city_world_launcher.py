from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from tools.remote_operation.city_world import launcher


class CityWorldLauncherTest(unittest.TestCase):
    def test_generated_launcher_is_core_free_activate_only_input(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            config_path = launcher.write_launcher_config(
                launcher_runtime=root / "launcher",
                service_runtime=root / "worker",
                listen_address="127.0.0.1",
                worker_port=54210,
                web_port=8008,
                max_download_gib=8.0,
                parallel_workers=6,
                dem_parallel_workers=4,
                terrain_spacing_m="auto",
            )
            config = json.loads(config_path.read_text(encoding="utf-8"))
            self.assertEqual(
                [asset["name"] for asset in config["assets"]],
                ["city-world-worker", "city-world-web"],
            )
            self.assertTrue(all(
                asset["activation_timing"] == "before_start"
                for asset in config["assets"]
            ))
            self.assertEqual(
                config["assets"][1]["depends_on"], ["city-world-worker"]
            )
            serialized = json.dumps(config)
            self.assertNotIn("hako-cmd", serialized)
            self.assertIn("--ready-file", serialized)
            self.assertIn("tools.remote_operation.city_world.worker", serialized)
            self.assertIn("tools.remote_operation.city_world.web_smoke", serialized)
            worker_args = config["assets"][0]["args"]
            self.assertEqual(
                worker_args[worker_args.index("--parallel-workers") + 1], "6"
            )
            self.assertEqual(
                worker_args[worker_args.index("--dem-parallel-workers") + 1], "4"
            )
            self.assertEqual(
                worker_args[worker_args.index("--terrain-spacing-m") + 1], "auto"
            )

    def test_launcher_entry_uses_activate_only_background_mode(self) -> None:
        command = launcher._launcher_command(
            "config.json", "--mode", "activate-only", "--background", "session.json"
        )
        self.assertIn("hakoniwa_pdu.apps.launcher.hako_launcher", command)
        self.assertEqual(command[-4:], ["--mode", "activate-only", "--background", "session.json"])


if __name__ == "__main__":
    unittest.main()
