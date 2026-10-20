# MCU Agent MCP Server（可选 / 实验性）

此目录把部分 `core/tools` 与 runtime API 暴露为 MCP 工具。MCP 传输层是 **optional / experimental**，不是 lifecycle 的权威实现；没有安装 `mcp` 时，core parser 与路径 helper 仍可独立导入和测试。

## 安装与启动

按宿主环境单独安装 MCP Python 包，然后启动：

```bash
python mcu_server.py
python mcu_server.py --test <product_root>
```

```json
{
  "mcpServers": {
    "mcu-agent-experimental": {
      "command": "python",
      "args": ["<path>/mcp-server/mcu_server.py"],
      "env": {}
    }
  }
}
```

## 路径安全契约

所有接收调用方路径的工具都要求显式 `product_root`：

- `product_root` 是信任边界，可以是绝对路径；
- `file_path`、`project_root`、`config_path`、`compile_commands`、`skill_root` 等必须是相对 `product_root` 的路径；
- 绝对子路径、任何 `..`、不存在的输入以及解析后逃出根目录的 symlink 都会被拒绝；
- parser 失败会返回带异常类型和原因的 `error`，不会把空连接伪装为成功。

例如：

```text
parse_netlist(product_root="/work/product", file_path="Hardware/board.net")
extract_pdf(product_root="/work/product", file_path="Evidence/mcu.pdf")
```

## 工具状态

| 工具 | 状态 | 描述 |
|------|------|------|
| `parse_ioc` | experimental | 解析根内 CubeMX `.ioc` |
| `parse_netlist` | experimental | 自动检测并解析 ENET、KiCad、Eagle、Cadence、Altium/OrCAD 网表 |
| `extract_pdf` | experimental | 提取根内 PDF 文本 |
| `collect_docs` / `identify_chips` | disabled | 禁止 project-wide 扫描；改用 manifest 显式 artifact |
| `generate_schematic` / `generate_datasheet` / `generate_requirements` | disabled | 改用 runtime work-package / Task handler |
| `resolve_artifact` | experimental | 从 canonical manifest 按 role 解析输入 |
| `validate_esp_idf` | experimental | 验证根内 ESP-IDF 工程 |
| `review_evidence` | experimental | 计算证据审查预算，不直接写 lifecycle state |
| `plan_work` | experimental façade | 薄调用 `RuntimeEngine.plan`，随后由 `LifecycleService.persist_plan` 持久化；不在 MCP 内独立门控 |
| `claim_task` | experimental façade | 按 Work Package / Task / session ID 薄调用 `LifecycleService.claim_task`；governed read-write Task 必须 `workspace=true` |
| `finalize_task` | experimental façade | 必须提供 `product_root`、Work Package / Task / session / claim revision，薄调用 `LifecycleService.finalize_task`；旧 task/context JSON finalize 不再暴露 |
| `promote_task` | experimental façade | 晋升已验证的 claim workspace；生成 tracked audit 和 commit trailers |
| `route_esp_skill` | experimental | 路由根内可选 skill |
| `session_bootstrap` / project memory tools | experimental | 薄调用对应 runtime service |
| `get_knowledge` / `get_workflow` / `list_tools` | experimental | 读取安装包内的固定资源或列出工具 |

MCP 不暴露文件型 `prepare_task` 或调用方 JSON 的直接 `transition_state`。`plan_work`、`claim_task`、`finalize_task`、`promote_task` 共享 runtime 的 durable lifecycle service；project contract、workflow digest、gate、状态转换、真实变更扫描与输出验证均由 runtime 实现所有，MCP 层不复制另一套规则。
