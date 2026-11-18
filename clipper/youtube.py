"""Minimal YouTube Data API v3 uploader (refresh token + resumable upload).

Uses plain HTTP so the runner doesn't need google-api-python-client.
Needs YT_CLIENT_ID, YT_CLIENT_SECRET and YT_REFRESH_TOKEN
(create the refresh token once with tools/get_youtube_refresh_token.py).
"""
from __future__ import annotations

import os
import time

import requests

TOKEN_URL = "https://oauth2.googleapis.com/token"
UPLOAD_URL = "https://www.googleapis.com/upload/youtube/v3/videos"
CHUNK = 8 * 1024 * 1024


class QuotaExceeded(Exception):
    pass


def configured() -> bool:
    return all(os.environ.get(k) for k in ("YT_CLIENT_ID", "YT_CLIENT_SECRET", "YT_REFRESH_TOKEN"))


_token: tuple[str, float] | None = None


def access_token() -> str:
    global _token
    if _token and _token[1] > time.time() + 60:
        return _token[0]
    r = requests.post(TOKEN_URL, timeout=30, data={
        "client_id": os.environ["YT_CLIENT_ID"],
        "client_secret": os.environ["YT_CLIENT_SECRET"],
        "refresh_token": os.environ["YT_REFRESH_TOKEN"],
        "grant_type": "refresh_token",
    })
    if r.status_code != 200:
        hint = ""
        if "invalid_grant" in r.text:
            hint = (" The refresh token was revoked or expired. If your OAuth consent screen "
                    "is in 'Testing', tokens die after 7 days: set it to 'In production' and "
                    "run tools/get_youtube_refresh_token.py again.")
        raise RuntimeError(f"Google token refresh failed: {r.status_code} {r.text[:300]}{hint}")
    d = r.json()
    _token = (d["access_token"], time.time() + int(d.get("expires_in", 3600)))
    return _token[0]


def _raise_for(r: requests.Response, what: str):
    if r.status_code == 403 and any(x in r.text for x in ("quotaExceeded", "uploadLimitExceeded", "rateLimitExceeded")):
        raise QuotaExceeded(r.text[:300])
    raise RuntimeError(f"{what} failed: {r.status_code} {r.text[:400]}")


def set_thumbnail(video_id: str, jpg_path: str) -> None:
    """Custom thumbnails need a phone-verified channel (youtube.com/verify)."""
    with open(jpg_path, "rb") as fh:
        r = requests.post(f"https://www.googleapis.com/upload/youtube/v3/thumbnails/set?videoId={video_id}",
                          data=fh.read(), timeout=120,
                          headers={"Authorization": f"Bearer {access_token()}", "Content-Type": "image/jpeg"})
    if r.status_code != 200:
        hint = " Verify your channel at youtube.com/verify to allow custom thumbnails." if r.status_code == 403 else ""
        raise RuntimeError(f"Thumbnail upload failed: {r.status_code} {r.text[:200]}{hint}")


def upload(path: str, title: str, description: str, tags: list[str],
           privacy: str = "private", publish_at: str | None = None,
           category_id: str = "20") -> str:
    """Upload a video and return its id. category 20 = Gaming."""
    size = os.path.getsize(path)
    status = {"privacyStatus": privacy, "selfDeclaredMadeForKids": False}
    if publish_at:
        status["privacyStatus"] = "private"
        status["publishAt"] = publish_at
    # Keep tags inside YouTube's 500-character budget.
    kept, total = [], 0
    for t in tags:
        if total + len(t) + 1 > 480:
            break
        kept.append(t)
        total += len(t) + 1
    meta = {
        "snippet": {"title": title[:100], "description": description[:4900],
                    "tags": kept, "categoryId": category_id,
                    "defaultLanguage": "en", "defaultAudioLanguage": "en"},
        "status": status,
    }
    r = requests.post(
        f"{UPLOAD_URL}?uploadType=resumable&part=snippet,status", json=meta, timeout=60,
        headers={"Authorization": f"Bearer {access_token()}",
                 "X-Upload-Content-Type": "video/mp4",
                 "X-Upload-Content-Length": str(size)},
    )
    if r.status_code != 200:
        _raise_for(r, "Upload init")
    session = r.headers["Location"]

    offset, failures = 0, 0
    with open(path, "rb") as fh:
        while offset < size:
            fh.seek(offset)
            chunk = fh.read(CHUNK)
            end = offset + len(chunk) - 1
            try:
                r = requests.put(session, data=chunk, timeout=300, headers={
                    "Authorization": f"Bearer {access_token()}",
                    "Content-Length": str(len(chunk)),
                    "Content-Range": f"bytes {offset}-{end}/{size}",
                })
            except requests.RequestException as exc:
                r = None
                err = exc
            if r is not None and r.status_code in (200, 201):
                return r.json()["id"]
            if r is not None and r.status_code == 308:
                rng = r.headers.get("Range")
                offset = int(rng.split("-")[1]) + 1 if rng else 0
                failures = 0
                continue
            if r is not None and r.status_code < 500:
                _raise_for(r, "Upload")
            failures += 1
            if failures > 5:
                raise RuntimeError(f"Upload kept failing: {r.status_code if r is not None else err}")
            time.sleep(2 ** failures)
            # Ask the server how much it already has, then resume from there.
            q = requests.put(session, timeout=60, headers={
                "Authorization": f"Bearer {access_token()}",
                "Content-Length": "0", "Content-Range": f"bytes */{size}"})
            if q.status_code in (200, 201):
                return q.json()["id"]
            rng = q.headers.get("Range")
            offset = int(rng.split("-")[1]) + 1 if rng else 0
    raise RuntimeError("Upload finished without a video id")
