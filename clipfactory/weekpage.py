"""Clip Review's Week page, under Clips: a read-only week of every account's clips, cards and stories with their status.

    http://127.0.0.1:8781/week?date=YYYY-MM-DD&acct=<slug>

Monday first. On a computer it is a grid, accounts down the side and the seven days across; each cell lists the
day's items by time, each with a status chip (Posted, Scheduled, Needs you, Problem, Empty, Practice) and a link to
the clip on the Clips page or to the Cards tab. On a phone the days stack, today first. A strip on top counts the
month per account: posted, scheduled, to sign, empty, failed, and cards posted.

It reads files only: the day boards (state/<slug>/YYYY-MM-DD.json, the Clips page's own words for each slot) and the
cards boards (state/<slug>/cards/YYYY-MM-DD.json). Clients and switched off accounts are not in it. The page re-reads
/week/data every minute and redraws only when something changed. Nothing here acts: no click is sent from it.
"""
from __future__ import annotations

import calendar as _cal
import urllib.parse
from datetime import date, datetime, timedelta, timezone
from typing import Dict, List, Optional, Tuple

from . import config as C
from . import socialui as SX
from . import store
from . import switches as SW

esc = SX.esc

STATUS = {"posted": "Posted", "scheduled": "Scheduled", "needs": "Needs you", "problem": "Problem", "empty": "Empty",
          "practice": "Practice"}
CARD_KINDS = {"quote": "Quote card", "docket": "Docket carousel", "week_best": "Week's best", "countdown":
              "Countdown story", "clip_of_day": "Clip of the day", "story": "Story repost", "story_repost":
              "Story repost"}
STORY_KINDS = ("story", "story_repost")
CARD_STATUS = {"posted": "posted", "scheduled": "scheduled", "approved": "scheduled", "ready": "needs",
               "draft": "needs", "failed": "problem", "expired": "problem", "declined": "empty",
               "skipped": "empty"}
TARGET_WORDS = {"facebook_story": "Facebook story", "instagram_story": "Instagram story",
                "facebook_feed": "Facebook post", "instagram_carousel": "Instagram carousel"}


# ------------------------------------------------------------------------------------------------------ helpers
def _now(cfg: C.Config, now: Optional[datetime] = None) -> datetime:
    t = now or cfg.now()
    if t.tzinfo is None:
        t = t.replace(tzinfo=cfg.tz())
    return t


def _day(cfg: C.Config, raw, now: datetime) -> date:
    try:
        return C.parse_day(str(raw), cfg) if raw else now.astimezone(cfg.tz()).date()
    except (SystemExit, ValueError):
        return now.astimezone(cfg.tz()).date()


def monday(d: date) -> date:
    return d - timedelta(days=d.weekday())


def _short(a: C.Account) -> str:
    return str(a.short or (a.raw or {}).get("short") or a.name.split()[0])


def _accounts(cfg: C.Config, acct: str) -> List[C.Account]:
    """The owner's own accounts working here (never a client's, never one switched off), or the one picked."""
    return SX.own_accounts(cfg, acct or "all")


def _pick(cfg: C.Config, raw) -> str:
    raw = str(raw or "")
    return raw if raw in {a.slug for a in _accounts(cfg, "all")} else "all"


def _when(v) -> Optional[datetime]:
    return store._when(v) if v not in (None, "") else None


def _clip_href(a: C.Account, d: date, slot: str) -> str:
    return f"/?date={d.isoformat()}&acct={urllib.parse.quote(a.slug)}#c-{a.slug}-{slot.replace(':', '')}"


def _cards_href(a: C.Account, d: date) -> str:
    return f"/cards?date={d.isoformat()}&acct={urllib.parse.quote(a.slug)}"


def _board(a: C.Account, d: date) -> dict:
    bd = store.read(C.STATE / a.slug / f"{d.isoformat()}.json", {})
    return bd if isinstance(bd, dict) else {}


def _cards_board(a: C.Account, d: date) -> dict:
    bd = store.read(store.path(a.slug, "cards", f"{d.isoformat()}.json"), {})
    return bd if isinstance(bd, dict) else {}


# ------------------------------------------------------------------------------------------------------ one item
def clip_status(cfg: C.Config, a: C.Account, s: dict, now: datetime) -> str:
    """A clip slot in the Week's words, from the Clips page's own reading of it (review._state, _need_why)."""
    from . import board as B
    from . import review as R
    st = s.get("status")
    try:
        if st in B.SIGNED or (st in B.SIGNABLE and s.get("clip")):
            if R._need_why(cfg, a, s, now):
                return "needs"
            words = R._state(cfg, a, s, now)[1]
            if st in B.SIGNABLE:
                return "problem" if R._too_late(s, now) else "needs"
            if words == "Posted":
                return "posted"
            if words == "Not all posted":
                return "problem"
            return "scheduled"
    except (KeyError, TypeError, ValueError, OSError):
        return "problem"
    if st in (B.REFUSED, B.FAILED):
        return "problem"
    return "empty"


