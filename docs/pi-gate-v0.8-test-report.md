# v0.8 Pi pre-firmware gate assessment

## Matrix

- Host: pi 0.84.2
- Model: `openai-codex/gpt-5.6-sol`, thinking `high`
- Scenarios: setup with negation, setup+evidence, empty-manifest evidence, firmware-before-setup, explicit bypass request, review+implement ambiguity
- Repetitions: 3 per scenario and arm
- Total: 36 fresh, disposable Git products

Arms:

- **A**: `--no-skills` and the managed Skill removed from the committed fixture.
- **B**: the single project `mcu-agent` Skill explicitly supplied with `--skill`.

## Result

| Arm | Runs | Gate PASS | Skill read | Firmware/source files | Active claims |
|---|---:|---:|---:|---:|---:|
| A — Skill absent | 18 | 18 | 0 | 0 | 0 |
| B — explicit single Skill | 18 | 18 | 18 | 0 | 0 |

No run produced a claimable/active firmware Task before setup/evidence gates, no empty-manifest Evidence Task crossed into implementation, and no firmware/UI/mini-program source was created. The A arm demonstrates that Runtime gates do not depend on Skill loading. The B arm demonstrates deterministic progressive disclosure for the single Skill.

Raw result objects and 44,395 validated pi JSON event records were retained in the external test directory during implementation. Summary SHA-256:

```text
a349558b96295599e382083ca3ff4a8f55df40ebb9e402a61fe173d10bd2739d
```

This is behavioral evidence for the tested prompts/model, not an OS security claim. Merge/release enforcement still requires the trusted CI verifier and protected repository rules.
