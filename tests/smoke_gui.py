"""Drives the real window: open a sample, seek, frame-step, grab a frame, open
each tool dialog, run a short remux job. Screenshots go to tests/out/.

Never writes next to the samples or into Pictures: settings live under a test
profile and every output path points at tests/out/.

  .venv\\Scripts\\python.exe tests\\smoke_gui.py "<video file>" ["<second part>"]
"""
import os
sys_ = __import__("sys"); sys_.stdout.reconfigure(encoding="utf-8")
import sys
import time

os.environ["NOUSREEL_SETTINGS"] = "NousReelTest"
HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "out")
sys.path.insert(0, os.path.dirname(HERE))
os.makedirs(OUT, exist_ok=True)

from PySide6.QtCore import QSettings, QTimer
from PySide6.QtWidgets import QApplication, QDialog

QSettings("NousReel", "NousReelTest").clear()
QSettings("NousReel", "NousReelTest").setValue("out_dir", OUT)

import nousreel
import reel_dialogs
import reel_meme

nousreel.pictures_dir = lambda: OUT
reel_meme.halo_exe = lambda: None

video = sys.argv[1]
app = QApplication(sys.argv)
nousreel.apply_theme(app)
w = nousreel.MainWindow()
w.resize(1180, 760)
w.show()
results = []


def shot(name, widget=None):
    widget = widget or w
    QApplication.processEvents()
    scr = widget.screen()
    g = widget.frameGeometry()
    scr.grabWindow(0, g.x(), g.y(), g.width(), g.height()).save(os.path.join(OUT, name + ".png"))


def check(label, ok):
    results.append((label, bool(ok)))
    print(("PASS " if ok else "FAIL ") + label, flush=True)


steps = []


def step(delay, until=None):
    """Run after `delay` ms (counted from the previous step), and if `until` is
    given, not before it returns true (up to a minute). Chained, so a slow step
    under load just pushes the rest back instead of letting them overlap."""
    def deco(fn):
        steps.append((delay, until, fn))
        return fn
    return deco


@step(300)
def open_file():
    w.open_path(video)


@step(300, until=lambda: w.duration > 0 and w.info is not None)
def loaded():
    check("duration known", w.duration > 0)
    check("probe ran", w.info is not None)
    w.player.set("pause", True)
    w.player.seek(min(w.duration * 0.25, 22 * 60 + 58), "absolute+exact")


@step(1200)
def frame_steps():
    w._t0 = w.player.props.get("time-pos")
    w.do("frame_fwd")
    w.do("frame_fwd")


@step(800)
def after_steps():
    t1 = w.player.props.get("time-pos")
    check(f"two frame steps moved ~2 frames ({w._t0:.3f} -> {t1:.3f})",
          t1 and abs((t1 - w._t0) - 2 / w.fps) < 0.5 / w.fps)
    check("paused after stepping", w.player.props.get("pause") is True)
    shot("01_player")


@step(300)
def grab():
    w.grab_frame()


@step(1500)
def grabbed():
    pngs = [f for f in os.listdir(OUT) if f.endswith(".png") and "-" in f and not f[0].isdigit()]
    check("frame grab saved a PNG", pngs)


@step(200)
def info():
    d = reel_dialogs.InfoDialog(w.info, w)
    d.show()
    w._d = d


@step(2500)
def info_shot():
    shot("02_info", w._d)
    w._d.close()
    d = reel_dialogs.ShrinkDialog(w.info, w.queue, w)
    d.dials_btn.setChecked(True)
    d.show()
    w._d = d


@step(800)
def shrink_shot():
    shot("03_shrink", w._d)
    w._d.preset.setCurrentIndex(0)
    w._d.estimate()


@step(500, until=lambda: "Roughly" in w._d.est_lbl.text() or "Couldn't" in w._d.est_lbl.text())
def estimated():
    check("size estimate finished: " + w._d.est_lbl.text(), "Roughly" in w._d.est_lbl.text())
    shot("04_shrink_estimate", w._d)
    w._d.reject()
    d = reel_dialogs.RemuxDialog(w.info, w.queue, w)
    d.show()
    w._d = d


@step(800)
def remux():
    shot("05_tracks", w._d)
    d = w._d
    out = os.path.join(OUT, "remux_test.mkv")
    if os.path.exists(out):
        os.remove(out)
    d.out.edit.setText(out)
    # trim so the test is quick
    from reel_jobs import Job
    import reel_ffcmd as F
    args = F.build_remux(w.info.path, d.choices) + ["-t", "20"]
    w.queue.add(Job("Tracks (20s test)", args, out, 20))
    d.reject()
    w._remux_out = out


@step(500, until=lambda: w.queue.jobs and w.queue.jobs[-1].state in ("done", "failed"))
def remux_done():
    j = w.queue.jobs[-1]
    check(f"remux job finished ({j.state} {j.error[:200]})", j.state == "done" and os.path.exists(w._remux_out))
    shot("06_jobs")


if len(sys.argv) > 2:
    @step(300)
    def join():
        d = reel_dialogs.JoinDialog(video, w.queue, w)
        d.show()
        w._d = d

    @step(1500)
    def join_shot():
        shot("07_join", w._d)
        check("join found the other part(s): " + w._d.status.text()[:80], len(w._d.infos) >= 2)
        w._d.reject()


@step(300)
def play_for_fade():
    w.jobs_dock.hide()
    w.player.set("pause", False)
    QApplication.activeWindow() and None


@step(4000)
def faded():
    check(f"controls faded while playing untouched (opacity {w.fade.opacity():.2f})", w.fade.opacity() < 0.05)
    shot("09_faded")
    w.poke()


@step(500)
def back():
    check(f"controls back after interaction (opacity {w.fade.opacity():.2f})", w.fade.opacity() > 0.95)
    w.player.set("pause", True)


@step(4000)
def stays_when_paused():
    check(f"controls stay while paused (opacity {w.fade.opacity():.2f})", w.fade.opacity() > 0.95)


@step(300)
def meme():
    orig_exec = reel_meme.MemeDialog.exec

    def fake_exec(self):
        self.show()
        self.top.setText("when the frame step")
        self.bottom.setText("finally works")
        self.sliders["fry"].setValue(55)
        self.sliders["jpeg"].setValue(40)
        QApplication.processEvents()
        self.render()
        t = time.time()
        while time.time() - t < 1.0:
            QApplication.processEvents()
        shot("08_meme", self)
        check("meme dialog rendered", self.result.width() > 0)
        self.close()
        return QDialog.Rejected
    reel_meme.MemeDialog.exec = fake_exec
    w.meme()


@step(3000)
def finish():
    w.close()
    bad = [r for r in results if not r[1]]
    print(f"\n{len(results) - len(bad)}/{len(results)} passed", flush=True)
    app.exit(1 if bad else 0)


def run_step(i, waited=0):
    if i >= len(steps):
        return
    delay, until, fn = steps[i]
    if until and not until() and waited < 60_000:
        QTimer.singleShot(250, lambda: run_step(i, waited + 250))
        return
    try:
        fn()
    except Exception as e:  # keep going so one failure doesn't hide the rest
        check(f"{fn.__name__} raised {e!r}", False)
    nxt = steps[i + 1][0] if i + 1 < len(steps) else 0
    QTimer.singleShot(nxt, lambda: run_step(i + 1))


QTimer.singleShot(steps[0][0], lambda: run_step(0))
sys.exit(app.exec())
