# Hakoniwa Runtime Primer

[日本語](hakoniwa-runtime-primer-ja.md)

This document explains the runtime assumptions that an AI or human must
understand before writing Hakoniwa Recipes.

For the conceptual structure of the Hakoniwa ecosystem, including PDU, PDU
Registry, MBody Registry, Endpoint, Bridge Core, RPC, and time coordination,
see [箱庭ベースエコシステムガイド](hakoniwa-base-ecosystem-ja.md).

Catalog entries describe parts. Recipes describe system compositions. The
Ecosystem Guide explains where those parts fit. This primer focuses on the
runtime rules that make concrete compositions executable.

## Core Mental Model

Hakoniwa is not just a set of libraries. A running Hakoniwa system is a group of
assets that participate in a shared simulation runtime.

The usual local runtime model is:

```text
Hakoniwa core runtime
  - simulation lifecycle
  - simulation time
  - shared-memory PDU data space
  - asset coordination

Assets
  - simulator
  - controller
  - visualizer
  - bridge
  - service
  - external tool
```

A Recipe must identify which processes are Hakoniwa assets, which processes are
external clients, and how they exchange data.

## Core-Pro And Core-Cpp Roles

`hakoniwa-core-cpp` is the low-level simulation hub. It provides the shared
memory master data, PDU buffers, asset registration, simulation event control,
and world-time APIs.

`hakoniwa-core-pro` packages and extends that base for users. It provides
installed headers/libraries, `hakopy`, `hako-cmd`, conductor support, asset
APIs, data receive events, and PDU-based services.

`hakoniwa-pdu-python` is installed as the Python package `hakoniwa-pdu`. It
provides Python PDU utilities and the Hakoniwa launcher entrypoint:

```bash
python3.12 -m hakoniwa_pdu.apps.launcher.hako_launcher path/to/launch.json
```

In user-facing Recipes, treat them like this:

- `hakoniwa-core-pro`: the component users install and depend on.
- `hakoniwa-core-cpp`: the bundled core mechanism that explains how runtime
  coordination works.
- `hakoniwa-pdu-python` / `hakoniwa-pdu`: the Python package that provides PDU
  conversion/communication APIs and the launcher application.

Do not ask users to reason from `hakoniwa-core-cpp` internals unless the Recipe
is about core development, shared-memory debugging, or low-level asset APIs.

## Core Runtime Requirement

When a demo uses Hakoniwa PDU shared memory, the Hakoniwa core runtime must be
available. A controller or visualizer that imports `hakopy` or uses shared
memory PDU APIs is not standalone; it expects an initialized Hakoniwa runtime
and compatible PDU definitions.

Typical requirements include:

- installed Hakoniwa core libraries and tools;
- shared-memory access permissions;
- generated or packaged PDU definitions;
- the Python version selected by the runtime owner and a matching SOABI-tagged
  native binding when `hakopy` is involved;
- a simulation lifecycle owner, usually controlled through `hako-cmd` or a
  launcher.

Do not present a SHM/PDU demo as executable until the core runtime and PDU
configuration path are known.

On Linux/macOS, the common install prefix is `/usr/local/hakoniwa`. Installed
runtime artifacts can include:

- `hako-cmd` under `/usr/local/hakoniwa/bin`;
- libraries under `/usr/local/hakoniwa/lib`;
- C/C++ headers under `/usr/local/hakoniwa/include/hakoniwa`;
- PDU offset files under `/usr/local/hakoniwa/share/hakoniwa/offset`;
- core config under `/etc/hakoniwa/cpp_core_config.json`;
- mmap data under `/var/lib/hakoniwa/mmap`;
- `hakopy` on the import path controlled by the runtime owner.

`hakoniwa-core-pro` can build `hakopy` for a selected compatible Python. A
SOABI-enabled build installs the ABI-tagged module, for example
`hakopy.cpython-312-...so`, and records the selected implementation, exact
version, SOABI, extension suffix, and installed artifact in its resolved build
manifest and Receipt. The exact version is traceability metadata; runtime
compatibility is decided from the Python implementation and SOABI.

Business Pack Foundation narrows that general Core capability to **CPython
3.12**. It creates one shared environment under
`work/foundation/install/python`, builds Core with `python.soabi: true`, and
exposes the installed `hakopy` from
`work/foundation/install/share/hakoniwa/python` through the
Foundation-controlled import path. This directory is not a normal
`site-packages` installation.

