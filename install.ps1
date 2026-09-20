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
    # Exit non-zero on abort. Exiting 0 having changed nothing made an
    # agent-driven update report success, leaving the user believing they run
    # a version they do not.
    try {
        $reply = Read-Host 'Overwrite it? [y/N]'
    } catch {
        Write-Error 'No console to prompt on, so nothing was changed. Re-run with -Force.'
        exit 3
    }
    if ($reply -notmatch '^(y|yes)$') {
        Write-Error 'Aborted. Nothing was changed.'
        exit 3
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
Write-Host 'Run /sql-reviewer. Claude Code watches the skills directory, so a running'
Write-Host 'session picks this up without a restart -- unless the skills directory did'
Write-Host 'not exist when that session started, in which case restart it once.'
