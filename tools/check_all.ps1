[CmdletBinding()]
param(
  [string]$RuntimePath = '',
  [switch]$SkipPytest
)

$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent $PSScriptRoot
if (-not $RuntimePath) { $RuntimePath = Join-Path $Root '.runtime/toolchain.json' }
if (-not (Test-Path -LiteralPath $RuntimePath)) { throw 'runtime toolchain manifest is missing' }
$runtime = Get-Content -Raw -Encoding UTF8 -LiteralPath $RuntimePath | ConvertFrom-Json
$manifest = Join-Path $PSScriptRoot 'platform-manifest.json'
$expectedHash = (Get-FileHash -LiteralPath $manifest -Algorithm SHA256).Hash
if ($runtime.manifest_sha256 -ne $expectedHash) { throw 'runtime authority hash mismatch' }

$required = @(
  'git_path','bash_path','uv_path','python_path','node_path','npm_cli_path','cmake_path','ctest_path','ninja_path',
  'vs_install_path','vsdevcmd_path','cl_path','link_path','lib_path','rc_path','mt_path','arm_toolchain_root',
  'arm_gcc_path','arm_gxx_path','arm_asm_path','arm_as_path','arm_ar_path','arm_ranlib_path','arm_ld_path',
  'arm_objcopy_path','arm_size_path'
)
foreach ($key in $required) {
  $path = $runtime.paths.$key
  if (-not $path -or -not [IO.Path]::IsPathRooted($path) -or -not (Test-Path -LiteralPath $path) -or -not $runtime.versions.$key) { throw "invalid runtime closure: $key" }
}

$env:PETCARE_TEST_GIT = $runtime.paths.git_path
$env:PETCARE_TEST_BASH = $runtime.paths.bash_path
$env:PYTHON_PATH = $runtime.paths.python_path
$oldPath = $env:PATH
try {
  $env:PATH = "$env:SystemRoot\System32"
  & $runtime.paths.python_path (Join-Path $PSScriptRoot 'validate_platform_manifest.py') --manifest $manifest
  if ($LASTEXITCODE) { exit $LASTEXITCODE }
  & $runtime.paths.python_path (Join-Path $PSScriptRoot 'docs_check.py') --root $Root
  if ($LASTEXITCODE) { exit $LASTEXITCODE }
  & $runtime.paths.python_path -c "import sys; assert sys.version_info[:3] == (3, 12, 13); print(sys.version)"
  if ($LASTEXITCODE) { exit $LASTEXITCODE }

  if (-not $SkipPytest) {
    & $runtime.paths.python_path -m pytest --version
    if ($LASTEXITCODE) { exit $LASTEXITCODE }
    $pytestArgs = @(
      '-m','pytest',
      (Join-Path $PSScriptRoot 'tests/test_validate_platform_manifest.py'),
      (Join-Path $PSScriptRoot 'tests/test_evidence_manifest.py'),
      (Join-Path $PSScriptRoot 'tests/test_bootstrap_ci.py'),
      '-q'
    )
    & $runtime.paths.python_path @pytestArgs
    if ($LASTEXITCODE) { exit $LASTEXITCODE }
  }

  & (Join-Path $PSScriptRoot 'tests/test_bootstrap_toolchain.ps1')
  & (Join-Path $PSScriptRoot 'build_pico_host.ps1') -RuntimePath $RuntimePath -BuildDir (Join-Path $Root '.runtime/tests/check-all-host') -DryRun

  foreach ($key in @('git_path','bash_path','uv_path','python_path','node_path','cmake_path','ctest_path','ninja_path','arm_gcc_path','arm_gxx_path','arm_asm_path','arm_as_path','arm_ar_path','arm_ranlib_path','arm_ld_path','arm_objcopy_path','arm_size_path')) {
    $path = $runtime.paths.$key
    & $path --version | Out-Null
    if ($LASTEXITCODE) { throw "stripped-PATH child failed: $key" }
  }
  if ($runtime.fixture) {
    foreach ($key in @('npm_cli_path','vsdevcmd_path','cl_path','link_path','lib_path','rc_path','mt_path')) {
      & $runtime.paths.$key --version | Out-Null
      if ($LASTEXITCODE) { throw "stripped-PATH child failed: $key" }
    }
  } else {
    & $runtime.paths.node_path $runtime.paths.npm_cli_path --version | Out-Null
    if ($LASTEXITCODE) { throw 'stripped-PATH npm child failed' }
  }
} finally {
  $env:PATH = $oldPath
}
Write-Output 'manifest-backed complete check PASS'
