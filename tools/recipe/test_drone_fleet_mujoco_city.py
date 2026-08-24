from __future__ import annotations

import importlib.util
import json
import tempfile
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path


SCRIPT = Path(__file__).with_name("drone_fleet_mujoco_city.py")
SPEC = importlib.util.spec_from_file_location("drone_fleet_mujoco_city", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
recipe = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(recipe)


class FleetMujocoCityTest(unittest.TestCase):
    def _fake_drone_root(self, root: Path) -> Path:
        drone_root = root / "hakoniwa-drone-pro"
        types = drone_root / "config" / "drone" / "fleets" / "types"
        tools = drone_root / "tools"
        types.mkdir(parents=True)
        tools.mkdir(parents=True)
        (tools / "gen_mujoco_multidrone_xml.py").write_text(
            """
def generate_xml(scene, drone, count):
    bodies = []
    for index in range(1, count + 1):
        bodies.append(drone.replace('__ID__', str(index)))
    return scene.replace('__DRONE_BODIES__', '\\n'.join(bodies))
""".strip()
            + "\n",
            encoding="utf-8",
        )
        (types / "mujoco-scene.xml.template").write_text(
            """<mujoco>
  <size nstack="1" nconmax="1"/>
  <default><default class="drone"><geom density="1"/></default></default>
  <asset/>
  <worldbody>
    <geom name="ground" type="plane" size="1 1 .1"/>
    <body name="landmark_box_1"><geom type="box" size="1 1 1"/></body>
    __DRONE_BODIES__
  </worldbody>
</mujoco>
""",
            encoding="utf-8",
        )
        (types / "mujoco-drone.xml.template").write_text(
            '<body name="d__ID___b_drone_base" childclass="drone">'
            '<freejoint/><geom type="box" size=".1 .1 .1"/></body>\n',
            encoding="utf-8",
        )
        return drone_root

    def test_base_fleet_has_deterministic_names_and_collision_mask(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            drone_root = self._fake_drone_root(root)
            output = root / "fleet.xml"
            recipe._generate_base_fleet_xml(drone_root, 2, output)
            record = recipe._prepare_base_fleet_xml(output, 2)
            tree = ET.parse(output)
            model = tree.getroot()
            self.assertEqual(
                record["drone_body_names"],
                ["d1_b_drone_base", "d2_b_drone_base"],
            )
            self.assertEqual(record["removed_demo_landmarks"], ["landmark_box_1"])
            self.assertEqual(model.find("size").attrib, recipe.MODEL_SIZE)
            default = model.find("./default/default[@class='drone']/geom")
            self.assertEqual(default.get("contype"), "2")
            self.assertEqual(default.get("conaffinity"), "1")
            self.assertIsNone(model.find("./worldbody/body[@name='landmark_box_1']"))

    def test_invalid_drone_count_is_rejected_before_city_resolution(self) -> None:
        with self.assertRaisesRegex(recipe.FleetMujocoError, r"\[1, 200\]"):
            recipe.build_shared_model(
                drone_root=Path("missing"),
                city_world_path=Path("missing"),
                drone_count=0,
                output_dir=Path("missing"),
            )

    def test_safe_spawn_selection_rejects_building_and_keeps_separation(self) -> None:
        def terrain_height(_x: float, _y: float) -> float:
            return 5.0

        def city_height(x: float, y: float) -> float:
            # Simulate a building occupying the center launch footprint.
            return 15.0 if abs(x) < 1.0 and abs(y) < 1.0 else 5.0

        points = recipe._select_safe_spawn_points(
            drone_count=2,
            half_extent_m={"north_south": 20.0, "east_west": 20.0},
            terrain_height=terrain_height,
            city_height=city_height,
        )
        self.assertNotEqual((points[0]["x_m"], points[0]["y_m"]), (0.0, 0.0))
        separation = (
            (points[0]["x_m"] - points[1]["x_m"]) ** 2
            + (points[0]["y_m"] - points[1]["y_m"]) ** 2
        ) ** 0.5
        self.assertGreaterEqual(separation, recipe.SPAWN_MIN_SEPARATION_M)

    def test_formation_targets_are_resolved_in_city_local_frame(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            formation = root / "formation.json"
            formation.write_text(
                json.dumps({"id": "demo", "points": [[1.0, 2.0, 0.0]]}),
                encoding="utf-8",
            )
            show_path = root / "show.json"
            show = {
                "options": {"center": [10.0, 20.0, 0.0], "scale": 2.0},
                "formation_files": [{"id": "demo", "path": "formation.json"}],
            }
            show_path.write_text(json.dumps(show), encoding="utf-8")
            self.assertEqual(
                recipe._formation_targets(show, show_path=show_path),
                [(12.0, 24.0)],
            )


if __name__ == "__main__":
    unittest.main()
