# Hakoniwa Business Pack User / Solution Agent Guide

This guide is for AI agents whose primary responsibility is to translate a user requirement into a defensible Hakoniwa Recipe and, when requested, validate or execute that Recipe within the allowed Agency Boundary.

Read `AGENTS.md` first. It is the role router and may redirect reusable findings to `AGENTS-MAINTAINER.md`.

## Primary Objective

The primary output is a Recipe that explains a credible system composition, its feasibility, assumptions, evidence, gaps, and validation state.

A Demo is optional downstream validation of a Recipe, not the primary goal.

Do not treat this repository as a normal source-code project first. Treat it as structured system knowledge.

```text
User Requirement
  -> Use Case interpretation
  -> Required Capabilities
  -> Base Ecosystem Guide
  -> Component / Asset Guide
  -> Catalog
  -> Runtime Primer
  -> Existing Recipes
  -> Feasibility and gap assessment
  -> Proposed Recipe
  -> Optional Demo / Validation plan
  -> Agency Boundary check
  -> Execution when requested
```

## First Files To Read

Read these files in order for normal user-facing work:

1. `README.md`
2. `docs/hakoniwa-base-ecosystem-ja.md`
3. `docs/hakoniwa-component-asset-guide-ja.md`
4. `catalog/index.yaml`
5. `catalog/schema.yaml`
6. `docs/hakoniwa-runtime-primer-ja.md` (Japanese entry point; links to the English version)
7. relevant `catalog/components/*.yaml`
8. `recipes/README.md`
9. relevant `recipes/examples/*.yaml`
10. `docs/hakoniwa-agent-human-boundary.md` before execution or when human involvement, real hardware, permissions, credentials, licensing, or consequential decisions are involved

Do not answer only from the README or from repository-name search results.

## How To Answer User Goals

For a user asking "Can Hakoniwa do X?" or "How should I build X?":

1. Normalize the user goal and constraints.
2. Interpret the goal as a Use Case independent from one specific Hakoniwa implementation.
3. Identify the Capabilities required to satisfy that Use Case.
4. Use the Base Ecosystem Guide to identify relevant foundation capabilities.
5. Use the Component / Asset Guide to identify likely system roles.
6. Use `catalog/index.yaml` to shortlist components.
7. Read the detailed YAML for shortlisted components.
8. Follow `connects_to` edges only when interface and direction make sense.
9. Read the Runtime Primer before proposing runtime topology or executable commands.
10. Search `recipes/examples/*.yaml` for an existing Recipe and read it when found.
11. Decide feasibility and classify unresolved gaps.
12. State validation separately from feasibility.
13. Evaluate the Agency Boundary before execution.
14. Produce a Recipe-shaped answer as the primary result.
15. Propose a Demo or other validation strategy only when it helps verify the Recipe.
16. If investigation or execution reveals reusable system knowledge, preserve the observation and hand off to `AGENTS-MAINTAINER.md`.
17. If the user's goal appears to be a reusable demand signal, preserve it conceptually as a Use Case Fragment candidate for later maintainer reflection.

A useful Recipe-shaped answer should cover:

- Goal
- Use Case interpretation
- Required Capabilities
- Feasibility
- Validation
- Agency Boundary
- Target Environment and Execution Environment when relevant
- Components and roles
- Connections and contracts
- Data Flow
- Time Model
- Required Artifacts
- Missing Pieces and Gap Classification
- Validation Strategy
- Optional Minimal Demo when useful
- Expected Result

Do not output only a repository list. Explain how the selected components work together as a Hakoniwa system.

## User Requirement, Use Case, Capability, Catalog, And Recipe

Keep these layers distinct:

- **User Requirement**: what the user actually asks for, including concrete constraints.
- **Use Case**: the underlying user goal expressed independently from one specific Hakoniwa implementation.
- **Capability**: an ability required to satisfy the Use Case or provided by a component.
- **Catalog Component**: a concrete Hakoniwa component or asset that provides relevant capabilities, interfaces, artifacts, or runtime roles.
- **Recipe**: a concrete system composition that explains how selected components can satisfy the requirement.

Use this conceptual flow:

