# Hakoniwa Business Pack Agent Guide

This repository is a catalog and recipe hub for composing Hakoniwa components
into feasible demos and product designs.

Do not treat this repository as a normal source-code project first. Treat it as
structured system knowledge.

```text
User goal
  -> docs/hakoniwa-base-ecosystem-ja.md
  -> docs/hakoniwa-component-asset-guide-ja.md
  -> catalog/index.yaml
  -> catalog/components/*.yaml
  -> docs/hakoniwa-runtime-primer.md
  -> recipes/examples/*.yaml
  -> docs/hakoniwa-agent-human-boundary.md
  -> proposed Recipe or Demo plan
  -> Agency Boundary check
  -> Execution
```

The documents have different responsibilities:

- `docs/hakoniwa-base-ecosystem-ja.md`
  - Explains the common Hakoniwa foundations and their relationships.
  - Use it to understand PDU, PDU Registry, MBody Registry, Endpoint, Bridge,
    RPC, Core, Conductor, and declarative JSON configuration.
- `docs/hakoniwa-component-asset-guide-ja.md`
  - Explains where major simulation, SDK, integration, visualization, and
    interaction components fit in the ecosystem.
  - Use it before choosing concrete Catalog entries.
- `catalog/index.yaml` and `catalog/components/*.yaml`
  - Describe component facts, capabilities, interfaces, dependencies, and gaps.
- `docs/hakoniwa-runtime-primer.md`
  - Explains runtime rules required to make a concrete composition executable.
- `docs/hakoniwa-agent-human-boundary.md`
  - Defines where an agent may proceed autonomously and where a human decision,
    physical action, permission, credential, license entitlement, or explicit
    approval is required.
- `recipes/`
  - Describes concrete or planned system compositions.

## First Files To Read

Read these files in order:

1. `README.md`
2. `docs/hakoniwa-base-ecosystem-ja.md`
3. `docs/hakoniwa-component-asset-guide-ja.md`
4. `catalog/index.yaml`
5. `catalog/schema.yaml`
6. `docs/hakoniwa-runtime-primer.md`
7. Relevant `catalog/components/*.yaml`
8. `recipes/README.md`
9. Relevant `recipes/examples/*.yaml`
10. `docs/hakoniwa-agent-human-boundary.md` before execution or when a Recipe
    includes human involvement, real hardware, external permissions, or
    consequential decisions.

Do not answer only from the README or from a repository-name search.

## How To Answer User Goals

For a user asking "Can Hakoniwa do X?" or "How should I build X?":

1. Normalize the user goal and constraints.
2. Use the Base Ecosystem Guide to identify relevant foundation capabilities.
3. Use the Component / Asset Guide to identify likely system roles.
4. Use `catalog/index.yaml` to shortlist components.
5. Read the detailed YAML for shortlisted components.
6. Follow `connects_to` edges only when interface and direction make sense.
7. Read the Runtime Primer before proposing runtime topology or executable commands.
8. Search `recipes/examples/*.yaml` for an existing Recipe and read it when found.
9. Decide feasibility.
10. State validation separately from feasibility.
11. Evaluate the Agency Boundary before execution.
12. Produce a Recipe-shaped answer.

A useful Recipe-shaped answer should cover:

- Goal
- Feasibility
- Validation
- Agency Boundary
- Target Environment and Execution Environment when relevant
- Components and roles
- Connections and contracts
- Data Flow
- Time Model
- Required Artifacts
- Missing Pieces
- Minimal Demo
- Expected Result

Do not output only a repository list. Explain how the selected components work
together as a Hakoniwa system.

## Capability, Feasibility, Validation, And Agency Boundary

Keep these four questions separate:

- **Capability**: what an individual component can do.
- **Feasibility**: whether current components and artifacts form a credible system.
- **Validation**: what has actually been executed or verified.
- **Agency Boundary**: which steps an agent may execute and where human or external
  involvement is required.

The key rules are:

```text
feasible != agent_can_execute
verified != safe_to_execute_without_human
```

Before every non-trivial execution step, determine whether it is:

1. `agent_action`: simulation-only or controlled software work that the user has
   already asked to execute;
2. `human_decision`: a judgement depending on intent, domain validity, acceptance,
   cost, licensing choice, or business responsibility;
