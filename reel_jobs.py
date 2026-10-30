"""Running ffmpeg jobs one after another with progress, and the Jobs panel."""
import os
import time

from PySide6.QtCore import QObject, QProcess, QUrl, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (QHBoxLayout, QLabel, QProgressBar, QPushButton, QScrollArea,
                               QVBoxLayout, QWidget)

from reel_media import fmt_size, fmt_time
from reel_tools_path import find


class Job(QObject):
    """One ffmpeg run. Writes to '<name>.partial' and renames on success, so a
    cancelled or failed job never leaves a half-file with the real name."""
    changed = Signal()
    finished = Signal(bool)

    def __init__(self, label, args, out_path, duration, cleanup=()):
        super().__init__()
        self.label = label
        self.args = args
        self.out = out_path
        self.part = out_path + ".partial"
        self.duration = max(duration, 0.001)
        self.cleanup = list(cleanup)
        self.state = "queued"   # queued / running / done / failed / cancelled
        self.progress = 0.0
        self.speed = ""
        self.size = 0
        self.error = ""
        self.started = 0.0
        self.ended = 0.0
        self._proc = None
        self._log = []
        self._buf = b""

    def start(self):
        exe = find("ffmpeg")
        if not exe:
            self._fail("ffmpeg isn't installed (winget install Gyan.FFmpeg).")
            return
        self.state = "running"
        self.started = time.time()
        self._proc = QProcess(self)
        self._proc.setProgram(exe)
        self._proc.setArguments(["-nostats", "-progress", "pipe:1", "-loglevel", "error"] + self.args
                                + ["-f", "matroska", self.part])
        self._proc.readyReadStandardOutput.connect(self._stdout)
        self._proc.readyReadStandardError.connect(
            lambda: self._log.append(bytes(self._proc.readAllStandardError()).decode("utf-8", "replace")))
        self._proc.finished.connect(self._done)
        self._proc.errorOccurred.connect(self._proc_error)
        self._proc.start()
        self.changed.emit()

    def cancel(self):
        if self.state == "queued":
            self.state = "cancelled"
            self.changed.emit()
            self.finished.emit(False)
        elif self.state == "running" and self._proc:
            self.state = "cancelled"
            self._proc.write(b"q")       # ffmpeg's own "stop now" key
            if not self._proc.waitForFinished(3000):
                self._proc.kill()

    def _stdout(self):
        self._buf += bytes(self._proc.readAllStandardOutput())
        lines = self._buf.split(b"\n")
        self._buf = lines.pop()
        for line in lines:
            k, _, v = line.decode("ascii", "replace").strip().partition("=")
            if k == "out_time_us" and v.strip().lstrip("-").isdigit():
                self.progress = min(1.0, max(0.0, int(v) / 1e6 / self.duration))
            elif k == "speed":
                self.speed = v.strip()
            elif k == "total_size" and v.strip().isdigit():
                self.size = int(v)
            elif k == "progress":
                self.changed.emit()

    def _proc_error(self, err):
        if err == QProcess.FailedToStart:
            self._fail("ffmpeg couldn't start.")

    def _done(self, code, status):
        self.ended = time.time()
        for p in self.cleanup:
            try:
                os.remove(p)
            except OSError:
                pass
        if self.state == "cancelled":
            self._remove_part()
            self.changed.emit()
            self.finished.emit(False)
            return
        if code == 0 and os.path.exists(self.part):
            try:
                os.replace(self.part, self.out)
            except OSError as e:
                self._fail(f"Finished, but couldn't rename the file: {e}")
                return
            self.state = "done"
            self.progress = 1.0
            self.size = os.path.getsize(self.out)
            self.changed.emit()
            self.finished.emit(True)
        else:
            self._remove_part()
            self._fail("".join(self._log).strip()[-1200:] or f"ffmpeg stopped with code {code}.")

    def _remove_part(self):
        try:
            os.remove(self.part)
        except OSError:
            pass

    def _fail(self, msg):
        self.state = "failed"
        self.error = msg
        self.changed.emit()
        self.finished.emit(False)

    def eta(self):
        if self.state != "running" or self.progress <= 0.01:
            return ""
        spent = time.time() - self.started
        return fmt_time(spent / self.progress - spent, frac=False)


