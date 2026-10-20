---
name: build-debug
description: 编译调试
version: "3.0.0"
status: stable
category:
  - build
  - debug
mcu_support:
  - stm32
  - esp32
  - ch592
  - ti-c2000
requires:
  - ready-task
  - platform-contract
outputs:
  - build_result
  - debug_log
contract_version: "3.0"
priority: 70
---

# Workflow: 编译调试 (Build & Debug)

## 描述

处理编译错误、链接错误、构建问题。

## 输入

- `project_path`: 项目路径
- `error_message`: 编译错误信息

## 输出

- 修复后的代码
- 编译成功

## 知识依赖

### 通用知识
- `.mcu-agent/knowledge/common/debugging-methods.md` — 编译错误库
- `.mcu-agent/knowledge/common/common-gotchas.md` — 经验教训

### 平台知识 (根据 config.yaml 自动选择)

读取 `.mcu-agent/config.yaml` 中的 `knowledge.platform` 字段：
- `stm32` → 加载 `.mcu-agent/knowledge/platform/stm32/keil-errors.md`
- `esp32` → 加载 `.mcu-agent/knowledge/platform/esp32/esp-idf-errors.md`
- `ch592` → 加载 `.mcu-agent/knowledge/platform/ch592/mrs-errors.md`

## 执行步骤

Build 只验证 runtime 选中的 firmware project root 和 Task scope，不扫描或猜测其它工程。

### Step 1: 读取编译错误信息

获取完整的编译错误输出。

### Step 2: 匹配已知错误

从 `.mcu-agent/knowledge/common/debugging-methods.md` 查找匹配的错误模式。

### Step 3: 查找经验

从 `.mcu-agent/knowledge/common/common-gotchas.md` 查找相关经验。

### Step 4: 定位问题

分析错误信息，定位问题文件和代码行。

### Step 5: 生成修复方案

根据错误类型生成修复方案。

### Step 6: 应用修复

修改代码，应用修复。

### Step 7: 重新编译

执行编译命令，验证修复是否成功。

ESP-IDF 构建后必须执行 Platform Contract，记录 `scope`、`target`、每个 component 的 `registered/included` 和 inclusion evidence。进程退出码为 0 但预期 component 未进入 build graph 时，结果仍为 `FAIL`。

### Step 8: 循环

- 编译成功 → 完成
- 编译失败 → 回到 Step 2
- 超过 5 轮 → 停止，报告问题

## 知识沉淀

当修复的错误具有通用价值时（不在 `common/debugging-methods.md` 中），生成知识审核文档：

1. 在 `.mcu-agent/knowledge/project/` 创建经验记录
2. 在 `.mcu-agent/knowledge/` 生成 `knowledge-review.md`（参考审核模板）
3. **不自动修改 common/ 或 <family>/ 目录**

审核模板：`.mcu-agent/templates/knowledge-review-template.md`
提升指南：`.mcu-agent/knowledge/project-knowledge-guide.md`

## 关键规则

- 编译失败最多自动修复 5 轮
- 每次修复后必须重新编译验证
- 超过 5 轮仍失败，停止并报告问题
- 项目经验不自动进入公共知识库
- 编译器/链接器执行错误属于 `FAILED`；缺少外部硬件证据或配置输入属于 `BLOCKED`/`WAITING_EXTERNAL`，不得伪装为编译失败继续修复。
- 依赖 artifact 为 `STALE` 时不得声称当前构建已验证；先更新 manifest 或阻塞对应 Task。
- build 成功只能证明代码可构建，不能确认硬件事实、关闭 evidence debt 或批准 Production EAR。
