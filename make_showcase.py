"""Makes the website screenshots from His Girl Friday (1940, public domain):
the player paused on the 22:58 restaurant frame, the meme maker on that frame,
and Shrink with its dials showing. Writes site/public/screenshot-*.png.

  .venv\\Scripts\\python.exe make_showcase.py "<His Girl Friday video>"
"""
import os
import sys
import time

os.environ["NOUSREEL_SETTINGS"] = "NousReelShowcase"
HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "site", "public")
sys.path.insert(0, HERE)

from PySide6.QtCore import QSettings, Qt, QTimer
from PySide6.QtGui import QImage
from PySide6.QtWidgets import QApplication

QSettings("NousReel", "NousReelShowcase").clear()
import nousreel
import reel_dialogs
import reel_meme

video = sys.argv[1]
app = QApplication(sys.argv[:1])
nousreel.apply_theme(app)
w = nousreel.MainWindow()
w.resize(1440, 900)
w.show()


def wait(ms):
    end = time.time() + ms / 1000
    while time.time() < end:
        app.processEvents()
        time.sleep(0.01)


def grab_main(path):
    # never a screen grab: Qt draws the window and mpv supplies the video
    box = {}
    nousreel.window_picture(w, path, lambda ok: box.setdefault("ok", ok))
    for _ in range(100):
        if box:
            break
        wait(50)
    return box.get("ok")


def run():
    w.open_path(video)
    for _ in range(100):
        if w.duration and w.info:
            break
        wait(100)
    w.player.set("pause", True)
    w.player.seek(1378.0, "absolute+exact")
    wait(1500)
    w.poke()
    wait(300)
    grab_main(os.path.join(OUT, "screenshot.png"))

    frame = os.path.join(HERE, "build", "showcase-frame.png")
    os.makedirs(os.path.dirname(frame), exist_ok=True)
    got = {}
    w.player.screenshot(frame, callback=lambda e, d: got.setdefault("e", e))
    for _ in range(50):
        if got:
            break
        wait(100)
    d = reel_meme.MemeDialog(QImage(frame), "His Girl Friday", OUT, w)
    d.resize(1280, 760)
    d.show()
    d.top.setText("when the dialogue")
    d.bottom.setText("is faster than the subtitles")
    d.sliders["fry"].setValue(35)
    d.sliders["jpeg"].setValue(35)
    d.sliders["crunch"].setValue(15)
    wait(400)
    d.render()
    wait(300)
    d.grab().save(os.path.join(OUT, "screenshot-meme.png"))
    d.close()

    s = reel_dialogs.ShrinkDialog(w.info, w.queue, w)
    s.dials_btn.setChecked(True)
    s.resize(1320, 820)
    # a neutral example instead of this computer's real folders and user name
    s.out.edit.setText(r"C:\Users\you\Videos\His Girl Friday (1940).mkv")
    s.show()
    wait(500)
    s.grab().save(os.path.join(OUT, "screenshot-shrink.png"))
    s.reject()
    w.close()
    app.quit()


QTimer.singleShot(500, run)
app.exec()
for n in ("screenshot.png", "screenshot-meme.png", "screenshot-shrink.png"):
    p = os.path.join(OUT, n)
    print(("saved " if os.path.exists(p) else "MISSING ") + p)
