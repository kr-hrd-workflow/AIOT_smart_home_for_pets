[CmdletBinding()]
param(
    [ValidateSet('Native', 'Docker')]
    [string]$Provider = 'Native',
    [ValidateRange(1, 900)]
    [int]$TimeoutSeconds = 480,
    [string]$ToolchainRuntime = '',
    [string]$ServicesRuntime = '',
    [switch]$Fixture
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
Add-Type -AssemblyName System.Net.Http -ErrorAction Stop

$root = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$authorityPath = Join-Path $root 'tools\platform-manifest.json'
$toolchainPath = if ($ToolchainRuntime) { [IO.Path]::GetFullPath($ToolchainRuntime) } else { Join-Path $root '.runtime\toolchain.json' }
$servicesPath = if ($ServicesRuntime) { [IO.Path]::GetFullPath($ServicesRuntime) } else { Join-Path $root '.runtime\services.json' }
$servicesScript = Join-Path $root 'tools\services.ps1'
$e2eScript = Join-Path $root 'tools\e2e_check.py'
$privacyScript = Join-Path $root 'tools\privacy_check.py'
$provisionScript = Join-Path $root 'tools\provision_vision_model.ps1'
$fixturePath = Join-Path $root 'backend\tests\fixtures\vision_sequence.json'
$backendRoot = Join-Path $root 'backend'
$backendPythonPath = Join-Path $backendRoot '.venv\Scripts\python.exe'
$backendVenvConfigPath = Join-Path $backendRoot '.venv\pyvenv.cfg'
$deadline = [DateTimeOffset]::UtcNow.AddSeconds($TimeoutSeconds)
$streamDrainTimeoutMilliseconds = 10000
$powerShellPath = (Get-Process -Id $PID).Path

function Get-RequiredJson([string]$Path, [string]$Label) {
    if (-not [IO.Path]::IsPathRooted($Path) -or -not (Test-Path -LiteralPath $Path -PathType Leaf)) {
        throw "$Label is missing or not absolute"
    }
    try { return Get-Content -LiteralPath $Path -Raw -Encoding UTF8 | ConvertFrom-Json }
    catch { throw "$Label is invalid" }
}

$authority = Get-RequiredJson $authorityPath 'platform manifest'
$toolchain = Get-RequiredJson $toolchainPath 'toolchain runtime'
$services = Get-RequiredJson $servicesPath 'services runtime'
$authorityHash = (Get-FileHash -LiteralPath $authorityPath -Algorithm SHA256).Hash
if ($toolchain.manifest_sha256 -ne $authorityHash -or $services.manifest_sha256 -ne $authorityHash) {
    throw 'runtime authority hash mismatch'
}
if ($authority.schema_version -ne 1 -or $toolchain.schema_version -ne 1 -or $services.schema_version -ne 1 -or
    $toolchain.fixture -or $services.fixture -or $toolchain.platform -ne 'windows-x64' -or
    $toolchain.versions.python_path -ne "$($authority.managed_exact.python.version)+$($authority.managed_exact.python.build)" -or
    $toolchain.versions.node_path -ne $authority.managed_exact.node.version -or
    $services.versions.postgresql -ne $authority.managed_exact.postgresql.version -or
    $services.versions.mosquitto -ne $authority.managed_exact.mosquitto.version -or
    $services.versions.paho_mqtt -ne $authority.managed_exact.backend_dependencies.'paho-mqtt') {
    throw 'runtime identity/version closure mismatch'
}
foreach ($key in @('git_path','bash_path','uv_path','python_path','node_path','npm_cli_path')) {
    $path = [string]$toolchain.paths.$key
    if (-not [IO.Path]::IsPathRooted($path) -or -not (Test-Path -LiteralPath $path -PathType Leaf)) {
        throw "invalid toolchain closure: $key"
    }
}
if (-not (Test-Path -LiteralPath $backendPythonPath -PathType Leaf) -or
    -not (Test-Path -LiteralPath $backendVenvConfigPath -PathType Leaf)) {
    throw 'backend lock environment is missing'
}
$backendVenv = @{}
foreach ($line in Get-Content -LiteralPath $backendVenvConfigPath -Encoding UTF8) {
    if ($line -match '^\s*([^=]+?)\s*=\s*(.*?)\s*$') { $backendVenv[$matches[1].Trim()] = $matches[2].Trim() }
}
$expectedPythonHome = [IO.Path]::GetFullPath((Split-Path -Parent ([string]$toolchain.paths.python_path)))
if (-not $backendVenv.ContainsKey('home') -or
    [IO.Path]::GetFullPath([string]$backendVenv.home) -ne $expectedPythonHome -or
    $backendVenv.version_info -ne $authority.managed_exact.python.version -or
    $backendVenv.uv -ne $authority.managed_exact.uv.version -or
    $backendVenv.'include-system-site-packages' -ne 'false') {
    throw 'backend lock environment identity mismatch'
}
foreach ($key in @('postgres_path','pg_ctl_path','initdb_path','pg_isready_path','psql_path','mosquitto_path','mosquitto_passwd_path','python_path')) {
    $path = [string]$services.paths.$key
    if (-not [IO.Path]::IsPathRooted($path) -or -not (Test-Path -LiteralPath $path -PathType Leaf)) {
        throw "invalid service closure: $key"
    }
}
$localLive = $services.mqtt_profiles.local_live
if (-not $localLive -or $localLive.bind_host -ne '127.0.0.1' -or $localLive.client_host -ne '127.0.0.1' -or $localLive.port -ne 18883) {
    throw 'mqtt_profiles.local_live must be exact and loopback-only'
}
if ($services.ports.postgresql -ne 55432 -or $services.ports.mqtt -ne 18883) {
    throw 'service port authority mismatch'
}
foreach ($path in @($servicesScript,$e2eScript,$privacyScript,$provisionScript,$fixturePath)) {
    if (-not (Test-Path -LiteralPath $path -PathType Leaf)) { throw "required integration file is missing: $path" }
}
if ($Provider -eq 'Docker') {
    foreach ($key in @('docker_path','compose_plugin_path')) {
        $path = [string]$services.paths.$key
        if (-not [IO.Path]::IsPathRooted($path) -or -not (Test-Path -LiteralPath $path -PathType Leaf)) {
            throw "Docker provider closure is missing: $key"
        }
    }
    $compose = Get-Content -LiteralPath (Join-Path $root 'compose.yml') -Raw -Encoding UTF8
    foreach ($digest in @(
        'postgres:17.10@sha256:0af65001d05296a2ead57ac4a6412433d8913d1bb5d0c88435a7d1e1ee5cb04b',
        'eclipse-mosquitto:2.0.22@sha256:212f89e1eaeb2c322d6441b64396e3346026674db8fa9c27beac293405c32b3c'
    )) {
        if (-not $compose.Contains($digest)) { throw 'Compose image digest mismatch' }
    }
}

function Assert-EnvironmentBoundary {
    $allowed = @('PETCARE_POSTGRES_PASSWORD','PETCARE_MQTT_PASSWORD')
    foreach ($entry in [Environment]::GetEnvironmentVariables('Process').GetEnumerator()) {
        $name = [string]$entry.Key
        $value = [string]$entry.Value
        if (-not [string]::IsNullOrWhiteSpace($value) -and
            $name -match '(?i)(PASSWORD|TOKEN|SECRET|CREDENTIAL|PRIVATE_KEY|SERVICE_ROLE|ACCESS_KEY)' -and
            $name -notin $allowed) {
            throw 'inherited secret-like environment is not allowed'
        }
    }
}

function Get-Secret([string]$Name) {
    $value = [Environment]::GetEnvironmentVariable($Name, 'Process')
    if ([string]::IsNullOrWhiteSpace($value)) { throw "required secret environment is missing: $Name" }
    return $value
}

function Get-RemainingMilliseconds {
    $remaining = [int][Math]::Floor(($deadline - [DateTimeOffset]::UtcNow).TotalMilliseconds)
    if ($remaining -le 0) { throw 'PetCare integration global timeout exceeded' }
    return $remaining
}

function Test-PortOpen([int]$Port) {
    $client = [Net.Sockets.TcpClient]::new()
    try {
        $task = $client.ConnectAsync('127.0.0.1', $Port)
        return $task.Wait(150) -and $client.Connected
    } catch { return $false } finally { $client.Dispose() }
}

function Assert-PortsClosed([int[]]$Ports) {
    foreach ($port in $Ports) {
        if (Test-PortOpen $port) { throw "unexpected project listener is active: $port" }
    }
}

function Wait-Port([int]$Port) {
    while ((Get-RemainingMilliseconds) -gt 0) {
        if (Test-PortOpen $Port) { return }
        Start-Sleep -Milliseconds 100
    }
}

function Stop-ProcessTree([int]$ProcessId) {
    if ($ProcessId -le 0) { return }
    $ids = [Collections.Generic.List[int]]::new()
    try {
        $processes = @(Get-CimInstance Win32_Process -ErrorAction Stop)
        function Add-Descendants([int]$ParentId) {
            foreach ($process in $processes | Where-Object { $_.ParentProcessId -eq $ParentId }) {
                Add-Descendants ([int]$process.ProcessId)
                $ids.Add([int]$process.ProcessId)
            }
        }
        Add-Descendants $ProcessId
    } catch { $ids.Clear() }
    foreach ($id in $ids) { Stop-Process -Id $id -Force -ErrorAction SilentlyContinue }
    Stop-Process -Id $ProcessId -Force -ErrorAction SilentlyContinue
}

function ConvertTo-ProcessArgument([string]$Value) {
    if ($Value.Length -eq 0) { return '""' }
    if ($Value -notmatch '[\s"]') { return $Value }
    $result = [Text.StringBuilder]::new('"')
    $slashes = 0
    foreach ($character in $Value.ToCharArray()) {
        if ($character -eq '\') { $slashes += 1; continue }
        if ($character -eq '"') {
            [void]$result.Append(('\' * ($slashes * 2 + 1)) + '"')
        } else {
            [void]$result.Append(('\' * $slashes) + $character)
        }
        $slashes = 0
    }
    [void]$result.Append(('\' * ($slashes * 2)) + '"')
    return $result.ToString()
}

function Get-BaseEnvironment([ValidateSet('python','node','powershell')][string]$Kind) {
    $environment = @{}
    foreach ($name in @(
        'SystemRoot','WINDIR','SystemDrive','ComSpec','PATHEXT','TEMP','TMP',
        'USERPROFILE','USERNAME','APPDATA','LOCALAPPDATA','ProgramData','ALLUSERSPROFILE'
    )) {
        $value = [Environment]::GetEnvironmentVariable($name, 'Process')
        if (-not [string]::IsNullOrWhiteSpace($value)) { $environment[$name] = $value }
    }
    $system = Join-Path $env:SystemRoot 'System32'
    if ($Kind -eq 'node') {
        $environment.PATH = @(
            (Split-Path -Parent ([string]$toolchain.paths.node_path)),
            (Split-Path -Parent ([string]$toolchain.paths.git_path)),
            (Split-Path -Parent ([string]$toolchain.paths.bash_path)),
            $system
        ) -join [IO.Path]::PathSeparator
    } else {
        $environment.PATH = $system
    }
    $environment.PYTHONUTF8 = '1'
    $environment.PYTHONUNBUFFERED = '1'
    return $environment
}

function Start-IsolatedProcess(
    [string]$Name,
    [string]$FilePath,
    [string[]]$Arguments,
    [string]$WorkingDirectory,
    [hashtable]$Environment,
    [switch]$RedirectInput,
    [switch]$AllowOpenStreams
) {
    if (-not [IO.Path]::IsPathRooted($FilePath) -or -not (Test-Path -LiteralPath $FilePath -PathType Leaf)) {
        throw "child executable is not an absolute file: $Name"
    }
    $info = [Diagnostics.ProcessStartInfo]::new()
    $info.FileName = $FilePath
    $info.WorkingDirectory = $WorkingDirectory
    $info.UseShellExecute = $false
    $info.CreateNoWindow = $true
    $info.RedirectStandardOutput = $true
    $info.RedirectStandardError = $true
    $info.RedirectStandardInput = [bool]$RedirectInput
    $info.StandardOutputEncoding = [Text.UTF8Encoding]::new($false)
    $info.StandardErrorEncoding = [Text.UTF8Encoding]::new($false)
    $argumentText = ($Arguments | ForEach-Object { ConvertTo-ProcessArgument ([string]$_) }) -join ' '
    $info.Arguments = $argumentText
    if ($info.PSObject.Properties.Name -contains 'Environment') {
        $info.Environment.Clear()
        foreach ($key in $Environment.Keys) { $info.Environment[[string]$key] = [string]$Environment[$key] }
    } else {
        $info.EnvironmentVariables.Clear()
        foreach ($key in $Environment.Keys) { $info.EnvironmentVariables[[string]$key] = [string]$Environment[$key] }
    }
    $process = [Diagnostics.Process]::new()
    $process.StartInfo = $info
    if (-not $process.Start()) { throw "child failed to start: $Name" }
    try { $process.PriorityClass = [Diagnostics.ProcessPriorityClass]::BelowNormal } catch { }
    return [pscustomobject]@{
        Name = $Name
        Process = $process
        Stdout = $process.StandardOutput.ReadToEndAsync()
        Stderr = $process.StandardError.ReadToEndAsync()
        CommandLine = "$FilePath $argumentText"
        Completed = $false
        AllowOpenStreams = [bool]$AllowOpenStreams
    }
}

function Get-SecretForms([string[]]$Secrets) {
    $forms = [Collections.Generic.HashSet[string]]::new([StringComparer]::Ordinal)
    foreach ($secret in $Secrets) {
        if ([string]::IsNullOrEmpty($secret)) { continue }
        $bytes = [Text.Encoding]::UTF8.GetBytes($secret)
        $base64 = [Convert]::ToBase64String($bytes)
        $hex = -join ($bytes | ForEach-Object { $_.ToString('X2') })
        $percent = [Uri]::EscapeDataString($secret)
        foreach ($value in @(
            $secret,$percent,$percent.Replace('%20','+'),$base64,$base64.TrimEnd('='),
            $base64.Replace('+','-').Replace('/','_'),$base64.Replace('+','-').Replace('/','_').TrimEnd('='),
            $hex,$hex.ToLowerInvariant(),
            "postgresql://petcare:${percent}@127.0.0.1:55432/petcare",
            "postgresql+psycopg://petcare:${percent}@127.0.0.1:55432/petcare",
            "mqtt://petcare:${percent}@127.0.0.1:18883"
        )) { if ($value) { [void]$forms.Add($value) } }
    }
    return @($forms)
}

function Protect-Text([string]$Text, [string[]]$Secrets) {
    $safe = [string]$Text
    foreach ($form in (Get-SecretForms $Secrets | Sort-Object Length -Descending)) {
        $safe = $safe.Replace($form, '[REDACTED]')
    }
    foreach ($identity in @(
        @{ Value = $root; Replacement = '[PROJECT_ROOT]' },
        @{ Value = $env:USERPROFILE; Replacement = '[USER_HOME]' },
        @{ Value = $env:USERNAME; Replacement = '[OS_USER]' }
    )) {
        $value = [string]$identity.Value
        if ([string]::IsNullOrWhiteSpace($value)) { continue }
        foreach ($form in @($value, $value.Replace('\','/'), $value.Replace('\','\\')) | Select-Object -Unique) {
            $safe = [regex]::Replace(
                $safe,
                [regex]::Escape($form),
                [string]$identity.Replacement,
                [Text.RegularExpressions.RegexOptions]::IgnoreCase
            )
        }
    }
    $safe = [regex]::Replace($safe, '(?i)(?:https?|wss?)://(?:localhost|127(?:\.\d{1,3}){3})(?::\d+)?[^\s]*', '[LOCAL_URL]')
    return $safe
}

function Complete-Child($Child, [string]$LogRoot, [string[]]$Secrets) {
    if ($Child.Completed) { return $Child.Process.ExitCode }
    $Child.Process.WaitForExit()
    $streams = [Threading.Tasks.Task]::WhenAll([Threading.Tasks.Task[]]@($Child.Stdout,$Child.Stderr))
    $streamsClosed = $streams.Wait($streamDrainTimeoutMilliseconds)
    if (-not $streamsClosed -and -not $Child.AllowOpenStreams) {
        throw "child output streams remained open: $($Child.Name)"
    }
    $stdout = if ($Child.Stdout.IsCompleted) { Protect-Text $Child.Stdout.Result $Secrets } else { '[managed service output pipe open]' }
    $stderr = if ($Child.Stderr.IsCompleted) { Protect-Text $Child.Stderr.Result $Secrets } else { '' }
    [IO.File]::WriteAllText((Join-Path $LogRoot "$($Child.Name).stdout.log"), $stdout, [Text.UTF8Encoding]::new($false))
    [IO.File]::WriteAllText((Join-Path $LogRoot "$($Child.Name).stderr.log"), $stderr, [Text.UTF8Encoding]::new($false))
    [IO.File]::WriteAllText(
        (Join-Path $LogRoot "$($Child.Name).command.log"),
        (Protect-Text ([string]$Child.CommandLine) $Secrets),
        [Text.UTF8Encoding]::new($false)
    )
    $Child.Completed = $true
    return $Child.Process.ExitCode
}

function Wait-Child($Child, [string]$LogRoot, [string[]]$Secrets) {
    while (-not $Child.Process.HasExited) {
        if ((Get-RemainingMilliseconds) -le 0) { break }
        if (-not $Child.Process.WaitForExit([Math]::Min(250, (Get-RemainingMilliseconds)))) { continue }
    }
    if (-not $Child.Process.HasExited) {
        Stop-ProcessTree $Child.Process.Id
        throw 'PetCare integration global timeout exceeded'
    }
    $code = Complete-Child $Child $LogRoot $Secrets
    if ($code -ne 0) { throw "$($Child.Name) failed" }
}

function Wait-Backend([ValidateSet('invalid','healthy')][string]$State, $Child) {
    $client = [Net.Http.HttpClient]::new()
    try {
        while ((Get-RemainingMilliseconds) -gt 0) {
            $Child.Process.Refresh()
            if ($Child.Process.HasExited) { throw 'backend exited before readiness' }
            try {
                if ($State -eq 'invalid') {
                    $json = $client.GetStringAsync('http://127.0.0.1:8000/api/camera/status').GetAwaiter().GetResult() | ConvertFrom-Json
                    if ($json.state -eq 'offline' -and $json.reason -eq 'invalid_frame_shape') { return }
                } else {
                    $json = $client.GetStringAsync('http://127.0.0.1:8000/api/health').GetAwaiter().GetResult() | ConvertFrom-Json
                    if ($json.status -eq 'healthy' -and $json.database -eq 'up' -and $json.mqtt -eq 'up' -and $json.camera -eq 'online' -and $json.queue -eq 'ok' -and $json.worker -eq 'running') { return }
                }
            } catch { }
            Start-Sleep -Milliseconds 200
        }
    } finally { $client.Dispose() }
}

function Wait-Dashboard($Child) {
    $client = [Net.Http.HttpClient]::new()
    try {
        while ((Get-RemainingMilliseconds) -gt 0) {
            $Child.Process.Refresh()
            if ($Child.Process.HasExited) { throw 'dashboard exited before readiness' }
            $response = $null
            try {
                $response = $client.GetAsync('http://127.0.0.1:3000/').GetAwaiter().GetResult()
                if ($response.IsSuccessStatusCode) {
                    $body = $response.Content.ReadAsStringAsync().GetAwaiter().GetResult()
                    if ($body.Contains('PetCare')) { return }
                }
            } catch { }
            finally { if ($null -ne $response) { $response.Dispose() } }
            Start-Sleep -Milliseconds 200
        }
        throw 'dashboard did not become HTTP-ready'
    } finally { $client.Dispose() }
}

function Stop-BackendGracefully($Child, [string]$LogRoot, [string[]]$Secrets) {
    if ($Child.Process.HasExited) { throw 'backend exited before graceful shutdown request' }
    $Child.Process.StandardInput.WriteLine('STOP')
    $Child.Process.StandardInput.Close()
    $limit = [DateTimeOffset]::UtcNow.AddSeconds(30)
    while (-not $Child.Process.HasExited -and [DateTimeOffset]::UtcNow -lt $limit) {
        if ((Get-RemainingMilliseconds) -le 0) { break }
        $Child.Process.WaitForExit(250) | Out-Null
    }
    if (-not $Child.Process.HasExited) {
        Stop-ProcessTree $Child.Process.Id
        throw 'backend graceful shutdown timed out'
    }
    $code = Complete-Child $Child $LogRoot $Secrets
    $stdout = Get-Content -LiteralPath (Join-Path $LogRoot "$($Child.Name).stdout.log") -Raw -ErrorAction SilentlyContinue
    if ($code -ne 0 -or -not $stdout.Contains('BACKEND_SERVER_STOPPED=GRACEFUL')) {
        throw 'backend did not prove graceful lifecycle shutdown'
    }
}

Assert-EnvironmentBoundary
Assert-PortsClosed @(8000,3000,55432,18883,58082,58083)
$logRoot = Join-Path $root ('.runtime\integration\' + [guid]::NewGuid().ToString('N'))
$resolvedLogRoot = [IO.Path]::GetFullPath($logRoot)
$allowedRuntime = [IO.Path]::GetFullPath((Join-Path $root '.runtime\integration'))
if (-not $resolvedLogRoot.StartsWith($allowedRuntime + [IO.Path]::DirectorySeparatorChar, [StringComparison]::OrdinalIgnoreCase)) {
    throw 'integration runtime path escaped the repository'
}
New-Item -ItemType Directory -Path $logRoot -Force | Out-Null

$children = [Collections.Generic.List[object]]::new()
$servicesStarted = $false
$firstError = $null
$secretsForRedaction = @()
try {
    $syncEnvironment = Get-BaseEnvironment 'python'
    $sync = Start-IsolatedProcess 'backend-lock-sync' $toolchain.paths.uv_path @(
        'sync','--project',$backendRoot,'--locked','--offline','--python',$toolchain.paths.python_path,
        '--no-install-project'
    ) $root $syncEnvironment
    $children.Add($sync)
    Wait-Child $sync $logRoot @()

    if ($Fixture) {
        $environment = Get-BaseEnvironment 'python'
        $server = Start-IsolatedProcess 'fixture-server' $backendPythonPath @($e2eScript,'fixture-server','--port','58082') $root $environment -RedirectInput
        $children.Add($server)
        Wait-Port 58082
        if ($env:PETCARE_RUNNER_FIXTURE_HANG -eq '1') {
            $hang = Start-IsolatedProcess 'fixture-hang' $backendPythonPath @($e2eScript,'fixture-hang','--port','58083') $root $environment
            $children.Add($hang)
            Wait-Port 58083
            Write-Output 'PETCARE_RUNNER_FIXTURE_HANG_READY'
            while ($true) { Get-RemainingMilliseconds | Out-Null; Start-Sleep -Milliseconds 100 }
        }
        $client = Start-IsolatedProcess 'fixture-client' $backendPythonPath @($e2eScript,'fixture-client','--port','58082') $root $environment
        $children.Add($client)
        Wait-Child $client $logRoot @()
        $server.Process.StandardInput.WriteLine('STOP')
        $server.Process.StandardInput.Close()
        Wait-Child $server $logRoot @()
        Assert-PortsClosed @(58082,58083)
        Write-Output 'PETCARE_LOCAL_INTEGRATION_FIXTURE=PASS'
        exit 0
    }

    $postgresPassword = Get-Secret 'PETCARE_POSTGRES_PASSWORD'
    $mqttUsername = [Environment]::GetEnvironmentVariable('PETCARE_MQTT_USERNAME', 'Process')
    if ([string]::IsNullOrWhiteSpace($mqttUsername)) { throw 'required MQTT username environment is missing' }
    $mqttPassword = Get-Secret 'PETCARE_MQTT_PASSWORD'
    if ($postgresPassword -eq $mqttPassword) { throw 'database and MQTT secrets must be independent' }
    $sitesSentinel = 'sites-' + [guid]::NewGuid().ToString('N')
    $secretsForRedaction = @($postgresPassword,$mqttPassword,$sitesSentinel)
    $encodedPostgres = [Uri]::EscapeDataString($postgresPassword)
    $databaseUrl = "postgresql+psycopg://petcare:${encodedPostgres}@127.0.0.1:55432/petcare_test"
    $modelPath = Join-Path $root '.runtime\models\yolo11n.pt'
    $validCamera = Join-Path $logRoot 'camera-valid.png'
    $invalidCamera = Join-Path $logRoot 'camera-invalid.png'
    $resultPath = Join-Path $logRoot 'external-result.json'
    $providerValue = if ($Provider -eq 'Native') { 'native' } else { 'compose' }

    $serviceEnvironment = Get-BaseEnvironment 'powershell'
    $serviceEnvironment.PETCARE_POSTGRES_PASSWORD = $postgresPassword
    $serviceEnvironment.PETCARE_MQTT_USERNAME = $mqttUsername
    $serviceEnvironment.PETCARE_MQTT_PASSWORD = $mqttPassword
    $reset = Start-IsolatedProcess 'services-reset' $powerShellPath @('-NoProfile','-ExecutionPolicy','Bypass','-File',$servicesScript,'-Action','Reset','-RuntimePath',$servicesPath,'-Provider',$providerValue,'-Profile','local_live','-ConfirmReset') $root $serviceEnvironment
    $children.Add($reset); Wait-Child $reset $logRoot $secretsForRedaction
    $start = Start-IsolatedProcess 'services-start' $powerShellPath @('-NoProfile','-ExecutionPolicy','Bypass','-File',$servicesScript,'-Action','Start','-RuntimePath',$servicesPath,'-Provider',$providerValue,'-Profile','local_live') $root $serviceEnvironment -AllowOpenStreams
    $children.Add($start); Wait-Child $start $logRoot $secretsForRedaction
    $servicesStarted = $true
    $listeners = @(Get-NetTCPConnection -State Listen -LocalPort 55432,18883 -ErrorAction Stop)
    if ($listeners.Count -ne 2 -or @($listeners | Where-Object { $_.LocalAddress -ne '127.0.0.1' }).Count -ne 0) {
        throw 'local-live service listener escaped loopback'
    }

    $provisionEnvironment = Get-BaseEnvironment 'powershell'
    $provision = Start-IsolatedProcess 'vision-model-provision' $powerShellPath @(
        '-NoProfile','-ExecutionPolicy','Bypass','-File',$provisionScript
    ) $root $provisionEnvironment
    $children.Add($provision); Wait-Child $provision $logRoot $secretsForRedaction
    if (-not (Test-Path -LiteralPath $modelPath -PathType Leaf) -or
        (Get-Item -LiteralPath $modelPath).Length -ne 5613764 -or
        (Get-FileHash -LiteralPath $modelPath -Algorithm SHA256).Hash -ne '0EBBC80D4A7680D14987A577CD21342B65ECFD94632BD9A8DA63AE6417644EE1') {
        throw 'pinned YOLO model identity mismatch'
    }

    $pythonEnvironment = Get-BaseEnvironment 'python'
    $prepare = Start-IsolatedProcess 'camera-prepare' $backendPythonPath @($e2eScript,'prepare-camera','--valid',$validCamera,'--invalid',$invalidCamera) $root $pythonEnvironment
    $children.Add($prepare); Wait-Child $prepare $logRoot $secretsForRedaction

    $backendEnvironment = Get-BaseEnvironment 'python'
    $backendEnvironment.DATABASE_URL = $databaseUrl
    $backendEnvironment.PETCARE_MQTT_PROFILE = 'local_live'
    $backendEnvironment.PETCARE_SERVICES_MANIFEST = $servicesPath
    $backendEnvironment.PETCARE_MQTT_USERNAME = $mqttUsername
    $backendEnvironment.PETCARE_MQTT_PASSWORD = $mqttPassword
    $backendEnvironment.PETCARE_CAMERA_SOURCE = 'file'
    $backendEnvironment.PETCARE_CAMERA_MODEL = $modelPath
    $database = Start-IsolatedProcess 'database-prepare' $backendPythonPath @(
        $e2eScript,'prepare-database'
    ) $root $backendEnvironment
    $children.Add($database); Wait-Child $database $logRoot $secretsForRedaction
    $migration = Start-IsolatedProcess 'alembic-upgrade' $backendPythonPath @('-m','alembic','-c','alembic.ini','upgrade','head') (Join-Path $root 'backend') $backendEnvironment
    $children.Add($migration); Wait-Child $migration $logRoot $secretsForRedaction

    $backendEnvironment.PETCARE_CAMERA_FILE = $invalidCamera
    $invalidBackend = Start-IsolatedProcess 'backend-invalid-shape' $backendPythonPath @($e2eScript,'serve-backend') $root $backendEnvironment -RedirectInput
    $children.Add($invalidBackend)
    Wait-Backend 'invalid' $invalidBackend
    Stop-BackendGracefully $invalidBackend $logRoot $secretsForRedaction

    $backendEnvironment.PETCARE_CAMERA_FILE = $validCamera
    $modelBackend = Start-IsolatedProcess 'backend-real-model' $backendPythonPath @($e2eScript,'serve-backend') $root $backendEnvironment -RedirectInput
    $children.Add($modelBackend)
    Wait-Backend 'healthy' $modelBackend
    Stop-BackendGracefully $modelBackend $logRoot $secretsForRedaction

    $testEnvironment = Get-BaseEnvironment 'python'
    $testEnvironment.TEST_DATABASE_URL = $databaseUrl
    $testEnvironment.PETCARE_LIVE_FIXTURE = '1'
    $testEnvironment.PETCARE_SERVICES_MANIFEST = $servicesPath
    $testEnvironment.PETCARE_MQTT_USERNAME = $mqttUsername
    $testEnvironment.PETCARE_MQTT_PASSWORD = $mqttPassword
    $tests = Start-IsolatedProcess 'pytest-live-stack' $backendPythonPath @(
        '-m','pytest','backend/tests/integration/test_live_stack.py','backend/tests/test_rule_ingress.py','backend/tests/test_rule_worker.py','-q'
    ) $root $testEnvironment
    $children.Add($tests); Wait-Child $tests $logRoot $secretsForRedaction

    $liveBackend = Start-IsolatedProcess 'backend-live' $backendPythonPath @($e2eScript,'serve-backend') $root $backendEnvironment -RedirectInput
    $children.Add($liveBackend)
    Wait-Backend 'healthy' $liveBackend

    $nodeEnvironment = Get-BaseEnvironment 'node'
    $nodeEnvironment.npm_config_script_shell = [string]$toolchain.paths.bash_path
    $nodeEnvironment.WRANGLER_LOG_PATH = (Join-Path $logRoot 'wrangler.log')
    $dashboard = Start-IsolatedProcess 'dashboard-live' $toolchain.paths.node_path @($toolchain.paths.npm_cli_path,'run','dev','--','--hostname','127.0.0.1','--port','3000') (Join-Path $root 'dashboard') $nodeEnvironment
    $children.Add($dashboard)
    Wait-Dashboard $dashboard

    $externalEnvironment = Get-BaseEnvironment 'python'
    $externalEnvironment.PETCARE_SERVICES_MANIFEST = $servicesPath
    $externalEnvironment.PETCARE_MQTT_USERNAME = $mqttUsername
    $externalEnvironment.PETCARE_MQTT_PASSWORD = $mqttPassword
    $external = Start-IsolatedProcess 'external-check' $backendPythonPath @($e2eScript,'verify-external','--output',$resultPath) $root $externalEnvironment
    $children.Add($external); Wait-Child $external $logRoot $secretsForRedaction

    Stop-ProcessTree $liveBackend.Process.Id
    $liveBackend.Process.WaitForExit(10000) | Out-Null
    Complete-Child $liveBackend $logRoot $secretsForRedaction | Out-Null
    Assert-PortsClosed @(8000)
    $restartBackend = Start-IsolatedProcess 'backend-restart' $backendPythonPath @($e2eScript,'serve-backend') $root $backendEnvironment -RedirectInput
    $children.Add($restartBackend)
    Wait-Backend 'healthy' $restartBackend
    $restart = Start-IsolatedProcess 'restart-check' $backendPythonPath @($e2eScript,'verify-restart','--baseline',$resultPath) $root $externalEnvironment
    $children.Add($restart); Wait-Child $restart $logRoot $secretsForRedaction
    Stop-BackendGracefully $restartBackend $logRoot $secretsForRedaction

    Stop-ProcessTree $dashboard.Process.Id
    $dashboard.Process.WaitForExit(10000) | Out-Null
    Complete-Child $dashboard $logRoot $secretsForRedaction | Out-Null
    Remove-Item -LiteralPath $validCamera,$invalidCamera -Force

    $stop = Start-IsolatedProcess 'services-stop' $powerShellPath @(
        '-NoProfile','-ExecutionPolicy','Bypass','-File',$servicesScript,
        '-Action','Stop','-RuntimePath',$servicesPath,'-Provider',$providerValue,'-Profile','local_live'
    ) $root $serviceEnvironment
    $children.Add($stop); Wait-Child $stop $logRoot $secretsForRedaction
    $servicesStarted = $false
    Assert-PortsClosed @(8000,3000,55432,18883)

    $wranglerLog = Join-Path $logRoot 'wrangler.log'
    if (Test-Path -LiteralPath $wranglerLog -PathType Leaf) {
        $safeWranglerLog = Protect-Text (Get-Content -LiteralPath $wranglerLog -Raw -Encoding UTF8) $secretsForRedaction
        [IO.File]::WriteAllText($wranglerLog, $safeWranglerLog, [Text.UTF8Encoding]::new($false))
    }

    $combinedLog = Join-Path $logRoot 'task-14-redacted.log'
    $combined = Get-ChildItem -LiteralPath $logRoot -Filter '*.log' -File | Sort-Object Name | ForEach-Object {
        "[$($_.Name)]`n" + (Get-Content -LiteralPath $_.FullName -Raw -Encoding UTF8)
    }
    [IO.File]::WriteAllText($combinedLog, (Protect-Text ($combined -join "`n") $secretsForRedaction), [Text.UTF8Encoding]::new($false))
    $artifactSource = Join-Path $logRoot 'task-14-petcare-sites-mvp-completion.json'
    $artifact = [ordered]@{
        task_or_gate_id = '14'
        kind = 'local-live'
        status = 'PASS'
        red = @(
            [ordered]@{command='invalid 640x479 camera frame';exit_code=1;result='EXPECTED_RED'},
            [ordered]@{command='hostile HTTP and WebSocket Origin';exit_code=403;result='EXPECTED_RED'},
            [ordered]@{command='anonymous MQTT connection';exit_code=1;result='EXPECTED_RED'}
        )
        checks = @(
            [ordered]@{command='manifest-backed real local-live integration';exit_code=0;result='PASS'},
            [ordered]@{command='production-handler dog and cat fixture sequence with wall-clock thresholds';exit_code=0;result='PASS'},
            [ordered]@{command='hard restart persistence and no replay';exit_code=0;result='PASS'},
            [ordered]@{command='privacy sentinel scan';exit_code=0;result='PASS'}
        )
        hardware = [ordered]@{status='NOT_RUN';reason='physical Pico nodes and webcam were not connected'}
    }
    [IO.File]::WriteAllText($artifactSource, ($artifact | ConvertTo-Json -Depth 8), [Text.UTF8Encoding]::new($false))

    $privacyEnvironment = Get-BaseEnvironment 'python'
    $privacyEnvironment.PETCARE_TEST_GIT = [string]$toolchain.paths.git_path
    $privacyEnvironment.PETCARE_SENTINEL_DB = $postgresPassword
    $privacyEnvironment.PETCARE_SENTINEL_MQTT = $mqttPassword
    $privacyEnvironment.PETCARE_SENTINEL_SITES = $sitesSentinel
    $privacy = Start-IsolatedProcess 'privacy-check' $backendPythonPath @(
        $privacyScript,'--repo',$root,'--artifact',$logRoot,
        '--sentinel-environment','PETCARE_SENTINEL_DB',
        '--sentinel-environment','PETCARE_SENTINEL_MQTT',
        '--sentinel-environment','PETCARE_SENTINEL_SITES'
    ) $root $privacyEnvironment
    $children.Add($privacy); Wait-Child $privacy $logRoot $secretsForRedaction

    $evidenceRoot = Join-Path $root '.omo\evidence'
    New-Item -ItemType Directory -Path $evidenceRoot -Force | Out-Null
    foreach ($source in @($artifactSource,$combinedLog)) {
        $destination = Join-Path $evidenceRoot ([IO.Path]::GetFileName($source))
        $temporary = $destination + '.tmp'
        [IO.File]::WriteAllBytes($temporary, [IO.File]::ReadAllBytes($source))
        Move-Item -LiteralPath $temporary -Destination $destination -Force
        if ((Get-FileHash -LiteralPath $source -Algorithm SHA256).Hash -ne (Get-FileHash -LiteralPath $destination -Algorithm SHA256).Hash) {
            throw 'atomic evidence copy hash mismatch'
        }
    }
}
catch {
    $firstError = $_
}
finally {
    foreach ($child in $children) {
        try {
            $child.Process.Refresh()
            if (-not $child.Process.HasExited) {
                Stop-ProcessTree $child.Process.Id
                $child.Process.WaitForExit(10000) | Out-Null
            }
            if ($child.Process.HasExited) { Complete-Child $child $logRoot $secretsForRedaction | Out-Null }
        } catch { if ($null -eq $firstError) { $firstError = $_ } }
    }
    if ($servicesStarted) {
        try {
            $stopEnvironment = Get-BaseEnvironment 'powershell'
            $stopEnvironment.PETCARE_POSTGRES_PASSWORD = $postgresPassword
            $stopEnvironment.PETCARE_MQTT_USERNAME = $mqttUsername
            $stopEnvironment.PETCARE_MQTT_PASSWORD = $mqttPassword
            $stop = Start-IsolatedProcess 'services-stop' $powerShellPath @('-NoProfile','-ExecutionPolicy','Bypass','-File',$servicesScript,'-Action','Stop','-RuntimePath',$servicesPath,'-Provider',$providerValue,'-Profile','local_live') $root $stopEnvironment
            $children.Add($stop); Wait-Child $stop $logRoot $secretsForRedaction
        } catch { if ($null -eq $firstError) { $firstError = $_ } }
    }
    if (Test-Path -LiteralPath $logRoot -PathType Container) {
        try {
            foreach ($media in Get-ChildItem -LiteralPath $logRoot -File | Where-Object { $_.Extension -in @('.png','.jpg','.jpeg','.webp','.mp4','.mjpeg') }) {
                Remove-Item -LiteralPath $media.FullName -Force
            }
            foreach ($log in Get-ChildItem -LiteralPath $logRoot -Filter '*.log' -File) {
                $safe = Protect-Text (Get-Content -LiteralPath $log.FullName -Raw -Encoding UTF8) $secretsForRedaction
                [IO.File]::WriteAllText($log.FullName, $safe, [Text.UTF8Encoding]::new($false))
            }
        } catch { if ($null -eq $firstError) { $firstError = $_ } }
    }
    if ($Fixture -and (Test-Path -LiteralPath $logRoot -PathType Container)) {
        try { Remove-Item -LiteralPath $logRoot -Recurse -Force }
        catch { if ($null -eq $firstError) { $firstError = $_ } }
    }
}

if ($null -ne $firstError) { throw $firstError }
Assert-PortsClosed @(8000,3000,55432,18883,58082,58083)
Write-Output 'PETCARE_LOCAL_INTEGRATION=PASS'
