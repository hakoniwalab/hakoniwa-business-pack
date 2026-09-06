#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path

FOUNDATION_SCRIPT = Path(__file__).resolve().parents[1] / "foundation.py"
SPEC = importlib.util.spec_from_file_location(
    "business_pack_foundation_component_prepare", FOUNDATION_SCRIPT
)
assert SPEC is not None and SPEC.loader is not None
foundation = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = foundation
SPEC.loader.exec_module(foundation)


class FoundationComponentPrepareTest(unittest.TestCase):
    def test_catalog_prepares_endpoint_before_doctor(self) -> None:
        catalog = foundation.load_build_catalog(
            FOUNDATION_SCRIPT.parents[1] / "catalog" / "foundation-components.json"
        )

        operations = catalog["hakoniwa-pdu-endpoint"]["operations"]
        self.assertEqual(operations[:2], ["prepare", "doctor"])

    def test_prepare_command_targets_foundation_python(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "hakoniwa-business-pack"
            source = Path(temporary) / "hakoniwa-pdu-endpoint"
            hako = source / "tools" / "hako.py"
            hako.parent.mkdir(parents=True)
            hako.write_text("# test\n", encoding="utf-8")
            paths = foundation.resolve_workspace(root, "test")

            commands = foundation.component_commands(
                "hakoniwa-pdu-endpoint",
                source,
                ["prepare", "doctor"],
                paths,
            )

        self.assertEqual([command[-1] for command in commands], ["prepare", "doctor"])
        for command in commands:
            self.assertEqual(
                command[command.index("--python-venv") + 1],
                str(paths.foundation_python),
            )
            self.assertEqual(
                command[0],
                str(foundation.foundation_python_executable(paths.foundation_python)),
            )


if __name__ == "__main__":
    unittest.main()
