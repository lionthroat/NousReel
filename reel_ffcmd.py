"""Building ffmpeg command lines. Pure functions (no Qt), so the dialogs can show
the exact command and the tests can check it without running anything.

Every builder returns the arguments *without* the output file; the job runner
adds `-f matroska <partial file>` and renames it when ffmpeg finishes."""
import os
import subprocess

from reel_media import COMPACT_AUDIO, IMAGE_SUBS
from reel_tools_path import find, no_window

# ---- the dials -------------------------------------------------------------
VIDEO_CODECS = {
    # key: (label, ffmpeg encoder, quality range, default quality)
    "x265": ("H.265 / HEVC (small files, slower)", "libx265", (16, 34), 23),
    "x264": ("H.264 / AVC (plays everywhere)", "libx264", (14, 32), 21),
    "av1": ("AV1 (smallest, newest, slowest)", "libsvtav1", (18, 50), 30),
    "nvenc_hevc": ("H.265 on the graphics card (fast)", "hevc_nvenc", (16, 38), 26),
    "nvenc_h264": ("H.264 on the graphics card (fast)", "h264_nvenc", (14, 36), 23),
    "copy": ("Don't re-encode the picture (copy it)", None, (0, 0), 0),
}
SPEEDS = ["Fastest", "Fast", "Balanced", "Slow", "Slowest"]
_SPEED_MAP = {
    "libx264": ["veryfast", "faster", "medium", "slow", "slower"],
    "libx265": ["veryfast", "faster", "medium", "slow", "slower"],
    "libsvtav1": ["10", "8", "6", "5", "4"],
    "hevc_nvenc": ["p2", "p3", "p5", "p6", "p7"],
    "h264_nvenc": ["p2", "p3", "p5", "p6", "p7"],
}
HEIGHTS = [("Keep the original size", 0), ("1080p", 1080), ("720p", 720), ("576p (PAL DVD)", 576),
           ("480p (NTSC DVD)", 480)]
DEINTERLACE = [("Off", "off"), ("Only frames that need it", "auto"), ("Every frame", "all")]
DENOISE = [("Off", "off"), ("Light", "light"), ("Medium", "medium"), ("Strong", "strong")]
_DENOISE_FILTERS = {"light": "hqdn3d=1.5:1.5:6:6", "medium": "hqdn3d=3:2.5:8:7",
                    "strong": "hqdn3d=5:4:11:9"}
GRAIN = [("Keep it", "keep"), ("Let the encoder smooth it", "smooth")]
AUDIO_MODES = [("Smart: copy if compact, else convert to AAC", "auto"), ("Copy as-is", "copy"),
               ("AAC (most compatible)", "aac"), ("Opus (smallest)", "opus"),
               ("Dolby Digital AC-3", "ac3")]

DEFAULT_SETTINGS = {"codec": "x265", "quality": 23, "speed": "Balanced", "height": 0,
                    "deinterlace": "off", "denoise": "off", "grain": "keep",
                    "audio_mode": "auto", "audio_kbps": 0, "stereo": False}

PRESETS = [
    ("Plex 720p · small (H.265)",
     "Shrinks a Blu-ray or big rip to around 2–4 GB for a feature film. Looks very close to the "
     "original on a TV. Plays on nearly every Plex app made since 2017.",
     {"codec": "x265", "quality": 24, "height": 720}),
    ("Plex 720p · plays everywhere (H.264)",
     "A bit bigger than the H.265 version, but H.264 plays on absolutely everything, including "
     "very old TVs, browsers and phones.",
     {"codec": "x264", "quality": 21, "height": 720}),
    ("Plex 1080p · good (H.265)",
     "Keeps full HD. For a Blu-ray remux this usually lands around 4–7 GB.",
     {"codec": "x265", "quality": 22, "height": 1080}),
    ("Same picture size, smaller file (H.265)",
     "Doesn't change the resolution, just compresses more efficiently. Good for files that were "
     "saved at a wastefully high bitrate.",
     {"codec": "x265", "quality": 23, "height": 0}),
    ("Fast on the graphics card (H.265 720p)",
     "Uses your graphics card's video encoder, so it's several times faster. Files come out "
     "somewhat bigger than the CPU presets at the same look.",
     {"codec": "nvenc_hevc", "quality": 27, "height": 720, "speed": "Slow"}),
    ("Old film or DVD rip cleanup (H.264)",
     "For messy public-domain finds: fixes interlacing ‘comb’ lines if there are any, gently "
     "smooths noise (noise wastes a lot of file size), and makes a 480p file.",
     {"codec": "x264", "quality": 20, "height": 480, "deinterlace": "auto", "denoise": "light"}),
    ("Just fix the tracks (no re-encode)",
     "Copies the picture untouched. Only the tracks you keep and the audio choice change. "
     "Fast, and saves space when a file has bulky extra audio tracks.",
     {"codec": "copy"}),
]


