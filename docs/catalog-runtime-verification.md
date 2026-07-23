# Catalog Runtime Verification

Hakoniwa Business Pack should distinguish static catalog knowledge from executable evidence about whether a component or Recipe is ready in a concrete workspace.

The goal of Catalog Runtime Verification is to let Business Pack orchestrate component-owned diagnostic scripts without moving component-specific runtime knowledge into Business Pack itself.

## Responsibility boundary

```text
Hakoniwa Business Pack
  - selects components from Catalog / Recipe / category
  - locates the corresponding local repositories
  - invokes declared runtime checks
  - aggregates pass / fail / unknown results

Component repository
  - owns doctor.bash / doctor.ps1 / validators / smoke checks
  - knows its build options, native dependencies, generated assets, and runtime prerequisites
  - returns a non-zero exit status when the declared check fails
```

Business Pack must not duplicate checks such as whether a specific DLL was built with SHM support or whether a particular MJCF references missing meshes. Those checks belong in the repository that owns that runtime contract.

## Catalog contract

A component may declare optional `runtime_checks` entries:

```yaml
runtime_checks:
  - id: build-readiness
    kind: doctor
    platforms:
      - macos
      - linux
    command:
      - bash
      - doctor.bash
    working_directory: repository
    output: text
    notes:
      - Checks whether the local environment is ready to build and run the component.
```

A Windows repository can expose an equivalent PowerShell check:

```yaml
runtime_checks:
  - id: build-readiness
    kind: doctor
    platforms:
      - windows
    command:
      - powershell
      - -NoProfile
      - -ExecutionPolicy
      - Bypass
      - -File
      - doctor.ps1
    working_directory: repository
    output: text
```

The command is an argument array rather than a shell string so the Business Pack orchestrator does not need to reinterpret quoting.

Initial contract:

- exit status `0`: the declared check passed;
- non-zero exit status: the declared check failed;
- no check declared for the current platform: runtime readiness is `unknown`, not `pass`;
- `working_directory: repository`: run from the selected component repository root;
- `output: text`: stdout/stderr are diagnostic evidence for humans and agents;
- future checks may use `output: json` for a structured component-level result contract.

## Business Pack orchestrator

`tools/catalog_doctor.rb` selects catalog components and invokes their declared runtime checks.

Examples from a workspace where Hakoniwa repositories are siblings:

```bash
ruby tools/catalog_doctor.rb --component hakoniwa-mujoco-robots
ruby tools/catalog_doctor.rb --category physics-environment
ruby tools/catalog_doctor.rb --recipe mujoco-turtlebot3-mbody
ruby tools/catalog_doctor.rb --all
```

Use a different workspace root explicitly when needed:

```bash
ruby tools/catalog_doctor.rb \
  --recipe mujoco-turtlebot3-mbody \
  --workspace /path/to/hakoniwa-workspace
```

Useful modes:

```bash
# Show what would run without executing component scripts.
ruby tools/catalog_doctor.rb --category communication --dry-run

# Machine-readable aggregate output.
ruby tools/catalog_doctor.rb --recipe mujoco-turtlebot3-mbody --json

# Treat components without a declared platform check as a failure condition.
ruby tools/catalog_doctor.rb --all --strict
```

By default, an undeclared check is reported as `UNKNOWN` and does not make the command fail. A declared check that cannot run because its repository is missing, its command cannot be started, or the command exits non-zero is `FAIL`.

## Verification levels

Do not collapse all validation into one status.

```text
Catalog validation
  -> Is the structured knowledge internally valid?

Component runtime check
  -> Is this component locally ready for the declared purpose?

Recipe runtime check
  -> Are the component-level prerequisites for this Recipe locally ready?

Runtime smoke / behavioral validation
  -> Does the composed system actually execute and show the expected behavior?
```

Passing a component doctor is not proof that an entire Recipe is behaviorally verified. It is stronger preflight evidence that can prevent avoidable runtime failures before a Demo begins.

## Relationship to Knowledge Refinement

A reusable runtime failure should first be captured as a Knowledge Candidate with evidence. During maintainer reflection, ask whether the learned rule can be detected mechanically.

```text
Observation
  -> Knowledge Candidate
  -> Validation / Review
  -> Reflection
       |-- documentation knowledge
       |     -> Catalog / Primer / Guide / Recipe
       |
       `-- machine-detectable knowledge
             -> component-owned doctor / validator / test / CI guardrail
             -> Catalog runtime_checks declaration
             -> Business Pack runtime verification
```

The preferred maturation path is **detect before auto-fix**.

A low-risk guardrail should normally:

1. detect the missing or incompatible runtime prerequisite;
2. explain what failed;
3. provide a remediation command or reference when known;
4. return a useful exit status;
5. avoid silently installing software, downloading third-party assets, changing licenses, or modifying runtime architecture.

An agent operating with repository modification permission may turn a verified, machine-detectable Knowledge Candidate into a Draft PR against the owning component repository. The PR should add the smallest reproducible diagnostic/test guardrail and should preserve human gates for licensing, external downloads, credentials, physical actions, or consequential architecture decisions.

## Long-term direction

The intended feedback loop is:

```text
real user / AI execution
  -> failure or discovery
  -> Knowledge Candidate
  -> reflection
  -> executable guardrail PR
  -> component doctor becomes stronger
  -> Business Pack can verify more of the Catalog
  -> future Recipes fail earlier and more clearly
```

The mature state of a reusable runtime lesson is therefore not only "documented". When the rule is machine-detectable, the stronger state is "guarded": the software can detect the same condition before the next user has to rediscover it.
