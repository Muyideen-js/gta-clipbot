"""Run once on your PC to get the YouTube refresh token for GitHub Actions.

    python tools/get_youtube_refresh_token.py CLIENT_ID CLIENT_SECRET

Use an OAuth client of type "Desktop app" (Google Cloud Console ->
APIs & Services -> Credentials). Your browser opens, you approve, and the
script prints the three values to save as repository secrets.

Standard library only; nothing to install.
"""
import hashlib
import base64
import http.server
import json
import secrets
import sys
import threading
import urllib.parse
import urllib.request
import webbrowser

SCOPE = "https://www.googleapis.com/auth/youtube.upload"


def main() -> None:
    if len(sys.argv) != 3:
        print(__doc__)
        sys.exit(1)
    client_id, client_secret = sys.argv[1], sys.argv[2]
    verifier = secrets.token_urlsafe(64)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    state = secrets.token_urlsafe(16)
    result = {}

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            if q.get("state", [""])[0] == state and "code" in q:
                result["code"] = q["code"][0]
                msg = "Done. You can close this tab and go back to the terminal."
            else:
                result["error"] = q.get("error", ["unknown"])[0]
                msg = f"Authorization failed: {result['error']}"
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.end_headers()
            self.wfile.write(msg.encode())

        def log_message(self, *a):
            pass

    server = http.server.HTTPServer(("127.0.0.1", 0), Handler)
    redirect = f"http://127.0.0.1:{server.server_port}"
    url = "https://accounts.google.com/o/oauth2/v2/auth?" + urllib.parse.urlencode({
        "client_id": client_id, "redirect_uri": redirect, "response_type": "code",
        "scope": SCOPE, "access_type": "offline", "prompt": "consent", "state": state,
        "code_challenge": challenge, "code_challenge_method": "S256",
    })
    print("Opening your browser. If it doesn't open, visit:\n" + url)
    webbrowser.open(url)
    t = threading.Thread(target=server.handle_request)
    t.start()
    t.join(timeout=300)
    server.server_close()
    if "code" not in result:
        sys.exit(f"No authorization code received ({result.get('error', 'timed out')}).")

    body = urllib.parse.urlencode({
        "code": result["code"], "client_id": client_id, "client_secret": client_secret,
        "redirect_uri": redirect, "grant_type": "authorization_code", "code_verifier": verifier,
    }).encode()
    with urllib.request.urlopen("https://oauth2.googleapis.com/token", body) as resp:
        tokens = json.load(resp)
    if "refresh_token" not in tokens:
        sys.exit(f"Google returned no refresh token: {tokens}")
    print("\nAdd these as repository secrets (Settings -> Secrets and variables -> Actions):\n")
    print(f"YT_CLIENT_ID      = {client_id}")
    print(f"YT_CLIENT_SECRET  = {client_secret}")
    print(f"YT_REFRESH_TOKEN  = {tokens['refresh_token']}")


if __name__ == "__main__":
    main()
