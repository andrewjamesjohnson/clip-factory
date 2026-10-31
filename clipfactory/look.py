"""The Clip Review look of 2026-10-29 (Andrew: "not changing the fundamentals, just cleaning it up"; the mockup he
approved: Desktop/CLIP FACTORY/Clip Review Redesign 10.29/Clip Review mockup v3.html). The Clips page keeps every card,
button and refresh it had; this module draws what is new around them: the four numbers on top, the breaking line, the
right column (yesterday, best clips, early numbers, what needs a look), each account's column beside its lane, the
platform squares on each tile, the Calendar tab and the month under the date button. It only reads."""
from __future__ import annotations

import html
import json
import re
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

from . import board as B
from . import config as C

PLATS = [("youtube_shorts", "YT"), ("facebook_reels", "FB"), ("instagram_reels", "IG"), ("tiktok", "TT")]
NAMES = {"YT": "YouTube", "FB": "Facebook", "IG": "Instagram", "TT": "TikTok"}
ICON = {
    "YT": '<svg class="i" viewBox="0 0 24 24"><path fill-rule="evenodd" d="M6 5h12a4 4 0 0 1 4 4v6a4 4 0 0 1-4 4H6a4 4 0 '
          '0 1-4-4V9a4 4 0 0 1 4-4zm4 3.8v6.4l5.4-3.2z"/></svg>',
    "FB": '<svg class="i" viewBox="0 0 24 24"><path d="M13.6 22v-8.2h2.8l.4-3.3h-3.2V8.4c0-.9.3-1.6 1.6-1.6h1.7V3.9c-.3 '
          '0-1.3-.1-2.5-.1-2.5 0-4.2 1.5-4.2 4.3v2.4H7.4v3.3h2.8V22z"/></svg>',
    "IG": '<svg class="i" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2.4"><rect x="3.2" y="3.2" '
          'width="17.6" height="17.6" rx="5" fill="none"/><circle cx="12" cy="12" r="4.1" fill="none"/><circle '
          'cx="17.4" cy="6.6" r="1.2" stroke="none"/></svg>',
    "TT": '<svg class="i" viewBox="0 0 24 24"><path d="M16.3 2h-3.4v13.3a2.9 2.9 0 1 1-2.9-2.9l.9.1V9.1a6.4 6.4 0 1 0 '
          '5.4 6.3V8.7a7.9 7.9 0 0 0 4.4 1.3V6.6A4.4 4.4 0 0 1 16.3 2z"/></svg>',
}
# a platform row's class (review._plat_rows) -> its square: posted, missed, held up, not yet, by hand
SQUARE = {"done": "ok", "ok": "ok", "bad": "bad", "warn": "held", "wait": "up", "hand": "hand"}
# each account's colour in charts and the calendar: the dataviz reference palette's first slots, validated light
# 10-29 (CVD separation 24.7, normal-vision 33.6); a third or fourth account takes the next slots
ACCOUNT_COLOURS = {"politics": "#2a78d6", "gaming": "#eb6834"}
MORE_COLOURS = ["#1baf7a", "#eda100", "#e87ba4"]
LOGOS = C.ROOT / "config" / "logos"
CAL_ICON = ('<svg class="i" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="3.5" y="5" '
            'width="17" height="15.5" rx="2"/><path d="M3.5 10h17M8 3v4M16 3v4"/></svg>')


def esc(s: object) -> str:
    return html.escape(str(s if s is not None else ""), quote=True)


def colour(cfg: C.Config, slug: str) -> str:
    if slug in ACCOUNT_COLOURS:
        return ACCOUNT_COLOURS[slug]
    others = [a for a in cfg.accounts if a not in ACCOUNT_COLOURS]
    return MORE_COLOURS[others.index(slug) % len(MORE_COLOURS)] if slug in others else "#8a94a6"


def k(n: object) -> str:
    """12345 -> 12K, 1234 -> 1.2K."""
    try:
        n = float(n or 0)
    except (TypeError, ValueError):
        return "0"
    if n >= 1e6:
        return f"{n / 1e6:.1f}M"
    if n >= 1e4:
        return f"{n / 1e3:.0f}K"
    if n >= 1e3:
        return f"{n / 1e3:.1f}".rstrip("0").rstrip(".") + "K"
    return str(int(round(n)))


def logo_file(slug: str) -> Optional[Path]:
    """config/logos/<slug>.jpg when the account has one (the page's /logo/<slug>.jpg; the page's policy takes
    pictures from the page itself only)."""
    if not slug.replace("-", "").isalnum():
        return None
    p = LOGOS / f"{slug}.jpg"
    return p if p.is_file() else None


def logo(acct, cls: str = "") -> str:
    """The account's own picture (config/logos/<slug>.jpg, 96 px), or its first letters."""
    slug = getattr(acct, "slug", str(acct))
    if logo_file(slug):
        return f'<img class="logo {cls}" src="/logo/{esc(slug)}.jpg" alt="">'
    name = getattr(acct, "name", slug)
    letters = "".join(w[0] for w in str(name).split()[:2]).upper()
    return f'<span class="logo {cls}">{esc(letters)}</span>'


def views_of(s: dict) -> Dict[str, int]:
    """Views per platform the slot has posted to (YT, FB, IG)."""
    out = {}
    for plat, ab in PLATS[:3]:
        rec = (s.get("outlets") or {}).get(plat) or {}
        if rec.get("post_id") or rec.get("video_id") or rec.get("url"):
            try:
                out[ab] = int((rec.get("metrics") or {}).get("views") or 0)
            except (TypeError, ValueError):
                out[ab] = 0
    return out


def squares(rows: Sequence[Tuple[str, str, str, str, str]], signed: bool, s: dict,
            now: Optional[datetime] = None) -> str:
    """The four platform squares of a tile (YouTube, Facebook, Instagram, TikTok), each coloured by what happened, with
    the views so far. rows: review._plat_rows of a signed slot; an unsigned clip shows every platform as not yet."""
    by_name = {r[1]: r for r in rows}
    out = []
    for plat, ab in PLATS:
        r = by_name.get(NAMES[ab])
        if r is None:
            if ab == "TT":
                cls, words = "hand", "posted by hand"
            elif signed:
                continue
            else:
                cls, words = "up", "waits for Approve"
        else:
            cls, words = SQUARE.get(r[0], "up"), r[2]
            if cls == "held" and "uploads in the last 24 hours" in words and _ahead(s, now):
                cls = "up"      # the rolling upload window: it goes up before its time (10-29: every GTA tile was amber)
        out.append(f'<i class="p {cls}" title="{esc(NAMES[ab] + ": " + words)}">{ICON[ab]}</i>')
    v = sum(views_of(s).values())
    return f'<span class="pf">{"".join(out)}{f"<span class=pv>{k(v)}</span>" if v else ""}</span>'


def _ahead(s: dict, now: Optional[datetime]) -> bool:
    """Is the slot's time still to come?"""
    try:
        return now is not None and datetime.fromisoformat(str(s["publish_at"])) > now
    except (KeyError, TypeError, ValueError):
        return False


