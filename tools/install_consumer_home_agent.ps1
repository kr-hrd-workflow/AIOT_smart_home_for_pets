[CmdletBinding()]
param(
    [ValidateSet('Install', 'Status', 'Uninstall', 'Fixture')]
    [string]$Action = 'Install',
    [string]$SiteOrigin = 'https://kr-hrd-petcare-aiot-team.cpark333333.chatgpt.site',
    [string]$InstallRoot = '',
    [string]$FixtureRoot = ''
)

$ErrorActionPreference = 'Stop'
$SourceRoot = Split-Path -Parent $PSScriptRoot
$FirewallRuleName = 'PetCare-Pico-MQTT'
$ServiceNames = @('PetCarePostgres', 'PetCareMqtt', 'PetCareHomeAgent')

function Assert-Elevated {
    $principal = [Security.Principal.WindowsPrincipal]::new(
        [Security.Principal.WindowsIdentity]::GetCurrent()
    )
    if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
        throw 'administrator privileges are required'
    }
}

function Assert-SiteOrigin([string]$Value) {
    $uri = [Uri]$Value
    if (
        -not $uri.IsAbsoluteUri -or
        $uri.Scheme -cne 'https' -or
        $uri.AbsolutePath -cne '/' -or
        $uri.Query -or
        $uri.Fragment -or
        $Value.EndsWith('/')
    ) {
        throw 'SiteOrigin must be an HTTPS origin without a path'
    }
}

