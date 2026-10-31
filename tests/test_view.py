"""Picture view options in the real window: Fit / Fill / Stretch, zoom, shape,
rotate, black-bar removal, normal view, and remembering a file's view. Makes
its own clips: a 4:3 one, and a 16:9 one with black bars baked into the picture.
Screenshots of each view go to tests/out/view_*.png.

  .venv\\Scripts\\python.exe tests\\test_view.py
"""
import os
import subprocess
import sys
import time

os.environ["NOUSREEL_SETTINGS"] = "NousReelTest"
HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "out")
sys.path.insert(0, os.path.dirname(HERE))
sys.stdout.reconfigure(encoding="utf-8")
os.makedirs(os.path.join(OUT, "view"), exist_ok=True)

from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication

QSettings("NousReel", "NousReelTest").clear()
import nousreel
from reel_tools_path import find

fails = []


def check(label, ok):
    print(("PASS " if ok else "FAIL ") + label, flush=True)
    if not ok:
        fails.append(label)


def clip(name, vf):
    p = os.path.join(OUT, "view", name)
    if not os.path.exists(p):
        subprocess.run([find("ffmpeg"), "-nostdin", "-v", "error", "-y", "-f", "lavfi", "-i",
                        "testsrc2=s=640x480:r=24:d=20", "-vf", vf, "-c:v", "libx264", "-preset", "veryfast", p],
                       check=True, stdin=subprocess.DEVNULL)
    return p


four3 = clip("four-by-three.mkv", "scale=640:480")
# a 2.39:1 picture inside a 1280x720 frame: 92-pixel black bars top and bottom
boxed = clip("letterboxed.mkv", "scale=1280:536,pad=1280:720:0:92:black")

app = QApplication(sys.argv[:1])
nousreel.apply_theme(app)
w = nousreel.MainWindow()
w.resize(1400, 760)
w.show()


def wait(ms, until=None):
    end = time.time() + ms / 1000
    while time.time() < end:
        app.processEvents()
        if until and until():
            return True
        time.sleep(0.01)
    return bool(until and until())


def prop(name):
    box = {}
    w.player.command("get_property", name, callback=lambda e, d: box.update(e=e, d=d))
    wait(3000, lambda: box)
    return box.get("d")


def picture(name):
    box = {}
    nousreel.window_picture(w, os.path.join(OUT, f"view_{name}.png"), lambda ok: box.setdefault("ok", ok))
    wait(5000, lambda: box)


def open_and_wait(path):
    w.duration = 0
    w.open_path(path)
    wait(15000, lambda: w.duration > 0 and w.info is not None and w.info.path == os.path.normpath(path))
    w.player.set("pause", True)
    w.player.seek(8, "absolute+exact")
    wait(800)


open_and_wait(four3)
check("starts in Fit", w.view.mode == "fit" and prop("keepaspect") is True and prop("panscan") == 0)
picture("fit")
w.do("view_cycle")
wait(400)
check("W -> Fill (panscan 1)", w.view.mode == "fill" and prop("panscan") == 1)
picture("fill")
w.do("view_cycle")
wait(400)
check("W -> Stretch (keepaspect off)", w.view.mode == "stretch" and prop("keepaspect") is False)
picture("stretch")
w.do("view_cycle")
wait(300)
check("W -> back to Fit", w.view.mode == "fit" and prop("keepaspect") is True and prop("panscan") == 0)

w.do("zoom_in")
w.do("zoom_in")
wait(300)
z = prop("video-zoom")
check(f"two zoom steps (video-zoom {z})", z is not None and abs(z - 0.2) < 1e-6)
w.set_view(shape="16:9")
wait(300)
a = prop("video-aspect-override")
check(f"shape 16:9 (video-aspect-override {a})", a is not None and abs(float(a) - 16 / 9) < 0.01)
w.do("rotate")
wait(300)
check(f"rotate 90 (video-rotate {prop('video-rotate')})", prop("video-rotate") == 90)
check("button label shows it: " + w.view_btn.text(), "16:9" in w.view_btn.text() and "90°" in w.view_btn.text())
w.do("view_reset")
wait(300)
check("0 -> normal view", w.view.is_default() and prop("video-zoom") == 0 and prop("video-rotate") == 0)

open_and_wait(boxed)
picture("bars_before")
w.do("bars")
wait(20000, lambda: w.view.crop is not None)
check(f"black bars found: {w.view.crop}", w.view.crop is not None
      and abs(w.view.crop[1] - 536) <= 4 and abs(w.view.crop[3] - 92) <= 4 and w.view.crop[0] >= 1272)
wait(600)
vc = prop("video-crop")
filt = prop("vf")
check(f"crop reached mpv (video-crop {vc!r}, vf {filt})", bool(vc) or bool(filt))
w.set_view(mode="fill")
wait(500)
picture("bars_removed_fill")

open_and_wait(four3)
check("a different file starts in its own (normal) view", w.view.is_default())
open_and_wait(boxed)
check("the boxed file's view was remembered", w.view.crop is not None and w.view.mode == "fill")
w.do("bars")
wait(500)
check("B again puts the bars back", w.view.crop is None and not prop("video-crop") and not prop("vf"))
w.do("view_reset")
wait(300)

w.close()
print(f"\n{'all passed' if not fails else str(len(fails)) + ' failed'}")
sys.exit(1 if fails else 0)
