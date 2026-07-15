[CmdletBinding()]
param(
  [switch]$CheckOnly,
  [string]$FixtureRoot = '',
  [string]$OutputPath = '',
  [ValidateSet('none','wrong-byte')][string]$Mutation = 'none'
)

$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent $PSScriptRoot
$ManifestPath = Join-Path $PSScriptRoot 'platform-manifest.json'
$Manifest = Get-Content -Raw -LiteralPath $ManifestPath | ConvertFrom-Json
$ManifestHash = (Get-FileHash -LiteralPath $ManifestPath -Algorithm SHA256).Hash
$RuntimeRoot = Join-Path $Root '.runtime'
if (-not $OutputPath) { $OutputPath = Join-Path $RuntimeRoot 'toolchain.json' }

$RequiredKeys = @(
  'git_path','bash_path','uv_path','python_path','node_path','npm_cli_path','cmake_path','ctest_path','ninja_path',
  'vs_install_path','vsdevcmd_path','cl_path','link_path','lib_path','rc_path','mt_path','arm_toolchain_root',
  'arm_gcc_path','arm_gxx_path','arm_asm_path','arm_as_path','arm_ar_path','arm_ranlib_path','arm_ld_path',
  'arm_objcopy_path','arm_size_path'
)

function Write-Utf8Json([object]$Value, [string]$Path) {
  $parent = Split-Path -Parent $Path
  New-Item -ItemType Directory -Force -Path $parent | Out-Null
  $json = $Value | ConvertTo-Json -Depth 12
  [IO.File]::WriteAllText($Path, $json, [Text.UTF8Encoding]::new($false))
}

function Assert-Closure([Collections.IDictionary]$Paths, [Collections.IDictionary]$Versions) {
  foreach ($key in $RequiredKeys) {
    if (-not $Paths.Contains($key) -or -not [IO.Path]::IsPathRooted([string]$Paths[$key])) { throw "missing absolute runtime path: $key" }
    if (-not (Test-Path -LiteralPath $Paths[$key])) { throw "runtime path does not exist: $key" }
    if (-not $Versions.Contains($key) -or -not [string]$Versions[$key]) { throw "missing runtime version: $key" }
  }
}

