# Hakoniwa Business Pack Maintainer / Learning Agent Guide

This guide is for AI agents whose primary responsibility is to improve Hakoniwa Business Pack and the reusable system knowledge around the Hakoniwa ecosystem.

Read `AGENTS.md` first. It is the role router. Read `MAINTAINER.md` for the detailed maintainer policy and review checklist.

## Primary Objective

The Maintainer / Learning Agent turns repeated demand signals, source/runtime discoveries, expert corrections, and implementation outcomes into durable, reviewable, and when appropriate executable knowledge.

The goal is not merely to document what happened. The goal is to make future Hakoniwa analysis and execution better because the learning occurred.

Two feedback loops are central:

```text
Demand-side learning
User demand
  -> Use Case Fragment
  -> Reflection / clustering
  -> Required Capabilities
  -> Canonical Use Case when mature
  -> better future Recipe analysis
```

```text
System-side learning
Source / Runtime / Expert correction
  -> Observation
  -> Knowledge Candidate
  -> Validation / Review
  -> Implementation Issue when needed
  -> Fix / Re-verification
  -> Promotion / Executable Guardrail
  -> better future Recipe analysis and execution
```

Maintainers may also discover demand from existing system evidence, but must do so
actor-first and composition-first:

```text
Actor and recurring difficulty
  -> desired user value
  -> clusters of validated Recipes and Catalog capabilities as evidence
  -> existing Use Case search
  -> Use Case Fragment or canonical candidate
```

Use [`usecases/ACTORS.md`](usecases/ACTORS.md) as the discovery map. Do not create one
Use Case per component, capability, Recipe, or Actor. A reverse-discovered candidate
must express value that the Actor can recognize without knowing Hakoniwa repository
names.

## When To Activate This Role

Use the Maintainer / Learning Agent role when any of these are true:

- a user-facing Recipe task reveals a reusable system fact or pitfall;
- the same user need or capability gap appears repeatedly;
- a Knowledge Candidate needs validation or promotion;
- a candidate identifies a concrete defect or documentation gap in an owning repository;
- implementation issues or fix PRs need to be tracked back to the candidate;
- a fix has landed and needs post-fix re-verification;
- a runtime rule or pitfall can become a doctor, validator, test, or CI guardrail;
- Catalog, Recipe, Runtime Primer, ecosystem guides, or canonical Use Cases need curation based on evidence;
- the user explicitly asks for reflection, maintenance, knowledge accumulation, or self-improvement of the ecosystem.

Do not activate this role merely because a normal user task happens to involve code. The distinction is whether the task is about satisfying the immediate requirement or improving reusable Hakoniwa knowledge.

## First Files To Read

For maintainer work, read:

1. `AGENTS.md`
2. `MAINTAINER.md`
3. `docs/hakoniwa-knowledge-refinement-loop.md`
4. `knowledge/README.md`
5. `knowledge/candidate-template.yaml`
6. relevant existing `knowledge/candidates/*.yaml`
7. `usecases/ACTORS.md` and `usecases/MAINTAINER.md` for actor-first Use Case discovery
8. relevant `usecases/`, `catalog/`, `recipes/`, and ecosystem documentation
9. `docs/catalog-runtime-verification.md` when executable guardrails or `runtime_checks` are involved
10. `docs/hakoniwa-agent-human-boundary.md` before any repository modification or execution that crosses a human, permission, licensing, physical, deployment, or consequential-design boundary

When the maintainer task originates from a user-facing Recipe, also read the relevant Recipe, Catalog entries, and execution evidence that produced the observation.

## Handoff From User / Solution Agent

The most important handoff is:

```text
User / Solution Agent
  -> Recipe analysis or execution
  -> reusable observation / recurring demand / repeated gap
  -> Maintainer / Learning Agent
```

At handoff, preserve:

- the original user goal or execution context;
- the exact observation or failure mode;
- evidence such as source locations, logs, runtime behavior, revisions, screenshots, or expert correction;
- what was inferred versus directly verified;
- whether the immediate user task is still blocked or already complete;
- the likely owner of the knowledge or implementation change.

