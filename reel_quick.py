"""One-step '720p for Plex': right-click a video in Explorer and it shrinks in a
small progress window. Several files selected at once go into one queue, one
after another, through a single running copy."""
import json
import os
import re
import sys
import tempfile
import time

from PySide6.QtCore import QLockFile, QTimer
from PySide6.QtNetwork import QLocalServer, QLocalSocket
from PySide6.QtWidgets import (QApplication, QHBoxLayout, QLabel, QPushButton, QSystemTrayIcon,
                               QVBoxLayout, QWidget)

import reel_ffcmd as F
from reel_jobs import Job, JobQueue, JobsPanel
from reel_media import PROTECTED_MSG, ProbeError, fmt_size, plex_name, probe, unique_path

SERVER = "nousreel-plex720-" + re.sub(r"\W", "", os.environ.get("USERNAME", "user"))
QUICK_PRESET = 0          # "Plex 720p · small (H.265)"
EPISODE = re.compile(r"[Ss]\d{1,2}[ ._-]?[Ee]\d{1,3}|\b\d{1,2}x\d{2}\b")


def quick_output(path):
    """'Show - S01E02 - Title.m4v' -> 'Show - S01E02 - Title - 720p.mkv';
    'Chop Shop.2007.1080p.mkv' -> 'Chop Shop (2007) - 720p.mkv'. Plex reads
    ' - 720p' as a version of the same title, so both can live side by side."""
    folder, name = os.path.split(path)
    stem = os.path.splitext(name)[0]
    base = stem if EPISODE.search(stem) else plex_name(path)
    base = re.sub(r"[\s._-]*(480p|576p|720p|1080p|2160p)$", "", base, flags=re.I)
    return unique_path(os.path.join(folder, f"{base} - 720p.mkv"))


def check(info):
    """None if it's worth converting, else a plain-English reason to skip."""
    name = os.path.basename(info.path)
    if info.protected:
        return PROTECTED_MSG.format(name=name)
    v = info.video
    if not v:
        return f"{name} has no picture track."
    if v.height <= 720 and info.bitrate and info.bitrate < 3_000_000:
        return (f"{name} is already {v.height}p at {info.bitrate / 1e6:.1f} Mbps, so converting it "
                f"wouldn't save much. Left as it is.")
    return None


def make_job(path):
    info = probe(path)
    reason = check(info)
    if reason:
        return None, reason
    out = quick_output(path)
    args = F.build_shrink(info, F.default_choices(info), F.preset_settings(QUICK_PRESET))
    return Job(os.path.basename(path), args, out, info.duration), None


class QuickWindow(QWidget):
    def __init__(self, icon):
        super().__init__()
        self.setWindowTitle("720p for Plex — Nous Reel")
        self.setWindowIcon(icon)
        self.resize(640, 300)
        self.queue = JobQueue()
        self.results = []
        lay = QVBoxLayout(self)
        lay.addWidget(QLabel(f"<b>Shrinking to 720p for Plex</b>  ·  {F.PRESETS[QUICK_PRESET][0]}"))
        self.note = QLabel("New files are saved next to the originals with “ - 720p” added. "
                           "The originals aren't touched. You can minimise this window.")
        self.note.setWordWrap(True)
        self.note.setStyleSheet("color: palette(placeholder-text);")
        lay.addWidget(self.note)
        self.skipped = QLabel()
        self.skipped.setWordWrap(True)
        self.skipped.setStyleSheet("color: #ebcb8b;")
        self.skipped.hide()
        lay.addWidget(self.skipped)
        lay.addWidget(JobsPanel(self.queue, self._play), 1)
        row = QHBoxLayout()
        self.status = QLabel()
        row.addWidget(self.status, 1)
        close = QPushButton("Close")
        close.clicked.connect(self.close)
        row.addWidget(close)
        lay.addLayout(row)
        self.tray = QSystemTrayIcon(icon, self)
        self.tray.setToolTip("Nous Reel: 720p for Plex")
        self.tray.show()
        self.queue.all_done.connect(self._all_done)
        self.queue.added.connect(lambda j: j.changed.connect(self._update_status))

    def add_paths(self, paths):
        _dbg("add_paths", paths)
        notes = []
        for p in paths:
            if any(os.path.normcase(j.out) == os.path.normcase(p) for j in self.queue.jobs):
                continue
            try:
                job, reason = make_job(p)
            except ProbeError as e:
                job, reason = None, f"Couldn't read {os.path.basename(p)}: {e}"
            if reason:
                notes.append(reason)
            else:
                self.queue.add(job)
        if notes:
            self.skipped.setText((self.skipped.text() + "\n" if self.skipped.text() else "") + "\n".join(notes))
            self.skipped.show()
        self._update_status()
        if not self.queue.jobs:
            self.status.setText("Nothing to convert.")

    def _update_status(self):
        running = [j for j in self.queue.jobs if j.state == "running"]
        waiting = sum(j.state == "queued" for j in self.queue.jobs)
        if running:
            j = running[0]
            txt = f"{j.label}: {j.progress * 100:.0f}%"
            if waiting:
                txt += f"  ·  {waiting} more waiting"
            self.status.setText(txt)
            self.setWindowTitle(f"{j.progress * 100:.0f}% · 720p for Plex — Nous Reel")

    def _all_done(self):
        done = [j for j in self.queue.jobs if j.state == "done"]
        failed = [j for j in self.queue.jobs if j.state == "failed"]
        if not self.queue.jobs:
            return
        msg = f"{len(done)} file{'s' if len(done) != 1 else ''} ready for Plex"
        if failed:
            msg += f", {len(failed)} didn't finish"
        self.status.setText(msg + ".")
        self.setWindowTitle("Done · 720p for Plex — Nous Reel")
        self.tray.showMessage("Nous Reel", msg + ".", QSystemTrayIcon.Information, 8000)
        QApplication.alert(self)
        if os.environ.get("NOUSREEL_QUICK_EXIT_WHEN_DONE"):   # tests only
            QApplication.instance().exit(0 if not failed else 2)

    def _play(self, path):
        os.startfile(path)

    def closeEvent(self, e):
        if self.queue.busy():
            from PySide6.QtWidgets import QMessageBox
            r = QMessageBox.question(self, "Nous Reel", "Still converting. Stop and close?")
            if r != QMessageBox.Yes:
                e.ignore()
                return
            self.queue.cancel_all()
        self.tray.hide()
        super().closeEvent(e)


