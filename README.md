# Universal MCU Agent Platform

面向嵌入式软件工程生命周期的 Agent runtime。pi 与 Codex 以统一 Python CLI 为正式控制面；其他 adapter 和可选 MCP façade 必须复用同一套 Intent、Work Package、Capability Gate、证据治理和平台验证规则。

当前发布版本：**0.8.0**。v0.8 引入 tracked project contract、机器 workflow policy、claim worktree、promotion audit 和 trusted CI verifier。升级前先阅读 [v0.8 迁移说明](docs/migration-v0.8.md)；从 v0.7 以前版本升级仍禁止使用旧 updater/force。

## 统一架构

```text
用户请求
  -> tracked Project Contract / derived Project State
  -> Intent / Work Package / Workflow Digest
  -> Artifact + Capability Gate
  -> READY Task
  -> Claim Worktree
  -> Build / Debug / Validation
  -> DONE
  -> Promotion Audit
  -> Trusted CI / Protected Merge
```

Agent adapter 只负责把统一入口接入宿主 Agent，不再各自维护 MCU skill、生命周期或路由逻辑。

每个项目的运行时位于：

```text
<project-root>/.mcu-agent/
├── runtime/                 # 可替换的发行代码
├── tools/ workflows/ ...   # 可替换的发行内容
├── state/                   # Intent、WP、claim、session、status 等可变状态（Git 忽略）
├── workspaces/              # claim 专属 Git worktree（Git 忽略）
├── audit/                   # tracked promotion audit
├── project-contract.json    # tracked canonical layout/platform/gates
├── policy.lock              # tracked workflow policy digest
├── custom/                  # 用户扩展，更新时保留
├── knowledge/project/       # 项目经验，更新时保留
├── config.yaml              # 用户配置，更新时保留
├── artifact-manifest.yaml   # canonical artifact，更新时保留
└── agent-entry.md
```

硬件 `UNKNOWN` 仍按 Task 粒度阻塞；但所有 source-producing Task 还必须满足 project contract、platform root 和 contract-required Artifact 角色。Skill 被禁用也不能取消 Runtime/claim/CI 门禁。

## 安装

先在将运行 runtime 的 Python 3.10+ 环境安装核心依赖：

```bash
python -m pip install .
```

MCP/PDF/开发依赖分别使用 `.[mcp]`、`.[pdf]`、`.[dev]` extras；安装器不会静默修改用户 Python 环境。

### Windows

从本仓库根目录执行：

```powershell
# Claude Code
.\install.ps1 -Agent claude-code -Project "D:\work\pet-robot"

# Codex
.\install.ps1 -Agent codex -Project "D:\work\pet-robot"

# OpenCode
.\install.ps1 -Agent opencode -Project "D:\work\pet-robot"

# Cursor
.\install.ps1 -Agent cursor -Project "D:\work\pet-robot"

# pi
.\install.ps1 -Agent pi -Project "D:\work\pet-robot"

# 同时部署所有 adapter
.\install.ps1 -Agent all -Project "D:\work\pet-robot"
```

项目安装会写入 `.mcu-agent/agent-entry.md`，其唯一来源是 `adapters/common/runtime-entry.md`。pi 与 Codex 共享项目根 `AGENTS.md` 中的 managed block；安装器只刷新该区块，绝不覆盖区块外的用户内容。`all` 只部署一次 runtime，pi/Codex 也只写一次共享区块。

统一入口只允许通过 CLI 执行 `session-bootstrap --agent auto`、`plan --product-root`、`claim-task`、`finalize-task` 和 `abandon-task`/`attest-target`。每个会话先运行：

```powershell
python .mcu-agent\runtime\cli.py session-bootstrap --agent auto --product-root .
```

安装和更新都会幂等刷新入口 managed block。已有安装的普通重复调用不替换 runtime；显式 `--update` / `-Update` 或 `--force` / `-Force` 才刷新发行方 managed 内容并先备份，且永远不能删除用户状态或配置。

