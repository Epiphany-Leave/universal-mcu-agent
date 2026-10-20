# Universal MCU Agent Platform installer/updater (Windows PowerShell / PowerShell)
# Managed runtime files are refreshed; user-owned data is never removed by -Force.

param(
    [ValidateSet("claude-code", "codex", "opencode", "cursor", "pi", "all")]
    [string]$Agent = "claude-code",
    [string]$Project = "",
    [string]$EspDevSkillPath = "",
    [switch]$CleanLegacyClaude,
    [switch]$Force,
    [switch]$Update
)

$ErrorActionPreference = "Stop"
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$CoreDir = Join-Path $ScriptDir "core"
$PlatformVersion = (Get-Content (Join-Path $ScriptDir "VERSION") -Raw).Trim()
$Python = if ($env:PYTHON) { $env:PYTHON } elseif (Get-Command python -ErrorAction SilentlyContinue) { "python" } elseif (Get-Command python3 -ErrorAction SilentlyContinue) { "python3" } else { $null }

if (-not $Python) { throw "Python 3.10+ is required" }
& $Python -c "import sys; raise SystemExit(sys.version_info < (3, 10))"
if ($LASTEXITCODE -ne 0) { throw "Python 3.10+ is required" }
& $Python -c "import yaml, jsonschema"
if ($LASTEXITCODE -ne 0) { throw "Missing core Python dependencies. Run: $Python -m pip install '$ScriptDir'" }

if ($Agent -ne "claude-code" -or $Project) {
    if (-not $Project) { throw "Agent '$Agent' requires -Project" }
    if (-not (Test-Path -LiteralPath $Project -PathType Container)) { throw "Project does not exist: $Project" }
    $Project = (Resolve-Path -LiteralPath $Project).Path
}

foreach ($Name in @("runtime", "tools", "workflows", "templates", "config", "registry", "schemas", "analysis", "knowledge")) {
    if (-not (Test-Path -LiteralPath (Join-Path $CoreDir $Name) -PathType Container)) {
        throw "Missing installer source: core/$Name"
    }
}
$UnifiedEntry = Join-Path $ScriptDir "adapters/common/runtime-entry.md"
if (-not (Test-Path -LiteralPath $UnifiedEntry -PathType Leaf)) { throw "Missing unified runtime entry" }

function New-ReleaseTimestamp {
    $Utc = (Get-Date).ToUniversalTime().ToString("yyyyMMddTHHmmssfffZ")
    return "$Utc-$PID-$([Guid]::NewGuid().ToString('N').Substring(0, 8))"
}

function Backup-UserData {
    param([string]$AgentDir, [string]$Stamp)
    $Backup = Join-Path $AgentDir "backups/$Stamp"
    New-Item -ItemType Directory -Path $Backup -Force | Out-Null
    foreach ($Name in @("state", "custom", "config.yaml", "artifact-manifest.yaml", "project-contract.json", "policy.lock")) {
        $Source = Join-Path $AgentDir $Name
        if (Test-Path -LiteralPath $Source) { Copy-Item -LiteralPath $Source -Destination (Join-Path $Backup $Name) -Recurse -Force }
    }
    $ProjectKnowledge = Join-Path $AgentDir "knowledge/project"
    if (Test-Path -LiteralPath $ProjectKnowledge) {
        New-Item -ItemType Directory -Path (Join-Path $Backup "knowledge") -Force | Out-Null
        Copy-Item -LiteralPath $ProjectKnowledge -Destination (Join-Path $Backup "knowledge/project") -Recurse -Force
    }
    $LegacyBackup = Join-Path $Backup "legacy-runtime-state"
    foreach ($Name in @("intents", "work-packages", "claims", "attestations", "sessions", "memories", "phase-state", "phase-state.json", "evidence-debts", "evidence-debts.json", "assumptions", "engineering-assumptions.json", "ledger", "capabilities.json", "execution-ledger.json")) {
        $Source = Join-Path $AgentDir "runtime/$Name"
        if (Test-Path -LiteralPath $Source) {
            New-Item -ItemType Directory -Path $LegacyBackup -Force | Out-Null
            Copy-Item -LiteralPath $Source -Destination (Join-Path $LegacyBackup $Name) -Recurse -Force
        }
    }
    Write-Host "  [BACKUP] $Backup" -ForegroundColor Green
}