`hakoniwa-pdu` appears through `pip install hakoniwa-pdu` from the
`hakoniwa-pdu-python` project. It is also installed into the active Python 3.12
environment.

If `hakopy` imports in one Python but `hakoniwa-pdu` is installed in another,
the demo is not ready. Record the intended Python command or environment in the
Recipe, and verify both imports in that same Python 3.12 interpreter. A useful
preflight is:

```bash
python3.12 -c "import hakopy; import hakoniwa_pdu"
python3.12 -m pip show hakoniwa-pdu
```

A `Successfully installed` message alone does not prove that the Foundation was
updated. The package may be in Blender's bundled Python, system Python, pyenv,
Homebrew, or another venv. For a Foundation-aware Recipe, use the interpreter
under `work/foundation/install/python` to verify `sys.executable`, `sys.prefix`,
the `Version` and `Location` from `pip show`, and smoke imports for the required
modules.

Python 3.12 is the Business Pack Foundation contract, not a condition that
should be rediscovered independently in each Recipe. Foundation `doctor`
checks the interpreter identity and compares its SOABI with the Core Receipt.
Foundation build must fail before modifying component workspaces when it is
invoked with Python 3.13/3.14 and the shared Foundation environment has not yet
been initialized. Component-owned launch wrappers should use the Foundation
interpreter and fail before starting assets when the required modules cannot be
imported.

Native binary dependencies follow the shared `schemas/native-runtime.yaml`
contract. Catalog profiles declare the distribution release, managed runtimes,
binary roles, and required libraries for each platform. Recipes select a profile
and active roles; they do not choose an OS inspection command. The common
validator delegates only binary dependency discovery and resolution to an ELF,
Mach-O, or future PE adapter, then reports declared and undeclared unresolved
libraries through one platform-independent result contract.

## Assets

An asset is a runtime participant managed by Hakoniwa conventions. It may be a
native simulator, a controller, a visualizer, a bridge, or a service.

Assets commonly have:

- an asset name;
- a process command;
- startup timing relative to simulation start;
- PDU channels they publish or subscribe;
- lifecycle behavior when simulation starts or stops;
- logs or observable output.

Not every useful process must be a strict Hakoniwa asset. Some tools can join
loosely as external clients by reading or writing PDU data, connecting through a
bridge, or calling a service API. Recipes must state this distinction.

The core examples show the basic pattern:

- `examples/hello_world`: one asset registers, waits for `hako-cmd start`, runs
  callbacks, then responds to `stop` and `reset`.
- `examples/pdu_communication`: two assets act as plant and controller, share
  PDU channels through a config file, and require a separate command process to
  start/stop/reset the simulation.
- `examples/service`: assets expose and call PDU-based request/response
  services.
- `examples/external/topic` and `examples/external/service`: Python processes
  can join through SHM topic or service APIs without being ordinary registered
  simulation assets.

## PDU Runtime Contracts

The conceptual meaning of PDU and the role of PDU Registry belong to the
[Ecosystem Guide](hakoniwa-base-ecosystem-ja.md). This primer only covers what a
Recipe must know at runtime.

A Recipe should identify:

- PDU name or channel;
- PDU type;
- producer;
- consumer;
- transport path, such as SHM, Endpoint, Bridge, WebSocket, or RPC;
- PDU definition/config file used by both sides.

If the PDU type, name, producer/consumer, or config path is unknown, the
connection is not fully specified.

Do not assume two components are compatible only because both mention "PDU".
They must agree on names, types, sizes, generated bindings, and runtime config.

## PDU Definition Files

Hakoniwa runtime PDU configuration usually has two different layers. Do not
collapse them into one concept.

```text
PDU schemas and generated bindings
  -> concrete PDU type layouts, sizes, offsets, converters

pdutypes.json
  -> the PDU channels available in one PDU type set

pdudef.json or pdu_def.json
  -> which PDU type set is assigned to which robot or asset name

runtime participants
  -> simulator, controller, bridge, visualizer, or external client using the
     same PDU definition/config
```

`pdutypes.json` defines the contents of a PDU type set. It usually lists entries
such as:

- `channel_id`: numeric channel inside that type set;
- `pdu_size`: binary payload size;
- `name`: semantic PDU name, such as `laser_scan` or `hako_cmd_game`;
- `type`: message type, such as `sensor_msgs/LaserScan`.

