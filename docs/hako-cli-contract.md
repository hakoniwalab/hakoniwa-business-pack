# Hakoniwa `hako.py` CLI Contract

## Purpose

Hakoniwa repositories may expose `tools/hako.py` as a component-owned, cross-platform operational entry point for humans, CI, Business Pack recipes, and AI agents.

The contract standardizes the **interface and command semantics**, not the internal implementation. A repository may delegate to Bash, PowerShell, CMake, native tools, or other existing scripts. Component-specific build and diagnostic knowledge remains owned by the component repository.

The standard invocation form is:

```text
python tools/hako.py <command> [command arguments/options]
```

A repository MUST NOT be assumed to support every standard command merely because `tools/hako.py` exists. Supported commands MUST be declared in the Business Pack catalog.

## Contract version

This document defines contract version `1.0`.

Catalog entries that declare `hako_cli` SHOULD include `contract_version: "1.0"` so tooling can reason about command semantics without parsing repository documentation.

## Standard commands

### `doctor`

Purpose: determine whether the current environment and repository state satisfy the prerequisites needed by the component.

Semantics:

- SHOULD be non-destructive and safe to run before build or execution.
- SHOULD report actionable missing prerequisites or inconsistent repository state.
- SHOULD absorb platform-specific detection where practical.
- MUST return exit code `0` when no blocking readiness problem is detected.
- MUST return a non-zero exit code when a blocking readiness problem is detected.
- SHOULD NOT install, download, or repair dependencies automatically unless the user explicitly requests such behavior through a separate option.

Typical checks include toolchains, submodules, native dependencies, required files, platform support, package discovery, and component-specific runtime prerequisites.

### `configure`

Purpose: configure or generate the component build/runtime environment without completing the full build.

This command is OPTIONAL. It is appropriate for repositories where configuration is a meaningful independently inspectable phase, such as CMake generation.

A `--dry-run` option MAY be provided when the component can safely show the resolved configuration command without applying it.

### `build`

Purpose: build the component through a platform-independent entry point.

Semantics:

- SHOULD hide OS/shell-specific dispatch from the caller.
- MAY delegate to existing repository-owned Bash, PowerShell, CMake, or other native build scripts.
- SHOULD preserve the repository's established build semantics rather than reimplementing them inside `hako.py`.
- MUST return non-zero when the delegated build fails.

### `test`

Purpose: run the component-owned automated test suite.

This command is OPTIONAL. It may run unit, integration, contract, or other automated tests owned by the repository. The catalog SHOULD describe the actual validation scope instead of assuming that `test` means full runtime validation.

### `smoke [mode]`

Purpose: execute the smallest reproducible runtime scenario that demonstrates the component is operational, beyond compilation or unit tests.

This command is OPTIONAL.

Semantics:

- SHOULD exercise real component startup and a minimal behavioral path when practical.
- SHOULD be bounded and suitable for CI or reproducible local validation.
- MAY accept a positional `mode` or component-specific options when multiple smoke scenarios are useful.
- Supported smoke modes MUST be declared in the catalog when they form a stable public operational contract.

Example: Hakoniwa Conductor Light supports `smoke auto` and `smoke manual`. `auto` verifies dynamic attach and world-time progress; `manual` additionally verifies explicit start/stop/reset state transitions.

### `run [target/options]`

Purpose: launch a component, example, demo, or other repository-owned runnable target.

This command is OPTIONAL. Because runtime targets vary substantially between repositories, arguments are component-specific and MUST be documented by the owning repository/catalog entry if exposed as a stable interface.

## Extension commands

Repositories MAY expose commands beyond the standard vocabulary when the operation is component-specific, for example asset generation or registry generation. Such commands do not become part of the common contract merely by appearing in one repository.

Business Pack tooling SHOULD prefer standard commands when equivalent behavior exists.

## Catalog declaration

Catalog entries SHOULD declare supported `hako.py` capabilities using `hako_cli` when the repository exposes the standard entry point.

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
    smoke:
      supported: true
      modes:
        - auto
        - manual
    run:
      supported: false
```

Rules:

- Absence of `hako_cli` means support is unknown or the repository does not expose the standard interface; it MUST NOT be interpreted as failure.
- A command not declared under `commands` MUST NOT be assumed to exist.
- `supported: false` MAY be used when explicitly recording a known unsupported standard command is useful; otherwise omission is preferred.
- `modes` records stable positional smoke modes such as `auto` and `manual`.
- `options` records stable, operationally relevant options; it is not intended to mirror every argparse/help flag.
- Catalog declarations describe repository-owned behavior. Business Pack MUST NOT duplicate the implementation of component-specific doctor/build/test/smoke logic.

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

Business Pack defines and catalogs the interface. Each component owns the implementation and remains free to evolve its internal build and runtime tooling without forcing callers to learn OS-specific entry points.
