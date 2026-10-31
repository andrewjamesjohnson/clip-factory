"""The Money tab (2026-10-29, Andrew: "another tab for monetization... coach me on better ways to make more money,
merch, the best ways to make money through these clips now, reaching out to people"). It reads the Stats tab's own
numbers (statspage.data: views per post, followers, the payout rates, the goal and which programs each account is in)
and turns them into a coach: what pays now, how close each account is to each platform's program, and the next steps,
with the outreach note ready to copy. It only reads. The programs' bars were checked 2026-10-29 (sources on the page);
the payout rates are the owner's (state/stats/settings.json, the Stats tab's Money part)."""
from __future__ import annotations

import json
from collections import defaultdict
from datetime import date, datetime, timedelta
from typing import Dict, List, Optional

from . import config as C
from . import look as LK
from .look import esc, k

# what each platform's program asks (checked 2026-10-29; guides disagree on Facebook's bar, so it says so)
YT_SUBS, YT_VIEWS_90 = 1000, 10_000_000          # Partner Program; from 2027-02-01 Shorts pay only past 10M in 90 days
TT_FOLLOWERS, TT_VIEWS_30 = 10_000, 100_000      # Creator Rewards: videos over 1 minute, personal account, 18+
FB_FOLLOWERS = 10_000                              # Content Monetization: by invite; the guides' usual bar
SOURCES = [
    ("YouTube: Shorts pay from 2027-02-01 (YouTube blog, Aug 10 2026)", "https://blog.youtube/"),
    ("TikTok Creator Rewards: what it asks (Toptal, 2026)", "https://www.toptal.com/creator/post/how-to-join-the-tiktok-creator-rewards-program"),
    ("Facebook: how creators get paid in 2026 (Buffer)", "https://www.buffer.com/resources/how-to-make-money-on-facebook/"),
    ("Clipping pay: 50 Whop campaigns, Sept 2026 (median $1 per 1,000 views)", "https://www.builtbyfoundry.io/blog/what-is-clipping-1-dollar-per-1000-views-50-campaigns"),
    ("Whop Content Rewards: how campaigns pay", "https://docs.whop.com/manage-your-business/growth-marketing/content-rewards"),
]
OUTREACH = ("Hi {name}, I run a small clip studio. I cut 3 short clips from your latest video (links below). "
            "I can post 30 like these a month to your TikTok, Reels and Shorts for $750. "
            "Want me to send the rest of this week's? Andrew")


def money(n: float) -> str:
    n = float(n or 0)
    if n >= 10_000:
        return f"${n / 1000:.0f}K"
    if n >= 100:
        return f"${n:,.0f}"
    return f"${n:,.2f}" if n else "$0"


def numbers(cfg: C.Config, now: datetime) -> dict:
    """The Stats tab's numbers, per account: views in the last 30 and 90 days by platform, followers, and the money
    they would make at the owner's payout rates or in clipping campaigns."""
    from . import statspage as SP
    d = SP.data(cfg, now)
    sets = d.get("settings") or {}
    rpm = sets.get("rpm") or {}
    fees = float(d.get("campaign_fees") or 0.1)
    today = now.date()
    d30, d90 = (today - timedelta(days=29)).isoformat(), (today - timedelta(days=89)).isoformat()
    accts = []
    for a in d.get("accounts") or []:
        slug = a["id"]
        v30: Dict[str, int] = defaultdict(int)
        v90: Dict[str, int] = defaultdict(int)
        n30 = 0
        tt_long = 0
        for p in d.get("posts") or []:
            if p.get("account") != slug:
                continue
            day, v = str(p.get("day") or ""), int(p.get("views") or 0)
            if day >= d30:
                v30[p["platform"]] += v
                n30 += 1
                if p["platform"] == "tiktok" and float(p.get("dur") or 0) > 60:
                    tt_long += v
            if day >= d90:
                v90[p["platform"]] += v
        fol = {}
        for plat, f in ((d.get("followers") or {}).get(slug) or {}).items():
            typed = ((sets.get("followers_typed") or {}).get(slug) or {}).get(plat) or {}
            n = f.get("followers") if f.get("followers") is not None else typed.get("n")
            fol[plat] = int(n) if isinstance(n, (int, float)) else None
        programs = (v30.get("facebook", 0) * float(rpm.get("facebook") or 0)
                    + v30.get("youtube", 0) * float(rpm.get("youtube") or 0)
                    + tt_long * float(rpm.get("tiktok_long") or 0)) / 1000
        campaign = sum(v30.values()) * float(rpm.get("campaign") or 1.0) / 1000 * (1 - fees)
        eligible = (sets.get("eligible") or {}).get(slug) or {}
        accts.append({"slug": slug, "name": a.get("name") or slug, "v30": dict(v30), "v90": dict(v90), "n30": n30,
                      "followers": fol, "programs": programs, "campaign": campaign,
                      "in": [p for p, on in eligible.items() if on]})
    return {"accounts": accts, "goal": float(sets.get("goal") or 0), "rpm": rpm, "fees": fees,
            "read_at": d.get("read_at") or ""}


