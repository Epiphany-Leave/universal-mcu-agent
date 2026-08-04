# Universal MCU Agent Platform — 安装/更新脚本
# 用法:
#   .\install.ps1 -Agent claude-code                          # 安装（全局）
#   .\install.ps1 -Agent codex -Project "D:\MyProject"        # 安装（项目级）
#   .\install.ps1 -Agent all -Project "D:\MyProject" -Update  # 更新（保留项目数据）

param(
    [ValidateSet("claude-code","codex","opencode","cursor","all")]
    [string]$Agent = "claude-code",
    [string]$Project = "",
    [string]$EspDevSkillPath = "",
    [switch]$CleanLegacyClaude,
    [switch]$Force,
    [switch]$Update
)

$ErrorActionPreference = "Stop"
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$CoreDir = "$ScriptDir\core"
$PlatformVersion = (Get-Content "$ScriptDir\VERSION" -Raw).Trim()

Write-Host ""
Write-Host "╔══════════════════════════════════════════════════════════╗" -ForegroundColor Cyan
Write-Host "║        Universal MCU Agent Platform v$PlatformVersion          ║" -ForegroundColor Cyan
Write-Host "╚══════════════════════════════════════════════════════════╝" -ForegroundColor Cyan
Write-Host ""

# ── 版本比较 ──────────────────────────────────────────────────

function Compare-Version {
    param([string]$V1, [string]$V2)
    $a = [version]$V1; $b = [version]$V2
    if ($a -lt $b) { return -1 }
    if ($a -gt $b) { return 1 }
    return 0
}

# ── 辅助函数 ──────────────────────────────────────────────────

function Copy-Dir {
    param([string]$Src, [string]$Dst, [string]$Label)
    if (Test-Path $Dst) {
        if ($Force) { Remove-Item -Recurse -Force $Dst } else {
            Write-Host "  [SKIP] $Label (exists)" -ForegroundColor Gray; return
        }
    }
    if (-not (Test-Path $Src)) {
        Write-Host "  [MISS] $Label" -ForegroundColor Yellow; return
    }
    Copy-Item -Recurse $Src $Dst
    Get-ChildItem -Path $Dst -Recurse -Directory -Filter "__pycache__" -ErrorAction SilentlyContinue | Remove-Item -Recurse -Force
    Write-Host "  [OK]   $Label" -ForegroundColor Green
}

function Copy-Single {
    param([string]$Src, [string]$Dst, [string]$Label)
    if ((Test-Path $Dst) -and -not $Force) {
        Write-Host "  [SKIP] $Label (exists)" -ForegroundColor Gray; return
    }
    if (-not (Test-Path $Src)) {
        Write-Host "  [MISS] $Label" -ForegroundColor Yellow; return
    }
    Copy-Item $Src $Dst -Force
    Write-Host "  [OK]   $Label" -ForegroundColor Green
}

function Remove-LegacyClaude {
    param([string]$ClaudeHome)
    $LegacySkillDir = "$ClaudeHome\skills\mcu"
    if (Test-Path -LiteralPath $LegacySkillDir) {
        Remove-Item -LiteralPath $LegacySkillDir -Recurse -Force
        Write-Host "  [CLEAN] $LegacySkillDir" -ForegroundColor Yellow
    }
    $LegacyCommands = @("mcu-build.md", "mcu-code-qa.md", "mcu-datasheet.md", "mcu-entry.md", "mcu-firmware.md", "mcu-orchestrator.md", "mcu-project-setup.md", "mcu-requirements.md", "mcu-schematic.md")
    foreach ($Name in $LegacyCommands) {
        $Path = "$ClaudeHome\commands\$Name"
        if (Test-Path -LiteralPath $Path) {
            Remove-Item -LiteralPath $Path -Force
            Write-Host "  [CLEAN] $Path" -ForegroundColor Yellow
        }
    }
}

# ── Install 模式：完整部署 ────────────────────────────────────

