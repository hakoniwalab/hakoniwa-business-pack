#!/usr/bin/env python3
"""Contract tests for the Shibuya Drone Recipe operator."""

from __future__ import annotations

import importlib.util
import hashlib
import io
import json
import contextlib
import tempfile
import unittest
from pathlib import Path
from unittest import mock

SCRIPT = Path(__file__).with_name("drone_shibuya_gamepad.py")
SPEC = importlib.util.spec_from_file_location("drone_shibuya_gamepad_recipe", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
recipe = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(recipe)


class DroneShibuyaGamepadTest(unittest.TestCase):
    def _paths(self, temporary: str):
        foundation = recipe.gamepad.load_foundation_module()
        paths = foundation.resolve_workspace(Path(temporary), recipe.RECIPE_ID)
        foundation.prepare_workspace(paths)
        return paths

    def _write_json(self, path: Path, data: object) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")

    def _fixture_sources(self, temporary: str) -> None:
        fixture = Path(temporary) / "sources"
        self.drone_root = fixture / "hakoniwa-drone-core"
        self.map_root = fixture / "hakoniwa-map-viewer"
        self.threejs_root = fixture / "hakoniwa-threejs-drone"

        source_config = {
            "name": "Drone",
            "simulation": {
                "timeStep": 0.003,
                "location": {
                    **recipe.MUJOCO_LOCATION,
                    "magneticField": {"intensity_nT": 53045.1},
                },
            },
            "components": {
                "droneDynamics": {
                    "mujoco": {
                        "modelName": "drone_base",
                        "modelPath": "config/drone/mujoco-shibuya-api-1/drone.xml",
                    }
                }
            },
            "controller": {
                "moduleDirectory": "../drone_control/cmake-build/workspace/FlightController",
                "moduleName": "FlightController",
                "paramFilePath": str(recipe.SOURCE_CONTROLLER_PARAM),
            },
        }
        source_dir = self.drone_root / recipe.SOURCE_DRONE_CONFIG
        self._write_json(source_dir / "drone_config_0.json", source_config)
        (source_dir / "drone.xml").write_text(
            '<mujoco><option timestep="0.003"/><worldbody/></mujoco>\n',
            encoding="utf-8",
        )
        param = self.drone_root / recipe.SOURCE_CONTROLLER_PARAM
        param.parent.mkdir(parents=True, exist_ok=True)
        param.write_text("1 2 3\n", encoding="utf-8")
        for path, content in (
            (
                self.drone_root / "drone_api" / "rc" / "rc-custom.py",
                "print('rc')\n",
            ),
            (
                self.drone_root
                / "drone_api"
                / "rc"
                / "rc_config"
                / "ps4-control.json",
                "{}\n",
            ),
            (
                self.drone_root / "config" / "pdudef" / "drone-pdudef-1.json",
                "{}\n",
            ),
            (
                self.drone_root
                / "config"
                / "assets"
                / "visual_state_publisher"
                / recipe.VISUAL_STATE_CONFIG,
                "{}\n",
            ),
            (
                self.drone_root
                / "config"
                / "assets"
                / "web_bridge_fleets"
                / "bridge"
                / "bridge.json",
                "{}\n",
            ),
        ):
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")

        map_ui = self.map_root / "src" / "client" / "src" / "ui.js"
        map_ui.parent.mkdir(parents=True, exist_ok=True)
        map_ui.write_text(
            "const map = L.map('map').setView([35.6812, 139.7671], 15);\n"
            "let ORIGIN_LAT = 35.6625;\n"
            "let ORIGIN_LON = 139.70625;\n",
            encoding="utf-8",
        )
        (self.map_root / "src" / "client" / "index.html").write_text(
            "<html>map</html>\n", encoding="utf-8"
        )
        icon = self.map_root / "images" / "drone.svg"
        icon.parent.mkdir(parents=True, exist_ok=True)
        icon.write_text("<svg/>\n", encoding="utf-8")

        scene = {
            "version": "1.0",
            "format": "compact",
            "environments": [
                {
                    "name": "town",
                    "model": f"../assets/local_models/{recipe.GLB_NAME}",
                    "pos": [0, 0, -15.04],
                    "hpr": [0, 0, 180],
                }
            ],
            "droneTypesPath": "./drone_types-quadrotor_dji.json",
            "drones": [{"name": "Drone", "type": "quadrotor_dji"}],
        }
        viewer = {
            "version": "1.0",
            "three": {"sceneConfigPath": "./drone_config-compact-1.json"},
            "pdu": {
                "pduDefPath": "./pdudef-fleets.json",
                "wsUri": "ws://127.0.0.1:8765",
                "wireVersion": "v2",
            },
            "stateInput": {"mode": "fleets"},
        }
        self._write_json(
            self.threejs_root / "config" / "drone_config-compact-dji-1.json",
            scene,
        )
        self._write_json(
            self.threejs_root / "config" / "viewer-config-fleets.json", viewer
        )
        self._write_json(
            self.threejs_root / "config" / "drone_types-quadrotor_base.json",
            {"types": {}},
        )
        self._write_json(
            self.threejs_root / "config" / "pdudef-fleets.json", {"robots": []}
        )
        for path in (
            self.threejs_root / "src" / "public" / "drone_viewer.js",
            self.threejs_root / "assets" / "models" / "base-drone-frame.glb",
            self.threejs_root
            / "thirdparty"
            / "hakoniwa-pdu-javascript"
            / "src"
            / "PduManager.js",
        ):
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"fixture")

    def _runtime(self, paths):
        return recipe.RuntimePaths(
            system_name="Darwin",
            drone_service=self.drone_root / "lib" / "mac-main_hako_drone_service",
            visual_state_publisher=(
                self.drone_root / "lib" / "mac-drone_visual_state_publisher"
            ),
            foundation_python=paths.foundation_python / "bin" / "python3",
            hako_cmd=paths.install_prefix / "bin" / "hako-cmd",
            web_bridge=paths.install_prefix / "bin" / "hakoniwa-pdu-web-bridge",
        )

    def _materialize(self, temporary: str):
        self._fixture_sources(temporary)
        paths = self._paths(temporary)
        glb = Path(temporary) / recipe.GLB_NAME
        glb.write_bytes(b"test-shibuya-glb")
        record = recipe.materialize_runtime(
            paths,
            self.drone_root,
            self.map_root,
            self.threejs_root,
            glb,
            "unit-test fixture",
            self._runtime(paths),
        )
        return paths, glb, record

    def test_materialization_preserves_source_and_changes_only_allowlist(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            paths, _glb, _record = self._materialize(temporary)
            source_json_path = (
                self.drone_root
                / recipe.SOURCE_DRONE_CONFIG
                / "drone_config_0.json"
            )
            source_xml_path = (
                self.drone_root / recipe.SOURCE_DRONE_CONFIG / "drone.xml"
            )
            source_json_hash = recipe._sha256(source_json_path)
            source_xml_hash = recipe._sha256(source_xml_path)
            generated_dir = (
                paths.recipe_config
                / recipe.GENERATED_DRONE_CONFIG.relative_to("config")
            )
            before = recipe._load_json(source_json_path)
            after = recipe._load_json(generated_dir / "drone_config_0.json")

            self.assertEqual(
                recipe._json_changes(before, after),
                recipe.ALLOWED_JSON_CHANGES,
            )
            self.assertEqual(
                after["controller"]["moduleName"],
                "RadioController",
            )
            self.assertEqual(
                after["controller"]["paramFilePath"],
                str(recipe.GENERATED_CONTROLLER_PARAM),
            )
            self.assertEqual(
                recipe._sha256(generated_dir / "drone.xml"),
                source_xml_hash,
            )

            self.assertEqual(recipe._sha256(source_json_path), source_json_hash)
            self.assertEqual(recipe._sha256(source_xml_path), source_xml_hash)

    def test_controller_parameters_are_copied_byte_for_byte(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            paths, _glb, _record = self._materialize(temporary)
            generated = (
                paths.recipe_config
                / recipe.GENERATED_CONTROLLER_PARAM.relative_to("config")
            )
            source = self.drone_root / recipe.SOURCE_CONTROLLER_PARAM
            self.assertEqual(recipe._sha256(generated), recipe._sha256(source))

    def test_browser_bundle_uses_supplied_glb_and_fleets_config(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            paths, glb, record_path = self._materialize(temporary)
            record = recipe._load_json(record_path)
            embedded = (
                paths.recipe_root
                / "web"
                / "map-viewer"
                / "thirdparty"
                / "hakoniwa-threejs-drone"
            )
            generated_glb = embedded / "assets" / "local_models" / recipe.GLB_NAME
            viewer = recipe._load_json(
                embedded / "config" / recipe.VIEWER_CONFIG_NAME
            )
            scene = recipe._load_json(
                embedded / "config" / recipe.SCENE_CONFIG_NAME
            )

            self.assertEqual(generated_glb.read_bytes(), glb.read_bytes())
            self.assertEqual(
                record["glb"]["sha256"],
                recipe._sha256(generated_glb),
            )
            self.assertEqual(
                viewer["three"]["sceneConfigPath"],
                f"./{recipe.SCENE_CONFIG_NAME}",
            )
            self.assertEqual(viewer["stateInput"]["mode"], "fleets")
            self.assertEqual(scene["droneTypesPath"], "./drone_types-quadrotor_base.json")
            self.assertEqual(scene["drones"][0]["type"], "quadrotor_base")
            self.assertEqual(
                scene["environments"][0]["model"],
                f"../assets/local_models/{recipe.GLB_NAME}",
            )

    def test_launcher_and_portal_expose_complete_owned_runtime(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            paths, _glb, _record = self._materialize(temporary)
            launcher = recipe._load_json(paths.recipe_config / "launcher.json")
            names = [asset["name"] for asset in launcher["assets"]]

            self.assertEqual(
                names,
                [
                    "drone-service-1",
                    "visual-state-publisher",
                    "web-bridge-fleets",
                    "remote-controller",
                    "map-viewer-webserver",
                ],
            )
            self.assertNotIn("hakoniwa-envsim", json.dumps(launcher).lower())
            self.assertIn(
                str(recipe.GENERATED_DRONE_CONFIG),
                launcher["assets"][0]["args"],
            )
            self.assertEqual(launcher["assets"][0]["delay_sec"], 8)
            self.assertTrue((paths.recipe_root / "index.html").is_file())
            portal = (paths.recipe_root / "index.html").read_text(encoding="utf-8")
            self.assertIn("Leaflet + Three.js", portal)
            self.assertIn(recipe.VIEWER_URL.replace("&", "&amp;"), portal)
            self.assertIn("python tools/workspace.py enter", portal)
            self.assertIn("python tools/recipe/drone_shibuya_gamepad.py start", portal)
            self.assertIn("python tools/recipe/drone_shibuya_gamepad.py stop", portal)
            self.assertIn("data-copy=\"exit\"", portal)
            self.assertLess(
                portal.index("python tools/recipe/drone_shibuya_gamepad.py stop"),
                portal.index("data-copy=\"exit\""),
            )

    def test_map_viewer_origin_matches_plateau_without_source_mutation(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            paths, _glb, record_path = self._materialize(temporary)
            record = recipe._load_json(record_path)
            coordinates = record["coordinate_invariants"]

            self.assertEqual(
                coordinates["drone_simulation_location"],
                recipe.MUJOCO_LOCATION,
            )
            self.assertEqual(
                coordinates["plateau_map_origin"],
                recipe.MAP_ORIGIN,
            )
            self.assertNotEqual(
                recipe.MUJOCO_LOCATION["longitude"],
                recipe.MAP_ORIGIN["longitude"],
            )
            self.assertEqual(
                coordinates["map_viewer_source_origin"],
                recipe.MAP_VIEWER_DEFAULT_ORIGIN,
            )
            self.assertFalse(
                coordinates["map_origin_derived_from_drone_location"]
            )
            generated_ui = (
                paths.recipe_root
                / "web"
                / "map-viewer"
                / "src"
                / "client"
                / "src"
                / "ui.js"
            ).read_text(encoding="utf-8")
            source_ui = (
                self.map_root / "src" / "client" / "src" / "ui.js"
            ).read_text(encoding="utf-8")
            self.assertIn(
                "setView([35.6625, 139.70625], 15)",
                generated_ui,
            )
            self.assertIn("let ORIGIN_LON = 139.70625;", generated_ui)
            self.assertNotIn("let ORIGIN_LON = 139.69375;", generated_ui)
            self.assertIn(
                "setView([35.6812, 139.7671], 15)",
                source_ui,
            )
            self.assertIn("let ORIGIN_LON = 139.70625;", source_ui)

    def test_validation_rejects_non_allowlisted_generated_change(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            paths, _glb, _record = self._materialize(temporary)
            generated = (
                paths.recipe_config
                / recipe.GENERATED_DRONE_CONFIG.relative_to("config")
                / "drone_config_0.json"
            )
            data = recipe._load_json(generated)
            data["simulation"]["timeStep"] = 0.004
            recipe._write_json(generated, data)

            with self.assertRaisesRegex(
                recipe.RecipeError, "four allowlisted paths"
            ):
                recipe.validate_materialization(paths, self.drone_root)

    def test_launcher_control_contract_uses_session_file(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            paths = self._paths(temporary)
            python = paths.foundation_python / "bin" / "python3"
            session = recipe.session_file(paths)
            launcher = paths.recipe_config / "launcher.json"

            start = recipe.gamepad.launcher_start_command(python, launcher, session)
            stop = recipe.gamepad.launcher_control_command(
                python, "terminate", session
            )
            self.assertIn("--background", start)
            self.assertEqual(start[-1], str(session))
            self.assertEqual(stop[-2:], ["terminate", str(session)])
            self.assertNotIn("kill", " ".join(stop))

    def test_demo_readiness_requires_viewer_simulation_and_both_ports(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            paths = self._paths(temporary)
            log = paths.recipe_logs / "drone-service-1.out"
            log.write_text(
                "Viewer thread started.\nWAIT RUNNING\n",
                encoding="utf-8",
            )
            with mock.patch.object(recipe, "_tcp_ready", return_value=True) as ready:
                ok, missing = recipe.wait_for_demo_ready(paths, timeout_sec=0)

            self.assertTrue(ok)
            self.assertEqual(missing, [])
            self.assertEqual(ready.call_args_list, [mock.call(8000), mock.call(8765)])

    def test_demo_readiness_reports_missing_runtime_signals(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            paths = self._paths(temporary)
            with mock.patch.object(recipe, "_tcp_ready", return_value=False):
                ok, missing = recipe.wait_for_demo_ready(paths, timeout_sec=0)

            self.assertFalse(ok)
            self.assertEqual(
                missing,
                [
                    "MuJoCo Viewer",
                    "simulation",
                    "HTTP port 8000",
                    "WebSocket port 8765",
                ],
            )

    def test_background_handoff_explains_returned_but_running_state(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            paths = self._paths(temporary)
            runtime = recipe.RuntimePaths(
                system_name="Darwin",
                drone_service=Path("/runtime/drone-service"),
                visual_state_publisher=Path("/runtime/visual-state-publisher"),
                foundation_python=paths.foundation_python / "bin" / "python3",
                hako_cmd=paths.install_prefix / "bin" / "hako-cmd",
                web_bridge=paths.install_prefix / "bin" / "web-bridge",
            )
            output = io.StringIO()

            with contextlib.redirect_stdout(output):
                recipe.print_background_handoff(paths, runtime)

            content = output.getvalue()
            self.assertIn("still running in background", content)
            self.assertIn("open-viewer", content)
            self.assertIn("status", content)
            self.assertIn("stop", content)
            self.assertIn(str(recipe.session_file(paths)), content)
            self.assertIn(str(paths.recipe_logs), content)

    def test_open_viewer_refuses_browser_when_http_server_is_absent(self) -> None:
        with (
            mock.patch.object(recipe, "_tcp_ready", return_value=False),
            mock.patch.object(recipe.webbrowser, "open") as browser,
        ):
            rc = recipe.open_viewer()

        self.assertEqual(rc, 1)
        browser.assert_not_called()

    def test_local_glb_is_staged_under_recipe_workspace(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            paths = self._paths(temporary)
            source = Path(temporary) / "input.glb"
            source.write_bytes(b"local-release-asset")
            digest = hashlib.sha256(source.read_bytes()).hexdigest()
            with mock.patch.object(recipe, "GLB_SHA256", digest):
                staged, provenance = recipe._stage_glb(paths, source)

            self.assertEqual(staged, paths.recipe_assets / recipe.GLB_NAME)
            self.assertEqual(staged.read_bytes(), source.read_bytes())
            self.assertEqual(provenance, str(source.resolve()))

    def test_default_glb_is_downloaded_from_declared_release(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            paths = self._paths(temporary)
            payload = b"downloaded-release-asset"
            digest = hashlib.sha256(payload).hexdigest()
            with (
                mock.patch.object(recipe, "GLB_SHA256", digest),
                mock.patch.object(
                    recipe.urllib.request,
                    "urlopen",
                    return_value=io.BytesIO(payload),
                ) as urlopen,
            ):
                staged, provenance = recipe._stage_glb(paths, None)

            urlopen.assert_called_once_with(recipe.GLB_DOWNLOAD_URL, timeout=60)
            self.assertEqual(staged.read_bytes(), payload)
            self.assertEqual(provenance, recipe.GLB_RELEASE_URL)

    def test_glb_checksum_mismatch_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            paths = self._paths(temporary)
            source = Path(temporary) / "wrong.glb"
            source.write_bytes(b"not-the-release-asset")
            with self.assertRaisesRegex(recipe.RecipeError, "checksum mismatch"):
                recipe._stage_glb(paths, source)

    def test_recipe_pins_release_url_asset_url_and_checksum(self) -> None:
        content = recipe.recipe_file().read_text(encoding="utf-8")
        self.assertIn(recipe.GLB_RELEASE_URL, content)
        self.assertIn(recipe.GLB_DOWNLOAD_URL, content)
        self.assertIn(recipe.GLB_SHA256, content)
        self.assertIn(
            f"assets/{recipe.GLB_NAME}",
            content,
        )

    def test_adjacent_owner_repositories_match_materialization_contract(self) -> None:
        workspace = SCRIPT.resolve().parents[2]
        drone_root = workspace / "hakoniwa-drone-core"
        map_root = workspace / "hakoniwa-map-viewer"
        threejs_root = workspace / "hakoniwa-threejs-drone"
        if not all(path.is_dir() for path in (drone_root, map_root, threejs_root)):
            self.skipTest("adjacent owner repositories are not available")

        with tempfile.TemporaryDirectory() as temporary:
            paths = self._paths(temporary)
            glb = Path(temporary) / recipe.GLB_NAME
            glb.write_bytes(b"contract-only-glb-fixture")
            runtime = recipe.RuntimePaths(
                system_name="Darwin",
                drone_service=drone_root / "lib" / "mac-main_hako_drone_service",
                visual_state_publisher=(
                    drone_root / "lib" / "mac-drone_visual_state_publisher"
                ),
                foundation_python=paths.foundation_python / "bin" / "python3",
                hako_cmd=paths.install_prefix / "bin" / "hako-cmd",
                web_bridge=(
                    paths.install_prefix / "bin" / "hakoniwa-pdu-web-bridge"
                ),
            )
            recipe.materialize_runtime(
                paths,
                drone_root,
                map_root,
                threejs_root,
                glb,
                "contract-only test fixture",
                runtime,
            )
            details = recipe.validate_materialization(paths, drone_root)
            self.assertEqual(set(details.values()), {"OK", "RadioController"})


if __name__ == "__main__":
    unittest.main()