def _bar(have: Optional[float], need: float) -> str:
    if have is None:
        return '<span class="pbar none"><i style="width:0"></i></span>'
    pct = max(0.0, min(100.0, (have or 0) * 100 / need))
    return f'<span class="pbar"><i style="width:{pct:.1f}%"></i></span>'


def _have(n: Optional[float]) -> str:
    return "not read yet" if n is None else f"{n:,.0f}"


def _progress(cfg: C.Config, nums: dict) -> str:
    rows = []
    for a in nums["accounts"]:
        f, v30, v90 = a["followers"], a["v30"], a["v90"]
        cells = [
            ("YouTube", "Partner Program",
             [("subscribers", f.get("youtube"), YT_SUBS), ("Shorts views, 90 days", v90.get("youtube", 0), YT_VIEWS_90)]),
            ("TikTok", "Creator Rewards",
             [("followers", f.get("tiktok"), TT_FOLLOWERS), ("views, 30 days", v30.get("tiktok", 0), TT_VIEWS_30)]),
            ("Facebook", "Content Monetization (by invite)",
             [("followers", f.get("facebook"), FB_FOLLOWERS)]),
            ("Instagram", "No pay per view; gifts and subscriptions from 10K",
             [("followers", f.get("instagram"), 10_000)]),
        ]
        out = []
        for plat, prog, bars in cells:
            lines = "".join(f'<div class="pl"><span>{esc(w)}</span><b>{_have(h)} <small>of {k(n)}</small></b>{_bar(h, n)}</div>'
                            for w, h, n in bars)
            ab = {"YouTube": "YT", "TikTok": "TT", "Facebook": "FB", "Instagram": "IG"}[plat]
            out.append(f'<div class="prog"><div class="ph">{LK.ICON[ab]}<b>{plat}</b><span>{esc(prog)}</span></div>{lines}</div>')
        rows.append(f'<div class="acct">{LK.logo(cfg.account(a["slug"]) if a["slug"] in cfg.accounts else a["slug"])}'
                    f'<h3>{esc(a["name"])}</h3><span class="muted">{"in: " + ", ".join(a["in"]) if a["in"] else "in no paid program yet"}</span></div>'
                    f'<div class="progs">{"".join(out)}</div>')
    return "".join(rows)