# ------------------------------------------------------------------------------------------------------ the top
def kpis(n_need: int, n_posted: int, n_coming: int, n_missed: int, day_words: str) -> str:
    """The four numbers over the lanes, for the day the page shows (Needs you counts every day)."""
    return (f'<div class="kpi need"><button data-go="need"><b>{n_need}</b><span>Need{"s" if n_need == 1 else ""} you</span></button></div>'
            f'<div class="kpi okk"><b>{n_posted}</b><span>Posted {esc(day_words)}</span></div>'
            f'<div class="kpi"><b>{n_coming}</b><span>Still to post</span></div>'
            f'<div class="kpi bad"><button data-go="bad"><b>{n_missed}</b><span>Missed a platform</span></button></div>')


def split_chips(chips: Sequence[dict]) -> Tuple[List[dict], List[dict]]:
    """The Today band's chips: (breaking ones, the rest)."""
    brk, rest = [], []
    for c in chips or []:
        if isinstance(c, dict) and str(c.get("words") or "").strip():
            (brk if str(c.get("key") or "").startswith("breaking") else rest).append(c)
    return brk, rest


def breaking(chips: Sequence[dict]) -> str:
    """One line for breaking news: the first story, the rest behind "+N more"."""
    stories = [c for c in chips if c.get("key") != "breaking:more"]
    if not stories:
        return ""
    more = len(stories) - 1
    for c in chips:
        if c.get("key") == "breaking:more":
            try:
                more += int(str(c["words"]).split("+", 1)[1].split()[0])
            except (IndexError, ValueError):
                pass
    w = re.sub(r"^BREAKING[,:.]?\s*", "", str(stories[0]["words"])) or str(stories[0]["words"])
    first = dict(stories[0], words=w[:1].upper() + w[1:])
    rest = "".join(f"<li>{esc(c['words'])}</li>" for c in stories[1:])
    tail = (f'<details class="more"><summary>+{more} more</summary><ul>{rest}</ul></details>' if more and rest
            else f'<span class="muted">+{more} more</span>' if more else "")
    return f'<span class="tag">Breaking</span><span class="txt" title="{esc(first["words"])}">{esc(first["words"])}</span>{tail}'


# ------------------------------------------------------------------------------------------------------ the right column
def _shown(s: dict) -> Optional[str]:
    """The picture a card shows: the slot's designed cover (coverart.py) when it has one, else its poster."""
    art = (s.get("cover_art") or {}).get("path") if isinstance(s.get("cover_art"), dict) else None
    return art if art and Path(art).is_file() else s.get("cover")


def _cover_url(path: Optional[str]) -> str:
    from . import review as R
    return R._file_url(path) if path and Path(path).is_file() else ""


def _title(s: dict) -> str:
    from . import review as R
    return R._title(s)


def _chg(c) -> str:
    try:
        c = float(c)
    except (TypeError, ValueError):
        return ""
    if abs(c) < 0.02:
        return '<span class="same">same</span>'
    return f'<span class="{"up" if c > 0 else "down"}">{"↑" if c > 0 else "↓"} {abs(c) * 100:.0f}%</span>'


def _row(left: str, title: str, meta: str, right: str, href: str = "", attrs: str = "") -> str:
    tag = "a" if href else "div"
    h = f' href="{esc(href)}"' if href else ""
    return (f'<{tag} class="rrow"{h}{attrs}>{left}<span class="mid"><span class="t">{title}</span>'
            f'<span class="m">{meta}</span></span><span class="v">{right}</span></{tag}>')


def _mini(url: str, acct) -> str:
    return f'<img class="mini" src="{esc(url)}" alt="" loading="lazy">' if url else logo(acct)


def rail(cfg: C.Config, now: datetime, accts: list, rest_chips: Sequence[dict], acct: str = "all") -> str:
    """The right column: yesterday's views per account, the best clips of the last two days, the early numbers, what
    needs a look, and the rest of the Today band. Each part is skipped when it has nothing; a part that fails to read
    is left out (the page never goes down over it)."""
    from . import learn as L
    from . import checkpoints as CP
    from .socialui import _with_acct
    acct_q = "" if acct == "all" else "?acct=" + acct
    parts: List[str] = []
    reps = []
    for a in accts:
        try:
            if L._is_client(a) or L.SW.mode(a, "learn") != "on":
                continue
            reps.append((a, L._latest(a, now, cfg) or {}))
        except Exception:  # noqa: BLE001  (a report that does not read is left out)
            reps.append((a, {}))
    have = [(a, r) for a, r in reps if r.get("kpis")]
    if have:
        tv = sum(int(r["kpis"].get("views_day") or 0) for _, r in have)
        tp = sum(int(r["kpis"].get("views_prev") or 0) for _, r in have)
        rows = "".join(_row(logo(a), esc(a.name), f'{int(r["kpis"].get("posts_day") or 0)} posts · '
                            f'{float(r["kpis"].get("success_rate") or 0) * 100:.0f}% went out',
                            f'{k(r["kpis"].get("views_day"))}<small>{_chg(r["kpis"].get("change"))}</small>',
                            href="/reports" + acct_q)
                       for a, r in sorted(have, key=lambda x: -int(x[1]["kpis"].get("views_day") or 0)))
        parts.append(f'<section class="box"><div class="box-h"><span class="label">Yesterday</span>'
                     f'<a href="/reports{esc(acct_q)}">Report ›</a></div><div class="yhead"><b>{k(tv)}</b>'
                     f'<span class="muted">views</span>{_chg(tv / tp - 1) if tp else ""}</div>{rows}</section>')
    best = []
    for a, r in reps:
        for b in (r.get("best") or [])[:5]:
            best.append((float(b.get("views_24h") or 0), a, b))
    best.sort(key=lambda x: -x[0])
    if best:
        rows = []
        for v, a, b in best[:3]:
            s = {}
            try:
                s = (B.read(a, date.fromisoformat(str(b.get("board")))) or {}).get("slots", {}).get(b.get("slot")) or {}
            except (ValueError, TypeError, OSError):
                pass
            ab = dict(PLATS).get(b.get("outlet"), "")
            when = f'{date.fromisoformat(str(b["board"])):%b} {int(str(b["board"])[8:])}, {b.get("slot")}' if b.get("board") else ""
            x = b.get("x")
            right = f'{k(v)}<small class="{"up" if (x or 0) >= 1.5 else "same"}">{f"{float(x):.1f}x usual" if x else ""}</small>'
            rows.append(_row(_mini(_cover_url(_shown(s)), a), esc(b.get("title") or _title(s)),
                             f'<i class="tick" style="background:{colour(cfg, a.slug)}"></i>{ICON.get(ab, "")}{esc(when)}',
                             right, href=str(b.get("url") or ""), attrs=' target="_blank" rel="noopener"'))
        parts.append(f'<section class="box"><div class="box-h"><span class="label">Best clips · last 2 days</span>'
                     f'<a href="/stats{esc(acct_q)}">Stats ›</a></div>{"".join(rows)}</section>')
    sig = []
    for a in accts:
        try:
            for e in CP.early(cfg, a, now) or []:
                sig.append((a, e))
        except Exception:  # noqa: BLE001
            continue
    hot = sorted([x for x in sig if x[1].get("kind") == "hot"], key=lambda x: -float(x[1].get("x") or 0))
    slow = sorted([x for x in sig if x[1].get("kind") == "slow"], key=lambda x: float(x[1].get("x") or 0))
    seen, pick = set(), []
    for a, e in hot + slow:
        key = (a.slug, e.get("board"), e.get("slot"))
        if key not in seen and (len([p for p in pick if p[1].get("kind") == e.get("kind")]) < (3 if e.get("kind") == "hot" else 2)):
            seen.add(key)
            pick.append((a, e))
    if pick:
        rows = []
        today = now.date().isoformat()
        for a, e in pick:
            s = {}
            try:
                s = (B.read(a, date.fromisoformat(str(e.get("board")))) or {}).get("slots", {}).get(e.get("slot")) or {}
            except (ValueError, TypeError, OSError):
                pass
            ab = dict(PLATS).get(e.get("outlet"), "")
            x = float(e.get("x") or 0)
            hotp = e.get("kind") == "hot"
            day_w = "today" if e.get("board") == today else "yesterday" if e.get("board") else ""
            href = f'/?date={e.get("board")}{acct_q.replace("?", "&")}#c-{a.slug}-{str(e.get("slot")).replace(":", "")}'
            rows.append(_row(_mini(_cover_url(_shown(s)), a), esc(_title(s) if s else e.get("title")),
                             f'<i class="tick" style="background:{colour(cfg, a.slug)}"></i>{ICON.get(ab, "")}'
                             f'{esc(NAMES.get(ab, ""))} at {esc(e.get("label"))} · {day_w} {esc(e.get("slot"))}',
                             f'<span class="{"up" if hotp else "warnc"}">{"↑" if hotp else "↓"} {x:.1f}x</span>'
                             f'<small class="same">{"fast start" if hotp else "slow start"}</small>', href=href))
        parts.append(f'<section class="box"><div class="box-h"><span class="label">Early numbers</span>'
                     f'<span class="muted">first hours vs the usual</span></div>{"".join(rows)}</section>')
    probs = []
    for a, r in reps:
        for p in r.get("problems") or []:
            if p.get("words"):
                probs.append((float(p.get("views_lost") or 0), a, p))
    probs.sort(key=lambda x: -x[0])
    if probs:
        rows = []
        for lost, a, p in probs[:3]:
            fix = str(p.get("fix") or "")
            first = fix.split(": ")[0].split(". ")[0]
            rows.append(_row(f'<span class="sev {"bad" if lost >= 1000 else "warn"}">!</span>', esc(p["words"]),
                             f'<i class="tick" style="background:{colour(cfg, a.slug)}"></i>{esc(first)}',
                             f'<span class="down">−{k(lost)}</span><small class="same">views</small>' if lost else "",
                             href="/reports" + acct_q, attrs=f' title="{esc(fix)}"'))
        parts.append(f'<section class="box"><div class="box-h"><span class="label">Needs a look</span>'
                     f'<a href="/reports{esc(acct_q)}">All problems ›</a></div>{"".join(rows)}</section>')
    also = [c for c in rest_chips if not str(c.get("key") or "").startswith(("learn-views", "learn-best",
                                                                               "learn-problems", "checkpoints-"))]
    if also:
        rows = "".join(f'<a class="also {esc(c.get("level") or "")}" href="{esc(_with_acct(str(c["href"]), acct))}">{esc(c["words"])}'
                       f'<span>›</span></a>' if str(c.get("href") or "").startswith("/") else
                       f'<span class="also {esc(c.get("level") or "")}">{esc(c["words"])}</span>' for c in also)
        parts.append(f'<section class="box"><div class="box-h"><span class="label">Also today</span></div>'
                     f'<div class="alsos">{rows}</div></section>')
    return "".join(parts)


