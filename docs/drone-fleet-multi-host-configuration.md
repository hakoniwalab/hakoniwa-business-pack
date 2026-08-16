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
python3 tools/recipe/drone_fleet_multi_host.py status
python3 tools/recipe/drone_fleet_multi_host.py stop
```

Each operation verifies that the stored host still exists in the current
experiment, its role and platform match, and its configuration hash equals the
current bundle. A changed experiment or regenerated bundle requires another
`configure --host ...`.

Because generation is deterministic, configuration bundles do not need to be
transferred between the machines. Before a run, both sides compare the input,
configuration, schema, and source revision hashes.

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
