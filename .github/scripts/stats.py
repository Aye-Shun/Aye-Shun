"""Builds stats.svg, a neofetch-style GitHub stats card for the profile README.

.github/workflows/stats.yml runs this once a day with the repo's GITHUB_TOKEN
and commits stats.svg whenever the numbers change. To preview the card
locally without a token, render a saved stats file instead:

    python3 .github/scripts/stats.py --from-json sample.json
"""

import argparse
import base64
import json
import os
import urllib.request
from datetime import datetime, timezone
from xml.sax.saxutils import escape

LOGIN = os.environ.get("GH_LOGIN", "Aye-Shun")
OUT = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "stats.svg"))

QUERY = """
query($login: String!) {
  user(login: $login) {
    login
    createdAt
    avatarUrl(size: 320)
    followers { totalCount }
    pullRequests { totalCount }
    issues { totalCount }
    repositories(ownerAffiliations: OWNER, privacy: PUBLIC, isFork: false, first: 100) {
      totalCount
      nodes { stargazerCount }
    }
    contributionsCollection {
      totalCommitContributions
      contributionCalendar {
        totalContributions
        weeks { contributionDays { date contributionCount } }
      }
    }
  }
}
"""

# GitHub dark palette, same as the banner and badges
BG, BAR, BORDER = "#0d1117", "#010409", "#30363d"
TEXT, MUTED = "#e6edf3", "#8b949e"
ANSI = ["#484f58", "#ff7b72", "#3fb950", "#d29922", "#58a6ff", "#bc8cff", "#39c5cf", "#b1bac4"]
FONT = "ui-monospace,SFMono-Regular,'SF Mono',Menlo,Consolas,'Liberation Mono',monospace"


def fetch(login, token):
    req = urllib.request.Request(
        "https://api.github.com/graphql",
        data=json.dumps({"query": QUERY, "variables": {"login": login}}).encode(),
        headers={"Authorization": f"bearer {token}", "User-Agent": "profile-stats-card"},
    )
    with urllib.request.urlopen(req, timeout=30) as r:
        body = json.load(r)
    if body.get("errors"):
        raise SystemExit(f"GitHub API error: {body['errors']}")
    u = body["data"]["user"]
    cc = u["contributionsCollection"]
    days = [d for w in cc["contributionCalendar"]["weeks"] for d in w["contributionDays"]]
    current, best = streaks(days)
    # SVGs shown through <img> can't load outside URLs, so embed the avatar
    with urllib.request.urlopen(u["avatarUrl"], timeout=30) as r:
        avatar = f"data:{r.headers.get_content_type()};base64,{base64.b64encode(r.read()).decode()}"
    return {
        "login": u["login"],
        "created_at": u["createdAt"],
        "contributions": cc["contributionCalendar"]["totalContributions"],
        "commits": cc["totalCommitContributions"],
        "prs": u["pullRequests"]["totalCount"],
        "issues": u["issues"]["totalCount"],
        "repos": u["repositories"]["totalCount"],
        "stars": sum(n["stargazerCount"] for n in u["repositories"]["nodes"]),
        "followers": u["followers"]["totalCount"],
        "streak": current,
        "best_streak": best,
        "avatar": avatar,
    }


def streaks(days):
    """Current and longest runs of days with at least one contribution.

    An empty today doesn't end the current streak, since the day isn't over.
    """
    counts = [d["contributionCount"] for d in sorted(days, key=lambda d: d["date"])]
    best = run = 0
    for c in counts:
        run = run + 1 if c else 0
        best = max(best, run)
    if counts and not counts[-1]:
        counts = counts[:-1]
    current = 0
    for c in reversed(counts):
        if not c:
            break
        current += 1
    return current, best


def plural(n, word):
    return f"{n:,} {word}{'' if n == 1 else 's'}"


def uptime(created_at, today):
    """Account age, neofetch style: '1 year, 4 months'."""
    start = datetime.fromisoformat(created_at.replace("Z", "+00:00")).date()
    months = (today.year - start.year) * 12 + today.month - start.month - (today.day < start.day)
    years, months = divmod(max(months, 0), 12)
    parts = [plural(years, "year")] if years else []
    if months or not years:
        parts.append(plural(months, "month"))
    return ", ".join(parts)


