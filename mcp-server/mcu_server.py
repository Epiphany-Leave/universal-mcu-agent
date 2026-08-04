#!/usr/bin/env python3
"""
MCU Agent MCP Server

将 core/tools 封装为 MCP 工具，供任何支持 MCP 的 Agent 调用。

用法:
  python mcu_server.py                    # 启动 MCP 服务器
  python mcu_server.py --test <project>   # 测试模式
"""

import sys
import os
import json
import asyncio
from pathlib import Path

# 添加 core/tools 到 Python 路径
SCRIPT_DIR = Path(__file__).parent
CORE_TOOLS = SCRIPT_DIR.parent / "core" / "tools"
CORE_DIR = SCRIPT_DIR.parent / "core"
sys.path.insert(0, str(CORE_TOOLS))
sys.path.insert(0, str(CORE_DIR))

try:
    import yaml
    HAS_YAML = True
except ImportError:
    yaml = None
    HAS_YAML = False

try:
    from mcp.server.fastmcp import FastMCP
except ImportError:
    print("请安装 MCP: pip install mcp")
    print("或: pip install -r requirements.txt")
    sys.exit(1)

# ── 创建 MCP 服务器 ──────────────────────────────────────────

mcp = FastMCP("MCU Agent")

# ── 工具实现 ─────────────────────────────────────────────────

@mcp.tool()
def parse_ioc(file_path: str) -> str:
    """解析 CubeMX .ioc 文件，返回引脚配置 JSON。

    Args:
        file_path: .ioc 文件的绝对路径
    """
    from parsers.ioc_parser import parse_ioc_file, ioc_to_hardware_interfaces
    result = parse_ioc_file(file_path)
    if result:
        interfaces = ioc_to_hardware_interfaces(result)
        return json.dumps(interfaces, ensure_ascii=False, indent=2)
    return json.dumps({"error": "解析失败"}, ensure_ascii=False)


@mcp.tool()
def parse_netlist(file_path: str) -> str:
    """解析网表文件（自动检测格式），返回接口信息 JSON。

    支持格式: Altium (.SchDoc), KiCad (.kicad_sch), Eagle (.sch), ENET (.enet)

    Args:
        file_path: 网表文件的绝对路径
    """
    from parsers.netlist_parser import detect_netlist_format, get_parser
    path = Path(file_path)
    format_type = detect_netlist_format(path)
    parser = get_parser(format_type)
    if parser is None:
        return json.dumps({"error": f"不支持的格式: {format_type}"}, ensure_ascii=False)
    parsed = parser.parse(path)
    result = {
        "file": file_path,
        "format": format_type,
        "mcu": parser.find_mcu(parsed),
        "interfaces": parser.extract_interfaces(parsed),
    }
    return json.dumps(result, ensure_ascii=False, indent=2)


@mcp.tool()
def collect_docs(project_path: str) -> str:
    """收集项目目录下的所有文档文本内容。

    Args:
        project_path: 项目根目录路径
    """
    return json.dumps({
        "error": "project-wide document scanning is disabled in v0.6",
        "next": "register explicit inputs in artifact-manifest.yaml and call resolve_artifact",
        "project_path": project_path,
    }, ensure_ascii=False)


@mcp.tool()
def extract_pdf(file_path: str, keywords: str = "") -> str:
    """提取 PDF 文本内容，可按关键词过滤。

    Args:
        file_path: PDF 文件的绝对路径
        keywords: 可选，逗号分隔的关键词列表，如 "pin,register,voltage"
    """
    try:
        import fitz
        terms = [term for term in keywords.split(",") if term] or ["pin", "register", "voltage", "current"]
        document = fitz.open(file_path)
        pages = []
        for page in document:
            content = page.get_text()
            if any(term.lower() in content.lower() for term in terms):
                pages.append({"page": page.number + 1, "text": content[:4000]})
            if len(pages) >= 20:
                break
        result = {"file": file_path, "total_pages": document.page_count, "matched_pages": len(pages), "pages": pages}
        document.close()
        return json.dumps(result, ensure_ascii=False, indent=2)
    except Exception as exc:
        return json.dumps({"error": str(exc)}, ensure_ascii=False)


@mcp.tool()
def identify_chips(project_path: str) -> str:
    """从项目文件中识别芯片和模块。

    Args:
        project_path: 项目根目录路径
    """
    return json.dumps({
        "error": "project-wide chip scanning is disabled in v0.6",
        "next": "resolve an explicit schematic/netlist artifact and call parse_netlist",
        "project_path": project_path,
    }, ensure_ascii=False)


