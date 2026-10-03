# Download and validate the official Windows bundle. Run by the installing agent.
[CmdletBinding()]
param(
    [string]$InstallRoot = (Join-Path $env:LOCALAPPDATA 'Programs\concept-memory'),
    [string]$Version = 'latest',
    [string]$ArchivePath,
    [string]$ChecksumPath
)
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
$repo = 'fangshuituilaji/concept-memory'
$headers = @{ 'User-Agent' = 'concept-memory-installer'; Accept = 'application/vnd.github+json' }
$stage = $null
$backup = $null
$installed = $false

function Test-Bundle([string]$Root) {
    foreach ($relative in @('python\python.exe', 'bin\memory-mcp.cmd', 'src\memory_system\mcp_server.py',
        'install\check_install.ps1', 'install\configure_client.py', 'install\INSTALL.md', 'VERSION')) {
        if (-not (Test-Path -LiteralPath (Join-Path $Root $relative) -PathType Leaf)) {
            throw "Bundle missing $relative. Use a release containing the one-message installer; older releases are not sufficient."
        }
    }
    $check = & powershell.exe -NoProfile -ExecutionPolicy Bypass -File (Join-Path $Root 'install\check_install.ps1') 2>&1
    if ($LASTEXITCODE -ne 0 -or -not ($check -match '\bPASS\b')) {
        throw ('Bundle self-check failed: ' + ($check -join [Environment]::NewLine))
    }
}

