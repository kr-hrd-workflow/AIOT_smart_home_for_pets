$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$bootstrap = Join-Path $root 'tools/bootstrap_toolchain.ps1'
$build = Join-Path $root 'tools/build_pico_host.ps1'
$fixture = Join-Path $root '.runtime/tests/windows-fixture'
$output = Join-Path $root '.runtime/tests/toolchain-fixture.json'

$parameters = (Get-Command -Name $bootstrap).Parameters
if ($null -eq $parameters) { throw 'ASSERT: bootstrap parameters are not discoverable' }
foreach ($requiredParameter in @('FixtureRoot','OutputPath','Mutation')) {
  if (-not $parameters.ContainsKey($requiredParameter)) { throw "ASSERT: bootstrap missing fixture parameter $requiredParameter" }
}
& $bootstrap -FixtureRoot $fixture -OutputPath $output
if ($LASTEXITCODE) { throw 'Windows fixture bootstrap failed' }
$data = Get-Content -Raw -Encoding UTF8 -LiteralPath $output | ConvertFrom-Json
$keys = @(
  'git_path','bash_path','uv_path','python_path','node_path','npm_cli_path','cmake_path','ctest_path','ninja_path',
  'vs_install_path','vsdevcmd_path','cl_path','link_path','lib_path','rc_path','mt_path','arm_toolchain_root',
  'arm_gcc_path','arm_gxx_path','arm_asm_path','arm_as_path','arm_ar_path','arm_ranlib_path','arm_ld_path',
  'arm_objcopy_path','arm_size_path'
)
$expectedHash = (Get-FileHash -LiteralPath (Join-Path $root 'tools/platform-manifest.json') -Algorithm SHA256).Hash
if ($data.manifest_sha256 -ne $expectedHash) { throw 'authority hash was not inherited' }
foreach ($key in $keys) {
  $value = $data.paths.$key
  if (-not $value -or -not [IO.Path]::IsPathRooted($value) -or -not (Test-Path -LiteralPath $value)) { throw "invalid path: $key" }
  if (-not $data.versions.$key) { throw "missing version: $key" }
}

$oldPath = $env:PATH
try {
  $env:PATH = "$env:SystemRoot\System32"
  & $build -RuntimePath $output -BuildDir (Join-Path $root '.runtime/tests/windows-host-build') -DryRun
  if ($LASTEXITCODE) { throw 'stripped-PATH Windows child failed' }

  $nonFixture = Get-Content -Raw -Encoding UTF8 -LiteralPath $output | ConvertFrom-Json
  $nonFixture.fixture = $false
  $failingTool = Join-Path $fixture 'failing-cl.cmd'
  [IO.File]::WriteAllText($failingTool, "@exit /b 7`r`n", [Text.ASCIIEncoding]::new())
  $nonFixture.paths.cl_path = $failingTool
  $nonFixtureRuntime = Join-Path $root '.runtime/tests/toolchain-nonfixture-failing.json'
  [IO.File]::WriteAllText($nonFixtureRuntime, ($nonFixture | ConvertTo-Json -Depth 12), [Text.UTF8Encoding]::new($false))
  $probeChild = Start-Process -FilePath (Get-Process -Id $PID).Path -Wait -PassThru -WindowStyle Hidden -ArgumentList @(
    '-NoProfile','-ExecutionPolicy','Bypass','-File',$build,
    '-RuntimePath',$nonFixtureRuntime,'-BuildDir',(Join-Path $root '.runtime/tests/windows-host-build'),'-DryRun'
  )
  if ($probeChild.ExitCode -eq 0) { throw 'actual-runtime MSVC child was not executed' }
} finally { $env:PATH = $oldPath }

$child = Start-Process -FilePath (Get-Process -Id $PID).Path -Wait -PassThru -WindowStyle Hidden -ArgumentList @(
  '-NoProfile','-ExecutionPolicy','Bypass','-File',$bootstrap,
  '-FixtureRoot',(Join-Path $root '.runtime/tests/windows-wrong-byte'),'-OutputPath',$output,'-Mutation','wrong-byte'
)
if ($child.ExitCode -eq 0) { throw 'wrong managed bytes were accepted' }

$checkAll = Join-Path $root 'tools/check_all.ps1'
$mutations = @('manifest_sha256','ctest_path','cl_path','link_path','rc_path','arm_asm_path','arm_ar_path','arm_ld_path')
foreach ($mutation in $mutations) {
  $changed = Get-Content -Raw -Encoding UTF8 -LiteralPath $output | ConvertFrom-Json
  if ($mutation -eq 'manifest_sha256') { $changed.manifest_sha256 = '0' * 64 }
  else { $changed.paths.$mutation = 'relative-or-missing' }
  $altered = Join-Path $root ".runtime/tests/windows-altered-$mutation.json"
  [IO.File]::WriteAllText($altered, ($changed | ConvertTo-Json -Depth 12), [Text.UTF8Encoding]::new($false))
  $child = Start-Process -FilePath (Get-Process -Id $PID).Path -Wait -PassThru -WindowStyle Hidden -ArgumentList @(
    '-NoProfile','-ExecutionPolicy','Bypass','-File',$checkAll,'-RuntimePath',$altered,'-SkipPytest'
  )
  if ($child.ExitCode -eq 0) { throw "runtime mutation was accepted: $mutation" }
}
Write-Output 'Windows bootstrap complete fixture PASS'