Example shape:

```json
[
  {
    "channel_id": 3,
    "pdu_size": 8192,
    "name": "laser_scan",
    "type": "sensor_msgs/LaserScan"
  }
]
```

`pdudef.json` or `pdu_def.json` assigns those PDU type sets to runtime names. In
the compact format, it commonly maps `paths[].id` to a `pdutypes.json` file,
then maps each `robots[].name` to one of those IDs:

```json
{
  "paths": [
    { "id": "tb3-endpoint", "path": "pdutypes.json" }
  ],
  "robots": [
    { "name": "TB3", "pdutypes_id": "tb3-endpoint" }
  ]
}
```

In short:

- `pdutypes`: what channels and binary layouts exist in a set;
- `pdudef`: where that set is placed in the system, by robot or asset name;
- generated bindings: language-specific structs/classes and converters for the
  PDU types;
- offset files: binary layout metadata used by converters and runtime tools;
- Endpoint/Bridge/viewer configs: runtime wiring that must refer to compatible
  PDU names, types, sizes, and assignments.

For existing demos, reuse the provided `pdutypes` and `pdudef` files unless the
Recipe explicitly changes the PDU space. For new systems:

1. define the PDU channels and types needed by the producers and consumers;
2. generate or select matching bindings, sizes, and offsets;
3. assign the resulting PDU type set to the robot or asset names used at
   runtime;
4. make every simulator, controller, bridge, visualizer, and external client use
   the same compatible definition files.

If an AI proposes a new asset, such as a weather, wind, sensor, or controller
asset, it must identify both the new PDU channels and where those channels live
in the runtime PDU definition. Otherwise the Recipe is only conceptual, not a
complete Hakoniwa composition.

### Semantic alignment of physics, viewer, and PDU configuration

Existing files, valid JSON, and a Launcher-schema pass do not prove that a
composition is executable. Configuration used by the simulator, native viewer,
controller, publisher, and Bridge must also agree semantically.

For a native MuJoCo viewer composition, verify at least:

- the physics configuration selected with the viewer option actually creates a
  MuJoCo model;
- `modelName`, body, joint, and propeller names exist in the loaded MJCF;
- simulator and controller use the same robot name;
- simulator, controller, visual-state publisher, and Bridge refer to compatible
  `pdudef` assignments;
- visual-state publisher robot counts, input assignments, and output settings
  match the actual fleet.

For example, combining `--mujoco-viewer` with a configuration whose
`physicsEquation` is `BodyFrame` can pass file checks but fail when viewer
initialization attempts to access a MuJoCo model that was never created. Recipe
doctors and tests should validate the selected physics, model, robot, and PDU
configuration as one contract rather than checking each file independently.

Details about PDU schema generation, language bindings, fixed-offset converters,
CDR converters, and Registry capabilities are intentionally kept in the
Ecosystem Guide and Catalog instead of this runtime primer.

## Simulation Time

Hakoniwa separates process startup from simulation time progression.

Starting a simulator process does not necessarily mean simulation time is
running. In many demos:

```text
1. start the process that owns Conductor, if the demo uses an embedded owner
2. start simulator-like assets and required controller assets
3. wait until required assets are registered and show WAIT START or equivalent
4. issue hako-cmd start
5. start after-start controllers, visualizers, or bridge clients
6. observe state changes
7. issue hako-cmd stop where supported, or let the launcher/process policy stop assets
```

`hako-cmd start` and `hako-cmd stop` control simulation lifecycle. They are not
the same as starting or killing OS processes.

For asset programs that embed Conductor startup, starting the executable is often
the step that creates the Hakoniwa runtime domain and registers the plant asset.
In that pattern, the correct manual sequence is:

```text
terminal 1: start the Conductor-owning plant asset program
            wait for asset registration, PDU channel creation, and WAIT START
terminal 2: start controller or sender assets that must join before time starts
            wait for their registration and WAIT START
terminal 3: hako-cmd start
```

Do not treat `hako-cmd start` as the first command in the demo. It is the
transition from the registered/waiting state into running simulation time. If a
controller is an ordinary external client rather than a registered asset, the
Recipe must say whether it starts before or after `hako-cmd start`.

Runtime validation must therefore check behavior after simulation start:

- simulation step/time advances;
- PDU values change;
- robot/drone pose changes;
- controller commands are consumed;
- sensors produce meaningful data.

