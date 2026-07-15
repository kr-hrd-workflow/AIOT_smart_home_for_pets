[CmdletBinding()]
param(
  [string]$ToolchainRuntime = '',
  [string]$OutputPath = '',
  [string]$FixtureRoot = '',
  [string]$HardwareAddress = '',
  [switch]$CheckOnly
)

$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent $PSScriptRoot
if (-not $ToolchainRuntime) { $ToolchainRuntime = Join-Path $Root '.runtime/toolchain.json' }
if (-not $OutputPath) { $OutputPath = Join-Path $Root '.runtime/services.json' }
$manifestPath = Join-Path $PSScriptRoot 'platform-manifest.json'
$manifest = Get-Content -Raw -Encoding UTF8 -LiteralPath $manifestPath | ConvertFrom-Json
$manifestHash = (Get-FileHash -LiteralPath $manifestPath -Algorithm SHA256).Hash
$postgresVersion = $manifest.managed_exact.postgresql.version
$mosquittoInput = $manifest.managed_exact.mosquitto
$mosquittoVersion = $mosquittoInput.version
$pahoVersion = $manifest.managed_exact.backend_dependencies.'paho-mqtt'
$capabilityVersions = [ordered]@{}

function Test-Rfc1918([string]$Address) {
  $parsed = $null
  if (-not [Net.IPAddress]::TryParse($Address, [ref]$parsed) -or $parsed.AddressFamily -ne [Net.Sockets.AddressFamily]::InterNetwork) { return $false }
  $bytes = $parsed.GetAddressBytes()
  return $bytes[0] -eq 10 -or ($bytes[0] -eq 172 -and $bytes[1] -ge 16 -and $bytes[1] -le 31) -or ($bytes[0] -eq 192 -and $bytes[1] -eq 168)
}

function Write-ServicesManifest([hashtable]$Paths, [bool]$Fixture) {
  $profiles = [ordered]@{
    local_live = [ordered]@{bind_host='127.0.0.1';port=18883;client_host='127.0.0.1'}
  }
  if ($HardwareAddress) {
    if (-not (Test-Rfc1918 $HardwareAddress)) { throw 'hardware address must be one explicit RFC1918 IPv4 address' }
    if (-not $Fixture -and -not (Get-NetIPAddress -AddressFamily IPv4 -IPAddress $HardwareAddress -ErrorAction SilentlyContinue)) { throw 'hardware address is not assigned to a local interface' }
    $profiles.hardware = [ordered]@{bind_host=$HardwareAddress;port=18883;client_host=$HardwareAddress}
  }
  $versions = [ordered]@{postgresql=$postgresVersion;mosquitto=$mosquittoVersion;cjson='1.7.19';openssl='3.5.7';paho_mqtt=$pahoVersion}
  foreach ($property in $capabilityVersions.GetEnumerator()) { $versions[$property.Key] = $property.Value }
  $runtime = [ordered]@{
    schema_version = 1
    manifest_sha256 = $manifestHash
    fixture = $Fixture
    paths = $Paths
    versions = $versions
    mqtt_profiles = $profiles
    ports = [ordered]@{postgresql=55432;mqtt=18883}
  }
  $parent = Split-Path -Parent $OutputPath
  New-Item -ItemType Directory -Force -Path $parent | Out-Null
  [IO.File]::WriteAllText($OutputPath, ($runtime | ConvertTo-Json -Depth 12), [Text.UTF8Encoding]::new($false))
}

if ($FixtureRoot) {
  $FixtureRoot = [IO.Path]::GetFullPath($FixtureRoot)
  New-Item -ItemType Directory -Force -Path $FixtureRoot | Out-Null
  $paths = [ordered]@{}
  foreach ($name in @('postgres','pg_ctl','initdb','pg_isready','psql','mosquitto','mosquitto_passwd','python')) {
    $path = Join-Path $FixtureRoot "$name.cmd"
    [IO.File]::WriteAllText($path, "@echo fixture-$name`r`n@exit /b 0`r`n", [Text.ASCIIEncoding]::new())
    $paths["${name}_path"] = $path
  }
  Write-ServicesManifest $paths $true
  Write-Output "Service bootstrap fixture PASS: $OutputPath"
  exit 0
}

if (-not (Test-Path -LiteralPath $ToolchainRuntime)) { throw 'run bootstrap_toolchain.ps1 first' }
$toolchain = Get-Content -Raw -Encoding UTF8 -LiteralPath $ToolchainRuntime | ConvertFrom-Json
if ($toolchain.manifest_sha256 -ne $manifestHash) { throw 'toolchain authority hash mismatch' }

