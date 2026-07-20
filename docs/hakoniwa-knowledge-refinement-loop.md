# Hakoniwa Knowledge Refinement Loop

## 1. Purpose

Hakoniwa Business Pack should not remain a static catalog of manually curated knowledge.

AI agents will read Hakoniwa source repositories, documentation, tests, runtime logs, generated artifacts, Recipes, and user corrections. Those activities produce new knowledge about how Hakoniwa actually works.

The goal of the Knowledge Refinement Loop is to turn those discoveries into structured, reviewable knowledge that can improve the Business Pack over time.

The core loop is:

```text
Source Repository / Runtime / Conversation
              |
              v
         Observation
              |
              v
      Knowledge Candidate
              |
              v
     Validation / Review
              |
              v
          Promotion
              |
      +-------+--------+--------+---------+
      |       |        |        |         |
      v       v        v        v         v
   Catalog  Runtime  Ecosystem Component  Recipe /
            Primer   Guide     Asset Guide Pattern
```

The important principle is that an agent should not immediately rewrite high-level knowledge from a single code reading or inference.

New knowledge should first be captured with evidence and confidence, then promoted to the appropriate knowledge layer.

## 2. Knowledge Sources

Knowledge may be discovered from:

- source code;
- README and design documents;
- tests and CI results;
- runtime logs and execution traces;
- generated artifacts and configuration;
- successful or failed Demo execution;
- existing Recipes;
- issue and pull-request discussions;
- direct corrections or explanations from Hakoniwa developers and domain experts.

A source-code match alone is weak evidence. Runtime behavior, tests, explicit design documents, and expert correction may provide stronger evidence depending on the claim.

## 3. Observation First

An Observation records what was seen without immediately deciding that the existing knowledge system must change.

Examples:

- a component supports a backend not yet listed in Catalog;
- a generated artifact is consumed differently by MuJoCo and Godot;
- a runtime must start in a specific order;
- a configuration is declarative JSON rather than code-generated integration;
- a user correction reveals that two similar-looking components operate at different layers;
- a Demo exposes a recurring failure mode or validation rule.

An Observation should preserve source evidence and uncertainty.

## 4. Knowledge Candidate

When an Observation may affect reusable system knowledge, create a Knowledge Candidate.

A candidate should answer:

- What was learned?
- Where did the evidence come from?
- How confident are we?
- What kind of knowledge is it?
- Which existing knowledge layer may need to change?
- Does it contradict or refine current documentation?
- What validation is still needed?

Recommended knowledge types:

- `implementation_fact`
- `capability`
- `interface_contract`
- `runtime_rule`
- `design_intent`
- `architectural_principle`
- `usage_pattern`
- `known_pitfall`
- `validation_rule`
- `agency_boundary`

## 5. Promotion Targets

Promote validated knowledge according to its scope.

### Catalog

Use Catalog when the knowledge describes a specific component.

Examples:

- supported protocol or backend;
- capability;
- dependency;
- interface;
- platform support;
- known limitation.

### Runtime Primer

Use the Runtime Primer when the knowledge is needed to run or compose Hakoniwa correctly.

Examples:

- startup order;
- shared-memory assumptions;
- Conductor ownership;
- launcher lifecycle;
- PDU assignment rules;
- cleanup behavior;
- runtime failure signals.

### Base Ecosystem Guide

Use the Base Ecosystem Guide when the knowledge changes the conceptual understanding of common Hakoniwa infrastructure.

Examples:

- the role of PDU Registry;
- the distinction between Endpoint, Bridge, and RPC;
- declarative configuration as an ecosystem design principle;
- time-synchronization architecture.

### Component / Asset Guide

Use the Component / Asset Guide when the knowledge clarifies where a major component fits in the ecosystem.

Examples:

- MBody as the body-model conversion hub;
- Envsim as environment modeling and world conversion;
- PDU ROS as a lightweight runtime type-conversion bridge;
- PDU Python as a broader SDK and utility layer.

### Recipe / Pattern

Use a Recipe or reusable pattern when the knowledge describes how components work together.

Examples:

- a verified composition;
- a repeatable integration pattern;
- a human gate in an execution flow;
- a multi-process topology;
- a known combination with explicit validation evidence.

## 6. Design Intent and Tacit Knowledge

Source code usually reveals what the software does.

It often does not reveal why the architecture exists.

Important tacit knowledge includes questions such as:

- Why should Endpoint be used instead of direct shared memory in a distributed case?
- Why is Bridge Core separated from transport endpoints?
- Why is PDU ROS intentionally lighter than Endpoint-side ROS 2 / Zenoh integration?
- Why should Runtime Delegation remain outside introductory ecosystem explanations?
- Why are different generated artifacts needed for MuJoCo and Godot?

When a developer or domain expert provides this kind of explanation, record it as `design_intent` or `architectural_principle` rather than losing it in conversation history.

These candidates are especially valuable because they capture knowledge that may not be recoverable from code alone.

## 7. Confidence and Validation

Recommended confidence values:

- `low`: inferred from weak or incomplete evidence;
- `medium`: supported by source or documentation but not yet validated end to end;
- `high`: supported by authoritative source, runtime evidence, tests, or explicit expert confirmation.

Confidence is not the same as promotion status.

A high-confidence implementation detail may still belong only in Catalog, while a lower-level observation may require broader validation before changing an ecosystem guide.

## 8. Contradictions and Corrections

When new evidence contradicts existing knowledge:

1. Do not silently overwrite the old claim.
2. Create a Knowledge Candidate that records the contradiction.
3. Identify the existing document or Catalog entry affected.
4. Compare evidence and source revisions.
5. Update the promoted knowledge only after the newer interpretation is sufficiently grounded.

Direct expert corrections should be treated as strong evidence, but the final documentation should still state the rationale and scope clearly.

## 9. Recommended Agent Behavior

When an agent learns something important while reading code, running a Demo, or talking with a developer:

1. Decide whether the information is reusable beyond the current task.
2. If not reusable, keep it in the current task context.
3. If reusable, record an Observation or Knowledge Candidate.
4. Attach repository, path, revision, runtime evidence, or expert source when available.
5. Classify the knowledge type.
6. Identify likely promotion targets.
7. Validate the claim to the appropriate level.
8. Propose or create the corresponding documentation or Catalog update.
9. Preserve unresolved uncertainty instead of forcing promotion.

The agent should prefer incremental refinement over large speculative rewrites.

## 10. Long-Term Goal

The desired system is a continuously improving knowledge base:

```text
Development
   +
Runtime Validation
   +
AI Investigation
   +
Developer Conversation
        |
        v
Structured Knowledge Candidates
        |
        v
Reviewed and Promoted Knowledge
        |
        v
Better Catalogs, Guides, Recipes, and Agents
```

This allows Hakoniwa knowledge to accumulate even when one person is not manually writing every document.

The goal is not to remove human expertise. The goal is to make expert corrections, design intent, implementation discoveries, and runtime lessons persist as structured system knowledge instead of disappearing into individual memory or one-off conversations.
