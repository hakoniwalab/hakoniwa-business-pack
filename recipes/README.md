# Hakoniwa Product Recipes

Recipes describe how a user goal can be realized with Hakoniwa components.

Catalog entries are the parts list. A recipe is the system design that composes
those parts into an implementable demo or product path.

## Definition

A Hakoniwa recipe is a structured system composition document:

- It starts from a user goal.
- It selects components from `catalog/`.
- It assigns each component a role.
- It describes PDU, endpoint, asset, artifact, and time-model relationships.
- It states feasibility and missing pieces.
- It separates design feasibility from runtime validation.
- It separates runtime physics artifacts from visualization artifacts when they
  are generated or consumed through different paths.
- It records component-to-component connection contracts, not only component
  lists.
- It gives a minimal demo path that can validate the design.

The first goal is not code generation. The first goal is to convert an ambiguous
user request into a Hakoniwa-valid system composition.

## Using Existing Recipes

When an AI or human receives a user request, do not stop at the catalog search.
After candidate components are found, check `recipes/examples/*.yaml` for an
existing composition that already covers the goal or a close variant.

Use the existing recipe to recover:

- validated and unvalidated paths;
- target and execution environment assumptions;
- required launchers, commands, and runtime artifacts;
- automation choices such as scripted controllers instead of manual devices;
- observability criteria and known failure signals.

Only fall back to component README files for details that are missing from the
recipe, or to verify that the source repository still supports the recorded
steps. If repository behavior has changed, update the recipe with the new
evidence instead of leaving the knowledge only in the conversation.

## Target Environment Requirement

A recipe can describe a platform-neutral composition, but an executable demo or
runbook must define a target environment.

Before producing executable steps, collect or state:

- OS and version
- CPU architecture
- whether the target is native, container, VM, or WSL
- required installed runtimes such as Godot, MuJoCo, Python, Node.js, Docker, or
  .NET
- required Hakoniwa install prefixes such as `/usr/local/hakoniwa`
- whether required components are already built or must be built
- commercial/private component availability

If these details are missing and they change the commands, dependencies, or
feasibility, ask the user before writing a runbook. If they do not change the
architecture, state assumptions explicitly.

When a runtime repository provides a diagnostic command such as `doctor.bash`,
make it an explicit preflight step in executable recipes. A passing doctor check
supports the environment claim; it does not by itself verify runtime behavior.
If the diagnostic fails, record the missing requirement as a blocker or
environment gap.

## Feasibility, Validation, And Connection Contracts

`feasibility` describes whether the catalog supports a credible system design.
It does not mean the recipe has been executed.

`validation` describes what has actually been run or checked. Use step-level
statuses so a recipe can be partially verified:

- `not_tested`: no execution evidence yet.
- `partially_verified`: at least one core step has evidence, but gaps remain.
- `verified`: the intended demo or runbook has completed successfully.
- `blocked`: execution is currently stopped by a known blocker.

Component capability and composition confidence are separate. Two components can
both be capable, while the connection between them still needs an adapter,
runtime contract, world composition step, or environment assumption.

Each `connections[]` entry should therefore include a `contract`:

- `status`: validation state of that specific connection.
- `requires`: conditions, adapters, generated artifacts, or runtime assumptions
  needed for the connection to work.
- `validation_notes`: evidence or blockers discovered during testing.

## Artifact Sets

Do not assume a robot model has one universal artifact path.

For Hakoniwa robot demos, the artifacts used by the physics runtime and the
artifacts used by visualization may be different even when they originate from
the same URDF or registry entry. For example, MuJoCo may need a physics-ready
MJCF or minimal world with runtime-specific actuator and visual-mesh handling,
while Godot may need GLB parts, a scene, and a robot sync profile.

Recipes should group artifacts by consumer intent:

- `physics_artifacts`: inputs used by the simulation runtime.
- `visualization_artifacts`: inputs used by Godot, Foxglove, or another viewer.
- `pdu_artifacts`: PDU definitions, endpoint configs, and sync profiles.
- `runtime_artifacts`: commands, manifests, or generated worlds needed to run a
  demo.

If one source generates multiple artifact sets, state which set has been
validated. A successful GLB generation does not prove the MJCF is runtime-ready,
and a successful MuJoCo run does not prove the visualization path is verified.

## Demo Observability

A demo is useful only if the intended behavior is visible or measurable. Recipe
authors should therefore describe the observation path, not only the launch
commands.

State:

- observable success signals, such as viewer motion, sensor plots, PDU values,
  log lines, screenshots, or generated files;
- failure signals, such as unchanged robot pose, missing PDU channels,
  all-infinite sensor ranges, or a process that only starts but does not advance;
- world or fixture requirements that make the behavior observable;
- automatic controllers or scripted inputs that remove unnecessary manual
  dependencies such as a joystick;
- launcher behavior when a demo starts several Hakoniwa assets.

For sensor recipes, the world must exercise the sensor. A LiDAR recipe should
include obstacles at scan height and within range; a camera recipe should include
visible geometry and lighting; a contact recipe should include an object along
the expected path.

Do not treat launcher exit alone as success. A launcher may intentionally stop
all assets after a scripted controller exits. For runtime demos, record behavior
evidence such as:

- the expected model, manifest, world, or generated artifact was loaded;
- simulation time or steps advanced;
- robot pose, joints, sensor values, or PDU contents changed as expected;
- route or controller phases completed;
- viewer, plot, log, or artifact output matched the intended observation path.

If only process startup or launcher shutdown was checked, mark that step as
partial evidence rather than full runtime verification.

## Core Shape

Every recipe should make these sections explicit:

- `goal`
- `feasibility`
- `validation`
- `constraints`
- `execution_environment`
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
- `target_environment` when executable demo steps are included

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

## Authoring Rules

- Prefer component IDs and roles that exist in `catalog/`.
- Record source artifacts as repository/path/revision triples.
- Put uncertainty in `missing_pieces`, not in prose.
- Keep `demo.steps` concrete enough to validate against existing repositories.
- Do not assume code has been generated unless `source_artifacts` or a demo step
  points to an existing implementation.
