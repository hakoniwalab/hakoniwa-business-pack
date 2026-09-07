"""Regression checks for switching from pre-workdir Workspace activations."""
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import workspace


class LegacyWorkspaceSwitchTest(unittest.TestCase):
    def test_switch_removes_old_foundation_but_preserves_other_paths(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            old = root / "old" / "foundation" / "install"
            other = root / "install-extra"
            for system in ("Linux", "Darwin"):
                for with_workdir in (False, True):
                    with self.subTest(system=system, with_workdir=with_workdir):
                        base = {
                            "HAKONIWA_HOME": str(old),
                            "PATH": os.pathsep.join(map(str, (old / "python/bin", old / "bin", other / "bin"))),
                            "LD_LIBRARY_PATH": os.pathsep.join(map(str, (old / "lib", other / "lib"))),
                            "DYLD_LIBRARY_PATH": os.pathsep.join(map(str, (old / "lib", other / "lib"))),
                            "PYTHONHOME": "stale",
                            "PYTHONPATH": "stale",
                        }
                        if with_workdir:
                            base["HAKONIWA_WORK_DIR"] = str(root / "old")
                        original = dict(base)
                        paths = workspace.resolve_workspace(root / "bp", root / "new")
                        with patch.object(workspace.platform, "system", return_value=system):
                            env = workspace.build_environment(paths, base=base)
                        self.assertEqual(base, original)
                        self.assertNotIn("PYTHONHOME", env)
                        self.assertNotIn("PYTHONPATH", env)
                        self.assertEqual(env["VIRTUAL_ENV"], str(paths.foundation_python_root))
                        self.assertEqual(env["PATH"].split(os.pathsep), [
                            str(paths.foundation_python_bin), str(paths.foundation_bin), str(other / "bin")])
                        for variable in ("LD_LIBRARY_PATH", "DYLD_LIBRARY_PATH"):
                            expected = [str(other / "lib")]
                            if variable == ("LD_LIBRARY_PATH" if system == "Linux" else "DYLD_LIBRARY_PATH"):
                                expected.insert(0, str(paths.foundation_lib))
                            self.assertEqual(env[variable].split(os.pathsep), expected)

    def test_home_symlink_is_removed_without_workdir(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            old = root / "old"
            old.mkdir()
            alias = root / "alias"
            alias.symlink_to(old, target_is_directory=True)
            paths = workspace.resolve_workspace(root / "bp", root / "new")
            env = workspace.build_environment(paths, base={
                "HAKONIWA_HOME": str(alias),
                "PATH": str(old / "python/bin"),
            })
            self.assertNotIn(str(old / "python/bin"), env["PATH"].split(os.pathsep))

    def test_clean_environment_preserves_unrelated_paths(self):
        paths = workspace.resolve_workspace(Path("/tmp/bp"), Path("/tmp/new-work"))
        env = workspace.build_environment(paths, base={"PATH": "/usr/bin"})
        self.assertIn("/usr/bin", env["PATH"].split(os.pathsep))


if __name__ == "__main__":
    unittest.main()
