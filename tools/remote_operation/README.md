# Remote operation helpers

This package provides a reusable control plane for coordinating Recipe-local
runners on multiple hosts. It uses Hakoniwa PDU Endpoint for communication but
does **not** provide SSH, remote shell access, or arbitrary command execution.

## Architecture and responsibility

```text
controller / server                         worker / client
-------------------                         ---------------
validated command JSON  -- PDU/TCP ------> validated command JSON
                                               |
                                               v
                                      fixed local operation table
                                               |
                                               v
                                         existing Recipe tool

validated status JSON   <--- PDU/TCP ------ validated status JSON
```

The wire payload is compact UTF-8 JSON governed by
`schemas/remote-operation/message.schema.json`. Only enumerated commands and
statuses can be represented. Executable paths, shell commands, environment
variables, and arbitrary command arguments are intentionally absent.

`pdu_transport.py` carries the JSON bytes through the Python binding of
`hakoniwa-pdu-endpoint` using bidirectional TCP. The Endpoint build may set
`features.hakoniwa_core: false`; no Hakoniwa Core process or binding is used by
this control plane.

The server binds to a Recipe-selected reachable address. Clients initiate the
connection, so a WSL2-private address never needs to be published and the
server never needs inbound access to a WSL2 guest.

This package owns:

- JSON encode/decode and validation;
- session, attempt, sequence, host, and configuration identity;
- allowed command/status values and their state transitions;
- PDU Endpoint TCP configuration and byte transport;
- durable JSON Lines communication logs.

A consuming Recipe owns:

- the fixed mapping from protocol commands to local operations;
- process startup, observation, timeout, and cleanup;
- result preservation and recovery policy;
- the decision about which host is controller or worker.

## Message lifecycle

The current lifecycle is:

```text
REGISTERED
  <- PREPARE -> PREPARING -> READY
  <- LAUNCH  -> LAUNCHED [-> JOINED]
  <- RUN     -> RUNNING -> TERMINATED
  <- CLEANUP -> CLEANED
```

`JOINED` is optional because the Conductor server can observe all joined
clients directly. Any active phase may report `FAILED`; cleanup remains a
separate explicit operation.

Every message carries the following common identity:

```json
{
  "schema_version": 1,
  "kind": "status",
  "type": "READY",
  "session_id": "local-recipe-0123456789abcdef",
  "sequence": 3,
  "attempt": 1,
  "source_host": "cli-01",
  "configuration_id": "drone-fleet-single-host-ci",
  "config_hash": "<64 lowercase hexadecimal characters>"
}
```

Unknown fields and unknown command types such as `EXEC` are rejected.

## Prerequisites

The local Recipe smoke assumes that Business Pack has already materialized:

- the standard Foundation Python environment;
- the Python/native `hakoniwa-pdu-endpoint` runtime;
- the native Drone distribution and MuJoCo runtime;
- the Foundation components requested by `drone-fleet-single-host-ci.yaml`.

The smoke reuses existing materialized components. It does not download or
build dependencies.

## Run the local Recipe smoke

The first integration uses two local processes and the existing one-drone,
headless onboarding Recipe. The controller sends only protocol commands; the
worker maps them to the fixed local lifecycle `configure`, `doctor`, `start`,
`smoke`, and `stop`.

Run it from the Business Pack repository root:

```bash
python3 tools/workspace.py run -- \
  python3 -m tools.remote_operation.single_host_recipe_smoke run
```

`workspace.py run` is required even when the prompt already shows `(hako)`.
On macOS it passes the Foundation library directory directly to Python without
an intermediate system shell discarding `DYLD_LIBRARY_PATH`.

During execution, the controller displays the protocol flow. A successful run
contains lines equivalent to:

```text
[PDU][srv-01][RECEIVE] seq=1 attempt=1 status:REGISTERED source=cli-01
[PDU][srv-01][SEND] seq=1 attempt=1 command:PREPARE source=srv-01
[PDU][srv-01][RECEIVE] seq=2 attempt=1 status:PREPARING source=cli-01
[PDU][srv-01][RECEIVE] seq=3 attempt=1 status:READY source=cli-01
...
[PDU][srv-01][RECEIVE] seq=7 attempt=1 status:CLEANED source=cli-01
[OK] remote-operation controlled Recipe completed
```

