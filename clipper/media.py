"""Thin wrappers around the ffmpeg binary.

Only `ffmpeg` is required (no ffprobe), so the same code runs on the GitHub
runner (apt ffmpeg) and on a laptop with a static build. Set FFMPEG=/path/to/ffmpeg
to override the binary.
"""
from __future__ import annotations

import os
import re
import subprocess
from dataclasses import dataclass

FFMPEG = os.environ.get("FFMPEG", "ffmpeg")


@dataclass
class VideoInfo:
    path: str
    duration: float
    width: int
    height: int
    has_audio: bool
    box: tuple[int, int, int, int] | None = None   # picture area without black bars (w, h, x, y)


def run(args: list[str], capture: bool = False) -> subprocess.CompletedProcess:
    cmd = [FFMPEG, "-hide_banner", "-nostdin", "-y", *args]
    res = subprocess.run(cmd, capture_output=True)
    if res.returncode != 0:
        tail = res.stderr.decode(errors="replace")[-1500:]
        raise RuntimeError(f"ffmpeg failed ({res.returncode}): {' '.join(cmd[:12])} ...\n{tail}")
    return res


def probe(path: str) -> VideoInfo:
    """Read duration, size and audio presence from `ffmpeg -i` output."""
    res = subprocess.run([FFMPEG, "-hide_banner", "-nostdin", "-i", path], capture_output=True)
    text = res.stderr.decode(errors="replace")
    m = re.search(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)", text)
    if not m:
        raise RuntimeError(f"Could not read duration of {path}. Is it a video file?\n{text[-800:]}")
    duration = int(m[1]) * 3600 + int(m[2]) * 60 + float(m[3])
    v = re.search(r"Stream #.*?Video:.*?(\d{2,5})x(\d{2,5})", text)
    if not v:
        raise RuntimeError(f"No video stream found in {path}")
    width, height = int(v[1]), int(v[2])
    # Rotated phone recordings report rotation in side data.
    if re.search(r"rotate\s*:\s*-?(90|270)|rotation of -?(90|270)", text):
        width, height = height, width
    has_audio = bool(re.search(r"Stream #.*?Audio:", text))
    info = VideoInfo(path, duration, width, height, has_audio)
    info.box = content_box(info)
    return info


def content_box(info: VideoInfo) -> tuple[int, int, int, int] | None:
    """Find black letterbox bars by sampling a few points of the video.
    Returns (w, h, x, y) of the real picture, or None if there are no bars."""
    from collections import Counter
    found: Counter = Counter()
    for frac in (0.25, 0.5, 0.75):
        t = max(info.duration * frac - 1, 0)
        res = subprocess.run([FFMPEG, "-hide_banner", "-nostdin", "-ss", f"{t:.1f}", "-i", info.path,
                              "-t", "2", "-vf", "cropdetect=limit=24:round=2:reset=0", "-an", "-f", "null", "-"],
                             capture_output=True)
        m = re.findall(r"crop=(\d+):(\d+):(\d+):(\d+)", res.stderr.decode(errors="replace"))
        if m:
            found[tuple(int(v) for v in m[-1])] += 1
    if not found:
        return None
    w, h, x, y = found.most_common(1)[0][0]
    # Ignore tiny trims and implausible results (e.g. a fully dark scene).
    if h < info.height * 0.5 or w < info.width * 0.5:
        return None
    if info.height - h < info.height * 0.04 and info.width - w < info.width * 0.04:
        return None
    return (w, h, x, y)
