from __future__ import annotations

import argparse
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch


MODULE_PATH = Path(__file__).with_name("ros2_service_add_two_ints.py")
SPEC = importlib.util.spec_from_file_location("add_two_ints_recipe", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
recipe = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(recipe)


class ConfigurationTests(unittest.TestCase):
    def test_exposure_classification_requires_explicit_ip(self) -> None:
        self.assertEqual(recipe.exposure("127.0.0.1"), "loopback")
        self.assertEqual(recipe.exposure("0.0.0.0"), "wildcard")
        self.assertEqual(recipe.exposure("192.0.2.10"), "non-loopback")
        with self.assertRaises(recipe.RecipeError):
            recipe.exposure("host.example")

    def test_physical_configs_split_host_and_container_addresses(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            config = Path(temporary)
            recipe.write_endpoint_configs(config, "0.0.0.0", 54010)

            host = json.loads(
                (config / "host-rpc-comm.json").read_text(encoding="utf-8")
            )
            container = json.loads(
                (config / "container-rpc-comm.json").read_text(encoding="utf-8")
            )

            self.assertEqual(host["local"], {"address": "0.0.0.0", "port": 54010})
            self.assertEqual(
                container["remote"],
                {"address": "host.docker.internal", "port": 54010},
            )
            self.assertEqual(host["expected_clients"], 4)

    def test_offset_copy_uses_pdu_python_repository_copy(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            source = base / "hakoniwa-pdu-python" / "tests" / "config" / "offset" / "hako_srv_msgs"
            source.mkdir(parents=True)
            for name in recipe.REQUIRED_OFFSETS:
                (source / name).write_text(name, encoding="utf-8")
            destination = base / "work-offset"

            with patch.object(recipe, "sibling", side_effect=lambda name: base / name):
                recipe.copy_offsets(destination)

            for name in recipe.REQUIRED_OFFSETS:
                self.assertEqual(
                    (destination / "hako_srv_msgs" / name).read_text(encoding="utf-8"),
                    name,
                )

    def test_dockerfile_builds_linux_artifacts_independently(self) -> None:
        content = recipe.dockerfile_text()
        self.assertIn("COPY --from=endpoint", content)
        self.assertIn("COPY --from=rpc", content)
        self.assertIn("COPY --from=pdu-python", content)
        self.assertIn("COPY --from=pdu-ros", content)
        self.assertIn("/opt/hakoniwa", content)
        self.assertIn("--python-venv /opt/hakoniwa/python", content)
        self.assertNotIn("/opt/hako-python", content)
        self.assertNotIn("work/foundation/install", content)


class LifecycleTests(unittest.TestCase):
    def test_doctor_port_probe_reports_bind_failure(self) -> None:
        probe = MagicMock()
        probe.__enter__.return_value = probe
        probe.bind.side_effect = OSError("already in use")
        with patch.object(recipe.socket, "socket", return_value=probe):
            available, detail = recipe.tcp_port_available("0.0.0.0", 54010)
        self.assertFalse(available)
        self.assertIn("already in use", detail)

    def test_host_worker_is_detached_from_short_lived_start_command(self) -> None:
        options = recipe.background_process_options()
        if recipe.os.name == "nt":
            self.assertNotEqual(options.get("creationflags", 0), 0)
        else:
            self.assertEqual(options, {"start_new_session": True})

    def test_managed_host_requires_matching_token_and_pid(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            host_session = Path(temporary) / "host-session.json"
            recipe.atomic_json(
                host_session,
                {"state": "RUNNING", "pid": 123, "token": "owner-token"},
            )
            with patch.object(recipe, "pid_alive", return_value=True):
                self.assertTrue(
                    recipe.managed_host_alive(
                        {"host_pid": 123, "host_token": "owner-token"},
                        host_session,
                    )
                )
                self.assertFalse(
                    recipe.managed_host_alive(
                        {"host_pid": 123, "host_token": "stale-token"},
                        host_session,
                    )
                )

    def test_start_refuses_unapproved_wildcard_before_spawning(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            runtime_config = base / "runtime.json"
            recipe.atomic_json(
                runtime_config,
                {
                    "bind_address": "0.0.0.0",
                    "port": 54010,
                    "exposure": "wildcard",
                    "approve_non_loopback_bind": False,
                },
            )
            fake_paths = {"runtime_config": runtime_config}
            args = argparse.Namespace(approve_non_loopback_bind=False)

            with patch.object(recipe, "paths", return_value=fake_paths):
                with self.assertRaisesRegex(recipe.RecipeError, "start refused"):
                    recipe.start(args)


if __name__ == "__main__":
    unittest.main()
