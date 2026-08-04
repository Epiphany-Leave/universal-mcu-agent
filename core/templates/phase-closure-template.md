# Phase Closure v2: <phase-id> r<revision>

- Mode: `<production|prototype|exploration>`
- Status: `<COMPLETED|WAITING_EXTERNAL|CANCELLED>`
- Result: `<PASS|PASS_WITH_DEBT|BLOCKED|FAILED>`
- Completion criteria: <met/not met with artifact IDs>
- Work Packages: <stable IDs and aggregate states>
- READY Tasks handed to scheduler: <Task IDs>
- Blocked Tasks: <Task ID, capability facts, debt IDs>
- Review rounds and input fingerprints: <ledger references>
- Consumed/produced artifacts: <canonical manifest IDs>
- Requested external inputs: <only for scoped waiting>

Phase closure does not require every Task to be DONE. After `COMPLETED`, open debts and blocked Tasks remain independently resumable; do not create child Step reports.
