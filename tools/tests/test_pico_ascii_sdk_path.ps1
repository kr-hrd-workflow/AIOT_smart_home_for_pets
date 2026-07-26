[CmdletBinding()]
param(
  [string]$RuntimePath = ''
)

$ErrorActionPreference = 'Stop'

$root = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
if (-not $RuntimePath) { $RuntimePath = Join-Path $root '.runtime/toolchain.json' }
if (-not (Test-Path -LiteralPath $RuntimePath)) { throw 'ASSERT: runtime manifest is missing' }

$driveName = @('P','Q','R','S','T','U','V','W','X','Y','Z') |
  Where-Object { -not (Microsoft.PowerShell.Management\Get-PSDrive -Name $_ -PSProvider FileSystem -ErrorAction SilentlyContinue) } |
  Select-Object -First 1
if (-not $driveName) { throw 'ASSERT: no temporary ASCII drive letter is available' }

$subst = Join-Path $env:SystemRoot 'System32/subst.exe'
& $subst "${driveName}:" $root
if ($LASTEXITCODE) { throw "ASSERT: failed to create test drive $driveName" }

function Get-PSDrive {
  [CmdletBinding()]
  param([string]$Name, [string]$PSProvider)

  if ($Name -eq $driveName -and $PSProvider -eq 'FileSystem') {
    return Microsoft.PowerShell.Management\Get-PSDrive @PSBoundParameters
  }
}

try {
  $runtime = Get-Content -Raw -Encoding UTF8 -LiteralPath $RuntimePath | ConvertFrom-Json
  $output = & "${driveName}:\tools\build_pico.ps1" -RuntimePath "${driveName}:\.runtime\toolchain.json" -DryRun
  if ($LASTEXITCODE) { throw 'ASSERT: Pico dry-run failed' }

  $line = $output | Where-Object { $_ -like 'effective_pico_sdk=*' } | Select-Object -First 1
  if (-not $line) { throw 'ASSERT: Pico dry-run did not report the effective SDK path' }

  $actual = $line.Substring('effective_pico_sdk='.Length)
  $expected = "${driveName}:\.runtime\managed\pico-sdk-$($runtime.pico_sdk.tag)"
  if ($actual -ne $expected) {
    throw "ASSERT: expected independent ASCII SDK path $expected, got $actual"
  }
} finally {
  & $subst "${driveName}:" '/D' | Out-Null
}

Write-Output 'Pico ASCII SDK path PASS'