def preset_settings(i):
    s = dict(DEFAULT_SETTINGS)
    s.update(PRESETS[i][2])
    return s


# ---- plain-English explanations shown next to the dials ----------------------
def explain(dial, value):
    if dial == "codec":
        return {
            "x265": "The codec is the method used to compress the picture. H.265 makes files about "
                    "half the size of H.264 at the same look, but takes longer to encode.",
            "x264": "H.264 is the most widely supported codec there is. Files are bigger than H.265, "
                    "but it's fast to encode and plays on everything.",
            "av1": "AV1 is the newest codec: smallest files, but very slow to encode, and some older "
                   "TVs and Plex clients can't play it without converting it on the fly.",
            "nvenc_hevc": "Uses the video encoder built into your graphics card. Much faster, but "
                          "less efficient than the CPU encoder, so files are a little bigger.",
            "nvenc_h264": "H.264 on your graphics card: the fastest option, biggest files of the "
                          "re-encoding choices.",
            "copy": "The picture is copied bit-for-bit, with no quality change. Only the audio "
                    "and track choices apply. Resolution and the picture dials are switched off.",
        }[value]
    if dial == "quality":
        return ("This is the quality target (called CRF, or CQ on the graphics card). Lower = better "
                "picture and bigger file. Each step of about 6 roughly halves or doubles the size. "
                "Unlike a fixed bitrate, the encoder spends more bits on busy scenes and fewer on "
                "still ones.")
    if dial == "speed":
        return ("How hard the encoder works to find savings. Slower settings make smaller files at "
                "the same quality, but the gains shrink fast: Slow takes about twice as long as "
                "Balanced for maybe 5–10% smaller.")
    if dial == "height":
        return ("Picture height in lines. 720p has about half the pixels of 1080p. On a TV across "
                "the room, a good 720p encode is hard to tell from 1080p. Videos are never "
                "enlarged: if the source is smaller, it stays as it is.")
    if dial == "deinterlace":
        return ("Old TV and DVD video was sometimes stored as two half-pictures woven together "
                "(interlaced). On a computer that shows as comb-like lines on movement. This "
                "un-weaves it. ‘Only frames that need it’ is safe to leave on.")
    if dial == "denoise":
        return ("Film grain and video noise are random, so they're expensive to compress. Gentle "
                "smoothing can shrink files a lot, but too much makes faces look waxy. Light is "
                "usually invisible; Strong is for very rough tapes.")
    if dial == "grain":
        return ("Tells the encoder whether to work at preserving film grain (bigger files, more "
                "‘film’ look) or let it smooth it away (smaller files).")
    if dial == "audio_mode":
        return ("Lossless Blu-ray audio (DTS-HD MA, TrueHD) can be 3–5 GB on its own. Converting to "
                "AAC at a sensible bitrate sounds the same to almost everyone and is a fraction of "
                "the size. Smart mode leaves already-compact audio (AC-3, AAC, MP3) alone.")
    return ""


# ---- builders ----------------------------------------------------------------
def default_choices(info):
    """One dict per stream of the main file: keep it? its language/title/flags."""
    out = []
    for s in info.streams:
        out.append({"input": 0, "index": s.index, "kind": s.kind, "codec": s.codec,
                    # Cover art is a one-picture stream that stalls trimmed jobs, and Plex
                    # uses its own posters anyway, so it starts unticked
                    "keep": s.kind in ("video", "audio", "subtitle") and not s.attached_pic,
                    "lang": s.lang, "title": s.title, "default": s.default, "forced": s.forced,
                    "channels": s.channels, "bitrate": s.bitrate, "attached_pic": s.attached_pic,
                    "label": s.describe()})
    return out


def external_sub_choice(input_no, path, lang="eng"):
    return {"input": input_no, "index": 0, "kind": "subtitle", "codec": _sub_codec_from_ext(path),
            "keep": True, "lang": lang, "title": "", "default": False, "forced": False,
            "channels": 0, "bitrate": 0, "attached_pic": False, "path": path,
            "label": f"Subtitle file: {os.path.basename(path)}"}


def _sub_codec_from_ext(path):
    ext = os.path.splitext(path)[1].lower()
    return {".srt": "subrip", ".ass": "ass", ".ssa": "ass", ".vtt": "webvtt", ".sup": "hdmv_pgs_subtitle",
            ".sub": "dvd_subtitle"}.get(ext, "subrip")


def _inputs(src, choices):
    args = []
    if os.path.splitext(src)[1].lower() == ".avi":
        args += ["-fflags", "+genpts"]
    args += ["-i", src]
    extra = sorted({c["input"]: c.get("path") for c in choices if c["input"] > 0}.items())
    for _, path in extra:
        args += ["-i", path]
    return args


