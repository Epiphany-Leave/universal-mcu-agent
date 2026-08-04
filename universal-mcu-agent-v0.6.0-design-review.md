# Universal-MCU-Agent v0.6.0 全局架构审查

> 审查版本：v0.5.0  
> 审查日期：2026-08-01  
> 审查结论：**NEEDS_RUNTIME_ARCHITECTURE_UPGRADE**  
> 本文记录 v0.6 runtime 改造前的审查基线。后续实现已按本报告结论落地；本页中的“未修改”描述仅适用于审查阶段。

## 1. 执行摘要

v0.5.0 已经建立了正确的证据治理方向：Evidence Debt、EAR、Artifact Manifest、`WAITING_EXTERNAL`、`PASS_WITH_DEBT`、phase/work-item 分离和 lifecycle validator 都已存在。问题在于，这些能力目前主要存在于 Markdown 规则、JSON Schema 和事后校验器中，并未形成统一的运行时执行模型。

当前系统实质上仍是：

```text
自然语言 -> 关键词匹配 -> 单个 Markdown workflow -> Agent 自行解释并执行
```

而不是：

```text
Human Intent
  -> Phase
    -> Capability Graph
      -> Work Package
        -> Implementation Task
          -> Build / Debug / Validation
```

因此，v0.5.0 能表达“hardware assessment 已完成但 lcd_driver 仍被阻塞”，却不能可靠地调度这个结果。不同 Agent 仍可能把局部 `UNKNOWN` 解释为整个 firmware workflow 的入口条件不满足，再次进入审计并生成更多报告。

补充集成决策：外部 `esp-dev-skill` 适合作为 ESP 专用知识/recipe provider，不应成为 lifecycle controller。v0.6 通过 `core/runtime/esp_dev_skill.py` 将其路由结果注入 `TaskExecutor` 上下文，代码修改仍由模型完成并必须经过 Task scope、manifest、证据门控和平台契约验证。

v0.6.0 的首要目标应当是：**从“文档驱动的证据审计流程”升级为“具有持久化领域状态、依赖门控和用户意图边界的嵌入式工程执行内核”。**

## 2. 审查范围与方法

已全量阅读用户指定范围：

- `core/workflows/`：全部 10 个文件；
- `core/schemas/`：全部 4 个 schema；
- `core/registry/`：全部 3 个 registry；
- `core/tools/`：入口、配置、检测、生命周期校验、测试及全部网表解析器。

为追踪真实执行边界，额外审查了：

- `core/templates/` 中目录、phase、closure、debt、EAR 和项目配置模板；
- `core/config/` 的 dispatch 与文件分类规则；
- 工程创建、ESP-IDF、架构和 evidence governance 知识；
- Codex、Claude Code、OpenCode、Cursor adapters；
- MCP server、Windows/Linux 安装与升级脚本；
- v0.5.0 修正报告、迁移说明及 lifecycle 示例。

现有测试结果：`core/tools/tests` 共 5 项全部通过；`validate_lifecycle.py self-test` 通过。测试只覆盖少量静态结构约束，不覆盖状态转换、调度、manifest 解析、平台 component contract 或端到端 workflow。

## 3. 当前架构画像

### 3.1 已具备的声明层

v0.5.0 已能声明：

- Phase status：`PLANNED / ACTIVE / WAITING_EXTERNAL / COMPLETED / CANCELLED`；
- Phase result：`PASS / PASS_WITH_DEBT / BLOCKED / FAILED`；
- Work-item readiness：`READY / BLOCKED_HARDWARE / BLOCKED_CONFIG / BLOCKED_ARCHITECTURE / DISABLED`；
- Evidence Debt、Artifact、EAR 及 Production/Prototype/Exploration 模式；
- closure、reopen、resume criteria 和 artifact dependency。

这部分设计方向正确，应保留“status 与 result 分离”，而不是简单把 `COMPLETED_WITH_DEBT` 塞回 status。对用户可展示 `COMPLETED_WITH_DEBT`，其规范化存储应仍是：

```yaml
status: COMPLETED
result: PASS_WITH_DEBT
```

