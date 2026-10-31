"""Nous Reel: a video player that can also shrink, join and fix the tracks of a
file, and turn any frame into a deliberately ruined meme."""
import json
import os
import sys
import tempfile

from PySide6.QtCore import QEvent, QPropertyAnimation, QRectF, QSettings, QSize, QStandardPaths, Qt, QTimer
from PySide6.QtGui import (QAction, QColor, QIcon, QImage, QKeySequence, QPainter, QPalette, QPen,
                           QPixmap, QShortcut)
from PySide6.QtWidgets import (QApplication, QDockWidget, QFileDialog, QGraphicsOpacityEffect, QHBoxLayout, QLabel,
                               QMainWindow, QMenu, QMessageBox, QSlider, QStyle, QToolButton,
                               QVBoxLayout, QWidget)

from reel_dialogs import InfoDialog, JoinDialog, RemuxDialog, ShrinkDialog
from reel_jobs import JobQueue, JobsPanel
from reel_media import PROTECTED_MSG, ProbeError, fmt_time, plex_name, probe
from reel_meme import MemeDialog
from reel_player import MpvWidget
from reel_tools_path import find

APP = "Nous Reel"
HERE = os.path.dirname(os.path.abspath(__file__))
VIDEO_FILTER = ("Videos (*.mkv *.mp4 *.m4v *.avi *.mov *.webm *.wmv *.mpg *.mpeg *.ts *.m2ts *.vob *.flv *.ogv);;"
                "All files (*)")

# action, Qt shortcut, mpv input name (forwarded from the video surface)
KEYS = [
    ("play", "Space", "SPACE"), ("play", "K", "k"),
    ("back5", "Left", "LEFT"), ("fwd5", "Right", "RIGHT"),
    ("back1", "Shift+Left", "Shift+LEFT"), ("fwd1", "Shift+Right", "Shift+RIGHT"),
    ("back10", "J", "j"), ("fwd10", "L", "l"),
    ("frame_back", ",", ","), ("frame_fwd", ".", "."),
    ("vol_up", "Up", "UP"), ("vol_down", "Down", "DOWN"),
    ("fullscreen", "F", "f"), ("exit_fs", "Esc", "ESC"),
    ("meme", "M", "m"), ("grab", "G", "g"),
    ("cycle_sub", "S", "s"), ("cycle_audio", "A", "a"), ("toggle_sub", "V", "v"),
    ("slower", "[", "["), ("faster", "]", "]"), ("speed_reset", "Backspace", "BS"),
    ("start", "Home", "HOME"), ("info", "I", "i"),
]
HELP = [
    ("Space or K", "Play / pause"), (", and .", "Step one frame back / forward (pauses)"),
    ("← →", "Back / forward 5 seconds"), ("Shift + ← →", "1 second"), ("J and L", "10 seconds"),
    ("↑ ↓ or mouse wheel", "Volume"), ("F or double-click", "Full screen (Esc leaves)"),
    ("M", "Meme this frame"), ("G", "Save this frame as a picture"),
    ("S / A", "Next subtitle / sound track"), ("V", "Subtitles on / off"),
    ("[ and ]", "Slower / faster (Backspace resets)"), ("Home", "Back to the start"),
    ("I", "What's inside this file"),
]


def version():
    for base in (getattr(sys, "_MEIPASS", ""), HERE):
        try:
            with open(os.path.join(base, "VERSION"), encoding="utf-8") as f:
                return f.read().strip()
        except OSError:
            pass
    return "0.1.0"


def settings():
    return QSettings("NousReel", os.environ.get("NOUSREEL_SETTINGS", "NousReel"))


def app_icon():
    ico = os.path.join(getattr(sys, "_MEIPASS", HERE), "icon.ico")
    if os.path.exists(ico):
        return QIcon(ico)
    pm = QPixmap(64, 64)
    pm.fill(Qt.transparent)
    qp = QPainter(pm)
    qp.setRenderHint(QPainter.Antialiasing)
    qp.setBrush(QColor("#5e81ac"))
    qp.setPen(Qt.NoPen)
    qp.drawEllipse(4, 4, 56, 56)
    qp.setBrush(QColor("#2e3440"))
    for x, y in ((32, 16), (32, 48), (16, 32), (48, 32)):
        qp.drawEllipse(x - 7, y - 7, 14, 14)
    qp.drawEllipse(28, 28, 8, 8)
    qp.end()
    return QIcon(pm)