# ------------------------------------------------------------------------------------------------------ a lane
def progress(done: int, total: int) -> str:
    pct = 0 if not total else round(done * 100 / total)
    return (f'<span class="line"><b>{done}</b> of {total} posted</span>'
            f'<span class="bar"><i style="width:{pct}%"></i></span>')


def day_stats(acct, d: date) -> Optional[dict]:
    """One account's board day in numbers: clips, posted, missed platforms, views per platform, its best clip."""
    try:
        bd = B.read(acct, d)
    except (OSError, ValueError):
        return None
    if not bd or not bd.get("slots"):
        return None
    n = posted = missed = 0
    views = {"YT": 0, "FB": 0, "IG": 0}
    best = None
    for k_, s in sorted(bd["slots"].items()):
        if not s.get("clip"):
            continue
        n += 1
        v = views_of(s)
        if v:
            posted += 1
        for plat, _ in PLATS[:3]:
            rec = (s.get("outlets") or {}).get(plat) or {}
            if rec.get("gave_up") and not rec.get("capped") and not (rec.get("post_id") or rec.get("video_id")):
                missed += 1
        for ab, x in v.items():
            views[ab] += x
        tv = sum(v.values())
        if v and (best is None or tv > best[0]):
            best = (tv, s)
    if not n:
        return None
    b = None
    if best:
        b = {"title": _title(best[1]), "views": best[0], "slot": best[1].get("slot"), "img": _cover_url(best[1].get("cover"))}
    return {"n": n, "posted": posted, "missed": missed, "views": views, "total": sum(views.values()), "best": b}


def lane_end(acct, d: date, label: str, today: date, acct_q: str) -> str:
    """The small card at a lane's start (the day before) or end (the day after): its numbers, and a link to it."""
    q = f"/?date={d.isoformat()}" + acct_q.replace("?", "&")
    st = day_stats(acct, d)
    if st:
        return (f'<a class="daycard" href="{esc(q)}"><span class="label">{esc(label)}</span>'
                f'<span class="dd">{d:%a, %b} {d.day}</span><b>{k(st["total"])}</b><span>views from {st["n"]} clips</span>'
                f'<span class="go">Open ›</span></a>')
    if d > today:
        ds = acct.day_shift()
        how = ("Nothing dropped yet." if acct.fed_by_hand() else     # the lane's own line says where to drop
               f"Made today from {ds[0]}. Ready to look at by {ds[1]}." if ds else
               "Made tonight from 21:00. Ready to look at by 06:30.")
        return (f'<a class="daycard flat" href="{esc(q)}"><span class="label">{esc(label)}</span>'
                f'<span class="dd">{d:%a, %b} {d.day}</span><span>{esc(how)}</span>'
                f'<span class="go">Open ›</span></a>' if d == today + timedelta(days=1) else "")
    return ""


# ------------------------------------------------------------------------------------------------------ calendar
def month_days(month: date) -> List[Optional[date]]:
    first = month.replace(day=1)
    nxt = (first + timedelta(days=32)).replace(day=1)
    cells: List[Optional[date]] = [None] * first.weekday()
    d = first
    while d < nxt:
        cells.append(d)
        d += timedelta(days=1)
    while len(cells) % 7:
        cells.append(None)
    return cells


def month_data(accts: list, month: date) -> Dict[str, Dict[str, dict]]:
    """{iso day: {slug: day_stats}} for every day of the month that has a board."""
    out: Dict[str, Dict[str, dict]] = {}
    for d in [x for x in month_days(month) if x]:
        for a in accts:
            st = day_stats(a, d)
            if st:
                out.setdefault(d.isoformat(), {})[a.slug] = st
    return out


