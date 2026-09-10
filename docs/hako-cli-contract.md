# Hakoniwa `hako.py` CLI Contract

## Purpose

Hakoniwa repositories may expose `tools/hako.py` as a component-owned, cross-platform operational entry point for humans, CI, Business Pack recipes, and AI agents.

The goal is to define a small set of common operations that can be understood consistently across repositories. The contract standardizes command names, their operational meaning, and—when a repository is manifest-driven—the common way to select the configuration source. It does **not** standardize repository-internal implementation or manifest schema.

A repository may delegate to Bash, PowerShell, CMake, native tools, or other existing scripts. Component-specific build and diagnostic knowledge remains owned by the component repository.

The standard invocation form is:

```text
python tools/hako.py <command> [common options] [repository-specific options]
```

A repository is not required to implement every standard operation. The Business Pack catalog records which operations and common options each repository actually supports.

## Contract version

This document defines contract version `1.1`.

Contract v1.1 keeps the v1.0 operation vocabulary unchanged and promotes explicit manifest/configuration selection with `--config <path>` into the common option contract for manifest-driven repositories.

Catalog declarations SHOULD include `contract_version: "1.1"` when they implement the v1.1 semantics described here.

## Standard operation model

Contract v1.1 defines the same six standard operations as v1.0:

```text
doctor -> configure -> build -> test -> install
                         |
                         +--------> smoke
```

This diagram is a conceptual lifecycle, not a mandatory execution sequence. A repository may not need every phase, and a smoke test may run directly from build-tree artifacts.

The six names form the common cross-repository vocabulary:

- `doctor`: determine whether the environment/repository is ready.
- `configure`: resolve and prepare the build/runtime configuration.
- `build`: produce component artifacts.
- `test`: verify the component with automated tests.
- `install`: place consumable artifacts into an installation/package layout.
- `smoke`: exercise the smallest reproducible live behavior proving the component operates.

## Standard operations

### `doctor`

Purpose: determine whether the current environment and repository state satisfy the prerequisites needed by the component.

Semantics:

- SHOULD be non-destructive and safe to run before build or execution.
- SHOULD report actionable missing prerequisites or inconsistent repository state.
- SHOULD absorb platform-specific detection where practical.
- MUST return exit code `0` when no blocking readiness problem is detected.
- MUST return a non-zero exit code when a blocking readiness problem is detected.
- SHOULD NOT install, download, or repair dependencies automatically unless such behavior is explicitly requested by a repository-specific option.

In operational terms, `doctor` answers:

> Can this repository be operated correctly in the current environment?

### `configure`

Purpose: resolve and prepare the component's build/runtime configuration without completing the full build.

This operation is OPTIONAL. It is appropriate where configuration is a meaningful independently inspectable phase, such as manifest resolution, CMake generation, toolchain selection, feature resolution, or generation of resolved configuration files.

In operational terms, `configure` answers:

> What exactly are we going to build, and with which resolved settings?

### `build`

Purpose: produce component build artifacts through an OS-independent entry point.

Semantics:

- SHOULD hide OS/shell-specific dispatch from the caller.
- MAY delegate to existing repository-owned Bash, PowerShell, CMake, or other native build scripts.
- SHOULD preserve the repository's established build semantics rather than reimplementing them inside `hako.py`.
- MUST return non-zero when the delegated build fails.

In operational terms, `build` answers:

> Can this component be produced successfully from the resolved repository state?

### `test`

Purpose: run the component-owned automated verification suite.

This operation is OPTIONAL. It may include unit, integration, contract, import, or other automated tests owned by the repository. The catalog SHOULD describe the actual validation scope instead of assuming that `test` means complete runtime validation.

In operational terms, `test` answers:

> Do the component's defined automated contracts still hold?

Narrower commands such as `test-basic` or `test-timeout-cancel` are repository extensions unless separately promoted into a future common contract.

### `install`

Purpose: place build artifacts into the component-defined or user-requested installation/package layout so downstream consumers can use them.

