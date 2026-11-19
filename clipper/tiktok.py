"""Post videos to TikTok with the official Content Posting API (Direct Post).

Needs TIKTOK_CLIENT_KEY, TIKTOK_CLIENT_SECRET and TIKTOK_REFRESH_TOKEN
(made once with tools/tiktok_auth.py).

Until TikTok approves the app ("audit"), the API only allows private
(SELF_ONLY) posts. post_video() tries public first and falls back to private,
so posts turn public by themselves once the app is approved.
"""
from __future__ import annotations

import math
import os
import time

import requests

API = "https://open.tiktokapis.com/v2"
CHUNK = 10 * 1024 * 1024  # TikTok allows 5–64 MB chunks


class TikTokError(RuntimeError):
    pass


def refresh_access_token() -> dict:
    r = requests.post(f"{API}/oauth/token/", timeout=30, data={
        "client_key": os.environ["TIKTOK_CLIENT_KEY"],
        "client_secret": os.environ["TIKTOK_CLIENT_SECRET"],
        "grant_type": "refresh_token",
        "refresh_token": os.environ["TIKTOK_REFRESH_TOKEN"],
    })
    data = r.json()
    if "access_token" not in data:
        raise TikTokError(f"token refresh failed: {data.get('error')} {data.get('error_description')}")
    return data


def _post(token: str, path: str, body: dict) -> dict:
    r = requests.post(f"{API}{path}", json=body, timeout=60, headers={
        "Authorization": f"Bearer {token}", "Content-Type": "application/json; charset=UTF-8"})
    data = r.json()
    err = data.get("error", {})
    if err.get("code") not in (None, "", "ok"):
        raise TikTokError(f"{err.get('code')}: {err.get('message')}")
    return data.get("data", {})


def creator_info(token: str) -> dict:
    return _post(token, "/post/publish/creator_info/query/", {})


def _init(token: str, caption: str, privacy: str, size: int) -> dict:
    chunk = size if size <= 64 * 1024 * 1024 else CHUNK
    count = max(1, size // chunk)
    return _post(token, "/post/publish/video/init/", {
        "post_info": {"title": caption[:2200], "privacy_level": privacy,
                      "disable_duet": False, "disable_comment": False, "disable_stitch": False},
        "source_info": {"source": "FILE_UPLOAD", "video_size": size,
                        "chunk_size": chunk, "total_chunk_count": count},
    }) | {"_chunk": chunk, "_count": count}


def _upload(url: str, path: str, size: int, chunk: int, count: int) -> None:
    with open(path, "rb") as fh:
        for i in range(count):
            start = i * chunk
            end = size - 1 if i == count - 1 else start + chunk - 1
            fh.seek(start)
            data = fh.read(end - start + 1)
            for attempt in range(3):
                try:
                    r = requests.put(url, data=data, timeout=300, headers={
                        "Content-Type": "video/mp4", "Content-Length": str(len(data)),
                        "Content-Range": f"bytes {start}-{end}/{size}"})
                    if r.status_code in (200, 201, 206):
                        break
                    raise TikTokError(f"upload chunk {i} got HTTP {r.status_code}: {r.text[:200]}")
                except (requests.RequestException, TikTokError):
                    if attempt == 2:
                        raise
                    time.sleep(5)


def wait_for(token: str, publish_id: str, minutes: int = 10) -> str:
    end = time.time() + minutes * 60
    status = "PROCESSING"
    while time.time() < end:
        d = _post(token, "/post/publish/status/fetch/", {"publish_id": publish_id})
        status = d.get("status", status)
        if status == "PUBLISH_COMPLETE":
            return status
        if status == "FAILED":
            raise TikTokError(f"TikTok rejected the post: {d.get('fail_reason')}")
        time.sleep(10)
    return status  # still processing; TikTok usually finishes on its own


def post_video(path: str, caption: str) -> dict:
    tok = refresh_access_token()
    token = tok["access_token"]
    info = creator_info(token)
    options = info.get("privacy_level_options") or ["SELF_ONLY"]
    want = os.environ.get("TIKTOK_PRIVACY", "PUBLIC_TO_EVERYONE")
    order = [p for p in (want, "SELF_ONLY") if p in options] or [options[0]]
    size = os.path.getsize(path)
    last = None
    for privacy in dict.fromkeys(order):
        try:
            init = _init(token, caption, privacy, size)
        except TikTokError as e:
            last = e
            if "unaudited" in str(e).lower() or "privacy" in str(e).lower():
                continue  # app not approved yet: post privately instead
            raise
        _upload(init["upload_url"], path, size, init["_chunk"], init["_count"])
        status = wait_for(token, init["publish_id"])
        return {"publish_id": init["publish_id"], "privacy": privacy, "status": status,
                "new_refresh_token": tok.get("refresh_token") != os.environ["TIKTOK_REFRESH_TOKEN"]}
    raise TikTokError(f"could not start the post: {last}")