function New-FixtureRuntime([string]$TargetRoot) {
  $TargetRoot = [IO.Path]::GetFullPath($TargetRoot)
  $bin = Join-Path $TargetRoot 'bin'
  $vs = Join-Path $TargetRoot 'vs'
  $arm = Join-Path $TargetRoot 'arm'
  New-Item -ItemType Directory -Force -Path $bin,$vs,$arm | Out-Null
  $archive = Join-Path $TargetRoot 'managed-archive.fixture'
  [IO.File]::WriteAllBytes($archive, [Text.Encoding]::UTF8.GetBytes('sealed managed fixture bytes'))
  $archiveHash = (Get-FileHash -LiteralPath $archive -Algorithm SHA256).Hash
  if ($Mutation -eq 'wrong-byte') { [IO.File]::AppendAllText($archive, 'tampered') }
  if ((Get-FileHash -LiteralPath $archive -Algorithm SHA256).Hash -ne $archiveHash) { throw 'managed artifact SHA-256 mismatch' }

  $versions = [ordered]@{}
  $paths = [ordered]@{}
  $versionByKey = @{
    git_path='2.55.0.2'; bash_path='5.2'; uv_path='0.11.28'; python_path='3.12.13+20260623';
    node_path='22.23.1'; npm_cli_path='10.9.2'; cmake_path='4.3.4'; ctest_path='4.3.4'; ninja_path='1.13.2';
    vs_install_path='17.14.35'; vsdevcmd_path='17.14.35'; cl_path='19.44'; link_path='14.44'; lib_path='14.44';
    rc_path='10.0.26100.0'; mt_path='10.0.26100.0'; arm_toolchain_root='14.2.Rel1'; arm_gcc_path='14.2.Rel1';
    arm_gxx_path='14.2.Rel1'; arm_asm_path='14.2.Rel1'; arm_as_path='14.2.Rel1'; arm_ar_path='14.2.Rel1';
    arm_ranlib_path='14.2.Rel1'; arm_ld_path='14.2.Rel1'; arm_objcopy_path='14.2.Rel1'; arm_size_path='14.2.Rel1'
  }
  $actualGitPath = if ($env:PETCARE_TEST_GIT) { $env:PETCARE_TEST_GIT } else { (Get-Command git.exe -ErrorAction SilentlyContinue).Source }
  $actualBashPath = if ($env:PETCARE_TEST_BASH) { $env:PETCARE_TEST_BASH } elseif ($actualGitPath) { Join-Path (Split-Path (Split-Path $actualGitPath -Parent) -Parent) 'bin/bash.exe' } else { '' }
  foreach ($key in $RequiredKeys) {
    if ($key -eq 'vs_install_path') { $path = $vs }
    elseif ($key -eq 'arm_toolchain_root') { $path = $arm }
    elseif ($key -eq 'git_path' -and $actualGitPath) {
      $path = Join-Path $bin 'git.cmd'
      $content = "@echo off`r`nif `"%~1`"==`"--version`" (`r`n  echo git version 2.55.0.windows.2`r`n  exit /b 0`r`n)`r`n`"$actualGitPath`" %*`r`nexit /b %errorlevel%`r`n"
      [IO.File]::WriteAllText($path, $content, [Text.ASCIIEncoding]::new())
    }
    elseif ($key -eq 'bash_path' -and (Test-Path -LiteralPath $actualBashPath)) { $path = $actualBashPath }
    elseif ($key -eq 'python_path' -and (Test-Path (Join-Path $RuntimeRoot 'cpython-3.12.13+20260623/python/python.exe'))) { $path = (Resolve-Path (Join-Path $RuntimeRoot 'cpython-3.12.13+20260623/python/python.exe')).Path }
    elseif ($key -eq 'uv_path' -and (Test-Path (Join-Path $RuntimeRoot 'uv-0.11.28/uv.exe'))) { $path = (Resolve-Path (Join-Path $RuntimeRoot 'uv-0.11.28/uv.exe')).Path }
    else {
      $path = Join-Path $bin ($key + '.cmd')
      [IO.File]::WriteAllText($path, "@echo off`r`necho $($versionByKey[$key])`r`nexit /b 0`r`n", [Text.ASCIIEncoding]::new())
    }
    $paths[$key] = [IO.Path]::GetFullPath($path)
    $versions[$key] = $versionByKey[$key]
  }
  Assert-Closure $paths $versions
  return [ordered]@{
    schema_version = 1
    manifest_sha256 = $ManifestHash
    platform = 'windows-x64'
    fixture = $true
    paths = $paths
    versions = $versions
    packages = [ordered]@{
      git='Git.Git@2.55.0.2'; uv='astral-sh.uv@0.11.28'; node='OpenJS.NodeJS.22@22.23.1';
      cmake='Kitware.CMake@4.3.4'; ninja='Ninja-build.Ninja@1.13.2';
      visual_studio='Microsoft.VisualStudio.2022.BuildTools@17.14.35';
      arm_gnu='Arm.GnuArmEmbeddedToolchain@14.2.Rel1'; postgresql='PostgreSQL.PostgreSQL.17@17.10-2';
      mosquitto='EclipseFoundation.Mosquitto@2.1.2'
    }
    environment = [ordered]@{}
  }
}

