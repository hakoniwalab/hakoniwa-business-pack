from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path


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


if __name__ == "__main__":
    unittest.main()
