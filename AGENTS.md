# Hakoniwa Business Pack Agent Router

This repository is structured system knowledge for translating user requirements into defensible Hakoniwa system compositions and for improving that knowledge from real usage.

Do not treat this repository as a normal source-code project first.

`AGENTS.md` is the stable entry point for AI agents. It routes the agent to one of two primary roles:

- **User / Solution Agent**: understand a user goal and produce a defensible Hakoniwa Recipe, with execution or validation only when requested.
- **Maintainer / Learning Agent**: turn reusable demand signals, implementation discoveries, runtime evidence, and corrections into durable Business Pack knowledge and executable guardrails.

## Choose The Active Role

### User / Solution Agent

Read [`AGENTS-USER.md`](AGENTS-USER.md) when the task is primarily about using Hakoniwa to satisfy a user need.

Typical triggers:

- "Can Hakoniwa do X?"
- "How should I build X with Hakoniwa?"
- selecting components for a requirement
- designing or updating a Recipe
- evaluating feasibility or validation status
- creating or running a simulation-only Demo
- implementing an already-defined Recipe

The normal flow is:

```text
User Requirement
  -> Search / interpret existing Use Cases
  -> Required Capabilities
  -> Catalog Components
  -> Recipe
  -> Feasibility / Validation / Agency Boundary
  -> Optional Execution
  -> Preserve unmet or reusable demand as Use Case knowledge
```

Before evaluating feasibility from Catalog components alone, search `usecases/` for an existing reusable problem or desired outcome that matches the user's intent. Reuse or refine that Use Case when appropriate.

If no matching Use Case exists, derive a provisional Use Case from the user's goal. A request that is currently `not_feasible`, `partially_feasible`, or `unknown` is still valuable demand knowledge: preserve the unmet need as a Use Case Fragment or canonical Use Case candidate instead of discarding it after feasibility analysis.

The Recipe is the primary system-composition output. A Demo is optional downstream validation, not the primary goal.

### Maintainer / Learning Agent

Read [`AGENTS-MAINTAINER.md`](AGENTS-MAINTAINER.md) when the task is primarily about improving Hakoniwa Business Pack or the ecosystem knowledge it represents.

Typical triggers:

- reusable knowledge was discovered during source inspection or runtime execution
- a recurring user need should be preserved or generalized
- a Recipe exposed a repeated Catalog or capability gap
- a Knowledge Candidate needs validation, implementation tracking, resolution, or promotion
- an owning component repository should gain a doctor, validator, test, or CI guardrail
- a merged fix needs post-fix re-verification
- the user explicitly asks for reflection, curation, maintenance, or repository-level learning

The normal flow is:

```text
Observation / Demand Signal
  -> Knowledge Candidate or Use Case Fragment
  -> Validation / Reflection
  -> Implementation Issue when needed
  -> Fix / Re-verification
  -> Promotion or Executable Guardrail
```

Maintainers also have a reverse-discovery path:

```text
Catalog capabilities + existing Recipes
  -> identify demonstrated user value
  -> search existing Use Cases
  -> propose missing Use Case candidates
  -> review / generalize
  -> add canonical Use Cases when justified
```

Use [`usecases/MAINTAINER.md`](usecases/MAINTAINER.md) for this Catalog/Recipe-to-Usecase discovery workflow. Do not manufacture a customer need merely because a component exists; every proposed Use Case must express credible user value independently from the implementation.

## Role Handoff

A single task may cross the boundary between the two roles.

The most common direction is:

```text
User / Solution Agent
  -> Recipe analysis or execution
  -> reusable discovery or recurring demand signal
  -> Maintainer / Learning Agent
```

Do not lose a reusable discovery in a one-off answer merely because the task began as a user-facing Recipe task.

When a discovery is reusable:

1. finish or clearly preserve the user's immediate task state;
2. identify the reusable observation, gap, or demand signal;
3. switch to or hand off to the Maintainer / Learning Agent guidance;
4. preserve evidence and uncertainty before promotion.

The reverse handoff is also valid. A maintainer may improve Catalog knowledge or guardrails and then return to the User / Solution Agent to re-run or reassess the original Recipe.

## Repository Bootstrap

