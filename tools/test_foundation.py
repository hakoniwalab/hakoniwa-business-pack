#!/usr/bin/env python3
from __future__ import annotations

import json
import importlib.util
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path, PureWindowsPath

FOUNDATION_SCRIPT = Path(__file__).with_name("foundation.py")
SPEC = importlib.util.spec_from_file_location("business_pack_foundation", FOUNDATION_SCRIPT)
assert SPEC is not None and SPEC.loader is not None
foundation = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = foundation
SPEC.loader.exec_module(foundation)


class FoundationWorkspaceTest(unittest.TestCase):
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


class FoundationInspectorTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.prefix = self.root / "install"
        self.recipe = self.root / "recipe.yaml"

    def tearDown(self) -> None:
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
        platform_os: str | None = None,
        artifact: str = "lib/component.marker",
        create_artifact: bool = True,
    ) -> None:
        os_name, architecture = foundation._host_contract()
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
        receipt.write_text(
            "schema_version: 1\n"
            "component:\n"
            f"  id: {component}\n"
            '  version: "1.0.0"\n'
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

    def test_core_build_uses_orchestrator_python(self) -> None:
        paths = foundation.resolve_workspace(self.root, "test")
        source = self.root / "hakoniwa-core-pro"
        hako = source / "tools" / "hako.py"
        hako.parent.mkdir(parents=True)
        hako.write_text("# test\n", encoding="utf-8")

        commands = foundation.component_commands(
            "hakoniwa-core-pro", source, ["build"], paths
        )

        command = commands[0]
        python_index = command.index("--python-executable") + 1
        self.assertEqual(command[python_index], foundation.sys.executable)

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


if __name__ == "__main__":
    unittest.main()