$managedRoot = Join-Path $Root '.runtime/services-managed'
$cacheRoot = Join-Path $Root '.runtime/bootstrap-cache'
$postgresRoot = Join-Path $managedRoot 'postgresql'
$postgresArchive = Join-Path $cacheRoot "postgresql-$postgresVersion-windows-x64-binaries.zip"
$postgresUrl = $manifest.managed_exact.postgresql.windows_url
$postgresSha256 = $manifest.managed_exact.postgresql.windows_sha256
New-Item -ItemType Directory -Force -Path $managedRoot,$cacheRoot | Out-Null
if (-not (Test-Path -LiteralPath $postgresArchive)) {
  if ($CheckOnly) { throw 'managed PostgreSQL archive is missing' }
  Invoke-WebRequest -UseBasicParsing -Uri $postgresUrl -OutFile $postgresArchive
}
if ((Get-FileHash -LiteralPath $postgresArchive -Algorithm SHA256).Hash -ne $postgresSha256) { throw 'PostgreSQL archive SHA-256 mismatch' }
$postgresBin = Join-Path $postgresRoot 'pgsql/bin'
$postgresExecutables = @('postgres.exe','pg_ctl.exe','initdb.exe','pg_isready.exe','psql.exe')
$postgresStampPath = Join-Path $managedRoot 'postgresql-build.json'
$postgresReady = $false
if (Test-Path -LiteralPath $postgresStampPath) {
  $postgresStamp = Get-Content -Raw -Encoding UTF8 -LiteralPath $postgresStampPath | ConvertFrom-Json
  $postgresReady = $postgresStamp.archive_sha256 -eq $postgresSha256
  foreach ($name in $postgresExecutables) {
    $binary = Join-Path $postgresBin $name
    $property = $name.Replace('.exe','_sha256')
    if (-not (Test-Path -LiteralPath $binary) -or $postgresStamp.$property -ne (Get-FileHash -LiteralPath $binary -Algorithm SHA256).Hash) { $postgresReady = $false; break }
  }
}
if (-not $postgresReady) {
  if ($CheckOnly) { throw 'managed PostgreSQL extraction stamp is missing or mismatched' }
  if (Test-Path -LiteralPath $postgresRoot) { Remove-Item -LiteralPath $postgresRoot -Recurse -Force }
  Expand-Archive -LiteralPath $postgresArchive -DestinationPath $postgresRoot
  $postgresStamp = [ordered]@{archive_sha256=$postgresSha256}
  foreach ($name in $postgresExecutables) {
    $binary = Join-Path $postgresBin $name
    if (-not (Test-Path -LiteralPath $binary)) { throw "PostgreSQL extraction is missing $name" }
    $postgresStamp[$name.Replace('.exe','_sha256')] = (Get-FileHash -LiteralPath $binary -Algorithm SHA256).Hash
  }
  [IO.File]::WriteAllText($postgresStampPath, ($postgresStamp | ConvertTo-Json), [Text.UTF8Encoding]::new($false))
}

$gitPath = $toolchain.paths.git_path
$cmakePath = $toolchain.paths.cmake_path
$ninjaPath = $toolchain.paths.ninja_path
$msvcBin = Split-Path -Parent $toolchain.paths.cl_path
$nmakePath = Join-Path $msvcBin 'nmake.exe'
foreach ($path in @($gitPath,$cmakePath,$ninjaPath,$nmakePath,$toolchain.paths.cl_path,$toolchain.paths.rc_path,$toolchain.paths.mt_path)) {
  if (-not [IO.Path]::IsPathRooted($path) -or -not (Test-Path -LiteralPath $path)) { throw 'service source-build tool closure is incomplete' }
}
foreach ($property in $toolchain.environment.PSObject.Properties) { Set-Item -Path "Env:$($property.Name)" -Value $property.Value }

function Assert-ManagedChild([string]$Path) {
  $resolved = [IO.Path]::GetFullPath($Path)
  $allowed = [IO.Path]::GetFullPath($managedRoot)
  if (-not $resolved.StartsWith($allowed + [IO.Path]::DirectorySeparatorChar, [StringComparison]::OrdinalIgnoreCase)) { throw "managed path escaped runtime: $Path" }
  return $resolved
}