def plays(nums: dict) -> List[dict]:
    """The coach's moves, best first: what pays soonest for the least work, with this factory's own numbers."""
    A = {a["slug"]: a for a in nums["accounts"]}
    politics, gta = A.get("politics", {}), A.get("gaming", {})
    all30 = sum(sum(a["v30"].values()) for a in nums["accounts"])
    camp = sum(a["campaign"] for a in nums["accounts"])
    fb30 = (politics.get("v30") or {}).get("facebook", 0)
    lv30 = sum((politics.get("v30") or {}).values()) or 1
    fbrate = float(nums["rpm"].get("facebook") or 0)
    out = [
        {"key": "campaigns", "first": "Open Whop, go to Content Rewards and pick 2 campaigns.", "title": "Get paid per view now: clipping campaigns",
         "money": f"about {money(camp)} a month", "effort": "Low", "soon": "This week",
         "why": f"Creators and brands pay clippers for views, with no follower minimum. You made {all30:,} views in the "
                f"last 30 days. At the usual $1 per 1,000 views (minus about 10% fees), that many campaign views would pay "
                f"about {money(camp)}.",
         "steps": ["Open Whop and go to Content Rewards.",
                   "Pick 2 campaigns: one GTA or gaming creator, one podcast or news creator.",
                   "Check each one's rules and its minimum views a clip (often about 2,000).",
                   "Tell me which two: I add a campaign lane, so the factory cuts their videos by their rules."]},
        {"key": "service", "first": "List 20 people who post long videos but no shorts.", "title": "Sell your clips as a service",
         "money": "$500 to $2,000 a month for each client", "effort": "Medium", "soon": "2 to 3 weeks",
         "why": "The factory already makes 18 clips a day and has client pages. People who post long videos but no "
                "shorts pay for this: podcasts, coaches, real estate agents, local businesses, small streamers.",
         "steps": ["List 20 of them (the same calls you make for websites).",
                   "I cut 3 free clips from one video of each.",
                   "Send the note below with the 3 links. Offer 30 clips a month for $750.",
                   "Each yes gets its own client page and posting."]},
        {"key": "facebook", "first": "Open Facebook, Professional Dashboard, Monetization.", "title": "Facebook pays the Politics first",
         "money": f"{money(fb30 * fbrate / 1000)} a month at your rate now; it grows with views", "effort": "Low",
         "soon": "Check today",
         "why": f"Facebook is where the Politics's views are: {fb30:,} in 30 days ({fb30 * 100 // lv30}% of them). "
                "Its Content Monetization is by invite. The guides disagree on the bar, so check yours.",
         "steps": ["Open Facebook, Professional Dashboard, Monetization.",
                   "Ask to join Content Monetization if there is no invite yet.",
                   "Keep the Politics at 6 posts a day there (posts 7 and up get almost no views).",
                   "Gaming has the same 6-a-day cap since Oct 29."]},
        {"key": "tiktok", "first": "Post every Politics clip to TikTok each day.", "title": "TikTok pays for clips over 1 minute",
         "money": "about $0.40 to $1.00 per 1,000 views, once in", "effort": "Medium", "soon": "When 10,000 followers",
         "why": "Creator Rewards pays only for videos over 1 minute. Every Politics clip is 65 to 90 seconds, so they "
                "all count. It needs 10,000 followers and 100,000 views in 30 days, on a personal account.",
         "steps": ["Post every Politics clip to TikTok each day (they wait in Ready to post).",
                   "Type the TikTok follower count on the Stats tab once a week, so this tab can track it.",
                   "Apply in TikTok's Creator tools as soon as both bars are met."]},
        {"key": "launch", "first": "Add one deals link to each Gaming bio.", "title": "GTA 6 launch week (Nov 19)",
         "money": "the biggest GTA traffic of the year", "effort": "Low", "soon": "Before Nov 19",
         "why": "Launch week brings more GTA searches and views than any other week. Clips that week can carry links "
                "that pay.",
         "steps": ["Add one link in each Gaming bio (game, console or controller deals).",
                   "Run more GTA slots that week; I can add them.",
                   "Pin the best launch clip on every platform."]},
        {"key": "youtube", "first": "Keep posting there for followers.", "title": "YouTube is for reach, not pay (for now)",
         "money": "$0 until a channel passes 10M Shorts views in 90 days", "effort": "None", "soon": "Not this year",
         "why": f"From Feb 1, 2027, Shorts earn only past 10 million Shorts views in 90 days, per channel. Gaming has "
                f"{(gta.get('v90') or {}).get('youtube', 0):,} and the Politics {(politics.get('v90') or {}).get('youtube', 0):,}.",
         "steps": ["Keep posting there for followers and search.",
                   "Before Feb 2027, decide one GTA channel or several (each counts on its own)."]},
        {"key": "email", "first": "Start a free Politics newsletter page.", "title": "Own the audience: a free Politics email",
         "money": "sponsors pay per subscriber later", "effort": "Medium", "soon": "Next month",
         "why": "Followers belong to the platforms. An email list is yours: the day's top clip and one line.",
         "steps": ["Start a free newsletter page (Beehiiv or Substack).", "Put the link in every Politics bio.",
                   "One email a day: the best clip, one sentence."]},
        {"key": "merch", "first": "Wait for 10,000 followers on one platform.", "title": "Merch, later",
         "money": "worth it past about 10,000 followers", "effort": "Medium", "soon": "Later",
         "why": "Merch sells to fans, and the accounts are still small. When Gaming grows, its gaming is the merch. "
                "Keep the Politics to news: no political merch.",
         "steps": ["Wait for 10,000 followers on one platform.", "Then print on demand (no stock, no cost up front)."]},
    ]
    return out


