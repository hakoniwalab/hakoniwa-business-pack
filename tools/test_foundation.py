#!/usr/bin/env python3
from __future__ import annotations

import json
import importlib.util
import io
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path, PureWindowsPath
from unittest import mock

FOUNDATION_SCRIPT = Path(__file__).with_name("foundation.py")
SPEC = importlib.util.spec_from_file_location("business_pack_foundation", FOUNDATION_SCRIPT)
assert SPEC is not None and SPEC.loader is not None
foundation = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = foundation
SPEC.loader.exec_module(foundation)


class FoundationWorkspaceTest(unittest.TestCase):
    def test_doctor_warns_about_workspace_and_continues(self) -> None:
        inspection = {"status": "SATISFIED", "components": [], "runtime": {}}
        with mock.patch.object(foundation, "warn_if_workspace_invalid") as warning:
            with mock.patch.object(
                foundation, "inspect_foundation", return_value=inspection
            ):
                with mock.patch.object(foundation, "print_inspection"):
                    result = foundation.main(["doctor", "--recipe", "recipe.yaml"])

        self.assertEqual(result, 0)
        warning.assert_called_once_with(foundation.repository_root())

    def test_prepare_does_not_warn_about_workspace(self) -> None:
        with mock.patch.object(foundation, "warn_if_workspace_invalid") as warning:
            with mock.patch.object(foundation, "prepare_workspace"):
                with mock.patch.object(foundation, "print_paths"):
                    result = foundation.main(["prepare", "--recipe-id", "test"])

        self.assertEqual(result, 0)
        warning.assert_not_called()

    def test_resolve_workspace_stays_under_business_pack_work(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "business-pack"
            paths = foundation.resolve_workspace(root, "drone-threejs")
            resolved_root = root.resolve()

            self.assertEqual(
                paths.foundation_root, resolved_root / "work" / "foundation"
            )
            self.assertEqual(
                paths.recipe_root,
                resolved_root / "work" / "recipes" / "drone-threejs",
            )
            self.assertEqual(
                paths.foundation_python,
                resolved_root / "work" / "foundation" / "install" / "python",
            )
            for directory in paths.directories():
                self.assertTrue(directory.is_relative_to(resolved_root / "work"))
                value = str(directory)
                self.assertNotIn("/usr/local", value)
                self.assertNotIn("/etc/hakoniwa", value)
                self.assertNotIn("/var/lib/hakoniwa", value)

    def test_windows_layout_uses_the_same_relative_contract(self) -> None:
        root = PureWindowsPath("C:/work/hakoniwa-business-pack")

        self.assertEqual(
            root / "work" / "foundation" / "install",
            PureWindowsPath(
                "C:/work/hakoniwa-business-pack/work/foundation/install"
            ),
        )
        self.assertEqual(
            root / "work" / "recipes" / "drone-threejs" / "config",
            PureWindowsPath(
                "C:/work/hakoniwa-business-pack/work/recipes/drone-threejs/config"
            ),
        )

    def test_prepare_is_idempotent_and_does_not_create_run_directories(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "business-pack"
            paths = foundation.resolve_workspace(root, "drone-threejs")

            foundation.prepare_workspace(paths)
            first = sorted(
                path.relative_to(root).as_posix()
                for path in (root / "work").rglob("*")
                if path.is_dir()
            )
            foundation.prepare_workspace(paths)
            second = sorted(
                path.relative_to(root).as_posix()
                for path in (root / "work").rglob("*")
                if path.is_dir()
            )

            self.assertEqual(first, second)
            self.assertFalse(any("run-" in path for path in second))

    def test_rejects_unsafe_recipe_id(self) -> None:
        with self.assertRaises(foundation.FoundationError):
            foundation.resolve_workspace(Path("/tmp/example"), "../other")

    def test_rejects_foundation_override_outside_business_pack_work(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "business-pack"
            with self.assertRaises(foundation.FoundationError):
                foundation.resolve_workspace(
                    root,
                    "test",
                    root.parent / "outside",
                )

    def test_cli_paths_json_does_not_create_workspace(self) -> None:
        script = Path(foundation.__file__).resolve()
        result = subprocess.run(
            [
                sys.executable,
                str(script),
                "paths",
                "--recipe-id",
                "drone-threejs",
                "--json",
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        data = json.loads(result.stdout)

        self.assertTrue(data["foundation_root"].endswith("work/foundation"))
        self.assertTrue(
            data["foundation_python"].endswith("work/foundation/install/python")
        )
        self.assertTrue(data["recipe_root"].endswith("work/recipes/drone-threejs"))


class FoundationPythonContractTest(unittest.TestCase):
    def test_accepts_cpython_312_with_foundation_prefix(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            python_root = Path(temporary) / "install" / "python"
            probe = {
                "implementation": "CPython",
                "executable": str(python_root / "bin" / "python"),
                "prefix": str(python_root),
                "version": "3.12.10",
                "major": 3,
                "minor": 12,
                "soabi": "cpython-312-test",
                "extension_suffix": ".cpython-312-test.so",
            }

            foundation.validate_foundation_python_probe(probe, python_root)

    def test_rejects_python_313_and_314(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            python_root = Path(temporary) / "install" / "python"
            for minor in (13, 14):
                with self.subTest(minor=minor):
                    probe = {
                        "implementation": "CPython",
                        "executable": str(python_root / "bin" / "python"),
                        "prefix": str(python_root),
                        "version": f"3.{minor}.0",
                        "major": 3,
                        "minor": minor,
                        "soabi": f"cpython-3{minor}-test",
                        "extension_suffix": f".cpython-3{minor}-test.so",
                    }
                    with self.assertRaisesRegex(
                        foundation.FoundationError,
                        "required=3.12",
                    ):
                        foundation.validate_foundation_python_probe(
                            probe, python_root
                        )

    def test_missing_foundation_python_is_reported_without_mutation(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            prefix = Path(temporary) / "install"

            result = foundation.inspect_foundation_python(prefix)

            self.assertEqual(result["status"], "MISSING")
            self.assertFalse(prefix.exists())

    def test_python_313_fails_before_creating_foundation_workspace(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            paths = foundation.resolve_workspace(root, "test")
            recipe = root / "recipe.yaml"
            with mock.patch.object(
                foundation.platform,
                "python_implementation",
                return_value="CPython",
            ), mock.patch.object(foundation.sys, "version_info", (3, 13, 0)):
                with self.assertRaisesRegex(
                    foundation.FoundationError,
                    "must be created with CPython 3.12",
                ):
                    foundation.ensure_foundation_python(paths, recipe)

            self.assertFalse(paths.work_root.exists())

    def test_build_catalog_declares_the_runtime_python_contract(self) -> None:
        catalog = foundation.load_build_catalog(
            FOUNDATION_SCRIPT.parent.parent / "catalog" / "foundation-components.json"
        )

        self.assertIn("hakoniwa-core-pro", catalog)

    def test_python_probe_strips_windows_extension_from_soabi_fallback(self) -> None:
        completed = subprocess.CompletedProcess(
            args=[], returncode=0,
            stdout=json.dumps({
                "implementation": "CPython", "executable": "python.exe", "prefix": "python",
                "version": "3.12.10", "major": 3, "minor": 12,
                "soabi": "", "extension_suffix": ".cp312-win_amd64.pyd",
            }),
            stderr="",
        )
        with mock.patch.object(foundation.subprocess, "run", return_value=completed):
            probe = foundation.probe_python(Path("python.exe"))
        self.assertEqual(probe["soabi"], "cp312-win_amd64")

    def test_untagged_extension_does_not_invent_soabi(self) -> None:
        completed = subprocess.CompletedProcess(
            args=[], returncode=0,
            stdout=json.dumps({
                "implementation": "CPython", "executable": "python", "prefix": "python",
                "version": "3.12.10", "major": 3, "minor": 12,
                "soabi": "", "extension_suffix": ".so",
            }),
            stderr="",
        )
        with mock.patch.object(foundation.subprocess, "run", return_value=completed):
            probe = foundation.probe_python(Path("python"))
        self.assertFalse(probe["soabi"])

    def test_toolchain_selection_is_persisted_under_foundation_config(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            paths = foundation.resolve_workspace(root, "test-recipe")
            vcpkg = root / "external" / "vcpkg"
            vcpkg.mkdir(parents=True)
            (vcpkg / ("vcpkg.exe" if sys.platform == "win32" else "vcpkg")).write_text("test\n", encoding="utf-8")
            cmake = vcpkg / "scripts" / "buildsystems" / "vcpkg.cmake"
            cmake.parent.mkdir(parents=True)
            cmake.write_text("# test\n", encoding="utf-8")
            output = foundation.configure_foundation_toolchain(paths, vcpkg)
            self.assertEqual(output, paths.foundation_config / "toolchain.json")
            self.assertEqual(foundation.load_foundation_toolchain(paths)["vcpkg_root"], str(vcpkg.resolve()))


class FoundationInspectorTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.prefix = self.root / "install"
        self.recipe = self.root / "recipe.yaml"
        self.python_patcher = mock.patch.object(
            foundation,
            "inspect_foundation_python",
            return_value={
                "status": "SATISFIED",
                "executable": str(self.prefix / "python" / "bin" / "python"),
                "version": "3.12.10",
                "soabi": "cpython-312-test",
                "reason": None,
            },
        )
        self.python_patcher.start()

    def tearDown(self) -> None:
        self.python_patcher.stop()
        self.temporary.cleanup()

    def write_recipe(self, body: str) -> None:
        self.recipe.write_text(
            "id: test\nfoundation_requirements:\n" + body,
            encoding="utf-8",
        )

    def write_receipt(
        self,
        component: str,
        *,
        capabilities: str = "  available: true\n",
        build_limits: str = "{}",
        dependencies: str = "{}",
        version: str = "1.0.0",
        platform_os: str | None = None,
        artifact: str = "lib/component.marker",
        create_artifact: bool = True,
    ) -> None:
        os_name, architecture = foundation._host_contract()
        if component == "hakoniwa-core-pro" and artifact == "lib/component.marker":
            artifact = "share/hakoniwa/python/hakopy.cpython-312-test.so"
        resolved = (
            self.prefix
            / "share"
            / "hakoniwa"
            / "receipts"
            / "resolved"
            / f"{component}.yaml"
        )
        resolved.parent.mkdir(parents=True, exist_ok=True)
        resolved.write_text("version: 1\n", encoding="utf-8")
        if create_artifact:
            artifact_path = self.prefix / artifact
            artifact_path.parent.mkdir(parents=True, exist_ok=True)
            artifact_path.write_text("installed\n", encoding="utf-8")
        receipt = resolved.parent.parent / f"{component}.yaml"
        python_receipt = (
            "python:\n"
            "  binding_mode: soabi\n"
            "  implementation: CPython\n"
            "  version: 3.12.10\n"
            "  major: 3\n"
            "  minor: 12\n"
            "  soabi: cpython-312-test\n"
            "  extension_suffix: .cpython-312-test.so\n"
            f"  artifact: {artifact}\n"
            if component == "hakoniwa-core-pro"
            else ""
        )
        receipt.write_text(
            "schema_version: 1\n"
            "component:\n"
            f"  id: {component}\n"
            f'  version: "{version}"\n'
            '  source_revision: "revision-current"\n'
            "platform:\n"
            f'  os: "{platform_os or os_name}"\n'
            f'  architecture: "{architecture}"\n'
            '  toolchain: "test"\n'
            "install:\n"
            f'  prefix: "{self.prefix}"\n'
            "capabilities:\n"
            f"{capabilities}"
            f"build_limits: {build_limits}\n"
            f"dependencies: {dependencies}\n"
            "artifacts:\n"
            f'  - path: "{artifact}"\n'
            "    kind: test\n"
            f"{python_receipt}"
            "resolved_manifest: "
            f'"share/hakoniwa/receipts/resolved/{component}.yaml"\n',
            encoding="utf-8",
        )

    def test_satisfied(self) -> None:
        self.write_recipe(
            "  component-a:\n"
            "    capabilities:\n"
            "      available: true\n"
        )
        self.write_receipt("component-a")

        result = foundation.inspect_foundation(self.recipe, self.prefix)

        self.assertEqual(result["status"], "SATISFIED")
        self.assertEqual(result["components"][0]["reasons"], [])

    def test_minimum_component_version_is_enforced(self) -> None:
        self.write_recipe(
            "  component-a:\n"
            "    version:\n"
            "      min: 1.6.5\n"
            "    capabilities:\n"
            "      available: true\n"
        )
        self.write_receipt("component-a", version="1.6.4")

        result = foundation.inspect_foundation(self.recipe, self.prefix)

        self.assertEqual(result["status"], "INCOMPATIBLE")
        reason = result["components"][0]["reasons"][0]
        self.assertEqual(reason["field"], "component.version.min")
        self.assertEqual(reason["required"], "1.6.5")
        self.assertEqual(reason["installed"], "1.6.4")

    def test_minimum_component_version_accepts_newer_numeric_version(self) -> None:
        self.write_recipe(
            "  component-a:\n"
            "    version:\n"
            "      min: 1.6.5\n"
            "    capabilities:\n"
            "      available: true\n"
        )
        self.write_receipt("component-a", version="1.6.10")

        result = foundation.inspect_foundation(self.recipe, self.prefix)

        self.assertEqual(result["status"], "SATISFIED")

    def test_missing_when_neither_receipt_nor_known_artifact_exists(self) -> None:
        self.write_recipe(
            "  hakoniwa-core-pro:\n"
            "    capabilities:\n"
            "      shared_memory: true\n"
        )

        result = foundation.inspect_foundation(self.recipe, self.prefix)

        self.assertEqual(result["status"], "MISSING")

    def test_unknown_when_known_artifact_exists_without_receipt(self) -> None:
        self.write_recipe(
            "  hakoniwa-core-pro:\n"
            "    capabilities:\n"
            "      shared_memory: true\n"
        )
        binary = self.prefix / "bin" / "hako-cmd"
        binary.parent.mkdir(parents=True)
        binary.write_text("legacy install\n", encoding="utf-8")

        result = foundation.inspect_foundation(self.recipe, self.prefix)

        self.assertEqual(result["status"], "UNKNOWN")
        self.assertEqual(
            result["components"][0]["reasons"][0]["field"], "receipt"
        )

    def test_shared_python_runtime_does_not_imply_pdu_python_install(self) -> None:
        self.write_recipe(
            "  hakoniwa-pdu-python:\n"
            "    capabilities:\n"
            "      shm_backend: true\n"
        )
        (self.prefix / "python" / "Lib" / "site-packages").mkdir(parents=True)
        result = foundation.inspect_foundation(self.recipe, self.prefix)
        self.assertEqual(result["components"][0]["status"], "MISSING")

    def test_pdu_owned_package_without_receipt_is_unknown(self) -> None:
        self.write_recipe(
            "  hakoniwa-pdu-python:\n"
            "    capabilities:\n"
            "      shm_backend: true\n"
        )
        package = self.prefix / "python" / "Lib" / "site-packages" / "hakoniwa_pdu"
        package.mkdir(parents=True)
        result = foundation.inspect_foundation(self.recipe, self.prefix)
        self.assertEqual(result["components"][0]["status"], "UNKNOWN")

    def test_malformed_receipt_is_unknown_instead_of_crashing(self) -> None:
        self.write_recipe(
            "  component-a:\n"
            "    capabilities:\n"
            "      available: true\n"
        )
        receipt = (
            self.prefix
            / "share"
            / "hakoniwa"
            / "receipts"
            / "component-a.yaml"
        )
        receipt.parent.mkdir(parents=True)
        receipt.write_text(
            "schema_version: 1\n"
            "component: invalid\n"
            "platform: invalid\n"
            "install: invalid\n"
            "capabilities: invalid\n"
            "build_limits: invalid\n"
            "dependencies: invalid\n"
            "artifacts: invalid\n"
            "resolved_manifest: invalid\n",
            encoding="utf-8",
        )

        result = foundation.inspect_foundation(self.recipe, self.prefix)

        self.assertEqual(result["status"], "UNKNOWN")
        self.assertEqual(
            result["components"][0]["reasons"][0]["field"],
            "receipt.schema",
        )

    def test_incompatible_reports_capability_limit_platform_and_artifact(self) -> None:
        self.write_recipe(
            "  component-a:\n"
            "    capabilities:\n"
            "      required_feature: true\n"
            "    build_limits:\n"
            "      asset_num:\n"
            "        min: 256\n"
        )
        self.write_receipt(
            "component-a",
            capabilities="  required_feature: false\n",
            build_limits="\n  asset_num: 16",
            platform_os="other-os",
            create_artifact=False,
        )

        result = foundation.inspect_foundation(self.recipe, self.prefix)
        fields = {
            reason["field"] for reason in result["components"][0]["reasons"]
        }

        self.assertEqual(result["status"], "INCOMPATIBLE")
        self.assertIn("capabilities.required_feature", fields)
        self.assertIn("build_limits.asset_num.min", fields)
        self.assertIn("platform.os", fields)
        self.assertIn("artifacts.lib/component.marker", fields)

    def test_build_limit_without_nested_integer_min_is_actionable(self) -> None:
        self.write_recipe(
            "  component-a:\n"
            "    build_limits:\n"
            "      asset_num: {min: 16}\n"
        )
        self.write_receipt("component-a", build_limits="\n  asset_num: 16")

        with self.assertRaisesRegex(
            foundation.FoundationError,
            "build_limits.asset_num.min must be an integer",
        ):
            foundation.inspect_foundation(self.recipe, self.prefix)

    def test_core_receipt_soabi_must_match_foundation_python(self) -> None:
        self.write_recipe(
            "  hakoniwa-core-pro:\n"
            "    capabilities:\n"
            "      shared_memory: true\n"
        )
        self.write_receipt(
            "hakoniwa-core-pro",
            capabilities="  shared_memory: true\n",
        )
        receipt = (
            self.prefix
            / "share"
            / "hakoniwa"
            / "receipts"
            / "hakoniwa-core-pro.yaml"
        )
        receipt.write_text(
            receipt.read_text(encoding="utf-8").replace(
                "soabi: cpython-312-test",
                "soabi: cpython-313-test",
            ),
            encoding="utf-8",
        )

        result = foundation.inspect_foundation(self.recipe, self.prefix)
        core = result["components"][0]

        self.assertEqual(result["status"], "INCOMPATIBLE")
        self.assertIn(
            {
                "field": "python.soabi",
                "required": "cpython-312-test",
                "installed": "cpython-313-test",
            },
            core["reasons"],
        )

    def test_core_receipt_rejects_legacy_untagged_hakopy_alongside_soabi_artifact(self) -> None:
        self.write_recipe(
            "  hakoniwa-core-pro:\n"
            "    capabilities:\n"
            "      shared_memory: true\n"
        )
        self.write_receipt(
            "hakoniwa-core-pro",
            capabilities="  shared_memory: true\n",
        )
        legacy = self.prefix / "share/hakoniwa/python/hakopy.so"
        legacy.write_text("legacy\n", encoding="utf-8")

        result = foundation.inspect_foundation(self.recipe, self.prefix)
        core = result["components"][0]

        self.assertEqual(result["status"], "INCOMPATIBLE")
        self.assertTrue(
            any(reason["field"] == "python.artifacts.unique" for reason in core["reasons"])
        )

    def test_core_receipt_rejects_python_artifact_not_derived_from_extension_suffix(self) -> None:
        self.write_recipe(
            "  hakoniwa-core-pro:\n"
            "    capabilities:\n"
            "      shared_memory: true\n"
        )
        self.write_receipt(
            "hakoniwa-core-pro",
            capabilities="  shared_memory: true\n",
        )
        receipt = self.prefix / "share/hakoniwa/receipts/hakoniwa-core-pro.yaml"
        receipt.write_text(
            receipt.read_text(encoding="utf-8").replace(
                "artifact: share/hakoniwa/python/hakopy.cpython-312-test.so",
                "artifact: share/hakoniwa/python/hakopy.so",
            ),
            encoding="utf-8",
        )

        result = foundation.inspect_foundation(self.recipe, self.prefix)
        core = result["components"][0]

        self.assertEqual(result["status"], "INCOMPATIBLE")
        self.assertTrue(
            any(reason["field"] == "python.artifact" for reason in core["reasons"])
        )

    def test_false_capability_requires_installed_false(self) -> None:
        self.write_recipe(
            "  component-a:\n"
            "    capabilities:\n"
            "      optional_feature: false\n"
        )
        self.write_receipt(
            "component-a",
            capabilities="  optional_feature: true\n",
        )

        result = foundation.inspect_foundation(self.recipe, self.prefix)

        self.assertEqual(result["status"], "INCOMPATIBLE")
        self.assertEqual(
            result["components"][0]["reasons"][0],
            {
                "field": "capabilities.optional_feature",
                "required": False,
                "installed": True,
            },
        )

    def test_dependency_receipt_mismatch_is_incompatible(self) -> None:
        self.write_recipe(
            "  component-a:\n"
            "    capabilities:\n"
            "      available: true\n"
        )
        self.write_receipt("dependency-a")
        self.write_receipt(
            "component-a",
            dependencies=(
                "\n"
                "  dependency-a:\n"
                '    version: "0.9.0"\n'
                '    source_revision: "revision-old"\n'
            ),
        )

        result = foundation.inspect_foundation(self.recipe, self.prefix)
        fields = {
            reason["field"] for reason in result["components"][0]["reasons"]
        }

        self.assertEqual(result["status"], "INCOMPATIBLE")
        self.assertIn("dependencies.dependency-a.version", fields)
        self.assertIn("dependencies.dependency-a.source_revision", fields)

    def test_aggregate_status_uses_actionable_priority(self) -> None:
        self.write_recipe(
            "  component-a:\n"
            "    capabilities:\n"
            "      available: true\n"
            "  missing-component:\n"
            "    capabilities:\n"
            "      available: true\n"
        )
        self.write_receipt(
            "component-a",
            capabilities="  available: false\n",
        )

        result = foundation.inspect_foundation(self.recipe, self.prefix)

        self.assertEqual(result["status"], "INCOMPATIBLE")
        self.assertEqual(
            [component["status"] for component in result["components"]],
            ["INCOMPATIBLE", "MISSING"],
        )

    def build_catalog(self) -> dict[str, dict]:
        return {
            "hakoniwa-core-pro": {
                "source": "../hakoniwa-core-pro",
                "dependencies": [],
                "operations": ["doctor", "build", "install"],
            },
            "hakoniwa-pdu-endpoint": {
                "source": "../hakoniwa-pdu-endpoint",
                "dependencies": ["hakoniwa-core-pro"],
                "operations": ["doctor", "configure", "build", "install"],
            },
            "hakoniwa-pdu-bridge-core": {
                "source": "../hakoniwa-pdu-bridge-core",
                "dependencies": [
                    "hakoniwa-core-pro",
                    "hakoniwa-pdu-endpoint",
                ],
                "operations": ["doctor", "configure", "build", "install"],
            },
        }

    def test_dependency_order_uses_catalog_graph(self) -> None:
        order = foundation.dependency_order(
            ["hakoniwa-pdu-bridge-core"], self.build_catalog()
        )

        self.assertEqual(
            order,
            [
                "hakoniwa-core-pro",
                "hakoniwa-pdu-endpoint",
                "hakoniwa-pdu-bridge-core",
            ],
        )

    def test_core_free_endpoint_narrows_optional_core_dependency(self) -> None:
        requirements = {
            "hakoniwa-pdu-endpoint": {
                "capabilities": {
                    "tcp": True,
                    "hakoniwa_core": False,
                }
            }
        }

        order = foundation.dependency_order(
            ["hakoniwa-pdu-endpoint"],
            self.build_catalog(),
            requirements,
        )

        self.assertEqual(order, ["hakoniwa-pdu-endpoint"])

    def test_plan_has_no_actions_when_foundation_is_satisfied(self) -> None:
        self.write_recipe(
            "  hakoniwa-core-pro:\n"
            "    capabilities:\n"
            "      available: true\n"
        )
        self.write_receipt("hakoniwa-core-pro")

        plan = foundation.create_build_plan(
            self.recipe, self.prefix, self.build_catalog(), self.root
        )

        self.assertEqual(plan["status"], "SATISFIED")
        self.assertEqual(plan["actions"], [])

    def test_plan_adds_dependencies_and_rebuilds_downstream(self) -> None:
        self.write_recipe(
            "  hakoniwa-pdu-bridge-core:\n"
            "    capabilities:\n"
            "      available: true\n"
        )
        self.write_receipt("hakoniwa-pdu-endpoint")
        self.write_receipt("hakoniwa-pdu-bridge-core")

        plan = foundation.create_build_plan(
            self.recipe, self.prefix, self.build_catalog(), self.root
        )

        self.assertEqual(
            [action["component"] for action in plan["actions"]],
            [
                "hakoniwa-core-pro",
                "hakoniwa-pdu-endpoint",
                "hakoniwa-pdu-bridge-core",
            ],
        )
        self.assertEqual(plan["actions"][0]["reason"], "MISSING")
        self.assertIn("dependency rebuild", plan["actions"][1]["reason"])
        self.assertIn("dependency rebuild", plan["actions"][2]["reason"])

    def test_plan_blocks_unknown_without_overwriting_it(self) -> None:
        self.write_recipe(
            "  hakoniwa-core-pro:\n"
            "    capabilities:\n"
            "      shared_memory: true\n"
        )
        binary = self.prefix / "bin" / "hako-cmd"
        binary.parent.mkdir(parents=True)
        binary.write_text("unmanaged install\n", encoding="utf-8")

        plan = foundation.create_build_plan(
            self.recipe, self.prefix, self.build_catalog(), self.root
        )

        self.assertEqual(plan["status"], "BLOCKED")
        self.assertEqual(plan["blocked"], ["hakoniwa-core-pro"])
        self.assertEqual(plan["actions"], [])

    def test_force_rebuilds_component_and_downstream_dependencies(self) -> None:
        self.write_recipe(
            "  hakoniwa-pdu-bridge-core:\n"
            "    capabilities:\n"
            "      available: true\n"
        )
        for component_id in (
            "hakoniwa-core-pro",
            "hakoniwa-pdu-endpoint",
            "hakoniwa-pdu-bridge-core",
        ):
            self.write_receipt(component_id)

        plan = foundation.create_build_plan(
            self.recipe,
            self.prefix,
            self.build_catalog(),
            self.root,
            {"hakoniwa-core-pro"},
        )

        self.assertEqual(
            [action["component"] for action in plan["actions"]],
            [
                "hakoniwa-core-pro",
                "hakoniwa-pdu-endpoint",
                "hakoniwa-pdu-bridge-core",
            ],
        )
        self.assertEqual(plan["actions"][0]["reason"], "FORCED")
        self.assertIn("dependency rebuild", plan["actions"][1]["reason"])

    def test_force_rejects_component_outside_recipe_dependency_closure(self) -> None:
        self.write_recipe(
            "  hakoniwa-core-pro:\n"
            "    capabilities:\n"
            "      available: true\n"
        )

        with self.assertRaisesRegex(
            foundation.FoundationError, "outside the Recipe dependency closure"
        ):
            foundation.create_build_plan(
                self.recipe,
                self.prefix,
                self.build_catalog(),
                self.root,
                {"hakoniwa-pdu-bridge-core"},
            )

    def test_core_build_uses_foundation_python_and_soabi_manifest(self) -> None:
        paths = foundation.resolve_workspace(self.root, "test")
        source = self.root / "hakoniwa-core-pro"
        hako = source / "tools" / "hako.py"
        hako.parent.mkdir(parents=True)
        hako.write_text("# test\n", encoding="utf-8")
        (source / "hakoniwa-build.yaml").write_text(
            "version: 1\nlimits:\n  asset_num: 16\npython:\n  soabi: false\n"
            "validation:\n  tests: true\n",
            encoding="utf-8",
        )

        commands = foundation.component_commands(
            "hakoniwa-core-pro", source, ["build"], paths
        )

        command = commands[0]
        python_index = command.index("--python-executable") + 1
        expected_python = foundation.foundation_python_executable(
            paths.foundation_python
        )
        self.assertEqual(command[0], str(expected_python))
        self.assertEqual(command[python_index], str(expected_python))
        mmap_index = command.index("--core-mmap-dir") + 1
        self.assertEqual(command[mmap_index], paths.foundation_mmap.as_posix())
        self.assertNotIn("\\", command[mmap_index])
        manifest_index = command.index("--config") + 1
        manifest = Path(command[manifest_index])
        self.assertIn("  soabi: true", manifest.read_text(encoding="utf-8"))
        self.assertIn("  tests: false", manifest.read_text(encoding="utf-8"))

    def test_build_stops_after_failed_component_doctor(self) -> None:
        paths = foundation.resolve_workspace(self.root, "test")
        source = self.root / "hakoniwa-core-pro"
        hako = source / "tools" / "hako.py"
        hako.parent.mkdir(parents=True)
        hako.write_text("# test\n", encoding="utf-8")
        foundation_python = foundation.foundation_python_executable(
            paths.foundation_python
        )
        doctor = [str(foundation_python), "tools/hako.py", "doctor"]
        build = [str(foundation_python), "tools/hako.py", "build"]
        plan = {
            "blocked": [],
            "recipe": str(self.recipe),
            "actions": [
                {
                    "component": "hakoniwa-core-pro",
                    "source": str(self.root / "hakoniwa-core-pro"),
                    "operations": ["doctor", "build"],
                    "requirements": {},
                }
            ],
        }
        python_contract = {
            "version": "3.12.0",
            "soabi": "cpython-312-test",
        }

        with mock.patch.object(
            foundation,
            "ensure_foundation_python",
            return_value=(foundation_python, python_contract),
        ), mock.patch.object(
            foundation, "prepare_workspace"
        ), mock.patch.object(
            foundation, "component_commands", return_value=[doctor, build]
        ), mock.patch.object(
            foundation.subprocess,
            "run",
            return_value=subprocess.CompletedProcess(doctor, 1),
        ) as run:
            with self.assertRaisesRegex(
                foundation.FoundationError,
                "hakoniwa-core-pro command failed with exit code 1",
            ):
                foundation.execute_build_plan(plan, paths)

        run.assert_called_once_with(
            doctor,
            cwd=source,
            check=False,
        )

    def test_build_source_validation_fails_before_bootstrap_with_recipe_hint(self) -> None:
        paths = foundation.resolve_workspace(self.root, "test")
        source = self.root / "missing-core"
        plan = {
            "blocked": [],
            "recipe": str(self.recipe),
            "actions": [
                {
                    "component": "hakoniwa-core-pro",
                    "source": str(source),
                    "operations": ["doctor", "build", "install"],
                    "requirements": {},
                }
            ],
        }

        with mock.patch.object(foundation, "ensure_foundation_python") as bootstrap:
            with self.assertRaisesRegex(
                foundation.FoundationError, "Foundation source is missing"
            ) as raised:
                foundation.execute_build_plan(plan, paths)

        bootstrap.assert_not_called()
        self.assertIn("recipe.py configure", str(raised.exception))

    def test_direct_foundation_plan_rejects_missing_source(self) -> None:
        source = self.root / "missing-core"
        plan = {
            "blocked": [],
            "recipe": str(self.recipe),
            "actions": [
                {
                    "component": "hakoniwa-core-pro",
                    "source": str(source),
                    "operations": ["build"],
                    "requirements": {},
                }
            ],
        }
        stderr = io.StringIO()

        with mock.patch.object(foundation, "warn_if_workspace_invalid"):
            with mock.patch.object(foundation, "load_build_catalog", return_value={}):
                with mock.patch.object(
                    foundation, "create_build_plan", return_value=plan
                ):
                    with mock.patch.object(foundation.sys, "stderr", stderr):
                        result = foundation.main(
                            ["plan", "--recipe", str(self.recipe)]
                        )

        self.assertEqual(result, 2)
        self.assertIn("recipe.py configure", stderr.getvalue())

    def test_normalize_core_config_repairs_unescaped_windows_path(self) -> None:
        paths = foundation.resolve_workspace(self.root, "test")
        config = paths.foundation_config / "cpp_core_config.json"
        config.parent.mkdir(parents=True, exist_ok=True)
        windows_path = paths.foundation_mmap.as_posix()
        config.write_text(
            '{\n  "shm_type": "mmap",\n'
            f'  "core_mmap_path": "{windows_path.replace("/", chr(92))}",\n'
            '  "asset_timeout_usec": 600000000\n}\n',
            encoding="utf-8",
        )

        with mock.patch.object(foundation.platform, "system", return_value="Windows"):
            foundation.normalize_core_config_for_windows(config, paths.foundation_mmap)

        data = json.loads(config.read_text(encoding="utf-8"))
        self.assertEqual(data["core_mmap_path"], windows_path)

    def test_normalize_core_config_preserves_valid_escaped_windows_path(self) -> None:
        paths = foundation.resolve_workspace(self.root, "test")
        config = paths.foundation_config / "cpp_core_config.json"
        config.parent.mkdir(parents=True, exist_ok=True)
        windows_path = paths.foundation_mmap.as_posix()
        config.write_text(
            json.dumps({"shm_type": "mmap", "core_mmap_path": windows_path.replace("/", "\\")}),
            encoding="utf-8",
        )

        with mock.patch.object(foundation.platform, "system", return_value="Windows"):
            foundation.normalize_core_config_for_windows(config, paths.foundation_mmap)

        data = json.loads(config.read_text(encoding="utf-8"))
        self.assertEqual(data["core_mmap_path"], windows_path)

    def test_core_runtime_config_inspection_rejects_invalid_json(self) -> None:
        paths = foundation.resolve_workspace(self.root, "test")
        config = paths.foundation_config / "cpp_core_config.json"
        config.parent.mkdir(parents=True, exist_ok=True)
        config.write_text(
            '{"core_mmap_path": "C:\\project\\broken"}',
            encoding="utf-8",
        )

        result = foundation.inspect_core_runtime_config(paths.install_prefix)

        self.assertEqual(result["status"], "INCOMPATIBLE")
        self.assertIn("not valid JSON", result["reason"])

    def test_core_runtime_config_inspection_accepts_managed_mmap(self) -> None:
        paths = foundation.resolve_workspace(self.root, "test")
        config = paths.foundation_config / "cpp_core_config.json"
        config.parent.mkdir(parents=True, exist_ok=True)
        config.write_text(
            json.dumps({"core_mmap_path": paths.foundation_mmap.as_posix()}),
            encoding="utf-8",
        )

        result = foundation.inspect_core_runtime_config(paths.install_prefix)

        self.assertEqual(result["status"], "SATISFIED")
        self.assertEqual(result["core_mmap_path"], paths.foundation_mmap.as_posix())

    def test_component_commands_keep_shared_python_before_endpoint_cffi(self) -> None:
        paths = foundation.resolve_workspace(self.root, "test")
        source = self.root / "hakoniwa-pdu-endpoint"
        hako = source / "tools" / "hako.py"
        hako.parent.mkdir(parents=True)
        hako.write_text("# test\n", encoding="utf-8")

        commands = foundation.component_commands(
            "hakoniwa-pdu-endpoint",
            source,
            ["doctor", "configure", "build", "install"],
            paths,
        )

        self.assertEqual([command[-1] for command in commands], [
            "doctor",
            "configure",
            "build",
            "install",
        ])
        for command in commands:
            venv_index = command.index("--python-venv") + 1
            self.assertEqual(
                command[venv_index], str(paths.install_prefix / "python")
            )
        manifest = paths.foundation_build / "hakoniwa-pdu-endpoint.yaml"
        content = manifest.read_text(encoding="utf-8")
        self.assertIn(f'hakoniwa_core_root: "{paths.install_prefix}"', content)
        self.assertIn("  python: true", content)

    def test_core_free_endpoint_manifest_disables_core_and_python(self) -> None:
        paths = foundation.resolve_workspace(self.root, "test")
        source = self.root / "hakoniwa-pdu-endpoint"
        hako = source / "tools" / "hako.py"
        hako.parent.mkdir(parents=True)
        hako.write_text("# test\n", encoding="utf-8")

        commands = foundation.component_commands(
            "hakoniwa-pdu-endpoint",
            source,
            ["doctor", "build", "install"],
            paths,
            {
                "capabilities": {
                    "tcp": True,
                    "hakoniwa_core": False,
                }
            },
        )

        manifest = paths.foundation_build / "hakoniwa-pdu-endpoint.yaml"
        content = manifest.read_text(encoding="utf-8")
        self.assertIn("  hakoniwa_core: false", content)
        self.assertIn("  python: false", content)
        self.assertIn('  hakoniwa_core_root: ""', content)
        self.assertTrue(all("--python-venv" not in command for command in commands))

    def test_rpc_commands_install_python_into_foundation_venv(self) -> None:
        paths = foundation.resolve_workspace(self.root, "test")
        source = self.root / "hakoniwa-pdu-rpc"
        hako = source / "tools" / "hako.py"
        hako.parent.mkdir(parents=True)
        hako.write_text("# test\n", encoding="utf-8")

        commands = foundation.component_commands(
            "hakoniwa-pdu-rpc",
            source,
            ["doctor", "build", "install"],
            paths,
        )

        for command in commands:
            index = command.index("--python-venv") + 1
            self.assertEqual(command[index], str(paths.foundation_python))

    def test_athrill_manifest_enables_required_exdev_on_windows(self) -> None:
        paths = foundation.resolve_workspace(self.root, "test")
        source = self.root / "athrill-target-v850e2m"
        source.mkdir()

        with mock.patch.object(
            foundation, "load_foundation_toolchain",
            return_value={"vcpkg_root": "C:/project/vcpkg"},
        ):
            manifest = foundation.write_component_manifest(
                "athrill-target-v850e2m",
                source,
                paths,
                {
                    "capabilities": {
                        "exdev": True,
                        "mros": False,
                        "vdev": False,
                    }
                },
            )

        self.assertIsNotNone(manifest)
        content = manifest.read_text(encoding="utf-8")
        self.assertIn("  exdev: true", content)
        self.assertIn("  mros: false", content)
        self.assertIn("  vdev: false", content)

    def test_athrill_device_manifest_resolves_core_and_component_split(self) -> None:
        paths = foundation.resolve_workspace(self.root, "test")
        source = self.root / "athrill-device"
        source.mkdir()

        manifest = foundation.write_component_manifest(
            "athrill-device",
            source,
            paths,
            {
                "capabilities": {
                    "hakotime_static": True,
                    "hakotime_shared": False,
                    "hakopdu_ev3": True,
                }
            },
        )

        self.assertIsNotNone(manifest)
        content = manifest.read_text(encoding="utf-8")
        self.assertIn("  hakotime: false", content)
        self.assertIn("  hakopdu_ev3: true", content)
        self.assertIn(
            f'  hakoniwa_core_root: "{paths.install_prefix}"', content
        )
        self.assertIn(f'  athrill_root: "{source.parent / "athrill"}"', content)

    def test_athrill_device_does_not_require_saved_vcpkg_toolchain(self) -> None:
        paths = foundation.resolve_workspace(self.root, "test")
        source = self.root / "athrill-device"
        source.mkdir()

        with mock.patch.object(
            foundation,
            "load_foundation_toolchain",
            side_effect=AssertionError("vcpkg must not be inspected"),
        ):
            manifest = foundation.write_component_manifest(
                "athrill-device", source, paths, {}
            )

        self.assertIsNotNone(manifest)


if __name__ == "__main__":
    unittest.main()
