"""The tool windows: what's inside a file, tracks & subtitles (remux), shrink
(presets or every dial), and joining parts."""
import json
import os
import tempfile

from PySide6.QtCore import QObject, QProcess, QSettings, Qt, QThread, Signal
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import (QAbstractItemView, QCheckBox, QComboBox, QDialog, QDialogButtonBox,
                               QFileDialog, QFormLayout, QGroupBox, QHBoxLayout, QHeaderView, QLabel,
                               QLineEdit, QListWidget, QMessageBox, QPlainTextEdit, QPushButton, QScrollArea,
                               QSlider, QSpinBox, QTableWidget, QTableWidgetItem, QToolButton,
                               QVBoxLayout, QWidget)

import reel_ffcmd as F
from reel_jobs import Job
from reel_media import (LANGS, LANG_NAMES, ProbeError, fmt_size, fmt_time, plex_name, probe,
                        sample_bitrates, unique_path)
from reel_tools_path import find


def settings():
    return QSettings("NousReel", os.environ.get("NOUSREEL_SETTINGS", "NousReel"))


def hint(text):
    lab = QLabel(text)
    lab.setWordWrap(True)
    lab.setStyleSheet("color: palette(placeholder-text);")
    return lab


class OutputPicker(QWidget):
    """Where the new file goes. Defaults to 'Title (Year).mkv' next to the source."""
    def __init__(self, src, suffix=""):
        super().__init__()
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        folder = settings().value("out_dir", "") or os.path.dirname(src)
        if not os.path.isdir(folder):
            folder = os.path.dirname(src)
        name = plex_name(src) + suffix + ".mkv"
        self.edit = QLineEdit(unique_path(os.path.join(folder, name)))
        btn = QPushButton("Browse…")
        btn.clicked.connect(self.browse)
        lay.addWidget(self.edit, 1)
        lay.addWidget(btn)

    def browse(self):
        path, _ = QFileDialog.getSaveFileName(self, "Save as", self.edit.text(), "Matroska video (*.mkv)")
        if path:
            if not path.lower().endswith(".mkv"):
                path += ".mkv"
            self.edit.setText(path)

    def path(self):
        return self.edit.text().strip()


def confirm_output(parent, out, sources):
    if not out:
        return False
    if any(os.path.normcase(os.path.abspath(out)) == os.path.normcase(os.path.abspath(s)) for s in sources):
        QMessageBox.warning(parent, "Nous Reel", "The new file can't replace the original. Pick another name.")
        return False
    if os.path.exists(out):
        r = QMessageBox.question(parent, "Nous Reel", f"{os.path.basename(out)} already exists. Replace it?")
        if r != QMessageBox.Yes:
            return False
    os.makedirs(os.path.dirname(out) or ".", exist_ok=True)
    settings().setValue("out_dir", os.path.dirname(out))
    return True


