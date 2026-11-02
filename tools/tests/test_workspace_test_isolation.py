from __future__ import annotations

import ast
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parents[1]))
from workspace_test_isolation import isolate_from_active_workspace  # noqa: E402

isolate_from_active_workspace()

import workspace_test_isolation  # noqa: E402


REPOSITORY = Path(__file__).resolve().parents[2]
TOOLS = REPOSITORY / "tools"

# Modules that wrote toolchain.json, build manifests or a Foundation Python
# into the caller's Workspace before they were isolated.
FORMERLY_LEAKING_MODULES = (
    "tools/tests/test_foundation_consumer.py",
    "tools/tests/test_foundation_toolchain_prefix.py",
    "tools/tests/test_foundation_component_prepare.py",
    "tools/test_foundation.py",
)


def _calls_isolation_at_module_level(path: Path) -> bool:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for node in tree.body:
        if (
            isinstance(node, ast.Expr)
            and isinstance(node.value, ast.Call)
            and isinstance(node.value.func, ast.Name)
            and node.value.func.id == "isolate_from_active_workspace"
        ):
            return True
    return False


class WorkspaceTestIsolationTest(unittest.TestCase):
    def test_every_tool_test_module_isolates_itself(self) -> None:
        modules = sorted(
            path
            for path in TOOLS.rglob("test*.py")
            if "__pycache__" not in path.parts
        )
        self.assertTrue(modules)
        missing = [
            str(path.relative_to(REPOSITORY))
            for path in modules
            if not _calls_isolation_at_module_level(path)
        ]
        self.assertEqual(
            missing,
            [],
            "tool test modules must call isolate_from_active_workspace() at import time",
        )

    def test_isolation_removes_workspace_identity_only(self) -> None:
        env = {name: "/real/workspace" for name in workspace_test_isolation.WORKSPACE_ENVIRONMENT}
        env["PATH"] = "/usr/bin"

        isolate_from_active_workspace(env)

        self.assertEqual(env, {"PATH": "/usr/bin"})

    def test_active_workspace_is_untouched_by_formerly_leaking_tests(self) -> None:
        # One process per module: isolation is process-wide, so a module that
        # isolates itself would otherwise hide one that does not.
        for module in FORMERLY_LEAKING_MODULES:
            with self.subTest(module=module):
                self._assert_module_leaves_workspace_untouched(module)

    def _assert_module_leaves_workspace_untouched(self, module: str) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            workspace = Path(temporary) / "active-workspace"
            work = workspace / "work"
            work.mkdir(parents=True)
            env = dict(os.environ)
            env.update(
                {
                    "HAKONIWA_WORKSPACE_ACTIVE": "1",
                    "HAKONIWA_WORKSPACE_ROOT": str(workspace),
                    "HAKONIWA_WORK_DIR": str(work),
                    "HAKONIWA_HOME": str(work / "foundation" / "install"),
                    "HAKO_CONFIG_PATH": str(
                        work / "foundation" / "config" / "cpp_core_config.json"
                    ),
                    "HAKO_PDU_ENDPOINT_RUNTIME_DIRS": str(
                        work / "foundation" / "install" / "bin"
                    ),
                }
            )

            result = subprocess.run(
                [sys.executable, "-m", "unittest", module],
                cwd=REPOSITORY,
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                check=False,
            )

            self.assertEqual(result.returncode, 0, result.stdout)
            written = sorted(
                str(path.relative_to(work)) for path in work.rglob("*")
            )
            self.assertEqual(written, [])


if __name__ == "__main__":
    unittest.main()
