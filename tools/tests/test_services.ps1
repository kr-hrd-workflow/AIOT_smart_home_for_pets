$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$bootstrap = Join-Path $root 'tools/bootstrap_services.ps1'
$services = Join-Path $root 'tools/services.ps1'
$platformManifest = Join-Path $root 'tools/platform-manifest.json'
$fixture = Join-Path $root '.runtime/tests/services-fixture'
$runtime = Join-Path $root '.runtime/tests/services.json'
$hardwareRuntime = Join-Path $root '.runtime/tests/services-hardware.json'

foreach ($path in @($bootstrap,$services,(Join-Path $root 'compose.yml'),(Join-Path $root 'infra/mosquitto/mosquitto.conf'),(Join-Path $root 'infra/mosquitto/docker-entrypoint.sh'),(Join-Path $root '.env.example'))) {
  if (-not (Test-Path -LiteralPath $path)) { throw "ASSERT: missing $path" }
}
$managedMosquitto = (Get-Content -Raw -Encoding UTF8 -LiteralPath $platformManifest | ConvertFrom-Json).managed_exact.mosquitto
foreach ($property in @('source_url','source_ref','source_commit','cjson_source_url','cjson_ref','cjson_commit','openssl_source_url','openssl_ref','openssl_commit','perl_url','perl_sha256')) {
  if ([string]::IsNullOrWhiteSpace($managedMosquitto.$property)) { throw "ASSERT: Mosquitto source closure missing $property" }
}

foreach ($required in @('FixtureRoot','OutputPath','ToolchainRuntime','CheckOnly')) {
  if (-not (Get-Command $bootstrap).Parameters.ContainsKey($required)) { throw "ASSERT: bootstrap missing $required" }
}
& $bootstrap -FixtureRoot $fixture -OutputPath $runtime
$data = Get-Content -Raw -Encoding UTF8 -LiteralPath $runtime | ConvertFrom-Json
if ($data.versions.postgresql -ne '17.10-2' -or $data.versions.mosquitto -ne '2.1.2') { throw 'ASSERT: native service pins' }
foreach ($key in @('postgres_path','pg_ctl_path','initdb_path','pg_isready_path','psql_path','mosquitto_path','mosquitto_passwd_path','python_path')) {
  $path = $data.paths.$key
  if (-not [IO.Path]::IsPathRooted($path) -or -not (Test-Path -LiteralPath $path)) { throw "ASSERT: invalid $key" }
}
$local = $data.mqtt_profiles.local_live
if ($local.bind_host -ne '127.0.0.1' -or $local.port -ne 18883 -or $local.client_host -ne '127.0.0.1') { throw 'ASSERT: local MQTT profile' }
if ($data.mqtt_profiles.PSObject.Properties.Name -contains 'hardware') { throw 'ASSERT: fixture must not invent hardware profile' }

foreach ($required in @('Action','RuntimePath','Provider','Profile','HardwareAddress','ConfirmReset','DryRun')) {
  if (-not (Get-Command $services).Parameters.ContainsKey($required)) { throw "ASSERT: services missing $required" }
}
foreach ($address in @('10.0.0.1','172.16.0.1','172.31.255.254','192.168.1.10')) {
  & $services -Action ValidateAddress -RuntimePath $runtime -HardwareAddress $address | Out-Null
}
foreach ($address in @('0.0.0.0','127.0.0.1','169.254.1.1','172.15.0.1','172.32.0.1','192.0.2.1','224.0.0.1','255.255.255.255')) {
  $child = Start-Process -FilePath (Get-Process -Id $PID).Path -Wait -PassThru -WindowStyle Hidden -ArgumentList @(
    '-NoProfile','-ExecutionPolicy','Bypass','-File',$services,'-Action','ValidateAddress','-RuntimePath',$runtime,'-HardwareAddress',$address
  )
  if ($child.ExitCode -eq 0) { throw "ASSERT: forbidden hardware address $address" }
}
& $services -Action Start -RuntimePath $runtime -Provider native -Profile local_live -DryRun
$hardwareWithoutAuthority = Start-Process -FilePath (Get-Process -Id $PID).Path -Wait -PassThru -WindowStyle Hidden -ArgumentList @(
  '-NoProfile','-ExecutionPolicy','Bypass','-File',$services,'-Action','Start','-RuntimePath',$runtime,'-Provider','native','-Profile','hardware','-HardwareAddress','192.168.1.10','-DryRun'
)
if ($hardwareWithoutAuthority.ExitCode -eq 0) { throw 'ASSERT: hardware profile bypassed runtime authority' }
& $bootstrap -FixtureRoot $fixture -OutputPath $hardwareRuntime -HardwareAddress '192.168.1.10'
& $services -Action Stop -RuntimePath $hardwareRuntime -Provider native -Profile hardware -HardwareAddress '192.168.1.10' -DryRun

