"""Reading what's inside a media file (ffprobe), plus small helpers for names,
times and sizes that the dialogs share."""
import json
import os
import re
import subprocess

from reel_tools_path import find, no_window

LANGS = [("eng", "English"), ("spa", "Spanish"), ("fre", "French"), ("ger", "German"),
         ("ita", "Italian"), ("por", "Portuguese"), ("jpn", "Japanese"), ("rus", "Russian"),
         ("chi", "Chinese"), ("kor", "Korean"), ("gre", "Greek"), ("swe", "Swedish"),
         ("dut", "Dutch"), ("pol", "Polish"), ("ara", "Arabic"), ("hin", "Hindi"),
         ("und", "Unknown")]
LANG_NAMES = dict(LANGS)
LANG_NAMES.update({"en": "English", "fra": "French", "deu": "German", "zho": "Chinese",
                   "nld": "Dutch", "ell": "Greek", "es": "Spanish", "ja": "Japanese"})

TEXT_SUBS = {"subrip", "ass", "ssa", "mov_text", "webvtt", "text"}
IMAGE_SUBS = {"hdmv_pgs_subtitle", "dvd_subtitle", "dvb_subtitle", "xsub"}
# Audio formats that are already compact enough to copy through untouched
COMPACT_AUDIO = {"aac", "ac3", "eac3", "opus", "mp3", "vorbis", "mp2"}

CODEC_NAMES = {
    "h264": "H.264 (AVC)", "hevc": "H.265 (HEVC)", "av1": "AV1", "mpeg4": "MPEG-4 Part 2 (XviD/DivX)",
    "mpeg2video": "MPEG-2 (DVD)", "vc1": "VC-1", "vp9": "VP9", "mjpeg": "JPEG image",
    "aac": "AAC", "ac3": "Dolby Digital (AC-3)", "eac3": "Dolby Digital Plus", "dts": "DTS",
    "truehd": "Dolby TrueHD", "mp3": "MP3", "opus": "Opus", "flac": "FLAC", "pcm_s16le": "PCM (uncompressed)",
    "pcm_s24le": "PCM (uncompressed)", "vorbis": "Vorbis", "mp2": "MP2",
    "subrip": "SRT text", "ass": "ASS styled text", "mov_text": "MP4 text", "webvtt": "WebVTT text",
    "hdmv_pgs_subtitle": "Blu-ray PGS (pictures)", "dvd_subtitle": "DVD VobSub (pictures)",
}


class ProbeError(Exception):
    pass


def probe(path):
    exe = find("ffprobe")
    if not exe:
        raise ProbeError("ffprobe isn't installed (it comes with ffmpeg: winget install Gyan.FFmpeg).")
    r = subprocess.run([exe, "-v", "error", "-show_format", "-show_streams", "-show_chapters",
                        "-of", "json", path], capture_output=True, creationflags=no_window())
    if r.returncode != 0:
        raise ProbeError(r.stderr.decode("utf-8", "replace").strip() or "ffprobe couldn't read this file.")
    data = json.loads(r.stdout.decode("utf-8", "replace"))
    return MediaInfo(path, data)


class Stream:
    def __init__(self, d):
        self.d = d
        self.index = d.get("index", 0)
        self.kind = d.get("codec_type", "")
        self.codec = d.get("codec_name", "?")
        self.profile = d.get("profile", "")
        tags = {k.lower(): v for k, v in (d.get("tags") or {}).items()}
        self.tags = tags
        self.lang = tags.get("language", "und")
        self.title = tags.get("title", "")
        disp = d.get("disposition") or {}
        self.default = bool(disp.get("default"))
        self.forced = bool(disp.get("forced"))
        self.attached_pic = bool(disp.get("attached_pic")) or self.kind == "attachment"
        self.width = d.get("width") or 0
        self.height = d.get("height") or 0
        self.channels = d.get("channels") or 0
        self.sample_rate = int(d.get("sample_rate") or 0)
        self.bitrate = _int(d.get("bit_rate")) or _int(tags.get("bps")) or _int(tags.get("bps-eng"))
        fr = d.get("avg_frame_rate") or d.get("r_frame_rate") or "0/1"
        try:
            n, m = fr.split("/")
            self.fps = float(n) / float(m) if float(m) else 0.0
        except ValueError:
            self.fps = 0.0
        self.field_order = d.get("field_order", "")

    @property
    def is_text_sub(self):
        return self.kind == "subtitle" and self.codec in TEXT_SUBS

    def codec_label(self):
        name = CODEC_NAMES.get(self.codec, self.codec.upper())
        if self.codec == "dts" and self.profile and self.profile not in ("DTS", "unknown"):
            name = self.profile.replace("DTS-HD MA", "DTS-HD Master Audio (lossless)")
        return name

    def lang_label(self):
        return LANG_NAMES.get(self.lang, self.lang)

    def channels_label(self):
        return {1: "mono", 2: "stereo", 6: "5.1 surround", 8: "7.1 surround"}.get(
            self.channels, f"{self.channels} ch")

    def describe(self):
        if self.kind == "video" and self.attached_pic:
            return f"Cover art ({self.width}×{self.height} {self.codec_label()})"
        if self.kind == "video":
            s = f"{self.codec_label()}  {self.width}×{self.height}"
            if self.fps:
                s += f"  {self.fps:.3f} fps"
            return s
        if self.kind == "audio":
            s = f"{self.codec_label()}  {self.channels_label()}"
        elif self.kind == "subtitle":
            s = self.codec_label()
        else:
            s = self.codec_label()
        s += f"  ·  {self.lang_label()}"
        if self.title:
            s += f"  ·  “{self.title}”"
        return s


