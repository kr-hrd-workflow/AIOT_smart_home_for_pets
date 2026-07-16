[CmdletBinding()]
param(
  [string]$RuntimePath = '',
  [string]$BuildRoot = '',
  [ValidateSet('all','entrance-01','petzone-01')][string]$Profile = 'all',
  [switch]$Hardware,
  [switch]$DryRun
)

$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent $PSScriptRoot
if (-not $RuntimePath) { $RuntimePath = Join-Path $Root '.runtime/toolchain.json' }
if (-not $BuildRoot) { $BuildRoot = Join-Path $Root '.runtime/pico-build' }
$AuthorityPath = Join-Path $PSScriptRoot 'platform-manifest.json'
$Authority = Get-Content -Raw -Encoding UTF8 -LiteralPath $AuthorityPath | ConvertFrom-Json
$Runtime = Get-Content -Raw -Encoding UTF8 -LiteralPath $RuntimePath | ConvertFrom-Json
$ExpectedManifestHash = (Get-FileHash -LiteralPath $AuthorityPath -Algorithm SHA256).Hash
if ($Runtime.manifest_sha256 -ne $ExpectedManifestHash) { throw 'runtime authority hash mismatch' }

$Pin = $Authority.managed_exact.pico_sdk
if (-not $Runtime.pico_sdk -or
    $Runtime.pico_sdk.origin -ne $Pin.url -or
    $Runtime.pico_sdk.tag -ne $Pin.tag -or
    $Runtime.pico_sdk.commit -ne $Pin.commit -or
    -not [IO.Path]::IsPathRooted($Runtime.pico_sdk.path) -or
    -not (Test-Path -LiteralPath $Runtime.pico_sdk.path)) {
  throw 'run bootstrap_pico_sdk.ps1 first'
}

$RequiredKeys = @(
  'git_path','bash_path','uv_path','python_path','node_path','npm_cli_path','cmake_path','ctest_path','ninja_path',
  'vs_install_path','vsdevcmd_path','cl_path','link_path','lib_path','rc_path','mt_path','arm_toolchain_root',
  'arm_gcc_path','arm_gxx_path','arm_asm_path','arm_as_path','arm_ar_path','arm_ranlib_path','arm_ld_path',
  'arm_objcopy_path','arm_size_path'
)
foreach ($Key in $RequiredKeys) {
  $Path = $Runtime.paths.$Key
  if (-not $Path -or -not [IO.Path]::IsPathRooted($Path) -or -not (Test-Path -LiteralPath $Path) -or -not $Runtime.versions.$Key) {
    throw "invalid runtime closure: $Key"
  }
}

$AsciiRoot = $null
foreach ($Letter in 'P','Q','R','S','T','U','V','W','X','Y','Z') {
  $Candidate = "${Letter}:\"
  $CandidateAuthority = Join-Path $Candidate 'tools/platform-manifest.json'
  $CandidateRuntime = Join-Path $Candidate '.runtime/toolchain.json'
  if ((Test-Path -LiteralPath $CandidateAuthority) -and (Test-Path -LiteralPath $CandidateRuntime) -and
      (Get-FileHash -LiteralPath $CandidateAuthority -Algorithm SHA256).Hash -eq $ExpectedManifestHash -and
      (Get-FileHash -LiteralPath $CandidateRuntime -Algorithm SHA256).Hash -eq (Get-FileHash -LiteralPath $RuntimePath -Algorithm SHA256).Hash) {
    $AsciiRoot = $Candidate
    break
  }
}
if (-not $AsciiRoot) { throw 'verified ASCII workspace drive is required for the Windows Arm GNU toolchain' }

