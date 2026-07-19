# Hakoniwa Component Catalog

This directory stores machine-readable component facts used to turn user goals
into Hakoniwa recipes and runnable demos.

## Purpose

The catalog answers these questions for each Hakoniwa component:

- What does this component do?
- What does it not do?
- What inputs and outputs does it expose?
- Which other components can it connect to?
- What dependencies or platform constraints matter?
- Which use cases and demos are already supported?

The intended reader is both human and AI. Keep entries explicit, short, and
grounded in the source repository.

## Layout

```text
catalog/
├── README.md
├── schema.yaml
├── index.yaml
├── component-template.yaml
├── tools/
│   └── generate_index.rb
│   └── validate_catalog.rb
└── components/
    └── <component-id>.yaml
```

Use one YAML file per component. The file name should match `id`.

`index.yaml` is the lightweight entry point for AI-assisted search. It should
contain enough information to shortlist candidate components before reading the
larger component YAML files.

Regenerate it from component entries:

```bash
ruby catalog/tools/generate_index.rb
```

Validate catalog entries before regenerating the index:

```bash
ruby catalog/tools/validate_catalog.rb
```

`schema.yaml` defines the controlled vocabulary used by catalog entries. When a
new value is needed, add it there first and then update entries.

## Authoring Rules

- Prefer facts that can be verified from the component repository.
- Use concise phrases instead of long prose.
- Put uncertainty in `status.notes` or `known_gaps`; do not hide it.
- Keep `capabilities` user-facing: describe outcomes, not only APIs.
- Keep `interfaces` concrete: files, protocols, commands, APIs, or data types.
- Use `recipe_roles` to explain how this component participates in a product.
- Treat `connects_to` as component-level planning knowledge. It means a
  relationship is relevant for recipes; it does not prove the connection is
  runtime-verified.
- Add links to local source docs so later catalog updates can be audited.
- Use controlled vocabulary from `schema.yaml` for fields such as
  `connects_to.direction`, `category.primary`, `status.maturity`, and
  `recipe_roles.role`.
- Mark commercial/private components explicitly with `distribution.channel` and
  the `commercial` secondary category.

## Minimum Viable Entry

A useful first entry should fill these fields:

- `id`
- `name`
- `summary`
- `category`
- `status`
- `capabilities`
- `limitations`
- `interfaces`
- `dependencies`
- `connects_to`
- `typical_usecases`
- `demo_candidates`
- `source_refs`

Other fields can start as empty lists and be filled as the catalog matures.
