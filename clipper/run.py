"""Pipeline entry point used by GitHub Actions (and runnable locally).

    python -m clipper.run --inputs work/inputs.txt --clips 5 --clip-seconds 30

For every source video: detect highlights -> cut 9:16 -> caption -> upload to
YouTube as PRIVATE -> send the clip to Telegram. Each clip finishes all steps
before the next starts, so a failure late in the run keeps the earlier clips.
A JSON manifest is written for the dashboard.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import shutil
import sys
import traceback
from urllib.parse import urlparse

import requests

from . import captions, cut, detect, longform, media, schedule, telegram, youtube

VIDEO_EXT = (".mp4", ".mov", ".mkv", ".webm", ".avi", ".m4v")


def log(msg: str) -> None:
    try:
        print(msg, flush=True)
    except UnicodeEncodeError:  # Windows consoles without UTF-8
        print(msg.encode("ascii", "replace").decode(), flush=True)


def download(url: str, dest_dir: str) -> str:
    """Fetch a video from a direct link, Google Drive or Dropbox."""
    os.makedirs(dest_dir, exist_ok=True)
    host = urlparse(url).netloc
    if any(h in host for h in ("youtube.com", "youtu.be", "twitch.tv", "kick.com")):
        return _ytdlp(url, dest_dir)
    if "drive.google.com" in host or "docs.google.com" in host:
        import gdown  # handles Drive's large-file confirmation page
        out = gdown.download(url, output=dest_dir + os.sep, quiet=False, fuzzy=True)
        if not out:
            raise RuntimeError("Google Drive download failed. Is the file shared as 'Anyone with the link'?")
        return out
    if "dropbox.com" in host:
        url = re.sub(r"([?&])dl=0", r"\1dl=1", url)
        if "dl=1" not in url:
            url += ("&" if "?" in url else "?") + "dl=1"
    name = os.path.basename(urlparse(url).path) or "video.mp4"
    if not name.lower().endswith(VIDEO_EXT):
        name += ".mp4"
    path = os.path.join(dest_dir, name)
    with requests.get(url, stream=True, timeout=60, allow_redirects=True) as r:
        r.raise_for_status()
        ctype = r.headers.get("content-type", "")
        if "text/html" in ctype:
            raise RuntimeError(f"{url} returned a web page, not a video. Use a direct download link.")
        with open(path, "wb") as fh:
            for chunk in r.iter_content(1 << 20):
                fh.write(chunk)
    return path


def _ytdlp(url: str, dest_dir: str) -> str:
    """Download a finished stream/VOD with yt-dlp (max 1080p).

    GitHub's servers are often asked to 'sign in to confirm you're not a bot'.
    Put a cookies.txt export (Netscape format) in the YTDLP_COOKIES secret to get past it.
    """
    import subprocess
    cmd = ["yt-dlp", "-f", "bv*[height<=1080]+ba/b[height<=1080]/b", "--merge-output-format", "mp4",
           "--no-playlist", "--no-part", "-N", "4", "-o", os.path.join(dest_dir, "%(id)s.%(ext)s"),
           "--print", "after_move:filepath"]
    cookies = os.environ.get("YTDLP_COOKIES", "").strip()
    if cookies:
        cpath = os.path.join(dest_dir, "cookies.txt")
        with open(cpath, "w", encoding="utf-8") as fh:
            fh.write(cookies + "\n")
        cmd += ["--cookies", cpath]
    res = subprocess.run(cmd + [url], capture_output=True, text=True)
    if res.returncode != 0:
        err = res.stderr[-1200:]
        if "confirm you" in err or "Sign in" in err:
            err += ("\nYouTube blocked the GitHub server. Add a cookies.txt export as the "
                    "YTDLP_COOKIES secret, or attach the recording to a GitHub Release instead.")
        if "live event will begin" in err or "is live" in err.lower():
            err += "\nThe stream is still live. Run this again after it ends."
        raise RuntimeError(f"yt-dlp failed:\n{err}")
    path = res.stdout.strip().splitlines()[-1]
    return path


def caption_html(clip: dict) -> str:
    tags = " ".join("#" + t for t in clip["hashtags"])
    lines = [f"🎮 <b>{telegram.esc(clip['title'])}</b>", "", telegram.esc(tags), ""]
    lines.append(f"⏱ {clip['duration']:.0f}s from {telegram.esc(clip['source'])} @ "
                 f"{int(clip['start'] // 60)}:{int(clip['start'] % 60):02d}")
    st = clip["youtube"]["status"]
    if st == "uploaded":
        yt = clip["youtube"]
        if yt.get("goes_live"):
            lines.append(f"🗓 Goes public {yt['goes_live']}")
        elif yt.get("privacy") == "public":
            lines.append("🟢 Public now")
        else:
            lines.append("🔒 Private draft")
        lines.append(f"▶ {yt['studio_url']}")
    elif st == "quota":
        lines.append("⚠ Not on YouTube: daily API quota used up. Upload this file by hand or re-run tomorrow.")
    elif st == "capped":
        lines.append("⏸ Not uploaded: over this run's upload cap.")
    elif st == "error":
        lines.append("⚠ YouTube upload failed, see the run log.")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--inputs", help="text file with one video path per line")
    ap.add_argument("--url", default="", help="download this video first")
    ap.add_argument("--clips", type=int, default=5, help="clips per source video")
    ap.add_argument("--clip-seconds", type=float, default=30)
    ap.add_argument("--layout", choices=["crop", "blur"], default="crop")
    ap.add_argument("--upload", default="true")
    ap.add_argument("--max-uploads", type=int, default=6)
    ap.add_argument("--long-minutes", type=float, default=0,
                    help="also build a 'best of' 16:9 video this long (0 = off)")
    ap.add_argument("--long-segment", type=float, default=60, help="seconds per moment in the long video")
    ap.add_argument("--publish", choices=["schedule", "now", "private"], default="schedule",
                    help="schedule = spread posts across the day, now = public at once, private = drafts")
    ap.add_argument("--work", default="work")
    ap.add_argument("--manifest-dir", default="manifests")
    args = ap.parse_args(argv)

    run_id = os.environ.get("GITHUB_RUN_ID") or dt.datetime.now().strftime("local-%Y%m%d-%H%M%S")
    out_dir = os.path.join(args.work, "clips")
    os.makedirs(out_dir, exist_ok=True)

    sources: list[str] = []
    if args.inputs and os.path.exists(args.inputs):
        with open(args.inputs, encoding="utf-8") as fh:
            sources += [ln.strip() for ln in fh if ln.strip()]
    if args.url:
        log(f"Downloading {args.url}")
        sources.append(download(args.url, os.path.join(args.work, "in")))
    sources = [s for s in sources if s.lower().endswith(VIDEO_EXT) and os.path.exists(s)]
    if not sources:
        log("No video files to process.")
        return 0

    do_upload = args.upload.lower() == "true" and youtube.configured()
    if args.upload.lower() == "true" and not youtube.configured():
        log("YouTube secrets not set, skipping uploads.")
    tg = telegram.configured()
    if not tg:
        log("Telegram secrets not set, skipping notifications.")

    manifest = {
        "run_id": run_id,
        "created": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "repo": os.environ.get("GITHUB_REPOSITORY", ""),
        "settings": {"clips": args.clips, "clip_seconds": args.clip_seconds,
                     "layout": args.layout, "upload": do_upload},
        "sources": [],
        "clips": [],
    }
    uploads = 0
    quota_hit = False
    n = 0
    used_titles: list[str] = []
    sched = schedule.Schedule(os.path.join(args.manifest_dir, "schedule.json"))
    credit = (os.environ.get("CREDIT_LINE") or "").strip()

    def publish_upload(path: str, title: str, description: str, hashtags: list[str], kind: str) -> dict:
        """Upload with the chosen publish mode. Returns the manifest's youtube block."""
        desc = description.strip()
        if credit:
            desc += f"\n\n{credit}"
        desc += "\n\n" + " ".join("#" + t for t in hashtags)
        tags = hashtags + [t for t in captions.KEYWORD_TAGS if t.replace(" ", "").lower()
                           not in {h.lower() for h in hashtags}]
        when = None
        privacy = "public" if args.publish == "now" else "private"
        if args.publish == "schedule":
            when = sched.next(kind, title)
        try:
            vid = youtube.upload(path, title, desc, tags, privacy=privacy,
                                 publish_at=schedule.utc_iso(when) if when else None)
        except Exception:
            if when:
                sched.release(when)
            raise
        block = {"status": "uploaded", "id": vid, "privacy": privacy,
                 "studio_url": f"https://studio.youtube.com/video/{vid}/edit",
                 "watch_url": (f"https://youtube.com/shorts/{vid}" if kind == "short"
                               else f"https://youtu.be/{vid}")}
        if when:
            block["publish_at"] = schedule.utc_iso(when)
            block["goes_live"] = schedule.human(when, sched.tz)
        return block

    for src in sources:
        name = os.path.basename(src)
        log(f"\n=== {name}")
        try:
            info = media.probe(src)
        except Exception as exc:
            log(f"  skipped: {exc}")
            manifest["sources"].append({"name": name, "error": str(exc)})
            continue
        log(f"  {info.width}x{info.height}, {info.duration:.0f}s, audio={info.has_audio}")
        highlights = detect.find_highlights(info, args.clips, args.clip_seconds) if args.clips > 0 else []
        log(f"  highlights at: {', '.join(f'{h.peak:.0f}s' for h in highlights)}")
        manifest["sources"].append({"name": name, "duration": round(info.duration, 1),
                                    "width": info.width, "height": info.height})

        # Long "best of" first, so it gets an upload slot before the Shorts use the quota.
        if args.long_minutes and info.duration >= args.long_minutes * 60 * 1.3:
            lv = {"id": f"{run_id}-long-{len(manifest.get('long', [])) + 1}", "source": name,
                  "youtube": {"status": "skipped"}, "telegram": False}
            try:
                log(f"  building {args.long_minutes:.0f}-min best-of")
                segs = longform.pick_segments(info, args.long_minutes, args.long_segment)
                lpath = os.path.join(out_dir, f"gta6_{lv['id']}.mp4")
                total = longform.build(info, segs, args.long_segment, lpath)
                stills = cut.frames(lpath, total, lpath[:-4] + "_f", count=4, width=640)
                cap = captions.write_caption({"kind": "long", "source": name, "start": 0,
                                              "duration": total, "intensity": "massive"}, stills)
                best = max(segs, key=lambda s: s.score)
                thumb = longform.thumbnail(info, best, cap["thumb_text"], lpath[:-4] + "_thumb.jpg")
                lv.update(title=cap["title"], description=cap["description"], hashtags=cap["hashtags"],
                          thumb_text=cap["thumb_text"], duration=round(total, 1), segments=len(segs),
                          size_mb=round(os.path.getsize(lpath) / 1048576, 1))
                log(f"      {total / 60:.1f} min, title: {cap['title']}")
                if do_upload and not quota_hit and uploads < args.max_uploads:
                    chapters = "\n".join(
                        f"{int(sum(min(args.long_segment, info.duration - x.start) for x in segs[:i]) // 60)}:"
                        f"{int(sum(min(args.long_segment, info.duration - x.start) for x in segs[:i]) % 60):02d}"
                        f" Moment {i + 1}" for i in range(len(segs))) if len(segs) >= 3 else ""
                    desc = f"{cap['description']}\n\n{chapters}" if chapters else cap["description"]
                    try:
                        lv["youtube"] = publish_upload(lpath, cap["title"], desc, cap["hashtags"], "long")
                        vid = lv["youtube"]["id"]
                        uploads += 1
                        try:
                            youtube.set_thumbnail(vid, thumb)
                            lv["youtube"]["thumbnail"] = True
                        except Exception as exc:
                            log(f"      thumbnail: {exc}")
                    except youtube.QuotaExceeded:
                        quota_hit = True
                        lv["youtube"] = {"status": "quota"}
                    except Exception as exc:
                        lv["youtube"] = {"status": "error", "error": str(exc)[:300]}
                        log(f"      upload failed: {exc}")
                if tg:
                    try:
                        yt = lv["youtube"]
                        link = yt.get("studio_url", "not on YouTube, file is in the run artifacts")
                        when = (f"🗓 Goes public {yt['goes_live']}\n" if yt.get("goes_live")
                                else "🟢 Public now\n" if yt.get("privacy") == "public" else "")
                        with open(thumb, "rb") as fh:
                            telegram._call("sendPhoto", {
                                "chat_id": os.environ["TG_CHAT_ID"], "parse_mode": "HTML",
                                "caption": (f"🎬 <b>{telegram.esc(cap['title'])}</b>\n"
                                            f"{total / 60:.0f} min · {len(segs)} moments\n{when}▶ {telegram.esc(link)}")[:1020]},
                                {"photo": ("thumb.jpg", fh, "image/jpeg")})
                        lv["telegram"] = True
                    except Exception as exc:
                        log(f"      telegram failed: {exc}")
            except Exception as exc:
                lv["error"] = str(exc)[:400]
                log(f"      long video failed: {exc}")
                traceback.print_exc()
            manifest.setdefault("long", []).append(lv)
        elif args.long_minutes:
            log(f"  source too short for a {args.long_minutes:.0f}-min best-of, skipping it")

        for h in highlights:
            n += 1
            cid = f"{run_id}-{n:02d}"
            dur = min(args.clip_seconds, info.duration - h.start)
            clip = {"id": cid, "source": name, "start": h.start, "duration": round(dur, 2),
                    "score": h.score, "intensity": h.intensity,
                    "youtube": {"status": "skipped"}, "telegram": False}
            base = os.path.join(out_dir, f"gta6_{cid}")
            try:
                log(f"  [{n}] cutting {h.start:.1f}s +{dur:.0f}s")
                cut.cut(info, h.start, dur, base + ".mp4", args.layout)
                stills = cut.frames(base + ".mp4", dur, base + "_f")
                clip["thumb"] = cut.tiny_thumb_data_uri(base + ".mp4", dur, base + "_t.jpg")
                clip["size_mb"] = round(os.path.getsize(base + ".mp4") / 1048576, 1)

                cap = captions.write_caption(
                    {"source": name, "start": h.start, "duration": dur, "intensity": h.intensity,
                     "avoid": used_titles},
                    stills)
                used_titles.append(cap["title"])
                clip.update(title=cap["title"], description=cap["description"],
                            hashtags=cap["hashtags"], caption_source=cap["source"])
                if "#shorts" not in clip["title"].lower():
                    yt_title = f"{clip['title']} #Shorts"[:100]
                else:
                    yt_title = clip["title"]
                log(f"      title ({cap['source']}): {clip['title']}")

                if do_upload and quota_hit:
                    clip["youtube"] = {"status": "quota"}
                elif do_upload and uploads >= args.max_uploads:
                    clip["youtube"] = {"status": "capped"}
                elif do_upload:
                    try:
                        clip["youtube"] = publish_upload(base + ".mp4", yt_title, clip["description"],
                                                         clip["hashtags"], "short")
                        uploads += 1
                        log(f"      uploaded: {clip['youtube']['id']}"
                            + (f", goes public {clip['youtube']['goes_live']}" if clip["youtube"].get("goes_live") else ""))
                    except youtube.QuotaExceeded as exc:
                        quota_hit = True
                        clip["youtube"] = {"status": "quota", "error": str(exc)[:200]}
                        log("      YouTube quota exhausted, remaining clips go to Telegram only")
                    except Exception as exc:
                        clip["youtube"] = {"status": "error", "error": str(exc)[:300]}
                        log(f"      upload failed: {exc}")

                if tg:
                    try:
                        telegram.send_clip(base + ".mp4", stills[1] if len(stills) > 1 else None,
                                           caption_html(clip))
                        clip["telegram"] = True
                    except Exception as exc:
                        log(f"      telegram failed: {exc}")
            except Exception as exc:
                clip["error"] = str(exc)[:400]
                log(f"      clip failed: {exc}")
                traceback.print_exc()
            manifest["clips"].append(clip)

    os.makedirs(args.manifest_dir, exist_ok=True)
    if args.publish == "schedule":
        sched.save()
    for fname in (f"{run_id}.json", "latest.json"):
        with open(os.path.join(args.manifest_dir, fname), "w", encoding="utf-8") as fh:
            json.dump(manifest, fh, indent=1, ensure_ascii=False)

    ok = [c for c in manifest["clips"] if "error" not in c]
    up = [c for c in ok if c["youtube"]["status"] == "uploaded"]
    if tg:
        try:
            longs = [x for x in manifest.get("long", []) if x["youtube"]["status"] == "uploaded"]
            how = {"schedule": "scheduled across the day: YouTube posts them by itself",
                   "now": "posted publicly", "private": "uploaded as private drafts"}[args.publish]
            not_up = len(ok) - len(up)
            telegram.send_message(
                f"✅ <b>Run finished</b>: {len(ok)} Shorts"
                + (f" + {len(longs)} best-of video" if longs else "")
                + f", {len(up) + len(longs)} {how}."
                + (f"\n📲 {not_up} more are above as files: post them by hand or they'll wait for tomorrow's quota."
                   if not_up and do_upload else "")
                + ("\n⚠ YouTube's daily upload quota ran out during this run." if quota_hit else ""))
        except Exception as exc:
            log(f"telegram summary failed: {exc}")

    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as fh:
            fh.write(f"### {len(ok)} clips · {len(up)} uploaded\n\n| # | Title | YouTube |\n|---|---|---|\n")
            for c in manifest["clips"]:
                yt = c["youtube"]
                link = f"[{yt['id']}]({yt['studio_url']})" if yt.get("id") else yt["status"]
                fh.write(f"| {c['id']} | {c.get('title', c.get('error', ''))[:80]} | {link} |\n")
    log(f"\nDone: {len(ok)} clips, {len(up)} uploaded.")
    return 0 if ok or not manifest["clips"] else 1


if __name__ == "__main__":
    sys.exit(main())
