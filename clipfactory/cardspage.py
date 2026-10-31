"""Clip Review's Cards tab (/cards): the day's cards and story reposts, each to look at and sign.

Per account: every card of the day as it will post (a story at 9:16, a carousel as a strip you swipe), its kind,
time, targets, status and where its picture and words come from ("speaker verified by Andrew 10-19"); Approve,
Decline (with reasons), Make again, Change time, and Approve all; the standing rule for story reposts (sign, pause);
and what the Instagram stories test found. The page reads files only (state/<slug>/cards/...); /cards/data gives the
same main as JSON every 60 seconds and the page redraws only when it changed and nothing is open.

Every click that acts is a person on this page (review._from_page) and confirms the exact words and where they go:

    card-approve       {account, date, id, seen}            seen = the card's hash the page showed
    card-decline       {account, date, id, reasons, note}
    card-remake        {account, date, id}                  06:26 to 20:45 only (it draws)
    card-time          {account, date, id, at: "HH:MM"}
    card-approve-all   {account, date, seen: {id: hash}}
    card-rule-sign     {account, fields: {platforms, which, per_day, delay_min}, version}
    card-rule-pause    {account}
"""
from __future__ import annotations

import html
import json
from datetime import date, datetime, timedelta
from typing import Dict, List, Optional, Tuple

from . import cards as CD
from . import config as C
from . import socialui as SX
from . import switches as SW

BACK_DAYS = 7


def esc(s: object) -> str:
    return html.escape(str(s), quote=True)


CSS = """
.cd-intro{color:var(--sub);font-size:13.5px;margin:0}
.cd-days{display:flex;align-items:center;gap:6px;flex-wrap:wrap}
.cd-days .cd-day{font-weight:600;font-size:14px;padding:0 6px}
.cd-acct{display:flex;flex-direction:column;gap:12px}
.cd-acct>.sx-head{justify-content:flex-start}
.cd-sw{color:var(--sub);font-size:12.5px;flex-basis:100%}
.cd-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(280px,1fr));gap:16px;align-items:start}
.cd-card .top{flex-wrap:wrap}
.cd-prev{background:#000;display:flex;justify-content:center}
.cd-story img{display:block;height:auto;width:auto;max-width:100%;max-height:min(620px,70vh);aspect-ratio:9/16}
.cd-strip{display:flex;justify-content:flex-start;gap:2px;overflow-x:auto;scroll-snap-type:x mandatory;-webkit-overflow-scrolling:touch}
.cd-strip img{display:block;flex:0 0 82%;width:82%;height:auto;aspect-ratio:4/5;object-fit:cover;scroll-snap-align:start}
.cd-n{color:var(--sub);font-size:12px}
.cd-words{white-space:pre-wrap;overflow-wrap:anywhere;font-size:13px}
.cd-words ol{margin:0;padding-left:18px}
.cd-outs{list-style:none;margin:0;padding:8px 0 0;border-top:1px solid var(--line);display:grid;gap:5px;font-size:12.5px}
.cd-outs li{display:flex;gap:8px;align-items:baseline}
.cd-outs b{font-weight:600;flex:0 0 118px}
.cd-outs a{color:var(--blue);text-decoration:none}
.cd-outs .bad{color:var(--bad)}.cd-outs .ok{color:var(--ok)}
.cd-why,.cd-time{display:flex;flex-direction:column;gap:8px;padding:10px;border:1px solid var(--line);border-radius:6px;background:var(--bg)}
.cd-why[hidden],.cd-time[hidden]{display:none}
.cd-r{height:28px;padding:0 9px;font-size:12.5px;font-weight:550;color:var(--sub)}
.cd-r[aria-pressed=true]{color:var(--ink);background:var(--mute);border-color:var(--ink)}
.cd-note,.cd-at{font:inherit;font-size:13px;height:32px;padding:0 8px;border:1px solid var(--line);border-radius:6px;background:var(--card);color:var(--ink)}
.cd-note{width:100%}
.cd-side{display:grid;grid-template-columns:minmax(0,1fr) minmax(0,1fr);gap:16px;align-items:start}
.cd-box{background:var(--card);border:1px solid var(--line);border-radius:8px;padding:14px;display:flex;flex-direction:column;gap:10px;min-width:0}
.cd-box h3{margin:0;font-size:14px;font-weight:650}
.cd-row{display:flex;gap:10px;flex-wrap:wrap;align-items:center}
.cd-row label{display:inline-flex;gap:6px;align-items:center;font-size:13px;max-width:100%;min-width:0}
.cd-row select{max-width:100%;min-width:0;flex:1 1 auto}
.cd-row input[type=checkbox]{width:18px;height:18px;margin:0}
.cd-row input[type=number]{width:70px}
.cd-stories{list-style:none;margin:0;padding:0;display:grid;gap:4px;font-size:12.5px}
.cd-go{color:var(--sub)}
.cd-skips summary{cursor:pointer;font-size:13.5px;font-weight:600;min-height:40px;display:flex;align-items:center}
.cd-skips b{font-weight:600}
.cd-empty{color:var(--sub);font-size:13.5px;padding:28px 16px;text-align:center}
@media (max-width:900px){.cd-side{grid-template-columns:minmax(0,1fr)}}
@media (max-width:600px){.cd-grid{grid-template-columns:minmax(0,1fr)}
.cd-story img{max-height:58vh}.cd-strip img{flex-basis:88%;width:88%}
.cd-r{height:40px;min-width:40px}.cd-note,.cd-at{height:40px;font-size:16px}
.cd-outs b{flex-basis:96px}.cd-row input[type=number]{width:84px}
.cd-row label{min-height:40px}.cd-row label.cd-which{flex:1 1 100%}.cd-row select{width:100%}}
"""

