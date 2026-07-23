# Hakoniwa Business Pack Maintainer Guide

This guide defines maintainer responsibilities for evolving Hakoniwa Business Pack knowledge over time.

The maintainer role is different from the normal agent/user interaction described in `AGENTS.md`.

- `AGENTS.md` focuses on interpreting a user's requirement and producing a defensible Hakoniwa Recipe.
- `MAINTAINER.md` focuses on curating repeated observations, use-case fragments, Catalog gaps, and reusable knowledge into durable Business Pack assets.

## Use Case Fragments

Do not require every user request to become a complete or canonical use case immediately.

When a conversation, customer request, experiment, issue, or design discussion contains a potentially reusable user need, preserve a short fragment even when it is incomplete.

A fragment should be lightweight. Capture only enough to remember the user value and why it may matter.

Suggested fields:

```yaml
id: short-stable-id
summary: one or two sentences describing what the user wants to achieve
source: conversation | customer_request | issue | experiment | other
status: fragment
notes:
  - optional context
```

Do not force detailed architecture, component selection, or a complete Recipe into the fragment unless that information is already known.

The purpose is to avoid losing real demand signals simply because they are not yet mature enough to become a formal use case.

## Reflection And Promotion

Periodically review accumulated use-case fragments and look for recurring patterns.

Use this reflection flow:

```text
Use Case Fragments
  -> cluster similar user goals
  -> separate customer-specific constraints from general intent
  -> identify the reusable user value
  -> identify required Capabilities
  -> compare against Catalog knowledge
  -> promote stable patterns into canonical Use Cases
```

A fragment or group of fragments is a good promotion candidate when:

- similar requests appear more than once;
- the user value is clear without depending on one customer-specific configuration;
- the required Capabilities can be expressed generically;
- more than one Recipe or component combination could potentially satisfy the same need;
- the pattern helps explain what Hakoniwa is useful for.

Do not promote a fragment merely because a demo exists.

Avoid promotion when:

- the request is highly customer-specific;
- the value depends on one-off infrastructure or proprietary conditions;
- it is only a technical showcase with no clear user goal;
- the pattern is still too ambiguous to state independently of a particular implementation.

## Canonical Use Cases

A canonical Use Case should describe the user goal independently from one specific Hakoniwa implementation.

Prefer this conceptual separation:

```text
User Requirement
  -> Use Case
  -> Required Capabilities
  -> Catalog Components
  -> Recipe
```

A Use Case answers **what the user wants to achieve**.
A Capability answers **what abilities are required**.
A Catalog entry answers **which concrete component provides those abilities**.
A Recipe answers **how a concrete Hakoniwa system can satisfy the requirement**.

Multiple Recipes may satisfy the same Use Case, and one reusable Recipe may support more than one Use Case.

## Gap Reflection

Use-case reflection is also a way to improve the Catalog.

When reviewing fragments or Recipes, classify unresolved needs rather than hiding them in prose.

Useful gap categories include:

- `missing_capability`: no known Catalog component currently provides the required ability;
- `missing_catalog_entry`: a relevant implementation or asset may exist, but it is not represented in the Catalog;
- `undocumented_capability`: a Catalog component may support the need, but the capability is not stated clearly enough to rely on;
- `unresolved_connection`: relevant components exist, but the interface, direction, artifact, or runtime contract connecting them is not sufficiently known.

Repeated gaps are important product and knowledge signals.

If the same missing capability or undocumented area appears across multiple requests, consider whether to:

- extend an existing Catalog entry;
- inspect source or runtime behavior and create a Knowledge Candidate;
- add a missing component or asset to the Catalog;
- document a reusable connection or Recipe pattern;
- prioritize implementation work for a genuinely missing capability.

## Relationship To The Knowledge Refinement Loop

Use Case reflection does not replace the Knowledge Refinement Loop.

Use Case fragments capture **demand-side knowledge**: what users are trying to achieve.
Knowledge Candidates capture **system-side knowledge**: implementation facts, runtime rules, design intent, architectural principles, usage patterns, and pitfalls.

These two loops should reinforce each other:

```text
User demand
  -> Use Case Fragment
  -> Recipe analysis
  -> Capability / Catalog gap
  -> Source / Runtime investigation
  -> Knowledge Candidate
  -> Catalog / Guide / Recipe improvement
  -> better future Recipe analysis
```

## Executable Knowledge Reflection

Documentation is not always the final promotion target for validated knowledge.

When a Knowledge Candidate contains a reusable `runtime_rule`, `known_pitfall`, or `validation_rule`, ask whether the condition can be detected reproducibly by software.

Prefer this maturity path when the answer is yes:

```text
Observation
  -> Knowledge Candidate
  -> verified knowledge
  -> Catalog / Primer / Recipe clarification
  -> executable guardrail
       - doctor.bash / doctor.ps1
       - validator
       - test
       - CI check
  -> Catalog runtime_checks declaration
  -> Business Pack runtime verification
```

The preferred first guardrail is **detect before auto-fix**.

A diagnostic PR should normally:

- detect the learned failure condition deterministically;
- explain the failed prerequisite or contract;
- provide a remediation hint when the correct action is known;
- return a useful non-zero status for failure;
- avoid silently installing software, downloading third-party assets, changing licensing choices, or changing runtime architecture.

An AI maintainer agent may propose or create a Draft PR for the owning component repository when all of the following are true:

- the Knowledge Candidate is sufficiently validated;
- the guardrail is software-only and low risk;
- the owning repository and appropriate doctor/test/validator location are clear;
- repository modification permission is available;
- no licensing, credentials, external cost, physical action, deployment approval, or consequential design decision must be crossed automatically.

When a human judgement is required, preserve it as an explicit gate. For example, an agent may add a check that reports a missing third-party asset and suggests a fetch command, but it should not silently decide that redistribution or automatic download is acceptable.

After the component PR is merged, add or update the component Catalog `runtime_checks` declaration so `tools/catalog_doctor.rb` can invoke the guardrail. See `docs/catalog-runtime-verification.md`.

The strongest mature state for a machine-detectable lesson is therefore not merely **documented**, but **guarded**: a future user or agent can be warned before rediscovering the same failure manually.

## Maintainer Review Checklist

During a periodic review:

1. Review recently added use-case fragments.
2. Merge or relate obvious duplicates without erasing useful source context.
3. Identify recurring user value independent of implementation details.
4. Extract required Capabilities.
5. Check whether the current Catalog actually states those Capabilities.
6. Record missing, undocumented, or unresolved areas explicitly.
7. Promote only sufficiently generic and useful patterns into canonical Use Cases.
8. Feed technical discoveries into the existing Knowledge Refinement Loop.
9. For validated runtime/validation knowledge, decide whether it can become an executable guardrail.
10. When appropriate, create or request the smallest component-owned doctor/test/validator PR and then expose it through Catalog `runtime_checks`.
11. Keep Recipes concrete; keep Use Cases implementation-independent.

The goal is not to maximize the number of Use Cases.
The goal is to let real user demand gradually reveal the reusable value patterns of the Hakoniwa ecosystem while turning reusable runtime lessons into durable, executable protections when possible.
