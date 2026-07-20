# Hakoniwa Runtime Primer

This document explains the runtime assumptions that an AI or human must
understand before writing Hakoniwa Recipes.

Catalog entries describe parts. Recipes describe system compositions. This
primer describes the runtime rules that make those compositions valid.

## Core Mental Model

Hakoniwa is not just a set of libraries. A running Hakoniwa system is a group of
assets that participate in a shared simulation runtime.

The usual runtime model is:

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
python -m hakoniwa_pdu.apps.launcher.hako_launcher path/to/launch.json
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
- Python 3.12 for Hakoniwa Python workflows unless a Recipe explicitly says
  otherwise;
- matching Python 3.12/native runtime environments when `hakopy` is involved;
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
- `hakopy` in the Python environment used by the demo.

`hakopy` appears through the `hakoniwa-core-pro` install. It is installed into a
Python `site-packages` location, so the Python 3.12 interpreter used by a Recipe
must be the same environment where that `hakopy` module is importable.

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

## PDU

PDU means Protocol Data Unit. In Hakoniwa Recipes, a PDU is the typed data
contract exchanged between components.

Examples:

- robot pose;
- wheel or game controller commands;
- LiDAR scans;
- drone position and attitude;
- camera or sensor state;
- service request/response payloads.

A PDU contract is more than a topic name. A Recipe should identify:

- PDU name or channel;
- PDU type;
- producer;
- consumer;
- transport path, such as SHM, endpoint, bridge, WebSocket, or RPC;
- PDU definition/config file used by both sides.

If the PDU type, name, or config path is unknown, the connection is not fully
specified.

## PDU Definition Files

Hakoniwa PDU configuration usually has two different layers. Do not collapse
them into one concept.

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

`pdutypes.json` defines the contents of a PDU type set. It usually lists
entries such as:

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

`pdudef.json` or `pdu_def.json` assigns those PDU type sets to runtime names.
In the compact format, it commonly maps `paths[].id` to a `pdutypes.json` file,
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
- endpoint/bridge/viewer configs: runtime wiring that must refer to compatible
  PDU names, types, sizes, and assignments.

For existing demos, reuse the provided `pdutypes` and `pdudef` files unless the
Recipe explicitly changes the PDU space. For new systems, design both layers:

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

## PDU Registry

`hakoniwa-pdu-registry` is the source of PDU schemas and generated bindings for
Hakoniwa components. It is the data-model authority for many Recipes.

Use it when a Recipe needs to answer:

- what PDU types exist;
- which generated language bindings are needed;
- whether Python, JavaScript, Ruby, Elixir, C++, Godot, or Foxglove can read the data;
- which compact PDU definition or endpoint configuration should be shared by
  simulator, controller, bridge, and visualizer.

Do not assume two components are compatible only because both mention "PDU".
They must agree on names, types, sizes, generated bindings, and config files.

## Generated Bindings And Converters

Generated language support has multiple layers. Do not treat "Ruby support",
"Elixir support", "JavaScript support", or any other binding label as one
binary yes/no capability.

Separate these concerns:

- type definitions: generated structs/classes/modules representing the PDU
  fields in a target language;
- fixed-offset binary converters: `pdu_to_*` and `*_to_pdu` style converters
  that read and write the Hakoniwa shared-memory PDU binary layout using known
  offsets;
- offset metadata: generated layout data that tells converters where fields and
  heap-backed variable arrays live;
- CDR runtime and converters: a separate wire-format path for DDS/CDR-style
  serialization;
- size registries: generated lookup tables for fixed PDU size or CDR minimum
  size;
- interop tests: cross-language checks that prove generated data can be decoded
  by another known-good implementation.

A fixed-offset converter implementation is not CDR support. CDR support needs
its own runtime, converter templates, size handling, and interop tests. Likewise,
a generated type file is not enough to claim that a language can participate in
PDU communication.

When adding a new language binding, use staged validation:

1. generate type definitions for simple scalar and nested struct messages;
2. generate fixed-offset converters from offset files;
3. test syntax or compilation in the target language;
4. test same-language round trips for fixed fields, arrays, and variable arrays;
5. test interop against an existing oracle such as C++ or Python;
6. include nested variable arrays and empty variable arrays in the test set;
7. treat CDR as a later independent milestone unless it is implemented and
   verified explicitly.

For Recipe work, this matters because a component may need only fixed-offset
SHM PDU conversion, while a bridge, DDS adapter, or external wire protocol may
need CDR. Record which layer is required and which layer is verified.

## Simulation Time

Hakoniwa separates process startup from simulation time progression.

Starting a simulator process does not necessarily mean simulation time is
running. In many demos:

```text
1. start simulator-like assets
2. initialize shared memory and PDU data
3. issue hako-cmd start
4. start controllers, visualizers, or bridge clients
5. observe state changes
6. issue hako-cmd stop or let the launcher stop assets
```

`hako-cmd start` and `hako-cmd stop` control simulation lifecycle. They are not
the same as starting or killing OS processes.

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
python -m hakoniwa_pdu.apps.launcher.hako_launcher path/to/launch.json
```

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
- starting a controller before simulation time exists;
- starting multiple Conductor owners in one composed demo;
- treating `hako-cmd stop` as process cleanup;
- leaving bridge, viewer, HTTP server, or simulator processes running;
- inventing component IDs that do not exist in the catalog;
- calling a browser viewer a simulator;
- claiming "PDU-compatible" without naming the PDU contract.
- claiming a language binding is complete when only type generation exists;
- claiming CDR support from fixed-offset converter generation or CDR size file
  regeneration;
- skipping cross-language oracle tests for generated converters;
- ignoring nested variable arrays or empty variable arrays when validating
  generated PDU converters.