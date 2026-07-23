[CmdletBinding()]
param(
    [switch]$CheckOnly,
    [string]$FixtureRoot = '',
    [string]$OutputPath = '',
    [ValidateSet('none', 'wrong-byte')]
    [string]$Mutation = 'none'
)

$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent $PSScriptRoot
$ManifestPath = Join-Path $PSScriptRoot 'platform-manifest.json'
$RuntimeRoot = Join-Path $Root '.runtime'
$Manifest = Get-Content -Raw -Encoding UTF8 -LiteralPath $ManifestPath | ConvertFrom-Json
$ManifestHash = (Get-FileHash -LiteralPath $ManifestPath -Algorithm SHA256).Hash
if (-not $OutputPath) { $OutputPath = Join-Path $RuntimeRoot 'agent-tools.json' }
$OutputPath = [IO.Path]::GetFullPath($OutputPath)
$ToolNames = @('cloudflared_path', 'ffmpeg_path', 'ffprobe_path', 'python_path', 'uv_path')

function Write-Json([object]$Value, [string]$Path) {
    $parent = Split-Path -Parent $Path
    New-Item -ItemType Directory -Force -Path $parent | Out-Null
    $temporary = Join-Path $parent ('.' + [IO.Path]::GetFileName($Path) + '.' + [guid]::NewGuid().ToString('N') + '.new')
    $backup = Join-Path $parent ('.' + [IO.Path]::GetFileName($Path) + '.' + [guid]::NewGuid().ToString('N') + '.bak')
    try {
        [IO.File]::WriteAllText(
            $temporary,
            ($Value | ConvertTo-Json -Depth 8),
            [Text.UTF8Encoding]::new($false)
        )
        if (Test-Path -LiteralPath $Path) {
            [IO.File]::Replace($temporary, $Path, $backup)
        }
        else {
            [IO.File]::Move($temporary, $Path)
        }
    }
    finally {
        if (Test-Path -LiteralPath $temporary) { Remove-Item -LiteralPath $temporary -Force }
        if (Test-Path -LiteralPath $backup) { Remove-Item -LiteralPath $backup -Force }
    }
}

function Get-ExpectedVersions {
    $managed = $Manifest.managed_exact
    return [ordered]@{
        cloudflared_path = [string]$managed.cloudflared.version
        ffmpeg_path = [string]$managed.ffmpeg.version
        ffprobe_path = [string]$managed.ffmpeg.version
        python_path = "$($managed.python.version)+$($managed.python.build)"
        uv_path = [string]$managed.uv.version
    }
}

