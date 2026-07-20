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
  -> proposed Recipe or Demo plan
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
  - Keep conceptual ecosystem explanations in the ecosystem guides instead of
    duplicating them here.
- `recipes/`
  - Describes concrete or planned system compositions.

## First Files To Read

Read these files in order:

1. `README.md`
   - Project concept and intent.
2. `docs/hakoniwa-base-ecosystem-ja.md`
   - Common ecosystem architecture and design model.
3. `docs/hakoniwa-component-asset-guide-ja.md`
   - Positioning of major Catalog components and assets.
4. `catalog/index.yaml`
   - Lightweight component search index.
5. `catalog/schema.yaml`
   - Controlled vocabulary for categories, roles, maturity, distribution, and
     graph edge direction.
6. `docs/hakoniwa-runtime-primer.md`
   - Runtime rules: shared memory, PDU runtime contracts, simulation time,
     Conductor ownership, launchers, external clients, and cleanup.
7. Relevant `catalog/components/*.yaml`
   - Detailed component facts for shortlisted candidates.
8. `recipes/README.md`
   - Definition of a Hakoniwa Recipe.
9. Relevant `recipes/examples/*.yaml`
   - Existing system compositions to reuse or adapt.

Do not answer only from the README or from a repository-name search.

## How To Answer User Goals

For a user asking "Can Hakoniwa do X?" or "How should I build X?":

1. Normalize the user goal and constraints.
2. Use the Base Ecosystem Guide to identify which foundation capabilities are
   relevant: data model, body model, communication, transfer, RPC, or time
   coordination.
3. Use the Component / Asset Guide to identify the likely system roles:
   simulation runtime, environment/world model, SDK, external integration,
   visualization, or interaction.
4. Use `catalog/index.yaml` to shortlist components by:
   - `summary`
   - `category`
   - `recipe_roles`
   - `connects_to`
   - `tags`
   - `distribution`
5. Read the detailed YAML for shortlisted components.
6. Follow `connects_to` edges only when the interface and direction make sense.
7. Read `docs/hakoniwa-runtime-primer.md` before proposing runtime topology or
   executable commands.
8. Search `recipes/examples/*.yaml` for an existing Recipe that matches the
   goal, selected components, tags, or demo intent.
   - If one exists, read it before opening source repositories or proposing
     commands.
   - Listing Recipe filenames is not enough. Open and use the matching Recipe.
   - Prefer adapting an existing Recipe over rediscovering execution steps from
     component README files.
9. Decide feasibility:
   - `feasible`: existing components and artifacts are enough for a minimal demo.
   - `partially_feasible`: core path exists, but missing pieces remain.
   - `not_feasible`: current catalog has no credible implementation path.
   - `unknown`: catalog evidence is insufficient; state what must be verified.
10. Produce a Recipe-shaped answer:
   - Goal
   - Feasibility
   - Validation
   - Target Environment and Execution Environment, if executable steps are requested
   - Components
   - Component Roles
   - Connections
   - Connection Contracts
   - Data Flow
   - Time Model
   - Required Artifacts
   - Missing Pieces
   - Minimal Demo
   - Expected Result

Do not output only a repository list. Explain how the selected components work
together as a Hakoniwa system.

## Important Component Distinctions

Do not collapse components that solve similar-looking problems at different
layers.

Examples:

- `hakoniwa-pdu-endpoint`
  - Communication endpoint infrastructure.
  - Treat ROS 2 / Zenoh support here as a transport/integration path at the
    Endpoint layer.
- `hakoniwa-pdu-ros`
  - Lightweight Python bridge that inspects PDU and ROS 2 message types at
    runtime, converts fields, and transfers data bidirectionally.
  - Do not describe it as the same mechanism as Endpoint-side ROS 2 / Zenoh
    connectivity.
  - Prefer it when the goal is simply to connect existing ROS 2 nodes and PDU
    without requiring Zenoh as part of the architecture.