function Install-Project {
    param([string]$ProjectPath)

    $AgentDir = "$ProjectPath\.mcu-agent"

    Write-Host ""
    Write-Host "── INSTALL to: $AgentDir ─────────────────────────" -ForegroundColor Yellow

    if (-not (Test-Path $AgentDir)) {
        New-Item -ItemType Directory -Path $AgentDir -Force | Out-Null
    }

    Write-Host "[Tools]" -ForegroundColor Cyan
    Copy-Dir "$CoreDir\tools" "$AgentDir\tools" "tools/"

    Write-Host "[Runtime]" -ForegroundColor Cyan
    Copy-Dir "$CoreDir\runtime" "$AgentDir\runtime" "runtime/"
    Copy-Single "$ScriptDir\adapters\common\runtime-entry.md" "$AgentDir\agent-entry.md" "agent-entry.md"

    Write-Host "[Workflows]" -ForegroundColor Cyan
    Copy-Dir "$CoreDir\workflows" "$AgentDir\workflows" "workflows/"

    Write-Host "[Knowledge]" -ForegroundColor Cyan
    Copy-Dir "$CoreDir\knowledge" "$AgentDir\knowledge" "knowledge/"

    Write-Host "[Templates]" -ForegroundColor Cyan
    Copy-Dir "$CoreDir\templates" "$AgentDir\templates" "templates/"

    Write-Host "[Config]" -ForegroundColor Cyan
    Copy-Dir "$CoreDir\config" "$AgentDir\config" "config/"

    Write-Host "[Registry]" -ForegroundColor Cyan
    Copy-Dir "$CoreDir\registry" "$AgentDir\registry" "registry/"

    Write-Host "[Schemas]" -ForegroundColor Cyan
    Copy-Dir "$CoreDir\schemas" "$AgentDir\schemas" "schemas/"

    Write-Host "[Custom]" -ForegroundColor Cyan
    if (-not (Test-Path "$AgentDir\custom")) {
        New-Item -ItemType Directory -Path "$AgentDir\custom\skills" -Force | Out-Null
        New-Item -ItemType Directory -Path "$AgentDir\custom\workflows" -Force | Out-Null
        New-Item -ItemType Directory -Path "$AgentDir\custom\registry" -Force | Out-Null
        New-Item -ItemType File -Path "$AgentDir\custom\skills\.gitkeep" -Force | Out-Null
        New-Item -ItemType File -Path "$AgentDir\custom\workflows\.gitkeep" -Force | Out-Null
        # 复制 registry 模板
        $RegistryTemplate = "$CoreDir\templates\custom-registry-template.yaml"
        if (Test-Path $RegistryTemplate) {
            Copy-Item $RegistryTemplate "$AgentDir\custom\registry\workflows.yaml" -Force
        }
        Write-Host "  [OK]   custom/ (empty, for user extensions)" -ForegroundColor Green
    } else {
        Write-Host "  [SKIP] custom/ (exists, preserved)" -ForegroundColor Gray
    }

    Write-Host "[Analysis]" -ForegroundColor Cyan
    Copy-Dir "$CoreDir\analysis" "$AgentDir\analysis" "analysis/"

    Write-Host "[Config YAML]" -ForegroundColor Cyan
    $ConfigDst = "$AgentDir\config.yaml"
    if ((-not (Test-Path $ConfigDst)) -or $Force) {
        $Template = "$CoreDir\templates\mcu-config-template.yaml"
        if (Test-Path $Template) {
            $ProjectName = Split-Path $ProjectPath -Leaf
            $Content = Get-Content $Template -Raw
            $Content = $Content -replace '<项目名称>', $ProjectName
            Set-Content -Path $ConfigDst -Value $Content -Encoding UTF8
            Write-Host "  [OK]   config.yaml" -ForegroundColor Green
        }
    } else {
        Write-Host "  [SKIP] config.yaml (exists)" -ForegroundColor Gray
    }

    if ($EspDevSkillPath) {
        $SkillSource = (Resolve-Path -LiteralPath $EspDevSkillPath -ErrorAction Stop).Path
        if (-not (Test-Path -LiteralPath "$SkillSource\SKILL.md")) {
            throw "EspDevSkillPath must point to an esp-dev-skill root containing SKILL.md"
        }
        Copy-Dir $SkillSource "$AgentDir\skills\esp-dev-skill" "skills/esp-dev-skill"
    }

    Write-Host "[Version]" -ForegroundColor Cyan
    Set-Content -Path "$AgentDir\VERSION" -Value $PlatformVersion -Encoding UTF8
    Write-Host "  [OK]   VERSION ($PlatformVersion)" -ForegroundColor Green

    # Knowledge 版本
    if (Test-Path "$CoreDir\knowledge\VERSION") {
        $KnowledgeVersion = (Get-Content "$CoreDir\knowledge\VERSION" -Raw).Trim()
        Set-Content -Path "$AgentDir\knowledge\VERSION" -Value $KnowledgeVersion -Encoding UTF8
        Write-Host "  [OK]   knowledge/VERSION ($KnowledgeVersion)" -ForegroundColor Green
    }
}

