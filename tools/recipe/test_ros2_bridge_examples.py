from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import unittest.mock
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
                if item.direction == "server":
                    self.assertEqual(
                        endpoints[client_id]["remote"]["address"],
                        "host.docker.internal",
                    )
                else:
                    self.assertEqual(
                        endpoints[client_id]["remote"]["address"],
                        "127.0.0.1",
                    )

    def test_action_client_transport_can_reverse_only_tcp_roles(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            target = Path(temporary) / "transport.json"
            item = bridge.PROFILES["action-client"]
            bridge.write_transport(item, target, "host-listens")
            endpoints = json.loads(target.read_text(encoding="utf-8"))[
                "endpoints"
            ]

            self.assertEqual(endpoints["fibonacci-client"]["role"], "server")
            self.assertEqual(
                endpoints["fibonacci-client"]["local"]["address"],
                "0.0.0.0",
            )
            self.assertEqual(endpoints["fibonacci-server"]["role"], "client")
            self.assertEqual(
                endpoints["fibonacci-server"]["remote"]["address"],
                "host.docker.internal",
            )

    def test_host_listener_requires_explicit_exposure_approval(self) -> None:
        args = bridge.parser().parse_args(
            ["action-client", "configure", "--tcp-direction", "host-listens"]
        )
        with self.assertRaisesRegex(bridge.RecipeError, "non-loopback"):
            bridge.configure(args)

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
        observed_feedback = []

        observed = probe.wait_action_event(
            client,
            expected=4,
            ignored=(2,),
            observed_ignored=observed_feedback,
        )

        self.assertIs(observed, result)
        self.assertEqual(observed_feedback, [feedback])

    def test_fibonacci_sequence_matches_ros_tutorial_order_contract(self) -> None:
        self.assertEqual(probe.fibonacci_sequence(2), [0, 1, 1])
        self.assertEqual(
            probe.fibonacci_sequence(10),
            [0, 1, 1, 2, 3, 5, 8, 13, 21, 34, 55],
        )

    def test_cancel_smoke_is_available_as_a_recipe_command(self) -> None:
        args = bridge.parser().parse_args(
            ["action-client", "cancel-smoke", "--iterations", "10"]
        )
        self.assertEqual(args.command, "cancel-smoke")
        self.assertEqual(args.iterations, 10)

    def test_action_probe_waits_for_the_async_tcp_connection(self) -> None:
        raw = SimpleNamespace(
            is_running=unittest.mock.Mock(side_effect=[False, False, True])
        )
        with unittest.mock.patch.object(probe.time, "sleep"):
            probe.wait_action_client_connected(raw, settle_sec=0.0)
        self.assertEqual(raw.is_running.call_count, 3)


if __name__ == "__main__":
    unittest.main()
