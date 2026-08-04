# Universal-MCU-Agent Lifecycle Migration Notes

## v0.6 Runtime Migration

- v0.5 lifecycle JSON remains readable; v0.6 writes independent Intent, Work Package, Task, capability, and execution-ledger records.
- Existing phase `work_items` are projected to Work Packages without rewriting historical phase reports.
- Existing `artifact-manifest.json` remains readable by ID. New canonical writes use `.mcu-agent/artifact-manifest.yaml` schema v2 and require one CURRENT artifact per `scope + role`.
- Existing `02_Software/Projects/<name>` roots remain usable as legacy roots. The upgrade produces a relocation proposal but never moves them automatically.
- New projects use `hardware/` and `software/<project-name>/`; root-level `firmware/` and `drivers/` are forbidden.
- Step8 Hardware Enablement closures map to `COMPLETED / PASS_WITH_DEBT`. Evidence continuation becomes a bounded Work Package, not an endless child phase.
- Adapters call the shared runtime. Keyword routing in legacy adapter text is no longer authoritative.

## Compatibility

Version 0.5.0 adds lifecycle contracts without rewriting existing project reports. Existing workflow Markdown and knowledge files remain loadable.

## New Projects

1. Start with `project.mode: production` unless an owner explicitly selects another mode.
2. Create phase state, evidence debt registry, and artifact manifest at the paths configured under `lifecycle`.
3. Validate lifecycle JSON before claiming phase closure or waiting status.
4. Use stable debt IDs across phases.

## Existing Projects

1. Detect the latest closure report and active successor phase.
2. Propose, but do not silently create or backfill, a lifecycle manifest.
3. Treat a closed assessment with blocked work items as `COMPLETED / PASS_WITH_DEBT`.
4. Import only stable, non-duplicated debt identities. Historical UNKNOWN lists remain historical artifacts.
5. Default to Production mode. Prototype/Exploration requires explicit approval.

## State Mapping

| Legacy expression | Lifecycle mapping |
|---|---|
| Assessment PASS, some Drivers blocked | Phase `COMPLETED / PASS_WITH_DEBT`; work items `BLOCKED_HARDWARE` |
| Missing external document | Phase `WAITING_EXTERNAL / BLOCKED`; debt `WAITING_EXTERNAL` |
| Build/config repair exhausted | Phase result `FAILED` or `BLOCKED`, depending on whether input is external |
| Architecture conflict found | Work item `BLOCKED_ARCHITECTURE`; phase may close if its purpose was detection |
| UNKNOWN remains unresolved | Debt remains `OPEN` or `WAITING_EXTERNAL`; never defaulted |

## Evidence Migration

- Do not promote existing BSP/example/sdkconfig values during migration.
- Official board profiles must first pass provenance and applicability qualification.
- ADRs remain decisions and cannot be migrated as hardware evidence.
- EARs are never generated automatically from UNKNOWN values.

## Tooling Changes

- `mcu_generate.py` now resolves the Production directory from `.mcu-agent/config.yaml` and writes hardware/datasheet outputs under its `Hardware` child.
- Missing display values are rendered as `UNKNOWN`, not `LOW`, `12bit`, or another default.
- Parser failures are emitted as warnings rather than silently discarded.
- `mcu_parse.py` searches configured English directories first while retaining legacy Chinese-directory compatibility.

## Validation

```bash
python .mcu-agent/tools/validate_lifecycle.py self-test
python .mcu-agent/tools/validate_lifecycle.py validate phase <phase.json>
python .mcu-agent/tools/validate_lifecycle.py validate bundle <lifecycle-bundle.json>
```

Bundle validation is required when Production/Prototype mode, debt waiver, and EAR relationships must be checked together.