### 3.2 缺失的执行层

仓库没有真正的 workflow engine、transition service、scheduler 或 state repository：

- `orchestrator.md:40-86` 仍通过关键词选择 workflow；
- `validate_lifecycle.py` 只能校验一个最终 JSON 是否形状合法，不能执行或验证状态迁移；
- MCP server 只暴露解析、生成、知识和 workflow 文本读取，没有 lifecycle、Work Package 或调度 API；
- adapter 大多仅提示 Agent 阅读 Markdown，Cursor/OpenCode 甚至未同步 v0.5.0 lifecycle 全局规则。

结论：当前一致性依赖 LLM 自觉遵守提示词，而不是由 Universal-MCU-Agent 平台强制保证。

## 4. 架构问题与根因

### P0-1：Phase 与 Work Item 虽已分字段，但仍在同一对象中耦合

`phase-state.schema.json:30-40` 只给 work item 定义了 `id/readiness/debt_ids`。它没有类型、目标、验收条件、依赖、任务、实现状态、验证层级或输出，因此 work item 不是可执行对象，只是 phase 报告中的阻塞摘要。

后果：

- phase closure 后没有独立对象可由下一阶段继续实现；
- 一个 phase manifest 只能有一个 `successor_phase`，无法表达多个可并行 Work Package；
- `READY` 不能自动转成 `IMPLEMENTING`，`BLOCKED_HARDWARE` 也不能在新证据到达后定向恢复；
- Step8 的工作项只能继续附着在 Step8 报告上，形成 `Step8.x` 扩张。

### P0-2：Firmware 入口仍是全局硬件门

`firmware.md:14-16` 将 `hardware.json` 和 `datasheet.json` 声明为整个 workflow 的 requires；`firmware.md:77-80` 又要求读取完整硬件和 datasheet 产物。虽然后文写了 work-item 可独立 blocked，但入口契约仍鼓励 Agent 先判断“硬件资料是否完整”，而不是“当前任务依赖的事实是否满足”。

这正是 `UNKNOWN = Block everything` 的结构来源。

### P0-3：没有 Human Intent Boundary

当前自然语言分诊只识别关键词和 workflow，没有持久化：

- 用户本轮的目标是实现、评审、取证还是探索；
- 用户授权的工作范围；
- 是否允许 Agent 自动扩展审查；
- 用户是否明确要求进入 implementation；
- 哪些新问题只能登记 debt、不能抢占当前目标。

于是“帮我做一个简单 UI”可能被解释成“先完成全部显示硬件审计”。系统没有规则规定：发现非当前任务依赖的问题时，只登记 debt，不得把它提升为新的主任务。

### P0-4：Review Budget 不存在

Build workflow 有最多 5 轮修复，但 evidence review 没有机器可读预算、内容指纹或耗尽动作。`WAITING_EXTERNAL` 仅靠 Agent 比较 identity/revision/hash；没有执行 ledger 记录某 phase 已审查几轮、输入集合是否相同、最后结论是什么。

因此 AI-A/AI-B 可以在相同输入上不断产生语义相近的新报告。

### P0-5：Artifact Manifest 不是 canonical resolver

当前 manifest schema 记录 `id/path/type/revision/sha256/...`，validator 能检查重复 ID、缺失依赖和环，但不能保证：

- 同一 scope/role 只有一个 `CURRENT` artifact；
- path 唯一且确实存在；
- 文件内容与声明 SHA-256 一致；
- producer、supersedes 和状态转换合法；
- workflow 只从 manifest 解析输入；
- PRD、hardware.json、datasheet 的多个副本不会被重新 `rglob` 发现。

实际 workflow 仍明确要求扫描整个项目，例如 `requirements.md:50`、`schematic.md:49`、`datasheet.md:47`。所以 manifest 已存在，但尚未成为唯一事实入口。

### P0-6：Platform Detector 只返回一个扁平结果

`project_detector.py:108-118` 采用 first-match `if/elif`；多个嵌套工程只能返回第一个命中项。`rglob` 后直接取第一个 `sdkconfig`（`:183-187`），没有候选根、嵌套层级、冲突或 workspace topology。

