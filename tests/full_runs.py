"""Full-length runs on the real samples (slow): lossless join of two parts, and
a whole-film shrink with the first preset. Outputs go to tests/out/ only.

  .venv\\Scripts\\python.exe tests\\full_runs.py join "<part1>" "<part2>"
  .venv\\Scripts\\python.exe tests\\full_runs.py shrink "<video>" [drop-lang ...]
"""
import os
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "out")
sys.path.insert(0, os.path.dirname(HERE))
sys.stdout.reconfigure(encoding="utf-8")
import reel_ffcmd as F
from reel_media import fmt_size, fmt_time, plex_name, probe
from reel_tools_path import find

os.makedirs(OUT, exist_ok=True)
mode, *rest = sys.argv[1:]


def run(args, out):
    t = time.time()
    r = subprocess.run([find("ffmpeg"), "-nostdin", "-loglevel", "error"] + args + ["-f", "matroska", out],
                       stdin=subprocess.DEVNULL, capture_output=True)
    print(f"rc={r.returncode} in {fmt_time(time.time() - t, frac=False)}", flush=True)
    if r.returncode:
        print(r.stderr.decode("utf-8", "replace")[-2000:])
        sys.exit(1)
    q = probe(out)
    print(f"{os.path.basename(out)}: {fmt_size(q.size)}, {fmt_time(q.duration)}")
    for s in q.streams:
        print("  ", s.index, s.describe())
    return q


if mode == "join":
    infos = [probe(p) for p in rest]
    print("problems:", F.join_problems(infos) or "none")
    lst = os.path.join(tempfile.gettempdir(), "nousreel-join-test.txt")
    out = os.path.join(OUT, plex_name(rest[0]) + " (joined).mkv")
    q = run(F.build_join_copy(rest, lst), out)
    want = sum(i.duration for i in infos)
    print(f"parts total {fmt_time(want)} vs joined {fmt_time(q.duration)} (diff {q.duration - want:+.3f}s)")
elif mode == "shrink":
    src, drop = rest[0], set(rest[1:])
    info = probe(src)
    ch = F.default_choices(info)
    for c in ch:
        if c["lang"] in drop:
            c["keep"] = False
    out = os.path.join(OUT, plex_name(src) + " (shrunk).mkv")
    q = run(F.build_shrink(info, ch, F.preset_settings(0)), out)
    print(f"{fmt_size(info.size)} -> {fmt_size(q.size)} ({q.size / info.size * 100:.1f}%)")
