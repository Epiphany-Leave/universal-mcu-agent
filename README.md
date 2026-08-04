# Universal MCU Agent Platform

面向嵌入式软件工程生命周期的 Agent runtime。Claude Code、Codex、OpenCode、Cursor 和 MCP 使用同一套 Intent、Work Package、Capability Gate、TaskExecutor、证据治理和平台验证规则。

## 统一架构

```text
用户请求
  -> Intent
  -> Work Package
  -> Capability Gate
  -> READY Task
  -> TaskExecutor
  -> AI 模型编写代码
  -> Build / Debug / Validation
  -> DONE / BLOCKED_* / FAILED
```

Agent adapter 只负责把统一入口接入宿主 Agent，不再各自维护 MCU skill、生命周期或路由逻辑。

每个项目的运行时位于：

```text
<project-root>/.mcu-agent/
├── runtime/
├── workflows/
├── knowledge/
├── registry/
├── schemas/
└── agent-entry.md
```

硬件 `UNKNOWN` 只阻塞声明依赖该事实的 Task。无关的 UI mock、协议抽象、host test 和其他 READY Task 可以继续执行。

## 安装

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

# 同时部署所有 adapter
.\install.ps1 -Agent all -Project "D:\work\pet-robot"
```

Claude 项目安装会创建项目根 `CLAUDE.md`；Codex 使用 `AGENTS.md`；OpenCode 使用 `.opencode/instructions/mcu-agent.md`；Cursor 使用 `.cursorrules`。这些文件都由同一份 `adapters/common/runtime-entry.md` 生成。

入口规则是强制派发协议：Claude Code 处理任何 MCU 请求前必须先执行 `plan --state-root .`，读取 `next_tasks` 后才能进入 workflow 或修改代码。若现有项目的 `CLAUDE.md` 是旧版本，请重新安装并覆盖入口：

```powershell
.\install.ps1 -Agent claude-code -Project "D:\work\pet-robot" -Force
```

清理旧版本 Claude 全局入口：

```powershell
.\install.ps1 -Agent claude-code -CleanLegacyClaude
```

该选项只删除旧安装器创建的 `~/.claude/skills/mcu` 和 `mcu-*.md` 命令，不删除其他 Claude 配置。

### Linux / macOS

```bash
./install.sh --agent claude-code --project /path/to/project
./install.sh --agent codex --project /path/to/project
./install.sh --agent all --project /path/to/project
```

## Runtime 使用

进入项目后，所有 MCU 请求先规划：

```bash
python .mcu-agent/runtime/cli.py plan "帮我写 QMC5883 驱动" --mode production --state-root .
python .mcu-agent/runtime/cli.py plan "帮我做一个 UI" --mode prototype --state-root .
```

对 READY Task 执行模型上下文准备和结果提交：

```bash
python .mcu-agent/runtime/cli.py prepare-task \
  <task.json> <work-package.json> software/<project-name> \
  --request "帮我做一个 UI"

python .mcu-agent/runtime/cli.py finalize-task \
  <task.json> <context.json> <model-result.json>
```

TaskExecutor 负责编辑范围、canonical artifact、模型输出、验证结果和 Task 状态；AI 模型仍负责实际代码设计与修改。

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
adapters/common/    所有 Agent 共用的 runtime 入口
adapters/           宿主 Agent 的最薄接入层
mcp-server/         MCP 工具接口
```

## 验证

```powershell
$env:PYTHONDONTWRITEBYTECODE='1'
python -m unittest discover -s core\runtime\tests -v
python -m unittest discover -s core\tools\tests -v
python mcp-server\mcu_server.py --test .
```

## 许可

Universal MCU Agent Platform：MIT License。
