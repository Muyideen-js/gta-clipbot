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
        if not os.environ.get("YT_REFRESH_TOKEN"):
            log(f"No {SECRETS} and no YouTube keys: clips will be made but not uploaded.")
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


def ytdlp(*args: str, timeout: int = 180) -> str:
    loc = ["--ffmpeg-location", os.environ["FFMPEG"]] if os.environ.get("FFMPEG") else []
    # If YouTube says "confirm you're not a bot", a cookies.txt export placed here fixes it.
    cookies = os.path.join(os.path.expanduser("~"), ".clipbot", "cookies.txt")
    if os.path.exists(cookies):
        loc += ["--cookies", cookies]
    try:
        res = subprocess.run([sys.executable, "-m", "yt_dlp", "--no-warnings", "--socket-timeout", "30",
                              *loc, *args], capture_output=True, text=True, encoding="utf-8",
                             errors="replace", timeout=timeout)
    except subprocess.TimeoutExpired:
        raise RuntimeError(f"YouTube didn't answer within {timeout} s (internet down?)")
    if res.returncode != 0:
        raise RuntimeError(res.stderr.strip()[-600:])
    return res.stdout.strip()


def live_status(url: str) -> str:
    try:
        return ytdlp("--skip-download", "--print", "live_status", url).splitlines()[-1]
    except Exception as exc:
        if "not currently live" in str(exc):      # Twitch/Kick channel pages after the stream ends
            return "not_live"
        log(f"status check failed: {exc}")
        return "unknown"


def stream_inputs(url: str, height: int) -> list[str]:
    """ffmpeg input args for the live stream. YouTube often serves video and audio
    as separate HLS playlists, so this may return two inputs."""
    # Only H.264 + AAC over HLS: the recorder writes MPEG-TS pieces, which can't hold
    # YouTube's VP9/Opus DASH formats (the picture would be silently dropped).
    fmt = (f"bv*[height<={height}][protocol*=m3u8][vcodec^=avc1]+ba[protocol*=m3u8]/"
           f"b[height<={height}][protocol*=m3u8][vcodec^=avc1]")
    urls = [u for u in ytdlp("-g", "-f", fmt, url).splitlines() if u.startswith("http")]
    args: list[str] = []
    for u in urls[:2]:
        args += ["-reconnect", "1", "-reconnect_streamed", "1", "-reconnect_delay_max", "10", "-i", u]
    maps = ["-map", "0:v:0", "-map", "1:a:0"] if len(urls) > 1 else ["-map", "0:v:0", "-map", "0:a:0?"]
    return args + maps


def has_sound(path: str) -> bool:
    """True if the recording has a real audio track. After a reconnect the audio
    stream sometimes comes back empty ("0 channels"): picture only, no sound."""
    res = subprocess.run([os.environ.get("FFMPEG", "ffmpeg"), "-hide_banner", "-i", path],
                         capture_output=True, text=True, encoding="utf-8", errors="replace")
    audio = [ln for ln in res.stderr.splitlines() if "Audio:" in ln]
    video = any("Video:" in ln for ln in res.stderr.splitlines())
    return video and bool(audio) and not any("0 channels" in ln for ln in audio)


