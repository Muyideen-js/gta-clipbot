"""Download new clips made on GitHub into Desktop\\GTA Clips (one folder per clip).

    python tools/get_clips.py        (also runs every 10 min via Windows Task Scheduler)

Needs the GitHub CLI (gh) logged in. Remembers every clip it already downloaded,
so clips added to a release later (every 20 min during a stream) still arrive.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile

REPO = "Muyideen-js/gta-clipbot"
DEST = os.path.join(os.path.expanduser("~"), "Desktop", "GTA Clips")
STATE = os.path.join(os.path.expanduser("~"), ".clipbot")
SEEN = os.path.join(STATE, "downloaded_clips.txt")
LOG = os.path.join(STATE, "get_clips.log")


def log(msg: str) -> None:
    line = f"[{dt.datetime.now():%d %b %H:%M}] {msg}"
    try:
        print(line)
    except UnicodeEncodeError:
        print(line.encode("ascii", "replace").decode())
    os.makedirs(STATE, exist_ok=True)
    with open(LOG, "a", encoding="utf-8") as fh:
        fh.write(line + "\n")


def gh(*args: str) -> str:
    return subprocess.run(["gh", *args], capture_output=True, text=True, check=True,
                          encoding="utf-8", errors="replace", timeout=600).stdout


def place(tmp: str, mp4: str) -> None:
    """Turn <Group>__<Folder>.mp4 (+ .txt) into DEST/<Group>/<Folder>/video.mp4 + text files."""
    stem = mp4[:-4]
    group, _, folder = stem.partition("__")
    group = group.replace("_", " ")
    folder = re.sub(r"_+", " ", folder).strip() or stem
    target = os.path.join(DEST, group, folder)
    k = 2
    while os.path.exists(target):
        target = os.path.join(DEST, group, f"{folder} ({k})")
        k += 1
    os.makedirs(target)
    shutil.move(os.path.join(tmp, mp4), os.path.join(target, "video.mp4"))
    txt = os.path.join(tmp, stem + ".txt")
    if os.path.exists(txt):
        text = open(txt, encoding="utf-8").read()
        for label, fname in (("YOUTUBE", "youtube.txt"), ("TITLE", "title.txt"),
                             ("DESCRIPTION", "description.txt"), ("TIKTOK", "tiktok.txt")):
            m = re.search(rf"===== {label} =====\n(.*?)(?=\n===== |\Z)", text, re.S)
            if m:
                with open(os.path.join(target, fname), "w", encoding="utf-8") as fh:
                    fh.write(m.group(1).strip() + "\n")


def main() -> int:
    os.makedirs(STATE, exist_ok=True)
    seen = set(open(SEEN, encoding="utf-8").read().split("\n")) if os.path.exists(SEEN) else set()
    try:
        releases = json.loads(gh("release", "list", "-R", REPO, "--limit", "15", "--json", "tagName"))
    except Exception as exc:
        log(f"Couldn't reach GitHub ({str(exc)[:100]}). Will try again next time.")
        return 0
    total = 0
    for tag in [r["tagName"] for r in reversed(releases) if r["tagName"].startswith("clips-")]:
        try:
            assets = json.loads(gh("release", "view", tag, "-R", REPO, "--json", "assets"))["assets"]
        except Exception:
            continue
        new = [a["name"] for a in assets if a["name"].endswith(".mp4") and f"{tag}/{a['name']}" not in seen]
        for name in new:
            with tempfile.TemporaryDirectory() as tmp:
                try:
                    gh("release", "download", tag, "-R", REPO, "-D", tmp,
                       "-p", name, "-p", name[:-4] + ".txt")
                    place(tmp, name)
                except Exception as exc:
                    log(f"Couldn't download {name}: {str(exc)[:100]}")
                    continue
            with open(SEEN, "a", encoding="utf-8") as fh:
                fh.write(f"{tag}/{name}\n")
            seen.add(f"{tag}/{name}")
            total += 1
    if total:
        log(f"{total} new clips in {DEST}")
    else:
        log("No new clips.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