def render(s, today):
    rows = [
        ("Uptime", uptime(s["created_at"], today), ""),
        ("Contributions", f"{s['contributions']:,}", " (last year)"),
        ("Commits", f"{s['commits']:,}", " (last year)"),
        ("Pull requests", f"{s['prs']:,}", ""),
        ("Issues", f"{s['issues']:,}", ""),
        ("Repos", f"{s['repos']:,}", " public"),
        ("Stars", f"{s['stars']:,}", ""),
        ("Followers", f"{s['followers']:,}", ""),
        ("Streak", plural(s["streak"], "day"), f" (best {s['best_streak']:,})"),
    ]
    login = escape(s["login"])
    summary = f"GitHub stats for {login}: " + ", ".join(f"{k.lower()} {v}{x}" for k, v, x in rows)

    W, bar, pad, av, lh = 560, 36, 24, 160, 22
    ix = pad + av + 32                      # left edge of the info column
    header_y = bar + 42
    row0 = header_y + 46
    blocks_y = row0 + (len(rows) - 1) * lh + 18
    prompt_y = blocks_y + 50
    H = prompt_y + 22
    av_y = (header_y - 14 + blocks_y + 16) / 2 - av / 2   # centred on the info column

    def delay(i):
        return f'style="animation-delay:{0.15 + i * 0.07:.2f}s"'

    out = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" viewBox="0 0 {W} {H}" '
        f'role="img" aria-label="{summary}">',
        f"<title>{summary}</title>",
        "<style>"
        f"text{{font-family:{FONT};font-size:14px;fill:{TEXT}}}"
        f".k{{fill:#58a6ff;font-weight:700}}.m{{fill:{MUTED}}}.g{{fill:#3fb950;font-weight:700}}.c{{fill:#39c5cf}}"
        f".t{{font-size:12px;fill:{MUTED}}}"
        ".r{animation:in .45s cubic-bezier(.2,.8,.2,1) both}"
        "@keyframes in{from{opacity:0;transform:translateX(-8px)}to{opacity:1;transform:none}}"
        ".cur{animation:blink 1s steps(1) infinite}@keyframes blink{50%{fill-opacity:0}}"
        "@media (prefers-reduced-motion:reduce){*{animation:none!important}}"
        "</style>",
        f'<defs><clipPath id="av"><rect x="{pad}" y="{av_y:.1f}" width="{av}" height="{av}" rx="14"/></clipPath></defs>',
        # window and title bar
        f'<rect x=".5" y=".5" width="{W - 1}" height="{H - 1}" rx="12" fill="{BG}" stroke="{BORDER}"/>',
        f'<path d="M.5 {bar}V12.5a12 12 0 0 1 12-12h{W - 25}a12 12 0 0 1 12 12V{bar}z" fill="{BAR}"/>',
        f'<line x1="0" y1="{bar}" x2="{W}" y2="{bar}" stroke="{BORDER}"/>',
        *(f'<circle cx="{20 + i * 20}" cy="{bar / 2}" r="6" fill="{c}"/>' for i, c in enumerate(["#ff5f57", "#febc2e", "#28c840"])),
        f'<text class="t" x="{W / 2}" y="{bar / 2 + 4}" text-anchor="middle">kenneth@aye-shun: ~ — ghfetch</text>',
        # avatar
        f'<g class="r"><image href="{s["avatar"]}" x="{pad}" y="{av_y:.1f}" width="{av}" height="{av}" '
        f'preserveAspectRatio="xMidYMid slice" clip-path="url(#av)"/>'
        f'<rect x="{pad}" y="{av_y:.1f}" width="{av}" height="{av}" rx="14" fill="none" stroke="{BORDER}"/></g>',
        # user@host header and its underline
        f'<text class="r" {delay(0)} x="{ix}" y="{header_y}"><tspan class="g">{login}</tspan>@<tspan class="k">github</tspan></text>',
        f'<text class="r m" {delay(0)} x="{ix}" y="{header_y + 20}">{"-" * (len(s["login"]) + 7)}</text>',
    ]
    for i, (key, value, extra) in enumerate(rows):
        out.append(
            f'<text class="r" {delay(i + 1)} x="{ix}" y="{row0 + i * lh}">'
            f'<tspan class="k">{key}</tspan>: {escape(value)}<tspan class="m">{escape(extra)}</tspan></text>'
        )
    out.append(
        f'<g class="r" {delay(len(rows) + 1)}>'
        + "".join(f'<rect x="{ix + i * 26}" y="{blocks_y}" width="26" height="16" fill="{c}"/>' for i, c in enumerate(ANSI))
        + "</g>"
    )
    out.append(
        f'<text class="r" {delay(len(rows) + 2)} x="{pad}" y="{prompt_y}">'
        f'<tspan class="g">→</tspan> <tspan class="c">~</tspan> <tspan class="cur">█</tspan></text>'
    )
    out.append("</svg>")
    return "\n".join(out) + "\n"


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--from-json", help="render stats saved in this file instead of calling the API")
    args = ap.parse_args()
    if args.from_json:
        with open(args.from_json) as f:
            stats = json.load(f)
    else:
        token = os.environ.get("GITHUB_TOKEN")
        if not token:
            raise SystemExit("GITHUB_TOKEN is not set (or pass --from-json)")
        stats = fetch(LOGIN, token)
    with open(OUT, "w") as f:
        f.write(render(stats, datetime.now(timezone.utc).date()))
    print(f"wrote {OUT}")


if __name__ == "__main__":
    main()
