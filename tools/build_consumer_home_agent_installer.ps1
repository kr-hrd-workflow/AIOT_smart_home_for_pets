[CmdletBinding()]
param(
    [string]$SiteOrigin = 'https://kr-hrd-petcare-aiot-team.cpark333333.chatgpt.site',
    [string]$OutputDirectory = ''
)

$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent $PSScriptRoot
if (-not $OutputDirectory) {
    $OutputDirectory = Join-Path $Root 'packaging\windows\release'
}
$OutputDirectory = [IO.Path]::GetFullPath($OutputDirectory)
$ExpectedOutputRoot = [IO.Path]::GetFullPath((Join-Path $Root 'packaging\windows\release')).TrimEnd('\')
if (
    -not $OutputDirectory.Equals($ExpectedOutputRoot, [StringComparison]::OrdinalIgnoreCase) -and
    -not $OutputDirectory.StartsWith($ExpectedOutputRoot + '\', [StringComparison]::OrdinalIgnoreCase)
) {
    throw 'installer output must stay under packaging\windows\release'
}

$uri = [Uri]$SiteOrigin
if (
    -not $uri.IsAbsoluteUri -or
    $uri.Scheme -cne 'https' -or
    $uri.AbsolutePath -cne '/' -or
    $uri.Query -or
    $uri.Fragment -or
    $SiteOrigin.EndsWith('/')
) {
    throw 'SiteOrigin must be an HTTPS origin without a path'
}

$BuildRoot = [IO.Path]::GetFullPath((Join-Path $Root '.runtime\consumer-installer-build'))
$RuntimeBoundary = [IO.Path]::GetFullPath((Join-Path $Root '.runtime')).TrimEnd('\')
if (-not $BuildRoot.StartsWith($RuntimeBoundary + '\', [StringComparison]::OrdinalIgnoreCase)) {
    throw 'installer build root escaped .runtime'
}
if (Test-Path -LiteralPath $BuildRoot) {
    Remove-Item -LiteralPath $BuildRoot -Recurse -Force
}
$StageRoot = Join-Path $BuildRoot 'bundle'
New-Item -ItemType Directory -Force -Path $StageRoot, $OutputDirectory | Out-Null

function Copy-Directory([string]$RelativePath) {
    $source = Join-Path $Root $RelativePath
    $destination = Join-Path $StageRoot $RelativePath
    New-Item -ItemType Directory -Force -Path $destination | Out-Null
    $copy = Start-Process -FilePath "$env:SystemRoot\System32\robocopy.exe" `
        -WindowStyle Hidden -Wait -PassThru `
        -ArgumentList @(
            $source,
            $destination,
            '/E',
            '/R:1',
            '/W:1',
            '/XD',
            '__pycache__',
            '/XF',
            '*.pyc',
            '/NFL',
            '/NDL',
            '/NJH',
            '/NJS',
            '/NP'
        )
    if ($copy.ExitCode -gt 7) { throw "bundle copy failed: $RelativePath ($($copy.ExitCode))" }
}

function Copy-File([string]$RelativePath) {
    $source = Join-Path $Root $RelativePath
    $destination = Join-Path $StageRoot $RelativePath
    New-Item -ItemType Directory -Force -Path (Split-Path -Parent $destination) | Out-Null
    Copy-Item -LiteralPath $source -Destination $destination -Force
}

Copy-Directory 'backend\app'
Copy-Directory 'backend\migrations'
foreach ($file in @(
    'backend\alembic.ini',
    'backend\requirements-home-agent.in',
    'backend\requirements-home-agent.lock',
    'infra\mosquitto\mosquitto.conf',
    'packaging\windows\install-home-agent.ps1',
    'tools\bootstrap_agent_runtime.ps1',
    'tools\install_consumer_home_agent.ps1',
    'tools\platform-manifest.json',
    'tools\services.ps1'
)) {
    Copy-File $file
}

$BundleDraftPath = Join-Path $BuildRoot 'PetCare-Home-Agent-Bundle.zip'
Compress-Archive -Path (Join-Path $StageRoot '*') -DestinationPath $BundleDraftPath -CompressionLevel Optimal
$BundleHash = (Get-FileHash -LiteralPath $BundleDraftPath -Algorithm SHA256).Hash
$BundleBase64 = [Convert]::ToBase64String([IO.File]::ReadAllBytes($BundleDraftPath))
Get-ChildItem -LiteralPath $OutputDirectory -Filter 'PetCare-Home-Agent-Bundle*.zip' -File |
    Remove-Item -Force

$source = @'
using System;
using System.Diagnostics;
using System.IO;
using System.IO.Compression;
using System.Security.Cryptography;
using System.Security.Principal;
using System.Text;

internal static class Program
{
    private const string BundleBase64 = "__BUNDLE_BASE64__";
    private const string BundleSha256 = "__BUNDLE_SHA256__";
    private const string SiteOrigin = "__SITE_ORIGIN__";

    private static bool IsAdministrator()
    {
        using (WindowsIdentity identity = WindowsIdentity.GetCurrent())
        {
            return new WindowsPrincipal(identity).IsInRole(WindowsBuiltInRole.Administrator);
        }
    }

    private static string Sha256(string path)
    {
        using (FileStream stream = File.OpenRead(path))
        using (SHA256 hash = SHA256.Create())
        {
            return BitConverter.ToString(hash.ComputeHash(stream)).Replace("-", "");
        }
    }

    public static int Main()
    {
        Console.OutputEncoding = Encoding.UTF8;
        if (!IsAdministrator())
        {
            try
            {
                Process.Start(new ProcessStartInfo
                {
                    FileName = Process.GetCurrentProcess().MainModule.FileName,
                    UseShellExecute = true,
                    Verb = "runas"
                });
                return 0;
            }
            catch
            {
                Console.Error.WriteLine("PetCare Home Agent installation requires administrator approval.");
                return 1;
            }
        }

        string temporary = Path.Combine(
            Path.GetTempPath(),
            "PetCare-Home-Agent-" + Guid.NewGuid().ToString("N")
        );
        Directory.CreateDirectory(temporary);
        try
        {
            string archive = Path.Combine(temporary, "bundle.zip");
            File.WriteAllBytes(archive, Convert.FromBase64String(BundleBase64));
            if (!String.Equals(Sha256(archive), BundleSha256, StringComparison.Ordinal))
            {
                Console.Error.WriteLine("PetCare installer integrity verification failed.");
                return 2;
            }
            ZipFile.ExtractToDirectory(archive, temporary);
            string installer = Path.Combine(
                temporary,
                "tools",
                "install_consumer_home_agent.ps1"
            );
            Process process = Process.Start(new ProcessStartInfo
            {
                FileName = "powershell.exe",
                Arguments = "-NoProfile -ExecutionPolicy Bypass -File \"" + installer +
                    "\" -Action Install -SiteOrigin \"" + SiteOrigin + "\"",
                UseShellExecute = false,
                WorkingDirectory = temporary
            });
            process.WaitForExit();
            return process.ExitCode;
        }
        catch (Exception error)
        {
            Console.Error.WriteLine("PetCare installation failed: " + error.Message);
            return 3;
        }
        finally
        {
            try { Directory.Delete(temporary, true); } catch { }
        }
    }
}
'@
$source = $source.
    Replace('__BUNDLE_BASE64__', $BundleBase64).
    Replace('__BUNDLE_SHA256__', $BundleHash).
    Replace('__SITE_ORIGIN__', $SiteOrigin)
$sourcePath = Join-Path $BuildRoot 'PetCareHomeAgentSetup.cs'
[IO.File]::WriteAllText($sourcePath, $source, [Text.UTF8Encoding]::new($false))

$compiler = Join-Path $env:WINDIR 'Microsoft.NET\Framework64\v4.0.30319\csc.exe'
if (-not (Test-Path -LiteralPath $compiler -PathType Leaf)) {
    throw '.NET Framework C# compiler is unavailable'
}
$SetupPath = Join-Path $OutputDirectory 'PetCare-Home-Agent-Setup.exe'
& $compiler `
    /nologo `
    /target:exe `
    /optimize+ `
    /platform:x64 `
    "/out:$SetupPath" `
    /reference:System.IO.Compression.dll `
    /reference:System.IO.Compression.FileSystem.dll `
    $sourcePath
if ($LASTEXITCODE) { throw 'consumer installer compilation failed' }

$SetupHash = (Get-FileHash -LiteralPath $SetupPath -Algorithm SHA256).Hash
[IO.File]::WriteAllText(
    (Join-Path $OutputDirectory 'PetCare-Home-Agent-Setup.sha256'),
    "$SetupHash  PetCare-Home-Agent-Setup.exe`n",
    [Text.UTF8Encoding]::new($false)
)
Write-Output "Consumer Home Agent installer PASS: $SetupPath"
Write-Output "Embedded bundle SHA-256: $BundleHash"
Write-Output "Setup SHA-256: $SetupHash"