ESP-IDF fallback 还要求根 `CMakeLists.txt` 与 `main/idf_component.yml` 同时存在（`:354-369`），但 `idf_component.yml` 并非 ESP-IDF 应用的必需文件；顶层 CMake 通常也不包含 `idf_component_register`。这会漏检合法工程。

当前 detector 不能区分：

- 产品仓库根；
- firmware application root；
- 独立 component/BSP；
- bare-metal 工程；
- monorepo 内多个 firmware target；
- 示例工程与主工程。

### P0-7：Build PASS 不验证 component 被发现

现有 ESP-IDF 知识只给出示例目录，没有 Component Contract。系统没有验证：

- `components/<component_name>/CMakeLists.txt` 是否存在；
- component 是否在默认扫描深度或 `EXTRA_COMPONENT_DIRS` 中；
- `idf_component_register` 是否注册了预期源码和 include；
- component 是否实际进入构建图、`compile_commands.json` 或链接产物。

因此 `components/drivers/lcd` 未被 ESP-IDF 发现时，空主工程仍可 Build PASS。

### P1-1：产品目录与 firmware root 混淆

当前模板将工程放入 `02_Software/Projects`，驱动另放 `02_Software/Drivers`；配置同样将 `firmware` 和 `drivers` 指向两个平级目录。它没有 `product_root`、`software_root`、`firmware_project_root` 三种概念。

v0.6.0 应采用以下不变量：

```text
<product-root>/
├── hardware/
└── software/
    └── <project-name>/       # firmware application root
        ├── CMakeLists.txt
        ├── main/
        └── components/
            └── <component-name>/
```

禁止新建 `<product-root>/firmware`。平台专属目录必须位于 `software/<project-name>` 内；共享软件若未来需要，应通过显式 workspace/package contract 引入，不能回到根级散放。

### P1-2：EAR 已有记录格式，但不是 gate input

EAR schema 已包含 scope、risk、validation、expiry 和 rollback，这是良好基础。但没有：

- EAR 与 capability fact/work package/task 的结构化引用；
- approval/expiry/revocation 状态机；
- 到期后自动重新阻塞；
- 验证成功后由 evidence 替换 assumption 的流程；
- Prototype 产物与 Production release 的可追踪隔离。

### P1-3：适配器能力不一致

Codex adapter 包含 v0.5.0 lifecycle 规则，Cursor/OpenCode adapter 仍是旧版短规则；Claude Code skill 也只是转向 Markdown。相同项目换一个 Agent 可能得到不同的 phase closure 行为。

核心约束不应复制进各 adapter 提示词。adapter 应只调用同一 execution kernel，并展示 kernel 返回的 next actions。

### P1-4：当前工具链存在会削弱审计可信度的实现缺陷

这些不是 v0.6.0 域模型本身，但必须作为 P0 稳定性基线处理：

- 网表注册表引用 `altium_netlist_parser` 等不存在模块，而文件实际为 `altium_parser.py` 等；
- 多个 parser 从不存在的 `src.parsers.netlist_parser` 导入；
- MCP `parse_netlist` 引用不存在的 `parse_netlist` 函数；
- MCP `generate_requirements` 引用不存在的生成函数；
- MCP `get_workflow` 使用未定义的 `HAS_YAML/yaml`；
- PowerShell update 调用未定义的 `Copy-File`；
- 解析器中仍存在宽泛异常吞掉行为，warning 不能替代 source-level completeness 判定。

这些缺陷会造成“工具未执行成功”与“没有发现硬件证据”混淆。

## 5. v0.6.0 目标领域模型

### 5.1 四层执行对象

| 层 | 职责 | 生命周期 |
|---|---|---|
| Phase | 治理边界和里程碑，如 Hardware Enablement | `PLANNED/ACTIVE/WAITING_EXTERNAL/COMPLETED/CANCELLED` + result |
| Capability | 可被任务消费的能力及其事实依赖，如 `display.render`、`i2c.bus` | `AVAILABLE/DEGRADED/BLOCKED/UNKNOWN` |
| Work Package | 从用户意图生成的可交付工作单元，如 QMC5883 driver、Pet UI | `PROPOSED/READY/BLOCKED/IN_PROGRESS/VALIDATING/DONE/CANCELLED` |
| Task | 可执行、可验证、可重试的原子工作 | `READY/BLOCKED_*/IMPLEMENTING/VALIDATING/DONE/FAILED/SKIPPED` |