def _dbg(*a):
    if os.environ.get("NOUSREEL_QUICK_DEBUG"):
        print(f"[{os.getpid()} {time.time():.2f}]", *a, flush=True)


def _send_to_running(paths, wait_ms=500):
    sock = QLocalSocket()
    sock.connectToServer(SERVER)
    if not sock.waitForConnected(wait_ms):
        return False
    sock.write(json.dumps(paths).encode("utf-8") + b"\n")
    sock.flush()
    sock.waitForBytesWritten(1000)
    # Hanging up before the queue has read the message can lose it on Windows,
    # so wait for its "ok" (and let the caller retry without one)
    got = b""
    deadline = time.time() + 8
    while b"ok" not in got and time.time() < deadline:
        if sock.waitForReadyRead(250):
            got += bytes(sock.readAll())
    _dbg("handed over" if b"ok" in got else "no ack", paths)
    sock.disconnectFromServer()
    return b"ok" in got


def run(paths, icon):
    """Entry point for `--plex720 file…`. Returns the app exit code."""
    paths = [os.path.abspath(p) for p in paths if os.path.isfile(p)]
    if not paths:
        return 1
    # Explorer starts one copy per selected file. The first to get the lock
    # runs the queue; the others hand over their file and quit.
    lock = QLockFile(os.path.join(tempfile.gettempdir(), SERVER + ".lock"))
    for _ in range(40):
        if _send_to_running(paths):
            return 0
        if lock.tryLock(0):
            break
        time.sleep(0.15)
    else:
        # something holds the lock but isn't answering: never drop the file, run on our own
        _dbg("queue unreachable; running separately")
    win = QuickWindow(icon)
    server = QLocalServer()
    QLocalServer.removeServer(SERVER)
    _dbg("serving", server.listen(SERVER))
    pending = []

    def incoming():
        while server.hasPendingConnections():
            c = server.nextPendingConnection()
            buf = []

            def read(c=c, buf=buf):
                buf.append(bytes(c.readAll()))
                data = b"".join(buf)
                if not data.endswith(b"\n"):
                    return
                buf.clear()
                _dbg("received", data)
                c.write(b"ok\n")
                c.flush()
                for line in data.splitlines():
                    try:
                        pending.extend(json.loads(line))
                    except ValueError:
                        pass
                QTimer.singleShot(0, drain)
            c.readyRead.connect(read)
            if c.bytesAvailable():
                read()

    def drain():
        # probing files is slow; do it after the reply has gone out
        items = pending[:]
        pending.clear()
        if items:
            win.add_paths(items)
    server.newConnection.connect(incoming)
    win.show()
    pending.extend(paths)
    QTimer.singleShot(0, drain)
    code = QApplication.instance().exec()
    server.close()
    lock.unlock()
    return code
