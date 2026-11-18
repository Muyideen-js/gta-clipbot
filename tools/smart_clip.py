"""Smart download: clip a FINISHED stream/video without downloading all of it.

    python tools/smart_clip.py <link> [--shorts 20] [--long-minutes 30] [--publish schedule]

1. Download only the audio of the whole stream (small: ~60 MB per hour).
2. Find the best moments from the audio (same scoring as the rest of the bot).
3. Download only those moments in HD with yt-dlp --download-sections.
4. Cut, caption, upload (up to the daily cap) and save the rest to
   Desktop/GTA Clips/Post by hand, exactly like the live bot.

Slow internet just makes it take longer; there are no gaps like in live recording.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from live_clipper import ROOT, load_env, log, ytdlp  # noqa: E402

sys.path.insert(0, ROOT)
from clipper import detect, longform, media  # noqa: E402


def duration_of(path: str) -> float:
    res = subprocess.run([os.environ.get("FFMPEG", "ffmpeg"), "-hide_banner", "-i", path],
                         capture_output=True, text=True, encoding="utf-8", errors="replace")
    m = re.search(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)", res.stderr)
    if not m:
        raise RuntimeError(f"Couldn't read the length of {path}")
    return int(m[1]) * 3600 + int(m[2]) * 60 + float(m[3])


def download_section(url: str, a: float, b: float, out: str, height: int) -> str:
    """Download just [a, b] seconds of the video. Retries, since the connection is flaky."""
    fmt = f"bv*[height<={height}]+ba/b[height<={height}]/bv*+ba/b"
    last = ""
    for attempt in range(4):
        try:
            ytdlp("--download-sections", f"*{max(a, 0):.1f}-{b:.1f}", "-f", fmt,
                  "--merge-output-format", "mp4", "--force-overwrites", "--retries", "10",
                  "--fragment-retries", "20", "-o", out, url)
            if os.path.exists(out) and os.path.getsize(out) > 50_000:
                return out
            last = "empty file"
        except Exception as exc:
            last = str(exc)[-300:]
        log(f"   download attempt {attempt + 1} failed, retrying... ({last.splitlines()[-1] if last else ''})")
    raise RuntimeError(f"Couldn't download {a:.0f}-{b:.0f}s: {last}")


def run_pipeline(inputs: list[str], work: str, extra: list[str], args) -> None:
    lst = os.path.join(work, f"inputs_{len(os.listdir(work))}.txt")
    with open(lst, "w", encoding="utf-8") as fh:
        fh.write("\n".join(inputs) + "\n")
    cmd = [sys.executable, "-m", "clipper.run", "--inputs", lst, "--work", os.path.join(work, "out"),
           "--manifest-dir", os.path.join(ROOT, "manifests"), "--publish", args.publish,
           "--max-uploads", str(args.max_uploads), *extra]
    proc = subprocess.Popen(cmd, cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            text=True, encoding="utf-8", errors="replace")
    for ln in proc.stdout:
        if any(k in ln for k in ("title (", "uploaded:", "saved to", "Done", "quota", "best-of",
                                 "failed", "min, title")):
            log("   " + ln.strip())
    proc.wait()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("url")
    ap.add_argument("--shorts", type=int, default=20)
    ap.add_argument("--clip-seconds", type=float, default=30)
    ap.add_argument("--long-minutes", type=float, default=30, help="0 = no best-of video")
    ap.add_argument("--long-segment", type=float, default=60)
    ap.add_argument("--quality", type=int, default=1080, help="Shorts download height")
    ap.add_argument("--long-quality", type=int, default=720, help="best-of download height")
    ap.add_argument("--publish", choices=["schedule", "now", "private"], default="schedule")
    ap.add_argument("--max-uploads", type=int, default=6)
    args = ap.parse_args()
    load_env()

    status = ytdlp("--skip-download", "--print", "%(live_status)s|%(id)s|%(duration)s", args.url).splitlines()[-1]
    live, vid, _ = (status.split("|") + ["", ""])[:3]
    if live == "is_live":
        sys.exit("The stream is still live. Run this after it ends (or use tools/live_clipper.py).")
    work = os.path.join(ROOT, "work", "smart", f"{dt.datetime.now():%Y%m%d-%H%M}-{vid}")
    os.makedirs(work, exist_ok=True)
    log(f"Smart download into {work}")

    # 1. Audio only.
    log("Step 1/4: downloading the sound only...")
    audio = ytdlp("-f", "ba[ext=m4a]/ba", "--retries", "10", "--fragment-retries", "20",
                  "-o", os.path.join(work, "audio.%(ext)s"), "--print", "after_move:filepath",
                  args.url).splitlines()[-1].strip()
    dur = duration_of(audio)
    log(f"   got {dur / 3600:.1f} h of audio ({os.path.getsize(audio) / 1048576:.0f} MB)")

    # 2. Best moments from the audio.
    info = media.VideoInfo(audio, dur, 1920, 1080, True)
    log("Step 2/4: finding the best moments...")
    shorts = detect.find_highlights(info, args.shorts, args.clip_seconds) if args.shorts else []
    segs = longform.pick_segments(info, args.long_minutes, args.long_segment) \
        if args.long_minutes and dur >= args.long_minutes * 60 * 1.3 else []
    log(f"   {len(shorts)} Shorts moments, {len(segs)} best-of moments")

    # 3+4a. Best-of first, so it gets an upload slot before the Shorts.
    if segs:
        log(f"Step 3/4: downloading {len(segs)} best-of moments at {args.long_quality}p...")
        parts, chapters, acc = [], [], 0.0
        ff = os.environ.get("FFMPEG", "ffmpeg")
        for i, h in enumerate(segs):
            raw = download_section(args.url, h.start - 0.5, h.start + args.long_segment + 0.5,
                                   os.path.join(work, f"long_{i:03d}_raw.mp4"), args.long_quality)
            sd = duration_of(raw)
            part = os.path.join(work, f"long_{i:03d}.mp4")
            subprocess.run([ff, "-hide_banner", "-loglevel", "error", "-y", "-i", raw,
                            "-vf", "scale=1920:1080:force_original_aspect_ratio=decrease,"
                                   "pad=1920:1080:(ow-iw)/2:(oh-ih)/2,setsar=1,fps=30,"
                                   f"fade=t=in:st=0:d=0.4,fade=t=out:st={max(sd - 0.4, 0):.2f}:d=0.4",
                            "-af", f"aresample=async=1:first_pts=0,afade=t=in:d=0.4,"
                                   f"afade=t=out:st={max(sd - 0.4, 0):.2f}:d=0.4",
                            "-c:v", "libx264", "-preset", "veryfast", "-crf", "21", "-pix_fmt", "yuv420p",
                            "-c:a", "aac", "-b:a", "160k", "-ar", "48000", "-ac", "2", part], check=True)
            os.remove(raw)
            parts.append(part)
            chapters.append(round(acc, 1))
            acc += sd
            log(f"   best-of {i + 1}/{len(segs)} ready")
        listfile = os.path.join(work, "long_concat.txt")
        with open(listfile, "w", encoding="utf-8") as fh:
            fh.writelines(f"file '{p.replace(os.sep, '/')}'\n" for p in parts)
        reel = os.path.join(work, "best_of.mp4")
        subprocess.run([ff, "-hide_banner", "-loglevel", "error", "-y", "-f", "concat", "-safe", "0",
                        "-i", listfile, "-c", "copy", "-movflags", "+faststart", reel], check=True)
        with open(reel + ".chapters.json", "w", encoding="utf-8") as fh:
            json.dump(chapters, fh)
        for p in parts:
            os.remove(p)
        log("   posting the best-of...")
        run_pipeline([reel], work, ["--clips", "0", "--long-whole", "--long-minutes", str(args.long_minutes)], args)

    # 3+4b. Shorts: each moment with 3 s either side; the pipeline picks the exact 30 s.
    if shorts:
        log(f"Step 4/4: downloading {len(shorts)} Shorts moments at {args.quality}p...")
        files = []
        for i, h in enumerate(shorts):
            try:
                files.append(download_section(args.url, h.start - 3, h.start + args.clip_seconds + 3,
                                              os.path.join(work, f"short_{i:03d}.mp4"), args.quality))
                log(f"   moment {i + 1}/{len(shorts)} downloaded")
            except Exception as exc:
                log(f"   skipped moment {i + 1}: {exc}")
        if files:
            run_pipeline(files, work, ["--clips", "1", "--clip-seconds", str(args.clip_seconds)], args)
    log("All done. Check Desktop\\GTA Clips and YouTube Studio.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
