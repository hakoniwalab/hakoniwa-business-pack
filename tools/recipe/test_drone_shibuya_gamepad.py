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
    @contextlib.contextmanager
    def _mock_mjb_compiler(self):
        def compile_fixture(xml_path: Path, mjb_path: Path, library_path: Path):
            del library_path
            mjb_path.parent.mkdir(parents=True, exist_ok=True)
            mjb_path.write_bytes(b"compiled-mjb-fixture")
            return {
                "format": "mjb",
                "source_xml": str(xml_path.resolve()),
                "source_xml_sha256": recipe._sha256(xml_path),
                "output_mjb": str(mjb_path.resolve()),
                "output_mjb_sha256": recipe._sha256(mjb_path),
                "output_mjb_size_bytes": mjb_path.stat().st_size,
                "mujoco_version": "3.9.0-test",
                "mujoco_library": "/fixture/libmujoco",
                "mujoco_library_sha256": "fixture",
                "reload_validation": "passed",
                "compatibility": "test fixture",
            }

        with mock.patch.object(
            recipe, "find_mujoco_library", return_value=Path("/fixture/libmujoco")
        ), mock.patch.object(recipe, "compile_mujoco_xml", side_effect=compile_fixture):
            yield

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

        city_source_config = json.loads(json.dumps(source_config))
        city_source_config["simulation"]["timeStep"] = 0.001
        city_source_config["components"]["droneDynamics"]["position_meter"] = [
            0.0,
            0.0,
            -0.9,
        ]
        city_source_config["components"]["droneDynamics"]["mujoco"][
            "modelPath"
        ] = "config/drone/mujoco/drone.xml"
        city_source_config["controller"]["moduleName"] = "RadioController"
        city_source_config["controller"]["moduleDirectory"] = (
            "../drone_control/cmake-build/workspace/RadioController"
        )
        city_source_config["controller"]["paramFilePath"] = str(
            recipe.CITY_WORLD_CONTROLLER_PARAM
        )
        city_source_dir = self.drone_root / recipe.CITY_WORLD_SOURCE_DRONE_CONFIG
        self._write_json(city_source_dir / "drone_config_0.json", city_source_config)
        (city_source_dir / "drone.xml").write_text(
            '<mujoco><option timestep="0.001"/><default><default class="drone">'
            '<geom friction="0.5"/></default></default><asset/>'
            '<worldbody><geom name="ground" type="plane"/>'
            '<body name="drone_base" childclass="drone"><freejoint/>'
            '<geom name="base" type="box" size=".1 .1 .1"/></body>'
            '<body name="box"><freejoint/><geom name="box_geom" type="box" '
            'size=".1 .1 .1"/></body></worldbody></mujoco>\n',
            encoding="utf-8",
        )
        city_param = self.drone_root / recipe.CITY_WORLD_CONTROLLER_PARAM
        city_param.parent.mkdir(parents=True, exist_ok=True)
        city_param.write_text("generic controller\n", encoding="utf-8")
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
        legacy_viewer = json.loads(json.dumps(viewer))
        legacy_viewer["pdu"]["pduDefPath"] = "./pdudef.json"
        legacy_viewer["stateInput"]["mode"] = "legacy"
        self._write_json(
            self.threejs_root / "config" / "viewer-config-legacy.json",
            legacy_viewer,
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
            foundation_python=paths.foundation_python / "bin" / "python3",
            hako_cmd=paths.install_prefix / "bin" / "hako-cmd",
            web_bridge=paths.install_prefix / "bin" / "hakoniwa-pdu-web-bridge",
        )

    def _city_world_fixture(self, temporary: str):
        job = Path(temporary) / "city-job"
        world = job / "build" / "world"
        terrain = job / "build" / "components" / "terrain"
        world.mkdir(parents=True)
        terrain.mkdir(parents=True)
        (terrain / "terrain.hf").write_bytes(b"height-field")
        xml = world / "city-world.xml"
        xml.write_text(
            '<mujoco><asset><hfield name="plateau_terrain" '
            'file="../components/terrain/terrain.hf" size="10 10 1 1"/>'
            '</asset><worldbody><geom name="plateau_ground" type="hfield" '
            'hfield="plateau_terrain"/><body name="city_building">'
            '<geom name="city_box" type="box" size="1 1 1" '
            'contype="1" conaffinity="0"/></body></worldbody></mujoco>\n',
            encoding="utf-8",
        )
        glb = world / "city-world.glb"
        glb.write_bytes(b"city-world-glb")
        receipt = {
            "schema_version": 1,
            "coordinate_frame": {
                "origin": {
                    "latitude": 35.1,
                    "longitude": 139.2,
                    "altitude_offset_m": 12.5,
                },
                "half_extent_m": {"north_south": 10, "east_west": 10},
                "coordinate_systems": {
                    "mjcf": "X=North,Y=-East,Z=Up",
                    "glb": "X=East,Y=Up,Z=-North",
                },
            },
            "mjcf": {"path": str(xml), "sha256": recipe._sha256(xml)},
            "glb": {"path": str(glb), "sha256": recipe._sha256(glb)},
        }
        self._write_json(world / "city-world-receipt.json", receipt)
        return job

    def _materialize(self, temporary: str):
        self._fixture_sources(temporary)
        paths = self._paths(temporary)
        glb = Path(temporary) / recipe.GLB_NAME
        glb.write_bytes(b"test-shibuya-glb")
        with self._mock_mjb_compiler():
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

    def test_generated_city_world_is_composed_with_single_drone(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            self._fixture_sources(temporary)
            paths = self._paths(temporary)
            city_world = recipe._resolve_city_world(
                self._city_world_fixture(temporary)
            )
            with self._mock_mjb_compiler():
                record_path = recipe.materialize_runtime(
                    paths,
                    self.drone_root,
                    self.map_root,
                    self.threejs_root,
                    city_world.glb_path,
                    "unit-test City World receipt",
                    self._runtime(paths),
                    city_world=city_world,
                    spawn_altitude_m=20.0,
                )
            record = recipe._load_json(record_path)
            generated_dir = (
                paths.recipe_config
                / recipe.GENERATED_DRONE_CONFIG.relative_to("config")
            )
            generated_config = recipe._load_json(
                generated_dir / "drone_config_0.json"
            )
            xml_root = recipe.ET.parse(generated_dir / "drone.xml").getroot()

            self.assertEqual(record["mode"], "city-world")
            self.assertEqual(
                record["coordinate_invariants"]["plateau_map_origin"],
                {"latitude": 35.1, "longitude": 139.2},
            )
            self.assertEqual(
                generated_config["components"]["droneDynamics"]["position_meter"],
                [0.0, 0.0, -20.0],
            )
            self.assertEqual(
                generated_config["simulation"]["location"]["altitude"], 12.5
            )
            self.assertEqual(
                generated_config["components"]["droneDynamics"]["mujoco"]["modelPath"],
                str(recipe.GENERATED_MUJOCO_MJB),
            )
            self.assertTrue((generated_dir / "drone.mjb").is_file())
            self.assertIsNone(xml_root.find(".//geom[@name='ground']"))
            self.assertIsNotNone(xml_root.find(".//geom[@name='plateau_ground']"))
            self.assertIsNotNone(xml_root.find(".//body[@name='drone_base']"))
            drone_default = xml_root.find(
                "./default/default[@class='drone']/geom"
            )
            self.assertEqual(drone_default.get("contype"), "2")
            self.assertEqual(drone_default.get("conaffinity"), "1")
            hfield = xml_root.find("./asset/hfield[@name='plateau_terrain']")
            self.assertTrue(Path(hfield.get("file")).is_absolute())
            scene = recipe._load_json(
                Path(record["browser_bundle"]["scene_config"])
            )
            self.assertEqual(scene["environments"][0]["pos"], [0, 0, 0])
            self.assertEqual(scene["environments"][0]["hpr"], [0, 0, 0])
            self.assertEqual(
                scene["environments"][0]["model"],
                "../assets/local_models/city-world.glb",
            )
            details = recipe.validate_materialization(paths, self.drone_root)
            self.assertEqual(details["drone_xml_contract"], "OK")

    def test_collision_masks_allow_city_drone_but_not_drone_drone(self) -> None:
        city = (1, 0)
        drone = (2, 1)

        def can_collide(left, right):
            return bool((left[0] & right[1]) or (right[0] & left[1]))

        self.assertTrue(can_collide(city, drone))
        self.assertFalse(can_collide(drone, drone))

    def test_browser_bundle_uses_supplied_glb_and_legacy_config(self) -> None:
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
            self.assertEqual(viewer["stateInput"]["mode"], "legacy")
            self.assertEqual(viewer["pdu"]["pduDefPath"], "./pdudef.json")
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
                    "web-bridge-single-drone",
                    "remote-controller",
                    "map-viewer-webserver",
                ],
            )
            self.assertNotIn("hakoniwa-envsim", json.dumps(launcher).lower())
            self.assertIn(
                str(recipe.GENERATED_DRONE_CONFIG),
                launcher["assets"][0]["args"],
            )
            self.assertEqual(
                launcher["assets"][0]["delay_sec"],
                recipe.CITY_WORLD_DRONE_READY_DELAY_SEC,
            )
            self.assertNotIn("--mujoco-viewer", launcher["assets"][0]["args"])
            remote_controller = next(
                asset
                for asset in launcher["assets"]
                if asset["name"] == "remote-controller"
            )
            self.assertEqual(remote_controller["args"][0], "-u")
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

    def test_demo_readiness_requires_simulation_and_both_ports(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            paths = self._paths(temporary)
            log = paths.recipe_logs / "drone-service-1.out"
            log.write_text(
                "WAIT RUNNING\n",
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