Process startup alone is only partial evidence.

For callback assets, the user-visible signs are often:

```text
WAIT START
hako-cmd start
WAIT RUNNING
PDU DATA CREATED
on_initialize / on_simulation_step callbacks
hako-cmd stop
hako-cmd reset
on_reset callback
```

For polling or external clients, the signs may instead be successful SHM
initialization, service start, PDU publish/subscribe callbacks, or service
responses. Recipes should describe the expected signs for the chosen mode.

## Conductor

Conductor coordinates Hakoniwa simulation time and lifecycle behavior. In a
single composed simulation, treat Conductor as a singleton owner unless the
component documentation or Recipe explicitly says otherwise.

Important rule:

- one composed demo should normally have one Conductor owner;
- do not start multiple independent Conductor owners for the same SHM/runtime
  domain;
- in multi-process demos, choose which process owns Conductor startup;
- other simulator processes should run with Conductor startup disabled or in the
  documented non-owner mode.

This rule is especially important for multi-robot and mirror-body demos.

Some APIs expose explicit conductor calls, such as `conductor_start()` /
`conductor_stop()` or helper wrappers that start the conductor internally. That
does not change the singleton rule. A Recipe must still identify which process
is allowed to call those APIs.

For the distinction between Core PRO, Hakoniwa Conductor, and Conductor Light, see
the Ecosystem Guide. This primer focuses only on runtime ownership and startup
rules for the selected composition.

## Standard Process Roles

Use these roles when reasoning about startup order and data flow:

- `simulator`: owns physics or domain simulation and usually publishes state.
- `controller`: sends command PDUs or service calls.
- `visualizer`: displays state or sensor data and should not own physics.
- `bridge`: moves PDU data between transports, such as SHM to WebSocket.
- `service`: exposes command or RPC operations.
- `asset_generator`: produces runtime artifacts before the demo starts.
- `external_client`: joins through PDU, bridge, RPC, or API without being the
  main simulation runtime.

These roles are not interchangeable. For example, a browser viewer cannot
replace a physics simulator, and a bridge cannot prove that state is changing
unless an upstream producer is running.

## Registered Assets Vs External Clients

Hakoniwa supports both registered assets and looser external clients.

Registered assets typically:

- call an asset registration API;
- receive simulation lifecycle events;
- wait for start/stop/reset;
- participate in world-time progression;
- may create logical PDU channels from their asset config.

External clients typically:

- initialize against an existing SHM/PDU service config;
- read or write PDU data with APIs such as `read_pdu_for_external`,
  `write_pdu_for_external`, SHM topic publisher/subscriber, or SHM service
  client/server helpers;
- may not receive ordinary asset lifecycle callbacks;
- require some runtime owner to have initialized the shared-memory domain.

Use external clients for tools, monitors, quick controllers, or service clients
that should not own physics or core lifecycle. Do not use external mode as a
shortcut to avoid specifying the real simulator, PDU config, or conductor owner.

## Launcher

Hakoniwa demos should prefer a launcher when multiple processes must start in a
specific order. A launcher configuration is a runtime artifact.

The commonly used Hakoniwa launcher is provided by the `hakoniwa-pdu` Python
package, from the `hakoniwa-pdu-python` repository. It is invoked with:

```bash
python3.12 -m hakoniwa_pdu.apps.launcher.hako_launcher path/to/launch.json
```

`hakoniwa-pdu` 1.6.5 and later provide a caller-owned session-file lifecycle for
AI and scripts that manage a long-running Launcher:

```bash
python3.12 -m hakoniwa_pdu.apps.launcher.hako_launcher path/to/launch.json \
  --background work/recipes/<recipe-id>/runtime/launcher-session.json

python3.12 -m hakoniwa_pdu.apps.launcher.hako_launcher_ctl \
  status work/recipes/<recipe-id>/runtime/launcher-session.json

python3.12 -m hakoniwa_pdu.apps.launcher.hako_launcher_ctl \
  terminate work/recipes/<recipe-id>/runtime/launcher-session.json
```

The caller-facing contract is the session file, not a PID. `terminate` does not
directly kill the recorded PID; it invokes the Launcher's normal
`LauncherService.terminate()` path. A Recipe that depends on this interface
should require both `version.min: 1.6.5` and
`launcher_background_lifecycle: true`.

The `hakoniwa-pdu` launcher model uses:

