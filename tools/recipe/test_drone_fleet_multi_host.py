from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import drone_fleet_multi_host as recipe
import drone_fleet_single_host as yaml_support


class DroneFleetMultiHostTest(unittest.TestCase):
    def experiment(self) -> dict:
        return yaml_support.load_simple_yaml(recipe.DEFAULT_EXPERIMENT)

    def conductor(self, root: Path) -> Path:
        (root / "tools").mkdir(parents=True)
        (root / "tools" / "hako.py").write_text("#!/usr/bin/env python3\n")
        (root / "eu-config").mkdir()
        (root / "CMakeLists.txt").write_text("cmake_minimum_required(VERSION 3.20)\n")
        return root

    def test_current_legacy_experiment_is_valid(self) -> None:
        resolved = recipe.validate_experiment(self.experiment())
        self.assertEqual(resolved["derived"]["global_drone_range"], [0, 255])
        self.assertEqual(resolved["derived"]["host_ids"], ["srv-01", "cli-01"])

    def test_rejects_client_address(self) -> None:
        value = self.experiment()
        value["deployment"]["hosts"]["cli-01"]["address"] = "172.20.0.2"
        with self.assertRaisesRegex(recipe.RecipeError, "must not declare address"):
            recipe.validate_experiment(value)

    def test_rejects_partition_gap(self) -> None:
        value = self.experiment()
        value["deployment"]["hosts"]["cli-01"]["global_start_index"] = 129
        with self.assertRaisesRegex(recipe.RecipeError, "not contiguous"):
            recipe.validate_experiment(value)

    def test_rejects_host_keys_as_directory_names(self) -> None:
        value = self.experiment()
        value["deployment"]["hosts"]["../client"] = value["deployment"]["hosts"].pop(
            "cli-01"
        )
        with self.assertRaisesRegex(recipe.RecipeError, "stable host ids"):
            recipe.validate_experiment(value)

    def test_builds_legacy_conductor_input(self) -> None:
        resolved = recipe.validate_experiment(self.experiment())
        generated = recipe.build_conductor_input(resolved)

        self.assertEqual(generated["mode"], "simple")
        self.assertEqual(generated["execution_nodes"], ["cli-01"])
        self.assertEqual(
            generated["connection_pairs"],
            [{"client_node_id": "cli-01", "server_node_id": "srv-01-01"}],
        )
        self.assertEqual(
            generated["comm_defaults"],
            {"tcp": {"base_port": 54011, "connection_initiator": "client"}},
        )
        self.assertEqual(
            generated["conductor_defaults"],
            {"delta_time_usec": 10000, "max_delay_time_usec": 20000},
        )
        self.assertNotIn("real_sleep_msec", generated["conductor_defaults"])
        self.assertNotIn("simtime_publish_mode", generated["conductor_defaults"])
        self.assertEqual(
            generated["pdu_type_groups"][0]["robot_types"][0]["pdu_names"],
            ["drone_visual_state_array_1"],
        )
        self.assertEqual(
            generated["unit_placement"],
            {"mode": "manual", "nodes": {"cli-01": ["vsp-cli-01"]}},
        )

    def test_legacy_conductor_input_matches_authoritative_reference(self) -> None:
        resolved = recipe.validate_experiment(self.experiment())
        generated = recipe.build_conductor_input(resolved)
        expected = json.loads(
            (
                recipe.DEFAULT_CONDUCTOR_ROOT
                / "eu-config"
                / "eu-input.fleets.json"
            ).read_text(encoding="utf-8")
        )
        self.assertEqual(generated, expected)

    def test_materializes_deterministic_role_specific_bundles(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            conductor = self.conductor(root / "conductor")
            output = root / "output"
            identity = {"revision": "abc123", "dirty": False}
            with mock.patch.object(recipe, "git_identity", return_value=identity):
                first = recipe.materialize(
                    recipe.DEFAULT_EXPERIMENT,
                    output,
                    conductor,
                    write=True,
                )
                second = recipe.materialize(
                    recipe.DEFAULT_EXPERIMENT,
                    output,
                    conductor,
                    write=True,
                )

            self.assertEqual(first, second)
            index = json.loads((output / "bundle-index.json").read_text())
            server = json.loads((output / "bundles/srv-01/manifest.json").read_text())
            client = json.loads((output / "bundles/cli-01/manifest.json").read_text())
            self.assertEqual(server["config_hash"], client["config_hash"])
            self.assertEqual(index["config_hash"], server["config_hash"])
            self.assertTrue(server["visualization"]["web_bridge"])
            self.assertFalse(client["visualization"]["web_bridge"])
            self.assertNotIn("address", client["host"])
            shared = index["shared_inputs"]["conductor"]
            conductor_input_path = output / shared["path"]
            conductor_input = json.loads(conductor_input_path.read_text())
            self.assertEqual(
                shared["sha256"],
                recipe.digest(conductor_input),
            )
            self.assertEqual(
                server["shared_input_refs"]["conductor"]["sha256"],
                shared["sha256"],
            )
            self.assertEqual(
                client["shared_input_refs"]["conductor"]["sha256"],
                shared["sha256"],
            )
            self.assertEqual(
                shared["schema"]["id"],
                "https://github.com/hakoniwalab/hakoniwa-conductor/"
                "schemas/eu-input-v1.schema.json",
            )
            self.assertFalse((output / "bundles/server/inputs").exists())
            self.assertFalse((output / "bundles/client/inputs").exists())
            node_map = json.loads(
                (output / "config/conductor/node-ip-map.json").read_text()
            )
            self.assertEqual(node_map, {"nodes": {"srv-01": "192.168.2.100"}})
            self.assertFalse((output / "config/local-selection.json").exists())

    def test_plan_does_not_write_output(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            conductor = self.conductor(root / "conductor")
            output = root / "output"
            with mock.patch.object(
                recipe, "git_identity", return_value={"revision": "abc", "dirty": False}
            ):
                recipe.materialize(
                    recipe.DEFAULT_EXPERIMENT, output, conductor, write=False
                )
            self.assertFalse(output.exists())

    def test_configure_invokes_private_conductor_with_shared_input(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            conductor = self.conductor(root / "conductor")
            eu_input = root / "output/config/conductor/eu-input.json"
            with mock.patch.object(recipe.subprocess, "run") as run:
                run.return_value.returncode = 0
                recipe.run_conductor_configure(conductor, eu_input)
            command = run.call_args.args[0]
            self.assertEqual(command[-3:], ["configure", "--config", str(eu_input)])

    def test_materializes_both_hosts_through_shared_runtime_specs(self) -> None:
        resolved = recipe.validate_experiment(self.experiment())
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            drone = root / "drone"
            (drone / "tools").mkdir(parents=True)
            (drone / "tools/gen_fleet_scale_config.py").write_text("# fixture\n")
            with (
                mock.patch.object(recipe.fleet_runtime, "prepare_config") as prepare,
                mock.patch.object(
                    recipe.fleet_runtime, "validate_partitions", return_value=[]
                ),
            ):
                generated = recipe.materialize_host_runtimes(
                    resolved, root / "output", drone
                )

            self.assertEqual(set(generated), {"srv-01", "cli-01"})
            self.assertEqual(prepare.call_count, 2)
            server_spec = prepare.call_args_list[0].args[2]
            client_spec = prepare.call_args_list[1].args[2]
            self.assertEqual(
                (
                    server_spec.local_drone_count,
                    server_spec.process_count,
                    server_spec.global_drone_count,
                    server_spec.global_start_index,
                    server_spec.output_chunk_base_index,
                    server_spec.max_drones_per_packet,
                ),
                (128, 4, 256, 0, 0, 128),
            )
            self.assertEqual(
                (
                    client_spec.local_drone_count,
                    client_spec.process_count,
                    client_spec.global_drone_count,
                    client_spec.global_start_index,
                    client_spec.output_chunk_base_index,
                    client_spec.max_drones_per_packet,
                ),
                (128, 12, 256, 128, 1, 128),
            )

            server_launcher = recipe.host_launcher_spec(resolved, "srv-01")
            client_launcher = recipe.host_launcher_spec(resolved, "cli-01")
            self.assertTrue(server_launcher.external_conductor)
            self.assertTrue(server_launcher.web_bridge)
            self.assertTrue(server_launcher.viewer)
            self.assertEqual(
                server_launcher.viewer_activation_timing, "before_start"
            )
            self.assertEqual(server_launcher.z_offset_m, 0.0)
            self.assertTrue(client_launcher.external_conductor)
            self.assertFalse(client_launcher.web_bridge)
            self.assertFalse(client_launcher.viewer)
            self.assertEqual(
                client_launcher.viewer_activation_timing, "after_start"
            )
            self.assertEqual(client_launcher.z_offset_m, 2.0)

    def test_conductor_assets_pin_checkout_binaries_and_configs(self) -> None:
        resolved = recipe.validate_experiment(self.experiment())
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            conductor = root / "conductor"
            build = conductor / "cmake-build"
            build.mkdir(parents=True)
            (build / "main_server").write_text("")
            (build / "main_client").write_text("")
            (build / "main_server").chmod(0o755)
            (build / "main_client").chmod(0o755)
            generated = root / "generated"
            server = recipe.conductor_launcher_asset(
                resolved, "srv-01", conductor, generated
            )
            client = recipe.conductor_launcher_asset(
                resolved, "cli-01", conductor, generated
            )
        self.assertEqual(server["name"], "conductor-server")
        self.assertEqual(server["command"], str(build / "main_server"))
        self.assertEqual(
            server["args"],
            [
                "--config",
                str(generated / "conductor" / "srv-01.json"),
                "--server-node-id",
                "srv-01-01",
                "--enable-conductor",
            ],
        )
        self.assertEqual(client["name"], "conductor-client")
        self.assertEqual(client["command"], str(build / "main_client"))
        self.assertEqual(
            client["args"],
            ["--config", str(generated / "conductor" / "cli-01.json")],
        )

    def test_local_selection_accepts_unique_role_and_rejects_stale_hash(self) -> None:
        resolved = recipe.validate_experiment(self.experiment())
        index = {"config_hash": "abc123"}
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            output = root / "output"
            recipe.atomic_json(output / "bundle-index.json", index)
            recipe.atomic_json(output / "config/resolved-experiment.json", resolved)
            with (
                mock.patch.object(recipe, "LOCAL_SELECTION", root / "local.json"),
                mock.patch.object(recipe.platform, "system", return_value="Linux"),
            ):
                recipe.write_local_selection(resolved, index, "client")
                loaded = recipe.load_local_selection(output)
                self.assertEqual(loaded["selection"]["host_id"], "cli-01")
                self.assertEqual(loaded["selection"]["role"], "client")
                recipe.atomic_json(output / "bundle-index.json", {"config_hash": "new"})
                with self.assertRaisesRegex(recipe.RecipeError, "stale"):
                    recipe.load_local_selection(output)

    def test_cli_persists_host_only_during_configure(self) -> None:
        parsed = recipe.parser().parse_args(["configure", "--host", "server"])
        self.assertEqual(parsed.command, "configure")
        self.assertEqual(parsed.host, "server")
        for command in (
            "doctor",
            "start",
            "open-viewer",
            "run",
            "status",
            "stop",
        ):
            parsed = recipe.parser().parse_args([command])
            self.assertEqual(parsed.command, command)
            self.assertFalse(hasattr(parsed, "host"))

    def test_open_viewer_is_server_only_and_uses_global_drone_count(self) -> None:
        resolved = recipe.validate_experiment(self.experiment())
        client_state = {
            "selection": {"host_id": "cli-01", "role": "client"},
            "resolved": resolved,
        }
        with mock.patch.object(recipe, "load_local_selection", return_value=client_state):
            with self.assertRaisesRegex(recipe.RecipeError, "server-only"):
                recipe.open_viewer_local(Path("/output"))

        server_state = {
            "selection": {"host_id": "srv-01", "role": "server"},
            "resolved": resolved,
        }
        response = mock.MagicMock()
        response.__enter__.return_value.status = 200
        with (
            mock.patch.object(recipe, "load_local_selection", return_value=server_state),
            mock.patch.object(
                recipe.urllib.request, "urlopen", return_value=response
            ) as urlopen,
            mock.patch.object(recipe.webbrowser, "open", return_value=True) as browser,
        ):
            self.assertEqual(recipe.open_viewer_local(Path("/output")), 0)

        urlopen.assert_called_once_with(
            "http://127.0.0.1:8000/index.html", timeout=2.0
        )
        viewer_url = browser.call_args.args[0]
        self.assertIn("dynamicSpawn=true", viewer_url)
        self.assertIn("maxDynamicDrones=256", viewer_url)

    def test_run_is_server_only_and_uses_launcher_control_start(self) -> None:
        resolved = recipe.validate_experiment(self.experiment())
        client_state = {
            "selection": {"host_id": "cli-01", "role": "client"},
            "resolved": resolved,
        }
        with mock.patch.object(recipe, "load_local_selection", return_value=client_state):
            with self.assertRaisesRegex(recipe.RecipeError, "server-only"):
                recipe.launcher_control(
                    "run", Path("/output"), Path("/drone"), Path("/conductor"), Path("/viewer")
                )

        server_state = {
            "selection": {"host_id": "srv-01", "role": "server"},
            "resolved": resolved,
        }
        paths = recipe.SimpleNamespace(
            runtime_root=Path("/runtime"),
            recipe_config=Path("/config"),
        )
        completed = recipe.SimpleNamespace(returncode=0)
        with (
            mock.patch.object(recipe, "load_local_selection", return_value=server_state),
            mock.patch.object(recipe, "host_runtime_paths", return_value=paths),
            mock.patch.object(
                recipe.yaml_support,
                "resolve_foundation_python",
                return_value=Path("/foundation/python"),
            ),
            mock.patch.object(recipe.yaml_support, "runtime_environment", return_value={}),
            mock.patch.object(recipe.subprocess, "run", return_value=completed) as run,
        ):
            self.assertEqual(
                recipe.launcher_control(
                    "run", Path("/output"), Path("/drone"), Path("/conductor"), Path("/viewer")
                ),
                0,
            )
        self.assertEqual(run.call_args.args[0][-2:], ["start", "/runtime/launcher-session.json"])

    def test_launcher_manual_run_contract_probe(self) -> None:
        completed = recipe.SimpleNamespace(returncode=0)
        with mock.patch.object(recipe.subprocess, "run", return_value=completed) as run:
            self.assertEqual(
                recipe.launcher_supports_manual_run(Path("/foundation/python")),
                (True, "background activate-only and control start"),
            )
        self.assertEqual(run.call_args.args[0][:2], ["/foundation/python", "-c"])

        completed.returncode = 1
        with mock.patch.object(recipe.subprocess, "run", return_value=completed):
            ok, detail = recipe.launcher_supports_manual_run(Path("/foundation/python"))
        self.assertFalse(ok)
        self.assertIn("rebuild hakoniwa-pdu-python", detail)


if __name__ == "__main__":
    unittest.main()
