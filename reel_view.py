"""How the picture sits in the window: Fit / Fill / Stretch, zoom, a shape
override for wrongly flagged files, rotation, and cropping off black bars that
are baked into the picture. All of it is mpv properties; nothing re-encodes."""
import re
import subprocess

from reel_tools_path import find, no_window

MODES = [
    ("fit", "Fit", "The whole picture, with black bars where its shape and the window's differ"),
    ("fill", "Fill", "Zoom until the window is full; the edges are cropped"),
    ("stretch", "Stretch", "Fill the window by stretching the picture out of shape"),
]
SHAPES = [
    ("auto", "As the file says", "-1"),
    ("4:3", "4:3 (old TV and classic films)", "4:3"),
    ("16:9", "16:9 (widescreen TV)", "16:9"),
    ("1.85:1", "1.85:1 (widescreen film)", "1.85"),
    ("2.39:1", "2.39:1 (scope / anamorphic film)", "2.39"),
]
ZOOM_STEP = 0.1          # mpv's video-zoom is log2: 0.1 is about 7% per step
ZOOM_LIMIT = 2.0         # up to 4x in or out


class View:
    def __init__(self, d=None):
        d = d or {}
        self.mode = d.get("mode", "fit")
        self.zoom = float(d.get("zoom", 0.0))
        self.shape = d.get("shape", "auto")
        self.rotate = int(d.get("rotate", 0)) % 360
        crop = d.get("crop")
        self.crop = tuple(crop) if crop else None      # (w, h, x, y) in source pixels

    def to_dict(self):
        return {"mode": self.mode, "zoom": round(self.zoom, 3), "shape": self.shape,
                "rotate": self.rotate, "crop": list(self.crop) if self.crop else None}

    def is_default(self):
        return (self.mode == "fit" and abs(self.zoom) < 1e-6 and self.shape == "auto"
                and self.rotate == 0 and not self.crop)

    def label(self):
        """Short text for the control-bar button: 'Fit', 'Fill · 4:3', 'Fit · 1.5×'."""
        bits = [dict((k, n) for k, n, _ in MODES)[self.mode]]
        if self.shape != "auto":
            bits.append(self.shape)
        if abs(self.zoom) > 1e-6:
            bits.append(f"{2 ** self.zoom:.2f}".rstrip("0").rstrip(".") + "×")
        if self.crop:
            bits.append("no bars")
        if self.rotate:
            bits.append(f"{self.rotate}°")
        return " · ".join(bits)

    def apply(self, player):
        player.set("keepaspect", self.mode != "stretch")
        player.set("panscan", 1.0 if self.mode == "fill" else 0.0)
        player.set("video-zoom", self.zoom)
        player.set("video-aspect-override", dict((k, v) for k, _, v in SHAPES)[self.shape])
        player.set("video-rotate", self.rotate)
        apply_crop(player, self.crop)


def apply_crop(player, crop):
    """mpv 0.38+ crops with its video-crop property (keeps hardware decoding);
    older mpv (Ubuntu 22.04 / Mint 21) gets a crop filter, which needs the
    decoded frames copied back to memory."""
    def fallback(err, _data):
        if err == "success":
            return
        if crop:
            player.set("hwdec", "auto-copy-safe")
            player.command("vf", "set", "crop={}:{}:{}:{}".format(*crop))
        else:
            player.command("vf", "clr", "")
            player.set("hwdec", "auto-safe")
    value = "{}x{}+{}+{}".format(*crop) if crop else ""
    player.command("set_property", "video-crop", value, callback=fallback)


_CROP = re.compile(rb"crop=(\d+):(\d+):(\d+):(\d+)")


def detect_bars(path, duration, width, height, points=(0.2, 0.45, 0.7), seconds=2.0):
    """Look at a few moments of the film for black bars baked into the picture.
    Returns (w, h, x, y) to crop to, or None if there are none worth removing.
    Uses the union of what each moment found, so a dark scene can't over-crop."""
    exe = find("ffmpeg")
    if not exe or not width or not height:
        return None
    boxes = []
    for frac in points:
        at = max(0.0, duration * frac) if duration else 0.0
        r = subprocess.run([exe, "-nostdin", "-hide_banner", "-ss", f"{at:.2f}", "-i", path, "-t", str(seconds),
                            "-map", "0:v:0", "-vf", "cropdetect=limit=24:round=2:reset=0", "-f", "null", "-"],
                           capture_output=True, creationflags=no_window())
        found = _CROP.findall(r.stderr)
        if found:
            boxes.append(tuple(int(v) for v in found[-1]))
    if not boxes:
        return None
    x = min(b[2] for b in boxes)
    y = min(b[3] for b in boxes)
    right = max(b[2] + b[0] for b in boxes)
    bottom = max(b[3] + b[1] for b in boxes)
    w, h = min(right, width) - x, min(bottom, height) - y
    # bars thinner than about 2% each side aren't worth it (and are often just a soft edge)
    if w <= 0 or h <= 0 or (w >= width * 0.96 and h >= height * 0.96):
        return None
    return (w - w % 2, h - h % 2, x, y)
