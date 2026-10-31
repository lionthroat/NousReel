# Nous Reel

A video player for Windows that can also fix, join and shrink video files, and
turn any frame into a properly ruined meme.

It plays with **mpv** and does everything else with **ffmpeg**, both free and
open source. Nous Reel is the friendly part on top: plain-English explanations,
presets for the common jobs, and every dial visible when you want to learn
what's going on.

## What it does

**Play.** Opens anything mpv can (MKV, MP4, AVI, DVD rips, old formats).
Resumes where you left off. Choose subtitle and sound tracks from the bar, or
drop an `.srt` onto the window.

The controls fade out after a few seconds of playing and come back when you
move the mouse, scroll, or press a key. They stay up while paused.

**720p for Plex from the right-click menu.** Turn it on under *Tools ▸ “720p
for Plex” in the right-click menu*, then right-click any video in File
Explorer and choose **720p for Plex (Nous Reel)**. A small window shows
progress and you can carry on with your day. Select several videos to queue
them all. The new file is saved next to the original as `… - 720p.mkv`, which
Plex treats as another version of the same title. Files that are already
small are skipped, and copy-protected files (iTunes / Apple TV purchases) are
explained rather than attempted. On Windows 11 the entry is under *Show more
options*, or hold **Shift** while right-clicking to go straight to it. It's
added for your user account only and is removed when you untick it.

**Step through frames.** `,` and `.` move one frame back or forward. The time
display shows the exact frame number.

**Meme this frame** (`M`). Grabs the exact frame at full resolution and opens
the meme maker:
- crop, with aspect presets (square, 4:5, 9:16…)
- ruin it: Crunch, JPEG rot, Deep fry (black-and-white goes orange), Noise,
  Blur, Color bleed, Glitch, Crooked, Bad crop
- Chaos (random ruin), Reroll (same settings, new randomness), Clean
- captions: classic Impact top/bottom, or a white bar with black text
- Save, Copy (paste straight into a chat), or Open in Nous Halo

`G` saves a clean frame to `Pictures\Nous Reel`.

**Tools menu**
- **Check this file for damage**: decodes every frame of the picture and
  sound (an 84-minute film takes a couple of minutes) and lists each trouble
  spot with its timestamp and a Jump button. Good for checking a rip from a
  thrifted disc, a download, or a file that ends early.
- **What's inside this file**: the container, every track in plain English,
  and a bar showing where the bytes go (why is this file 23 GB?)
- **Tracks & subtitles**: keep or drop tracks, fix language labels, pick the
  default, build `.srt` files into the video. Lossless, takes seconds.
- **Shrink**: presets ("Plex 720p · small", "Old film or DVD rip cleanup"…)
  or the dials: codec, quality, encoder effort, resolution, deinterlace, noise
  smoothing, film grain, sound. Each dial explains itself, **Estimate the size**
  encodes three samples to predict the result, and the ffmpeg command is one
  click away.
- **Join parts**: stitches CD1/CD2 rips into one file. Matching parts join
  losslessly; mismatched parts are re-encoded so they fit together.

New files are written next to the original as `Title (Year).mkv`, the name Plex
looks for. Originals are never touched or replaced. A job writes to a
`.partial` file and renames it only when ffmpeg finishes, so a cancelled job
never leaves a half-file behind.

## Keys

| Key | |
|---|---|
| Space / K | Play / pause |
| `,` `.` | One frame back / forward |
| ← → | 5 seconds (Shift: 1 s, J/L: 10 s) |
| ↑ ↓, wheel | Volume |
| F, double-click | Full screen (Esc leaves) |
| M | Meme this frame |
| G | Save this frame |
| S / A / V | Next subtitle / next sound track / subtitles on-off |
| [ ] Backspace | Slower / faster / normal speed |
| I | What's inside this file |

## Running it

Needs Python 3.12+, plus mpv and ffmpeg:

```
winget install Gyan.FFmpeg
winget install shinchiro.mpv
python -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
.venv\Scripts\python.exe nousreel.py
```

Nous Reel finds mpv and ffmpeg on PATH, in their winget install folders, or
from `NOUSREEL_MPV` / `NOUSREEL_FFMPEG` / `NOUSREEL_FFPROBE`.

## Tests

```
.venv\Scripts\python.exe tests\test_ffcmd.py
.venv\Scripts\python.exe tests\test_quick.py
.venv\Scripts\python.exe tests\test_damage.py "<a video>"
.venvScriptspython.exe tests	est_damage.py "<a video>"
.venv\Scripts\python.exe tests\smoke_gui.py "<a video>" ["join"]
.venv\Scripts\python.exe tests\full_runs.py join "<part 1>" "<part 2>"
```

`test_ffcmd.py` checks the command builders (no video needed). `smoke_gui.py`
drives the real window and saves screenshots to `tests\out\`. It uses its own
settings profile and writes nothing next to the video or into Pictures.

## Files

| File | |
|---|---|
| `nousreel.py` | Main window, transport, keys, frame grabs |
| `reel_player.py` | mpv drawing into the window, driven over its IPC pipe |
| `reel_media.py` | ffprobe reading, track descriptions, Plex names |
| `reel_ffcmd.py` | ffmpeg command builders, presets, dial explanations |
| `reel_jobs.py` | Job queue with progress, and the Jobs panel |
| `reel_dialogs.py` | What's inside, Tracks & subtitles, Shrink, Join |
| `reel_meme.py` | Meme maker and the ruin filters |
| `reel_damage.py` | Check this file for damage |
| `reel_quick.py` | One-step 720p for Plex window and its single queue |
| `reel_shell.py` | The Explorer right-click entry (per user) |