def glyph(kind, size=20, color="#e5e9f0"):
    """Transport icons drawn with QPainter so they're crisp and evenly sized
    (emoji glyphs render at different sizes and colours on Windows)."""
    from PySide6.QtCore import QPointF
    from PySide6.QtGui import QPolygonF
    pm = QPixmap(size * 2, size * 2)
    pm.setDevicePixelRatio(2)
    pm.fill(Qt.transparent)
    qp = QPainter(pm)
    qp.setRenderHint(QPainter.Antialiasing)
    qp.setPen(Qt.NoPen)
    qp.setBrush(QColor(color))
    u = size / 20

    def tri(x0, y0, x1, y1, x2, y2):
        qp.drawPolygon(QPolygonF([QPointF(x0 * u, y0 * u), QPointF(x1 * u, y1 * u), QPointF(x2 * u, y2 * u)]))

    def bar(x, y, w, h):
        qp.drawRect(QRectF(x * u, y * u, w * u, h * u))
    if kind == "play":
        tri(6, 3, 6, 17, 17, 10)
    elif kind == "pause":
        bar(5, 4, 3.5, 12); bar(11.5, 4, 3.5, 12)
    elif kind == "open":
        tri(10, 3, 3, 11, 17, 11); bar(3, 13.5, 14, 3)
    elif kind == "back10":
        tri(10, 4, 10, 16, 2, 10); tri(18, 4, 18, 16, 10, 10)
    elif kind == "fwd10":
        tri(2, 4, 2, 16, 10, 10); tri(10, 4, 10, 16, 18, 10)
    elif kind == "frame_back":
        bar(4, 4, 2.5, 12); tri(16, 4, 16, 16, 7.5, 10)
    elif kind == "frame_fwd":
        tri(4, 4, 4, 16, 12.5, 10); bar(13.5, 4, 2.5, 12)
    elif kind in ("vol", "mute"):
        bar(3, 7.5, 3.5, 5); tri(5, 10, 11, 3.5, 11, 16.5)
        pen = QPen(QColor(color), 1.6 * u)
        qp.setPen(pen)
        qp.setBrush(Qt.NoBrush)
        if kind == "vol":
            qp.drawArc(QRectF(8 * u, 6 * u, 7 * u, 8 * u), -60 * 16, 120 * 16)
            qp.drawArc(QRectF(8 * u, 3 * u, 10 * u, 14 * u), -60 * 16, 120 * 16)
        else:
            qp.drawLine(QPointF(13 * u, 7 * u), QPointF(18 * u, 13 * u))
            qp.drawLine(QPointF(18 * u, 7 * u), QPointF(13 * u, 13 * u))
    elif kind == "fullscreen":
        pen = QPen(QColor(color), 2 * u)
        qp.setPen(pen)
        qp.setBrush(Qt.NoBrush)
        for (x, y, dx, dy) in ((3, 3, 1, 1), (17, 3, -1, 1), (3, 17, 1, -1), (17, 17, -1, -1)):
            qp.drawLine(QPointF(x * u, y * u), QPointF((x + 5 * dx) * u, y * u))
            qp.drawLine(QPointF(x * u, y * u), QPointF(x * u, (y + 5 * dy) * u))
    qp.end()
    return QIcon(pm)


def pictures_dir():
    base = QStandardPaths.writableLocation(QStandardPaths.PicturesLocation) or os.path.expanduser("~")
    return os.path.join(base, "Nous Reel")


