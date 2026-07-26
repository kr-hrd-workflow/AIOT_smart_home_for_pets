$ErrorActionPreference = 'Stop'

function ConvertFrom-VisualStudioBuildToolsJson {
  [CmdletBinding()]
  param([Parameter(Mandatory)][string]$Json)

  if ([string]::IsNullOrWhiteSpace($Json)) { return @() }
  $instances = $Json | ConvertFrom-Json
  return $instances
}

function Select-ExactVisualStudioBuildToolsInstance {
  [CmdletBinding()]
  param(
    [object[]]$Instances,
    [Parameter(Mandatory)][string]$Version,
    [string]$InstallationPath = ''
  )

  $versionPattern = '^' + [regex]::Escape($Version) + '(?:\+|$)'
  $expectedPath = if ($InstallationPath) {
    [IO.Path]::GetFullPath($InstallationPath).TrimEnd('\', '/')
  } else {
    ''
  }

  foreach ($instance in $Instances) {
    if (-not $instance) { continue }
    $semanticVersion = [string]$instance.catalog.productSemanticVersion
    if ($semanticVersion -notmatch $versionPattern) { continue }
    if ($instance.isComplete -ne $true -or $instance.isLaunchable -ne $true -or $instance.isRebootRequired -eq $true) { continue }
    if ($expectedPath) {
      $actualPath = [IO.Path]::GetFullPath([string]$instance.installationPath).TrimEnd('\', '/')
      if (-not [string]::Equals($actualPath, $expectedPath, [StringComparison]::OrdinalIgnoreCase)) { continue }
    }
    return $instance
  }
  return $null
}