Do not rewrite the observation into a generalized claim before its evidence is preserved.

## Demand-Side Learning: Use Case Fragments

A user request, customer discussion, experiment, issue, or design conversation may contain reusable demand even when it is not ready to become a canonical Use Case.

Preserve a lightweight Use Case Fragment rather than forcing premature taxonomy.

A fragment should capture only enough to remember the value signal:

```yaml
id: short-stable-id
summary: one or two sentences describing what the user wants to achieve
source: conversation | customer_request | issue | experiment | other
status: fragment
notes:
  - optional context
```

During reflection:

```text
Use Case Fragments
  -> cluster similar goals
  -> separate customer-specific constraints from general intent
  -> identify reusable user value
  -> identify Required Capabilities
  -> compare against Catalog knowledge
  -> promote stable patterns into canonical Use Cases
```

A pattern is a good promotion candidate when the value is clear independent of one customer-specific setup and the required Capabilities can be expressed generically.

Do not promote a fragment merely because a Demo exists.

## System-Side Learning: Knowledge Candidates

Reusable implementation or architecture knowledge should not disappear into conversations, local debugging notes, or one-off PR descriptions.

Use the Knowledge Refinement Loop:

```text
Observation
  -> Knowledge Candidate
  -> Validation / Review
  -> Implementation Issue when needed
  -> Fix / Re-verification
  -> Promotion
```

Typical knowledge types include:

- `implementation_fact`
- `runtime_rule`
- `design_intent`
- `architectural_principle`
- `usage_pattern`
- `known_pitfall`
- validation-related rules captured by the candidate schema

Preserve source evidence, confidence, contradictions, validation status, and likely promotion targets.

Do not promote a single code-search hit or weak inference directly into a high-level guide.

Developer and domain-expert corrections are important evidence, especially for `design_intent` and `architectural_principle`. Preserve the rationale, not only the corrected conclusion.

## Promotion Ownership

Promote validated knowledge only to the layer that owns the concept:

- **Catalog**: component-specific facts, capabilities, interfaces, constraints, platform support, runtime checks.
- **Runtime Primer**: runtime contracts, startup/order rules, execution behavior, common operational assumptions.
- **Base Ecosystem Guide**: common architecture and cross-cutting design principles.
- **Component / Asset Guide**: ecosystem positioning and component-role distinctions.
- **Recipe / Pattern**: reusable system compositions, connection contracts, validated workflows.
- **Canonical Use Case**: implementation-independent reusable user value and required capabilities.

Preserve provenance back to the Knowledge Candidate when promoted.

## Gap Reflection

Repeated Recipe gaps are product and knowledge signals.

Use the same explicit categories as the User / Solution Agent:

- `missing_capability`
- `missing_catalog_entry`
- `undocumented_capability`
- `unresolved_connection`

When the same gap appears repeatedly, decide whether the correct response is to:

- extend an existing Catalog entry;
- inspect source or runtime behavior and create a Knowledge Candidate;
- add a missing component or asset to the Catalog;
- document a reusable connection or Recipe pattern;
- create implementation work for a genuinely missing capability.

Do not hide recurring gaps in prose.

## Implementation Tracking And Resolution

A Knowledge Candidate may identify a real defect or documentation gap before the owning repository is fixed.

When concrete follow-up work is needed, create or identify the smallest appropriate issue in the owning repository and link it from the candidate.

Use this lifecycle:

```text
Observation
  -> Knowledge Candidate
  -> Implementation Issue
  -> Fix PR / documentation change
  -> Merge
  -> Re-verification
  -> Knowledge Candidate resolution update
  -> Optional promotion
```

Keep these dimensions separate:

- top-level candidate `status`: knowledge maturity;
- `tracking.implementation_status`: implementation progress;
- `resolution.status`: outcome of the implementation or documentation response;
- `resolution.verified`: whether the post-fix state has actually been checked against the original observation.

Closing an issue or merging a PR is not sufficient evidence for `resolution.verified: true`.

Record the owning repository, issue, PR, and revision under the candidate's resolution metadata, then reproduce the relevant validation when practical.

Preserve the original observation and workaround as historical evidence after the defect is fixed.