This operation is OPTIONAL.

Semantics:

- SHOULD reuse the repository's existing install/package contract.
- MAY build first when required by the component implementation.
- MUST return non-zero when installation fails.
- Installation destinations and options remain component-specific and SHOULD be documented by the owning repository/catalog entry.

### `smoke [mode]`

Purpose: execute the smallest reproducible runtime scenario that demonstrates the component is operational beyond compilation or unit tests.

This operation is OPTIONAL.

Semantics:

- SHOULD exercise real component startup and a minimal behavioral path when practical.
- SHOULD be bounded and suitable for CI or reproducible local validation.
- MAY accept a positional `mode` or component-specific options when multiple smoke scenarios are useful.
- Supported smoke modes MUST be declared in the catalog when they form a stable operational contract.

## Common manifest/config selection: `--config`

Contract v1.1 promotes `--config <path>` to a common option for repositories whose `hako.py` is driven by a build/configuration manifest.

Canonical form:

```text
python tools/hako.py <command> --config <path> [repository-specific options]
```

Semantics:

- `--config` selects the repository-owned build/configuration manifest used by the requested operation.
- A manifest-driven repository SHOULD support `--config <path>` consistently across the standard operations for which the manifest is relevant.
- An explicitly supplied relative path SHOULD be interpreted relative to the caller's current working directory.
- A repository MAY define and document a default manifest, commonly `hakoniwa-build.yaml`.
- The resolution rule for an omitted default is repository-owned and MUST be documented when it matters operationally.
- Repositories that are not manifest-driven are not required to invent a manifest solely to satisfy this contract.
- Existing repository-specific CLI/native options MAY remain available for backward compatibility.

Most importantly, the common contract standardizes **selection**, not **schema**:

```text
common:     how a caller selects the configuration source
repo-owned: what that configuration source is allowed to contain
```

The selected manifest's schema, supported keys, defaults, validation rules, and precedence with repository-specific overrides remain component-owned. Contract v1.1 does not require identical YAML structures across repositories.

A repository MAY expose only a very small manifest surface. The component roll-out that motivated v1.1 intentionally produced different shapes while preserving the same selection contract:

- Endpoint / Bridge Core: relatively rich manifest-driven build configuration.
- Conductor Light: component/build/validation configuration owned directly by `hako.py`.
- Core Pro: user-facing YAML adapted into the existing native build-default mechanism.
- PDU RPC: a thin configuration layer preserving existing `build`, `test`, `install`, and extension-command semantics.
- MuJoCo Robots: only `build.dir` is configurable in manifest v1; the platform-native build drivers remain authoritative for everything else.

This diversity is intentional. A cross-repository contract is successful when it removes caller-side platform/config-selection knowledge without forcing unrelated component internals into one schema.

## Source dependency ownership

When a component is built from source together with another repository, the
component and the source orchestrator have different responsibilities.

The component-owned manifest and `hako.py`:

- SHOULD accept dependency roots through repository-owned manifest fields such
  as `paths.<dependency>_root`;
- SHOULD validate the resolved root, required source artifacts, repository
  identity, revision, and dirty state when those facts affect the build;
- SHOULD pass the resolved root to CMake or the repository's native build
  system without copying the dependency into the component repository;
- SHOULD record the resolved root and revision in inspectable build provenance;
- MUST NOT silently replace, reset, update, or repair an existing dependency
  checkout;
- SHOULD NOT clone or download a source dependency as an implicit side effect of
  `doctor`, `configure`, `build`, `test`, `install`, or `smoke`.

The caller or source orchestrator—such as Business Pack Recipe configuration,
Foundation source resolution, CI, or an explicit user setup step—owns:

- repository URL and access policy;
- requested revision;
- clone or checkout placement;
- materialization before component build operations begin.

This gives the standard flow a visible repository boundary:

```text
Business Pack / CI / user setup
  -> materialize dependency source
  -> select component manifest
  -> component hako.py resolves paths.<dependency>_root
  -> component doctor / configure / build / install
```