@mcp.tool()
def generate_schematic(project_path: str) -> str:
    """解析硬件文件，生成 hardware.json 和硬件接口速查.md。

    Args:
        project_path: 项目根目录路径
    """
    return json.dumps({
        "error": "scanner-based generation is disabled in v0.6",
        "next": "resolve hardware.schematic.primary, then run the schematic Task handler",
        "project_path": project_path,
    }, ensure_ascii=False)


@mcp.tool()
def generate_datasheet(project_path: str) -> str:
    """解析芯片手册 PDF，生成 datasheet.json 和芯片手册提取.md。

    Args:
        project_path: 项目根目录路径
    """
    return json.dumps({
        "error": "scanner-based generation is disabled in v0.6",
        "next": "resolve device.<identity>.datasheet, then run the datasheet Task handler",
        "project_path": project_path,
    }, ensure_ascii=False)


@mcp.tool()
def generate_requirements(project_path: str) -> str:
    """分析项目文档，生成 requirements.json 和产品功能说明.md。

    Args:
        project_path: 项目根目录路径
    """
    return json.dumps({
        "error": "requirements generation is a runtime work-package handler, not a standalone scanner",
        "next": "call plan_work and resolve requirements.primary from artifact-manifest.yaml",
        "project_path": project_path,
    }, ensure_ascii=False)


@mcp.tool()
def plan_work(
    user_input: str,
    mode: str = "production",
    platform: str = "",
    capabilities_json: str = "[]",
    assumptions_json: str = "[]",
) -> str:
    """Classify human intent and return capability-gated work packages."""
    from runtime.engine import RuntimeEngine
    try:
        payload = json.loads(capabilities_json)
        capabilities = payload.get("capabilities", payload) if isinstance(payload, dict) else payload
        assumption_payload = json.loads(assumptions_json)
        assumptions = assumption_payload.get("assumptions", assumption_payload) if isinstance(assumption_payload, dict) else assumption_payload
        result = RuntimeEngine().plan(
            user_input,
            mode=mode,
            platform=platform or None,
            capabilities=capabilities,
            assumptions=assumptions,
        )
        return json.dumps(result, ensure_ascii=False, indent=2)
    except (ValueError, json.JSONDecodeError) as exc:
        return json.dumps({"error": str(exc)}, ensure_ascii=False)


@mcp.tool()
def review_evidence(phase_id: str, artifacts_json: str, ledger_json: str = "", max_rounds: int = 2) -> str:
    """Apply review budget and return the updated execution ledger."""
    from runtime.review_budget import ReviewBudget
    try:
        artifacts_payload = json.loads(artifacts_json)
        artifacts = artifacts_payload.get("artifacts", artifacts_payload) if isinstance(artifacts_payload, dict) else artifacts_payload
        ledger = json.loads(ledger_json) if ledger_json else None
        budget = ReviewBudget(ledger)
        result = budget.request(phase_id, artifacts, max_rounds=max_rounds)
        return json.dumps({"result": result, "ledger": budget.ledger}, ensure_ascii=False, indent=2)
    except (ValueError, json.JSONDecodeError) as exc:
        return json.dumps({"error": str(exc)}, ensure_ascii=False)


@mcp.tool()
def resolve_artifact(product_root: str, role: str, scope: str = "product") -> str:
    """Resolve one CURRENT artifact from the canonical manifest."""
    from runtime.artifact_resolver import ArtifactManifestError, ArtifactResolver
    try:
        result = ArtifactResolver(product_root).resolve(role, scope=scope)
        return json.dumps(result, ensure_ascii=False, indent=2)
    except ArtifactManifestError as exc:
        return json.dumps({"error": str(exc)}, ensure_ascii=False)


@mcp.tool()
def validate_esp_idf(
    project_root: str,
    compile_commands: str = "",
    build_succeeded: bool = False,
    expected_components_json: str = "[]",
) -> str:
    """Validate ESP-IDF project layout and actual component inclusion."""
    from runtime.platforms.esp_idf import EspIdfAdapter
    path = Path(compile_commands) if compile_commands else None
    result = EspIdfAdapter().validate(
        Path(project_root),
        compile_commands=path,
        build_succeeded=True if build_succeeded else None,
        expected_components=json.loads(expected_components_json),
    )
    return json.dumps(result, ensure_ascii=False, indent=2)