function Get-EffectivePath([string]$Path) {
  $FullPath = [IO.Path]::GetFullPath($Path)
  $RootPath = [IO.Path]::GetFullPath($Root).TrimEnd('\')
  if ($FullPath.StartsWith($RootPath + '\', [StringComparison]::OrdinalIgnoreCase) -or $FullPath -eq $RootPath) {
    return Join-Path $AsciiRoot $FullPath.Substring($RootPath.Length).TrimStart('\')
  }
  return $FullPath
}

$EffectivePaths = @{}
foreach ($Key in $RequiredKeys) { $EffectivePaths[$Key] = Get-EffectivePath $Runtime.paths.$Key }

$GitPath = $Runtime.paths.git_path
$SdkPath = [IO.Path]::GetFullPath([string]$Runtime.pico_sdk.path)
$EffectiveSdkPath = Get-EffectivePath $SdkPath
$Origin = (& $GitPath -C $SdkPath remote get-url origin).Trim()
$Commit = (& $GitPath -C $SdkPath rev-parse HEAD).Trim()
$Tag = (& $GitPath -C $SdkPath describe --tags --exact-match).Trim()
if ($LASTEXITCODE -or $Origin -ne $Pin.url -or $Commit -ne $Pin.commit -or $Tag -ne $Pin.tag) {
  throw 'Pico SDK identity mismatch'
}
$SubmoduleState = & $GitPath -C $SdkPath submodule status --recursive
if ($LASTEXITCODE -or $SubmoduleState | Where-Object { $_ -match '^[\-+U]' }) { throw 'Pico SDK submodule mismatch' }
$Dirty = & $GitPath -C $SdkPath status --porcelain=v1 --untracked-files=all --ignore-submodules=all
if ($LASTEXITCODE -or $Dirty) { throw 'Pico SDK working tree is dirty' }
foreach ($Line in $SubmoduleState) {
  $Parts = $Line.Trim().Split([char[]]@(' ', "`t"), [StringSplitOptions]::RemoveEmptyEntries)
  $SubmodulePath = Join-Path $SdkPath $Parts[1]
  $Dirty = & $GitPath -C $SubmodulePath status --porcelain=v1 --untracked-files=all --ignore-submodules=all
  if ($LASTEXITCODE -or $Dirty) { throw "Pico SDK submodule is dirty: $($Parts[1])" }
}

foreach ($Key in @('arm_gcc_path','arm_gxx_path','arm_asm_path','arm_as_path','arm_ar_path','arm_ranlib_path','arm_ld_path','arm_objcopy_path','arm_size_path')) {
  $VersionLine = (& $Runtime.paths.$Key --version | Select-Object -First 1)
  $ExpectedVersion = if ($Key -in @('arm_gcc_path','arm_gxx_path','arm_asm_path')) { '14\.2\.1' } else { '2\.43\.1\.20241119' }
  if ($LASTEXITCODE -or $VersionLine -notmatch 'Arm GNU Toolchain 14\.2\.Rel1' -or $VersionLine -notmatch $ExpectedVersion) {
    throw "ARM tool identity mismatch: $Key"
  }
}
if ((& $Runtime.paths.cmake_path --version | Select-Object -First 1) -notmatch '4\.3\.4') { throw 'CMake identity mismatch' }
if ((& $Runtime.paths.ninja_path --version) -ne '1.13.2') { throw 'Ninja identity mismatch' }

if ($DryRun) {
  Write-Output "Pico dry-run PASS: SDK $Tag@$Commit, board=$($Pin.board), platform=$($Pin.platform)"
  foreach ($Key in $RequiredKeys) { Write-Output "$Key=$($Runtime.paths.$Key)" }
  exit 0
}

$SecretsPath = Join-Path $Root 'firmware/pico_pet_node/pico/include/petcare_secrets.hpp'
if ($Hardware) {
  $ServicesPath = Join-Path $Root '.runtime/services.json'
  if (-not (Test-Path -LiteralPath $ServicesPath)) { throw 'services runtime is missing' }
  $Services = Get-Content -Raw -Encoding UTF8 -LiteralPath $ServicesPath | ConvertFrom-Json
  $Endpoint = $Services.mqtt_profiles.hardware
  if (-not $Endpoint -or -not $Endpoint.client_host -or -not $Endpoint.port) { throw 'hardware MQTT profile is not configured' }
  foreach ($Name in @('PETCARE_WIFI_SSID','PETCARE_WIFI_PASSWORD','PETCARE_MQTT_USERNAME','PETCARE_MQTT_PASSWORD')) {
    if (-not [Environment]::GetEnvironmentVariable($Name)) { throw "missing hardware environment value: $Name" }
  }

  function ConvertTo-CppBytes([string]$Value) {
    $Builder = [Text.StringBuilder]::new()
    foreach ($Byte in [Text.Encoding]::UTF8.GetBytes($Value)) {
      if ($Byte -ge 32 -and $Byte -le 126 -and $Byte -notin @(34,92)) { [void]$Builder.Append([char]$Byte) }
      elseif ($Byte -eq 34) { [void]$Builder.Append('\"') }
      elseif ($Byte -eq 92) { [void]$Builder.Append('\\') }
      else { [void]$Builder.Append(('\' + [Convert]::ToString($Byte, 8).PadLeft(3, '0'))) }
    }
    $Builder.ToString()
  }

  $Header = @"
#pragma once
#include <cstdint>
namespace petcare::secrets {
inline constexpr char wifi_ssid[] = "$(ConvertTo-CppBytes $env:PETCARE_WIFI_SSID)";
inline constexpr char wifi_password[] = "$(ConvertTo-CppBytes $env:PETCARE_WIFI_PASSWORD)";
inline constexpr char mqtt_host[] = "$(ConvertTo-CppBytes ([string]$Endpoint.client_host))";
inline constexpr std::uint16_t mqtt_port = $([int]$Endpoint.port);
inline constexpr char mqtt_username[] = "$(ConvertTo-CppBytes $env:PETCARE_MQTT_USERNAME)";
inline constexpr char mqtt_password[] = "$(ConvertTo-CppBytes $env:PETCARE_MQTT_PASSWORD)";
}
"@
  if (-not (Test-Path -LiteralPath $SecretsPath)) {
    [IO.File]::WriteAllBytes($SecretsPath, [byte[]]::new(0))
  }
  $Acl = [Security.AccessControl.FileSecurity]::new()
  $Acl.SetAccessRuleProtection($true, $false)
  $User = [Security.Principal.WindowsIdentity]::GetCurrent().User
  $Acl.AddAccessRule([Security.AccessControl.FileSystemAccessRule]::new($User, 'FullControl', 'Allow'))
  Set-Acl -LiteralPath $SecretsPath -AclObject $Acl
  try {
    [IO.File]::WriteAllText($SecretsPath, $Header, [Text.UTF8Encoding]::new($false))
  } catch {
    [IO.File]::WriteAllBytes($SecretsPath, [byte[]]::new(0))
    throw
  }
} else {
  [IO.File]::Copy(
    (Join-Path $Root 'firmware/pico_pet_node/pico/include/petcare_secrets.example.hpp'),
    $SecretsPath,
    $true
  )
}

function Get-CacheValue([string]$CachePath, [string]$Name) {
  $Match = Select-String -LiteralPath $CachePath -Pattern "^$([regex]::Escape($Name)):[^=]+=(.*)$" | Select-Object -First 1
  if (-not $Match) { throw "missing CMake cache value: $Name" }
  $Match.Matches[0].Groups[1].Value
}

function Assert-CachePath([string]$CachePath, [string]$Name, [string]$Expected) {
  $Actual = Get-CacheValue $CachePath $Name
  if ([IO.Path]::GetFullPath($Actual) -ne [IO.Path]::GetFullPath($Expected)) {
    throw "CMake tool path mismatch: $Name"
  }
}

$Targets = if ($Profile -eq 'all') { @('entrance-01','petzone-01') } else { @($Profile) }
$SourcePath = Get-EffectivePath (Join-Path $Root 'firmware/pico_pet_node/pico')
$BuildRoot = Get-EffectivePath ([IO.Path]::GetFullPath($BuildRoot))
$OldPath = $env:PATH
$OldCC = $env:CC
$OldCXX = $env:CXX
$OldRC = $env:RC
$OldPicoMbedTlsPath = $env:PICO_MBEDTLS_PATH
try {
  foreach ($Property in $Runtime.environment.PSObject.Properties) {
    if ($Property.Name -ne 'Path') { Set-Item -Path "Env:$($Property.Name)" -Value $Property.Value }
  }
  $env:CC = $EffectivePaths.cl_path
  $env:CXX = $EffectivePaths.cl_path
  $env:RC = $Runtime.paths.rc_path
  $env:PICO_MBEDTLS_PATH = Get-EffectivePath (Join-Path $Root '.runtime/picotool-no-mbedtls')
  $env:PATH = @(
    "$env:SystemRoot\System32",
    (Split-Path -Parent $EffectivePaths.cl_path),
    (Split-Path -Parent $Runtime.paths.rc_path),
    (Split-Path -Parent $EffectivePaths.cmake_path),
    (Split-Path -Parent $EffectivePaths.ninja_path)
  ) -join ';'
  foreach ($Target in $Targets) {
    $BuildPath = Join-Path $BuildRoot $Target
    $Arguments = @(
      '-S', $SourcePath, '-B', $BuildPath, '-G', 'Ninja',
      "-DPICO_SDK_PATH=$EffectiveSdkPath", "-DPICO_BOARD=$($Pin.board)", "-DPICO_PLATFORM=$($Pin.platform)",
      "-DPICO_TOOLCHAIN_PATH=$($EffectivePaths.arm_toolchain_root)",
      "-DCMAKE_MAKE_PROGRAM=$($EffectivePaths.ninja_path)",
      "-DCMAKE_C_COMPILER=$($EffectivePaths.arm_gcc_path)",
      "-DCMAKE_CXX_COMPILER=$($EffectivePaths.arm_gxx_path)",
      "-DCMAKE_ASM_COMPILER=$($EffectivePaths.arm_asm_path)",
      "-DCMAKE_AR=$($EffectivePaths.arm_ar_path)",
      "-DCMAKE_RANLIB=$($EffectivePaths.arm_ranlib_path)",
      "-DCMAKE_LINKER=$($EffectivePaths.arm_ld_path)",
      "-DCMAKE_OBJCOPY=$($EffectivePaths.arm_objcopy_path)",
      "-DCMAKE_SIZE=$($EffectivePaths.arm_size_path)",
      "-DPython3_EXECUTABLE=$($EffectivePaths.python_path)",
      '-DCMAKE_BUILD_TYPE=Release'
    )
    & $EffectivePaths.cmake_path @Arguments
    if ($LASTEXITCODE) { throw "Pico configure failed: $Target" }
    & $EffectivePaths.cmake_path --build $BuildPath --target $Target
    if ($LASTEXITCODE) { throw "Pico build failed: $Target" }

    $CachePath = Join-Path $BuildPath 'CMakeCache.txt'
    if ((Get-CacheValue $CachePath 'PICO_BOARD') -ne $Pin.board -or
        (Get-CacheValue $CachePath 'PICO_PLATFORM') -ne $Pin.resolved_platform) {
      throw "Pico board/platform cache mismatch: $Target"
    }
    Assert-CachePath $CachePath 'CMAKE_C_COMPILER' $EffectivePaths.arm_gcc_path
    Assert-CachePath $CachePath 'CMAKE_CXX_COMPILER' $EffectivePaths.arm_gxx_path
    Assert-CachePath $CachePath 'CMAKE_ASM_COMPILER' $EffectivePaths.arm_asm_path
    Assert-CachePath $CachePath 'CMAKE_AR' $EffectivePaths.arm_ar_path
    Assert-CachePath $CachePath 'CMAKE_RANLIB' $EffectivePaths.arm_ranlib_path
    Assert-CachePath $CachePath 'CMAKE_LINKER' $EffectivePaths.arm_ld_path
    Assert-CachePath $CachePath 'CMAKE_OBJCOPY' $EffectivePaths.arm_objcopy_path
    Assert-CachePath $CachePath 'CMAKE_SIZE' $EffectivePaths.arm_size_path

    $Artifacts = [ordered]@{}
    foreach ($Extension in @('uf2','elf','map')) {
      $ArtifactName = if ($Extension -eq 'map') { "$Target.elf.map" } else { "$Target.$Extension" }
      $Artifact = Join-Path $BuildPath $ArtifactName
      if (-not (Test-Path -LiteralPath $Artifact) -or (Get-Item -LiteralPath $Artifact).Length -eq 0) {
        throw "missing Pico artifact: $Target.$Extension"
      }
      $Artifacts[$Extension] = [ordered]@{
        path = [IO.Path]::GetFullPath($Artifact)
        bytes = (Get-Item -LiteralPath $Artifact).Length
        sha256 = (Get-FileHash -LiteralPath $Artifact -Algorithm SHA256).Hash
      }
    }
    $Tools = [ordered]@{}
    foreach ($Key in $RequiredKeys) {
      $Value = $Runtime.paths.$Key
      $Tools[$Key] = [ordered]@{manifest_path=$Value; effective_path=$EffectivePaths[$Key]; version=$Runtime.versions.$Key}
      if (Test-Path -LiteralPath $Value -PathType Leaf) { $Tools[$Key].sha256 = (Get-FileHash -LiteralPath $Value -Algorithm SHA256).Hash }
    }
    $Metadata = [ordered]@{
      profile = $Target
      requested_board = [string]$Pin.board
      requested_platform = [string]$Pin.platform
      resolved_board = Get-CacheValue $CachePath 'PICO_BOARD'
      resolved_platform = Get-CacheValue $CachePath 'PICO_PLATFORM'
      pico_sdk = $Runtime.pico_sdk
      tools = $Tools
      artifacts = $Artifacts
    }
    [IO.File]::WriteAllText(
      (Join-Path $BuildPath 'build-metadata.json'),
      ($Metadata | ConvertTo-Json -Depth 12),
      [Text.UTF8Encoding]::new($false)
    )
    Write-Output "Pico RP2350 build PASS: $Target"
  }
} finally {
  $env:PATH = $OldPath
  $env:CC = $OldCC
  $env:CXX = $OldCXX
  $env:RC = $OldRC
  $env:PICO_MBEDTLS_PATH = $OldPicoMbedTlsPath
}
