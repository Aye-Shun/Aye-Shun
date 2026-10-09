"""Builds league.svg, a League of Legends rank and last-session card for the profile README.

The stats come from deeplol.gg's public read-only endpoints (no API key).
deeplol only re-checks a player's games when someone presses Update on their
deeplol page, so the card shows whatever deeplol last saved, with that date.

.github/workflows/league.yml runs this every few hours and commits league.svg
whenever it changes. To preview the card without fetching, render saved data:

    python3 .github/scripts/league.py --from-json sample.json

Needs Pillow, to crop the rank emblem and shrink champion icons.
"""

import argparse
import base64
import io
import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime
from xml.sax.saxutils import escape
from zoneinfo import ZoneInfo

from PIL import Image

RIOT_ID = os.environ.get("RIOT_ID", "")             # "Name#TAG"
PLATFORM = os.environ.get("LOL_PLATFORM", "na1").lower()
QUEUE = int(os.environ.get("LOL_QUEUE", "420"))     # 420 = Ranked Solo/Duo, 440 = Ranked Flex
TZ = ZoneInfo(os.environ.get("LOL_TIMEZONE", "America/New_York"))
SESSION_GAP_MIN = 120    # a longer break than this between games ends a session (matches deeplol)
MATCH_LOOKBACK = 40      # most recent games (any queue) to look through for the latest session
SHOWN_GAMES = 24         # champion icons on the card (3 rows); the W/L count covers the whole session
PER_ROW = 8
OUT = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "league.svg"))

QUEUES = {420: ("ranked_solo_5x5", "Ranked Solo/Duo"), 440: ("ranked_flex_sr", "Ranked Flex")}
DIVISIONS = {1: "I", 2: "II", 3: "III", 4: "IV"}
DEEPLOL = "https://b2c-api-cdn.deeplol.gg"
USER_AGENT = "profile-league-card (+https://github.com/Aye-Shun/Aye-Shun)"
DDRAGON = "https://ddragon.leagueoflegends.com"
CDRAGON = "https://raw.communitydragon.org/latest/plugins/rcp-fe-lol-static-assets/global/default/images"

# GitHub dark palette, same as the other cards
BG, BAR, BORDER = "#0d1117", "#010409", "#30363d"
TEXT, MUTED, GREEN, RED = "#e6edf3", "#8b949e", "#3fb950", "#ff7b72"
FONT = "ui-monospace,SFMono-Regular,'SF Mono',Menlo,Consolas,'Liberation Mono',monospace"
TIER_COLORS = {
    "IRON": "#a19d94", "BRONZE": "#c58a5c", "SILVER": "#a8b5c0", "GOLD": "#e8b64c",
    "PLATINUM": "#4fc1b0", "EMERALD": "#34c77b", "DIAMOND": "#7d9cff", "MASTER": "#c77dff",
    "GRANDMASTER": "#ff6b6b", "CHALLENGER": "#f5d77a",
}
APEX = {"MASTER", "GRANDMASTER", "CHALLENGER"}   # no divisions above Diamond


def download(url):
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.read()


class DeeplolDown(Exception):
    """deeplol answered with errors or obviously incomplete data."""


def deeplol(path, **params):
    """GET one of deeplol's read-only endpoints, retrying a couple of times on errors."""
    url = f"{DEEPLOL}{path}?{urllib.parse.urlencode(params)}"
    for attempt in range(3):
        try:
            data = json.loads(download(url))
            if not isinstance(data, dict):
                raise ValueError(f"expected a JSON object, got {type(data).__name__}")
            return data
        except (urllib.error.URLError, TimeoutError, ValueError) as e:
            if attempt == 2:
                raise DeeplolDown(f"{path}: {e}") from e
            time.sleep(5 * (attempt + 1))


def data_uri(im, size, fmt):
    """Shrink an image to fit size x size and return it as a data URI.

    Everything is embedded in the SVG, so keep it small: JPEG for the square
    champion art, a 256-colour PNG for the emblem (it needs transparency).
    """
    im = im.copy()
    im.thumbnail((size, size), Image.LANCZOS)
    buf = io.BytesIO()
    if fmt == "jpeg":
        im.convert("RGB").save(buf, "JPEG", quality=85, optimize=True)
    else:
        im.quantize(256, method=Image.Quantize.FASTOCTREE).save(buf, "PNG", optimize=True)
    return f"data:image/{fmt};base64," + base64.b64encode(buf.getvalue()).decode()


