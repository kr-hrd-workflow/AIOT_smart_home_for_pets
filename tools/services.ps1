[CmdletBinding()]
param(
  [ValidateSet('Start','Stop','Status','Reset','ValidateAddress')]
  [string]$Action,
  [string]$RuntimePath = '',
  [ValidateSet('native','compose')]
  [string]$Provider = 'native',
  [ValidateSet('local_live','hardware')]
  [string]$Profile = 'local_live',
  [string]$HardwareAddress = '',
  [switch]$ConfirmReset,
  [switch]$DryRun
)

$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent $PSScriptRoot
if (-not $RuntimePath) { $RuntimePath = Join-Path $Root '.runtime/services.json' }

function Test-Rfc1918([string]$Address) {
  $parsed = $null
  if (-not [Net.IPAddress]::TryParse($Address, [ref]$parsed) -or $parsed.AddressFamily -ne [Net.Sockets.AddressFamily]::InterNetwork) { return $false }
  $bytes = $parsed.GetAddressBytes()
  return $bytes[0] -eq 10 -or ($bytes[0] -eq 172 -and $bytes[1] -ge 16 -and $bytes[1] -le 31) -or ($bytes[0] -eq 192 -and $bytes[1] -eq 168)
}

if ($Action -eq 'ValidateAddress') {
  if (-not (Test-Rfc1918 $HardwareAddress)) { throw 'hardware address must be one explicit RFC1918 IPv4 address' }
  Write-Output $HardwareAddress
  exit 0
}
if (-not (Test-Path -LiteralPath $RuntimePath)) { throw 'run bootstrap_services.ps1 first' }
$runtime = Get-Content -Raw -Encoding UTF8 -LiteralPath $RuntimePath | ConvertFrom-Json
$manifestPath = Join-Path $PSScriptRoot 'platform-manifest.json'
if ($runtime.manifest_sha256 -ne (Get-FileHash -LiteralPath $manifestPath -Algorithm SHA256).Hash) { throw 'service authority hash mismatch' }
foreach ($key in @('postgres_path','pg_ctl_path','initdb_path','pg_isready_path','psql_path','mosquitto_path','mosquitto_passwd_path','python_path')) {
  $path = $runtime.paths.$key
  if (-not [IO.Path]::IsPathRooted($path) -or -not (Test-Path -LiteralPath $path)) { throw "invalid service closure: $key" }
}

$bindHost = '127.0.0.1'
$postgresHost = '127.0.0.1'
if ($Profile -eq 'hardware') {
  if (-not (Test-Rfc1918 $HardwareAddress)) { throw 'hardware profile requires one explicit RFC1918 IPv4 address' }
  $hardwareProfile = $runtime.mqtt_profiles.hardware
  if (-not $hardwareProfile -or $hardwareProfile.bind_host -ne $HardwareAddress -or $hardwareProfile.client_host -ne $HardwareAddress -or $hardwareProfile.port -ne 18883) { throw 'hardware address does not match the bootstrapped runtime authority' }
  if ($Action -in @('Start','Status') -and -not (Get-NetIPAddress -AddressFamily IPv4 -IPAddress $HardwareAddress -ErrorAction SilentlyContinue)) { throw 'hardware address is not assigned to a local interface' }
  $bindHost = $HardwareAddress
}
if ($DryRun) {
  Write-Output "service $Action dry-run PASS: $Provider/$Profile $bindHost"
  exit 0
}

function Require-Secret([string]$Name) {
  $value = [Environment]::GetEnvironmentVariable($Name)
  if ([string]::IsNullOrEmpty($value)) { throw "required environment variable is missing: $Name" }
  return $value
}

function Protect-Path([string]$Path) {
  $item = Get-Item -LiteralPath $Path
  $grant = if ($item.PSIsContainer) { "${env:USERNAME}:(OI)(CI)F" } else { "${env:USERNAME}:F" }
  & "$env:SystemRoot\System32\icacls.exe" $Path '/inheritance:r' '/grant:r' $grant | Out-Null
  if ($LASTEXITCODE) { throw "failed to restrict ACL: $Path" }
}

function Get-SubstTarget([string]$Drive) {
  $output = & "$env:SystemRoot\System32\subst.exe" 2>$null
  foreach ($line in $output) {
    if ([string]$line -match '^([A-Z]:)\\?:\s*=>' -and $Matches[1] -eq $Drive) { return $Drive }
  }
  return ''
}

