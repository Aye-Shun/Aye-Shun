"""Builds spotify.svg, a card of the tracks I played most recently on Spotify.

.github/workflows/spotify.yml runs this every hour with the SPOTIFY_CLIENT_ID,
SPOTIFY_CLIENT_SECRET and SPOTIFY_REFRESH_TOKEN secrets (spotify_auth.py sets
them up) and publishes the card to the spotify-card branch. Without the
secrets it does nothing. Preview with saved data instead of calling Spotify:

    python3 .github/scripts/spotify.py --from-json sample.json

Needs Pillow, to shrink the album art that gets embedded in the SVG.
"""

import argparse
import base64
import io
import json
import os
import unicodedata
import urllib.parse
import urllib.request
from datetime import datetime
from xml.sax.saxutils import escape
from zoneinfo import ZoneInfo

from PIL import Image

OUT = os.environ.get("CARD_OUT", "spotify.svg")
TZ = ZoneInfo(os.environ.get("CARD_TIMEZONE", "America/New_York"))
RECENT = 4   # tracks listed under the last played one

# GitHub dark palette, same as the other cards, plus Spotify green
BG, BAR, BORDER = "#0d1117", "#010409", "#30363d"
TEXT, MUTED, SPOTIFY = "#e6edf3", "#8b949e", "#1ed760"
FONT = "ui-monospace,SFMono-Regular,'SF Mono',Menlo,Consolas,'Liberation Mono',monospace"


def get_json(url, data=None, headers=None):
    req = urllib.request.Request(url, data=data, headers={"User-Agent": "profile-spotify-card", **(headers or {})})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)


def art_uri(url, size):
    req = urllib.request.Request(url, headers={"User-Agent": "profile-spotify-card"})
    with urllib.request.urlopen(req, timeout=30) as r:
        im = Image.open(io.BytesIO(r.read())).convert("RGB")
    im.thumbnail((size, size), Image.LANCZOS)
    buf = io.BytesIO()
    im.save(buf, "JPEG", quality=85, optimize=True)
    return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()


def fetch(client_id, client_secret, refresh_token):
    auth = base64.b64encode(f"{client_id}:{client_secret}".encode()).decode()
    token = get_json("https://accounts.spotify.com/api/token",
                     data=urllib.parse.urlencode({"grant_type": "refresh_token", "refresh_token": refresh_token}).encode(),
                     headers={"Authorization": f"Basic {auth}", "Content-Type": "application/x-www-form-urlencoded"})
    items = get_json("https://api.spotify.com/v1/me/player/recently-played?limit=20",
                     headers={"Authorization": f"Bearer {token['access_token']}"})["items"]
    tracks = []
    for it in items:
        t = it["track"]
        if tracks and tracks[-1]["url"] == t["external_urls"].get("spotify"):
            continue   # the same song played twice in a row
        images = sorted(t["album"]["images"], key=lambda i: i.get("width") or 0)
        tracks.append({
            "name": t["name"],
            "artists": ", ".join(a["name"] for a in t["artists"]),
            "album": t["album"]["name"],
            "url": t["external_urls"].get("spotify"),
            "played_at": it["played_at"],
            "art_url": next((i["url"] for i in images if (i.get("width") or 0) >= 200), images[-1]["url"] if images else None),
            "small_url": images[0]["url"] if images else None,
        })
        if len(tracks) > RECENT:
            break
    for i, t in enumerate(tracks):   # big art for the last played track, thumbnails for the rest
        big, small = t.pop("art_url"), t.pop("small_url")
        src, size = (big, 224) if i == 0 else (small, 72)
        t["art"] = art_uri(src, size) if src else None
    return {"tracks": tracks}


def fit(text, max_px, char_px):
    """Cut text to roughly max_px wide in a monospace font, counting wide (CJK) characters as two."""
    out, used = "", 0
    for ch in text:
        w = char_px * (2 if unicodedata.east_asian_width(ch) in "WF" else 1)
        if used + w > max_px:
            return out.rstrip() + "…"
        out, used = out + ch, used + w
    return out


def when(played_at):
    t = datetime.fromisoformat(played_at.replace("Z", "+00:00")).astimezone(TZ)
    return f"{t:%b} {t.day}, {t.hour % 12 or 12}:{t:%M} {'AM' if t.hour < 12 else 'PM'}"


