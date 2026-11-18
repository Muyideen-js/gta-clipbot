"""Cut a highlight to a 1080x1920 (9:16) H.264 MP4 ready for Shorts."""
from __future__ import annotations

import base64
import os

from . import media

OUT_W, OUT_H = 1080, 1920


def _crop_box(w: int, h: int) -> tuple[int, int, int, int]:
    """Largest centred 9:16 box inside a w x h frame (even numbers)."""
    if w / h > 9 / 16:
        ch = h
        cw = int(h * 9 / 16)
    else:
        cw = w
        ch = int(w * 16 / 9)
    cw -= cw % 2
    ch -= ch % 2
    return cw, ch, (w - cw) // 2, (h - ch) // 2


def cut(info: media.VideoInfo, start: float, duration: float, out_path: str,
        layout: str = "crop") -> str:
    """layout="crop": centre crop, fills the screen.
    layout="blur": whole 16:9 frame in the middle over a blurred fill, keeps HUD/minimap."""
    # Trim black letterbox bars first, so they don't end up inside the Short.
    pre = ""
    w, h = info.width, info.height
    if info.box:
        bw, bh, bx, by = info.box
        pre = f"crop={bw}:{bh}:{bx}:{by},"
        w, h = bw, bh
    if layout == "blur":
        graph = (
            f"[0:v]{pre}split=2[a][b];"
            f"[a]scale={OUT_W}:{OUT_H}:force_original_aspect_ratio=increase,"
            f"crop={OUT_W}:{OUT_H},boxblur=20:2[bg];"
            f"[b]scale={OUT_W}:-2[fg];"
            f"[bg][fg]overlay=(W-w)/2:(H-h)/2,setsar=1,fps=30[v]"
        )
    else:
        cw, ch, cx, cy = _crop_box(w, h)
        graph = f"[0:v]{pre}crop={cw}:{ch}:{cx}:{cy},scale={OUT_W}:{OUT_H},setsar=1,fps=30[v]"

    args = [
        "-ss", f"{start:.2f}", "-i", info.path, "-t", f"{duration:.2f}",
        "-filter_complex", graph, "-map", "[v]", "-map", "0:a:0?",
        # Keep sound on the video's clock: fill any gaps in the recording with silence
        # instead of letting the audio slide ahead (fps=30 above does the same for frames).
        "-af", "aresample=async=1:first_pts=0",
        "-c:v", "libx264", "-preset", os.environ.get("X264_PRESET", "veryfast"),
        "-crf", os.environ.get("X264_CRF", "21"), "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "160k", "-ar", "48000",
        "-movflags", "+faststart", out_path,
    ]
    media.run(args)
    return out_path


def frames(clip_path: str, duration: float, out_prefix: str, count: int = 3,
           width: int = 360) -> list[str]:
    """Grab a few stills from the finished clip (for captions and thumbnails)."""
    paths = []
    for i in range(count):
        t = duration * (i + 1) / (count + 1)
        p = f"{out_prefix}_{i}.jpg"
        media.run(["-ss", f"{t:.2f}", "-i", clip_path, "-frames:v", "1",
                   "-vf", f"scale={width}:-2", "-q:v", "4", p])
        paths.append(p)
    return paths


def tiny_thumb_data_uri(clip_path: str, duration: float, out_path: str) -> str:
    """108x192 JPEG as a data: URI, small enough to keep inside the manifest."""
    media.run(["-ss", f"{duration * 0.45:.2f}", "-i", clip_path, "-frames:v", "1",
               "-vf", "scale=108:192", "-q:v", "6", out_path])
    with open(out_path, "rb") as fh:
        return "data:image/jpeg;base64," + base64.b64encode(fh.read()).decode()
