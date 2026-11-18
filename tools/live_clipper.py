"""Clip a YouTube/Twitch stream WHILE it's live, from your own PC.

    python tools/live_clipper.py <stream link> [--chunk-min 30] [--shorts-per-chunk 3]
                                 [--long-minutes 30] [--publish schedule]

How it works: one ffmpeg process records the live stream into back-to-back
pieces (no gaps). Each time a piece is finished, the normal pipeline runs on
it in the background: highlights -> 9:16 Shorts -> titles -> YouTube upload.
When the stream ends, the pieces are joined and a best-of video is made.

Keys are read from ~/.clipbot/secrets.env (YT_CLIENT_ID=..., etc.).
"""
from __future__ import annotations

import argparse
import datetime as dt
import glob
import os
import queue
import shutil
import subprocess
import sys
import threading
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SECRETS = os.path.join(os.path.expanduser("~"), ".clipbot", "secrets.env")


LOG_FILE = os.path.join(ROOT, "work", "live.log")


def log(msg: str) -> None:
    line = f"[{dt.datetime.now():%H:%M:%S}] {msg}"
    try:
        os.makedirs(os.path.dirname(LOG_FILE), exist_ok=True)
        with open(LOG_FILE, "a", encoding="utf-8") as fh:
            fh.write(line + "\n")
    except OSError:
        pass
    try:
        print(line, flush=True)
    except UnicodeEncodeError:
        print(line.encode("ascii", "replace").decode(), flush=True)


def load_env() -> None:
    if os.path.exists(SECRETS):
        with open(SECRETS, encoding="utf-8") as fh:
            for ln in fh:
                if "=" in ln and not ln.lstrip().startswith("#"):
                    k, v = ln.split("=", 1)
                    os.environ.setdefault(k.strip(), v.strip())
    else:
        log(f"No {SECRETS}: clips will be made but not uploaded.")
    # ffmpeg: use the one on PATH, else the bundled imageio-ffmpeg binary.
    if not os.environ.get("FFMPEG") and not shutil.which("ffmpeg"):
        try:
            import imageio_ffmpeg
            os.environ["FFMPEG"] = imageio_ffmpeg.get_ffmpeg_exe()
        except ImportError:
            sys.exit("ffmpeg not found. Run: pip install imageio-ffmpeg")
    # AI titles: GitHub Models accepts the GitHub CLI login, so nothing else to set up.
    if not os.environ.get("GITHUB_TOKEN") and shutil.which("gh"):
        tok = subprocess.run(["gh", "auth", "token"], capture_output=True, text=True).stdout.strip()
        if tok:
            os.environ["GITHUB_TOKEN"] = tok
    # Let yt-dlp find deno if it was pip-installed into this environment.
    os.environ["PATH"] = os.path.dirname(sys.executable) + os.pathsep + os.environ.get("PATH", "")


def ytdlp(*args: str) -> str:
    res = subprocess.run([sys.executable, "-m", "yt_dlp", "--no-warnings", *args],
                         capture_output=True, text=True, encoding="utf-8", errors="replace")
    if res.returncode != 0:
        raise RuntimeError(res.stderr.strip()[-600:])
    return res.stdout.strip()


def live_status(url: str) -> str:
    try:
        return ytdlp("--skip-download", "--print", "live_status", url).splitlines()[-1]
    except Exception as exc:
        log(f"status check failed: {exc}")
        return "unknown"


def stream_inputs(url: str, height: int) -> list[str]:
    """ffmpeg input args for the live stream. YouTube often serves video and audio
    as separate HLS playlists, so this may return two inputs."""
    fmt = (f"bv*[height<={height}][protocol*=m3u8]+ba[protocol*=m3u8]/"
           f"b[height<={height}][protocol*=m3u8]/bv*+ba/b")
    urls = [u for u in ytdlp("-g", "-f", fmt, url).splitlines() if u.startswith("http")]
    args: list[str] = []
    for u in urls[:2]:
        args += ["-reconnect", "1", "-reconnect_streamed", "1", "-reconnect_delay_max", "10", "-i", u]
    maps = ["-map", "0:v:0", "-map", "1:a:0"] if len(urls) > 1 else ["-map", "0:v:0", "-map", "0:a:0?"]
    return args + maps


