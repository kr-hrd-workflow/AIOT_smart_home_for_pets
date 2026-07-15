[CmdletBinding()]
param(
    [string]$SourcePath,
    [string]$ModelDirectory,
    [string]$ManifestPath
)

$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent $PSScriptRoot
$ManifestPath = if ($ManifestPath) { $ManifestPath } else { Join-Path $PSScriptRoot 'platform-manifest.json' }
$ModelRoot = [IO.Path]::GetFullPath((Join-Path $Root '.runtime\models'))
$Destination = if ($ModelDirectory) { [IO.Path]::GetFullPath($ModelDirectory) } else { $ModelRoot }

if (-not ($Destination.Equals($ModelRoot, [StringComparison]::OrdinalIgnoreCase) -or
          $Destination.StartsWith($ModelRoot + [IO.Path]::DirectorySeparatorChar, [StringComparison]::OrdinalIgnoreCase))) {
    throw 'ModelDirectory must be under .runtime/models'
}

$pin = (Get-Content -LiteralPath $ManifestPath -Raw | ConvertFrom-Json).managed_exact.model
if ($pin.package -ne 'ultralytics' -or
    $pin.version -ne '8.3.0' -or
    $pin.file -ne 'yolo11n.pt' -or
    $pin.url -ne 'https://github.com/ultralytics/assets/releases/download/v8.3.0/yolo11n.pt' -or
    [int64]$pin.bytes -ne 5613764 -or
    $pin.sha256 -ne '0EBBC80D4A7680D14987A577CD21342B65ECFD94632BD9A8DA63AE6417644EE1') {
    throw 'Vision model pin mismatch in tools/platform-manifest.json'
}

function Assert-ModelArtifact([string]$Path) {
    if ((Get-Item -LiteralPath $Path).Length -ne [int64]$pin.bytes) {
        throw 'Vision model size mismatch'
    }
    if ((Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash -ne $pin.sha256) {
        throw 'Vision model hash mismatch'
    }
}

New-Item -ItemType Directory -Path $Destination -Force | Out-Null
$Target = Join-Path $Destination $pin.file
$Partial = "$Target.partial"

if (Test-Path -LiteralPath $Target) {
    Assert-ModelArtifact $Target
    Write-Output $Target
    exit 0
}

try {
    Remove-Item -LiteralPath $Partial -Force -ErrorAction SilentlyContinue
    if ($SourcePath) {
        Copy-Item -LiteralPath $SourcePath -Destination $Partial
    } else {
        Invoke-WebRequest -Uri $pin.url -OutFile $Partial
    }
    Assert-ModelArtifact $Partial
    Move-Item -LiteralPath $Partial -Destination $Target
    Write-Output $Target
} finally {
    Remove-Item -LiteralPath $Partial -Force -ErrorAction SilentlyContinue
}
