# Hakoniwa Workspace Environment

[日本語](hakoniwa-workspace-environment-ja.md)

## Purpose

Hakoniwa Foundation keeps reusable binaries, libraries, a shared Python virtual environment, Core configuration, and runtime state under `work/foundation/`.

The Hakoniwa Workspace Environment applies that Foundation only to an isolated child process. It does not modify the parent shell or system Python. This prevents stale `hakopy.pyd` files, persistent `PYTHONPATH` entries, and unrelated virtual environments from taking precedence over the selected Foundation.

## Standard user operation

The entry point is the same on every supported OS:

```bash
python tools/workspace.py enter
```

`enter` refreshes the required preparation before starting an isolated child shell:

- regenerate compatibility activation scripts;
- update the Python bootstrap `.pth` when the Foundation venv exists;
- build the latest workspace environment;
- start a child shell without user profiles;
- prefix its prompt with `(hako)`.

Example:

```text
(hako) tmori@TakashinoMBP hakoniwa-business-pack %
```

The `(hako)` marker means the current shell is inside the Hakoniwa Workspace Environment.

Leave it with the same command on every OS:

```bash
exit
```

The child shell terminates and control returns to the unchanged parent shell. No environment-restoration command is required.

The standard lifecycle is:

```text
enter -> Hakoniwa work -> exit
```

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

The environment sets Foundation-owned Python, binary, library, configuration, and runtime paths. It intentionally does not inherit `PYTHONPATH` or `PYTHONHOME`.

## Run one command

CI, Recipe wrappers, AI agents, and other non-interactive callers use:

```bash
python tools/workspace.py run -- <command> [args...]
```

`run` also refreshes the preparation before starting the command with the same workspace contract.

Example:

```bash
python tools/workspace.py run -- python tools/foundation.py doctor \
  --recipe recipes/examples/drone-single-mujoco-threejs.yaml
```

## Validate Python bindings

After entering the workspace, validate the selected interpreter and module origins with:

```bash
python tools/workspace.py doctor
```

The check requires:

- `sys.executable` and `sys.prefix` under the Foundation venv;
- `hakopy`, `hakoniwa_pdu`, and `hakoniwa_pdu_endpoint` resolved from the Foundation workspace.

The Business Pack Foundation Python contract is CPython 3.12. Before component
build/install, `tools/foundation.py` validates that identity and requires Core's
SOABI-tagged `hakopy`. Foundation `doctor` compares the active interpreter
SOABI with the Python metadata recorded in the Core Component Receipt. A
missing legacy Receipt or an ABI mismatch is `INCOMPATIBLE`, not an invitation
to load an untagged extension.

## Low-level compatibility operations

`prepare`, the POSIX activation script, and the PowerShell activation script remain available for compatibility and debugging, but they are not the standard user workflow.

```bash
python tools/workspace.py prepare
source work/foundation/activate
deactivate_hakoniwa
```

```powershell
. .\work\foundation\Activate.ps1
Exit-HakoniwaWorkspace
```

Normal users should use the OS-independent `enter` and `exit` lifecycle.

## Ownership

| Layer | Responsibility |
| --- | --- |
| Component `hako.py install` | Install Python/native artifacts into the Foundation boundary |
| Foundation workspace | Own the shared venv, Core Python, configuration, runtime state, and Receipts |
| Workspace Environment | Remove ambient Python discovery and put the Foundation first in a child process |
| `doctor` / smoke | Verify the selected interpreter and actual module origins |
| Recipe / Launcher wrapper | Use `workspace.py run` or the same environment contract |

The goal is not an OS-specific activation procedure. It is a clear lifecycle: enter the selected Hakoniwa Foundation, work inside it, and discard the child shell with `exit`.
