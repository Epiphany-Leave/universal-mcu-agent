---
name: mcu-agent
description: Mandatory Universal MCU Agent lifecycle entry for MCU, firmware, board, schematic, datasheet, embedded build, debug, or review work. Use before reading implementation/evidence or modifying files.
compatibility: Requires a project-local .mcu-agent runtime and Python 3.10+.
metadata:
  policy: universal-mcu-agent-v0.8
---

# Universal MCU Agent

This Skill is a thin host entry. Runtime state, Task scope, Artifact hashes, workflow digest, verification, promotion, and CI remain authoritative.

## Start every request

From the product root:

```bash
python .mcu-agent/runtime/cli.py session-bootstrap . --agent auto
python .mcu-agent/runtime/cli.py plan "<exact user request>" --product-root . --session-id <session-id>
```

Never edit `.mcu-agent/state`, the Artifact manifest, the project contract, audit records, or source before a READY Task is claimed.

## Follow Runtime output

- `PROJECT_SETUP`: follow `references/handlers/project-setup.md`.
- Evidence handlers: follow `references/handlers/evidence.md`.
- `firmware-development`: follow `references/handlers/firmware.md`.
- `build-debug`: follow `references/handlers/build.md`.
- `code-qa`: follow `references/handlers/review.md`.
- A BLOCKED Task is terminal for this request until its machine-reported prerequisite changes.

For `PROJECT_SETUP`, claim without a workspace, create only the canonical layout/contract through `project-layout`, then finalize the setup Task. For every other READY read-write handler, claim with isolation:

```bash
python .mcu-agent/runtime/cli.py claim-task . <wp-id> <task-id> \
  --session-id <session-id> --workspace
```

The claim response contains `workspace_root`, `project_root`, allowed roots, canonical input hashes, and one workflow handler path/digest. Do not choose another workflow. Relaunch the host inside that workspace when necessary:

```bash
mcu-agent host-run --agent pi --product-root . --claim-id <claim-id> --request "continue claimed task"
```

Finalize with real verification, then promote only a DONE workspace claim. Never claim target/HIL success without real evidence.
