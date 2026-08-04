# Claude Code Adapter 安装脚本
# 用法: .\install.ps1 [-Project "D:\MyProject"] [-EspDevSkillPath "D:\skills\esp-dev-skill"] [-Force]

param(
    [string]$Project = "",
    [string]$EspDevSkillPath = "",
    [switch]$CleanLegacyClaude,
    [switch]$Force
)

$ErrorActionPreference = "Stop"
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$RootDir = Split-Path -Parent (Split-Path -Parent $ScriptDir)

# 调用统一安装脚本
& "$RootDir\install.ps1" -Agent claude-code -Project $Project -EspDevSkillPath $EspDevSkillPath -CleanLegacyClaude:$CleanLegacyClaude -Force:$Force
