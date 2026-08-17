# Performance result layouts

`drone-fleet-performance.yaml` is the repository-managed path authority for
Experiment A, B, and C performance and Temporal Validation results. It records the paths already used by the
current measurement workflow; it does not move or rewrite any result.

The distinction is intentional:

- A and B run independently on each machine, so collection adds the `mac` or
  `wsl2` machine dimension under `exp-results/`.
- C already records the `srv-01` or `cli-01` host dimension inside its result
  series, so collection preserves that host path in the server workspace.
- B and C Temporal Validation are separate series; their observer-instrumented
  results are never mixed into the performance series.
- `transfer_groups` packages each Performance series together with its matching
  Temporal Validation series while preserving their separate destinations.

ZIP transfer, placement, aggregation, and plotting tools resolve paths from
this file instead of duplicating path literals. Measurement commands remain
independent from collection and analysis.

The machine-readable contract is
`schemas/result-layouts/drone-fleet-performance.schema.json`. The dependency-free
loader and resolver are in `tools/result_layout.py`.