def path_to_goal(nums: dict) -> str:
    """How the plays add up to the owner's monthly goal: a plan, not a forecast."""
    goal = nums.get("goal") or 0
    if goal <= 0:
        return ""
    views = sum(sum(a["v30"].values()) for a in nums["accounts"])
    rate = float(nums["rpm"].get("campaign") or 1.0) * (1 - float(nums.get("fees") or 0.1))
    clients = 8
    svc = clients * 750
    camp_views = 3_000_000
    camp = camp_views / 1000 * rate
    rest = max(0.0, goal - svc - camp)
    rows = [(f"{clients} clip clients at $750 a month", svc, "you call businesses already"),
            (f"{camp_views / 1e6:.0f} million campaign views a month", camp, f"you get about {views:,} views a month now"),
            ("Platform pay and links (Facebook, TikTok, launch links)", rest, "grows as the accounts grow")]
    lines = "".join(f'<div class="gl"><span>{esc(w)}<small>{esc(s)}</small></span><b>{money(v)}</b></div>' for w, v, s in rows)
    return (f'<section class="mbox"><span class="label">A path to {money(goal)} a month</span>'
            f'<p class="muted small">A plan, not a promise: what each move would need to bring in.</p>{lines}'
            f'<div class="gl total"><span>Together</span><b>{money(svc + camp + rest)}</b></div></section>')


def page(cfg: C.Config, now: datetime, tok: str, css: str, tabs_html: str, switch_html: str, acct: str = "all") -> str:
    try:
        nums = numbers(cfg, now)
    except Exception as e:  # noqa: BLE001  (the tab says what failed rather than going down)
        nums = {"accounts": [], "goal": 0, "rpm": {}, "fees": 0.1, "read_at": "", "error": str(e)}
    if acct != "all":
        nums["accounts"] = [a for a in nums["accounts"] if a["slug"] == acct]
    earned = 0.0                                    # actual: only programs an account is IN (none on 2026-10-29)
    camp = sum(a["campaign"] for a in nums["accounts"])
    prog = sum(a["programs"] for a in nums["accounts"])
    goal = nums.get("goal") or 0
    ins = [a["name"] for a in nums["accounts"] if a["in"]]
    ps = plays(nums) if nums["accounts"] else []
    top3 = "".join(f"<li><b>{esc(p['title'])}.</b> {esc(p['first'])}</li>" for p in ps[:3])
    cards = "".join(
        f'<article class="play" id="play-{esc(p["key"])}"><div class="pn">{i}</div><div class="pb">'
        f'<h3>{esc(p["title"])}</h3><div class="tags"><span class="t1">{esc(p["money"])}</span>'
        f'<span>Effort: {esc(p["effort"])}</span><span>When: {esc(p["soon"])}</span></div>'
        f'<p>{esc(p["why"])}</p><ol>{"".join(f"<li>{esc(s)}</li>" for s in p["steps"])}</ol></div></article>'
        for i, p in enumerate(ps, 1))
    note = OUTREACH.format(name="{name}")
    src = "".join(f'<li><a href="{esc(u)}" target="_blank" rel="noopener">{esc(t)}</a></li>' for t, u in SOURCES)
    body = (
        f'<section class="kpis">'
        + (f'<div class="kpi"><b>{money(earned)}</b><span>Earned so far (no account is in a paid program yet)</span></div>'
           if not ins else '<div class="kpi"><b>On Stats</b><span>Earned so far: the Stats tab counts the programs you are in</span></div>')
        + f'<div class="kpi okk"><b>{money(camp)}</b><span>A month in clipping campaigns (estimate)</span></div>'
        f'<div class="kpi"><b>{money(prog)}</b><span>A month at your payout rates (estimate)</span></div>'
        f'<div class="kpi"><b>{money(goal)}</b><span>Your goal a month</span></div></section>'
        f'<div class="mwrap"><div class="mmain">'
        f'<section class="mbox first"><span class="label">This week, do these 3</span><ol class="three">{top3}</ol></section>'
        f'{path_to_goal(nums)}'
        f'<section class="mbox"><div class="mh"><span class="label">Your coach</span>'
        f'<span class="muted">Best first: what pays soonest for the least work. Numbers are the last 30 days.</span></div>'
        f'{cards}</section></div>'
        f'<aside class="mside"><section class="mbox"><span class="label">How close each account is</span>'
        f'<p class="muted small">Each platform pays only past its bar. Bars checked Oct 29, 2026.</p>{_progress(cfg, nums)}</section>'
        f'<section class="mbox"><span class="label">Reach out: the note</span>'
        f'<p class="muted small">For selling clips as a service. Put their name in, add 3 clip links.</p>'
        f'<textarea id="note" readonly rows="6">{esc(note)}</textarea>'
        f'<button class="btn" id="copy" type="button">Copy the note</button></section>'
        f'<section class="mbox"><span class="label">Where these facts come from</span><ul class="src">{src}</ul>'
        f'<p class="muted small">Payout rates are yours, on the Stats tab (Money). {esc(nums.get("error") or "")}</p></section>'
        f'</aside></div>')
    js = ("document.getElementById('copy').addEventListener('click',async e=>{const t=document.getElementById('note');"
          "try{await navigator.clipboard.writeText(t.value);e.target.textContent='Copied'}"
          "catch(x){t.select();document.execCommand('copy');e.target.textContent='Copied'}"
          "setTimeout(()=>{e.target.textContent='Copy the note'},2000)});")
    return ("<!doctype html><html lang=en><head><meta charset=utf-8>"
            "<meta name=viewport content='width=device-width,initial-scale=1'>"
            f"<title>Clip Review · Money</title><style>{css}{CSS}</style></head><body class=v3>"
            f"<header><h1>Clip Review</h1>{tabs_html}{switch_html}</header>"
            f"<main class=moneymain>{body}</main><script>{js}</script></body></html>")


