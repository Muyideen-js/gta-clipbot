"""Runs on GitHub every 10 minutes: if a streamer from streamers.json is live and
playing GTA, and no recording is already running, start the "Live clip" workflow."""
from __future__ import annotations

import json
import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import live_clipper  # noqa: E402

live_clipper.LOG_FILE = os.devnull
from watch_streamers import live_video, streamers  # noqa: E402

live_clipper.log = print


def gh(*args: str) -> str:
    return subprocess.run(["gh", *args], capture_output=True, text=True, check=True).stdout


def main() -> int:
    busy = []
    for status in ("in_progress", "queued"):
        busy += json.loads(gh("run", "list", "--workflow", "live-clip.yml", "--status", status,
                              "--json", "databaseId", "--limit", "5"))
    if busy:
        print("A recording is already running; nothing to do.")
        return 0
    for s in streamers():
        url = live_video(s)
        if url:
            print(f"{s['name']} is live: {url}. Starting a recording.")
            gh("workflow", "run", "live-clip.yml", "-f", f"url={url}", "-f", f"streamer={s['name']}")
            return 0
    print("Nobody on the list is live and playing GTA.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
