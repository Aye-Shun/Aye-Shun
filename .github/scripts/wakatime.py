"""Builds wakatime.svg, a card of this week's coding time by language, from WakaTime.

.github/workflows/wakatime.yml runs this daily with the WAKATIME_API_KEY secret
and publishes the card to the wakatime-card branch. Without the secret it does
nothing, so the workflow can exist before WakaTime is connected. Preview:

    python3 .github/scripts/wakatime.py --from-json sample.json
"""

import argparse
import base64
import json
import os
import urllib.request
from xml.sax.saxutils import escape

OUT = os.environ.get("CARD_OUT", "wakatime.svg")
API = "https://wakatime.com/api/v1/users/current/summaries?range=Last%207%20Days"
SHOWN = 6

# GitHub dark palette, same as the other cards
BG, BAR, BORDER, TRACK = "#0d1117", "#010409", "#30363d", "#21262d"
TEXT, MUTED = "#e6edf3", "#8b949e"
FONT = "ui-monospace,SFMono-Regular,'SF Mono',Menlo,Consolas,'Liberation Mono',monospace"
# GitHub's language colours, lightened where they'd vanish on the dark card
LANG_COLORS = {
    "Python": "#3572A5", "Java": "#b07219", "JavaScript": "#f1e05a", "TypeScript": "#3178c6",
    "TSX": "#3178c6", "JSX": "#f1e05a", "Lua": "#6a7fdb", "Luau": "#00a2ff", "C": "#a8b9cc",
    "C++": "#f34b7d", "C#": "#178600", "SQL": "#e38c00", "R": "#198ce7", "HTML": "#e34c26",
    "CSS": "#8a63d2", "Markdown": "#4a8bd8", "JSON": "#cbcb41", "YAML": "#cb171e", "TOML": "#9c4221",
    "Bash": "#89e051", "Shell": "#89e051", "Assembly": "#b08a4a", "Go": "#00add8", "Rust": "#dea584",
    "Jupyter": "#da5b0b", "Text": "#8b949e",
}
FALLBACK = ["#58a6ff", "#3fb950", "#d2a8ff", "#ffa657", "#ff7b72", "#39c5cf"]


