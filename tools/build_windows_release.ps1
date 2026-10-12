param(
    [Parameter(Mandatory = $true)]
    [ValidatePattern('^\d+\.\d+\.\d+$')]
    [string]$Version
)

$ErrorActionPreference = 'Stop'
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$python = Join-Path $projectRoot '.venv\Scripts\python.exe'
$entryPoint = Join-Path $projectRoot 'video_cut.py'
$releaseName = "Easy-Video-Editor-Windows-x64-v$Version"
$releaseRoot = Join-Path $projectRoot 'release'
$packageRoot = Join-Path $releaseRoot $releaseName
$archivePath = Join-Path $releaseRoot "$releaseName.zip"
$workRoot = Join-Path $projectRoot ".venv\package-work\build-v$Version"
$distRoot = Join-Path $projectRoot ".venv\package-work\dist-v$Version"
$builtApp = Join-Path $distRoot 'Easy Video Editor'

if (-not (Test-Path $python)) {
    throw "Project virtual environment not found: $python"
}
if (-not (Test-Path (Join-Path $projectRoot 'tools\ffmpeg.exe')) -or
    -not (Test-Path (Join-Path $projectRoot 'tools\ffprobe.exe'))) {
    throw 'tools\ffmpeg.exe and tools\ffprobe.exe are required to package the app.'
}
if ((Test-Path $packageRoot) -or (Test-Path $archivePath) -or
    (Test-Path $workRoot) -or (Test-Path $distRoot)) {
    throw "Output for version $Version already exists. Choose a new version number."
}

New-Item -ItemType Directory -Path $releaseRoot -Force | Out-Null

# Keep unrelated bundled applications' DLLs out of PyInstaller's dependency search.
$originalPath = $env:PATH
$env:PATH = @(
    (Join-Path $env:WINDIR 'System32'),
    $env:WINDIR,
    (Join-Path $env:WINDIR 'System32\Wbem'),
    (Join-Path $projectRoot '.venv\Scripts')
) -join [IO.Path]::PathSeparator

try {
    & $python -m PyInstaller `
        --noconfirm `
        --clean `
        --windowed `
        --onedir `
        --name 'Easy Video Editor' `
        --contents-directory '_internal' `
        --workpath $workRoot `
        --distpath $distRoot `
        --specpath $workRoot `
        $entryPoint
    if ($LASTEXITCODE -ne 0) {
        throw "PyInstaller failed with exit code $LASTEXITCODE."
    }
}
finally {
    $env:PATH = $originalPath
}

if (-not (Test-Path (Join-Path $builtApp 'Easy Video Editor.exe'))) {
    throw 'PyInstaller did not produce the expected executable.'
}

New-Item -ItemType Directory -Path $packageRoot | Out-Null
Copy-Item -Path (Join-Path $builtApp '*') -Destination $packageRoot -Recurse

# Qt uses the Windows ICU API here. PyInstaller can otherwise pick up a
# same-named ICU DLL from an unrelated runtime earlier on PATH.
$internalRoot = Join-Path $packageRoot '_internal'
Get-ChildItem $internalRoot -Recurse -File -Filter 'icu*.dll' |
    Remove-Item -Force

Copy-Item (Join-Path $projectRoot 'LICENSE') $packageRoot
Copy-Item (Join-Path $projectRoot 'README.md') $packageRoot
Copy-Item (Join-Path $projectRoot 'THIRD-PARTY-NOTICES.md') $packageRoot
$packageToolsRoot = Join-Path $packageRoot 'tools'
$packageLicensesRoot = Join-Path $packageRoot 'licenses'
New-Item -ItemType Directory -Path $packageToolsRoot, $packageLicensesRoot | Out-Null
Copy-Item (Join-Path $projectRoot 'tools\ffmpeg.exe') $packageToolsRoot
Copy-Item (Join-Path $projectRoot 'tools\ffprobe.exe') $packageToolsRoot
Copy-Item (Join-Path $projectRoot 'tools\FFmpeg-LICENSE.txt') $packageToolsRoot
Copy-Item (Join-Path $projectRoot 'tools\FFmpeg-README.txt') $packageToolsRoot
Copy-Item -Path (Join-Path $projectRoot 'licenses\*') -Destination $packageLicensesRoot -Recurse

Compress-Archive -Path $packageRoot -DestinationPath $archivePath -CompressionLevel Optimal
Write-Output "Packaged executable: $(Join-Path $packageRoot 'Easy Video Editor.exe')"
Write-Output "Release archive: $archivePath"
