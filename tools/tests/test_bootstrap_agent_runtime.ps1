$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$bootstrap = Join-Path $root 'tools/bootstrap_agent_runtime.ps1'
$testRoot = Join-Path $root '.runtime/tests/agent-runtime'
$fixtureRoot = Join-Path $testRoot 'fixture'
$output = Join-Path $testRoot 'agent-tools.json'

if (-not (Test-Path -LiteralPath $bootstrap -PathType Leaf)) {
    throw 'ASSERT: bootstrap_agent_runtime.ps1 is missing'
}

$parameters = (Get-Command -Name $bootstrap).Parameters
foreach ($name in @('CheckOnly', 'FixtureRoot', 'OutputPath', 'Mutation')) {
    if (-not $parameters.ContainsKey($name)) {
        throw "ASSERT: bootstrap missing parameter $name"
    }
}

& $bootstrap -FixtureRoot $fixtureRoot -OutputPath $output
if ($LASTEXITCODE) { throw 'agent runtime fixture bootstrap failed' }

$data = Get-Content -Raw -Encoding UTF8 -LiteralPath $output | ConvertFrom-Json
$names = @('cloudflared_path', 'ffmpeg_path', 'ffprobe_path', 'python_path', 'uv_path')
$expectedVersions = @{
    cloudflared_path = '2026.7.2'
    ffmpeg_path = '8.1.2-22-g94138f6973'
    ffprobe_path = '8.1.2-22-g94138f6973'
    python_path = '3.12.13+20260623'
    uv_path = '0.11.28'
}
$manifest = Join-Path $root 'tools/platform-manifest.json'

if ($data.schema_version -ne 1 -or -not [bool]$data.fixture) { throw 'invalid fixture marker' }
if ($data.platform -ne 'windows-x64' -or $data.architecture -ne 'x64') { throw 'invalid fixture platform' }
if ($data.manifest_sha256 -cne (Get-FileHash -LiteralPath $manifest -Algorithm SHA256).Hash) {
    throw 'manifest authority mismatch'
}
foreach ($section in @('paths', 'executable_sha256', 'versions')) {
    $actual = @($data.$section.PSObject.Properties.Name | Sort-Object)
    if ((Compare-Object ($names | Sort-Object) $actual).Count) {
        throw "invalid agent tools section: $section"
    }
}
foreach ($name in $names) {
    $path = [string]$data.paths.$name
    $hash = [string]$data.executable_sha256.$name
    if (-not [IO.Path]::IsPathRooted($path) -or -not (Test-Path -LiteralPath $path -PathType Leaf)) {
        throw "invalid fixture path: $name"
    }
    if ($hash -notmatch '^[0-9A-F]{64}$' -or $hash -cne (Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash) {
        throw "invalid fixture hash: $name"
    }
    if ([string]$data.versions.$name -cne $expectedVersions[$name]) {
        throw "invalid fixture version: $name"
    }
}

$child = Start-Process -FilePath (Get-Process -Id $PID).Path -Wait -PassThru -WindowStyle Hidden -ArgumentList @(
    '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', $bootstrap,
    '-FixtureRoot', (Join-Path $testRoot 'wrong-byte'),
    '-OutputPath', $output,
    '-Mutation', 'wrong-byte'
)
if ($child.ExitCode -eq 0) { throw 'wrong fixture bytes were accepted' }

$source = Get-Content -Raw -LiteralPath $bootstrap
if ($source -match 'https?://' -or $source -match '[A-Fa-f0-9]{64}') {
    throw 'bootstrap duplicates manifest URL or SHA authority'
}
$checkAll = Get-Content -Raw -LiteralPath (Join-Path $root 'tools/check_all.ps1')
if ($checkAll -notmatch 'test_bootstrap_agent_runtime\.ps1') {
    throw 'bootstrap fixture is missing from the complete local check'
}

Write-Output 'Agent runtime Windows bootstrap fixture PASS'
