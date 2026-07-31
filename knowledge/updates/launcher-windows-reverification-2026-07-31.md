# Launcher background lifecycle: native Windows re-verification

Source: https://github.com/hakoniwalab/hakoniwa-business-pack/pull/60#issuecomment-5141579769

The `hakoniwa-pdu` 1.6.5 session-file lifecycle contract was re-verified on native Windows 11 x86_64 with CPython 3.12.7.

Verified results:

- `hako_launcher ... --background <session-file>` exits successfully and records a RUNNING session.
- `hako_launcher_ctl status <session-file>` reports the expected PID and RUNNING state.
- `hako_launcher_ctl terminate <session-file>` follows the normal Launcher cleanup path and reports TERMINATED.
- No POSIX signal, CTRL_BREAK, or `taskkill` knowledge is required by the caller.
- Launcher and asset child processes are removed after termination.
- The control port has no remaining LISTEN socket; TIME_WAIT is not treated as a leaked listener.
- `terminate` is idempotent.
- `status` remains machine-readable after termination.
- Startup failure is persisted as `FAILED` with the failing command return code and log path.

Scope limits:

- Single-asset lifecycle only.
- `serve` mode, multi-asset dependency ordering, and recovery after killing the Launcher without `terminate` were not tested.
- The test used an isolated venv containing `hakoniwa-pdu` 1.6.5 rather than a Foundation-materialized runtime.

This evidence is sufficient to set `resolution.verified: true` for `launcher-cleanup-contract-lacks-windows-normal-termination-path`.
