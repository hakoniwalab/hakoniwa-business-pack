# Hakoniwa Business Pack Agent Guide

This repository is a catalog and recipe hub for composing Hakoniwa components
into feasible demos and product designs.

When answering user requests, do not treat this repository as a normal source
code project first. Treat it as structured knowledge:

```text
User goal
  -> catalog/index.yaml
  -> catalog/components/*.yaml
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
4. Relevant `catalog/components/*.yaml`
   - Detailed component facts for shortlisted candidates.
5. `recipes/README.md`
   - Definition of a Hakoniwa Recipe.
6. Relevant `recipes/examples/*.yaml`
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
3. Read the detailed YAML for shortlisted components.
4. Follow `connects_to` edges only when the interface and direction make sense.
5. Decide feasibility:
   - `feasible`: existing components and artifacts are enough for a minimal demo.
   - `partially_feasible`: core path exists, but missing pieces remain.
   - `not_feasible`: current catalog has no credible implementation path.
   - `unknown`: catalog is insufficient; state what must be verified.
6. Produce a Recipe-shaped answer:
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
- Python environment policy, such as system Python, venv, conda, and whether
  `hakopy` is available when SHM/service features are required
- Hakoniwa install prefix, usually `/usr/local/hakoniwa`
- whether required components are already built or must be built
- whether required physical devices such as joysticks are available
- availability of commercial/private components

If missing information changes commands or feasibility, ask the user before
producing the runbook. If it does not change the architecture, state assumptions
and continue.

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

## Commercial And Private Components

Catalog entries use:

```yaml
repository:
  visibility: public | private | unknown
distribution:
  channel: oss | commercial | unknown
```

When proposing a user-facing Recipe:

- Mention when a required component is `commercial`.
- Prefer OSS components when the user asks for an OSS-only or public setup.
- Do not claim a private/commercial repository is publicly available.
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
