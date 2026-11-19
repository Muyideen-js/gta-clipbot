"""Download new clips made on GitHub into Desktop\\GTA Clips (one folder per clip).

    python tools/get_clips.py        (or double-click "Get new clips.bat")

Needs the GitHub CLI (gh) logged in. Remembers what it already downloaded.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile

REPO = "Muyideen-js/gta-clipbot"
DEST = os.path.join(os.path.expanduser("~"), "Desktop", "GTA Clips")
SEEN = os.path.join(os.path.expanduser("~"), ".clipbot", "downloaded_releases.txt")


def gh(*args: str) -> str:
    return subprocess.run(["gh", *args], capture_output=True, text=True, check=True,
                          encoding="utf-8", errors="replace").stdout


def main() -> int:
    seen = set()
    if os.path.exists(SEEN):
        seen = set(open(SEEN, encoding="utf-8").read().split())
    releases = json.loads(gh("release", "list", "-R", REPO, "--limit", "50", "--json", "tagName"))
    new = [r["tagName"] for r in releases if r["tagName"].startswith("clips-") and r["tagName"] not in seen]
    if not new:
        print("No new clips.")
        return 0
    total = 0
    for tag in reversed(new):
        print(f"Downloading {tag} ...")
        with tempfile.TemporaryDirectory() as tmp:
            gh("release", "download", tag, "-R", REPO, "-D", tmp)
            for f in sorted(os.listdir(tmp)):
                if not f.endswith(".mp4"):
                    continue
                stem = f[:-4]
                group, _, folder = stem.partition("__")
                group = group.replace("_", " ")
                folder = re.sub(r"_+", " ", folder).strip() or stem
                target = os.path.join(DEST, group, folder)
                k = 2
                while os.path.exists(target):
                    target = os.path.join(DEST, group, f"{folder} ({k})")
                    k += 1
                os.makedirs(target)
                shutil.move(os.path.join(tmp, f), os.path.join(target, "video.mp4"))
                txt = os.path.join(tmp, stem + ".txt")
                if os.path.exists(txt):
                    text = open(txt, encoding="utf-8").read()
                    # Split back into the separate copy-paste files.
                    for label, fname in (("YOUTUBE", "youtube.txt"), ("TITLE", "title.txt"),
                                         ("DESCRIPTION", "description.txt"), ("TIKTOK", "tiktok.txt")):
                        m = re.search(rf"===== {label} =====\n(.*?)(?=\n===== |\Z)", text, re.S)
                        if m:
                            with open(os.path.join(target, fname), "w", encoding="utf-8") as fh:
                                fh.write(m.group(1).strip() + "\n")
                total += 1
        os.makedirs(os.path.dirname(SEEN), exist_ok=True)
        with open(SEEN, "a", encoding="utf-8") as fh:
            fh.write(tag + "\n")
    print(f"Done: {total} new clips in {DEST}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
