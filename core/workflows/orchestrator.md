---
name: orchestrator
description: Runtime-backed intent and work-package entry point
version: "3.0.0"
status: stable
category: [system, routing]
mcu_support: [stm32, esp32, ch592, ti-c2000]
requires: [runtime]
outputs: [intent-record, work-package, gated-tasks]
contract_version: "3.0"
priority: 0
---

# Workflow Handler: Orchestrator

The runtime owns intent, lifecycle, and scheduling. This handler must not route directly from keywords to a workflow.

## Entry

```bash
python .mcu-agent/runtime/cli.py plan "<user request>" --mode <mode> --state-root <product-root> [--platform <platform>] [--capabilities <capabilities.json>]
```

## Contract

1. Persist the returned Intent Record and Work Package before implementation.
2. Execute `next_tasks` in user-request priority order.
3. A blocked task creates or references a stable debt; it does not replace the current user request.
4. `expansion: register_only` forbids starting unrelated assessment work.
5. Workflow Markdown is a task handler. It cannot change phase or work-package state directly.
6. State changes go through `runtime.transition_engine`; artifacts come through `runtime.artifact_resolver`.
7. `WAITING_EXTERNAL` is scoped to the blocked task or evidence-resolution Work Package unless no other requested task is READY.

## Outcome

Return READY tasks, blocked tasks with exact capability facts, and the next executable action. Do not create a new Step report merely because a debt remains open.