Phase 不再拥有 work item 的完整状态，只引用 Work Package ID 并聚合统计。Work Package 和 Task 必须独立持久化，才能在 phase closure 后继续开发或定向恢复。

### 5.2 Human Intent Boundary

每次自然语言入口先创建 `Intent Record`：

```yaml
intent_id: intent-20260801-001
kind: implementation          # implementation/review/evidence/debug/plan
statement: "帮我先构建一个简单符合需求的UI界面"
requested_outcome: runnable_ui
scope: [ui]
mode: prototype
expansion_policy: register_only
authority: user
```

核心规则：

1. 用户明确要求 implementation 时，调度器必须优先创建并执行可运行的 Work Package。
2. 新发现但不阻塞当前 Task 的问题只登记 debt，不得抢占用户目标。
3. 扩展到新硬件审计、架构重写或额外 Work Package，必须有依赖关系或用户授权。
4. `WAITING_EXTERNAL` 只暂停依赖对应输入的 Task/Work Package；除非 phase 本身唯一目标就是取证，否则不能冻结整个项目。
5. 用户说“停止审查、进入实现”时，未完成审计按预算关闭为 `PASS_WITH_DEBT`，然后调度 READY 范围。

### 5.3 Capability Gate

不建议同时手工维护 `blocks` 与 `allows` 两份容易漂移的列表。规范数据应保存 capability facts 和 Work Package requirements，由 gate engine 推导 blocked/allowed 结果；可在输出中物化 `blocks/allows` 作为只读视图。

```yaml
capability_id: display.render
facts:
  panel.identity:
    state: UNKNOWN
    value: null
    evidence_ids: []
impacts:
  - requirement: panel.identity
    blocks: [lcd_board_binding, on_target_display_validation]
    does_not_block: [ui_model, ui_mock_backend, animation, state_machine]
```

Work Package 对事实声明门控强度：

```yaml
requirements:
  - capability: i2c.bus
    facts: [api_contract]
    gate: REQUIRED
  - capability: board.qmc5883
    facts: [sda_pin, scl_pin]
    gate: REQUIRED_FOR_TARGET_VALIDATION
  - capability: qmc5883.device
    facts: [register_map]
    gate: REQUIRED
```

这样 QMC5883 portable driver、host tests 和 board binding 可分别调度；未知 GPIO 只阻塞 board binding/on-target validation。

### 5.4 Phase Closure 与 Review Budget

每个 phase definition 必须声明：

```yaml
completion_criteria:
  - selected scope audited
  - unknown facts registered as stable debts
  - impact scope computed
review_budget:
  max_rounds: 2
  fingerprint_inputs: [artifact_ids, revisions, sha256]
  on_exhausted: CLOSE_WITH_DEBT
```

执行 ledger 记录 round、input fingerprint、findings fingerprint 和动作。当连续输入指纹未变化时，不消耗新审查轮次并直接返回已有结论；达到预算后自动生成唯一 closure，禁止生成 `Step8.x.y`。

`WAITING_EXTERNAL` 应优先属于 evidence-resolution Work Package。Hardware Enablement 在“审计完成、debt 登记、影响范围明确”后应关闭为 `COMPLETED/PASS_WITH_DEBT`。

### 5.5 Canonical Artifact Manifest

新增根级唯一入口：

```text
<product-root>/.mcu-agent/artifact-manifest.yaml
```

manifest v2 至少增加：

- `scope`：product/project/component；
- `role`：`requirements.primary`、`hardware.schematic.primary`、`device.qmc5883.datasheet` 等；
- `logical_name` 和 `media_type`；
- `identity/revision/sha256`；
- `current` 与 supersession history；
- `discovered_by/accepted_by/accepted_at`；
- path confinement 和 existence policy。

