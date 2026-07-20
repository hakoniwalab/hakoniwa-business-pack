# Hakoniwa Business Pack Agent Guide

This repository is a catalog and recipe hub for composing Hakoniwa components
into feasible demos and product designs.

When answering user requests, do not treat this repository as a normal source
code project first. Treat it as structured knowledge:

```text
User goal
  -> catalog/index.yaml
  -> catalog/components/*.yaml
  -> docs/hakoniwa-runtime-primer.md
  -> recipes/examples/*.yaml
  -> proposed Recipe or Demo plan
```

## First Files To Read

Read these files in order:

1. `README.md`
   - Project concept and intent.
2. `catalog/index.yaml`
   - Lightweight component search index.
3. `catalog/schema.yaml`
   - Controlled vocabulary for categories, roles, maturity, distribution, and
     graph edge direction.
4. `docs/hakoniwa-runtime-primer.md`
   - Hakoniwa runtime rules: assets, PDU, shared memory, simulation time,
     Conductor, launchers, external clients, and cleanup.
5. Relevant `catalog/components/*.yaml`
   - Detailed component facts for shortlisted candidates.
6. `recipes/README.md`
   - Definition of a Hakoniwa Recipe.
7. Relevant `recipes/examples/*.yaml`
   - Existing system compositions to reuse or adapt.

## How To Answer User Goals

For a user asking "Can Hakoniwa do X?" or "How should I build X?":

1. Normalize the user goal and constraints.
2. Use `catalog/index.yaml` to shortlist components by:
   - `summary`
   - `category`
   - `recipe_roles`
   - `connects_to`
   - `tags`
   - `distribution`
3. Read `docs/hakoniwa-runtime-primer.md` before proposing runtime topology or
   executable commands.
4. Read the detailed YAML for shortlisted components.
5. Follow `connects_to` edges only when the interface and direction make sense.
6. Search `recipes/examples/*.yaml` for an existing Recipe that matches the
   goal, selected components, tags, or demo intent.
   - If one exists, read it before opening source repositories or proposing
     commands.
   - Listing recipe filenames is not enough. Open and use the matching Recipe
     before executing a demo.
   - Prefer adapting an existing Recipe over rediscovering execution steps from
     component README files.
   - Treat the Recipe as the current system-composition memory, including
     validation notes, environment assumptions, launcher behavior, and known
     failure signals.
7. Decide feasibility:
   - `feasible`: existing components and artifacts are enough for a minimal demo.
   - `partially_feasible`: core path exists, but missing pieces remain.
   - `not_feasible`: current catalog has no credible implementation path.
   - `unknown`: catalog is insufficient; state what must be verified.
8. Produce a Recipe-shaped answer:
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

Do not output only a repository list. A useful answer explains how the selected
components work together as a Hakoniwa system.

Do not execute a demo from a component README until you have checked whether a
matching Recipe exists and read that Recipe. Catalogs identify candidate
components; Recipes explain proven or planned compositions and should guide demo
execution.

Do not jump from a capability question directly into adjacent source
repositories. If `catalog/index.yaml` identifies relevant components but no
matching Recipe exists, first answer with the Recipe shape and validation state.
Then, if the user asks to proceed, create or update a Recipe before running
build, fetch, install, launch, or long-running server commands.

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
- Explain that Godot is a good fit for 3D scene visualization, while Foxglove is
  a good fit for sensor streams, time series, and schema-aware inspection.

## Feasibility vs Validation

Do not confuse design feasibility with runtime verification.

- `feasible`: the catalog and known artifacts support a credible implementation
  path.
- `verified`: the recipe or demo has actually been executed successfully.
- `partially_feasible`: the main architecture exists, but missing pieces or
  validation gaps remain.
- `unknown`: the catalog is insufficient to make a defensible claim.

Never claim a recipe is verified unless explicit validation evidence exists,
such as a checked demo run, test result, CI result, or recorded execution note.
If a recipe has only been validated structurally against catalog entries and
source artifacts, say that clearly.

Component capability and composition validation are separate claims.

- A component can be capable of generating or consuming an artifact.
- Another component can be capable of running a compatible runtime.
- The connection between them may still require an adapter, world composition
  step, runtime-specific configuration, generated manifest, device, or execution
  permission.

When proposing a Recipe, treat `connections[]` as first-class design objects.
For each important connection, state:

- what interface or artifact crosses the boundary,
- what contract must hold for the connection to work,
- whether that connection is verified, partially verified, blocked, not tested,
  or only inferred from catalog evidence.

