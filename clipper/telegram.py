"""Telegram Bot API: send each clip as a playable video with its caption."""
from __future__ import annotations

import html
import os
import time

import requests

MAX_BOT_UPLOAD = 49 * 1024 * 1024   # Bot API limit is 50 MB


def configured() -> bool:
    return bool(os.environ.get("TG_BOT_TOKEN") and os.environ.get("TG_CHAT_ID"))


def _call(method: str, data: dict, files: dict | None = None) -> dict:
    url = f"https://api.telegram.org/bot{os.environ['TG_BOT_TOKEN']}/{method}"
    for attempt in range(4):
        r = requests.post(url, data=data, files=files, timeout=300)
        d = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
        if r.status_code == 429 and attempt < 3:
            time.sleep(int(d.get("parameters", {}).get("retry_after", 5)) + 1)
            if files:
                for f in files.values():
                    f[1].seek(0)
            continue
        if not d.get("ok"):
            raise RuntimeError(f"Telegram {method} failed: {r.status_code} {r.text[:300]}")
        return d
    raise RuntimeError(f"Telegram {method} rate limited")


def esc(s: str) -> str:
    return html.escape(s, quote=False)


def send_message(text: str) -> None:
    _call("sendMessage", {"chat_id": os.environ["TG_CHAT_ID"], "text": text,
                          "parse_mode": "HTML", "disable_web_page_preview": "true"})


def send_clip(video_path: str, thumb_path: str | None, caption_html: str) -> None:
    chat = os.environ["TG_CHAT_ID"]
    caption_html = caption_html[:1020]
    if os.path.getsize(video_path) <= MAX_BOT_UPLOAD:
        files = {"video": ("clip.mp4", open(video_path, "rb"), "video/mp4")}
        if thumb_path:
            files["thumbnail"] = ("thumb.jpg", open(thumb_path, "rb"), "image/jpeg")
        try:
            _call("sendVideo", {"chat_id": chat, "caption": caption_html, "parse_mode": "HTML",
                                "supports_streaming": "true", "width": "1080", "height": "1920"},
                  files)
        finally:
            for f in files.values():
                f[1].close()
        return
    # Too big for the bot API: send a still instead.
    if thumb_path:
        with open(thumb_path, "rb") as fh:
            _call("sendPhoto", {"chat_id": chat, "parse_mode": "HTML",
                                "caption": caption_html + "\n<i>(clip over 50 MB, see the run artifacts)</i>"},
                  {"photo": ("thumb.jpg", fh, "image/jpeg")})
    else:
        send_message(caption_html)
