# Universal-MCU-Agent Architecture Correction Report

## 1. Review Scope

This review covers the Universal-MCU-Agent implementation under `.mcu-agent/`, its workflow and knowledge registries, workflow definitions, templates, parsing/generation tools, Cyber-Tamagotchi generated artifacts, all Step 8.x reports, hardware evidence reports, ADRs, closure documents, and evidence-resolution phase documents.

Project artifacts were read as behavioral evidence only. This correction is implemented in Universal-MCU-Agent and must not rewrite generated project artifacts.

## 2. Executive Result

**NEEDS_ARCHITECTURE_CORRECTION**

The evidence safety policy is directionally correct. Step 8.3 correctly refused to invent REC, LCD, and audio facts. The failure is lifecycle architecture: Universal-MCU-Agent has workflows but no persistent phase model, terminal outcome contract, external waiting state, evidence-debt lifecycle, artifact dependency graph, or execution mode.

Consequently, a valid `BLOCKED_HARDWARE` work-item result was repeatedly interpreted as an unfinished phase. Each follow-up report restated the same blocker with greater detail, but the platform had no machine-readable way to declare “assessment complete, implementation blocked, waiting for external evidence.”

The correction must preserve evidence gates while allowing phases to terminate and engineering to continue safely in unaffected scopes.

## 3. Why Step 8.3 Expanded Indefinitely

Step 8.3 produced correct reports for Button, LCD, and Audio. The expansion occurred because:

1. `firmware.md` defines linear execution steps but no phase completion semantics.
2. `orchestrator.md` routes by keywords and checks file presence only; it does not load a current phase state.
3. `BLOCKED_HARDWARE` is used both as a component-readiness result and an implicit workflow status.
4. Workflow outputs are symbolic names such as `firmware_log`, not enforceable contracts.
5. No debt registry exists, so each report re-embeds the complete UNKNOWN list.
6. No `WAITING_EXTERNAL` terminal state exists, so missing documents appear to require another internal audit.
7. No closure rule stops creation of more `Step 8.3.x` artifacts after a closure artifact.
8. ADRs and evidence debts are Markdown-only and have no dependency/status validation.
9. The Agent lacks a controlled prototype path, so the only expressible choices are unsafe guessing or indefinite production blocking.
10. The Agent has no artifact manifest to detect that later reports depend on stale evidence or superseded reports.

The Step 8.3 closure report manually supplied the missing platform behavior. That manual correction proves the needed lifecycle model.

## 4. Required Architecture Findings

### 4.1 Phase lifecycle management

**Finding: flawed.** A workflow invocation is treated as a sequence of tasks rather than a stateful phase instance. There are no entry criteria, completion criteria, terminal outcome, closure artifact, or successor phase fields.

Correction: introduce a phase state machine and require a phase manifest for multi-step work.

### 4.2 `BLOCKED_HARDWARE` semantics

**Finding: incorrectly overloaded.** It correctly describes an implementation gate, but it was treated as evidence that the assessment phase was incomplete.

Correction: separate:

- phase lifecycle status: `PLANNED`, `ACTIVE`, `WAITING_EXTERNAL`, `COMPLETED`, `CANCELLED`;
- phase result: `PASS`, `PASS_WITH_DEBT`, `BLOCKED`, `FAILED`;
- work-item readiness: `READY`, `BLOCKED_HARDWARE`, `BLOCKED_CONFIG`, `BLOCKED_ARCHITECTURE`, `DISABLED`.

A phase may be `COMPLETED` with result `PASS_WITH_DEBT` while components remain `BLOCKED_HARDWARE`.

### 4.3 Evidence debt lifecycle

**Finding: required and missing.** Evidence gaps were repeatedly copied between reports. They need stable IDs, state, source requirements, dependency links, and closure records.

Correction: add an evidence-debt schema with states `OPEN`, `EVIDENCE_SUBMITTED`, `UNDER_REVIEW`, `WAITING_EXTERNAL`, `CONFIRMED`, `CONFLICT`, `REJECTED`, `WAIVED_PROTOTYPE_ONLY`, and `CLOSED`.