STATUS_TAG = {"draft": ("Nowhere to post it", "t-warn"), "ready": ("Waits for Approve", "t-ready"),
              "approved": ("Signed", "t-ok"), "scheduled": ("Queued", "t-ok"), "posted": ("Posted", "t-done"),
              "declined": ("Declined", "t-mute"), "failed": ("Did not post", "t-bad"),
              "expired": ("Expired", "t-mute"), "skipped": ("Skipped", "t-mute")}
REMAKE_ASK = "Make this {what} for {name} again? It is drawn again from its sources, and Approve is needed again."
OUT_WORDS = {"queued": "queued", "sending": "on its way", "done": "posted", "practice": "practised (nothing sent)",
             "failed": "failed, it tries again", "held": "waits (a hold)", "skipped": "did not go",
             "refused": "did not go", "gave_up": "gave up"}


# ------------------------------------------------------------------------------------------------------ reading
def _day(cfg: C.Config, q: dict, now: datetime) -> date:
    today = CD._local(cfg, now).date()
    try:
        d = date.fromisoformat(str((q or {}).get("date") or "")[:10])
    except ValueError:
        d = today
    lo, hi = today - timedelta(days=BACK_DAYS), today + timedelta(days=1)
    return min(max(d, lo), hi)


def _day_label(d: date, today: date) -> str:
    if d == today:
        return "Today, " + d.strftime("%b ") + str(d.day)
    if d == today + timedelta(days=1):
        return "Tomorrow, " + d.strftime("%b ") + str(d.day)
    if d == today - timedelta(days=1):
        return "Yesterday, " + d.strftime("%b ") + str(d.day)
    return d.strftime("%a %b ") + str(d.day)


def _accts(cfg: C.Config, acct: str) -> List[C.Account]:
    return [a for a in SX.own_accounts(cfg, acct) if a.slug in cfg.accounts]


def _q(day: date, acct: str) -> str:
    return f"?date={day.isoformat()}" + (f"&acct={esc(acct)}" if acct and acct != "all" else "")


def _img(path: str, alt: str) -> str:
    return f'<img src="{esc(SX.file_url(path))}" alt="{esc(alt)}" loading="lazy">'


def _time(cfg: C.Config, card: dict) -> str:
    t = CD.post_time(card)
    if card.get("kind") == "quote" and not card.get("post_at"):
        lo, hi = ((card.get("after") or {}).get("minutes") or [60, 90])[:2]
        return f"{lo} to {hi} min after its {(card.get('after') or {}).get('slot', '')} clip is live"
    return CD._local(cfg, t).strftime("%H:%M") if t else "?"


