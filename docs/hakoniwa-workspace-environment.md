# Hakoniwa Workspace Environment

[日本語](hakoniwa-workspace-environment-ja.md)

## Purpose

Hakoniwa Foundation keeps reusable binaries, libraries, a shared Python virtual environment, Core configuration, and runtime state under `work/foundation/`.

The Hakoniwa Workspace Environment is the corresponding **process-environment boundary**. It is not an OS-level container. It applies a Foundation-owned environment only to a selected shell or child process.

This prevents stale `hakopy.pyd` files, persistent `PYTHONPATH` entries, unrelated virtual environments, and system Python state from taking precedence over the selected Foundation.

## Managed boundary

```text
work/foundation/
├── install/
│   ├── bin/
│   ├── lib/
│   ├── python/                  # shared Foundation venv
│   └── share/hakoniwa/python/  # Core hakopy
├── config/cpp_core_config.json
├── activate
└── Activate.ps1
```

The environment sets:

- `HAKONIWA_WORKSPACE_ACTIVE=1`
- `HAKONIWA_WORKSPACE_ROOT=<business-pack-root>`
- `HAKONIWA_HOME=<workspace>/work/foundation/install`
- `HAKO_CONFIG_PATH=<workspace>/work/foundation/config/cpp_core_config.json`
- `VIRTUAL_ENV=<workspace>/work/foundation/install/python`
- `PYTHONNOUSERSITE=1`
- Foundation Python and Foundation `bin` at the front of `PATH`
- Foundation `lib` at the front of `LD_LIBRARY_PATH` on Linux
- Foundation `lib` at the front of `DYLD_LIBRARY_PATH` on macOS
- Foundation `bin` at the front of Windows `PATH` for DLL lookup

It intentionally does not inherit:

- `PYTHONPATH`
- `PYTHONHOME`

The Foundation venv, its installed packages, and its `.pth` entries own Python module selection.

## Prepare

Generate local activation scripts:

```bash
python tools/workspace.py prepare
```

The generated files live under `work/` and are not committed.

## Enter and leave

### Isolated child shell

```bash
python tools/workspace.py enter
```

Exit the child shell to return to the original environment. The parent shell is unchanged.

### Current POSIX shell

```bash
source work/foundation/activate
```

Restore the previous environment with:

```bash
deactivate_hakoniwa
```

The script preserves whether each managed variable was originally unset or set.

### PowerShell

```powershell
. .\work\foundation\Activate.ps1
```

Restore the previous environment with:

```powershell
Exit-HakoniwaWorkspace
```

## Run one command

CI, Recipe wrappers, AI agents, and other non-interactive callers must not depend on a previously activated shell.

```bash
python tools/workspace.py run -- <command> [args...]
```

Example:

```bash
python tools/workspace.py run -- python tools/foundation.py doctor \
  --recipe recipes/examples/drone-single-mujoco-threejs.yaml
```

`enter`, generated activation scripts, and `run` use the same environment contract.

## Validate Python bindings

```bash
python tools/workspace.py doctor
```

The check requires:

- `sys.executable` under the Foundation venv;
- `sys.prefix` under the Foundation venv;
- `hakopy` resolved from the Foundation workspace;
- `hakoniwa_pdu` resolved from the Foundation workspace;
- `hakoniwa_pdu_endpoint` resolved from the Foundation workspace.

A module resolved from an ambient path is a boundary violation and fails the check.

## Ownership

| Layer | Responsibility |
| --- | --- |
| Component `hako.py install` | Install Python/native artifacts into the Foundation boundary |
| Foundation workspace | Own the shared venv, Core Python, configuration, runtime state, and Receipts |
| Workspace Environment | Remove ambient Python discovery and put the Foundation first |
| `doctor` / smoke | Verify the selected interpreter and actual module origins |
| Recipe / Launcher wrapper | Use `workspace.py run` or the same environment contract |

## Non-goals

- OS filesystem, network, or user-namespace isolation
- Container-image dependency pinning
- Concurrent Foundation profile switching
- Modifying or deleting system Python or user virtual environments

The goal is a reproducible process environment explicitly owned by the selected Hakoniwa Foundation, not a container.
