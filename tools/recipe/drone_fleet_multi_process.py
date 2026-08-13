#!/usr/bin/env python3
"""Facade for one Experiment B multi-process performance condition."""

from __future__ import annotations

import drone_fleet_single_host as operator


operator.RECIPE_ID = "drone-fleet-multi-process-scaling"
operator.OPERATOR_NAME = "drone_fleet_multi_process.py"
operator.DEFAULT_EXPERIMENT = (
    operator.ROOT
    / "recipes"
    / "experiments"
    / "drone-fleet-performance"
    / "multi-process-scaling.yaml"
)

if __name__ == "__main__":
    raise SystemExit(operator.main())
