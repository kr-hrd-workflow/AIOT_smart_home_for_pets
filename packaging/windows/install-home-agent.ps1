param(
    [Parameter(Mandatory = $true)]
    [ValidateSet('Install', 'Uninstall', 'Status', 'Fixture')]
    [string]$Action,
    [string]$ConfigPath,
    [string]$ToolsPath,
    [string]$JetsonConfigPath,
    [string]$PairingBundle,
    [string]$FixturePath
)

$ErrorActionPreference = 'Stop'
$RegistryPath = 'HKLM:\Software\PetCare\HomeAgent'

function Assert-AbsoluteFile([string]$Path, [string]$Label) {
    if ([string]::IsNullOrWhiteSpace($Path) -or -not [System.IO.Path]::IsPathRooted($Path)) {
        throw "$Label must be an absolute path"
    }
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
        throw "$Label does not exist"
    }
}

function Assert-OwnerOnlyAcl([string]$Path) {
    $allowed = @(
        [System.Security.Principal.WindowsIdentity]::GetCurrent().User.Value,
        'S-1-5-18'
    )
    $acl = Get-Acl -LiteralPath $Path
    foreach ($rule in $acl.Access) {
        if ($rule.AccessControlType -eq 'Allow' -and $allowed -notcontains $rule.IdentityReference.Translate([System.Security.Principal.SecurityIdentifier]).Value) {
            throw 'runtime file ACL is not owner-only'
        }
    }
}

function Assert-Elevated {
    $principal = [System.Security.Principal.WindowsPrincipal]::new(
        [System.Security.Principal.WindowsIdentity]::GetCurrent()
    )
    if (-not $principal.IsInRole([System.Security.Principal.WindowsBuiltInRole]::Administrator)) {
        throw 'administrator privileges are required'
    }
}

function Read-AgentTools([string]$Path) {
    Assert-AbsoluteFile $Path 'ToolsPath'
    $data = Get-Content -Raw -LiteralPath $Path | ConvertFrom-Json
    $manifestPath = Join-Path (Split-Path -Parent (Split-Path -Parent $PSScriptRoot)) 'tools\platform-manifest.json'
    $expectedManifestHash = (Get-FileHash -LiteralPath $manifestPath -Algorithm SHA256).Hash
    if ($data.schema_version -ne 1 -or [bool]$data.fixture -or [string]$data.manifest_sha256 -cne $expectedManifestHash) {
        throw 'agent tools manifest authority mismatch'
    }
    $toolNames = @('cloudflared_path', 'ffmpeg_path', 'ffprobe_path', 'python_path', 'uv_path')
    $actualNames = @($data.paths.PSObject.Properties.Name | Sort-Object)
    if ((Compare-Object ($toolNames | Sort-Object) $actualNames).Count -ne 0) {
        throw 'agent tools manifest paths are invalid'
    }
    foreach ($name in $toolNames) {
        $executable = [string]$data.paths.$name
        Assert-AbsoluteFile $executable $name
        $expectedHash = [string]$data.executable_sha256.$name
        if ($expectedHash -notmatch '^[0-9A-F]{64}$' -or (Get-FileHash -LiteralPath $executable -Algorithm SHA256).Hash -cne $expectedHash) {
            throw "agent tools executable hash mismatch: $name"
        }
    }
    return $data
}

function Get-FileIdentity([string]$Path) {
    if (-not ('PetCare.NativeFileIdentity' -as [type])) {
        Add-Type @'
using System;
using System.Runtime.InteropServices;
namespace PetCare {
    [StructLayout(LayoutKind.Sequential)]
    public struct ByHandleFileInformation {
        public uint FileAttributes;
        public System.Runtime.InteropServices.ComTypes.FILETIME CreationTime;
        public System.Runtime.InteropServices.ComTypes.FILETIME LastAccessTime;
        public System.Runtime.InteropServices.ComTypes.FILETIME LastWriteTime;
        public uint VolumeSerialNumber;
        public uint FileSizeHigh;
        public uint FileSizeLow;
        public uint NumberOfLinks;
        public uint FileIndexHigh;
        public uint FileIndexLow;
    }
    public static class NativeFileIdentity {
        [DllImport("kernel32.dll", SetLastError = true)]
        public static extern bool GetFileInformationByHandle(IntPtr handle, out ByHandleFileInformation information);
    }
}
'@
    }
    $share = [System.IO.FileShare]::ReadWrite -bor [System.IO.FileShare]::Delete
    $stream = [System.IO.File]::Open($Path, [System.IO.FileMode]::Open, [System.IO.FileAccess]::Read, $share)
    try {
        $information = [PetCare.ByHandleFileInformation]::new()
        if (-not [PetCare.NativeFileIdentity]::GetFileInformationByHandle(
            $stream.SafeFileHandle.DangerousGetHandle(), [ref]$information
        )) {
            throw 'pairing bundle identity query failed'
        }
        return '{0:X8}:{1:X8}:{2:X8}' -f $information.VolumeSerialNumber, $information.FileIndexHigh, $information.FileIndexLow
    }
    finally {
        $stream.Dispose()
    }
}

