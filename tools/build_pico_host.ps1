[CmdletBinding()]
param(
  [string]$RuntimePath = '',
  [string]$BuildDir = '',
  [switch]$DryRun
)

$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent $PSScriptRoot
if (-not $RuntimePath) { $RuntimePath = Join-Path $Root '.runtime/toolchain.json' }
if (-not $BuildDir) { $BuildDir = Join-Path $Root 'build/pico-host-windows' }
if (-not (Test-Path -LiteralPath $RuntimePath)) { throw 'run bootstrap_toolchain.ps1 first' }
$runtime = Get-Content -Raw -Encoding UTF8 -LiteralPath $RuntimePath | ConvertFrom-Json
$manifest = Join-Path $PSScriptRoot 'platform-manifest.json'
$expectedHash = (Get-FileHash -LiteralPath $manifest -Algorithm SHA256).Hash
if ($runtime.manifest_sha256 -ne $expectedHash) { throw 'runtime authority hash mismatch' }

$keys = @('cmake_path','ctest_path','ninja_path','cl_path','link_path','lib_path','rc_path','mt_path')
foreach ($key in $keys) {
  $path = $runtime.paths.$key
  if (-not $path -or -not [IO.Path]::IsPathRooted($path) -or -not (Test-Path -LiteralPath $path) -or -not $runtime.versions.$key) { throw "invalid runtime closure: $key" }
}
if ($runtime.environment) {
  foreach ($property in $runtime.environment.PSObject.Properties) {
    if ($property.Name -ne 'Path') { Set-Item -Path "Env:$($property.Name)" -Value $property.Value }
  }
}

function Invoke-Probe([string]$Path, [string[]]$Arguments, [int[]]$ExpectedExitCodes = @(0)) {
  $previousPreference = $ErrorActionPreference
  $ErrorActionPreference = 'Continue'
  try {
    & $Path @Arguments *> $null
    $exitCode = $LASTEXITCODE
  } finally { $ErrorActionPreference = $previousPreference }
  if ($ExpectedExitCodes -notcontains $exitCode) { throw "tool probe failed: $Path ($exitCode)" }
}

if ($runtime.fixture) {
  foreach ($key in $keys) { Invoke-Probe $runtime.paths.$key @('--version') }
  Invoke-Probe $runtime.paths.vsdevcmd_path @('--version')
} else {
  & $env:ComSpec /d /s /c "`"$($runtime.paths.vsdevcmd_path)`" -arch=x64 -host_arch=x64 >nul && echo VSCMD_OK" *> $null
  if ($LASTEXITCODE) { throw 'VsDevCmd child probe failed' }
  Invoke-Probe $runtime.paths.cmake_path @('--version')
  Invoke-Probe $runtime.paths.ctest_path @('--version')
  Invoke-Probe $runtime.paths.ninja_path @('--version')
  Invoke-Probe $runtime.paths.cl_path @('/?')
  Invoke-Probe $runtime.paths.link_path @('/?') @(1100)
  Invoke-Probe $runtime.paths.lib_path @('/?') @(1100)
  Invoke-Probe $runtime.paths.rc_path @('/?')
  Invoke-Probe $runtime.paths.mt_path @('/?')
}

$toolchain = Join-Path $Root '.runtime/toolchains/pico-host-windows.cmake'
New-Item -ItemType Directory -Force -Path (Split-Path -Parent $toolchain) | Out-Null
function CMakePath([string]$Path) { $Path.Replace('\','/') }
$lines = @(
  'set(CMAKE_SYSTEM_NAME Windows)',
  ('set(CMAKE_C_COMPILER "{0}")' -f (CMakePath $runtime.paths.cl_path)),
  ('set(CMAKE_CXX_COMPILER "{0}")' -f (CMakePath $runtime.paths.cl_path)),
  ('set(CMAKE_LINKER "{0}")' -f (CMakePath $runtime.paths.link_path)),
  ('set(CMAKE_AR "{0}")' -f (CMakePath $runtime.paths.lib_path)),
  ('set(CMAKE_RC_COMPILER "{0}")' -f (CMakePath $runtime.paths.rc_path)),
  ('set(CMAKE_MT "{0}")' -f (CMakePath $runtime.paths.mt_path))
)
[IO.File]::WriteAllLines($toolchain, $lines, [Text.UTF8Encoding]::new($false))

if ($DryRun) {
  Write-Output 'manifest-backed host build PASS'
  exit 0
}

& $runtime.paths.cmake_path -S (Join-Path $Root 'firmware/pico_pet_node') -B $BuildDir -G Ninja "-DCMAKE_TOOLCHAIN_FILE=$toolchain" "-DCMAKE_MAKE_PROGRAM=$($runtime.paths.ninja_path)"
if ($LASTEXITCODE) { exit $LASTEXITCODE }
& $runtime.paths.cmake_path --build $BuildDir
if ($LASTEXITCODE) { exit $LASTEXITCODE }
& $runtime.paths.ctest_path --test-dir $BuildDir --output-on-failure
exit $LASTEXITCODE