# ── Update 模式：安全更新 ─────────────────────────────────────

function Update-Project {
    param([string]$ProjectPath)

    $AgentDir = "$ProjectPath\.mcu-agent"

    # 检查项目是否有 .mcu-agent
    if (-not (Test-Path $AgentDir)) {
        Write-Host "  [ERROR] $AgentDir not found. Use Install mode first." -ForegroundColor Red
        return
    }

    # 读取项目当前版本
    $ProjectVersion = "0.0.0"
    if (Test-Path "$AgentDir\VERSION") {
        $ProjectVersion = (Get-Content "$AgentDir\VERSION" -Raw).Trim()
    }

    Write-Host ""
    Write-Host "── UPDATE: $AgentDir ────────────────────────────" -ForegroundColor Yellow
    Write-Host "  Current:  v$ProjectVersion" -ForegroundColor White
    Write-Host "  Available: v$PlatformVersion" -ForegroundColor White
    Write-Host ""

    $cmp = Compare-Version $ProjectVersion $PlatformVersion
    if ($cmp -ge 0) {
        Write-Host "  Already up to date." -ForegroundColor Green
        return
    }

    Write-Host "  Updating..." -ForegroundColor Cyan
    Write-Host ""

    # 更新：tools（覆盖）
    Write-Host "[Update]" -ForegroundColor Cyan
    if (Test-Path "$AgentDir\tools") { Remove-Item -Recurse -Force "$AgentDir\tools" }
    Copy-Dir "$CoreDir\tools" "$AgentDir\tools" "tools/"

    if (Test-Path "$AgentDir\runtime") { Remove-Item -Recurse -Force "$AgentDir\runtime" }
    Copy-Dir "$CoreDir\runtime" "$AgentDir\runtime" "runtime/"

    # 更新：workflows（覆盖）
    if (Test-Path "$AgentDir\workflows") { Remove-Item -Recurse -Force "$AgentDir\workflows" }
    Copy-Dir "$CoreDir\workflows" "$AgentDir\workflows" "workflows/"

    # 更新：templates（覆盖）
    if (Test-Path "$AgentDir\templates") { Remove-Item -Recurse -Force "$AgentDir\templates" }
    Copy-Dir "$CoreDir\templates" "$AgentDir\templates" "templates/"

    # 更新：config（覆盖）
    if (Test-Path "$AgentDir\config") { Remove-Item -Recurse -Force "$AgentDir\config" }
    Copy-Dir "$CoreDir\config" "$AgentDir\config" "config/"

    # 更新：registry（覆盖）
    if (Test-Path "$AgentDir\registry") { Remove-Item -Recurse -Force "$AgentDir\registry" }
    Copy-Dir "$CoreDir\registry" "$AgentDir\registry" "registry/"

    # 更新：schemas（覆盖）
    if (Test-Path "$AgentDir\schemas") { Remove-Item -Recurse -Force "$AgentDir\schemas" }
    Copy-Dir "$CoreDir\schemas" "$AgentDir\schemas" "schemas/"

    # 更新：analysis（覆盖）
    if (Test-Path "$AgentDir\analysis") { Remove-Item -Recurse -Force "$AgentDir\analysis" }
    Copy-Dir "$CoreDir\analysis" "$AgentDir\analysis" "analysis/"

    # 更新：VERSION
    Set-Content -Path "$AgentDir\VERSION" -Value $PlatformVersion -Encoding UTF8
    Write-Host "  [OK]   VERSION ($PlatformVersion)" -ForegroundColor Green

    # 更新：knowledge（保留 project/）
    Write-Host ""
    Write-Host "[Knowledge]" -ForegroundColor Cyan
    $KnowledgeVersion = "0.0.0"
    if (Test-Path "$CoreDir\knowledge\VERSION") {
        $KnowledgeVersion = (Get-Content "$CoreDir\knowledge\VERSION" -Raw).Trim()
    }
    $ProjectKnowledgeVersion = "0.0.0"
    if (Test-Path "$AgentDir\knowledge\VERSION") {
        $ProjectKnowledgeVersion = (Get-Content "$AgentDir\knowledge\VERSION" -Raw).Trim()
    }
    Write-Host "  Current:   v$ProjectKnowledgeVersion" -ForegroundColor White
    Write-Host "  Available: v$KnowledgeVersion" -ForegroundColor White

    $kcmp = Compare-Version $ProjectKnowledgeVersion $KnowledgeVersion
    if ($kcmp -lt 0) {
        # 备份 project/ 目录
        $ProjectKnowledge = "$AgentDir\knowledge\project"
        $ProjectKnowledgeBackup = "$AgentDir\knowledge_project_backup"
        if (Test-Path $ProjectKnowledge) {
            if (Test-Path $ProjectKnowledgeBackup) { Remove-Item -Recurse -Force $ProjectKnowledgeBackup }
            Copy-Item -Recurse $ProjectKnowledge $ProjectKnowledgeBackup
        }

        # 更新 common/ 和平台目录（保留 project/）
        @("common", "stm32", "esp32", "ch592", "protocols", "mcu", "peripherals") | ForEach-Object {
            $SrcDir = "$CoreDir\knowledge\$_"
            $DstDir = "$AgentDir\knowledge\$_"
            if (Test-Path $SrcDir) {
                if (Test-Path $DstDir) { Remove-Item -Recurse -Force $DstDir }
                Copy-Item -Recurse $SrcDir $DstDir
                Write-Host "  [OK]   knowledge/$_/" -ForegroundColor Green
            }
        }

        # 恢复 project/ 目录
        if (Test-Path $ProjectKnowledgeBackup) {
            if (Test-Path $ProjectKnowledge) { Remove-Item -Recurse -Force $ProjectKnowledge }
            Copy-Item -Recurse $ProjectKnowledgeBackup $ProjectKnowledge
            Remove-Item -Recurse -Force $ProjectKnowledgeBackup
        }

        # 更新 VERSION 和 CHANGELOG
        Copy-Single "$CoreDir\knowledge\VERSION" "$AgentDir\knowledge\VERSION" "knowledge/VERSION"
        Copy-Single "$CoreDir\knowledge\CHANGELOG.md" "$AgentDir\knowledge\CHANGELOG.md" "knowledge/CHANGELOG.md"

        Write-Host "  Knowledge updated: v$ProjectKnowledgeVersion -> v$KnowledgeVersion" -ForegroundColor Green
    } else {
        Write-Host "  Knowledge already up to date." -ForegroundColor Green
    }

    Write-Host ""
    Write-Host "[Preserved]" -ForegroundColor Green
    Write-Host "  [KEEP]  knowledge/project/ (project experience preserved)" -ForegroundColor Green

    # 保留：custom/（不覆盖）
    if (Test-Path "$AgentDir\custom") {
        Write-Host "  [KEEP]  custom/ (user extensions preserved)" -ForegroundColor Green
    }

    # 保留：config.yaml（不覆盖）
    Write-Host "  [KEEP]  config.yaml (user config preserved)" -ForegroundColor Green

    Write-Host ""
    Write-Host "  Update complete: v$ProjectVersion -> v$PlatformVersion" -ForegroundColor Green
}