# ---- tracks table ------------------------------------------------------------------
class TracksTable(QTableWidget):
    """Keep? · track · language · name · default · forced, for remux and shrink."""
    changed = Signal()
    COLS = ["Keep", "Track", "Language", "Name", "Default", "Forced"]

    def __init__(self, choices):
        super().__init__(0, len(self.COLS))
        self.choices = choices
        self.setHorizontalHeaderLabels(self.COLS)
        self.verticalHeader().hide()
        self.setSelectionMode(QAbstractItemView.NoSelection)
        self.setEditTriggers(QAbstractItemView.NoEditTriggers)
        hh = self.horizontalHeader()
        hh.setSectionResizeMode(1, QHeaderView.Stretch)
        hh.setSectionResizeMode(3, QHeaderView.Interactive)
        self.setColumnWidth(0, 44)
        self.setColumnWidth(2, 120)
        self.setColumnWidth(3, 180)
        self.setColumnWidth(4, 60)
        self.setColumnWidth(5, 60)
        self.rebuild()

    def rebuild(self):
        self.setRowCount(0)
        for row, c in enumerate(self.choices):
            self.insertRow(row)
            keep = QCheckBox()
            keep.setChecked(c["keep"])
            keep.toggled.connect(lambda v, c=c: self._set(c, "keep", v))
            self.setCellWidget(row, 0, _center(keep))
            kind = {"video": "Picture", "audio": "Sound", "subtitle": "Subtitles"}.get(c["kind"], c["kind"].title())
            item = QTableWidgetItem(f"{kind}:  {c['label']}")
            item.setToolTip(c["label"])
            self.setItem(row, 1, item)
            lang = QComboBox()
            codes = [code for code, _ in LANGS]
            if c["lang"] not in codes:
                lang.addItem(LANG_NAMES.get(c["lang"], c["lang"]), c["lang"])
            for code, name in LANGS:
                lang.addItem(name, code)
            lang.setCurrentIndex(max(0, lang.findData(c["lang"])))
            lang.currentIndexChanged.connect(lambda i, w=lang, c=c: self._set(c, "lang", w.itemData(i)))
            self.setCellWidget(row, 2, lang)
            title = QLineEdit(c["title"])
            title.setFrame(False)
            title.textEdited.connect(lambda t, c=c: self._set(c, "title", t))
            self.setCellWidget(row, 3, title)
            for col, key in ((4, "default"), (5, "forced")):
                box = QCheckBox()
                box.setChecked(c[key])
                box.setEnabled(c["kind"] in ("audio", "subtitle") or key == "default")
                box.toggled.connect(lambda v, c=c, k=key: self._set(c, k, v))
                self.setCellWidget(row, col, _center(box))
        self.resizeRowsToContents()

    def _set(self, c, key, value):
        c[key] = value
        if key == "default" and value:
            # only one default per kind; untick the others
            for other in self.choices:
                if other is not c and other["kind"] == c["kind"] and other["default"]:
                    other["default"] = False
            self.rebuild()
        self.changed.emit()


def _center(w):
    box = QWidget()
    lay = QHBoxLayout(box)
    lay.setContentsMargins(0, 0, 0, 0)
    lay.setAlignment(Qt.AlignCenter)
    lay.addWidget(w)
    return box


class CommandBox(QWidget):
    """A fold-out 'what's happening behind the scenes' box with the ffmpeg command."""
    def __init__(self):
        super().__init__()
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        self.toggle = QToolButton()
        self.toggle.setText("▸ Show the ffmpeg command")
        self.toggle.setCheckable(True)
        self.toggle.setStyleSheet("QToolButton{border:none}")
        self.toggle.toggled.connect(self._fold)
        self.text = QPlainTextEdit()
        self.text.setReadOnly(True)
        self.text.setMaximumHeight(110)
        self.text.hide()
        lay.addWidget(self.toggle)
        lay.addWidget(self.text)
        self.toggle.setChecked(settings().value("show_command", False, type=bool))

    def _fold(self, on):
        self.text.setVisible(on)
        self.toggle.setText(("▾ Hide" if on else "▸ Show") + " the ffmpeg command")
        settings().setValue("show_command", on)

    def set(self, args, out):
        self.text.setPlainText(F.display_command(args, out))


# ---- what's inside ------------------------------------------------------------------
class _Sampler(QThread):
    done = Signal(object)

    def __init__(self, path, duration):
        super().__init__()
        self.path, self.duration = path, duration

    def run(self):
        self.done.emit(sample_bitrates(self.path, self.duration))


class BytesBar(QWidget):
    COLORS = {"video": "#5e81ac", "audio": "#a3be8c", "subtitle": "#ebcb8b", "other": "#b48ead"}

    def __init__(self):
        super().__init__()
        self.parts = []
        self.setMinimumHeight(22)

    def set_parts(self, parts):
        self.parts = parts
        self.update()

    def paintEvent(self, e):
        qp = QPainter(self)
        total = sum(p[1] for p in self.parts) or 1
        x = 0.0
        for kind, n in self.parts:
            w = self.width() * n / total
            qp.fillRect(int(x), 0, max(1, int(round(w))), self.height(), QColor(self.COLORS.get(kind, "#888")))
            x += w


