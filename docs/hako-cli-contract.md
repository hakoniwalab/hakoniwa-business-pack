# Hakoniwa `hako.py` CLI Contract

## Purpose

Hakoniwa repositories may expose `tools/hako.py` as a component-owned, cross-platform operational entry point for humans, CI, Business Pack recipes, and AI agents.

The goal is to define a small set of **common operations that can be understood consistently across repositories**. The contract standardizes command names and their operational meaning; it does not standardize the internal implementation.

A repository may delegate to Bash, PowerShell, CMake, native tools, or other existing scripts. Component-specific build and diagnostic knowledge remains owned by the component repository.

The standard invocation form is:

```text
python tools/hako.py <command> [command arguments/options]
```

A repository is not required to implement every standard operation. The Business Pack catalog records which operations each repository actually supports.

## Contract version

This document defines contract version `1.0`.

Catalog declarations SHOULD include `contract_version: "1.0"` so tooling can reason about command semantics without parsing repository-specific documentation.

## Standard operation model

Contract v1.0 defines six standard operations:

```text
doctor -> configure -> build -> test -> install
                         |
                         +--------> smoke
```

This diagram is a conceptual lifecycle, not a mandatory execution sequence. For example, a repository may not need `configure` or `install`, and a smoke test may run directly from build-tree artifacts.

The six names form the common cross-repository vocabulary:

- `doctor`: determine whether the environment/repository is ready.
- `configure`: resolve and prepare the build/runtime configuration.
- `build`: produce component artifacts.
- `test`: verify the component with automated tests.
- `install`: place consumable artifacts into an installation/package layout.
- `smoke`: exercise the smallest reproducible live behavior proving the component operates.

These meanings are deliberately broad enough to apply across different Hakoniwa repositories while remaining distinct enough for humans, CI, recipes, and AI agents to choose the correct operation.

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

Typical checks include toolchains, submodules, native dependencies, required files, platform support, package discovery, and component-specific runtime prerequisites.

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

In operational terms, `install` answers:

> Can the built component be materialized in the layout expected by downstream consumers?

### `smoke [mode]`

Purpose: execute the smallest reproducible runtime scenario that demonstrates the component is operational beyond compilation or unit tests.

This operation is OPTIONAL.

Semantics:

- SHOULD exercise real component startup and a minimal behavioral path when practical.
- SHOULD be bounded and suitable for CI or reproducible local validation.
- MAY accept a positional `mode` or component-specific options when multiple smoke scenarios are useful.
- Supported smoke modes MUST be declared in the catalog when they form a stable operational contract.

In operational terms, `smoke` answers:

> Does the built component actually perform its minimum expected live behavior?

Example: Hakoniwa Conductor Light supports `smoke auto` and `smoke manual`. `auto` verifies dynamic attach and world-time progress; `manual` additionally verifies explicit start/stop/reset state transitions.

## `--dry-run` and other options

`--dry-run` is **not** a seventh standard operation. It is an optional modifier whose exact placement is repository-owned.

A repository MAY expose `--dry-run` on `doctor`, `configure`, or another operation when doing so is useful and its behavior is unambiguous. In practice it is most naturally associated with diagnosis/configuration because those phases resolve intent before expensive or state-changing work.

The common contract intentionally does not require one universal `--dry-run` behavior. Depending on the repository it may, for example:

- print a resolved configuration;
- print the native command that would be executed;
- validate arguments/configuration without invoking the underlying build tool;
- suppress a later build/test/runtime action after resolution.

Therefore:

- tooling MUST NOT assume `--dry-run` exists;
- tooling MUST NOT assume which standard operation accepts it;
- the catalog SHOULD record it only where the implementation actually supports it;
- the owning repository defines its detailed semantics.

The same rule applies to other command options such as `--config`, `--build-dir`, or native argument pass-through: stable operationally relevant options may be cataloged, but they are not themselves part of the common operation vocabulary.

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

For example, `run` is not a standard v1.0 operation because the meaning of "run" varies too widely between libraries, services, simulators, generators, and sample-oriented repositories. A repository remains free to expose `run` as an extension.

## Catalog declaration

Catalog data SHOULD declare the supported `hako.py` surface when the repository exposes the standard entry point.

Example:

```yaml
hako_cli:
  path: tools/hako.py
  contract_version: "1.0"
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
- `modes` records stable positional smoke modes such as `auto` and `manual`.
- `options` records stable, operationally relevant options; it is not intended to mirror every argparse/help flag.
- Stable repository-specific commands MAY be recorded separately as extensions, but their semantics are not standardized by this contract.
- Catalog declarations describe repository-owned behavior. Business Pack MUST NOT duplicate component-specific doctor/configure/build/test/install/smoke implementation logic.

## Relationship to `runtime_checks`

`hako_cli` and `runtime_checks` answer different questions.

- `hako_cli` declares the **available operational interface** of the component.
- `runtime_checks` records **specific executable checks and evidence**, including platform coverage and exact invocation.

For example, a component may declare `hako_cli.commands.smoke.modes: [auto, manual]` and separately provide two `runtime_checks` entries documenting the exact smoke commands and platforms on which they are verified.

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

Business Pack defines the shared operational vocabulary and catalogs support. Each component owns the implementation and remains free to evolve its internal tooling without forcing callers to learn OS-specific entry points.

The contract is successful when a caller can approach an unfamiliar Hakoniwa repository and ask the same small set of operational questions — readiness, configuration, build, verification, installation, and live smoke behavior — without first learning that repository's platform-specific scripts.
