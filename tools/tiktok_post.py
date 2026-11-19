"""Post the next clip to TikTok when one is due (run every 30 min by tiktok-post.yml).

Each day (Lagos time) gets 6 or 7 posts, spread evenly between TIKTOK_START_HOUR
and TIKTOK_END_HOUR with a little random wiggle. A run posts at most one clip,
and only when fewer clips have gone out today than slots have passed.

Clips come from the newest GitHub releases made by the live clipper,
"Post_by_hand" ones first; each clip is posted only once
(manifests/tiktok_posted.json). Use --now to post one immediately.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import random
import subprocess
import sys
import tempfile
from zoneinfo import ZoneInfo

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from clipper.tiktok import post_video  # noqa: E402

REPO = os.environ.get("GITHUB_REPOSITORY", "Muyideen-js/gta-clipbot")
LOG = os.path.join("manifests", "tiktok_posted.json")
TZ = ZoneInfo(os.environ.get("POST_TZ", "Africa/Lagos"))
START = float(os.environ.get("TIKTOK_START_HOUR", "9"))
END = float(os.environ.get("TIKTOK_END_HOUR", "23"))


def gh(*args: str) -> str:
    return subprocess.run(["gh", *args, "-R", REPO], check=True, capture_output=True,
                          text=True, encoding="utf-8").stdout


def posts_today(day: str) -> int:
    return sum(1 for p in load() if p.get("day") == day)


def day_plan(day: str) -> list[dt.datetime]:
    """6 or 7 times for this day, the same every time it's computed."""
    seed = int(hashlib.sha256(day.encode()).hexdigest(), 16)
    rnd = random.Random(seed)
    n = 6 + seed % 2
    base = dt.datetime.fromisoformat(day).replace(tzinfo=TZ)
    step = (END - START) / (n - 1)
    out = []
    for i in range(n):
        hours = START + i * step + rnd.uniform(-0.2, 0.2) * step
        out.append(base + dt.timedelta(hours=min(max(hours, START), END)))
    return out


def load() -> list[dict]:
    try:
        with open(LOG, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return []


def save(entries: list[dict]) -> None:
    os.makedirs(os.path.dirname(LOG), exist_ok=True)
    with open(LOG, "w", encoding="utf-8") as fh:
        json.dump(entries, fh, indent=1)


def candidates(done: set[str]) -> list[tuple[str, str]]:
    releases = json.loads(gh("release", "list", "-L", "12", "--json", "tagName"))
    hand, bot = [], []
    for rel in releases:
        tag = rel["tagName"]
        if not tag.startswith("clips-"):
            continue
        assets = json.loads(gh("release", "view", tag, "--json", "assets"))["assets"]
        for a in sorted(assets, key=lambda a: a["name"], reverse=True):
            name = a["name"]
            if name.endswith(".mp4") and f"{tag}/{name}" not in done:
                (hand if name.startswith("Post_by_hand") else bot).append((tag, name))
    return hand + bot


def caption_from(txt: str, fallback: str) -> str:
    sections, cur = {}, None
    for line in txt.splitlines():
        if line.startswith("===== ") and line.endswith(" ====="):
            cur = line.strip("= ").strip()
            sections[cur] = []
        elif cur:
            sections[cur].append(line)
    for key in ("TIKTOK", "TITLE"):
        if sections.get(key):
            return "\n".join(sections[key]).strip()
    return fallback


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--now", action="store_true", help="post one clip right away")
    args = ap.parse_args()

    if not all(os.environ.get(k) for k in ("TIKTOK_CLIENT_KEY", "TIKTOK_CLIENT_SECRET", "TIKTOK_REFRESH_TOKEN")):
        print("TikTok isn't connected yet (secrets missing). Nothing to do.")
        return

    now = dt.datetime.now(TZ)
    day = now.date().isoformat()
    plan = day_plan(day)
    due = sum(1 for t in plan if t <= now)
    sent = posts_today(day)
    print(f"Today {day}: {len(plan)} posts planned at "
          + ", ".join(t.strftime("%H:%M") for t in plan) + f". Sent {sent}, due {due}.")
    if not args.now and sent >= due:
        print("Nothing due right now.")
        return

    entries = load()
    done = {e["key"] for e in entries}
    queue = candidates(done)
    if not queue:
        print("No new clips to post.")
        return

    with tempfile.TemporaryDirectory() as tmp:
        for tag, name in queue[:3]:  # try up to 3 in case a file is bad
            base = name[:-4]
            try:
                gh("release", "download", tag, "-p", name, "-p", base + ".txt", "-D", tmp, "--clobber")
            except subprocess.CalledProcessError as e:
                print(f"Couldn't download {name}: {e.stderr[:200]}")
                continue
            txt_path = os.path.join(tmp, base + ".txt")
            txt = open(txt_path, encoding="utf-8").read() if os.path.exists(txt_path) else ""
            title = base.split("__", 1)[-1].split("_-_", 1)[-1].replace("_", " ")
            caption = caption_from(txt, f"{title} #GTA6 #fyp #gaming")
            print(f"Posting {name} ...")
            try:
                res = post_video(os.path.join(tmp, name), caption)
            except Exception as e:  # noqa: BLE001
                print(f"TikTok post failed: {e}")
                sys.exit(1)
            entries.append({"key": f"{tag}/{name}", "day": day, "time": now.isoformat(timespec="minutes"),
                            "privacy": res["privacy"], "status": res["status"], "publish_id": res["publish_id"]})
            save(entries)
            print(f"Posted ({res['privacy']}, {res['status']}).")
            if res["privacy"] == "SELF_ONLY":
                print("Posted privately: TikTok only allows public posts after the app is approved.")
            if res.get("new_refresh_token"):
                print("::warning::TikTok issued a new refresh token; run tools/tiktok_auth.py again soon.")
            return
    print("Couldn't post any clip this time.")


if __name__ == "__main__":
    main()