class InfoDialog(QDialog):
    def __init__(self, info, parent=None):
        super().__init__(parent)
        self.info = info
        self.setWindowTitle("What's inside: " + os.path.basename(info.path))
        self.resize(920, 520)
        lay = QVBoxLayout(self)
        lay.addWidget(QLabel(f"<b>{os.path.basename(info.path)}</b>"))
        lay.addWidget(hint(
            f"The <b>container</b> is {info.container_label()}: the box that holds the tracks. "
            f"Inside are {len(info.streams)} tracks. Most of a file's size is the picture track, "
            f"and how big that is depends on its <b>bitrate</b>: how many bits it may use per second."))
        form = QFormLayout()
        form.addRow("Size", QLabel(fmt_size(info.size)))
        form.addRow("Length", QLabel(fmt_time(info.duration, frac=False)))
        form.addRow("Overall bitrate", QLabel(f"{info.bitrate / 1e6:.2f} Mbps  "
                                              f"({_bitrate_context(info.bitrate)})"))
        if info.chapters:
            form.addRow("Chapters", QLabel(str(len(info.chapters))))
        lay.addLayout(form)
        lay.addWidget(QLabel("<b>Where the bytes go</b>"))
        self.bar = BytesBar()
        lay.addWidget(self.bar)
        self.table = QTableWidget(len(info.streams), 3)
        self.table.setHorizontalHeaderLabels(["Track", "Details", "Share of the file"])
        self.table.verticalHeader().hide()
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeToContents)
        for row, s in enumerate(info.streams):
            kind = {"video": "Picture", "audio": "Sound", "subtitle": "Subtitles",
                    "attachment": "Attachment"}.get(s.kind, s.kind)
            self.table.setItem(row, 0, QTableWidgetItem(f"{kind} #{s.index}"))
            d = s.describe() + ("  (default)" if s.default else "")
            self.table.setItem(row, 1, QTableWidgetItem(d))
            self.table.setItem(row, 2, QTableWidgetItem("measuring…"))
        lay.addWidget(self.table, 1)
        self.notes = hint("")
        lay.addWidget(self.notes)
        bb = QDialogButtonBox(QDialogButtonBox.Close)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)
        self._notes()
        self.sampler = _Sampler(info.path, info.duration)
        self.sampler.done.connect(self._measured)
        self.sampler.start()

    def _notes(self):
        tips = []
        v = self.info.video
        if v and v.height >= 1000 and self.info.bitrate > 20e6:
            tips.append("This looks like a Blu-ray remux (the disc's video copied untouched). "
                        "Shrink can bring it down to a few GB.")
        for s in self.info.of("audio"):
            if s.codec in ("truehd", "flac") or "MA" in (s.profile or ""):
                tips.append(f"Sound #{s.index} is lossless ({s.codec_label()}), which is huge. "
                            "Shrink's Smart audio converts it to AAC.")
        if any(s.codec == "mov_text" for s in self.info.streams):
            tips.append("The MP4 text subtitles are already built into this file.")
        self.notes.setText("  ".join(tips))

    def _measured(self, rates):
        total_rate = sum(rates.values()) or 1
        parts = []
        for row, s in enumerate(self.info.streams):
            r = rates.get(s.index)
            if r is None:
                txt = "tiny" if s.kind in ("subtitle", "attachment") else "?"
                share = 0
            else:
                share = r / total_rate
                txt = f"{share * 100:.1f}%  ·  about {fmt_size(self.info.size * share)}  ·  {r / 1e6:.2f} Mbps"
            self.table.setItem(row, 2, QTableWidgetItem(txt))
            parts.append((s.kind if s.kind in BytesBar.COLORS else "other", share))
        self.bar.set_parts(parts)

    def closeEvent(self, e):
        self.sampler.wait(100)
        super().closeEvent(e)


def _bitrate_context(bps):
    m = bps / 1e6
    if m > 25:
        return "Blu-ray disc territory"
    if m > 8:
        return "high: a big, detailed file"
    if m > 3:
        return "typical for HD streaming"
    if m > 1:
        return "lean, typical of a compact 720p file"
    return "very lean, DVD-rip sized"


