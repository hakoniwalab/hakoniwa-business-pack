from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
EXPORTER = ROOT / "recipes" / "tools" / "export_recipe_json.rb"


class RecipeExporterEncodingTest(unittest.TestCase):
    @unittest.skipUnless(shutil.which("ruby"), "Ruby is required for the Recipe exporter")
    def test_non_ascii_recipe_exports_as_ascii_safe_json(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            recipe = Path(temporary) / "locale-safe.yaml"
            recipe.write_text(
                "id: locale-safe\n"
                "title: 日本語のRecipe\n"
                "description: 'colon: value'\n",
                encoding="utf-8",
            )

            completed = subprocess.run(
                ["ruby", str(EXPORTER), str(recipe)],
                capture_output=True,
                check=False,
            )

        self.assertEqual(completed.returncode, 0, completed.stderr.decode(errors="replace"))
        exported = completed.stdout.decode("ascii")
        data = json.loads(exported)
        self.assertEqual(data["title"], "日本語のRecipe")
        self.assertEqual(data["description"], "colon: value")


if __name__ == "__main__":
    unittest.main()