function Get-FileRevision {
    param([string]$Path)
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) { return 0 }
    try {
        $Object = Get-Content -LiteralPath $Path -Raw -Encoding UTF8 | ConvertFrom-Json
        if ($null -ne $Object.revision) { return [int]$Object.revision }
        if ($null -ne $Object.metadata -and $null -ne $Object.metadata.revision) { return [int]$Object.metadata.revision }
    } catch { }
    try {
        $Text = Get-Content -LiteralPath $Path -Raw -Encoding UTF8
        $Match = [regex]::Match($Text, '(?m)^\s*revision\s*:\s*(\d+)\s*$')
        if ($Match.Success) { return [int]$Match.Groups[1].Value }
    } catch { }
    return 0
}

function Move-LegacyFile {
    param([string]$Source, [string]$Destination, [System.Collections.ArrayList]$Actions)
    $Parent = Split-Path -Parent $Destination
    New-Item -ItemType Directory -Path $Parent -Force | Out-Null
    if (-not (Test-Path -LiteralPath $Destination)) {
        $Revision = Get-FileRevision $Source
        Move-Item -LiteralPath $Source -Destination $Destination -Force
        [void]$Actions.Add([ordered]@{ source = $Source; destination = $Destination; action = "moved"; revision = $Revision })
        return
    }
    $LegacyRevision = Get-FileRevision $Source
    $StateRevision = Get-FileRevision $Destination
    if ($LegacyRevision -gt $StateRevision) {
        Remove-Item -LiteralPath $Destination -Recurse -Force
        Move-Item -LiteralPath $Source -Destination $Destination -Force
        $Action = "legacy-replaced-lower-revision"
    } else {
        Remove-Item -LiteralPath $Source -Recurse -Force
        $Action = "state-kept-equal-or-higher-revision"
    }
    [void]$Actions.Add([ordered]@{ source = $Source; destination = $Destination; action = $Action; legacy_revision = $LegacyRevision; state_revision = $StateRevision })
}

