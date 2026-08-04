# Universal MCU Agent Runtime

All MCU requests enter the project's `.mcu-agent` runtime before any implementation or review work:

```bash
python .mcu-agent/runtime/cli.py plan "<user request>" --mode <production|prototype|exploration> --state-root .
```

Use the returned Work Package and READY Tasks as authoritative. Do not route directly to a platform skill or workflow. For implementation Tasks:

1. Run `prepare-task` and use only its `allowed_roots`.
2. Let the model edit code inside that scope.
3. Run the declared host/build/target verification.
4. Run `finalize-task` and record the verification result.

Hardware UNKNOWN blocks only Tasks that declare the missing capability. Keep unrelated READY Tasks moving. Resolve external inputs through the canonical artifact manifest. Use the platform contract for ESP-IDF component inclusion; a successful build alone is insufficient.

For ESP work, `.mcu-agent/skills/esp-dev-skill` is an optional read-only knowledge source. It may provide recipes and API references, but it cannot widen Task scope, change lifecycle state, or bypass evidence and platform validation.
