# Hakoniwa Knowledge Candidates

This directory stores reusable knowledge discovered while reading source repositories, running demos, validating Recipes, or receiving corrections from Hakoniwa developers and domain experts.

The purpose is to avoid losing important findings in one-off conversations or local debugging sessions.

Use the process described in `../docs/hakoniwa-knowledge-refinement-loop.md`.

## Workflow

```text
Observation
  -> Knowledge Candidate
  -> Validation / Review
  -> Implementation Issue (when code or docs should change)
  -> Fix / Re-verification
  -> Promotion
```

Knowledge Candidates are temporary structured records. They are not automatically authoritative documentation.

A candidate's top-level `status` describes **knowledge maturity** (`candidate`, `validating`, `promoted`, or `rejected`). It must not be used as a substitute for implementation progress.

When a candidate results in concrete repository work, use `tracking` to record related issues and implementation state. Use `resolution` to record what ultimately changed and whether the result was re-verified.

Closing an implementation issue does not by itself prove that the original observation is resolved. Record the fixing PR/revision and set `resolution.verified: true` only after appropriate verification.

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
├── tools/
│   └── validate_candidates.rb
└── candidates/
    └── <candidate-id>.yaml
```

The `candidates/` directory may be created when the first actual candidate is recorded.

Validate candidate structure and lifecycle metadata with:

```bash
ruby knowledge/tools/validate_candidates.rb
```

## Important Rule

Do not promote a single inference from source-code text directly into high-level ecosystem documentation.

Preserve evidence, confidence, contradictions, remaining validation needs, implementation tracking, and resolution evidence first.

Expert corrections and design explanations are valuable sources of tacit knowledge. Record the rationale, not only the corrected conclusion.
