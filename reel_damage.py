"""Check this file for damage: decode every frame of the picture and sound,
collect ffmpeg's decoding errors, and group them into trouble spots with
timestamps. Catches scratched-disc rips, broken downloads and cut-off files."""
import os
import re

from PySide6.QtCore import QObject, QProcess, Signal
from PySide6.QtWidgets import (QDialog, QDialogButtonBox, QHBoxLayout, QLabel, QListWidget,
                               QListWidgetItem, QProgressBar, QPushButton, QVBoxLayout, QWidget)

from reel_media import fmt_time
from reel_tools_path import find

VIDEO_DECODERS = {"h264", "hevc", "mpeg2video", "mpeg4", "mpeg1video", "vc1", "av1", "vp9", "vp8", "msmpeg4v3",
                  "wmv3", "mjpeg", "theora", "h263"}
AUDIO_DECODERS = {"ac3", "eac3", "dca", "dts", "aac", "mp3", "mp3float", "mp2", "mp2float", "truehd", "flac",
                  "opus", "vorbis", "pcm_s16le", "wmav2", "alac"}
MERGE_GAP = 3.0       # errors closer than this many seconds are one trouble spot
_TAG = re.compile(r"^\[(?P<who>[^\]@]+?) @ [0-9a-fA-Fx]+\]")
_PTS = re.compile(r"pkt_pts_time:(-?[\d.]+)")
_DTS = re.compile(r"pkt_dts_time:(-?[\d.]+)")


def classify(line):
    """'picture', 'sound' or 'file' for one ffmpeg error line."""
    m = _TAG.match(line)
    who = m.group("who") if m else ""
    if who.startswith("vist"):
        return "picture"
    if who.startswith("aist"):
        return "sound"
    who = who.split("/")[-1]
    if who in VIDEO_DECODERS:
        return "picture"
    if who in AUDIO_DECODERS:
        return "sound"
    return "file"


def group_spots(events):
    """[(seconds, kind, line)] -> [{start, end, kinds, count, sample}] in time order."""
    spots = []
    for t, kind, line in sorted(events, key=lambda e: e[0]):
        if spots and t - spots[-1]["end"] <= MERGE_GAP:
            s = spots[-1]
            s["end"] = t
            s["kinds"].add(kind)
            s["count"] += 1
        else:
            spots.append({"start": t, "end": t, "kinds": {kind}, "count": 1, "sample": line})
    return spots


class DamageCheck(QObject):
    progress = Signal(float)          # 0..1
    finished = Signal(dict)

    def __init__(self, info):
        super().__init__()
        self.info = info
        self.duration = max(info.duration, 0.001)
        self.t = 0.0
        self.pkt_t = 0.0
        self.events = []
        self.reached = 0.0
        self._buf = b""
        self._err = b""
        self.proc = None
        self.cancelled = False

    def start(self):
        # -debug_ts logs every packet's timestamp as it's read, so each error
        # line can be pinned to the moment in the film it came from
        args = ["-nostdin", "-hide_banner", "-nostats", "-progress", "pipe:1", "-stats_period", "0.5",
                "-loglevel", "level+info", "-debug_ts", "-i", self.info.path, "-map", "0:v:0?", "-map", "0:a?",
                "-f", "null", "-"]
        self.proc = QProcess(self)
        self.proc.setProgram(find("ffmpeg"))
        self.proc.setArguments(args)
        self.proc.readyReadStandardOutput.connect(self._out)
        self.proc.readyReadStandardError.connect(self._errs)
        self.proc.finished.connect(self._done)
        self.proc.start()

    def cancel(self):
        self.cancelled = True
        if self.proc and self.proc.state() != QProcess.NotRunning:
            self.proc.kill()

    def _out(self):
        self._buf += bytes(self.proc.readAllStandardOutput())
        *lines, self._buf = self._buf.split(b"\n")
        for line in lines:
            k, _, v = line.decode("ascii", "replace").strip().partition("=")
            if k == "out_time_us" and v.lstrip("-").isdigit():
                self.t = max(0.0, int(v) / 1e6)
                self.reached = max(self.reached, self.t)
                self.progress.emit(min(1.0, self.t / self.duration))

    def _errs(self):
        self._err += bytes(self.proc.readAllStandardError())
        *lines, self._err = self._err.split(b"\n")
        for raw in lines:
            # "[vist#0:0/h264 @ …] demuxer -> …" on ffmpeg 7+, a bare "demuxer -> …" on 4.x
            if b"] demuxer -> " in raw or raw.lstrip().startswith(b"demuxer -> ") or b"] [info] demuxer -> " in raw:
                m = _PTS.search(raw.decode("ascii", "replace"))
                if not m or m.group(1).startswith("N"):
                    m = _DTS.search(raw.decode("ascii", "replace"))
                if m:
                    try:
                        self.pkt_t = float(m.group(1))
                    except ValueError:
                        pass
                continue
            if b"[error]" not in raw and b"[fatal]" not in raw:
                continue
            line = raw.decode("utf-8", "replace").replace("[error] ", "").replace("[fatal] ", "").strip()
            self.events.append((max(0.0, self.pkt_t), classify(line), line))

    def _done(self, code, _status):
        if self.cancelled:
            return
        short = self.duration - self.reached
        self.finished.emit({
            "spots": group_spots(self.events),
            "errors": len(self.events),
            "reached": self.reached,
            "duration": self.duration,
            "ends_early": short > max(2.0, self.duration * 0.01),
            "exit": code,
        })