def _words_html(card: dict) -> str:
    w = card.get("words") or {}
    k = card.get("kind")
    if k == "news":                              # every word that goes: the slides' own, then each post's caption
        hero = w.get("hero") or {}
        lead = (f"“{hero.get('quote', '')}” {hero.get('name', '')}" if hero.get("kind") == "quote"
                else f"{hero.get('figure', '')} {hero.get('label', '')}")
        caps = "".join(f"\n\n<b>{esc(p.capitalize())} words</b>\n{esc(t)}"
                       for p, t in (w.get("caption") or {}).items() if t)
        return (f'<div class="cd-words"><b>{esc(w.get("headline", ""))}</b>\n{esc(w.get("line", ""))}'
                f"\n\n{esc(lead.strip())}\n{esc(w.get('story', ''))}"
                f"\n\nSources: {esc(', '.join(w.get('sources') or []))}{caps}</div>")
    if k == "quote":
        who = " ".join(x for x in (w.get("name"), w.get("title") and f"({str(w['title']).title()})") if x)
        return (f'<div class="cd-words">“{esc(w.get("quote", ""))}”'
                + (f"\n{esc(who)}" if who else "") + "</div>")
    if k in CD.CAROUSEL_KINDS:
        items = "".join(f"<li>{esc(i.get('title', ''))}" + (f" <span class=cd-n>{esc(i.get('host'))}</span>"
                                                            if i.get("host") else "")
                        + (f" <span class=cd-n>{esc(CD._views_words(int(i['views'])))}</span>" if
                           isinstance(i.get("views"), int) else "") + "</li>"
                        for i in w.get("items") or [] if isinstance(i, dict))
        return f'<div class="cd-words"><ol>{items}</ol></div>' if items else ""
    if k == "countdown":
        d = w.get("days")
        return f'<div class="cd-words">{"OUT TODAY" if d == 0 else esc(f"{d} days to GTA 6")}</div>'
    if k == "clip_of_day":
        return (f'<div class="cd-words">{esc(w.get("hook", ""))}'
                + (f"\n{esc(w.get('credit'))}" if w.get("credit") else "") + "</div>")
    return ""


def _source_line(card: dict) -> str:
    src = card.get("source") or {}
    k = card.get("kind")
    if k == "news":
        pic = src.get("picture") or {}
        return (f"Written by the {src.get('by') or 'news desk'}; facts checked on {len(src.get('checked') or [])} "
                f"pages; picture from the atlas ({pic.get('credit') or pic.get('atlas') or '?'}, "
                f"{pic.get('licence') or '?'})")
    if k == "quote":
        q = src.get("quote") or {}
        cs = q.get("clip_s") or []
        at = f", at {cs[0]:.0f}s of the clip" if cs else ""
        return f"Words from the captions{at}; {src.get('line') or 'speaker verified'}; frame of the source"
    if k == "countdown":
        art = src.get("art")
        ground = ("Master frame of " if art == "master" else "Frame of ") + (src.get("line") or "yesterday's clip") \
            if art in ("master", "clip") else "The account's own look alone (its lockup on the kit's flat ink)"
        return f"{ground}; launch day " + str(src.get("launch") or CD.LAUNCH)
    if k == "clip_of_day":
        art = src.get("art")
        if art == "ink":
            return ("No clean frame of the source of " + (src.get("line") or "our clip")
                    + " on this Mac: the lockup and the kit's flat ink; words from its signed copy")
        return (("Clean frame of the source of " if art == "master" else "Frame of ") + (src.get("line") or "our clip")
                + "; words from its signed copy")
    if k in CD.CAROUSEL_KINDS:
        n = len(src.get("slots") or [])
        return f"{n} posted clips; titles from their signed copy" + ("; views from the stats read"
                                                                     if k == "week_best" else "")
    return ""


def _outs_html(card: dict) -> str:
    rows = []
    for t in card.get("targets") or []:
        r = (card.get("outlets") or {}).get(t) or {}
        st = str(r.get("state") or "")
        if not st:
            continue
        cls = "ok" if st in ("done", "practice") else "bad" if st in CD.GIVEN_UP else ""
        words = OUT_WORDS.get(st, st)
        link = f' <a href="{esc(r["url"])}" target="_blank" rel="noopener">open</a>' if r.get("url") else ""
        why = f" ({esc(r.get('why'))})" if r.get("why") and st in CD.GIVEN_UP else ""
        rows.append(f'<li><b>{esc(CD.TARGET_WORDS.get(t, t))}</b><span class="{cls}">{esc(words)}{why}</span>{link}'
                    "</li>")
    return f'<ul class="cd-outs">{"".join(rows)}</ul>' if rows else ""


