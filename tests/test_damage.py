"""Check-for-damage on three copies of a one-minute clip: clean, with two
stretches of bytes scrambled (like scratches), and cut off halfway.

  .venv\\Scripts\\python.exe tests\\test_damage.py "<a video>"
"""
import os
import random
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "out", "damage")
sys.path.insert(0, os.path.dirname(HERE))
sys.stdout.reconfigure(encoding="utf-8")
os.makedirs(OUT, exist_ok=True)

from PySide6.QtCore import QCoreApplication
import reel_damage as D
from reel_media import probe
from reel_tools_path import find

fails = []


def check(label, ok):
    print(("PASS " if ok else "FAIL ") + label, flush=True)
    if not ok:
        fails.append(label)


# unit: grouping and classifying
check("h264 line is picture", D.classify("[h264 @ 0000020a] error while decoding MB 4 5") == "picture")
check("ac3 line is sound", D.classify("[ac3 @ 0x55] frame sync error") == "sound")
check("demuxer line is file", D.classify("[matroska,webm @ 0x1] Invalid EBML") == "file")
spots = D.group_spots([(10.0, "picture", "a"), (11.5, "picture", "b"), (30.0, "sound", "c")])
check("errors 1.5 s apart merge, 18 s apart don't", len(spots) == 2 and spots[0]["count"] == 2)

src = sys.argv[1]
clean = os.path.join(OUT, "clean.mkv")
subprocess.run([find("ffmpeg"), "-nostdin", "-v", "error", "-y", "-ss", "600", "-t", "60", "-i", src,
                "-map", "0:v:0", "-map", "0:a:0", "-c", "copy", clean], check=True, stdin=subprocess.DEVNULL)
size = os.path.getsize(clean)

scratched = os.path.join(OUT, "scratched.mkv")
shutil.copy(clean, scratched)
rng = random.Random(7)
with open(scratched, "r+b") as f:
    for frac in (0.35, 0.7):
        f.seek(int(size * frac))
        f.write(bytes(rng.randrange(256) for _ in range(40_000)))

cut = os.path.join(OUT, "cut.mkv")
with open(clean, "rb") as a, open(cut, "wb") as b:
    b.write(a.read(size // 2))

app = QCoreApplication([])


def run(path):
    box = {}
    chk = D.DamageCheck(probe(path))
    chk.finished.connect(lambda r: (box.update(r), app.quit()))
    chk.start()
    app.exec()
    return box


r = run(clean)
check(f"clean copy: no spots, not cut off ({D.summary(r)[:40]}…)", not r["spots"] and not r["ends_early"])
r = run(scratched)
starts = [round(s["start"]) for s in r["spots"]]
check(f"scratched copy: trouble spots found at {starts}s ({r['errors']} errors)", len(r["spots"]) >= 2)
check("spots are near the scrambled places (~21 s and ~42 s)",
      any(15 <= t <= 28 for t in starts) and any(36 <= t <= 50 for t in starts))
r = run(cut)
check(f"cut-off copy flagged: reached {r['reached']:.0f}s of {r['duration']:.0f}s", r["ends_early"])

print(f"\n{'all passed' if not fails else str(len(fails)) + ' failed'}")
sys.exit(1 if fails else 0)