- `hakoniwa-pdu-python`
  - Do not describe it only as a Python language binding.
  - It includes multiple concerns such as PDU conversion, WebSocket topics,
    WebSocket RPC, SHM backend support, and launcher/runtime utilities.
  - Select the needed capability explicitly.
- `hakoniwa-mbody-registry`
  - Treat it as a body-model conversion and asset-generation hub.
  - It can produce artifacts consumed by MuJoCo and Godot; do not treat one
    generated representation as universal across runtimes.
- `hakoniwa-envsim`
  - Treat it broadly as environment modeling, world generation, visualization,
    querying, and external-data conversion.
  - It can support flows such as transforming PLATEAU-derived world data for use
    in MuJoCo-oriented simulation environments.
- `hakoniwa-godot`
  - Do not reduce it to a passive viewer. It can participate in PDU exchange,
    interaction, control, and optional time synchronization.
- `hakoniwa-mujoco-robots`
  - Treat it as Hakoniwa robot simulation assets and integration around MuJoCo,
    not merely the MuJoCo engine itself.

Athrill-related components may exist in the Catalog, but the current Japanese
Component / Asset Guide intentionally does not position them yet. Do not infer
that omission means the Catalog entries do not exist.

## Ambiguous Requests

When important constraints are missing:

- Ask for clarification only when the missing information changes the
  architecture.
- Otherwise, state reasonable assumptions explicitly.
- Present alternatives when multiple credible compositions exist.
- Prefer explaining trade-offs over forcing the user to know Hakoniwa component
  names.

Example:

- If the user says "I want to visualize a robot", do not immediately require
  them to choose a component.
- Explain that Godot is a good fit for 3D scene visualization and interaction,
  while Foxglove is a good fit for sensor streams, time series, and schema-aware
  inspection.

## Feasibility vs Validation

Do not confuse design feasibility with runtime verification.

- `feasible`: catalog and known artifacts support a credible implementation path.
- `verified`: the Recipe or Demo has actually been executed successfully.
- `partially_feasible`: the main architecture exists, but gaps remain.
- `unknown`: the catalog is insufficient to make a defensible claim.

Never claim a Recipe is verified unless explicit validation evidence exists,
such as a checked demo run, test result, CI result, or recorded execution note.

Component capability and composition validation are separate claims.

For every important `connections[]` entry, state:

- what interface or artifact crosses the boundary;
- what contract must hold;
- whether the connection is verified, partially verified, blocked, not tested,
  or inferred from catalog evidence.

## Evidence And Unknown Runtime Paths

Do not infer executable feasibility from source-code text matches alone. A
keyword hit is not evidence that a complete Hakoniwa Recipe exists.

Before creating or presenting a runnable Recipe, confirm:

- every `components[].id` exists in `catalog/components/*.yaml`;
- every component role uses `catalog/schema.yaml`;
- the simulator or runtime entrypoint is identified;
- launcher assets use real commands, not placeholders;
- required PDU, service, Endpoint, Bridge, RPC, and sync configs are identified;
- `pdutypes.json` is not confused with `pdudef.json` / `pdu_def.json`;
- new PDU channels identify name, type, source schema or size, producer,
  consumer, generated bindings or offsets, and runtime assignment;
- generated binding claims separate type generation, fixed-offset conversion,
  CDR conversion, size registries, and interop tests;
- commercial/private dependencies are called out explicitly;
- validation status matches actual execution evidence.

If a required runtime path is unknown:

- use `unknown` or `partially_feasible`, not `feasible`;
- use `blocked` or `not_tested`, not `verified`;
- record missing facts in `missing_pieces`;
- inspect or request the missing evidence;
- do not write runnable-looking placeholder launch commands.

Do not invent component IDs. If a needed component is missing from the Catalog,
record it as a gap and stop or mark the Recipe blocked until cataloged.

