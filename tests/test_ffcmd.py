"""Fast checks of the command builders and helpers. No video files needed.

  .venv\\Scripts\\python.exe tests\\test_ffcmd.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.stdout.reconfigure(encoding="utf-8")
import reel_ffcmd as F
from reel_media import MediaInfo, plex_name

fails = []


def check(label, ok):
    print(("PASS " if ok else "FAIL ") + label)
    if not ok:
        fails.append(label)


def fake(path, streams, duration=5000.0):
    data = {"format": {"duration": str(duration), "size": "1000000", "bit_rate": "1600"},
            "streams": [dict(index=i, **s) for i, s in enumerate(streams)]}
    return MediaInfo(path, data)


V1080 = {"codec_type": "video", "codec_name": "h264", "width": 1920, "height": 1080, "avg_frame_rate": "24000/1001"}
V304 = {"codec_type": "video", "codec_name": "mpeg4", "width": 576, "height": 304, "avg_frame_rate": "24000/1001"}
DTSHD = {"codec_type": "audio", "codec_name": "dts", "profile": "DTS-HD MA", "channels": 6, "tags": {"language": "eng"}}
AC3RU = {"codec_type": "audio", "codec_name": "ac3", "channels": 2, "bit_rate": "192000", "tags": {"language": "rus"},
         "disposition": {"default": 1}}
MOVTEXT = {"codec_type": "subtitle", "codec_name": "mov_text", "tags": {"language": "eng"}}
COVER = {"codec_type": "video", "codec_name": "mjpeg", "width": 600, "height": 800, "disposition": {"attached_pic": 1}}
MP3 = {"codec_type": "audio", "codec_name": "mp3", "channels": 2, "sample_rate": "48000"}

# names Plex can match
for src, want in [("Chop Shop.2007.1080p.mkv", "Chop Shop (2007)"), ("Haxan.1922.720p.x264.mkv", "Haxan (1922)"),
                  ("His Girl Friday  1940 720p.mp4", "His Girl Friday (1940)"), ("Tampopo.cd1.avi", "Tampopo"),
                  ("Oliver! (1968) CD2.avi", "Oliver! (1968)"), ("Nosferatu_1922_DVDRip.avi", "Nosferatu (1922)")]:
    check(f"plex_name({src!r}) = {plex_name(src)!r}", plex_name(src) == want)

# cover art starts unticked; everything else kept
info = fake("x.mkv", [V1080, AC3RU, DTSHD, COVER])
ch = F.default_choices(info)
check("cover art unticked by default", [c["keep"] for c in ch] == [True, True, True, False])

# smart audio: lossless DTS-HD becomes AAC 5.1, compact AC-3 is copied
check("DTS-HD MA -> AAC 384k", F.audio_plan(ch[2], F.DEFAULT_SETTINGS) == ("aac", 384))
check("AC-3 192k copied", F.audio_plan(ch[1], F.DEFAULT_SETTINGS) == ("copy", 0))
s = dict(F.DEFAULT_SETTINGS, stereo=True)
check("stereo downmix converts even compact audio", F.audio_plan(ch[2], s) == ("aac", 160))

# shrink command: scaled to 720, x265, metadata per output index
ch[1]["keep"] = False
args = F.build_shrink(info, ch, F.preset_settings(0))
cmd = " ".join(args)
check("maps only kept streams", "-map 0:0 -map 0:2" in cmd and "-map 0:1" not in cmd and "-map 0:3" not in cmd)
check("scales 1080 -> 720", "scale=-2:720" in cmd)
check("x265 crf 24", "-c:0 libx265 -crf 24" in cmd)
check("DTS becomes AAC on output stream 1", "-c:1 aac -b:1 384k" in cmd)

# never upscale a small source
check("no upscale of 304-line video", F.video_filters(304, {"height": 720}) == [])
check("deinterlace + denoise order", F.video_filters(480, {"height": 480, "deinterlace": "auto", "denoise": "light"})
      == ["bwdif=mode=send_frame:deint=interlaced", "hqdn3d=1.5:1.5:6:6"])

# copy mode leaves the picture alone
args = F.build_shrink(info, ch, F.preset_settings(len(F.PRESETS) - 1))
check("copy preset copies video", "-c:0 copy" in " ".join(args) and "scale" not in " ".join(args))

# remux: MP4 text subtitles become SRT, external file added as input 1
mp4 = fake("hgf.mp4", [V1080, MP3, MOVTEXT])
ch = F.default_choices(mp4)
ch.append(F.external_sub_choice(1, "subs.srt"))
args = F.build_remux("hgf.mp4", ch)
cmd = " ".join(args)
check("remux converts mov_text to srt", "-c:2 srt" in cmd)
check("remux adds the subtitle file as an input", args.count("-i") == 2 and "-map 1:0" in cmd)
check("remux copies everything else", "-c:0 copy" in cmd and "-c:1 copy" in cmd and "-c:3 copy" in cmd)

# AVI inputs get generated timestamps
check("AVI gets +genpts", "+genpts" in F.build_remux("a.avi", F.default_choices(fake("a.avi", [V304, MP3]))))

# join: identical parts are fine, a mismatch is explained
a, b = fake("a.avi", [V304, MP3]), fake("b.avi", [V304, MP3])
check("matching parts join losslessly", F.join_problems([a, b]) == [])
c = fake("c.avi", [V1080, MP3])
probs = F.join_problems([a, c])
check("mismatch explained: " + "; ".join(probs), any("picture size" in p for p in probs))

# the concat list escapes quotes in file names
import tempfile
lst = os.path.join(tempfile.gettempdir(), "nousreel-test-list.txt")
F.write_concat_list([r"C:\movies\It's a Wonderful Life.cd1.avi"], lst)
check("concat list escapes apostrophes", open(lst, encoding="utf-8").read() ==
      "file 'C:/movies/It'\\''s a Wonderful Life.cd1.avi'\n")
os.remove(lst)

check("display_command quotes spaces", F.display_command(["-i", "a b.mkv"], "out put.mkv")
      == 'ffmpeg -i "a b.mkv" "out put.mkv"')

print(f"\n{'all passed' if not fails else str(len(fails)) + ' failed'}")
sys.exit(1 if fails else 0)
