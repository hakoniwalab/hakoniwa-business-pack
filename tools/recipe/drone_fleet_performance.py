#!/usr/bin/env python3
"""Facade for the single-process Drone Fleet performance series."""

from __future__ import annotations

import drone_fleet_single_host as operator


operator.RECIPE_ID = "drone-fleet-single-process-scaling"
operator.OPERATOR_NAME = "drone_fleet_performance.py"
operator.DEFAULT_EXPERIMENT = (
    operator.ROOT
    / "recipes"
    / "experiments"
    / "drone-fleet-performance"
    / "single-process-scaling.yaml"
)
if __name__ == "__main__":
    raise SystemExit(operator.main())