def _clip_items(cfg: C.Config, a: C.Account, d: date, now: datetime) -> List[dict]:
    from . import review as R
    out = []
    for k, s in sorted((_board(a, d).get("slots") or {}).items()):
        if not isinstance(s, dict):
            continue
        st = clip_status(cfg, a, s, now)
        try:
            title = R._title(s) if s.get("clip") else ""
        except Exception:  # noqa: BLE001  (a missing words file never hides the slot)
            title = str(s.get("slug") or "").replace("-", " ")
        out.append({"k": "clip", "time": k, "title": title or "No clip", "s": st,
                    "words": STATUS[st], "href": _clip_href(a, d, k)})
    return out


def card_status(a: C.Account, c: dict) -> str:
    st = CARD_STATUS.get(str(c.get("status") or ""), "empty")
    if st in ("posted", "scheduled"):
        key = "cards.story_repost" if str(c.get("kind") or "") in STORY_KINDS else "cards.post"
        try:
            if SW.mode(a, key) == "practice":
                return "practice"
        except Exception:  # noqa: BLE001
            pass
    return st


def _card_items(cfg: C.Config, a: C.Account, d: date) -> List[dict]:
    tz = cfg.tz()
    out = []
    for cid, c in sorted((_cards_board(a, d).get("cards") or {}).items()):
        if not isinstance(c, dict):
            continue
        t = _when(c.get("at"))
        kind = str(c.get("kind") or "")
        st = card_status(a, c)
        targets = ", ".join(TARGET_WORDS.get(str(x), str(x).replace("_", " ")) for x in (c.get("targets") or []))
        out.append({"k": "story" if kind in STORY_KINDS else "card",
                    "time": t.astimezone(tz).strftime("%H:%M") if t is not None else "",
                    "title": CARD_KINDS.get(kind, kind.replace("_", " ").capitalize() or "Card")
                    + (f": {targets}" if targets else ""),
                    "s": st, "words": STATUS[st], "href": _cards_href(a, d)})
    return out


def items(cfg: C.Config, a: C.Account, d: date, now: Optional[datetime] = None) -> List[dict]:
    """Every clip, card and story of the account on that day, by time."""
    now = _now(cfg, now)
    got = _clip_items(cfg, a, d, now) + _card_items(cfg, a, d)
    return sorted(got, key=lambda x: (x["time"] or "99:99", x["k"]))


# ------------------------------------------------------------------------------------------------------ the month
def month_counts(cfg: C.Config, a: C.Account, d: date, now: Optional[datetime] = None) -> Dict[str, int]:
    """The month around d for one account: posted, scheduled, to sign, empty, failed slots, and cards posted."""
    from . import review as R
    now = _now(cfg, now)
    out = {"posted": 0, "scheduled": 0, "review": 0, "empty": 0, "failed": 0, "cards": 0}
    last = _cal.monthrange(d.year, d.month)[1]
    for i in range(1, last + 1):
        day = date(d.year, d.month, i)
        for s in (_board(a, day).get("slots") or {}).values():
            if not isinstance(s, dict):
                continue
            try:
                k = R._slot_kind(cfg, a, s, now)
            except Exception:  # noqa: BLE001
                k = "problem"
            out["failed" if k == "problem" else k] = out.get("failed" if k == "problem" else k, 0) + 1
        for c in (_cards_board(a, day).get("cards") or {}).values():
            if isinstance(c, dict) and c.get("status") == "posted":
                out["cards"] += 1
    return out


# ------------------------------------------------------------------------------------------------------ the data
def week(cfg: C.Config, q: dict, now: Optional[datetime] = None) -> dict:
    """The week for the page: {"monday", "days": [iso], "today", "accounts": [{id, name, short, days: {iso: [item]},
    month}], "prev", "next"}."""
    now = _now(cfg, now)
    acct = _pick(cfg, q.get("acct"))
    start = monday(_day(cfg, q.get("date"), now))
    days = [start + timedelta(days=i) for i in range(7)]
    accts = []
    for a in _accounts(cfg, acct):
        accts.append({"id": a.slug, "name": a.name, "short": _short(a),
                      "days": {d.isoformat(): items(cfg, a, d, now) for d in days},
                      "month": month_counts(cfg, a, start + timedelta(days=3), now)})
    return {"acct": acct, "monday": start.isoformat(), "days": [d.isoformat() for d in days],
            "today": now.astimezone(cfg.tz()).date().isoformat(), "accounts": accts,
            "month": (start + timedelta(days=3)).strftime("%B %Y"),
            "prev": (start - timedelta(days=7)).isoformat(), "next": (start + timedelta(days=7)).isoformat()}


