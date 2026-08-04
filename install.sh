#!/usr/bin/env bash
# Universal MCU Agent Platform — 安装/更新脚本 (Linux/Mac)
# 用法:
#   ./install.sh --agent claude-code                          # 安装
#   ./install.sh --agent codex --project /path/to/project     # 安装（项目级）
#   ./install.sh --agent all --project /path --update         # 更新

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CORE_DIR="$SCRIPT_DIR/core"
PLATFORM_VERSION=$(cat "$SCRIPT_DIR/VERSION" | tr -d '[:space:]')

AGENT="claude-code"
PROJECT=""
FORCE=false
UPDATE=false
CLEAN_LEGACY_CLAUDE=false

while [[ $# -gt 0 ]]; do
    case $1 in
        --agent)   AGENT="$2"; shift 2 ;;
        --project) PROJECT="$2"; shift 2 ;;
        --force)   FORCE=true; shift ;;
        --update)  UPDATE=true; shift ;;
        --clean-legacy-claude) CLEAN_LEGACY_CLAUDE=true; shift ;;
        -h|--help)
            echo "用法: ./install.sh --agent <claude-code|codex|opencode|cursor|all> [--project <path>] [--force] [--update] [--clean-legacy-claude]"
            exit 0
            ;;
        *) echo "Unknown option: $1"; exit 1 ;;
    esac
done

echo ""
echo "╔══════════════════════════════════════════════════════════╗"
echo "║        Universal MCU Agent Platform v$PLATFORM_VERSION          ║"
echo "╚══════════════════════════════════════════════════════════╝"
echo ""
echo "  Agent:   $AGENT"
echo "  Mode:    $($UPDATE && echo "Update" || echo "Install")"
[[ -n "$PROJECT" ]] && echo "  Project: $PROJECT"
echo ""

# ── 辅助函数 ──────────────────────────────────────────────────

copy_dir() {
    local src="$1" dst="$2" label="$3"
    if [[ -d "$dst" ]]; then
        if $FORCE; then rm -rf "$dst"; else echo "  [SKIP] $label (exists)"; return; fi
    fi
    if [[ ! -d "$src" ]]; then echo "  [MISS] $label"; return; fi
    cp -r "$src" "$dst"
    find "$dst" -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
    echo "  [OK]   $label"
}

copy_file() {
    local src="$1" dst="$2" label="$3"
    if [[ -f "$dst" ]] && ! $FORCE; then echo "  [SKIP] $label (exists)"; return; fi
    if [[ ! -f "$src" ]]; then echo "  [MISS] $label"; return; fi
    cp "$src" "$dst"
    echo "  [OK]   $label"
}

# ── Install 模式 ──────────────────────────────────────────────

install_project() {
    local project="$1"
    local agent_dir="$project/.mcu-agent"

    echo "── INSTALL to: $agent_dir ─────────────────────────"
    mkdir -p "$agent_dir"

    echo "[Tools]"
    copy_dir "$CORE_DIR/tools" "$agent_dir/tools" "tools/"

    echo "[Runtime]"
    copy_dir "$CORE_DIR/runtime" "$agent_dir/runtime" "runtime/"
    copy_file "$SCRIPT_DIR/adapters/common/runtime-entry.md" "$agent_dir/agent-entry.md" "agent-entry.md"

    echo "[Workflows]"
    copy_dir "$CORE_DIR/workflows" "$agent_dir/workflows" "workflows/"

    echo "[Knowledge]"
    copy_dir "$CORE_DIR/knowledge" "$agent_dir/knowledge" "knowledge/"

    echo "[Templates]"
    copy_dir "$CORE_DIR/templates" "$agent_dir/templates" "templates/"

    echo "[Config]"
    copy_dir "$CORE_DIR/config" "$agent_dir/config" "config/"

    echo "[Registry]"
    copy_dir "$CORE_DIR/registry" "$agent_dir/registry" "registry/"

    echo "[Schemas]"
    copy_dir "$CORE_DIR/schemas" "$agent_dir/schemas" "schemas/"

    echo "[Custom]"
    if [[ ! -d "$agent_dir/custom" ]]; then
        mkdir -p "$agent_dir/custom/skills" "$agent_dir/custom/workflows" "$agent_dir/custom/registry"
        touch "$agent_dir/custom/skills/.gitkeep" "$agent_dir/custom/workflows/.gitkeep"
        # 复制 registry 模板
        local registry_template="$CORE_DIR/templates/custom-registry-template.yaml"
        if [[ -f "$registry_template" ]]; then
            cp "$registry_template" "$agent_dir/custom/registry/workflows.yaml"
        fi
        echo "  [OK]   custom/ (empty, for user extensions)"
    else
        echo "  [SKIP] custom/ (exists, preserved)"
    fi

    echo "[Analysis]"
    copy_dir "$CORE_DIR/analysis" "$agent_dir/analysis" "analysis/"

    echo "[Config YAML]"
    local config_dst="$agent_dir/config.yaml"
    if [[ ! -f "$config_dst" ]] || $FORCE; then
        local template="$CORE_DIR/templates/mcu-config-template.yaml"
        if [[ -f "$template" ]]; then
            local name=$(basename "$project")
            sed "s/<项目名称>/$name/g" "$template" > "$config_dst"
            echo "  [OK]   config.yaml"
        fi
    else
        echo "  [SKIP] config.yaml (exists)"
    fi

    echo "[Version]"
    echo "$PLATFORM_VERSION" > "$agent_dir/VERSION"
    echo "  [OK]   VERSION ($PLATFORM_VERSION)"

    # Knowledge 版本
    if [[ -f "$CORE_DIR/knowledge/VERSION" ]]; then
        local knowledge_version=$(cat "$CORE_DIR/knowledge/VERSION" | tr -d '[:space:]')
        echo "$knowledge_version" > "$agent_dir/knowledge/VERSION"
        echo "  [OK]   knowledge/VERSION ($knowledge_version)"
    fi
}