class JobQueue(QObject):
    added = Signal(object)
    all_done = Signal()

    def __init__(self):
        super().__init__()
        self.jobs = []

    def add(self, job):
        self.jobs.append(job)
        job.finished.connect(lambda ok: self._next())
        self.added.emit(job)
        self._next()

    def _next(self):
        if any(j.state == "running" for j in self.jobs):
            return
        for j in self.jobs:
            if j.state == "queued":
                j.start()
                return
        self.all_done.emit()

    def busy(self):
        return any(j.state in ("running", "queued") for j in self.jobs)

    def cancel_all(self):
        for j in self.jobs:
            if j.state in ("queued", "running"):
                j.cancel()


class JobRow(QWidget):
    def __init__(self, job, play_cb):
        super().__init__()
        self.job = job
        self.play_cb = play_cb
        lay = QVBoxLayout(self)
        lay.setContentsMargins(8, 6, 8, 6)
        top = QHBoxLayout()
        self.title = QLabel(f"<b>{job.label}</b>")
        self.status = QLabel()
        self.status.setStyleSheet("color: palette(placeholder-text);")
        top.addWidget(self.title, 1)
        top.addWidget(self.status)
        self.bar = QProgressBar()
        self.bar.setRange(0, 1000)
        self.bar.setTextVisible(False)
        self.bar.setFixedHeight(8)
        btns = QHBoxLayout()
        self.out = QLabel(os.path.basename(job.out))
        self.out.setStyleSheet("color: palette(placeholder-text);")
        self.cancel = QPushButton("Cancel")
        self.cancel.clicked.connect(job.cancel)
        self.show_btn = QPushButton("Show in folder")
        self.show_btn.clicked.connect(self._reveal)
        self.play = QPushButton("Play")
        self.play.clicked.connect(lambda: play_cb(job.out))
        btns.addWidget(self.out, 1)
        for b in (self.show_btn, self.play, self.cancel):
            btns.addWidget(b)
        self.err = QLabel()
        self.err.setWordWrap(True)
        self.err.setStyleSheet("color: #d65c5c;")
        self.err.hide()
        lay.addLayout(top)
        lay.addWidget(self.bar)
        lay.addLayout(btns)
        lay.addWidget(self.err)
        job.changed.connect(self.refresh)
        self.refresh()

    def _reveal(self):
        import subprocess
        target = self.job.out if os.path.exists(self.job.out) else os.path.dirname(self.job.out)
        if os.path.isfile(target):
            subprocess.Popen(["explorer", "/select,", os.path.normpath(target)])
        else:
            QDesktopServices.openUrl(QUrl.fromLocalFile(target))

    def refresh(self):
        j = self.job
        self.bar.setValue(int(j.progress * 1000))
        if j.state == "running":
            bits = [f"{j.progress * 100:.0f}%"]
            if j.speed and j.speed != "N/A":
                bits.append(f"{j.speed} real time")
            if j.size:
                bits.append(fmt_size(j.size) + " so far")
            if j.eta():
                bits.append(f"about {j.eta()} left")
            self.status.setText("  ·  ".join(bits))
        elif j.state == "done":
            took = fmt_time(j.ended - j.started, frac=False)
            self.status.setText(f"Done in {took}  ·  {fmt_size(j.size)}")
        else:
            self.status.setText({"queued": "Waiting its turn", "failed": "Didn't finish",
                                 "cancelled": "Cancelled"}.get(j.state, j.state))
        self.cancel.setVisible(j.state in ("queued", "running"))
        self.play.setVisible(j.state == "done")
        self.show_btn.setVisible(j.state == "done")
        if j.state == "failed":
            self.err.setText(j.error)
            self.err.show()


class JobsPanel(QWidget):
    def __init__(self, queue, play_cb):
        super().__init__()
        self.queue = queue
        self.play_cb = play_cb
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        self.empty = QLabel("No jobs yet. Tools ▸ Shrink, Join parts, or Tracks & subtitles start one.")
        self.empty.setStyleSheet("color: palette(placeholder-text); padding: 12px;")
        outer.addWidget(self.empty)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        self.inner = QWidget()
        self.rows = QVBoxLayout(self.inner)
        self.rows.addStretch(1)
        scroll.setWidget(self.inner)
        outer.addWidget(scroll)
        queue.added.connect(self.add_row)

    def add_row(self, job):
        self.empty.hide()
        self.rows.insertWidget(self.rows.count() - 1, JobRow(job, self.play_cb))
