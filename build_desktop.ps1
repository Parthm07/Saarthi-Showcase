param(
    [string]$Python = 'python',
    [string]$Output = '',
    [string]$Version = '',
    [string]$ReleaseBaseUrl = '',
    [switch]$Publish
)
$ErrorActionPreference = 'Stop'
$projectRoot = $PSScriptRoot
if (-not $Output) { $Output = Join-Path (Split-Path $projectRoot -Parent) 'outputs\saarthi-managed' }
$Output = [System.IO.Path]::GetFullPath($Output)
$toolRoot = Join-Path $projectRoot '.desktop-build-tools'
$previousPythonPath = $env:PYTHONPATH
try {
    $env:PYTHONPATH = $toolRoot
    & $Python -c 'import PyInstaller, cryptography, psycopg, psycopg_binary'
    if ($LASTEXITCODE -ne 0) { throw 'Install the pinned desktop build dependencies before building.' }
    if ($Version) {
        & $Python (Join-Path $projectRoot 'build_release.py') --set-version $Version
        if ($LASTEXITCODE -ne 0) { throw 'Invalid version' }
    }
    # Only create the private publisher key on its first use. Later builds must reuse it.
    & $Python (Join-Path $projectRoot 'build_release.py') --init-key
    if ($LASTEXITCODE -ne 0) { throw 'Publisher signing key check failed' }
    $releaseVersion = (& $Python -c 'from saarthi_runtime import VERSION; print(VERSION)').Trim()
    $buildRoot = Join-Path $projectRoot ('.desktop-build\managed-' + $releaseVersion)
    New-Item -ItemType Directory -Force -Path $Output, $buildRoot | Out-Null
    $common = @('--noconfirm','--onefile','--noupx','--distpath',$Output)
    foreach ($component in @(
        @{Name='SaarthiAdmin'; Entry='desktop_app.py'; Windowed=$true},
        @{Name='SaarthiAgent'; Entry='packaged_agent.py'; Windowed=$false},
        @{Name='SaarthiLauncher'; Entry='desktop_launcher.py'; Windowed=$true},
        @{Name='SaarthiUpdate'; Entry='desktop_setup.py'; Windowed=$true}
    )) {
        $componentArgs = @($common) + @('--name',$component.Name,'--workpath',(Join-Path $buildRoot $component.Name),'--specpath',$buildRoot)
        if ($component.Windowed) { $componentArgs += '--windowed' } else { $componentArgs += '--console' }
        if ($component.Name -eq 'SaarthiAdmin') { $componentArgs += @('--add-data',"$(Join-Path $projectRoot 'desktop'):desktop") }
        if ($component.Name -eq 'SaarthiAgent') { $componentArgs += @('--collect-all','psycopg_binary','--collect-all','psycopg') }
        & $Python -m PyInstaller @componentArgs (Join-Path $projectRoot $component.Entry)
        if ($LASTEXITCODE -ne 0) { throw ('Build failed: ' + $component.Name) }
    }
    $releaseArgs = @((Join-Path $projectRoot 'build_release.py'),'--folder',$Output,'--version',$releaseVersion)
    if ($ReleaseBaseUrl) { $releaseArgs += @('--base-url',$ReleaseBaseUrl) }
    & $Python @releaseArgs
    if ($LASTEXITCODE -ne 0) { throw 'Release signing failed' }
    $package = Join-Path $Output ('Saarthi-' + $releaseVersion + '.zip')
    # --add-data's destination is a directory, not a filename. Bundle the
    # fixed filename expected by desktop_setup at the extraction root.
    $embeddedPackage = Join-Path $buildRoot 'release.zip'
    Copy-Item -LiteralPath $package -Destination $embeddedPackage -Force
    & $Python -m PyInstaller @common --windowed --name SaarthiAdminSetup --workpath (Join-Path $buildRoot 'Setup') --specpath $buildRoot --add-data "${embeddedPackage}:." (Join-Path $projectRoot 'desktop_setup.py')
    if ($LASTEXITCODE -ne 0) { throw 'Complete setup build failed' }
    & $Python -c 'import hashlib, pathlib, sys; from PyInstaller.archive.readers import CArchiveReader; embedded=CArchiveReader(sys.argv[1]).extract("release.zip"); assert hashlib.sha256(embedded).digest()==hashlib.sha256(pathlib.Path(sys.argv[2]).read_bytes()).digest(), "Installer release mismatch"' (Join-Path $Output 'SaarthiAdminSetup.exe') $package
    if ($LASTEXITCODE -ne 0) { throw 'Complete setup embedded release validation failed' }
    foreach ($guide in @('DESKTOP_ADMIN.md','DEPLOYMENT_AND_UPDATES.md')) {
        if (Test-Path -LiteralPath (Join-Path $projectRoot $guide)) { Copy-Item -LiteralPath (Join-Path $projectRoot $guide) -Destination $Output -Force }
    }
    Get-FileHash -Algorithm SHA256 -LiteralPath (Join-Path $Output 'SaarthiAdminSetup.exe'), $package | Format-Table -AutoSize
    if ($Publish) {
        & $Python (Join-Path $projectRoot 'publish_desktop.py') $package
        if ($LASTEXITCODE -ne 0) { throw 'Online publishing failed. The local update ZIP is ready; retry with publish_desktop.py.' }
    }
} finally { $env:PYTHONPATH = $previousPythonPath }
