# Actor Discovery Map

This document is a maintainer-facing discovery map for finding representative
Hakoniwa Usecases from the people who may benefit from them.

It is deliberately not a list of Usecases and must not be converted mechanically
into one Usecase per Actor, Catalog component, or Recipe. Its purpose is to help a
maintainer ask:

> Who would recognize this situation as their own problem, and what outcome would
> make their work better?

Use the Actor as the starting point, then inspect combinations of validated Recipes
and Catalog capabilities for evidence.

```text
Actor
  -> situation and recurring difficulty
  -> desired user value
  -> Recipe clusters and Catalog composition
  -> existing Usecase search
  -> Usecase Fragment or canonical Usecase candidate
```

## How To Use This Map

1. Select an Actor whose work is represented by the current Catalog or Recipes.
2. Read the situation and difficulties from that Actor's point of view.
3. Inspect all related Recipes as a cluster, rather than translating one Recipe into
   one Usecase.
4. State the desired outcome without Hakoniwa repository names.
5. Search existing Usecases for the same outcome.
6. Record a Fragment or candidate only when multiple capabilities form meaningful
   user value that is not already obvious from one Catalog entry.
7. Keep feasibility, validation evidence, limitations, and missing pieces separate.

The identifiers marked **existing** are already allowed by
[`schema.yaml`](schema.yaml). Identifiers marked **candidate** are discovery terms;
they must be reviewed and added to the controlled vocabulary before a canonical
Usecase uses them.

## Actor Inventory

### Robot control developer

- Vocabulary: `robot-control-developer`, `control-software-developer` (**existing**)
- Self-identifying situation: I have control logic, commands, or sensor-driven
  behavior that I want to exercise before using physical hardware.
- Recurring difficulties:
  - hardware availability and physical-operation constraints slow early iteration;
  - command, model, and feedback mismatches are found too late;
  - it is difficult to isolate control defects from hardware and environment effects.
- Desired user value:
  - execute the control path against observable simulated motion and sensor feedback;
  - find software and data-contract problems before physical-system validation;
  - repeat the same behavior under controlled conditions.
- Evidence clusters:
  - `mujoco-turtlebot3-mbody`
  - `mujoco-turtlebot3-wall-follower`
  - `drone-single-mujoco-threejs-gamepad`
  - `drone-single-mujoco-shibuya-map-gamepad`
- Existing Usecase coverage: `robot-control-prevalidation`

### ROS 2 application developer

- Vocabulary: `ros2-application-developer` (**existing**)
- Self-identifying situation: I want to keep using ROS 2 messages, Services, Actions,
  tools, and containerized nodes while connecting them to a Hakoniwa system.
- Recurring difficulties:
  - the ROS 2 runtime and GUI/physics runtime may need to live in different
    environments;
  - transport direction, generated bindings, and lifecycle ownership are easy to
    confuse;
  - validating both client and server roles requires multiple repositories and
    processes.
- Desired user value:
  - integrate existing ROS 2 applications without exposing PDU/RPC implementation
    details to application code;
  - use a repeatable Host + Docker topology for Service and Action validation;
  - choose either the ROS side or Hakoniwa side as client/server according to the
    application architecture.
- Evidence clusters:
  - `ros2-service-add-two-ints-host-docker`
  - `ros2-service-add-two-ints-client-host-docker`
  - `ros2-action-fibonacci-server-host-docker`
  - `ros2-action-fibonacci-client-host-docker`
- Related Catalog composition:
  - `hakoniwa-pdu-ros`
  - `hakoniwa-pdu-rpc`
  - `hakoniwa-pdu-endpoint`
  - `hakoniwa-pdu-registry`
- Existing Usecase coverage: `ros2-application-integration`

### Web visualization developer

- Vocabulary: `web-visualization-developer` (**existing**)
- Self-identifying situation: I build browser-facing visualization or interaction,
  but I do not want the browser to own robot physics or simulation time.
- Recurring difficulties:
  - native simulator state is not directly usable by browser applications;
  - binary data contracts, WebSocket transport, and viewer configuration span
    different technology stacks;
  - a process being alive does not prove that live simulation data reaches the UI.