## Evidence And Unknown Runtime Paths

Do not infer executable feasibility from source-code text matches alone. A
string such as `takeoff`, `land`, `drone`, `server`, or `launcher` is a search
hit, not evidence that a complete Hakoniwa Recipe exists.

Before creating or presenting a runnable Recipe, confirm these facts from
catalog entries, existing recipes, or source evidence:

- every `components[].id` exists in `catalog/components/*.yaml`;
- every component role uses `catalog/schema.yaml`;
- the simulator or runtime entrypoint is identified;
- launcher assets use real commands, not placeholders;
- required PDU, service, endpoint, and sync configs are identified;
- missing commercial/private components are called out explicitly;
- validation status matches actual execution evidence.

If any required runtime entrypoint or component identity is unknown:

- set `feasibility.status: unknown` or `partially_feasible`, not `feasible`;
- set `validation.status: blocked` or `not_tested`, not `verified`;
- record missing facts in `missing_pieces`;
- ask for or inspect the missing repository/component;
- do not write a runnable-looking launch file with placeholder commands.

Do not invent component IDs inside a Recipe. If a needed component is missing
from the catalog, describe it as a `missing_pieces` or `known_gap` item such as
"catalog candidate: drone simulator runtime", then stop or mark the Recipe as
blocked until the component is cataloged.

## Executable Demo And Runbook Requirements

Before writing executable demo steps or a runbook, collect the target
environment. This is required because Hakoniwa commands, build steps, dynamic
library paths, and Godot/MuJoCo setup differ by platform.

Required information:

- OS and version
- CPU architecture
- native/container/VM/WSL execution mode
- GUI or headless execution
- shared-memory access permissions when Hakoniwa PDU SHM is involved
- Godot installation and binary path when Godot is involved
- MuJoCo installation when MuJoCo is involved
- Python/Node.js/.NET/Docker versions when relevant
- Python 3.12 for Hakoniwa Python workflows unless the selected Recipe
  explicitly states another supported version
- Python environment policy, such as system Python, venv, conda, and whether
  `hakopy` is available when SHM/service features are required
- Hakoniwa install prefix, usually `/usr/local/hakoniwa`
- whether required components are already built or must be built
- whether required physical devices such as joysticks are available
- availability of commercial/private components and required license variants

If missing information changes commands or feasibility, ask the user before
producing the runbook. If it does not change the architecture, state assumptions
and continue.

If a selected runtime repository provides a diagnostic command such as
`doctor.bash`, run or instruct the user to run it before attempting an executable
demo. Treat a passing doctor check as environment evidence, not as proof that
the demo behavior is verified. If the doctor check fails, report the missing
requirements and do not continue to destructive setup or install steps without
explicit user approval.

Treat these actions as execution side effects, not harmless exploration:

- fetching release assets or external dependencies;
- building native binaries;
- starting simulator, bridge, viewer, web server, or background service
  processes;
- opening GUI windows or browser viewers;
- modifying adjacent source repositories to make a demo work.

Before taking those actions, make sure the user is asking for local execution,
not only asking "Can Hakoniwa do this?" or "What is possible?" For local
execution, state which Recipe or draft Recipe is being followed, run the
available preflight checks, and keep track of cleanup commands.

When starting long-running processes, record how to stop them. Prefer launcher
or conductor-managed lifecycles when available. If a tool session, quota limit,
or interruption occurs, do not leave services running silently; stop them or
report the exact remaining processes.

## Demo Observability Requirements

A runnable demo must make the intended behavior observable. Do not stop at
"processes start" unless the recipe goal is only lifecycle validation.

For each executable demo, state:

- what the user should see or measure,
- which PDU, log line, plot, viewer, or generated artifact proves progress,
- what fixture or environment condition makes sensors produce meaningful data,
- which controller or script drives the system without relying on manual input,
- which failure signal means the composition did not work.

For sensor demos, verify that the simulated world actually intersects the sensor
model. For example, a LiDAR demo needs obstacles at the scan height and within
range; a camera demo needs visible geometry and lighting; a contact demo needs a
collision object along the motion path.

When a demo uses a launcher, include the launcher as a runtime artifact and
state which assets it starts before simulation start and after simulation start.

Launcher termination is not enough to claim success. For example,
`asset exited: route_demo -> abort all` can be expected launcher behavior after a
scripted route completes, but it only proves the controller process exited. To
claim the demo worked, inspect observation evidence such as:

