from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from tools import package_portable_workspace as portable


class PortableWorkspacePackageTest(unittest.TestCase):
    def test_windows_python_prefers_portable_root_executable(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "Scripts").mkdir()
            (root / "Scripts" / "python.exe").write_bytes(b"venv")
            self.assertEqual(
                portable._windows_python(root),
                root / "Scripts" / "python.exe",
            )
            (root / "python.exe").write_bytes(b"portable")
            self.assertEqual(portable._windows_python(root), root / "python.exe")

    def test_embedded_pth_enables_site_packages_and_site(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            path = root / "python312._pth"
            path.write_text(
                "python312.zip\n.\n# import site\n",
                encoding="utf-8",
            )
            portable._rewrite_embedded_python_pth(root)
            lines = path.read_text(encoding="utf-8").splitlines()
            self.assertIn(r"Lib\site-packages", lines)
            self.assertIn(r"..\..\..\..", lines)
            self.assertIn(r"..\..\..\..\..\hakoniwa-pdu-python\src", lines)
            self.assertIn(r"..\..\..\..\..\hakoniwa-envsim\tools", lines)
            self.assertIn(
                r"..\..\..\..\..\hakoniwa-envsim\src\city_pipeline", lines
            )
            self.assertIn("import site", lines)

    def test_absolute_windows_pth_entry_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            site_packages = Path(temporary)
            (site_packages / "bad.pth").write_text(
                "C:\\dev\\hakoniwa\\python\n",
                encoding="utf-8",
            )
            with self.assertRaises(portable.PortablePackageError):
                portable._reject_absolute_pth_entries(site_packages)

    def test_foundation_copy_excludes_rebuildable_build_tree(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary) / "foundation"
            (source / "build" / "deep" / "intermediate.txt").parent.mkdir(
                parents=True
            )
            (source / "build" / "deep" / "intermediate.txt").write_text("x")
            (source / "install" / "bin").mkdir(parents=True)
            (source / "install" / "bin" / "runtime.dll").write_bytes(b"runtime")
            destination = Path(temporary) / "package" / "foundation"
            portable._copy_foundation(source, destination)
            self.assertFalse((destination / "build").exists())
            self.assertTrue((destination / "install" / "bin" / "runtime.dll").is_file())

    def test_site_package_copy_excludes_python_bytecode_cache(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary) / "source-python"
            package = source / "Lib" / "site-packages" / "demo"
            (package / "__pycache__").mkdir(parents=True)
            (package / "__init__.py").write_text("", encoding="utf-8")
            (package / "__pycache__" / "demo.cpython-312.pyc").write_bytes(b"pyc")
            destination = Path(temporary) / "portable-python"
            portable._copy_site_packages(source, destination)
            self.assertTrue((destination / "Lib" / "site-packages" / "demo" / "__init__.py").is_file())
            self.assertFalse((destination / "Lib" / "site-packages" / "demo" / "__pycache__").exists())

    def test_site_package_copy_excludes_pip(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary) / "source-python"
            site_packages = source / "Lib" / "site-packages"
            (site_packages / "pip" / "_vendor").mkdir(parents=True)
            (site_packages / "pip" / "_vendor" / "installer.py").write_text(
                "", encoding="utf-8"
            )
            (site_packages / "pip-24.0.dist-info").mkdir()
            (site_packages / "runtime_module.py").write_text("", encoding="utf-8")
            destination = Path(temporary) / "portable-python"
            portable._copy_site_packages(source, destination)
            target = destination / "Lib" / "site-packages"
            self.assertTrue((target / "runtime_module.py").is_file())
            self.assertFalse((target / "pip").exists())
            self.assertFalse((target / "pip-24.0.dist-info").exists())

    def test_site_package_copy_excludes_setuptools(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary) / "source-python"
            site_packages = source / "Lib" / "site-packages"
            (site_packages / "setuptools" / "_vendor").mkdir(parents=True)
            (site_packages / "setuptools" / "_vendor" / "build_meta.py").write_text(
                "", encoding="utf-8"
            )
            (site_packages / "setuptools-70.0.dist-info").mkdir()
            destination = Path(temporary) / "portable-python"
            portable._copy_site_packages(source, destination)
            target = destination / "Lib" / "site-packages"
            self.assertFalse((target / "setuptools").exists())
            self.assertFalse((target / "setuptools-70.0.dist-info").exists())

    def test_site_package_copy_excludes_wheel_sbom(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary) / "source-python"
            site_packages = source / "Lib" / "site-packages"
            distribution = site_packages / "pydantic_core-2.46.5.dist-info"
            (distribution / "sboms").mkdir(parents=True)
            (distribution / "METADATA").write_text("Name: pydantic_core\n")
            (distribution / "sboms" / "pydantic-core.cyclonedx.json").write_text(
                "{}\n", encoding="utf-8"
            )
            destination = Path(temporary) / "portable-python"
            portable._copy_site_packages(source, destination)
            target = destination / "Lib" / "site-packages" / distribution.name
            self.assertTrue((target / "METADATA").is_file())
            self.assertFalse((target / "sboms").exists())

    def test_foundation_runtime_package_is_copied_beside_portable_python(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary) / "source-python"
            endpoint = source / "hakoniwa_pdu_endpoint"
            endpoint.mkdir(parents=True)
            (endpoint / "c_endpoint.pyd").write_bytes(b"native extension")
            extra = source / "hakoniwa_example"
            extra.mkdir()
            (extra / "extension.pyd").write_bytes(b"native extension")
            destination = Path(temporary) / "portable-python"
            destination.mkdir()
            portable._copy_foundation_python_runtime_packages(source, destination)
            self.assertTrue(
                (destination / "hakoniwa_pdu_endpoint" / "c_endpoint.pyd").is_file()
            )
            self.assertTrue((destination / "hakoniwa_example" / "extension.pyd").is_file())

    def test_start_script_uses_packaged_python_and_web_ui_recipe(self) -> None:
        script = portable._render_start_batch()
        self.assertIn(
            r"work\foundation\install\python\python.exe",
            script,
        )
        self.assertIn(
            r"-m tools.remote_operation.city_world.launcher start",
            script,
        )
        self.assertIn(r"tools\workspace.py run --", script)
        self.assertIn(r"work\foundation\install\bin", script)
        self.assertIn(
            r'--launcher-runtime-dir "%BUSINESS_PACK%\work\recipes\city-world-web-ui\launcher"',
            script,
        )
        self.assertIn("--open-browser", script)

    def test_control_scripts_use_same_portable_runtime(self) -> None:
        for command in ("status", "stop"):
            script = portable._render_control_batch(command)
            self.assertIn(
                rf"-m tools.remote_operation.city_world.launcher {command}",
                script,
            )
            self.assertIn(r"tools\workspace.py run --", script)
            self.assertIn(
                r"work\foundation\install\python\python.exe",
                script,
            )

    def test_urban_profile_declares_runtime_repositories(self) -> None:
        profile = portable.load_profile("urban-car-rc", portable.WORKSPACE_ROOT)
        names = {item.name for item in profile.repositories}
        self.assertEqual(profile.recipe_id, "urban-car-rc")
        self.assertIn("hakoniwa-urban-mobility", names)
        self.assertIn("hakoniwa-mbody-registry", names)
        self.assertIn("hakoniwa-pdu-python", names)
        self.assertIn("hakoniwa-pdu-python/src", profile.python_paths)
        self.assertIn(
            "hakoniwa-urban-mobility/apps/car", profile.python_paths
        )

    def test_urban_start_relocates_before_start(self) -> None:
        script = portable._render_urban_batch("start")
        prepare = r"tools\portable_urban_car.py prepare"
        start = r"tools\urban_mobility.py start"
        self.assertIn(prepare, script)
        self.assertIn(start, script)
        self.assertLess(script.index(prepare), script.index(start))
        self.assertIn(r"work\foundation\install\python\python.exe", script)
        self.assertIn(
            r'set "HAKONIWA_WORK_DIR=%BUSINESS_PACK%\work"', script
        )
        self.assertIn(
            r'set "HAKO_CONFIG_PATH=%BUSINESS_PACK%\work\foundation\config\cpp_core_config.json"',
            script,
        )

    def test_urban_control_scripts_override_ambient_workspace(self) -> None:
        for command in ("status", "stop"):
            script = portable._render_urban_batch(command)
            self.assertIn(
                r'set "HAKONIWA_WORK_DIR=%BUSINESS_PACK%\work"', script
            )
            self.assertIn(
                r'set "HAKONIWA_HOME=%BUSINESS_PACK%\work\foundation\install"',
                script,
            )

    def _write_repository_profile(self, workspace: Path, **overrides) -> Path:
        owner = workspace / "hakoniwa-example-app"
        (owner / "portable").mkdir(parents=True)
        payload = {
            "schema_version": 1,
            "id": "example-app",
            "package_id": "hakoniwa-example-app-windows-x64",
            "recipe_id": "example-recipe",
            "title": "Example App",
            "tool": "tools/example_portable.py",
            "entrypoint_name": "example-app",
            "readme": "portable/README-WINDOWS.txt",
            "repositories": [
                {
                    "name": "hakoniwa-example-app",
                    "required_artifact": "tools/example_portable.py",
                    "include_paths": ["tools", "portable"],
                }
            ],
            "python_paths": ["hakoniwa-business-pack"],
            "validation_imports": ["yaml"],
            "staging_cleanup": ["hakoniwa-example-app/build/generated"],
        }
        payload.update(overrides)
        path = owner / "portable" / "windows-profile.json"
        path.write_text(json.dumps(payload), encoding="utf-8")
        return path

    def test_repository_profile_is_discovered_from_sibling(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            workspace = Path(temporary)
            self._write_repository_profile(workspace)
            profile = portable.load_profile("example-app", workspace)
            self.assertEqual(profile.kind, "repository")
            self.assertEqual(profile.package_id, "hakoniwa-example-app-windows-x64")
            self.assertEqual(profile.repositories[0].path, workspace / "hakoniwa-example-app")
            self.assertEqual(profile.repository_tool.repository, "hakoniwa-example-app")
            self.assertEqual(profile.repository_tool.validation_imports, ("yaml",))
            self.assertEqual(profile.requirements, ())
            # Built-in profiles stay available beside repository profiles.
            self.assertEqual(
                portable.load_profile("urban-car-rc", workspace).kind, "urban-car-rc"
            )

    def test_repository_profile_rejects_paths_outside_the_package(self) -> None:
        for override in (
            {"tool": "../escape.py"},
            {"staging_cleanup": ["/abs/path"]},
            {"python_paths": ["..\\escape"]},
            {"repositories": [{"name": "other", "required_artifact": "x", "include_paths": []}]},
            {"schema_version": 2},
        ):
            with self.subTest(override=override), tempfile.TemporaryDirectory() as temporary:
                workspace = Path(temporary)
                self._write_repository_profile(workspace, **override)
                with self.assertRaises(ValueError):
                    portable.load_profile("example-app", workspace)

    def test_repository_entrypoints_select_packaged_workspace(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            workspace = Path(temporary)
            self._write_repository_profile(workspace)
            profile = portable.load_profile("example-app", workspace)
        self.assertEqual(
            portable._repository_entrypoints(profile),
            {
                "start": "start-example-app.bat",
                "status": "status-example-app.bat",
                "stop": "stop-example-app.bat",
            },
        )
        for command in ("start", "status", "stop"):
            script = portable._render_repository_batch(profile, command)
            self.assertIn(
                rf'"%HAKO_PYTHON%" tools\example_portable.py {command}', script
            )
            self.assertIn(r'set "APP_ROOT=%PACKAGE_ROOT%hakoniwa-example-app"', script)
            self.assertIn('set "HAKONIWA_PORTABLE_WORKSPACE=1"', script)
            self.assertIn(
                r'set "HAKO_CONFIG_PATH=%BUSINESS_PACK%\work\foundation\config\cpp_core_config.json"',
                script,
            )
            self.assertIn('set "PYTHONPATH="', script)

    def test_repository_staging_cleanup_drops_generated_state(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            workspace = Path(temporary)
            self._write_repository_profile(workspace)
            profile = portable.load_profile("example-app", workspace)
            package_root = workspace / "package"
            foundation = package_root / "hakoniwa-business-pack/work/foundation"
            (foundation / "config").mkdir(parents=True)
            (foundation / "config/cpp_core_config.json").write_text(
                json.dumps({"shm_type": "mmap", "core_mmap_path": "C:/staging/mmap"}),
                encoding="utf-8",
            )
            (foundation / "runtime/mmap").mkdir(parents=True)
            (foundation / "runtime/mmap/mmap-0x100.bin").write_bytes(b"x")
            generated = package_root / "hakoniwa-example-app/build/generated"
            generated.mkdir(parents=True)
            (generated / "launcher.json").write_text("{}", encoding="utf-8")

            portable._remove_staging_specific_workspace_files(package_root, profile)

            self.assertFalse(generated.exists())
            self.assertFalse((foundation / "runtime/mmap").exists())
            payload = json.loads(
                (foundation / "config/cpp_core_config.json").read_text(encoding="utf-8")
            )
            self.assertEqual(payload["core_mmap_path"], portable.PORTABLE_MMAP_TOKEN)

    def test_profile_controls_default_output_name(self) -> None:
        args = portable.parser().parse_args(["--profile", "urban-car-rc"])
        self.assertIsNone(args.output)
        profile = portable.load_profile(args.profile, portable.WORKSPACE_ROOT)
        self.assertEqual(profile.package_id, "hakoniwa-urban-car-rc-windows-x64")


if __name__ == "__main__":
    unittest.main()
