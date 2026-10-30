"""Meme this frame: crop a grabbed frame and deliberately ruin it (crunch, JPEG rot,
deep fry, glitches, crooked crops) the way memes look after a hundred reposts."""
import os
import random
import subprocess

import numpy as np
from PySide6.QtCore import QBuffer, QIODevice, QPointF, QRectF, QSettings, Qt, QTimer, Signal
from PySide6.QtGui import (QColor, QFont, QFontMetricsF, QGuiApplication, QImage, QPainter,
                           QPainterPath, QPen, QPixmap, QTransform)
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QFileDialog, QFormLayout, QGroupBox,
                               QHBoxLayout, QLabel, QLineEdit, QMessageBox, QPushButton, QScrollArea,
                               QSlider, QVBoxLayout, QWidget)

# name, label, tooltip
RUIN = [
    ("crunch", "Crunch", "Shrinks the picture and blows it back up, like a thumbnail that got reposted at full size."),
    ("jpeg", "JPEG rot", "Saves it as a bad JPEG over and over. Blocky squares and fuzzy halos around edges."),
    ("fry", "Deep fry", "Cranks contrast, saturation and sharpening until it glows. Black-and-white frames go orange."),
    ("noise", "Noise", "Grainy static."),
    ("blur", "Blur", "Out of focus, as if screenshotted from a phone video."),
    ("bleed", "Color bleed", "Red and blue drift apart like a worn VHS tape."),
    ("glitch", "Glitch", "Slices of the picture slip sideways."),
    ("tilt", "Crooked", "Tilts it, showing black corners, like a sloppy phone screenshot."),
    ("sloppy", "Bad crop", "Leaves an uneven sliver of black border, as if cropped in a hurry."),
]
ASPECTS = [("Free", 0), ("Original", -1), ("1:1 square", 1.0), ("4:3", 4 / 3), ("16:9", 16 / 9),
           ("4:5 (Instagram)", 4 / 5), ("9:16 (phone)", 9 / 16)]
CAPTION_STYLES = ["None", "Classic (Impact, top and bottom)", "Top bar (white bar, black text)"]


# ---- image helpers ---------------------------------------------------------
def to_np(img):
    img = img.convertToFormat(QImage.Format_RGB888)
    w, h, bpl = img.width(), img.height(), img.bytesPerLine()
    arr = np.frombuffer(img.constBits(), np.uint8, bpl * h).reshape(h, bpl)[:, : w * 3]
    return arr.reshape(h, w, 3).astype(np.float32)


def to_qimage(arr):
    a = np.ascontiguousarray(np.clip(arr, 0, 255).astype(np.uint8))
    h, w, _ = a.shape
    return QImage(a.data, w, h, w * 3, QImage.Format_RGB888).copy()


def jpeg_roundtrip(img, quality):
    buf = QBuffer()
    buf.open(QIODevice.ReadWrite)
    img.save(buf, "JPG", quality)
    out = QImage()
    out.loadFromData(buf.data(), "JPG")
    return out


def box_blur(a, r):
    r = int(r)
    if r < 1:
        return a
    k = 2 * r + 1
    pad = np.pad(a, ((r + 1, r), (0, 0), (0, 0)), mode="edge")
    c = np.cumsum(pad, axis=0)
    a = (c[k:] - c[:-k]) / k
    pad = np.pad(a, ((0, 0), (r + 1, r), (0, 0)), mode="edge")
    c = np.cumsum(pad, axis=1)
    return (c[:, k:] - c[:, :-k]) / k


# ---- the pipeline -------------------------------------------------------------
def default_params():
    p = {k: 0 for k, _, _ in RUIN}
    p.update(top="", bottom="", style=1, size=50, fry_caption=True, seed=1)
    return p


