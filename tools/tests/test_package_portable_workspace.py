from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.append(str(Path(__file__).resolve().parents[1]))
from workspace_test_isolation import isolate_from_active_workspace  # noqa: E402

isolate_from_active_workspace()

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

    def test_start_script_delegates_portable_lifecycle_to_runtime_module(self) -> None:
        script = portable._render_start_batch()
        self.assertIn(
            r"work\foundation\install\python\python.exe",
            script,
        )
        self.assertIn(
            r'tools\portable_workspace_runtime.py start-city-world --root "%BUSINESS_PACK%"',
            script,
        )
        self.assertNotIn("HAKONIWA_WORK_DIR", script)
        self.assertNotIn("PYTHONPATH", script)
        self.assertNotIn("tools\workspace.py run", script)
        self.assertNotIn("tools.remote_operation.city_world.launcher", script)

    def test_control_scripts_delegate_to_portable_runtime_module(self) -> None:
        for command in ("status", "stop"):
            script = portable._render_control_batch(command)
            self.assertIn(
                rf'tools\portable_workspace_runtime.py {command}-city-world --root "%BUSINESS_PACK%"',
                script,
            )
            self.assertIn(
                r"work\foundation\install\python\python.exe",
                script,
            )
            self.assertNotIn("HAKONIWA_WORK_DIR", script)
            self.assertNotIn("PYTHONPATH", script)
            self.assertNotIn("tools\workspace.py run", script)

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
            {"staging_cleanup": ["C:/abs/path"]},
            {"readme": "D:relative-to-drive.txt"},
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
            self.assertIn('set "HAKONIWA_WORKSPACE_ACTIVE=1"', script)
            self.assertIn(
                r'set "HAKO_CONFIG_PATH=%BUSINESS_PACK%\work\foundation\config\cpp_core_config.json"',
                script,
            )
            self.assertIn('set "PYTHONPATH="', script)

    def test_city_world_staged_validation_uses_only_packaged_environment(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            package_root = Path(temporary) / "package"
            business_pack = package_root / "hakoniwa-business-pack"
            python = business_pack / "work/foundation/install/python/python.exe"
            python.parent.mkdir(parents=True)
            python.write_bytes(b"python")
            required = (
                package_root / "hakoniwa-envsim/tools/hako.py",
                package_root / "hakoniwa-pdu-javascript/src/index.js",
                package_root
                / "hakoniwa-pdu-python/src/hakoniwa_pdu/apps/launcher/hako_launcher.py",
            )
            for path in required:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("", encoding="utf-8")

            receipts = (
                business_pack
                / "work/foundation/install/share/hakoniwa/receipts"
            )
            receipts.mkdir(parents=True)
            (receipts / "component.yaml").write_text(
                "schema_version: 1\n"
                "component:\n"
                "  id: component\n"
                "install:\n"
                "  prefix: old\n",
                encoding="utf-8",
            )

            calls = []

            def record(command, cwd, label, env=None):
                calls.append((command, cwd, label, env))

            with mock.patch.dict(
                portable.os.environ,
                {
                    "HAKONIWA_WORK_DIR": "C:/source/work",
                    "HAKONIWA_HOME": "C:/source/install",
                    "PYTHONPATH": "C:/source/python",
                },
                clear=False,
            ), mock.patch.object(
                portable, "_run_checked", side_effect=record
            ), mock.patch.object(
                portable, "_materialize_web_ui_recipe"
            ) as materialize:
                portable._validate_staged_package(package_root)

            self.assertEqual(len(calls), 3)
            expected_work = str((business_pack / "work").resolve())
            for _command, cwd, _label, env in calls:
                self.assertEqual(cwd, business_pack)
                self.assertEqual(env["HAKONIWA_WORK_DIR"], expected_work)
                self.assertNotEqual(env["HAKONIWA_WORK_DIR"], "C:/source/work")
                self.assertNotIn("PYTHONPATH", env)
            materialize.assert_called_once()
            materialize_env = materialize.call_args.args[2]
            self.assertEqual(materialize_env["HAKONIWA_WORK_DIR"], expected_work)
            self.assertNotIn("PYTHONPATH", materialize_env)

    def test_city_world_staging_cleanup_drops_generated_recipe_runtime(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            package_root = Path(temporary) / "package"
            generated = (
                package_root
                / "hakoniwa-business-pack"
                / "work"
                / "recipes"
                / "city-world-web-ui"
            )
            generated.mkdir(parents=True)
            (generated / "environment.json").write_text(
                '{"RECIPE_REPOSITORY": "C:/staging"}',
                encoding="utf-8",
            )
            profile = portable.load_profile(
                "city-world-web-ui", portable.WORKSPACE_ROOT
            )

            portable._remove_staging_specific_workspace_files(
                package_root, profile
            )

            self.assertFalse(generated.exists())

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

    def test_staging_path_budget_rejects_paths_over_windows_max_path(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            site = Path(temporary) / "site-packages"
            deep = site / "pkg" / ("m" * 40 + ".py")
            deep.parent.mkdir(parents=True)
            deep.write_text("x", encoding="utf-8")
            self.assertEqual(
                portable._longest_relative_path(site), (len("pkg\\" + "m" * 40 + ".py"), "pkg\\" + "m" * 40 + ".py")
            )
            portable._require_staging_path_budget(site, Path("C:/short"))
            with self.assertRaisesRegex(portable.PortablePackageError, "Windows limit"):
                portable._require_staging_path_budget(site, Path("C:/" + "s" * 220))

    def test_staging_path_budget_skips_trees_the_copy_excludes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            site = Path(temporary) / "site-packages"
            (site / "pkg").mkdir(parents=True)
            (site / "pkg" / "m.py").write_text("x", encoding="utf-8")
            for excluded in ("pkg/__pycache__", "pip", "demo-1.0.dist-info/sboms"):
                deep = site / excluded / ("n" * 200 + ".pyc")
                deep.parent.mkdir(parents=True)
                deep.write_text("x", encoding="utf-8")
            portable._require_staging_path_budget(site, Path("C:/" + "s" * 200))

    def test_extraction_budget_accounts_for_package_folder(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            package_root = Path(temporary) / "pkg"
            (package_root / "a").mkdir(parents=True)
            (package_root / "a" / "b.txt").write_text("x", encoding="utf-8")
            # An older package left beside it must not affect the budget.
            older = Path(temporary) / "older-package-with-a-long-name" / "deep" / ("x" * 80)
            older.parent.mkdir(parents=True)
            older.write_text("x", encoding="utf-8")
            budget = portable._report_extraction_budget(package_root)
            self.assertEqual(budget, portable.WINDOWS_MAX_PATH_CHARS - len("pkg\\a\\b.txt") - 1)

    def test_profile_controls_default_output_name(self) -> None:
        args = portable.parser().parse_args(["--profile", "urban-car-rc"])
        self.assertIsNone(args.output)
        profile = portable.load_profile(args.profile, portable.WORKSPACE_ROOT)
        self.assertEqual(profile.package_id, "hakoniwa-urban-car-rc-windows-x64")


if __name__ == "__main__":
    unittest.main()
