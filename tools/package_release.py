"""Flatten the clip folders made on GitHub into release assets.

GitHub release asset names can't contain folders (and spaces become dots), so
each clip becomes two files:
    <Group>__<Folder>.mp4     the video
    <Group>__<Folder>.txt     title, description, TikTok caption, YouTube link
with spaces written as "_". tools/get_clips.py turns them back into folders.
"""
from __future__ import annotations

import os
import re
import shutil
import sys

src, out = sys.argv[1], sys.argv[2]
os.makedirs(out, exist_ok=True)
count = 0
for group in sorted(os.listdir(src)) if os.path.isdir(src) else []:
    gdir = os.path.join(src, group)
    for folder in sorted(os.listdir(gdir)):
        fdir = os.path.join(gdir, folder)
        video = os.path.join(fdir, "video.mp4")
        if not os.path.exists(video):
            continue
        name = re.sub(r"[^A-Za-z0-9._-]+", "_", f"{group}__{folder}").strip("_")[:120]
        shutil.copy2(video, os.path.join(out, name + ".mp4"))
        parts = []
        for fname, label in (("youtube.txt", "YOUTUBE"), ("title.txt", "TITLE"),
                             ("description.txt", "DESCRIPTION"), ("tiktok.txt", "TIKTOK")):
            p = os.path.join(fdir, fname)
            if os.path.exists(p):
                with open(p, encoding="utf-8") as fh:
                    parts.append(f"===== {label} =====\n{fh.read().strip()}\n")
        with open(os.path.join(out, name + ".txt"), "w", encoding="utf-8") as fh:
            fh.write("\n".join(parts))
        count += 1
print(f"{count} clips packaged")