class SeekSlider(QSlider):
    """Clicking anywhere jumps there (instead of paging), like every other player."""
    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton:
            v = QStyle.sliderValueFromPosition(self.minimum(), self.maximum(), int(e.position().x()), self.width())
            self.setValue(v)
            self.sliderMoved.emit(v)
        super().mousePressEvent(e)


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowIcon(app_icon())
        self.setAcceptDrops(True)
        self.resize(1180, 760)
        self.path = None
        self.info = None
        self.duration = 0.0
        self.fps = 24.0
        self.dragging = False
        self.queue = JobQueue()

        self.player = MpvWidget()
        self.player.prop.connect(self.on_prop)
        self.player.key.connect(self.on_mpv_key)
        self.player.failed.connect(lambda m: QMessageBox.critical(self, APP, m))
        self.player.file_loaded.connect(self.on_loaded)

        self.controls = self._build_controls()
        self.controls.setAttribute(Qt.WA_StyledBackground, True)
        # Idle fade: the bar keeps its space (so the picture never jumps) and
        # fades to the black behind it
        self.fade = QGraphicsOpacityEffect(self.controls)
        self.fade.setOpacity(1.0)
        self.controls.setGraphicsEffect(self.fade)
        self.fade_anim = QPropertyAnimation(self.fade, b"opacity", self)
        self.idle_timer = QTimer(self, singleShot=True, interval=2500, timeout=self._idle)
        central = QWidget()
        central.setObjectName("central")
        central.setAttribute(Qt.WA_StyledBackground, True)
        lay = QVBoxLayout(central)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        lay.addWidget(self.player, 1)
        lay.addWidget(self.controls)
        self.setCentralWidget(central)

        self.jobs = JobsPanel(self.queue, self.open_path)
        self.jobs_dock = QDockWidget("Jobs", self)
        self.jobs_dock.setObjectName("jobs")
        self.jobs_dock.setWidget(self.jobs)
        self.addDockWidget(Qt.BottomDockWidgetArea, self.jobs_dock)
        self.jobs_dock.hide()
        self.queue.added.connect(lambda j: self.jobs_dock.show())
        self.queue.added.connect(lambda j: j.finished.connect(lambda ok, j=j: self._job_finished(j, ok)))

        self._build_menus()
        for action, seq, _ in KEYS:
            sc = QShortcut(QKeySequence(seq), self)
            sc.activated.connect(lambda a=action: self.do(a))
        self.hide_timer = QTimer(self, singleShot=True, interval=2500, timeout=self._hide_fs_controls)
        self.save_timer = QTimer(self, interval=10_000, timeout=self._remember_position)
        self.save_timer.start()
        self.update_title()
        geo = settings().value("geometry")
        if geo is not None:
            self.restoreGeometry(geo)
        QApplication.instance().installEventFilter(self)
        QTimer.singleShot(0, self.player.start)

    # ---- idle fade -----------------------------------------------------------
    def eventFilter(self, obj, e):
        if e.type() in (QEvent.MouseMove, QEvent.HoverMove, QEvent.Wheel, QEvent.MouseButtonPress) \
                and isinstance(obj, QWidget) and obj.window() is self:
            self.poke()
        return False

    def poke(self):
        """Any interaction: bring the controls back and restart the idle clock."""
        if self.isFullScreen():
            self._show_fs_controls()
        self._fade_to(1.0, 120)
        self.idle_timer.start()

    def _idle(self):
        if self.isFullScreen():
            return
        busy = (self.player.props.get("pause") is not False or not self.path or self.dragging
                or self.controls.underMouse() or QApplication.activePopupWidget() is not None)
        if busy:
            # stays up while paused (frame stepping), with a menu open, or with the mouse on it
            self.idle_timer.start()
            return
        self._fade_to(0.0, 450)

    def _fade_to(self, value, ms):
        if abs(self.fade.opacity() - value) < 1e-3 and self.fade_anim.state() != QPropertyAnimation.Running:
            return
        if self.fade_anim.endValue() == value and self.fade_anim.state() == QPropertyAnimation.Running:
            return
        self.fade_anim.stop()
        self.fade_anim.setDuration(ms)
        self.fade_anim.setStartValue(self.fade.opacity())
        self.fade_anim.setEndValue(value)
        self.fade_anim.start()

    # ---- layout ----------------------------------------------------------
    def _build_controls(self):
        bar = QWidget()
        bar.setObjectName("controls")
        v = QVBoxLayout(bar)
        v.setContentsMargins(10, 6, 10, 8)
        v.setSpacing(4)
        self.seek = SeekSlider(Qt.Horizontal)
        self.seek.setRange(0, 10_000)
        self.seek.setFocusPolicy(Qt.NoFocus)
        self.seek.sliderPressed.connect(lambda: setattr(self, "dragging", True))
        self.seek.sliderReleased.connect(self._seek_released)
        self.seek.sliderMoved.connect(self._seek_moved)
        v.addWidget(self.seek)
        row = QHBoxLayout()
        row.setSpacing(4)

        def btn(kind, tip, slot, w=34):
            b = QToolButton()
            b.setIcon(glyph(kind))
            b.setIconSize(QSize(20, 20))
            b.setToolTip(tip)
            b.setFixedSize(w, 30)
            b.setFocusPolicy(Qt.NoFocus)   # so Space and arrows always reach the player
            b.clicked.connect(slot)
            row.addWidget(b)
            return b

        btn("open", "Open a video (Ctrl+O)", self.open_dialog)
        btn("back10", "Back 10 seconds (J)", lambda: self.do("back10"))
        btn("frame_back", "One frame back ( , )", lambda: self.do("frame_back"))
        self.play_btn = btn("play", "Play / pause (Space)", lambda: self.do("play"), 42)
        btn("frame_fwd", "One frame forward ( . )", lambda: self.do("frame_fwd"))
        btn("fwd10", "Forward 10 seconds (L)", lambda: self.do("fwd10"))
        self.time_lbl = QLabel("--:--")
        self.time_lbl.setStyleSheet("font-family: Consolas, monospace; padding-left: 8px;")
        row.addWidget(self.time_lbl)
        row.addStretch(1)
        self.speed_lbl = QLabel("")
        row.addWidget(self.speed_lbl)
        self.sub_btn = QToolButton()
        self.sub_btn.setText("Subtitles")
        self.sub_btn.setPopupMode(QToolButton.InstantPopup)
        self.sub_btn.setFocusPolicy(Qt.NoFocus)
        self.sub_menu = QMenu(self.sub_btn)
        self.sub_btn.setMenu(self.sub_menu)
        row.addWidget(self.sub_btn)
        self.audio_btn = QToolButton()
        self.audio_btn.setText("Sound")
        self.audio_btn.setPopupMode(QToolButton.InstantPopup)
        self.audio_btn.setFocusPolicy(Qt.NoFocus)
        self.audio_menu = QMenu(self.audio_btn)
        self.audio_btn.setMenu(self.audio_menu)
        row.addWidget(self.audio_btn)
        self.mute_btn = btn("vol", "Mute", lambda: self.player.command("cycle", "mute"))
        self.vol = QSlider(Qt.Horizontal)
        self.vol.setRange(0, 130)
        self.vol.setFixedWidth(100)
        self.vol.setFocusPolicy(Qt.NoFocus)
        self.vol.setValue(int(settings().value("volume", 100)))
        self.vol.valueChanged.connect(lambda x: self.player.set("volume", x))
        row.addWidget(self.vol)
        meme = QToolButton()
        meme.setText("  Meme this frame  ")
        meme.setToolTip("Grab the exact frame on screen and open the meme maker (M)")
        meme.setObjectName("meme")
        meme.setFixedHeight(30)
        meme.setFocusPolicy(Qt.NoFocus)
        meme.clicked.connect(lambda: self.do("meme"))
        row.addWidget(meme)
        btn("fullscreen", "Full screen (F)", lambda: self.do("fullscreen"))
        v.addLayout(row)
        self._rebuild_track_menus()
        return bar

    def _build_menus(self):
        mb = self.menuBar()
        m = mb.addMenu("&File")
        self._act(m, "Open…", self.open_dialog, QKeySequence.Open)
        self.recent_menu = m.addMenu("Open recent")
        self._rebuild_recent()
        m.addSeparator()
        self._act(m, "Save this frame as a picture", lambda: self.do("grab"))
        self._act(m, "Meme this frame…", lambda: self.do("meme"))
        m.addSeparator()
        self._act(m, "Quit", self.close, QKeySequence.Quit)
        t = mb.addMenu("&Tools")
        self._act(t, "What's inside this file…", lambda: self.do("info"))
        self._act(t, "Check this file for damage…", self.tool_damage)
        t.addSeparator()
        self._act(t, "Tracks && subtitles…", self.tool_remux)
        self._act(t, "Shrink…", self.tool_shrink)
        self._act(t, "Join parts…", self.tool_join)
        t.addSeparator()
        self._act(t, "Show jobs", lambda: self.jobs_dock.show())
        if os.name == "nt":
            import reel_shell
            t.addSeparator()
            rc = self._act(t, "“720p for Plex” in the right-click menu", self.toggle_shell_menu)
            rc.setCheckable(True)
            rc.setChecked(reel_shell.is_installed())
            rc.setToolTip("Right-click any video in File Explorer to shrink it to 720p in one step")
        v = mb.addMenu("&View")
        self._act(v, "Full screen", lambda: self.do("fullscreen"))
        top = self._act(v, "Always on top", self.toggle_on_top)
        top.setCheckable(True)
        h = mb.addMenu("&Help")
        self._act(h, "Keyboard shortcuts", self.show_keys, "F1")
        self._act(h, "About Nous Reel", self.about)

    def _act(self, menu, text, slot, shortcut=None):
        a = QAction(text, self)
        a.triggered.connect(slot)
        if shortcut:
            a.setShortcut(QKeySequence(shortcut))
        menu.addAction(a)
        return a

    # ---- files -----------------------------------------------------------
    def open_dialog(self):
        path, _ = QFileDialog.getOpenFileName(self, "Open video", settings().value("last_dir", ""), VIDEO_FILTER)
        if path:
            self.open_path(path)

    def open_path(self, path):
        if not os.path.exists(path):
            QMessageBox.warning(self, APP, f"{path} doesn't exist any more.")
            return
        self._remember_position()
        path = os.path.normpath(path)
        self.path = path
        self.info = None
        settings().setValue("last_dir", os.path.dirname(path))
        self._add_recent(path)
        self.player.load(path)
        self.player.set("pause", False)
        self.update_title()

    def on_loaded(self):
        try:
            self.info = probe(self.path)
        except ProbeError:
            self.info = None
        if self.info and self.info.protected:
            QMessageBox.warning(self, APP, PROTECTED_MSG.format(name=os.path.basename(self.path)))
        v = self.info.video if self.info else None
        self.fps =v.fps if v and v.fps else (self.player.props.get("container-fps") or 24.0)
        pos = self._positions().get(self.path)
        if pos:
            self.player.seek(pos, "absolute")
            self.player.osd(f"Resumed at {fmt_time(pos, frac=False)}  (Home goes back to the start)", 2500)

    def _positions(self):
        try:
            return json.loads(settings().value("positions", "") or "{}")
        except ValueError:
            return {}

    def _remember_position(self):
        """Resume where you left off: remembered after the first minute, forgotten near the end."""
        if not self.path or not self.duration:
            return
        t = self.player.props.get("time-pos") or 0
        pos = self._positions()
        if 60 < t < self.duration - 180:
            pos[self.path] = round(t, 1)
        else:
            pos.pop(self.path, None)
        while len(pos) > 200:
            pos.pop(next(iter(pos)))
        settings().setValue("positions", json.dumps(pos))

    def _add_recent(self, path):
        rec = [p for p in (settings().value("recent", []) or []) if p != path]
        if isinstance(rec, str):
            rec = [rec]
        rec.insert(0, path)
        settings().setValue("recent", rec[:12])
        self._rebuild_recent()

    def _rebuild_recent(self):
        self.recent_menu.clear()
        rec = settings().value("recent", []) or []
        if isinstance(rec, str):
            rec = [rec]
        for p in rec:
            a = self.recent_menu.addAction(os.path.basename(p))
            a.setToolTip(p)
            a.triggered.connect(lambda _=False, p=p: self.open_path(p))
        self.recent_menu.setEnabled(bool(rec))

    def dragEnterEvent(self, e):
        if e.mimeData().hasUrls():
            e.acceptProposedAction()

    def dropEvent(self, e):
        urls = [u.toLocalFile() for u in e.mimeData().urls() if u.isLocalFile()]
        subs = [u for u in urls if os.path.splitext(u)[1].lower() in (".srt", ".ass", ".ssa", ".vtt", ".sup")]
        vids = [u for u in urls if u not in subs]
        if vids:
            self.open_path(vids[0])
        for s in subs:
            self.player.command("sub-add", s, "select")
            self.player.osd(f"Loaded subtitles: {os.path.basename(s)}")

    # ---- mpv state -----------------------------------------------------------
    def on_prop(self, name, value):
        if name == "duration":
            self.duration = float(value or 0)
        elif name == "time-pos":
            self._update_time(value)
        elif name == "pause":
            self.play_btn.setIcon(glyph("play" if value else "pause"))
            self.poke()
        elif name == "track-list":
            self._rebuild_track_menus()
        elif name in ("sid", "aid", "sub-visibility"):
            self._rebuild_track_menus()
        elif name == "volume" and value is not None:
            self.vol.blockSignals(True)
            self.vol.setValue(int(value))
            self.vol.blockSignals(False)
            settings().setValue("volume", int(value))
        elif name == "mute":
            self.mute_btn.setIcon(glyph("mute" if value else "vol"))
        elif name == "speed":
            self.speed_lbl.setText("" if not value or abs(value - 1) < 1e-3 else f"{value:g}×  ")
        elif name == "path" and value is None:
            self.time_lbl.setText("--:--")

    def _update_time(self, t):
        if t is None:
            return
        frame = int(round(t * self.fps)) if self.fps else 0
        self.time_lbl.setText(f"{fmt_time(t)} / {fmt_time(self.duration, frac=False)}   frame {frame:,}")
        if self.duration and not self.dragging:
            self.seek.blockSignals(True)
            self.seek.setValue(int(t / self.duration * 10_000))
            self.seek.blockSignals(False)

    def _seek_moved(self, v):
        if self.duration:
            self.player.seek(v / 10_000 * self.duration, "absolute+keyframes")

    def _seek_released(self):
        self.dragging = False
        if self.duration:
            self.player.seek(self.seek.value() / 10_000 * self.duration, "absolute+exact")

    def _rebuild_track_menus(self):
        self.sub_menu.clear()
        self.audio_menu.clear()
        sid = self.player.props.get("sid")
        aid = self.player.props.get("aid")
        off = self.sub_menu.addAction("Off")
        off.setCheckable(True)
        off.setChecked(not sid)
        off.triggered.connect(lambda: self.player.set("sid", "no"))
        for t in self.player.tracks("sub"):
            a = self.sub_menu.addAction(_track_label(t))
            a.setCheckable(True)
            a.setChecked(t.get("selected", False))
            a.triggered.connect(lambda _=False, i=t["id"]: self.player.set("sid", i))
        self.sub_menu.addSeparator()
        self.sub_menu.addAction("Load a subtitle file…", self._load_sub)
        for t in self.player.tracks("audio"):
            a = self.audio_menu.addAction(_track_label(t))
            a.setCheckable(True)
            a.setChecked(t.get("selected", False))
            a.triggered.connect(lambda _=False, i=t["id"]: self.player.set("aid", i))
        if not self.player.tracks("audio"):
            self.audio_menu.addAction("No sound tracks").setEnabled(False)

    def _load_sub(self):
        start = os.path.dirname(self.path) if self.path else ""
        path, _ = QFileDialog.getOpenFileName(self, "Load subtitles", start,
                                              "Subtitles (*.srt *.ass *.ssa *.vtt *.sup);;All files (*)")
        if path:
            self.player.command("sub-add", path, "select")

    # ---- actions -----------------------------------------------------------
    def on_mpv_key(self, name):
        self.poke()
        if name == "MOUSE_MOVE":
            return
        if name == "MBTN_LEFT_DBL":
            return self.do("fullscreen")
        if name == "MBTN_RIGHT":
            return self.do("play")
        if name in ("WHEEL_UP", "WHEEL_DOWN"):
            return self.do("vol_up" if name == "WHEEL_UP" else "vol_down")
        for action, _, mpv_name in KEYS:
            if mpv_name == name:
                return self.do(action)

    def do(self, action):
        self.poke()
        p = self.player
        seeks = {"back5": -5, "fwd5": 5, "back1": -1, "fwd1": 1, "back10": -10, "fwd10": 10}
        if action == "play":
            p.toggle_pause()
        elif action in seeks:
            p.seek(seeks[action], "relative+exact")
        elif action == "frame_back":
            p.frame_step(back=True)
        elif action == "frame_fwd":
            p.frame_step()
        elif action == "vol_up":
            p.command("add", "volume", 5)
        elif action == "vol_down":
            p.command("add", "volume", -5)
        elif action == "fullscreen":
            self.toggle_fullscreen()
        elif action == "exit_fs":
            if self.isFullScreen():
                self.toggle_fullscreen()
        elif action == "meme":
            self.meme()
        elif action == "grab":
            self.grab_frame()
        elif action == "cycle_sub":
            p.command("cycle", "sub")
            QTimer.singleShot(150, lambda: p.osd("Subtitles: " + _current_label(p, "sub")))
        elif action == "cycle_audio":
            p.command("cycle", "audio")
            QTimer.singleShot(150, lambda: p.osd("Sound: " + _current_label(p, "audio")))
        elif action == "toggle_sub":
            p.command("cycle", "sub-visibility")
        elif action == "slower":
            p.command("multiply", "speed", 1 / 1.1)
        elif action == "faster":
            p.command("multiply", "speed", 1.1)
        elif action == "speed_reset":
            p.set("speed", 1.0)
        elif action == "start":
            p.seek(0, "absolute")
        elif action == "info":
            self.tool_info()

    def toggle_fullscreen(self):
        if self.isFullScreen():
            self.showNormal()
            self.menuBar().show()
            self.controls.show()
            self.hide_timer.stop()
        else:
            self.menuBar().hide()
            self.controls.hide()
            self.showFullScreen()

    def _show_fs_controls(self):
        if not self.controls.isVisible():
            self.controls.show()
        self.hide_timer.start()

    def _hide_fs_controls(self):
        if self.isFullScreen() and not self.controls.underMouse():
            self.controls.hide()
        elif self.isFullScreen():
            self.hide_timer.start()

    def toggle_on_top(self, on):
        self.setWindowFlag(Qt.WindowStaysOnTopHint, on)
        self.show()

    # ---- frames ------------------------------------------------------------
    def _frame_name(self):
        t = self.player.props.get("time-pos") or 0
        h, rem = divmod(t, 3600)
        m, s = divmod(rem, 60)
        stamp = f"{int(h)}-{int(m):02d}-{s:06.3f}".replace(".", "_")
        return f"{plex_name(self.path)} {stamp}"

    def _snap(self, path, then):
        if not self.path:
            QMessageBox.information(self, APP, "Open a video first.")
            return
        self.player.set("pause", True)

        def done(err, _data):
            if err != "success" or not os.path.exists(path):
                QMessageBox.warning(self, APP, f"Couldn't grab the frame ({err}).")
                return
            then(path)
        # a moment for the pause to land so the frame on screen is the one saved
        QTimer.singleShot(60, lambda: self.player.screenshot(path, callback=done))

    def grab_frame(self):
        folder = pictures_dir()
        os.makedirs(folder, exist_ok=True)
        path = os.path.join(folder, self._frame_name() + ".png")
        self._snap(path, lambda p: self.player.osd(f"Saved {os.path.basename(p)} in Pictures\\Nous Reel", 2500))

    def meme(self):
        if not self.path:
            QMessageBox.information(self, APP, "Open a video first.")
            return
        tmp = os.path.join(tempfile.gettempdir(), "nousreel-frame.png")

        def open_meme(path):
            img = QImage(path)
            d = MemeDialog(img, self._frame_name(), settings().value("meme_dir", pictures_dir()), self)
            d.exec()
            settings().setValue("meme_dir", d.save_dir)
        self._snap(tmp, open_meme)

    # ---- tools -------------------------------------------------------------
    def _need_info(self):
        if not self.path:
            path, _ = QFileDialog.getOpenFileName(self, "Choose a video", settings().value("last_dir", ""),
                                                  VIDEO_FILTER)
            if not path:
                return None
            self.open_path(path)
        if self.info is None or self.info.path != self.path:
            try:
                self.info = probe(self.path)
            except ProbeError as e:
                QMessageBox.warning(self, APP, str(e))
                return None
        if self.info.protected:
            QMessageBox.warning(self, APP, PROTECTED_MSG.format(name=os.path.basename(self.path)))
            return None
        return self.info

    def tool_info(self):
        info = self._need_info()
        if info:
            InfoDialog(info, self).exec()

    def tool_damage(self):
        info = self._need_info()
        if not info:
            return
        from reel_damage import DamageDialog
        d = DamageDialog(info, lambda t, p=info.path: self.jump_to(p, t), self)
        d.setAttribute(Qt.WA_DeleteOnClose)
        d.show()          # not modal: keep watching while it checks

    def jump_to(self, path, t):
        if os.path.normcase(path) != os.path.normcase(self.path or ""):
            self.open_path(path)
            QTimer.singleShot(1500, lambda: self.player.seek(t, "absolute+exact"))
        else:
            self.player.seek(t, "absolute+exact")
        self.player.set("pause", False)

    def tool_remux(self):
        info = self._need_info()
        if info:
            RemuxDialog(info, self.queue, self).exec()

    def tool_shrink(self):
        info = self._need_info()
        if info:
            ShrinkDialog(info, self.queue, self).exec()

    def tool_join(self):
        JoinDialog(self.path, self.queue, self).exec()

    def toggle_shell_menu(self, on):
        import reel_shell
        if on:
            reel_shell.install()
            QMessageBox.information(
                self, APP,
                "Added. Right-click a video in File Explorer and choose “720p for Plex (Nous Reel)”.\n\n"
                "On Windows 11 it's under “Show more options”, or hold Shift while you right-click "
                "to go straight to it. Select several videos to queue them all.")
        else:
            reel_shell.uninstall()
            self.statusBar().showMessage("Removed “720p for Plex” from the right-click menu.", 6000)

    def _job_finished(self, job, ok):
        if ok:
            self.statusBar().showMessage(f"Finished: {os.path.basename(job.out)}", 8000)
            QApplication.alert(self)

    # ---- misc --------------------------------------------------------------
    def update_title(self):
        self.setWindowTitle(f"{os.path.basename(self.path)} — {APP}" if self.path else APP)

    def show_keys(self):
        rows = "".join(f"<tr><td style='padding:2px 16px 2px 0'><b>{k}</b></td><td>{v}</td></tr>" for k, v in HELP)
        QMessageBox.information(self, "Keyboard shortcuts", f"<table>{rows}</table>")

    def about(self):
        tools = ", ".join(f"{n}: {'found' if find(n) else 'missing'}" for n in ("mpv", "ffmpeg", "ffprobe"))
        QMessageBox.about(self, APP, f"<b>{APP}</b> {version()}<br>A video player that can shrink, join and "
                                     f"fix files, and make memes.<br><br>Uses mpv for playback and ffmpeg for "
                                     f"everything else.<br><small>{tools}</small>")

    def closeEvent(self, e):
        if self.queue.busy():
            r = QMessageBox.question(self, APP, "A job is still running. Stop it and quit?")
            if r != QMessageBox.Yes:
                e.ignore()
                return
            self.queue.cancel_all()
        self._remember_position()
        settings().setValue("geometry", self.saveGeometry())
        self.player.shutdown()
        super().closeEvent(e)