A repository MAY document a sibling checkout as its default dependency root.
It MAY also provide a separate, explicitly invoked bootstrap or download
extension when standalone onboarding requires one. Such an extension is not an
implicit standard-operation behavior and MUST disclose its network and mutation
effects.

Runtime packages and product-owned managed runtimes are separate from source
repository dependencies. Their owner may provide explicit download/install
operations and a version authority when the runtime contract requires it.

The intent is not to forbid all downloads. It is to keep source acquisition
visible and orchestrator-owned, while keeping component-specific build
knowledge component-owned.

## `--dry-run` and repository-owned options

`--dry-run` is not a seventh standard operation and does not acquire one universal behavior in v1.1.

A repository MAY expose `--dry-run` where useful. Depending on the repository it may print resolved configuration, print delegated commands, validate without invoking the underlying build tool, or suppress a later action.

Therefore:

- tooling MUST NOT assume `--dry-run` exists;
- tooling MUST NOT assume which standard operation accepts it;
- the catalog SHOULD record it only where the implementation actually supports it;
- the owning repository defines its detailed semantics.

Other options such as `--build-dir`, `--install-dir`, build-type overrides, dependency roots, toolchain arguments, and native argument pass-through remain repository-owned unless explicitly promoted by a future contract revision.

## Extension commands

Repositories MAY expose additional commands when the operation is repository-specific.

Examples include:

```text
test-basic
test-timeout-cancel
package-test
forge
generate
run
```

Extension commands are intentionally outside the common contract:

- their names and semantics are owned by the repository;
- Business Pack MAY catalog stable extensions for discovery;
- cross-repository tooling MUST NOT assume an extension exists or has the same meaning elsewhere;
- when a standard operation expresses the same intent, tooling SHOULD prefer the standard operation.

## Catalog declaration

Catalog data SHOULD declare the supported `hako.py` surface when the repository exposes the standard entry point.

Example:

```yaml
hako_cli:
  path: tools/hako.py
  contract_version: "1.1"
  common_options:
    - --config
  commands:
    doctor:
      supported: true
    configure:
      supported: true
      options:
        - --dry-run
    build:
      supported: true
    test:
      supported: true
    install:
      supported: true
    smoke:
      supported: true
      modes:
        - auto
        - manual
  extensions:
    - package-test
```

Rules:

- Absence of `hako_cli` means support is unknown or the repository does not expose the standard interface; it MUST NOT be interpreted as failure.
- A standard operation not declared under `commands` MUST NOT be assumed to exist.
- `supported: false` MAY be used when explicitly recording a known unsupported standard operation is useful; otherwise omission is preferred.
- `common_options` records options whose cross-repository semantics are defined by this contract, currently `--config` for manifest-driven repositories.
- `options` records stable repository-owned modifiers for individual commands.
- `modes` records stable positional smoke modes such as `auto` and `manual`.
- Stable repository-specific commands MAY be recorded separately as extensions.
- Catalog declarations describe repository-owned behavior. Business Pack MUST NOT duplicate component-specific implementation logic.

## Relationship to `runtime_checks`

`hako_cli` and `runtime_checks` answer different questions.

- `hako_cli` declares the available operational interface of the component.
- `runtime_checks` records specific executable checks and evidence, including platform coverage and exact invocation.

## Design principle

The common contract intentionally stops at the repository boundary:

```text
Human / CI / AI / Business Pack
            |
            v
     tools/hako.py
            |
     component-owned logic
      /      |       \
   Bash  PowerShell  CMake / native tools
```

Contract v1.1 adds one more reusable boundary rule:

> Standardize the caller-facing selection mechanism only when that removes real cross-repository friction. Preserve component-owned semantics behind that boundary.

This is why `--config` is common while manifest schema is not. The contract is successful when a caller can approach an unfamiliar Hakoniwa repository and ask the same operational questions—and select the intended configuration source—without first learning that repository's OS-specific build scripts or inventing a universal component schema.