CSS = r"""
.moneymain .kpis{border-bottom:1px solid var(--line)}
.mwrap{display:grid;grid-template-columns:minmax(0,1fr) 380px;align-items:start}
.mmain{min-width:0;border-right:1px solid var(--line)}
.mbox{padding:18px 24px;border-bottom:1px solid var(--line)}
.mbox .label{display:block;margin-bottom:8px}
.mh{display:flex;align-items:baseline;gap:12px;flex-wrap:wrap;margin-bottom:6px}
.mh .label{margin:0}
.small{font-size:12.5px;margin:0 0 10px}
.three{margin:0;padding-left:20px;display:flex;flex-direction:column;gap:6px;font-size:14px}
.three b{font-weight:600}
.play{display:grid;grid-template-columns:28px minmax(0,1fr);gap:12px;padding:16px 0;border-top:1px solid var(--line)}
.play:first-of-type{border-top:0}
.pn{width:26px;height:26px;border-radius:6px;background:var(--ink);color:var(--card);font-weight:700;font-size:13px;display:flex;align-items:center;justify-content:center}
.play h3{margin:2px 0 6px;font-size:15.5px;font-weight:600;line-height:1.35}
.tags{display:flex;gap:8px;flex-wrap:wrap;margin-bottom:8px}
.tags span{font-size:12px;color:var(--sub);border:1px solid var(--line);border-radius:4px;padding:1px 8px}
.tags .t1{color:var(--ok);border-color:var(--ok);font-weight:600}
.play p{margin:0 0 8px;font-size:13.5px;color:var(--ink);line-height:1.5}
.play ol{margin:0;padding-left:20px;font-size:13.5px;color:var(--sub);display:flex;flex-direction:column;gap:3px}
.acct{display:flex;align-items:center;gap:10px;margin:14px 0 8px}
.acct .logo{width:28px;height:28px}
.acct h3{margin:0;font-size:14px;font-weight:600}
.acct .muted{font-size:12px;margin-left:auto}
.progs{display:flex;flex-direction:column}
.prog{padding:8px 0;border-top:1px solid var(--line)}
.ph{display:flex;align-items:center;gap:6px;font-size:13px;margin-bottom:4px}
.ph span{color:var(--sub);font-size:12px}
.pl{display:grid;grid-template-columns:1fr auto;gap:2px 8px;font-size:12px;color:var(--sub);margin:4px 0}
.pl b{color:var(--ink);font-weight:600;font-variant-numeric:tabular-nums}
.pl b small{color:var(--sub);font-weight:500}
.pbar{grid-column:1/-1;height:6px;border-radius:3px;background:var(--line);overflow:hidden;display:block}
.pbar i{display:block;height:100%;background:var(--ink);border-radius:3px}
.pbar.none{background:none;height:0;border-top:1px dashed var(--line);margin-top:3px}
#note{width:100%;font:inherit;font-size:13px;line-height:1.5;border:1px solid var(--line);border-radius:6px;padding:10px;color:var(--ink);background:var(--mute);resize:vertical;margin-bottom:10px}
.gl{display:flex;justify-content:space-between;align-items:baseline;gap:12px;padding:8px 0;border-top:1px solid var(--line);font-size:13.5px}
.gl span small{display:block;font-size:12px;color:var(--sub)}
.gl b{font-size:15px;font-variant-numeric:tabular-nums}
.gl.total b{font-size:18px}
.src{margin:0;padding-left:18px;font-size:12.5px;display:flex;flex-direction:column;gap:4px}
.src a{color:var(--ink)}
@media (max-width:900px){.mwrap{grid-template-columns:1fr}.mmain{border-right:0}}
@media (max-width:760px){.mbox{padding:16px}.moneymain .kpis{grid-template-columns:repeat(2,minmax(0,1fr))}}
"""