function Move-LegacyState {
    param([string]$AgentDir, [string]$Stamp)
    $Legacy = Join-Path $AgentDir "runtime"
    $State = Join-Path $AgentDir "state"
    $Report = Join-Path $State "migration-reports/$Stamp.json"
    New-Item -ItemType Directory -Path (Split-Path -Parent $Report) -Force | Out-Null
    $Actions = New-Object System.Collections.ArrayList
    $Mappings = @(
        [pscustomobject]@{ Source = "intents"; Destination = "intents" }
        [pscustomobject]@{ Source = "work-packages"; Destination = "work-packages" }
        [pscustomobject]@{ Source = "claims"; Destination = "claims" }
        [pscustomobject]@{ Source = "attestations"; Destination = "attestations" }
        [pscustomobject]@{ Source = "sessions"; Destination = "sessions" }
        [pscustomobject]@{ Source = "memories"; Destination = "memories" }
        [pscustomobject]@{ Source = "capabilities.json"; Destination = "capabilities.json" }
        [pscustomobject]@{ Source = "phase-state"; Destination = "phase-state.json" }
        [pscustomobject]@{ Source = "phase-state.json"; Destination = "phase-state.json" }
        [pscustomobject]@{ Source = "evidence-debts"; Destination = "evidence-debts.json" }
        [pscustomobject]@{ Source = "evidence-debts.json"; Destination = "evidence-debts.json" }
        [pscustomobject]@{ Source = "assumptions"; Destination = "engineering-assumptions.json" }
        [pscustomobject]@{ Source = "engineering-assumptions.json"; Destination = "engineering-assumptions.json" }
        [pscustomobject]@{ Source = "ledger"; Destination = "execution-ledger.json" }
        [pscustomobject]@{ Source = "execution-ledger.json"; Destination = "execution-ledger.json" }
    )
    foreach ($Mapping in $Mappings) {
        $Source = Join-Path $Legacy $Mapping.Source
        $Destination = Join-Path $State $Mapping.Destination
        if (-not (Test-Path -LiteralPath $Source)) { continue }
        if (Test-Path -LiteralPath $Source -PathType Container) {
            $DirectoryDestination = $Destination
            if ([IO.Path]::GetExtension($Mapping.Destination) -eq ".json") {
                $DirectoryDestination = Join-Path $State ("legacy/" + $Mapping.Source)
                [void]$Actions.Add([ordered]@{
                    source = $Source
                    destination = $DirectoryDestination
                    action = "legacy-directory-preserved-for-manual-review"
                })
            }
            $PrefixLength = $Source.TrimEnd('\', '/').Length + 1
            foreach ($File in @(Get-ChildItem -LiteralPath $Source -Recurse -Force -File)) {
                $Relative = $File.FullName.Substring($PrefixLength)
                Move-LegacyFile $File.FullName (Join-Path $DirectoryDestination $Relative) $Actions
            }
            if (Test-Path -LiteralPath $Source) { Remove-Item -LiteralPath $Source -Recurse -Force }
        } else {
            Move-LegacyFile $Source $Destination $Actions
        }
    }
    $Status = if ($Actions.Count -gt 0) { "migrated" } else { "no-legacy-state" }
    $Payload = [ordered]@{ schema_version = "1.0"; source = $Legacy; destination = $State; status = $Status; actions = @($Actions) }
    $Payload | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath $Report -Encoding UTF8
    Write-Host "  [MIGRATE] $Report" -ForegroundColor Green
}

function Ensure-UserContent {
    param([string]$ProjectPath, [string]$AgentDir)
    foreach ($Path in @("state", "custom/skills", "custom/workflows", "custom/registry")) {
        New-Item -ItemType Directory -Path (Join-Path $AgentDir $Path) -Force | Out-Null
    }
    foreach ($Path in @("custom/skills/.gitkeep", "custom/workflows/.gitkeep")) {
        $Full = Join-Path $AgentDir $Path
        if (-not (Test-Path -LiteralPath $Full)) { New-Item -ItemType File -Path $Full -Force | Out-Null }
    }
    $CustomRegistry = Join-Path $AgentDir "custom/registry/workflows.yaml"
    $RegistryTemplate = Join-Path $CoreDir "templates/custom-registry-template.yaml"
    if (-not (Test-Path -LiteralPath $CustomRegistry) -and (Test-Path -LiteralPath $RegistryTemplate)) {
        Copy-Item -LiteralPath $RegistryTemplate -Destination $CustomRegistry
    }
    $Config = Join-Path $AgentDir "config.yaml"
    $ConfigTemplate = Join-Path $CoreDir "templates/mcu-config-template.yaml"
    if (-not (Test-Path -LiteralPath $Config) -and (Test-Path -LiteralPath $ConfigTemplate)) {
        $ProjectName = Split-Path $ProjectPath -Leaf
        $Content = (Get-Content -LiteralPath $ConfigTemplate -Raw -Encoding UTF8).Replace('<项目名称>', $ProjectName)
        Set-Content -LiteralPath $Config -Value $Content -Encoding UTF8
    }
    $ArtifactManifest = Join-Path $AgentDir "artifact-manifest.yaml"
    if (-not (Test-Path -LiteralPath $ArtifactManifest -PathType Leaf)) {
        Copy-Item -LiteralPath (Join-Path $CoreDir "templates/artifact-manifest-template.yaml") -Destination $ArtifactManifest
    }
    $ProjectKnowledge = Join-Path $AgentDir "knowledge/project"
    if (-not (Test-Path -LiteralPath $ProjectKnowledge)) {
        New-Item -ItemType Directory -Path (Split-Path -Parent $ProjectKnowledge) -Force | Out-Null
        Copy-Item -LiteralPath (Join-Path $CoreDir "knowledge/project") -Destination $ProjectKnowledge -Recurse
    }
    if ($EspDevSkillPath) {
        $SkillSource = (Resolve-Path -LiteralPath $EspDevSkillPath).Path
        if (-not (Test-Path -LiteralPath (Join-Path $SkillSource "SKILL.md") -PathType Leaf)) { throw "EspDevSkillPath must contain SKILL.md" }
        $SkillDestination = Join-Path $AgentDir "skills/esp-dev-skill"
        if (-not (Test-Path -LiteralPath $SkillDestination)) {
            New-Item -ItemType Directory -Path (Split-Path -Parent $SkillDestination) -Force | Out-Null
            Copy-Item -LiteralPath $SkillSource -Destination $SkillDestination -Recurse
        } else {
            Write-Host "  [KEEP] skills/esp-dev-skill" -ForegroundColor Gray
        }
    }
}

function Replace-ManagedContent {
    param([string]$AgentDir)
    $Staging = Join-Path $AgentDir (".install-staging." + [guid]::NewGuid().ToString("N"))
    New-Item -ItemType Directory -Path $Staging -Force | Out-Null
    foreach ($Name in @("runtime", "tools", "workflows", "templates", "config", "registry", "schemas", "analysis")) {
        $Source = Join-Path $CoreDir $Name
        $Destination = Join-Path $Staging $Name
        & $Python -c "import shutil,sys; shutil.copytree(sys.argv[1], sys.argv[2], ignore=shutil.ignore_patterns('__pycache__','*.pyc','*.pyc.*','*.pyo','.pytest_cache'))" $Source $Destination
        if ($LASTEXITCODE -ne 0) { throw "Failed to stage managed directory: $Name" }
    }
    Copy-Item -LiteralPath $UnifiedEntry -Destination (Join-Path $Staging "agent-entry.md")
    foreach ($Name in @("runtime", "tools", "workflows", "templates", "config", "registry", "schemas", "analysis")) {
        $Destination = Join-Path $AgentDir $Name
        if (Test-Path -LiteralPath $Destination) { Remove-Item -LiteralPath $Destination -Recurse -Force }
        Move-Item -LiteralPath (Join-Path $Staging $Name) -Destination $Destination
        Write-Host "  [MANAGED] $Name/" -ForegroundColor Green
    }
    Move-Item -LiteralPath (Join-Path $Staging "agent-entry.md") -Destination (Join-Path $AgentDir "agent-entry.md") -Force
    Remove-Item -LiteralPath $Staging -Recurse -Force

    $KnowledgeDestination = Join-Path $AgentDir "knowledge"
    New-Item -ItemType Directory -Path $KnowledgeDestination -Force | Out-Null
    foreach ($Item in @(Get-ChildItem -LiteralPath $KnowledgeDestination -Force)) {
        if ($Item.Name -ne "project") { Remove-Item -LiteralPath $Item.FullName -Recurse -Force }
    }
    foreach ($Item in @(Get-ChildItem -LiteralPath (Join-Path $CoreDir "knowledge") -Force)) {
        if ($Item.Name -ne "project") { Copy-Item -LiteralPath $Item.FullName -Destination (Join-Path $KnowledgeDestination $Item.Name) -Recurse -Force }
    }
    Copy-Item -LiteralPath (Join-Path $CoreDir "knowledge/VERSION") -Destination (Join-Path $KnowledgeDestination "VERSION") -Force
    Copy-Item -LiteralPath (Join-Path $CoreDir "knowledge/CHANGELOG.md") -Destination (Join-Path $KnowledgeDestination "CHANGELOG.md") -Force
    Write-Host "  [MANAGED] knowledge/platform/ and non-project knowledge" -ForegroundColor Green
}

function Test-RuntimeSmoke {
    param([string]$AgentDir)
    foreach ($Name in @("runtime", "tools", "workflows", "templates", "config", "registry", "schemas", "analysis", "knowledge")) {
        if (-not (Test-Path -LiteralPath (Join-Path $AgentDir $Name) -PathType Container)) { throw "Smoke test failed: missing $Name" }
    }
    $Cli = Join-Path $AgentDir "runtime/cli.py"
    if (-not (Test-Path -LiteralPath $Cli -PathType Leaf)) { throw "Smoke test failed: runtime/cli.py missing" }
    if (-not (Test-Path -LiteralPath (Join-Path $AgentDir "agent-entry.md") -PathType Leaf)) { throw "Smoke test failed: agent-entry.md missing" }
    if (-not (Test-Path -LiteralPath (Join-Path $AgentDir "config.yaml") -PathType Leaf)) { throw "Smoke test failed: config.yaml missing" }
    if (-not (Test-Path -LiteralPath (Join-Path $AgentDir "artifact-manifest.yaml") -PathType Leaf)) { throw "Smoke test failed: artifact-manifest.yaml missing" }
    $OldBytecode = $env:PYTHONDONTWRITEBYTECODE
    $OldPythonPath = $env:PYTHONPATH
    $env:PYTHONDONTWRITEBYTECODE = "1"
    $env:PYTHONPATH = $AgentDir
    try {
        & $Python $Cli --help | Out-Null
        if ($LASTEXITCODE -ne 0) { throw "runtime CLI exited $LASTEXITCODE" }
        & $Python -c 'from runtime.workflow_registry import WorkflowRegistry as W; r=W(); r.descriptor(next(iter(list(r.payload.values())[1])))' | Out-Null
        if ($LASTEXITCODE -ne 0) { throw "workflow policy smoke exited $LASTEXITCODE" }
    }
    finally { $env:PYTHONDONTWRITEBYTECODE = $OldBytecode; $env:PYTHONPATH = $OldPythonPath }
    Write-Host "  [SMOKE] runtime CLI and workflow policy OK" -ForegroundColor Green
}

function Set-ManagedAgentsBlock {
    param([string]$Path)
    $Begin = "<!-- BEGIN UNIVERSAL MCU AGENT -->"
    $End = "<!-- END UNIVERSAL MCU AGENT -->"
    $Block = "$Begin`nRead and follow ``.mcu-agent/agent-entry.md`` for all Universal MCU Agent work.`n$End"
    $Text = if (Test-Path -LiteralPath $Path) { Get-Content -LiteralPath $Path -Raw -Encoding UTF8 } else { "" }
    $BeginCount = ([regex]::Matches($Text, [regex]::Escape($Begin))).Count
    $EndCount = ([regex]::Matches($Text, [regex]::Escape($End))).Count
    if ($BeginCount -ne $EndCount) { throw "Malformed managed block in $Path" }
    $Pattern = '(?s)' + [regex]::Escape($Begin) + '.*?' + [regex]::Escape($End)
    if ($BeginCount -gt 0) {
        $ManagedRegex = New-Object System.Text.RegularExpressions.Regex($Pattern)
        $First = $ManagedRegex.Match($Text)
        $Prefix = $Text.Substring(0, $First.Index)
        $Suffix = $Text.Substring($First.Index + $First.Length)
        $Suffix = $ManagedRegex.Replace($Suffix, "")
        $Text = $Prefix + $Block + $Suffix
    } else {
        $Text = $Text.TrimEnd()
        if ($Text) { $Text += "`n`n" }
        $Text += $Block
    }
    $Parent = Split-Path -Parent $Path
    if ($Parent) { New-Item -ItemType Directory -Path $Parent -Force | Out-Null }
    Set-Content -LiteralPath $Path -Value ($Text.TrimEnd() + "`n") -Encoding UTF8
    Write-Host "  [ENTRY] managed block: $Path" -ForegroundColor Green
}

function Set-ManagedGitIgnore {
    param([string]$ProjectPath)
    $Path = Join-Path $ProjectPath ".gitignore"
    $Begin = "# BEGIN UNIVERSAL MCU AGENT"
    $End = "# END UNIVERSAL MCU AGENT"
    $Block = "$Begin`n.mcu-agent/state/`n.mcu-agent/workspaces/`n.mcu-agent/backups/`n.mcu-agent/.install-staging.*/`n$End"
    $Text = if (Test-Path -LiteralPath $Path) { Get-Content -LiteralPath $Path -Raw -Encoding UTF8 } else { "" }
    if (([regex]::Matches($Text, [regex]::Escape($Begin))).Count -ne ([regex]::Matches($Text, [regex]::Escape($End))).Count) {
        throw "Malformed Universal MCU Agent gitignore block in $Path"
    }
    $Pattern = '(?s)' + [regex]::Escape($Begin) + '.*?' + [regex]::Escape($End)
    if ([regex]::IsMatch($Text, $Pattern)) { $Text = [regex]::Replace($Text, $Pattern, $Block) }
    else { $Text = $Text.TrimEnd(); if ($Text) { $Text += "`n`n" }; $Text += $Block }
    Set-Content -LiteralPath $Path -Value ($Text.TrimEnd() + "`n") -Encoding UTF8
    Write-Host "  [GITIGNORE] managed state/workspace block: $Path" -ForegroundColor Green
}

function Install-PiSkill {
    param([string]$ProjectPath)
    $Source = Join-Path $ScriptDir "adapters/pi/skills/mcu-agent"
    $Destination = Join-Path $ProjectPath ".agents/skills/mcu-agent"
    if (-not (Test-Path -LiteralPath (Join-Path $Source "SKILL.md") -PathType Leaf)) { throw "Missing pi MCU Skill source" }
    New-Item -ItemType Directory -Path (Split-Path -Parent $Destination) -Force | Out-Null
    if (Test-Path -LiteralPath $Destination) { Remove-Item -LiteralPath $Destination -Recurse -Force }
    Copy-Item -LiteralPath $Source -Destination $Destination -Recurse
    Write-Host "  [SKILL] $Destination" -ForegroundColor Green
}

function Copy-ThinEntry {
    param([string]$Destination)
    $Begin = "<!-- BEGIN UNIVERSAL MCU AGENT ENTRY -->"
    $End = "<!-- END UNIVERSAL MCU AGENT ENTRY -->"
    $Body = (Get-Content -LiteralPath $UnifiedEntry -Raw -Encoding UTF8).Trim()
    $Block = "$Begin`n$Body`n$End"
    $Text = if (Test-Path -LiteralPath $Destination) { Get-Content -LiteralPath $Destination -Raw -Encoding UTF8 } else { "" }
    if (([regex]::Matches($Text, [regex]::Escape($Begin))).Count -ne ([regex]::Matches($Text, [regex]::Escape($End))).Count) {
        throw "Malformed managed entry block in $Destination"
    }
    $Pattern = '(?s)' + [regex]::Escape($Begin) + '.*?' + [regex]::Escape($End)
    if ([regex]::IsMatch($Text, $Pattern)) { $Text = [regex]::Replace($Text, $Pattern, $Block) }
    elseif ($Text.TrimStart().StartsWith("# Universal MCU Agent Runtime Entry")) { $Text = $Block }
    else { $Text = $Text.TrimEnd(); if ($Text) { $Text += "`n`n" }; $Text += $Block }
    New-Item -ItemType Directory -Path (Split-Path -Parent $Destination) -Force | Out-Null
    Set-Content -LiteralPath $Destination -Value ($Text.TrimEnd() + "`n") -Encoding UTF8
    Write-Host "  [ENTRY] managed block: $Destination" -ForegroundColor Green
}

function Assert-SafeAgentTree {
    param([string]$ProjectPath)
    $AgentDir = Join-Path $ProjectPath ".mcu-agent"
    $ManagedPaths = @(
        $AgentDir,
        (Join-Path $ProjectPath ".agents"),
        (Join-Path $ProjectPath ".agents\skills"),
        (Join-Path $ProjectPath ".agents\skills\mcu-agent"),
        (Join-Path $ProjectPath ".opencode"),
        (Join-Path $ProjectPath ".opencode\instructions"),
        (Join-Path $ProjectPath ".opencode\instructions\mcu-agent.md"),
        (Join-Path $ProjectPath "AGENTS.md"),
        (Join-Path $ProjectPath "CLAUDE.md"),
        (Join-Path $ProjectPath ".cursorrules"),
        (Join-Path $ProjectPath ".gitignore")
    )
    foreach ($Path in $ManagedPaths) {
        $Item = Get-Item -LiteralPath $Path -Force -ErrorAction SilentlyContinue
        if ($null -ne $Item -and (($Item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0)) {
            throw "Refusing installer operation with linked/reparse managed path: $Path"
        }
    }
    $RootItem = Get-Item -LiteralPath $AgentDir -Force -ErrorAction SilentlyContinue
    if ($null -eq $RootItem) { return }
    if (-not $RootItem.PSIsContainer) {
        throw "Refusing installer operation through non-directory .mcu-agent: $AgentDir"
    }
    foreach ($Item in @(Get-ChildItem -LiteralPath $AgentDir -Force -Recurse -ErrorAction Stop)) {
        if (($Item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
            throw "Refusing installer operation with linked/reparse entry: $($Item.FullName)"
        }
    }
}

function Deploy-Project {
    param([string]$ProjectPath)
    $AgentDir = Join-Path $ProjectPath ".mcu-agent"
    Assert-SafeAgentTree $ProjectPath
    $Stamp = New-ReleaseTimestamp
    $Existed = Test-Path -LiteralPath $AgentDir -PathType Container
    New-Item -ItemType Directory -Path $AgentDir -Force | Out-Null
    $VersionPath = Join-Path $AgentDir "VERSION"
    if ($Update -and -not (Test-Path -LiteralPath $VersionPath -PathType Leaf)) {
        throw "$AgentDir is not an installed project"
    }
    if ((Test-Path -LiteralPath $VersionPath -PathType Leaf) -and -not $Update -and -not $Force) {
        Write-Host "  [KEEP] managed runtime already installed; use -Update or -Force to refresh" -ForegroundColor Gray
        Test-RuntimeSmoke $AgentDir
        return
    }
    if ($Existed -or $Update -or $Force) { Backup-UserData $AgentDir $Stamp }
    Move-LegacyState $AgentDir $Stamp
    Ensure-UserContent $ProjectPath $AgentDir
    Replace-ManagedContent $AgentDir
    try { Test-RuntimeSmoke $AgentDir }
    catch { Write-Error "$_; VERSION was not updated"; throw }
    Set-Content -LiteralPath $VersionPath -Value $PlatformVersion -Encoding UTF8
    Write-Host "  [VERSION] $PlatformVersion" -ForegroundColor Green
}

function Remove-LegacyClaude {
    $ClaudeHome = Join-Path $env:USERPROFILE ".claude"
    Remove-Item -LiteralPath (Join-Path $ClaudeHome "skills/mcu") -Recurse -Force -ErrorAction SilentlyContinue
    foreach ($Name in @("mcu-build", "mcu-code-qa", "mcu-datasheet", "mcu-entry", "mcu-firmware", "mcu-orchestrator", "mcu-project-setup", "mcu-requirements", "mcu-schematic")) {
        Remove-Item -LiteralPath (Join-Path $ClaudeHome "commands/$Name.md") -Force -ErrorAction SilentlyContinue
    }
}

function Install-AgentEntry {
    param([string]$Name, [string]$ProjectPath)
    if ($ProjectPath) { Set-ManagedGitIgnore $ProjectPath }
    switch ($Name) {
        "codex" { Set-ManagedAgentsBlock (Join-Path $ProjectPath "AGENTS.md"); break }
        "pi" {
            Set-ManagedAgentsBlock (Join-Path $ProjectPath "AGENTS.md")
            Install-PiSkill $ProjectPath
            break
        }
        "claude-code" {
            if ($ProjectPath) { Copy-ThinEntry (Join-Path $ProjectPath "CLAUDE.md") }
            else { Copy-ThinEntry (Join-Path $env:USERPROFILE ".claude/CLAUDE.md") }
            break
        }
        "opencode" { Copy-ThinEntry (Join-Path $ProjectPath ".opencode/instructions/mcu-agent.md"); break }
        "cursor" { Copy-ThinEntry (Join-Path $ProjectPath ".cursorrules"); break }
    }
}

if ($Project) { Assert-SafeAgentTree $Project }
Write-Host "Universal MCU Agent Platform v$PlatformVersion - $Agent" -ForegroundColor Cyan
if ($Force) { Write-Host "  [INFO] -Force refreshes managed files only; user data remains protected" -ForegroundColor Gray }
if ($CleanLegacyClaude) { Remove-LegacyClaude }

if ($Agent -eq "claude-code" -and -not $Project) {
    Install-AgentEntry "claude-code" ""
} elseif ($Agent -eq "all") {
    # Deploy once; pi and Codex share one managed AGENTS.md entry.
    Deploy-Project $Project
    Set-ManagedAgentsBlock (Join-Path $Project "AGENTS.md")
    Set-ManagedGitIgnore $Project
    Install-PiSkill $Project
    Copy-ThinEntry (Join-Path $Project "CLAUDE.md")
    Copy-ThinEntry (Join-Path $Project ".opencode/instructions/mcu-agent.md")
    Copy-ThinEntry (Join-Path $Project ".cursorrules")
} else {
    Deploy-Project $Project
    Install-AgentEntry $Agent $Project
}

Write-Host "Operation complete." -ForegroundColor Green