# ── Update 模式 ───────────────────────────────────────────────

update_project() {
    local project="$1"
    local agent_dir="$project/.mcu-agent"

    if [[ ! -d "$agent_dir" ]]; then
        echo "  [ERROR] $agent_dir not found. Use Install mode first."
        return
    fi

    local project_version="0.0.0"
    if [[ -f "$agent_dir/VERSION" ]]; then
        project_version=$(cat "$agent_dir/VERSION" | tr -d '[:space:]')
    fi

    echo "── UPDATE: $agent_dir ────────────────────────────"
    echo "  Current:   v$project_version"
    echo "  Available: v$PLATFORM_VERSION"
    echo ""

    # 版本比较
    if [[ "$project_version" == "$PLATFORM_VERSION" ]]; then
        echo "  Already up to date."
        return
    fi

    echo "  Updating..."
    echo ""

    echo "[Update]"
    # 更新：tools（覆盖）
    rm -rf "$agent_dir/tools"
    copy_dir "$CORE_DIR/tools" "$agent_dir/tools" "tools/"

    rm -rf "$agent_dir/runtime"
    copy_dir "$CORE_DIR/runtime" "$agent_dir/runtime" "runtime/"

    # 更新：workflows（覆盖）
    rm -rf "$agent_dir/workflows"
    copy_dir "$CORE_DIR/workflows" "$agent_dir/workflows" "workflows/"

    # 更新：templates（覆盖）
    rm -rf "$agent_dir/templates"
    copy_dir "$CORE_DIR/templates" "$agent_dir/templates" "templates/"

    # 更新：config（覆盖）
    rm -rf "$agent_dir/config"
    copy_dir "$CORE_DIR/config" "$agent_dir/config" "config/"

    # 更新：registry（覆盖）
    rm -rf "$agent_dir/registry"
    copy_dir "$CORE_DIR/registry" "$agent_dir/registry" "registry/"

    # 更新：schemas（覆盖）
    rm -rf "$agent_dir/schemas"
    copy_dir "$CORE_DIR/schemas" "$agent_dir/schemas" "schemas/"

    # 更新：analysis（覆盖）
    rm -rf "$agent_dir/analysis"
    copy_dir "$CORE_DIR/analysis" "$agent_dir/analysis" "analysis/"

    # 更新：VERSION
    echo "$PLATFORM_VERSION" > "$agent_dir/VERSION"
    echo "  [OK]   VERSION ($PLATFORM_VERSION)"

    # 更新：knowledge（保留 project/）
    echo ""
    echo "[Knowledge]"
    local knowledge_version="0.0.0"
    if [[ -f "$CORE_DIR/knowledge/VERSION" ]]; then
        knowledge_version=$(cat "$CORE_DIR/knowledge/VERSION" | tr -d '[:space:]')
    fi
    local project_knowledge_version="0.0.0"
    if [[ -f "$agent_dir/knowledge/VERSION" ]]; then
        project_knowledge_version=$(cat "$agent_dir/knowledge/VERSION" | tr -d '[:space:]')
    fi
    echo "  Current:   v$project_knowledge_version"
    echo "  Available: v$knowledge_version"

    if [[ "$project_knowledge_version" != "$knowledge_version" ]]; then
        # 备份 project/
        local project_knowledge="$agent_dir/knowledge/project"
        local backup="$agent_dir/knowledge_project_backup"
        if [[ -d "$project_knowledge" ]]; then
            rm -rf "$backup"
            cp -r "$project_knowledge" "$backup"
        fi

        # 更新 common/ 和平台目录
        for dir in common stm32 esp32 ch592 protocols mcu peripherals; do
            local src="$CORE_DIR/knowledge/$dir"
            local dst="$agent_dir/knowledge/$dir"
            if [[ -d "$src" ]]; then
                rm -rf "$dst"
                cp -r "$src" "$dst"
                echo "  [OK]   knowledge/$dir/"
            fi
        done

        # 恢复 project/
        if [[ -d "$backup" ]]; then
            rm -rf "$project_knowledge"
            cp -r "$backup" "$project_knowledge"
            rm -rf "$backup"
        fi

        # 更新 VERSION 和 CHANGELOG
        copy_file "$CORE_DIR/knowledge/VERSION" "$agent_dir/knowledge/VERSION" "knowledge/VERSION"
        copy_file "$CORE_DIR/knowledge/CHANGELOG.md" "$agent_dir/knowledge/CHANGELOG.md" "knowledge/CHANGELOG.md"

        echo "  Knowledge updated: v$project_knowledge_version -> v$knowledge_version"
    else
        echo "  Knowledge already up to date."
    fi

    echo ""
    echo "[Preserved]"
    echo "  [KEEP]  knowledge/project/ (project experience preserved)"
    if [[ -d "$agent_dir/custom" ]]; then
        echo "  [KEEP]  custom/ (user extensions preserved)"
    fi
    echo "  [KEEP]  config.yaml (user config preserved)"

    echo ""
    echo "  Update complete: v$project_version -> v$PLATFORM_VERSION"
}