# ---- tracks & subtitles (remux) -------------------------------------------------------
class RemuxDialog(QDialog):
    def __init__(self, info, queue, parent=None):
        super().__init__(parent)
        self.info, self.queue = info, queue
        self.setWindowTitle("Tracks & subtitles")
        self.resize(900, 560)
        self.choices = F.default_choices(info)
        lay = QVBoxLayout(self)
        lay.addWidget(QLabel(f"<b>{os.path.basename(info.path)}</b>  ·  {info.summary()}"))
        lay.addWidget(hint("Choose which tracks to keep, fix their language labels, and build subtitle "
                           "files into the video. Nothing is re-encoded, so it's lossless and takes "
                           "seconds to a couple of minutes. The original file isn't touched."))
        self.table = TracksTable(self.choices)
        self.table.changed.connect(self.refresh)
        lay.addWidget(self.table, 1)
        row = QHBoxLayout()
        add = QPushButton("Add subtitle file…")
        add.clicked.connect(self.add_sub)
        row.addWidget(add)
        row.addStretch(1)
        lay.addLayout(row)
        self.found = self._sidecars()
        if self.found:
            lay.addWidget(hint("Found next to the video: " + ", ".join(os.path.basename(p) for p in self.found)
                               + ". Use “Add subtitle file” to build it in."))
        form = QFormLayout()
        self.out = OutputPicker(info.path)
        self.out.edit.textChanged.connect(self.refresh)
        form.addRow("Save as", self.out)
        lay.addLayout(form)
        self.cmd = CommandBox()
        lay.addWidget(self.cmd)
        bb = QDialogButtonBox()
        self.go = bb.addButton("Make the new file", QDialogButtonBox.AcceptRole)
        bb.addButton(QDialogButtonBox.Cancel)
        bb.accepted.connect(self.start)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)
        self.refresh()

    def _sidecars(self):
        folder = os.path.dirname(self.info.path)
        try:
            names = os.listdir(folder)
        except OSError:
            return []
        return [os.path.join(folder, n) for n in names
                if os.path.splitext(n)[1].lower() in (".srt", ".ass", ".ssa", ".vtt", ".sup")]

    def add_sub(self):
        start = self.found[0] if self.found else os.path.dirname(self.info.path)
        path, _ = QFileDialog.getOpenFileName(self, "Add subtitle file", start,
                                              "Subtitles (*.srt *.ass *.ssa *.vtt *.sup);;All files (*)")
        if not path:
            return
        n = max([c["input"] for c in self.choices] + [0]) + 1
        low = os.path.basename(path).lower()
        lang = next((code for code, name in LANGS if name.lower() in low or f".{code}." in low
                     or low.endswith(f"_{code}.srt")), "eng")
        self.choices.append(F.external_sub_choice(n, path, lang))
        self.table.rebuild()
        self.refresh()

    def refresh(self):
        self.cmd.set(F.build_remux(self.info.path, self.choices), self.out.path())
        self.go.setEnabled(any(c["keep"] for c in self.choices))

    def start(self):
        out = self.out.path()
        if not confirm_output(self, out, [self.info.path]):
            return
        self.queue.add(Job(f"Tracks: {os.path.basename(out)}", F.build_remux(self.info.path, self.choices),
                           out, self.info.duration))
        self.accept()


# ---- shrink --------------------------------------------------------------------------
class _Estimator(QObject):
    """Encodes three short slices with the chosen settings and scales up."""
    done = Signal(object)

    def __init__(self, info, settings_, choices):
        super().__init__()
        self.info, self.s, self.choices = info, settings_, choices
        self.slices = [0.2, 0.5, 0.8]
        self.length = 8.0
        self.bytes = 0
        self.proc = None
        self.tmp = os.path.join(tempfile.gettempdir(), f"nousreel-estimate-{os.getpid()}.mkv")

    def start(self):
        self._next()

    def _next(self):
        if not self.slices:
            self._finish()
            return
        at = self.info.duration * self.slices.pop(0)
        v = next((c for c in self.choices if c["kind"] == "video" and c["keep"] and not c["attached_pic"]), None)
        if not v:
            self.done.emit(None)
            return
        src_h = self.info.video.height if self.info.video else 0
        args = ["-hide_banner", "-y", "-ss", f"{at:.2f}", "-t", str(self.length), "-i", self.info.path,
                "-map", f"0:{v['index']}"] + F.video_args(0, self.s, src_h) + ["-f", "matroska", self.tmp]
        self.proc = QProcess(self)
        self.proc.setProgram(find("ffmpeg"))
        self.proc.setArguments(args)
        self.proc.finished.connect(self._slice_done)
        self.proc.start()

    def _slice_done(self, code, _status):
        if code != 0:
            self.done.emit(None)
            return
        self.bytes += os.path.getsize(self.tmp)
        self._next()

    def _finish(self):
        try:
            os.remove(self.tmp)
        except OSError:
            pass
        video_bps = self.bytes * 8 / (3 * self.length)
        audio_bps = 0
        for c in self.choices:
            if c["keep"] and c["kind"] == "audio":
                codec, kbps = F.audio_plan(c, self.s)
                audio_bps += kbps * 1000 if codec != "copy" else (c.get("bitrate") or 1_500_000)
        self.done.emit((video_bps + audio_bps) * self.info.duration / 8)

    def cancel(self):
        self.slices = []
        if self.proc and self.proc.state() != QProcess.NotRunning:
            self.proc.kill()