def _go_words(cfg: C.Config, a: C.Account, card: dict) -> str:
    """'about 09:15 (the 08:45 Reel keeps 30 minutes clear)' when the card cannot go at its own time; else ''."""
    if card.get("kind") == "quote" and not card.get("post_at"):
        return ""
    t = CD.post_time(card)
    go, why = CD.go_time(cfg, a, card)
    if t is None or go is None or go <= t:
        return ""
    return f"about {CD._local(cfg, go).strftime('%H:%M')} ({why})"


def _ask_approve(cfg: C.Config, a: C.Account, card: dict) -> str:
    where = " and ".join(CD.TARGET_WORDS.get(t, t) for t in card.get("targets") or []) or "nowhere"
    go = _go_words(cfg, a, card)
    return (f"Sign this {CD.KIND_WORDS.get(card.get('kind'), card.get('kind'))} for {a.name}? It goes to {where} at "
            f"{_time(cfg, card)}" + (f", {go}" if go else "") + ". The picture and words go exactly as shown here.")


def note_words(cfg: C.Config, note: object) -> str:
    """A card's note as the page shows it: its UTC stamp as the local HH:MM, then its words."""
    n = str(note or "")
    head, _, rest = n.partition(" ")
    t = CD._when(head) if rest and len(head) >= 19 and head[4:5] == "-" and "T" in head else None
    if t is None:
        return n
    return f"{CD._local(cfg, t).strftime('%H:%M')} {rest}"


def _card_html(cfg: C.Config, a: C.Account, day: date, card: dict, now: datetime) -> str:
    cid = str(card.get("id") or "")
    st = str(card.get("status") or "")
    label, cls = STATUS_TAG.get(st, (st, "t-mute"))
    if st == "posted" and all(((card.get("outlets") or {}).get(t) or {}).get("state") == "practice"
                              for t in card.get("targets") or []):
        label, cls = "Practised", "t-mute"
    imgs = [i for i in (card.get("files") or {}).get("images") or [] if isinstance(i, dict)]
    alt = CD.KIND_WORDS.get(card.get("kind"), cid)
    if card.get("kind") in CD.CAROUSEL_KINDS:
        prev = f'<div class="cd-prev cd-strip" tabindex="0" aria-label="{esc(alt)}, {len(imgs)} slides">' + "".join(
            _img(i["path"], f"{alt}, slide {n}") for n, i in enumerate(imgs, 1)) + "</div>"
    else:
        prev = f'<div class="cd-prev cd-story">{_img(imgs[0]["path"], alt) if imgs else ""}</div>'
    where = ", ".join(CD.TARGET_WORDS.get(t, t) for t in card.get("targets") or []) or "no target"
    notes = [note_words(cfg, n) for n in card.get("notes") or []]
    btns = []
    h = CD.card_hash(card)
    kind_w = CD.KIND_WORDS.get(card.get("kind"), cid)
    if st in CD.OPEN and card.get("targets"):
        btns.append(f'<button class="go" data-op="card-approve" data-seen="{esc(h)}" '
                    f'data-ask="{esc(_ask_approve(cfg, a, card))}">Approve</button>')
    if st in CD.OPEN + CD.SIGNED:           # a queued card too: what waits in the queue is refused when it comes up
        btns.append('<button data-op="card-decline-open">Decline</button>')
    if st not in ("scheduled", "posted"):
        btns.append(f'<button data-op="card-remake" data-ask="{esc(REMAKE_ASK.format(what=kind_w, name=a.name))}">'
                    "Make again</button>")
    if st != "posted" and card.get("kind") != "quote":
        btns.append('<button data-op="card-time-open">Change time</button>')
    go = _go_words(cfg, a, card) if st in CD.OPEN + CD.SIGNED else ""
    reasons = "".join(f'<button class="cd-r" type="button" data-r="{esc(c)}" aria-pressed="false">{esc(lbl)}</button>'
                      for c, lbl in CD.DECLINE)
    cur = CD._local(cfg, CD.post_time(card)).strftime("%H:%M") if CD.post_time(card) else ""
    return (f'<article class="card cd-card" data-id="{esc(cid)}" data-key="{esc(h)}" '
            f'data-name="{esc(kind_w)} for {esc(a.name)}">'
            f'<div class="top"><span class="slot">{esc(_time(cfg, card))}</span>'
            f'<span class="tag {cls}">{esc(label)}</span><span class="when">{esc(where)}</span></div>'
            f"{prev}<div class=body><div class=title>{esc(alt)}"
            + (f' <span class="cd-n">{len(imgs)} slides</span>' if len(imgs) > 1 else "") + "</div>"
            f'<div class="meta">{esc(_source_line(card))}</div>'
            + (f'<div class="meta cd-go">Goes {esc(go)}</div>' if go else "") + _words_html(card)
            + (f'<div class="note">{esc(notes[-1])}</div>' if notes else "")
            + _outs_html(card)
            + (f'<div class="btns">{"".join(btns)}</div>' if btns else "")
            + f'<div class="cd-why" hidden><div class="dx-h">Why not this card?</div>'
            f'<div class="dx-chips">{reasons}</div>'
            f'<input class="cd-note" type="text" maxlength="200" placeholder="A note (optional)" aria-label="Note">'
            f'<div class="btns"><button class="go" data-op="card-decline" data-ask="Decline this {esc(kind_w)} for '
            f'{esc(a.name)}? It will not post to {esc(where)}.">Decline</button>'
            f'<button data-op="card-cancel">Cancel</button></div></div>'
            f'<div class="cd-time" hidden><label class="dx-h" for="t-{esc(a.slug)}-{esc(cid)}">New time (08:00 to '
            f'23:30)</label><input class="cd-at" id="t-{esc(a.slug)}-{esc(cid)}" type="time" min="08:00" '
            f'max="23:30" value="{esc(cur)}"><div class="btns"><button class="go" data-op="card-time">Save the '
            f'time</button><button data-op="card-cancel">Cancel</button></div></div>'
            "</div></article>")


