<#
.SYNOPSIS
    Install the sql-reviewer skill so it is available as /sql-reviewer in Claude Code.

.DESCRIPTION
    Copies skills\sql-reviewer into the user's Claude skills directory
    (%USERPROFILE%\.claude\skills), or into a specific project when -Project is given.

.PARAMETER Project
    Install into <Project>\.claude\skills instead of the user-level directory.

.PARAMETER Force
    Overwrite an existing installation without prompting.

.EXAMPLE
    .\install.ps1

.EXAMPLE
    .\install.ps1 -Project C:\code\my-repo
#>
[CmdletBinding()]
param(
    [string] $Project,
    [switch] $Force
)

$ErrorActionPreference = 'Stop'

$srcDir = Join-Path $PSScriptRoot 'skills\sql-reviewer'

if (-not (Test-Path (Join-Path $srcDir 'SKILL.md'))) {
    Write-Error "SKILL.md not found under '$srcDir'. Run this script from inside a clone of the claude-sql-reviewer repository."
    exit 1
}

if ($Project) {
    $destRoot = Join-Path $Project '.claude\skills'
} else {
    $destRoot = Join-Path $env:USERPROFILE '.claude\skills'
}

$dest = Join-Path $destRoot 'sql-reviewer'

if ((Test-Path $dest) -and (-not $Force)) {
    Write-Host "A skill is already installed at:"
    Write-Host "  $dest"
    $reply = Read-Host 'Overwrite it? [y/N]'
    if ($reply -notmatch '^(y|yes)$') {
        Write-Host 'Aborted. Nothing was changed.'
        exit 0
    }
}

if (-not (Test-Path $destRoot)) {
    New-Item -ItemType Directory -Path $destRoot -Force | Out-Null
}

if (Test-Path $dest) {
    Remove-Item -Recurse -Force $dest
}

Copy-Item -Recurse -Path $srcDir -Destination $dest

Write-Host "Installed to: $dest"
Write-Host ''
Write-Host 'Start a new Claude Code session and run:  /sql-reviewer'
