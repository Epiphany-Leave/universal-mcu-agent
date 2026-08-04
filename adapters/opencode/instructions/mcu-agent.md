# Universal MCU Agent v0.6 Runtime Entry

Run the shared runtime before selecting a handler:

`python .mcu-agent/runtime/cli.py plan "<user request>" --mode <mode>`

Execute returned READY tasks in user priority order. Prepare coding context with `runtime/cli.py prepare-task`, edit only its `allowed_roots`, and finalize with `runtime/cli.py finalize-task` after verification. For ESP work, pass `--esp-skill-root <path>` when an `esp-dev-skill` checkout is available; use its references as read-only guidance. Treat `.mcu-agent/workflows/` as task handlers only. Artifacts must be resolved through `.mcu-agent/artifact-manifest.yaml`, state changes must use the runtime, and blocked tasks must not stop unrelated READY work. Validate ESP-IDF component inclusion after builds.
