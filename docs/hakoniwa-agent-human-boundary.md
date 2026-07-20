# Hakoniwa Agent / Human Boundary

## 1. Purpose

Hakoniwa Business Pack is intended to help an AI agent move from a user goal to a credible Hakoniwa system composition, Recipe, and minimal Demo.

However, technical feasibility and agent autonomy are different questions.

A composition may be technically feasible while still requiring a human to make a judgement, perform a physical action, provide credentials, approve a side effect, or accept responsibility for a real-world consequence.

Therefore, agents must evaluate two dimensions separately:

- **Can this be done with the current Hakoniwa ecosystem?**
- **Can the agent proceed autonomously, or is human involvement required?**

In short:

```text
feasible != agent_can_execute
verified != safe_to_execute_without_human
```

This document defines the default boundary between agent work and human involvement.

## 2. Boundary Classes

Use the following four classes when reasoning about a Recipe or Demo.

### A. Agent can execute autonomously

The agent may proceed without an additional human decision when the action is fully inside a controlled software environment and the user has already requested execution.

Typical examples:

- read Catalog, ecosystem guides, Runtime Primer, and Recipes;
- shortlist Hakoniwa components;
- propose system topology;
- generate or edit Recipe YAML;
- generate PDU definitions and declarative JSON configuration;
- generate source code, launchers, adapters, or test scripts;
- build components in an explicitly approved local or CI environment;
- start and stop simulation-only processes after execution has been requested;
- inspect logs, PDU values, generated artifacts, screenshots, and test results;
- retry or modify simulation-only configuration within the agreed scope;
- run automated validation that has no real-world side effect.

Autonomous execution still requires the normal Business Pack rules for environment checks, cleanup, evidence, and validation status.

### B. Agent can propose, but human decision is required

The agent may analyze options and recommend a choice, but should not make the final decision when the decision depends on user intent, domain judgement, business responsibility, or acceptance criteria.

Typical examples:

- deciding whether a simulation result is physically valid enough for the user's purpose;
- selecting acceptable model fidelity;
- defining experiment conditions or evaluation metrics when multiple legitimate choices exist;
- deciding whether a sensor, failure, disturbance, or environmental model is representative of reality;
- choosing between commercial and non-commercial licensing paths;
- deciding whether a private or commercial dependency is acceptable;
- approving a design that changes project scope, cost, schedule, or deliverables;
- accepting a partially verified composition as sufficient for production or customer delivery.

The agent should present the alternatives, evidence, uncertainty, and consequences, then stop at an explicit decision gate.

### C. Human action is required

The agent may provide instructions, checks, and expected observations, but a person must perform the physical or externally situated action.

Typical examples:

- connect or disconnect a USB device, joystick, sensor, robot, vehicle, or flight controller;
- wire hardware;
- power physical equipment on or off;
- place obstacles, targets, markers, or test fixtures in the real environment;
- calibrate real sensors when physical manipulation is needed;
- measure real-world values;
- inspect a physical device or environment;
- press a hardware safety button;
- move equipment to a test area;
- perform an onsite procedure.

A Recipe should model this as an explicit human gate. Later automated steps must not assume the action was completed until the human confirms it or reliable evidence proves it.

### D. External permission, credential, or explicit approval is required

The agent must stop until the required authority or access is provided.

Typical examples:

- access to a private repository;
- commercial license entitlement;
- API keys, cloud credentials, VPN access, or organization authentication;
- permission to install software on a managed machine;
- permission to modify an external repository or production system;
- permission to publish, deploy, purchase, or incur cost;
- explicit approval before connecting simulation output to a real actuator or device;
- approval required by organizational, legal, safety, or operational rules.

The agent should identify exactly what permission is missing and why it blocks the next step.

## 3. Real-World and Safety-Critical Boundary

A simulation-only action and a real-world action must not be treated as equivalent.

For example:

```text
Generate motor command in simulation
    -> may be autonomous

Send motor command to real hardware
    -> explicit human approval required
```

The same applies to:

- drones;
- robots;
- vehicles;
- industrial equipment;
- real actuators;
- operational networks;
- production systems.

An agent may prepare configuration and test the same flow in simulation, but must not silently cross from simulation to real-world actuation.

A Recipe that can operate in both simulation and real hardware modes must make the boundary explicit.

## 4. Recommended Recipe Representation

Recipes may record the boundary using an `agency_boundary` section.

Example:

```yaml
agency_boundary:
  agent_actions:
    - id: generate_configs
      description: Generate PDU, Endpoint, and Bridge configuration.
      autonomous: true

    - id: run_simulation
      description: Launch and validate the simulation-only composition.
      autonomous: true
      preconditions:
        - user_requested_execution

  human_decisions:
    - id: approve_model_fidelity
      description: Decide whether the selected physics and sensor models are sufficient.
      required_before:
        - customer_acceptance

  human_actions:
    - id: connect_flight_controller
      description: Physically connect the target flight controller over USB.
      confirmation_required: true
      required_before:
        - hardware_in_the_loop_test

  required_permissions:
    - id: commercial_license
      description: Confirm entitlement to the required commercial component.
      status: required
      required_before:
        - commercial_component_execution
```

This section is initially descriptive rather than a strict validator requirement, so existing Recipes do not become invalid immediately.

New executable Recipes should add it when human involvement, real hardware, external permissions, or consequential decisions exist.

## 5. Decision Rule for Agents

Before executing each non-trivial step, ask:

1. Is this step technically feasible with known components and artifacts?
2. Is this step simulation-only or does it affect the real world?
3. Has the user requested execution, not only explanation or design?
4. Does this step require a human judgement?
5. Does this step require a physical human action?
6. Does this step require external permission, credentials, license rights, or cost approval?
7. Is there an explicit success signal and cleanup path?

If the answer to 4, 5, or 6 is yes, create a gate instead of pretending the Recipe is fully autonomous.

## 6. Reporting the Boundary

When presenting a Recipe-shaped answer, an agent should be able to say clearly:

```text
Agent can do:
- generate the system composition
- create configuration
- run the simulation
- inspect validation evidence

Human decision required:
- approve whether the model fidelity is sufficient

Human action required:
- connect the physical device

Permission required:
- confirm access to the commercial component
```

This is part of the system design, not an operational footnote.

## 7. Relationship to Feasibility and Validation

The Business Pack should keep these concepts separate:

- **Capability**: what a component can do.
- **Feasibility**: whether the components can form a credible system.
- **Validation**: what has actually been tested or verified.
- **Agency Boundary**: which steps the agent may perform and where a human or external authority is required.

A strong Recipe makes all four visible.
