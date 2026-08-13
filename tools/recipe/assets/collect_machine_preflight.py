#!/usr/bin/env python3
"""Collect whole-machine load before benchmark assets are activated."""

from __future__ import annotations

import argparse
import time
from dataclasses import asdict
from pathlib import Path

from hakoniwa_measurement import (
    JsonLinesWriter,
    MachineResourceMonitor,
    write_json_atomic,
)


def _sample_window(args: argparse.Namespace, samples_path: Path):
    samples_path.unlink(missing_ok=True)
    monitor = MachineResourceMonitor(args.sampling_interval_sec)
    deadline_ns = time.monotonic_ns() + int(args.duration_sec * 1_000_000_000)
    monitor.start()
    with JsonLinesWriter(samples_path) as writer:
        while True:
            remaining_ns = deadline_ns - time.monotonic_ns()
            if remaining_ns <= 0:
                break
            time.sleep(
                min(args.sampling_interval_sec, remaining_ns / 1_000_000_000)
            )
            sample = monitor.poll_if_due()
            if sample is not None:
                writer.write(sample)
        writer.write(monitor.sample_now())
    return monitor.finish()


def _passed(args: argparse.Namespace, result) -> bool:
    return (
        result.invalid_sample_count == 0
        and result.cpu_average_percent is not None
        and result.cpu_average_percent <= args.cpu_limit_percent
        and result.memory_used_max_percent is not None
        and result.memory_used_max_percent <= args.memory_limit_percent
    )


def collect(args: argparse.Namespace) -> int:
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.samples.parent.mkdir(parents=True, exist_ok=True)
    started_ns = time.monotonic_ns()
    deadline_ns = started_ns + int(args.settle_timeout_sec * 1_000_000_000)
    rejected_windows = []
    window_index = 0
    while True:
        window_index += 1
        candidate = args.samples.with_name(
            f".{args.samples.name}.window-{window_index}"
        )
        result = _sample_window(args, candidate)
        passed = _passed(args, result)
        print(
            "INFO: host_machine_preflight_window "
            f"index={window_index} cpu_average_percent={result.cpu_average_percent} "
            f"memory_max_percent={result.memory_used_max_percent} "
            f"status={'PASS' if passed else 'WAIT'}"
        )
        if passed or time.monotonic_ns() >= deadline_ns:
            candidate.replace(args.samples)
            break
        rejected_windows.append(asdict(result))
        candidate.unlink(missing_ok=True)
    wait_sec = (time.monotonic_ns() - started_ns) / 1_000_000_000
    write_json_atomic(
        args.output,
        {
            "boundary": "before_asset_activation",
            "passed": passed,
            "cpu_limit_percent": args.cpu_limit_percent,
            "memory_limit_percent": args.memory_limit_percent,
            "samples_path": str(args.samples.resolve()),
            "settle_timeout_sec": args.settle_timeout_sec,
            "settle_elapsed_sec": wait_sec,
            "window_count": window_index,
            "rejected_windows": rejected_windows,
            "machine": asdict(result),
        },
        replace=True,
    )
    print(
        "RESULT: host_machine_preflight "
        f"cpu_average_percent={result.cpu_average_percent} "
        f"cpu_limit_percent={args.cpu_limit_percent} "
        f"memory_max_percent={result.memory_used_max_percent} "
        f"memory_limit_percent={args.memory_limit_percent} "
        f"status={'PASS' if passed else 'FAIL'}"
    )
    return 0 if passed else 1


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser()
    result.add_argument("--duration-sec", type=float, required=True)
    result.add_argument("--sampling-interval-sec", type=float, required=True)
    result.add_argument("--settle-timeout-sec", type=float, required=True)
    result.add_argument("--cpu-limit-percent", type=float, required=True)
    result.add_argument("--memory-limit-percent", type=float, required=True)
    result.add_argument("--output", type=Path, required=True)
    result.add_argument("--samples", type=Path, required=True)
    return result


if __name__ == "__main__":
    raise SystemExit(collect(parser().parse_args()))