## Executable Demo And Runbook Requirements

Before writing executable demo steps, collect the target environment when it
changes commands or feasibility:

- OS and version
- CPU architecture
- native/container/VM/WSL execution mode
- GUI or headless execution
- SHM access permissions
- Godot and MuJoCo installation when relevant
- Python/Node.js/.NET/Docker versions when relevant
- Python 3.12 for Hakoniwa Python workflows unless a Recipe verifies another version
- Python environment policy and `hakopy` availability
- Hakoniwa install prefix, usually `/usr/local/hakoniwa`
- whether components are already built
- required physical devices
- availability of commercial/private components and license variants

Before local execution of any SHM/PDU Recipe, run or ask the user to run:

```bash
bash tools/doctor.bash
```

A passing doctor check is environment evidence, not proof that the target
Recipe behavior is verified.

Treat these actions as execution side effects:

- fetching release assets or external dependencies;
- building native binaries;
- starting simulator, bridge, viewer, web server, or background services;
- opening GUI windows or browser viewers;
- modifying adjacent source repositories.

Make sure the user is asking for local execution before taking those actions.
Track cleanup commands for long-running processes.

For launcher-based demos:

- foreground blocking may be normal;
- keep the launcher alive while later controllers or viewers run elsewhere;
- do not use empty stdout/stderr as the only failure signal;
- use active readiness checks such as process state, HTTP/WebSocket response,
  PDU changes, or service responses;
- do not edit generated launch files unless the Recipe explicitly names them as
  user-editable.

Avoid broad cleanup such as killing every Python process. Stop known launcher
sessions and recorded PIDs only.

## Demo Observability Requirements

A runnable Demo must make the intended behavior observable.

For each executable Demo, state:

- what the user should see or measure;
- which PDU, log line, plot, viewer, or generated artifact proves progress;
- what environment condition makes sensors produce meaningful data;
- which controller or script drives the system;
- which failure signal means the composition did not work.

Launcher termination alone is not proof of behavior. Report lifecycle results
and behavioral evidence separately.

## Multi-Process Mirror Demos

For multi-robot Hakoniwa demos, check `recipes/README.md` for the
Multi-Process Mirror Pattern.

Make explicit:

- which simulator process owns Conductor startup;
- which process owns the viewer;
- which robot is real in each process;
- which robots are mirrored;
- which pose PDUs are published and subscribed;
- which controller targets each real robot.

Only one simulator process should start Conductor. Other simulator processes
must use the documented non-owner mode.

## Recipe Principles

A Hakoniwa Recipe is a system-composition document that explains:

- which assets run;
- which runtime owns physics or visualization;
- what data is exchanged as PDU;
- which Endpoint or Bridge connects components;
- which time model is used;
- which Registry generates or supplies artifacts;
- what minimal Demo validates the composition.

Separate artifact sets by consumer intent:

- `physics_artifacts`: MuJoCo or another physics runtime;
- `visualization_artifacts`: Godot, Foxglove, or another viewer;
- `pdu_artifacts`: PDU names, types, Endpoint configs, and sync profiles;
- `runtime_artifacts`: generated worlds, manifests, launchers, and commands.

State validation separately for each artifact set.

If the user asks for an implementation, create or update a Recipe first unless
an appropriate Recipe already exists.

## Licensing, Distribution, And Private Repositories

Catalog entries use repository visibility and distribution/license metadata as
separate facts.

When proposing a user-facing Recipe:

- mention `commercial`, `non-commercial`, or `dual-license` dependencies when
  relevant;
- prefer OSS components when the user requests OSS-only;
- do not treat a public repository as OSS unless its license supports that;
- do not claim private/commercial source distribution is publicly available;
- do not expose local filesystem paths; use repository/path/revision evidence.

For `dual-license` entries, treat one technical component as one component unless
source evidence shows distinct implementations. Select the applicable license
variant separately.

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
