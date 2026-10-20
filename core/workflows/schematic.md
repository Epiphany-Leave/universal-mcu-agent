---
name: schematic-analysis
description: Extract capabilities from canonical hardware evidence
version: "3.0.0"
status: stable
category: [hardware, schematic]
mcu_support: [stm32, esp32, ch592, ti-c2000]
requires: [hardware.schematic.primary]
outputs: [hardware-capabilities, evidence-debts]
contract_version: "3.0"
priority: 70
---

# Workflow Handler: Schematic Analysis

Resolve `hardware.schematic.primary` by role from the canonical manifest. Do not search for `.ioc`, netlists, PDFs, or similarly named files.

Parse the selected artifact, retain structured warnings, update capability facts, and register stable evidence debts for UNKNOWN values. The phase closes when the selected scope is classified and task impacts are computed, even when debts remain open.

Generated config and examples are implementation references; they cannot establish populated hardware truth.
