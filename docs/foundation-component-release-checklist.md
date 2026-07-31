# Foundation Component Release Checklist

[日本語](foundation-component-release-checklist-ja.md)

This document defines the maintainer contract for releasing a new externally
usable feature from a Hakoniwa component that participates in the reusable
Foundation.

Its purpose is to prevent partial releases where source code is new but package
metadata, Component Receipts, Catalog evidence, Recipes, or the active local
Foundation still describe an older state.

## 1. Classify the external contract

Record two separate dimensions:

- `capability`: whether the installed component actually provides the feature.
- `version.min`: the first formal release whose external contract includes it.

When a feature must not be used before its formal release, require both:

```yaml
foundation_requirements:
  hakoniwa-pdu-python:
    version:
      min: 1.6.5
    capabilities:
      launcher_background_lifecycle: true
```

Do not infer a feature only from a version, and do not use a Capability in a way
that loses the formal release boundary.

## 2. Update the owner repository

Treat these as one release operation:

- package or project version metadata;
- user-facing README, design documentation, and CLI help;
- the Capability emitted in the Component Receipt by `hako.py`;
- `smoke` checks against the installed artifact;
- compatibility tests for manifest defaults and the existing native contract;
- CI for the supported platforms.

Before declaring a Receipt Capability, verify it from the installed artifact.
Importing the source tree is not sufficient evidence.

## 3. Keep build output unambiguous

Do not leave multiple generations of wheels or packages in one build directory.

Rules:

- remove only stale outputs owned by the component before building;
- do not broadly delete a workspace or shared directory;
- fail instead of silently selecting one of multiple install candidates;
- generate the Receipt from the artifact that was actually installed and smoked.

For example, this is ambiguous:

```text
build/
  hakoniwa_pdu-1.6.3-py3-none-any.whl
  hakoniwa_pdu-1.6.5-py3-none-any.whl
```

Scope cleanup to the owner package, such as `hakoniwa_pdu-*.whl`.

## 4. Verify Python environment identity

A successful `pip install` does not prove that the Foundation was updated.
The package may have been installed into Blender Python, system Python, pyenv,
Homebrew, or another virtual environment.

Run checks through the exact Foundation interpreter:

```bash
work/foundation/install/python/bin/python -c \
  "import sys; print(sys.executable); print(sys.prefix)"

work/foundation/install/python/bin/python -m pip show <package>
```

Collect at least:

- package version;
- the `Location` from `pip show`;
- `sys.executable` and `sys.prefix`;
- smoke imports for the required modules and CLIs;
- version, source revision, and Capability from the Component Receipt.

Never use installation success from another Python environment as evidence that
the Foundation venv is current.

## 5. Update Business Pack

After the owner repository release, update:

- Catalog `verification.source_revision` and `verified_at`;
- Capability descriptions and evidence revisions;
- Recipe `version.min` for formal minimum releases;
- the Capability actually required by each Recipe;
- Foundation schema, validators, and tests;
- validation evidence for execution or reuse decisions.

Catalog evidence should include both the feature implementation and the revision
that establishes the public package version.

## 6. Verify the Foundation transition

Verify both rejection of the old Receipt and acceptance of the new Receipt:

```bash
python3.12 tools/foundation.py doctor --recipe <recipe.yaml>
python3.12 tools/foundation.py plan --recipe <recipe.yaml>
python3.12 tools/foundation.py build --recipe <recipe.yaml>
python3.12 tools/foundation.py doctor --recipe <recipe.yaml>
```

Expected transition:

```text
old version / missing capability
  -> INCOMPATIBLE
  -> rebuild only the affected component and known downstream dependencies
  -> smoke the installed artifact
  -> SATISFIED
```

Build success alone is not sufficient. The final Receipt and installed artifact
must satisfy the Recipe requirements.

## 7. Release completion criteria

- [ ] Version metadata is updated.
- [ ] The user-facing external contract is documented.
- [ ] The Receipt records the new Capability.
- [ ] Smoke verifies the Capability from the installed artifact.
- [ ] A stale build artifact cannot be selected.
- [ ] Owner-repository unit tests and CI pass.
- [ ] Catalog revisions and evidence are updated.
- [ ] Relevant Recipes declare `version.min` and the Capability.
- [ ] An old Foundation Receipt evaluates as `INCOMPATIBLE`.
- [ ] The rebuilt Foundation evaluates as `SATISFIED`.
- [ ] Process lifecycle validation is distinguished from behavioral validation.

This checklist is not only about assigning a release number. It creates an
explainable chain of evidence for why a future human or AI may reuse an
installed Foundation.