3. `human_action`: a physical or externally situated action that a person must
   perform;
4. `required_permission`: an external permission, credential, entitlement, cost
   approval, deployment approval, or explicit real-world actuation approval.

If a human decision, human action, or permission is required, create an explicit
gate. Do not continue as though the Recipe were fully autonomous.

### Autonomous agent actions

Examples include:

- reading ecosystem, Catalog, Runtime Primer, and Recipe knowledge;
- selecting components and proposing topology;
- generating Recipe YAML, PDU definitions, declarative JSON, source code, launchers,
  adapters, and tests;
- building or running simulation-only compositions after the user requested execution;
- inspecting logs, PDU values, generated artifacts, screenshots, and test results;
- retrying simulation-only configuration within the agreed scope;
- stopping processes and cleaning up known simulation resources.

### Human decision gates

Examples include:

- accepting model fidelity or physical validity for the intended use;
- defining experiment conditions or evaluation metrics when legitimate alternatives exist;
- deciding whether a failure, sensor, disturbance, or environmental model is
  representative enough;
- choosing license or commercial/private dependency paths;
- accepting partially verified work for production or customer delivery;
- approving changes to scope, cost, schedule, or deliverables.

### Human action gates

Examples include:

- connecting, wiring, powering, moving, or inspecting physical devices;
- placing real fixtures, obstacles, markers, or targets;
- physical sensor calibration or real-world measurement;
- pressing a hardware safety control;
- performing an onsite procedure.

### Permission and approval gates

Examples include:

- private-repository access;
- commercial license entitlement;
- API keys, VPN, cloud credentials, or organization authentication;
- software-install permission on managed machines;
- permission to modify, publish, deploy, purchase, or incur cost;
- explicit approval before simulation output is connected to a real actuator,
  robot, drone, vehicle, industrial device, operational network, or production system.

Never silently cross from simulation to real-world actuation.

For detailed rules and the recommended Recipe representation, read
`docs/hakoniwa-agent-human-boundary.md`.

## Important Component Distinctions

Do not collapse components that solve similar-looking problems at different layers.

- `hakoniwa-pdu-endpoint`
  - Communication endpoint infrastructure.
  - Treat ROS 2 / Zenoh support here as an Endpoint-layer transport/integration path.
- `hakoniwa-pdu-ros`
  - Lightweight Python bridge that inspects PDU and ROS 2 message types at runtime,
    converts fields, and transfers data bidirectionally.
  - Prefer it when the goal is simply to connect existing ROS 2 nodes and PDU
    without requiring Zenoh as part of the architecture.
- `hakoniwa-pdu-python`
  - Do not describe it only as a Python language binding.
  - It includes PDU conversion, WebSocket topics, WebSocket RPC, SHM backend support,
    and launcher/runtime utilities. Select the needed capability explicitly.
- `hakoniwa-mbody-registry`
  - Treat it as a body-model conversion and asset-generation hub.
  - Artifacts consumed by MuJoCo and Godot are runtime-specific.
- `hakoniwa-envsim`
  - Treat it broadly as environment modeling, world generation, visualization,
    querying, and external-data conversion, including PLATEAU-oriented flows.
- `hakoniwa-godot`
  - Do not reduce it to a passive viewer; it can participate in PDU exchange,
    interaction, control, and optional time synchronization.
- `hakoniwa-mujoco-robots`
  - Treat it as Hakoniwa robot simulation assets and integration around MuJoCo,
    not merely the MuJoCo engine itself.

Athrill-related components may exist in the Catalog, but the current Japanese
Component / Asset Guide intentionally does not position them yet.

## Ambiguous Requests

Ask for clarification only when missing information changes the architecture,
execution commands, feasibility, or Agency Boundary. Otherwise state reasonable
assumptions and present alternatives with trade-offs.

## Feasibility vs Validation

Do not confuse design feasibility with runtime verification.

- `feasible`: catalog and known artifacts support a credible implementation path.
- `partially_feasible`: the main architecture exists, but gaps remain.
- `not_feasible`: the current Catalog has no credible path.
- `unknown`: evidence is insufficient.
- `verified`: actual execution evidence exists for the intended Recipe or Demo behavior.

Never claim a Recipe is verified from source-code text matches, process startup,
or component capability alone.

