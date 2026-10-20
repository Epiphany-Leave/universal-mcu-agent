---
name: requirements-analysis
description: Analyze canonical requirement artifacts
version: "3.0.0"
status: stable
category: [requirements, documentation]
mcu_support: [stm32, esp32, ch592, ti-c2000]
requires: [requirements.primary]
outputs: [requirements-structured, acceptance-tests]
contract_version: "3.0"
priority: 60
---

# Workflow Handler: Requirements Analysis

Resolve `requirements.primary` and any explicitly selected supplemental roles from `.mcu-agent/artifact-manifest.yaml`. Never scan the project or select the first PRD/document by filename.

Produce structured requirements and acceptance tests, register each output in the manifest, and preserve dependency IDs. Requirements express intent and never confirm physical hardware facts.
