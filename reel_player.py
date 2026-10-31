"""The video surface: mpv.exe drawing into a Qt widget (--wid), driven over its
JSON IPC pipe. Using the installed player this way means no libmpv build is needed."""
import json
import os
import tempfile
import uuid

from PySide6.QtCore import QProcess, QTimer, Qt, Signal
from PySide6.QtGui import QPalette, QColor
from PySide6.QtNetwork import QLocalSocket
from PySide6.QtWidgets import QWidget

from reel_tools_path import find

OBSERVED = ["time-pos", "duration", "pause", "estimated-frame-number", "estimated-frame-count",
            "container-fps", "track-list", "path", "media-title", "volume", "mute",
            "video-params", "eof-reached", "sid", "aid", "sub-visibility", "speed"]


# mpv input names forwarded back to the app, so keys and mouse work no matter
# whether the video surface or the Qt window has focus
FORWARD = ["SPACE", "LEFT", "RIGHT", "Shift+LEFT", "Shift+RIGHT", "UP", "DOWN", "j", "k", "l",
           ",", ".", "f", "ESC", "m", "g", "s", "a", "v", "[", "]", "BS", "HOME", "i",
           "MBTN_LEFT_DBL", "WHEEL_UP", "WHEEL_DOWN", "MOUSE_MOVE", "MBTN_RIGHT"]


def _input_conf():
    path = os.path.join(tempfile.gettempdir(), "nousreel-input.conf")
    with open(path, "w", encoding="utf-8") as f:
        for name in FORWARD:
            f.write(f"{name} script-message nousreel {name}\n")
    return path


class MpvWidget(QWidget):
    """Emits prop(name, value) whenever an observed mpv property changes, and
    key(name) for forwarded keys and mouse actions on the video."""
    prop = Signal(str, object)
    key = Signal(str)
    ready = Signal()
    failed = Signal(str)
    file_loaded = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WA_NativeWindow)
        self.setAttribute(Qt.WA_DontCreateNativeAncestors)
        pal = self.palette(); pal.setColor(QPalette.Window, QColor(0, 0, 0)); self.setPalette(pal)
        self.setAutoFillBackground(True)
        self.setMinimumSize(320, 180)
        self.props = {}
        name = "nousreel-" + uuid.uuid4().hex[:10]
        # a named pipe on Windows, a socket file elsewhere; QLocalSocket takes either
        self._pipe = name if os.name == "nt" else os.path.join(tempfile.gettempdir(), name)
        self._ipc_arg = f"\\\\.\\pipe\\{name}" if os.name == "nt" else self._pipe
        self._sock = QLocalSocket(self)
        self._sock.readyRead.connect(self._read)
        self._sock.connected.connect(self._on_connected)
        self._buf = b""
        self._req = 0
        self._replies = {}
        self._queue = []
        self._proc = None
        self._tries = 0

    # ---- lifecycle -------------------------------------------------------
    def start(self):
        exe = find("mpv")
        if not exe:
            how = "winget install shinchiro.mpv" if os.name == "nt" else "sudo apt install mpv"
            self.failed.emit(f"mpv isn't installed. Install it with:  {how}")
            return
        self._proc = QProcess(self)
        # Only options that older mpv (0.34, Ubuntu 22.04 / Mint 21) also knows:
        # an unknown option makes mpv refuse to start
        args = [f"--wid={int(self.winId())}", f"--input-ipc-server={self._ipc_arg}",
                "--idle=yes", "--force-window=yes", "--keep-open=yes", "--no-config",
                "--osc=no", "--osd-level=1", "--input-default-bindings=no", "--input-vo-keyboard=yes",
                f"--input-conf={_input_conf()}", "--input-cursor=yes", "--cursor-autohide=1200",
                "--cursor-autohide-fs-only=yes", "--hr-seek=yes", "--hwdec=auto-safe",
                "--sub-auto=fuzzy", "--audio-file-auto=no",
                "--screenshot-format=png", "--screenshot-high-bit-depth=no",
                "--osd-font-size=28", "--msg-level=all=warn"]
        self._proc.setProgram(exe)
        self._proc.setArguments(args)
        self._proc.errorOccurred.connect(lambda e: self.failed.emit(f"mpv couldn't start ({e})."))
        self.log = []
        self._proc.readyReadStandardError.connect(
            lambda: self.log.append(bytes(self._proc.readAllStandardError()).decode("utf-8", "replace")))
        self._proc.readyReadStandardOutput.connect(
            lambda: self.log.append(bytes(self._proc.readAllStandardOutput()).decode("utf-8", "replace")))
        self._proc.start()
        QTimer.singleShot(150, self._connect)

    def _connect(self):
        if self._sock.state() == QLocalSocket.ConnectedState:
            return
        self._tries += 1
        if self._tries > 60:
            self.failed.emit("Couldn't talk to mpv (its control pipe never opened).")
            return
        self._sock.abort()
        self._sock.connectToServer(self._pipe)
        if not self._sock.waitForConnected(100):
            QTimer.singleShot(100, self._connect)

    def _on_connected(self):
        for i, name in enumerate(OBSERVED):
            self._send({"command": ["observe_property", i + 1, name]})
        for msg in self._queue:
            self._send(msg)
        self._queue.clear()
        self.ready.emit()

    def shutdown(self):
        if self._sock.state() == QLocalSocket.ConnectedState:
            self._send({"command": ["quit"]})
            self._sock.flush()
        if self._proc and not self._proc.waitForFinished(1500):
            self._proc.kill()
            self._proc.waitForFinished(1000)

    # ---- IPC -------------------------------------------------------------
    def _send(self, msg):
        if self._sock.state() != QLocalSocket.ConnectedState:
            self._queue.append(msg)
            return
        self._sock.write(json.dumps(msg).encode("utf-8") + b"\n")

    def command(self, *args, callback=None):
        msg = {"command": list(args)}
        if callback:
            self._req += 1
            msg["request_id"] = self._req
            self._replies[self._req] = callback
        self._send(msg)

    def set(self, name, value):
        self.command("set_property", name, value)

    def _read(self):
        self._buf += bytes(self._sock.readAll())
        while b"\n" in self._buf:
            line, self._buf = self._buf.split(b"\n", 1)
            try:
                msg = json.loads(line)
            except ValueError:
                continue
            if msg.get("event") == "property-change":
                self.props[msg["name"]] = msg.get("data")
                self.prop.emit(msg["name"], msg.get("data"))
            elif msg.get("event") == "client-message":
                args = msg.get("args") or []
                if len(args) == 2 and args[0] == "nousreel":
                    self.key.emit(args[1])
            elif msg.get("event") == "file-loaded":
                self.file_loaded.emit()
            elif "request_id" in msg:
                cb = self._replies.pop(msg["request_id"], None)
                if cb:
                    cb(msg.get("error"), msg.get("data"))

    # ---- conveniences ----------------------------------------------------
    def load(self, path, sub_files=()):
        self.command("loadfile", path, "replace")
        for s in sub_files:
            self.command("sub-add", s, "auto")

    def toggle_pause(self):
        self.command("cycle", "pause")

    def frame_step(self, back=False):
        self.command("frame-back-step" if back else "frame-step")

    def seek(self, seconds, mode="absolute+exact"):
        self.command("seek", seconds, mode)

    def screenshot(self, path, callback=None):
        # "video" = the decoded frame at its own resolution, no subtitles or OSD
        self.command("screenshot-to-file", path, "video", callback=callback)

    def osd(self, text, ms=1200):
        self.command("show-text", text, ms)

    def tracks(self, kind):
        return [t for t in (self.props.get("track-list") or []) if t.get("type") == kind]