def human(seconds):
    """WakaTime-style durations: '5 hrs 59 mins', '47 mins', '30 secs'."""
    seconds = int(seconds)
    if seconds < 60:
        return f"{seconds} secs"
    hours, mins = divmod(seconds // 60, 60)
    parts = [f"{hours} hr{'s' if hours != 1 else ''}"] if hours else []
    if mins or not hours:
        parts.append(f"{mins} min{'s' if mins != 1 else ''}")
    return " ".join(parts)


def fetch(key):
    # Daily summaries are up to date within minutes; the /stats endpoint is
    # recalculated in the background and can lag by hours (days for new accounts).
    req = urllib.request.Request(API, headers={
        "Authorization": "Basic " + base64.b64encode(key.encode()).decode(),
        "User-Agent": "profile-wakatime-card",
    })
    with urllib.request.urlopen(req, timeout=30) as r:
        days = json.load(r)["data"]
    total = sum(d["grand_total"]["total_seconds"] for d in days)
    langs, editors = {}, {}
    for d in days:
        for l in d.get("languages") or []:
            langs[l["name"]] = langs.get(l["name"], 0) + l["total_seconds"]
        for e in d.get("editors") or []:
            editors[e["name"]] = editors.get(e["name"], 0) + e["total_seconds"]
    top = sorted(((n, s) for n, s in langs.items() if s > 0), key=lambda x: -x[1])[:SHOWN]
    active_days = sum(1 for d in days if d["grand_total"]["total_seconds"] > 0)
    return {
        "total": human(total),
        "seconds": total,
        "daily": human(total / active_days) if active_days else "",
        "editors": [n for n, _ in sorted(editors.items(), key=lambda x: -x[1])[:3]],
        "languages": [{"name": n, "percent": 100 * s / total, "text": human(s)} for n, s in top],
    }


def render(s):
    W, bar, pad = 560, 36, 24
    langs = s["languages"]
    head_y = bar + 38
    rows_y = head_y + (52 if s["editors"] else 30)
    row_h = 26
    prompt_y = (rows_y + len(langs) * row_h + 18) if langs else (rows_y + 26)
    H = prompt_y + 22
    name_x, bar_x, bar_w = pad, pad + 108, 220
    time_x = bar_x + bar_w + 14
    summary = (f"Coding time in the last 7 days: {s['total']}" +
               "".join(f", {l['name']} {l['text']}" for l in langs))
    out = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" viewBox="0 0 {W} {H}" '
        f'role="img" aria-label="{escape(summary)}">',
        f"<title>{escape(summary)}</title>",
        "<style>"
        f"text{{font-family:{FONT};font-size:14px;fill:{TEXT}}}"
        f".k{{fill:#58a6ff;font-weight:700}}.m{{fill:{MUTED}}}.g{{fill:#3fb950;font-weight:700}}.c{{fill:#39c5cf}}"
        f".t{{font-size:12px;fill:{MUTED}}}"
        ".r{animation:in .45s cubic-bezier(.2,.8,.2,1) both}"
        "@keyframes in{from{opacity:0;transform:translateX(-8px)}to{opacity:1;transform:none}}"
        ".grow{transform-box:fill-box;transform-origin:left;animation:grow .9s cubic-bezier(.2,.8,.2,1) both}"
        "@keyframes grow{from{transform:scaleX(0)}to{transform:none}}"
        ".cur{animation:blink 1s steps(1) infinite}@keyframes blink{50%{fill-opacity:0}}"
        "@media (prefers-reduced-motion:reduce){*{animation:none!important}}"
        "</style>",
        f'<rect x=".5" y=".5" width="{W - 1}" height="{H - 1}" rx="12" fill="{BG}" stroke="{BORDER}"/>',
        f'<path d="M.5 {bar}V12.5a12 12 0 0 1 12-12h{W - 25}a12 12 0 0 1 12 12V{bar}z" fill="{BAR}"/>',
        f'<line x1="0" y1="{bar}" x2="{W}" y2="{bar}" stroke="{BORDER}"/>',
        *(f'<circle cx="{20 + i * 20}" cy="{bar / 2}" r="6" fill="{c}"/>' for i, c in enumerate(["#ff5f57", "#febc2e", "#28c840"])),
        f'<text class="t" x="{W / 2}" y="{bar / 2 + 4}" text-anchor="middle">kenneth@aye-shun: ~ — wakatime</text>',
        f'<text class="r" x="{pad}" y="{head_y}"><tspan class="k">This week</tspan>: <tspan class="g">{escape(s["total"])}</tspan>'
        + (f'<tspan class="m"> · {escape(s["daily"])}/day</tspan>' if s["daily"] and s["seconds"] else "") + "</text>",
    ]
    if s["editors"]:
        out.append(f'<text class="r" style="animation-delay:.07s" x="{pad}" y="{head_y + 22}"><tspan class="k">Editors</tspan>: '
                   f'<tspan class="m">{escape(", ".join(s["editors"]))}</tspan></text>')
    top = max((l["percent"] for l in langs), default=1) or 1
    for i, l in enumerate(langs):
        y = rows_y + i * row_h
        color = LANG_COLORS.get(l["name"], FALLBACK[i % len(FALLBACK)])
        delay = f'style="animation-delay:{0.15 + i * 0.08:.2f}s"'
        out.append(
            f'<g class="r" {delay}>'
            f'<text x="{name_x}" y="{y}">{escape(l["name"][:12])}</text>'
            f'<rect x="{bar_x}" y="{y - 10}" width="{bar_w}" height="10" rx="5" fill="{TRACK}"/>'
            f'<rect class="grow" {delay} x="{bar_x}" y="{y - 10}" width="{max(bar_w * l["percent"] / top, 3):.1f}" height="10" rx="5" fill="{color}"/>'
            f'<text class="t" x="{time_x}" y="{y}">{escape(l["text"])}</text>'
            f'<text class="t" x="{W - pad}" y="{y}" text-anchor="end">{l["percent"]:.1f}%</text></g>'
        )
    if not langs:
        out.append(f'<text class="r m" x="{pad}" y="{rows_y}">No coding time recorded in the last 7 days</text>')
    out += [
        f'<text class="r" x="{pad}" y="{prompt_y}"><tspan class="g">→</tspan> <tspan class="c">~</tspan> <tspan class="cur">█</tspan></text>',
        f'<text class="r t" x="{W - pad}" y="{prompt_y}" text-anchor="end">via WakaTime · last 7 days</text>',
        "</svg>",
    ]
    return "\n".join(out) + "\n"


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--from-json", help="render data saved in this file instead of calling WakaTime")
    args = ap.parse_args()
    if args.from_json:
        with open(args.from_json) as f:
            stats = json.load(f)
    else:
        key = os.environ.get("WAKATIME_API_KEY")
        if not key:
            print("::notice::WAKATIME_API_KEY isn't set yet, so there's no card to build")
            return
        stats = fetch(key)
    os.makedirs(os.path.dirname(os.path.abspath(OUT)), exist_ok=True)
    with open(OUT, "w") as f:
        f.write(render(stats))
    print(f"wrote {OUT}")


if __name__ == "__main__":
    main()