清理旧版本 Claude 全局入口：

```powershell
.\install.ps1 -Agent claude-code -CleanLegacyClaude
```

该选项只删除旧安装器创建的 `~/.claude/skills/mcu` 和 `mcu-*.md` 命令，不删除其他 Claude 配置。

### Linux / macOS

```bash
./install.sh --agent claude-code --project /path/to/project
./install.sh --agent codex --project /path/to/project
./install.sh --agent pi --project /path/to/project
./install.sh --agent all --project /path/to/project

# 安全升级（必须使用已切换到 v0.8 的安装器）
./install.sh --agent all --project /path/to/project --update
```

## pi 集成（项目级，不修改全局配置）

安装器部署一个项目级 `.agents/skills/mcu-agent` 入口 Skill，并继续维护薄 `AGENTS.md` block。Skill 改善流程解释但不是安全边界；Runtime、promotion 和 CI 独立复算。也可使用 `mcu-agent host-run --agent pi --product-root . --request "..."` 显式加载固定入口 Skill。

### 1. 部署统一入口

```bash
./install.sh --agent pi --project /path/to/project
# 或 PowerShell: .\install.ps1 -Agent pi -Project "D:\path\to\project"
```

安装器部署 `.mcu-agent/` 和单一 `.agents/skills/mcu-agent/`，幂等维护项目根 `AGENTS.md` 与 `.gitignore` managed block。不要手动复制整个 adapter 文件覆盖已有规则或 Skill。

### 2. 会话握手（每个 pi/Codex 会话开始一次）

```bash
python .mcu-agent/runtime/cli.py session-bootstrap --agent auto --product-root .
```

### 3. MCP 边界

`mcp-server/` 仅供具备 MCP transport 的其他宿主做实验性集成，不属于 pi/Codex 主链路。它不会由安装器部署或写入项目 `.mcp.json`；当前仓库本地 `.mcp.json` 中的 codegraph 配置也与 MCU runtime 无关。核心 CLI 需要安装 `pyproject.toml` 声明的 PyYAML 与 jsonschema 依赖。

### 4. 升级已有项目的 runtime

不要手动覆盖 runtime。v0.8 安装器会先备份用户状态、配置、manifest、contract 与 policy lock，再迁移旧 runtime 状态、替换 managed 内容并验证 workflow digest。安装不会替既有项目生成 contract；请按 [v0.8 迁移说明](docs/migration-v0.8.md) 显式初始化或批准迁移。

## Runtime 使用

进入项目后，所有 MCU 请求先规划：

```bash
python .mcu-agent/runtime/cli.py plan "帮我写 QMC5883 驱动" --product-root . --session-id <session-id>
python .mcu-agent/runtime/cli.py plan "帮我做一个 UI" --product-root . --session-id <session-id>
```

新产品首次 plan 只能得到 `PROJECT_SETUP`。创建 contract 并显式登记输入：

```bash
python .mcu-agent/runtime/cli.py project-layout . tea --create --platform stm32 --chip STM32G431CBU6
python .mcu-agent/runtime/cli.py artifact register . \
  --role requirements.primary --path inputs/requirements.md --scope product
```

受治理的 read-write Task 必须在 worktree 中 claim、finalize、promote：

```bash
python .mcu-agent/runtime/cli.py claim-task . <wp> <task> --session-id <session> --workspace
python .mcu-agent/runtime/cli.py finalize-task . <wp> <task> <result.json> --claim-id <claim>
python .mcu-agent/runtime/cli.py promote-task . <wp> <task> --claim-id <claim>
```

Promotion 不自动 commit/push；使用返回的 `MCU-Claim`、`MCU-Policy`、`MCU-Verification` trailers。CI 配置见 [v0.8 CI gate](docs/ci-gate-v0.8.md)。

### 硬件事实自动加载（config.yaml → capabilities）

`plan` 支持 `--config` 参数：自动把 `config.yaml` 中已填写的 `mcu.*` / `hardware.*` 事实加载为 capabilities，已确认的硬件不再阻塞对应 Task。

