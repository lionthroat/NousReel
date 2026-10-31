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
check("output-side complaints aren't damage", D.is_output_noise(
    "[null @ 0x55623f503e80] Application provided invalid, non monotonically increasing dts to muxer in stream 0: 2 >= 2")
      and not D.is_output_noise("[h264 @ 0x1] error while decoding MB 4 5"))
spots = D.group_spots([(10.0, "picture", "a"), (11.5, "picture", "b"), (30.0, "sound", "c")])
check("errors 1.5 s apart merge, 18 s apart don't", len(spots) == 2 and spots[0]["count"] == 2)

src = sys.argv[1]
clean = os.path.join(OUT, "clean.mkv")
# up to a minute from 10 minutes in, or most of a short sample (CI's is 20 s)
src_len = probe(src).duration
# (a short sample is cut from the very start: a copy cut mid-way between keyframes
# really is damaged at its first frame, and older ffmpeg says so)
start = min(600.0, src_len * 0.1) if src_len > 120 else 0.0
length = min(60.0, src_len * 0.8)
subprocess.run([find("ffmpeg"), "-nostdin", "-v", "error", "-y", "-ss", f"{start:.2f}", "-t", f"{length:.2f}",
                "-i", src, "-map", "0:v:0", "-map", "0:a:0", "-c", "copy", clean], check=True,
               stdin=subprocess.DEVNULL)
size = os.path.getsize(clean)
clip_len = probe(clean).duration

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
for s in r["spots"]:
    print(f"   clean-copy spot at {s['start']:.2f}s: {s['sample']}")
r = run(scratched)
starts = [round(s["start"]) for s in r["spots"]]
check(f"scratched copy: trouble spots found at {starts}s ({r['errors']} errors)", len(r["spots"]) >= 2)
# bytes 35% and 70% into the file land roughly 35% and 70% into the clip
near = [clip_len * 0.35, clip_len * 0.7]
slack = max(3.0, clip_len * 0.2)
check(f"spots are near the scrambled places (~{near[0]:.0f} s and ~{near[1]:.0f} s)",
      all(any(abs(t - n) <= slack for t in starts) for n in near))
r = run(cut)
check(f"cut-off copy flagged: reached {r['reached']:.0f}s of {r['duration']:.0f}s", r["ends_early"])

print(f"\n{'all passed' if not fails else str(len(fails)) + ' failed'}")
sys.exit(1 if fails else 0)