约束：同一 `scope + role` 最多一个 CURRENT；workflow 只能按 role/ID 读取。全盘扫描仅允许在显式 `artifact ingest` 阶段执行，发现候选冲突时必须让用户或规则选择，不能按“第一个同名文件”静默决定。

manifest 推荐 YAML 作为人工维护入口；validator 内部使用结构化 parser。v0.5.0 JSON manifest 可继续读取，但 v0.6.0 只写 YAML v2。

### 5.6 Platform Detector 与 Component Contract

Detector 输出应从单一结果改为候选集合：

```yaml
workspace:
  product_root: ...
projects:
  - id: pet-robot-fw
    root: software/pet-robot
    kind: application
    platform: esp-idf
    target: esp32s3
    confidence: high
    evidence: [...]
components:
  - root: software/pet-robot/components/qmc5883
    owner_project: pet-robot-fw
```

检测应采用可扩展 adapter：ESP-IDF、STM32Cube、Keil、MRS、CCS、CMake bare-metal、Make bare-metal、standalone BSP。每个 adapter 同时实现：

- `detect()`：候选根与证据；
- `layout_contract()`：合法目录和关键文件；
- `component_contract()`：发现/注册规则；
- `build_contract()`：构建命令和成功证据；
- `validation_contract()`：host/build/target 验证层级。

ESP-IDF Component Contract 至少验证：

1. component 位于 `components/<name>`，或被 `EXTRA_COMPONENT_DIRS` 显式注册；
2. component 根含 `CMakeLists.txt`；
3. `idf_component_register` 包含预期 SRCS/SRC_DIRS 与 INCLUDE_DIRS；
4. 依赖通过 REQUIRES/PRIV_REQUIRES 可解析；
5. 预期源文件实际出现在 build graph/compile commands；
6. 需要时验证 component library 进入链接图。

Build PASS 必须附带 `build_scope` 和 `included_components`，不能仅凭进程退出码宣称实现已验证。

### 5.7 Prototype / EAR 执行模型

EAR 应成为 task-scoped gate override，而不是替代事实：

```yaml
ear_id: EAR-LCD-001
scope: [wp-ui, task-mock-resolution]
assumption: "mock viewport uses 320x240"
state: APPROVED
expires_at: "hardware panel identified"
validation_method: "replace mock profile and run visual regression"
```

规则：

- 只允许 Prototype/Exploration；
- 只解锁明确列出的 Task；
- underlying capability fact 继续为 UNKNOWN；
- 过期/撤销后相关 Task 重新计算 gate；
- Production release gate 拒绝任何仍被 EAR 支撑的目标硬件声明；
- host mock、接口定义和纯业务逻辑可以进入实现，但产物必须带验证层级标签。

## 6. 建议的运行流程

```text
User Intent
  -> Intent Classifier
  -> Work Package Factory
  -> Artifact Resolver (manifest only)
  -> Capability Gate
      -> READY tasks ------> Implement -> Build -> Debug -> Validate
      -> BLOCKED tasks ----> Debt/EAR -> WAITING_EXTERNAL or bounded prototype
  -> Phase Aggregator
  -> Closure / Next ready work
```

自然语言路由不应再直接选择 `firmware.md`。它先选择 Work Package 类型，例如：

- `SENSOR_DRIVER`：QMC5883 driver；
- `UI_FEATURE`：宠物机器人 UI；
- `SCHEDULER_OPTIMIZATION`：FreeRTOS 调度优化；
- `HARDWARE_EVIDENCE_RESOLUTION`：指定 debt 的取证；
- `PLATFORM_INTEGRATION`：board binding；
- `VALIDATION`：host/build/on-target 验证。

## 7. 目标行为示例

### 7.1 “帮我写 QMC5883 磁力计驱动”

生成一个 `SENSOR_DRIVER` Work Package，并拆为：

| Task | Gate | 初始状态 |
|---|---|---|
| 定义 portable API | C 语言/平台接口已知 | READY |
| 实现 register driver | 匹配器件的 register map | READY 或 BLOCKED_REQUIREMENT |
| host fake-I2C tests | portable API | READY |
| ESP-IDF component integration | project root + component contract | READY/blocked config |
| board binding | I2C port/pins/address policy | BLOCKED_HARDWARE |
| on-target angle validation | 实物、安装方向、校准方法 | BLOCKED_HARDWARE |