```bash
python .mcu-agent/runtime/cli.py plan "写一个 OV3660 摄像头驱动" \
  --mode production --config .mcu-agent/config.yaml --product-root . --project-root software/<project-name>
```

发现新硬件事实后用 `update-config` 回填（保留注释与格式）：

```bash
python .mcu-agent/runtime/cli.py update-config .mcu-agent/config.yaml \
  --set mcu.chip=esp32s31 --set hardware.camera=OV3660
```

对应关系：`hardware.camera` → `board.camera.identity`、`hardware.screen` → `lcd.display.panel.identity`、`mcu.chip` → `board.identity.chip` 等（见 `core/runtime/config_loader.py`）。外部传入的 `--capabilities` 仍然支持并优先于 config 自动加载。

### 证据提取与能力库（完整链路）

证据类请求（手册/原理图/需求）会派发到 `HARDWARE_EVIDENCE_RESOLUTION` work package，拆分为 `datasheet_extraction` / `hardware_extraction` / `requirements_extraction` 任务，每个任务解析来源工件、提取事实并注册产物。提取出的能力事实可以持久化到项目能力库，之后 `plan` 自动加载：

```bash
# 提取 .ioc 引脚事实并写入能力库（ARM）
python .mcu-agent/runtime/cli.py extract-ioc-facts Core/Inc/main.ioc --output /tmp/caps.json
python .mcu-agent/runtime/cli.py capability <product-root> add --input /tmp/caps.json
python .mcu-agent/runtime/cli.py capability <product-root> list

# 之后 plan 自动按 config < state capability store < 显式 capabilities 合并
python .mcu-agent/runtime/cli.py plan "写一个 I2C 传感器驱动" --product-root .
```

`device.<identity>.datasheet` 角色支持通配回退：`device.camera.datasheet` 会自动解析到 `device.esp32s31.datasheet` 等实际条目。

### 外部技能库（esp-dev-skill + aix-skills）

两个只读知识源安装在 `.mcu-agent/skills/`，`claim-task` 返回的 `context` 会自动注入匹配的技能参考：

| 仓库 | 内容 | 匹配方式 |
|------|------|----------|
| `esp-dev-skill`（JasonYANG170/esp-dev-skill） | 41 个 ESP 框架子技能（ESP-IDF/ADF/Brookesia/Matter/...），含 recipe/API/pitfalls | 请求关键词路由到框架仓库（I2C→esp-idf+i2c_master；摄像头→esp-video-components；音频→esp-adf...） |
| `aix-skills`（easyzoom/aix-skills） | 76 个通用嵌入式技能（stm32-hal-ll、sensor-driver、cortex-m-debug、freertos...） | 中英文同义词扩展后的词频匹配，返回 top-5 |

```bash
# 安装（重新 clone 到 .mcu-agent/skills/ 即可恢复）
git clone --depth 1 https://github.com/JasonYANG170/esp-dev-skill .mcu-agent/skills/esp-dev-skill
git clone --depth 1 https://github.com/easyzoom/aix-skills .mcu-agent/skills/aix-skills
# 显式路由
python .mcu-agent/runtime/cli.py route-esp-skill "写一个 I2C 传感器驱动" --product-root .
```

claim context 中的 `esp_dev_skill`（ESP 路由）与 `skill_references`（通用技能）都是只读指导，不能扩大 Task 范围或绕过平台验证。

对 READY Task 使用 ID 型持久接口。`claim-task` 会原子写入 `IMPLEMENTING`、建立真实文件基线，并直接返回完整 `context`；无需手工拆分 task/package JSON：

```bash
python .mcu-agent/runtime/cli.py claim-task . <work-package-id> <task-id> \
  --session-id <session-id> --workspace
```

返回值中的不透明 `claim_id` 用于后续 finalize（`claim_revision` 仅为 v0.7 兼容字段）；`context` 包含：

