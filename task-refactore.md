# City World Web UI Recipe Refactor

## Goal

Complete the Workspace-oriented City World delivery started by PR #209.  A
clean source checkout must be able to materialize the City World Web UI
runtime through one Recipe, without requiring Hakoniwa Core, `hakopy`,
`hako-cmd`, or an accidentally pre-populated Python environment.

## Principles

- Keep PLATEAU asset generation and interactive Web UI runtime as separate
  Recipes.
- Foundation owns shared CPython and native Core-free Endpoint artifacts.
- `city-world-web-ui` owns its launcher configuration and, ultimately, its
  runtime state below `work/recipes/city-world-web-ui/`.
- Envsim, PDU Python, and PDU JavaScript remain revision-addressable source
  dependencies; their built/runtime state must not leak outside Workspace.
- A Recipe is feasible only when its validation explicitly distinguishes
  static contracts from clean-host execution evidence.

## Work plan

| Phase | Status | Deliverable | Acceptance |
| --- | --- | --- | --- |
| 1. Core-free Web UI Recipe | implemented; clean-host validation pending | `city-world-web-ui` Recipe, Foundation requirements, and Launcher | `recipe.py plan` resolves only Core-free Endpoint plus the three source dependencies. Clean-host `configure` / `doctor` / launch evidence remains required. |
| 2. Recipe-owned runtime state | implemented | Move Worker, Launcher, job, cache, and logs under the new Recipe workspace | New default Worker, Web UI, and lifecycle paths are under `work/recipes/city-world-web-ui/`; existing legacy data is preserved in place. |
| 3. Operator entry points | implemented; runtime smoke pending | Unified `configure`, `doctor`, `start`, `status`, and `stop` tools | Source users need no direct module invocation; doctor probes PDU Python Launcher and Core-free Endpoint imports with Foundation Python. |
| 4. Documentation | implemented | Separate asset-generation, Web UI, and portable delivery guides | Source Web UI guide, protocol, and portable package guide name the new Recipe and its ownership boundaries. |
| 5. Portable package migration | implemented; Windows package validation pending | PR #209 package tool consumes the Web UI Recipe contract | The ZIP contains Foundation Python, the Core-free Endpoint runtime, and Recipe source dependencies; it no longer embeds the asset-generation Recipe venv. |
| 6. Cross-platform validation | planned | Windows/macOS/Linux clean-host smoke and manual PLATEAU E2E record | Build/runtime imports are automated; external API generation remains explicitly recorded. |

## Phase 1 decisions

- Foundation request: `hakoniwa-pdu-endpoint` with
  `hakoniwa_core: false` and `python_binding: true`.
- `hakoniwa-pdu-python` is a Recipe-local source dependency, not a Foundation
  component. Its Launcher is loaded through the generated `PYTHONPATH`.
- The generic Recipe Launcher starts the Worker and static Web UI in
  `activate-only` mode. It does not start Hakoniwa Core.
- The generic Recipe doctor checks declared Foundation receipts and source
  artifacts. A Recipe-specific import doctor will be added with the operator
  entry points in Phase 3.

## Out of scope for Phase 1

- Moving existing `work/remote-operation/city-world-*` state.
- Changing portable ZIP layout.
- Downloading PLATEAU CityGML or claiming browser/large-area E2E validation.
