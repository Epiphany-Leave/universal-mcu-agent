# Universal MCU Agent Runtime

This project uses the shared Universal MCU Agent runtime. Before replying to or editing any MCU request, run:

```bash
python .mcu-agent/runtime/cli.py plan "<user request>" --mode production --state-root .
```

Read `next_tasks` and execute the returned Task handler. Follow `.mcu-agent/agent-entry.md` for scope, verification, and finalization rules. Do not invoke a separate MCU skill or route around the runtime.
