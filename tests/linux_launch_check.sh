#!/usr/bin/env bash
# Checks the installed nous-reel (.deb) on Linux. Run it under xvfb-run with a video:
#   xvfb-run -a bash tests/linux_launch_check.sh some.mkv
# It opens the video, checks mpv is playing it inside the window, then starts
# again as if in a Wayland session and checks it falls back to X11.
# Used by .github/workflows/build.yml.
set -uo pipefail
video=$1
name=$(basename "$video")
pid=
fail() { echo "FAIL: $*"; [ -n "$pid" ] && kill "$pid" 2>/dev/null; exit 1; }
wait_window() {  # wait_window <title regex>: prints the window id
    for _ in $(seq 30); do
        w=$(xdotool search --name "$1" 2>/dev/null | head -1)
        [ -n "$w" ] && { echo "$w"; return; }
        sleep 1
    done
}
check_mpv() {
    for _ in $(seq 20); do
        m=$(pgrep -P "$pid" mpv | head -1)
        [ -n "$m" ] && { tr '\0' ' ' < "/proc/$m/cmdline" | grep -o -- "--wid=[0-9]*"; return 0; }
        sleep 1
    done
    return 1
}

export NOUSREEL_SETTINGS=NousReelTest
XDG_CURRENT_DESKTOP=X-Cinnamon nous-reel "$video" & pid=$!
win=$(wait_window "Nous Reel")
kill -0 "$pid" 2>/dev/null || fail "nous-reel exited early"
[ -n "$win" ] || fail "no Nous Reel window"
echo "window: $(xdotool getwindowname "$win")"
check_mpv || fail "mpv isn't running inside the window"
sleep 4
kill -0 "$pid" 2>/dev/null || fail "nous-reel died while playing"
xdotool getwindowname "$win" | grep -qF "$name" || fail "window title doesn't name the video"
kill "$pid"; wait "$pid" 2>/dev/null

XDG_SESSION_TYPE=wayland WAYLAND_DISPLAY=wayland-none nous-reel "$video" & pid=$!
win=$(wait_window "Nous Reel")
kill -0 "$pid" 2>/dev/null || fail "nous-reel exited early in a Wayland session"
[ -n "$win" ] || fail "no window in a Wayland session"
check_mpv || fail "mpv isn't running in a Wayland session"
echo "Wayland session -> X11 window: $(xdotool getwindowname "$win")"
kill "$pid"; wait "$pid" 2>/dev/null
echo "Launch checks OK"
