---
name: datasheet-extraction
description: Extract device capability facts from canonical documentation
version: "2.0.0"
status: stable
category: [hardware, datasheet]
mcu_support: [stm32, esp32, ch592, ti-c2000]
requires: [device.<identity>.datasheet]
outputs: [device-capabilities, evidence-debts]
contract_version: "2.0"
priority: 60
---

# Workflow Handler: Datasheet Extraction

The caller provides a stable device identity and manifest role such as `device.qmc5883.datasheet`. Resolve that role only. Do not scan all PDFs or substitute documentation for a similar part.

Extract cited facts, update the matching capability, and register unresolved facts as debt. Documentation assessment closes after classification; dependent tasks may remain blocked independently.