For every important `connections[]` entry, state:

- what interface or artifact crosses the boundary;
- what contract must hold;
- whether the connection is verified, partially verified, blocked, not tested,
  or inferred from catalog evidence.

## Evidence And Unknown Runtime Paths

Before presenting a runnable Recipe, confirm:

- every `components[].id` exists in `catalog/components/*.yaml`;
- component roles use `catalog/schema.yaml`;
- simulator and runtime entrypoints are identified;
- launcher assets use real commands, not placeholders;
- required PDU, service, Endpoint, Bridge, RPC, and sync configs are identified;
- `pdutypes.json` is not confused with `pdudef.json` / `pdu_def.json`;
- new PDU channels identify type, schema or size, producer, consumer, generated
  bindings or offsets, and runtime assignment;
- generated binding claims separate type generation, fixed-offset conversion,
  CDR conversion, size registries, and interop tests;
- commercial/private dependencies are explicit;
- validation status matches evidence;
- Agency Boundary gates are explicit when needed.

If a required runtime path is unknown, record it in `missing_pieces` and do not
write runnable-looking placeholder commands.

## Executable Demo And Runbook Requirements

Before executable steps, collect target-environment details when they change
commands, feasibility, or Agency Boundary: OS, architecture, execution mode, GUI,
SHM access, runtime versions, Python environment, Hakoniwa install prefix, build
status, physical devices, and commercial/private availability.

Before local execution of SHM/PDU Recipes, run or ask the user to run:

```bash
bash tools/doctor.bash
```

A passing doctor check is environment evidence, not behavior verification.

Treat fetching, building, launching, opening GUI/browser processes, modifying
adjacent repositories, deploying, connecting hardware, and incurring cost as
side-effecting actions. Confirm the requested execution scope and Agency Boundary
before proceeding. Track cleanup for long-running processes.

Avoid broad cleanup such as killing every Python process. Stop known launcher
sessions and recorded PIDs only.

## Demo Observability Requirements

A runnable Demo must make intended behavior observable. State success signals,
failure signals, required fixtures, automatic controllers or scripted inputs,
and the evidence used to distinguish process lifecycle from actual behavior.

Launcher termination or startup alone is not proof that the composition worked.

## Multi-Process Mirror Demos

For multi-robot Hakoniwa demos, check `recipes/README.md` for the Multi-Process
Mirror Pattern. Make Conductor ownership, viewer ownership, real versus mirrored
robots, pose PDU publications/subscriptions, and controller targets explicit.
Only one simulator process should own Conductor startup.

## Recipe Principles

A Hakoniwa Recipe is a system-composition document. It should explain assets,
runtime ownership, PDU exchange, Endpoint/Bridge connections, time model,
Registry-generated artifacts, validation, observability, and Agency Boundary.

Separate artifact sets by consumer intent:

- `physics_artifacts`
- `visualization_artifacts`
- `pdu_artifacts`
- `runtime_artifacts`

State validation separately for each artifact set.

If the user asks for implementation, create or update a Recipe first unless an
appropriate Recipe already exists.

## Licensing, Distribution, And Private Repositories

Repository visibility, component identity, and distribution/license rights are
separate facts. Mention commercial, non-commercial, or dual-license dependencies
when relevant. Do not treat a public repository as OSS unless its license supports
that claim, and do not claim private/commercial source distribution is public.

License choice and entitlement may be an Agency Boundary gate.

## Validation Commands

Run after changing catalog entries:

```bash
ruby catalog/tools/validate_catalog.rb
ruby catalog/tools/generate_index.rb
```

Run after changing recipes:

```bash
ruby recipes/tools/validate_recipes.rb
```

The index is generated from detailed component YAML files. Do not hand-edit
`catalog/index.yaml` except to debug the generator.

## Authoring Rules

- Keep facts grounded in source repositories.
- Preserve `verification.source_revision` for Catalog entries.
- Put uncertainty in `known_gaps` or `missing_pieces`.
- Use controlled vocabulary from `catalog/schema.yaml`.
- Keep `connects_to.direction` precise:
  - `uses`: current component depends on or consumes the target.
  - `used_by`: target commonly consumes or builds on the current component.
  - `bidirectional`: both sides coordinate as peers.
  - `related`: useful for planning, but not a direct dependency.
