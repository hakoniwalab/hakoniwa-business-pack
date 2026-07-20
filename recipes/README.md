# Hakoniwa Product Recipes

Recipes describe how a user goal can be realized with Hakoniwa components.
Catalog entries are the parts list. A Recipe is the system design that composes
those parts into an implementable Demo or product path.

## Definition

A Hakoniwa Recipe is a structured system-composition document:

- It starts from a user goal.
- It selects components from `catalog/`.
- It assigns each component a role.
- It describes PDU, Endpoint, asset, artifact, and time-model relationships.
- It states feasibility and missing pieces.
- It separates design feasibility from runtime validation.
- It records component-to-component connection contracts.
- It separates runtime physics artifacts from visualization artifacts.
- It gives a minimal Demo path that can validate the design.
- It records the Agency Boundary when a step requires human judgement, physical
  action, external permission, credentials, license entitlement, or explicit
  real-world approval.

The first goal is not code generation. The first goal is to convert an ambiguous
user request into a Hakoniwa-valid system composition.

Before writing executable topology or runbook steps, read:

- `../docs/hakoniwa-runtime-primer.md`
- `../docs/hakoniwa-agent-human-boundary.md`

The Runtime Primer explains executable Hakoniwa runtime assumptions. The Agent /
Human Boundary explains who may perform each step and where execution must stop
for human or external involvement.

## Recipe Reasoning Flow

Use this order:

```text
Capability
  -> Feasibility
  -> Validation
  -> Agency Boundary
  -> Execution
```

These are separate dimensions.

- **Capability** asks what each component can do.
- **Feasibility** asks whether the selected components form a credible system.
- **Validation** records what has actually been executed or verified.
- **Agency Boundary** records what the agent may execute and where a human or
  external authority is required.

In particular:

```text
feasible != agent_can_execute
verified != safe_to_execute_without_human
```

A technically feasible Recipe may still be blocked by a human decision, physical
action, permission, license entitlement, credential, cost approval, or real-world
safety boundary.

## Using Existing Recipes

When an AI or human receives a user request, do not stop at the Catalog search.
After candidate components are found, check `recipes/examples/*.yaml` for an
existing composition that already covers the goal or a close variant.

Listing Recipe filenames is not enough. Open and read the matching Recipe before
executing a Demo from a component README.

Use the existing Recipe to recover:

- validated and unvalidated paths;
- target and execution environment assumptions;
- required launchers, commands, and runtime artifacts;
- connection contracts;
- automation choices such as scripted controllers instead of manual devices;
- observability criteria and known failure signals;
- Agency Boundary gates, when present.

Prefer adapting an existing Recipe over rediscovering execution steps from source
repositories.

## Target Environment Requirement

A Recipe can describe a platform-neutral composition, but an executable Demo or
runbook must define a target environment.

Before producing executable steps, collect or state:

- OS and version;
- CPU architecture;
- native, container, VM, or WSL execution mode;
- required runtimes such as Godot, MuJoCo, Python, Node.js, Docker, or .NET;
- Python 3.12 for Hakoniwa Python workflows unless the Recipe verifies another version;
- Hakoniwa install prefix such as `/usr/local/hakoniwa`;
- whether required components are already built;
- commercial/private component availability;
- required physical devices;
- permissions and credentials that affect execution.

If missing information changes commands, feasibility, or Agency Boundary, ask the
user before writing the runbook.

For Recipes that use Hakoniwa shared memory, PDU, Python controllers, or the
launcher, include the Business Pack common preflight:

```bash
bash tools/doctor.bash
```

A passing doctor check supports the environment claim; it does not verify runtime
behavior or remove an Agency Boundary gate.

## Feasibility, Validation, And Connection Contracts

`feasibility` describes whether the Catalog supports a credible system design. It
does not mean the Recipe has been executed.

`validation` describes what has actually been run or checked:

- `not_tested`
- `partially_verified`
- `verified`
- `blocked`

Component capability and composition confidence are separate. Two components may
both be capable while the connection between them still requires an adapter,
runtime contract, world-composition step, generated artifact, or environment
assumption.

Each important `connections[]` entry should therefore include a `contract` with:

- `status`
- `requires`
- `validation_notes`

## Agency Boundary

`agency_boundary` is currently optional so existing Recipes remain valid. Add it
for new executable Recipes when human involvement, real hardware, external
permissions, consequential decisions, or license/credential gates exist.

The section has four parts:

```yaml
agency_boundary:
  agent_actions: []
  human_decisions: []
  human_actions: []
  required_permissions: []
```

### `agent_actions`

Use for steps the agent may execute autonomously inside the approved software or
simulation scope.

```yaml
agent_actions:
  - id: run_simulation
    description: Launch and validate the simulation-only composition.
    autonomous: true
    preconditions:
      - user_requested_execution
```

Typical examples include generating configuration, building approved components,
running simulation-only processes, inspecting logs, and cleaning up known local
processes.

### `human_decisions`

Use when the agent may recommend but a person must make the final judgement.

```yaml
human_decisions:
  - id: approve_model_fidelity
    description: Decide whether the selected model fidelity is sufficient.
    required_before:
      - customer_acceptance
```

Typical examples include accepting physical-model fidelity, choosing evaluation
criteria, accepting partially verified behavior, selecting a licensing path, or
approving a change in project scope, cost, or deliverables.

### `human_actions`

