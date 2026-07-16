[CmdletBinding()]
param(
  [string]$RuntimePath = '',
  [switch]$CheckOnly
)

$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent $PSScriptRoot
if (-not $RuntimePath) { $RuntimePath = Join-Path $Root '.runtime/toolchain.json' }
$AuthorityPath = Join-Path $PSScriptRoot 'platform-manifest.json'
$Authority = Get-Content -Raw -Encoding UTF8 -LiteralPath $AuthorityPath | ConvertFrom-Json
$Runtime = Get-Content -Raw -Encoding UTF8 -LiteralPath $RuntimePath | ConvertFrom-Json
$ExpectedManifestHash = (Get-FileHash -LiteralPath $AuthorityPath -Algorithm SHA256).Hash
if ($Runtime.manifest_sha256 -ne $ExpectedManifestHash) { throw 'runtime authority hash mismatch' }

$GitPath = $Runtime.paths.git_path
if (-not $GitPath -or -not [IO.Path]::IsPathRooted($GitPath) -or -not (Test-Path -LiteralPath $GitPath)) {
  throw 'invalid manifest Git path'
}
$Pin = $Authority.managed_exact.pico_sdk
$SdkPath = Join-Path $Root ".runtime/managed/pico-sdk-$($Pin.tag)"

if (-not (Test-Path -LiteralPath $SdkPath)) {
  if ($CheckOnly) { throw 'pinned Pico SDK checkout is missing' }
  & $GitPath clone --branch $Pin.tag --depth 1 --recurse-submodules --shallow-submodules $Pin.url $SdkPath
  if ($LASTEXITCODE) { throw 'Pico SDK clone failed' }
} elseif (-not $CheckOnly) {
  & $GitPath -C $SdkPath submodule update --init --recursive --depth 1
  if ($LASTEXITCODE) { throw 'Pico SDK submodule update failed' }
}

$Origin = (& $GitPath -C $SdkPath remote get-url origin).Trim()
$Commit = (& $GitPath -C $SdkPath rev-parse HEAD).Trim()
$Tag = (& $GitPath -C $SdkPath describe --tags --exact-match).Trim()
if ($LASTEXITCODE -or $Origin -ne $Pin.url -or $Tag -ne $Pin.tag -or $Commit -ne $Pin.commit) {
  throw 'Pico SDK origin/tag/commit mismatch'
}
$SubmoduleState = & $GitPath -C $SdkPath submodule status --recursive
if ($LASTEXITCODE -or $SubmoduleState | Where-Object { $_ -match '^[\-+U]' }) {
  throw 'Pico SDK submodule state mismatch'
}
$Dirty = & $GitPath -C $SdkPath status --porcelain=v1 --untracked-files=all --ignore-submodules=all
if ($LASTEXITCODE -or $Dirty) { throw 'Pico SDK working tree is dirty' }
foreach ($Line in $SubmoduleState) {
  $Parts = $Line.Trim().Split([char[]]@(' ', "`t"), [StringSplitOptions]::RemoveEmptyEntries)
  $SubmodulePath = Join-Path $SdkPath $Parts[1]
  $Dirty = & $GitPath -C $SubmodulePath status --porcelain=v1 --untracked-files=all --ignore-submodules=all
  if ($LASTEXITCODE -or $Dirty) { throw "Pico SDK submodule is dirty: $($Parts[1])" }
}

$Identity = [ordered]@{
  path = [IO.Path]::GetFullPath($SdkPath)
  origin = [string]$Pin.url
  tag = [string]$Pin.tag
  commit = [string]$Pin.commit
}
$Runtime | Add-Member -NotePropertyName pico_sdk -NotePropertyValue $Identity -Force
[IO.File]::WriteAllText(
  [IO.Path]::GetFullPath($RuntimePath),
  ($Runtime | ConvertTo-Json -Depth 12),
  [Text.UTF8Encoding]::new($false)
)
Write-Output "Pico SDK $($Pin.tag) identity PASS: $SdkPath"