- `assets[]` with `name`, `command`, `args`, `cwd`, `stdout`, `stderr`, `env`,
  `activation_timing`, `depends_on`, `delay_sec`, and `start_grace_sec`;
- `defaults` for common cwd, logs, env operations, delays, and grace periods;
- environment merge operations such as `set`, `prepend`, `append`, and `unset`;
- `immediate` mode, which runs `activate -> hako-cmd start -> watch`;
- `serve` mode, which accepts commands such as `activate`, `start`, `stop`,
  `reset`, `terminate`, and `status`;
- watch behavior that aborts all assets if one asset exits unexpectedly.

The launcher is normally a long-running lifecycle manager, not a setup command
that returns after startup. In `immediate` mode it may remain in the foreground
after `hako-cmd start` because it is watching the assets it started. Treat that
as normal runtime behavior. If an AI or script needs to run later commands, it
must either keep the launcher session alive in the background, use a separate
terminal/session for controllers, or use `serve` mode where appropriate.

Do not conclude that a launcher-based demo failed only because the launcher did
not exit. Also do not conclude that an asset failed only because its log file is
empty. Some assets, such as `python -m http.server`, may write no stdout until a
browser request arrives. Prefer active readiness checks:

- launcher output reaches `hako-cmd start exited with 0`;
- the expected asset is still running;
- the expected socket responds, such as `curl -I http://127.0.0.1:8000/`;
- the expected bridge endpoint accepts a browser or client connection;
- downstream PDU, service, or visual-state logs show changing data.

### Launcher lifecycle state and Demo readiness

Distinguish a background Launcher session state from a Demo that is ready for
the operator:

```text
Launcher session RUNNING
  = the control endpoint responds and the Launcher lifecycle is still active

Demo Ready
  = the Recipe-required assets, simulation, ports, Bridge, Viewer, and PDU data
    flow are usable
```

An exit code of `0` from `hako_launcher_ctl status` alone proves neither that
the JSON `state` is `RUNNING` nor that the Demo is ready. Parse the response
state and then run the Recipe-specific active readiness checks. The Launcher
main process may remain alive even when managed assets have exited and the
expected HTTP server or Bridge port is absent.

When a background `start` command returns after readiness checks, its operator
handoff must state:

- that the Demo continues running in the background after command return;
- which GUI, behavior, ports, or data flow the operator should confirm;
- the next command that opens the viewer;
- the session status command;
- the normal stop command;
- the session file and log locations.

A fixed `delay_sec` may be a temporary ordering measure for a slow model load,
but it is not readiness evidence. Prefer conditions tied directly to the
contract, such as asset registration, `WAIT START` / `WAIT RUNNING`, HTTP
responses, socket listeners, Bridge connectivity, or PDU updates.

Launcher timing is expressed through `activation_timing`:

```text
before_start assets
  - simulator
  - runtime services that must initialize PDU data

hako-cmd start
  - simulation time starts

after_start assets
  - controllers
  - visualizers
  - bridge clients
  - scripted demo drivers
```

Use `depends_on` for asset ordering constraints, and `delay_sec` /
`start_grace_sec` for practical startup timing. Do not invent launcher fields
from another generation of the tooling; check the installed `hakoniwa-pdu`
version and schema if the Recipe depends on launcher behavior.

Some launchers generate concrete launch files as intermediate artifacts. Do not
edit generated launch files during a demo run unless the Recipe explicitly says
that is the intended customization point. Regeneration can discard the edit. If
a command needs to change, find the source script, environment variable, or
Recipe parameter that controls generation, and record the change as a new
issue or Recipe update before treating it as a fix.

### Launcher termination and cleanup contract

`hako-cmd stop` and Launcher termination are different operations. `hako-cmd
stop` changes Hakoniwa simulation lifecycle state; it does not mean that the
Launcher-owned OS processes have been terminated.

For the current `hakoniwa-pdu` Launcher, interactive `Ctrl+C` sends `SIGINT` to
the Launcher main process. The Launcher installs a `SIGINT` handler that calls
its normal termination path, which invokes `LauncherService.terminate()` and
`monitor.abort()` to stop the assets owned by that Launcher.

On POSIX, Launcher-managed assets are started in their own process
sessions/groups. Therefore, killing the Launcher's process group is not a safe
replacement for the Launcher's own termination path.

Use this normal shutdown order:

