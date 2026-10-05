from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parents[1]))
from workspace_test_isolation import isolate_from_active_workspace  # noqa: E402

isolate_from_active_workspace()


TOOLS_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOLS_DIR))

from foundation_lib.build import _core_foundation_manifest  # noqa: E402


class CoreFoundationManifestTest(unittest.TestCase):
    def test_callback_assets_shared_is_disabled_by_default(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            manifest = Path(temporary) / "hakoniwa-build.yaml"
            manifest.write_text("version: 1\n", encoding="utf-8")
            generated = _core_foundation_manifest(manifest)

        self.assertIn("features:\n  callback_assets_shared: false", generated)

    def test_callback_assets_shared_can_be_enabled_for_a_recipe(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            manifest = Path(temporary) / "hakoniwa-build.yaml"
            manifest.write_text(
                "version: 1\nfeatures:\n  callback_assets_shared: false\n",
                encoding="utf-8",
            )
            generated = _core_foundation_manifest(
                manifest, callback_assets_shared=True
            )

        self.assertIn("features:\n  callback_assets_shared: true", generated)


    def test_core_shared_is_not_written_unless_a_recipe_requires_it(self) -> None:
        # Without the requirement the build input is the same as before, and an
        # older Core PRO that rejects unknown features keys is never given it.
        with tempfile.TemporaryDirectory() as temporary:
            manifest = Path(temporary) / "hakoniwa-build.yaml"
            manifest.write_text("version: 1\n", encoding="utf-8")
            generated = _core_foundation_manifest(
                manifest, callback_assets_shared=True
            )

        self.assertNotIn("core_shared", generated)

    def test_core_shared_can_be_enabled_for_a_recipe(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            manifest = Path(temporary) / "hakoniwa-build.yaml"
            manifest.write_text(
                "version: 1\nfeatures:\n  callback_assets_shared: false\n"
                "  core_shared: false\n",
                encoding="utf-8",
            )
            generated = _core_foundation_manifest(
                manifest, callback_assets_shared=True, core_shared=True
            )

        self.assertIn("  callback_assets_shared: true", generated)
        self.assertIn("  core_shared: true", generated)
        self.assertEqual(generated.count("core_shared"), 1)

    def test_the_build_input_is_unchanged_when_core_shared_is_not_required(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            manifest = Path(temporary) / "hakoniwa-build.yaml"
            manifest.write_text(
                "version: 1\nfeatures:\n  callback_assets_shared: false\n",
                encoding="utf-8",
            )
            before = _core_foundation_manifest(manifest, callback_assets_shared=True)
            after = _core_foundation_manifest(
                manifest, callback_assets_shared=True, core_shared=False
            )

        self.assertEqual(before, after)


if __name__ == "__main__":
    unittest.main()