def _sub_codec(c):
    if c["codec"] == "mov_text":
        return "srt"           # MP4's text format can't live in MKV, but converts losslessly to SRT
    if c["codec"] == "webvtt":
        return "webvtt"
    return "copy"


def _meta(i, c):
    args = ["-metadata:s:%d" % i, "language=" + (c["lang"] or "und"),
            "-metadata:s:%d" % i, "title=" + (c["title"] or "")]
    disp = [d for d, on in (("default", c["default"]), ("forced", c["forced"])) if on]
    if c.get("attached_pic"):
        disp.append("attached_pic")
    args += ["-disposition:%d" % i, "+".join(disp) if disp else "0"]
    return args


def build_remux(src, choices):
    """Change which tracks are in the file, their labels, add subtitle files. No re-encoding."""
    args = ["-hide_banner", "-y"] + _inputs(src, choices)
    tail = []
    out_i = 0
    for c in choices:
        if not c["keep"]:
            continue
        args += ["-map", f"{c['input']}:{c['index']}"]
        codec = _sub_codec(c) if c["kind"] == "subtitle" else "copy"
        tail += [f"-c:{out_i}", codec] + _meta(out_i, c)
        out_i += 1
    return args + ["-map_chapters", "0", "-map_metadata", "0"] + tail


def audio_plan(c, settings):
    """(ffmpeg codec, kbps or 0) for one kept audio track."""
    mode = settings.get("audio_mode", "auto")
    ch = 2 if settings.get("stereo") else (c.get("channels") or 2)
    kbps = settings.get("audio_kbps") or {1: 96, 2: 160, 6: 384, 8: 512}.get(ch, 64 * ch)
    if mode == "auto":
        compact = c["codec"] in COMPACT_AUDIO and (not c.get("bitrate") or c["bitrate"] <= 640_000)
        if compact and not settings.get("stereo"):
            return "copy", 0
        return "aac", kbps
    if mode == "copy":
        return "copy", 0
    if mode == "opus":
        return "libopus", settings.get("audio_kbps") or {1: 64, 2: 128, 6: 256, 8: 320}.get(ch, 48 * ch)
    if mode == "ac3":
        return "ac3", min(max(kbps, 192), 640)
    return "aac", kbps


def video_filters(src_height, settings):
    f = []
    if settings.get("deinterlace") == "auto":
        f.append("bwdif=mode=send_frame:deint=interlaced")
    elif settings.get("deinterlace") == "all":
        f.append("bwdif=mode=send_frame:deint=all")
    if settings.get("denoise") in _DENOISE_FILTERS:
        f.append(_DENOISE_FILTERS[settings["denoise"]])
    h = settings.get("height") or 0
    if h and src_height and src_height > h:
        f.append(f"scale=-2:{h}:flags=lanczos")
    return f


def video_args(i, settings, src_height):
    key = settings.get("codec", "x265")
    enc = VIDEO_CODECS[key][1]
    if enc is None:
        return [f"-c:{i}", "copy"]
    speed = _SPEED_MAP[enc][SPEEDS.index(settings.get("speed", "Balanced"))]
    q = str(settings.get("quality", VIDEO_CODECS[key][3]))
    args = []
    filt = video_filters(src_height, settings)
    if filt:
        args += [f"-filter:{i}", ",".join(filt)]
    args += [f"-c:{i}", enc]
    if enc in ("libx264", "libx265"):
        args += ["-crf", q, "-preset", speed]
        if settings.get("grain") == "keep":
            args += ["-tune", "grain"] if enc == "libx265" else ["-tune", "film"]
        if enc == "libx265":
            args += ["-x265-params", "log-level=error"]
    elif enc == "libsvtav1":
        args += ["-crf", q, "-preset", speed]
        if settings.get("grain") == "keep":
            args += ["-svtav1-params", "film-grain=8"]
    else:  # nvenc
        args += ["-rc", "vbr", "-cq", q, "-b:v", "0", "-preset", speed, "-spatial-aq", "1"]
    args += ["-pix_fmt", "yuv420p"]
    return args


