# Drone Fleet multi-host configuration

The `drone-fleet-multi-host` Recipe separates the public configuration contract
from the product that generates Conductor runtime files.

## Ownership and publication boundary

```text
Business Pack Experiment YAML
        |
        | validate and translate
        v
public Hakoniwa Conductor eu-input v1
        |
        | hakoniwa-conductor-pro configure (private product)
        v
generated runtime configuration (published with the Recipe)
```

- `hakoniwa-conductor/schemas/eu-input-v1.schema.json` is the public input
  contract.
- Business Pack owns the translation from the experiment YAML to
  `eu-input.json`.
- `hakoniwa-conductor-pro` owns the private generation implementation.
- The Recipe declares that the private product is required for regeneration.
- The schema-valid input and generated runtime files are publishable Recipe
  artifacts. Running an unchanged published Recipe does not require generator
  source.
- `generation-manifest.json` records the input SHA-256 and generator revision.

The legacy acceptance test compares the generated `eu-input.json` semantically
with `hakoniwa-conductor-pro/eu-config/eu-input.fleets.json`. The Conductor
runtime generator output is also compared with the existing `generated-fleets`
reference, excluding explicitly documented provenance files and obsolete
reference-only files.

## Stable identities

Roles and host IDs are different concepts:

| Host ID | Role | Current environment | Conductor node |
| --- | --- | --- | --- |
| `srv-01` | `server` | native macOS | `srv-01-01` |
| `cli-01` | `client` | Ubuntu under WSL2 | `cli-01` |

The client initiates TCP connections to `srv-01` at `192.168.2.100`. A private
or dynamic WSL2 address is never configuration authority.

## Deterministic local generation

Both hosts use the same revisions and experiment YAML. `configure` always
generates the complete shared topology, while `--host` records which part this
machine will execute:

```bash
# On srv-01 (macOS)
python3 tools/recipe/drone_fleet_multi_host.py \
  --experiment recipes/experiments/drone-fleet-performance/multi-host-legacy-256.yaml \
  configure --host srv-01

# On cli-01 (WSL2/Linux)
python3 tools/recipe/drone_fleet_multi_host.py \
  --experiment recipes/experiments/drone-fleet-performance/multi-host-legacy-256.yaml \
  configure --host cli-01
```

The unique role aliases `server` and `client` are also accepted. The selection
is machine-local state under `.hako/`; it is not included in the shared
configuration hash or committed to Git. Later lifecycle operations therefore
need no host argument:

```bash
python3 tools/recipe/drone_fleet_multi_host.py doctor
python3 tools/recipe/drone_fleet_multi_host.py start
python3 tools/recipe/drone_fleet_multi_host.py open-viewer  # srv-01 only
python3 tools/recipe/drone_fleet_multi_host.py run  # srv-01 only, after both hosts are ready
python3 tools/recipe/drone_fleet_multi_host.py status
python3 tools/recipe/drone_fleet_multi_host.py stop
```

`start` only activates host-local assets. The server starts first, including
WebBridge and the static Three.js HTTP server. The operator then runs
`open-viewer` on `srv-01` and presses Connect before starting the client. After
both sessions report `ACTIVATED` and the client has joined, a human runs `run`
on `srv-01`. That command invokes `hako-cmd start`; Conductor propagates the
Start event and the client starts its local Hakoniwa Core. Immediately before
invoking it, `run` reads the generated `remote-api.json` participant set and
requires current-session server-log Join evidence for every participant.
Missing clients cause a fail-closed error and `hako-cmd start` is not invoked.
Running `run` or `open-viewer` on a client is rejected.

Each operation verifies that the stored host still exists in the current
experiment, its role and platform match, and its configuration hash equals the
current bundle. A changed experiment or regenerated bundle requires another
`configure --host ...`.

Because generation is deterministic, configuration bundles do not need to be
transferred between the machines. Before a run, both sides compare the input,
configuration, schema, and source revision hashes.

### Effective configuration and binary identity

