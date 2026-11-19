"""Connect a TikTok account once (makes TIKTOK_REFRESH_TOKEN).

    python tools/tiktok_auth.py url             # prints the sign-in link
    python tools/tiktok_auth.py code <CODE>     # swaps the code for tokens

Reads TIKTOK_CLIENT_KEY / TIKTOK_CLIENT_SECRET from the environment or
~/.clipbot/secrets.env. After sign-in, TikTok sends you to the callback page
(docs/tiktok.html), which shows the code to copy. The refresh token is saved
to ~/.clipbot/secrets.env and as the GitHub secret TIKTOK_REFRESH_TOKEN
(it is never printed).
"""
from __future__ import annotations

import os
import secrets
import subprocess
import sys
import urllib.parse

import requests

REDIRECT = os.environ.get("TIKTOK_REDIRECT_URI", "https://muyideen-js.github.io/gta-clipbot/tiktok.html")
ENV = os.path.join(os.path.expanduser("~"), ".clipbot", "secrets.env")
REPO = "Muyideen-js/gta-clipbot"


def read_env() -> dict:
    out = {}
    if os.path.exists(ENV):
        for line in open(ENV, encoding="utf-8"):
            if "=" in line and not line.lstrip().startswith("#"):
                k, v = line.split("=", 1)
                out[k.strip()] = v.strip()
    return {**out, **{k: v for k, v in os.environ.items() if k.startswith("TIKTOK_")}}


def set_env(key: str, value: str) -> None:
    lines = open(ENV, encoding="utf-8").read().splitlines() if os.path.exists(ENV) else []
    lines = [l for l in lines if not l.startswith(key + "=")] + [f"{key}={value}"]
    with open(ENV, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")


def main() -> None:
    env = read_env()
    key, secret = env.get("TIKTOK_CLIENT_KEY"), env.get("TIKTOK_CLIENT_SECRET")
    if not key or not secret:
        sys.exit("Set TIKTOK_CLIENT_KEY and TIKTOK_CLIENT_SECRET in ~/.clipbot/secrets.env first.")
    if len(sys.argv) >= 2 and sys.argv[1] == "url":
        print("https://www.tiktok.com/v2/auth/authorize/?" + urllib.parse.urlencode({
            "client_key": key, "scope": "user.info.basic,video.publish", "response_type": "code",
            "redirect_uri": REDIRECT, "state": secrets.token_urlsafe(12)}))
        return
    if len(sys.argv) >= 3 and sys.argv[1] == "code":
        code = urllib.parse.unquote(sys.argv[2].strip())
        r = requests.post("https://open.tiktokapis.com/v2/oauth/token/", timeout=30, data={
            "client_key": key, "client_secret": secret, "code": code,
            "grant_type": "authorization_code", "redirect_uri": REDIRECT})
        data = r.json()
        if "refresh_token" not in data:
            sys.exit(f"TikTok said: {data.get('error')} {data.get('error_description')}")
        set_env("TIKTOK_REFRESH_TOKEN", data["refresh_token"])
        for name, val in (("TIKTOK_CLIENT_KEY", key), ("TIKTOK_CLIENT_SECRET", secret),
                          ("TIKTOK_REFRESH_TOKEN", data["refresh_token"])):
            subprocess.run(["gh", "secret", "set", name, "-R", REPO, "--body", val], check=True)
        print(f"Connected. Scopes: {data.get('scope')}. Refresh token saved (valid about "
              f"{int(data.get('refresh_expires_in', 0)) // 86400} days).")
        return
    print(__doc__)


if __name__ == "__main__":
    main()