function Assert-InstallRoot([string]$Path) {
    $programData = [IO.Path]::GetFullPath($env:ProgramData).TrimEnd('\')
    $resolved = [IO.Path]::GetFullPath($Path).TrimEnd('\')
    if (-not $resolved.StartsWith($programData + '\PetCare\', [StringComparison]::OrdinalIgnoreCase)) {
        throw 'InstallRoot must stay under ProgramData\PetCare'
    }
    return $resolved
}

function Write-Json([object]$Value, [string]$Path) {
    $parent = Split-Path -Parent $Path
    New-Item -ItemType Directory -Force -Path $parent | Out-Null
    [IO.File]::WriteAllText(
        $Path,
        ($Value | ConvertTo-Json -Depth 12),
        [Text.UTF8Encoding]::new($false)
    )
}

function Set-OwnerSystemAcl([string]$Path, [switch]$Recurse) {
    $currentIdentity = [Security.Principal.WindowsIdentity]::GetCurrent()
    $identity = $currentIdentity.User.Value
    $account = $currentIdentity.Name
    $item = Get-Item -LiteralPath $Path
    $suffix = if ($item.PSIsContainer) { '(OI)(CI)F' } else { 'F' }
    $arguments = @(
        $Path,
        '/inheritance:r',
        '/grant:r',
        "${account}:$suffix",
        "SYSTEM:$suffix"
    )
    if ($Recurse) { $arguments += @('/T', '/C') }
    & "$env:SystemRoot\System32\icacls.exe" @arguments *> $null
    if ($LASTEXITCODE) { throw "failed to protect runtime ACL: $Path" }
    $acl = Get-Acl -LiteralPath $Path
    $allowed = @($identity, 'S-1-5-18') | Sort-Object
    $actual = @(
        $acl.Access | ForEach-Object {
            $_.IdentityReference.Translate([Security.Principal.SecurityIdentifier]).Value
        } | Sort-Object -Unique
    )
    if (
        -not $acl.AreAccessRulesProtected -or
        $acl.Access.Count -ne 2 -or
        @($acl.Access | Where-Object {
            $_.AccessControlType -ne [Security.AccessControl.AccessControlType]::Allow -or $_.IsInherited
        }).Count -ne 0 -or
        (Compare-Object $allowed $actual).Count -ne 0
    ) {
        throw "failed to verify runtime ACL: $Path"
    }
}

function New-Secret {
    $bytes = New-Object byte[] 32
    $generator = [Security.Cryptography.RandomNumberGenerator]::Create()
    try {
        $generator.GetBytes($bytes)
    }
    finally {
        $generator.Dispose()
    }
    return [Convert]::ToBase64String($bytes).TrimEnd('=').Replace('+', '-').Replace('/', '_')
}

function Get-VerifiedArtifact(
    [string]$Name,
    [string]$Url,
    [string]$Sha256,
    [string]$Path
) {
    New-Item -ItemType Directory -Force -Path (Split-Path -Parent $Path) | Out-Null
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
        Invoke-WebRequest -UseBasicParsing -Uri $Url -OutFile $Path
    }
    if ((Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash -cne $Sha256) {
        throw "managed artifact SHA-256 mismatch: $Name"
    }
    return $Path
}

function Find-One([string]$Root, [string]$Name) {
    $matches = @(Get-ChildItem -LiteralPath $Root -Recurse -File -Filter $Name)
    if ($matches.Count -ne 1) { throw "managed executable count is invalid: $Name" }
    return $matches[0].FullName
}

function Copy-ConsumerSource([string]$Destination) {
    if ([IO.Path]::GetFullPath($SourceRoot) -ceq [IO.Path]::GetFullPath($Destination)) {
        return
    }
    New-Item -ItemType Directory -Force -Path $Destination | Out-Null
    $copy = Start-Process -FilePath "$env:SystemRoot\System32\robocopy.exe" `
        -WindowStyle Hidden -Wait -PassThru `
        -ArgumentList @($SourceRoot, $Destination, '/E', '/R:2', '/W:1', '/NFL', '/NDL', '/NJH', '/NJS', '/NP')
    if ($copy.ExitCode -gt 7) { throw "Home Agent source copy failed: $($copy.ExitCode)" }
}

function Test-Rfc1918([Net.IPAddress]$Address) {
    if ($Address.AddressFamily -ne [Net.Sockets.AddressFamily]::InterNetwork) { return $false }
    $bytes = $Address.GetAddressBytes()
    return (
        $bytes[0] -eq 10 -or
        ($bytes[0] -eq 172 -and 16 -le $bytes[1] -and $bytes[1] -le 31) -or
        ($bytes[0] -eq 192 -and $bytes[1] -eq 168)
    )
}

function Get-HardwareAddress {
    $routes = @(
        Get-NetRoute -AddressFamily IPv4 -DestinationPrefix '0.0.0.0/0' -ErrorAction SilentlyContinue |
            Sort-Object RouteMetric, InterfaceMetric
    )
    foreach ($route in $routes) {
        $profile = Get-NetConnectionProfile -InterfaceIndex $route.InterfaceIndex -ErrorAction SilentlyContinue |
            Select-Object -First 1
        if (-not $profile -or $profile.NetworkCategory -notin @('Private', 'DomainAuthenticated')) {
            continue
        }
        $addresses = @(
            Get-NetIPAddress -AddressFamily IPv4 -InterfaceIndex $route.InterfaceIndex -ErrorAction SilentlyContinue |
                Where-Object { $_.AddressState -eq 'Preferred' }
        )
        foreach ($candidate in $addresses) {
            $parsed = [Net.IPAddress]$candidate.IPAddress
            if (Test-Rfc1918 $parsed) { return $parsed.ToString() }
        }
    }
    throw 'no active private RFC1918 network was found; set the Windows network profile to Private'
}

function Wait-Listener([string]$Address, [int]$Port) {
    for ($attempt = 0; $attempt -lt 80; $attempt++) {
        $client = [Net.Sockets.TcpClient]::new()
        try {
            $pending = $client.ConnectAsync($Address, $Port)
            if ($pending.Wait(250) -and $client.Connected) { return }
        }
        catch {}
        finally {
            $client.Dispose()
        }
    }
    throw "service listener did not become ready: ${Address}:$Port"
}

function Remove-ManagedService([string]$Name) {
    $service = Get-Service -Name $Name -ErrorAction SilentlyContinue
    if (-not $service) { return }
    if ($service.Status -ne 'Stopped') {
        Stop-Service -Name $Name -Force -ErrorAction SilentlyContinue
        $service.WaitForStatus('Stopped', [TimeSpan]::FromSeconds(20))
    }
    & "$env:SystemRoot\System32\sc.exe" delete $Name *> $null
    if ($LASTEXITCODE) { throw "failed to remove service: $Name" }
}

function Write-Fixture([string]$Path) {
    if ([string]::IsNullOrWhiteSpace($Path)) { throw 'FixtureRoot is required' }
    $root = [IO.Path]::GetFullPath($Path)
    New-Item -ItemType Directory -Force -Path $root | Out-Null
    Write-Json ([ordered]@{
        action = 'Fixture'
        mutates_system = $false
        services = $ServiceNames
        firewall = [ordered]@{
            name = $FirewallRuleName
            local_port = 18883
            profile = 'Private'
            remote_address = 'LocalSubnet'
        }
        site_origin = $SiteOrigin
        jetson_optional = $true
        supabase_customer_configuration = $false
    }) (Join-Path $root 'consumer-installer-fixture.json')
}

Assert-SiteOrigin $SiteOrigin
if ($Action -eq 'Fixture') {
    Write-Fixture $FixtureRoot
    Write-Output 'Consumer Home Agent installer fixture PASS'
    exit 0
}

if (-not $InstallRoot) {
    $InstallRoot = Join-Path $env:ProgramData 'PetCare\HomeAgent'
}
$InstallRoot = Assert-InstallRoot $InstallRoot
$RuntimeRoot = Join-Path $InstallRoot '.runtime'
$ConfigPath = Join-Path $RuntimeRoot 'agent.json'
$ToolsPath = Join-Path $RuntimeRoot 'agent-tools.json'
$JetsonConfigPath = Join-Path $RuntimeRoot 'jetson.json'
$ServicesPath = Join-Path $RuntimeRoot 'services.json'
$PackagingScript = Join-Path $InstallRoot 'packaging\windows\install-home-agent.ps1'

Assert-Elevated

if ($Action -eq 'Status') {
    $status = foreach ($name in $ServiceNames) {
        $service = Get-Service -Name $name -ErrorAction SilentlyContinue
        [ordered]@{
            name = $name
            installed = $null -ne $service
            running = $null -ne $service -and $service.Status -eq 'Running'
        }
    }
    Write-Output ($status | ConvertTo-Json -Compress)
    exit 0
}

if ($Action -eq 'Uninstall') {
    foreach ($name in @('PetCareHomeAgent', 'PetCareMqtt', 'PetCarePostgres')) {
        Remove-ManagedService $name
    }
    Get-NetFirewallRule -Name $FirewallRuleName -ErrorAction SilentlyContinue |
        Remove-NetFirewallRule -ErrorAction SilentlyContinue
    if (Test-Path -LiteralPath 'HKLM:\Software\PetCare\HomeAgent') {
        Remove-Item -LiteralPath 'HKLM:\Software\PetCare\HomeAgent' -Recurse -Force
    }
    Write-Output "PetCare services removed; household data remains at $InstallRoot"
    exit 0
}

Copy-ConsumerSource $InstallRoot
New-Item -ItemType Directory -Force -Path $RuntimeRoot | Out-Null
Set-OwnerSystemAcl $InstallRoot
Set-OwnerSystemAcl $RuntimeRoot

$ManifestPath = Join-Path $InstallRoot 'tools\platform-manifest.json'
$Manifest = Get-Content -Raw -Encoding UTF8 -LiteralPath $ManifestPath | ConvertFrom-Json
$ManifestHash = (Get-FileHash -LiteralPath $ManifestPath -Algorithm SHA256).Hash
$CacheRoot = Join-Path $RuntimeRoot 'bootstrap-cache'
$ManagedRoot = Join-Path $RuntimeRoot 'managed'
New-Item -ItemType Directory -Force -Path $CacheRoot, $ManagedRoot | Out-Null

$pythonArchive = Get-VerifiedArtifact `
    'python' `
    $Manifest.managed_exact.python.windows.url `
    $Manifest.managed_exact.python.windows.sha256 `
    (Join-Path $CacheRoot 'consumer-python.tar.gz')
$pythonRoot = Join-Path $ManagedRoot 'consumer-python'
if (-not (Test-Path -LiteralPath $pythonRoot -PathType Container)) {
    New-Item -ItemType Directory -Force -Path $pythonRoot | Out-Null
    & "$env:SystemRoot\System32\tar.exe" -xf $pythonArchive -C $pythonRoot
    if ($LASTEXITCODE) { throw 'managed Python extraction failed' }
}
$pythonPath = @(
    Get-ChildItem -LiteralPath $pythonRoot -Recurse -File -Filter python.exe |
        Sort-Object { $_.FullName.Length }
)[0].FullName
if ((& $pythonPath --version) -cne "Python $($Manifest.managed_exact.python.version)") {
    throw 'managed Python version mismatch'
}

$uvArchive = Get-VerifiedArtifact `
    'uv' `
    $Manifest.managed_exact.uv.windows.url `
    $Manifest.managed_exact.uv.windows.sha256 `
    (Join-Path $CacheRoot 'consumer-uv.zip')
$uvRoot = Join-Path $ManagedRoot 'consumer-uv'
if (-not (Test-Path -LiteralPath $uvRoot -PathType Container)) {
    Expand-Archive -LiteralPath $uvArchive -DestinationPath $uvRoot
}
$uvPath = Find-One $uvRoot 'uv.exe'
if ((& $uvPath --version) -notmatch "^uv $([regex]::Escape($Manifest.managed_exact.uv.version)) ") {
    throw 'managed uv version mismatch'
}

Write-Json ([ordered]@{
    schema_version = 1
    manifest_sha256 = $ManifestHash
    fixture = $false
    paths = [ordered]@{
        python_path = [IO.Path]::GetFullPath($pythonPath)
        uv_path = [IO.Path]::GetFullPath($uvPath)
    }
    versions = [ordered]@{
        python_path = "$($Manifest.managed_exact.python.version)+$($Manifest.managed_exact.python.build)"
        uv_path = [string]$Manifest.managed_exact.uv.version
    }
}) (Join-Path $RuntimeRoot 'toolchain.json')

& (Join-Path $InstallRoot 'tools\bootstrap_agent_runtime.ps1')
if ($LASTEXITCODE) { throw 'Home Agent runtime bootstrap failed' }
$AgentTools = Get-Content -Raw -Encoding UTF8 -LiteralPath $ToolsPath | ConvertFrom-Json

$dependencyLock = Join-Path $InstallRoot 'backend\requirements-home-agent.lock'
& $uvPath pip install --python $pythonPath --require-hashes --requirement $dependencyLock
if ($LASTEXITCODE) { throw 'Home Agent dependency installation failed' }
$sitePackages = (& $pythonPath -c 'import site; print(site.getsitepackages()[0])').Trim()
if (-not [IO.Path]::IsPathRooted($sitePackages)) { throw 'Python site-packages path is invalid' }
[IO.File]::WriteAllText(
    (Join-Path $sitePackages 'petcare-home-agent.pth'),
    (Join-Path $InstallRoot 'backend') + [Environment]::NewLine,
    [Text.UTF8Encoding]::new($false)
)
$pywin32PostInstall = Join-Path (Split-Path -Parent $pythonPath) 'Scripts\pywin32_postinstall.py'
if (Test-Path -LiteralPath $pywin32PostInstall -PathType Leaf) {
    & $pythonPath $pywin32PostInstall -install *> $null
    if ($LASTEXITCODE) { throw 'pywin32 service bootstrap failed' }
}
& $pythonPath -c 'import app.agent_runtime, app.main, app.windows_service'
if ($LASTEXITCODE) { throw 'Home Agent import verification failed' }

$postgresArchive = Get-VerifiedArtifact `
    'postgresql' `
    $Manifest.managed_exact.postgresql.windows_url `
    $Manifest.managed_exact.postgresql.windows_sha256 `
    (Join-Path $CacheRoot 'consumer-postgresql.zip')
$postgresRoot = Join-Path $RuntimeRoot 'services-managed\postgresql'
if (-not (Test-Path -LiteralPath $postgresRoot -PathType Container)) {
    Expand-Archive -LiteralPath $postgresArchive -DestinationPath $postgresRoot
}
$postgresPath = Find-One $postgresRoot 'postgres.exe'
$pgCtlPath = Find-One $postgresRoot 'pg_ctl.exe'
$initdbPath = Find-One $postgresRoot 'initdb.exe'
$pgIsReadyPath = Find-One $postgresRoot 'pg_isready.exe'
$psqlPath = Find-One $postgresRoot 'psql.exe'
if ((& $postgresPath --version) -notmatch "^postgres \(PostgreSQL\) $([regex]::Escape($Manifest.managed_exact.postgresql.version.Split('-')[0]))") {
    throw 'managed PostgreSQL version mismatch'
}

$wingetPath = (Get-Command winget.exe -ErrorAction Stop).Source
& $wingetPath upgrade `
    --id $Manifest.managed_exact.mosquitto.windows_id `
    --version $Manifest.managed_exact.mosquitto.version `
    --exact --silent --source winget `
    --accept-package-agreements --accept-source-agreements *> $null
$mosquittoPath = Join-Path $env:ProgramFiles 'mosquitto\mosquitto.exe'
$mosquittoPasswdPath = Join-Path $env:ProgramFiles 'mosquitto\mosquitto_passwd.exe'
if (-not (Test-Path -LiteralPath $mosquittoPath -PathType Leaf)) {
    & $wingetPath install `
        --id $Manifest.managed_exact.mosquitto.windows_id `
        --version $Manifest.managed_exact.mosquitto.version `
        --exact --silent --source winget `
        --accept-package-agreements --accept-source-agreements *> $null
}
if (
    -not (Test-Path -LiteralPath $mosquittoPath -PathType Leaf) -or
    -not (Test-Path -LiteralPath $mosquittoPasswdPath -PathType Leaf) -or
    (@(& $mosquittoPath -h 2>&1) -join "`n") -notmatch "mosquitto version $([regex]::Escape($Manifest.managed_exact.mosquitto.version))"
) {
    throw 'managed Mosquitto version mismatch'
}

$HardwareAddress = Get-HardwareAddress
Write-Json ([ordered]@{
    schema_version = 1
    manifest_sha256 = $ManifestHash
    fixture = $false
    paths = [ordered]@{
        postgres_path = [IO.Path]::GetFullPath($postgresPath)
        pg_ctl_path = [IO.Path]::GetFullPath($pgCtlPath)
        initdb_path = [IO.Path]::GetFullPath($initdbPath)
        pg_isready_path = [IO.Path]::GetFullPath($pgIsReadyPath)
        psql_path = [IO.Path]::GetFullPath($psqlPath)
        mosquitto_path = [IO.Path]::GetFullPath($mosquittoPath)
        mosquitto_passwd_path = [IO.Path]::GetFullPath($mosquittoPasswdPath)
        python_path = [IO.Path]::GetFullPath($pythonPath)
    }
    versions = [ordered]@{
        postgresql = [string]$Manifest.managed_exact.postgresql.version
        mosquitto = [string]$Manifest.managed_exact.mosquitto.version
        paho_mqtt = [string]$Manifest.managed_exact.backend_dependencies.'paho-mqtt'
    }
    mqtt_profiles = [ordered]@{
        local_live = [ordered]@{
            bind_host = '127.0.0.1'
            port = 18883
            client_host = '127.0.0.1'
        }
        hardware = [ordered]@{
            bind_host = $HardwareAddress
            port = 18883
            client_host = $HardwareAddress
            allow_public_network = $false
        }
    }
    ports = [ordered]@{ postgresql = 55432; mqtt = 18883 }
}) $ServicesPath

$installedServices = @(
    $ServiceNames | Where-Object { Get-Service -Name $_ -ErrorAction SilentlyContinue }
)
if ($installedServices.Count) {
    throw 'PetCare Home Agent is already installed; use Status or Uninstall before reinstalling'
}
$temporaryServicesStarted = $false
try {
    Get-NetFirewallRule -Name $FirewallRuleName -ErrorAction SilentlyContinue |
        Remove-NetFirewallRule -ErrorAction SilentlyContinue
    New-NetFirewallRule `
        -Name $FirewallRuleName `
        -DisplayName 'PetCare Pico MQTT' `
        -Direction Inbound `
        -Action Allow `
        -Protocol TCP `
        -LocalPort 18883 `
        -LocalAddress $HardwareAddress `
        -RemoteAddress LocalSubnet `
        -Profile Private | Out-Null
$pendingPath = Join-Path $RuntimeRoot 'install-secrets.json'
if (Test-Path -LiteralPath $ConfigPath -PathType Leaf) {
    $existing = Get-Content -Raw -Encoding UTF8 -LiteralPath $ConfigPath | ConvertFrom-Json
    if ([string]$existing.origin -cne $SiteOrigin) { throw 'installed Home Agent belongs to another Sites origin' }
    $databaseMatch = [regex]::Match(
        [string]$existing.local_settings.database_url,
        '^postgresql\+psycopg://petcare:([^@]+)@127\.0\.0\.1:55432/petcare$'
    )
    if (-not $databaseMatch.Success) { throw 'installed Home Agent database configuration is invalid' }
    $postgresPassword = [Uri]::UnescapeDataString($databaseMatch.Groups[1].Value)
    $mqttUsername = [string]$existing.local_settings.mqtt_username
    $mqttPassword = [string]$existing.local_settings.mqtt_password
}
else {
    if (Test-Path -LiteralPath $pendingPath -PathType Leaf) {
        $pending = Get-Content -Raw -Encoding UTF8 -LiteralPath $pendingPath | ConvertFrom-Json
    }
    else {
        $pending = [ordered]@{
            postgres_password = New-Secret
            mqtt_username = 'petcare-device'
            mqtt_password = New-Secret
        }
        Write-Json $pending $pendingPath
        Set-OwnerSystemAcl $pendingPath
    }
    $postgresPassword = [string]$pending.postgres_password
    $mqttUsername = [string]$pending.mqtt_username
    $mqttPassword = [string]$pending.mqtt_password
}

$oldPostgres = $env:PETCARE_POSTGRES_PASSWORD
$oldMqttUser = $env:PETCARE_MQTT_USERNAME
$oldMqttPassword = $env:PETCARE_MQTT_PASSWORD
$oldDatabase = $env:DATABASE_URL
$oldProfile = $env:PETCARE_MQTT_PROFILE
try {
    $env:PETCARE_POSTGRES_PASSWORD = $postgresPassword
    $env:PETCARE_MQTT_USERNAME = $mqttUsername
    $env:PETCARE_MQTT_PASSWORD = $mqttPassword
    $env:PETCARE_MQTT_PROFILE = 'hardware'
    $env:DATABASE_URL = "postgresql+psycopg://petcare:${postgresPassword}@127.0.0.1:55432/petcare"

    $temporaryServicesStarted = $true
    & (Join-Path $InstallRoot 'tools\services.ps1') `
        -Action Start `
        -RuntimePath $ServicesPath `
        -Provider native `
        -Profile hardware `
        -HardwareAddress $HardwareAddress
    if ($LASTEXITCODE) { throw 'local data service initialization failed' }

    $oldPgPassword = $env:PGPASSWORD
    try {
        $env:PGPASSWORD = $postgresPassword
        $databaseExists = (& $psqlPath -h 127.0.0.1 -p 55432 -U petcare -d postgres -tAc `
            "SELECT 1 FROM pg_database WHERE datname='petcare'").Trim()
        if ($databaseExists -ne '1') {
            & $psqlPath -h 127.0.0.1 -p 55432 -U petcare -d postgres -v ON_ERROR_STOP=1 `
                -c 'CREATE DATABASE petcare' | Out-Null
            if ($LASTEXITCODE) { throw 'PetCare database creation failed' }
        }
    }
    finally {
        $env:PGPASSWORD = $oldPgPassword
    }

    Push-Location (Join-Path $InstallRoot 'backend')
    try {
        & $pythonPath -m alembic -c alembic.ini upgrade head
        if ($LASTEXITCODE) { throw 'PetCare database migration failed' }
    }
    finally {
        Pop-Location
    }

    if (-not (Test-Path -LiteralPath $ConfigPath -PathType Leaf)) {
        & $pythonPath -m app.agent_runtime enroll --origin $SiteOrigin --config $ConfigPath
        if ($LASTEXITCODE) { throw 'Home Agent enrollment failed' }
    }
    Remove-Item -LiteralPath $pendingPath -Force -ErrorAction SilentlyContinue

    & (Join-Path $InstallRoot 'tools\services.ps1') `
        -Action Stop `
        -RuntimePath $ServicesPath `
        -Provider native `
        -Profile hardware `
        -HardwareAddress $HardwareAddress
    if ($LASTEXITCODE) { throw 'temporary data service shutdown failed' }
    $temporaryServicesStarted = $false
}
finally {
    $env:PETCARE_POSTGRES_PASSWORD = $oldPostgres
    $env:PETCARE_MQTT_USERNAME = $oldMqttUser
    $env:PETCARE_MQTT_PASSWORD = $oldMqttPassword
    $env:DATABASE_URL = $oldDatabase
    $env:PETCARE_MQTT_PROFILE = $oldProfile
}

$serviceRoot = Join-Path $RuntimeRoot 'services\hardware'
$pgData = Join-Path $serviceRoot 'postgres-data'
$pgLog = Join-Path $serviceRoot 'postgres-service.log'
$mqttConfig = Join-Path $serviceRoot 'mosquitto.conf'
$mqttPasswordFile = Join-Path $serviceRoot 'mosquitto.passwords'
$mqttTemplate = Get-Content -Raw -Encoding UTF8 -LiteralPath (Join-Path $InstallRoot 'infra\mosquitto\mosquitto.conf')
$mqttPhysicalConfig = $mqttTemplate.
    Replace('{{PORT}}', '18883').
    Replace('{{BIND_HOST}}', $HardwareAddress).
    Replace('{{PASSWORD_FILE}}', $mqttPasswordFile.Replace('\', '/'))
[IO.File]::WriteAllText($mqttConfig, $mqttPhysicalConfig, [Text.UTF8Encoding]::new($false))
Set-OwnerSystemAcl $InstallRoot -Recurse

& $pgCtlPath register `
    -N PetCarePostgres `
    -D $pgData `
    -l $pgLog `
    -S auto `
    -o '-p 55432 -h 127.0.0.1'
if ($LASTEXITCODE) { throw 'PostgreSQL service registration failed' }
& "$env:SystemRoot\System32\sc.exe" failure PetCarePostgres `
    reset= 86400 actions= restart/5000/restart/30000/restart/120000 *> $null
& "$env:SystemRoot\System32\sc.exe" failureflag PetCarePostgres 1 *> $null

$mqttCommand = "`"$mosquittoPath`" -c `"$mqttConfig`""
New-Service `
    -Name PetCareMqtt `
    -BinaryPathName $mqttCommand `
    -DisplayName 'PetCare MQTT' `
    -Description 'Private LAN MQTT broker for PetCare Pico devices.' `
    -StartupType Automatic | Out-Null
& "$env:SystemRoot\System32\sc.exe" failure PetCareMqtt `
    reset= 86400 actions= restart/5000/restart/30000/restart/120000 *> $null
& "$env:SystemRoot\System32\sc.exe" failureflag PetCareMqtt 1 *> $null

Start-Service PetCarePostgres
Start-Service PetCareMqtt
Wait-Listener '127.0.0.1' 55432
Wait-Listener $HardwareAddress 18883

& $PackagingScript `
    -Action Install `
    -ConfigPath $ConfigPath `
    -ToolsPath $ToolsPath `
    -JetsonConfigPath $JetsonConfigPath
if ($LASTEXITCODE) { throw 'Home Agent service installation failed' }
& "$env:SystemRoot\System32\sc.exe" config PetCareHomeAgent depend= PetCarePostgres/PetCareMqtt *> $null
if ($LASTEXITCODE) { throw 'Home Agent service dependency configuration failed' }

Write-Output 'PetCare Home Agent installation PASS'
Write-Output 'Return to the authenticated dashboard to configure both Pico Wi-Fi connections.'
}
catch {
    if ($temporaryServicesStarted) {
        try {
            & (Join-Path $InstallRoot 'tools\services.ps1') `
                -Action Stop `
                -RuntimePath $ServicesPath `
                -Provider native `
                -Profile hardware `
                -HardwareAddress $HardwareAddress *> $null
        }
        catch {}
    }
    foreach ($name in @('PetCareHomeAgent', 'PetCareMqtt', 'PetCarePostgres')) {
        try { Remove-ManagedService $name } catch {}
    }
    if (Test-Path -LiteralPath 'HKLM:\Software\PetCare\HomeAgent') {
        Remove-Item -LiteralPath 'HKLM:\Software\PetCare\HomeAgent' -Recurse -Force -ErrorAction SilentlyContinue
    }
    Get-NetFirewallRule -Name $FirewallRuleName -ErrorAction SilentlyContinue |
        Remove-NetFirewallRule -ErrorAction SilentlyContinue
    throw
}
