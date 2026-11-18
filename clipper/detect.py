"""Highlight detection from the audio track.

GTA highlights (crashes, gunfights, explosions, sirens, a mic reaction) show up
as sudden jumps in loudness relative to what was playing just before. Raw
loudness alone favours long stretches of loud music, so each moment is scored
on two things:

  * spike   - how far the loudness rises above the rolling median of the
              surrounding 30 s (the "baseline")
  * energy  - absolute loudness, with a smaller weight

Peaks are then picked greedily with a minimum gap so clips don't overlap.
"""
from __future__ import annotations

import subprocess
from dataclasses import dataclass

import numpy as np

from . import media

SR = 8000          # audio sample rate used for analysis
HOP = 0.25         # seconds between analysis frames
WIN = 0.5          # analysis window in seconds
BASELINE = 30.0    # rolling-median window for the baseline, seconds


@dataclass
class Highlight:
    start: float     # clip start (seconds into the source)
    peak: float      # loudest moment inside the clip
    score: float     # 0..1, relative to the best moment in this video
    intensity: str   # "massive" / "big" / "solid", used as caption context


def _envelope(path: str) -> np.ndarray:
    """Return loudness in dB, one value per HOP seconds."""
    proc = subprocess.run(
        [media.FFMPEG, "-hide_banner", "-nostdin", "-v", "error", "-i", path,
         "-vn", "-ac", "1", "-ar", str(SR), "-f", "s16le", "-"],
        capture_output=True,
    )
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.decode(errors="replace")[-800:])
    pcm = np.frombuffer(proc.stdout, dtype=np.int16).astype(np.float32) / 32768.0
    hop = int(SR * HOP)
    per_win = int(round(WIN / HOP))          # WIN is a whole number of hops
    n_hops = pcm.size // hop
    if n_hops < per_win * 4:
        return np.zeros(0, dtype=np.float32)
    # Mean power per hop, then average neighbouring hops to get the window.
    power = np.mean(np.square(pcm[: n_hops * hop].reshape(n_hops, hop)), axis=1)
    win_power = np.convolve(power, np.ones(per_win) / per_win, mode="valid")
    return (10 * np.log10(win_power + 1e-10)).astype(np.float32)


def _rolling_median(x: np.ndarray, w: int) -> np.ndarray:
    if x.size < w:
        return np.full_like(x, np.median(x))
    pad = w // 2
    xp = np.pad(x, (pad, w - pad - 1), mode="edge")
    view = np.lib.stride_tricks.sliding_window_view(xp, w)
    # Median of every window, in blocks to bound memory.
    out = np.empty(x.size, dtype=np.float32)
    for i in range(0, x.size, 5000):
        out[i:i + 5000] = np.median(view[i:i + 5000], axis=1)
    return out


def _zscore(x: np.ndarray) -> np.ndarray:
    sd = float(np.std(x))
    return (x - float(np.mean(x))) / (sd if sd > 1e-6 else 1.0)


def even_spread(duration: float, count: int, clip_len: float) -> list[Highlight]:
    usable = max(duration - clip_len, 0)
    return [
        Highlight(start=round(usable * (i + 1) / (count + 1), 2),
                  peak=round(usable * (i + 1) / (count + 1) + clip_len / 2, 2),
                  score=0.0, intensity="solid")
        for i in range(count)
    ]


def find_highlights(info: media.VideoInfo, count: int, clip_len: float,
                    skip_intro: float = 5.0) -> list[Highlight]:
    if info.duration <= clip_len + 1:
        return [Highlight(0.0, info.duration / 2, 1.0, "solid")]
    count = max(1, min(count, int(info.duration // (clip_len + 5)) or 1))
    if not info.has_audio:
        return even_spread(info.duration, count, clip_len)

    db = _envelope(info.path)
    if db.size < 20 or float(np.ptp(db)) < 3:   # silent or flat audio
        return even_spread(info.duration, count, clip_len)

    base = _rolling_median(db, int(BASELINE / HOP))
    spike = np.clip(db - base, 0, None)
    score = 0.7 * _zscore(spike) + 0.3 * _zscore(db)
    # Smooth over ~2 s so a single click doesn't win over a sustained moment.
    k = int(2.0 / HOP)
    score = np.convolve(score, np.ones(k) / k, mode="same")

    times = np.arange(score.size) * HOP + WIN / 2
    pre = clip_len * 0.4                  # put the peak ~40% into the clip
    min_gap = clip_len + 5
    valid = (times >= skip_intro + pre * 0.25) & (times <= info.duration - 2)

    picks: list[tuple[float, float]] = []
    for i in np.argsort(-score):
        if not valid[i]:
            continue
        t = float(times[i])
        if any(abs(t - p) < min_gap for p, _ in picks):
            continue
        picks.append((t, float(score[i])))
        if len(picks) >= count:
            break

    if not picks:
        return even_spread(info.duration, count, clip_len)

    top = max(s for _, s in picks)
    low = min(s for _, s in picks)
    out = []
    for peak, s in sorted(picks):
        start = min(max(peak - pre, 0.0), max(info.duration - clip_len, 0.0))
        rel = 1.0 if top == low else (s - low) / (top - low)
        intensity = "massive" if rel > 0.75 else "big" if rel > 0.35 else "solid"
        out.append(Highlight(round(start, 2), round(peak, 2), round(rel, 3), intensity))
    return out