def build_shrink(info, choices, settings):
    src = info.path
    args = ["-hide_banner", "-y"] + _inputs(src, choices)
    tail = []
    out_i = 0
    src_h = info.video.height if info.video else 0
    for c in choices:
        if not c["keep"]:
            continue
        args += ["-map", f"{c['input']}:{c['index']}"]
        if c["kind"] == "video" and not c.get("attached_pic"):
            tail += video_args(out_i, settings, src_h)
        elif c["kind"] == "audio":
            codec, kbps = audio_plan(c, settings)
            tail += [f"-c:{out_i}", codec]
            if kbps:
                tail += [f"-b:{out_i}", f"{kbps}k"]
            if codec != "copy" and settings.get("stereo") and (c.get("channels") or 2) > 2:
                tail += [f"-ac:{out_i}", "2"]
            elif codec == "libopus" and (c.get("channels") or 2) > 2:
                tail += [f"-mapping_family:{out_i}", "1"]
        elif c["kind"] == "subtitle":
            tail += [f"-c:{out_i}", _sub_codec(c)]
        else:
            tail += [f"-c:{out_i}", "copy"]
        tail += _meta(out_i, c)
        out_i += 1
    return args + ["-map_chapters", "0", "-map_metadata", "0"] + tail


def join_problems(infos):
    """Reasons the parts can't simply be stitched together without re-encoding."""
    first = infos[0]
    probs = []
    def sig(info):
        v = info.video
        a = info.of("audio")
        return {
            "video codec": v.codec if v else None,
            "picture size": (v.width, v.height) if v else None,
            "frame rate": round(v.fps, 2) if v else None,
            "audio tracks": len(a),
            "audio codec": tuple(x.codec for x in a),
            "audio channels": tuple(x.channels for x in a),
            "audio sample rate": tuple(x.sample_rate for x in a),
        }
    base = sig(first)
    for n, info in enumerate(infos[1:], start=2):
        s = sig(info)
        for k in base:
            if s[k] != base[k]:
                probs.append(f"Part {n} has a different {k} ({_fmt(s[k])} vs {_fmt(base[k])}).")
    return probs


def _fmt(v):
    if isinstance(v, tuple) and len(v) == 2 and all(isinstance(x, int) for x in v):
        return f"{v[0]}×{v[1]}"
    if isinstance(v, tuple):
        return ", ".join(str(x) for x in v) or "none"
    return str(v)


def write_concat_list(paths, list_path):
    with open(list_path, "w", encoding="utf-8") as f:
        for p in paths:
            f.write("file '" + p.replace("\\", "/").replace("'", r"'\''") + "'\n")


def build_join_copy(paths, list_path, write=True):
    if write:  # previews only show the command
        write_concat_list(paths, list_path)
    args = ["-hide_banner", "-y"]
    if any(p.lower().endswith(".avi") for p in paths):
        args += ["-fflags", "+genpts"]
    return args + ["-f", "concat", "-safe", "0", "-i", list_path, "-map", "0", "-c", "copy"]


def build_join_encode(infos, quality=20):
    """Different formats: decode every part, scale to part 1's size, encode once."""
    v0 = infos[0].video
    w, h = (v0.width, v0.height) if v0 else (1280, 720)
    args = ["-hide_banner", "-y"]
    for info in infos:
        args += ["-i", info.path]
    parts = []
    for n in range(len(infos)):
        parts.append(f"[{n}:v:0]scale={w}:{h}:force_original_aspect_ratio=decrease,"
                     f"pad={w}:{h}:(ow-iw)/2:(oh-ih)/2,setsar=1,fps={v0.fps if v0 and v0.fps else 24}[v{n}];"
                     f"[{n}:a:0]aresample=48000,aformat=channel_layouts=stereo[a{n}]")
    concat = "".join(f"[v{n}][a{n}]" for n in range(len(infos)))
    fc = ";".join(parts) + f";{concat}concat=n={len(infos)}:v=1:a=1[v][a]"
    return args + ["-filter_complex", fc, "-map", "[v]", "-map", "[a]",
                   "-c:v", "libx264", "-crf", str(quality), "-preset", "medium", "-pix_fmt", "yuv420p",
                   "-c:a", "aac", "-b:a", "192k"]


def display_command(args, out="output.mkv"):
    """The command as you'd type it, for the 'what's happening' box."""
    def q(a):
        a = str(a)
        return f'"{a}"' if (not a or any(ch in a for ch in ' ;[]()&|\'')) else a
    return "ffmpeg " + " ".join(q(a) for a in args) + " " + q(out)


_nvenc = None


def nvenc_available():
    """Whether this machine's graphics card can encode H.265 (checked once)."""
    global _nvenc
    if _nvenc is None:
        _nvenc = _probe_nvenc()
    return _nvenc


def _probe_nvenc():
    exe = find("ffmpeg")
    if not exe:
        return False
    try:
        r = subprocess.run([exe, "-hide_banner", "-f", "lavfi", "-i", "color=black:s=256x256:d=0.1",
                            "-c:v", "hevc_nvenc", "-f", "null", "-"], capture_output=True,
                           timeout=15, creationflags=no_window())
        return r.returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def is_image_sub(c):
    return c["codec"] in IMAGE_SUBS