# ── Claude Code 全局部署 ──────────────────────────────────────

function Install-ClaudeCode {
    Write-Host "── Claude Code ──────────────────────────────────────" -ForegroundColor Yellow
    $ClaudeHome = "$env:USERPROFILE\.claude"

    if ($CleanLegacyClaude) {
        Remove-LegacyClaude $ClaudeHome
    }

    Write-Host "[Global]" -ForegroundColor Cyan

    if ($Project) {
        if ($Update) { Update-Project $Project } else { Install-Project $Project }
        Copy-Single "$ScriptDir\adapters\common\runtime-entry.md" "$Project\CLAUDE.md" "CLAUDE.md"
    } else {
        Copy-Single "$ScriptDir\adapters\common\runtime-entry.md" "$ClaudeHome\CLAUDE.md" "global CLAUDE.md"
    }
}

# ── Codex 部署 ────────────────────────────────────────────────

function Install-Codex {
    Write-Host "── Codex ────────────────────────────────────────────" -ForegroundColor Yellow
    if (-not $Project) {
        Write-Host "  [ERROR] Codex requires -Project parameter" -ForegroundColor Red; return
    }

    if ($Update) {
        Update-Project $Project
    } else {
        Install-Project $Project
    }

    Write-Host "[AGENTS.md]" -ForegroundColor Cyan
    $Src = "$ScriptDir\adapters\common\runtime-entry.md"
    Copy-Single $Src "$Project\AGENTS.md" "AGENTS.md"
}