1. **`allowed_roots`**：唯一允许修改的目录；finalize 在 verifier 运行后再次依据 claim 基线扫描真实 SHA-256 增改删，因此模型与构建/HIL 命令的写入都不能绕过范围检查。
2. **`hardware_facts`**：config、持久能力库和本次 plan 显式 capability 的最终快照。
3. **`input_artifacts` / `input_artifact_errors`**：通过 canonical manifest 解析的输入及有界内容摘要。
4. **`workflow`**：Runtime 唯一选择的 handler path/version/digest。
5. **`esp_dev_skill`、`skill_references`、`project_memories`**：只读实现指导。
6. **`verification_tier` / `verification_profile`**：runtime 必须执行的验证契约。

模型完成编辑后只提交结果说明；验证和状态转换仍由 runtime 所有：

```bash
python .mcu-agent/runtime/cli.py finalize-task . <work-package-id> <task-id> \
  <model-result.json> --claim-id <claim-id> --config .mcu-agent/config.yaml
python .mcu-agent/runtime/cli.py promote-task . <work-package-id> <task-id> \
  --claim-id <claim-id>
```

### 分级真实验证

- `analysis`：`model-result.json` 必须包含非空 `analysis.conclusion` 与 `analysis.sources`；声明的 artifact 仍需通过 role/path/schema 检查。
- `host`：runtime 执行 host profile 的单元测试、静态检查或模拟测试。
- `build`：runtime 执行 build profile，记录 argv、cwd、退出码、输出摘要及错误行。
- `target`：必须先通过 build profile；普通 build 单独永远不能完成 Task，之后还须运行声明 `kind: hil` 的 target profile，或通过 `attest-target` 提交带操作者、板卡身份、步骤、日志/图片及 SHA-256 的人工证据（finalize 会重新校验哈希）。

ID 型 finalize 会自动运行匹配的 verifier；没有匹配的真实 verifier 时 Task 保持 `VALIDATING`，不会回退到模型自报。验证 profile 使用结构化 argv，默认 `shell=false`：

```yaml
verification:
  profiles:
    build:
      tier: build
      argv_windows: ["idf.py", "build"]
      argv_posix: ["idf.py", "build"]
      cwd: "."
      timeout: 900
```

旧 `toolchain.build_command` 默认拒绝执行；迁移期只有用户在该次 finalize 显式传入 `--allow-shell-command` 才会运行，并写入 audit record。旧文件型 `prepare-task`/`finalize-task` 仅保留一个版本用于兼容，不是 pi/Codex 正式入口。

## ARM（STM32）支持

CubeMX `.ioc` 引脚配置可以自动转成 capability 事实，让 ARM 项目的硬件事实同样进入门控与代码上下文：

```bash
# 1. 从 .ioc 提取引脚事实（I2C/SPI/UART/SDMMC/ADC/TIM/DCMI 等）
python .mcu-agent/runtime/cli.py extract-ioc-facts \
  Core/Inc/main.ioc --output .mcu-agent/ioc-capabilities.json

# 2. plan 时带上事实（对应 Task 自动 READY）
python .mcu-agent/runtime/cli.py plan "写一个 I2C 传感器驱动" \
  --capabilities .mcu-agent/ioc-capabilities.json --product-root . --platform stm32 \
  --project-root software/<project-name>
```

示例映射（`core/runtime/platforms/stm32.py`）：

| .ioc 信号 | capability |
|-----------|------------|
| `I2C1_SCL` / `I2C1_SDA` | `board.i2c1.scl_pin` / `board.i2c1.sda_pin` |
| `USART1_TX` / `USART1_RX` | `board.uart1.tx_pin` / `board.uart1.rx_pin` |
| `SPI1_SCK/MISO/MOSI` | `board.spi1.sck/miso/mosi_pin` |
| `SDMMC1_D0/CLK/CMD` | `board.sdmmc1.d0/clk/cmd_pin` |
| `DCMI_D0...D7` | `board.dcmi.data_d*_pin` |

