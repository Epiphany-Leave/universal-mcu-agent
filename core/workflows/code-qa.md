---
name: code-qa
description: Analyze source within a runtime-selected project or component scope
version: "3.0.0"
status: stable
category: [code, analysis]
mcu_support: [stm32, esp32, ch592, ti-c2000]
requires: [review-task, selected-source-scope]
outputs: [analysis-result]
contract_version: "3.0"
priority: 50
---

# Workflow Handler: Code Q&A

Analyze only the firmware project/component scope selected by the runtime Task. Source search inside that scope is allowed; project-wide artifact discovery is not. External documents and generated reports must be resolved by manifest role.

Answers cite source paths and lines, distinguish source behavior from hardware truth, and register optional reports in the canonical manifest.