Use when a person must perform a physical or externally situated action.

```yaml
human_actions:
  - id: connect_flight_controller
    description: Physically connect the flight controller over USB.
    confirmation_required: true
    required_before:
      - hardware_in_the_loop_test
```

Typical examples include wiring hardware, powering devices, placing fixtures,
physical calibration, real-world measurements, or onsite procedures.

A later automated step must not assume the action occurred until the human confirms
it or reliable evidence proves completion.

### `required_permissions`

Use when execution depends on an external authority, credential, entitlement, or
explicit approval.

```yaml
required_permissions:
  - id: commercial_license
    description: Confirm entitlement to the required commercial component.
    status: required
    required_before:
      - commercial_component_execution
```

Typical examples include private-repository access, commercial licenses, API keys,
VPN access, managed-machine install rights, deployment/publish/purchase approval,
or approval before connecting simulation output to real hardware.

## Human Gates

A human gate is part of the Recipe topology, not an operational footnote.

When a step has `required_before`, all dependent execution steps must stop until
the gate is satisfied.

Examples:

```text
Generate motor command in simulation
  -> agent may execute

Send motor command to real actuator
  -> explicit approval gate
```

```text
Launch browser viewer
  -> agent may execute

Press Connect in browser
  -> human action gate

Run mission and claim visual verification
  -> only after connection is confirmed
```

Do not silently cross from simulation to real-world actuation.

## Executable Recipe Side Effects And Cleanup

Executable Recipes must describe side effects and cleanup. Starting a simulator,
bridge, viewer, HTTP server, or background process is part of the Demo contract.

For each long-running asset, record:

- whether it is started by a launcher or manually;
- whether it opens a GUI, browser, socket, shared-memory segment, or service;
- the readiness or success signal;
- how it is stopped;
- process names or PIDs to check if execution is interrupted.

Do not run fetch, build, install, launch, deployment, or server commands only to
answer a capability question. First identify or draft the Recipe, then execute it
when the user asks and the Agency Boundary permits it.

Avoid broad cleanup such as killing every Python process. Stop known launcher
sessions and recorded PIDs only.

## Blocked Or Unknown Recipes

A Recipe may document a blocked investigation, but it must not look runnable when
a runtime path is unknown.

If a simulator, service, launcher asset, component identity, permission, or human
gate is unresolved:

- do not use `feasibility.status: feasible` unless the technical path is actually credible;
- do not use `validation.status: verified` without execution evidence;
- do not invent component IDs;
- do not use runnable-looking placeholder commands;
- put technical unknowns in `missing_pieces`;
- put human/external gates in `agency_boundary`;
- mark dependent execution as blocked until the gate is satisfied.

Search hits are not evidence.

## Artifact Sets

Do not assume a robot model has one universal artifact path. Group artifacts by
consumer intent:

- `physics_artifacts`
- `visualization_artifacts`
- `pdu_artifacts`
- `runtime_artifacts`

State validation separately for each set. Successful GLB generation does not prove
an MJCF is runtime-ready, and a successful MuJoCo run does not prove the Godot or
Foxglove path is verified.

## Demo Observability

A Demo is useful only if intended behavior is visible or measurable. State:

- observable success signals;
- failure signals;
- world or fixture requirements;
- automatic controllers or scripted inputs;
- launcher readiness behavior;
- lifecycle evidence separately from behavioral evidence.

For sensor Recipes, the world must exercise the sensor. A LiDAR Demo needs relevant
obstacles; a camera Demo needs visible geometry and lighting; a contact Demo needs
an object along the motion path.

Launcher startup or exit alone is not proof that the intended behavior occurred.

If observability requires human action, model it as an explicit gate.

## Multi-Process Mirror Pattern

For multi-robot Hakoniwa Demos, one useful pattern is multiple simulator processes,
each owning one real robot in its local MJCF world and mirroring remote robots via
pose PDU.

For a two-robot case:

```text
Process A world:
  real:   TB3 Burger
  mirror: TB3 Waffle

Process B world:
  real:   TB3 Waffle
  mirror: TB3 Burger
```

Each process publishes its real robot pose and subscribes to the remote robot pose.
Control programs are separate controller processes.

Make explicit:

- simulator processes;
- Conductor owner;
- viewer owner;
- real robots;
- mirrored robots;
- published/subscribed PDU names;
- controller targets.

Only one simulator process should start Conductor. Other simulator processes use
the documented non-owner mode.

Do not describe a PDU-only external viewer as equivalent to a multi-process mirror
composition.

## Core Shape

Every Recipe should make these sections explicit when applicable:

- `goal`
- `feasibility`
- `validation`
- `constraints`
- `target_environment`
- `execution_environment`
- `agency_boundary` when human/external gates exist
- `process_topology` for multi-process Demos
- `components`
- `connections`
- `data_flow`
- `time_model`
- `artifact_sets`
- `artifacts`
- `missing_pieces`
- `demo`
- `demo.observability`
- `demo.verification_checks`
- `expected_result`
- `source_catalogs`
- `source_artifacts`

## Layout

```text
recipes/
├── README.md
├── recipe-template.yaml
├── tools/
│   └── validate_recipes.rb
└── examples/
    └── <recipe-id>.yaml
```

`agency_boundary` remains optional at the validator level for compatibility with
existing Recipes. New executable Recipes should include it whenever a meaningful
human or external gate exists.
