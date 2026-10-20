# Universal MCU Agent Runtime Entry

All MCU work uses the installed CLI lifecycle. Host instructions and Skills are guidance; project contract, workflow policy, claim scope, promotion and CI are authoritative.

From the product root, bootstrap each session and retain `session_id`:

```bash
python .mcu-agent/runtime/cli.py session-bootstrap --agent auto --product-root .
```

Plan every exact user request:

```bash
python .mcu-agent/runtime/cli.py plan "<user request>" --product-root . --session-id <session-id>
```

An uninitialized product can return only `PROJECT_SETUP`. A BLOCKED Task must not be claimed. Register explicit inputs only through:

```bash
python .mcu-agent/runtime/cli.py artifact register . \
  --role <canonical-role> --path <product-relative-path> --scope <scope>
```

Claim a READY Task in an isolated worktree and retain the opaque `claim_id`:

```bash
python .mcu-agent/runtime/cli.py claim-task . <work-package-id> <task-id> \
  --session-id <session-id> --workspace
```

Use only the returned workspace, allowed roots, Artifact IDs/hashes and handler path/digest. After real verification:

```bash
python .mcu-agent/runtime/cli.py finalize-task . <work-package-id> <task-id> \
  <result.json> --claim-id <claim-id>
```

A DONE workspace is not canonical until promotion:

```bash
python .mcu-agent/runtime/cli.py promote-task . <work-package-id> <task-id> \
  --claim-id <claim-id>
```

Use the returned commit trailers. If work cannot complete, use `abandon-task`; target evidence must use `attest-target` and cannot be fabricated. Never edit `.mcu-agent/state`, contract, policy lock, manifest or audit files directly.
