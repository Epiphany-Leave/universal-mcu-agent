---
name: evidence-governance
description: Hardware evidence, debt, lifecycle, and assumption boundaries
category: [engineering, architecture]
platform: []
keywords: [evidence, debt, lifecycle, ADR, EAR, waiting]
related_workflows: [orchestrator, schematic-analysis, datasheet-extraction, firmware-development, evidence-resolution]
source: [platform-policy]
confidence: verified
version: "1.0.0"
---

# Evidence Governance

## Evidence classes

| Class | Meaning | Can establish Production hardware truth? |
|---|---|---|
| `HARDWARE_FACT` | Qualified schematic, BOM, assembly record, or direct inspection | Yes |
| `DEVICE_DOCUMENTATION` | Official document for the identified device/revision | Yes, for device capability |
| `OFFICIAL_BOARD_PROFILE` | Vendor configuration for a qualified board/revision | Configuration only |
| `OFFICIAL_BOARD_VALIDATION` | Vendor test or validation for a qualified board | Supporting evidence |
| `IMPLEMENTATION_REFERENCE` | Example or reference source code | No |
| `GENERATED_CONFIG` | `.ioc`, `sdkconfig`, generated code, or tool output | No |
| `UNTRUSTED_OR_MODIFIED` | Unknown, copied, AI-modified, or unqualified content | No |

Requirements describe intent. ADRs approve choices. Neither establishes what is physically populated or electrically connected. An ADR may depend on confirmed evidence IDs, but cannot close a physical evidence debt.

## Modes

- `production`: only confirmed admissible evidence may satisfy implementation gates. EARs and `WAIVED_PROTOTYPE_ONLY` debts are forbidden as gate satisfaction.
- `prototype`: bounded, approved EARs may permit reversible prototype work. The underlying fact remains unknown.
- `exploration`: disposable investigations may use labelled assumptions. Outputs are non-production.

Changing mode requires an explicit owner decision recorded in the phase manifest. A mode change never changes evidence confidence.

## Stop rules

- A work item can remain `BLOCKED_HARDWARE` while its assessment phase is `COMPLETED / PASS_WITH_DEBT`.
- `WAITING_EXTERNAL` terminates the current run. Record requested artifacts and exact resume criteria, then stop.
- Do not repeat a scan or create another child report when artifact IDs, revisions, and hashes are unchanged.
- A closed phase can resume only through `reopen` metadata with reason, authority, prior revision, and an incremented phase revision.
- Unknown values remain `UNKNOWN`; presentation defaults are not evidence.