The Experiment YAML and `eu-input.json` describe intent; they are not by
themselves proof of the settings used by a run. `configure` therefore verifies
that all five Conductor timing fields (`delta_time_usec`,
`max_delay_time_usec`, `real_sleep_msec`, `simtime_publish_mode`, and
`simtime_publish_interval_usec`) have the expected value in both generated role
configurations. `doctor` repeats this check before launch.

The launcher invokes the exact `hakoniwa-conductor-pro/cmake-build/main_server`
or `main_client` binary checked by `doctor`, with the corresponding generated
configuration passed explicitly. It does not use a wrapper that may resolve an
older binary from an installed prefix. A performance result is valid for its
declared timing profile only when this effective-config and executable-identity
contract passes on every host.

## Workspace layout v1

```text
.hako/recipes/drone-fleet-multi-host/
└── local-selection.json          # machine-local; ignored by Git

work/recipes/drone-fleet-multi-host/
├── bundle-index.json
├── config/
│   ├── resolved-experiment.json
│   └── conductor/
│       ├── eu-input.json
│       ├── node-ip-map.json
│       └── generated/
│           ├── generation-manifest.json
│           ├── execution-unit.json
│           ├── conductor/
│           ├── endpoint/
│           ├── bridge.json
│           ├── rpc.json
│           └── remote-api.json
├── bundles/
│   ├── srv-01/
│   │   ├── manifest.json
│   │   └── config/              # 128 UAV / 4-process Drone, VSP, Show
│   └── cli-01/
│       ├── manifest.json
│       └── config/              # 128 UAV / 12-process Drone, VSP, Show
├── local/<host-id>/
├── runtime/<host-id>/
└── results/<series>/hosts/<host-id>/<configuration-id>/attempt-XX/
```

`config/` is experiment-scoped and identical on both hosts. `local/`,
`runtime/`, and `results/` are host-scoped. Observed state such as PIDs,
timestamps, logs, and metrics never contributes to the configuration hash.

Both host `config/` trees are produced through the same runtime materializer
used by `drone-fleet-single-host`. The server VSP owns global range `0..127`
and chunk `0`; the client VSP owns range `128..255` and chunk `1`. Launcher
JSON is deliberately deferred to host-local `doctor`/`start`: it embeds
Foundation, native executable, MuJoCo, and viewer paths for the machine that
will execute it. External-Conductor launchers disable the built-in Conductor
in every Drone Service; only `srv-01` owns WebBridge and Viewer assets.

## Result collection

Each host runs the existing per-host aggregation locally. After the batch,
`cli-01` results are copied to `srv-01`, for example with `scp`. A separate
multi-host aggregation step joins host summaries by `configuration_id`,
`attempt`, `run_id`, and `config_hash`. CPU, memory, and network observations
remain identified by host.

Only results need transfer. Initial experiments do not require remote command
execution, configuration push, a shared filesystem, or an inbound connection
to WSL2.

## Headless scaling preflight

`multi-host-scaling.yaml` is the ICRA-oriented, headless scaling Recipe. Its
matrix varies only the total UAV count (`64`, `128`, `256`) and currently uses
one attempt. Equal allocation resolves each condition across `srv-01` and
`cli-01`; their Experiment B-derived process policy is 6 and 12 processes
respectively. This differs from the legacy connectivity baseline, which used
4 processes on `srv-01` and 12 on `cli-01`.

The Conductor `real_sleep_msec` value is deliberately a scalar Recipe setting,
not a matrix axis. Before the final scaling run, edit that one value manually
through `10`, `5`, `2`, `1`, and `0`, and run only the 256-UAV condition. This
keeps exploratory tuning distinct from the final experiment. If RTF and both
hosts' CPU usage are acceptable at `0`, leave it fixed at `0` and execute the
full UAV-count matrix.

Inspect and configure a condition with the scaling operator:

```bash
python3 tools/recipe/drone_fleet_multi_host_scaling.py plan

# Run on both machines, changing only the local host identity.
python3 tools/recipe/drone_fleet_multi_host_scaling.py \
  configure --host srv-01 --drone-count 256
python3 tools/recipe/drone_fleet_multi_host_scaling.py \
  configure --host cli-01 --drone-count 256
```