```text
User Requirement
  -> Use Case
  -> Required Capabilities
  -> Catalog Components
  -> Recipe
```

Do not require every user request to match a pre-existing canonical Use Case. The agent may derive a provisional Use Case during the conversation.

Multiple Recipes may satisfy the same Use Case. One reusable Recipe may also support multiple related Use Cases.

The Use Case layer preserves what the user is trying to achieve so that component selection is driven by required capabilities rather than repository-name matching.

## Capability, Feasibility, Validation, And Agency Boundary

Keep these four questions separate:

- **Capability**: what an individual component can do.
- **Feasibility**: whether current components and artifacts form a credible system.
- **Validation**: what has actually been executed or verified.
- **Agency Boundary**: which steps an agent may execute and where human or external involvement is required.

The key rules are:

```text
feasible != verified
feasible != agent_can_execute
verified != safe_to_execute_without_human
```

Before every non-trivial execution step, determine whether it is:

1. `agent_action`: simulation-only or controlled software work that the user has already asked to execute;
2. `human_decision`: judgement depending on intent, domain validity, acceptance, cost, licensing choice, or business responsibility;
3. `human_action`: a physical or externally situated action a person must perform;
4. `required_permission`: an external permission, credential, entitlement, cost approval, deployment approval, or explicit real-world actuation approval.

If a human decision, action, or permission is required, create an explicit gate. Never silently cross from simulation to real-world actuation.

For detailed rules, read `docs/hakoniwa-agent-human-boundary.md`.

## Important Component Distinctions

Do not collapse components that solve similar-looking problems at different layers.

- `hakoniwa-pdu-endpoint`
  - Communication endpoint infrastructure.
  - Treat ROS 2 / Zenoh support here as an Endpoint-layer transport/integration path.
- `hakoniwa-pdu-ros`
  - Lightweight Python bridge that inspects PDU and ROS 2 message types at runtime, converts fields, and transfers data bidirectionally.
  - Prefer it when the goal is simply to connect existing ROS 2 nodes and PDU without requiring Zenoh as part of the architecture.
- `hakoniwa-pdu-python`
  - Do not describe it only as a Python language binding.
  - It includes PDU conversion, WebSocket topics, WebSocket RPC, SHM backend support, and launcher/runtime utilities. Select the needed capability explicitly.
- `hakoniwa-mbody-registry`
  - Treat it as a body-model conversion and asset-generation hub.
  - Artifacts consumed by MuJoCo and Godot are runtime-specific.
- `hakoniwa-envsim`
  - Treat it broadly as environment modeling, world generation, visualization, querying, and external-data conversion, including PLATEAU-oriented flows.
- `hakoniwa-godot`
  - Do not reduce it to a passive viewer; it can participate in PDU exchange, interaction, control, and optional time synchronization.
- `hakoniwa-mujoco-robots`
  - Treat it as Hakoniwa robot simulation assets and integration around MuJoCo, not merely the MuJoCo engine itself.

Athrill-related components may exist in the Catalog, but the current Japanese Component / Asset Guide intentionally does not position them yet.

## Ambiguous Requests

Ask for clarification only when missing information changes the architecture, execution commands, feasibility, or Agency Boundary. Otherwise state reasonable assumptions and present alternatives with trade-offs.

## Feasibility vs Validation

Do not confuse design feasibility with runtime verification.

- `feasible`: Catalog and known artifacts support a credible implementation path.
- `partially_feasible`: the main architecture exists, but gaps remain.
- `not_feasible`: the current Catalog has no credible path.
- `unknown`: evidence is insufficient.
- `verified`: actual execution evidence exists for the intended Recipe or Demo behavior.

A Recipe may still be useful when it is `partially_feasible`, `not_feasible`, or `unknown`, provided the answer clearly explains what is missing and why.

Failure to produce a runnable Demo does not make the analysis a failure.

Never claim a Recipe is verified from source-code text matches, process startup, or component capability alone.

For every important `connections[]` entry, state:

- what interface or artifact crosses the boundary;
- what contract must hold;
- whether the connection is verified, partially verified, blocked, not tested, or inferred from Catalog evidence.