- Desired user value:
  - consume simulation state through an explicit WebSocket/PDU boundary;
  - develop or replace a browser UI independently from the physics runtime;
  - verify connection and visible state changes through a reproducible composition.
- Evidence clusters:
  - `drone-single-mujoco-threejs-mac`
  - `drone-single-mujoco-threejs-gamepad`
  - `drone-single-mujoco-shibuya-map-gamepad`
  - `shadow-hand-hakoniwa-to-foxglove`
- Existing Usecase coverage: `browser-based-simulation-visualization`

### Robot model and simulation integrator

- Vocabulary: `robot-system-integrator` (**existing**)
- Self-identifying situation: I have URDF, Xacro, MJCF, meshes, or robot-specific
  settings and need to turn them into a runnable, controllable simulation asset.
- Recurring difficulties:
  - source models, generated artifacts, actuator settings, and PDU contracts drift;
  - visualization success alone does not prove that commands and state feedback work;
  - robot-specific fixes are easily mixed into reusable runtime infrastructure.
- Desired user value:
  - materialize source assets into a traceable Hakoniwa/MuJoCo composition;
  - validate command input, motion, and state output before broader integration;
  - keep reusable generation contracts separate from robot-specific decisions.
- Evidence clusters:
  - `agilex-tracer-urdf-to-mujoco`
  - `unitree-go1-menagerie-mjcf-to-hakoniwa`
  - `shadow-hand-menagerie-mjcf-to-hakoniwa`
  - `mujoco-turtlebot3-mbody`
- Related Catalog composition:
  - `hakoniwa-mbody-registry`
  - `hakoniwa-mujoco-robots`
  - `hakoniwa-pdu-registry`
  - `hakoniwa-pdu-endpoint`
- Existing Usecase coverage: `robot-model-simulation-onboarding`

### Robotics researcher

- Vocabulary: `robotics-researcher` (**candidate**)
- Self-identifying situation: I want repeatable experiments that combine robot
  behavior, environments, disturbances, and observable results.
- Recurring difficulties:
  - experiment conditions and runtime setup are difficult to reproduce;
  - changing visualization, models, or controllers can change unrelated parts of the
    environment;
  - a demo result may be mistaken for evidence that applies to every target system.
- Desired user value:
  - compose interchangeable simulation, control, environment, and observation
    capabilities under explicit contracts;
  - repeat a representative experiment and retain its evidence boundary;
  - identify which assumptions require human review or additional validation.
- Evidence clusters:
  - TurtleBot3 control and visualization variants
  - Drone Three.js and city-model variants
  - robot-body materialization Recipes
- Related Catalog opportunities:
  - combine `hakoniwa-envsim` with validated robot/drone Recipes when a concrete
    experiment and validation evidence become available.

### Distributed simulation developer

- Vocabulary: `distributed-simulation-developer` (**existing**)
- Self-identifying situation: I need simulation participants on more than one process
  or node to advance under a controlled common-time model.
- Recurring difficulties:
  - process startup alone does not prove time synchronization;
  - single-node and multi-node conductor choices have different contracts;
  - generated configuration and runtime ownership are difficult to reproduce by hand.
- Desired user value:
  - validate synchronized advancement with small observable assets before integrating
    a larger distributed system;
  - select the appropriate conductor topology from the number of participating nodes;
  - reuse generated configuration and packaged binaries without exposing private
    implementation sources.
- Evidence clusters:
  - `hakoniwa-conductor-python-time-sync`
  - `mujoco-turtlebot3-dual-mirror`
  - `hakoniwa-conductor-v1-0-0-binary-package` as supporting setup evidence
- Related Catalog composition:
  - `hakoniwa-conductor`
  - `hakoniwa-conductor-light`
  - `hakoniwa-pdu-endpoint`
  - `hakoniwa-pdu-rpc`
- Existing Usecase coverage: `coordinated-multi-participant-simulation`

### Manufacturing engineering team

