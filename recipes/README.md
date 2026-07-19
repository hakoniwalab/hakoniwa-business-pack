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
- It gives a minimal demo path that can validate the design.

The first goal is not code generation. The first goal is to convert an ambiguous
user request into a Hakoniwa-valid system composition.

## Core Shape

Every recipe should make these sections explicit:

- `goal`
- `feasibility`
- `constraints`
- `components`
- `connections`
- `data_flow`
- `time_model`
- `artifacts`
- `missing_pieces`
- `demo`
- `expected_result`
- `source_catalogs`
- `source_artifacts`

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

