---
name: my-handler
description: Runtime-selected task handler
version: "1.0.0"
status: experimental
category: [custom]
requires: [ready-task]
outputs: []
contract_version: "2.0"
---

# Custom Task Handler

## Entry Contract

- The shared runtime has created an Intent Record and Work Package.
- The selected Task is `READY` or `IMPLEMENTING`.
- Artifact inputs are declared as manifest roles and resolved through `artifact-manifest.yaml`.

## Allowed Mutations

List exact Task output paths and state transitions.

## Forbidden Mutations

- Do not scan the product tree for artifact candidates.
- Do not edit phase/work-package state directly.
- Do not replace the current user-request Work Package with discovered debt.

## Verification

Declare `analysis`, `host`, `build`, or `target`. Platform builds must run their Platform Contract before Task completion.