def emblem_uri(tier):
    """The tier's ranked emblem, cropped out of its big transparent canvas to a square."""
    im = Image.open(io.BytesIO(download(f"{CDRAGON}/ranked-emblem/emblem-{tier.lower()}.png"))).convert("RGBA")
    # ignore the faint outer glow when finding the emblem's edges
    im = im.crop(im.getchannel("A").point(lambda a: 255 if a > 40 else 0).getbbox())
    side = max(im.size)
    square = Image.new("RGBA", (side, side))
    square.paste(im, ((side - im.width) // 2, (side - im.height) // 2))
    return data_uri(square, 192, "png")


def champion_icons():
    """championId -> (champion name, function returning its icon as a data URI)."""
    version = json.loads(download(f"{DDRAGON}/api/versions.json"))[0]
    data = json.loads(download(f"{DDRAGON}/cdn/{version}/data/en_US/champion.json"))["data"]

    def icon(file):
        im = Image.open(io.BytesIO(download(f"{DDRAGON}/cdn/{version}/img/champion/{file}"))).convert("RGB")
        return data_uri(im, 88, "jpeg")

    return {int(c["key"]): (c["name"], lambda f=c["image"]["full"]: icon(f)) for c in data.values()}


def session_games(puuid, platform, champs):
    """The player's latest run of games in QUEUE, newest first.

    deeplol's match list mixes every queue (its queue filter is ignored), so
    each match is opened to check its queue; match details are CDN-cached.
    """
    games, newer_start = [], None
    for offset in range(0, MATCH_LOOKBACK, 20):
        page = deeplol("/match/matches", puu_id=puuid, platform_id=platform, offset=offset, count=20,
                       queue_type="ALL", champion_id=0, only_list=1, last_updated_at=0)["match_id_list"]
        for m in page:
            match = deeplol("/match/match-cached", match_id=m["match_id"], platform_id=platform)
            info = match["match_basic_dict"]
            if info["queue_id"] != QUEUE:
                continue
            start = info["creation_timestamp"]
            if newer_start is not None and newer_start - (start + info["game_duration"]) > SESSION_GAP_MIN * 60:
                return games
            me = next(p for p in match["participants_list"] if p["puu_id"] == puuid)
            stats = me["final_stat_dict"]
            name, icon = champs.get(me["champion_id"], (f"Champion {me['champion_id']}", None))
            games.append({
                "champion": name,
                "result": "remake" if info["is_remake"] else "win" if me["is_win"] else "loss",
                "kda": f"{stats['kills']}/{stats['deaths']}/{stats['assists']}",
                "start": int(start * 1000),
                "duration": info["game_duration"],
                "icon": icon,
            })
            newer_start = start
        if len(page) < 20:
            break
    return games


def fetch():
    if "#" not in RIOT_ID:
        raise SystemExit('Set RIOT_ID to your Riot ID, like "Name#NA1"')
    name, tag = RIOT_ID.rsplit("#", 1)
    platform = PLATFORM.upper()
    who = deeplol("/summoner/summoner", riot_id_name=name, riot_id_tag_line=tag, platform_id=platform)
    who = who.get("summoner_basic_info_dict") or {}
    puuid = who.get("puu_id")
    if not puuid:
        raise DeeplolDown(f"no player {RIOT_ID} on {platform} (check the Riot ID, or deeplol is down)")

    queue_key, queue_name = QUEUES[QUEUE]
    entry = deeplol("/summoner/summoner-realtime", platform_id=platform, summoner_id="", puu_id=puuid)
    if "season_tier_info_dict" not in entry:
        raise DeeplolDown(f"no rank data: {str(entry)[:120]}")
    entry = entry["season_tier_info_dict"].get(queue_key) or {}
    tier = entry.get("tier") or None
    updated = deeplol("/summoner/updated-time", puu_id=puuid, platform_id=platform).get("updated_timestamp")
    if not updated:
        raise DeeplolDown("no last-updated time")

    # Matches only change when deeplol re-checks the player, so skip the
    # heavy match downloads if neither that nor the rank moved since last time.
    state = {"updated": updated, **{k: entry.get(k) for k in ("tier", "division", "league_points", "wins", "losses")}}
    if previous_state() == state and os.environ.get("LEAGUE_FORCE") != "true":
        return None

    games = session_games(puuid, platform, champion_icons())
    if not games and entry.get("wins", 0) + entry.get("losses", 0) > 0:
        # ranked games on record but none in the match list: deeplol is mid-hiccup
        raise DeeplolDown("empty match list for a player with ranked games")
    for i, g in enumerate(games):   # newest first, like deeplol's list
        g["icon"] = g["icon"]() if g["icon"] and i < SHOWN_GAMES else None

    return {
        "riot_id": f"{who['riot_id_name']}#{who['riot_id_tag_line']}",
        "platform": PLATFORM,
        "queue": queue_name,
        "tier": tier,
        "division": DIVISIONS.get(entry.get("division")),
        "lp": entry.get("league_points", 0),
        "wins": entry.get("wins", 0),
        "losses": entry.get("losses", 0),
        "emblem": emblem_uri(tier) if tier else None,
        "games": games,
        "updated": updated,
        "state": state,
    }


def previous_state():
    """The deeplol state the current league.svg was built from, if any."""
    try:
        with open(OUT) as f:
            m = re.search(r"<!-- card-state (\{.*?\}) -->", f.read(4096))
        return json.loads(m.group(1)) if m else None
    except (OSError, ValueError):
        return None


def server_name(platform):
    names = {"eun1": "EUNE", "la1": "LAN", "la2": "LAS", "oc1": "OCE", "sg2": "SG", "tw2": "TW", "vn2": "VN"}
    return names.get(platform, platform.rstrip("0123456789").upper())


def render(s):
    tier = s["tier"]
    color = TIER_COLORS.get(tier, MUTED)
    if tier:
        rank = tier.title() + ("" if tier in APEX else f" {s['division']}")
        rank_text = f"{rank} · {s['lp']} LP"
    else:
        rank_text = "Unranked"
    played = s["wins"] + s["losses"]
    winrate = s["wins"] / played if played else 0
    games = s["games"]
    wins = sum(g["result"] == "win" for g in games)
    losses = sum(g["result"] == "loss" for g in games)
    if games:   # newest first
        first, last = (datetime.fromtimestamp(g["start"] / 1000, TZ) for g in (games[-1], games[0]))
        if first.date() == last.date():
            days = f"{last:%b} {last.day}"
        elif first.month == last.month:
            days = f"{first:%b} {first.day}–{last.day}"
        else:
            days = f"{first:%b} {first.day}–{last:%b} {last.day}"
        played_min = sum(g.get("duration", 0) for g in games) // 60
        session_when = days + (f" · {played_min // 60}h {played_min % 60}m" if played_min else "")
    name, tag = s["riot_id"].rsplit("#", 1)
    summary = (f"League of Legends, {s['riot_id']} ({server_name(s['platform'])}): {rank_text}, "
               f"{s['wins']} wins {s['losses']} losses this season ({winrate:.0%}), "
               + (f"last session {wins} wins {losses} losses" if games else "no recent ranked games"))

    W, bar, pad, emb = 560, 36, 24, 128
    ix = pad + emb + 24
    header_y = bar + 38
    rows_y = header_y + 46
    lh = 22
    meter_y = rows_y + lh + 16
    top_bottom = meter_y + 10
    emb_y = (header_y - 14 + top_bottom) / 2 - emb / 2
    sep_y = max(top_bottom, emb_y + emb) + 20
    sess_y = sep_y + 30
    icon, row_h = 44, 76
    step = (W - 2 * pad - icon) / (PER_ROW - 1)   # spread each row across the card
    icons_y = sess_y + 16
    shown = games[:SHOWN_GAMES]
    rows = -(-len(shown) // PER_ROW)
    more_y = icons_y + rows * row_h - 4
    prompt_y = (more_y + (26 if len(games) > len(shown) else 0) + 22) if shown else (sess_y + 36)
    H = prompt_y + 22
    out = []
    t = 0.15

    def tick(step=0.07):
        nonlocal t
        t += step
        return f'style="animation-delay:{t - step:.2f}s"'

    out += [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" viewBox="0 0 {W} {H}" '
        f'role="img" aria-label="{escape(summary)}">',
        f"<title>{escape(summary)}</title>",
        *([f"<!-- card-state {json.dumps(s['state'], separators=(',', ':'))} -->"] if s.get("state") else []),
        "<style>"
        f"text{{font-family:{FONT};font-size:14px;fill:{TEXT}}}"
        f".k{{fill:#58a6ff;font-weight:700}}.m{{fill:{MUTED}}}.g{{fill:{GREEN};font-weight:700}}"
        f".l{{fill:{RED};font-weight:700}}.c{{fill:#39c5cf}}.t{{font-size:12px;fill:{MUTED}}}.s{{font-size:11px}}"
        ".r{animation:in .45s cubic-bezier(.2,.8,.2,1) both}"
        "@keyframes in{from{opacity:0;transform:translateX(-8px)}to{opacity:1;transform:none}}"
        ".p{animation:pop .4s cubic-bezier(.2,.8,.2,1) both}"
        "@keyframes pop{from{opacity:0;transform:translateY(8px)}to{opacity:1;transform:none}}"
        ".grow{transform-box:fill-box;transform-origin:left;animation:grow .9s cubic-bezier(.2,.8,.2,1) both}"
        "@keyframes grow{from{transform:scaleX(0)}to{transform:none}}"
        ".cur{animation:blink 1s steps(1) infinite}@keyframes blink{50%{fill-opacity:0}}"
        "@media (prefers-reduced-motion:reduce){*{animation:none!important}}"
        "</style>",
        f'<rect x=".5" y=".5" width="{W - 1}" height="{H - 1}" rx="12" fill="{BG}" stroke="{BORDER}"/>',
        f'<path d="M.5 {bar}V12.5a12 12 0 0 1 12-12h{W - 25}a12 12 0 0 1 12 12V{bar}z" fill="{BAR}"/>',
        f'<line x1="0" y1="{bar}" x2="{W}" y2="{bar}" stroke="{BORDER}"/>',
        *(f'<circle cx="{20 + i * 20}" cy="{bar / 2}" r="6" fill="{c}"/>' for i, c in enumerate(["#ff5f57", "#febc2e", "#28c840"])),
        f'<text class="t" x="{W / 2}" y="{bar / 2 + 4}" text-anchor="middle">kenneth@aye-shun: ~ — league</text>',
    ]

    # rank emblem
    if s["emblem"]:
        out.append(f'<image class="r" href="{s["emblem"]}" x="{pad}" y="{emb_y:.1f}" width="{emb}" height="{emb}"/>')
    else:
        out.append(f'<g class="r"><circle cx="{pad + emb / 2}" cy="{emb_y + emb / 2:.1f}" r="{emb / 2 - 8}" fill="none" '
                   f'stroke="{BORDER}" stroke-width="2" stroke-dasharray="6 6"/>'
                   f'<text class="m" x="{pad + emb / 2}" y="{emb_y + emb / 2 + 5:.1f}" text-anchor="middle">?</text></g>')

    # Riot ID header, rank and season record
    out.append(f'<text class="r" {tick()} x="{ix}" y="{header_y}"><tspan class="g">{escape(name)}</tspan>'
               f'<tspan class="m">#{escape(tag)}</tspan> <tspan class="k">{server_name(s["platform"])}</tspan></text>')
    out.append(f'<text class="r t" {tick(0)} x="{W - pad}" y="{header_y}" text-anchor="end">{escape(s["queue"])}</text>')
    out.append(f'<text class="r m" {tick(0)} x="{ix}" y="{header_y + 20}">{"-" * (len(s["riot_id"]) + len(server_name(s["platform"])) + 1)}</text>')
    out.append(f'<text class="r" {tick()} x="{ix}" y="{rows_y}"><tspan class="k">Rank</tspan>: '
               f'<tspan fill="{color}" font-weight="700">{escape(rank_text)}</tspan></text>')
    if played:
        out.append(f'<text class="r" {tick()} x="{ix}" y="{rows_y + lh}"><tspan class="k">Season</tspan>: '
                   f'<tspan class="g">{s["wins"]}W</tspan> <tspan class="l">{s["losses"]}L</tspan>'
                   f'<tspan class="m"> · </tspan>{winrate:.1%} winrate</text>')
        mw = W - pad - ix
        out.append(f'<g class="r" {tick()}><rect x="{ix}" y="{meter_y}" width="{mw}" height="8" rx="4" fill="{RED}" fill-opacity=".85"/>'
                   f'<rect class="grow" style="animation-delay:{t:.2f}s" x="{ix}" y="{meter_y}" width="{max(winrate * mw, 0.01):.1f}" height="8" rx="4" fill="{GREEN}"/></g>')
    else:
        out.append(f'<text class="r m" {tick()} x="{ix}" y="{rows_y + lh}">No ranked games this season yet</text>')

    # last session
    out.append(f'<line x1="{pad}" y1="{sep_y}" x2="{W - pad}" y2="{sep_y}" stroke="{BORDER}" stroke-dasharray="4 4"/>')
    if games:
        record = f'<tspan class="g">{wins}W</tspan> <tspan class="l">{losses}L</tspan>'
        count = f"{len(games)} game{'s' if len(games) != 1 else ''}"
        out.append(f'<text class="r" {tick()} x="{pad}" y="{sess_y}"><tspan class="k">Last session</tspan>: '
                   f'{record}<tspan class="m"> · {count}</tspan></text>')
        out.append(f'<text class="r t" {tick(0)} x="{W - pad}" y="{sess_y}" text-anchor="end">{escape(session_when)}</text>')
        for i, g in enumerate(shown):
            x = round(pad + (i % PER_ROW) * step, 1)
            y = icons_y + (i // PER_ROW) * row_h
            ring = {"win": GREEN, "loss": RED}.get(g["result"], MUTED)
            letter = {"win": "W", "loss": "L"}.get(g["result"], "R")
            body = (f'<clipPath id="ic{i}"><rect x="{x}" y="{y}" width="{icon}" height="{icon}" rx="8"/></clipPath>'
                    + (f'<image href="{g["icon"]}" x="{x}" y="{y}" width="{icon}" height="{icon}" clip-path="url(#ic{i})"'
                       + (' opacity=".45"' if g["result"] == "remake" else "") + "/>"
                       if g["icon"] else
                       f'<rect x="{x}" y="{y}" width="{icon}" height="{icon}" rx="8" fill="{BAR}"/>'
                       f'<text class="s m" x="{x + icon / 2}" y="{y + icon / 2 + 4}" text-anchor="middle">{escape(g["champion"][:3])}</text>')
                    + f'<rect x="{x - 1}" y="{y - 1}" width="{icon + 2}" height="{icon + 2}" rx="9" fill="none" stroke="{ring}" stroke-width="2"/>'
                    # W/L badge on the corner, so the result doesn't rely on colour alone
                    + f'<rect x="{x + icon - 13}" y="{y + icon - 13}" width="16" height="16" rx="4" fill="{ring}"/>'
                    + f'<text class="s" x="{x + icon - 5}" y="{y + icon - 1}" text-anchor="middle" fill="{BG}" '
                      f'style="fill:{BG};font-weight:700">{letter}</text>'
                    + f'<text class="s m" x="{x + icon / 2}" y="{y + icon + 18}" text-anchor="middle">{g["kda"]}</text>')
            out.append(f'<g class="p" {tick(0.05)}><title>{escape(g["champion"])}: {g["result"]} {g["kda"]}</title>{body}</g>')
        if len(games) > len(shown):
            out.append(f'<text class="r m s" {tick()} x="{pad}" y="{more_y + 18}">'
                       f'+{len(games) - len(shown)} earlier games not shown</text>')
    else:
        out.append(f'<text class="r" {tick()} x="{pad}" y="{sess_y}"><tspan class="k">Last session</tspan>: '
                   f'<tspan class="m">no {escape(s["queue"])} games found</tspan></text>')

    out.append(f'<text class="r" {tick()} x="{pad}" y="{prompt_y}">'
               f'<tspan class="g">→</tspan> <tspan class="c">~</tspan> <tspan class="cur">█</tspan></text>')
    source = "via deeplol.gg"
    if s.get("updated"):
        when = datetime.fromtimestamp(s["updated"], TZ)
        source += f" · updated {when:%b} {when.day}"
    out.append(f'<text class="r t" {tick(0)} x="{W - pad}" y="{prompt_y}" text-anchor="end">{source}</text>')
    out.append("</svg>")
    return "\n".join(out) + "\n"


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--from-json", help="render data saved in this file instead of fetching it")
    args = ap.parse_args()
    if args.from_json:
        with open(args.from_json) as f:
            stats = json.load(f)
    else:
        try:
            stats = fetch()
        except DeeplolDown as e:
            # an outage shouldn't fail the run or replace a good card with an empty one;
            # the card's "updated" date shows how old it is
            print(f"::warning::deeplol is having problems ({e}); keeping the current league.svg")
            return
        if stats is None:
            print("deeplol has nothing new since the last card; keeping league.svg")
            return
    with open(OUT, "w") as f:
        f.write(render(stats))
    print(f"wrote {OUT}")


if __name__ == "__main__":
    main()