未确认板级引脚不能阻塞 portable driver 和 host tests。

### 7.2 “帮我做一个宠物机器人 UI”

`ui_model`、`state_machine`、`animation`、`mock_display_backend` 进入 READY；`lcd_board_binding` 和 `on_target_validation` 因 panel identity 未知而 blocked。UI Work Package 继续实现、构建和 mock validation。

## 8. 文件与模块影响范围

### 8.1 建议新增

- `core/runtime/`：intent service、state store、transition engine、artifact resolver、capability gate、scheduler、review ledger；
- `core/schemas/intent-record.schema.json`；
- `core/schemas/capability.schema.json`；
- `core/schemas/work-package.schema.json`；
- `core/schemas/task.schema.json`；
- `core/schemas/execution-ledger.schema.json`；
- `core/schemas/platform-project.schema.json`；
- `core/platforms/<platform>/`：detector、layout/component/build contracts；
- `core/registry/work-package-types.yaml`；
- `core/registry/platforms.yaml`；
- `core/templates/artifact-manifest-template.yaml`；
- `core/templates/work-package-template.yaml`。

### 8.2 必须重构

| 范围 | 修改方向 |
|---|---|
| `orchestrator.md` | 从 keyword-to-workflow 改为 intent-to-work-package；只描述策略，不承担状态机 |
| `firmware.md` | 移除全局 hardware/datasheet requires；按 Task capability gate 执行 |
| `schematic/datasheet/requirements` | 从全盘扫描改为 manifest role 输入；扫描下沉到 ingest |
| `build.md` | 增加 build scope、component inclusion、validation tier |
| schemas | v2 引用独立 Work Package/Task；增加 transition invariants |
| validator | 从 shape validation 升级为 repository + transition + hash + contract validation |
| `project_detector.py` | 返回 workspace topology 和多候选工程；平台 adapter 化 |
| `project_config.py` | 显式 product/software/project roots；支持 legacy mapping |
| generators/parsers | 输入输出都通过 artifact resolver；工具失败与空证据分离 |
| registries | 注册 typed contracts、schema、handler、version，而非只存 Markdown path |
| MCP server | 暴露 intent、plan、gate、transition、manifest、platform validate API |
| adapters | 仅做 UI/Agent glue；所有 Agent 调同一个 runtime |
| installers | 部署 runtime/platforms，执行 config/schema migration 和 adapter parity check |

### 8.3 工程目录变更

新项目必须写入：

```text
hardware/
software/<project-name>/
```

禁止生成根级 `/firmware`。现有 `02_Software/Projects/<name>` 应作为 legacy layout 读取，不能在升级时静默移动。迁移工具先输出 relocation plan，经用户确认后移动，并同步 manifest path/hash。

## 9. Migration Strategy

### Stage 0：v0.5.1 稳定性基线

- 修复 parser/MCP/install 的已确认调用错误；
- 增加无 `__pycache__` 的 unit/integration test 运行方式；
- 为现有 schemas 补 transition negative tests；
- 冻结 v0.5 artifact ID 与 debt ID 语义。

### Stage 1：双读单写领域模型

- runtime 同时读取 v0.5 JSON 与 v0.6 YAML；
- 只写 v0.6 schema；
- 将 phase 内 work item 投影为独立 Work Package；
- 保留原 phase/report，不重写历史；
- Step8 assessment 映射为 `COMPLETED/PASS_WITH_DEBT`，Step8.4 映射为 evidence-resolution Work Package。

### Stage 2：Canonical Manifest 与目录拓扑

- 首次运行生成 manifest migration proposal；
- 对同 role 多候选 artifact 要求显式选择；
- 识别 product root 与所有 project roots；
- 新项目强制 `software/<project-name>`；旧目录继续只读兼容；
- relocation 必须单独授权、可回滚并更新 hash。

### Stage 3：Work Package 调度与 Capability Gate