- Vocabulary: `manufacturing-engineering-team` (**existing**)
- Self-identifying situation: We want to understand robot-arm command, motion, and
  state integration before committing to physical-system integration work.
- Recurring difficulties:
  - robot-specific models and software contracts must be aligned;
  - physical-system access, safety gates, and commercial assets limit early testing;
  - simulation evidence can be overgeneralized into physical performance claims.
- Desired user value:
  - expose integration gaps in a software-only reference path;
  - separate technical feasibility from physical-system approval;
  - make commercial/private access and model-fidelity decisions explicit.
- Evidence clusters:
  - the `hakoniwa-robot-arm-pack` reference integration
  - Shadow Hand model/control and Foxglove visualization Recipes as narrower public
    evidence for command/state composition
- Existing Usecase coverage: `industrial-robot-arm-preintegration`

### Demonstration owner and technical sales

- Vocabulary: `demonstration-owner`, `technical-sales` (**existing**)
- Self-identifying situation: I need another person to reproduce, operate, observe,
  and safely stop a multi-process demonstration.
- Recurring difficulties:
  - setup and runtime instructions are mixed together;
  - background startup appears successful before the observable demo is ready;
  - cleanup, GUI interaction, licensing, and human actions are easy to omit.
- Desired user value:
  - use a generated guide as a common handoff point;
  - distinguish Foundation setup from Recipe-specific execution;
  - show readiness, viewer access, validation, logs, and cleanup explicitly.
- Evidence clusters:
  - browser-based Drone Recipes
  - Shadow Hand Foxglove Recipe
  - TurtleBot3 Godot and mirror Recipes
- Existing Usecase coverage: partially covered by
  `browser-based-simulation-visualization`; broader repeatable-demo operation remains
  a candidate.

### Robotics educator or learner

- Vocabulary: `robotics-educator`, `robotics-learner` (**candidates**)
- Self-identifying situation: I want learners to change robot behavior through a
  familiar visual or scripting environment without first learning native simulation
  internals.
- Recurring difficulties:
  - installation and low-level middleware concepts create a high entry barrier;
  - an educational UI must still connect to observable robot behavior;
  - Catalog capability alone does not prove a complete teaching workflow.
- Desired user value:
  - interact with a simulated robot through an approachable environment;
  - observe the effect of commands while the simulation runtime remains separated;
  - reuse a documented exercise with explicit setup and cleanup.
- Related Catalog opportunity:
  - `hakoniwa-scratch`
  - `hakoniwa-pdu-javascript`
  - `hakoniwa-pdu-bridge-core`
- Evidence boundary: no current Recipe cluster validates a complete educational
  workflow, so this remains an unmet or partially evidenced demand candidate.

## From Actors To Usecases

Do not create a canonical Usecase merely because an Actor entry exists. Prefer a
small number of outcomes that cut across several Recipes or connect several Catalog
capabilities.

Good candidate shape:

```text
Actor:
  ROS 2 application developer

Situation:
  ROS 2 nodes run in a container while simulation runs on the host

Problem:
  Service and Action roles, generated contracts, and TCP lifecycle span runtimes

Desired outcome:
  Validate existing ROS 2 client/server behavior against Hakoniwa without embedding
  the simulator into the ROS 2 environment

Evidence:
  four Host + Docker Service/Action Recipes and their Catalog composition
```

Weak candidate shape:

```text
Actor:
  developer

Desired outcome:
  use hakoniwa-pdu-rpc
```

The weak form only restates a component. It does not identify a reusable problem or
user value.

## Review Questions

- Can the Actor recognize the situation without knowing Hakoniwa repository names?
- Is the Actor the beneficiary, the operator, or a supporting participant?
- Does the desired outcome improve a decision or workflow rather than merely expose a
  feature?
- Does the evidence combine multiple capabilities or Recipes into meaningful value?
- Is an existing Usecase already broad enough to cover the outcome?
- Which claims are verified, partially verified, unknown, or blocked?
- Which candidate Actor identifiers deserve promotion into `schema.yaml`?