# ── 部署函数 ──────────────────────────────────────────────────

deploy_project() {
    local project="$1"
    if $UPDATE; then
        update_project "$project"
    else
        install_project "$project"
    fi
}

# ── Claude Code ───────────────────────────────────────────────

install_claude_code() {
    echo "── Claude Code ──────────────────────────────────────"
    local claude_home="$HOME/.claude"

    if $CLEAN_LEGACY_CLAUDE; then
        rm -rf "$claude_home/skills/mcu"
        for name in mcu-build mcu-code-qa mcu-datasheet mcu-entry mcu-firmware mcu-orchestrator mcu-project-setup mcu-requirements mcu-schematic; do
            rm -f "$claude_home/commands/$name.md"
        done
        echo "  [CLEAN] legacy Claude mcu skills and commands"
    fi

    if [[ -n "$PROJECT" ]]; then
        echo ""
        deploy_project "$PROJECT"
        copy_file "$SCRIPT_DIR/adapters/common/runtime-entry.md" "$PROJECT/CLAUDE.md" "CLAUDE.md"
    else
        copy_file "$SCRIPT_DIR/adapters/common/runtime-entry.md" "$claude_home/CLAUDE.md" "global CLAUDE.md"
    fi
}

install_codex() {
    echo "── Codex ────────────────────────────────────────────"
    if [[ -z "$PROJECT" ]]; then echo "  [ERROR] Requires --project"; return; fi
    deploy_project "$PROJECT"
    echo "[AGENTS.md]"
    copy_file "$SCRIPT_DIR/adapters/common/runtime-entry.md" "$PROJECT/AGENTS.md" "AGENTS.md"
}

install_opencode() {
    echo "── OpenCode ─────────────────────────────────────────"
    if [[ -z "$PROJECT" ]]; then echo "  [ERROR] Requires --project"; return; fi
    deploy_project "$PROJECT"
    echo "[Instructions]"
    mkdir -p "$PROJECT/.opencode/instructions"
    copy_file "$SCRIPT_DIR/adapters/common/runtime-entry.md" "$PROJECT/.opencode/instructions/mcu-agent.md" "instructions/mcu-agent.md"
}

install_cursor() {
    echo "── Cursor ───────────────────────────────────────────"
    if [[ -z "$PROJECT" ]]; then echo "  [ERROR] Requires --project"; return; fi
    deploy_project "$PROJECT"
    echo "[CursorRules]"
    copy_file "$SCRIPT_DIR/adapters/common/runtime-entry.md" "$PROJECT/.cursorrules" ".cursorrules"
}

# ── 执行 ──────────────────────────────────────────────────────

case $AGENT in
    claude-code) install_claude_code ;;
    codex)       install_codex ;;
    opencode)    install_opencode ;;
    cursor)      install_cursor ;;
    all)
        install_claude_code
        [[ -n "$PROJECT" ]] && { install_codex; install_opencode; install_cursor; }
        ;;
esac

echo ""
echo "╔══════════════════════════════════════════════════════════╗"
echo "║                    Operation Complete                   ║"
echo "╚══════════════════════════════════════════════════════════╝"
echo ""
