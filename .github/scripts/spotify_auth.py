"""One-time Spotify login for the profile's Spotify card. Run it on your own computer:

    python3 .github/scripts/spotify_auth.py

Before running it, create an app at https://developer.spotify.com/dashboard
with the redirect URI http://127.0.0.1:8765/callback and the Web API ticked.

It asks for the app's client ID and secret (the secret isn't shown as you
type), opens Spotify's consent page, catches the redirect on 127.0.0.1 and
saves the three values as GitHub Actions secrets with the gh CLI. The secret
and the token are never printed or written to disk.
"""

import base64
import getpass
import http.server
import json
import os
import secrets
import subprocess
import urllib.parse
import urllib.request
import webbrowser

REPO = os.environ.get("CARD_REPO", "Aye-Shun/Aye-Shun")
PORT = 8765
REDIRECT = f"http://127.0.0.1:{PORT}/callback"
SCOPE = "user-read-recently-played"


def wait_for_code(state):
    """Serve one request on 127.0.0.1 and return the ?code= Spotify redirects back with."""
    result = {}

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            query = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            ok = query.get("state", [""])[0] == state and "code" in query
            result["code"] = query["code"][0] if ok else None
            result["error"] = query.get("error", [""])[0]
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            msg = "Spotify is connected. You can close this tab." if ok else "Something went wrong. Check the terminal."
            self.wfile.write(f"<p style='font:16px sans-serif'>{msg}</p>".encode())

        def log_message(self, *args):
            pass

    with http.server.HTTPServer(("127.0.0.1", PORT), Handler) as server:
        while "code" not in result:
            server.handle_request()
    if not result["code"]:
        raise SystemExit(f"Spotify didn't send a login code ({result['error'] or 'state mismatch'}). Try again.")
    return result["code"]


def main():
    client_id = input("Spotify app client ID: ").strip()
    client_secret = getpass.getpass("Spotify app client secret (hidden): ").strip()
    if not client_id or not client_secret:
        raise SystemExit("Both the client ID and the client secret are needed.")

    state = secrets.token_urlsafe(16)
    url = "https://accounts.spotify.com/authorize?" + urllib.parse.urlencode({
        "client_id": client_id, "response_type": "code", "redirect_uri": REDIRECT, "scope": SCOPE, "state": state,
    })
    print("\nOpening Spotify in your browser. Log in and press Agree.")
    print(f"If nothing opens, visit:\n{url}\n")
    webbrowser.open(url)
    code = wait_for_code(state)

    auth = base64.b64encode(f"{client_id}:{client_secret}".encode()).decode()
    req = urllib.request.Request(
        "https://accounts.spotify.com/api/token",
        data=urllib.parse.urlencode({"grant_type": "authorization_code", "code": code, "redirect_uri": REDIRECT}).encode(),
        headers={"Authorization": f"Basic {auth}", "Content-Type": "application/x-www-form-urlencoded"},
    )
    with urllib.request.urlopen(req, timeout=30) as r:
        tokens = json.load(r)
    req = urllib.request.Request("https://api.spotify.com/v1/me", headers={"Authorization": f"Bearer {tokens['access_token']}"})
    with urllib.request.urlopen(req, timeout=30) as r:
        me = json.load(r)

    for name, value in [("SPOTIFY_CLIENT_ID", client_id), ("SPOTIFY_CLIENT_SECRET", client_secret),
                        ("SPOTIFY_REFRESH_TOKEN", tokens["refresh_token"])]:
        subprocess.run(["gh", "secret", "set", name, "-R", REPO], input=value, text=True, check=True, capture_output=True)
    print(f"Connected as {me.get('display_name') or me.get('id')}.")
    print(f"Saved SPOTIFY_CLIENT_ID, SPOTIFY_CLIENT_SECRET and SPOTIFY_REFRESH_TOKEN to {REPO}.")
    print(f"Spotify profile: {me.get('external_urls', {}).get('spotify', '')}")


if __name__ == "__main__":
    main()
