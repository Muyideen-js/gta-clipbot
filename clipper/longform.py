"""'Best of' long video: the strongest moments of a stream, in order, as one 16:9 video,
plus a click-worthy thumbnail (best frame + big outlined text)."""
from __future__ import annotations

import os

from . import detect, media

FONT_CANDIDATES = [
    os.environ.get("THUMB_FONT", ""),
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
    "C:/Windows/Fonts/impact.ttf",
    "C:/Windows/Fonts/arialbd.ttf",
]


def pick_segments(info: media.VideoInfo, minutes: float, seg_seconds: float = 60) -> list[detect.Highlight]:
    count = max(1, int(minutes * 60 // seg_seconds))
    segs = detect.find_highlights(info, count, seg_seconds, skip_intro=30)
    return sorted(segs, key=lambda h: h.start)


def build(info: media.VideoInfo, segs: list[detect.Highlight], seg_seconds: float, out_path: str) -> float:
    """Cut each segment to 1080p and join them with short fades. Returns total seconds."""
    parts = []
    work = os.path.dirname(out_path)
    for i, h in enumerate(segs):
        dur = min(seg_seconds, info.duration - h.start)
        p = os.path.join(work, f"_seg{i:03d}.mp4")
        vf = ("scale=1920:1080:force_original_aspect_ratio=decrease,"
              "pad=1920:1080:(ow-iw)/2:(oh-ih)/2,setsar=1,fps=30,"
              f"fade=t=in:st=0:d=0.4,fade=t=out:st={max(dur - 0.4, 0):.2f}:d=0.4")
        args = ["-ss", f"{h.start:.2f}", "-i", info.path, "-t", f"{dur:.2f}", "-vf", vf]
        if info.has_audio:
            args += ["-af", f"aresample=async=1:first_pts=0,afade=t=in:d=0.4,afade=t=out:st={max(dur - 0.4, 0):.2f}:d=0.4",
                     "-c:a", "aac", "-b:a", "160k", "-ar", "48000", "-ac", "2"]
        args += ["-c:v", "libx264", "-preset", "veryfast", "-crf", "21", "-pix_fmt", "yuv420p", p]
        media.run(args)
        parts.append(p)
    listfile = os.path.join(work, "_concat.txt")
    with open(listfile, "w", encoding="utf-8") as fh:
        for p in parts:
            fh.write(f"file '{os.path.abspath(p).replace(os.sep, '/')}'\n")
    media.run(["-f", "concat", "-safe", "0", "-i", listfile, "-c", "copy", "-movflags", "+faststart", out_path])
    for p in parts + [listfile]:
        os.remove(p)
    return media.probe(out_path).duration


def thumbnail(info: media.VideoInfo, best: detect.Highlight, text: str, out_path: str) -> str:
    """1280x720 JPEG: frame at the loudest moment, darkened lower third, huge outlined text."""
    text = " ".join(text.upper().split()[:5])[:32]
    tfile = out_path + ".txt"
    with open(tfile, "w", encoding="utf-8") as fh:
        fh.write(text)
    font = next((f for f in FONT_CANDIDATES if f and os.path.exists(f)), "")
    fontarg = f"fontfile='{font.replace(':', chr(92) + ':')}':" if font else ""
    tpath = tfile.replace("\\", "/").replace(":", "\\:")
    vf = ("scale=1280:720:force_original_aspect_ratio=increase,crop=1280:720,"
          "eq=contrast=1.15:saturation=1.35,"
          "drawbox=y=ih*0.58:w=iw:h=ih*0.42:color=black@0.55:t=fill,"
          f"drawtext={fontarg}textfile='{tpath}':fontsize=110:fontcolor=white:"
          "borderw=8:bordercolor=black:x=(w-text_w)/2:y=h*0.62")
    try:
        media.run(["-ss", f"{best.peak:.2f}", "-i", info.path, "-frames:v", "1", "-vf", vf, "-q:v", "2", out_path])
    except RuntimeError:  # no drawtext/font available: plain frame
        media.run(["-ss", f"{best.peak:.2f}", "-i", info.path, "-frames:v", "1",
                   "-vf", "scale=1280:720:force_original_aspect_ratio=increase,crop=1280:720", "-q:v", "2", out_path])
    os.remove(tfile)
    return out_path