@mcp.tool()
def transition_state(kind: str, record_json: str, target: str, reopen_json: str = "") -> str:
    """Apply a validated phase, Work Package, or Task transition."""
    from runtime.transition_engine import TransitionEngine, TransitionError
    try:
        record = json.loads(record_json)
        reopen = json.loads(reopen_json) if reopen_json else None
        result = TransitionEngine().transition(kind, record, target, reopen=reopen)
        return json.dumps(result, ensure_ascii=False, indent=2)
    except (json.JSONDecodeError, TransitionError) as exc:
        return json.dumps({"error": str(exc)}, ensure_ascii=False)


@mcp.tool()
def prepare_task(
    task_json: str,
    request: str,
    mode: str,
    project_root: str,
    product_root: str = "",
    skill_root: str = "",
    platform: str = "",
) -> str:
    """Prepare bounded context for a model to implement one Task."""
    from runtime.task_executor import TaskExecutor, TaskExecutionError
    try:
        task = json.loads(task_json)
        product = product_root or project_root
        resolver = None
        if task.get("input_roles"):
            from runtime.artifact_resolver import ArtifactResolver
            resolver = ArtifactResolver(product)
        context = TaskExecutor().prepare(
            task,
            request=request,
            mode=mode,
            project_root=project_root,
            product_root=product,
            manifest=resolver,
            skill_root=skill_root or None,
            platform=platform or None,
        )
        return json.dumps(context, ensure_ascii=False, indent=2)
    except (json.JSONDecodeError, TaskExecutionError) as exc:
        return json.dumps({"error": str(exc)}, ensure_ascii=False)


@mcp.tool()
def route_esp_skill(
    request: str,
    product_root: str = "",
    skill_root: str = "",
    platform: str = "",
) -> str:
    """Route an optional esp-dev-skill package to bounded reference files."""
    from runtime.esp_dev_skill import EspDevSkillError, EspDevSkillRouter
    try:
        result = EspDevSkillRouter(skill_root or None, product_root=product_root or None).route(
            request, platform=platform or None
        )
        return json.dumps(result, ensure_ascii=False, indent=2)
    except EspDevSkillError as exc:
        return json.dumps({"error": str(exc)}, ensure_ascii=False)


@mcp.tool()
def finalize_task(
    task_json: str,
    context_json: str,
    model_result_json: str,
    product_root: str = "",
) -> str:
    """Validate model-reported edits and return the next Task state."""
    from runtime.task_executor import TaskExecutionError, TaskExecutor
    try:
        task = json.loads(task_json)
        context = json.loads(context_json)
        model_result = json.loads(model_result_json)
        resolver = None
        if product_root and any(isinstance(item, dict) and item.get("role") for item in model_result.get("output_artifacts", [])):
            from runtime.artifact_resolver import ArtifactResolver
            resolver = ArtifactResolver(product_root)
        result = TaskExecutor().execute(
            task,
            context=context,
            model_runner=lambda _context: model_result,
            manifest=resolver,
        )
        return json.dumps(result, ensure_ascii=False, indent=2)
    except (json.JSONDecodeError, TaskExecutionError) as exc:
        return json.dumps({"error": str(exc)}, ensure_ascii=False)


@mcp.tool()
def get_knowledge(topic: str) -> str:
    """获取 MCU 知识库内容。

    Args:
        topic: 知识主题，可选值:
            - architecture: 程序架构规范
            - coding-style: 编码风格
            - code-rules: 代码生成规则
            - compilation-errors: 编译错误库
            - gotchas: 经验教训
            - timepiece: 时间片轮询框架
            - engineering: 工程创建方法
            - evidence-governance: 证据与生命周期治理
    """
    knowledge_dir = SCRIPT_DIR.parent / "core" / "knowledge"
    topic_map = {
        "architecture": "common/architecture.md",
        "coding-style": "common/coding-style.md",
        "code-rules": "common/code-rules.md",
        "compilation-errors": "common/debugging-methods.md",
        "gotchas": "common/common-gotchas.md",
        "timepiece": "common/timepiece-framework.md",
        "engineering": "common/engineering-rules.md",
        "evidence-governance": "common/evidence-governance.md",
    }

    if topic not in topic_map:
        return json.dumps({
            "error": f"未知主题: {topic}",
            "available": list(topic_map.keys())
        }, ensure_ascii=False)

    file_path = knowledge_dir / topic_map[topic]
    if file_path.exists():
        return file_path.read_text(encoding="utf-8")
    return json.dumps({"error": f"文件不存在: {file_path}"}, ensure_ascii=False)


