# Checks a built dist\NousReel (or an installed copy): it starts with a video and
# plays it through its own bundled mpv, the one-step 720p mode converts a clip,
# and the installer's right-click switches work (under a throwaway registry key).
#   powershell -ExecutionPolicy Bypass -File tests\test_frozen.ps1 [-App <folder>] -Video <file>
param([string]$App = "", [Parameter(Mandatory)][string]$Video)
$ErrorActionPreference = "Stop"
# $PSScriptRoot is empty in param defaults on PowerShell 5
if (-not $App) { $App = Join-Path $PSScriptRoot "..\dist\NousReel" }
$exe = Join-Path (Resolve-Path $App) "NousReel.exe"
$out = Join-Path $PSScriptRoot "out\frozen"
New-Item -ItemType Directory -Force $out | Out-Null
$env:NOUSREEL_SETTINGS = "NousReelTest"
function Fail($m) { throw "FAIL: $m" }

# 1. opens a video and plays it with the bundled mpv
$p = Start-Process $exe -ArgumentList "`"$Video`"" -PassThru
Start-Sleep 10
if ($p.HasExited) { Fail "exited right after opening a video (code $($p.ExitCode))" }
$mpv = Get-CimInstance Win32_Process -Filter "ParentProcessId=$($p.Id)" | Where-Object Name -eq "mpv.exe"
if (-not $mpv) { Fail "no mpv child process" }
$want = Join-Path (Split-Path $exe) "bin\mpv.exe"
if ($mpv.ExecutablePath -ne $want) { Fail "mpv came from $($mpv.ExecutablePath), not the bundled $want" }
$title = (Get-Process -Id $p.Id).MainWindowTitle
Write-Host "PASS running with bundled mpv; window: '$title'"
Stop-Process -Id $mpv.ProcessId -Force -ErrorAction SilentlyContinue
Stop-Process -Id $p.Id -Force
Start-Sleep 1

# 2. one-step 720p on a short generated 1080p clip, with the bundled ffmpeg
$ff = Join-Path (Split-Path $exe) "bin\ffmpeg.exe"
$clip = Join-Path $out "Frozen Test - S01E01 - Pilot.mp4"
Remove-Item "$out\*720p*" -ErrorAction SilentlyContinue
& $ff -nostdin -v error -y -f lavfi -i "testsrc2=s=1920x1080:r=24:d=5" -f lavfi -i "sine=d=5" -c:v libx264 -b:v 8M -c:a aac -shortest $clip
$env:NOUSREEL_QUICK_EXIT_WHEN_DONE = "1"
$q = Start-Process $exe -ArgumentList "--plex720", "`"$clip`"" -PassThru -Wait
Remove-Item Env:NOUSREEL_QUICK_EXIT_WHEN_DONE
$made = Join-Path $out "Frozen Test - S01E01 - Pilot - 720p.mkv"
if ($q.ExitCode -ne 0 -or -not (Test-Path $made)) { Fail "720p for Plex didn't produce $made (exit $($q.ExitCode))" }
$h = & (Join-Path (Split-Path $exe) "bin\ffprobe.exe") -v error -select_streams v:0 -show_entries stream=height,codec_name -of csv=p=0 $made
if ($h -ne "hevc,720") { Fail "converted file is '$h', not hevc,720" }
Write-Host "PASS 720p for Plex made a 720p H.265 file"

# 3. the installer's right-click switches, pointed at a test key
$env:NOUSREEL_SHELL_BASE = "Software\NousReelTest\Classes\SystemFileAssociations"
$key = "HKCU:\$env:NOUSREEL_SHELL_BASE\.mkv\shell\NousReel.Plex720\command"
Start-Process $exe -ArgumentList "--add-right-click" -Wait
$cmd = (Get-ItemProperty $key -ErrorAction SilentlyContinue)."(default)"
if ($cmd -ne "`"$exe`" --plex720 `"%1`"") { Fail "right-click command is '$cmd'" }
Start-Process $exe -ArgumentList "--remove-right-click" -Wait
if (Test-Path $key) { Fail "--remove-right-click left the entry" }
Remove-Item "HKCU:\Software\NousReelTest" -Recurse -ErrorAction SilentlyContinue
Remove-Item Env:NOUSREEL_SHELL_BASE
Write-Host "PASS right-click entry added and removed"
Write-Host "All frozen-app checks passed"
