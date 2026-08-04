# Changelog

## 0.6.0 (2026-08-01)

### Runtime architecture upgrade

- Added `core/runtime` with Intent Manager, Work Package Factory, task-level Capability Gate, transition engine, state store, and bounded evidence Review Budget.
- Added `TaskExecutor` to bridge model-owned code edits with scoped context, changed-file boundary checks, verification, and controlled Task transitions.
- Added independent v2 schemas for intents, capabilities, Work Packages, Tasks, phases, evidence debts, execution ledger, EARs, canonical artifacts, and platform results.
- Replaced keyword-to-workflow orchestration with runtime-first intent planning. Workflows are now Task handlers.
- Removed the global firmware `hardware.json`/`datasheet.json` gate; UNKNOWN facts block only Tasks that declare those facts.
- Added canonical `.mcu-agent/artifact-manifest.yaml` resolution with role uniqueness, path confinement, and hash validation.
- Added the ESP-IDF Platform Adapter and Component Contract, including `compile_commands.json` inclusion checks.
- Enforced new project roots under `software/<project-name>` and prohibited root-level `firmware/` and `drivers/` for new projects.
- Added six runtime conformance tests covering UI partial progress, QMC5883 task splitting, review deduplication, canonical layout, artifact conflict, and component inclusion.
- Unified Codex, Claude Code, OpenCode, Cursor, and MCP around the shared runtime entry.
- Fixed parser module registration/imports, MCP netlist dispatch, and the PowerShell updater's invalid copy helper call.
- Added an optional `esp-dev-skill` knowledge provider. It routes ESP recipes and API references into bounded Task context without changing lifecycle, evidence, artifact, or platform-contract authority.
- Added `-EspDevSkillPath` installer support to opt a checked-out ESP skill into `.mcu-agent/skills/esp-dev-skill`; core updates preserve that external directory.
- Removed the duplicated Claude `mcu-*` skill/command layer. Claude, Codex, OpenCode, and Cursor now receive the same runtime entry contract; legacy Claude globals can be removed with `-CleanLegacyClaude`.

## 0.5.0 (2026-08-01)

### Lifecycle and evidence correction

- Added phase, evidence-debt, artifact-manifest, and engineering-assumption schemas.
- Added a standard-library lifecycle validator and evidence-resolution workflow.
- Separated terminal phase outcomes from blocked work-item readiness.
- Added `WAITING_EXTERNAL` stop/resume semantics and Production/Prototype/Exploration modes.
- Classified evidence provenance and prohibited ADR/example/generated defaults from replacing physical facts.
- Switched generators/parsers to configured English paths with legacy path compatibility, `UNKNOWN` rendering, and structured parse warnings.
- Updated installers and Codex adapter to deploy and enforce lifecycle contracts.

## 0.4.0 (2026-07-24)

### Knowledge Architecture Enhancement

- 新增 `core/knowledge/schema.md` — 知识格式规范 (Front Matter)
- 新增 `core/registry/knowledge.yaml` — 知识注册表
- 新增 `core/templates/knowledge-template.md` — 知识文件模板
- Knowledge 目录重构：`stm32/` → `platform/stm32/`
- 新增 `platform/` 一级目录
- 新增 `application/` 目录（待扩展）

### Knowledge Schema

```yaml
---
name: uart-debugging
category: [communication, debugging]
platform: [stm32, esp32]
keywords: [uart, serial, baudrate]
related_workflows: [firmware, build]
confidence: verified
version: "1.0.0"
---
```

### Knowledge 加载流程

```
用户请求 → Skill Registry → Workflow → Knowledge Registry → 加载相关知识
```

## 0.3.0 (2026-07-24)

### 新增

- Workflow Metadata：所有 8 个 workflow 增加 front matter metadata
- Skill Registry：`core/registry/workflows.yaml` + `skills.yaml`
- Workflow 版本管理：`core/workflows/CHANGELOG.md`
- Custom Skill 扩展：`.mcu-agent/custom/` 用户自定义目录
- 自定义模板：`core/templates/custom-skill-template.md` + `custom-workflow-template.md`