`WAIVED_PROTOTYPE_ONLY` never changes an evidence fact to confirmed and cannot satisfy a Production gate.

### 4.4 ADR/evidence boundary

**Finding: conceptually correct in project ADRs, not encoded by the Agent.** The project manually states that ADRs cannot replace physical evidence, but no platform rule validates this.

Correction:

- evidence records answer “what is physically/documentarily true”;
- ADRs answer “which design choice is approved given confirmed facts”;
- an ADR may depend on evidence IDs;
- an ADR cannot close a physical evidence debt;
- decisions containing implementation parameters must cite confirmed evidence or an approved prototype-only EAR.

### 4.5 Prototype engineering assumptions

**Finding: should be supported with strict isolation.** Without an assumption mechanism, teams either stall or misuse BSP/example values as facts.

Correction: add Engineering Assumption Records (EARs) for Prototype and Exploration modes only. EARs require scope, source, rationale, risks, validation plan, owner, expiry, and rollback. They never update `hardware.json`/`datasheet.json` as confirmed facts and cannot authorize Production release.

### 4.6 External evidence waiting

**Finding: missing.** `NEED_EXTERNAL_INPUT` emerged in project reports but has no workflow state.

Correction: add `WAITING_EXTERNAL` as a terminal-for-current-run phase status. It must record requested artifacts, debt IDs, responsible party/role if known, and resume criteria. The Agent must stop internal re-auditing until new artifact identity or revision is supplied.

### 4.7 Artifact dependency tracking

**Finding: required.** Reports depend on hardware JSON, datasheet JSON, requirements, ADRs, BSP profiles, and earlier phase reports, but dependencies are prose only.

Correction: add an artifact manifest with stable ID, path, type, revision/hash, producer, dependencies, supersedes links, evidence class, and lifecycle status. A stale dependency must prevent a report from claiming current validation.

### 4.8 Phase output contracts

**Finding: missing.** Workflow front matter lists output labels without required fields, paths, or validation.

Correction: every phase/workflow must define:

- entry criteria;
- allowed mutations;
- forbidden mutations;
- required artifacts and schema versions;
- terminal statuses/results;
- closure criteria;
- resume criteria;
- successor workflow/phase;
- validation commands.

### 4.9 Production / Prototype / Exploration modes

**Finding: required.** A single safety posture cannot distinguish production truth from bounded experimentation.

Correction:

| Mode | Evidence policy | EAR | Implementation |
|---|---|---|---|
| `production` | Confirmed evidence only | Forbidden as gate satisfaction | Only evidence-backed configuration |
| `prototype` | Confirmed evidence preferred | Allowed with explicit approval, scope, expiry, validation and rollback | Bounded prototype path only |
| `exploration` | Unknowns may be investigated but remain unknown | Allowed and clearly labelled | Disposable/non-production experiment only |

Mode changes require an explicit user/owner decision and are recorded in the phase manifest. Modes never change evidence confidence by themselves.

## 5. Additional Platform Defects Found Since Initial Project Setup

### 5.1 Path convention drift

`config.yaml` uses English production paths, while `mcu_generate.py` writes to `05_生产资料` and `mcu_parse.py docs` searches Chinese-only project directories. Workflow documents also alternate between `05_Production/hardware.json` and `05_Production/Hardware/hardware.json`.

Impact: artifacts may be silently generated in the wrong tree, bypassing later workflow checks.

Correction: tools must resolve paths from `.mcu-agent/config.yaml`, with English defaults matching the standard project template.

### 5.2 Unsafe generated defaults

`mcu_generate.py` renders missing GPIO default state as `LOW` and missing ADC resolution as `12bit`. These are presentation defaults without evidence.

Impact: UNKNOWN can be displayed as a fact.

Correction: render missing values as `UNKNOWN`; structured output must include source and confidence.