def mini(accts: list, day: date) -> str:
    """The month under the date button: {iso: [views, missed]} for the day's month (JSON)."""
    data = month_data(accts, day)
    return json.dumps({d: [sum(x["total"] for x in v.values()), sum(x["missed"] for x in v.values())]
                       for d, v in data.items()})


def calendar_page(cfg: C.Config, now: datetime, accts: list, month: date, tok: str, css: str, tabs_html: str,
                  switch_html: str, acct_q: str) -> str:
    """The Calendar tab: each day of the month, its views and each account's clips; a day opens on a hover or a tap
    (posted, views per platform, its best clip, what missed), and links to that day on Clips."""
    data = month_data(accts, month)
    today = now.date()
    tops = [sum(x["total"] for x in v.values()) for v in data.values()] or [1]
    top = max(tops) or 1
    cells = []
    for d in month_days(month):
        if d is None:
            cells.append('<div class="cell empty"></div>')
            continue
        v = data.get(d.isoformat(), {})
        tot = sum(x["total"] for x in v.values())
        bar = "".join(f'<i style="flex:{x["total"]};background:{colour(cfg, s)}"></i>' for s, x in v.items() if x["total"])
        lines = ""
        for s, x in v.items():
            miss = f'<em>{x["missed"]} missed</em>' if x["missed"] else ""
            lines += f'<span><i class="tick" style="background:{colour(cfg, s)}"></i>{x["n"]} clips{miss}</span>'
        plan = ('<span class="plan">Made tonight from 21:00</span>' if d == today + timedelta(days=1) and not v else "")
        cls = " today" if d == today else "" if v else " out"
        cells.append(f'<a class="cell{cls}" data-day="{d.isoformat()}" href="/?date={d.isoformat()}{esc(acct_q.replace("?", "&"))}">'
                     f'<span class="dtop"><span class="dn">{d.day}{"<span class=tag>Today</span>" if d == today else ""}</span>'
                     f'{f"<span class=tot>{k(tot)}</span>" if v else ""}</span>'
                     f'{f"<span class=cbar style=width:{max(4, tot * 100 // top)}%>{bar}</span>" if v else ""}'
                     f'<span class="cacc">{lines}</span>{plan}</a>')
    names = {a.slug: a.name for a in accts}
    legend = "".join(f'<span><i class="tick" style="background:{colour(cfg, a.slug)}"></i>{esc(a.name)}</span>' for a in accts)
    prev_m = (month.replace(day=1) - timedelta(days=1)).replace(day=1)
    next_m = (month.replace(day=1) + timedelta(days=32)).replace(day=1)
    mq = acct_q.replace("?", "&")
    pop = {d: {s: dict(x, name=names.get(s, s), colour=colour(cfg, s), logo=logo(next(a for a in accts if a.slug == s), "s"))
               for s, x in v.items()} for d, v in data.items()}
    body = (f'<div class="toolbar"><h1>{month:%B %Y}</h1><a class="btn icon" href="/calendar?month={prev_m:%Y-%m}{esc(mq)}" '
            f'aria-label="Month before">‹</a><a class="btn icon" href="/calendar?month={next_m:%Y-%m}{esc(mq)}" '
            f'aria-label="Month after">›</a><span class="sp"></span><span class="legend">{legend}'
            f'<span class="muted">The number is the views on that day\'s clips</span></span></div>'
            f'<div class="calwrap"><div class="cal-grid">'
            + "".join(f'<div class="cal-dow">{w}</div>' for w in ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"))
            + "".join(cells) + '</div></div><div class="pop" id="pop"></div>')
    js = CAL_JS.replace("__POP__", json.dumps(pop).replace("</", "<\\/")).replace("__ICON__", json.dumps(ICON)).replace(
        "__ACCTQ__", json.dumps(mq))
    return ("<!doctype html><html lang=en><head><meta charset=utf-8>"
            "<meta name=viewport content='width=device-width,initial-scale=1'>"
            f"<title>Clip Review · Calendar</title><style>{css}</style></head><body class=v3>"
            f"<header><h1>Clip Review</h1>{tabs_html}{switch_html}</header>"
            f"<main class=calmain>{body}</main><script>{js}</script></body></html>")


CAL_JS = r"""
const POP=__POP__, ICON=__ICON__, AQ=__ACCTQ__;
const pop=document.getElementById('pop');
const kk=n=>{n=+n||0;return n>=1e6?(n/1e6).toFixed(1)+'M':n>=1e4?Math.round(n/1e3)+'K':n>=1e3?(n/1e3).toFixed(1).replace(/\.0$/,'')+'K':String(Math.round(n))};
const esc=s=>String(s==null?'':s).replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
function show(cell){
  const d=cell.dataset.day,v=POP[d];if(!v){pop.classList.remove('open');return}
  const dt=new Date(d+'T12:00:00'),tot=Object.values(v).reduce((s,x)=>s+x.total,0);
  pop.innerHTML='<h4>'+dt.toLocaleDateString('en-US',{weekday:'long',month:'long',day:'numeric'})+'</h4><div class="sub">'+kk(tot)+' views on this day\'s clips, counted now</div>'+
    Object.values(v).map(x=>'<div class="pacc">'+x.logo+'<span class="nm">'+esc(x.name)+'</span><span class="tv">'+kk(x.total)+'</span>'+
      '<div class="pl"><span>'+x.posted+' of '+x.n+' posted</span>'+['YT','FB','IG'].map(p=>'<span>'+ICON[p]+kk(x.views[p])+'</span>').join('')+'</div>'+
      (x.missed?'<div class="mx">'+x.missed+' platform post'+(x.missed>1?'s':'')+' missed</div>':'')+
      (x.best?'<div class="best">'+(x.best.img?'<img src="'+esc(x.best.img)+'" alt="">':'')+'<div><span>Best: '+esc(x.best.title)+'</span><span class="muted">'+esc(x.best.slot)+' · '+kk(x.best.views)+' views</span></div></div>':'')+
      '</div>').join('')+'<a class="btn wide" href="/?date='+d+AQ+'">Open this day on Clips</a>';
  pop.classList.add('open');
  const r=cell.getBoundingClientRect(),w=pop.offsetWidth,h=pop.offsetHeight;
  if(innerWidth<760){pop.style.left='8px';pop.style.top=(innerHeight-h-8)+'px';return}
  let x=r.right+8;if(x+w>innerWidth-8)x=r.left-w-8;
  pop.style.left=Math.max(8,x)+'px';pop.style.top=Math.max(8,Math.min(innerHeight-h-8,r.top))+'px';
}
let last=null;
const grid=document.querySelector('.cal-grid');
grid.addEventListener('mouseover',e=>{const c=e.target.closest('.cell[data-day]');if(c&&c!==last){last=c;show(c)}});
grid.addEventListener('focusin',e=>{const c=e.target.closest('.cell[data-day]');if(c)show(c)});
grid.addEventListener('click',e=>{const c=e.target.closest('.cell[data-day]');   // a phone: the first tap shows the day
  if(c&&innerWidth<760&&!(pop.classList.contains('open')&&last===c)){e.preventDefault();last=c;show(c)}});
grid.addEventListener('mouseleave',()=>{setTimeout(()=>{if(!pop.matches(':hover')){pop.classList.remove('open');last=null}},120)});
pop.addEventListener('mouseleave',()=>pop.classList.remove('open'));
document.addEventListener('keydown',e=>{if(e.key==='Escape')pop.classList.remove('open')});
"""


def legend() -> str:
    """What a tile's platform squares mean."""
    def sq(cls: str, ab: str) -> str:
        return f'<i class="p {cls}">{ICON[ab]}</i>'
    return (f'<span class="legend"><span>{sq("ok", "YT")}Posted</span><span>{sq("up", "YT")}Not yet</span>'
            f'<span>{sq("held", "YT")}Waiting</span><span>{sq("bad", "YT")}Missed</span><span>{sq("hand", "TT")}By hand</span></span>')

CSS = r"""
/* ---------------- the 10-29 look (look.py): Clips and Calendar wear body.v3 ---------------- */
body.v3{background:var(--card)}
body.v3 header{height:56px;padding:0 24px;gap:22px;background:var(--card);flex-wrap:nowrap}
body.v3 header h1{font-size:16px;font-weight:700;letter-spacing:-.01em;white-space:nowrap}
body.v3 .tabs a{height:55px;padding:0 2px;margin:0 8px;font-weight:500}
body.v3 .switch a{height:55px}
body.v3 main{padding:0;max-width:none;margin:0}
body.v3 section{margin:0}
body.v3 button.go{background:var(--ink);border-color:var(--ink);color:var(--card)}
body.v3 :focus-visible{outline-color:var(--ink)}
.label{font-size:11px;font-weight:700;letter-spacing:.08em;text-transform:uppercase;color:var(--sub)}
.muted{color:var(--sub)}
svg.i{width:13px;height:13px;display:block;fill:currentColor;flex:0 0 auto}
.btn{display:inline-flex;align-items:center;justify-content:center;gap:6px;height:32px;padding:0 12px;border:1px solid var(--line);border-radius:6px;background:var(--card);color:var(--ink);text-decoration:none;font-size:13px;font-weight:500;white-space:nowrap}
.btn:hover{border-color:var(--sub)} .btn.icon{width:32px;padding:0} .btn.wide{width:100%;margin-top:10px}
.kpis{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));border-bottom:1px solid var(--line)}
.kpi{padding:14px 24px;border-right:1px solid var(--line);min-width:0}
.kpi:last-child{border-right:0}
.kpi button{all:unset;display:block;cursor:pointer;width:100%}
.kpi:has(button):hover{background:var(--mute)}
.kpi b{display:block;font-size:26px;font-weight:600;letter-spacing:-.02em;line-height:1.15;font-variant-numeric:tabular-nums}
.kpi span{color:var(--sub);font-size:12.5px}
.kpi.need b{color:var(--warn)} .kpi.bad b{color:var(--bad)} .kpi.okk b{color:var(--ok)}
.breaking{display:flex;gap:12px;align-items:baseline;padding:10px 24px;border-bottom:1px solid var(--line);font-size:13px}
.breaking:empty{display:none}
.breaking .tag{background:none;padding:0;color:var(--bad);font-weight:700;font-size:11px;letter-spacing:.08em;text-transform:uppercase;line-height:inherit}
.breaking .txt{flex:1;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.breaking details{position:relative;white-space:nowrap}
.breaking summary{color:var(--ink);font-weight:500;font-size:13px}
.breaking details ul{position:absolute;right:0;top:26px;z-index:12;width:min(520px,90vw);background:var(--card);border:1px solid var(--line);border-radius:8px;box-shadow:0 12px 32px rgba(14,23,38,.14);margin:0;padding:6px 14px;list-style:none;white-space:normal}
.breaking details li{padding:7px 0;border-top:1px solid var(--line)} .breaking details li:first-child{border-top:0}
.wrap{display:grid;grid-template-columns:minmax(0,1fr) 352px;align-items:start}
.lanes{min-width:0;border-right:1px solid var(--line)}
.lanes-h{display:flex;align-items:center;gap:16px;padding:12px 24px;border-bottom:1px solid var(--line);flex-wrap:wrap}
.legend{display:flex;gap:14px;align-items:center;color:var(--sub);font-size:12px;flex-wrap:wrap}
.legend span{display:inline-flex;align-items:center;gap:6px}
.lanes-h .seg{margin-left:auto}
.seg{display:inline-flex;border:1px solid var(--line);border-radius:6px;overflow:hidden}
.seg button{height:30px;border:0;border-left:1px solid var(--line);border-radius:0;background:var(--card);color:var(--sub);font-weight:500}
.seg button:first-child{border-left:0}
.seg button[aria-pressed=true]{background:var(--ink);color:var(--card)}
/* a lane: the account's column, then its clips in time order */
.lane{display:grid;grid-template-columns:188px minmax(0,1fr);border-bottom:1px solid var(--line)}
.lane > .who{padding:16px 16px 16px 24px;border-right:1px solid var(--line);min-width:0}
.lane .acct-head{flex-direction:column;align-items:flex-start;gap:7px;min-height:0}
.lane .acct-head h2,.lane .needs-head h2{font-size:15px;font-weight:600;line-height:1.25}
.lane .acct-head .counts{font-size:12px;line-height:1.4}
.lane .acct-head .plinks{flex-wrap:wrap;gap:8px;font-size:12px}
.lane .acct-head .grow{display:none}
.lane .acct-head .line{font-size:12.5px;color:var(--sub)} .lane .acct-head .line b{color:var(--ink)}
.bar{display:block;width:100%;height:4px;border-radius:2px;background:var(--line);overflow:hidden}
.bar i{display:block;height:100%;background:var(--ink)}
.logo{width:40px;height:40px;border-radius:50%;object-fit:cover;flex:0 0 auto;background:var(--mute);border:1px solid var(--line);display:inline-flex;align-items:center;justify-content:center;font-size:11px;font-weight:700;color:var(--sub)}
.logo.s{width:18px;height:18px;font-size:7px}
.lane-main{min-width:0;display:flex;flex-direction:column}
.lane-row{display:flex;gap:10px;overflow-x:auto;padding:14px 24px 14px 16px;align-items:flex-start;scrollbar-width:thin}
.lane-row .grid{display:flex;gap:10px;flex:0 0 auto;align-items:flex-start}
.lane-row .grid:empty{display:none}
.lane .routes{margin:0;padding:0 24px 12px 16px;font-size:12px}
.lane .empties{padding:0 24px 12px 16px;color:var(--sub);font-size:12.5px}
.lane .empties:empty,.lane .routes:empty{display:none}
#needs.lane{grid-template-columns:1fr}
#needs.lane > .who{border-right:0;padding:12px 24px 0}
#needs .needs-head{gap:6px 14px;margin:0;min-height:0}
#needs .needs-head h2{font-size:15px}
#needs .needs-head .health{font-size:12px;color:var(--sub)}
#needs .lane-row{padding-left:24px}
.lanes > #casts{padding:14px 24px;border-bottom:1px solid var(--line)}
#needs .needs-head .n{color:var(--warn)}
#needs:not(:has(.card)){display:none}
/* a tile: the card, folded; a tap opens the same card on the right (its buttons work as they always did). The cover
   stays clean: the time, length and state sit in a row under it */
.lane-row .card:not(.open){width:176px;flex:0 0 auto;position:relative;cursor:pointer;display:flex;flex-direction:column;border-radius:6px}
.lane-row .card:not(.open):hover{border-color:var(--sub)}
.lane-row .card:not(.open) video{order:1;aspect-ratio:3/4;max-height:none;height:auto;object-fit:cover;pointer-events:none;background:var(--mute)}
.lane-row .card:not(.open) video::-webkit-media-controls,.lane-row .card:not(.open) video::-webkit-media-controls-enclosure{display:none!important}
.lane-row .card:not(.open) .top{order:2;display:flex;flex-wrap:wrap;align-items:center;gap:2px 6px;padding:8px 10px 0;min-height:0;font-size:12px;color:var(--sub);pointer-events:none}
.lane-row .card:not(.open) .top .slot{order:1;font-size:12.5px;font-weight:600;color:var(--ink)}
.lane-row .card:not(.open) .top .len{order:2;display:inline;color:var(--sub)}
.lane-row .card:not(.open) .top .len::before{content:"· "}
.lane-row .card:not(.open) .top .tag{order:3;margin-left:auto;font-size:11px;padding:0 6px;line-height:18px}
.lane-row .card:not(.open) .top .tag.t-ok,.lane-row .card:not(.open) .top .tag.t-done,.lane-row .card:not(.open) .top .tag.t-mute{display:none}
.lane-row .card:not(.open) .top .when{order:4;margin-left:auto;font-size:12px;color:var(--sub);text-align:right;max-width:100%;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.lane-row .card:not(.open) .top .when.warn{color:var(--warn)} .lane-row .card:not(.open) .top .when.bad{color:var(--bad)}
.lane-row .card:not(.open) .top .where{order:9;flex-basis:100%;font-size:11.5px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.lane-row .card:not(.open) .top .pf{position:absolute;left:10px;right:10px;bottom:9px;display:flex;gap:3px;align-items:center}
.lane-row .card:not(.open) .body{order:3;padding:4px 10px 38px;gap:0}
.lane-row .card:not(.open) .body > :not(.title){display:none}
#needs-grid .card:not(.open) .title,.lane-row .card:not(.open) .title{font-size:13px;font-weight:500;line-height:1.32;display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden;min-height:34px}
#needs-grid .card.open .title{font-size:16px}
.lane-row .card.next:not(.open){border-color:var(--ink);box-shadow:inset 0 0 0 1px var(--ink)}
.lane-row .card.next:not(.open)::before{content:"NEXT · " attr(data-when);display:block;height:24px;line-height:24px;background:var(--ink);color:var(--card);font-size:11px;font-weight:700;letter-spacing:.05em;padding:0 10px;white-space:nowrap;overflow:hidden}
.lane-row .card.next:not(.open) .top .when{display:none}
.lane-row .card:not(.open):has(.top .t-ready),.lane-row .card:not(.open):has(.top .t-warn){border-color:var(--warn);box-shadow:inset 0 0 0 1px var(--warn)}
.lane-row .card:not(.open):has(.top .t-bad),.lane-row .card:not(.open):has(.pf .p.bad){border-color:var(--bad)}
.card .top .len,.card .top .pf{display:none}
.p{width:22px;height:18px;border-radius:3px;display:inline-flex;align-items:center;justify-content:center;flex:0 0 auto}
.p.ok{background:var(--ok);color:#fff} .p.bad{background:var(--bad);color:#fff}
.p.up{color:var(--sub);box-shadow:inset 0 0 0 1px var(--line)} .p.hand{color:var(--sub);box-shadow:inset 0 0 0 1px var(--line);opacity:.75}
.p.held{color:var(--warn);box-shadow:inset 0 0 0 1px var(--warn)}
.pf .pv{margin-left:auto;font-size:12px;font-weight:600;color:var(--sub);font-variant-numeric:tabular-nums}
.lanes.list .lane-row .card:not(.open) video{display:none}
.lanes.list .lane-row .card:not(.open){width:208px}
/* the open card: on the right, whole */
.lane-row .card.open{position:fixed;top:0;right:0;bottom:0;width:min(480px,100vw);z-index:40;overflow-y:auto;border:0;border-left:1px solid var(--line);border-radius:0;box-shadow:-16px 0 40px rgba(14,23,38,.14);background:var(--card)}
.lane-row .card.open .top{position:sticky;top:0;z-index:3;background:var(--card);border-bottom:1px solid var(--line);padding:12px 60px 12px 16px}
.lane-row .card.open video{width:auto;max-width:100%;max-height:62vh;margin:14px auto 0;border-radius:6px}
.lane-row .card.open .body{padding:14px 18px 28px}
.lane-row .card.open .title{font-size:16px}
.ghost{width:176px;flex:0 0 auto;align-self:stretch;min-height:300px;border:1px dashed var(--line);border-radius:6px}
.scrim{position:fixed;inset:0;background:rgba(14,23,38,.22);z-index:39;display:none}
.drawer-x{position:fixed;top:10px;right:12px;z-index:42;display:none;width:36px;height:36px;padding:0;font-size:20px;line-height:1}
body.drawer-on .scrim{display:block} body.drawer-on .drawer-x{display:inline-flex;align-items:center;justify-content:center}
/* the day before and the day after, at each end of a lane */
.daycard{width:132px;flex:0 0 auto;align-self:stretch;min-height:120px;border:1px dashed var(--line);border-radius:6px;padding:12px;display:flex;flex-direction:column;gap:5px;color:var(--sub);font-size:12.5px;text-decoration:none}
.daycard:hover{border-color:var(--sub);background:var(--mute)}
.daycard .dd{color:var(--ink);font-weight:600}
.daycard b{font-size:22px;font-weight:600;color:var(--ink);letter-spacing:-.02em;line-height:1.1}
.daycard .go{margin-top:auto;font-weight:600;color:var(--ink)}
/* the right column */
.rail{position:sticky;top:57px;max-height:calc(100vh - 57px);overflow-y:auto;scrollbar-width:thin}
.rail .box{border-bottom:1px solid var(--line);padding-bottom:6px}
.box-h{display:flex;align-items:center;justify-content:space-between;padding:14px 20px 6px}
.box-h a{font-size:12px;font-weight:500;color:var(--sub);text-decoration:none} .box-h a:hover{color:var(--ink)}
.yhead{display:flex;align-items:baseline;gap:10px;padding:2px 20px 10px}
.yhead b{font-size:28px;font-weight:600;letter-spacing:-.02em}
.rrow{display:grid;grid-template-columns:36px minmax(0,1fr) auto;gap:12px;align-items:center;padding:8px 20px;min-height:58px;color:inherit;text-decoration:none}
.rrow:hover{background:var(--mute)}
.rrow + .rrow{border-top:1px solid var(--line)}
.rrow .logo{width:36px;height:36px}
.rrow .mini{width:36px;height:48px;border-radius:4px;object-fit:cover;background:var(--mute);display:block}
.rrow .mid{min-width:0}
.rrow .t{display:block;font-size:13px;font-weight:500;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.rrow .m{display:flex;align-items:center;gap:6px;font-size:12px;color:var(--sub);margin-top:2px;white-space:nowrap;overflow:hidden}
.rrow .v{text-align:right;font-size:14px;font-weight:600;font-variant-numeric:tabular-nums;white-space:nowrap}
.rrow .v small{display:block;font-size:11.5px;font-weight:600}
.tick{width:3px;height:13px;border-radius:2px;display:inline-block;flex:0 0 auto}
.up{color:var(--ok)} .down{color:var(--bad)} .same{color:var(--sub)} .warnc{color:var(--warn)}
.sev{width:36px;height:36px;border-radius:6px;display:flex;align-items:center;justify-content:center;font-weight:700;font-size:15px}
.sev.bad{background:var(--badbg);color:var(--bad)} .sev.warn{background:var(--warnbg);color:var(--warn)}
.alsos{display:flex;flex-direction:column;padding:0 20px 8px}
.also{display:flex;justify-content:space-between;padding:8px 0;border-top:1px solid var(--line);font-size:13px;color:var(--ink);text-decoration:none}
.also:first-child{border-top:0} .also.warn{color:var(--warn)} .also.bad{color:var(--bad)}
.calbox{display:none}
/* the month under the date button */
.days{position:relative}
.dbtn{display:inline-flex;gap:8px}
.minical{position:absolute;top:40px;right:0;width:316px;background:var(--card);border:1px solid var(--line);border-radius:8px;box-shadow:0 12px 32px rgba(14,23,38,.16);padding:14px;display:none;z-index:30}
.minical.open{display:block}
.mc-head{display:flex;align-items:center;justify-content:space-between;margin-bottom:10px;font-weight:600}
.mc-grid{display:grid;grid-template-columns:repeat(7,1fr);gap:2px}
.mc-grid .dow{font-size:10.5px;color:var(--sub);text-align:center;padding-bottom:4px;font-weight:600}
.mc-day{height:40px;border-radius:6px;display:flex;flex-direction:column;align-items:center;justify-content:center;font-size:12.5px;font-weight:500;position:relative;color:var(--ink);text-decoration:none}
.mc-day small{font-size:9.5px;color:var(--sub);font-variant-numeric:tabular-nums}
.mc-day:hover{background:var(--mute)}
.mc-day.today{box-shadow:inset 0 0 0 1px var(--ink)}
.mc-day.sel{background:var(--ink);color:var(--card)} .mc-day.sel small{color:var(--card);opacity:.75}
.mc-day.off{color:var(--sub)}
.mc-day.miss::after{content:"";position:absolute;bottom:4px;width:12px;height:2px;border-radius:1px;background:var(--bad)}
.mc-foot{display:flex;justify-content:space-between;margin-top:10px;font-size:12px;color:var(--sub)}
/* the Calendar tab */
.calmain .toolbar{display:flex;align-items:center;gap:10px;padding:16px 24px;border-bottom:1px solid var(--line);flex-wrap:wrap}
.calmain h1{margin:0 6px 0 0;font-size:18px;font-weight:600}
.calmain .sp{flex:1}
.calwrap{padding:0 24px 24px}
.cal-grid{display:grid;grid-template-columns:repeat(7,minmax(0,1fr));border-left:1px solid var(--line);border-top:1px solid var(--line);margin-top:16px}
.cal-dow{padding:8px 10px;font-size:11px;font-weight:700;letter-spacing:.08em;text-transform:uppercase;color:var(--sub);border-right:1px solid var(--line);border-bottom:1px solid var(--line)}
.cell{min-height:118px;border-right:1px solid var(--line);border-bottom:1px solid var(--line);padding:8px 10px;display:flex;flex-direction:column;gap:6px;color:inherit;text-decoration:none;min-width:0}
a.cell:hover{background:var(--mute)}
.cell.out .dn{color:var(--sub)}
.cell .dtop{display:flex;justify-content:space-between;align-items:baseline}
.cell .dn{font-size:13px;font-weight:600}
.cell .tot{font-size:14px;font-weight:600;font-variant-numeric:tabular-nums}
.cell.today{box-shadow:inset 0 0 0 2px var(--ink)}
.cell .tag{font-size:10px;font-weight:700;letter-spacing:.06em;text-transform:uppercase;background:var(--ink);color:var(--card);padding:1px 5px;border-radius:3px;margin-left:6px}
.cbar{height:6px;display:flex;gap:2px}
.cbar i{display:block;height:100%;border-radius:2px}
.cacc{display:flex;flex-direction:column;gap:3px;font-size:12px;color:var(--sub)}
.cacc span{display:flex;align-items:center;gap:6px;white-space:nowrap;overflow:hidden}
.cacc em{font-style:normal;color:var(--bad);font-weight:600;margin-left:auto}
.cell .plan{font-size:12px;color:var(--sub);margin-top:auto}
.pop{position:fixed;width:340px;background:var(--card);border:1px solid var(--line);border-radius:8px;box-shadow:0 14px 36px rgba(14,23,38,.16);z-index:30;display:none;padding:14px 16px}
.pop.open{display:block}
.pop h4{margin:0 0 2px;font-size:15px;font-weight:600}
.pop .sub{font-size:12px;color:var(--sub);margin-bottom:10px}
.pacc{border-top:1px solid var(--line);padding:10px 0;display:grid;grid-template-columns:22px 1fr auto;gap:4px 10px;align-items:center}
.pacc .nm{font-weight:600;font-size:13px} .pacc .tv{font-weight:600;font-variant-numeric:tabular-nums}
.pacc .pl{grid-column:2/4;display:flex;gap:14px;font-size:12px;color:var(--sub)}
.pacc .pl span{display:inline-flex;align-items:center;gap:5px;font-variant-numeric:tabular-nums}
.pacc .best{grid-column:2/4;display:flex;gap:10px;align-items:center;margin-top:4px}
.pacc .best img{width:28px;height:40px;border-radius:3px;object-fit:cover}
.pacc .best div{font-size:12px;line-height:1.35;min-width:0}
.pacc .best div span{display:block;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.pacc .mx{grid-column:2/4;font-size:12px;color:var(--bad);font-weight:600}
@media (max-width:1180px){.wrap{grid-template-columns:minmax(0,1fr) 320px}}
@media (max-width:900px){.wrap{grid-template-columns:1fr}.lanes{border-right:0;border-bottom:1px solid var(--line)}.rail{position:static;max-height:none}}
@media (max-width:760px){
  body.v3 header{height:auto;flex-wrap:wrap;padding:8px 16px;gap:8px 12px;position:static}
  body.v3 .tabs{order:3;width:100%;overflow-x:auto}
  body.v3 .tabs a{height:40px}
  body.v3 .switch{order:4;width:100%;overflow-x:auto;border-left:0;padding-left:0}
  body.v3 .switch a{height:36px}
  .kpis{grid-template-columns:repeat(2,minmax(0,1fr))}
  .kpi{padding:12px 16px;border-bottom:1px solid var(--line)} .kpi:nth-child(2n){border-right:0}
  .kpi b{font-size:22px}
  .breaking,.lanes-h{padding-left:16px;padding-right:16px}
  .lane{grid-template-columns:1fr}
  .lane > .who{border-right:0;padding:14px 16px 0}
  .lane .acct-head{flex-direction:row;flex-wrap:wrap;align-items:center}
  .lane .acct-head .logo{width:32px;height:32px}
  .lane .acct-head .bar{flex-basis:100%}
  .lane-row{padding:12px 16px 14px}
  .lane-row .card:not(.open){width:150px}
  .box-h,.rrow,.yhead,.alsos{padding-left:16px;padding-right:16px}
  .minical{position:fixed;top:auto;bottom:8px;left:8px;right:8px;width:auto}
  .calwrap{padding:0 8px 16px} .calmain .toolbar{padding:12px 16px}
  .cal-dow{padding:6px 2px;text-align:center;font-size:10px}
  .cell{min-height:64px;padding:6px 4px;gap:4px;align-items:center}
  .cell .dtop{flex-direction:column;align-items:center;gap:2px}
  .cell .tot{font-size:11px}
  .cell .cacc,.cell .plan,.cell .tag{display:none}
  .cbar{width:100%!important;height:4px}
  .pop{width:auto;right:8px}
}
"""

JS = r"""
/* the 10-29 look (look.py): open a tile on the right, mark what goes next, the month under the date */
let OPEN=null;
const scrim=document.getElementById('scrim'), dx=document.getElementById('drawer-x');
function ghost(){document.querySelectorAll('.ghost').forEach(g=>g.remove());
  const c=OPEN&&document.getElementById(OPEN);if(!c||!c.classList.contains('open'))return;
  const g=document.createElement('div');g.className='ghost';c.parentNode.insertBefore(g,c)}
function openCard(c){if(OPEN===c.id)return;closeCard();OPEN=c.id;c.classList.add('open');document.body.classList.add('drawer-on');ghost();c.scrollTop=0}
function closeCard(){const c=OPEN&&document.getElementById(OPEN);if(c)c.classList.remove('open');OPEN=null;
  document.body.classList.remove('drawer-on');document.querySelectorAll('.ghost').forEach(g=>g.remove());
  document.querySelectorAll('.card video').forEach(v=>{if(!v.paused)v.pause()})}
document.addEventListener('click',ev=>{
  const c=ev.target.closest('.lane-row .card');
  if(!c||c.classList.contains('open'))return;
  ev.preventDefault();openCard(c)},true);
if(scrim)scrim.addEventListener('click',closeCard);
if(dx)dx.addEventListener('click',closeCard);
document.addEventListener('keydown',ev=>{if(ev.key==='Escape'&&OPEN){closeCard()}});
function markNext(){
  document.querySelectorAll('.lane-row').forEach(row=>{
    const cards=[...row.querySelectorAll('.grid .card')];cards.forEach(c=>c.classList.remove('next'));
    const n=cards.find(c=>{const t=c.querySelector('.top .tag');return t&&(t.classList.contains('t-ok')&&/Scheduled/.test(t.textContent))});
    if(n){n.classList.add('next');const w=n.querySelector('.top .when');n.dataset.when=w?w.textContent.trim():''}
  })}
function afterApply(){
  if(OPEN){const c=document.getElementById(OPEN);if(c){c.classList.add('open');ghost()}else closeCard()}
  markNext()}
let firstScroll=true;
function scrollLanes(){if(!firstScroll)return;firstScroll=false;
  document.querySelectorAll('.lane-row').forEach(row=>{
    const n=row.querySelector('.card.next')||[...row.querySelectorAll('.grid .card')].pop();if(!n)return;
    const prev=n.previousElementSibling||n.parentElement.previousElementSibling;
    const x=(prev?prev.offsetLeft:n.offsetLeft)-row.offsetLeft-16;if(x>0)row.scrollLeft=x})}
markNext();scrollLanes();
function openFromHash(){const id=decodeURIComponent(location.hash.slice(1));if(!id)return;
  const c=document.getElementById(id);if(c&&c.closest('.lane-row')){c.scrollIntoView({block:'center',inline:'center'});openCard(c)}}
openFromHash();addEventListener('hashchange',openFromHash);
document.querySelectorAll('.kpi button[data-go]').forEach(b=>b.addEventListener('click',()=>{
  const want=b.dataset.go;
  const c=[...document.querySelectorAll('.lane-row .card')].find(x=>want==='need'
    ?(x.closest('#needs')||x.querySelector('.top .t-ready'))
    :x.querySelector('.pf .p.bad, .top .t-bad'));
  if(c){c.scrollIntoView({block:'center',inline:'center',behavior:'smooth'});openCard(c)}
  else toast(want==='need'?'Nothing needs you right now':'Nothing missed a platform today')}));
const lanesEl=document.querySelector('.lanes'), segEl=document.getElementById('dens');
function setDens(d){if(!lanesEl)return;lanesEl.classList.toggle('list',d==='list');
  if(segEl)segEl.querySelectorAll('button').forEach(b=>b.setAttribute('aria-pressed',String(b.dataset.d===d)));
  try{localStorage.setItem('clip-review-dens',d)}catch(e){}}
if(segEl){segEl.addEventListener('click',ev=>{const b=ev.target.closest('button');if(b)setDens(b.dataset.d)});
  try{setDens(localStorage.getItem('clip-review-dens')||'pic')}catch(e){setDens('pic')}}
const mc=document.getElementById('minical'), mcb=document.getElementById('dbtn');
function drawMini(){if(!mc)return;const d0=new Date(DAY+'T12:00:00'),y=d0.getFullYear(),m=d0.getMonth();
  const first=new Date(y,m,1),lead=(first.getDay()+6)%7,days=new Date(y,m+1,0).getDate(),today=new Date();
  const iso=d=>y+'-'+String(m+1).padStart(2,'0')+'-'+String(d).padStart(2,'0');
  const tiso=today.getFullYear()+'-'+String(today.getMonth()+1).padStart(2,'0')+'-'+String(today.getDate()).padStart(2,'0');
  const kk=n=>n>=1e4?Math.round(n/1e3)+'K':n>=1e3?(n/1e3).toFixed(1).replace(/\.0$/,'')+'K':String(n);
  let h='<div class="mc-head"><span>'+d0.toLocaleDateString('en-US',{month:'long',year:'numeric'})+'</span></div><div class="mc-grid">'+
    ['M','T','W','T','F','S','S'].map(x=>'<span class="dow">'+x+'</span>').join('');
  for(let i=0;i<lead;i++)h+='<span></span>';
  for(let d=1;d<=days;d++){const k=iso(d),v=MINI[k];
    h+='<a class="mc-day'+(k===DAY?' sel':'')+(k===tiso?' today':'')+(v?'':' off')+(v&&v[1]?' miss':'')+'" href="'+dayHref(k)+'">'+d+'<small>'+(v?kk(v[0]):'')+'</small></a>'}
  mc.innerHTML=h+'</div><div class="mc-foot"><span>Small number = views</span><span><span style="color:var(--bad)">—</span> missed a post</span></div>'}
if(mcb&&mc){mcb.addEventListener('click',ev=>{ev.stopPropagation();if(!mc.innerHTML)drawMini();mc.classList.toggle('open')});
  document.addEventListener('click',ev=>{if(!mc.contains(ev.target))mc.classList.remove('open')})}
"""