class ShrinkDialog(QDialog):
    def __init__(self, info, queue, parent=None):
        super().__init__(parent)
        self.info, self.queue = info, queue
        self.setWindowTitle("Shrink")
        self.resize(1320, 820)
        self.choices = F.default_choices(info)
        self.nvenc = F.nvenc_available()
        self.est = None
        self._loading = False
        lay = QVBoxLayout(self)
        lay.addWidget(QLabel(f"<b>{os.path.basename(info.path)}</b>  ·  {info.summary()}"))

        top = QHBoxLayout()
        top.addWidget(QLabel("Recipe"))
        self.preset = QComboBox()
        for name, _, s in F.PRESETS:
            if s.get("codec", "").startswith("nvenc") and not self.nvenc:
                continue
            self.preset.addItem(name, name)
        self.preset.addItem("Custom (my own dial settings)", "custom")
        self.preset.currentIndexChanged.connect(self.pick_preset)
        top.addWidget(self.preset, 1)
        self.dials_btn = QPushButton("Show the dials")
        self.dials_btn.setCheckable(True)
        self.dials_btn.toggled.connect(self.toggle_dials)
        top.addWidget(self.dials_btn)
        lay.addLayout(top)
        self.preset_desc = hint("")
        lay.addWidget(self.preset_desc)

        self.dials = QGroupBox("The dials")
        self.dials_form = QFormLayout(self.dials)
        self.w = {}
        self.explain = {}
        codec = QComboBox()
        for key, (label, enc, _, _) in F.VIDEO_CODECS.items():
            if key.startswith("nvenc") and not self.nvenc:
                continue
            codec.addItem(label, key)
        self._dial("codec", "Codec", codec)
        q = QWidget()
        ql = QHBoxLayout(q)
        ql.setContentsMargins(0, 0, 0, 0)
        self.q_slider = QSlider(Qt.Horizontal)
        self.q_spin = QSpinBox()
        self.q_slider.valueChanged.connect(self.q_spin.setValue)
        self.q_spin.valueChanged.connect(self.q_slider.setValue)
        self.q_spin.valueChanged.connect(lambda _: self.dial_changed())
        self.q_hint = QLabel()
        self.q_hint.setStyleSheet("color: palette(placeholder-text);")
        ql.addWidget(QLabel("better"))
        ql.addWidget(self.q_slider, 1)
        ql.addWidget(QLabel("smaller"))
        ql.addWidget(self.q_spin)
        ql.addWidget(self.q_hint)
        self._dial("quality", "Quality", q, add_signal=False)
        speed = QComboBox()
        speed.addItems(F.SPEEDS)
        self._dial("speed", "Encoder effort", speed)
        height = QComboBox()
        for label, h in F.HEIGHTS:
            height.addItem(label, h)
        self._dial("height", "Resolution", height)
        for key, label, opts in (("deinterlace", "Deinterlace", F.DEINTERLACE),
                                 ("denoise", "Smooth noise", F.DENOISE), ("grain", "Film grain", F.GRAIN)):
            box = QComboBox()
            for text, val in opts:
                box.addItem(text, val)
            self._dial(key, label, box)
        audio = QComboBox()
        for text, val in F.AUDIO_MODES:
            audio.addItem(text, val)
        self._dial("audio_mode", "Sound", audio)
        stereo = QCheckBox("Mix surround down to stereo (smaller; fine for laptops and TV speakers)")
        stereo.toggled.connect(lambda _: self.dial_changed())
        self.w["stereo"] = stereo
        self.dials_form.addRow("", stereo)
        self.dials_scroll = QScrollArea()
        self.dials_scroll.setWidget(self.dials)
        self.dials_scroll.setWidgetResizable(True)
        self.dials_scroll.setFrameShape(QScrollArea.NoFrame)
        self.dials_scroll.setMinimumWidth(560)

        cols = QHBoxLayout()
        cols.addWidget(self.dials_scroll, 5)
        right = QVBoxLayout()
        right.addWidget(QLabel("<b>Tracks</b>  (untick the ones you don't want)"))
        self.table = TracksTable(self.choices)
        self.table.changed.connect(self.refresh)
        right.addWidget(self.table, 1)
        cols.addLayout(right, 6)
        lay.addLayout(cols, 1)

        est = QHBoxLayout()
        self.est_btn = QPushButton("Estimate the size")
        self.est_btn.setToolTip("Encodes three 8-second samples with these settings and scales up.")
        self.est_btn.clicked.connect(self.estimate)
        self.est_lbl = QLabel(f"Currently {fmt_size(info.size)}.")
        est.addWidget(self.est_btn)
        est.addWidget(self.est_lbl, 1)
        lay.addLayout(est)
        form = QFormLayout()
        self.out = OutputPicker(info.path)
        self.out.edit.textChanged.connect(self.refresh)
        form.addRow("Save as", self.out)
        lay.addLayout(form)
        self.cmd = CommandBox()
        lay.addWidget(self.cmd)
        bb = QDialogButtonBox()
        self.go = bb.addButton("Start shrinking", QDialogButtonBox.AcceptRole)
        bb.addButton(QDialogButtonBox.Cancel)
        bb.accepted.connect(self.start)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)

        last = settings().value("shrink_preset", "")
        i = self.preset.findData(last) if last else -1
        self.preset.setCurrentIndex(i if i >= 0 else 0)
        self.pick_preset(self.preset.currentIndex())
        self.dials_btn.setChecked(settings().value("show_dials", False, type=bool))
        self.toggle_dials(self.dials_btn.isChecked())

    def _dial(self, key, label, widget, add_signal=True):
        self.w[key] = widget
        box = QWidget()
        bl = QVBoxLayout(box)
        bl.setContentsMargins(0, 0, 0, 4)
        bl.addWidget(widget)
        ex = hint("")
        bl.addWidget(ex)
        self.explain[key] = ex
        if add_signal:
            widget.currentIndexChanged.connect(lambda _: self.dial_changed())
        self.dials_form.addRow(label, box)

    def toggle_dials(self, on):
        self.dials_scroll.setVisible(on)
        self.dials_btn.setText("Hide the dials" if on else "Show the dials")
        settings().setValue("show_dials", on)

    def pick_preset(self, i):
        key = self.preset.itemData(i)
        if key == "custom":
            self.preset_desc.setText("Your own settings. Change any dial below; each one says what it does.")
            if not self.dials_btn.isChecked():
                self.dials_btn.setChecked(True)
            try:
                stored = json.loads(settings().value("custom_settings", "") or "{}")
            except ValueError:
                stored = {}
            if stored:
                s = dict(F.DEFAULT_SETTINGS)
                s.update(stored)
                self.load_settings(s)
            return
        n = next(k for k, p in enumerate(F.PRESETS) if p[0] == key)
        self.preset_desc.setText(F.PRESETS[n][1])
        self.load_settings(F.preset_settings(n))

    def load_settings(self, s):
        self._loading = True
        for key in ("codec", "height", "deinterlace", "denoise", "grain", "audio_mode"):
            box = self.w[key]
            box.setCurrentIndex(max(0, box.findData(s[key])))
        self.w["speed"].setCurrentText(s["speed"])
        self._set_quality_range(s["codec"])
        self.q_spin.setValue(s["quality"])
        self.w["stereo"].setChecked(s["stereo"])
        self._loading = False
        self.refresh()

    def _set_quality_range(self, codec):
        lo, hi = F.VIDEO_CODECS[codec][2]
        for w in (self.q_slider, self.q_spin):
            w.blockSignals(True)
            w.setRange(lo, hi)
            w.blockSignals(False)

    def current(self):
        return {"codec": self.w["codec"].currentData(), "quality": self.q_spin.value(),
                "speed": self.w["speed"].currentText(), "height": self.w["height"].currentData(),
                "deinterlace": self.w["deinterlace"].currentData(), "denoise": self.w["denoise"].currentData(),
                "grain": self.w["grain"].currentData(), "audio_mode": self.w["audio_mode"].currentData(),
                "audio_kbps": 0, "stereo": self.w["stereo"].isChecked()}

    def dial_changed(self):
        if self._loading:
            return
        s = self.current()
        if self.sender() is self.w["codec"]:
            self._loading = True
            self._set_quality_range(s["codec"])
            self.q_spin.setValue(F.VIDEO_CODECS[s["codec"]][3])
            self._loading = False
            s = self.current()
        if self.preset.currentData() != "custom":
            self.preset.blockSignals(True)
            self.preset.setCurrentIndex(self.preset.findData("custom"))
            self.preset.blockSignals(False)
            self.preset_desc.setText("Your own settings. Change any dial below; each one says what it does.")
        settings().setValue("custom_settings", json.dumps(s))
        self.refresh()

    def refresh(self):
        s = self.current()
        copying = s["codec"] == "copy"
        for key in ("quality", "speed", "height", "deinterlace", "denoise", "grain"):
            self.w[key].setEnabled(not copying)
        for key, lab in self.explain.items():
            lab.setText(F.explain(key, s[key]) if key in s else "")
        lo, hi = F.VIDEO_CODECS[s["codec"]][2]
        self.q_hint.setText("" if copying else _quality_word(s["quality"], lo, hi))
        src_h = self.info.video.height if self.info.video else 0
        if s["height"] and src_h and src_h <= s["height"]:
            self.explain["height"].setText(self.explain["height"].text()
                                           + f"  This video is already {src_h} lines tall, so it stays that size.")
        self.cmd.set(F.build_shrink(self.info, self.choices, s), self.out.path())
        self.est_lbl.setText(f"Currently {fmt_size(self.info.size)}.")
        self.go.setEnabled(any(c["keep"] for c in self.choices))

    def estimate(self):
        if self.est:
            self.est.cancel()
        self.est_lbl.setText("Encoding three short samples…")
        self.est_btn.setEnabled(False)
        self.est = _Estimator(self.info, self.current(), self.choices)
        self.est.done.connect(self._estimated)
        self.est.start()

    def _estimated(self, size):
        self.est_btn.setEnabled(True)
        if size is None:
            self.est_lbl.setText("Couldn't estimate (the sample encode failed).")
            return
        pct = size / self.info.size * 100 if self.info.size else 0
        self.est_lbl.setText(f"Roughly <b>{fmt_size(size)}</b>, about {pct:.0f}% of the current "
                             f"{fmt_size(self.info.size)}. Real results vary ±20%.")

    def start(self):
        out = self.out.path()
        if not confirm_output(self, out, [self.info.path]):
            return
        if self.est:
            self.est.cancel()
        s = self.current()
        settings().setValue("shrink_preset", self.preset.currentData())
        self.queue.add(Job(f"Shrink: {os.path.basename(out)}", F.build_shrink(self.info, self.choices, s),
                           out, self.info.duration))
        self.accept()

    def reject(self):
        if self.est:
            self.est.cancel()
        super().reject()