### 5.3 Silent parser failure

Broad bare `except` blocks suppress parse errors.

Impact: incomplete evidence can look like a clean negative result.

Correction: emit structured warnings/errors with source path; never silently discard an evidence source.

### 5.4 Reference example ambiguity

`schematic.md` instructs the Agent to extract pin configuration from reference code without classifying provenance. `datasheet.md` uses examples to validate documentation without specifying that examples cannot override it.

Impact: generic or AI-modified examples may be promoted to hardware truth; official board BSP profiles cannot be represented distinctly.

Correction: introduce evidence classes: `HARDWARE_FACT`, `DEVICE_DOCUMENTATION`, `OFFICIAL_BOARD_PROFILE`, `OFFICIAL_BOARD_VALIDATION`, `IMPLEMENTATION_REFERENCE`, `GENERATED_CONFIG`, and `UNTRUSTED_OR_MODIFIED`. Only the first two directly establish physical/device facts. Board profiles require board/revision/provenance qualification.

### 5.5 Registry and template contract weakness

Workflow/skill registries are path indexes only. Custom workflow templates have no lifecycle or schema declarations.

Correction: add lifecycle metadata and output-contract references to registry entries and templates.

### 5.6 No machine validation

There is no validator for lifecycle states, evidence debts, artifacts, or forbidden Production EAR usage.

Correction: add a standard-library validator and documented validation command.

## 6. Target State Machine

```text
PLANNED
   |
   v
ACTIVE -------------------------+
   |                             |
   | missing external artifact   | recoverable internal prerequisite
   v                             |
WAITING_EXTERNAL --new input-----+
   |
   | closure contract satisfied
   v
COMPLETED

ACTIVE --explicit cancellation--> CANCELLED
```

Rules:

1. `WAITING_EXTERNAL` ends the current execution turn and forbids repeated internal scans with the same artifact set.
2. `COMPLETED` is compatible with `PASS_WITH_DEBT` and blocked work items.
3. `FAILED` is a phase result for execution failure, not missing external evidence.
4. Closed phases cannot create child step reports unless explicitly reopened with reason, authority, and a new phase revision.
5. A successor phase consumes the closure artifact and selected open debt IDs; it does not copy/recreate all debts.

## 7. Concrete Modification Plan

### Files to add

- `schemas/phase-state.schema.json`
- `schemas/evidence-debt.schema.json`
- `schemas/artifact-manifest.schema.json`
- `schemas/engineering-assumption.schema.json`
- `workflows/evidence-resolution.md`
- `knowledge/common/evidence-governance.md`
- `templates/phase-manifest-template.yaml`
- `templates/evidence-debt-template.yaml`
- `templates/engineering-assumption-record-template.md`
- `templates/phase-closure-template.md`
- `tools/validate_lifecycle.py`

### Files to modify

- `workflows/orchestrator.md`: load phase state before routing; honor closure and waiting states.
- `workflows/firmware.md`: add execution modes, hardware gate outcomes, phase contracts, and stop rules.
- `workflows/schematic.md`: classify reference sources; prohibit code-derived physical facts.
- `workflows/datasheet.md`: enforce device-document and example boundaries.
- `workflows/build.md`: distinguish build failure from evidence/config blocking.
- `registry/workflows.yaml`: register evidence resolution and contract metadata.
- `registry/skills.yaml`: add evidence lifecycle triggers.
- `registry/knowledge.yaml`: register evidence governance knowledge.
- `config/dispatch-rules.yaml`: route evidence/debt/ADR/waiting tasks.
- `templates/custom-workflow-template.md`: require lifecycle/output contracts.
- `knowledge/schema.md`: extend provenance and evidence confidence vocabulary.
- `workflows/CHANGELOG.md` and `.mcu-agent/VERSION`: document compatibility change.
- `tools/mcu_generate.py` and `tools/mcu_parse.py`: remove unsafe defaults, use configured paths, and expose parse warnings.