function Repair-RegistrySurface {
    $allowed = @('ConfigPath', 'ToolsPath', 'JetsonConfigPath')
    $legacySecrets = @('ConnectorToken', 'EnrollmentCode', 'PrivateKey')
    foreach ($name in @((Get-Item -LiteralPath $RegistryPath).Property)) {
        if ($legacySecrets -contains $name) {
            Remove-ItemProperty -LiteralPath $RegistryPath -Name $name
        }
        elseif ($allowed -notcontains $name) {
            throw "unexpected HomeAgent registry value: $name"
        }
    }
}

function Assert-ExactRegistrySurface {
    $expected = @('ConfigPath', 'ToolsPath', 'JetsonConfigPath') | Sort-Object
    $actual = @((Get-Item -LiteralPath $RegistryPath).Property | Sort-Object)
    if ((Compare-Object $expected $actual).Count -ne 0) {
        throw 'HomeAgent registry surface is not exact'
    }
}

if ($Action -eq 'Fixture') {
    foreach ($item in @($ConfigPath, $ToolsPath, $JetsonConfigPath, $PairingBundle, $FixturePath)) {
        if ([string]::IsNullOrWhiteSpace($item) -or -not [System.IO.Path]::IsPathRooted($item)) {
            throw 'fixture paths must be absolute'
        }
    }
    $fixture = [ordered]@{
        action = 'Fixture'
        config_path = $ConfigPath
        tools_path = $ToolsPath
        jetson_config_path = $JetsonConfigPath
        pairing_bundle = $PairingBundle
        mutates_system = $false
    }
    [System.IO.File]::WriteAllText($FixturePath, ($fixture | ConvertTo-Json -Compress), [System.Text.UTF8Encoding]::new($false))
    exit 0
}

if ($Action -eq 'Install') {
    Assert-Elevated
    Assert-AbsoluteFile $ConfigPath 'ConfigPath'
    Assert-AbsoluteFile $PairingBundle 'PairingBundle'
    if ([string]::IsNullOrWhiteSpace($JetsonConfigPath) -or -not [System.IO.Path]::IsPathRooted($JetsonConfigPath)) {
        throw 'JetsonConfigPath must be absolute'
    }
    Assert-OwnerOnlyAcl $ConfigPath
    Assert-OwnerOnlyAcl $PairingBundle
    $bundleIdentity = Get-FileIdentity $PairingBundle
    $tools = Read-AgentTools $ToolsPath
    $Python = [string]$tools.paths.python_path
    Assert-AbsoluteFile $Python 'manifest Python'
    $BackendVenv = Join-Path (Split-Path -Parent (Split-Path -Parent $PSScriptRoot)) 'backend\.venv'
    if (-not (Test-Path -LiteralPath $BackendVenv -PathType Container)) { throw 'backend .venv is missing' }

    & $Python -m app.agent_runtime pair-jetson --config $ConfigPath --bundle $PairingBundle --jetson-config $JetsonConfigPath
    if ($LASTEXITCODE -ne 0) { throw 'Jetson pairing failed' }
    if (-not (Test-Path -LiteralPath $PairingBundle -PathType Leaf)) {
        throw 'pairing bundle disappeared before verified deletion'
    }
    Assert-OwnerOnlyAcl $PairingBundle
    if ((Get-FileIdentity $PairingBundle) -cne $bundleIdentity) {
        throw 'pairing bundle identity changed before verified deletion'
    }
    Remove-Item -LiteralPath $PairingBundle

    New-Item -Path $RegistryPath -Force | Out-Null
    Repair-RegistrySurface
    New-ItemProperty -Path $RegistryPath -Name ConfigPath -PropertyType String -Value $ConfigPath -Force | Out-Null
    New-ItemProperty -Path $RegistryPath -Name ToolsPath -PropertyType String -Value $ToolsPath -Force | Out-Null
    New-ItemProperty -Path $RegistryPath -Name JetsonConfigPath -PropertyType String -Value $JetsonConfigPath -Force | Out-Null
    Assert-ExactRegistrySurface
    & $Python -m app.windows_service --startup auto install
    & "$env:SystemRoot\System32\sc.exe" failure PetCareHomeAgent reset= 86400 actions= restart/5000/restart/30000/restart/120000
    & "$env:SystemRoot\System32\sc.exe" failureflag PetCareHomeAgent 1
    & $Python -m app.windows_service start
    exit $LASTEXITCODE
}

$storedConfig = (Get-ItemProperty -Path $RegistryPath -Name ConfigPath).ConfigPath
$storedTools = (Get-ItemProperty -Path $RegistryPath -Name ToolsPath).ToolsPath
$storedJetson = (Get-ItemProperty -Path $RegistryPath -Name JetsonConfigPath).JetsonConfigPath
$tools = Read-AgentTools $storedTools
$Python = [string]$tools.paths.python_path

if ($Action -eq 'Status') {
    & "$env:SystemRoot\System32\sc.exe" query PetCareHomeAgent
    & $Python -m app.agent_runtime status --config $storedConfig --tools $storedTools --jetson-config $storedJetson
    exit $LASTEXITCODE
}

Assert-Elevated
& $Python -m app.windows_service stop
& $Python -m app.windows_service remove
Remove-Item -LiteralPath $RegistryPath -Recurse -Force