def _rule_html(cfg: C.Config, a: C.Account) -> str:
    rule = CD.read_rule(a)
    signed = CD.rule_signed(rule)
    st = rule.get("state")
    so = rule.get("signoff") or {}
    state = ("signed by " + str(so.get("by") or "") + " " + str(so.get("at") or "")[:10] if signed else
             "paused" if st == "paused" else "changed after it was signed: sign it again" if st == "signed"
             else "not signed yet")
    mode = SW.mode(a, "cards.story_repost")
    plats = rule.get("platforms") or []
    d0, d1 = (rule.get("delay_min") or [30, 60])[:2]
    checks = "".join(f'<label><input type="checkbox" name="platforms" value="{p}"{" checked" if p in plats else ""}>'
                     f"{p.capitalize()}</label>" for p in ("facebook", "instagram"))
    which = "".join(f'<option value="{v}"{" selected" if rule.get("which") == v else ""}>{w}</option>'
                    for v, w in (("all", "Every signed clip"),
                                 ("top", "Only the coach's top clips (7 or more out of 10)")))
    return (f'<div class="cd-box" data-x-form data-version="{esc(rule.get("version"))}" data-name="{esc(a.name)}">'
            f"<h3>Standing rule: story reposts</h3><div class=sx-muted>{esc(CD.rule_words(rule))}</div>"
            f"<div class=sx-muted>Rule: {esc(state)}. Switch: cards.story_repost is {esc(mode)}.</div>"
            f'<div class="cd-row">{checks}</div>'
            f'<div class="cd-row"><label class="cd-which">Which <select name="which">{which}</select></label></div>'
            f'<div class="cd-row"><label>A day <input type="number" name="per_day" min="1" max="6" '
            f'value="{esc(rule.get("per_day"))}"></label><label>Minutes after <input type="number" name="d0" '
            f'min="30" max="240" value="{esc(d0)}"></label><label>to <input type="number" name="d1" min="30" '
            f'max="240" value="{esc(d1)}"></label></div>'
            '<div class="btns"><button class="go" data-op="card-rule-sign">Sign this rule</button>'
            + (f'<button data-op="card-rule-pause" data-ask="Pause story reposts for {esc(a.name)}? None is queued '
               'until the rule is signed again.">Pause</button>' if st == "signed" else "") + "</div></div>")


def _probe_html(a: C.Account) -> str:
    from . import capabilities as CAP
    pr = CAP.probe_result(a, "ig_stories") or {}
    val = pr.get("value") if isinstance(pr.get("value"), dict) else None
    if val is None:
        words = f"Not tested yet. Run factory.py cards probe {a.slug} (it makes one unpublished test container)."
    else:
        words = f"{val.get('words') or ('They work.' if val.get('ok') else 'Refused.')} (tested {str(pr.get('at'))[:10]})"
    return f'<div class="cd-box"><h3>Instagram stories on this account</h3><div class=sx-muted>{esc(words)}</div></div>'


