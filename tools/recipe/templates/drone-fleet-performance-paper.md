# Drone Fleet Performance Results

> This file is generated from the official datasets under `exp-results/`.
> Edit the template or generator, not the generated Markdown.

## Experimental conditions

{{protocol_table}}

<!-- PAPER-TODO: Add the final CPU model, physical/logical core count, RAM,
OS/kernel, power policy, NIC link speed, MTU, and baseline RTT. These values are
not inferred from runtime utilization samples. -->

## Single-process scalability boundary

{{figure_a}}

{{experiment_a_observations}}

<!-- PAPER-TODO: Explain that this is machine characterization used to select
the later workloads, not a claim that one operating system is intrinsically
faster than another. -->

## Multi-process performance recovery

{{figure_b}}

{{experiment_b_table}}

{{experiment_b_observations}}

<!-- PAPER-TODO: Discuss useful process-count regions, recovery from the
single-process boundary, and degradation after over-parallelization. -->

## Multi-host end-to-end scaling

{{figure_c}}

{{experiment_c_table}}

{{experiment_c_observations}}

<!-- PAPER-TODO: The Mac/WSL2 lines are single-host references, not a strict
placement-only speedup baseline. Experiment B uses the embedded Conductor,
whereas Experiment C uses the external Conductor and TCP. -->

## Resource utilization

### Experiment B: comparison of median step time by process count

{{resource_b_table}}

### Experiment C: per-host utilization

{{resource_c_table}}

{{resource_observations}}

<!-- PAPER-TODO: CPU is whole-machine utilization. Compare changes within the
same host; do not treat Mac and WSL2 percentages as normalized compute-capacity
measurements. Memory is shown in GiB because percentages use different host
capacities. -->

## Temporal sanity validation

{{temporal_table}}

{{temporal_observations}}

<!-- PAPER-TODO: Treat this as a descriptive sanity validation at the selected
maximum conditions. Do not claim an atomic process-time spread or a general
mathematical synchronization guarantee. -->

## Reporting notes

- `T_step` is the primary performance metric.
- The horizontal `T_step = 1 ms` line corresponds to `RTF = 1` because the
  authoritative Core progression granularity is 1 ms.
- Experiment A has one measured attempt per condition and therefore has no
  error bars.
- Experiment B and C points use the median; whiskers show the observed minimum
  and maximum.
- Temporal Validation is generated from a separate observer-enabled dataset
  and is never mixed into the headline performance aggregation.

## Table field definitions

| Field | Meaning |
| --- | --- |
| Dataset | Experiment identity. `Temporal` denotes a dedicated observer-enabled validation dataset, not a performance run. |
| Hosts | Physical/execution environments participating in that dataset. `Separately` means independent single-host runs. |
| UAV workload / UAV / Total UAV | Number of simulated UAV Execution Units. In C, `Total UAV` is the sum across both hosts. |
| Split | Static UAV placement across Mac and WSL2, written as `Mac+WSL2`. |
| Simulator processes / Processes | Number of Drone simulator processes. In C, values are written as `Mac+WSL2`. Fleet/Show and Conductor processes are not included. |
| Configurations compared | Resource values for `1 process → the process count with the smallest median step time among those tested`. |
| Attempts / `n` | Number of measured attempts represented by the row. Triggered extension conditions contain five attempts; other B/C conditions contain three. |
| `T_step` | Average wall-clock seconds per authoritative 1 ms Core simulation step, displayed in milliseconds. Smaller is faster. |
| Median step time with 1 process | Median of the per-attempt average step times for the same host and UAV workload using one simulator process. This is the process-partitioning baseline. |
| Process count with smallest median | Number of simulator processes whose median step time is the smallest among the tested process counts. It does not claim a global mathematical optimum. |
| Median step time at selected process count | Median of the per-attempt average step times at the process count identified in the preceding column. |
| Speedup relative to 1 process | `median step time with 1 process / median step time at the selected process count`. A value of 4 means four times faster than the one-process baseline. |
| Median RTF at selected process count | Median RTF measured at the process count with the smallest median step time. |
| RTF | Simulated virtual-time elapsed divided by measured wall-clock elapsed. `RTF >= 1` means at least real-time progression. |
| Observed range | Minimum–maximum `T_step` among the represented attempts; it is not a confidence interval. |
| vs. WSL2 reference | Multi-host median step time divided by the WSL2 12-process median at the corresponding per-host UAV workload. It is a reference ratio, not strict placement-only speedup. |
| Extended | Whether the predeclared spread/failure rule triggered attempts 4 and 5. |
| CPU avg | Median of per-attempt whole-machine average CPU utilization during the measurement window. It is not process CPU time or cross-host normalized capacity. |
| Memory | Median of per-attempt whole-machine average used memory during the measurement window, converted from bytes to GiB. |
| Accepted / Rejected | Observer samples accepted or rejected by the stable world-time snapshot rule. |
| Median / p95 / Max lag | Distribution of accepted `Core world time - slowest participating Asset time` samples. It is not an atomic all-process time spread. |
| World-time start/end diff | Difference between authoritative server/client Core world time at the paired-run boundaries. |

{{provenance}}