def _href(day: str, acct: str) -> str:
    q = {"date": day}
    if acct and acct != "all":
        q["acct"] = acct
    return "/week?" + urllib.parse.urlencode(q)


def _label(iso: str, today: str) -> str:
    d = date.fromisoformat(iso)
    rel = " · today" if iso == today else ""
    return f"{d.strftime('%a %b')} {d.day}{rel}"


def _item_html(it: dict) -> str:
    return (f'<li class="wk-it wk-{esc(it["k"])}"><a href="{esc(it["href"])}">'
            f'<span class="wk-t">{esc(it["time"])}</span>'
            f'<span class="wk-s wk-s-{esc(it["s"])}">{esc(it["words"])}</span>'
            f'<span class="wk-title">{esc(it["title"])}</span></a></li>')


def _cell(its: List[dict]) -> str:
    if not its:
        return '<p class="wk-none">Nothing</p>'
    return '<ul class="wk-items">' + "".join(_item_html(i) for i in its) + "</ul>"


def _view_html(w: dict) -> str:
    today = w["today"]
    acct = w["acct"]
    nav = (f'<nav class="wk-nav" aria-label="Weeks"><a class="wk-step" href="{esc(_href(w["prev"], acct))}" '
           f'aria-label="The week before">Week before</a><a class="wk-step" href="{esc(_href(today, acct))}">'
           f'This week</a><a class="wk-step" href="{esc(_href(w["next"], acct))}" aria-label="The week after">'
           f'Week after</a></nav>')
    d0, d6 = date.fromisoformat(w["days"][0]), date.fromisoformat(w["days"][6])
    head = (f'<div class="sx-head"><h2>Week of {esc(d0.strftime("%b"))} {d0.day} to {esc(d6.strftime("%b"))} '
            f"{d6.day}</h2>{nav}</div>")
    strip = []
    for a in w["accounts"]:
        m = a["month"]
        strip.append(f'<div class="wk-m"><b>{esc(a["name"])}</b><span>{m["posted"]} posted</span>'
                     f'<span>{m["scheduled"]} scheduled</span><span>{m["review"]} to sign</span>'
                     f'<span>{m["empty"]} empty</span><span class="{"wk-bad" if m["failed"] else ""}">'
                     f'{m["failed"]} failed</span><span>{m["cards"]} card{"" if m["cards"] == 1 else "s"} posted</span></div>')
    month = (f'<section class="wk-month" aria-label="{esc(w["month"])}"><h3>{esc(w["month"])}</h3>'
             + ("".join(strip) or '<p class="sx-muted">No account works on this machine.</p>') + "</section>")
    if not w["accounts"]:
        return head + month
    cols = "".join(f'<th scope="col"{" class=wk-today" if d == today else ""}>{esc(_label(d, today))}</th>'
                   for d in w["days"])
    rows = "".join(f'<tr><th scope="row">{esc(a["name"])}</th>' + "".join(
        f'<td{" class=wk-today" if d == today else ""}>{_cell(a["days"][d])}</td>' for d in w["days"]) + "</tr>"
        for a in w["accounts"])
    grid = (f'<div class="wk-grid"><table><thead><tr><th scope="col">Account</th>{cols}</tr></thead>'
            f"<tbody>{rows}</tbody></table></div>")
    order = list(w["days"])
    if today in order:                              # phones: today first, then the days still to come, then the past
        i = order.index(today)
        order = order[i:] + order[:i]
    stack = []
    for d in order:
        per = "".join(f'<div class="wk-acct"><h4>{esc(a["name"])}</h4>{_cell(a["days"][d])}</div>'
                      for a in w["accounts"])
        stack.append(f'<section class="wk-day{" wk-today" if d == today else ""}" aria-label="{esc(_label(d, today))}">'
                     f"<h3>{esc(_label(d, today))}</h3>{per}</section>")
    return head + month + grid + f'<div class="wk-days">{"".join(stack)}</div>'


def data(cfg: C.Config, q: dict, now: Optional[datetime] = None) -> dict:
    """/week/data: the week's items and the month's counts, and the HTML the page swaps in. Files only."""
    w = week(cfg, dict(q or {}), now)
    return SX.stamp(dict(w, html=_view_html(w)))