function Get-ExactSource([string]$Name, [string]$Url, [string]$Ref, [string]$Commit, [string]$Destination, [switch]$Fresh) {
  $Destination = Assert-ManagedChild $Destination
  if ($Fresh -and (Test-Path -LiteralPath (Join-Path $Destination '.git'))) {
    $existingCommit = (& $gitPath -C $Destination rev-parse HEAD).Trim()
    $dirty = [string](& $gitPath -C $Destination status --porcelain)
    if ($LASTEXITCODE -or $existingCommit -ne $Commit -or -not [string]::IsNullOrWhiteSpace($dirty)) { throw "$Name existing source checkout is not the exact clean commit" }
  } elseif ($Fresh -and (Test-Path -LiteralPath $Destination)) {
    Remove-Item -LiteralPath $Destination -Recurse -Force
  }
  if (-not (Test-Path -LiteralPath (Join-Path $Destination '.git'))) {
    & $gitPath clone --quiet --depth 1 --branch $Ref $Url $Destination
    if ($LASTEXITCODE) { throw "$Name source checkout failed" }
  }
  $actual = (& $gitPath -C $Destination rev-parse HEAD).Trim()
  if ($LASTEXITCODE -or $actual -ne $Commit) { throw "$Name source commit mismatch" }
  return $Destination
}