def _stories_html(cfg: C.Config, a: C.Account, day: date) -> str:
    st = CD.read_board(a, day).get("stories") or {}
    if not st:
        return ""
    rows = []
    for key, r in sorted(st.items()):
        if not isinstance(r, dict):
            continue
        slot, _, plat = key.partition("|")
        link = f' <a href="{esc(r["url"])}" target="_blank" rel="noopener">open</a>' if r.get("url") else ""
        why = f": {r.get('why')}" if r.get("why") else ""
        rows.append(f"<li>{esc(slot)} {esc(plat.capitalize())} story: "
                    f"{esc(OUT_WORDS.get(str(r.get('state')), str(r.get('state'))) + why)}{link}</li>")
    return f'<div class="cd-box"><h3>Story reposts this day</h3><ul class="cd-stories">{"".join(rows)}</ul></div>'


def _not_made_html(made: dict) -> str:
    """Why a card was not made this day (the day's make kept its reasons): 'No quote card for 07:00: ...'."""
    rows = [r for r in (made or {}).get("skipped") or [] if isinstance(r, dict) and r.get("words")]
    if not rows:
        return ""
    items = "".join(f"<li><b>{esc(CD.KIND_WORDS.get(r.get('kind'), r.get('kind') or 'Card'))}</b> "
                    f"{esc(str(r.get('words')).strip())}</li>" for r in rows[:20])
    return (f'<details class="cd-box cd-skips"><summary>Not made this day ({len(rows)})</summary>'
            f'<ul class="cd-stories">{items}</ul></details>')


def _switch_words(a: C.Account) -> str:
    m = {k: SW.mode(a, k) for k in ("cards.make", "cards.post", "cards.story_repost")}
    if all(v == SW.OFF for v in m.values()):
        return (f"Cards are off for {a.name}. They are switched on the Log tab, or: factory.py social on {a.slug} "
                "cards.make")
    return (f"Made: {m['cards.make']}. Posted: {m['cards.post']}"
            + (" (practice: the log shows what would go, nothing is sent)" if m["cards.post"] == "practice" else "")
            + f". Story reposts: {m['cards.story_repost']}.")


def main_html(cfg: C.Config, acct: str, day: date, now: datetime) -> str:
    today = CD._local(cfg, now).date()
    prev_d, next_d = day - timedelta(days=1), day + timedelta(days=1)
    lo, hi = today - timedelta(days=BACK_DAYS), today + timedelta(days=1)
    step = (f'<a class="step" href="/cards{_q(prev_d, acct)}" aria-label="The day before">&lsaquo;</a>' if prev_d >= lo
            else '<span class="step" aria-hidden="true">&lsaquo;</span>')
    step2 = (f'<a class="step" href="/cards{_q(next_d, acct)}" aria-label="The day after">&rsaquo;</a>' if next_d <= hi
             else '<span class="step" aria-hidden="true">&rsaquo;</span>')
    head = (f'<div class="sx-head"><h2>Cards</h2><div class="cd-days">{step}<span class="cd-day">'
            f'{esc(_day_label(day, today))}</span>{step2}'
            + (f'<a class="today" href="/cards{_q(today, acct)}">Today</a>' if day != today else "") + "</div></div>"
            '<p class="cd-intro">Stills and stories from real footage, in each account\'s own look. Nothing posts '
            "until you Approve it here.</p>")
    out = [head]
    for a in _accts(cfg, acct):
        bd = CD.read_board(a, day)
        cards = sorted((c for c in (bd.get("cards") or {}).values() if isinstance(c, dict)),
                       key=lambda c: (str(c.get("post_at") or c.get("at") or ""), str(c.get("id") or "")))
        ready = [c for c in cards if c.get("status") in CD.OPEN and c.get("targets")]
        all_btn = ""
        if len(ready) >= 2:
            seen = {str(c["id"]): CD.card_hash(c) for c in ready}
            lines = "; ".join(f"{CD.KIND_WORDS.get(c.get('kind'), c['id'])} at {_time(cfg, c)} to "
                              + " and ".join(CD.TARGET_WORDS.get(t, t) for t in c.get("targets") or [])
                              for c in ready)
            cap = CD.day_cap(a)
            ask = (f"Sign all {len(ready)} cards for {a.name}, exactly as shown? {lines}. Each platform takes {cap} card "
                   f"{'post' if cap == 1 else 'posts'} a day at the most: the earliest are signed first, and any past "
                   f"that stay unsigned.")
            all_btn = (f'<button class="go" data-op="card-approve-all" data-seen="{esc(json.dumps(seen))}" '
                       f'data-ask="{esc(ask)}">Approve all {len(ready)}</button>')
        made = bd.get("made") or {}
        if cards:
            grid = '<div class="cd-grid">' + "".join(_card_html(cfg, a, day, c, now) for c in cards) + "</div>"
        else:
            grid = ('<div class="cd-empty">'
                    + ("No card for this day: " + (f"made at {CD._local(cfg, CD._when(made.get('at'))).strftime('%H:%M')}, "
                                                   "nothing had what a card needs." if made.get("at") else
                                                   "cards are made once a day after 06:26.")) + "</div>")
        out.append(f'<section class="cd-acct" data-acct="{esc(a.slug)}" data-date="{esc(day.isoformat())}">'
                   f'<div class="sx-head"><h2>{esc(a.name)}</h2>{all_btn}'
                   f'<span class="cd-sw">{esc(_switch_words(a))}</span></div>{grid}{_not_made_html(made)}'
                   f'<div class="cd-side">{_rule_html(cfg, a)}{_probe_html(a)}{_stories_html(cfg, a, day)}</div>'
                   "</section>")
    if len(out) == 1:
        out.append('<div class="cd-empty">No account of yours works on this machine.</div>')
    return "".join(out)


