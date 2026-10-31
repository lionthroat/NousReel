# Builds dist\NousReel\NousReel.exe (with mpv, ffmpeg and ffprobe in bin\), and
# installer-output\NousReel-Setup.exe if Inno Setup 6 is installed. Run from anywhere:
#   powershell -ExecutionPolicy Bypass -File build.ps1
#
# Where mpv and ffmpeg come from: $env:NOUSREEL_MPV_DIR (a folder with mpv.exe)
# and $env:NOUSREEL_FFMPEG_DIR (a folder with ffmpeg.exe, ffprobe.exe and, for a
# "shared" build, their DLLs). GitHub sets these to pinned downloads; locally
# they default to the winget installs Nous Reel already uses.
$ErrorActionPreference = "Stop"
$root = $PSScriptRoot
$py = Join-Path $root ".venv\Scripts\python.exe"

& $py -m pip install --quiet -r (Join-Path $root "requirements.txt")
& $py (Join-Path $root "make_icon.py")

# Version comes from the VERSION file
$version = (Get-Content (Join-Path $root "VERSION") -Raw).Trim()
$parts = @($version.Split(".") + @("0", "0", "0"))[0..3]
New-Item -ItemType Directory -Force (Join-Path $root "build") | Out-Null
$versionFile = Join-Path $root "build\version_info.txt"
(Get-Content (Join-Path $root "version_info.txt") -Raw).
    Replace("{VERSION_TUPLE}", ($parts -join ", ")).Replace("{VERSION}", $version) |
    Set-Content $versionFile -Encoding utf8

# QtNetwork stays: the player talks to mpv, and right-click copies to each other, over local sockets
& $py -m PyInstaller --noconfirm --clean --windowed --name NousReel `
    --icon (Join-Path $root "icon.ico") `
    --version-file $versionFile `
    --add-data "$(Join-Path $root 'icon.ico');." `
    --add-data "$(Join-Path $root 'VERSION');." `
    --add-data "$(Join-Path $root 'THIRD-PARTY.md');." `
    --distpath (Join-Path $root "dist") --workpath (Join-Path $root "build") `
    --specpath (Join-Path $root "build") `
    --exclude-module PySide6.QtQml --exclude-module PySide6.QtQuick --exclude-module PySide6.QtPdf `
    (Join-Path $root "nousreel.py")
if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed" }

# Qt drags in pieces this app never uses. Drop them.
$qt = Join-Path $root "dist\NousReel\_internal\PySide6"
$drop = @("opengl32sw.dll", "Qt6Quick*.dll", "Qt6Qml*.dll", "Qt6Pdf*.dll", "Qt6VirtualKeyboard*.dll",
          "Qt6OpenGL*.dll", "translations", "plugins\tls", "plugins\platforminputcontexts",
          "plugins\networkinformation", "plugins\imageformats\qpdf.dll", "plugins\imageformats\qsvg.dll",
          "plugins\iconengines", "Qt6Svg.dll")
foreach ($d in $drop) { Remove-Item (Join-Path $qt $d) -Recurse -Force -ErrorAction SilentlyContinue }

# mpv (playback) and ffmpeg/ffprobe (everything else) ride along in bin\
function Find-Tool($name) {
    & $py -c "import sys; sys.path.insert(0, r'$root'); from reel_tools_path import find; print(find('$name') or '')"
}
$mpvDir = $env:NOUSREEL_MPV_DIR
if (-not $mpvDir) { $mpvDir = Split-Path (Find-Tool "mpv") }
$ffDir = $env:NOUSREEL_FFMPEG_DIR
if (-not $ffDir) { $ffDir = Split-Path (Find-Tool "ffmpeg") }
foreach ($need in "$mpvDir\mpv.exe", "$ffDir\ffmpeg.exe", "$ffDir\ffprobe.exe") {
    if (-not (Test-Path $need)) { throw "Missing $need (install mpv and ffmpeg, or set NOUSREEL_MPV_DIR / NOUSREEL_FFMPEG_DIR)" }
}
$bin = Join-Path $root "dist\NousReel\bin"
New-Item -ItemType Directory -Force $bin | Out-Null
Copy-Item "$mpvDir\mpv.exe" $bin
Get-ChildItem $mpvDir -Filter *.dll | Copy-Item -Destination $bin
Copy-Item "$ffDir\ffmpeg.exe", "$ffDir\ffprobe.exe" $bin
Get-ChildItem $ffDir -Filter *.dll | Copy-Item -Destination $bin
Copy-Item (Join-Path $root "THIRD-PARTY.md") $bin

$mb = (Get-ChildItem (Join-Path $root "dist\NousReel") -Recurse -File | Measure-Object Length -Sum).Sum / 1MB
Write-Host ("Built: {0} (version {1}, {2:N0} MB)" -f (Join-Path $root 'dist\NousReel\NousReel.exe'), $version, $mb)

# The installer, if Inno Setup 6 is around
$iscc = @("${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe", "$env:ProgramFiles\Inno Setup 6\ISCC.exe",
          "$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe") | Where-Object { Test-Path $_ } | Select-Object -First 1
if ($iscc) {
    & $iscc /Q (Join-Path $root "installer.iss")
    if ($LASTEXITCODE -ne 0) { throw "Inno Setup failed" }
    Write-Host "Built: $(Join-Path $root 'installer-output\NousReel-Setup.exe')"
} else {
    Write-Host "(Inno Setup 6 not found, so no installer; GitHub builds it on version tags)"
}