function Slash([string]$Path) { return $Path.Replace('\','/') }
function Get-Sha256([string]$Path) { return (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash }

$mosquittoRoot = Join-Path $managedRoot 'mosquitto'
$mosquittoPath = Join-Path $mosquittoRoot 'mosquitto.exe'
$mosquittoPasswdPath = Join-Path $mosquittoRoot 'mosquitto_passwd.exe'
$mosquittoCommonPath = Join-Path $mosquittoRoot 'mosquitto_common.dll'
$packagedCjsonPath = Join-Path $mosquittoRoot 'cjson.dll'
$opensslRoot = Assert-ManagedChild (Join-Path $managedRoot 'openssl')
$cjsonRoot = Assert-ManagedChild (Join-Path $managedRoot 'cjson')
$opensslLibPath = Join-Path $opensslRoot 'lib/libssl.lib'
$opensslCryptoPath = Join-Path $opensslRoot 'lib/libcrypto.lib'
$cjsonLibPath = Join-Path $cjsonRoot 'lib/cjson.lib'
$cjsonDllPath = Join-Path $cjsonRoot 'bin/cjson.dll'
$dependencyStampPath = Join-Path $managedRoot 'mosquitto-dependencies.json'
$dependenciesReady = $false
if ((Test-Path -LiteralPath $dependencyStampPath) -and (Test-Path -LiteralPath $opensslLibPath) -and (Test-Path -LiteralPath $opensslCryptoPath) -and (Test-Path -LiteralPath $cjsonLibPath) -and (Test-Path -LiteralPath $cjsonDllPath)) {
  $dependencyStamp = Get-Content -Raw -Encoding UTF8 -LiteralPath $dependencyStampPath | ConvertFrom-Json
  $dependenciesReady = $dependencyStamp.openssl_commit -eq $mosquittoInput.openssl_commit -and $dependencyStamp.cjson_commit -eq $mosquittoInput.cjson_commit -and $dependencyStamp.libssl_sha256 -eq (Get-Sha256 $opensslLibPath) -and $dependencyStamp.libcrypto_sha256 -eq (Get-Sha256 $opensslCryptoPath) -and $dependencyStamp.cjson_lib_sha256 -eq (Get-Sha256 $cjsonLibPath) -and $dependencyStamp.cjson_dll_sha256 -eq (Get-Sha256 $cjsonDllPath)
}
$buildStampPath = Join-Path $managedRoot 'mosquitto-build.json'
$buildReady = $false
if ($dependenciesReady -and (Test-Path -LiteralPath $buildStampPath) -and (Test-Path -LiteralPath $mosquittoPath) -and (Test-Path -LiteralPath $mosquittoPasswdPath) -and (Test-Path -LiteralPath $mosquittoCommonPath) -and (Test-Path -LiteralPath $packagedCjsonPath)) {
  $stamp = Get-Content -Raw -Encoding UTF8 -LiteralPath $buildStampPath | ConvertFrom-Json
  $buildReady = $stamp.manifest_sha256 -eq $manifestHash -and $stamp.mosquitto_commit -eq $mosquittoInput.source_commit -and $stamp.openssl_commit -eq $mosquittoInput.openssl_commit -and $stamp.cjson_commit -eq $mosquittoInput.cjson_commit -and $stamp.mosquitto_sha256 -eq (Get-Sha256 $mosquittoPath) -and $stamp.mosquitto_passwd_sha256 -eq (Get-Sha256 $mosquittoPasswdPath) -and $stamp.mosquitto_common_sha256 -eq (Get-Sha256 $mosquittoCommonPath) -and $stamp.cjson_dll_sha256 -eq (Get-Sha256 $packagedCjsonPath)
}
if (-not $buildReady) {
  if ($CheckOnly) { throw "managed Mosquitto $mosquittoVersion build is missing" }

  $perlArchive = Join-Path $cacheRoot 'strawberry-perl-5.42.2.1-64bit-portable.zip'
  if (-not (Test-Path -LiteralPath $perlArchive)) { Invoke-WebRequest -UseBasicParsing -Uri $mosquittoInput.perl_url -OutFile $perlArchive }
  if ((Get-FileHash -LiteralPath $perlArchive -Algorithm SHA256).Hash -ne $mosquittoInput.perl_sha256) { throw 'Strawberry Perl SHA-256 mismatch' }
  $perlRoot = Assert-ManagedChild (Join-Path $managedRoot 'strawberry-perl')
  $perlPath = Join-Path $perlRoot 'perl/bin/perl.exe'
  if (-not (Test-Path -LiteralPath $perlPath)) {
    if (Test-Path -LiteralPath $perlRoot) { Remove-Item -LiteralPath $perlRoot -Recurse -Force }
    Expand-Archive -LiteralPath $perlArchive -DestinationPath $perlRoot
  }
  if ((& $perlPath -e 'print $^V') -ne 'v5.42.2') { throw 'Strawberry Perl version mismatch' }

  if (-not $dependenciesReady) {
    if (Test-Path -LiteralPath $opensslRoot) { Remove-Item -LiteralPath $opensslRoot -Recurse -Force }
    $opensslSource = Get-ExactSource 'OpenSSL' $mosquittoInput.openssl_source_url $mosquittoInput.openssl_ref $mosquittoInput.openssl_commit (Join-Path $managedRoot 'openssl-source-pristine') -Fresh
    $opensslBuild = Assert-ManagedChild (Join-Path $managedRoot 'openssl-build')
    if (Test-Path -LiteralPath $opensslBuild) { Remove-Item -LiteralPath $opensslBuild -Recurse -Force }
    New-Item -ItemType Directory -Force -Path $opensslBuild | Out-Null
    $oldPath = $env:Path
    $oldLcAll = $env:LC_ALL
    $oldLang = $env:LANG
    try {
      $env:Path = "$msvcBin;$perlRoot\perl\bin;$perlRoot\c\bin;$oldPath"
      $env:LC_ALL = ''
      $env:LANG = ''
      Push-Location $opensslBuild
      try {
        & $perlPath (Join-Path $opensslSource 'Configure') 'VC-WIN64A' 'no-shared' 'no-tests' 'no-asm' "--prefix=$opensslRoot" "--openssldir=$opensslRoot\ssl"
        if ($LASTEXITCODE) { throw 'OpenSSL configure failed' }
        & $nmakePath build_libs
        if ($LASTEXITCODE) { throw 'OpenSSL build failed' }
        & $nmakePath install_dev
        if ($LASTEXITCODE) { throw 'OpenSSL install failed' }
      } finally { Pop-Location }
    } finally {
      $env:Path = $oldPath
      $env:LC_ALL = $oldLcAll
      $env:LANG = $oldLang
    }
    if (Test-Path -LiteralPath $cjsonRoot) { Remove-Item -LiteralPath $cjsonRoot -Recurse -Force }
    $cjsonSource = Get-ExactSource 'cJSON' $mosquittoInput.cjson_source_url $mosquittoInput.cjson_ref $mosquittoInput.cjson_commit (Join-Path $managedRoot 'cjson-source') -Fresh
    $cjsonBuild = Assert-ManagedChild (Join-Path $managedRoot 'cjson-build')
    if (Test-Path -LiteralPath $cjsonBuild) { Remove-Item -LiteralPath $cjsonBuild -Recurse -Force }
    & $cmakePath -S $cjsonSource -B $cjsonBuild -G Ninja "-DCMAKE_MAKE_PROGRAM=$(Slash $ninjaPath)" "-DCMAKE_C_COMPILER=$(Slash $toolchain.paths.cl_path)" "-DCMAKE_RC_COMPILER:FILEPATH=$(Slash $toolchain.paths.rc_path)" "-DCMAKE_MT:FILEPATH=$(Slash $toolchain.paths.mt_path)" "-DCMAKE_INSTALL_PREFIX=$(Slash $cjsonRoot)" '-DCMAKE_POLICY_VERSION_MINIMUM=3.5' '-DBUILD_SHARED_LIBS=ON' '-DENABLE_CJSON_TEST=OFF' '-DENABLE_CJSON_UTILS=OFF'
    if ($LASTEXITCODE) { throw 'cJSON configure failed' }
    & $cmakePath --build $cjsonBuild --target install --parallel 4
    if ($LASTEXITCODE) { throw 'cJSON build failed' }
    $dependencyStamp = [ordered]@{
      openssl_commit = $mosquittoInput.openssl_commit
      cjson_commit = $mosquittoInput.cjson_commit
      libssl_sha256 = Get-Sha256 $opensslLibPath
      libcrypto_sha256 = Get-Sha256 $opensslCryptoPath
      cjson_lib_sha256 = Get-Sha256 $cjsonLibPath
      cjson_dll_sha256 = Get-Sha256 $cjsonDllPath
    }
    [IO.File]::WriteAllText($dependencyStampPath, ($dependencyStamp | ConvertTo-Json), [Text.UTF8Encoding]::new($false))
  }

  $mosquittoSource = Get-ExactSource 'Mosquitto' $mosquittoInput.source_url $mosquittoInput.source_ref $mosquittoInput.source_commit (Join-Path $managedRoot 'mosquitto-source') -Fresh
  $mosquittoBuild = Assert-ManagedChild (Join-Path $managedRoot 'mosquitto-build')
  if (Test-Path -LiteralPath $mosquittoBuild) { Remove-Item -LiteralPath $mosquittoBuild -Recurse -Force }
  & $cmakePath -S $mosquittoSource -B $mosquittoBuild -G Ninja "-DCMAKE_MAKE_PROGRAM=$(Slash $ninjaPath)" "-DCMAKE_C_COMPILER=$(Slash $toolchain.paths.cl_path)" "-DCMAKE_CXX_COMPILER=$(Slash $toolchain.paths.cl_path)" "-DCMAKE_RC_COMPILER:FILEPATH=$(Slash $toolchain.paths.rc_path)" "-DCMAKE_MT:FILEPATH=$(Slash $toolchain.paths.mt_path)" "-DOPENSSL_ROOT_DIR=$(Slash $opensslRoot)" '-DOPENSSL_USE_STATIC_LIBS=TRUE' "-DCJSON_INCLUDE_DIR=$(Slash (Join-Path $cjsonRoot 'include'))" "-DCJSON_LIBRARY=$(Slash (Join-Path $cjsonRoot 'lib/cjson.lib'))" '-DWITH_TESTS=OFF' '-DWITH_CLIENTS=OFF' '-DWITH_PLUGINS=OFF' '-DWITH_DOCS=OFF' '-DWITH_THREADING=OFF' '-DWITH_CTRL_SHELL=OFF' '-DWITH_LTO=OFF' '-DINC_MEMTRACK=OFF'
  if ($LASTEXITCODE) { throw 'Mosquitto configure failed' }
  & $cmakePath --build $mosquittoBuild --target mosquitto mosquitto_passwd --parallel 4
  if ($LASTEXITCODE) { throw 'Mosquitto build failed' }
  if (Test-Path -LiteralPath $mosquittoRoot) { Remove-Item -LiteralPath (Assert-ManagedChild $mosquittoRoot) -Recurse -Force }
  New-Item -ItemType Directory -Force -Path $mosquittoRoot | Out-Null
  Copy-Item -LiteralPath (Join-Path $mosquittoBuild 'src/mosquitto.exe') -Destination $mosquittoPath
  Copy-Item -LiteralPath (Join-Path $mosquittoBuild 'apps/mosquitto_passwd/mosquitto_passwd.exe') -Destination $mosquittoPasswdPath
  Copy-Item -LiteralPath (Join-Path $mosquittoBuild 'libcommon/mosquitto_common.dll') -Destination $mosquittoRoot
  Copy-Item -LiteralPath (Join-Path $cjsonRoot 'bin/cjson.dll') -Destination $mosquittoRoot
  $stamp = [ordered]@{
    manifest_sha256 = $manifestHash
    mosquitto_commit = $mosquittoInput.source_commit
    openssl_commit = $mosquittoInput.openssl_commit
    cjson_commit = $mosquittoInput.cjson_commit
    mosquitto_sha256 = Get-Sha256 $mosquittoPath
    mosquitto_passwd_sha256 = Get-Sha256 $mosquittoPasswdPath
    mosquitto_common_sha256 = Get-Sha256 $mosquittoCommonPath
    cjson_dll_sha256 = Get-Sha256 $packagedCjsonPath
  }
  [IO.File]::WriteAllText($buildStampPath, ($stamp | ConvertTo-Json), [Text.UTF8Encoding]::new($false))
}
$previousPreference = $ErrorActionPreference
$ErrorActionPreference = 'Continue'
try { $mosquittoHelp = @(& $mosquittoPath -h 2>&1) } finally { $ErrorActionPreference = $previousPreference }
$versionLine = $mosquittoHelp | Where-Object { $_ -match '^mosquitto version\s+(\S+)' } | Select-Object -First 1
$installedMosquitto = if ($versionLine) { $versionLine -replace '^mosquitto version\s+','' } else { '' }
if ($installedMosquitto -ne $mosquittoVersion) { throw 'managed Mosquitto version mismatch' }

$pythonPath = $toolchain.paths.python_path
$uvPath = $toolchain.paths.uv_path
$installedPaho = & $pythonPath -c "import importlib.metadata;`ntry: print(importlib.metadata.version('paho-mqtt'))`nexcept importlib.metadata.PackageNotFoundError: print('')"
if ($installedPaho -ne $pahoVersion) {
  if ($CheckOnly) { throw "paho-mqtt $pahoVersion is required" }
  & $uvPath pip install --python $pythonPath "paho-mqtt==$pahoVersion"
  if ($LASTEXITCODE) { throw 'paho-mqtt installation failed' }
}

$paths = [ordered]@{
  postgres_path = Join-Path $postgresBin 'postgres.exe'
  pg_ctl_path = Join-Path $postgresBin 'pg_ctl.exe'
  initdb_path = Join-Path $postgresBin 'initdb.exe'
  pg_isready_path = Join-Path $postgresBin 'pg_isready.exe'
  psql_path = Join-Path $postgresBin 'psql.exe'
  mosquitto_path = $mosquittoPath
  mosquitto_passwd_path = Join-Path $mosquittoRoot 'mosquitto_passwd.exe'
  python_path = $pythonPath
}
foreach ($entry in $paths.GetEnumerator()) {
  if (-not [IO.Path]::IsPathRooted($entry.Value) -or -not (Test-Path -LiteralPath $entry.Value)) { throw "missing absolute service path: $($entry.Key)" }
}
if ((& $paths.postgres_path --version) -notmatch '^postgres \(PostgreSQL\) 17\.10') { throw 'PostgreSQL binary version mismatch' }

$docker = Get-Command docker.exe -ErrorAction SilentlyContinue
if ($docker) {
  $composePath = ''
  foreach ($candidate in @(
    "$env:ProgramFiles\Docker\Docker\resources\cli-plugins\docker-compose.exe",
    "$env:USERPROFILE\.docker\cli-plugins\docker-compose.exe"
  )) { if (Test-Path -LiteralPath $candidate) { $composePath = [IO.Path]::GetFullPath($candidate); break } }
  if ($composePath) {
    $dockerText = [string](& $docker.Source --version)
    $composeText = [string](& $composePath version --short)
    $dockerMatch = [regex]::Match($dockerText, '(\d+\.\d+(?:\.\d+)?)')
    $composeMatch = [regex]::Match($composeText, '(\d+\.\d+(?:\.\d+)?)')
    if ($LASTEXITCODE -or -not $dockerMatch.Success -or -not $composeMatch.Success) {
      Write-Warning 'optional Docker/Compose capability could not be verified and will not be recorded'
    } else {
      $dockerVersion = [version]$dockerMatch.Groups[1].Value
      $composeVersion = [version]$composeMatch.Groups[1].Value
      if ($dockerVersion -lt [version]'26.1' -or $composeVersion -lt [version]'2.27') {
      Write-Warning 'optional Docker/Compose capability is below the required minimum and will not be recorded'
      } else {
        $paths.docker_path = $docker.Source
        $paths.compose_plugin_path = $composePath
        $capabilityVersions.docker_version = $dockerVersion.ToString()
        $capabilityVersions.compose_version = $composeVersion.ToString()
      }
    }
  }
}
Write-ServicesManifest $paths $false
Write-Output "Service bootstrap PASS: $OutputPath"