- simulator logs showing the intended model or manifest was loaded,
- simulation steps advancing after `hako-cmd start`,
- robot pose, joint, or sensor values changing,
- route/controller logs showing the intended phases completed,
- viewer, plot, PDU, or generated-artifact evidence matching
  `demo.observability.success_signals`.

Report both the lifecycle result and the behavior evidence. If only the
launcher lifecycle was checked, say the demo launch was checked but runtime
behavior was not verified.

## Multi-Process Mirror Demos

For multi-robot Hakoniwa demos, check `recipes/README.md` for the
Multi-Process Mirror Pattern before proposing an implementation.

Do not collapse this pattern into a single simulator plus an external viewer.
The intended pattern can require multiple Hakoniwa simulator processes, where
each process has one real robot in its local MJCF world and mirrored bodies for
remote robots.

When describing or implementing such a demo, make these roles explicit:

- which simulator process owns Conductor startup;
- which simulator process owns the viewer;
- which robot is real in each process;
- which robots are mirrored in each process;
- which pose PDUs are published and subscribed;
- which controller process targets each real robot.

Only one simulator process should start Conductor. Other simulator processes
must run with Conductor startup disabled.

## Recipe Principles

A Recipe is not source code and not a generic implementation spec.

A Hakoniwa Recipe is a system composition document that explains:

- which assets run,
- which runtime owns physics or visualization,
- what data is exchanged as PDU,
- which endpoint or bridge connects components,
- which time model is used,
- which registry generates or supplies artifacts,
- what minimal demo can validate the composition.

Separate artifact sets by consumer intent. Do not treat a URDF-derived robot as
one universal runtime artifact:

- `physics_artifacts` are consumed by MuJoCo or another physics runtime.
- `visualization_artifacts` are consumed by Godot, Foxglove, or another viewer.
- `pdu_artifacts` define PDU names, types, endpoint configs, and sync profiles.
- `runtime_artifacts` include generated worlds, manifests, and commands needed
  to run the demo.

State validation separately for each set. For example, GLB generation can be
verified while MuJoCo world composition is only partially verified, or MuJoCo
runtime motion can be verified while Godot visualization remains untested.

If the user asks for an implementation, create or update a Recipe first unless
the requested Recipe already exists.

## Licensing, Distribution, And Private Repositories

Catalog entries use:

```yaml
repository:
  visibility: public | private | unknown
distribution:
  channel: oss | non-commercial | commercial | dual-license | unknown
```

Repository visibility, technical component identity, and distribution/license
rights are separate facts. A public repository can carry non-commercial terms,
and the same technical component can be offered under a separate commercial
license.

When `distribution.channel` is `dual-license`:

- Treat the catalog entry as one technical component unless the source evidence
  shows distinct implementations.
- Read the component's `license` metadata to determine the applicable license
  variant for the user's intended use.
- Do not model a commercial license name as a separate component dependency when
  it is only another licensing path for the same codebase.
- Treat optional commercially licensed capabilities as conditional; do not
  assume they are available to every commercial user.

When proposing a user-facing Recipe:

- Mention when a required component is `commercial`, `non-commercial`, or
  `dual-license` when that affects the user's intended use.
- Prefer OSS components when the user asks for an OSS-only setup.
- Do not treat a public repository as OSS unless its license supports that claim.
- Do not claim a private repository or commercial source distribution is publicly
  available.
- Do not expose local filesystem paths. Use `repository`, `path`, and `revision`
  triples from `source_refs` or `source_artifacts`.

## Validation Commands

Run these after changing catalog entries:

```bash
ruby catalog/tools/validate_catalog.rb
ruby catalog/tools/generate_index.rb
```

Run this after changing recipes:

```bash
ruby recipes/tools/validate_recipes.rb
```

The index is generated from detailed component YAML files. Do not hand-edit
`catalog/index.yaml` except to debug the generator.

## Authoring Rules

- Keep facts grounded in source repositories.
- Preserve `verification.source_revision` for catalog entries.
- Put uncertainty in `known_gaps` or `missing_pieces`.
- Use controlled vocabulary from `catalog/schema.yaml`.
- Keep `connects_to.direction` precise:
  - `uses`: current component depends on or consumes the target.
  - `used_by`: target commonly consumes or builds on the current component.
  - `bidirectional`: both sides coordinate as peers.
  - `related`: useful for planning, but not a direct dependency.
