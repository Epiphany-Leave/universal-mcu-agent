# Universal MCU Agent v0.6 Runtime Entry

All MCU requests must enter the shared runtime before a workflow handler is selected:

At the start of a Codex session, run:

```bash
python .mcu-agent/runtime/cli.py session-bootstrap --agent codex --mode production .
```

```bash
python .mcu-agent/runtime/cli.py plan "<user request>" --mode <production|prototype|exploration>
```

Execute the returned READY tasks in user-request priority order. Use `.mcu-agent/workflows/` only as task handlers. Never let a handler replace the current Work Package, edit lifecycle state directly, or scan for artifacts.

For a coding Task, prepare the model context with `runtime/cli.py prepare-task`, make the edits inside the returned `allowed_roots`, then submit the model result with `runtime/cli.py finalize-task`. A Task is not `DONE` until build/test verification is recorded.

Resolve inputs through `.mcu-agent/artifact-manifest.yaml` with the Artifact Resolver. Apply state changes through the runtime transition engine. A `BLOCKED_*` task does not block unrelated READY tasks. `WAITING_EXTERNAL` remains scoped to the affected task or evidence-resolution Work Package.

For ESP-IDF builds, run the platform contract and require expected components to have `included: true`.

When an `esp-dev-skill` checkout is available, pass it to `prepare-task --esp-skill-root <path>` (or place it at `.mcu-agent/skills/esp-dev-skill`). The skill supplies read-only recipes and API references; it does not widen Task scope or change lifecycle state.
