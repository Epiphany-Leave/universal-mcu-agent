---
name: firmware-development
description: Implement runtime-selected firmware tasks
version: "2.0.0"
status: stable
category: [firmware, embedded]
mcu_support: [stm32, esp32, ch592, ti-c2000]
requires: [work-package, ready-task]
outputs: [source-code, task-validation]
contract_version: "2.0"
priority: 80
---

# Workflow Handler: Firmware Development

## Entry Contract

- A v2 Work Package exists.
- The selected Task state is `READY` or `IMPLEMENTING`.
- Only the Task's declared capability facts are gates.
- There is no global `hardware.json` or `datasheet.json` gate.

## Execution

1. Resolve only the Task's artifact roles through `.mcu-agent/artifact-manifest.yaml`.
2. Transition Task `READY -> IMPLEMENTING` through the runtime.
3. Load the platform contract for the selected firmware project root.
4. Implement only the selected Task and its declared dependencies.
5. Run the Task's verification tier: `host`, `build`, or `target`.
6. Transition to `VALIDATING`, then `DONE`; on missing facts, transition only this Task to the matching `BLOCKED_*` state.
7. Register newly discovered unrelated unknowns as debt without switching away from the current Work Package.

For Espressif Tasks, `prepare-task` may expose an optional `esp_dev_skill` section. Read the listed subskill, recipe, and resource files in that order. These are knowledge inputs only; they cannot authorize GPIO assumptions, change `allowed_roots`, or bypass manifest and platform-contract verification.

## Development Modes

- `production`: EAR cannot satisfy a target/release gate. Hardware-independent source and host tests may still be implemented.
- `prototype`: approved task-scoped EAR may unlock reversible mock or prototype work.
- `exploration`: disposable experiments are allowed and remain non-production.

## ESP-IDF

After build, run:

```bash
python .mcu-agent/runtime/cli.py validate-esp-idf <software/project-name> --build-succeeded --expected-component <component-name>
```

Build success is not sufficient when an expected component has `included: false`.