def _track_label(t):
    from reel_media import LANG_NAMES
    bits = []
    if t.get("lang"):
        bits.append(LANG_NAMES.get(t["lang"], t["lang"]))
    if t.get("title"):
        bits.append(t["title"])
    codec = t.get("codec", "")
    if t.get("type") == "audio" and t.get("demux-channel-count"):
        ch = t["demux-channel-count"]
        bits.append({1: "mono", 2: "stereo", 6: "5.1", 8: "7.1"}.get(ch, f"{ch} ch"))
    if codec:
        bits.append(codec)
    if t.get("external"):
        bits.append("file")
    return f"{t['id']}. " + " · ".join(bits) if bits else f"Track {t['id']}"


def _current_label(player, kind):
    for t in player.tracks(kind):
        if t.get("selected"):
            return _track_label(t)
    return "off"


def apply_theme(app):
    """Same Nord-ish dark palette as the other Nous apps."""
    app.setStyle("Fusion")
    p = QPalette()
    c = {"bg": "#2e3440", "base": "#272c36", "alt": "#3b4252", "text": "#e5e9f0", "mid": "#434c5e",
         "hi": "#5e81ac", "dis": "#6b7385"}
    p.setColor(QPalette.Window, QColor(c["bg"]))
    p.setColor(QPalette.WindowText, QColor(c["text"]))
    p.setColor(QPalette.Base, QColor(c["base"]))
    p.setColor(QPalette.AlternateBase, QColor(c["alt"]))
    p.setColor(QPalette.ToolTipBase, QColor(c["alt"]))
    p.setColor(QPalette.ToolTipText, QColor(c["text"]))
    p.setColor(QPalette.Text, QColor(c["text"]))
    p.setColor(QPalette.Button, QColor(c["alt"]))
    p.setColor(QPalette.ButtonText, QColor(c["text"]))
    p.setColor(QPalette.Highlight, QColor(c["hi"]))
    p.setColor(QPalette.HighlightedText, QColor("#ffffff"))
    p.setColor(QPalette.Mid, QColor(c["mid"]))
    p.setColor(QPalette.PlaceholderText, QColor(c["dis"]))
    for role in (QPalette.WindowText, QPalette.Text, QPalette.ButtonText):
        p.setColor(QPalette.Disabled, role, QColor(c["dis"]))
    app.setPalette(p)
    app.setStyleSheet("QDockWidget::title{background:#3b4252;padding:4px 6px}"
                      "#controls{background:#2e3440}"
                      "#central{background:#000000}"
                      "QToolButton{border-radius:4px;padding:2px 6px}"
                      "QToolButton:hover{background:#434c5e}"
                      "QToolButton#meme{background:#bf616a;color:white;font-weight:bold}"
                      "QToolButton#meme:hover{background:#d0717a}"
                      "QToolTip{border:1px solid #4c566a}")


def main():
    app = QApplication(sys.argv)
    app.setApplicationName(APP)
    app.setOrganizationName("NousReel")
    apply_theme(app)
    if "--plex720" in sys.argv:
        import reel_quick
        files = [a for a in sys.argv[1:] if a != "--plex720"]
        return reel_quick.run(files, app_icon())
    w = MainWindow()
    w.show()
    if len(sys.argv) > 1 and os.path.exists(sys.argv[1]):
        start = sys.argv[1]
        QTimer.singleShot(300, lambda: w.open_path(start))
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
