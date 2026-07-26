$ErrorActionPreference = 'Stop'

$root = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$build = Join-Path $root 'tools/build_pico.ps1'

function Get-PSDrive {
  [CmdletBinding()]
  param([string]$Name, [string]$PSProvider)

  if ($Name -in @('P','Q','R','S','T','U','V','W','X','Y','Z') -and $PSProvider -eq 'FileSystem') { return }
  Microsoft.PowerShell.Management\Get-PSDrive @PSBoundParameters
}

function Join-Path {
  [CmdletBinding()]
  param([string]$Path, [string]$ChildPath)

  if ($Path -match '^[P-Z]:\\$') { throw "ASSERT: candidate authority path constructed for unavailable drive $Path" }
  Microsoft.PowerShell.Management\Join-Path @PSBoundParameters
}

try {
  & $build -DryRun
  throw 'ASSERT: Pico drive probe completed without requiring an ASCII workspace drive'
} catch {
  if ($_.Exception.Message -ne 'verified ASCII workspace drive is required for the Windows Arm GNU toolchain') { throw }
}

Write-Output 'Pico drive probe guard PASS'
