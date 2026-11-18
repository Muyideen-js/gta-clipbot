"""Chat control: read messages sent to your Telegram bot and act on them.

Runs every few minutes from .github/workflows/telegram-commands.yml.
Only messages from TG_CHAT_ID are obeyed. Standard library only.

Things you can text the bot:
    https://youtube.com/watch?v=...            clip it with your default settings
    clip <link> 20 shorts 30 min               20 Shorts + a 30-minute best-of
    clip <link> 10 shorts no long post now     public right away instead of scheduled
    clip <link> private                        upload as drafts
    status                                     latest runs
    schedule                                   what's queued to go public
    help
"""
from __future__ import annotations

import json
import os
import re
import sys
import urllib.parse
import urllib.request

TG = f"https://api.telegram.org/bot{os.environ.get('TG_BOT_TOKEN', '')}"
GH = f"https://api.github.com/repos/{os.environ.get('GITHUB_REPOSITORY', '')}"
WORKFLOW = "clip-pipeline.yml"

HELP = (
    "🎮 <b>ClipBot commands</b>\n\n"
    "Send a link to a <b>finished</b> stream or video (YouTube, Twitch, Drive, Dropbox):\n"
    "• <code>link</code> → default settings\n"
    "• <code>clip link 20 shorts 30 min</code> → 20 Shorts + 30-min best-of\n"
    "• add <code>no long</code> to skip the long video\n"
    "• add <code>post now</code> (public at once) or <code>private</code> (drafts)\n"
    "• add <code>blur</code> to keep the whole frame (HUD/minimap)\n\n"
    "<code>status</code> latest runs · <code>schedule</code> upcoming posts"
)


def _req(url: str, data: dict | None = None, token: str | None = None, method: str | None = None):
    headers = {"Accept": "application/vnd.github+json"} if token else {}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    body = None
    if data is not None:
        body = json.dumps(data).encode()
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=body, headers=headers, method=method)
    with urllib.request.urlopen(req, timeout=30) as r:
        raw = r.read()
        return json.loads(raw) if raw else None


def reply(text: str) -> None:
    _req(f"{TG}/sendMessage", {"chat_id": os.environ["TG_CHAT_ID"], "text": text,
                               "parse_mode": "HTML", "disable_web_page_preview": True})


def parse(text: str) -> dict:
    """Turn a chat message into run settings."""
    t = text.lower()
    url = re.search(r"https?://\S+", text)
    out = {"video_url": url.group(0).rstrip(").,") if url else ""}
    if m := re.search(r"(\d{1,2})\s*(?:shorts?|clips?)", t):
        out["clips"] = str(max(1, min(20, int(m.group(1)))))
    if re.search(r"\bno\s+(?:long|best[- ]?of)\b", t):
        out["long_minutes"] = "0"
    elif m := re.search(r"(\d{2})\s*(?:-|\s)?\s*(?:min|minutes?|mins?)\b", t):
        out["long_minutes"] = str(max(10, min(90, int(m.group(1)))))
    elif re.search(r"\b(?:long|best[- ]?of)\b", t):
        out["long_minutes"] = "30"
    if re.search(r"\b(?:post|publish)\s+now\b|\bpublic\b", t):
        out["publish"] = "now"
    elif re.search(r"\b(?:private|drafts?)\b", t):
        out["publish"] = "private"
    if re.search(r"\bblur\b", t):
        out["layout"] = "blur"
    if m := re.search(r"(\d{2})\s*(?:s|sec|secs|seconds)\b", t):
        out["clip_seconds"] = str(max(15, min(60, int(m.group(1)))))
    return out


def start_run(settings: dict, token: str, branch: str) -> None:
    inputs = {k: v for k, v in settings.items() if v != ""}
    _req(f"{GH}/actions/workflows/{WORKFLOW}/dispatches", {"ref": branch, "inputs": inputs},
         token=token, method="POST")