def summary(result):
    """Plain-English verdict for a finished check."""
    spots, early = result["spots"], result["ends_early"]
    if not spots and not early:
        return ("<b>✓ No damage found.</b> Every frame of the picture and sound decoded cleanly from "
                "start to finish.")
    parts = []
    if spots:
        n = len(spots)
        parts.append(f"<b>{n} trouble spot{'s' if n != 1 else ''}</b> where the picture or sound couldn't "
                     f"be decoded properly ({result['errors']} errors in all). On a disc rip that usually "
                     f"means a scratch or smudge at that point. You may see blocky squares, a brief freeze "
                     f"or a skip there, or nothing at all if the damage is small enough for the player to "
                     f"cover up. Use Jump to look at each one.")
    if early:
        parts.append(f"<b>The file ends early:</b> it stops at {fmt_time(result['reached'], frac=False)} but "
                     f"says it's {fmt_time(result['duration'], frac=False)} long. The rip or download was "
                     f"probably cut off.")
    return "<br><br>".join(parts)


class DamageDialog(QDialog):
    def __init__(self, info, jump, parent=None):
        super().__init__(parent)
        self.info, self.jump = info, jump
        self.setWindowTitle("Check for damage")
        self.resize(640, 460)
        lay = QVBoxLayout(self)
        lay.addWidget(QLabel(f"<b>{os.path.basename(info.path)}</b>"))
        expl = QLabel("Plays the whole file as fast as your computer can, without showing it, and notes every "
                      "spot where the picture or sound is damaged. You can keep watching while it works.")
        expl.setWordWrap(True)
        expl.setStyleSheet("color: palette(placeholder-text);")
        lay.addWidget(expl)
        self.bar = QProgressBar()
        self.bar.setRange(0, 1000)
        lay.addWidget(self.bar)
        self.status = QLabel("Starting…")
        self.status.setWordWrap(True)
        lay.addWidget(self.status)
        self.list = QListWidget()
        self.list.hide()
        lay.addWidget(self.list, 1)
        bb = QDialogButtonBox(QDialogButtonBox.Close)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)
        self.check = DamageCheck(info)
        self.check.progress.connect(self._progress)
        self.check.finished.connect(self._finished)
        self.check.start()

    def _progress(self, f):
        self.bar.setValue(int(f * 1000))
        found = len(group_spots(self.check.events))
        self.status.setText(f"Checked {fmt_time(f * self.info.duration, frac=False)} of "
                            f"{fmt_time(self.info.duration, frac=False)}"
                            + (f"  ·  {found} trouble spot{'s' if found != 1 else ''} so far" if found else ""))

    def _finished(self, result):
        self.bar.setValue(1000)
        self.status.setText(summary(result))
        self.result = result
        if not result["spots"]:
            return
        self.list.show()
        kinds_word = {"picture": "picture", "sound": "sound", "file": "file structure"}
        for s in result["spots"]:
            what = " + ".join(kinds_word[k] for k in sorted(s["kinds"]))
            span = fmt_time(s["start"], frac=False)
            if s["end"] - s["start"] >= 1:
                span += f" – {fmt_time(s['end'], frac=False)}"
            item = QListWidgetItem()
            self.list.addItem(item)
            row = QHBoxLayout()
            lab = QLabel(f"<b>{span}</b>  {what} errors ({s['count']})")
            lab.setToolTip(s["sample"])
            btn = QPushButton("Jump")
            btn.clicked.connect(lambda _=False, t=s["start"]: self.jump(max(0.0, t - 2)))
            row.addWidget(lab, 1)
            row.addWidget(btn)
            w = QWidget()
            row.setContentsMargins(6, 2, 6, 2)
            w.setLayout(row)
            item.setSizeHint(w.sizeHint())
            self.list.setItemWidget(item, w)

    def reject(self):
        self.check.cancel()
        super().reject()