class MediaInfo:
    def __init__(self, path, data):
        self.path = path
        self.data = data
        fmt = data.get("format") or {}
        self.format_name = fmt.get("format_long_name") or fmt.get("format_name", "")
        self.duration = float(fmt.get("duration") or 0)
        self.size = int(fmt.get("size") or 0) or (os.path.getsize(path) if os.path.exists(path) else 0)
        self.bitrate = _int(fmt.get("bit_rate")) or (int(self.size * 8 / self.duration) if self.duration else 0)
        self.streams = [Stream(s) for s in data.get("streams") or []]
        self.chapters = data.get("chapters") or []

    def of(self, kind):
        return [s for s in self.streams if s.kind == kind and not (kind == "video" and s.attached_pic)]

    @property
    def video(self):
        v = self.of("video")
        return v[0] if v else None

    def container_label(self):
        ext = os.path.splitext(self.path)[1].lower()
        return {".mkv": "Matroska (MKV)", ".mp4": "MP4", ".m4v": "MP4", ".avi": "AVI",
                ".mov": "QuickTime (MOV)", ".webm": "WebM", ".ts": "MPEG transport stream",
                ".m2ts": "Blu-ray transport stream", ".vob": "DVD VOB", ".wmv": "Windows Media",
                ".mpg": "MPEG program stream"}.get(ext, self.format_name)

    def summary(self):
        v = self.video
        bits = [self.container_label(), fmt_size(self.size), fmt_time(self.duration, frac=False)]
        if v:
            bits.insert(1, f"{v.codec_label()} {v.width}×{v.height}")
        bits.append(f"{self.bitrate / 1e6:.1f} Mbps overall")
        return "  ·  ".join(bits)


def sample_bitrates(path, duration, seconds=60):
    """Measure each stream's bitrate from a slice in the middle of the file:
    {stream_index: bits_per_second}. Fast because it reads packets, not pixels."""
    exe = find("ffprobe")
    if not exe or duration <= 0:
        return {}
    span = min(seconds, max(duration * 0.5, 1))
    start = max(0.0, duration * 0.35)
    r = subprocess.run([exe, "-v", "error", "-read_intervals", f"{start:.1f}%+{span:.1f}",
                        "-show_entries", "packet=stream_index,size", "-of", "csv=p=0", path],
                       capture_output=True, creationflags=no_window())
    totals = {}
    for line in r.stdout.decode("ascii", "replace").splitlines():
        parts = line.split(",")
        if len(parts) >= 2 and parts[0].isdigit() and parts[1].isdigit():
            i = int(parts[0]); totals[i] = totals.get(i, 0) + int(parts[1])
    return {i: b * 8 / span for i, b in totals.items()}


# ---- small helpers ---------------------------------------------------------
def _int(v):
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return 0


def fmt_size(n):
    n = float(n or 0)
    for unit in ("bytes", "KB", "MB", "GB", "TB"):
        if n < 1000 or unit == "TB":
            return f"{n:.0f} {unit}" if unit in ("bytes", "KB") else f"{n:.1f} {unit}" if n < 100 else f"{n:.0f} {unit}"
        n /= 1000
    return f"{n:.1f} TB"


def fmt_time(t, frac=True):
    t = max(0.0, float(t or 0))
    h, rem = divmod(t, 3600)
    m, s = divmod(rem, 60)
    if frac:
        return f"{int(h):02d}:{int(m):02d}:{s:06.3f}"
    return f"{int(h)}:{int(m):02d}:{int(s):02d}"


def plex_name(path):
    """'Chop Shop.2007.1080p.mkv' -> 'Chop Shop (2007)'. Plex matches on Title (Year)."""
    stem = os.path.splitext(os.path.basename(path))[0]
    stem = re.sub(r"[._]+", " ", stem)
    stem = re.sub(r"\s*[-(\[]?\s*(cd|disc|disk|part|pt)\s*\d+\s*[)\]]?\s*$", "", stem, flags=re.I)
    m = re.match(r"^(.*?)[\s(\[]+((?:18|19|20)\d{2})(?=[\s)\]]|$)", stem)
    if m:
        title, year = m.group(1), m.group(2)
        title = re.sub(r"\s+", " ", title).strip(" -([")
        return f"{title} ({year})"
    stem = re.split(r"\s(?:480p|576p|720p|1080p|2160p|x264|x265|h264|hevc|bluray|bdrip|dvdrip|webrip)\b",
                    stem, maxsplit=1, flags=re.I)[0]
    return re.sub(r"\s+", " ", stem).strip()


def unique_path(path):
    if not os.path.exists(path):
        return path
    base, ext = os.path.splitext(path)
    i = 2
    while os.path.exists(f"{base} ({i}){ext}"):
        i += 1
    return f"{base} ({i}){ext}"