### Workflow Metadata 字段

```yaml
name: firmware-development
version: "1.0.0"
status: stable
category: [firmware, embedded]
mcu_support: [stm32, esp32, ch592, ti-c2000]
requires: [hardware.json, datasheet.json]
outputs: [source_code, firmware_log]
priority: 80
```

### Custom Skill 扩展

```
.mcu-agent/custom/
├── skills/          # 用户自定义 skill
└── workflows/       # 用户自定义 workflow
```

加载优先级：`custom/` > `core/`
update 时保留 `custom/` 目录

### Skill Registry

```
core/registry/
├── workflows.yaml   # workflow 索引
└── skills.yaml      # 触发词 → workflow 映射
```

## 0.2.3 (2026-07-24)

### 新增

- `core/config/mcu-alias.yaml` — MCU 型号标准化映射
- project_detector.py 集成 alias 映射，自动标准化检测结果

### MCU Alias 映射

| 原始字符串 | 标准化结果 |
|-----------|-----------|
| esp32s31 | ESP32-S31 |
| ESP32S31 | ESP32-S31 |
| ESP32-S31 | ESP32-S31 |
| stm32f407 | STM32F407 |
| f280049c | TMS320F280049C |

## 0.2.2 (2026-07-24)

### 新增

- `core/tools/project_detector.py` — MCU 项目自动检测工具
- `core/knowledge/VERSION` — 知识库独立版本号
- `core/knowledge/CHANGELOG.md` — 知识变更记录
- project-setup workflow 增加自动检测 MCU 平台步骤
- install 脚本支持 knowledge 版本管理

### 自动检测支持

| MCU 族 | 检测信号 |
|--------|----------|
| STM32 | .ioc, .uvprojx |
| ESP32 | sdkconfig, CMakeLists.txt + idf_component.yml, platformio.ini |
| CH592 | .cproject (WCH) |
| TI C2000 | .cproject (TMS320) |
| GD32/PY32 | .uvprojx (兼容 STM32) |

### Knowledge 版本管理

- update 模式保留 knowledge/project/（项目经验）
- 更新 common/ 和平台特定知识目录
- 显示 knowledge 版本变化

## 0.2.1 (2026-07-24)

### 配置增强

- mcu-config.yaml 增加 project.type、mcu.family、mcu.chip、toolchain.ide 字段
- mcu-config.yaml 增加 knowledge.platform 自动推导
- Workflow 支持根据 config.yaml 自动加载平台知识

### 知识审核

- 新增 knowledge-review-template.md 审核模板
- 新增 project-knowledge-guide.md 提升指南
- build/firmware/code-qa workflow 增加知识沉淀触发
- 项目经验不自动进入公共知识库

### Bug 修复

- 修复 workflow 中 knowledge 路径双重 common/ 前缀

## 0.2.0 (2026-07-24)

### 架构优化

- Knowledge 知识库分层：common/ + stm32/ + esp32/ + ch592/ + project/
- 安装脚本支持 install 和 update 两种模式
- update 模式保留项目 knowledge 和用户配置
- 统一 tools 命名（mcu-tools → tools）
- Workflow 引用路径统一为 .mcu-agent/

### Knowledge 分层

- common/: 架构规范、编码规范、调试方法、工程规则
- stm32/: Keil 错误、STM32 工程创建
- esp32/: ESP-IDF 错误、ESP32 工程创建
- ch592/: MRS 错误、CH592 工程创建
- project/: 项目经验模板

### 安装系统

- install.ps1: 新增 -Update 参数
- install.sh: 新增 --update 参数
- update 模式：更新 tools/workflows/templates/config/analysis，保留 knowledge/config.yaml

## 0.1.0 (2026-07-24)

### 初始版本

- 核心工具层 (core/tools/)
- 知识库 (core/knowledge/)
- 工作流 (core/workflows/)
- 4 个 Agent 适配器 (Claude Code / Codex / OpenCode / Cursor)
- MCP Server
- 安装脚本 (install.ps1 / install.sh)