def _quality_word(q, lo, hi):
    t = (q - lo) / max(1, hi - lo)
    if t < 0.2:
        return "near-lossless, big"
    if t < 0.4:
        return "excellent"
    if t < 0.6:
        return "very good"
    if t < 0.8:
        return "OK, visibly softer"
    return "rough, tiny"


# ---- join parts --------------------------------------------------------------------
class JoinDialog(QDialog):
    def __init__(self, first, queue, parent=None):
        super().__init__(parent)
        self.queue = queue
        self.setWindowTitle("Join parts")
        self.resize(760, 520)
        self.infos = []
        lay = QVBoxLayout(self)
        lay.addWidget(hint("Stitches a movie that came in pieces (CD1, CD2…) into one file, in the order "
                           "listed. If the pieces were made the same way, it's lossless and quick. "
                           "If not, they're re-encoded into one consistent file."))
        self.list = QListWidget()
        self.list.setDragDropMode(QAbstractItemView.InternalMove)
        self.list.model().rowsMoved.connect(lambda *a: self._reorder())
        lay.addWidget(self.list, 1)
        row = QHBoxLayout()
        add = QPushButton("Add parts…")
        add.clicked.connect(self.add)
        rem = QPushButton("Remove")
        rem.clicked.connect(self.remove)
        row.addWidget(add)
        row.addWidget(rem)
        row.addStretch(1)
        lay.addLayout(row)
        self.status = hint("")
        lay.addWidget(self.status)
        form = QFormLayout()
        self.out = OutputPicker(first or os.path.expanduser("~/movie.mkv"))
        self.out.edit.textChanged.connect(self.refresh)
        form.addRow("Save as", self.out)
        lay.addLayout(form)
        self.cmd = CommandBox()
        lay.addWidget(self.cmd)
        bb = QDialogButtonBox()
        self.go = bb.addButton("Join them", QDialogButtonBox.AcceptRole)
        bb.addButton(QDialogButtonBox.Cancel)
        bb.accepted.connect(self.start)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)
        if first:
            self._add_paths([first] + self._siblings(first))
        self.refresh()

    def _siblings(self, path):
        """Tampopo.cd1.avi -> Tampopo.cd2.avi, .cd3 … if they exist."""
        import re
        folder, name = os.path.split(path)
        m = re.search(r"(cd|disc|disk|part|pt)[\s._-]?(\d+)", name, re.I)
        if not m:
            return []
        out = []
        for n in range(int(m.group(2)) + 1, int(m.group(2)) + 9):
            cand = name[:m.start(2)] + str(n) + name[m.end(2):]
            p = os.path.join(folder, cand)
            if os.path.exists(p):
                out.append(p)
            else:
                break
        return out

    def add(self):
        paths, _ = QFileDialog.getOpenFileNames(self, "Add parts", settings().value("last_dir", ""),
                                                "Videos (*.avi *.mkv *.mp4 *.m4v *.mov *.mpg *.vob *.ts *.wmv);;All files (*)")
        self._add_paths(paths)
        self.refresh()

    def _add_paths(self, paths):
        for p in paths:
            try:
                info = probe(p)
            except ProbeError as e:
                QMessageBox.warning(self, "Join parts", f"Couldn't read {os.path.basename(p)}:\n{e}")
                continue
            self.infos.append(info)
            self.list.addItem(f"{os.path.basename(p)}   ·   {fmt_time(info.duration, frac=False)}   ·   "
                              f"{fmt_size(info.size)}")
            self.list.item(self.list.count() - 1).setData(Qt.UserRole, len(self.infos) - 1)

    def _reorder(self):
        order = [self.list.item(i).data(Qt.UserRole) for i in range(self.list.count())]
        self.infos = [self.infos[i] for i in order]
        for i in range(self.list.count()):
            self.list.item(i).setData(Qt.UserRole, i)
        self.refresh()

    def remove(self):
        row = self.list.currentRow()
        if row < 0:
            return
        idx = self.list.item(row).data(Qt.UserRole)
        self.list.takeItem(row)
        del self.infos[idx]
        for i in range(self.list.count()):
            self.list.item(i).setData(Qt.UserRole, i)
        self.refresh()

    def plan(self):
        if len(self.infos) < 2:
            return None, []
        probs = F.join_problems(self.infos)
        return ("encode" if probs else "copy"), probs

    def refresh(self):
        mode, probs = self.plan()
        total = sum(i.duration for i in self.infos)
        self.go.setEnabled(mode is not None)
        if mode is None:
            self.status.setText("Add at least two parts.")
            self.cmd.text.setPlainText("")
            return
        if mode == "copy":
            self.status.setText(f"✓ The parts match, so they'll be joined losslessly. "
                                f"Total length {fmt_time(total, frac=False)}.")
            self.cmd.set(F.build_join_copy([i.path for i in self.infos], "parts.txt", write=False), self.out.path())
        else:
            self.status.setText("The parts don't match, so they'll be re-encoded (H.264, 20) into one file:  "
                                + "  ".join(probs))
            self.cmd.set(F.build_join_encode(self.infos), self.out.path())

    def start(self):
        out = self.out.path()
        if not confirm_output(self, out, [i.path for i in self.infos]):
            return
        mode, _ = self.plan()
        total = sum(i.duration for i in self.infos)
        cleanup = []
        if mode == "copy":
            fd, lst = tempfile.mkstemp(prefix="nousreel-join-", suffix=".txt")
            os.close(fd)
            args = F.build_join_copy([i.path for i in self.infos], lst)
            cleanup.append(lst)
        else:
            args = F.build_join_encode(self.infos)
        self.queue.add(Job(f"Join: {os.path.basename(out)}", args, out, total, cleanup))
        self.accept()
