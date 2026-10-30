"""Finding ffmpeg, ffprobe and mpv on this machine."""
import glob
import os
import shutil
import sys

HERE = os.path.dirname(os.path.abspath(__file__))


def _bundled(name):
    # A frozen build ships its helpers in bin\ next to the exe
    for base in (getattr(sys, "_MEIPASS", ""), os.path.dirname(sys.executable), HERE):
        p = os.path.join(base, "bin", name + ".exe")
        if base and os.path.isfile(p):
            return p
    return None


def _winget(pattern):
    root = os.path.join(os.environ.get("LOCALAPPDATA", ""), "Microsoft", "WinGet", "Packages")
    hits = sorted(glob.glob(os.path.join(root, pattern)))
    return hits[-1] if hits else None


def find(name):
    """Full path to ffmpeg / ffprobe / mpv, or None."""
    env = os.environ.get("NOUSREEL_" + name.upper())
    if env and os.path.isfile(env):
        return env
    p = _bundled(name) or shutil.which(name)
    if p:
        return p
    if name in ("ffmpeg", "ffprobe"):
        return _winget(f"Gyan.FFmpeg*\\*\\bin\\{name}.exe")
    if name == "mpv":
        for base in (os.environ.get("ProgramFiles", r"C:\Program Files"),
                     os.environ.get("LOCALAPPDATA", "")):
            for sub in ("MPV Player", "mpv", os.path.join("Programs", "mpv")):
                p = os.path.join(base, sub, "mpv.exe")
                if os.path.isfile(p):
                    return p
    return None


def no_window():
    """subprocess flags so helper consoles never flash up on Windows."""
    return 0x08000000 if os.name == "nt" else 0  # CREATE_NO_WINDOW
