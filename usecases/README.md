# Hakoniwa Representative Usecases

Usecases describe **who wants what outcome and why it matters**. They sit between an
open-ended user goal and a concrete Hakoniwa Recipe.

```text
User Goal
  -> Usecase
  -> Recipe
  -> Components
  -> Demo / Product
```

The layers have different responsibilities:

- `catalog/` records component facts and evidence.
- `recipes/` records concrete system compositions and runtime validation.
- `usecases/` records reusable problem contexts, intended outcomes, and the evidence
  boundary for communicating them.

A Usecase is not a marketing claim and is not executable by itself. It must not turn
component capability into a broad customer outcome without traceable support.

## Usecase learning loop

Usecases are not limited to requests that Hakoniwa can already realize.

Before reducing a new user goal directly to Catalog components, search existing
Usecases for the same reusable outcome. If no matching Usecase exists, a reusable
request may become a Usecase Fragment or canonical Usecase candidate even when its
current realization is `not_feasible`, `partially_feasible`, or `unknown`.

Keep the desired outcome separate from implementation gaps:

```text
Unmet demand
  -> Usecase / Fragment
  -> Required Capabilities
  -> Catalog / Recipe assessment
  -> missing capability / missing catalog entry / undocumented capability /
     unresolved connection
```

When Catalog capabilities, Recipes, or validation evidence change, previously blocked
or unknown Usecases should be considered for re-evaluation.

Maintainers should also work in the reverse direction: inspect existing Catalog
capabilities and validated Recipes, identify the reusable user outcomes they enable,
search for corresponding Usecases, and propose missing Usecase candidates when the
value can be stated independently from the Hakoniwa implementation.

See [`MAINTAINER.md`](MAINTAINER.md) for the Catalog/Recipe-to-Usecase discovery and
reconciliation workflow.

## Status rules

Keep feasibility and validation separate:

- `feasible`: Catalog and Recipe evidence provide a credible implementation path.
- `partially_feasible`: a credible path exists, but a material composition gap remains.
- `unknown`: evidence is insufficient.
- `verified`: the referenced Recipe or source evidence has executed the representative
  behavior described by the Usecase.
- `partially_verified`: only a narrower reference path has been executed.
- `not_tested`: no execution evidence exists for the composed Usecase.

A verified reference demo does not verify every customer environment, robot model,
network topology, or operational outcome. Record those boundaries in `limitations`.

## Audience vocabulary

`audience.primary` uses the controlled vocabulary in `schema.yaml`. Reuse an existing
role identifier when possible. Add a new value to the schema before using it in an
example so search, grouping, and generated outputs do not drift between synonyms.

Audience values identify reusable roles such as `robot-system-integrator` or
`technical-sales`; they should not encode a specific customer or organization.

## Communication and future Claims

`communication_notes.safe_statements` and `avoid_statements` are currently inline
Usecase guardrails. They capture wording boundaries that are specific to the Usecase
and its evidence scope.

A future `claims/` layer may extract reusable statements and restrictions. When that
layer is introduced:

- reusable statements should move to stable Claim IDs;
- Usecases may reference them through `claim_refs`;
- inline communication notes should remain only when the boundary is specific to the
  Usecase, environment, or referenced Recipe;
- migration must not broaden a statement beyond its original evidence.

This intentionally permits limited duplication until the Claim model and validator are
available.

## Authoring rules

1. Start from an audience, situation, problem, and desired outcome.
2. Search existing Usecases before creating a new canonical entry.
3. Preserve reusable unmet demand even when no current Recipe can realize it.
4. Reference existing Recipes in `realized_by` when available.
5. Reference Catalog components in `supported_by` only for capabilities actually used.
6. Do not describe remote access, production readiness, cost reduction, safety, or
   superiority unless explicit evidence supports that statement.
7. Keep commercial/private access and human approval gates visible.
8. Put missing validation or a needed Recipe in `next_steps`.
9. Re-evaluate blocked or unknown Usecases after relevant Catalog / Recipe changes.
10. Run the Usecase validator after adding or changing a Usecase.

## Validation

Run:

```bash
ruby usecases/tools/validate_usecases.rb
```

The validator checks required fields, file and index IDs, controlled audience values,
status values, Catalog component references, Recipe references, and duplicate IDs.

## Layout

```text
usecases/
├── README.md
├── MAINTAINER.md
├── index.yaml
├── schema.yaml
├── tools/
│   └── validate_usecases.rb
└── examples/
    └── <usecase-id>.yaml
```