try {
    $arch = $env:PROCESSOR_ARCHITEW6432
    if (-not $arch) { $arch = $env:PROCESSOR_ARCHITECTURE }
    if ($env:OS -ne 'Windows_NT' -or $arch -ne 'AMD64') { throw 'Only Windows x64 is supported.' }
    if ([string]::IsNullOrWhiteSpace($InstallRoot)) { throw 'InstallRoot is required.' }
    $target = [IO.Path]::GetFullPath($InstallRoot).TrimEnd('\', '/')
    if ($target -match '[&^%]' -or $target -eq [IO.Path]::GetPathRoot($target).TrimEnd('\', '/')) {
        throw 'Use a non-root installation directory without &, ^ or %.'
    }
    $parent = Split-Path -Parent $target
    if (-not $parent) { throw 'Invalid installation directory.' }
    if (Test-Path -LiteralPath $target) {
        if (-not (Test-Path -LiteralPath (Join-Path $target 'VERSION') -PathType Leaf) -or
            -not (Test-Path -LiteralPath (Join-Path $target 'bin\memory-mcp.cmd') -PathType Leaf) -or
            -not (Test-Path -LiteralPath (Join-Path $target 'python\python.exe') -PathType Leaf)) {
            throw 'The destination contains unrelated files; choose another InstallRoot.'
        }
        $running = Get-CimInstance Win32_Process | Where-Object {
            $_.ExecutablePath -and $_.ExecutablePath.StartsWith($target + '\', [StringComparison]::OrdinalIgnoreCase)
        }
        if ($running) { throw 'This installation is running. Stop its MCP connection, then retry; no process was terminated.' }
    }
    New-Item -ItemType Directory -Path $parent -Force | Out-Null
    # Short staging names leave room for deep dependency paths on PowerShell 5.1.
    $stage = Join-Path $parent ('.cm-install-' + [Guid]::NewGuid().ToString('N').Substring(0, 12))
    New-Item -ItemType Directory -Path $stage | Out-Null
    if ($ArchivePath) {
        if (-not $ChecksumPath) { throw 'A local archive requires ChecksumPath; checksum verification cannot be skipped.' }
        $archive = (Resolve-Path -LiteralPath $ArchivePath).Path
        $checksum = (Resolve-Path -LiteralPath $ChecksumPath).Path
        $assetName = Split-Path -Leaf $archive
        if ($assetName -notmatch '^concept-memory-offline-win64-v(\d+\.\d+\.\d+)\.zip$') {
            throw 'Unexpected archive filename.'
        }
        $expectedVersion = $Matches[1]
        if ($Version -ne 'latest' -and $Version.TrimStart('v') -ne $expectedVersion) { throw 'Requested version does not match archive.' }
    } else {
        if ($Version -ne 'latest' -and $Version -notmatch '^v?\d+\.\d+\.\d+$') { throw 'Version must be latest or X.Y.Z.' }
        $releaseUrl = "https://api.github.com/repos/$repo/releases/latest"
        if ($Version -ne 'latest') { $releaseUrl = "https://api.github.com/repos/$repo/releases/tags/v$($Version.TrimStart('v'))" }
        $release = Invoke-RestMethod -Uri $releaseUrl -Headers $headers -TimeoutSec 60
        if ($release.tag_name -notmatch '^v(\d+\.\d+\.\d+)$') { throw 'Unexpected release tag.' }
        $expectedVersion = $Matches[1]
        $assetName = "concept-memory-offline-win64-v$expectedVersion.zip"
        $zipAssets = @($release.assets | Where-Object { $_.name -ceq $assetName })
        $sumAssets = @($release.assets | Where-Object { $_.name -ceq "$assetName.sha256" })
        if ($zipAssets.Count -ne 1 -or $sumAssets.Count -ne 1) { throw 'Release archive or SHA256 asset missing/ambiguous.' }
        $archive = Join-Path $stage $assetName
        $checksum = Join-Path $stage "$assetName.sha256"
        $downloadHeaders = @{ 'User-Agent' = 'concept-memory-installer'; Accept = 'application/octet-stream' }
        Invoke-WebRequest -UseBasicParsing -Uri "https://api.github.com/repos/$repo/releases/assets/$($zipAssets[0].id)" -Headers $downloadHeaders -OutFile $archive -TimeoutSec 180
        Invoke-WebRequest -UseBasicParsing -Uri "https://api.github.com/repos/$repo/releases/assets/$($sumAssets[0].id)" -Headers $downloadHeaders -OutFile $checksum -TimeoutSec 60
    }
    $sumText = (Get-Content -LiteralPath $checksum -Raw).Trim()
    $sumMatch = [regex]::Match($sumText, '^([a-fA-F0-9]{64})\s+\*?([^\r\n]+)$')
    if (-not $sumMatch.Success -or $sumMatch.Groups[2].Value -cne $assetName) { throw 'Malformed SHA256 file or incorrect filename.' }
    $hashAlgorithm = [Security.Cryptography.SHA256]::Create()
    $archiveStream = [IO.File]::OpenRead($archive)
    try { $digest = [BitConverter]::ToString($hashAlgorithm.ComputeHash($archiveStream)).Replace('-', '').ToLowerInvariant() }
    finally { $archiveStream.Dispose(); $hashAlgorithm.Dispose() }
    if ($digest -ne $sumMatch.Groups[1].Value.ToLowerInvariant()) { throw 'SHA256 mismatch; installation stopped.' }

    Add-Type -AssemblyName System.IO.Compression.FileSystem
    $zip = [IO.Compression.ZipFile]::OpenRead($archive)
    try {
        if ($zip.Entries.Count -eq 0) { throw 'Empty archive.' }
        foreach ($entry in $zip.Entries) {
            $name = $entry.FullName.Replace('\', '/')
            if ($name -notmatch '^concept-memory/' -or $name -match '(^|/)\.\.(/|$)' -or $name -match ':') {
                throw "Unsafe archive entry: $name"
            }
            if (($entry.ExternalAttributes -band 0xF0000000L) -eq 0xA0000000L) { throw 'Archive symlinks are not allowed.' }
        }
    } finally { $zip.Dispose() }
    $extract = Join-Path $stage 'e'
    [IO.Compression.ZipFile]::ExtractToDirectory($archive, $extract)
    $candidate = Join-Path $extract 'concept-memory'
    $bundleVersion = (Get-Content -LiteralPath (Join-Path $candidate 'VERSION') -Raw).Trim()
    if ($bundleVersion -ne $expectedVersion) { throw 'Bundle version does not match release filename/tag.' }
    Test-Bundle $candidate
    if (Test-Path -LiteralPath $target) {
        $backup = $target + '.previous-' + [Guid]::NewGuid().ToString('N')
        Move-Item -LiteralPath $target -Destination $backup
    }
    try {
        Move-Item -LiteralPath $candidate -Destination $target
        $installed = $true
        Test-Bundle $target
    } catch {
        if ($installed -and (Test-Path -LiteralPath $target)) {
            Move-Item -LiteralPath $target -Destination (Join-Path $stage 'failed-installation')
            $installed = $false
        }
        if ($backup -and (Test-Path -LiteralPath $backup)) { Move-Item -LiteralPath $backup -Destination $target; $backup = $null }
        throw
    }
    @{
        status = 'package_ready'; version = $bundleVersion; package_root = $target; sha256 = $digest
        previous_installation = $backup
        configure_script = (Join-Path $target 'install\configure_client.py')
        instructions = (Join-Path $target 'install\INSTALL.md')
        server = @{
            transport = 'stdio'; command = (Join-Path $target 'python\python.exe')
            args = @('-m', 'memory_system.mcp_server')
            env = @{ PYTHONPATH = "$target\python\Lib\site-packages;$target\src" }
        }
        next_step = 'Register in the CURRENT client, reload if needed, then verify actual agent tool calls. package_ready is not installation complete.'
    } | ConvertTo-Json -Depth 6
} catch {
    Write-Error $_
    exit 1
} finally {
    if ($stage -and (Test-Path -LiteralPath $stage)) {
        $resolvedStage = [IO.Path]::GetFullPath($stage)
        $resolvedParent = [IO.Path]::GetFullPath($parent).TrimEnd('\') + '\'
        if ($resolvedStage.StartsWith($resolvedParent, [StringComparison]::OrdinalIgnoreCase) -and
            (Split-Path -Leaf $resolvedStage).StartsWith('.cm-install-')) {
            Remove-Item -LiteralPath $resolvedStage -Recurse -Force
        }
    }
}
