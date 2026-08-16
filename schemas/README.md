# Business Pack shared schemas

This directory owns machine-readable contracts that are shared by more than one
Business Pack document, Recipe, or tool. Concrete Catalog entries, Recipes, and
experiment inputs remain in their domain directories; only their reusable
contracts belong here.

## Placement

Place a new shared schema under the domain of the document that consumes it:

```text
schemas/
├── README.md
├── <cross-domain-contract>.yaml
└── <domain>/
    ├── <domain-wide-document-kind>.yaml
    └── <family>/
        └── <document-kind>.yaml
```

Examples:

- `schemas/native-runtime.yaml` is a cross-domain Catalog-to-Recipe contract.
- `schemas/remote-operation/message.schema.json` is the JSON wire contract for
  constrained, state-machine-based coordination between Recipe hosts.
- `schemas/remote-operation/artifact-message.schema.json` is the separate,
  chunked ZIP-evidence transfer contract used after remote execution.
- `schemas/recipes/drone-fleet-performance/experiment.yaml` is the contract for
  Drone Fleet performance inputs below
  `recipes/experiments/drone-fleet-performance/`.

Existing schemas such as `catalog/schema.yaml`, `foundation/schema.yaml`,
`recipes/schema.yaml`, and `usecases/schema.yaml` stay in place for
compatibility. New schemas should use this directory when they are shared by
multiple documents or tools. A component-private manifest schema remains in the
owning component repository.

## Naming and ownership

- Create a family directory when a contract contains fields specific to one
  Recipe or a closely related Recipe family. Do not accumulate unrelated
  validators directly below `schemas/<domain>/`.
- Use a stable document-kind name inside the family, not the name of one example
  or platform.
- Keep schema filenames lowercase with hyphens only when the document kind
  contains multiple words.
- Put the concrete document under its normal domain path. A schema is not an
  example or an execution input.
- The schema owns portable structure and cross-field invariants. OS-specific
  inspection or runtime behavior belongs to a platform adapter or the owning
  component.
- Reject unknown controlled fields. Explicitly mark extension mappings whose
  contents are owned by a Recipe-specific tool.

## Versioning

- Every governed document declares its schema version.
- A validator must reject unsupported versions rather than guessing.
- Add optional, backward-compatible fields without changing the version.
- Increment the version when existing documents require migration or the
  meaning of an existing field changes.
- Do not encode a repository revision, temporary address, PID, observed status,
  or other resolved runtime state in a reusable input schema.

## Validation files

When a contract has executable invariants, keep its validator and tests beside
the schema:

```text
schemas/<domain>/<document-kind>.yaml
schemas/<domain>/<document_kind>_validation.rb
schemas/<domain>/test_<document_kind>_validation.rb

# Recipe-family-specific contract
schemas/recipes/<family>/<document-kind>.yaml
schemas/recipes/<family>/<document_kind>_validation.rb
schemas/recipes/<family>/test_<document_kind>_validation.rb
```

The validator checks portable structure and cross-field consistency. A
Recipe-specific runner may add stricter operational checks after it resolves
host capabilities, ports, build limits, paths, and generated artifacts.
