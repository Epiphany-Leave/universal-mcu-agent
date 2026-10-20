---
name: project-setup
description: Create or migrate the canonical product workspace layout
version: "3.0.0"
status: stable
category: [project, setup]
mcu_support: [stm32, esp32, ch592, ti-c2000]
requires: [product-root, project-name]
outputs: [product-layout, config.yaml, artifact-manifest.yaml]
contract_version: "3.0"
priority: 100
---

# Workflow Handler: Project Setup

## New Project

Create the runtime-owned layout:

```bash
python .mcu-agent/runtime/cli.py project-layout <product-root> <project-name> --create \
  [--platform <family>] [--chip <exact-chip>]
```

Required layout:

```text
<product-root>/hardware/
<product-root>/software/<project-name>/main/
<product-root>/software/<project-name>/components/
```

Root-level `firmware/` and `drivers/` are forbidden.

## Artifact Inputs

Project setup accepts explicit source paths only through `artifact register`; it must not edit `.mcu-agent/artifact-manifest.yaml` or call an internal resolver class. It must not recursively discover arbitrary documents. Candidate discovery is a separate, explicit migration/ingest operation and must not choose between duplicate roles automatically.

```bash
python .mcu-agent/runtime/cli.py artifact register <product-root> \
  --role <canonical-role> --path <product-relative-path> --scope product
```

## Existing Projects

Produce a migration proposal first:

```bash
python .mcu-agent/runtime/cli.py project-contract propose-migration \
  <product-root> <project-name> --output migration.json
```

The proposal never selects between duplicate candidates. After the user reviews and sets `selected_project_root`, apply only with explicit approval:

```bash
python .mcu-agent/runtime/cli.py project-contract apply-migration \
  <product-root> migration.json --approve
```

Do not move historical projects, rewrite reports, or change Artifact paths before that approval.
