# Hakoniwa Knowledge Candidates

This directory stores reusable knowledge discovered while reading source repositories, running demos, validating Recipes, or receiving corrections from Hakoniwa developers and domain experts.

The purpose is to avoid losing important findings in one-off conversations or local debugging sessions.

Use the process described in `../docs/hakoniwa-knowledge-refinement-loop.md`.

## Workflow

```text
Observation
  -> Knowledge Candidate
  -> Validation / Review
  -> Promotion
```

Knowledge Candidates are temporary structured records. They are not automatically authoritative documentation.

After validation, promote the knowledge to the appropriate layer:

- Catalog: component-specific facts and capabilities
- Runtime Primer: runtime rules and operational contracts
- Base Ecosystem Guide: common architecture and design principles
- Component / Asset Guide: ecosystem positioning of major components
- Recipe / Pattern: reusable system compositions and integration patterns

## Suggested Layout

```text
knowledge/
├── README.md
├── candidate-template.yaml
└── candidates/
    └── <candidate-id>.yaml
```

The `candidates/` directory may be created when the first actual candidate is recorded.

## Important Rule

Do not promote a single inference from source-code text directly into high-level ecosystem documentation.

Preserve evidence, confidence, contradictions, and remaining validation needs first.

Expert corrections and design explanations are valuable sources of tacit knowledge. Record the rationale, not only the corrected conclusion.
