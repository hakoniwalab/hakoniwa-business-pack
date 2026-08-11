from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock


SCRIPT = Path(__file__).with_name("agilex_tracer_demo.py")
SPEC = importlib.util.spec_from_file_location("agilex_tracer_demo", SCRIPT)
assert SPEC and SPEC.loader
recipe = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = recipe
SPEC.loader.exec_module(recipe)


class AgileXTracerRecipeTest(unittest.TestCase):
    def workspace(self, root: Path):
        recipe_root = root / "work/recipes" / recipe.RECIPE_ID
        foundation = root / "work/foundation"
        return SimpleNamespace(
            recipe_root=recipe_root,
            recipe_config=recipe_root / "config",
            recipe_logs=recipe_root / "logs",
            install_prefix=foundation / "install",
            foundation_python=foundation / "install/python",
            foundation_config=foundation / "config",
        )

    def runtime(self, workspace):
        return recipe.Runtime(
            system_name="Darwin",
            python=workspace.foundation_python / "bin/python3",
            hako_cmd=workspace.install_prefix / "bin/hako-cmd",
            endpoint_library=workspace.install_prefix / "lib/libhakoniwa_pdu_endpoint_core_callback.dylib",
            check_binary=workspace.recipe_root / "build/mujoco/examples/actuators/agilex_tracer/agilex-tracer-rover-example",
            plant_binary=workspace.recipe_root / "build/mujoco/examples/actuators/agilex_tracer/rover-twist-hakoniwa-asset",
            sender=workspace.recipe_root / "assets/scripts/send_rover_twist.py",
        )

    def test_recipe_identity_is_runtime_specific(self):
        self.assertEqual(recipe.RECIPE_ID, "agilex-tracer-hakoniwa-runtime")

    def test_build_manifest_keeps_output_under_recipe_work(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "business-pack"
            workspace = self.workspace(root)
            mujoco = root.parent / "hakoniwa-mujoco-robots"
            path = recipe.write_build_manifest(workspace, mujoco)
            build_dir = path.read_text(encoding="utf-8").split("dir:", 1)[1].strip()
            self.assertEqual((mujoco / build_dir).resolve(), (workspace.recipe_root / "build/mujoco").resolve())

    def test_launcher_uses_foundation_python_and_recipe_owned_inputs(self):
        with tempfile.TemporaryDirectory() as temporary:
            workspace = self.workspace(Path(temporary) / "business-pack")
            runtime = self.runtime(workspace)
            path = recipe.write_launcher(workspace, runtime, headless=True)
            data = json.loads(path.read_text())
            assets = {asset["name"]: asset for asset in data["assets"]}
            self.assertEqual(assets["rover-twist-sender"]["command"], str(runtime.python))
            self.assertEqual(assets["rover-twist-plant"]["args"][0], "--no-viewer")
            self.assertTrue(Path(data["defaults"]["cwd"]).is_relative_to(workspace.recipe_root))
            self.assertTrue(Path(assets["rover-twist-sender"]["args"][0]).is_relative_to(workspace.recipe_root))
            self.assertNotIn("/usr/local/hakoniwa", json.dumps(data))

    def test_windows_native_build_args_propagate_foundation_and_vcpkg(self):
        with tempfile.TemporaryDirectory() as temporary:
            workspace = self.workspace(Path(temporary) / "business-pack")
            runtime = self.runtime(workspace)
            runtime = recipe.Runtime(**{**runtime.__dict__, "system_name": "Windows"})
            vcpkg = Path(temporary) / "vcpkg"
            (vcpkg / "scripts/buildsystems").mkdir(parents=True)
            (vcpkg / "scripts/buildsystems/vcpkg.cmake").write_text("", encoding="utf-8")
            (vcpkg / "installed/x64-windows").mkdir(parents=True)
            workspace.foundation_config.mkdir(parents=True)
            (workspace.foundation_config / "toolchain.json").write_text(
                json.dumps({"vcpkg_root": str(vcpkg)}), encoding="utf-8"
            )
            command = recipe.native_build_args(workspace, runtime)
            self.assertEqual(command[command.index("-HakoniwaCoreRoot") + 1], str(workspace.install_prefix))
            self.assertEqual(command[command.index("-HakoniwaPduEndpointRoot") + 1], str(workspace.install_prefix))
            self.assertEqual(command[command.index("-ToolchainFile") + 1], str(vcpkg / "scripts/buildsystems/vcpkg.cmake"))
            self.assertEqual(command[command.index("-ExtraPrefixPaths") + 1], str(vcpkg / "installed/x64-windows"))

    def test_stage_inputs_copies_only_into_recipe_workspace(self):
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            workspace = self.workspace(base / "business-pack")
            mbody = base / "hakoniwa-mbody-registry"
            mujoco = base / "hakoniwa-mujoco-robots"
            sources = [
                mbody / "bodies/agilex_tracer/generated/tracer_v1.minimal_world.xml",
                mbody / "bodies/agilex_tracer/generated/tracer_description/meshes/tracer_wheel.obj",
                mujoco / "examples/actuators/agilex_tracer/send_rover_twist.py",
                mujoco / "config/actuator/joint/agilex_tracer_left_wheel.json",
                mujoco / "config/actuator/joint/agilex_tracer_right_wheel.json",
                mujoco / "config/rover-twist-pdudef-compact.json",
                mujoco / "config/rover-twist-pdutypes.json",
                mujoco / "config/endpoint/rover_twist_endpoint.json",
                mujoco / "config/endpoint/cache/buffer.json",
                mujoco / "config/endpoint/comm/shm_rover_twist_comm.json",
            ]
            for source in sources:
                source.parent.mkdir(parents=True, exist_ok=True)
                source.write_text("{}\n", encoding="utf-8")
            recipe.stage_inputs(workspace, mbody, mujoco)
            for destination in recipe.runtime_inputs(workspace):
                self.assertTrue(destination.is_file())
                self.assertTrue(destination.is_relative_to(workspace.recipe_root))
            self.assertTrue(
                (
                    workspace.recipe_root
                    / "assets/models/agilex_tracer/generated/tracer_description/meshes/tracer_wheel.obj"
                ).is_file()
            )
            self.assertTrue((workspace.recipe_root / "assets/scripts/send_rover_twist.py").is_file())

    def test_launcher_control_uses_session_file_not_pid(self):
        with tempfile.TemporaryDirectory() as temporary:
            workspace = self.workspace(Path(temporary) / "business-pack")
            runtime = self.runtime(workspace)
            command = recipe.launcher_command(runtime, "terminate", workspace)
            self.assertEqual(command[-2], "terminate")
            self.assertEqual(Path(command[-1]), recipe.session_file(workspace))


if __name__ == "__main__":
    unittest.main()