`Stm32Adapter`（已在平台注册表注册）支持 `detect`/`validate`：识别 `.ioc`/Keil/CubeMX 布局，校验结构并读取芯片型号。每一条事实都带 `evidence_ids: ioc:<file>:<pin>` 来源追踪。

## 项目记忆

Task 完成后，runtime 会生成项目范围记忆；只有真正进入 `DONE` 的 Task 可获得 `verified` confidence，失败或仍在验证的记录只能是 draft：

```text
.mcu-agent/state/memories/<memory-id>.json
```

后续 `claim-task` 返回 context 时会按请求、平台和 Task 标题检索相关记忆，并将结果放入 `project_memories`。项目记忆不会自动解锁硬件 Task，也不会自动进入公共知识库。

查看项目记忆：

```powershell
python .mcu-agent\runtime\cli.py memory-search . "ESP-IDF component build" --platform esp32
```

人工批准后，记忆状态变为 `APPROVED_PROJECT`：

```powershell
python .mcu-agent\runtime\cli.py memory-approve . mem-xxxxxxxxxxxx `
  --reviewer Epiphanye `
  --notes "确认可复用"
```

只有批准的记忆才能提升到当前项目的公共知识目录：

```powershell
python .mcu-agent\runtime\cli.py memory-promote . mem-xxxxxxxxxxxx `
  --target platform `
  --reviewer Epiphanye
```

支持的状态为 `DRAFT`、`APPROVED_PROJECT`、`PROMOTED`、`REJECTED`、`SUPERSEDED`。项目记忆必须保留来源 Task、源文件、验证结果、平台和置信度；公共知识提升仍需人工维护知识注册表。

## ESP 专用知识

外部 `esp-dev-skill` 是只读知识 provider，不是第二个 Agent 入口。它提供 ESP-IDF、Arduino、Matter、BLE、Wi-Fi、音频、视觉等 recipe/API/陷阱参考，但不能扩大 Task scope 或绕过生命周期验证。

推荐安装到项目运行时目录：

```powershell
.\install.ps1 `
  -Agent claude-code `
  -Project "D:\work\pet-robot" `
  -EspDevSkillPath "D:\skills\esp-dev-skill"
```

也可以显式路由：

```bash
python .mcu-agent/runtime/cli.py route-esp-skill \
  "帮我写 QMC5883 I2C 驱动" \
  --skill-root D:/skills/esp-dev-skill \
  --platform esp-idf
```

不要把外部 skill 另行复制到 `~/.claude/skills`，否则会产生绕过 Universal MCU Agent 的第二个 ESP 入口。外部仓库的版本和上游许可由项目维护者单独核验。

## 工程目录

新项目使用产品根与软件工程根分离的结构：

```text
<product-root>/
├── hardware/
└── software/
    └── <project-name>/
        ├── CMakeLists.txt
        ├── main/
        ├── components/<component-name>/
        └── tests/
```

禁止新项目使用产品根级 `firmware/` 或 `drivers/`。ESP-IDF component 必须直接位于 `components/<component-name>/`，并通过 Component Contract 验证其实际进入构建。

## 主要目录

```text
core/runtime/       执行内核、TaskExecutor、artifact 和平台适配器
core/schemas/       Intent、Task、Phase、Artifact、EAR 等契约
core/workflows/     Agent 无关的 Task handler 定义
core/knowledge/     通用 MCU 工程知识
core/tools/         解析、检测和生命周期工具
adapters/common/    pi/Codex 等宿主共享的唯一 runtime 入口
adapters/           宿主 Agent 的最薄接入层，不维护分叉生命周期
mcp-server/         可选、实验性的 MCP façade（不属于 pi/Codex 主链路）
```

## 验证

```powershell
$env:PYTHONDONTWRITEBYTECODE='1'
python -m unittest discover -s core\runtime\tests -v
python -m unittest discover -s core\tools\tests -v
python -m unittest discover -s tests -v
python mcp-server\mcu_server.py --test .
bash -n install.sh
```

## 许可

Universal MCU Agent Platform：MIT License。