This is a control-plane integration smoke, not a performance measurement.

## Evidence and logs

Each run writes the following files:

```text
work/remote-operation/single-host-recipe-smoke/
├── smoke-result.json          # final protocol/Recipe evidence
├── server-events.jsonl        # controller send/receive records
├── client-events.jsonl        # worker send/receive records
├── worker.stdout.log
├── worker.stderr.log
├── server-endpoint/
│   ├── endpoint.json
│   ├── cache.json
│   └── comm.json
├── client-endpoint/
│   ├── endpoint.json
│   ├── cache.json
│   └── comm.json
└── recipe-logs/
    ├── configure.stdout.log
    ├── configure.stderr.log
    ├── doctor.stdout.log
    ├── doctor.stderr.log
    ├── start.stdout.log
    ├── start.stderr.log
    ├── smoke.stdout.log
    ├── smoke.stderr.log
    ├── stop.stdout.log
    └── stop.stderr.log
```

The underlying Recipe result remains at:

```text
work/recipes/drone-fleet-single-host/validation/execution-summary.json
```

Useful inspection commands:

```bash
jq . work/remote-operation/single-host-recipe-smoke/smoke-result.json

jq -r \
  '[.direction, .message.sequence, .message.kind, .message.type] | @tsv' \
  work/remote-operation/single-host-recipe-smoke/server-events.jsonl

jq . \
  work/recipes/drone-fleet-single-host/validation/execution-summary.json
```

Communication JSONL records include a local wall-clock recording time and the
complete validated protocol message. They are suitable for diagnosing the
last completed transition after a timeout or failure.

## Tests

Run the dependency-free protocol and adapter tests:

```bash
python3 -m unittest \
  tools/tests/test_remote_operation_protocol.py \
  tools/tests/test_remote_operation_pdu_transport.py \
  tools/tests/test_remote_operation_recipe_smoke.py \
  -v
```

The normal unit suite uses a fake Endpoint so it remains portable. To verify
the real native Python Endpoint over loopback TCP, first install its runtime and
run:

```bash
HAKO_REMOTE_OPERATION_PDU_INTEGRATION=1 \
  python3 tools/workspace.py run -- \
  python3 -m unittest tools/tests/test_remote_operation_pdu_integration.py -v
```

## Reusing the library

New Recipe adapters should import the shared protocol and transport rather than
copying the local smoke:

```python
from tools.remote_operation import protocol
from tools.remote_operation.pdu_transport import (
    PduJsonTransport,
    write_tcp_endpoint_config,
)
```

The adapter must define a closed local mapping such as:

```python
operations = {
    "PREPARE": prepare_local_recipe,
    "LAUNCH": launch_local_recipe,
    "RUN": run_local_recipe,
    "CLEANUP": cleanup_local_recipe,
    "ABORT": cleanup_local_recipe,
}
```

Do not add an operation that evaluates a received command line, executable
path, environment mapping, or arbitrary argument list. If a new operation is
needed, add an enumerated protocol value, schema validation, state-transition
tests, and a locally defined implementation.

## Troubleshooting

### `Library not loaded: @rpath/libhakoniwa_pdu_endpoint.dylib`

Run through `python3 tools/workspace.py run -- ...`. Direct execution from an
interactive macOS shell may not preserve the Foundation `DYLD_LIBRARY_PATH`.

### `TCP peer closed ... mapped_error=IO_ERROR` after success

The native Endpoint currently reports peer socket closure using this diagnostic
line. It may be printed immediately before the controller displays `CLEANED`,
because the callback has already queued that status while the worker closes its
socket. If the event log contains `CLEANED` and the final result is `success`,
it is the expected shutdown of the local client connection. Use both event logs
to distinguish this from an early disconnect.

### A Recipe phase fails

Inspect the matching files under `recipe-logs/`, then check the final entries
of both event logs. The worker attempts the fixed local `stop` operation in its
cleanup path even when a phase reports `FAILED`.