```text
interactive launcher terminal available
  -> send Ctrl+C to that terminal/session

explicit background session file available
  -> hako_launcher_ctl terminate <session-file>
  -> wait for the normal Launcher path to persist state TERMINATED

only the launcher main PID is available on POSIX
  -> send SIGINT to that launcher PID
     kill -INT <launcher-pid>

after launcher termination
  -> confirm launcher reports state -> TERMINATED
  -> confirm managed asset processes are gone
  -> confirm Recipe-owned ports/listeners are released
  -> inspect logs for incomplete cleanup
```

Do not substitute these as the normal cleanup path:

```bash
kill <launcher-pid>       # default POSIX signal is SIGTERM, not Ctrl+C/SIGINT
kill -- -<PGID>           # may target the wrong process group and is too broad
kill -9 <launcher-pid>    # bypasses the Launcher's cleanup handler
```

Treat `SIGTERM`, process-group termination, and `SIGKILL` as escalation paths,
not as equivalent forms of `Ctrl+C`. Before escalation, verify process ownership
and avoid broad cleanup that can terminate unrelated Python, browser, or other
Recipe processes.

Do not generalize the POSIX `kill -INT` example to Windows. Use the
console/Launcher termination mechanism documented for the provider and target
environment. The cross-platform contract is: invoke the Launcher's supported
normal termination path, then verify that its owned assets and ports are gone.

A Launcher-based executable Recipe should record a structured `demo.cleanup`
contract. At minimum it should identify:

- Launcher owner/provider and mode when known;
- cleanup target;
- normal interactive action and signal;
- expected behavior;
- post-exit process/port/log verification;
- cautions and fallback boundaries.

When writing a Recipe, record:

- launcher file path;
- launcher provider and invocation command;
- asset names;
- asset commands;
- startup timing;
- environment variables;
- log files;
- expected termination behavior;
- cleanup behavior if interrupted.

Do not replace a launcher-based Recipe with a list of ad hoc terminal commands
unless the launcher does not exist or the Recipe is deliberately documenting a
manual runbook.

## External And Loose Integration

Some useful participants are not strict Hakoniwa assets. They can still be part
of a Recipe if their integration boundary is explicit.

Examples:

- a Python script that reads/writes SHM PDU through `hakopy`;
- a browser viewer connected through WebSocket bridge;
- a Node.js monitor connected through `hakoniwa-pdu-javascript`;
- an RPC client that calls a Hakoniwa service;
- a plotting tool that subscribes to sensor PDU streams.

For external participants, state:

- how they connect;
- whether they require the core runtime to already exist;
- whether they can start before or only after simulation start;
- what happens if the simulator or bridge is missing.

External integration is valid, but it does not remove the need for a real
producer, runtime, PDU config, and observable validation.

## Recipe Startup Checklist

Before writing executable steps, answer these questions:

- Which component owns the core simulation runtime?
- Which process starts or owns Conductor?
- Which assets must exist before `hako-cmd start`?
- Which executable performs asset registration, and what log line proves it is
  waiting for start?
- Which assets start after simulation time begins?
- Which processes are external clients rather than assets?
- Which PDU definitions/configs are shared across participants?
- Which PDU producers and consumers prove the data flow?
- Which bridge, endpoint, or RPC path is used?
- What observable evidence proves behavior, not just startup?
- How are all long-running processes stopped?

If these cannot be answered, mark the Recipe as `unknown`, `partially_feasible`,
or `blocked` instead of writing runnable-looking commands.

## Common AI Failure Modes

Avoid these mistakes:

- treating a repository README command as a full Hakoniwa Recipe;
- searching source text for a keyword and claiming executable feasibility;
- starting a simulator without identifying the PDU config;
- issuing `hako-cmd start` before required assets have registered;
- starting a controller before simulation time exists;
- starting multiple Conductor owners in one composed demo;
- treating `hako-cmd stop` as process cleanup;
- replacing `Ctrl+C`/`SIGINT` with default `kill <PID>` when the Recipe depends on the Launcher SIGINT cleanup path;
- broadly killing a launcher process group or unrelated Python/browser processes;
- leaving bridge, viewer, HTTP server, or simulator processes running;
- inventing component IDs that do not exist in the catalog;
- calling a browser viewer a simulator;
- claiming "PDU-compatible" without naming the PDU contract;
- confusing `pdutypes` with `pdudef`;
- ignoring runtime compatibility between PDU names, types, sizes, and assignments.
