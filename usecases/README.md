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
- `recipes/` records concrete Business Pack system compositions and runtime validation.
- Component repositories may own executable Recipes that are specific to that component or product.
- `usecases/` records reusable problem contexts, intended outcomes, and the evidence
  boundary for communicating them.

A Usecase is not a marketing claim and is not executable by itself. It must not turn
component capability into a broad customer outcome without traceable support.

## Usecase learning loop

Usecases are not limited to requests that Hakoniwa can already realize.

Before reducing a new user goal directly to Catalog components, search existing
Usecases for the same reusable outcome. If no matching Usecase exists, a reusable
request may become a Usecase Fragment or canonical Usecase candidate even when its
current realization is `partially_feasible` or `unknown`.

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

For actor-first discovery, use [`ACTORS.md`](ACTORS.md). It organizes recurring
situations, difficulties, desired value, and existing evidence by the people who may
benefit. It is an exploration map, not a rule to create one Usecase per Actor.

## Submit a Usecase without AI

AI is an optional interface, not a requirement for contributing a Usecase.

If you have a reusable problem or desired outcome but do not use an AI assistant,
open the dedicated **[Usecase Request form](https://github.com/hakoniwalab/hakoniwa-business-pack/issues/new?template=usecase-request.yml)** directly.
This direct link is preferred because some GitHub clients or entry points may open a
blank Issue editor instead of showing the template chooser.

The form asks only for the user goal and context: what you want to achieve, who needs
it, when it matters, what is difficult today, and what would become better if it were
possible.

You do not need to know Hakoniwa component names, Recipe IDs, or whether the request
is currently feasible. Maintainers will search existing Usecases, assess Catalog and
Recipe evidence, and record missing capabilities or unresolved gaps as needed.

The Usecase Request form is only one Issue entry path. Blank Issues remain enabled for
bugs, technical discussions, and other topics.

## Status rules

Keep feasibility and validation separate:

- `feasible`: Catalog and Recipe evidence provide a credible implementation path.
- `partially_feasible`: a credible path exists, but a material composition gap remains.
- `unknown`: evidence is insufficient.
- `verified`: the referenced Recipe or source evidence has executed the representative
  behavior described by the Usecase.
- `partially_verified`: only a narrower reference path has been executed.
- `not_tested`: no execution evidence exists for the composed Usecase.
- `blocked`: validation cannot currently proceed because a required dependency or condition is unavailable.

A verified reference demo does not verify every customer environment, robot model,
network topology, or operational outcome. Record those boundaries in `limitations`.

## Recipe references

`realized_by` may point to either a Business Pack Recipe or a component-owned Recipe.

For a Business Pack Recipe, use its `recipe_id`.

For a component-owned Recipe, record `repository`, `path`, and a full pinned commit
`revision`. The owning repository must already be represented by a Catalog component.
Do not copy the Recipe into Business Pack solely to make it discoverable. The pinned
revision prevents a Usecase from silently claiming an unreviewed newer component Recipe.
Private and commercial access requirements remain those of the owning component.

Mandatory CI validates the external reference shape and that its repository is known to
the Catalog. It intentionally does not fetch private repositories. Maintainers verify the
referenced path and revision when adding or refreshing the Usecase, just as Catalog source
evidence is reviewed at a pinned revision.

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
4. Reference existing Business Pack or component-owned Recipes in `realized_by` when available.
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
status values, Catalog component references, local Recipe IDs, pinned component-owned
Recipe references, and duplicate IDs. It validates the shape and Catalog ownership of
external references; it does not fetch private repositories during mandatory CI.

## Layout

```text
usecases/
├── README.md
├── ACTORS.md
├── MAINTAINER.md
├── index.yaml
├── schema.yaml
├── tools/
│   └── validate_usecases.rb
└── examples/
    └── <usecase-id>.yaml
```