def draw_captions(img, p):
    style = p.get("style", 0)
    top, bottom = p.get("top", "").strip(), p.get("bottom", "").strip()
    if style == 0 or not (top or bottom):
        return img
    w, h = img.width(), img.height()
    if style == 2:
        text = "\n".join(t for t in (top, bottom) if t)
        font = QFont("Arial")
        font.setPixelSize(max(12, int(h * 0.045 * p.get("size", 50) / 50)))
        font.setHintingPreference(QFont.PreferNoHinting)
        fm = QFontMetricsF(font)
        pad = fm.height() * 0.6
        rect = fm.boundingRect(QRectF(0, 0, w - 2 * pad, 10_000), Qt.TextWordWrap | Qt.AlignLeft, text)
        bar = int(rect.height() + 2 * pad)
        out = QImage(w, h + bar, QImage.Format_RGB888)
        out.fill(QColor("white"))
        qp = QPainter(out)
        qp.setRenderHint(QPainter.Antialiasing)
        qp.setRenderHint(QPainter.TextAntialiasing)
        qp.drawImage(0, bar, img)
        qp.setFont(font)
        qp.setPen(QColor("black"))
        qp.drawText(QRectF(pad, pad, w - 2 * pad, bar), Qt.TextWordWrap | Qt.AlignLeft, text)
        qp.end()
        return out
    out = img.copy()
    qp = QPainter(out)
    qp.setRenderHint(QPainter.Antialiasing)
    px = max(12, int(h * 0.11 * p.get("size", 50) / 50))
    font = QFont("Impact")
    font.setPixelSize(px)
    # hinting makes outline text drift apart letter by letter on Windows
    font.setHintingPreference(QFont.PreferNoHinting)
    for text, at_top in ((top, True), (bottom, False)):
        if not text:
            continue
        text = text.upper()
        f = QFont(font)
        fm = QFontMetricsF(f)
        while fm.horizontalAdvance(text) > w * 0.94 and f.pixelSize() > 8:
            f.setPixelSize(f.pixelSize() - 1)
            fm = QFontMetricsF(f)
        x = (w - fm.horizontalAdvance(text)) / 2
        y = fm.ascent() + h * 0.02 if at_top else h - fm.descent() - h * 0.03
        path = QPainterPath()
        path.addText(QPointF(x, y), f, text)
        qp.setPen(QPen(QColor("black"), max(2.0, f.pixelSize() / 12), Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
        qp.setBrush(QColor("white"))
        qp.drawPath(path)
    qp.end()
    return out


def ruin(src, p, crop=None):
    """Apply crop then every slider in `p` (0–100) to a QImage; returns a QImage."""
    rng = random.Random(p.get("seed", 1))
    img = src.copy(crop.toRect()) if crop is not None and not crop.isNull() else src.copy()
    img = img.convertToFormat(QImage.Format_RGB888)

    t = p.get("tilt", 0)
    if t:
        ang = (t / 100) * 9 * (1 if rng.random() < 0.5 else -1)
        rot = img.transformed(QTransform().rotate(ang), Qt.SmoothTransformation)
        out = QImage(img.size(), QImage.Format_RGB888)
        out.fill(QColor("black"))
        qp = QPainter(out)
        scale = 1 + (t / 100) * 0.04
        qp.translate(img.width() / 2 + rng.uniform(-1, 1) * img.width() * 0.02 * t / 100, img.height() / 2)
        qp.scale(scale, scale)
        qp.drawImage(QPointF(-rot.width() / 2, -rot.height() / 2), rot)
        qp.end()
        img = out

    s = p.get("sloppy", 0)
    if s:
        w, h = img.width(), img.height()
        m = s / 100 * 0.06
        l, r = int(w * m * rng.random()), int(w * m * rng.random())
        tp, bt = int(h * m * rng.random() * 1.5), int(h * m * rng.random())
        out = QImage(w, h, QImage.Format_RGB888)
        out.fill(QColor(rng.choice(["black", "black", "#111", "white"])))
        qp = QPainter(out)
        qp.drawImage(QRectF(l, tp, w - l - r, h - tp - bt), img)
        qp.end()
        # then "crop" a little off one side, which is what makes it look careless
        img = out.copy(0, 0, w - int(w * m * 0.5 * rng.random()), h)

    if p.get("fry_caption", True):
        img = draw_captions(img, p)

    a = to_np(img)
    h, w, _ = a.shape

    c = p.get("crunch", 0)
    if c:
        f = 1 + (c / 100) ** 1.3 * 11
        small = to_qimage(a).scaled(max(8, int(w / f)), max(8, int(h / f)), Qt.IgnoreAspectRatio,
                                    Qt.FastTransformation)
        mode = Qt.FastTransformation if c > 70 else Qt.SmoothTransformation
        a = to_np(small.scaled(w, h, Qt.IgnoreAspectRatio, mode))

    b = p.get("blur", 0)
    if b:
        a = box_blur(a, 1 + b / 100 * max(w, h) / 200)

    fry = p.get("fry", 0)
    if fry:
        k = fry / 100
        gray = (a * [0.299, 0.587, 0.114]).sum(axis=2, keepdims=True)
        colourless = float(np.abs(a - gray).mean()) < 6
        if colourless:
            # black-and-white film: fried memes go orange-red
            a = gray * np.array([1.0 + 0.55 * k, 1.0 + 0.1 * k, 1.0 - 0.55 * k], np.float32)
            gray = (a * [0.299, 0.587, 0.114]).sum(axis=2, keepdims=True)
        a = gray + (a - gray) * (1 + 3.5 * k)
        a = (a - 128) * (1 + 1.6 * k) + 128 + 18 * k
        a = np.clip(a, 0, 255)
        a = a + (a - box_blur(a, 2)) * (4 * k)
        levels = int(256 - 200 * k)
        a = np.round(np.clip(a, 0, 255) / 256 * levels) * (256 / levels)

    bl = p.get("bleed", 0)
    if bl:
        n = max(1, int(bl / 100 * w * 0.012))
        a = a.copy()
        a[:, :, 0] = np.roll(a[:, :, 0], n, axis=1)
        a[:, :, 2] = np.roll(a[:, :, 2], -n, axis=1)

    g = p.get("glitch", 0)
    if g:
        a = a.copy()
        for _ in range(int(2 + g / 100 * 18)):
            y0 = rng.randrange(0, h)
            bh = rng.randrange(1, max(2, int(h * 0.06 * g / 100) + 2))
            dx = int(rng.uniform(-1, 1) * w * 0.12 * g / 100)
            a[y0:y0 + bh] = np.roll(a[y0:y0 + bh], dx, axis=1)
            if rng.random() < 0.3:
                a[y0:y0 + bh, :, rng.randrange(3)] *= 1.6

    nz = p.get("noise", 0)
    if nz:
        nrng = np.random.default_rng(p.get("seed", 1))
        a = a + nrng.normal(0, nz / 100 * 45, (h, w, 1)).astype(np.float32)

    img = to_qimage(a)

    j = p.get("jpeg", 0)
    if j:
        gens = 1 + int(j / 100 * 24)
        q = int(60 - j / 100 * 54)
        for i in range(gens):
            img = jpeg_roundtrip(img, max(2, q + (i % 3) - 1))
            if i % 4 == 3:  # a slight resize each few saves keeps the damage piling up
                img = img.scaled(img.width() - 1, img.height(), Qt.IgnoreAspectRatio, Qt.SmoothTransformation)
        img = img.convertToFormat(QImage.Format_RGB888)

    if not p.get("fry_caption", True):
        img = draw_captions(img, p)
    return img


def randomize(p):
    rng = random.Random()
    q = dict(p)
    picks = rng.sample([k for k, _, _ in RUIN], rng.randint(2, 5))
    for k, _, _ in RUIN:
        q[k] = rng.randint(25, 95) if k in picks else 0
    q["seed"] = rng.randint(1, 10_000)
    return q


# ---- widgets ----------------------------------------------------------------
class Canvas(QWidget):
    """Shows the result, or the original with a draggable crop box."""
    crop_changed = Signal()

    def __init__(self, src):
        super().__init__()
        self.src = src
        self.result = src
        self.crop = QRectF()
        self.aspect = 0
        self.cropping = False
        self._drag = None
        self.setMinimumSize(480, 320)
        self.setMouseTracking(True)

    def set_result(self, img):
        self.result = img
        self.update()

    def _shown(self):
        return self.src if self.cropping else self.result

    def _fit(self):
        img = self._shown()
        s = min(self.width() / img.width(), self.height() / img.height())
        w, h = img.width() * s, img.height() * s
        return QRectF((self.width() - w) / 2, (self.height() - h) / 2, w, h), s

    def paintEvent(self, e):
        qp = QPainter(self)
        qp.fillRect(self.rect(), QColor("#1e2229"))
        r, s = self._fit()
        qp.setRenderHint(QPainter.SmoothPixmapTransform, s < 1)
        qp.drawImage(r, self._shown())
        if self.cropping and not self.crop.isNull():
            c = QRectF(r.x() + self.crop.x() * s, r.y() + self.crop.y() * s,
                       self.crop.width() * s, self.crop.height() * s)
            shade = QPainterPath()
            shade.addRect(r)
            hole = QPainterPath()
            hole.addRect(c)
            qp.fillPath(shade.subtracted(hole), QColor(0, 0, 0, 140))
            qp.setPen(QPen(QColor("#ffffff"), 1.5, Qt.DashLine))
            qp.drawRect(c)
            qp.setPen(QColor("#ffffff"))
            qp.drawText(c.adjusted(4, 2, 0, 0), Qt.AlignLeft | Qt.AlignTop,
                        f"{int(self.crop.width())}×{int(self.crop.height())}")

    def _to_img(self, pos):
        r, s = self._fit()
        x = min(max((pos.x() - r.x()) / s, 0), self.src.width())
        y = min(max((pos.y() - r.y()) / s, 0), self.src.height())
        return QPointF(x, y)

    def mousePressEvent(self, e):
        if not self.cropping or e.button() != Qt.LeftButton:
            return
        p = self._to_img(e.position())
        if not self.crop.isNull() and self.crop.contains(p):
            self._drag = ("move", p, QRectF(self.crop))
        else:
            self._drag = ("new", p, None)

    def mouseMoveEvent(self, e):
        if not self._drag:
            if self.cropping:
                p = self._to_img(e.position())
                self.setCursor(Qt.SizeAllCursor if self.crop.contains(p) else Qt.CrossCursor)
            return
        p = self._to_img(e.position())
        kind, start, orig = self._drag
        W, H = self.src.width(), self.src.height()
        if kind == "move":
            d = p - start
            r = orig.translated(d)
            r.moveLeft(min(max(r.left(), 0), W - r.width()))
            r.moveTop(min(max(r.top(), 0), H - r.height()))
            self.crop = r
        else:
            w, h = p.x() - start.x(), p.y() - start.y()
            ratio = self._ratio()
            if ratio:
                if abs(w) / ratio > abs(h):
                    h = abs(w) / ratio * (1 if h >= 0 else -1)
                else:
                    w = abs(h) * ratio * (1 if w >= 0 else -1)
            r = QRectF(start, QPointF(start.x() + w, start.y() + h)).normalized()
            self.crop = r.intersected(QRectF(0, 0, W, H))
        self.update()

    def mouseReleaseEvent(self, e):
        if self._drag:
            self._drag = None
            if self.crop.width() < 8 or self.crop.height() < 8:
                self.crop = QRectF()
            self.update()
            self.crop_changed.emit()

    def _ratio(self):
        if self.aspect == -1:
            return self.src.width() / self.src.height()
        return self.aspect

    def resizeEvent(self, e):
        self.update()


def halo_exe():
    s = QSettings("NousReel", os.environ.get("NOUSREEL_SETTINGS", "NousReel")).value("halo_path", "")
    cands = [s, os.path.join(os.environ.get("ProgramFiles", r"C:\Program Files"), "Nous Halo", "NousHalo.exe"),
             os.path.join(os.environ.get("LOCALAPPDATA", ""), "Programs", "Nous Halo", "NousHalo.exe"),
             os.path.expanduser(r"~\NousHalo\dist\NousHalo\NousHalo.exe")]
    return next((c for c in cands if c and os.path.isfile(c)), None)


class MemeDialog(QDialog):
    def __init__(self, frame, name_hint, save_dir, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Meme this frame")
        self.resize(1280, 800)
        self.src = frame
        self.name_hint = name_hint
        self.save_dir = save_dir
        self.p = default_params()
        self.canvas = Canvas(frame)
        self.canvas.crop_changed.connect(self.schedule)
        self.timer = QTimer(self, singleShot=True, interval=80, timeout=self.render)

        side = QWidget()
        sl = QVBoxLayout(side)
        crop_box = QGroupBox("Crop")
        cl = QHBoxLayout(crop_box)
        self.crop_btn = QPushButton("Draw crop box")
        self.crop_btn.setCheckable(True)
        self.crop_btn.toggled.connect(self.toggle_crop)
        self.aspect = QComboBox()
        for label, _ in ASPECTS:
            self.aspect.addItem(label)
        self.aspect.currentIndexChanged.connect(lambda i: setattr(self.canvas, "aspect", ASPECTS[i][1]))
        clear = QPushButton("Clear")
        clear.clicked.connect(self.clear_crop)
        cl.addWidget(self.crop_btn)
        cl.addWidget(self.aspect)
        cl.addWidget(clear)
        sl.addWidget(crop_box)

        ruin_box = QGroupBox("Ruin it")
        form = QFormLayout(ruin_box)
        self.sliders = {}
        for key, label, tip in RUIN:
            s = QSlider(Qt.Horizontal)
            s.setRange(0, 100)
            s.setToolTip(tip)
            s.valueChanged.connect(lambda v, k=key: self.set_param(k, v))
            lab = QLabel(label)
            lab.setToolTip(tip)
            form.addRow(lab, s)
            self.sliders[key] = s
        row = QHBoxLayout()
        chaos = QPushButton("Chaos")
        chaos.setToolTip("Random ruin. Press again for another.")
        chaos.clicked.connect(self.chaos)
        reroll = QPushButton("Reroll")
        reroll.setToolTip("Same settings, different random glitches, tilt and noise.")
        reroll.clicked.connect(self.reroll)
        reset = QPushButton("Clean")
        reset.setToolTip("All sliders back to zero.")
        reset.clicked.connect(self.reset)
        for b in (chaos, reroll, reset):
            row.addWidget(b)
        form.addRow(row)
        sl.addWidget(ruin_box)

        cap = QGroupBox("Caption")
        cf = QFormLayout(cap)
        self.style = QComboBox()
        self.style.addItems(CAPTION_STYLES)
        self.style.setCurrentIndex(1)
        self.style.currentIndexChanged.connect(lambda i: self.set_param("style", i))
        self.top = QLineEdit()
        self.top.setPlaceholderText("Top text")
        self.top.textChanged.connect(lambda t: self.set_param("top", t))
        self.bottom = QLineEdit()
        self.bottom.setPlaceholderText("Bottom text")
        self.bottom.textChanged.connect(lambda t: self.set_param("bottom", t))
        self.size = QSlider(Qt.Horizontal)
        self.size.setRange(20, 100)
        self.size.setValue(50)
        self.size.valueChanged.connect(lambda v: self.set_param("size", v))
        self.fry_cap = QCheckBox("Ruin the caption too")
        self.fry_cap.setChecked(True)
        self.fry_cap.toggled.connect(lambda v: self.set_param("fry_caption", v))
        cf.addRow("Style", self.style)
        cf.addRow(self.top)
        cf.addRow(self.bottom)
        cf.addRow("Size", self.size)
        cf.addRow(self.fry_cap)
        sl.addWidget(cap)
        sl.addStretch(1)

        out = QHBoxLayout()
        b_save = QPushButton("Save…")
        b_save.setDefault(True)
        b_save.clicked.connect(self.save)
        b_copy = QPushButton("Copy")
        b_copy.setToolTip("Copy the picture so you can paste it into a chat or post.")
        b_copy.clicked.connect(self.copy)
        self.b_halo = QPushButton("Open in Nous Halo")
        self.b_halo.clicked.connect(self.open_halo)
        self.b_halo.setEnabled(bool(halo_exe()))
        if not halo_exe():
            self.b_halo.setToolTip("Nous Halo isn't installed on this computer.")
        for b in (b_save, b_copy, self.b_halo):
            out.addWidget(b)
        sl.addLayout(out)
        self.info = QLabel()
        self.info.setStyleSheet("color: palette(placeholder-text);")
        sl.addWidget(self.info)

        scroll = QScrollArea()
        scroll.setWidget(side)
        scroll.setWidgetResizable(True)
        scroll.setFixedWidth(360)
        lay = QHBoxLayout(self)
        lay.addWidget(self.canvas, 1)
        lay.addWidget(scroll)
        self.render()

    def set_param(self, k, v):
        self.p[k] = v
        self.schedule()

    def schedule(self):
        self.timer.start()

    def render(self):
        crop = self.canvas.crop if not self.canvas.crop.isNull() else None
        self.result = ruin(self.src, self.p, crop)
        self.canvas.set_result(self.result)
        self.info.setText(f"{self.result.width()} × {self.result.height()} pixels")

    def toggle_crop(self, on):
        self.canvas.cropping = on
        self.crop_btn.setText("Done cropping" if on else "Draw crop box")
        self.canvas.setCursor(Qt.CrossCursor if on else Qt.ArrowCursor)
        self.canvas.update()
        if not on:
            self.render()

    def clear_crop(self):
        self.canvas.crop = QRectF()
        self.canvas.update()
        self.render()

    def _apply(self, q):
        self.p = q
        for k, s in self.sliders.items():
            s.blockSignals(True)
            s.setValue(q[k])
            s.blockSignals(False)
        self.render()

    def chaos(self):
        self._apply(randomize(self.p))

    def reroll(self):
        q = dict(self.p)
        q["seed"] = random.randint(1, 10_000)
        self._apply(q)

    def reset(self):
        q = dict(self.p)
        for k, _, _ in RUIN:
            q[k] = 0
        self._apply(q)

    def _default_name(self, ext):
        return os.path.join(self.save_dir, self.name_hint + ext)

    def save(self):
        os.makedirs(self.save_dir, exist_ok=True)
        path, filt = QFileDialog.getSaveFileName(self, "Save meme", self._default_name(".png"),
                                                 "PNG (*.png);;JPEG (*.jpg)")
        if not path:
            return
        ok = self.result.save(path, None, 92)
        if not ok:
            QMessageBox.warning(self, "Meme this frame", f"Couldn't save {path}.")
            return
        self.save_dir = os.path.dirname(path)
        self.info.setText(f"Saved {os.path.basename(path)}")

    def copy(self):
        QGuiApplication.clipboard().setImage(self.result)
        self.info.setText("Copied. Paste it anywhere.")

    def open_halo(self):
        exe = halo_exe()
        if not exe:
            return
        os.makedirs(self.save_dir, exist_ok=True)
        path = self._default_name(".png")
        base, ext = os.path.splitext(path)
        i = 2
        while os.path.exists(path):
            path = f"{base} ({i}){ext}"
            i += 1
        self.result.save(path)
        subprocess.Popen([exe, path])
        self.info.setText(f"Saved {os.path.basename(path)} and opened it in Nous Halo")
