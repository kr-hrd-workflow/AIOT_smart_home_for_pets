[CmdletBinding()]
param(
  [string]$RuntimePath = ''
)

$ErrorActionPreference = 'Stop'

$root = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
if (-not $RuntimePath) { $RuntimePath = Join-Path $root '.runtime/toolchain.json' }
if (-not (Test-Path -LiteralPath $RuntimePath)) { throw 'ASSERT: runtime manifest is missing' }

$available = @(
  'P','Q','R','S','T','U','V','W','X','Y','Z' |
    Where-Object {
      -not (Microsoft.PowerShell.Management\Get-PSDrive -Name $_ -PSProvider FileSystem -ErrorAction SilentlyContinue)
    }
)
if ($available.Count -lt 2) { throw 'ASSERT: two unused ASCII drives are required' }
$wrongDrive = $available[0]
$correctDrive = $available[1]

$testRoot = Join-Path ([IO.Path]::GetTempPath()) "petcare-pico-worktree-identity-$PID"
$wrongRoot = Join-Path $testRoot 'wrong-worktree'
$subst = Join-Path $env:SystemRoot 'System32/subst.exe'
$git = (Get-Content -Raw -Encoding UTF8 -LiteralPath $RuntimePath | ConvertFrom-Json).paths.git_path

New-Item -ItemType Directory -Path (Join-Path $wrongRoot 'tools') -Force | Out-Null
New-Item -ItemType Directory -Path (Join-Path $wrongRoot '.runtime') -Force | Out-Null
Copy-Item -LiteralPath (Join-Path $root 'tools/platform-manifest.json') -Destination (Join-Path $wrongRoot 'tools/platform-manifest.json')
Copy-Item -LiteralPath $RuntimePath -Destination (Join-Path $wrongRoot '.runtime/toolchain.json')

& $subst "${wrongDrive}:" $wrongRoot
if ($LASTEXITCODE) { throw "ASSERT: failed to create wrong test drive $wrongDrive" }
& $subst "${correctDrive}:" $root
if ($LASTEXITCODE) {
  & $subst "${wrongDrive}:" '/D' | Out-Null
  throw "ASSERT: failed to create correct test drive $correctDrive"
}

function Get-PSDrive {
  [CmdletBinding()]
  param([string]$Name, [string]$PSProvider)

  if ($Name -in @($wrongDrive, $correctDrive) -and $PSProvider -eq 'FileSystem') {
    return Microsoft.PowerShell.Management\Get-PSDrive @PSBoundParameters
  }
}

function Assert-CorrectWorkspaceSelected {
  $output = & "${correctDrive}:\tools\build_pico.ps1" `
    -RuntimePath "${correctDrive}:\.runtime\toolchain.json" -DryRun
  if ($LASTEXITCODE) { throw 'ASSERT: Pico dry-run failed' }

  $line = $output | Where-Object { $_ -like 'effective_workspace=*' } | Select-Object -First 1
  if (-not $line) { throw 'ASSERT: Pico dry-run did not report the effective workspace' }
  $actual = $line.Substring('effective_workspace='.Length)
  $expected = "${correctDrive}:\"
  if ($actual -ne $expected) {
    throw "ASSERT: wrong Git worktree was selected: $actual"
  }
}

try {
  Assert-CorrectWorkspaceSelected

  & $git -C $wrongRoot init --quiet
  if ($LASTEXITCODE) { throw 'ASSERT: failed to create wrong Git worktree fixture' }
  Assert-CorrectWorkspaceSelected
} finally {
  & $subst "${correctDrive}:" '/D' | Out-Null
  & $subst "${wrongDrive}:" '/D' | Out-Null
  $resolvedTestRoot = [IO.Path]::GetFullPath($testRoot)
  $resolvedTempRoot = [IO.Path]::GetFullPath([IO.Path]::GetTempPath()).TrimEnd('\') + '\'
  if (-not $resolvedTestRoot.StartsWith($resolvedTempRoot, [StringComparison]::OrdinalIgnoreCase) -or
      (Split-Path -Leaf $resolvedTestRoot) -ne "petcare-pico-worktree-identity-$PID") {
    throw 'ASSERT: unsafe test cleanup path'
  }
  Remove-Item -LiteralPath $resolvedTestRoot -Recurse -Force -ErrorAction SilentlyContinue
}

Write-Output 'Pico worktree identity PASS'