function New-Manifest([Collections.IDictionary]$Paths, [bool]$Fixture) {
    $versions = Get-ExpectedVersions
    $hashes = [ordered]@{}
    foreach ($name in $ToolNames) {
        $path = [string]$Paths[$name]
        if (-not [IO.Path]::IsPathRooted($path) -or -not (Test-Path -LiteralPath $path -PathType Leaf)) {
            throw "agent runtime executable is missing: $name"
        }
        $hashes[$name] = (Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash
    }
    return [ordered]@{
        schema_version = 1
        manifest_sha256 = $ManifestHash
        platform = 'windows-x64'
        architecture = 'x64'
        fixture = $Fixture
        paths = $Paths
        executable_sha256 = $hashes
        versions = $versions
    }
}

function New-Fixture([string]$Path) {
    $Path = [IO.Path]::GetFullPath($Path)
    $bin = Join-Path $Path 'bin'
    New-Item -ItemType Directory -Force -Path $bin | Out-Null
    $sealed = Join-Path $Path 'sealed.fixture'
    [IO.File]::WriteAllText($sealed, 'sealed agent fixture', [Text.UTF8Encoding]::new($false))
    $expected = (Get-FileHash -LiteralPath $sealed -Algorithm SHA256).Hash
    if ($Mutation -eq 'wrong-byte') { [IO.File]::AppendAllText($sealed, 'x') }
    if ((Get-FileHash -LiteralPath $sealed -Algorithm SHA256).Hash -cne $expected) {
        throw 'managed artifact SHA-256 mismatch'
    }
    $commands = [ordered]@{
        cloudflared_path = 'cloudflared version 2026.7.2'
        ffmpeg_path = 'ffmpeg version n8.1.2-22-g94138f6973'
        ffprobe_path = 'ffprobe version n8.1.2-22-g94138f6973'
        python_path = 'Python 3.12.13'
        uv_path = 'uv 0.11.28'
    }
    $paths = [ordered]@{}
    foreach ($name in $ToolNames) {
        $path = Join-Path $bin ($name + '.cmd')
        [IO.File]::WriteAllText(
            $path,
            "@echo off`r`necho $($commands[$name])`r`n",
            [Text.ASCIIEncoding]::new()
        )
        $paths[$name] = [IO.Path]::GetFullPath($path)
    }
    return $paths
}

function Get-VerifiedArtifact([string]$Name, [object]$Artifact, [string]$Extension) {
    $cache = Join-Path $RuntimeRoot 'bootstrap-cache'
    New-Item -ItemType Directory -Force -Path $cache | Out-Null
    $path = Join-Path $cache ("agent-$Name$Extension")
    if (-not (Test-Path -LiteralPath $path -PathType Leaf)) {
        if ($CheckOnly) { throw "missing managed artifact in CheckOnly mode: $Name" }
        Invoke-WebRequest -Uri $Artifact.url -OutFile $path -UseBasicParsing
    }
    if ((Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash -cne [string]$Artifact.sha256) {
        throw "managed artifact SHA-256 mismatch: $Name"
    }
    return $path
}

function Find-One([string]$Path, [string]$Name) {
    $matches = @(Get-ChildItem -LiteralPath $Path -Recurse -File -Filter $Name)
    if ($matches.Count -ne 1) { throw "managed executable count is invalid: $Name" }
    return $matches[0].FullName
}

function Expand-FfmpegExecutables([string]$Archive, [string]$Destination) {
    Add-Type -AssemblyName System.IO.Compression.FileSystem
    $zip = [IO.Compression.ZipFile]::OpenRead($Archive)
    try {
        foreach ($name in @('ffmpeg.exe', 'ffprobe.exe')) {
            $entries = @($zip.Entries | Where-Object { [IO.Path]::GetFileName($_.FullName) -ceq $name })
            if ($entries.Count -ne 1) { throw "managed executable count is invalid: $name" }
            [IO.Compression.ZipFileExtensions]::ExtractToFile(
                $entries[0],
                (Join-Path $Destination $name),
                $false
            )
        }
    }
    finally {
        $zip.Dispose()
    }
}

function Assert-Output([string]$Label, [string[]]$Arguments, [string]$Pattern) {
    $path = $Arguments[0]
    $output = & $path @($Arguments[1..($Arguments.Count - 1)]) 2>&1
    if ($LASTEXITCODE -ne 0 -or ($output -join "`n") -notmatch $Pattern) {
        throw "managed executable version mismatch: $Label"
    }
}

function Set-OwnerOnlyAcl([string]$Path) {
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent().User.Value
    $allowed = @($identity, 'S-1-5-18')
    $acl = Get-Acl -LiteralPath $Path
    $actual = @(
        $acl.Access | ForEach-Object {
            $_.IdentityReference.Translate([Security.Principal.SecurityIdentifier]).Value
        } | Sort-Object -Unique
    )
    if (
        $acl.AreAccessRulesProtected -and
        $acl.Access.Count -eq 2 -and
        @($acl.Access | Where-Object {
            $_.AccessControlType -ne [Security.AccessControl.AccessControlType]::Allow -or $_.IsInherited
        }).Count -eq 0 -and
        (Compare-Object ($allowed | Sort-Object) $actual).Count -eq 0
    ) {
        return
    }
    $isDirectory = Test-Path -LiteralPath $Path -PathType Container
    $suffix = if ($isDirectory) { '(OI)(CI)F' } else { 'F' }
    & "$env:SystemRoot\System32\icacls.exe" $Path '/inheritance:r' '/grant:r' "*${identity}:$suffix" "*S-1-5-18:$suffix" *> $null
    if ($LASTEXITCODE) { throw "runtime ACL protection failed: $Path" }
}

function Protect-Chain([string]$Path, [string]$Boundary) {
    $current = [IO.Path]::GetFullPath($Path)
    $Boundary = [IO.Path]::GetFullPath($Boundary)
    while ($true) {
        if ($current -ne $Boundary -and -not $current.StartsWith($Boundary + [IO.Path]::DirectorySeparatorChar, [StringComparison]::OrdinalIgnoreCase)) {
            throw 'agent tool path is outside runtime root'
        }
        Set-OwnerOnlyAcl $current
        if ($current -eq $Boundary) { return }
        $current = Split-Path -Parent $current
    }
}

if ($FixtureRoot) {
    $fixturePaths = New-Fixture $FixtureRoot
    Write-Json (New-Manifest $fixturePaths $true) $OutputPath
    Write-Output "Agent runtime Windows bootstrap fixture PASS: $OutputPath"
    exit 0
}

$runtimePath = [IO.Path]::GetFullPath($RuntimeRoot)
if ((Split-Path -Parent $OutputPath) -cne $runtimePath) {
    throw 'real agent tools manifest must be written directly under .runtime'
}

$managed = $Manifest.managed_exact
$toolchainPath = Join-Path $RuntimeRoot 'toolchain.json'
if (-not (Test-Path -LiteralPath $toolchainPath -PathType Leaf)) {
    throw 'run bootstrap_toolchain.ps1 first'
}
$toolchain = Get-Content -Raw -Encoding UTF8 -LiteralPath $toolchainPath | ConvertFrom-Json
if (
    [bool]$toolchain.fixture -or
    [string]$toolchain.manifest_sha256 -cne $ManifestHash -or
    [string]$toolchain.versions.python_path -cne "$($managed.python.version)+$($managed.python.build)" -or
    [string]$toolchain.versions.uv_path -cne [string]$managed.uv.version
) {
    throw 'toolchain runtime authority mismatch'
}

$ffmpegArchive = Get-VerifiedArtifact 'ffmpeg' $managed.ffmpeg.windows_x64 '.zip'
$cloudflaredArchive = Get-VerifiedArtifact 'cloudflared' $managed.cloudflared.windows_x64 '.exe'
$agentRoot = Join-Path $RuntimeRoot 'managed/agent'
$ffmpegRoot = Join-Path $agentRoot ("ffmpeg-" + [string]$managed.ffmpeg.version)
$cloudflaredRoot = Join-Path $agentRoot ("cloudflared-" + [string]$managed.cloudflared.version)

if (-not (Test-Path -LiteralPath $ffmpegRoot -PathType Container)) {
    if ($CheckOnly) { throw 'managed FFmpeg is not extracted' }
    $temporary = "$ffmpegRoot.new"
    if (Test-Path -LiteralPath $temporary) { throw 'incomplete FFmpeg extraction already exists' }
    New-Item -ItemType Directory -Force -Path $temporary | Out-Null
    Expand-FfmpegExecutables $ffmpegArchive $temporary
    Move-Item -LiteralPath $temporary -Destination $ffmpegRoot
}
New-Item -ItemType Directory -Force -Path $cloudflaredRoot | Out-Null
$cloudflaredPath = Join-Path $cloudflaredRoot 'cloudflared.exe'
if (-not (Test-Path -LiteralPath $cloudflaredPath -PathType Leaf)) {
    if ($CheckOnly) { throw 'managed cloudflared is not extracted' }
    New-Item -ItemType HardLink -Path $cloudflaredPath -Target $cloudflaredArchive | Out-Null
}
if ((Get-FileHash -LiteralPath $cloudflaredPath -Algorithm SHA256).Hash -cne [string]$managed.cloudflared.windows_x64.sha256) {
    throw 'managed cloudflared executable SHA-256 mismatch'
}

$paths = [ordered]@{
    cloudflared_path = [IO.Path]::GetFullPath($cloudflaredPath)
    ffmpeg_path = [IO.Path]::GetFullPath((Find-One $ffmpegRoot 'ffmpeg.exe'))
    ffprobe_path = [IO.Path]::GetFullPath((Find-One $ffmpegRoot 'ffprobe.exe'))
    python_path = [IO.Path]::GetFullPath([string]$toolchain.paths.python_path)
    uv_path = [IO.Path]::GetFullPath([string]$toolchain.paths.uv_path)
}

Assert-Output 'cloudflared' @($paths.cloudflared_path, '--version') ([regex]::Escape([string]$managed.cloudflared.version))
Assert-Output 'ffmpeg' @($paths.ffmpeg_path, '-version') ([regex]::Escape([string]$managed.ffmpeg.version))
Assert-Output 'ffprobe' @($paths.ffprobe_path, '-version') ([regex]::Escape([string]$managed.ffmpeg.version))
Assert-Output 'python' @($paths.python_path, '--version') ('Python ' + [regex]::Escape([string]$managed.python.version))
Assert-Output 'uv' @($paths.uv_path, '--version') ('uv ' + [regex]::Escape([string]$managed.uv.version))

$payload = New-Manifest $paths $false
Write-Json $payload $OutputPath
foreach ($path in @($paths.Values) + @($OutputPath)) {
    Protect-Chain ([string]$path) $runtimePath
}
Write-Output 'agent runtime PASS: windows-x64'