@mcp.tool()
def get_workflow(name: str) -> str:
    """获取工作流定义。

    Args:
        name: 工作流名称，如: orchestrator, project-setup, firmware, build 等
    """
    # 从 registry 读取 workflow 列表
    registry_path = SCRIPT_DIR.parent / "core" / "registry" / "workflows.yaml"
    workflows_dir = SCRIPT_DIR.parent / "core" / "workflows"

    workflow_map = {}
    if registry_path.exists():
        try:
            if HAS_YAML:
                with open(registry_path, "r", encoding="utf-8") as f:
                    registry = yaml.safe_load(f) or {}
                    for k, v in registry.get("workflows", {}).items():
                        workflow_map[k] = v.get("path", "")
                    aliases = {
                        "project-setup": "project-setup",
                        "schematic": "schematic-analysis",
                        "datasheet": "datasheet-extraction",
                        "requirements": "requirements-analysis",
                        "firmware": "firmware-development",
                        "build": "build-debug",
                    }
                    for alias, canonical in aliases.items():
                        if canonical in workflow_map:
                            workflow_map[alias] = workflow_map[canonical]
        except Exception:
            pass

    # 降级：硬编码
    if not workflow_map:
        workflow_map = {
            "orchestrator": "workflows/orchestrator.md",
            "project-setup": "workflows/project-setup.md",
            "schematic": "workflows/schematic.md",
            "datasheet": "workflows/datasheet.md",
            "requirements": "workflows/requirements.md",
            "firmware": "workflows/firmware.md",
            "build": "workflows/build.md",
            "code-qa": "workflows/code-qa.md",
            "evidence-resolution": "workflows/evidence-resolution.md",
        }

    if name not in workflow_map:
        return json.dumps({
            "error": f"未知工作流: {name}",
            "available": list(workflow_map.keys())
        }, ensure_ascii=False)

    file_path = SCRIPT_DIR.parent / "core" / workflow_map[name]
    if file_path.exists():
        return file_path.read_text(encoding="utf-8")
    return json.dumps({"error": f"文件不存在: {file_path}"}, ensure_ascii=False)


@mcp.tool()
def list_tools() -> str:
    """列出所有可用的 MCP 工具。"""
    tools = [
        {"name": "parse_ioc", "description": "解析 CubeMX .ioc 文件"},
        {"name": "parse_netlist", "description": "解析网表文件"},
        {"name": "collect_docs", "description": "收集项目文档"},
        {"name": "extract_pdf", "description": "提取 PDF 文本"},
        {"name": "identify_chips", "description": "识别芯片和模块"},
        {"name": "generate_schematic", "description": "生成硬件接口文件"},
        {"name": "generate_datasheet", "description": "生成芯片手册文件"},
        {"name": "generate_requirements", "description": "生成需求文件"},
        {"name": "plan_work", "description": "意图分类、Work Package 生成与 Capability Gate"},
        {"name": "review_evidence", "description": "证据审查预算与重复输入去重"},
        {"name": "resolve_artifact", "description": "从 canonical manifest 解析 artifact"},
        {"name": "validate_esp_idf", "description": "验证 ESP-IDF 工程与 component 实际纳入"},
        {"name": "transition_state", "description": "执行受控 Phase/Work Package/Task 状态转换"},
        {"name": "prepare_task", "description": "为模型准备受限 Task 上下文"},
        {"name": "finalize_task", "description": "验证模型修改并推进 Task 状态"},
        {"name": "route_esp_skill", "description": "将可选 esp-dev-skill 路由为受限 ESP recipe/API 参考"},
        {"name": "get_knowledge", "description": "获取 MCU 知识"},
        {"name": "get_workflow", "description": "获取工作流定义"},
        {"name": "list_tools", "description": "列出所有工具"},
    ]
    return json.dumps(tools, ensure_ascii=False, indent=2)


# ── 启动 ─────────────────────────────────────────────────────

def main():
    if len(sys.argv) > 1 and sys.argv[1] == "--test":
        # 测试模式
        project = sys.argv[2] if len(sys.argv) > 2 else "."
        print("=== MCU Agent MCP Server (Test Mode) ===")
        print(f"Project: {project}")
        print()
        print("Available tools:")
        print(list_tools())
        print()
        print("Testing collect_docs...")
        result = collect_docs(project)
        print(result[:500] + "..." if len(result) > 500 else result)
    else:
        # MCP 服务器模式
        print("Starting MCU Agent MCP Server...", file=sys.stderr)
        mcp.run()


if __name__ == "__main__":
    main()
