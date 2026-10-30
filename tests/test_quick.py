"""The right-click '720p for Plex' path, end to end, on generated clips (no real
videos needed): naming, skip reasons, the registry entry (under a test key),
and two copies started at once funnelling into one queue.

  .venv\\Scripts\\python.exe tests\\test_quick.py
"""
import os
import subprocess
import sys
import time
import winreg

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
OUT = os.path.join(HERE, "out", "quick")
sys.path.insert(0, ROOT)
sys.stdout.reconfigure(encoding="utf-8")
os.makedirs(OUT, exist_ok=True)

import reel_quick as Q
import reel_shell as S
from reel_media import probe
from reel_tools_path import find

fails = []


def check(label, ok):
    print(("PASS " if ok else "FAIL ") + label, flush=True)
    if not ok:
        fails.append(label)


# naming
for src, want in [(r"C:\tv\The Show - S02E05 - Pilot.m4v", r"C:\tv\The Show - S02E05 - Pilot - 720p.mkv"),
                  (r"C:\m\Chop Shop.2007.1080p.mkv", r"C:\m\Chop Shop (2007) - 720p.mkv"),
                  (r"C:\tv\Show.s01e02.1080p.mkv", r"C:\tv\Show.s01e02 - 720p.mkv")]:
    got = Q.quick_output(src)
    check(f"quick_output -> {os.path.basename(got)}", got == want)


def clip(name, w, h, seconds=6, rate="8M"):
    path = os.path.join(OUT, name)
    if not os.path.exists(path):
        subprocess.run([find("ffmpeg"), "-nostdin", "-v", "error", "-y", "-f", "lavfi", "-i",
                        f"testsrc2=s={w}x{h}:r=24000/1001:d={seconds}", "-f", "lavfi", "-i",
                        f"sine=f=440:d={seconds}", "-c:v", "libx264", "-b:v", rate, "-c:a", "ac3",
                        "-shortest", path], check=True, stdin=subprocess.DEVNULL)
    return path


for f in os.listdir(OUT):
    if " - 720p" in f:
        os.remove(os.path.join(OUT, f))
a = clip("Test Show - S01E01 - One.mp4", 1920, 1080)
b = clip("Test Show - S01E02 - Two.mp4", 1920, 1080)
small = clip("Small.2001.mp4", 640, 360, rate="800k")

check("small file is skipped with a reason", "already 360p" in (Q.check(probe(small)) or ""))
check("1080p file is converted", Q.check(probe(a)) is None)

# registry under a throwaway key
TEST_BASE = r"Software\NousReelTest\Classes\SystemFileAssociations"
S.uninstall(TEST_BASE)
check("not installed at first", not S.is_installed(TEST_BASE))
S.install(TEST_BASE, icon="test.ico")
with winreg.OpenKey(winreg.HKEY_CURRENT_USER, S._key(".m4v", TEST_BASE) + r"\command") as k:
    cmd = winreg.QueryValueEx(k, "")[0]
check("installed: " + cmd, S.is_installed(TEST_BASE) and "--plex720" in cmd and cmd.endswith('"%1"'))
S.uninstall(TEST_BASE)
check("uninstall removes it", not S.is_installed(TEST_BASE))
try:
    winreg.DeleteKey(winreg.HKEY_CURRENT_USER, r"Software\NousReelTest\Classes\SystemFileAssociations")
except OSError:
    pass

# two copies at once, as Explorer does for a two-file selection
env = dict(os.environ, NOUSREEL_QUICK_EXIT_WHEN_DONE="1", NOUSREEL_SETTINGS="NousReelTest")
cmd = [sys.executable, os.path.join(ROOT, "nousreel.py"), "--plex720"]
t = time.time()
p1 = subprocess.Popen(cmd + [a], env=env)
p2 = subprocess.Popen(cmd + [b], env=env)
r2 = p2.wait(120)
r1 = p1.wait(240)
check(f"both copies exited cleanly ({r1}, {r2}) in {time.time() - t:.0f}s", r1 == 0 and r2 == 0)
outs = [Q.quick_output(a).replace(" (2)", ""), Q.quick_output(b).replace(" (2)", "")]
outs = [os.path.join(OUT, os.path.basename(a)[:-4] + " - 720p.mkv"),
        os.path.join(OUT, os.path.basename(b)[:-4] + " - 720p.mkv")]
for o in outs:
    ok = os.path.exists(o)
    info = probe(o) if ok else None
    check(f"{os.path.basename(o)} exists and is 720p H.265",
          ok and info.video.height == 720 and info.video.codec == "hevc")
check("no leftover .partial files", not [f for f in os.listdir(OUT) if f.endswith(".partial")])

print(f"\n{'all passed' if not fails else str(len(fails)) + ' failed'}")
sys.exit(1 if fails else 0)
