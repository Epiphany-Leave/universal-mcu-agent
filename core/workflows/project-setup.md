---
name: project-setup
description: Create or migrate the canonical product workspace layout
version: "2.0.0"
status: stable
category: [project, setup]
mcu_support: [stm32, esp32, ch592, ti-c2000]
requires: [product-root, project-name]
outputs: [product-layout, config.yaml, artifact-manifest.yaml]
contract_version: "2.0"
priority: 100
---

# Workflow Handler: Project Setup

## New Project

Create the runtime-owned layout:

```bash
python .mcu-agent/runtime/cli.py project-layout <product-root> <project-name> --create
```

Required layout:

```text
<product-root>/hardware/
<product-root>/software/<project-name>/main/
<product-root>/software/<project-name>/components/
```

Root-level `firmware/` and `drivers/` are forbidden.

## Artifact Inputs

Project setup accepts explicit source paths and registers them in `.mcu-agent/artifact-manifest.yaml`. It must not recursively discover arbitrary documents. Candidate discovery is a separate, explicit migration/ingest operation and must not choose between duplicate roles automatically.

## Existing Projects

Detect all candidate firmware roots with `project_detector.py`. Produce a migration proposal only. Do not move `02_Software/Projects`, rewrite historical reports, or change artifact paths without user approval.
