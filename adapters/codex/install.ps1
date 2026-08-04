# Codex Adapter 安装脚本
# 用法: .\install.ps1 -Project "D:\MyProject" [-Force]

param(
    [Parameter(Mandatory=$true)]
    [string]$Project,
    [string]$EspDevSkillPath = "",
    [switch]$Force
)

$ErrorActionPreference = "Stop"
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$RootDir = Split-Path -Parent (Split-Path -Parent $ScriptDir)

& "$RootDir\install.ps1" -Agent codex -Project $Project -EspDevSkillPath $EspDevSkillPath -Force:$Force
