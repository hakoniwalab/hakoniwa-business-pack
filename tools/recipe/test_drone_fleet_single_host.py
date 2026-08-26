#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
import hashlib
import io
import json
import sys
import tarfile
import tempfile
import unittest
import zipfile
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

from tools.recipe.path_test_support import contains_path


SCRIPT = Path(__file__).with_name("drone_fleet_single_host.py")
SPEC = importlib.util.spec_from_file_location("drone_fleet_single_host_recipe", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
recipe = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = recipe
SPEC.loader.exec_module(recipe)

FOUNDATION_SCRIPT = Path(__file__).resolve().parents[1] / "foundation.py"
FOUNDATION_SPEC = importlib.util.spec_from_file_location(
    "drone_fleet_foundation_test", FOUNDATION_SCRIPT
)
assert FOUNDATION_SPEC is not None and FOUNDATION_SPEC.loader is not None
foundation = importlib.util.module_from_spec(FOUNDATION_SPEC)
sys.modules[FOUNDATION_SPEC.name] = foundation
FOUNDATION_SPEC.loader.exec_module(foundation)


class DroneFleetSingleHostTest(unittest.TestCase):
    def _experiment(
        self,
        root: Path,
        *,
        drones: int = 100,
        per_process: int = 10,
        visualization: bool = True,
    ) -> Path:
        path = root / "experiment.yaml"
        path.write_text(
            f"""version: 1
experiment:
  id: test-fleet
scale:
  drone_count: {drones}
  drones_per_process: {per_process}
  process_count: auto
runtime:
  mode: native
  visualization: {str(visualization).lower()}
  show_runner_real_time_sync: true
scenario:
  type: hakoniwa-word
  word: HAKONIWA
  letter_width_m: 2.0
  letter_height_m: 4.0
  letter_gap_m: 0.9
  altitude_m: 4.0
  duration_sec: 6.0
  hold_sec: 10.0
  speed_m_s: 3.0
  timeout_sec: 120.0
  land: false
results:
  enabled: false
  directory: results
""",
            encoding="utf-8",
        )
        return path

    def _mujoco_version(
        self,
        drone_root: Path,
        *,
        mujoco_version: str = "3.9.0",
    ) -> Path:
        drone_root.mkdir(parents=True, exist_ok=True)
        version_file = drone_root / "MUJOCO_VERSION.txt"
        version_file.write_text(
            mujoco_version + "\n", encoding="utf-8"
        )
        (drone_root / "NATIVE_RUNTIME_REQUIREMENTS.yaml").write_text(
            """schema_version: 1
profiles:
  public-v4.0.0:
    distribution_release: v4.0.0
    managed_runtimes:
      mujoco:
        required: true
        version_file: MUJOCO_VERSION.txt
        platforms:
          linux:
            library: vendor/mujoco/lib/libmujoco.so.{version}
          macos:
            library: vendor/mujoco/lib/libmujoco.{version}.dylib
    platforms:
      linux:
        dependency_inspector: elf
        binary_roles:
          drone_service: lnx/linux-main_hako_drone_service
          visual_state_publisher: lnx/linux-drone_visual_state_publisher
        required_libraries: [\"libOpenGL.so.0\", \"libglfw.so.3\"]
      macos:
        dependency_inspector: macho
        binary_roles:
          drone_service: mac/mac-main_hako_drone_service
          visual_state_publisher: mac/mac-drone_visual_state_publisher
        required_libraries: [\"libglfw.3.dylib\"]
""",
            encoding="utf-8",
        )
        return version_file

    def test_auto_process_count_and_build_limits(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            experiment = recipe.resolve_experiment(
                self._experiment(Path(temporary), drones=100, per_process=10)
            )
            self.assertEqual(experiment.process_count, 10)
            self.assertEqual(
                recipe.required_build_limits(experiment),
                {
                    "asset_num": 16,
                    "pdu_channel_max": 4096,
                    "recv_event_max": 2048,
                    "service_client_max": 128,
                    "service_max": 512,
                    "client_name_len_max": 64,
                    "service_name_len_max": 128,
                },
            )

    def test_auto_process_count_uses_ceiling(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            experiment = recipe.resolve_experiment(
                self._experiment(Path(temporary), drones=101, per_process=10)
            )
            self.assertEqual(experiment.process_count, 11)

    def test_drone_count_override_updates_single_process_shape(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = self._experiment(Path(temporary), drones=2, per_process=2)
            path.write_text(
                path.read_text(encoding="utf-8").replace(
                    "  process_count: auto", "  process_count: 1"
                ),
                encoding="utf-8",
            )
            experiment = recipe.resolve_experiment(
                path, drone_count_override=4
            )
            self.assertEqual(experiment.drone_count, 4)
            self.assertEqual(experiment.drones_per_process, 4)
            self.assertEqual(experiment.process_count, 1)

    def test_drone_and_process_count_overrides_resolve_partition_shape(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            experiment = recipe.resolve_experiment(
                self._experiment(Path(temporary), drones=4, per_process=2),
                drone_count_override=128,
                process_count_override=2,
            )
            self.assertEqual(experiment.drone_count, 128)
            self.assertEqual(experiment.process_count, 2)
            self.assertEqual(experiment.drones_per_process, 64)

    def test_parser_accepts_drone_count_override(self) -> None:
        args = recipe.parser().parse_args(
            ["configure", "--drone-count", "128", "--process-count", "2"]
        )
        self.assertEqual(args.drone_count, 128)
        self.assertEqual(args.process_count, 2)

    def test_formation_scale_override_scales_resolved_dimensions(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = self._experiment(Path(temporary), drones=2, per_process=2)
            baseline = recipe.resolve_experiment(path)
            scaled = recipe.resolve_experiment(
                path, formation_scale_override=5.0
            )
            self.assertEqual(scaled.letter_width_m, baseline.letter_width_m * 5.0)
            self.assertEqual(scaled.letter_height_m, baseline.letter_height_m * 5.0)
            self.assertEqual(scaled.letter_gap_m, baseline.letter_gap_m * 5.0)

    def test_formation_scale_override_is_bounded(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            with self.assertRaisesRegex(recipe.RecipeError, "formation-scale"):
                recipe.resolve_experiment(
                    self._experiment(Path(temporary), drones=2, per_process=2),
                    formation_scale_override=20.0,
                )

    def test_parser_accepts_city_max_altitude_and_formation_scale(self) -> None:
        args = recipe.parser().parse_args(
            [
                "configure",
                "--formation-scale",
                "5",
                "--altitude-mode",
                "city-max-clearance",
                "--above-city-clearance-m",
                "12",
                "--spawn-spacing-m",
                "1.25",
                "--formation-tilt-deg",
                "18",
            ]
        )
        self.assertEqual(args.formation_scale, 5.0)
        self.assertEqual(args.altitude_mode, "city-max-clearance")
        self.assertEqual(args.above_city_clearance_m, 12.0)
        self.assertEqual(args.spawn_spacing_m, 1.25)
        self.assertEqual(args.formation_tilt_deg, 18.0)

    def test_commands_reuse_configured_experiment_and_drone_root(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            configured = (
                root
                / "work"
                / "recipes"
                / recipe.RECIPE_ID
                / "config"
                / "resolved-experiment.yaml"
            )
            configured.parent.mkdir(parents=True)
            configured.write_text("version: 1\n", encoding="utf-8")
            drone_root = root / "hakoniwa-drone-pro"
            configured.with_name("mujoco-city-fleet.json").write_text(
                json.dumps({"drone_root": str(drone_root)}), encoding="utf-8"
            )
            with mock.patch.object(recipe, "ROOT", root):
                self.assertEqual(
                    recipe.command_experiment_path("start", None), configured
                )
                self.assertEqual(
                    recipe.command_drone_root("start", None), drone_root
                )

    def test_configure_without_experiment_keeps_source_default(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            configured = (
                root
                / "work"
                / "recipes"
                / recipe.RECIPE_ID
                / "config"
                / "resolved-experiment.yaml"
            )
            configured.parent.mkdir(parents=True)
            configured.touch()
            default_experiment = root / "default.yaml"
            with mock.patch.object(recipe, "ROOT", root), mock.patch.object(
                recipe, "DEFAULT_EXPERIMENT", default_experiment
            ):
                self.assertEqual(
                    recipe.command_experiment_path("configure", None),
                    default_experiment.absolute(),
                )

    def test_configure_defaults_to_public_drone_core(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            with mock.patch.object(recipe, "ROOT", root):
                self.assertEqual(
                    recipe.command_drone_root("configure", None),
                    (root.parent / "hakoniwa-drone-core").absolute(),
                )

    def test_legacy_city_cli_points_to_drone_show_repository(self) -> None:
        stderr = io.StringIO()
        with redirect_stderr(stderr):
            result = recipe.main(
                [
                    "configure",
                    "--mujoco-city-world",
                    "/tmp/city-world-receipt.json",
                    "--altitude-mode",
                    "city-max-clearance",
                ]
            )
        self.assertEqual(result, 2)
        self.assertIn("hakoniwa-drone-show", stderr.getvalue())
        self.assertIn("virtual_drone_show.py configure", stderr.getvalue())

    def test_asset_limit_boundaries_match_single_host_launcher_assets(self) -> None:
        def resolve(root: Path, process_count: int, visualization: bool):
            path = self._experiment(
                root,
                drones=100,
                per_process=10,
                visualization=visualization,
            )
            content = path.read_text(encoding="utf-8")
            content = content.replace(
                "  drones_per_process: 10", "  drones_per_process: auto"
            )
            content = content.replace(
                "  process_count: auto", f"  process_count: {process_count}"
            )
            path.write_text(content, encoding="utf-8")
            return recipe.resolve_experiment(path)

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self.assertEqual(
                recipe.required_build_limits(resolve(root, 15, False))["asset_num"],
                16,
            )
            self.assertEqual(
                recipe.required_build_limits(resolve(root, 16, False))["asset_num"],
                32,
            )
            self.assertEqual(
                recipe.required_build_limits(resolve(root, 13, True))["asset_num"],
                16,
            )
            self.assertEqual(
                recipe.required_build_limits(resolve(root, 14, True))["asset_num"],
                32,
            )

    def test_explicit_process_count_does_not_require_drones_per_process(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = self._experiment(Path(temporary), drones=100, per_process=10)
            content = path.read_text(encoding="utf-8")
            content = content.replace("  drones_per_process: 10\n", "")
            content = content.replace("  process_count: auto", "  process_count: 3")
            path.write_text(content, encoding="utf-8")
            experiment = recipe.resolve_experiment(path)
            self.assertEqual(experiment.process_count, 3)
            self.assertEqual(experiment.drones_per_process, 34)

    def test_explicit_process_count_accepts_auto_drones_per_process(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = self._experiment(Path(temporary), drones=200, per_process=10)
            content = path.read_text(encoding="utf-8")
            content = content.replace("  drones_per_process: 10", "  drones_per_process: auto")
            content = content.replace("  process_count: auto", "  process_count: 3")
            path.write_text(content, encoding="utf-8")
            experiment = recipe.resolve_experiment(path)
            self.assertEqual(experiment.drone_count, 200)
            self.assertEqual(experiment.process_count, 3)
            self.assertEqual(experiment.drones_per_process, 67)

    def test_partition_counts_assign_remainder_to_last_process(self) -> None:
        expected = {
            1: [200],
            2: [100, 100],
            3: [66, 67, 67],
            4: [50, 50, 50, 50],
            5: [40, 40, 40, 40, 40],
            6: [33, 33, 33, 33, 34, 34],
            8: [25] * 8,
            20: [10] * 20,
        }
        for process_count, counts in expected.items():
            with self.subTest(process_count=process_count):
                self.assertEqual(
                    recipe.expected_partition_counts(200, process_count), counts
                )

    def test_materialized_experiment_rejects_missing_process_partition(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            paths = foundation.resolve_workspace(root, recipe.RECIPE_ID)
            foundation.prepare_workspace(paths)
            path = self._experiment(root, drones=200, per_process=25)
            content = path.read_text(encoding="utf-8").replace(
                "  process_count: auto", "  process_count: 8"
            )
            path.write_text(content, encoding="utf-8")
            experiment = recipe.resolve_experiment(path)
            recipe.write_simple_yaml(
                paths.recipe_config / "resolved-experiment.yaml",
                recipe.resolved_experiment_dict(experiment),
            )
            errors = recipe.validate_materialized_experiment(paths, experiment)
            self.assertTrue(
                any("missing process 5 fleet partition" in error for error in errors),
                errors,
            )

    def test_start_does_not_launch_when_doctor_rejects_stale_workspace(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            experiment_path = self._experiment(root, drones=200, per_process=25)
            with mock.patch.object(recipe, "doctor", return_value=1) as doctor, mock.patch.object(
                recipe, "_run"
            ) as run:
                self.assertEqual(
                    recipe.start(experiment_path, root / "drone", root / "viewer"), 1
                )
            doctor.assert_called_once_with(
                experiment_path,
                root / "drone",
                root / "viewer",
                drone_count_override=None,
            )
            run.assert_not_called()

    def test_total_drone_count_is_derived_from_process_shape(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = self._experiment(Path(temporary), drones=100, per_process=10)
            content = path.read_text(encoding="utf-8")
            content = content.replace("  drone_count: 100", "  drone_count: auto")
            content = content.replace("  drones_per_process: 10", "  drones_per_process: 26")
            content = content.replace("  process_count: auto", "  process_count: 3")
            path.write_text(content, encoding="utf-8")
            experiment = recipe.resolve_experiment(path)
            self.assertEqual(experiment.drone_count, 78)
            self.assertEqual(experiment.process_count, 3)
            self.assertEqual(experiment.drones_per_process, 26)

    def test_accepts_headless_mode(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            experiment = recipe.resolve_experiment(
                self._experiment(Path(temporary), visualization=False)
            )
            self.assertFalse(experiment.visualization)

    def test_accepts_one_drone_with_partial_hakoniwa_formation(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            experiment = recipe.resolve_experiment(
                self._experiment(Path(temporary), drones=1, per_process=1)
            )
            self.assertEqual(experiment.drone_count, 1)
            self.assertEqual(experiment.process_count, 1)

    def test_rejects_zero_drones(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            with self.assertRaisesRegex(recipe.RecipeError, ">= 1"):
                recipe.resolve_experiment(
                    self._experiment(Path(temporary), drones=0, per_process=1)
                )

    def test_rejects_more_than_general_user_binary_profile(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            with self.assertRaisesRegex(
                recipe.RecipeError,
                "general-user limit of 200.*512-drone.*PRO.*license",
            ):
                recipe.resolve_experiment(
                    self._experiment(Path(temporary), drones=201, per_process=10)
                )

    def test_accepts_general_user_limit(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            experiment = recipe.resolve_experiment(
                self._experiment(Path(temporary), drones=200, per_process=10)
            )
            self.assertEqual(experiment.drone_count, 200)

    def test_prepare_native_downloads_verifies_and_extracts_linux_release(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            drone_root = root / "hakoniwa-drone-core"
            generator = drone_root / "tools" / "gen_fleet_scale_config.py"
            generator.parent.mkdir(parents=True)
            generator.touch()
            (drone_root / "lnx").mkdir()
            (drone_root / "lnx" / "linux-main_hako_drone_service").write_bytes(
                b"unproven-old-service"
            )
            (drone_root / "lnx" / "linux-drone_visual_state_publisher").write_bytes(
                b"unproven-old-vsp"
            )
            payload = io.BytesIO()
            with zipfile.ZipFile(payload, "w") as archive:
                archive.writestr("lnx/linux-main_hako_drone_service", b"service")
                archive.writestr("lnx/linux-drone_visual_state_publisher", b"vsp")
            archive_bytes = payload.getvalue()
            profile = {
                "Linux": ("lnx.zip", hashlib.sha256(archive_bytes).hexdigest())
            }
            workspace = {
                "mode": "reused",
                "repository": recipe.PUBLIC_DRONE_REPOSITORY_ID,
                "requested_ref": "main",
                "resolved_revision": "abc123",
                "dirty": False,
                "dirty_path_count": 0,
            }
            mujoco = {
                "mode": "downloaded",
                "version_authority": str(drone_root / "MUJOCO_VERSION.txt"),
                "version": "workspace-version",
                "asset": "workspace-selected-mujoco.tar.gz",
                "sha256": "f" * 64,
                "library": str(drone_root / "vendor/mujoco/lib/libmujoco.so.fixture"),
                "link_mode": "runtime-library-path",
            }
            evidence = root / "validation" / "native-distribution.json"
            with mock.patch.object(recipe, "PUBLIC_DRONE_ARCHIVES", profile), mock.patch.object(
                recipe, "prepare_drone_workspace", return_value=workspace
            ), mock.patch.object(
                recipe, "materialize_mujoco_runtime", return_value=mujoco
            ), mock.patch.object(
                recipe.urllib.request, "urlopen", return_value=io.BytesIO(archive_bytes)
            ) as urlopen:
                self.assertEqual(
                    recipe.prepare_native_distribution(
                        drone_root,
                        "Linux",
                        cache_root=root / "downloads",
                        evidence_path=evidence,
                    ),
                    0,
                )
            urlopen.assert_called_once_with(
                "https://github.com/toppers/hakoniwa-drone-core/releases/download/"
                "v4.0.0/lnx.zip"
            )
            self.assertTrue(
                (drone_root / "lnx" / "linux-main_hako_drone_service").is_file()
            )
            self.assertTrue(
                (drone_root / "lnx" / "linux-drone_visual_state_publisher").is_file()
            )
            self.assertEqual(
                (drone_root / "lnx" / "linux-main_hako_drone_service").read_bytes(),
                b"service",
            )
            recorded = json.loads(evidence.read_text(encoding="utf-8"))
            self.assertEqual(recorded["drone_workspace"]["resolved_revision"], "abc123")
            self.assertEqual(recorded["native_distribution"]["mode"], "downloaded")
            self.assertEqual(
                recorded["mujoco_runtime"]["version"], "workspace-version"
            )

    def test_prepare_drone_workspace_clones_latest_main(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            drone_root = Path(temporary) / "hakoniwa-drone-core"

            def run_checked(command, *, cwd=None):
                if command[:2] == ["git", "clone"]:
                    (drone_root / ".git").mkdir(parents=True)
                    generator = drone_root / "tools" / "gen_fleet_scale_config.py"
                    generator.parent.mkdir(parents=True)
                    generator.touch()

            def git_output(_root, *arguments):
                return {
                    ("remote", "get-url", "origin"): recipe.PUBLIC_DRONE_REPOSITORY,
                    ("rev-parse", "HEAD"): "new-main-revision",
                    ("status", "--short"): "",
                }[arguments]

            with mock.patch.object(recipe, "_run_checked", side_effect=run_checked) as run, mock.patch.object(
                recipe, "_git_output", side_effect=git_output
            ):
                evidence = recipe.prepare_drone_workspace(drone_root)

            clone = run.call_args_list[0].args[0]
            self.assertIn("--branch", clone)
            self.assertEqual(clone[clone.index("--branch") + 1], "main")
            self.assertNotIn(recipe.PUBLIC_DRONE_RELEASE, clone)
            self.assertEqual(evidence["mode"], "cloned")
            self.assertEqual(evidence["resolved_revision"], "new-main-revision")

    def test_prepare_drone_workspace_reuses_current_main_and_reports_dirty(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            drone_root = Path(temporary) / "hakoniwa-drone-core"
            (drone_root / ".git").mkdir(parents=True)
            generator = drone_root / "tools" / "gen_fleet_scale_config.py"
            generator.parent.mkdir(parents=True)
            generator.touch()

            def git_output(_root, *arguments):
                return {
                    ("remote", "get-url", "origin"): "git@github.com:toppers/hakoniwa-drone-core.git",
                    ("branch", "--show-current"): "main",
                    ("rev-parse", "HEAD"): "same-revision",
                    ("rev-parse", "origin/main"): "same-revision",
                    ("status", "--short"): " M config/generated.json",
                }[arguments]

            with mock.patch.object(recipe, "_run_checked") as run, mock.patch.object(
                recipe, "_git_output", side_effect=git_output
            ):
                evidence = recipe.prepare_drone_workspace(drone_root)

            self.assertEqual(evidence["mode"], "reused")
            self.assertTrue(evidence["dirty"])
            self.assertEqual(evidence["dirty_path_count"], 1)
            self.assertIn(
                mock.call(["git", "fetch", "origin", "main"], cwd=drone_root),
                run.call_args_list,
            )

    def test_prepare_drone_workspace_fast_forwards_stale_main(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            drone_root = Path(temporary) / "hakoniwa-drone-core"
            (drone_root / ".git").mkdir(parents=True)
            generator = drone_root / "tools" / "gen_fleet_scale_config.py"
            generator.parent.mkdir(parents=True)
            generator.touch()
            revisions = iter(["old-revision", "new-revision", "new-revision"])

            def git_output(_root, *arguments):
                if arguments == ("rev-parse", "HEAD"):
                    return next(revisions)
                return {
                    ("remote", "get-url", "origin"): recipe.PUBLIC_DRONE_REPOSITORY,
                    ("branch", "--show-current"): "main",
                    ("rev-parse", "origin/main"): "new-revision",
                    ("status", "--short"): "",
                }[arguments]

            with mock.patch.object(recipe, "_run_checked") as run, mock.patch.object(
                recipe, "_git_output", side_effect=git_output
            ), mock.patch.object(recipe, "_git_is_ancestor", return_value=True):
                evidence = recipe.prepare_drone_workspace(drone_root)

            self.assertEqual(evidence["mode"], "updated")
            self.assertEqual(evidence["resolved_revision"], "new-revision")
            self.assertIn(
                mock.call(["git", "merge", "--ff-only", "origin/main"], cwd=drone_root),
                run.call_args_list,
            )

    def test_prepare_drone_workspace_rejects_unrelated_checkout(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            drone_root = Path(temporary) / "hakoniwa-drone-core"
            (drone_root / ".git").mkdir(parents=True)
            with mock.patch.object(
                recipe,
                "_git_output",
                return_value="https://github.com/example/unrelated.git",
            ), self.assertRaisesRegex(recipe.RecipeError, "unexpected origin"):
                recipe.prepare_drone_workspace(drone_root)

    def test_linux_mujoco_version_comes_from_drone_workspace(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            drone_root = root / "hakoniwa-drone-core"
            workspace_version = "9.8.7"
            self._mujoco_version(drone_root, mujoco_version=workspace_version)
            archive_buffer = io.BytesIO()
            library_content = b"mujoco-runtime"
            with tarfile.open(fileobj=archive_buffer, mode="w:gz") as archive:
                info = tarfile.TarInfo(
                    f"mujoco-{workspace_version}/lib/libmujoco.so.{workspace_version}"
                )
                info.size = len(library_content)
                archive.addfile(info, io.BytesIO(library_content))
            archive_bytes = archive_buffer.getvalue()
            digest = hashlib.sha256(archive_bytes).hexdigest()
            asset = f"mujoco-{workspace_version}-linux-x86_64.tar.gz"
            checksum = f"{digest}  {asset}\n".encode()

            with mock.patch.object(
                recipe.platform, "machine", return_value="x86_64"
            ), mock.patch.object(
                recipe.urllib.request,
                "urlopen",
                side_effect=[io.BytesIO(checksum), io.BytesIO(archive_bytes)],
            ) as urlopen:
                evidence = recipe.materialize_mujoco_runtime(
                    drone_root, "Linux", root / "downloads"
                )

            self.assertEqual(evidence["version"], workspace_version)
            self.assertEqual(evidence["asset"], asset)
            self.assertEqual(evidence["sha256"], digest)
            self.assertEqual(evidence["link_mode"], "runtime-library-path")
            self.assertEqual(
                (
                    drone_root
                    / f"vendor/mujoco/lib/libmujoco.so.{workspace_version}"
                ).read_bytes(),
                library_content,
            )
            self.assertEqual(urlopen.call_count, 2)

    def test_doctor_reports_missing_declared_linux_library_before_start(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            paths = foundation.resolve_workspace(root, recipe.RECIPE_ID)
            foundation.prepare_workspace(paths)
            experiment_path = self._experiment(
                root, drones=1, per_process=1, visualization=False
            )
            experiment = recipe.resolve_experiment(experiment_path)
            requirements = paths.recipe_config / "foundation-requirements.yaml"
            binary = root / "drone/lnx/linux-main_hako_drone_service"
            binary.parent.mkdir(parents=True)
            binary.touch()
            output = io.StringIO()
            native_runtime = recipe.load_native_runtime_module()
            native_checks = (
                native_runtime.RuntimeCheck(
                    "drone service shared libraries",
                    False,
                    f"missing: libOpenGL.so.0 (declared by native runtime contract; required by {binary})",
                ),
            )

            foundation_api = mock.Mock()
            foundation_api.inspect_foundation.return_value = {"status": "SATISFIED"}
            with mock.patch.object(
                recipe,
                "_load_workspace",
                return_value=(experiment, foundation_api, paths, requirements),
            ), mock.patch.object(
                recipe.platform, "system", return_value="Linux"
            ), mock.patch.object(
                recipe, "load_native_runtime_module", return_value=native_runtime
            ), mock.patch.object(
                native_runtime,
                "validate_requirement",
                return_value=(mock.Mock(), native_checks),
            ), mock.patch.object(
                recipe, "_port_available", return_value=True
            ), mock.patch.object(
                recipe,
                "resolve_foundation_python",
                side_effect=recipe.RecipeError("Foundation Python fixture omitted"),
            ), mock.patch.object(
                recipe, "validate_materialized_experiment", return_value=[]
            ), redirect_stdout(output):
                result = recipe.doctor(experiment_path, root / "drone", root / "viewer")

            self.assertEqual(result, 1)
            diagnostic = output.getvalue()
            self.assertIn("[NG] drone service shared libraries", diagnostic)
            self.assertIn("libOpenGL.so.0", diagnostic)
            self.assertIn("declared by native runtime contract", diagnostic)
            self.assertIn(f"required by {binary}", diagnostic)

    def test_macos_mujoco_runs_workspace_installer_and_linker(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            drone_root = root / "hakoniwa-drone-core"
            tools = drone_root / "tools"
            tools.mkdir(parents=True)
            installer = tools / "install-mujoco-mac.bash"
            linker = tools / "link-mujoco-mac.bash"
            installer.touch()
            linker.touch()
            (drone_root / "mac").mkdir()
            workspace_version = "8.7.6"
            self._mujoco_version(drone_root, mujoco_version=workspace_version)
            archive_bytes = b"fixture-dmg"
            digest = hashlib.sha256(archive_bytes).hexdigest()
            asset = f"mujoco-{workspace_version}-macos-universal2.dmg"
            checksum = f"{digest}  {asset}\n".encode()

            def run_checked(command, *, cwd=None):
                if command[:2] == ["bash", str(installer)]:
                    library = (
                        drone_root
                        / f"vendor/mujoco/lib/libmujoco.{workspace_version}.dylib"
                    )
                    library.parent.mkdir(parents=True)
                    library.write_bytes(b"mujoco-dylib")

            with mock.patch.object(
                recipe.urllib.request,
                "urlopen",
                side_effect=[io.BytesIO(checksum), io.BytesIO(archive_bytes)],
            ), mock.patch.object(recipe, "_run_checked", side_effect=run_checked) as run:
                evidence = recipe.materialize_mujoco_runtime(
                    drone_root, "Darwin", root / "downloads"
                )

            self.assertEqual(evidence["version"], workspace_version)
            self.assertEqual(evidence["asset"], asset)
            self.assertEqual(evidence["link_mode"], "macos-install-name-and-rpath")
            commands = [call.args[0] for call in run.call_args_list]
            self.assertTrue(any(command[:2] == ["bash", str(installer)] for command in commands))
            self.assertTrue(any(command[:2] == ["bash", str(linker)] for command in commands))

    def test_verified_download_reuses_only_matching_cache(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            destination = Path(temporary) / "archive.zip"
            payload = b"verified-cache"
            destination.write_bytes(payload)
            with mock.patch.object(recipe.urllib.request, "urlopen") as urlopen:
                mode = recipe._verified_download(
                    "https://example.invalid/archive.zip",
                    destination,
                    hashlib.sha256(payload).hexdigest(),
                )
            self.assertEqual(mode, "verified-cache")
            urlopen.assert_not_called()

    def test_prepare_native_rejects_windows(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            with self.assertRaisesRegex(recipe.RecipeError, "supports macOS and Linux"):
                recipe.prepare_native_distribution(
                    Path(temporary) / "hakoniwa-drone-core", "Windows"
                )

    def test_linux_runtime_resolves_distribution_and_ld_library_path(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            paths = foundation.resolve_workspace(root, recipe.RECIPE_ID)
            foundation.prepare_workspace(paths)
            python = paths.foundation_python / "bin" / "python3"
            python.parent.mkdir(parents=True, exist_ok=True)
            python.touch()
            drone_root = root / "hakoniwa-drone-core"
            binary = drone_root / "lnx" / "linux-main_hako_drone_service"
            binary.parent.mkdir(parents=True)
            binary.touch()

            self.assertEqual(
                recipe.resolve_drone_binary(drone_root, "Linux"), binary.absolute()
            )
            with mock.patch.dict(recipe.os.environ, {"PATH": "/usr/bin"}, clear=True):
                environment = recipe.runtime_environment(paths, drone_root, "Linux")
            self.assertIn(str(paths.install_prefix / "lib"), environment["LD_LIBRARY_PATH"])
            self.assertIn(str(drone_root / "lib"), environment["LD_LIBRARY_PATH"])
            self.assertNotIn("DYLD_LIBRARY_PATH", environment)

    def test_prepare_native_rejects_archive_path_traversal(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            archive_path = root / "bad.zip"
            with zipfile.ZipFile(archive_path, "w") as archive:
                archive.writestr("../outside", b"bad")
            with self.assertRaisesRegex(recipe.RecipeError, "unsafe path"):
                recipe._safe_extract(archive_path, root / "destination")

    def test_generated_launcher_uses_one_builtin_conductor_owner(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            paths = foundation.resolve_workspace(root, recipe.RECIPE_ID)
            foundation.prepare_workspace(paths)
            experiment = recipe.resolve_experiment(
                self._experiment(root, drones=100, per_process=10)
            )
            drone_root = root / "hakoniwa-drone-core"
            (drone_root / "lib").mkdir(parents=True)
            (drone_root / "lib" / "mac-main_hako_drone_service").touch()
            (drone_root / "lib" / "mac-drone_visual_state_publisher").touch()
            show_runner = (
                drone_root / "drone_api" / "external_rpc" / "apps" / "show_asset_runner.py"
            )
            show_runner.parent.mkdir(parents=True)
            show_runner.touch()
            python = paths.foundation_python / "bin" / "python3"
            python.parent.mkdir(parents=True)
            python.touch()
            (paths.recipe_config / "scenario").mkdir(parents=True)
            (paths.recipe_validation).mkdir(parents=True, exist_ok=True)
            viewer_root = root / "hakoniwa-threejs-drone"
            viewer_root.mkdir()
            (viewer_root / "index.html").touch()
            launcher = recipe.write_launcher(
                paths,
                drone_root,
                viewer_root,
                experiment,
                "Darwin",
            )
            payload = json.loads(launcher.read_text(encoding="utf-8"))
            serialized = json.dumps(payload)
            services = [a for a in payload["assets"] if a["name"].startswith("drone-service-")]

            self.assertEqual(len(services), 10)
            self.assertNotIn("--disable-conductor", services[0]["args"])
            self.assertNotIn("depends_on", services[0])
            for index, service in enumerate(services[1:], start=1):
                self.assertIn("--disable-conductor", service["args"])
                self.assertNotIn("--real-sleep-msec", service["args"])
                self.assertEqual(
                    service["env"]["set"]["HAKO_CONFIG_PATH"],
                    str(paths.foundation_config / "cpp_core_config.json"),
                )
                self.assertEqual(
                    service["depends_on"], [f"drone-service-{index}"]
                )
            assets = {asset["name"]: asset for asset in payload["assets"]}
            self.assertNotIn("conductor-server", assets)
            self.assertNotIn("conductor-client", assets)
            self.assertEqual(
                payload["defaults"]["env"]["set"]["HAKO_CONFIG_PATH"],
                str(paths.foundation_config / "cpp_core_config.json"),
            )
            self.assertNotIn("/usr/local/hakoniwa", serialized)
            self.assertTrue(contains_path(payload, paths.install_prefix))
            self.assertIn("execution-summary.json", serialized)
            show_runner_asset = assets["show-runner"]
            poll_sleep_index = show_runner_asset["args"].index("--poll-sleep-msec")
            self.assertEqual(show_runner_asset["args"][poll_sleep_index + 1], "0")
            final_hold_index = show_runner_asset["args"].index(
                "--final-hold-extra-sec"
            )
            self.assertEqual(show_runner_asset["args"][final_hold_index + 1], "0.0")
            self.assertIn("--real-time-sync", show_runner_asset["args"])
            self.assertIn("visual-state-publisher", serialized)
            self.assertIn("web-bridge-fleets", serialized)
            self.assertIn("threejs-viewer-webserver", serialized)
            self.assertEqual(
                assets["threejs-viewer-webserver"]["activation_timing"],
                "after_start",
            )

    def test_mujoco_city_launcher_remains_alive_until_manual_stop(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            paths = foundation.resolve_workspace(root, recipe.RECIPE_ID)
            foundation.prepare_workspace(paths)
            experiment = recipe.resolve_experiment(
                self._experiment(root, drones=2, per_process=2)
            )
            drone_root = root / "hakoniwa-drone-core"
            (drone_root / "lib").mkdir(parents=True)
            (drone_root / "lib" / "mac-main_hako_drone_service").touch()
            (drone_root / "lib" / "mac-drone_visual_state_publisher").touch()
            show_runner = (
                drone_root / "drone_api" / "external_rpc" / "apps" / "show_asset_runner.py"
            )
            show_runner.parent.mkdir(parents=True)
            show_runner.touch()
            python = paths.foundation_python / "bin" / "python3"
            python.parent.mkdir(parents=True)
            python.touch()
            (paths.recipe_config / "scenario").mkdir(parents=True)
            paths.recipe_validation.mkdir(parents=True, exist_ok=True)
            city_glb = root / "city-world.glb"
            city_glb.write_bytes(b"city-glb")
            (paths.recipe_config / "mujoco-city-fleet.json").write_text(
                json.dumps(
                    {
                        "drone_count": 2,
                        "city_world": {
                            "glb": str(city_glb),
                            "origin": {
                                "latitude": 35.0988,
                                "longitude": 138.8587,
                            },
                            "half_extent_m": {
                                "north_south": 100.0,
                                "east_west": 80.0,
                            },
                        }
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            viewer_root = root / "hakoniwa-threejs-drone"
            viewer_root.mkdir()
            map_viewer_root = root / "hakoniwa-map-viewer"
            map_client = map_viewer_root / "src" / "client"
            (map_client / "src").mkdir(parents=True)
            (map_viewer_root / "images").mkdir()
            (map_client / "index.html").write_text(
                "<div id='map'></div>", encoding="utf-8"
            )
            (map_client / "src" / "ui.js").write_text(
                "const map = L.map('map').setView([35.6812, 139.7671], 15); // 東京駅\n"
                "let ORIGIN_LAT = 35.6625;   // zone の原点（仮）\n"
                "let ORIGIN_LON = 139.70625;\n"
                "setInterval(() => {\n      if (!viewer) return;\n"
                "      const drones = viewer.getDrones();\n",
                encoding="utf-8",
            )
            (viewer_root / "index.html").touch()
            for dirname in ("src", "config", "assets", "thirdparty"):
                (viewer_root / dirname).mkdir()
            (viewer_root / "config" / "drone_config-compact-1.json").write_text(
                json.dumps(
                    {
                        "version": "1.0",
                        "format": "compact",
                        "environments": [],
                        "main_camera": {},
                        "droneTypesPath": "./drone-types.json",
                        "drones": [],
                    }
                ),
                encoding="utf-8",
            )
            (viewer_root / "config" / "viewer-config-fleets.json").write_text(
                json.dumps(
                    {
                        "version": "1.0",
                        "three": {"sceneConfigPath": "./old.json"},
                        "pdu": {
                            "pduDefPath": "./pdudef.json",
                            "wsUri": "ws://127.0.0.1:8765",
                            "wireVersion": "v2",
                        },
                        "stateInput": {
                            "mode": "fleets",
                            "fleets": {
                                "roleMap": {
                                    "visual_state_array": "hako_msgs/DroneVisualStateArray"
                                }
                            },
                        },
                    }
                ),
                encoding="utf-8",
            )

            launcher = recipe.write_launcher(
                paths, drone_root, viewer_root, experiment, "Darwin"
            )
            payload = json.loads(launcher.read_text(encoding="utf-8"))
            show_runner_asset = next(
                asset for asset in payload["assets"] if asset["name"] == "show-runner"
            )
            final_hold_index = show_runner_asset["args"].index(
                "--final-hold-extra-sec"
            )
            self.assertEqual(
                show_runner_asset["args"][final_hold_index + 1], "86400.0"
            )
            city_viewer = (
                paths.recipe_root
                / "web"
                / "map-viewer"
                / "thirdparty"
                / "hakoniwa-threejs-drone"
            )
            generated_viewer = json.loads(
                (city_viewer / "config" / "viewer-config-fleets.json").read_text(
                    encoding="utf-8"
                )
            )
            generated_scene = json.loads(
                (city_viewer / "config" / "drone_config-city-fleet.json").read_text(
                    encoding="utf-8"
                )
            )
            self.assertEqual(
                generated_viewer["three"]["sceneConfigPath"],
                "./drone_config-city-fleet.json",
            )
            self.assertTrue(
                generated_viewer["stateInput"]["fleets"]["dynamicSpawn"]
            )
            self.assertEqual(
                generated_viewer["stateInput"]["fleets"]["maxDynamicDrones"], 2
            )
            self.assertEqual(
                generated_scene["environments"][0]["model"],
                "../assets/local_models/city-world.glb",
            )
            self.assertEqual(
                (city_viewer / "assets" / "local_models" / "city-world.glb").read_bytes(),
                b"city-glb",
            )
            generated_map_ui = (
                paths.recipe_root
                / "web"
                / "map-viewer"
                / "src"
                / "client"
                / "src"
                / "ui.js"
            ).read_text(encoding="utf-8")
            self.assertIn("[35.0988, 138.8587], 17", generated_map_ui)
            self.assertNotIn("await viewer.syncDroneStates()", generated_map_ui)

    def test_headless_launcher_omits_visualization_processes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            paths = foundation.resolve_workspace(root, recipe.RECIPE_ID)
            foundation.prepare_workspace(paths)
            experiment = recipe.resolve_experiment(
                self._experiment(root, drones=26, per_process=26, visualization=False)
            )
            drone_root = root / "hakoniwa-drone-core"
            (drone_root / "lib").mkdir(parents=True)
            (drone_root / "lib" / "mac-main_hako_drone_service").touch()
            show_runner = (
                drone_root / "drone_api" / "external_rpc" / "apps" / "show_asset_runner.py"
            )
            show_runner.parent.mkdir(parents=True)
            show_runner.touch()
            python = paths.foundation_python / "bin" / "python3"
            python.parent.mkdir(parents=True)
            python.touch()
            (paths.recipe_config / "scenario").mkdir(parents=True)
            paths.recipe_validation.mkdir(parents=True, exist_ok=True)
            launcher = recipe.write_launcher(
                paths,
                drone_root,
                root / "viewer-does-not-exist",
                experiment,
                "Darwin",
            )
            payload = json.loads(launcher.read_text(encoding="utf-8"))
            asset_names = {asset["name"] for asset in payload["assets"]}
            self.assertNotIn("conductor-server", asset_names)
            self.assertNotIn("conductor-client", asset_names)
            self.assertIn("drone-service-1", asset_names)
            self.assertIn("show-runner", asset_names)
            self.assertNotIn("visual-state-publisher", asset_names)
            self.assertNotIn("web-bridge-fleets", asset_names)
            self.assertNotIn("threejs-viewer-webserver", asset_names)
            drone_service = next(
                asset
                for asset in payload["assets"]
                if asset["name"] == "drone-service-1"
            )
            self.assertNotIn("--real-sleep-msec", drone_service["args"])

    def test_foundation_requirements_are_parseable_by_resolver(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            experiment = recipe.resolve_experiment(self._experiment(root))
            output = root / "requirements.yaml"
            recipe.write_foundation_requirements(output, experiment)
            requirements = foundation.load_foundation_requirements(output)
            self.assertEqual(
                requirements["hakoniwa-core-pro"]["build_limits"]["service_max"]["min"],
                512,
            )
            self.assertTrue(
                requirements["hakoniwa-pdu-python"]["capabilities"]["external_rpc"]
            )
            self.assertTrue(
                requirements["hakoniwa-pdu-bridge-core"]["capabilities"][
                    "web_bridge_fleets_config_format"
                ]
            )

    def test_headless_requirements_omit_web_bridge(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            experiment = recipe.resolve_experiment(
                self._experiment(root, visualization=False)
            )
            output = root / "requirements.yaml"
            recipe.write_foundation_requirements(output, experiment)
            requirements = foundation.load_foundation_requirements(output)
            self.assertIn("hakoniwa-core-pro", requirements)
            self.assertIn("hakoniwa-pdu-python", requirements)
            self.assertIn("hakoniwa-pdu-endpoint", requirements)
            self.assertNotIn("hakoniwa-pdu-bridge-core", requirements)

    def test_open_viewer_rejects_headless_experiment(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = self._experiment(Path(temporary), visualization=False)
            with self.assertRaisesRegex(recipe.RecipeError, "headless experiment"):
                recipe.open_viewer(path)

    def test_static_recipe_requirements_are_parseable_by_resolver(self) -> None:
        requirements = foundation.load_foundation_requirements(recipe.recipe_file())
        for component in (
            "hakoniwa-core-pro",
            "hakoniwa-pdu-python",
            "hakoniwa-pdu-endpoint",
            "hakoniwa-pdu-bridge-core",
        ):
            minimum = requirements[component]["build_limits"]["asset_num"]["min"]
            self.assertIsInstance(minimum, int, component)

    def test_session_file_uses_recipe_runtime_directory(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            paths = foundation.resolve_workspace(Path(temporary), recipe.RECIPE_ID)
            self.assertEqual(
                recipe.session_file(paths),
                paths.recipe_root / "runtime" / "launcher-session.json",
            )

    def test_viewer_url_enables_dynamic_spawn_for_resolved_drone_count(self) -> None:
        url = recipe.viewer_url(26)
        self.assertIn("dynamicSpawn=true", url)
        self.assertIn("templateDroneIndex=0", url)
        self.assertIn("maxDynamicDrones=26", url)

    def test_open_browser_only_prints_url_on_non_macos(self) -> None:
        with mock.patch.object(recipe, "is_wsl", return_value=False), mock.patch(
            "builtins.print"
        ) as output, mock.patch.object(
            recipe.platform, "system", return_value="Linux"
        ):
            self.assertTrue(recipe.open_browser("http://127.0.0.1:8000/test?a=1&b=2"))
        output.assert_called_once_with(
            "Open this URL in a browser: http://127.0.0.1:8000/test?a=1&b=2"
        )

    def test_open_browser_uses_macos_open_command(self) -> None:
        url = "http://127.0.0.1:8000/test?a=1&b=2"
        completed = mock.Mock(returncode=0)
        with mock.patch.object(recipe, "is_wsl", return_value=False), mock.patch.object(
            recipe.platform, "system", return_value="Darwin"
        ), mock.patch.object(
            recipe.subprocess, "run", return_value=completed
        ) as run:
            self.assertTrue(recipe.open_browser(url))
        run.assert_called_once_with(["open", url], check=False)

    def test_prepare_viewer_updates_existing_submodules(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            viewer_root = Path(temporary) / "hakoniwa-threejs-drone"
            (viewer_root / ".git").mkdir(parents=True)
            for required in recipe.viewer_required_files(viewer_root):
                required.parent.mkdir(parents=True, exist_ok=True)
                required.touch()
            with mock.patch.object(recipe, "_run_checked") as run:
                self.assertEqual(recipe.prepare_viewer(viewer_root), 0)
            run.assert_called_once_with(
                ["git", "submodule", "update", "--init", "--recursive"],
                cwd=viewer_root,
            )

    def test_prepare_viewer_rejects_incomplete_checkout(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            viewer_root = Path(temporary) / "hakoniwa-threejs-drone"
            viewer_root.mkdir()
            with self.assertRaisesRegex(recipe.RecipeError, "pdu-javascript"):
                recipe.prepare_viewer(viewer_root)

if __name__ == "__main__":
    unittest.main()