$allText = @(
  Get-Content -Raw -Encoding UTF8 (Join-Path $root 'compose.yml')
  Get-Content -Raw -Encoding UTF8 (Join-Path $root 'infra/mosquitto/docker-entrypoint.sh')
  Get-Content -Raw -Encoding UTF8 $bootstrap
  Get-Content -Raw -Encoding UTF8 $services
) -join "`n"
foreach ($required in @(
  'postgres:17.10@sha256:0af65001d05296a2ead57ac4a6412433d8913d1bb5d0c88435a7d1e1ee5cb04b',
  'eclipse-mosquitto:2.0.22@sha256:212f89e1eaeb2c322d6441b64396e3346026674db8fa9c27beac293405c32b3c',
  '127.0.0.1:55432:5432','18883','PGPASSFILE','WindowStyle Hidden','Start-Process -FilePath "$env:SystemRoot\System32\icacls.exe"',
  "importlib.metadata","version('paho-mqtt')",'-DOPENSSL_ROOT_DIR','mosquitto_passwd --parallel',
  'subst.exe','ascii-drive.txt','PSIsContainer','PackageNotFoundError','pgArgumentLine','ReadConsole',"ArgumentList @('-U'",
  'StartsWith($prefix, [StringComparison]::OrdinalIgnoreCase)',
  "postgresHost = '127.0.0.1'",'Test-ExpectedMosquittoProcess','mqtt-proof-','PETCARE_MQTT_PUBLISH_HOST',
  'container_bind_host','Invoke-PostgresProof','Get-NetIPAddress',
  'capabilityVersions','docker_version','compose_version','before1883',
  'mosquitto-dependencies.json','libssl_sha256','mosquitto_sha256',
  'auth.on_message','subscribed.wait','received.wait','postgresql-build.json','archive_sha256',
  "throw 'PostgreSQL is not ready'",'PostgreSQL credential file is missing',
  "throw 'Mosquitto stop failed; preserving the ASCII alias'",'function Stop-NativeServices'
)) { if (-not $allText.Contains($required)) { throw "ASSERT: missing contract $required" } }
foreach ($forbidden in @('mosquitto_passwd -b','mosquitto_passwd.exe -b',' --pw ',' -P ','$DockerPath compose','0.0.0.0:18883','"5432:5432"','-DWITH_TLS=OFF')) {
  if ($allText.Contains($forbidden)) { throw "ASSERT: forbidden contract $forbidden" }
}
foreach ($forbidden in @('winget.exe upgrade','mosquitto-$mosquittoVersion-install-windows-x64.exe')) {
  if ($allText.Contains($forbidden)) { throw "ASSERT: native source build bypassed: $forbidden" }
}
if ($allText.Contains('& $PSCommandPath -Action Stop')) { throw 'ASSERT: native reset recursively invokes the service script' }
$envExample = Get-Content -Encoding UTF8 (Join-Path $root '.env.example')
if ($envExample | Where-Object { $_ -match '=.+$' }) { throw 'ASSERT: example must not contain credential values' }
Write-Output 'Service contract fixture PASS'