# ------------------------------------------------------------------------------------------------------ the page
JS = r"""
(() => {
const main = document.getElementById('cd-main'); if (!main) return;
const DAY = main.dataset.day, ACCT = SX.ACCT;
function busy() {
  const f = document.activeElement;
  return !!(main.querySelector('.cd-why:not([hidden]),.cd-time:not([hidden])') || (f && main.contains(f) && f.matches('input,select,textarea')));
}
const P = SX.poll(() => '/cards/data?date=' + encodeURIComponent(DAY) + (ACCT && ACCT !== 'all' ? '&acct=' + encodeURIComponent(ACCT) : ''), 60000, d => {
  if (d && typeof d.html === 'string' && !busy()) main.innerHTML = d.html;
});
main.addEventListener('click', async ev => {
  const chip = ev.target.closest('.cd-r');
  if (chip) { chip.setAttribute('aria-pressed', String(chip.getAttribute('aria-pressed') !== 'true')); return; }
  const b = ev.target.closest('button[data-op]'); if (!b) return;
  const op = b.dataset.op, card = b.closest('[data-id]'), sec = b.closest('[data-acct]');
  if (!sec) return;
  const body = {op, account: sec.dataset.acct, date: sec.dataset.date};
  if (card) body.id = card.dataset.id;
  if (op === 'card-decline-open' || op === 'card-time-open') {
    const box = card.querySelector(op === 'card-decline-open' ? '.cd-why' : '.cd-time');
    card.querySelectorAll('.cd-why,.cd-time').forEach(x => { if (x !== box) x.hidden = true; });
    box.hidden = !box.hidden; return;
  }
  if (op === 'card-cancel') { card.querySelectorAll('.cd-why,.cd-time').forEach(x => { x.hidden = true; }); return; }
  let ask = b.dataset.ask || '';
  if (op === 'card-approve') body.seen = b.dataset.seen;
  if (op === 'card-approve-all') { try { body.seen = JSON.parse(b.dataset.seen || '{}'); } catch (e) { body.seen = {}; } }
  if (op === 'card-decline') {
    body.reasons = [...card.querySelectorAll('.cd-r[aria-pressed=true]')].map(x => x.dataset.r);
    body.note = (card.querySelector('.cd-note') || {}).value || '';
  }
  if (op === 'card-time') {
    body.at = (card.querySelector('.cd-at') || {}).value || '';
    ask = 'Move the ' + (card.dataset.name || 'card') + ' to ' + body.at + '? A signed or queued card needs Approve again.';
  }
  if (op === 'card-rule-sign') {
    const f = b.closest('[data-x-form]');
    const plats = [...f.querySelectorAll('input[name=platforms]:checked')].map(x => x.value);
    const which = f.querySelector('select[name=which]').value;
    const per = parseInt(f.querySelector('input[name=per_day]').value, 10);
    const d0 = parseInt(f.querySelector('input[name=d0]').value, 10), d1 = parseInt(f.querySelector('input[name=d1]').value, 10);
    body.fields = {platforms: plats, which, per_day: per, delay_min: [d0, d1]};
    body.version = f.dataset.version;
    ask = 'Sign the story repost rule for ' + (f.dataset.name || 'this account') + '? Each Reel live on ' + (plats.map(p => p[0].toUpperCase() + p.slice(1)).join(' and ') || 'no platform') +
      ' goes to that platform\'s story ' + d0 + ' to ' + d1 + ' minutes later, as the same signed bytes, 60 seconds at the most, ' + per + ' a day at the most, for ' +
      (which === 'all' ? 'every signed clip' : 'the clips the coach scored 7 or more out of 10') + '.';
  }
  if (!SX.ask(ask)) return;
  const r = await SX.act(body, b);
  if (r.ok) P.now();
});
})();
"""