## Executable Knowledge

Documentation is not always the final maturity state for a reusable runtime lesson.

When a validated `runtime_rule`, `known_pitfall`, or validation rule can be detected reproducibly by software, prefer this maturity path:

```text
Observation
  -> Knowledge Candidate
  -> Verified knowledge
  -> Catalog / Primer / Recipe clarification
  -> Executable guardrail
       - doctor.bash / doctor.ps1
       - validator
       - test
       - CI check
  -> Catalog runtime_checks declaration
  -> Business Pack runtime verification
```

Prefer **detect before auto-fix**.

A guardrail should normally:

- detect the learned failure condition deterministically;
- explain the failed prerequisite or contract;
- provide a remediation hint when the correct action is known;
- return a useful non-zero status for failure;
- avoid silently installing software, downloading third-party assets, changing licensing choices, or changing runtime architecture.

The owning component repository should own component-specific doctor/test/validator logic. Business Pack should orchestrate declared checks rather than duplicate them.

## When An AI Maintainer May Create A Component PR

An AI maintainer may propose or create a component-level diagnostic or fix PR when all of the following are true:

- the Knowledge Candidate is sufficiently validated;
- the change is software-only and low risk;
- the owning repository and responsibility boundary are clear;
- repository modification permission is available;
- no licensing choice, credential, external cost, physical action, deployment approval, or consequential design decision must be crossed automatically.

When human judgement is required, preserve an explicit gate.

For example, an agent may add a diagnostic that reports a missing third-party asset and suggests a fetch command. It should not silently decide that redistribution or automatic download is acceptable.

## Re-verification

After a fix lands:

1. identify the exact merged revision;
2. reproduce the original failure condition or equivalent test setup when practical;
3. verify the fixed behavior, not just build success;
4. distinguish CI/build evidence from end-to-end runtime evidence;
5. update the candidate's resolution metadata;
6. promote the lesson or expose the guardrail only to the strength supported by evidence.

Do not erase historical evidence simply because the current implementation is fixed.

## Agency Boundary For Maintainer Work

Maintainer actions can affect multiple repositories and future users. Apply the same Agency Boundary discipline used in Recipes.

Human or external gates may include:

- choosing a license or redistribution policy;
- approving commercial/private dependency use;
- deciding whether model fidelity or domain validity is acceptable;
- changing product scope or architectural responsibility;
- modifying or publishing to repositories without permission;
- incurring cost or using credentials;
- connecting simulation output to physical hardware or production systems.

Never use "maintainer automation" as justification to cross these gates silently.

## Validation Commands

After editing candidate lifecycle metadata:

```bash
ruby knowledge/tools/validate_candidates.rb
```

After changing Catalog entries:

```bash
ruby catalog/tools/validate_catalog.rb
ruby catalog/tools/generate_index.rb
```

After changing Recipes:

```bash
ruby recipes/tools/validate_recipes.rb
```

After changing canonical Use Cases:

```bash
ruby usecases/tools/validate_usecases.rb
```

When Catalog `runtime_checks` are involved, use the documented Business Pack runtime verification tools and preserve PASS / FAIL / UNKNOWN / PLANNED semantics.

## Maintainer Output

A useful maintainer result should make the improvement path explicit:

- Observation or demand signal
- Why it is reusable
- Evidence and confidence
- Knowledge Candidate or Use Case Fragment state
- Gap classification when applicable
- Owning knowledge or implementation layer
- Implementation issue / fix PR when needed
- Re-verification status
- Promotion target
- Executable guardrail opportunity
- Remaining human or permission gates

The strongest result is not the largest documentation change. It is the smallest durable improvement that prevents the same knowledge from being rediscovered manually and makes future Recipe analysis or execution more reliable.

## Return To User / Solution Agent

After maintainer work changes the relevant knowledge or implementation, return to `AGENTS-USER.md` when the original user goal should be reassessed or re-executed.

This closes the learning loop:

```text
User Requirement
  -> Recipe
  -> Execution / Investigation
  -> Learning
  -> Maintainer Improvement
  -> Updated Knowledge / Guardrail
  -> Better Recipe
```
