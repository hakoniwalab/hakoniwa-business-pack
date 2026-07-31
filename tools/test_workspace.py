#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
import venv
from pathlib import Path
from unittest import mock

WORKSPACE_SCRIPT = Path(__file__).with_name("workspace.py")
SPEC = importlib.util.spec_from_file_location("business_pack_workspace", WORKSPACE_SCRIPT)
assert SPEC is not None and SPEC.loader is not None
workspace = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = workspace
SPEC.loader.exec_module(workspace)

BOOTSTRAP_SCRIPT = (
    Path(__file__).resolve().parent.parent
    / "foundation"
    / "python"
    / "hakoniwa_workspace_bootstrap.py"
)
BOOTSTRAP_SPEC = importlib.util.spec_from_file_location(
    "business_pack_workspace_bootstrap",
    BOOTSTRAP_SCRIPT,
)
assert BOOTSTRAP_SPEC is not None and BOOTSTRAP_SPEC.loader is not None
bootstrap = importlib.util.module_from_spec(BOOTSTRAP_SPEC)
BOOTSTRAP_SPEC.loader.exec_module(bootstrap)


class WorkspaceEnvironmentTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name) / "business-pack"
        self.root.mkdir(parents=True)
        bootstrap_source = self.root / "foundation" / "python"
        bootstrap_source.mkdir(parents=True)
        shutil.copyfile(
            BOOTSTRAP_SCRIPT,
            bootstrap_source / "hakoniwa_workspace_bootstrap.py",
        )
        self.paths = workspace.resolve_workspace(self.root)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_layout_stays_under_business_pack_work(self) -> None:
        self.assertEqual(
            self.paths.foundation_root,
            self.root.resolve() / "work" / "foundation",
        )
        for path in (
            self.paths.install_prefix,
            self.paths.foundation_python_root,
            self.paths.foundation_config,
            self.paths.activate_posix,
            self.paths.activate_powershell,
        ):
            self.assertTrue(path.is_relative_to(self.root.resolve() / "work"))

    def test_environment_removes_ambient_python_discovery(self) -> None:
        base = {
            "PATH": os.pathsep.join(("/legacy/bin", "/another/bin")),
            "PYTHONPATH": "/legacy/hakopy",
            "PYTHONHOME": "/legacy/python",
            "HOME": "/home/test",
        }

        env = workspace.build_environment(self.paths, base)

        self.assertNotIn("PYTHONPATH", env)
        self.assertNotIn("PYTHONHOME", env)
        self.assertEqual(env["PYTHONNOUSERSITE"], "1")
        self.assertEqual(env["HAKONIWA_WORKSPACE_ACTIVE"], "1")
        self.assertEqual(env["HAKONIWA_WORKSPACE_ROOT"], str(self.root.resolve()))
        self.assertEqual(env["HAKONIWA_HOME"], str(self.paths.install_prefix))
        self.assertEqual(
            env["HAKO_PDU_ENDPOINT_RUNTIME_DIRS"],
            str(self.paths.foundation_bin),
        )
        self.assertEqual(env["VIRTUAL_ENV"], str(self.paths.foundation_python_root))
        path_entries = env["PATH"].split(os.pathsep)
        self.assertEqual(path_entries[0], str(self.paths.foundation_python_bin))
        self.assertEqual(path_entries[1], str(self.paths.foundation_bin))
        self.assertEqual(path_entries.count(str(self.paths.foundation_bin)), 1)
        self.assertEqual(env["HOME"], "/home/test")

    def test_prepare_generates_both_activation_surfaces(self) -> None:
        posix, powershell = workspace.prepare(self.paths)

        self.assertTrue(posix.is_file())
        self.assertTrue(powershell.is_file())
        posix_text = posix.read_text(encoding="utf-8")
        powershell_text = powershell.read_text(encoding="utf-8")
        self.assertIn("unset PYTHONPATH", posix_text)
        self.assertIn("unset PYTHONHOME", posix_text)
        self.assertIn("deactivate_hakoniwa", posix_text)
        self.assertIn(str(self.paths.foundation_python_bin), posix_text)
        self.assertIn("HAKO_PDU_ENDPOINT_RUNTIME_DIRS", posix_text)
        self.assertIn("Remove-Item Env:\\PYTHONPATH", powershell_text)
        self.assertIn("Remove-Item Env:\\PYTHONHOME", powershell_text)
        self.assertIn("Exit-HakoniwaWorkspace", powershell_text)
        self.assertIn(str(self.paths.foundation_python_bin), powershell_text)
        self.assertIn("HAKO_PDU_ENDPOINT_RUNTIME_DIRS", powershell_text)

    def test_prepare_installs_foundation_python_bootstrap_pth(self) -> None:
        site_packages = (
            self.paths.foundation_python_root
            / "lib"
            / "python3.12"
            / "site-packages"
        )
        site_packages.mkdir(parents=True)

        workspace.prepare(self.paths)

        pth_path = site_packages / "hakoniwa_workspace_bootstrap.pth"
        self.assertTrue(pth_path.is_file())
        self.assertEqual(
            pth_path.read_text(encoding="utf-8"),
            (
                f"{self.paths.business_pack_root / 'foundation' / 'python'}\n"
                "import hakoniwa_workspace_bootstrap\n"
            ),
        )

    def test_windows_site_packages_layout_is_detected(self) -> None:
        site_packages = (
            self.paths.foundation_python_root / "Lib" / "site-packages"
        )
        site_packages.mkdir(parents=True)

        self.assertEqual(
            workspace._foundation_site_package_dirs(
                self.paths,
                windows=True,
            ),
            [site_packages],
        )

    @unittest.skipUnless(
        os.name != "nt" and shutil.which("bash"),
        "POSIX bash is required",
    )
    def test_posix_activation_restores_previous_environment(self) -> None:
        workspace.prepare(self.paths)
        command = f"""
set -eu
export PYTHONPATH=/legacy/hakopy
export PYTHONHOME=/legacy/python
export HAKO_CONFIG_PATH=/legacy/config.json
export HAKO_PDU_ENDPOINT_RUNTIME_DIRS=/legacy/runtime
. {self.paths.activate_posix}
[ -z "${{PYTHONPATH+x}}" ]
[ -z "${{PYTHONHOME+x}}" ]
[ "$HAKONIWA_WORKSPACE_ACTIVE" = 1 ]
[ "$HAKO_CONFIG_PATH" = "{self.paths.foundation_config}" ]
[ "$HAKO_PDU_ENDPOINT_RUNTIME_DIRS" = "{self.paths.foundation_bin}" ]
deactivate_hakoniwa
[ "$PYTHONPATH" = /legacy/hakopy ]
[ "$PYTHONHOME" = /legacy/python ]
[ "$HAKO_CONFIG_PATH" = /legacy/config.json ]
[ "$HAKO_PDU_ENDPOINT_RUNTIME_DIRS" = /legacy/runtime ]
[ -z "${{HAKONIWA_WORKSPACE_ACTIVE+x}}" ]
"""
        result = subprocess.run(
            ["bash", "-c", command],
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    @unittest.skipUnless(
        os.name != "nt" and shutil.which("bash"),
        "POSIX bash is required",
    )
    def test_posix_activation_does_not_add_empty_library_path_entry(self) -> None:
        with mock.patch.object(workspace.platform, "system", return_value="Linux"):
            workspace.prepare(self.paths)
        command = f"""
set -eu
unset LD_LIBRARY_PATH
. {self.paths.activate_posix} >/dev/null
[ "$LD_LIBRARY_PATH" = "{self.paths.foundation_lib}" ]
"""
        result = subprocess.run(
            ["bash", "-c", command],
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_snapshot_validation_accepts_workspace_owned_modules(self) -> None:
        snapshot = {
            "executable": str(self.paths.foundation_python),
            "prefix": str(self.paths.foundation_python_root),
            "modules": {
                "hakopy": str(self.paths.foundation_core_python / "hakopy.so"),
                "hakoniwa_pdu": {
                    "file": None,
                    "search_locations": [
                        str(
                            self.paths.foundation_python_root
                            / "lib"
                            / "python3.12"
                            / "site-packages"
                            / "hakoniwa_pdu"
                        )
                    ],
                },
                "hakoniwa_pdu_endpoint": str(
                    self.paths.foundation_python_root
                    / "lib"
                    / "python3.12"
                    / "site-packages"
                    / "hakoniwa_pdu_endpoint"
                    / "__init__.py"
                ),
            },
            "errors": {},
        }

        self.assertEqual(workspace.validate_snapshot(snapshot, self.paths), [])

    @unittest.skipUnless(os.name != "nt", "POSIX venv symlink behavior")
    def test_snapshot_validation_accepts_venv_python_symlink(self) -> None:
        system_python = self.root.parent / "python3.12"
        system_python.write_text("", encoding="utf-8")
        self.paths.foundation_python.parent.mkdir(parents=True)
        self.paths.foundation_python.symlink_to(system_python)
        snapshot = {
            "executable": str(self.paths.foundation_python),
            "prefix": str(self.paths.foundation_python_root),
            "modules": {
                "hakopy": str(self.paths.foundation_core_python / "hakopy.so"),
                "hakoniwa_pdu": str(
                    self.paths.foundation_python_root / "site-packages" / "hakoniwa_pdu"
                ),
                "hakoniwa_pdu_endpoint": str(
                    self.paths.foundation_python_root
                    / "site-packages"
                    / "hakoniwa_pdu_endpoint"
                ),
            },
            "errors": {},
        }

        self.assertEqual(workspace.validate_snapshot(snapshot, self.paths), [])

    def test_runtime_snapshot_with_real_venv_and_namespace_package(self) -> None:
        venv.EnvBuilder(with_pip=False).create(self.paths.foundation_python_root)
        self.paths.foundation_bin.mkdir(parents=True)
        site_packages_dirs = workspace._foundation_site_package_dirs(self.paths)
        self.assertEqual(len(site_packages_dirs), 1)
        site_packages = site_packages_dirs[0]
        (site_packages / "hakopy.py").write_text(
            "VALUE = 'foundation'\n",
            encoding="utf-8",
        )
        (site_packages / "hakoniwa_pdu").mkdir()
        endpoint_package = site_packages / "hakoniwa_pdu_endpoint"
        endpoint_package.mkdir()
        (endpoint_package / "__init__.py").write_text(
            "VALUE = 'foundation'\n",
            encoding="utf-8",
        )
        workspace.prepare(self.paths)

        snapshot = workspace.runtime_snapshot(self.paths)

        self.assertEqual(workspace.validate_snapshot(snapshot, self.paths), [])
        pdu_info = snapshot["modules"]["hakoniwa_pdu"]
        self.assertIsNone(pdu_info["file"])
        self.assertEqual(
            pdu_info["search_locations"],
            [str(site_packages / "hakoniwa_pdu")],
        )

    def test_snapshot_validation_rejects_shadowed_hakopy(self) -> None:
        snapshot = {
            "executable": str(self.paths.foundation_python),
            "prefix": str(self.paths.foundation_python_root),
            "modules": {
                "hakopy": str(self.root.parent / "legacy" / "hakopy.pyd"),
                "hakoniwa_pdu": str(
                    self.paths.foundation_python_root / "site-packages" / "hakoniwa_pdu.py"
                ),
                "hakoniwa_pdu_endpoint": str(
                    self.paths.foundation_python_root
                    / "site-packages"
                    / "hakoniwa_pdu_endpoint.py"
                ),
            },
            "errors": {},
        }

        errors = workspace.validate_snapshot(snapshot, self.paths)

        self.assertTrue(any("hakopy resolved outside" in error for error in errors))

    def test_interactive_shell_commands_disable_profiles(self) -> None:
        self.assertEqual(
            workspace._interactive_shell_command("/bin/bash"),
            ["/bin/bash", "--noprofile", "--norc", "-i"],
        )
        self.assertEqual(
            workspace._interactive_shell_command("/bin/zsh"),
            ["/bin/zsh", "-f", "-i"],
        )
        self.assertEqual(
            workspace._interactive_shell_command("pwsh.exe"),
            ["pwsh.exe", "-NoLogo", "-NoProfile", "-NoExit"],
        )
        self.assertEqual(
            workspace._interactive_shell_command("cmd.exe"),
            ["cmd.exe", "/d"],
        )

    def test_windows_bootstrap_registers_foundation_dll_directory(self) -> None:
        self.paths.foundation_bin.mkdir(parents=True)
        calls: list[str] = []
        handles: list[object] = []

        def _add_directory(path: str) -> object:
            calls.append(path)
            handle = object()
            handles.append(handle)
            return handle

        registered = bootstrap.activate(
            {
                "HAKONIWA_WORKSPACE_ACTIVE": "1",
                "HAKONIWA_HOME": str(self.paths.install_prefix),
                "HAKO_PDU_ENDPOINT_RUNTIME_DIRS": str(self.paths.foundation_bin),
            },
            platform_name="win32",
            add_directory=_add_directory,
        )

        self.assertEqual(registered, [str(self.paths.foundation_bin.resolve())])
        self.assertEqual(calls, registered)
        self.assertEqual(len(handles), 1)

    def test_windows_bootstrap_is_inactive_outside_workspace(self) -> None:
        self.paths.foundation_bin.mkdir(parents=True)
        calls: list[str] = []

        registered = bootstrap.activate(
            {
                "HAKONIWA_HOME": str(self.paths.install_prefix),
                "HAKO_PDU_ENDPOINT_RUNTIME_DIRS": str(self.paths.foundation_bin),
            },
            platform_name="win32",
            add_directory=lambda path: calls.append(path),
        )

        self.assertEqual(registered, [])
        self.assertEqual(calls, [])

    @unittest.skipUnless(
        os.name == "nt" and hasattr(os, "add_dll_directory"),
        "native Windows DLL directories are required",
    )
    def test_windows_bootstrap_uses_native_add_dll_directory(self) -> None:
        self.paths.foundation_bin.mkdir(parents=True)
        previous_count = len(bootstrap._DLL_DIRECTORY_HANDLES)

        registered = bootstrap.activate(
            {
                "HAKONIWA_WORKSPACE_ACTIVE": "1",
                "HAKONIWA_HOME": str(self.paths.install_prefix),
                "HAKO_PDU_ENDPOINT_RUNTIME_DIRS": str(self.paths.foundation_bin),
            },
        )

        self.assertEqual(registered, [str(self.paths.foundation_bin.resolve())])
        handles = bootstrap._DLL_DIRECTORY_HANDLES[previous_count:]
        self.assertEqual(len(handles), 1)
        for handle in handles:
            handle.close()
        del bootstrap._DLL_DIRECTORY_HANDLES[previous_count:]

    @unittest.skipUnless(
        os.name == "nt" and (shutil.which("pwsh") or shutil.which("powershell.exe")),
        "PowerShell on Windows is required",
    )
    def test_powershell_activation_restores_previous_environment(self) -> None:
        workspace.prepare(self.paths)
        powershell = shutil.which("pwsh") or shutil.which("powershell.exe")
        activation = str(self.paths.activate_powershell).replace("'", "''")
        expected_runtime = str(self.paths.foundation_bin).replace("'", "''")
        script = f"""
$ErrorActionPreference = 'Stop'
$env:PYTHONPATH = '/legacy/hakopy'
$env:PYTHONHOME = '/legacy/python'
$env:HAKO_PDU_ENDPOINT_RUNTIME_DIRS = '/legacy/runtime'
. '{activation}'
if (Test-Path Env:\\PYTHONPATH) {{ exit 11 }}
if (Test-Path Env:\\PYTHONHOME) {{ exit 12 }}
if ($env:HAKONIWA_WORKSPACE_ACTIVE -ne '1') {{ exit 13 }}
if ($env:HAKO_PDU_ENDPOINT_RUNTIME_DIRS -ne '{expected_runtime}') {{ exit 14 }}
Exit-HakoniwaWorkspace
if ($env:PYTHONPATH -ne '/legacy/hakopy') {{ exit 15 }}
if ($env:PYTHONHOME -ne '/legacy/python') {{ exit 16 }}
if ($env:HAKO_PDU_ENDPOINT_RUNTIME_DIRS -ne '/legacy/runtime') {{ exit 17 }}
if (Test-Path Env:\\HAKONIWA_WORKSPACE_ACTIVE) {{ exit 18 }}
"""
        env = dict(os.environ)
        env.pop("HAKONIWA_WORKSPACE_ACTIVE", None)
        result = subprocess.run(
            [powershell, "-NoLogo", "-NoProfile", "-Command", script],
            env=env,
            check=False,
            capture_output=True,
            text=True,
        )

        self.assertEqual(result.returncode, 0, result.stderr)

    @unittest.skipUnless(
        os.name != "nt" and shutil.which("zsh"),
        "POSIX zsh is required",
    )
    def test_zsh_workspace_entry_does_not_load_polluting_zshrc(self) -> None:
        config_home = self.root / "zsh"
        config_home.mkdir()
        (config_home / ".zshrc").write_text(
            "export PYTHONPATH=/legacy/hakopy\n",
            encoding="utf-8",
        )
        env = workspace.build_environment(
            self.paths,
            {
                "PATH": os.environ.get("PATH", ""),
                "SHELL": shutil.which("zsh") or "zsh",
                "ZDOTDIR": str(config_home),
            },
        )
        command = [
            *workspace._interactive_shell_command(env["SHELL"]),
            "-c",
            "test -z \"${PYTHONPATH+x}\"",
        ]

        result = subprocess.run(
            command,
            env=env,
            check=False,
            capture_output=True,
            text=True,
        )

        self.assertEqual(result.returncode, 0, result.stderr)

    def test_run_command_does_not_inherit_pythonpath(self) -> None:
        script = "import os, sys; sys.exit(0 if 'PYTHONPATH' not in os.environ else 9)"
        with mock.patch.dict(os.environ, {"PYTHONPATH": "/legacy"}, clear=False):
            result = workspace.run_command(
                self.paths,
                [sys.executable, "-c", script],
            )
        self.assertEqual(result, 0)


if __name__ == "__main__":
    unittest.main()