function Get-VerifiedArchive([string]$Name, [object]$Artifact) {
  $cache = Join-Path $RuntimeRoot 'bootstrap-cache'
  New-Item -ItemType Directory -Force -Path $cache | Out-Null
  $extension = if ($Artifact.url -match '\.tar\.gz$') { '.tar.gz' } elseif ($Artifact.url -match '\.zip$') { '.zip' } elseif ($Artifact.url -match '\.msi$') { '.msi' } else { '.archive' }
  $path = Join-Path $cache ($Name + $extension)
  if (-not (Test-Path -LiteralPath $path)) {
    if ($CheckOnly) { throw "missing managed artifact in CheckOnly mode: $Name" }
    Invoke-WebRequest -Uri $Artifact.url -OutFile $path -UseBasicParsing
  }
  $actual = (Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash
  if ($actual -ne $Artifact.sha256) { throw "managed artifact SHA-256 mismatch: $Name" }
  return $path
}

function Expand-Managed([string]$Name, [string]$Archive) {
  $target = Join-Path $RuntimeRoot ('managed/' + $Name)
  if (-not (Test-Path -LiteralPath $target)) {
    if ($CheckOnly) { throw "managed input is not extracted: $Name" }
    New-Item -ItemType Directory -Force -Path $target | Out-Null
    if ($Archive.EndsWith('.zip')) { Expand-Archive -LiteralPath $Archive -DestinationPath $target }
    elseif ($Archive.EndsWith('.msi')) {
      $process = Start-Process msiexec.exe -Wait -PassThru -ArgumentList @('/a', $Archive, '/qn', "TARGETDIR=$target")
      if ($process.ExitCode) { throw "MSI extraction failed: $Name ($($process.ExitCode))" }
    } else {
      & tar.exe -xf $Archive -C $target
      if ($LASTEXITCODE) { throw "archive extraction failed: $Name" }
    }
  }
  return $target
}

function Assert-WingetPackage([string]$Id, [string]$Version, [string[]]$OverrideComponents = @(), [switch]$VerifyOnly) {
  $winget = (Get-Command winget.exe -ErrorAction Stop).Source
  if ($VerifyOnly) {
    & $winget show --id $Id --exact --version $Version --source winget --accept-source-agreements *> $null
    if ($LASTEXITCODE) { throw "exact winget authority is unavailable: $Id@$Version" }
    return
  }
  $lines = & $winget list --id $Id --exact --source winget --accept-source-agreements 2>&1
  $present = ($LASTEXITCODE -eq 0 -and (($lines -join "`n") -match [regex]::Escape($Version)))
  if (-not $present -and -not $CheckOnly -and -not $VerifyOnly) {
    $arguments = @('install','--id',$Id,'--version',$Version,'--exact','--silent','--accept-package-agreements','--accept-source-agreements')
    if ($OverrideComponents.Count) {
      $vsTarget = Join-Path $RuntimeRoot 'managed/vs'
      $override = "--wait --quiet --norestart --nocache --installPath `"$vsTarget`" " + (($OverrideComponents | ForEach-Object { "--add $_" }) -join ' ')
      $arguments += @('--override', $override)
    }
    & $winget @arguments
    if ($LASTEXITCODE) { throw "winget installation failed: $Id@$Version" }
    $lines = & $winget list --id $Id --exact --source winget --accept-source-agreements 2>&1
    $present = ($LASTEXITCODE -eq 0 -and (($lines -join "`n") -match [regex]::Escape($Version)))
  }
  if (-not $present) { throw "exact winget package is absent or mismatched: $Id@$Version" }
}

if ($FixtureRoot) {
  $runtime = New-FixtureRuntime $FixtureRoot
  Write-Utf8Json $runtime ([IO.Path]::GetFullPath($OutputPath))
  Write-Output "Windows bootstrap complete fixture PASS: $OutputPath"
  exit 0
}

New-Item -ItemType Directory -Force -Path $RuntimeRoot | Out-Null
$managed = $Manifest.managed_exact
$roots = @{}
foreach ($name in @('uv','python','node','cmake','ninja','arm_gnu')) {
  $archive = Get-VerifiedArchive $name $managed.$name.windows
  $roots[$name] = Expand-Managed $name $archive
}

$components = @($managed.visual_studio.components)
Assert-WingetPackage $managed.git.windows_id $managed.git.version
Assert-WingetPackage $managed.visual_studio.windows_id $managed.visual_studio.version $components
Assert-WingetPackage $managed.postgresql.windows_id $managed.postgresql.version -VerifyOnly
Assert-WingetPackage $managed.mosquitto.windows_id $managed.mosquitto.version -VerifyOnly

$find = {
  param([string]$RootPath, [string]$Leaf)
  $item = Get-ChildItem -LiteralPath $RootPath -Recurse -File -Filter $Leaf | Select-Object -First 1
  if (-not $item) { throw "managed executable missing: $Leaf" }
  $item.FullName
}
$gitPath = (Get-Command git.exe -ErrorAction Stop).Source
$bashPath = Join-Path (Split-Path (Split-Path $gitPath -Parent) -Parent) 'bin/bash.exe'
$uvPath = & $find $roots.uv 'uv.exe'
$pythonPath = & $find $roots.python 'python.exe'
$nodePath = & $find $roots.node 'node.exe'
$npmCliPath = & $find $roots.node 'npm-cli.js'
$cmakePath = & $find $roots.cmake 'cmake.exe'
$ctestPath = & $find $roots.cmake 'ctest.exe'
$ninjaPath = & $find $roots.ninja 'ninja.exe'
$armBin = Split-Path (& $find $roots.arm_gnu 'arm-none-eabi-gcc.exe') -Parent

& $pythonPath (Join-Path $PSScriptRoot 'validate_platform_manifest.py') --manifest $ManifestPath
if ($LASTEXITCODE) { throw 'sealed manifest validation failed' }
if ((& $gitPath --version) -notmatch '2\.55\.0\.windows\.2') { throw 'managed Git version mismatch' }
if ((& $pythonPath -c 'import sys; print(sys.version.split()[0])') -ne '3.12.13') { throw 'managed Python version mismatch' }
if ((& $pythonPath -c "import sys; print('20260623' if 'Jun 23 2026' in sys.version else 'wrong')") -ne '20260623') { throw 'managed Python build mismatch' }
if ((& $uvPath --version) -notmatch '^uv 0\.11\.28 ') { throw 'managed uv version mismatch' }
$pytestVersion = (& $pythonPath -c "import importlib.util; print('' if importlib.util.find_spec('pytest') is None else __import__('pytest').__version__)")
if ($pytestVersion -ne $managed.backend_dependencies.pytest) {
  if ($CheckOnly) { throw 'managed pytest version mismatch' }
  & $uvPath pip install --python $pythonPath "pytest==$($managed.backend_dependencies.pytest)"
  if ($LASTEXITCODE -or (& $pythonPath -c 'import pytest; print(pytest.__version__)') -ne $managed.backend_dependencies.pytest) { throw 'managed pytest installation failed' }
}
if ((& $nodePath --version) -ne 'v22.23.1') { throw 'managed Node version mismatch' }
if ((& $cmakePath --version | Select-Object -First 1) -notmatch '4\.3\.4') { throw 'managed CMake version mismatch' }
if ((& $ninjaPath --version) -ne '1.13.2') { throw 'managed Ninja version mismatch' }
if ((& (Join-Path $armBin 'arm-none-eabi-gcc.exe') --version | Select-Object -First 1) -notmatch '14\.2\.1') { throw 'managed Arm GNU version mismatch' }

$vswhere = Join-Path ${env:ProgramFiles(x86)} 'Microsoft Visual Studio/Installer/vswhere.exe'
$vsInstall = (& $vswhere -utf8 -latest -products Microsoft.VisualStudio.Product.BuildTools -version '[17.14,17.15)' -property installationPath | Select-Object -First 1)
if (-not $vsInstall) { throw 'Visual Studio Build Tools 17.14 is missing' }
foreach ($component in $components) {
  if (-not (& $vswhere -products Microsoft.VisualStudio.Product.BuildTools -version '[17.14,17.15)' -requires $component -property installationPath)) { throw "missing Visual Studio component: $component" }
}
$vsDevCmd = Join-Path $vsInstall 'Common7/Tools/VsDevCmd.bat'
$msvcRoot = Get-ChildItem -LiteralPath (Join-Path $vsInstall 'VC/Tools/MSVC') -Directory | Where-Object Name -Like '14.44.*' | Sort-Object Name -Descending | Select-Object -First 1
if (-not $msvcRoot) { throw 'MSVC 14.44 toolset is missing' }
$msvcBin = Join-Path $msvcRoot.FullName 'bin/Hostx64/x64'
$sdkBin = Get-ChildItem -LiteralPath (Join-Path ${env:ProgramFiles(x86)} 'Windows Kits/10/bin') -Directory | Where-Object Name -Like '10.0.26100.*' | Sort-Object Name -Descending | Select-Object -First 1
if (-not $sdkBin) { throw 'Windows SDK 26100 is missing' }
$sdkX64 = Join-Path $sdkBin.FullName 'x64'

$paths = [ordered]@{
  git_path=$gitPath; bash_path=$bashPath; uv_path=$uvPath; python_path=$pythonPath; node_path=$nodePath; npm_cli_path=$npmCliPath;
  cmake_path=$cmakePath; ctest_path=$ctestPath; ninja_path=$ninjaPath; vs_install_path=$vsInstall; vsdevcmd_path=$vsDevCmd;
  cl_path=(Join-Path $msvcBin 'cl.exe'); link_path=(Join-Path $msvcBin 'link.exe'); lib_path=(Join-Path $msvcBin 'lib.exe');
  rc_path=(Join-Path $sdkX64 'rc.exe'); mt_path=(Join-Path $sdkX64 'mt.exe'); arm_toolchain_root=$roots.arm_gnu;
  arm_gcc_path=(Join-Path $armBin 'arm-none-eabi-gcc.exe'); arm_gxx_path=(Join-Path $armBin 'arm-none-eabi-g++.exe');
  arm_asm_path=(Join-Path $armBin 'arm-none-eabi-gcc.exe'); arm_as_path=(Join-Path $armBin 'arm-none-eabi-as.exe');
  arm_ar_path=(Join-Path $armBin 'arm-none-eabi-ar.exe'); arm_ranlib_path=(Join-Path $armBin 'arm-none-eabi-ranlib.exe');
  arm_ld_path=(Join-Path $armBin 'arm-none-eabi-ld.exe'); arm_objcopy_path=(Join-Path $armBin 'arm-none-eabi-objcopy.exe');
  arm_size_path=(Join-Path $armBin 'arm-none-eabi-size.exe')
}
$versions = [ordered]@{}
foreach ($key in $RequiredKeys) { $versions[$key] = if ($key -like 'arm_*') { '14.2.Rel1' } elseif ($key -in @('cl_path','link_path','lib_path')) { $msvcRoot.Name } elseif ($key -in @('rc_path','mt_path')) { $sdkBin.Name } else { 'verified' } }
$versions.git_path=$managed.git.version; $versions.bash_path=(& $bashPath --version | Select-Object -First 1); $versions.uv_path=$managed.uv.version
$versions.python_path="$($managed.python.version)+$($managed.python.build)"; $versions.node_path=$managed.node.version; $versions.cmake_path=$managed.cmake.version
$versions.ctest_path=$managed.cmake.version; $versions.ninja_path=$managed.ninja.version; $versions.vs_install_path=$managed.visual_studio.version
$versions.vsdevcmd_path=$managed.visual_studio.version; $versions.npm_cli_path=(& $nodePath $npmCliPath --version)
Assert-Closure $paths $versions

$environment = [ordered]@{}
$environmentLines = & $env:ComSpec /d /s /c "`"$vsDevCmd`" -arch=x64 -host_arch=x64 >nul && set"
foreach ($line in $environmentLines) {
  if ($line -match '^(INCLUDE|LIB|LIBPATH|PATH)=(.*)$') { $environment[$matches[1]] = $matches[2] }
}
$runtime = [ordered]@{schema_version=1; manifest_sha256=$ManifestHash; platform='windows-x64'; fixture=$false; paths=$paths; versions=$versions; packages=[ordered]@{}; environment=$environment}
foreach ($name in @('git','uv','node','cmake','ninja','visual_studio','arm_gnu','postgresql','mosquitto')) {
  $entry = $managed.$name
  $runtime.packages[$name] = "$($entry.windows_id)@$($entry.version)"
}
Write-Utf8Json $runtime ([IO.Path]::GetFullPath($OutputPath))
Write-Output "Windows managed toolchain PASS: $OutputPath"