def status(token: str) -> str:
    d = _req(f"{GH}/actions/workflows/{WORKFLOW}/runs?per_page=5", token=token)
    runs = d.get("workflow_runs", [])
    if not runs:
        return "No runs yet. Send me a link to start one."
    icon = {"success": "✅", "failure": "❌", "cancelled": "⏹"}
    lines = ["<b>Latest runs</b>"]
    for r in runs:
        st = icon.get(r["conclusion"], "⏳") if r["status"] == "completed" else "⏳"
        lines.append(f"{st} {r['created_at'][5:16].replace('T', ' ')} UTC · "
                     f"{r['status'] if r['status'] != 'completed' else r['conclusion']} · "
                     f"<a href=\"{r['html_url']}\">log</a>")
    return "\n".join(lines)


def upcoming(token: str, branch: str) -> str:
    try:
        d = _req(f"{GH}/contents/manifests/schedule.json?ref={branch}", token=token)
        import base64
        data = json.loads(base64.b64decode(d["content"]))
    except Exception:
        return "Nothing scheduled yet."
    import datetime as dt
    from zoneinfo import ZoneInfo
    tz = ZoneInfo(data.get("tz", "Africa/Lagos"))
    now = dt.datetime.now(dt.timezone.utc)
    rows = []
    for s in data.get("slots", []):
        at = dt.datetime.fromisoformat(s["at"].replace("Z", "+00:00"))
        if at > now:
            rows.append(f"{'🎬' if s['kind'] == 'long' else '📱'} {at.astimezone(tz):%a %H:%M} · {s['label'][:50]}")
    return "<b>Going public next</b>\n" + "\n".join(rows[:15]) if rows else "Nothing scheduled yet."


def main() -> int:
    token = os.environ["GITHUB_TOKEN"]
    branch = os.environ.get("GITHUB_REF_NAME", "main")
    owner = str(os.environ["TG_CHAT_ID"]).strip()
    updates = _req(f"{TG}/getUpdates?timeout=0&allowed_updates=%5B%22message%22%5D") or {}
    items = updates.get("result", [])
    if not items:
        print("No new messages.")
        return 0
    for u in items:
        msg = u.get("message") or {}
        chat = str(msg.get("chat", {}).get("id", ""))
        text = (msg.get("text") or msg.get("caption") or "").strip()
        if chat != owner:
            print(f"Ignoring message from chat {chat}")
            continue
        low = text.lower()
        print(f"Command: {text[:120]}")
        try:
            if low in ("/start", "help", "/help", "?"):
                reply(HELP)
            elif low in ("status", "/status"):
                reply(status(token))
            elif low in ("schedule", "/schedule", "queue"):
                reply(upcoming(token, branch))
            elif msg.get("video") or msg.get("document"):
                reply("Telegram only lets bots download files up to 20 MB, so I can't take videos here. "
                      "Send a link instead (YouTube, Twitch, Drive or Dropbox), or attach the file to a GitHub Release.")
            else:
                settings = parse(text)
                if not settings["video_url"]:
                    reply("Send me a link to a finished stream or video. Type <code>help</code> for options.")
                    continue
                start_run(settings, token, branch)
                desc = [f"{settings.get('clips', 'default')} Shorts"]
                lm = settings.get("long_minutes")
                if lm and lm != "0":
                    desc.append(f"{lm}-min best-of")
                desc.append({"now": "posting right away", "private": "as private drafts"}
                            .get(settings.get("publish", ""), "posting on schedule"))
                reply(f"🚀 On it: {', '.join(desc)}.\nI'll send each clip here as it's done. "
                      "Text <code>status</code> anytime.")
        except Exception as exc:
            print(f"  failed: {exc}")
            try:
                reply(f"⚠ Couldn't do that: {str(exc)[:200]}")
            except Exception:
                pass
    # Acknowledge everything we've read so it isn't processed twice.
    last = max(u["update_id"] for u in items)
    _req(f"{TG}/getUpdates?offset={last + 1}&timeout=0")
    return 0


if __name__ == "__main__":
    sys.exit(main())
