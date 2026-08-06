# Universal MCU Agent Runtime

## Mandatory Dispatch Contract

At the start of each host-agent session, acknowledge this entry and run the session handshake once:

```bash
python .mcu-agent/runtime/cli.py session-bootstrap --agent <claude-code|codex|opencode|cursor> --mode <production|prototype|exploration> .
```

The handshake records the active entry file and applies to the rest of the session. It does not replace the per-request `plan` command below.

For **every** request involving an MCU project, firmware, hardware evidence, build, debug, UI, driver, or embedded software, you **MUST run the runtime plan command before replying, asking clarification, selecting a workflow, or editing a file**. Treat this as an execution requirement, not optional guidance.

Run it from the project root:

```bash
python .mcu-agent/runtime/cli.py plan "<user request>" --mode <production|prototype|exploration> --state-root .
```

If `.mcu-agent/runtime/cli.py` is missing, report that the project is not installed and do not perform MCU code changes. Do not ask the user to name a workflow to compensate for a missing runtime.

Read the JSON result. Use its `work_packages[0].tasks`, `next_tasks`, and `blocked_tasks` as the dispatch result. The Task `handler` identifies the workflow handler. Do not route directly by keyword, and do not claim that a workflow was selected until the command has run successfully.

Before executing each READY Task, load the handler definition from `.mcu-agent/registry/workflows.yaml` and read the referenced file under `.mcu-agent/workflows/`. For example, a Task with `handler: firmware-development` must load `.mcu-agent/workflows/firmware.md` before `prepare-task`. The workflow file is the execution contract; it is not optional background reading.

For implementation Tasks:

1. Run `prepare-task` and use only its `allowed_roots`.
2. Let the model edit code inside that scope.
3. Run the declared host/build/target verification.
4. Run `finalize-task` and record the verification result.

When the user asks for implementation, continue with READY Tasks in the returned user-priority order in the same turn. Do not stop after generating a plan unless a Task is blocked or external input is required.

Hardware UNKNOWN blocks only Tasks that declare the missing capability. Keep unrelated READY Tasks moving. Resolve external inputs through the canonical artifact manifest. Use the platform contract for ESP-IDF component inclusion; a successful build alone is insufficient.

For ESP work, `.mcu-agent/skills/esp-dev-skill` is an optional read-only knowledge source. It may provide recipes and API references, but it cannot widen Task scope, change lifecycle state, or bypass evidence and platform validation.
