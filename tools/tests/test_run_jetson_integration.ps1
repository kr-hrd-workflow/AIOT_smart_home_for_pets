$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$sourceRoot = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
$sourceToolchain = Get-Content -LiteralPath (Join-Path $sourceRoot '.runtime\toolchain.json') -Raw -Encoding UTF8 | ConvertFrom-Json
$gitPath = [string]$sourceToolchain.paths.git_path
$powershellPath = Join-Path $PSHOME 'powershell.exe'
if (-not (Test-Path -LiteralPath $gitPath -PathType Leaf) -or
    -not (Test-Path -LiteralPath $powershellPath -PathType Leaf)) {
    throw 'runner test prerequisites are missing'
}

$savedSecretEnvironment = @{}
foreach ($entry in [Environment]::GetEnvironmentVariables('Process').GetEnumerator()) {
    $name = [string]$entry.Key
    if ($name -ne 'TEST_DATABASE_URL' -and (
        $name -match '(?i)(PASSWORD|TOKEN|SECRET|CREDENTIAL|PRIVATE_KEY|SERVICE_ROLE|ACCESS_KEY)' -or
        $name -match '(?i)^(DATABASE_URL|PETCARE_AGENT_CONFIG|PETCARE_AGENT_TOOLS|PETCARE_JETSON_CONFIG)$' -or
        $name -match '(?i)((CONFIG|CREDENTIAL).*(PATH|FILE)|(PATH|FILE).*(CONFIG|CREDENTIAL))'
    )) {
        $savedSecretEnvironment[$name] = [string]$entry.Value
        [Environment]::SetEnvironmentVariable($name, $null, 'Process')
    }
}

function Invoke-Git {
    param([string]$Root, [string[]]$Arguments)
    & $gitPath -C $Root @Arguments | Out-Null
    if ($LASTEXITCODE -ne 0) { throw 'test Git command failed' }
}

function New-FixtureRepository {
    param([string]$TestSource = "def test_fixture():`n    assert True`n")
    $root = Join-Path ([System.IO.Path]::GetTempPath()) ('petcare-jetson-runner-' + [guid]::NewGuid().ToString('N'))
    New-Item -ItemType Directory -Path `
        (Join-Path $root 'tools'), `
        (Join-Path $root 'backend\tests\integration'), `
        (Join-Path $root '.runtime') -Force | Out-Null
    Copy-Item -LiteralPath (Join-Path $sourceRoot 'tools\run_jetson_integration.ps1') `
        -Destination (Join-Path $root 'tools\run_jetson_integration.ps1')
    Copy-Item -LiteralPath (Join-Path $sourceRoot 'tools\jetson_vision_soak.py') `
        -Destination (Join-Path $root 'tools\jetson_vision_soak.py')
    Copy-Item -LiteralPath (Join-Path $sourceRoot 'tools\platform-manifest.json') `
        -Destination (Join-Path $root 'tools\platform-manifest.json')
    Copy-Item -LiteralPath (Join-Path $sourceRoot '.runtime\toolchain.json') `
        -Destination (Join-Path $root '.runtime\toolchain.json')
    Set-Content -LiteralPath (Join-Path $root 'backend\tests\integration\test_jetson_vision_stack.py') `
        -Value $TestSource -Encoding UTF8
    Set-Content -LiteralPath (Join-Path $root '.gitignore') -Value ".runtime/evidence/`n" -Encoding UTF8
    Invoke-Git $root @('init', '-q')
    Invoke-Git $root @('config', 'user.email', 'fixture@example.invalid')
    Invoke-Git $root @('config', 'user.name', 'PetCare Fixture')
    Invoke-Git $root @('add', '.gitignore', 'tools', 'backend')
    Invoke-Git $root @('commit', '-q', '-m', 'fixture')
    return $root
}

function Invoke-Runner {
    param(
        [string]$Root,
        [string[]]$Arguments,
        [hashtable]$Environment = @{}
    )
    $runner = Join-Path $Root 'tools\run_jetson_integration.ps1'
    $values = @{
        PATH = [Environment]::GetEnvironmentVariable('PATH', 'Process')
        TEST_DATABASE_URL = [Environment]::GetEnvironmentVariable('TEST_DATABASE_URL', 'Process')
    }
    foreach ($name in $Environment.Keys) {
        $values[$name] = [Environment]::GetEnvironmentVariable($name, 'Process')
    }
    try {
        [Environment]::SetEnvironmentVariable('PATH', (Join-Path $env:SystemRoot 'System32'), 'Process')
        [Environment]::SetEnvironmentVariable(
            'TEST_DATABASE_URL',
            'postgresql+psycopg://petcare:fixture@127.0.0.1:55432/petcare_test',
            'Process'
        )
        foreach ($name in $Environment.Keys) {
            [Environment]::SetEnvironmentVariable($name, [string]$Environment[$name], 'Process')
        }
        $previous = $ErrorActionPreference
        try {
            $ErrorActionPreference = 'Continue'
            $output = & $powershellPath -NoProfile -ExecutionPolicy Bypass -File $runner @Arguments 2>&1 | Out-String
            $code = $LASTEXITCODE
        }
        finally {
            $ErrorActionPreference = $previous
        }
        return @{ Code = $code; Output = $output }
    }
    finally {
        foreach ($name in $values.Keys) {
            [Environment]::SetEnvironmentVariable($name, $values[$name], 'Process')
        }
    }
}

