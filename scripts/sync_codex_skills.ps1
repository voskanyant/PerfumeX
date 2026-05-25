param(
    [string]$ProjectSkillsPath = (Join-Path (Resolve-Path (Join-Path $PSScriptRoot "..")) "codex-skills"),
    [string]$CodexSkillsPath = (Join-Path $env:USERPROFILE ".codex\skills")
)

$ErrorActionPreference = "Stop"

if (!(Test-Path -LiteralPath $ProjectSkillsPath)) {
    throw "Project skills folder not found: $ProjectSkillsPath"
}

if (!(Test-Path -LiteralPath $CodexSkillsPath)) {
    New-Item -ItemType Directory -Path $CodexSkillsPath | Out-Null
}

$projectSkillsRoot = (Resolve-Path -LiteralPath $ProjectSkillsPath).Path
$codexSkillsRoot = (Resolve-Path -LiteralPath $CodexSkillsPath).Path

Get-ChildItem -LiteralPath $projectSkillsRoot -Directory | ForEach-Object {
    $skillName = $_.Name
    $sourcePath = $_.FullName
    $targetPath = Join-Path $codexSkillsRoot $skillName
    $skillFile = Join-Path $sourcePath "SKILL.md"

    if (!(Test-Path -LiteralPath $skillFile)) {
        Write-Warning "Skipping $skillName because SKILL.md is missing."
        return
    }

    if (Test-Path -LiteralPath $targetPath) {
        $targetItem = Get-Item -LiteralPath $targetPath
        $currentTarget = if ($targetItem.Target -is [array]) { $targetItem.Target[0] } else { $targetItem.Target }
        $isExpectedLink = $targetItem.LinkType -eq "Junction" -and
            $currentTarget -and
            [string]::Equals(
                (Resolve-Path -LiteralPath $currentTarget).Path,
                $sourcePath,
                [System.StringComparison]::OrdinalIgnoreCase
            )
        if ($isExpectedLink) {
            Write-Host "Already linked: $skillName"
            return
        }
        throw "Refusing to replace existing skill path: $targetPath"
    }

    New-Item -ItemType Junction -Path $targetPath -Target $sourcePath | Out-Null
    Write-Host "Linked: $skillName -> $sourcePath"
}