- 自然语言入口创建 Intent Record 和 Work Package；
- 现有 workflows 作为 handler/playbook 被 runtime 调用；
- 未满足依赖只阻塞关联 Task；
- 增加 EAR override、到期和定向恢复；
- 实施 review budget 与 input fingerprint 去重。

### Stage 4：Platform/Component Contract

- 先交付 ESP-IDF adapter 和 component inclusion 验证；
- 再扩展 STM32Cube/Keil、MRS、CCS 和 bare-metal；
- build result 必须记录实际工程根和纳入构建的组件；
- 补充 nested application、standalone BSP、monorepo fixtures。

### Stage 5：Adapter 与 MCP 收敛

- Codex/Claude/OpenCode/Cursor 统一调用 runtime；
- 删除 adapter 中重复的生命周期业务规则；
- MCP 增加结构化错误码，不以 JSON 字符串包装所有结果；
- 用同一 conformance suite 验证所有入口行为一致。

## 10. 兼容性与风险

| 风险 | 控制措施 |
|---|---|
| schema v2 破坏旧项目 | 双读单写、显式 migration proposal、历史只读 |
| capability graph 过度复杂 | 先覆盖 UI、sensor driver、platform integration 三类 Work Package |
| `blocks/allows` 双写漂移 | 只存 requirements/impact rules，结果由 gate engine派生 |
| 自动目录迁移损坏工程 | 默认不移动；确认后事务化迁移并校验 build |
| EAR 被误用为 Production 事实 | task scope、expiry、release gate 和 lineage 四层限制 |
| Agent 绕过 runtime 直接改状态 | adapter 只获最小 API；状态文件由 transition service 单写 |
| review budget 漏掉真实新证据 | artifact identity/revision/hash 变化会产生新 input fingerprint |

## 11. v0.6.0 验收标准

以下场景必须进入自动化 conformance tests：

1. LCD identity UNKNOWN 时，`lcd_board_binding` blocked，但 `ui_mock/animation/state_machine` READY。
2. Hardware Enablement 在债务登记和影响计算后关闭为 `COMPLETED/PASS_WITH_DEBT`，不会生成第三轮同输入审查。
3. 同一 artifact fingerprint 再次调用 evidence review，返回已有 `WAITING_EXTERNAL`，不创建新报告。
4. “写 QMC5883 驱动”生成 `SENSOR_DRIVER` Work Package 并至少执行所有 READY tasks。
5. 新工程只可创建于 `software/<project-name>`；根级 `firmware` 触发 contract failure。
6. `components/drivers/lcd` 未通过 `EXTRA_COMPONENT_DIRS` 注册时，即使 `idf.py build` 成功，Component Contract 仍失败。
7. 新 evidence 只恢复引用对应 debt/capability 的 Task，不重开无关 phase。
8. EAR 只解锁声明 scope，过期后重新计算 readiness，Production release 拒绝 EAR 依赖。
9. 多个同 role PRD/hardware/datasheet 必须产生 manifest conflict，不允许选择第一个文件。
10. Codex、Claude Code、OpenCode、Cursor、MCP 对同一 fixture 返回相同 Work Package 和 gate 结果。

## 12. 最终建议

v0.6.0 不应继续以“给现有 workflow 增加更多规则段落”为主线。v0.5.0 已证明，仅有正确规则不能保证 Agent 正确终止或继续开发。

建议把版本目标定义为三个不可妥协的架构成果：

1. **可执行领域模型**：Phase、Capability、Work Package、Task 独立持久化并由 transition service 管理；
2. **局部门控与用户意图优先**：UNKNOWN 只沿依赖图阻塞，用户明确的实现目标优先调度 READY 范围；
3. **平台契约和 canonical inputs**：manifest 是唯一 artifact resolver，platform adapter 验证工程结构、component discovery 和真实 build inclusion。

完成这三项后，Universal-MCU-Agent 才会从“能够正确记录为什么不能做”升级为“知道哪些现在能做、立即去做、如何验证，并在证据补齐后精确恢复”的完整嵌入式软件工程生命周期 Agent。