CSS = """
body.sx-week main{max-width:1700px}
.wk-nav{display:flex;flex-wrap:wrap;gap:6px}
.wk-step{display:inline-flex;align-items:center;height:32px;padding:0 10px;border:1px solid var(--line);border-radius:6px;background:var(--card);color:var(--ink);text-decoration:none;font-size:13px;white-space:nowrap}
.wk-step:hover{border-color:var(--sub)}
.wk-month,.wk-day{margin:0}
.wk-month{background:var(--card);border:1px solid var(--line);border-radius:8px;padding:12px 16px;display:flex;flex-direction:column;gap:6px}
.wk-month h3{font-size:14px;margin:0}
.wk-m{display:flex;flex-wrap:wrap;gap:4px 14px;font-size:13px;color:var(--sub)}
.wk-m b{color:var(--ink);min-width:120px}
.wk-bad{color:var(--bad);font-weight:600}
.wk-grid{overflow-x:auto;background:var(--card);border:1px solid var(--line);border-radius:8px}
.wk-grid table{width:100%;border-collapse:collapse;table-layout:fixed;min-width:0}
.wk-grid th,.wk-grid td{border-bottom:1px solid var(--line);border-right:1px solid var(--line);padding:8px;vertical-align:top;text-align:left;font-size:12.5px}
.wk-grid th:last-child,.wk-grid td:last-child{border-right:0}
.wk-grid tr:last-child th,.wk-grid tr:last-child td{border-bottom:0}
.wk-grid thead th{color:var(--sub);font-weight:600;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.wk-grid thead th:first-child,.wk-grid tbody th{width:110px}
.wk-grid tbody th{font-weight:600}
.wk-today{background:var(--bluebg)}
.wk-items{list-style:none;margin:0;padding:0;display:flex;flex-direction:column;gap:4px}
.wk-it a{display:flex;flex-wrap:wrap;align-items:center;gap:2px 6px;padding:4px 5px;border-radius:6px;color:var(--ink);text-decoration:none;min-width:0}
.wk-it a:hover{background:var(--bg)}
.wk-t{font-variant-numeric:tabular-nums;color:var(--sub);font-size:12px}
.wk-title{flex:1 1 100%;min-width:0;font-size:12.5px;overflow-wrap:anywhere}
.wk-s{font-size:11px;font-weight:600;padding:0 6px;border-radius:4px;line-height:17px;white-space:nowrap}
.wk-s-posted{color:var(--ok);background:var(--okbg)}
.wk-s-scheduled{color:var(--blue);background:var(--bluebg)}
.wk-s-needs{color:var(--warn);background:var(--warnbg)}
.wk-s-problem{color:var(--bad);background:var(--badbg)}
.wk-s-empty{color:var(--sub);background:var(--mute)}
.wk-s-practice{color:var(--ink);background:var(--mute)}
.wk-card .wk-title,.wk-story .wk-title{color:var(--sub)}
.wk-none{margin:0;color:var(--sub);font-size:12px}
.wk-days{display:none}
@media (max-width:900px){
.wk-grid{display:none}
.wk-days{display:flex;flex-direction:column;gap:12px}
.wk-day{background:var(--card);border:1px solid var(--line);border-radius:8px;padding:12px 14px;display:flex;flex-direction:column;gap:8px}
.wk-day h3{font-size:15px;margin:0}
.wk-day.wk-today{border-color:var(--blue)}
.wk-acct h4{font-size:12.5px;color:var(--sub);margin:0 0 4px;font-weight:600}
.wk-title{flex:1 1 160px}}
@media (max-width:600px){
.wk-step{height:40px;min-width:40px}
.wk-it a{min-height:40px}
.wk-m b{flex-basis:100%}
.wk-month{padding:12px}}
"""

JS = r"""
(() => {
'use strict';
const v = document.getElementById('wk-view');
if (!v) return;
v.dataset.html = v.innerHTML;
SX.poll(() => '/week/data' + location.search, 60000, d => {
  if (!d || typeof d.html !== 'string' || v.dataset.html === d.html) return;
  v.innerHTML = d.html; v.dataset.html = d.html;
});
})();
"""


def page(cfg: C.Config, tok: str, base_css: str, tabs_html: str, switch_html: str, acct: str, q: dict) -> str:
    """The Week page: read only, opened from the Clips calendar's Week link."""
    q = dict(q or {})
    acct = _pick(cfg, acct if acct and acct != "all" else q.get("acct"))
    if acct != "all":
        q["acct"] = acct
    w = week(cfg, q)
    main = f'<div id="wk-view" class="wk-view" aria-live="polite">{_view_html(w)}</div>'
    return SX.shell(cfg, "week", "Week", tok, base_css, tabs_html, switch_html, acct, main, css=CSS, js=JS)
