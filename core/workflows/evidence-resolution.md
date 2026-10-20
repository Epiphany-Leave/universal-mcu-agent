---
name: evidence-resolution
description: Resolve selected evidence debts under a bounded review budget
version: "3.0.0"
status: stable
category: [hardware, evidence, lifecycle]
mcu_support: [stm32, esp32, ch592, ti-c2000]
requires: [selected-debt-ids, artifact-manifest.yaml]
outputs: [capability-updates, review-conclusion]
contract_version: "3.0"
priority: 85
review_budget: {max_rounds: 2, exhausted_action: CLOSE_WITH_DEBT}
---

# Workflow Handler: Evidence Resolution

## Entry

The caller supplies selected stable debt IDs and manifest artifact IDs. This handler never scans the project tree.

Before review, apply the runtime budget:

```bash
python .mcu-agent/runtime/cli.py review <phase-id> <artifact-identities.json> --state-root <product-root> --max-rounds 2
```

## Actions

- `RETURN_EXISTING`: return the stored conclusion and `No new evidence detected`; create no report.
- `CLOSE_WITH_DEBT`: close the assessment as `COMPLETED / PASS_WITH_DEBT`; create no child Step.
- `REVIEW_ALLOWED`: resolve selected artifact IDs from the canonical manifest and review only selected debts.

After an allowed review, call the same command with `--conclusion <conclusion.json>` to persist the conclusion against that input fingerprint. When capability facts change, re-run `runtime/cli.py gate` on existing Work Packages; do not create replacement packages or reopen unrelated phases.

## Output Contract

Update capability facts and derive their task impacts. An UNKNOWN may block dependent binding/target-validation tasks while allowing mock, host, architecture, state-machine, or portable-driver tasks.

Hardware Evidence Resolution ends when selected facts are classified and impacts are computed. It does not wait for every hardware fact to become confirmed.