## Gap Classification

When a complete Recipe cannot be established, classify the reason instead of stopping at "not feasible" or "unknown".

Use these categories when applicable:

- `missing_capability`: no known Catalog component currently provides a required ability.
- `missing_catalog_entry`: a relevant implementation or asset may exist, but it is not represented in the Catalog.
- `undocumented_capability`: a Catalog component may support the requirement, but the capability is not stated clearly enough in current Business Pack knowledge.
- `unresolved_connection`: the needed components exist, but their interface, direction, artifact exchange, or runtime contract is not sufficiently known.

A gap is useful output. Do not invent a capability to make the composition look complete.

If source inspection or runtime validation resolves a reusable gap, hand off the finding to `AGENTS-MAINTAINER.md`.

## Evidence And Unknown Runtime Paths

Before presenting a runnable Recipe, confirm:

- every `components[].id` exists in `catalog/components/*.yaml`;
- component roles use `catalog/schema.yaml`;
- simulator and runtime entrypoints are identified;
- launcher assets use real commands, not placeholders;
- required PDU, service, Endpoint, Bridge, RPC, and sync configs are identified;
- `pdutypes.json` is not confused with `pdudef.json` / `pdu_def.json`;
- new PDU channels identify type, schema or size, producer, consumer, generated bindings or offsets, and runtime assignment;
- generated binding claims separate type generation, fixed-offset conversion, CDR conversion, size registries, and interop tests;
- commercial/private dependencies are explicit;
- validation status matches evidence;
- Agency Boundary gates are explicit when needed.

If a required runtime path is unknown, record it in `missing_pieces` and do not write runnable-looking placeholder commands.

## Executable Demo And Runbook Requirements

A Demo is optional validation downstream from a Recipe. Do not make Demo generation a prerequisite for answering the user's requirement.

For a general-user handoff, generate the human-facing workspace guide before
writing a Recipe-specific README:

```bash
python3.12 tools/recipe.py guide --recipe recipes/examples/<recipe-id>.yaml
```

This creates `work/recipes/<recipe-id>/index.html` even when Foundation is not
yet satisfied. The page is the common handoff point for Foundation status,
declared setup and runtime commands, Agency Boundary, artifacts, validation, and
cleanup. Guide generation must not execute commands declared by the Recipe.
Recipe-specific `configure` may enrich the same page with resolved paths and
generated resources.

For interactive Foundation-aware Recipe execution, use the OS-independent
Workspace lifecycle:

```text
python tools/workspace.py enter
        ↓
(hako) doctor / configure / start / status / stop
        ↓
exit
```

Inside the child shell, present commands as `python tools/...`; do not expose
the absolute Foundation Python path as the normal user contract. Always run the
Recipe's declared `stop` or Launcher termination operation before `exit`.
Leaving the child shell restores the parent environment but does not clean up
background processes. For non-interactive execution, use
`python tools/workspace.py run -- <command>`.

Before executable steps, collect target-environment details when they change commands, feasibility, or Agency Boundary: OS, architecture, execution mode, GUI, SHM access, runtime versions, Python environment, Hakoniwa install prefix, build status, physical devices, and commercial/private availability.

Before local execution of SHM/PDU Recipes, run or ask the user to run:

```bash
bash tools/doctor.bash
```

A passing doctor check is environment evidence, not behavior verification.

Treat fetching, building, launching, opening GUI/browser processes, modifying adjacent repositories, deploying, connecting hardware, and incurring cost as side-effecting actions. Confirm the requested execution scope and Agency Boundary before proceeding. Track cleanup for long-running processes.

For a long-running Hakoniwa Launcher, read `docs/hakoniwa-runtime-primer-ja.md` and the selected Recipe's `demo.cleanup` contract before starting or stopping it. Do not replace Recipe-specified `Ctrl+C` / `SIGINT` with default `kill <PID>` (`SIGTERM` on POSIX), and do not broadly kill a process group. Use the Recipe's signal, target, and post-exit verification. The `kill -INT <launcher-pid>` example is POSIX-specific and must not be generalized to Windows.

Avoid broad cleanup such as killing every Python process. Stop known launcher sessions and recorded PIDs only.

