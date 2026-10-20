# Knowledge Changelog

## 0.8.0 (2026-08-30)

- Workflow knowledge contracts moved to v3 machine descriptors and stable handler digests.
- Firmware guidance now requires `IMPLEMENTATION_ALLOWED`, claim workspaces, verified finalization and promotion.
- Project setup guidance now uses tracked contracts, explicit Artifact registration and approved migration proposals.
- Knowledge release version synchronized to 0.8.0.

## 0.7.0 (2026-08-26)

### 安装与路径一致性

- 知识库发布版本与平台版本同步为 0.7.0。
- 安装器统一部署平台知识到 `knowledge/platform/<platform>/`，不再生成旧的根级 `stm32/`、`esp32/`、`ch592/`。
- 更新时强制刷新知识库 `VERSION` 与 `CHANGELOG.md`，同时永久保留项目私有的 `knowledge/project/`。

## 0.3.0 (2026-07-24)

### 重构

- 目录结构从 `stm32/`、`esp32/`、`ch592/` 改为 `platform/stm32/`、`platform/esp32/`、`platform/ch592/`
- 新增 `platform/` 一级目录
- 新增 `application/` 目录（待扩展）
- 新增 `schema.md` 知识格式规范
- 新增 `registry/knowledge.yaml` 知识注册表
- 新增 `templates/knowledge-template.md` 知识文件模板

### Front Matter 规范

新增知识文件必须包含:
- name, description, category, keywords
- platform, related_workflows
- confidence, version

## 0.2.1 (2026-07-24)

### Added
- common/: 通用架构规范、编码规范、调试方法、工程规则
- stm32/: Keil 错误、STM32 工程创建、STM32 常见错误
- esp32/: ESP-IDF 错误、ESP32 工程创建、ESP32 常见错误
- ch592/: MRS 错误、CH592 工程创建、CH592 常见错误
- project/: 项目经验模板

### 适用平台
- common: 全部 MCU
- stm32: STM32 / GD32 / PY32
- esp32: ESP32 / ESP32-S2 / ESP32-S3 / ESP32-S31
- ch592: CH592 / CH591 / CH32