def process(piece_ts: str, args, extra: list[str]) -> None:
    if not has_sound(piece_ts):
        log(f"Skipping {os.path.basename(piece_ts)}: it was recorded without sound or picture.")
        return
    mp4 = piece_ts[:-3] + ".mp4"
    ff = os.environ.get("FFMPEG", "ffmpeg")
    subprocess.run([ff, "-hide_banner", "-loglevel", "error", "-y", "-i", piece_ts, "-c", "copy",
                    "-movflags", "+faststart", mp4], check=True)
    lst = mp4 + ".txt"
    with open(lst, "w", encoding="utf-8") as fh:
        fh.write(mp4 + "\n")
    log(f"Clipping {os.path.basename(mp4)} ...")
    cmd = [sys.executable, "-m", "clipper.run", "--inputs", lst,
           "--work", os.path.join(os.path.dirname(mp4), "work"),
           "--manifest-dir", os.path.join(ROOT, "manifests"),
           "--publish", args.publish, "--max-uploads", str(args.max_uploads),
           *(["--upload", "false"] if args.no_upload else []), *extra]
    res = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, encoding="utf-8", errors="replace")
    for ln in res.stdout.splitlines():
        if any(k in ln for k in ("title (", "uploaded:", "saved to", "Done", "quota", "best-of", "clip failed")):
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
    ap.add_argument("--resume", default="", help="continue in an earlier recording folder")
    ap.add_argument("--max-minutes", type=float, default=0,
                    help="stop recording after this long (GitHub runs are limited to 6 h); "
                         "writes work/still_live if the stream is still going")
    ap.add_argument("--no-upload", action="store_true", help="make clips but don't upload")
    ap.add_argument("--quality", type=int, default=1080, choices=[720, 1080],
                    help="1080 uses about 2.5 GB of data per hour, 720 about 1.3 GB")
    args = ap.parse_args()
    load_env()

    st = live_status(args.url)
    if st not in ("is_live", "unknown"):     # "unknown" = YouTube refused the check; try anyway
        sys.exit(f"That stream isn't live right now (status: {st}). "
                 "For a finished stream use: python -m clipper.run --url <link>")

    vid = args.url.rstrip("/").split("=")[-1].split("/")[-1][:20]
    work = args.resume or os.path.join(ROOT, "work", "live", f"{dt.datetime.now():%Y%m%d-%H%M}-{vid}")
    os.makedirs(work, exist_ok=True)
    log(f"Recording into {work}")
    # Resuming: earlier pieces are complete; rename them so they don't collide with new ones.
    stamp = f"{dt.datetime.now():%H%M%S}"
    for p in sorted(glob.glob(os.path.join(work, "piece_*.ts"))):
        new = os.path.join(work, f"earlier_{stamp}_{os.path.basename(p)}")
        os.replace(p, new)
        if os.path.exists(p + ".done"):          # keep "already clipped" with the piece
            os.replace(p + ".done", new + ".done")

    jobs: queue.Queue = queue.Queue()
    shorts = ["--clips", str(args.shorts_per_chunk), "--clip-seconds", str(args.clip_seconds)]

    def worker():
        while True:
            p = jobs.get()
            if p is None:
                return
            try:
                process(p, args, shorts)
                open(p + ".done", "w").close()   # so a restart doesn't clip it twice
            except Exception as exc:
                log(f"   piece failed: {exc}")

    t = threading.Thread(target=worker, daemon=True)
    t.start()
    queued: set[str] = set()
    ff = os.environ.get("FFMPEG", "ffmpeg")
    start_no = 0

    for p in sorted(glob.glob(os.path.join(work, "earlier_*.ts"))):
        if os.path.getsize(p) > 5_000_000 and not os.path.exists(p + ".done"):
            queued.add(p)
            log(f"Earlier recording {os.path.basename(p)} queued for clipping")
            jobs.put(p)

    def queue_finished(all_done: bool = False):
        pieces = sorted(glob.glob(os.path.join(work, "piece_*.ts")))
        ready = pieces if all_done else pieces[:-1]   # the newest piece is still being written
        for p in ready:
            if p not in queued and os.path.getsize(p) > 5_000_000 and not os.path.exists(p + ".done"):
                queued.add(p)
                log(f"Piece finished: {os.path.basename(p)}, queued for clipping")
                jobs.put(p)

    deadline = time.time() + args.max_minutes * 60 if args.max_minutes else None
    out_of_time = False
    while True:
        src = None
        for attempt in range(30):          # keep trying for up to ~30 minutes
            try:
                src = stream_inputs(args.url, args.quality)
                break
            except Exception as exc:
                msg = str(exc).strip().splitlines()[-1] if str(exc).strip() else "unknown error"
                log(f"Couldn't get the stream address ({msg[:120]}). Trying again in 1 min...")
                time.sleep(60)
        if not src:
            log("Gave up reconnecting after 30 minutes.")
            break
        log(f"Recording live at up to {args.quality}p (pieces of {args.chunk_min:.0f} min). "
            "Leave this window open. Ctrl+C stops.")
        # -copyts keeps the stream's own clock: picture and sound are separate live streams
        # that ffmpeg joins at slightly different moments, and resetting each to zero
        # would put the sound out of sync.
        rec = subprocess.Popen([ff, "-hide_banner", "-loglevel", "error", "-y", "-copyts",
                                *src, "-c", "copy",
                                "-f", "segment", "-segment_time", str(int(args.chunk_min * 60)),
                                "-segment_start_number", str(start_no), "-reset_timestamps", "1",
                                os.path.join(work, "piece_%03d.ts"),
                                "-progress", "pipe:1", "-nostats"],
                               stdout=subprocess.PIPE, text=True, encoding="utf-8", errors="replace")
        # Watchdog: ffmpeg reports its position every second. If it stops moving
        # (the connection dropped and ffmpeg hung), restart the recorder.
        progress = {"pos": "", "at": time.time()}

        def watch(proc=rec, state=progress):
            for ln in proc.stdout:
                if ln.startswith("out_time_us=") and ln != state["pos"]:
                    state["pos"], state["at"] = ln, time.time()

        threading.Thread(target=watch, daemon=True).start()
        started, sound_checked = time.time(), False
        try:
            while rec.poll() is None:
                time.sleep(15)
                queue_finished()
                # 45 s in, make sure this recording actually has sound.
                if not sound_checked and time.time() - started > 45:
                    sound_checked = True
                    current = sorted(glob.glob(os.path.join(work, "piece_*.ts")))
                    if current and os.path.getsize(current[-1]) > 500_000 and not has_sound(current[-1]):
                        log("This recording came in without sound or picture. Restarting the recorder...")
                        rec.terminate()
                        try:
                            rec.wait(timeout=20)
                        except subprocess.TimeoutExpired:
                            rec.kill()
                        open(current[-1] + ".done", "w").close()   # don't clip the silent part
                        break
                if deadline and time.time() > deadline:
                    log(f"Reached the {args.max_minutes:.0f}-minute limit for this run. Stopping the recorder...")
                    out_of_time = True
                    rec.terminate()
                    try:
                        rec.wait(timeout=20)
                    except subprocess.TimeoutExpired:
                        rec.kill()
                    break
                if time.time() - progress["at"] > 90:
                    log("Recording stopped moving for 90 s (connection problem). Restarting the recorder...")
                    rec.terminate()
                    try:
                        rec.wait(timeout=20)
                    except subprocess.TimeoutExpired:
                        rec.kill()
                    break
        except KeyboardInterrupt:
            log("Stopping recording...")
            rec.terminate()
            rec.wait(timeout=30)
            break
        start_no = len(glob.glob(os.path.join(work, "piece_*.ts")))
        if out_of_time:
            if live_status(args.url) in ("is_live", "unknown"):
                with open(os.path.join(ROOT, "work", "still_live"), "w", encoding="utf-8") as fh:
                    fh.write(args.url)
                log("The stream is still live: a new run will continue it.")
            break
        if live_status(args.url) not in ("is_live", "unknown"):
            log("Stream ended.")
            break
        log("Reconnecting to the stream...")

    queue_finished(all_done=True)
    jobs.put(None)
    log("Finishing the last pieces...")
    t.join()

    if args.long_minutes:
        mp4s = sorted(glob.glob(os.path.join(work, "earlier_*.mp4"))) +             sorted(glob.glob(os.path.join(work, "piece_*.mp4")))
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