function Test-AsciiAliasTarget([string]$Drive, [string]$TargetRoot) {
  $relativeMarker = 'tools\platform-manifest.json'
  $aliasMarker = Join-Path "$Drive\" $relativeMarker
  $targetMarker = Join-Path $TargetRoot $relativeMarker
  if (-not (Test-Path -LiteralPath $aliasMarker) -or -not (Test-Path -LiteralPath $targetMarker)) { return $false }
  return (Get-FileHash -LiteralPath $aliasMarker -Algorithm SHA256).Hash -eq (Get-FileHash -LiteralPath $targetMarker -Algorithm SHA256).Hash
}

function Ensure-AsciiAlias([string]$TargetRoot, [string]$StatePath) {
  $target = [IO.Path]::GetFullPath($TargetRoot).TrimEnd('\')
  $stored = if (Test-Path -LiteralPath $StatePath) { (Get-Content -Raw -LiteralPath $StatePath).Trim().TrimEnd(':') } else { '' }
  $candidates = @($stored) + @('P','R','S','T','U','V','W','X','Y','Z') | Where-Object { $_ -match '^[A-Z]$' } | Select-Object -Unique
  foreach ($letter in $candidates) {
    $drive = "${letter}:"
    $existing = Get-SubstTarget $drive
    if ($existing -and -not $stored) { continue }
    if ($existing -and $letter -ne $stored) { continue }
    if ($existing -and -not (Test-AsciiAliasTarget $drive $target)) {
      if ($letter -eq $stored) { throw "stored ASCII drive alias is occupied: $drive" }
      continue
    }
    if (-not $existing -and (Test-Path -LiteralPath "$drive\")) {
      if ($letter -eq $stored) { throw "stored ASCII drive alias is occupied: $drive" }
      continue
    }
    if (-not $existing) {
      & "$env:SystemRoot\System32\subst.exe" $drive $target
      if ($LASTEXITCODE) { continue }
    }
    [IO.File]::WriteAllText($StatePath, $letter, [Text.ASCIIEncoding]::new())
    Protect-Path $StatePath
    return "$drive\"
  }
  throw 'no free ASCII drive alias is available for PostgreSQL on Windows'
}

function Remove-AsciiAlias([string]$TargetRoot, [string]$StatePath) {
  if (-not (Test-Path -LiteralPath $StatePath)) { return }
  $letter = (Get-Content -Raw -LiteralPath $StatePath).Trim().TrimEnd(':')
  if ($letter -notmatch '^[A-Z]$') { throw 'invalid ASCII drive alias state' }
  $drive = "${letter}:"
  $target = [IO.Path]::GetFullPath($TargetRoot).TrimEnd('\')
  $existing = Get-SubstTarget $drive
  if ($existing -and -not (Test-AsciiAliasTarget $drive $target)) { throw "refusing to remove foreign drive alias: $drive" }
  if ($existing) {
    & "$env:SystemRoot\System32\subst.exe" $drive '/d'
    if ($LASTEXITCODE) { throw "failed to remove ASCII drive alias: $drive" }
  }
  Remove-Item -LiteralPath $StatePath -Force
}

function Wait-Port([string]$Address, [int]$Port) {
  for ($attempt = 0; $attempt -lt 60; $attempt++) {
    $client = [Net.Sockets.TcpClient]::new()
    try {
      $task = $client.ConnectAsync($Address, $Port)
      if ($task.Wait(250) -and $client.Connected) { return }
    } catch {} finally { $client.Dispose() }
    Start-Sleep -Milliseconds 250
  }
  throw "service port did not become ready: ${Address}:$Port"
}

function Get-PortIdentity([int]$Port) {
  @(Get-NetTCPConnection -State Listen -LocalPort $Port -ErrorAction SilentlyContinue | Sort-Object LocalAddress,OwningProcess | ForEach-Object { "$($_.LocalAddress):$($_.OwningProcess)" }) -join ','
}

function Get-ListenerOwner([string]$Address, [int]$Port) {
  $owners = @(Get-NetTCPConnection -State Listen -LocalAddress $Address -LocalPort $Port -ErrorAction SilentlyContinue | Select-Object -ExpandProperty OwningProcess -Unique)
  if ($owners.Count -ne 1) { return 0 }
  return [int]$owners[0]
}

function Test-ExpectedMosquittoProcess([int]$ProcessId, [string]$Address, [int]$Port) {
  if ($ProcessId -le 0 -or (Get-ListenerOwner $Address $Port) -ne $ProcessId) { return $false }
  $process = Get-Process -Id $ProcessId -ErrorAction SilentlyContinue
  if (-not $process -or $process.ProcessName -ne 'mosquitto') { return $false }
  try {
    return (Get-FileHash -LiteralPath $process.Path -Algorithm SHA256).Hash -eq (Get-FileHash -LiteralPath $runtime.paths.mosquitto_path -Algorithm SHA256).Hash
  } catch { return $false }
}

function Test-HardwareFirewall([string]$Address) {
  $ip = Get-NetIPAddress -AddressFamily IPv4 -IPAddress $Address -ErrorAction SilentlyContinue | Select-Object -First 1
  if (-not $ip) { return $false }
  $profile = Get-NetConnectionProfile -InterfaceIndex $ip.InterfaceIndex -ErrorAction SilentlyContinue | Select-Object -First 1
  if (-not $profile -or [string]$profile.NetworkCategory -ne 'Private') { return $false }
  $rules = Get-NetFirewallRule -Enabled True -Direction Inbound -Action Allow -ErrorAction SilentlyContinue | Where-Object { [string]$_.Profile -eq 'Private' }
  foreach ($rule in $rules) {
    $port = $rule | Get-NetFirewallPortFilter -ErrorAction SilentlyContinue
    $remote = $rule | Get-NetFirewallAddressFilter -ErrorAction SilentlyContinue
    $protocols = @($port.Protocol); $ports = @($port.LocalPort); $remoteAddresses = @($remote.RemoteAddress); $localAddresses = @($remote.LocalAddress)
    if ($protocols.Count -eq 1 -and [string]$protocols[0] -eq 'TCP' -and $ports.Count -eq 1 -and [string]$ports[0] -eq '18883' -and $remoteAddresses.Count -eq 1 -and [string]$remoteAddresses[0] -eq 'LocalSubnet' -and $localAddresses.Count -eq 1 -and [string]$localAddresses[0] -eq $Address) { return $true }
  }
  return $false
}

function Invoke-MqttProof([string]$HostName, [int]$Port, [string]$Username, [string]$Password) {
  $oldUser = $env:PETCARE_MQTT_USERNAME
  $oldPassword = $env:PETCARE_MQTT_PASSWORD
  $oldHost = $env:PETCARE_MQTT_HOST
  $oldPort = $env:PETCARE_MQTT_PORT
  try {
    $env:PETCARE_MQTT_USERNAME = $Username
    $env:PETCARE_MQTT_PASSWORD = $Password
    $env:PETCARE_MQTT_HOST = $HostName
    $env:PETCARE_MQTT_PORT = "$Port"
    $code = @'
import os, threading
import paho.mqtt.client as mqtt
host, port = os.environ["PETCARE_MQTT_HOST"], int(os.environ["PETCARE_MQTT_PORT"])
topic = f"petcare/health/{os.getpid()}"
connected = threading.Event(); subscribed = threading.Event(); received = threading.Event(); auth_result = []
auth = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="petcare-health-auth")
auth.username_pw_set(os.environ["PETCARE_MQTT_USERNAME"], os.environ["PETCARE_MQTT_PASSWORD"])
def auth_connected(client, userdata, flags, reason_code, properties):
    auth_result.append(bool(reason_code.is_failure)); connected.set()
def auth_subscribed(client, userdata, mid, reason_codes, properties):
    assert all(not code.is_failure for code in reason_codes); subscribed.set()
def auth_message(client, userdata, message):
    if message.topic == topic and message.payload == b"ok": received.set()
auth.on_connect = auth_connected
auth.on_subscribe = auth_subscribed
auth.on_message = auth_message
auth.connect(host, port, 5)
auth.loop_start()
assert connected.wait(5) and not auth_result[0]
result, _ = auth.subscribe(topic, qos=1)
assert result == mqtt.MQTT_ERR_SUCCESS and subscribed.wait(5)
published = auth.publish(topic, "ok", qos=1)
published.wait_for_publish(5)
assert published.is_published() and received.wait(5)
auth.disconnect(); auth.loop_stop()
done = threading.Event(); result = []
def connected(client, userdata, flags, reason_code, properties):
    result.append(bool(reason_code.is_failure)); done.set()
anon = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="petcare-health-anonymous")
anon.on_connect = connected
anon.connect(host, port, 5); anon.loop_start()
assert done.wait(5) and result[0]
anon.loop_stop()
'@
    $proofScript = Join-Path $Root ".runtime/mqtt-proof-$PID.py"
    New-Item -ItemType Directory -Force -Path (Split-Path -Parent $proofScript) | Out-Null
    [IO.File]::WriteAllText($proofScript, $code, [Text.UTF8Encoding]::new($false))
    try {
      & $runtime.paths.python_path $proofScript
      if ($LASTEXITCODE) { throw 'authenticated MQTT proof failed' }
    } finally { Remove-Item -LiteralPath $proofScript -Force -ErrorAction SilentlyContinue }
  } finally {
    $env:PETCARE_MQTT_USERNAME = $oldUser
    $env:PETCARE_MQTT_PASSWORD = $oldPassword
    $env:PETCARE_MQTT_HOST = $oldHost
    $env:PETCARE_MQTT_PORT = $oldPort
  }
}

function Invoke-PostgresProof([string]$HostName, [string]$Password, [switch]$UsePasswordEnvironment) {
  $oldPgPassword = $env:PGPASSWORD
  try {
    if ($UsePasswordEnvironment) { $env:PGPASSWORD = $Password }
    & $runtime.paths.psql_path -h $HostName -p 55432 -U petcare -d postgres -v ON_ERROR_STOP=1 -c 'SELECT 1' | Out-Null
    if ($LASTEXITCODE) { throw 'authenticated PostgreSQL proof failed' }
  } finally { $env:PGPASSWORD = $oldPgPassword }
}

if ($Provider -eq 'compose') {
  if (-not $runtime.paths.compose_plugin_path -or -not (Test-Path -LiteralPath $runtime.paths.compose_plugin_path)) { throw 'compatible standalone Compose plugin is unavailable' }
  if (-not $runtime.paths.docker_path -or -not (Test-Path -LiteralPath $runtime.paths.docker_path)) { throw 'compatible Docker Engine is unavailable' }
  if ($Profile -eq 'hardware' -and $Action -in @('Start','Status') -and -not (Test-HardwareFirewall $bindHost)) { throw 'hardware NOT_RUN: matching Private/LocalSubnet firewall evidence is unavailable' }
  $env:PETCARE_MQTT_PUBLISH_HOST = $bindHost
  $composeFile = Join-Path $Root 'compose.yml'
  switch ($Action) {
  'Start' {
    $postgresPassword = Require-Secret 'PETCARE_POSTGRES_PASSWORD'
    $mqttUsername = Require-Secret 'PETCARE_MQTT_USERNAME'
    $mqttPassword = Require-Secret 'PETCARE_MQTT_PASSWORD'
    $before5432 = Get-PortIdentity 5432
    $before1883 = Get-PortIdentity 1883
    $composeComplete = $false
    try {
    & $runtime.paths.compose_plugin_path -f $composeFile up -d
    if ($LASTEXITCODE) { throw 'Compose start failed' }
    Wait-Port $postgresHost 55432
    Wait-Port $bindHost 18883
    Invoke-PostgresProof $postgresHost $postgresPassword -UsePasswordEnvironment
    Invoke-MqttProof $bindHost 18883 $mqttUsername $mqttPassword
    if ((Get-PortIdentity 5432) -ne $before5432 -or (Get-PortIdentity 1883) -ne $before1883) { throw 'existing default service identity changed' }
    $composeComplete = $true
    } finally {
      if (-not $composeComplete) { & $runtime.paths.compose_plugin_path -f $composeFile down 2>$null | Out-Null }
    }
  }
  'Stop' {
    & $runtime.paths.compose_plugin_path -f $composeFile down
    if ($LASTEXITCODE) { throw 'Compose stop failed' }
  }
  'Status' {
    $postgresPassword = Require-Secret 'PETCARE_POSTGRES_PASSWORD'
    $mqttUsername = Require-Secret 'PETCARE_MQTT_USERNAME'
    $mqttPassword = Require-Secret 'PETCARE_MQTT_PASSWORD'
    & $runtime.paths.compose_plugin_path -f $composeFile ps
    if ($LASTEXITCODE) { throw 'Compose status failed' }
    Wait-Port $postgresHost 55432
    Wait-Port $bindHost 18883
    Invoke-PostgresProof $postgresHost $postgresPassword -UsePasswordEnvironment
    Invoke-MqttProof $bindHost 18883 $mqttUsername $mqttPassword
  }
  'Reset' {
    if (-not $ConfirmReset) { throw 'Reset requires -ConfirmReset' }
    & $runtime.paths.compose_plugin_path -f $composeFile down -v
    if ($LASTEXITCODE) { throw 'Compose reset failed' }
  }
  }
  Write-Output "compose services PASS: PostgreSQL ${postgresHost}:55432, MQTT ${bindHost}:18883"
  exit 0
}

$serviceRoot = [IO.Path]::GetFullPath((Join-Path $Root ".runtime/services/$Profile"))
$allowedRoot = [IO.Path]::GetFullPath((Join-Path $Root '.runtime/services'))
if (-not $serviceRoot.StartsWith($allowedRoot + [IO.Path]::DirectorySeparatorChar, [StringComparison]::OrdinalIgnoreCase)) { throw 'service root escaped repository runtime' }
$physicalServiceRoot = $serviceRoot
$asciiAliasState = Join-Path $physicalServiceRoot 'ascii-drive.txt'
$pgData = Join-Path $serviceRoot 'postgres-data'
$pgLog = Join-Path $serviceRoot 'postgres.log'
$pgPass = Join-Path $serviceRoot 'pgpass.conf'
$mqttPasswordFile = Join-Path $serviceRoot 'mosquitto.passwords'
$mqttConfig = Join-Path $serviceRoot 'mosquitto.conf'
$mqttPidFile = Join-Path $serviceRoot 'mosquitto.pid'

function Convert-ToAsciiAlias([string]$Path, [string]$AliasRoot) {
  $fullRoot = [IO.Path]::GetFullPath($Root).TrimEnd('\') + '\'
  $fullPath = [IO.Path]::GetFullPath($Path)
  if (-not $fullPath.StartsWith($fullRoot, [StringComparison]::OrdinalIgnoreCase)) { throw "native path escaped repository root: $Path" }
  return Join-Path $AliasRoot $fullPath.Substring($fullRoot.Length)
}

function Enable-AsciiServicePaths([string]$AliasRoot) {
  $script:serviceRoot = Convert-ToAsciiAlias $physicalServiceRoot $AliasRoot
  $script:pgData = Join-Path $serviceRoot 'postgres-data'
  $script:pgLog = Join-Path $serviceRoot 'postgres.log'
  $script:pgPass = Join-Path $serviceRoot 'pgpass.conf'
  $script:mqttPasswordFile = Join-Path $serviceRoot 'mosquitto.passwords'
  $script:mqttConfig = Join-Path $serviceRoot 'mosquitto.conf'
  $script:mqttPidFile = Join-Path $serviceRoot 'mosquitto.pid'
  foreach ($key in @('postgres_path','pg_ctl_path','initdb_path','pg_isready_path','psql_path','mosquitto_path','mosquitto_passwd_path','python_path')) {
    $runtime.paths.$key = Convert-ToAsciiAlias $runtime.paths.$key $AliasRoot
  }
}

function Stop-NativeServices {
  if (Test-Path -LiteralPath $asciiAliasState) { Enable-AsciiServicePaths (Ensure-AsciiAlias $Root $asciiAliasState) }
  $pgStopExit = 0
  if (Test-Path -LiteralPath (Join-Path $pgData 'PG_VERSION')) {
    $pg = Start-Process -FilePath $runtime.paths.pg_ctl_path -WindowStyle Hidden -Wait -PassThru -ArgumentList @('-D',$pgData,'stop','-m','fast')
    $pgStopExit = $pg.ExitCode
  }
  if (Test-Path -LiteralPath $mqttPidFile) {
    $mqttPid = [int](Get-Content -Raw -LiteralPath $mqttPidFile)
    if (Test-ExpectedMosquittoProcess $mqttPid $bindHost 18883) { Stop-Process -Id $mqttPid -Force }
    Remove-Item -LiteralPath $mqttPidFile -Force -ErrorAction SilentlyContinue
  }
  if (Get-PortIdentity 55432) { throw 'PostgreSQL stop failed; preserving the ASCII alias' }
  if (Get-PortIdentity 18883) { throw 'Mosquitto stop failed; preserving the ASCII alias' }
  Remove-AsciiAlias $Root $asciiAliasState
  if ($pgStopExit -and $pgStopExit -ne 1) { throw 'PostgreSQL stop command failed after the listener closed' }
}

switch ($Action) {
'Start' {
  if ($Profile -eq 'hardware' -and -not (Test-HardwareFirewall $bindHost)) { throw 'hardware NOT_RUN: matching Private/LocalSubnet firewall evidence is unavailable' }
  $postgresPassword = Require-Secret 'PETCARE_POSTGRES_PASSWORD'
  $mqttUsername = Require-Secret 'PETCARE_MQTT_USERNAME'
  $mqttPassword = Require-Secret 'PETCARE_MQTT_PASSWORD'
  $before5432 = Get-PortIdentity 5432
  $before1883 = Get-PortIdentity 1883
  if ((Get-PortIdentity 55432) -or (Get-PortIdentity 18883)) { throw 'managed service port is already occupied' }
  New-Item -ItemType Directory -Force -Path $physicalServiceRoot | Out-Null
  Protect-Path $physicalServiceRoot
  Enable-AsciiServicePaths (Ensure-AsciiAlias $Root $asciiAliasState)
  $startComplete = $false
  try {
  if (-not (Test-Path -LiteralPath (Join-Path $pgData 'PG_VERSION'))) {
    New-Item -ItemType Directory -Force -Path $pgData | Out-Null
    $pwInput = Join-Path $serviceRoot 'initdb-password.input'
    [IO.File]::WriteAllText($pwInput, "$postgresPassword`n", [Text.UTF8Encoding]::new($false))
    try {
      & $runtime.paths.initdb_path "--pgdata=$pgData" '--username=petcare' "--pwfile=$pwInput" '--auth-host=scram-sha-256' '--auth-local=trust' '--encoding=UTF8' '--no-locale'
      if ($LASTEXITCODE) { throw 'initdb failed' }
    } finally { Remove-Item -LiteralPath $pwInput -Force -ErrorAction SilentlyContinue }
  }
  [IO.File]::WriteAllText($pgPass, "${postgresHost}:55432:*:petcare:$postgresPassword`n", [Text.UTF8Encoding]::new($false))
  Protect-Path $pgPass
  $pgOptions = "-p 55432 -h $postgresHost"
  $pgArgumentLine = "-D `"$pgData`" -l `"$pgLog`" -o `"$pgOptions`" start"
  $pg = Start-Process -FilePath $runtime.paths.pg_ctl_path -WindowStyle Hidden -PassThru -ArgumentList $pgArgumentLine
  $pg.WaitForExit()
  if ($pg.ExitCode) { throw 'PostgreSQL start failed' }
  Wait-Port $postgresHost 55432

  $passwordInput = Join-Path $serviceRoot 'mosquitto-password.input'
  [IO.File]::WriteAllText($passwordInput, "${mqttUsername}:$mqttPassword`n", [Text.UTF8Encoding]::new($false))
  Protect-Path $passwordInput
  try {
    # Windows mosquitto_passwd uses ReadConsole, so redirected stdin is empty; transform an ACL-only file in place without password argv.
    $passwd = Start-Process -FilePath $runtime.paths.mosquitto_passwd_path -WindowStyle Hidden -Wait -PassThru -ArgumentList @('-U',$passwordInput)
    if ($passwd.ExitCode) { throw 'mosquitto password generation failed' }
    Move-Item -LiteralPath $passwordInput -Destination $mqttPasswordFile -Force
  } finally { Remove-Item -LiteralPath $passwordInput -Force -ErrorAction SilentlyContinue }
  Protect-Path $mqttPasswordFile
  $template = Get-Content -Raw -Encoding UTF8 (Join-Path $Root 'infra/mosquitto/mosquitto.conf')
  $config = $template.Replace('{{PORT}}','18883').Replace('{{BIND_HOST}}',$bindHost).Replace('{{PASSWORD_FILE}}',$mqttPasswordFile.Replace('\','/'))
  [IO.File]::WriteAllText($mqttConfig, $config, [Text.UTF8Encoding]::new($false))
  $mqtt = Start-Process -FilePath $runtime.paths.mosquitto_path -WindowStyle Hidden -PassThru -ArgumentList @('-c',$mqttConfig) -RedirectStandardOutput (Join-Path $serviceRoot 'mosquitto.stdout.log') -RedirectStandardError (Join-Path $serviceRoot 'mosquitto.stderr.log')
  [IO.File]::WriteAllText($mqttPidFile, "$($mqtt.Id)", [Text.ASCIIEncoding]::new())
  Wait-Port $bindHost 18883
  if (-not (Test-ExpectedMosquittoProcess $mqtt.Id $bindHost 18883)) { throw 'spawned Mosquitto does not own the expected listener' }

  $oldPgPass = $env:PGPASSFILE
  try {
    $env:PGPASSFILE = $pgPass
    Invoke-PostgresProof $postgresHost ''
  } finally { $env:PGPASSFILE = $oldPgPass }
  Invoke-MqttProof $bindHost 18883 $mqttUsername $mqttPassword
  if ((Get-PortIdentity 5432) -ne $before5432 -or (Get-PortIdentity 1883) -ne $before1883) { throw 'existing default service identity changed' }
  Write-Output "native services PASS: PostgreSQL ${bindHost}:55432, MQTT ${bindHost}:18883"
  $startComplete = $true
  } finally {
    if (-not $startComplete) {
      if ($mqtt -and (Test-ExpectedMosquittoProcess $mqtt.Id $bindHost 18883)) { Stop-Process -Id $mqtt.Id -Force -ErrorAction SilentlyContinue }
      Remove-Item -LiteralPath $mqttPidFile -Force -ErrorAction SilentlyContinue
      if ((Get-PortIdentity 55432) -and (Test-Path -LiteralPath (Join-Path $pgData 'PG_VERSION'))) {
        & $runtime.paths.pg_ctl_path -D $pgData stop -m fast 2>$null | Out-Null
      }
      if (Get-PortIdentity 55432) {
        Write-Warning 'PostgreSQL rollback did not stop the listener; preserving the ASCII alias'
      } else {
        try { Remove-AsciiAlias $Root $asciiAliasState } catch { Write-Warning $_.Exception.Message }
      }
    }
  }
}
'Stop' {
  Stop-NativeServices
  Write-Output 'native services stopped'
}
'Status' {
  if (Test-Path -LiteralPath $asciiAliasState) { Enable-AsciiServicePaths (Ensure-AsciiAlias $Root $asciiAliasState) }
  if ($Profile -eq 'hardware' -and -not (Test-HardwareFirewall $bindHost)) { throw 'hardware NOT_RUN: matching Private/LocalSubnet firewall evidence is unavailable' }
  & $runtime.paths.pg_isready_path -h $postgresHost -p 55432
  if ($LASTEXITCODE) { throw 'PostgreSQL is not ready' }
  $mqttRunning = $false
  if (Test-Path -LiteralPath $mqttPidFile) { $mqttRunning = Test-ExpectedMosquittoProcess ([int](Get-Content -Raw $mqttPidFile)) $bindHost 18883 }
  if (-not $mqttRunning) { throw 'Mosquitto is not running' }
  $postgresPassword = Require-Secret 'PETCARE_POSTGRES_PASSWORD'
  $mqttUsername = Require-Secret 'PETCARE_MQTT_USERNAME'
  $mqttPassword = Require-Secret 'PETCARE_MQTT_PASSWORD'
  if (-not (Test-Path -LiteralPath $pgPass)) { throw 'PostgreSQL credential file is missing' }
  $oldPgPass = $env:PGPASSFILE
  try {
    $env:PGPASSFILE = $pgPass
    Invoke-PostgresProof $postgresHost $postgresPassword
  } finally { $env:PGPASSFILE = $oldPgPass }
  Invoke-MqttProof $bindHost 18883 $mqttUsername $mqttPassword
  Write-Output 'native services healthy'
}
'Reset' {
  if (-not $ConfirmReset) { throw 'Reset requires -ConfirmReset' }
  Stop-NativeServices
  $resolved = [IO.Path]::GetFullPath($physicalServiceRoot)
  if (-not $resolved.StartsWith($allowedRoot + [IO.Path]::DirectorySeparatorChar, [StringComparison]::OrdinalIgnoreCase)) { throw 'refusing unsafe reset target' }
  if (Test-Path -LiteralPath $resolved) { Remove-Item -LiteralPath $resolved -Recurse -Force }
  Write-Output 'native service data reset'
}
}
