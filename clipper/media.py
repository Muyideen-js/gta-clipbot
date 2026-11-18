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
    return VideoInfo(path, duration, width, height, has_audio)