## 8. Schema Changes

The schemas must require stable IDs and enumerated states. They must reject:

- `COMPLETED` without a closure artifact;
- `WAITING_EXTERNAL` without resume criteria and requested inputs;
- `CONFIRMED` debt without source references and acceptance record;
- Production phase dependencies on `WAIVED_PROTOTYPE_ONLY` debt;
- Production use of an EAR as hardware truth;
- artifacts with missing path/type/status;
- dependency cycles within a submitted manifest set.

## 9. Prompt and Template Changes

Every phase prompt/report must explicitly answer:

1. What phase and revision is active?
2. What mode is active?
3. What are entry and exit criteria?
4. Which artifacts were consumed and produced?
5. Which debts changed state?
6. Is the phase completed, waiting externally, or cancelled?
7. Which work items remain blocked independent of phase completion?
8. What exact event permits resumption?

The closure template must prohibit “continue auditing” as a default next action after `WAITING_EXTERNAL` or `COMPLETED`.

## 10. Validation Rules

1. UNKNOWN remains UNKNOWN until an admissible source is reviewed and accepted.
2. Requirements cannot establish physical capability or population.
3. `sdkconfig`, examples, BSP defaults, and generated code cannot override confirmed hardware facts.
4. Qualified official board profiles are configuration evidence, not unnamed part identity.
5. ADRs cannot close physical evidence debt.
6. EARs are mode-scoped, expiring, reversible, and never Production facts.
7. Re-running a waiting phase with unchanged inputs is invalid; report the existing wait state instead.
8. A closed phase requires explicit reopen metadata before new child reports.
9. Artifact hashes/revisions must be updated when a dependency materially changes.
10. Work-item `BLOCKED_*` states do not prevent phase closure when assessment exit criteria are satisfied.

## 11. Migration Strategy

Existing projects remain readable. If no phase manifest exists, the Agent runs in legacy detection mode:

1. Discover closure reports and latest phase artifacts.
2. Generate a proposed manifest without rewriting existing reports.
3. Default mode to `production`.
4. Import open UNKNOWNs as `OPEN` debts only when stable identity can be established; do not duplicate report-local lists.
5. Require user/owner confirmation before enabling Prototype or Exploration mode.

Cyber-Tamagotchi Step 8.3 should migrate as `COMPLETED / PASS_WITH_DEBT`; its Button/LCD/Audio work items remain `BLOCKED_HARDWARE`. Step 8.4 is the successor evidence-resolution phase.

## 12. Expected New Behavior Example

```text
Phase: hardware-enablement-assessment
Status: COMPLETED
Result: PASS_WITH_DEBT
Mode: production
Closure: step8.3-closure-report.md

Work items:
  button_driver: BLOCKED_HARDWARE (BTN-REC-01)
  lcd_driver: BLOCKED_HARDWARE (LCD-ID-01)
  audio_driver: BLOCKED_HARDWARE (AUD-CODEC-01)

Successor: evidence-resolution
```

If no new documents exist:

```text
Phase: evidence-resolution
Status: WAITING_EXTERNAL
Result: BLOCKED
Requested inputs: LCD module identity, ES8389 official documentation, REC mapping
Resume when: at least one requested artifact has a new identity/revision/hash
Action: stop; do not create another hardware assessment report
```

This behavior terminates phases correctly without weakening production evidence safety.

## 13. Implementation Result

**IMPLEMENTED**

The planned lifecycle architecture, evidence governance, modes, schemas, templates, registry integration, configured path handling, UNKNOWN-safe rendering, and validation tool are present in Universal-MCU-Agent version 0.5.0.

Validation completed:

- Python syntax/compile checks: PASS.
- Lifecycle validator self-test, including Production rejection of prototype-only EAR waiver: PASS.
- JSON schema parsing: PASS.
- YAML config/registry/template parsing: PASS.
- Registry target-path existence: PASS.
- Configured Production directory resolution: PASS.

No generated project artifact was modified by this correction.
