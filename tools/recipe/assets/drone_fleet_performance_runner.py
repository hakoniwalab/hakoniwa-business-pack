#!/usr/bin/env python3
"""Measure a Drone Fleet run inside the existing ShowRunner Hakoniwa asset."""

from __future__ import annotations

import importlib.util
import json
import os
import shutil
import sys
import time
from pathlib import Path


def _load_upstream():
    drone_root = Path(os.environ["HAKO_DRONE_ROOT"]).resolve()
    script = drone_root / "drone_api" / "external_rpc" / "apps" / "show_asset_runner.py"
    sys.path.insert(0, str(drone_root))
    sys.path.insert(0, str(script.parent))
    spec = importlib.util.spec_from_file_location("hako_upstream_show_runner", script)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load Drone ShowRunner: {script}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


upstream = _load_upstream()

from hakoniwa_measurement import (  # noqa: E402
    HakoniwaTimeObserver,
    JsonLinesWriter,
    MachineResourceResult,
    MachineResourceMonitor,
    MeasurementResultSet,
    SimulationExecutionMeter,
    write_json_atomic,
)


class PerformanceShowStateMachine(upstream.AssetShowStateMachine):
    def __init__(self, args) -> None:
        self.fleet_phase_results: list[dict[str, object]] = []
        super().__init__(args)
        config_path = Path(os.environ["HAKO_PERFORMANCE_CONFIG"]).resolve()
        self.measurement_config = json.loads(config_path.read_text(encoding="utf-8"))
        self.trial_dir = Path(self.measurement_config["trial_directory"]).resolve()
        self.performance_meter = SimulationExecutionMeter(
            int(self.measurement_config["time_coordination"]["conductor_delta_time_usec"])
        )
        self.machine_monitor = MachineResourceMonitor(
            float(self.measurement_config["sampling_interval_sec"])
        )
        self.measurement_mode = str(self.measurement_config["mode"])
        self.temporal_observer = (
            HakoniwaTimeObserver.from_hakopy()
            if self.measurement_mode == "temporal"
            else None
        )
        self.temporal_samples_writer: JsonLinesWriter | None = None
        self.temporal_sampling_interval_usec = self.measurement_config.get(
            "temporal_sampling_interval_usec"
        )
        self.next_temporal_sample_world_usec: int | None = None
        self.samples_writer: JsonLinesWriter | None = None
        self.machine_preflight = None
        self.preflight_passed: bool | None = None
        self.preflight_collected = False
        self.preflight_boundary_start = "after_asset_activation_before_takeoff"
        self.measurement_target_world_usec: int | None = None
        self.measurement_minimum_world_usec: int | None = None
        self.measurement_start_monotonic_ns: int | None = None
        self.formation_completed = False
        self.formation_deadline_met: bool | None = None
        self.stop_reason: str | None = None
        self.invalid_reason: str | None = None
        self.measurement_started = False
        self.measurement_finished = False

    def _record_fleet_result(self, phase_name: str, results: list) -> None:
        failed_drones: list[str] = []
        for index, drone_name in enumerate(self.drone_names):
            response = results[index] if index < len(results) else RuntimeError("missing response")
            if isinstance(response, Exception) or not bool(getattr(response, "ok", False)):
                failed_drones.append(drone_name)
        total = len(self.drone_names)
        succeeded = total - len(failed_drones)
        status = "PASS" if not failed_drones and len(results) == total else "FAIL"
        summary = {
            "phase": phase_name,
            "total": total,
            "succeeded": succeeded,
            "failed": len(failed_drones),
            "failed_drones": failed_drones,
            "status": status,
        }
        self.fleet_phase_results.append(summary)
        failed_text = ",".join(failed_drones) if failed_drones else "none"
        print(
            "RESULT: fleet_phase "
            f"name={phase_name} total={total} succeeded={succeeded} "
            f"failed={len(failed_drones)} failed_drones={failed_text} status={status}"
        )

    def _on_simple_complete(self, phase_name: str):
        upstream_handler = super()._on_simple_complete(phase_name)

        def _handler(results: list) -> None:
            self._record_fleet_result(phase_name, results)
            upstream_handler(results)
            self._extend_final_phase_to_measurement_endpoint()

        return _handler

    def _extend_final_phase_to_measurement_endpoint(self) -> None:
        if self.phase_index != len(self.phases) - 1:
            return
        if self.measurement_target_world_usec is None:
            raise RuntimeError("measurement target is not initialized")
        world_usec = int(upstream.hakopy.simulation_time())
        self.formation_deadline_met = world_usec <= self.measurement_target_world_usec
        remaining_usec = max(0, self.measurement_target_world_usec - world_usec)
        self.hold_remaining_usec = max(self.hold_remaining_usec, remaining_usec)

    def _make_goto_complete(self, *, fid: str, step: dict):
        upstream_handler = super()._make_goto_complete(fid=fid, step=step)
        phase_name = f"goto:{fid}"

        def _handler(results: list) -> None:
            self._record_fleet_result(phase_name, results)
            upstream_handler(results)
            self._extend_final_phase_to_measurement_endpoint()

        return _handler

    def _start_measurement(self) -> None:
        now_ns = time.monotonic_ns()
        world_usec = int(upstream.hakopy.simulation_time())
        self.performance_meter.start(world_usec, now_ns)
        stop_conditions = self.measurement_config["stop_conditions"]
        invalid_conditions = self.measurement_config["invalid_conditions"]
        self.measurement_minimum_world_usec = world_usec + int(
            float(stop_conditions["minimum_virtual_time_sec"]) * 1_000_000
        )
        self.measurement_target_world_usec = world_usec + int(
            float(invalid_conditions["maximum_virtual_time_sec"])
            * 1_000_000
        )
        self.measurement_start_monotonic_ns = now_ns
        self.machine_monitor.start(now_ns)
        self.samples_writer = JsonLinesWriter(self.trial_dir / "machine-samples.jsonl")
        if self.temporal_observer is not None:
            self.temporal_samples_writer = JsonLinesWriter(
                self.trial_dir / "temporal-samples.jsonl"
            )
            self.next_temporal_sample_world_usec = world_usec
        self.measurement_started = True
        print(f"INFO: performance_measurement_start world_usec={world_usec}")

    def _collect_machine_preflight(self) -> None:
        if self.preflight_collected:
            return
        external_path = self.measurement_config.get("host_preflight_result_path")
        if external_path is not None:
            payload = json.loads(Path(external_path).read_text(encoding="utf-8"))
            machine = payload.get("machine")
            if not isinstance(machine, dict):
                raise RuntimeError("host preflight result does not contain machine data")
            self.machine_preflight = MachineResourceResult(**machine)
            self.preflight_passed = payload.get("passed") is True
            self.preflight_boundary_start = str(
                payload.get("boundary", "before_asset_activation")
            )
            samples_path = payload.get("samples_path")
            if isinstance(samples_path, str):
                shutil.copyfile(
                    samples_path,
                    self.trial_dir / "preflight-machine-samples.jsonl",
                )
            self.preflight_collected = True
            print(
                "RESULT: machine_preflight "
                f"samples={self.machine_preflight.sample_count} "
                f"cpu_average_percent={self.machine_preflight.cpu_average_percent} "
                "source=before_asset_activation "
                f"status={'PASS' if self.preflight_passed else 'FAIL'}"
            )
            return
        duration_sec = float(self.measurement_config["preflight_duration_sec"])
        interval_sec = float(self.measurement_config["sampling_interval_sec"])
        monitor = MachineResourceMonitor(interval_sec)
        samples_path = self.trial_dir / "preflight-machine-samples.jsonl"
        deadline_ns = time.monotonic_ns() + int(duration_sec * 1_000_000_000)
        monitor.start()
        with JsonLinesWriter(samples_path) as writer:
            while True:
                remaining_ns = deadline_ns - time.monotonic_ns()
                if remaining_ns <= 0:
                    break
                time.sleep(min(interval_sec, remaining_ns / 1_000_000_000))
                sample = monitor.poll_if_due()
                if sample is not None:
                    writer.write(sample)
            sample = monitor.sample_now()
            writer.write(sample)
        self.machine_preflight = monitor.finish()
        cpu_limit = float(
            self.measurement_config["invalid_conditions"]
            ["preflight_max_cpu_average_percent"]
        )
        memory_limit = float(
            self.measurement_config["invalid_conditions"]
            ["preflight_max_memory_used_percent"]
        )
        cpu_average = self.machine_preflight.cpu_average_percent
        memory_max = self.machine_preflight.memory_used_max_percent
        self.preflight_passed = (
            self.machine_preflight.invalid_sample_count == 0
            and cpu_average is not None
            and cpu_average <= cpu_limit
            and memory_max is not None
            and memory_max <= memory_limit
        )
        self.preflight_collected = True
        print(
            "RESULT: machine_preflight "
            f"samples={self.machine_preflight.sample_count} "
            f"cpu_average_percent={cpu_average} cpu_limit_percent={cpu_limit} "
            f"memory_max_percent={memory_max} memory_limit_percent={memory_limit} "
            f"status={'PASS' if self.preflight_passed else 'FAIL'}"
        )

    def _poll_machine(self) -> None:
        if not self.measurement_started or self.measurement_finished:
            return
        sample = self.machine_monitor.poll_if_due()
        if sample is not None and self.samples_writer is not None:
            self.samples_writer.write(sample)

    def _poll_temporal(self) -> None:
        if (
            not self.measurement_started
            or self.measurement_finished
            or self.temporal_observer is None
            or self.temporal_samples_writer is None
            or self.next_temporal_sample_world_usec is None
        ):
            return
        world_usec = int(upstream.hakopy.simulation_time())
        if world_usec < self.next_temporal_sample_world_usec:
            return
        sample = self.temporal_observer.observe()
        self.temporal_samples_writer.write(sample)
        interval = int(self.temporal_sampling_interval_usec)
        self.next_temporal_sample_world_usec = (
            (world_usec // interval) + 1
        ) * interval

    def _finish_measurement(self) -> None:
        if not self.measurement_started or self.measurement_finished:
            return
        now_ns = time.monotonic_ns()
        world_usec = int(upstream.hakopy.simulation_time())
        sample = self.machine_monitor.sample_now(now_ns)
        if self.samples_writer is not None:
            self.samples_writer.write(sample)
            self.samples_writer.close()
        if self.temporal_samples_writer is not None:
            self.temporal_samples_writer.close()
        machine_result = self.machine_monitor.finish()
        result = MeasurementResultSet(
            run_id=(
                f"{self.measurement_config['configuration_id']}-"
                f"attempt-{int(self.measurement_config['attempt']):02d}"
            ),
            mode=self.measurement_mode,
            minimum_machine_cpu_sample_count=int(
                self.measurement_config["stop_conditions"]
                ["minimum_cpu_sample_count"]
            ),
            status=(
                "success"
                if (
                    self.preflight_passed
                    and self.formation_deadline_met
                    and self.invalid_reason is None
                )
                else "invalid"
            ),
            performance=self.performance_meter.finish(world_usec, now_ns),
            machine_preflight=self.machine_preflight,
            machine=machine_result,
            temporal=(
                self.temporal_observer.result()
                if self.temporal_observer is not None
                else None
            ),
            metadata={
                **self.measurement_config,
                "measurement_boundary": {
                    "start": "takeoff_submit",
                    "end": "adaptive_conditions_satisfied",
                    "minimum_world_usec": self.measurement_minimum_world_usec,
                    "maximum_world_usec": self.measurement_target_world_usec,
                    "formation_completed": self.formation_completed,
                    "formation_deadline_met": self.formation_deadline_met,
                    "stop_reason": self.stop_reason,
                    "invalid_reason": self.invalid_reason,
                },
                "preflight_boundary": {
                    "start": self.preflight_boundary_start,
                    "duration_sec": self.measurement_config["preflight_duration_sec"],
                    "included_in_performance_measurement": False,
                    "passed": self.preflight_passed,
                },
                "temporal_observer_enabled": self.temporal_observer is not None,
                "host": {"platform": sys.platform},
                "fleet_phase_results": self.fleet_phase_results,
            },
        )
        result.validate()
        write_json_atomic(self.trial_dir / "result.json", result)
        self.measurement_finished = True
        print(
            "INFO: performance_measurement_done "
            f"world_usec={world_usec} status={result.status}"
        )
        fleet_passed = (
            len(self.fleet_phase_results) == len(self.phases)
            and all(item["status"] == "PASS" for item in self.fleet_phase_results)
        )
        overall = "PASS" if result.status == "success" and fleet_passed else "FAIL"
        print(
            "RESULT: drone_fleet_performance "
            f"run_id={result.run_id} phases={len(self.fleet_phase_results)}/"
            f"{len(self.phases)} fleet={'PASS' if fleet_passed else 'FAIL'} "
            f"measurement={result.status.upper()} validation="
            f"{'PASS' if result.validation and result.validation.passed else 'FAIL'} "
            f"status={overall}"
        )

    def _cpu_sample_count(self) -> int:
        return sum(
            1
            for sample in self.machine_monitor.samples
            if sample.cpu_percent is not None
        )

    def _evaluate_adaptive_stop(self) -> bool:
        if not self.measurement_started or self.measurement_finished:
            return False
        if (
            self.measurement_minimum_world_usec is None
            or self.measurement_target_world_usec is None
            or self.measurement_start_monotonic_ns is None
        ):
            raise RuntimeError("adaptive measurement boundary is not initialized")
        world_usec = int(upstream.hakopy.simulation_time())
        wall_sec = (
            time.monotonic_ns() - self.measurement_start_monotonic_ns
        ) / 1_000_000_000
        stop_conditions = self.measurement_config["stop_conditions"]
        invalid_conditions = self.measurement_config["invalid_conditions"]
        cpu_samples = self._cpu_sample_count()
        machine_samples = len(self.machine_monitor.samples)
        if (
            self.formation_completed
            and world_usec >= self.measurement_minimum_world_usec
            and cpu_samples >= int(stop_conditions["minimum_cpu_sample_count"])
            and machine_samples
            >= int(stop_conditions["minimum_machine_sample_count"])
        ):
            self.stop_reason = "adaptive_conditions_satisfied"
            return True
        if world_usec >= self.measurement_target_world_usec:
            self.invalid_reason = "maximum_virtual_time_exceeded"
            self.stop_reason = "invalid_limit_reached"
            return True
        if wall_sec >= float(invalid_conditions["maximum_wall_time_sec"]):
            self.invalid_reason = "maximum_wall_time_exceeded"
            self.stop_reason = "invalid_limit_reached"
            return True
        return False

    def _mark_done(self) -> None:
        self.done = True
        self.hold_remaining_usec = 0
        self.final_settle_remaining_usec = 0
        total_sec = time.perf_counter() - self.total_t0
        self.phase_times["total"] = total_sec
        print("INFO: asset_show_done")
        print(f"INFO: phase_time name=total sec={total_sec:.3f}")
        self._write_summary("done")

    def step_once(self) -> None:
        previous_index = self.phase_index
        had_pending = bool(self.pending)
        phase_name = (
            self.phases[self.phase_index].name
            if self.phase_index < len(self.phases)
            else None
        )
        if (
            not self.measurement_started
            and self.prepared
            and not self.pending
            and self.hold_remaining_usec == 0
            and phase_name == "takeoff"
        ):
            self._collect_machine_preflight()
            self._start_measurement()
        super().step_once()
        if (
            previous_index == 0
            and self.phase_index == 1
            and float(self.measurement_config["warmup_virtual_time_sec"]) > 0
        ):
            self.hold_remaining_usec = int(
                float(self.measurement_config["warmup_virtual_time_sec"]) * 1_000_000
            )
        self._poll_machine()
        self._poll_temporal()
        if (
            had_pending
            and previous_index == len(self.phases) - 1
            and self.phase_index >= len(self.phases)
        ):
            self.formation_completed = True
        if self._evaluate_adaptive_stop():
            self._finish_measurement()
            self._mark_done()


upstream.AssetShowStateMachine = PerformanceShowStateMachine


if __name__ == "__main__":
    raise SystemExit(upstream.main())