def page(cfg: C.Config, tok: str, base_css: str, tabs_html: str, switch_html: str, acct: str, q: dict) -> str:
    now = CD._now(cfg)
    day = _day(cfg, q, now)
    main = f'<div id="cd-main" data-day="{esc(day.isoformat())}">{main_html(cfg, acct, day, now)}</div>'
    return SX.shell(cfg, "cards", "Cards", tok, base_css, tabs_html, switch_html, acct, main, css=CSS, js=JS)


def data(cfg: C.Config, q: dict, now: Optional[datetime] = None) -> dict:
    now = CD._now(cfg, now)
    acct = str((q or {}).get("acct") or "all")
    day = _day(cfg, q, now)
    return SX.stamp({"day": day.isoformat(), "html": main_html(cfg, acct, day, now)})


# ------------------------------------------------------------------------------------------------------ clicks
WHAT = {"card-approve": "sign a card", "card-decline": "decline a card", "card-remake": "make a card again",
        "card-time": "change a card's time", "card-approve-all": "sign cards", "card-rule-sign": "sign a rule",
        "card-rule-pause": "pause a rule"}


def _acct_of(cfg: C.Config, body: dict) -> C.Account:
    slug = str(body.get("account") or "")
    mine = {a.slug: a for a in _accts(cfg, "all")}
    if slug not in mine:
        raise SystemExit(f"{slug or 'that account'}: not one of your accounts on this machine")
    return mine[slug]


def _date_of(body: dict) -> date:
    try:
        return date.fromisoformat(str(body.get("date") or "")[:10])
    except ValueError:
        raise SystemExit("which day (date) is missing") from None


def act(cfg: C.Config, body: dict, page: bool = False, now: Optional[datetime] = None) -> Tuple[bool, str]:
    op = str(body.get("op") or "")
    if op not in WHAT:
        raise SystemExit(f"{op or 'that'}: not a click the Cards tab knows")
    SX.need_page(page, WHAT[op])
    a = _acct_of(cfg, body)
    by = str(body.get("by") or (cfg.approvers[0] if cfg.approvers else "Andrew"))
    if op == "card-rule-sign":
        fields = body.get("fields") if isinstance(body.get("fields"), dict) else {}
        return True, CD.sign_rule(cfg, a, fields, body.get("version"), by, now)
    if op == "card-rule-pause":
        return True, CD.pause_rule(cfg, a, by, now)
    day = _date_of(body)
    if op == "card-approve-all":
        seen = body.get("seen")
        if not isinstance(seen, dict) or not seen:
            raise SystemExit("which cards (seen) is missing")
        return True, CD.approve_all(cfg, a, day, {str(k): str(v) for k, v in seen.items()}, by, now)
    cid = str(body.get("id") or "")
    if not cid:
        raise SystemExit("which card (id) is missing")
    if op == "card-approve":
        return True, CD.approve(cfg, a, day, cid, str(body.get("seen") or ""), by, now)
    if op == "card-decline":
        reasons = body.get("reasons") or []
        if not isinstance(reasons, list) or not all(isinstance(r, str) for r in reasons):
            raise SystemExit("reasons: a list of words")
        return True, CD.decline(cfg, a, day, cid, reasons, str(body.get("note") or ""), by, now)
    if op == "card-remake":
        return True, CD.remake(cfg, a, day, cid, by, now)
    if op == "card-time":
        return True, CD.set_time(cfg, a, day, cid, str(body.get("at") or ""), by, now)
    raise SystemExit(f"{op}: not a click the Cards tab knows")
