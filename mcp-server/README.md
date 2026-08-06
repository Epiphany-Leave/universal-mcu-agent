# MCU Agent MCP Server

将 core/tools 封装为 MCP (Model Context Protocol) 工具，供任何支持 MCP 的 Agent 调用。

## 安装

```bash
pip install -r requirements.txt
```

## 启动

```bash
# MCP 服务器模式（供 Agent 连接）
python mcu_server.py

# 测试模式
python mcu_server.py --test <project_path>
```

## 可用工具

| 工具 | 描述 |
|------|------|
| `parse_ioc` | 解析 CubeMX .ioc 文件 |
| `parse_netlist` | 解析网表文件（Altium/KiCad/Eagle/ENET） |
| `collect_docs` | 收集项目文档 |
| `extract_pdf` | 提取 PDF 文本 |
| `identify_chips` | 识别芯片和模块 |
| `generate_schematic` | 生成硬件接口文件 |
| `generate_datasheet` | 生成芯片手册文件 |
| `generate_requirements` | 生成需求文件 |
| `get_knowledge` | 获取 MCU 知识库 |
| `get_workflow` | 获取工作流定义 |
| `list_tools` | 列出所有工具 |
| `plan_work` | 生成 Intent、Work Package 和局部门控 Task |
| `review_evidence` | 应用 review budget 并去重相同 evidence 输入 |
| `resolve_artifact` | 从 canonical manifest 按 role 解析输入 |
| `validate_esp_idf` | 验证 ESP-IDF 工程和 component 实际纳入构建 |
| `transition_state` | 执行受控 Phase/Work Package/Task 状态转换 |
| `prepare_task` | 为模型准备受限 Task 上下文 |
| `finalize_task` | 验证模型修改并推进 Task 状态 |
| `route_esp_skill` | 将可选 esp-dev-skill 路由为受限 ESP recipe/API 参考 |
| `session_bootstrap` | 建立宿主 Agent 会话握手并记录当前项目会话 |
| `search_project_memory` | 检索当前项目范围内的已记录经验 |
| `approve_project_memory` | 人工批准一条项目记忆进入项目知识状态 |
| `promote_project_memory` | 将已批准的项目记忆提升为公共或平台知识 |

项目记忆默认写入项目的 `.mcu-agent/runtime/memories/`，并带有来源、置信度、验证信息和项目范围。`search_project_memory` 只提供上下文，不能解除 evidence 或 capability gate；只有人工批准后的记忆才允许通过 `promote_project_memory` 写入公共知识目录。

## MCP 配置

在 Agent 的 MCP 配置中添加：

```json
{
  "mcpServers": {
    "mcu-agent": {
      "command": "python",
      "args": ["<path>/mcp-server/mcu_server.py"],
      "env": {}
    }
  }
}
```

## 支持的 Agent

任何支持 MCP 协议的 Agent 都可以使用：
- Claude Code (VS Code)
- Cursor
- Continue
- 其他 MCP 兼容 Agent
