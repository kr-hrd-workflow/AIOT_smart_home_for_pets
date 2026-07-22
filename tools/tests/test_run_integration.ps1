$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$root = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
$runner = Join-Path $root 'tools\run_integration.ps1'
$toolchainPath = Join-Path $root '.runtime\toolchain.json'
$servicesPath = Join-Path $root '.runtime\services.json'
$powerShellPath = Join-Path $PSHOME 'powershell.exe'

foreach ($path in @($runner,$toolchainPath,$servicesPath,$powerShellPath)) {
    if (-not [IO.Path]::IsPathRooted($path) -or -not (Test-Path -LiteralPath $path -PathType Leaf)) {
        throw 'runner test prerequisite is missing or not absolute'
    }
}

$source = Get-Content -LiteralPath $runner -Raw -Encoding UTF8
foreach ($required in @(
    "[ValidateSet('Native', 'Docker')]",
    "mqtt_profiles.local_live",
    "127.0.0.1",
    "18883",
    "55432",
    "RedirectStandardOutput = `$true",
    "RedirectStandardError = `$true",
    "StandardOutputEncoding = [Text.UTF8Encoding]::new(`$false)",
    "StandardErrorEncoding = [Text.UTF8Encoding]::new(`$false)",
    "ProcessPriorityClass]::BelowNormal",
    "Add-Type -AssemblyName System.Net.Http -ErrorAction Stop",
    "Stop-ProcessTree",
    "AllowOpenStreams",
    "backend-lock-sync",
    "'--locked'",
    "'--offline'",
    ".venv\Scripts\python.exe",
    "'SystemDrive','ComSpec','PATHEXT'",
    "'ProgramData','ALLUSERSPROFILE'",
    "Replacement = '[PROJECT_ROOT]'",
    "Replacement = '[USER_HOME]'",
    "Replacement = '[OS_USER]'",
    "'--hostname','127.0.0.1'",
    "Wait-Dashboard",
    "PETCARE_LOCAL_INTEGRATION=PASS"
)) {
    if (-not $source.Contains($required)) { throw "runner source is missing required boundary: $required" }
}
if ($source -match '(?im)^\s*\[(?:string|securestring)\]\$(?:DatabaseUrl|MqttPassword|Token|Secret)\b') {
    throw 'runner exposes a forbidden secret or URL parameter'
}
if ($source.Contains("'--host','127.0.0.1'")) {
    throw 'runner uses vinext unsupported --host instead of --hostname'
}
if ($source.Contains('Wait-Port 3000')) {
    throw 'runner starts external checks before the dashboard is HTTP-ready'
}

$savedSecretEnvironment = @{}
foreach ($entry in [Environment]::GetEnvironmentVariables('Process').GetEnumerator()) {
    $name = [string]$entry.Key
    if ($name -match '(?i)(PASSWORD|TOKEN|SECRET|CREDENTIAL|PRIVATE_KEY|SERVICE_ROLE|ACCESS_KEY)') {
        $savedSecretEnvironment[$name] = [string]$entry.Value
        [Environment]::SetEnvironmentVariable($name, $null, 'Process')
    }
}

function Invoke-Runner {
    param(
        [string[]]$Arguments,
        [hashtable]$Environment = @{}
    )
    $saved = @{ PATH = [Environment]::GetEnvironmentVariable('PATH', 'Process') }
    foreach ($name in $Environment.Keys) {
        $saved[$name] = [Environment]::GetEnvironmentVariable([string]$name, 'Process')
    }
    try {
        [Environment]::SetEnvironmentVariable('PATH', (Join-Path $env:SystemRoot 'System32'), 'Process')
        foreach ($name in $Environment.Keys) {
            [Environment]::SetEnvironmentVariable([string]$name, [string]$Environment[$name], 'Process')
        }
        $previous = $ErrorActionPreference
        try {
            $ErrorActionPreference = 'Continue'
            $output = & $powerShellPath -NoProfile -ExecutionPolicy Bypass -File $runner @Arguments 2>&1 | Out-String
            $code = $LASTEXITCODE
        }
        finally { $ErrorActionPreference = $previous }
        return @{ Code = $code; Output = $output }
    }
    finally {
        foreach ($name in $saved.Keys) {
            [Environment]::SetEnvironmentVariable([string]$name, $saved[$name], 'Process')
        }
    }
}

function Test-PortClosed([int]$Port) {
    for ($attempt = 0; $attempt -lt 20; $attempt++) {
        $client = [Net.Sockets.TcpClient]::new()
        try {
            $task = $client.ConnectAsync('127.0.0.1', $Port)
            if ($task.Wait(100) -and $client.Connected) {
                Start-Sleep -Milliseconds 100
                continue
            }
            return $true
        }
        catch { return $true }
        finally { $client.Dispose() }
    }
    return $false
}

try {
    $fixture = Invoke-Runner @(
        '-Fixture','-TimeoutSeconds','30',
        '-ToolchainRuntime',$toolchainPath,'-ServicesRuntime',$servicesPath
    )
    if ($fixture.Code -ne 0 -or -not $fixture.Output.Contains('PETCARE_LOCAL_INTEGRATION_FIXTURE=PASS')) {
        throw "fixture runner failed through manifest tools: $($fixture.Output)"
    }
    if (-not (Test-PortClosed 58082) -or -not (Test-PortClosed 58083)) {
        throw 'fixture runner left a project listener active'
    }

    $secretValue = 'runner-secret-must-not-print'
    $rejected = Invoke-Runner @('-Fixture') @{ CLOUDFLARE_API_TOKEN = $secretValue }
    if ($rejected.Code -eq 0 -or -not $rejected.Output.Contains('inherited secret-like environment is not allowed')) {
        throw 'runner accepted an inherited production credential'
    }
    if ($rejected.Output.Contains($secretValue)) { throw 'runner printed an inherited credential value' }

    $listener = [Net.Sockets.TcpListener]::new([Net.IPAddress]::Loopback, 58082)
    try {
        $listener.Start()
        $occupied = Invoke-Runner @('-Fixture')
        if ($occupied.Code -eq 0 -or -not $occupied.Output.Contains('unexpected project listener')) {
            throw 'runner accepted an unexpected project listener'
        }
    }
    finally { $listener.Stop() }

    $timeout = Invoke-Runner @('-Fixture','-TimeoutSeconds','5') @{ PETCARE_RUNNER_FIXTURE_HANG = '1' }
    if ($timeout.Code -eq 0 -or
        -not $timeout.Output.Contains('PETCARE_RUNNER_FIXTURE_HANG_READY') -or
        -not $timeout.Output.Contains('PetCare integration global timeout exceeded')) {
        throw 'runner did not enforce its global timeout'
    }
    if (-not (Test-PortClosed 58082) -or -not (Test-PortClosed 58083)) {
        throw 'runner left a fixture process tree after timeout'
    }
}
finally {
    foreach ($name in $savedSecretEnvironment.Keys) {
        [Environment]::SetEnvironmentVariable($name, $savedSecretEnvironment[$name], 'Process')
    }
}

Write-Output 'PetCare local integration runner tests PASS'
