from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace


MODULE_PATH = Path(__file__).with_name("ros2_bridge_examples.py")
sys.path.insert(0, str(MODULE_PATH.parent))
SPEC = importlib.util.spec_from_file_location("ros2_bridge_examples", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
bridge = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = bridge
SPEC.loader.exec_module(bridge)

PROBE_PATH = Path(__file__).with_name("ros2_bridge_probe.py")
PROBE_SPEC = importlib.util.spec_from_file_location("ros2_bridge_probe", PROBE_PATH)
assert PROBE_SPEC is not None and PROBE_SPEC.loader is not None
probe = importlib.util.module_from_spec(PROBE_SPEC)
sys.modules[PROBE_SPEC.name] = probe
PROBE_SPEC.loader.exec_module(probe)


class ProfileContractTests(unittest.TestCase):
    def test_profiles_cover_the_three_missing_directions(self) -> None:
        self.assertEqual(
            set(bridge.PROFILES),
            {"service-client", "action-server", "action-client"},
        )
        self.assertEqual(bridge.PROFILES["service-client"].port, 54012)
        self.assertEqual(bridge.PROFILES["action-server"].port, 54013)
        self.assertEqual(bridge.PROFILES["action-client"].port, 54014)

    def test_transport_topology_follows_runtime_direction(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for name, item in bridge.PROFILES.items():
                target = root / f"{name}.json"
                bridge.write_transport(item, target)
                endpoints = json.loads(target.read_text(encoding="utf-8"))[
                    "endpoints"
                ]
                client_id = (
                    "hakoniwa-pdu-ros-service"
                    if item.kind == "service"
                    else "fibonacci-client"
                )
                server_id = (
                    "server_node"
                    if item.kind == "service"
                    else "fibonacci-server"
                )
                self.assertEqual(endpoints[client_id]["role"], "client")
                self.assertEqual(endpoints[server_id]["role"], "server")
                expected_host = (
                    "host.docker.internal"
                    if item.direction == "server"
                    else "127.0.0.1"
                )
                self.assertEqual(
                    endpoints[client_id]["remote"]["address"], expected_host
                )

    def test_action_container_uses_installed_hakoniwa_pdu_contract(self) -> None:
        for name in ("action-server", "action-client"):
            command = bridge.container_command(bridge.PROFILES[name])
            self.assertNotIn("PYTHONPATH", command)
            self.assertIn(name, command)

    def test_docker_image_installs_ros_service_and_action_examples(self) -> None:
        content = bridge.dockerfile_text()
        self.assertIn("ros-jazzy-demo-nodes-py", content)
        self.assertIn("ros-jazzy-action-tutorials-py", content)
        self.assertIn("COPY --from=pdu-python", content)

    def test_action_probe_treats_feedback_as_a_nonterminal_event(self) -> None:
        feedback = SimpleNamespace(
            event=2,
            feedback=SimpleNamespace(partial_sequence=[0, 1, 1]),
        )
        result = SimpleNamespace(event=4, feedback=None)
        client = SimpleNamespace(poll=lambda: events.pop(0))
        events = [feedback, result]

        observed = probe.wait_action_event(
            client,
            expected=4,
            ignored=(2,),
        )

        self.assertIs(observed, result)


if __name__ == "__main__":
    unittest.main()