After configuration, the host identity and resolved condition are local state,
so lifecycle commands take no host or condition arguments. Start `srv-01`
first, then `cli-01`; invoke `run` on `srv-01` after both sides are ready:

```bash
python3 tools/recipe/drone_fleet_multi_host_scaling.py doctor
python3 tools/recipe/drone_fleet_multi_host_scaling.py start
python3 tools/recipe/drone_fleet_multi_host_scaling.py status

# srv-01 only, after both hosts are active
python3 tools/recipe/drone_fleet_multi_host_scaling.py run

python3 tools/recipe/drone_fleet_multi_host_scaling.py stop
```

Before retrying the same attempt, run `clean` on each host after it has stopped:

```bash
python3 tools/recipe/drone_fleet_multi_host_scaling.py clean
```

`clean` removes only the selected host's Launcher session/log, local execution
logs and validation output, plus current-attempt result copies for every host
(including results previously collected with `scp`). Shared generated
configuration, host bundles, and the `.hako` host selection are preserved, so
another `configure` is unnecessary. It refuses to run while the Launcher
session is active; use `stop` first.

Each host writes its existing per-host measurement under:

```text
work/recipes/drone-fleet-multi-host/results/<series>/hosts/
└── <host-id>/uav-NNN-sleep-NNNms/attempt-01/
```

Copy the `cli-01` result subtree into the same relative location on `srv-01`.
The multi-host summarizer then verifies `host_id`, `configuration_id`,
`attempt`, `run_id`, `config_hash`, and the Conductor sleep value before
pairing results. It reports the server RTF as the authoritative RTF and keeps
average/maximum CPU observations for both hosts:

```bash
# One 256-UAV sleep pilot
python3 tools/recipe/drone_fleet_multi_host_scaling.py \
  summarize --drone-count 256

# Full 64/128/256 scaling after fixing real_sleep_msec to 0
python3 tools/recipe/drone_fleet_multi_host_scaling.py summarize
```

Summary filenames include the scalar sleep value, so pilot results from
different settings do not overwrite one another.

Results produced before the effective timing contract was introduced may still
demonstrate multi-host connectivity, but must not be used as timing-profile
performance evidence unless their generated role configurations and executed
binary identities can be reconstructed and verified.

### 64-UAV real-sleep pilot decision (2026-08-16)

The first valid paired pilot held the UAV count (64), placement (32 per host),
process policy (4 on `srv-01`, 12 on `cli-01`), 1 ms Conductor step, 20 ms
maximum delay, 10 ms SimTime publication interval, workload, and measurement
boundary constant. Only `real_sleep_msec` changed.

| `real_sleep_msec` | Server RTF | Server CPU avg. | Client CPU avg. |
| ---: | ---: | ---: | ---: |
| 10 | 0.8900 | 44.64% | 64.90% |
| 1 | **2.5449** | **45.10%** | **64.43%** |
| 0 | 2.4556 | 60.60% | 69.17% |

All three conditions completed all 64 UAV phases, passed preflight, and paired
with matching configuration hashes. Moving from 1 ms to 0 ms reduced server
RTF by about 3.5% while increasing average CPU by about 34% on the server and
7% on the client. Therefore 1 ms is the provisional scaling-profile choice: it
Pareto-dominated 0 ms in this pilot rather than being selected merely because
it was nonzero. A likely explanation is increased scheduler contention in the
zero-sleep (`yield()`-only) loop, but the measurements establish the selection;
they do not by themselves prove that mechanism.

This is a one-attempt, 64-UAV pilot. Reconfirm 1 ms versus 0 ms at 256 UAV
before treating 1 ms as the final full-matrix profile. The authoritative local
summaries are:

- `multi-host-scaling-sleep-010ms-uav-064.json`
- `multi-host-scaling-sleep-001ms-uav-064.json`
- `multi-host-scaling-sleep-000ms-uav-064.json`
