#!/usr/bin/env python3
"""
MCU Agent MCP Server (optional / experimental)

将 core/tools 封装为可选、实验性的 MCP 工具；runtime API 仍是权威入口。

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
    HAS_MCP = True
except ImportError:
    FastMCP = None
    HAS_MCP = False

from parsers.path_security import ProductPathError, resolve_product_path


class _UnavailableMCP:
    """Keep path/parser helpers importable when the optional MCP extra is absent."""

    def tool(self):
        return lambda function: function

    def run(self):
        raise RuntimeError("optional MCP support is not installed; install the 'mcp' package")


# This transport is explicitly optional/experimental; core parser/runtime imports
# do not depend on the MCP package.
mcp = FastMCP("MCU Agent (optional/experimental)") if HAS_MCP else _UnavailableMCP()


def _product_root(product_root: str) -> Path:
    return resolve_product_path(product_root, ".", kind="directory")


def _json_error(exc: Exception) -> str:
    return json.dumps({"error": f"{type(exc).__name__}: {exc}"}, ensure_ascii=False)


# ── 工具实现 ─────────────────────────────────────────────────

@mcp.tool()
def parse_ioc(product_root: str, file_path: str) -> str:
    """解析 product_root 内的 CubeMX .ioc；file_path 必须是相对路径。"""
    from parsers.ioc_parser import parse_ioc_file, ioc_to_hardware_interfaces
    try:
        path = resolve_product_path(product_root, file_path, kind="file")
        result = parse_ioc_file(path)
        if not result:
            raise ValueError("IOC parser returned no data")
        return json.dumps(ioc_to_hardware_interfaces(result), ensure_ascii=False, indent=2)
    except (OSError, UnicodeError, ValueError) as exc:
        return _json_error(exc)


@mcp.tool()
def parse_netlist(product_root: str, file_path: str) -> str:
    """解析 product_root 内的 EDA 网表；file_path 必须是相对路径。"""
    from parsers.netlist_parser import detect_netlist_format, get_parser, validate_parsed_netlist
    try:
        path = resolve_product_path(product_root, file_path, kind="file")
        format_type = detect_netlist_format(path)
        parser = get_parser(format_type)
        if parser is None:
            raise ValueError(f"no registered parser for detected format: {format_type}")
        parsed = validate_parsed_netlist(parser.parse(path), format_type)
        result = {
            "file": file_path,
            "format": format_type,
            "mcu": parser.find_mcu(parsed),
            "interfaces": parser.extract_interfaces(parsed),
            "components_count": len(parsed["components"]),
            "nets_count": len(parsed["nets"]),
        }
        return json.dumps(result, ensure_ascii=False, indent=2)
    except (OSError, UnicodeError, ValueError) as exc:
        return _json_error(exc)


@mcp.tool()
def collect_docs(product_root: str) -> str:
    """已禁用的扫描器；product_root 仍须是显式、有效的产品根。"""
    try:
        root = _product_root(product_root)
        return json.dumps({
            "error": "project-wide document scanning is disabled",
            "next": "register explicit inputs in artifact-manifest.yaml and call resolve_artifact",
            "product_root": str(root),
        }, ensure_ascii=False)
    except ValueError as exc:
        return _json_error(exc)


@mcp.tool()
def extract_pdf(product_root: str, file_path: str, keywords: str = "") -> str:
    """提取 product_root 内的 PDF；file_path 必须是相对路径。"""
    document = None
    try:
        import fitz
        path = resolve_product_path(product_root, file_path, kind="file")
        terms = [term.strip() for term in keywords.split(",") if term.strip()] or ["pin", "register", "voltage", "current"]
        document = fitz.open(path)
        pages = []
        for page in document:
            content = page.get_text()
            if any(term.lower() in content.lower() for term in terms):
                pages.append({"page": page.number + 1, "text": content[:4000]})
            if len(pages) >= 20:
                break
        result = {"file": file_path, "total_pages": document.page_count, "matched_pages": len(pages), "pages": pages}
        return json.dumps(result, ensure_ascii=False, indent=2)
    except Exception as exc:
        return _json_error(exc)
    finally:
        if document is not None:
            document.close()


@mcp.tool()
def identify_chips(product_root: str) -> str:
    """已禁用的扫描器；使用显式 product_root。"""
    try:
        return json.dumps({
            "error": "project-wide chip scanning is disabled",
            "next": "resolve an explicit schematic/netlist artifact and call parse_netlist",
            "product_root": str(_product_root(product_root)),
        }, ensure_ascii=False)
    except ValueError as exc:
        return _json_error(exc)


@mcp.tool()
def generate_schematic(product_root: str) -> str:
    """已禁用的扫描式生成器；使用 runtime Task handler。"""
    try:
        return json.dumps({
            "error": "scanner-based generation is disabled",
            "next": "resolve hardware.schematic.primary, then run the schematic Task handler",
            "product_root": str(_product_root(product_root)),
        }, ensure_ascii=False)
    except ValueError as exc:
        return _json_error(exc)


@mcp.tool()
def generate_datasheet(product_root: str) -> str:
    """已禁用的扫描式生成器；使用 runtime Task handler。"""
    try:
        return json.dumps({
            "error": "scanner-based generation is disabled",
            "next": "resolve device.<identity>.datasheet, then run the datasheet Task handler",
            "product_root": str(_product_root(product_root)),
        }, ensure_ascii=False)
    except ValueError as exc:
        return _json_error(exc)


@mcp.tool()
def generate_requirements(product_root: str) -> str:
    """已弃用：需求生成由 runtime work-package handler 所有。"""
    try:
        return json.dumps({
            "error": "requirements generation is a runtime work-package handler, not a standalone scanner",
            "next": "call plan_work and resolve requirements.primary from artifact-manifest.yaml",
            "product_root": str(_product_root(product_root)),
        }, ensure_ascii=False)
    except ValueError as exc:
        return _json_error(exc)


@mcp.tool()
def plan_work(
    user_input: str,
    product_root: str,
    mode: str = "production",
    platform: str = "",
    capabilities_json: str = "[]",
    assumptions_json: str = "[]",
    config_path: str = "",
    project_root: str = ".",
    session_id: str = "",
) -> str:
    """Thin-call the runtime planner and persist through LifecycleService."""
    from runtime.engine import RuntimeEngine
    from runtime.lifecycle_service import LifecycleService
    from runtime.state_store import StateStore
    try:
        root = _product_root(product_root)
        project = resolve_product_path(root, project_root, kind="directory")
        default_config = root / ".mcu-agent" / "config.yaml"
        config = (
            resolve_product_path(root, config_path, kind="file")
            if config_path
            else default_config if default_config.is_file() else None
        )
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
            config_path=str(config) if config else None,
            capability_store=str(StateStore(root).root / "capabilities.json"),
            project_root=str(project),
            product_root=str(root),
        )
        effective_project = Path(result.get("project_root") or project).resolve()
        result = LifecycleService(root, config_path=config).persist_plan(
            result, project_root=effective_project, session_id=session_id or None
        )
        return json.dumps(result, ensure_ascii=False, indent=2)
    except (ValueError, json.JSONDecodeError) as exc:
        return _json_error(exc)


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
        result = ArtifactResolver(_product_root(product_root)).resolve(role, scope=scope)
        return json.dumps(result, ensure_ascii=False, indent=2)
    except (ArtifactManifestError, ProductPathError) as exc:
        return _json_error(exc)


@mcp.tool()
def validate_esp_idf(
    product_root: str,
    project_root: str = ".",
    compile_commands: str = "",
    build_succeeded: bool = False,
    expected_components_json: str = "[]",
) -> str:
    """Validate a relative ESP-IDF project confined to product_root."""
    from runtime.platforms.esp_idf import EspIdfAdapter
    try:
        project = resolve_product_path(product_root, project_root, kind="directory")
        commands = resolve_product_path(product_root, compile_commands, kind="file") if compile_commands else None
        result = EspIdfAdapter().validate(
            project,
            compile_commands=commands,
            build_succeeded=True if build_succeeded else None,
            expected_components=json.loads(expected_components_json),
        )
        return json.dumps(result, ensure_ascii=False, indent=2)
    except (ValueError, json.JSONDecodeError) as exc:
        return _json_error(exc)


@mcp.tool()
def claim_task(
    product_root: str,
    work_package_id: str,
    task_id: str,
    session_id: str,
    project_root: str = "",
    expected_revision: int = -1,
    config_path: str = "",
    workspace: bool = False,
) -> str:
    """Atomically claim a persisted Task through LifecycleService."""
    from runtime.lifecycle_service import LifecycleError, LifecycleService
    try:
        root = _product_root(product_root)
        project = resolve_product_path(root, project_root, kind="directory") if project_root else None
        config = resolve_product_path(root, config_path, kind="file") if config_path else None
        result = LifecycleService(root, config_path=config).claim_task(
            work_package_id,
            task_id,
            session_id=session_id,
            project_root=project,
            expected_revision=None if expected_revision < 0 else expected_revision,
            workspace=workspace,
        )
        return json.dumps(result, ensure_ascii=False, indent=2)
    except (LifecycleError, ProductPathError, ValueError) as exc:
        return _json_error(exc)


@mcp.tool()
def promote_task(product_root: str, work_package_id: str, task_id: str, claim_id: str) -> str:
    """Promote one finalized governed claim workspace."""
    from runtime.lifecycle_service import LifecycleError, LifecycleService
    try:
        result = LifecycleService(_product_root(product_root)).promote_task(
            work_package_id, task_id, claim_id=claim_id,
        )
        return json.dumps(result, ensure_ascii=False, indent=2)
    except (LifecycleError, ProductPathError, ValueError) as exc:
        return _json_error(exc)


@mcp.tool()
def route_esp_skill(
    request: str,
    product_root: str,
    skill_root: str = "",
    platform: str = "",
) -> str:
    """Route an optional skill directory confined to product_root."""
    from runtime.esp_dev_skill import EspDevSkillError, EspDevSkillRouter
    try:
        product = _product_root(product_root)
        skill = resolve_product_path(product, skill_root, kind="directory") if skill_root else None
        result = EspDevSkillRouter(skill, product_root=product).route(request, platform=platform or None)
        return json.dumps(result, ensure_ascii=False, indent=2)
    except (EspDevSkillError, ProductPathError) as exc:
        return _json_error(exc)


@mcp.tool()
def session_bootstrap(product_root: str, agent: str = "auto", mode: str = "production") -> str:
    """Validate the host entry file and record a session runtime handshake."""
    from runtime.session_bootstrap import SessionBootstrap, SessionBootstrapError
    try:
        result = SessionBootstrap().start(_product_root(product_root), agent=agent, mode=mode)
        return json.dumps(result, ensure_ascii=False, indent=2)
    except (SessionBootstrapError, ProductPathError) as exc:
        return _json_error(exc)


@mcp.tool()
def search_project_memory(product_root: str, query: str, platform: str = "", limit: int = 5) -> str:
    """Search project-scoped memories without accessing public knowledge."""
    from runtime.memory import ProjectMemoryStore
    try:
        result = ProjectMemoryStore(_product_root(product_root)).search(query, platform=platform or None, limit=limit)
        return json.dumps(result, ensure_ascii=False, indent=2)
    except ProductPathError as exc:
        return _json_error(exc)


@mcp.tool()
def approve_project_memory(product_root: str, memory_id: str, reviewer: str, notes: str = "") -> str:
    """Approve a project memory for project-level reuse."""
    from runtime.memory import ProjectMemoryError, ProjectMemoryStore
    try:
        result = ProjectMemoryStore(_product_root(product_root)).approve(memory_id, reviewer=reviewer, notes=notes)
        return json.dumps(result, ensure_ascii=False, indent=2)
    except (ProjectMemoryError, ProductPathError) as exc:
        return _json_error(exc)


@mcp.tool()
def promote_project_memory(product_root: str, memory_id: str, target: str, reviewer: str) -> str:
    """Promote an approved memory into project common/platform knowledge."""
    from runtime.memory import ProjectMemoryError, ProjectMemoryStore
    try:
        result = ProjectMemoryStore(_product_root(product_root)).promote(memory_id, target=target, reviewer=reviewer)
        return json.dumps(result, ensure_ascii=False, indent=2)
    except (ProjectMemoryError, ProductPathError) as exc:
        return _json_error(exc)


@mcp.tool()
def finalize_task(
    product_root: str,
    work_package_id: str,
    task_id: str,
    session_id: str,
    claim_revision: int,
    model_result_json: str = "{}",
    config_path: str = "",
    verify: bool = True,
    allow_shell_command: bool = False,
) -> str:
    """Finalize a durable Task claim through the authoritative LifecycleService.

    Legacy task/context JSON finalization is intentionally not exposed.
    """
    from runtime.lifecycle_service import LifecycleError, LifecycleService
    from runtime.verifier import BuildVerifier
    try:
        product = _product_root(product_root)
        default_config = product / ".mcu-agent" / "config.yaml"
        config = (
            resolve_product_path(product, config_path, kind="file")
            if config_path
            else default_config if default_config.is_file() else None
        )
        model_result = json.loads(model_result_json)
        if not isinstance(model_result, dict):
            raise ValueError("model_result_json must contain an object")
        result = LifecycleService(product, config_path=config).finalize_task(
            work_package_id,
            task_id,
            session_id=session_id,
            claim_revision=claim_revision,
            model_result=model_result,
            verifier=(
                BuildVerifier(config, allow_shell_command=allow_shell_command)
                if verify
                else None
            ),
        )
        return json.dumps(result, ensure_ascii=False, indent=2)
    except (json.JSONDecodeError, LifecycleError, ProductPathError, ValueError) as exc:
        return _json_error(exc)


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
        {"name": "parse_ioc", "description": "[experimental] 解析 product_root 内的 CubeMX .ioc"},
        {"name": "parse_netlist", "description": "[experimental] 解析 product_root 内的网表"},
        {"name": "collect_docs", "description": "收集项目文档"},
        {"name": "extract_pdf", "description": "提取 PDF 文本"},
        {"name": "identify_chips", "description": "识别芯片和模块"},
        {"name": "generate_schematic", "description": "生成硬件接口文件"},
        {"name": "generate_datasheet", "description": "生成芯片手册文件"},
        {"name": "generate_requirements", "description": "生成需求文件"},
        {"name": "plan_work", "description": "薄调用 runtime planner 并由 LifecycleService 持久化"},
        {"name": "review_evidence", "description": "证据审查预算与重复输入去重"},
        {"name": "resolve_artifact", "description": "从 canonical manifest 解析 artifact"},
        {"name": "validate_esp_idf", "description": "验证 ESP-IDF 工程与 component 实际纳入"},
        {"name": "claim_task", "description": "通过 LifecycleService 原子认领持久化 Task"},
        {"name": "finalize_task", "description": "按 WP/task/claim ID 调用 LifecycleService finalize"},
        {"name": "promote_task", "description": "晋升已验证的 claim workspace patch"},
        {"name": "route_esp_skill", "description": "将可选 esp-dev-skill 路由为受限 ESP recipe/API 参考"},
        {"name": "session_bootstrap", "description": "验证宿主入口并记录本次 runtime 会话握手"},
        {"name": "search_project_memory", "description": "检索当前项目范围内的经验记忆"},
        {"name": "approve_project_memory", "description": "人工批准项目经验记忆"},
        {"name": "promote_project_memory", "description": "将已批准记忆提升到项目公共知识目录"},
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
        if not HAS_MCP:
            print("Optional/experimental MCP support is unavailable: install the 'mcp' package", file=sys.stderr)
            raise SystemExit(2)
        print("Starting optional/experimental MCU Agent MCP Server...", file=sys.stderr)
        mcp.run()


if __name__ == "__main__":
    main()
