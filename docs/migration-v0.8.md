# v0.8 project-contract, workspace and CI migration

## Safety first

Upgrade only with the v0.8 installer after making an external backup. Do not run a pre-v0.7 updater or force mode. The v0.8 installer preserves and backs up `state/`, `custom/`, `config.yaml`, `artifact-manifest.yaml`, `project-contract.json`, and project knowledge.

Installation does **not** create a project contract. Existing projects therefore become `UNINITIALIZED` for new governed plans until an explicit setup or migration is approved.

## New project

```bash
python .mcu-agent/runtime/cli.py project-layout . <project-name> --create \
  --platform <family> --chip <exact-chip>
```

This creates the canonical `hardware/` and `software/<project>/main|components` layout, `.gitkeep` placeholders, tracked `.mcu-agent/project-contract.json`, and `.mcu-agent/policy.lock`.

## Existing project

Generate a proposal without moving files:

```bash
python .mcu-agent/runtime/cli.py project-contract propose-migration \
  . <project-name> --output migration.json
```

Review every candidate. The runtime never selects duplicates. Set `selected_project_root` to exactly one listed product-relative root, review destination/platform/chip, then explicitly apply:

```bash
python .mcu-agent/runtime/cli.py project-contract apply-migration \
  . migration.json --approve
```

Do not apply a proposal that moves historical sources without an external backup and human review.

## Canonical input registration

Direct manifest edits and internal Python resolver calls are no longer workflow interfaces:

```bash
python .mcu-agent/runtime/cli.py artifact register . \
  --role requirements.primary --path inputs/requirements.md --scope product
```

Plan blocks before claim when a declared role is missing, unhashed, absent, or hash-mismatched.

## Governed implementation

New read-write Tasks require a clean Git repository and a claim worktree:

```bash
python .mcu-agent/runtime/cli.py claim-task . <wp> <task> \
  --session-id <session> --workspace
```

Write only below returned workspace/allowed roots. Store model result JSON outside the canonical product (or in ignored runtime state), finalize real verification, then promote:

```bash
python .mcu-agent/runtime/cli.py finalize-task . <wp> <task> result.json --claim-id <claim>
python .mcu-agent/runtime/cli.py promote-task . <wp> <task> --claim-id <claim>
```

Promotion applies the verified patch, emits `.mcu-agent/audit/<id>.json`, and returns required commit trailers. It does not commit, tag, push, or alter remotes.

## CI

Use a separately trusted v0.8 wheel and SHA-256 with `.github/workflows/mcu-policy.yml`. Configure the workflow as a required check and follow `docs/ci-gate-v0.8.md`. Local Skills and repository-local validator code are not trusted CI boundaries.

## Compatibility

Historical v0.7 packages remain readable. Full contract/workflow/Artifact revalidation applies only to newly planned v0.8 Tasks carrying governance metadata. Re-plan active work after migration rather than backfilling policy digests into old audit records.