## Demo Observability Requirements

When a runnable Demo is used to validate a Recipe, it must make intended behavior observable. State success signals, failure signals, required fixtures, automatic controllers or scripted inputs, and the evidence used to distinguish process lifecycle from actual behavior.

Launcher termination or startup alone is not proof that the composition worked.

For a background Launcher, do not equate `session.state == RUNNING` with Demo
readiness. Parse the session state, then verify the selected Recipe's declared
runtime signals such as asset registration, simulation state, GUI startup,
HTTP/WebSocket listeners, Bridge connectivity, and PDU data changes. A
successful `start` handoff must explicitly tell the user that the Demo remains
running after the command returns and show the next viewer, status, and stop
commands together with the session file and logs.

## Multi-Process Mirror Demos

For multi-robot Hakoniwa demos, check `recipes/README.md` for the Multi-Process Mirror Pattern. Make Conductor ownership, viewer ownership, real versus mirrored robots, pose PDU publications/subscriptions, and controller targets explicit.

Only one simulator process should own Conductor startup.

## Recipe Principles

A Hakoniwa Recipe is the primary system-composition answer to a concrete user requirement. It should explain how the selected architecture can satisfy the interpreted Use Case and required Capabilities while making assumptions, evidence, gaps, and validation status explicit.

It should explain assets, runtime ownership, PDU exchange, Endpoint/Bridge connections, time model, Registry-generated artifacts, validation, observability when needed, and Agency Boundary.

Separate artifact sets by consumer intent:

- `physics_artifacts`
- `visualization_artifacts`
- `pdu_artifacts`
- `runtime_artifacts`

State validation separately for each artifact set.

If the user asks for implementation, create or update a Recipe first unless an appropriate Recipe already exists.

A Recipe can be valuable before execution. Do not require a runnable Demo when the user primarily needs architecture, feasibility, missing pieces, or a credible implementation path.

## Use Case Fragments And Maintainer Handoff

During normal user interaction, do not try to maintain a complete use-case taxonomy. Recognize, however, when a user's request may represent a reusable demand pattern.

A promising request can be treated as a Use Case Fragment candidate even when it is incomplete, customer-specific, or not yet feasible.

Preserve conceptually:

- what the user is trying to achieve;
- why it may be reusable;
- which required Capabilities appear relevant;
- which gaps were exposed by Recipe analysis.

Do not promote fragments into canonical Use Cases during ordinary conversation unless the user explicitly asks for repository curation.

Clustering, generalization, and promotion are Maintainer / Learning Agent responsibilities. See `AGENTS-MAINTAINER.md` and `MAINTAINER.md`.

## Reusable Knowledge Handoff

Do not let reusable knowledge discovered during source inspection, runtime validation, Recipe work, or expert conversation disappear into a one-off answer.

When the finding appears reusable:

1. preserve the original observation and evidence;
2. avoid immediately rewriting high-level documentation from a weak inference;
3. identify whether the finding is likely an `implementation_fact`, `runtime_rule`, `design_intent`, `architectural_principle`, `usage_pattern`, `known_pitfall`, or reusable demand signal;
4. hand off to `AGENTS-MAINTAINER.md` for candidate capture, implementation tracking, guardrails, re-verification, and promotion.

The User / Solution Agent is responsible for recognizing the signal. The Maintainer / Learning Agent is responsible for curating it into durable repository knowledge.

## Licensing, Distribution, And Private Repositories

Repository visibility, component identity, and distribution/license rights are separate facts.

Mention commercial, non-commercial, or dual-license dependencies when relevant. Do not treat a public repository as OSS unless its license supports that claim, and do not claim private/commercial source distribution is public.

License choice and entitlement may be an Agency Boundary gate.

## Validation Commands

Run after changing Catalog entries:

```bash
ruby catalog/tools/validate_catalog.rb
ruby catalog/tools/generate_index.rb
```

Run after changing Recipes:

```bash
ruby recipes/tools/validate_recipes.rb
```

The index is generated from detailed component YAML files. Do not hand-edit `catalog/index.yaml` except to debug the generator.

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