# ── OpenCode 部署 ─────────────────────────────────────────────

function Install-OpenCode {
    Write-Host "── OpenCode ─────────────────────────────────────────" -ForegroundColor Yellow
    if (-not $Project) {
        Write-Host "  [ERROR] OpenCode requires -Project parameter" -ForegroundColor Red; return
    }

    if ($Update) {
        Update-Project $Project
    } else {
        Install-Project $Project
    }

    Write-Host "[Instructions]" -ForegroundColor Cyan
    $Src = "$ScriptDir\adapters\common\runtime-entry.md"
    $Dst = "$Project\.opencode\instructions"
    if (-not (Test-Path $Dst)) { New-Item -ItemType Directory -Path $Dst -Force | Out-Null }
    Copy-Single $Src "$Dst\mcu-agent.md" "instructions/mcu-agent.md"
}

# ── Cursor 部署 ───────────────────────────────────────────────

function Install-Cursor {
    Write-Host "── Cursor ───────────────────────────────────────────" -ForegroundColor Yellow
    if (-not $Project) {
        Write-Host "  [ERROR] Cursor requires -Project parameter" -ForegroundColor Red; return
    }

    if ($Update) {
        Update-Project $Project
    } else {
        Install-Project $Project
    }

    Write-Host "[CursorRules]" -ForegroundColor Cyan
    $Src = "$ScriptDir\adapters\common\runtime-entry.md"
    Copy-Single $Src "$Project\.cursorrules" ".cursorrules"
}

# ── 执行 ──────────────────────────────────────────────────────

switch ($Agent) {
    "claude-code" { Install-ClaudeCode }
    "codex"       { Install-Codex }
    "opencode"    { Install-OpenCode }
    "cursor"      { Install-Cursor }
    "all" {
        Install-ClaudeCode
        if ($Project) {
            Install-Codex
            Install-OpenCode
            Install-Cursor
        }
    }
}

Write-Host ""
Write-Host "╔══════════════════════════════════════════════════════════╗" -ForegroundColor Green
Write-Host "║                    Operation Complete                   ║" -ForegroundColor Green
Write-Host "╚══════════════════════════════════════════════════════════╝" -ForegroundColor Green
Write-Host ""
