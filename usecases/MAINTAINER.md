# Use Case Maintainer Workflow

This guide defines how maintainers grow `usecases/` from both observed demand and existing Business Pack capabilities.

A Use Case describes **who wants what outcome and why it matters**. It is implementation-independent. Catalog entries and Recipes are evidence for how Hakoniwa may realize that value; they are not substitutes for the value statement itself.

## Two discovery directions

Business Pack should grow Use Cases from both directions.

```text
Demand-driven
User / customer / experiment / issue
  -> reusable desired outcome
  -> existing Use Case search
  -> Use Case Fragment or canonical Use Case candidate
  -> Required Capabilities
  -> Catalog / Recipe assessment
```

```text
Capability-driven
Catalog capabilities + validated Recipes
  -> identify demonstrated behavior
  -> ask what reusable user outcome that behavior enables
  -> existing Use Case search
  -> missing Use Case candidate
  -> review / generalize / promote
```

Neither direction has priority over the other. Demand-driven discovery prevents unmet needs from disappearing. Capability-driven discovery prevents existing technical value from remaining invisible because nobody has written the corresponding Use Case yet.

For capability-driven discovery, prefer an actor-first, composition-based review over
creating one Usecase per Catalog capability or Recipe. Start with
[`ACTORS.md`](ACTORS.md), identify a recurring situation and desired value from that
Actor's point of view, and then inspect clusters of Recipes and their Catalog
composition as evidence. A single component feature that is already obvious from its
Catalog entry normally does not justify a separate Usecase.

## Preserve unmet demand before feasibility is solved

Do not discard a request because the current Catalog cannot realize it.

When a user goal is reusable but the implementation is incomplete, preserve the desired outcome separately from the implementation gap:

- Use Case / Fragment: who wants the outcome, the situation, problem, desired outcome, and why it matters.
- Recipe / Catalog analysis: feasibility, validation, missing capabilities, missing catalog entries, undocumented capabilities, unresolved connections, and implementation details.

A Use Case may therefore remain valuable while its realization is `not_feasible`, `partially_feasible`, `unknown`, `blocked`, or `not_tested`.

When Catalog entries, Recipes, or validation evidence later change, previously blocked or unknown Use Cases should be candidates for re-evaluation.

## Capability-driven discovery from Catalog and Recipes

Use this workflow periodically or after significant Catalog / Recipe growth.

1. Inspect `catalog/index.yaml` and relevant component entries for capabilities that are not represented by existing Use Cases.
2. Inspect `recipes/examples/*.yaml`, prioritizing Recipes with stronger validation evidence.
3. For each meaningful composition, write the demonstrated behavior in implementation-neutral language.
4. Ask: **who would care about this behavior, in what situation, and what outcome does it enable?**
5. Search `usecases/index.yaml` and `usecases/examples/*.yaml` for an existing equivalent or broader Use Case.
6. If an existing Use Case covers it, add or improve `realized_by`, `supported_by`, evidence boundaries, limitations, or `next_steps` as appropriate.
7. If no Use Case covers it, create a Use Case candidate only when the user value remains meaningful without naming the Hakoniwa implementation.
8. Generalize away component names, repository names, customer-specific topology, and one-off demo details unless they are genuine constraints of the problem.
9. Set feasibility and validation no stronger than the supporting Catalog / Recipe evidence.
10. Run the Use Case validator and review the candidate for duplicate or marketing-only value claims.

## Do not invent demand from components

The reverse path is not permission to turn every component capability into a Use Case.

Reject or defer a candidate when:

- it only restates a component feature;
- no plausible audience, situation, problem, or desired outcome can be stated;
- the value depends entirely on naming a particular Hakoniwa implementation;
- it is merely a benchmark result with no reusable user consequence;
- it broadens a narrow validated behavior into unsupported claims about production readiness, safety, cost reduction, superiority, or business outcome.

Prefer a small number of reusable Use Cases over a feature-by-feature mirror of the Catalog.

## Reconciliation rules

Use Cases, Recipes, and Catalog should reinforce each other without collapsing into one layer.

```text
Use Case
  = desired user value and evidence boundary

Recipe
  = concrete system composition and runtime validation

Catalog
  = component capabilities, interfaces, artifacts, constraints, and evidence
```

When reconciling the layers:

- one Use Case may be realized by multiple Recipes;
- one Recipe may support multiple related Use Cases;
- a Catalog capability may support many Use Cases without becoming a Use Case itself;
- an unmet Use Case may have no `realized_by` Recipe yet;
- a new Recipe should trigger a check for missing Use Cases;
- a new or materially expanded Catalog capability should trigger a check for both new Recipes and missing Use Cases;
- a newly satisfied gap should trigger re-evaluation of Use Cases that were previously `unknown` or `partially_feasible`.

## Suggested maintainer review

During a periodic Business Pack reflection, review in this order:

```text
1. New / changed Catalog capabilities
2. New / changed Recipes and validation evidence
3. Existing Use Cases
4. Coverage gaps
5. Candidate Use Cases
6. Previously blocked / unknown Use Cases for re-evaluation
```

For each candidate, answer:

- Who is the reusable audience?
- What situation or problem exists before Hakoniwa is selected?
- What outcome is desired?
- Which required capabilities follow from that outcome?
- Which Catalog entries support those capabilities?
- Which Recipes demonstrate a credible realization path?
- What remains missing, unverified, private, commercial, human-gated, or environment-specific?
- Does an existing Use Case already cover this value?

## Validation

After adding or changing canonical Use Cases, run:

```bash
ruby usecases/tools/validate_usecases.rb
```

The validator confirms structure and references. It does not prove that the proposed user value is real. Promotion from candidate to canonical Use Case still requires maintainer judgement and an evidence boundary appropriate to the claim.