When asked only to "understand this repository" or equivalent:

1. read this router;
2. read the overview and workflow sections of both `AGENTS-USER.md` and `AGENTS-MAINTAINER.md` so both roles are discoverable;
3. use the User / Solution Agent as the default active role unless the requested work is clearly maintenance-oriented;
4. continue with the common knowledge documents below.

This prevents repository understanding from stopping at the user-facing Recipe loop while still keeping normal tasks focused.

## Common Knowledge Documents

Both roles share the same underlying system knowledge. Read the relevant documents before making claims or executing changes:

1. `README.md`
2. `usecases/README.md`
3. `usecases/index.yaml`
4. `docs/hakoniwa-base-ecosystem-ja.md`
5. `docs/hakoniwa-component-asset-guide-ja.md`
6. `catalog/index.yaml`
7. `catalog/schema.yaml`
8. `docs/hakoniwa-runtime-primer.md`
9. relevant `catalog/components/*.yaml`
10. `recipes/README.md`
11. relevant `recipes/examples/*.yaml`

Read these additional documents when the task requires them:

- `usecases/MAINTAINER.md` when mining existing Catalog or Recipe knowledge for missing reusable Use Cases.
- `docs/hakoniwa-agent-human-boundary.md` before execution or when human judgement, physical action, permission, credentials, licensing, real hardware, or consequential decisions are involved.
- `docs/hakoniwa-knowledge-refinement-loop.md` and `knowledge/` when reusable system knowledge is discovered or curated.
- `MAINTAINER.md` when performing repository-level reflection, demand clustering, implementation tracking, promotion, or executable-knowledge maintenance.

## Shared Invariants

Keep these distinctions explicit regardless of active role:

```text
feasible != verified
feasible != agent_can_execute
verified != safe_to_execute_without_human
```

Do not invent missing capabilities, runnable-looking placeholder commands, verification evidence, or permissions.

Do not silently cross from simulation to real-world actuation.

Repository visibility, component identity, distribution channel, and license rights are separate facts.

## Use Case Learning Invariant

Use Cases are the durable record of **who wants what outcome and why it matters**. They are not limited to already-realized solutions.

Therefore:

- search existing Use Cases before reducing a new user goal directly to Catalog components;
- preserve reusable unmet demand even when the current feasibility result is `not_feasible`, `partially_feasible`, or `unknown`;
- keep implementation gaps in Recipe / Catalog knowledge while keeping the desired outcome in Use Case knowledge;
- re-evaluate previously blocked or unknown Use Cases when Catalog capabilities, Recipes, or validation evidence change;
- allow maintainers to discover candidate Use Cases from demonstrated Catalog/Recipe capabilities, but require an implementation-independent user-value statement before promotion.

This creates two complementary learning directions:

```text
Demand -> Use Case -> Capability -> Catalog / Recipe
Catalog / Recipe evidence -> demonstrated value -> Use Case candidate
```

## Understanding Report

After a bootstrap-style repository understanding task, report at least:

```text
I have read:
- AGENTS.md router: yes/no
- AGENTS-USER.md: yes/no
- AGENTS-MAINTAINER.md: yes/no
- usecases/README.md: yes/no
- docs/hakoniwa-base-ecosystem-ja.md: yes/no
- docs/hakoniwa-component-asset-guide-ja.md: yes/no
- catalog/index.yaml: yes/no
- catalog/schema.yaml: yes/no
- docs/hakoniwa-runtime-primer.md: yes/no
- relevant component catalogs: <names or none>
- relevant recipes: <names or none>

Active role:
- user-solution | maintainer-learning

Current understanding:
- what this repository is
- how the Hakoniwa ecosystem is structured
- where the relevant components fit
- what Hakoniwa runtime assumptions matter
- what can be proposed from Catalog and Recipe evidence
- what is feasible, verified, unknown, or blocked
- how reusable discoveries and demand signals feed back into Business Pack knowledge
- how unmet demand and demonstrated capabilities grow the Use Case layer
```

The purpose of the router is not to create two isolated agents. It is to make the active responsibility explicit while allowing a controlled handoff between **using Hakoniwa** and **improving Hakoniwa's reusable knowledge**.