def process(piece_ts: str, args, extra: list[str]) -> None:
    mp4 = piece_ts[:-3] + ".mp4"
    ff = os.environ.get("FFMPEG", "ffmpeg")
    subprocess.run([ff, "-hide_banner", "-loglevel", "error", "-y", "-i", piece_ts, "-c", "copy",
                    "-bsf:a", "aac_adtstoasc", "-movflags", "+faststart", mp4], check=True)
    lst = mp4 + ".txt"
    with open(lst, "w", encoding="utf-8") as fh:
        fh.write(mp4 + "\n")
    log(f"Clipping {os.path.basename(mp4)} ...")
    cmd = [sys.executable, "-m", "clipper.run", "--inputs", lst,
           "--work", os.path.join(os.path.dirname(mp4), "work"),
           "--manifest-dir", os.path.join(ROOT, "manifests"),
           "--publish", args.publish, "--max-uploads", str(args.max_uploads), *extra]
    res = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, encoding="utf-8", errors="replace")
    for ln in res.stdout.splitlines():
        if any(k in ln for k in ("title", "uploaded", "goes public", "failed", "Done", "quota", "best-of")):
            log("   " + ln.strip())
    if res.returncode != 0:
        log("   pipeline error: " + res.stderr.strip()[-400:])


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("url")
    ap.add_argument("--chunk-min", type=float, default=30, help="minutes per recorded piece")
    ap.add_argument("--shorts-per-chunk", type=int, default=3)
    ap.add_argument("--clip-seconds", type=float, default=30)
    ap.add_argument("--long-minutes", type=float, default=30, help="best-of length at the end (0 = off)")
    ap.add_argument("--publish", choices=["schedule", "now", "private"], default="schedule")
    ap.add_argument("--max-uploads", type=int, default=6)
    ap.add_argument("--quality", type=int, default=1080, choices=[720, 1080],
                    help="1080 uses about 2.5 GB of data per hour, 720 about 1.3 GB")
    args = ap.parse_args()
    load_env()

    st = live_status(args.url)
    if st != "is_live":
        sys.exit(f"That stream isn't live right now (status: {st}). "
                 "For a finished stream use: python -m clipper.run --url <link>")

    vid = args.url.rstrip("/").split("=")[-1].split("/")[-1][:20]
    work = os.path.join(ROOT, "work", "live", f"{dt.datetime.now():%Y%m%d-%H%M}-{vid}")
    os.makedirs(work, exist_ok=True)
    log(f"Recording into {work}")

    jobs: queue.Queue = queue.Queue()
    shorts = ["--clips", str(args.shorts_per_chunk), "--clip-seconds", str(args.clip_seconds)]

    def worker():
        while True:
            p = jobs.get()
            if p is None:
                return
            try:
                process(p, args, shorts)
            except Exception as exc:
                log(f"   piece failed: {exc}")

    t = threading.Thread(target=worker, daemon=True)
    t.start()
    queued: set[str] = set()
    ff = os.environ.get("FFMPEG", "ffmpeg")
    start_no = 0

    def queue_finished(all_done: bool = False):
        pieces = sorted(glob.glob(os.path.join(work, "piece_*.ts")))
        ready = pieces if all_done else pieces[:-1]   # the newest piece is still being written
        for p in ready:
            if p not in queued and os.path.getsize(p) > 5_000_000:
                queued.add(p)
                log(f"Piece finished: {os.path.basename(p)}, queued for clipping")
                jobs.put(p)

    while True:
        try:
            src = stream_inputs(args.url, args.quality)
        except Exception as exc:
            log(f"Couldn't get the stream address: {exc}")
            break
        log(f"Recording live at up to {args.quality}p (pieces of {args.chunk_min:.0f} min). "
            "Leave this window open. Ctrl+C stops.")
        rec = subprocess.Popen([ff, "-hide_banner", "-loglevel", "error", "-y",
                                *src, "-c", "copy",
                                "-f", "segment", "-segment_time", str(int(args.chunk_min * 60)),
                                "-segment_start_number", str(start_no), "-reset_timestamps", "1",
                                os.path.join(work, "piece_%03d.ts")])
        try:
            while rec.poll() is None:
                time.sleep(20)
                queue_finished()
        except KeyboardInterrupt:
            log("Stopping recording...")
            rec.terminate()
            rec.wait(timeout=30)
            break
        start_no = len(glob.glob(os.path.join(work, "piece_*.ts")))
        if live_status(args.url) != "is_live":
            log("Stream ended.")
            break
        log("Recording dropped (the address expires every few hours), reconnecting...")

    queue_finished(all_done=True)
    jobs.put(None)
    log("Finishing the last pieces...")
    t.join()

    if args.long_minutes:
        mp4s = sorted(glob.glob(os.path.join(work, "piece_*.mp4")))
        total_min = len(mp4s) * args.chunk_min
        if total_min >= args.long_minutes * 1.3:
            log(f"Building the {args.long_minutes:.0f}-min best-of from the whole stream...")
            lst = os.path.join(work, "all.txt")
            with open(lst, "w", encoding="utf-8") as fh:
                for p in mp4s:
                    fh.write(f"file '{p.replace(os.sep, '/')}'\n")
            full = os.path.join(work, "full_stream.mp4")
            subprocess.run([ff, "-hide_banner", "-loglevel", "error", "-y", "-f", "concat", "-safe", "0",
                            "-i", lst, "-c", "copy", full], check=True)
            lst2 = full + ".txt"
            with open(lst2, "w", encoding="utf-8") as fh:
                fh.write(full + "\n")
            res = subprocess.run([sys.executable, "-m", "clipper.run", "--inputs", lst2,
                                  "--work", os.path.join(work, "work_long"),
                                  "--manifest-dir", os.path.join(ROOT, "manifests"),
                                  "--clips", "0", "--long-minutes", str(args.long_minutes),
                                  "--publish", args.publish, "--max-uploads", str(args.max_uploads)],
                                 cwd=ROOT, capture_output=True, text=True, encoding="utf-8", errors="replace")
            for ln in res.stdout.splitlines()[-8:]:
                log("   " + ln.strip())
        else:
            log("Stream too short for the best-of, skipped.")
    log("All done. Check YouTube Studio -> Content.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
