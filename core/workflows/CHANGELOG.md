# Workflows Changelog

## Runtime contracts 2.0 (2026-08-01)

- Orchestrator now creates Intent Records and Work Packages through `core/runtime`.
- Workflows are Task handlers and no longer control project lifecycle.
- Firmware has no global hardware/datasheet gate.
- Evidence Resolution uses a two-round fingerprinted review budget.
- Requirements, schematic, and datasheet handlers resolve canonical manifest roles instead of scanning the project.
- Build validates platform scope and ESP-IDF component inclusion.

## Lifecycle contracts 1.0 (2026-08-01)

- 分离 phase status/result 与 work-item readiness
- 新增 `WAITING_EXTERNAL` 停止/恢复规则和 evidence-resolution workflow
- 加入 Production/Prototype/Exploration 模式与 EAR 边界
- 加入 evidence debt、artifact dependency 和 closure contract
- 统一配置驱动的 Production/Hardware 输出路径

## orchestrator 1.0.0 (2026-07-24)

- 初始版本
- 自然语言分诊
- 关键词 → workflow 映射

## project-setup 1.0.0 (2026-07-24)

- 初始版本
- 项目文件扫描与分类
- 标准目录创建
- .mcu-agent/ 部署
- MCU 平台自动检测

## schematic-analysis 1.0.0 (2026-07-24)

- 初始版本
- 支持 .ioc/.sch/.net/.pdf 格式
- 生成 hardware.json

## datasheet-extraction 1.0.0 (2026-07-24)

- 初始版本
- PDF 文本提取
- 芯片信息识别
- 生成 datasheet.json

## requirements-analysis 1.0.0 (2026-07-24)

- 初始版本
- 文档收集与分析
- 生成 requirements.json / constraints.json / test_cases.json

## firmware-development 1.0.0 (2026-07-24)

- 初始版本
- 多平台工程创建
- 代码生成规则
- 知识库集成

## build-debug 1.0.0 (2026-07-24)

- 初始版本
- 编译错误匹配
- 自动修复（最多 5 轮）
- 平台特定错误库

## code-qa 1.0.0 (2026-07-24)

- 初始版本
- 代码搜索与分析
- 分析报告生成
