[CmdletBinding()]
param(
    [switch]$Fixture,
    [ValidateRange(1, 600)]
    [int]$TimeoutSeconds = 120
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$root = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$toolchainPath = Join-Path $root '.runtime\toolchain.json'
$authorityPath = Join-Path $root 'tools\platform-manifest.json'
$collectorPath = Join-Path $root 'tools\jetson_vision_soak.py'
$evidenceDirectory = Join-Path $root '.runtime\evidence'

function Get-ManagedTools {
    if (-not (Test-Path -LiteralPath $toolchainPath -PathType Leaf) -or
        -not (Test-Path -LiteralPath $authorityPath -PathType Leaf)) {
        throw 'managed toolchain manifest is missing'
    }
    try {
        $manifest = Get-Content -LiteralPath $toolchainPath -Raw -Encoding UTF8 | ConvertFrom-Json
        $authorityHash = (Get-FileHash -LiteralPath $authorityPath -Algorithm SHA256).Hash
        $gitPath = [string]$manifest.paths.git_path
        $pythonPath = [string]$manifest.paths.python_path
    }
    catch {
        throw 'managed toolchain manifest is invalid'
    }
    if ($manifest.schema_version -ne 1 -or $manifest.manifest_sha256 -ne $authorityHash -or
        -not [System.IO.Path]::IsPathRooted($gitPath) -or
        -not [System.IO.Path]::IsPathRooted($pythonPath) -or
        -not (Test-Path -LiteralPath $gitPath -PathType Leaf) -or
        -not (Test-Path -LiteralPath $pythonPath -PathType Leaf)) {
        throw 'managed Git/Python identity is invalid'
    }
    return @{ Git = $gitPath; Python = $pythonPath }
}

function Assert-SecretEnvironment {
    foreach ($entry in [Environment]::GetEnvironmentVariables('Process').GetEnumerator()) {
        $name = [string]$entry.Key
        $value = [string]$entry.Value
        if ([string]::IsNullOrWhiteSpace($value) -or $name -eq 'TEST_DATABASE_URL') {
            continue
        }
        if ($name -match '(?i)^(DATABASE_URL|PETCARE_AGENT_CONFIG|PETCARE_AGENT_TOOLS|PETCARE_JETSON_CONFIG)$' -or
            $name -match '(?i)((CONFIG|CREDENTIAL).*(PATH|FILE)|(PATH|FILE).*(CONFIG|CREDENTIAL))') {
            throw 'inherited production configuration is not allowed'
        }
        if ($name -match '(?i)(PASSWORD|TOKEN|SECRET|CREDENTIAL|PRIVATE_KEY|SERVICE_ROLE|ACCESS_KEY)') {
            throw 'inherited secret-like environment is not allowed'
        }
    }
}

function Invoke-WithSafeEnvironment {
    param([scriptblock]$Operation)
    $original = @{}
    foreach ($entry in [Environment]::GetEnvironmentVariables('Process').GetEnumerator()) {
        $original[[string]$entry.Key] = [string]$entry.Value
    }
    $safeNames = @(
        'SystemRoot', 'WINDIR', 'ComSpec', 'TEMP', 'TMP', 'PATHEXT', 'OS',
        'PROCESSOR_ARCHITECTURE', 'PROCESSOR_IDENTIFIER', 'PROCESSOR_LEVEL',
        'PROCESSOR_REVISION', 'NUMBER_OF_PROCESSORS'
    )
    try {
        foreach ($name in @($original.Keys)) {
            [Environment]::SetEnvironmentVariable($name, $null, 'Process')
        }
        foreach ($name in $safeNames) {
            if ($original.ContainsKey($name)) {
                [Environment]::SetEnvironmentVariable($name, $original[$name], 'Process')
            }
        }
        $systemRoot = [string]$original['SystemRoot']
        $safePath = @(
            (Split-Path -Parent $tools.Python),
            (Split-Path -Parent $tools.Git),
            (Join-Path $systemRoot 'System32')
        ) -join [System.IO.Path]::PathSeparator
        [Environment]::SetEnvironmentVariable('PATH', $safePath, 'Process')
        [Environment]::SetEnvironmentVariable('TEST_DATABASE_URL', $original['TEST_DATABASE_URL'], 'Process')
        [Environment]::SetEnvironmentVariable('PYTHONUTF8', '1', 'Process')
        return & $Operation
    }
    finally {
        foreach ($entry in [Environment]::GetEnvironmentVariables('Process').GetEnumerator()) {
            [Environment]::SetEnvironmentVariable([string]$entry.Key, $null, 'Process')
        }
        foreach ($name in $original.Keys) {
            [Environment]::SetEnvironmentVariable($name, $original[$name], 'Process')
        }
    }
}

function Start-SafeProcess {
    param(
        [string]$FilePath,
        [string[]]$ArgumentList,
        [string]$StandardOutput,
        [string]$StandardError
    )
    return Invoke-WithSafeEnvironment {
        Start-Process -FilePath $FilePath -ArgumentList $ArgumentList `
            -WorkingDirectory $root -WindowStyle Hidden `
            -RedirectStandardOutput $StandardOutput -RedirectStandardError $StandardError -PassThru
    }
}

function Get-ProjectListeners {
    return @(
        [System.Net.NetworkInformation.IPGlobalProperties]::GetIPGlobalProperties().GetActiveTcpListeners() |
            Where-Object { $_.Port -in @(9443, 58080) }
    )
}

function Stop-ProcessTree {
    param([int]$ProcessId)
    $ids = [System.Collections.Generic.List[int]]::new()
    try {
        $processes = @(Get-CimInstance Win32_Process -ErrorAction Stop)
        function Add-Children {
            param([int]$ParentId)
            foreach ($process in $processes | Where-Object { $_.ParentProcessId -eq $ParentId }) {
                Add-Children -ParentId ([int]$process.ProcessId)
                $ids.Add([int]$process.ProcessId)
            }
        }
        Add-Children -ParentId $ProcessId
    }
    catch {
        $ids.Clear()
    }
    foreach ($id in $ids) {
        Stop-Process -Id $id -Force -ErrorAction SilentlyContinue
    }
    Stop-Process -Id $ProcessId -Force -ErrorAction SilentlyContinue
}

function Wait-FixtureHealth {
    param(
        [System.Diagnostics.Process]$Process,
        [int]$TimeoutMilliseconds = 10000
    )
    $watch = [System.Diagnostics.Stopwatch]::StartNew()
    while ($watch.ElapsedMilliseconds -lt $TimeoutMilliseconds) {
        $Process.Refresh()
        if ($Process.HasExited) {
            throw 'fixture service exited before health'
        }
        $client = $null
        $writer = $null
        $reader = $null
        try {
            $client = [System.Net.Sockets.TcpClient]::new()
            $connect = $client.ConnectAsync('127.0.0.1', 58080)
            if (-not $connect.Wait(250)) {
                throw 'fixture health connection timed out'
            }
            $stream = $client.GetStream()
            $writer = [System.IO.StreamWriter]::new($stream, [System.Text.Encoding]::ASCII, 1024, $true)
            $writer.NewLine = "`r`n"
            $writer.WriteLine('GET /health HTTP/1.0')
            $writer.WriteLine('Host: 127.0.0.1')
            $writer.WriteLine('Connection: close')
            $writer.WriteLine('')
            $writer.Flush()
            $reader = [System.IO.StreamReader]::new($stream, [System.Text.Encoding]::ASCII)
            $response = $reader.ReadToEnd()
            if ($response -match 'HTTP/1\.[01] 200' -and $response.EndsWith("PETCARE-JETSON-FIXTURE-V1`n")) {
                return
            }
        }
        catch { }
        finally {
            if ($null -ne $reader) { $reader.Dispose() }
            if ($null -ne $writer) { $writer.Dispose() }
            if ($null -ne $client) { $client.Dispose() }
        }
        Start-Sleep -Milliseconds 100
    }
    throw 'fixture service health timed out'
}

function Invoke-ManagedGit {
    param([string[]]$Arguments)
    $result = Invoke-WithSafeEnvironment {
        $output = (& $tools.Git -C $root @Arguments 2>&1 | Out-String).Trim()
        return @{ Output = $output; ExitCode = $LASTEXITCODE }
    }
    if ($result.ExitCode -ne 0) {
        throw 'managed Git command failed'
    }
    return $result.Output
}

$tools = Get-ManagedTools
Assert-SecretEnvironment

$head = Invoke-ManagedGit @('rev-parse', 'HEAD')
if ($head -notmatch '^[0-9a-f]{40}$') {
    throw 'candidate Git SHA is invalid'
}
$tracked = Invoke-ManagedGit @('status', '--short', '--untracked-files=no')
if (-not [string]::IsNullOrWhiteSpace($tracked)) {
    throw 'tracked tree must be clean'
}
if (@(Get-ProjectListeners).Count -gt 0) {
    throw 'unexpected project listener is already active'
}
if (-not (Test-Path -LiteralPath $collectorPath -PathType Leaf)) {
    throw 'Jetson collector is missing'
}

$evidenceHashes = $null
if (-not $Fixture) {
    $candidatePath = Join-Path $evidenceDirectory 'jetson-candidate-sha.txt'
    $bringupPath = Join-Path $evidenceDirectory 'jetson-bringup.json'
    $soakPath = Join-Path $evidenceDirectory 'jetson-vision-node.json'
    $evidencePaths = @($candidatePath, $bringupPath, $soakPath)
    if (@($evidencePaths | Where-Object { -not (Test-Path -LiteralPath $_ -PathType Leaf) }).Count -gt 0) {
        throw 'hardware evidence is missing'
    }
    $previousErrorActionPreference = $ErrorActionPreference
    try {
        $ErrorActionPreference = 'Continue'
        $verification = Invoke-WithSafeEnvironment {
            $output = (& $tools.Python $collectorPath verify-evidence `
                --candidate $candidatePath --bringup $bringupPath --soak $soakPath `
                --expected-candidate-sha $head 2>&1 | Out-String).Trim()
            return @{ Output = $output; ExitCode = $LASTEXITCODE }
        }
        $verificationExitCode = $verification.ExitCode
    }
    finally {
        $ErrorActionPreference = $previousErrorActionPreference
    }
    if ($verificationExitCode -ne 0) {
        throw 'hardware evidence schema/content/SHA validation failed'
    }
    $evidenceHashes = @{}
    foreach ($path in $evidencePaths) {
        $evidenceHashes[$path] = (Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash
    }
}

if ([string]::IsNullOrWhiteSpace($env:TEST_DATABASE_URL)) {
    throw 'Set the dedicated loopback TEST_DATABASE_URL.'
}
if ($env:TEST_DATABASE_URL -notmatch '^postgresql(?:\+psycopg)?://[^@]+@(?:127\.0\.0\.1|localhost):55432/petcare_test(?:\?|$)') {
    throw 'TEST_DATABASE_URL must target the dedicated loopback petcare_test database'
}

$logRoot = Join-Path ([System.IO.Path]::GetTempPath()) ('petcare-jetson-integration-' + [guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $logRoot | Out-Null
$fixtureProcess = $null
$testProcess = $null
$firstError = $null
try {
    if ($Fixture) {
        $fixtureProcess = Start-SafeProcess -FilePath $tools.Python -ArgumentList @(
            $collectorPath, 'fixture-service', '--port', '58080'
        ) -StandardOutput (Join-Path $logRoot 'fixture.out') `
            -StandardError (Join-Path $logRoot 'fixture.err')
        try { $fixtureProcess.PriorityClass = 'BelowNormal' } catch { }
        Wait-FixtureHealth -Process $fixtureProcess
    }

    $testProcess = Start-SafeProcess -FilePath $tools.Python -ArgumentList @(
        '-m', 'pytest', 'backend/tests/integration/test_jetson_vision_stack.py', '-q'
    ) -StandardOutput (Join-Path $logRoot 'pytest.out') `
        -StandardError (Join-Path $logRoot 'pytest.err')
    $null = $testProcess.Handle
    try { $testProcess.PriorityClass = 'BelowNormal' } catch { }
    $watch = [System.Diagnostics.Stopwatch]::StartNew()
    while (-not $testProcess.HasExited -and $watch.Elapsed.TotalSeconds -lt $TimeoutSeconds) {
        Start-Sleep -Milliseconds 250
        $testProcess.Refresh()
    }
    if (-not $testProcess.HasExited) {
        Stop-ProcessTree -ProcessId $testProcess.Id
        throw 'Jetson integration global timeout exceeded'
    }
    if ($testProcess.ExitCode -ne 0) {
        throw 'Jetson integration pytest failed'
    }

    if (-not $Fixture) {
        foreach ($path in $evidenceHashes.Keys) {
            if ((Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash -ne $evidenceHashes[$path]) {
                throw 'hardware evidence changed during integration'
            }
        }
    }
    if ((Invoke-ManagedGit @('rev-parse', 'HEAD')) -ne $head -or
        -not [string]::IsNullOrWhiteSpace((Invoke-ManagedGit @('status', '--short', '--untracked-files=no')))) {
        throw 'candidate changed during integration'
    }
}
catch {
    $firstError = $_
}
finally {
    foreach ($process in @($testProcess, $fixtureProcess)) {
        if ($null -ne $process) {
            try {
                $process.Refresh()
                $wasRunning = -not $process.HasExited
                Stop-ProcessTree -ProcessId $process.Id
                if ($wasRunning) {
                    if (-not $process.WaitForExit(5000)) {
                        throw 'child process cleanup timed out'
                    }
                }
            }
            catch {
                if ($null -eq $firstError) { $firstError = $_ }
            }
        }
    }
    try {
        Remove-Item -LiteralPath $logRoot -Recurse -Force
    }
    catch {
        if ($null -eq $firstError) { $firstError = $_ }
    }
}

if ($null -ne $firstError) {
    throw $firstError
}
Write-Output 'Jetson vision integration PASS'
