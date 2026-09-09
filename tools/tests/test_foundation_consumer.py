from __future__ import annotations

import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "foundation_consumer.py"
SPEC = importlib.util.spec_from_file_location("business_pack_foundation_consumer", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
consumer = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = consumer
SPEC.loader.exec_module(consumer)


class FoundationConsumerContextTest(unittest.TestCase):
    def make_paths(self, root: Path):
        return consumer._foundation.resolve_workspace(root / "business-pack", "test-recipe")

    def configure_fake_vcpkg(self, paths, root: Path) -> Path:
        vcpkg = root / "external" / "vcpkg"
        vcpkg.mkdir(parents=True)
        executable = vcpkg / (
            "vcpkg.exe" if consumer._foundation.sys.platform == "win32" else "vcpkg"
        )
        executable.write_text("test\n", encoding="utf-8")
        toolchain = vcpkg / "scripts" / "buildsystems" / "vcpkg.cmake"
        toolchain.parent.mkdir(parents=True)
        toolchain.write_text("# test\n", encoding="utf-8")
        consumer._foundation.configure_foundation_toolchain(paths, vcpkg)
        return vcpkg.resolve()

    def test_non_windows_context_does_not_export_vcpkg_build_context(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            paths = self.make_paths(root)
            self.configure_fake_vcpkg(paths, root)

            context = consumer.cmake_consumer_context(
                paths,
                platform_name="linux",
                architecture="x64",
            )

        self.assertEqual(context["schema_version"], 1)
        self.assertEqual(context["prefix_paths"], [str(paths.install_prefix)])
        self.assertIsNone(context["toolchain_file"])
        self.assertEqual(context["cache_variables"], {})

    def test_windows_context_derives_vcpkg_paths_from_foundation_state(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            paths = self.make_paths(root)
            vcpkg = self.configure_fake_vcpkg(paths, root)

            context = consumer.cmake_consumer_context(
                paths,
                platform_name="win32",
                architecture="x64",
            )

        self.assertEqual(
            context["prefix_paths"],
            [
                str(paths.install_prefix),
                str(vcpkg / "installed" / "x64-windows"),
            ],
        )
        self.assertEqual(
            context["toolchain_file"],
            str(vcpkg / "scripts" / "buildsystems" / "vcpkg.cmake"),
        )
        self.assertEqual(
            context["cache_variables"],
            {"VCPKG_TARGET_TRIPLET": "x64-windows"},
        )

    def test_windows_context_renders_direct_cmake_arguments(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            paths = self.make_paths(root)
            vcpkg = self.configure_fake_vcpkg(paths, root)

            args = consumer.cmake_consumer_args(
                paths,
                platform_name="win32",
                architecture="x64",
            )

        self.assertEqual(
            args,
            [
                "-DCMAKE_PREFIX_PATH="
                + ";".join(
                    [
                        str(paths.install_prefix),
                        str(vcpkg / "installed" / "x64-windows"),
                    ]
                ),
                "-DCMAKE_TOOLCHAIN_FILE="
                + str(vcpkg / "scripts" / "buildsystems" / "vcpkg.cmake"),
                "-DVCPKG_TARGET_TRIPLET=x64-windows",
            ],
        )

    def test_windows_without_foundation_toolchain_needs_no_vcpkg_guessing(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            paths = self.make_paths(Path(temporary))

            context = consumer.cmake_consumer_context(
                paths,
                platform_name="win32",
                architecture="x64",
            )

        self.assertEqual(context["prefix_paths"], [str(paths.install_prefix)])
        self.assertIsNone(context["toolchain_file"])
        self.assertEqual(context["cache_variables"], {})


if __name__ == "__main__":
    unittest.main()