function Test-PortClosed {
    param([int]$Port)
    for ($attempt = 0; $attempt -lt 20; $attempt++) {
        $client = [System.Net.Sockets.TcpClient]::new()
        try {
            $task = $client.ConnectAsync('127.0.0.1', $Port)
            if ($task.Wait(100) -and $client.Connected) {
                Start-Sleep -Milliseconds 100
                continue
            }
            return $true
        }
        catch {
            return $true
        }
        finally {
            $client.Dispose()
        }
    }
    return $false
}

$roots = [System.Collections.Generic.List[string]]::new()
try {
    $root = New-FixtureRepository
    $roots.Add($root)
    $fixture = Invoke-Runner $root @('-Fixture', '-TimeoutSeconds', '60')
    if ($fixture.Code -ne 0 -or -not $fixture.Output.Contains('Jetson vision integration PASS')) {
        throw "fixture runner failed through manifest tools: $($fixture.Output)"
    }
    if (-not (Test-PortClosed 58080)) { throw 'fixture service survived successful runner cleanup' }

    $secretText = 'do-not-print-this-value'
    $production = Invoke-Runner $root @('-Fixture') @{ CLOUDFLARE_API_TOKEN = $secretText }
    if ($production.Code -eq 0 -or -not $production.Output.Contains('inherited secret-like environment is not allowed')) {
        throw 'runner accepted an inherited production credential'
    }
    if ($production.Output.Contains($secretText)) { throw 'runner printed a production credential value' }

    foreach ($name in @('DATABASE_URL', 'PETCARE_AGENT_CONFIG', 'PETCARE_AGENT_TOOLS', 'PETCARE_JETSON_CONFIG', 'PRODUCTION_CONFIG_PATH')) {
        $rejected = Invoke-Runner $root @('-Fixture') @{ $name = 'C:\production\credential.json' }
        if ($rejected.Code -eq 0 -or -not $rejected.Output.Contains('inherited production configuration is not allowed')) {
            throw "runner accepted production configuration environment: $name"
        }
        if ($rejected.Output.Contains('C:\production\credential.json')) {
            throw 'runner printed a production configuration path'
        }
    }

    $allowlistRoot = New-FixtureRepository @'
import os

def test_child_receives_only_safe_runner_environment():
    assert os.environ['TEST_DATABASE_URL'].endswith('/petcare_test')
    assert 'UNRELATED_PARENT_MARKER' not in os.environ
    assert 'DATABASE_URL' not in os.environ
    assert 'PETCARE_AGENT_CONFIG' not in os.environ
    assert 'PETCARE_AGENT_TOOLS' not in os.environ
    assert 'PETCARE_JETSON_CONFIG' not in os.environ
'@
    $roots.Add($allowlistRoot)
    $allowlisted = Invoke-Runner $allowlistRoot @('-Fixture') @{ UNRELATED_PARENT_MARKER = 'not-for-child' }
    if ($allowlisted.Code -ne 0 -or -not $allowlisted.Output.Contains('Jetson vision integration PASS')) {
        throw 'runner did not pass a safe allowlist environment to pytest'
    }

    Set-Content -LiteralPath (Join-Path $root 'backend\tests\integration\test_jetson_vision_stack.py') `
        -Value "def test_fixture():`n    assert False`n" -Encoding UTF8
    $dirty = Invoke-Runner $root @('-Fixture') @{
        PETCARE_RUNNER_TESTING = '1'
        PETCARE_RUNNER_TEST_GIT_STATUS = ' '
        PETCARE_RUNNER_TEST_LISTENERS = ','
        PETCARE_RUNNER_TEST_HEAD = ('0' * 40)
    }
    if ($dirty.Code -eq 0 -or -not $dirty.Output.Contains('tracked tree must be clean')) {
        throw 'legacy runner override bypassed dirty-state enforcement'
    }

    $listenerRoot = New-FixtureRepository
    $roots.Add($listenerRoot)
    $listener = [System.Net.Sockets.TcpListener]::new([System.Net.IPAddress]::Loopback, 58080)
    try {
        $listener.Start()
        $occupied = Invoke-Runner $listenerRoot @('-Fixture')
        if ($occupied.Code -eq 0 -or -not $occupied.Output.Contains('unexpected project listener')) {
            throw 'runner accepted an unexpected project listener'
        }
    }
    finally {
        $listener.Stop()
    }

    $evidenceRoot = New-FixtureRepository
    $roots.Add($evidenceRoot)
    $missing = Invoke-Runner $evidenceRoot @()
    if ($missing.Code -eq 0 -or -not $missing.Output.Contains('hardware evidence is missing')) {
        throw 'runner accepted missing hardware evidence'
    }
    $evidence = Join-Path $evidenceRoot '.runtime\evidence'
    New-Item -ItemType Directory -Path $evidence -Force | Out-Null
    $head = (& $gitPath -C $evidenceRoot rev-parse HEAD | Out-String).Trim()
    Set-Content -LiteralPath (Join-Path $evidence 'jetson-candidate-sha.txt') -Value $head -NoNewline
    @{ candidate_sha = $head; status = 'PASS' } | ConvertTo-Json -Compress |
        Set-Content -LiteralPath (Join-Path $evidence 'jetson-bringup.json') -Encoding UTF8
    @{ candidate_sha = $head; status = 'PASS' } | ConvertTo-Json -Compress |
        Set-Content -LiteralPath (Join-Path $evidence 'jetson-vision-node.json') -Encoding UTF8
    $fake = Invoke-Runner $evidenceRoot @()
    if ($fake.Code -eq 0 -or -not $fake.Output.Contains('hardware evidence schema/content/SHA validation failed')) {
        throw 'runner accepted status-only fake hardware evidence'
    }

    $healthRoot = New-FixtureRepository
    $roots.Add($healthRoot)
    Set-Content -LiteralPath (Join-Path $healthRoot 'tools\jetson_vision_soak.py') `
        -Value "raise SystemExit(3)`n" -Encoding UTF8
    Invoke-Git $healthRoot @('add', 'tools/jetson_vision_soak.py')
    Invoke-Git $healthRoot @('commit', '-q', '-m', 'break health fixture')
    $health = Invoke-Runner $healthRoot @('-Fixture', '-TimeoutSeconds', '10')
    if ($health.Code -eq 0 -or -not $health.Output.Contains('fixture service exited before health')) {
        throw 'runner accepted a fixture service without authoritative health'
    }

    $timeoutSource = @'
import subprocess
import sys
import time

def test_global_timeout_process_tree():
    child = """import socket,time
from pathlib import Path
s=socket.socket()
s.setsockopt(socket.SOL_SOCKET,socket.SO_REUSEADDR,1)
s.bind(('127.0.0.1',58081))
s.listen(1)
Path('.runtime/descendant-ready').write_text('ready',encoding='utf-8')
time.sleep(60)
"""
    subprocess.Popen([sys.executable, '-c', child])
    time.sleep(60)
'@
    $timeoutRoot = New-FixtureRepository $timeoutSource
    $roots.Add($timeoutRoot)
    $timeout = Invoke-Runner $timeoutRoot @('-Fixture', '-TimeoutSeconds', '5')
    if ($timeout.Code -eq 0 -or -not $timeout.Output.Contains('Jetson integration global timeout exceeded')) {
        throw 'runner did not enforce its global timeout'
    }
    if (-not (Test-Path -LiteralPath (Join-Path $timeoutRoot '.runtime\descendant-ready') -PathType Leaf)) {
        throw 'timeout fixture descendant never started'
    }
    if (-not (Test-PortClosed 58080) -or -not (Test-PortClosed 58081)) {
        throw 'runner left a fixture or descendant process listening after timeout'
    }

    $exitedParentSource = @'
import subprocess
import sys
import time

def test_parent_exits_while_descendant_is_still_running():
    child = """import socket,time
from pathlib import Path
s=socket.socket()
s.setsockopt(socket.SOL_SOCKET,socket.SO_REUSEADDR,1)
s.bind(('127.0.0.1',58082))
s.listen(1)
Path('.runtime/exited-parent-descendant-ready').write_text('ready',encoding='utf-8')
time.sleep(60)
"""
    subprocess.Popen(
        [sys.executable, '-c', child],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        close_fds=True,
    )
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        if __import__('pathlib').Path('.runtime/exited-parent-descendant-ready').is_file():
            return
        time.sleep(0.05)
    raise AssertionError('descendant did not start')
'@
    $exitedParentRoot = New-FixtureRepository $exitedParentSource
    $roots.Add($exitedParentRoot)
    $exitedParent = Invoke-Runner $exitedParentRoot @('-Fixture', '-TimeoutSeconds', '30')
    if ($exitedParent.Code -ne 0 -or -not $exitedParent.Output.Contains('Jetson vision integration PASS')) {
        throw 'runner failed when pytest exited before its descendant'
    }
    if (-not (Test-Path -LiteralPath (Join-Path $exitedParentRoot '.runtime\exited-parent-descendant-ready') -PathType Leaf)) {
        throw 'exited-parent descendant never started'
    }
    if (-not (Test-PortClosed 58082)) {
        throw 'runner left a descendant alive after pytest parent exited'
    }
}
finally {
    foreach ($root in $roots) {
        if (Test-Path -LiteralPath $root) {
            Remove-Item -LiteralPath $root -Recurse -Force
        }
    }
    foreach ($name in $savedSecretEnvironment.Keys) {
        [Environment]::SetEnvironmentVariable($name, $savedSecretEnvironment[$name], 'Process')
    }
}

Write-Output 'Jetson integration runner tests PASS'