def render(s):
    W, bar, pad, art = 560, 36, 24, 112
    tracks = s["tracks"]
    ix = pad + art + 20
    top_y = bar + 22
    list_y = top_y + art + 46
    row_h = 40
    rest = tracks[1:]
    prompt_y = (list_y + len(rest) * row_h + 14) if rest else (top_y + art + 30) if tracks else (top_y + 56)
    H = prompt_y + 22
    first = tracks[0] if tracks else None
    summary = (f"Last played on Spotify: {first['name']} by {first['artists']}" if first else "No recently played tracks")
    out = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" viewBox="0 0 {W} {H}" '
        f'role="img" aria-label="{escape(summary)}">',
        f"<title>{escape(summary)}</title>",
        "<style>"
        f"text{{font-family:{FONT};font-size:14px;fill:{TEXT}}}"
        f".k{{fill:#58a6ff;font-weight:700}}.m{{fill:{MUTED}}}.g{{fill:#3fb950;font-weight:700}}.c{{fill:#39c5cf}}"
        f".t{{font-size:12px;fill:{MUTED}}}.h{{font-size:17px;font-weight:700}}"
        ".r{animation:in .45s cubic-bezier(.2,.8,.2,1) both}"
        "@keyframes in{from{opacity:0;transform:translateX(-8px)}to{opacity:1;transform:none}}"
        ".eq{transform-box:fill-box;transform-origin:bottom;animation:eq 1s ease-in-out infinite alternate}"
        "@keyframes eq{from{transform:scaleY(.25)}to{transform:none}}"
        ".cur{animation:blink 1s steps(1) infinite}@keyframes blink{50%{fill-opacity:0}}"
        "@media (prefers-reduced-motion:reduce){*{animation:none!important}}"
        "</style>",
        f'<rect x=".5" y=".5" width="{W - 1}" height="{H - 1}" rx="12" fill="{BG}" stroke="{BORDER}"/>',
        f'<path d="M.5 {bar}V12.5a12 12 0 0 1 12-12h{W - 25}a12 12 0 0 1 12 12V{bar}z" fill="{BAR}"/>',
        f'<line x1="0" y1="{bar}" x2="{W}" y2="{bar}" stroke="{BORDER}"/>',
        *(f'<circle cx="{20 + i * 20}" cy="{bar / 2}" r="6" fill="{c}"/>' for i, c in enumerate(["#ff5f57", "#febc2e", "#28c840"])),
        f'<text class="t" x="{W / 2}" y="{bar / 2 + 4}" text-anchor="middle">kenneth@aye-shun: ~ — spotify</text>',
    ]
    if first:
        if first["art"]:
            out.append(f'<defs><clipPath id="art"><rect x="{pad}" y="{top_y}" width="{art}" height="{art}" rx="10"/></clipPath></defs>'
                       f'<image class="r" href="{first["art"]}" x="{pad}" y="{top_y}" width="{art}" height="{art}" clip-path="url(#art)"/>')
        out += [
            f'<text class="r" x="{ix}" y="{top_y + 16}"><tspan class="k">Last played</tspan>'
            f'<tspan class="m"> · {when(first["played_at"])}</tspan></text>',
            f'<text class="r h" style="animation-delay:.07s" x="{ix}" y="{top_y + 46}">{escape(fit(first["name"], W - pad - ix, 10.2))}</text>',
            f'<text class="r" style="animation-delay:.14s" x="{ix}" y="{top_y + 70}">{escape(fit(first["artists"], W - pad - ix, 8.4))}</text>',
            f'<text class="r t" style="animation-delay:.21s" x="{ix}" y="{top_y + 90}">{escape(fit(first["album"], W - pad - ix, 7.2))}</text>',
        ]
        # a little equalizer under the title
        eq = "".join(f'<rect class="eq" style="animation-delay:-{d}s;animation-duration:{dur}s" x="{ix + i * 7}" '
                     f'y="{top_y + art - 16}" width="4" height="16" rx="1" fill="{SPOTIFY}"/>'
                     for i, (d, dur) in enumerate([(0.0, 0.9), (0.4, 0.7), (0.2, 1.1), (0.6, 0.8)]))
        out.append(f'<g class="r" style="animation-delay:.28s">{eq}</g>')
    else:
        out.append(f'<text class="r m" x="{pad}" y="{top_y + 20}">Nothing played recently</text>')

    if rest:
        out.append(f'<line x1="{pad}" y1="{list_y - 30}" x2="{W - pad}" y2="{list_y - 30}" stroke="{BORDER}" stroke-dasharray="4 4"/>')
        for i, t in enumerate(rest):
            y = list_y + i * row_h
            delay = f'style="animation-delay:{0.35 + i * 0.08:.2f}s"'
            thumb = (f'<clipPath id="a{i}"><rect x="{pad}" y="{y - 18}" width="32" height="32" rx="5"/></clipPath>'
                     f'<image href="{t["art"]}" x="{pad}" y="{y - 18}" width="32" height="32" clip-path="url(#a{i})"/>'
                     if t["art"] else "")
            label = fit(f'{t["name"]} — {t["artists"]}', W - 2 * pad - 44 - 130, 8.4)
            out.append(f'<g class="r" {delay}>{thumb}'
                       f'<text x="{pad + 44}" y="{y + 3}">{escape(label)}</text>'
                       f'<text class="t" x="{W - pad}" y="{y + 3}" text-anchor="end">{when(t["played_at"])}</text></g>')

    out += [
        f'<text class="r" x="{pad}" y="{prompt_y}"><tspan class="g">→</tspan> <tspan class="c">~</tspan> <tspan class="cur">█</tspan></text>',
        f'<text class="r t" x="{W - pad}" y="{prompt_y}" text-anchor="end">via <tspan fill="{SPOTIFY}">Spotify</tspan></text>',
        "</svg>",
    ]
    return "\n".join(out) + "\n"


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--from-json", help="render data saved in this file instead of calling Spotify")
    args = ap.parse_args()
    if args.from_json:
        with open(args.from_json) as f:
            data = json.load(f)
    else:
        creds = [os.environ.get(k) for k in ("SPOTIFY_CLIENT_ID", "SPOTIFY_CLIENT_SECRET", "SPOTIFY_REFRESH_TOKEN")]
        if not all(creds):
            print("::notice::Spotify isn't connected yet (run spotify_auth.py), so there's no card to build")
            return
        data = fetch(*creds)
    os.makedirs(os.path.dirname(os.path.abspath(OUT)), exist_ok=True)
    with open(OUT, "w") as f:
        f.write(render(data))
    print(f"wrote {OUT}")


if __name__ == "__main__":
    main()
