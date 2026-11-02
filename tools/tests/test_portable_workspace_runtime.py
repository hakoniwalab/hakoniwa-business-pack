from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.append(str(Path(__file__).resolve().parents[1]))
from workspace_test_isolation import isolate_from_active_workspace  # noqa: E402

isolate_from_active_workspace()

from tools import portable_workspace_runtime as runtime


class PortableWorkspaceRuntimeTest(unittest.TestCase):
    def test_portable_environment_overrides_ambient_workspace(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "hakoniwa-business-pack"
            env = runtime.portable_environment(
                root,
                base={
                    "PATH": "host-path",
                    "PYTHONPATH": "host-python",
                    "PYTHONHOME": "host-home",
                    "HAKONIWA_WORK_DIR": "C:/source/work",
                    "HAKONIWA_HOME": "C:/source/install",
                },
            )
            resolved = root.resolve()
            self.assertNotIn("PYTHONPATH", env)
            self.assertNotIn("PYTHONHOME", env)
            self.assertEqual(
                env["HAKONIWA_WORK_DIR"], str((resolved / "work").resolve())
            )
            self.assertEqual(
                env["HAKONIWA_HOME"],
                str((resolved / "work/foundation/install").resolve()),
            )
            self.assertEqual(env["HAKONIWA_WORKSPACE_ROOT"], str(resolved))
            self.assertEqual(env["HAKONIWA_PORTABLE_WORKSPACE"], "1")
            self.assertTrue(
                env["PATH"].startswith(
                    str((resolved / "work/foundation/install/python").resolve())
                    + os.pathsep
                )
            )

    def test_foundation_receipts_are_relocated_to_current_prefix(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "package" / "hakoniwa-business-pack"
            receipts = (
                root
                / "work"
                / "foundation"
                / "install"
                / "share"
                / "hakoniwa"
                / "receipts"
            )
            receipts.mkdir(parents=True)
            for name in ("hakoniwa-core-pro", "hakoniwa-pdu-endpoint"):
                (receipts / f"{name}.yaml").write_text(
                    "schema_version: 1\n"
                    "component:\n"
                    f"  id: {name}\n"
                    "install:\n"
                    "  prefix: \"C:\\\\source\\\\foundation\\\\install\"\n"
                    "capabilities: {}\n",
                    encoding="utf-8",
                )

            count = runtime.relocate_foundation_receipts(root)

            self.assertEqual(count, 2)
            expected = str((root / "work/foundation/install").resolve())
            for path in receipts.glob("*.yaml"):
                text = path.read_text(encoding="utf-8")
                self.assertIn(f"  prefix: {json.dumps(expected)}", text)
                self.assertNotIn("C:\\\\source", text)

    def test_prepare_and_doctor_runs_in_order(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "hakoniwa-business-pack"
            python = root / "work/foundation/install/python/python.exe"
            python.parent.mkdir(parents=True)
            python.write_bytes(b"python")
            receipts = root / "work/foundation/install/share/hakoniwa/receipts"
            receipts.mkdir(parents=True)
            (receipts / "component.yaml").write_text(
                "schema_version: 1\n"
                "component:\n"
                "  id: component\n"
                "install:\n"
                "  prefix: old\n",
                encoding="utf-8",
            )

            completed = mock.Mock(returncode=0)
            with mock.patch.object(runtime.subprocess, "run", return_value=completed) as run:
                runtime.prepare_and_doctor(
                    root,
                    configure_command=("configure-tool.py", "configure"),
                    doctor_command=("configure-tool.py", "doctor"),
                    extra_environment={"PORTABLE_PROFILE": "example"},
                    label="example portable profile",
                )

            commands = [call.args[0][1:] for call in run.call_args_list]
            self.assertEqual(
                commands,
                [
                    ["tools/workspace.py", "prepare"],
                    ["configure-tool.py", "configure"],
                    ["configure-tool.py", "doctor"],
                ],
            )
            for call in run.call_args_list:
                self.assertEqual(call.kwargs["env"]["PORTABLE_PROFILE"], "example")

    def test_city_world_start_runs_prepare_doctor_before_launcher(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "hakoniwa-business-pack"
            python = root / "work/foundation/install/python/python.exe"
            python.parent.mkdir(parents=True)
            python.write_bytes(b"python")
            env = runtime.portable_environment(root)
            env["HAKONIWA_PORTABLE_CITY_WORLD"] = "1"

            with mock.patch.object(
                runtime,
                "prepare_city_world",
                return_value=(python.resolve(), env),
            ) as prepare, mock.patch.object(runtime, "_run") as run:
                runtime.run_city_world(root, "start")

            prepare.assert_called_once_with(root.resolve())
            command = run.call_args.args[0]
            self.assertEqual(command[0], str(python.resolve()))
            self.assertEqual(
                command[1:5],
                ["-m", "tools.remote_operation.city_world.launcher", "start", "--launcher-runtime-dir"],
            )
            self.assertIn("--runtime-dir", command)
            self.assertIn("--parallel-workers", command)
            self.assertIn("--terrain-spacing-m", command)
            self.assertIn("--open-browser", command)
            self.assertEqual(
                run.call_args.args[2]["HAKONIWA_WORK_DIR"],
                str((root / "work").resolve()),
            )

    def test_city_world_status_does_not_reconfigure(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "hakoniwa-business-pack"
            python = root / "work/foundation/install/python/python.exe"
            python.parent.mkdir(parents=True)
            python.write_bytes(b"python")

            with mock.patch.object(runtime, "prepare_city_world") as prepare, mock.patch.object(
                runtime, "_run"
            ) as run:
                runtime.run_city_world(root, "status")

            prepare.assert_not_called()
            command = run.call_args.args[0]
            self.assertEqual(command[0], str(python.resolve()))
            self.assertEqual(
                command[1:4],
                ["-m", "tools.remote_operation.city_world.launcher", "status"],
            )
            self.assertNotIn("--runtime-dir", command)

    def test_prepare_city_world_relocates_before_configure(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "hakoniwa-business-pack"
            python = root / "work/foundation/install/python/python.exe"
            python.parent.mkdir(parents=True)
            python.write_bytes(b"python")
            receipts = (
                root
                / "work/foundation/install/share/hakoniwa/receipts"
            )
            receipts.mkdir(parents=True)
            (receipts / "hakoniwa-pdu-endpoint.yaml").write_text(
                "schema_version: 1\n"
                "component:\n"
                "  id: hakoniwa-pdu-endpoint\n"
                "install:\n"
                "  prefix: old\n"
                "capabilities: {}\n",
                encoding="utf-8",
            )

            completed = mock.Mock(returncode=0)
            with mock.patch.object(runtime.subprocess, "run", return_value=completed) as run:
                runtime.prepare_city_world(root)

            self.assertEqual(run.call_count, 3)
            prepare = run.call_args_list[0]
            configure = run.call_args_list[1]
            doctor = run.call_args_list[2]
            self.assertEqual(
                prepare.args[0],
                [str(python.resolve()), "tools/workspace.py", "prepare"],
            )
            self.assertEqual(
                configure.args[0],
                [
                    str(python.resolve()),
                    "tools/recipe/city_world_web_ui.py",
                    "configure",
                ],
            )
            self.assertEqual(
                doctor.args[0],
                [
                    str(python.resolve()),
                    "tools/recipe/city_world_web_ui.py",
                    "doctor",
                ],
            )
            self.assertEqual(
                prepare.kwargs["env"]["HAKONIWA_WORK_DIR"],
                str((root / "work").resolve()),
            )
            self.assertEqual(
                prepare.kwargs["env"]["HAKONIWA_PORTABLE_CITY_WORLD"], "1"
            )
            self.assertEqual(prepare.kwargs["cwd"], root.resolve())
            self.assertEqual(configure.kwargs["cwd"], root.resolve())
            self.assertEqual(doctor.kwargs["cwd"], root.resolve())


if __name__ == "__main__":
    unittest.main()
