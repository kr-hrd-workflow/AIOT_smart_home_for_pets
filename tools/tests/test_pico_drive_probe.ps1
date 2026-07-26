[CmdletBinding()]
param(
  [string]$RuntimePath = ''
)

$ErrorActionPreference = 'Stop'

$root = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$build = Join-Path $root 'tools/build_pico.ps1'
$checkAll = Join-Path $root 'tools/check_all.ps1'
if (-not $RuntimePath) { $RuntimePath = Join-Path $root '.runtime/toolchain.json' }
if (-not (Test-Path -LiteralPath $RuntimePath)) { throw 'ASSERT: provided runtime manifest is missing' }

$checkAllText = Get-Content -Raw -Encoding UTF8 -LiteralPath $checkAll
if ($checkAllText -notmatch '& \(Join-Path \$PSScriptRoot ''tests/test_pico_drive_probe\.ps1''\) -RuntimePath \$RuntimePath') {
  throw 'ASSERT: check_all must forward RuntimePath to the Pico drive probe'
}

$forwardedRuntime = Join-Path ([IO.Path]::GetTempPath()) "petcare-pico-runtime-forward-$PID.json"
$runtime = Get-Content -Raw -Encoding UTF8 -LiteralPath $RuntimePath | ConvertFrom-Json
$runtime.manifest_sha256 = 'runtime-path-forwarding-sentinel'
[IO.File]::WriteAllText($forwardedRuntime, ($runtime | ConvertTo-Json -Depth 100), [Text.UTF8Encoding]::new($false))

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
  try {
    & $build -RuntimePath $forwardedRuntime -DryRun
    throw 'ASSERT: build_pico ignored the custom RuntimePath'
  } catch {
    if ($_.Exception.Message -ne 'runtime authority hash mismatch') { throw }
  }

  try {
    & $build -RuntimePath $RuntimePath -DryRun
    throw 'ASSERT: Pico drive probe completed without requiring an ASCII workspace drive'
  } catch {
    if ($_.Exception.Message -ne 'verified ASCII workspace drive is required for the Windows Arm GNU toolchain') { throw }
  }
} finally {
  Remove-Item -LiteralPath $forwardedRuntime -Force -ErrorAction SilentlyContinue
}

Write-Output 'Pico drive probe guard PASS'
