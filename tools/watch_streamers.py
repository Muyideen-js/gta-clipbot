"""Watch several streamers and clip whoever is live.

    python tools/watch_streamers.py            (uses streamers.json, top = highest priority)

- Every 5 minutes it checks each channel in streamers.json, in order.
- The first one that's live gets recorded and clipped by live_clipper.py, with that
  streamer's credit line and hashtags. One stream at a time (home internet).
- When nobody is live, it works through the smart-download queue
  (~/.clipbot/smart_queue.txt): finished streams to clip from the replay.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from live_clipper import ROOT, load_env, log, ytdlp  # noqa: E402

CONFIG = os.path.join(ROOT, "streamers.json")
QUEUE = os.path.join(os.path.expanduser("~"), ".clipbot", "smart_queue.txt")
CHECK_EVERY = 300


def streamers() -> list[dict]:
    with open(CONFIG, encoding="utf-8") as fh:
        return json.load(fh)


def live_video(channel: str) -> str | None:
    """Return the watch URL of the channel's current live stream, or None."""
    try:
        out = ytdlp("--skip-download", "--print", "%(live_status)s|%(id)s",
                    channel.rstrip("/") + "/live", timeout=120).splitlines()[-1]
    except Exception as exc:
        msg = str(exc)
        if "not currently live" not in msg and "does not have a" not in msg:
            log(f"   check failed for {channel}: {msg.strip().splitlines()[-1][:120] if msg.strip() else ''}")
        return None
    status, vid = (out.split("|") + [""])[:2]
    return f"https://www.youtube.com/watch?v={vid}" if status == "is_live" and vid else None


def env_for(s: dict) -> dict:
    env = dict(os.environ)
    env["CREDIT_LINE"] = s.get("credit", "")
    env["EXTRA_TAGS"] = s.get("tags", "")
    return env


def queue_pop() -> list[str] | None:
    if not os.path.exists(QUEUE):
        return None
    with open(QUEUE, encoding="utf-8") as fh:
        lines = [ln.strip() for ln in fh if ln.strip() and not ln.startswith("#")]
    if not lines:
        return None
    with open(QUEUE, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines[1:]) + ("\n" if len(lines) > 1 else ""))
    return lines[0].split("|")


def main() -> int:
    load_env()
    py = sys.executable
    tools = os.path.dirname(os.path.abspath(__file__))
    log("Watching: " + ", ".join(s["name"] for s in streamers()))
    while True:
        found = None
        for s in streamers():
            url = live_video(s["channel"])
            if url:
                found = (s, url)
                break
        if found:
            s, url = found
            log(f"{s['name']} is LIVE: {url}. Recording and clipping...")
            subprocess.run([py, os.path.join(tools, "live_clipper.py"), url, *s.get("args", [
                "--quality", "720", "--chunk-min", "30", "--shorts-per-chunk", "6",
                "--long-minutes", "30", "--publish", "schedule", "--max-uploads", "6"])],
                cwd=ROOT, env=env_for(s))
            log(f"{s['name']}'s stream finished. Checking who else is live...")
            continue
        job = queue_pop()
        if job:
            url = job[0]
            s = next((x for x in streamers() if x["name"] == (job[1] if len(job) > 1 else "")), {})
            log(f"Nobody is live. Smart-clipping the replay {url} ({s.get('name', 'unknown streamer')})...")
            subprocess.run([py, os.path.join(tools, "smart_clip.py"), url, "--shorts", "20",
                            "--long-minutes", "30", "--publish", "schedule"], cwd=ROOT, env=env_for(s))
            continue
        log(f"Nobody is live. Checking again in {CHECK_EVERY // 60} min.")
        time.sleep(CHECK_EVERY)


if __name__ == "__main__":
    sys.exit(main())
