"""Clip Review's Log tab: every outward action with its result, how sending is going, what is paused, and the switches.

    http://127.0.0.1:8781/log                     Actions: every comment, reply, message, hide, story, card and post
    http://127.0.0.1:8781/log?view=health         sent, practised, success rate, failed posts, usage, time saved
    http://127.0.0.1:8781/log?view=holds          what Meta or YouTube asked us to pause, and Release where allowed
    http://127.0.0.1:8781/log?view=switches       each part per account: off, practice, on, and what it waits for

The tab reads files only (the outward log state/<slug>/outward/actions-YYYY-MM.jsonl, holds.json, guard.json, the
boards, the cards boards, the coach files, state/social/youtube-units.json): no platform is called to draw it. It
polls /log/data every 15 seconds (5 while something is on its way) and redraws only when the stamp changed; a row a
person opened stays open across a redraw.

Its clicks (POST /act, each only from a click on the page, each behind a confirm that names the change):

    log-release        {account, hold}             end a hold early, only one Meta or YouTube allows (outward.release)
    log-switch         {account, key, value}       one switch (switches.set_mode: going live needs the checklist)
    log-read-practice  {account, feature}          "I read these": the practice rows of a part were read
    social-stop / social-start                     Stop everything and Start again, in the header (social.act)

A switch Andrew tried to put live from here, refused only because the practice rows were not read yet, is kept in
state/<slug>/social/log-tries.json, so the doctor can say what it waits for.
"""
from __future__ import annotations

import hashlib
import json
import urllib.parse
from datetime import date, datetime, timedelta, timezone
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

from . import config as C
from . import registry as REG
from . import socialui as SX
from . import store
from . import switches as SW

esc = SX.esc

# ------------------------------------------------------------------------------------------------------ words
VIEWS = (("actions", "Actions"), ("health", "Health"), ("holds", "Holds"), ("switches", "Switches"))
WINDOWS = (("24h", "24 hours", 1), ("7d", "7 days", 7), ("30d", "30 days", 30))
DEFAULT_WINDOW = "7d"
ROWS_SHOWN = 200
TEXT_DAYS = 90         # outward.LOG_TEXT_DAYS: a log row keeps its words this long, then prune_job strips them
PLATFORMS = (("facebook", "Facebook"), ("instagram", "Instagram"), ("youtube", "YouTube"))
PLAT_WORDS = dict(PLATFORMS)
PLAT_WORDS.update({"tiktok": "TikTok"})
KIND_WORDS = {"post": "post", "story": "story", "card_post": "card post", "first_comment": "first comment",
              "reply_public": "public reply", "reply_private": "private reply", "dm": "message", "hide": "hide",
              "unhide": "unhide", "yt_moderate": "YouTube moderation", "read": "read"}
KINDS = tuple(KIND_WORDS)
KIND_PLURAL = {"post": "posts", "story": "stories", "card_post": "card posts", "first_comment": "first comments",
               "reply_public": "public replies", "reply_private": "private replies", "dm": "messages",
               "hide": "hides", "unhide": "unhides", "yt_moderate": "YouTube moderations", "read": "reads",
               "poll": "polls", "trial": "trial reels"}
STATE_WORDS = {"queued": "Queued", "practice": "Practice", "sending": "Sending", "done": "Done", "failed": "Failed",
               "skipped": "Skipped", "lookup": "Looking it up", "gave_up": "Gave up", "held": "Held",
               "watch": "Watch", "refused": "Refused"}
STATE_CLASS = {"done": "ok", "failed": "bad", "gave_up": "bad", "refused": "bad", "held": "warn", "watch": "warn",
               "skipped": "warn", "queued": "go", "sending": "go", "lookup": "go", "practice": "mute"}
BUSY = ("queued", "sending", "lookup")
FAMILY_WORDS = {"publish": "posts and stories", "comment": "comments and replies", "message": "messages",
                "moderation": "hiding comments", "read": "reading", "*": "everything social"}
MODE_WORDS = {"off": "Off", "watch": "Watch", "practice": "Practice", "testers": "Testers", "on": "On"}
MODE_MEANS = {"off": "It stops and does nothing.",
              "watch": "It writes what it would pause in the log; nothing is held.",
              "practice": "It runs the whole way and writes the exact words it would send; nothing is sent.",
              "testers": "It sends only to the testers listed for the account.",
              "on": "It works for real."}
SENDS = ("replies", "automations", "first_comment.facebook_reels", "first_comment.instagram_reels",
         "first_comment.youtube_shorts", "cards.post", "cards.story_repost", "poll", "trial_reels")
GUARD_MEANS = {"on": "It pauses a platform's posts when Meta or YouTube push back, and obeys every hold."}

# Our estimate of the minutes each thing saves him (a person doing it by hand): Health shows the formula.
MINUTES = (("post", "posts", 3.0), ("first_comment", "first comments", 1.0), ("reply_public", "public replies", 0.5),
           ("reply_private", "private replies and messages", 1.0), ("hide", "hides", 0.25), ("story", "stories", 2.0),
           ("card_post", "card posts", 3.0), ("card_made", "cards made", 10.0),
           ("scorecard", "written scorecards", 3.0))
SAVES_AS = {"dm": "reply_private", "unhide": "hide", "yt_moderate": "hide"}      # counted with these

# each switch: (words, the part's modules (built when one is), what it does)
PARTS: Dict[str, Tuple[str, Tuple[str, ...], str]] = {
    "guard": ("Posting guard", ("outward",),
              "Watches each post and pauses a platform when Meta or YouTube push back."),
    "engage.read": ("Read comments", ("engage",), "Reads the comments under your posts into the Inbox."),
    "engage.messages": ("Read messages", ("engage",), "Reads Instagram and Facebook messages into the Inbox."),
    "replies": ("Replies by hand", ("engage",), "Sends the replies, hides and messages you click in the Inbox."),
    "automations": ("Automations", ("rules",), "Answers comments and messages by the rules you signed."),
    "first_comment.facebook_reels": ("First comment on Facebook", ("firstcomment",),
                                     "Posts the signed first comment under each Facebook Reel."),
    "first_comment.instagram_reels": ("First comment on Instagram", ("firstcomment",),
                                      "Posts the signed first comment under each Instagram Reel."),
    "first_comment.youtube_shorts": ("First comment on YouTube", ("firstcomment",),
                                     "Posts the signed first comment under each Short once it is public."),
    "cards.make": ("Make cards", ("cards",), "Draws the day's cards for you to sign."),
    "cards.post": ("Post cards", ("cards",), "Posts the cards you signed."),
    "cards.story_repost": ("Story reposts", ("cards",), "Reposts each posted clip as a story, under the signed rule."),
    "coach": ("Hook coach", ("coach",), "Measures each draft's opening and shows the scorecard."),
    "checkpoints": ("Checkpoint stats", ("checkpoints",), "Reads each post again at 1 hour, 3, 6, 24, 72, 7 and 30 days."),
    "checkpoints.extra": ("Extra numbers", ("checkpoints",), "Reads watch time, saves and shares in their own calls."),
    "checkpoints.yt_analytics": ("YouTube Analytics", ("checkpoints",), "Reads YouTube Analytics where it is allowed."),
    "learn": ("Daily report", ("learn",), "Writes yesterday's report and proposals at 04:40."),
    "learn.playbook": ("Playbook", ("learn",), "Lets the proposals you approve steer the scout and the coach."),
    "learn.experiments": ("Experiments", ("learn",), "Runs the experiments you signed."),
    "ideas": ("Ideas", ("ideas",), "Reads what is winning on the benchmark channels."),
    "poll": ("Instagram poll", ("reelextras", "firstcomment"), "Adds the signed poll to the Instagram Reel."),
    "trial_reels": ("Trial reels", ("reelextras", "firstcomment"), "Posts experiment arms as Instagram trial reels."),
    "vs_card": ("VS card", ("vscard",), "Puts the fighting-game VS card in front of a clip that names two people."),
    "cover": ("Cover", ("covers",), "Opens each Instagram and Facebook Reel on the clip's poster frame."),
    "cover_art": ("Cover art", ("coverart",), "Makes a designed cover for each clip and sets it on Facebook and Instagram."),
    "pre_hook.fallback": ("Pre-hook when no cold open", ("prehook",),
                          "Puts the pre-hook in front of a clip that would open on its headline alone."),
}
GROUPS = (("Safety", ("guard",)),
          ("Inbox", ("engage.read", "engage.messages", "replies", "automations")),
          ("First comments", ("first_comment.facebook_reels", "first_comment.instagram_reels",
                              "first_comment.youtube_shorts")),
          ("Cards and stories", ("cards.make", "cards.post", "cards.story_repost")),
          ("Coach and numbers", ("coach", "checkpoints", "checkpoints.extra", "checkpoints.yt_analytics")),
          ("Reports", ("learn", "learn.playbook", "learn.experiments")),
          ("Ideas", ("ideas",)),
          ("Instagram extras", ("poll", "trial_reels")),
          ("Openers", ("vs_card", "pre_hook.fallback")),
          ("Covers", ("cover", "cover_art")))
# the permission a live value of a switch with no checklist needs: ("any" | "all", [(platform, need)])
MODE_NEEDS = {"engage.messages": ("any", (("instagram", "message"), ("facebook", "message"))),
              "replies": ("any", (("instagram", "comment"), ("facebook", "comment"), ("youtube", "comment"))),
              "checkpoints.extra": ("any", (("instagram", "analytics"), ("facebook", "analytics"))),
              "checkpoints.yt_analytics": ("all", (("youtube", "analytics"),))}

STOP_ASK = "Stop every comment, reply, message, story and card on every account? Signed clips keep posting."
START_ASK = ("Start again? Comments, replies, messages, stories and cards that are waiting go out at their turn, "
             "and anything past its time is dropped.")
TRIES = "log-tries.json"


# ------------------------------------------------------------------------------------------------------ small helpers
def _now(now: Optional[datetime] = None) -> datetime:
    t = now or datetime.now(timezone.utc)
    if t.tzinfo is None:
        t = t.replace(tzinfo=timezone.utc)
    return t.astimezone(timezone.utc)


def _iso(t: datetime) -> str:
    return _now(t).replace(microsecond=0).isoformat()


def _when(v) -> Optional[datetime]:
    return store._when(v) if v not in (None, "") else None


def _local(cfg: C.Config, t: Optional[datetime], now: datetime) -> str:
    """'14:02' today, 'Oct 19 14:02' another day (the account's time zone)."""
    if t is None:
        return ""
    tz = cfg.tz()
    lt, ln = t.astimezone(tz), now.astimezone(tz)
    return lt.strftime("%H:%M") if lt.date() == ln.date() else f"{lt.strftime('%b')} {lt.day} {lt.strftime('%H:%M')}"


def _short(a: C.Account) -> str:
    return str(a.short or (a.raw or {}).get("short") or a.name.split()[0])


def _plat(x) -> str:
    x = str(x or "")
    if x in PLAT_WORDS:
        return x
    head = x.partition("_")[0]
    return head if head in PLAT_WORDS else x


def _pct(n: float, d: float) -> str:
    return f"{round(100.0 * n / d)}%" if d else "no sends"


def _rate(done: int, failed: int, gave_up: int) -> Optional[float]:
    d = done + failed + gave_up
    return (done / d) if d else None


def _window(q: dict) -> Tuple[str, int]:
    w = str(q.get("window") or DEFAULT_WINDOW)
    for key, _, days in WINDOWS:
        if key == w:
            return key, days
    return DEFAULT_WINDOW, 7


def _accounts(cfg: C.Config, acct: str = "all") -> List[C.Account]:
    """The owner's own accounts working here (never a client's, never one switched off), or the one picked."""
    return SX.own_accounts(cfg, acct or "all")


def _pick(cfg: C.Config, raw) -> str:
    raw = str(raw or "")
    return raw if raw in {a.slug for a in _accounts(cfg)} else "all"


def _own(cfg: C.Config, slug) -> C.Account:
    """An owner account working here, for a click; anything else is refused in plain words."""
    for a in _accounts(cfg):
        if a.slug == str(slug or ""):
            return a
    raise SystemExit(f"{slug or 'that'}: not one of your accounts working on this machine")


def _rows(cfg: C.Config, a: C.Account, since: datetime, until: Optional[datetime] = None) -> List[dict]:
    """The account's outward log rows since then (none while outward is not built)."""
    f = REG.fn("outward:rows")
    if f is None:
        return []
    try:
        return [r for r in f(cfg, a.slug, since=since, until=until) if isinstance(r, dict)]
    except Exception as e:  # noqa: BLE001  (a log that will not read shows as empty, never breaks the page)
        REG._log_error("logpage:rows", e)
        return []


def _rk(r: dict) -> str:
    return hashlib.sha1(json.dumps(r, sort_keys=True, default=str).encode()).hexdigest()[:12]


def _clip_href(slug: str, board, slot) -> str:
    board, slot = str(board or ""), str(slot or "")
    if not board:
        return ""
    href = f"/?date={urllib.parse.quote(board)}&acct={urllib.parse.quote(slug)}"
    if len(slot) == 5 and slot[2] == ":":
        href += f"#c-{slug}-{slot.replace(':', '')}"
    return href


def _link(href: str, words: str) -> str:
    if href.startswith("/"):
        return f'<a href="{esc(href)}">{esc(words)}</a>'
    if href.startswith("https://"):
        return f'<a href="{esc(href)}" target="_blank" rel="noopener noreferrer">{esc(words)}</a>'
    return esc(words)


def _q(**kw) -> str:
    return "/log?" + urllib.parse.urlencode({k: v for k, v in kw.items() if v not in (None, "")})


def _seg(name: str, opts: Sequence[Tuple[str, str]], cur: str, label: str) -> str:
    return (f'<div class="seg lg-seg" role="group" aria-label="{esc(label)}">' + "".join(
        f'<button type="button" data-f="{esc(name)}" data-v="{esc(v)}" aria-pressed="{str(v == cur).lower()}">'
        f'{esc(w)}</button>' for v, w in opts) + "</div>")


def _select(name: str, label: str, opts: Sequence[Tuple[str, str]], cur: str) -> str:
    return (f'<label class="lg-f"><span>{esc(label)}</span><select data-f="{esc(name)}" aria-label="{esc(label)}">'
            + "".join(f'<option value="{esc(v)}"{" selected" if v == cur else ""}>{esc(w)}</option>' for v, w in opts)
            + "</select></label>")


# ------------------------------------------------------------------------------------------------------ the feature rows
def feature_of(row: dict) -> List[str]:
    """The checklist parts a log row counts for (switches.CHECKLIST: its kinds, and its outlet when the part has one)."""
    out = []
    for feat, spec in SW.CHECKLIST.items():
        if row.get("feature") == feat or (row.get("kind") in spec["kinds"] and
                                           (not spec["outlet"] or row.get("outlet") == spec["outlet"])):
            out.append(feat)
    return out


# ------------------------------------------------------------------------------------------------------ Actions
def _filters(cfg: C.Config, q: dict) -> dict:
    w, days = _window(q)
    f = {"acct": _pick(cfg, q.get("acct")), "window": w, "days": days,
         "platform": str(q.get("platform") or ""), "kind": str(q.get("kind") or ""),
         "state": str(q.get("state") or ""), "mode": str(q.get("mode") or ""),
         "feature": str(q.get("feature") or "")}
    if f["platform"] not in PLAT_WORDS:
        f["platform"] = ""
    if f["kind"] not in KIND_WORDS:
        f["kind"] = ""
    if f["state"] not in STATE_WORDS:
        f["state"] = ""
    if f["mode"] not in ("live", "practice"):
        f["mode"] = ""
    if f["feature"] not in SW.CHECKLIST:
        f["feature"] = ""
    return f


def actions(cfg: C.Config, q: dict, now: Optional[datetime] = None) -> dict:
    """The Actions view's rows, newest first, after the filters: {"rows": [...], "total", "shown", "busy"}."""
    now = _now(now)
    f = _filters(cfg, q)
    since = now - timedelta(days=f["days"])
    out: List[dict] = []
    for a in _accounts(cfg, f["acct"]):
        for r in _rows(cfg, a, since):
            if f["platform"] and _plat(r.get("platform")) != f["platform"]:
                continue
            if f["kind"] and r.get("kind") != f["kind"]:
                continue
            if f["state"] and r.get("state") != f["state"]:
                continue
            if f["mode"] and str(r.get("mode") or "live") != f["mode"]:
                continue
            if f["feature"] and f["feature"] not in feature_of(r):
                continue
            out.append(dict(r, acct=a.slug))
    out.sort(key=lambda r: (str(r.get("t") or ""), _rk(r)), reverse=True)
    latest: Dict[str, str] = {}
    for r in out:                                   # an action still on its way: its newest row is queued or sending
        aid = str(r.get("id") or "")
        if aid and aid not in latest:
            latest[aid] = str(r.get("state") or "")
    busy = any(s in BUSY for s in latest.values())
    return {"filters": f, "rows": out[:ROWS_SHOWN], "total": len(out), "busy": busy}


def _text_sha(r: dict) -> str:
    sha = str(r.get("text_sha") or "")
    if not sha and r.get("text") and r.get("text") != "[forgotten]":
        sha = hashlib.sha256(str(r["text"]).encode()).hexdigest()[:24]
    return sha


def _dl(items: Iterable[Tuple[str, str]]) -> str:
    inner = "".join(f"<dt>{esc(k)}</dt><dd>{v}</dd>" for k, v in items if v)
    return f'<dl class="lg-dl">{inner}</dl>' if inner else ""


def _row_detail(cfg: C.Config, r: dict, now: datetime) -> str:
    slug = str(r.get("acct") or "")
    t = r.get("target") if isinstance(r.get("target"), dict) else {}
    res = r.get("result") if isinstance(r.get("result"), dict) else {}
    err = r.get("error") if isinstance(r.get("error"), dict) else {}
    use = r.get("usage") if isinstance(r.get("usage"), dict) else {}
    sig = r.get("signed") if isinstance(r.get("signed"), dict) else {}
    org = r.get("origin") if isinstance(r.get("origin"), dict) else {}
    target = []
    for k, w in (("post_id", "post"), ("comment_id", "comment"), ("thread_id", "thread"), ("to", "to"),
                 ("intent", "asked"), ("item", "inbox item")):
        if t.get(k):
            target.append(f"{w} {esc(t[k])}")
    if t.get("board"):
        target.append(_link(_clip_href(slug, t.get("board"), t.get("slot")),
                            f"the clip {t.get('board')} {t.get('slot') or ''}".strip()))
    practice = str(r.get("mode") or "") == "practice"
    words = ""
    if r.get("text") == "[forgotten]":
        words = "[forgotten]"
    elif practice and r.get("text"):
        words = f'<span class="lg-words">{esc(r["text"])}</span>'
    elif _text_sha(r):
        note = (f"the words stay in the log for {TEXT_DAYS} days; this page shows only their digest" if r.get("text")
                else f"the words left the log after {TEXT_DAYS} days; only the digest is kept")
        words = f'<code>sha256 {esc(_text_sha(r))}</code> <span class="sx-muted">({note})</span>'
    result = ""
    if res.get("id") or res.get("url"):
        result = (f"<code>{esc(res.get('id') or '')}</code> " if res.get("id") else "") + (
            _link(str(res.get("url") or ""), "open") if str(res.get("url") or "").startswith("https://") else "")
    error = ""
    if err:
        bits = [f"HTTP {err['http']}" if err.get("http") else "", f"code {err['code']}" if err.get("code") is not None
                else "", f"subcode {err['subcode']}" if err.get("subcode") is not None else "",
                f"reason {err['reason']}" if err.get("reason") else "", f"kind {err['kind']}" if err.get("kind") else ""]
        error = esc(", ".join(b for b in bits if b))
        if err.get("msg"):
            error += f'<span class="lg-msg">{esc(err["msg"])}</span>'
    trace = ""
    if err.get("trace"):
        trace = (f'<span class="lg-trace"><code>{esc(err["trace"])}</code>'
                 f'<button type="button" class="lg-copy" data-copy="{esc(err["trace"])}">Copy</button></span>')
    usage = ""
    if use:
        bits = [f"business use {use['buc_pct']}%" if use.get("buc_pct") is not None else "",
                f"regain in {use['regain_min']} min" if use.get("regain_min") else "",
                f"app {use['app_pct']}%" if use.get("app_pct") is not None else ""]
        usage = esc(", ".join(b for b in bits if b))
    would = _local(cfg, _when(r.get("would_at")), now)
    signed = " ".join(x for x in (str(sig.get("what") or ""), f"by {sig['by']}" if sig.get("by") else "") if x)
    origin = " ".join(x for x in (str(org.get("by") or ""), f"rule {org['rule']}" if org.get("rule") else "") if x)
    return _dl((("Where", f"<code>{esc(r.get('endpoint'))}</code>" if r.get("endpoint") else
                 '<span class="sx-muted">no call made</span>'),
                ("Target", ", ".join(target)),
                ("Words", words),
                ("Result", result),
                ("Error", error),
                ("Trace", trace),
                ("Usage", usage),
                ("Why", esc(r.get("why") or "")),
                ("Would go at", esc(would)),
                ("Signed", esc(signed)),
                ("From", esc(origin)),
                ("Key", f"<code>{esc(r.get('key'))}</code>" if r.get("key") else ""),
                ("Action", f"<code>{esc(r.get('id'))}</code>" if r.get("id") else "")))


def _row_html(cfg: C.Config, r: dict, shorts: Dict[str, str], now: datetime) -> str:
    st = str(r.get("state") or "")
    plat = PLAT_WORDS.get(_plat(r.get("platform")), str(r.get("platform") or ""))
    kind = KIND_WORDS.get(str(r.get("kind") or ""), str(r.get("kind") or "").replace("_", " "))
    practice = str(r.get("mode") or "") == "practice"
    mode = '<span class="lg-mode">Practice</span>' if practice and st != "practice" else ""
    why = str(r.get("why") or "")
    if not why and isinstance(r.get("error"), dict):
        why = str(r["error"].get("msg") or "")
    if not why and practice and r.get("text"):
        why = str(r["text"])
    return (f'<details class="lg-row" data-k="{_rk(r)}"><summary>'
            f'<span class="lg-when">{esc(_local(cfg, _when(r.get("t")), now))}</span>'
            f'<span class="lg-acct">{esc(shorts.get(str(r.get("acct")), r.get("acct") or ""))}</span>'
            f'<span class="lg-what">{esc(plat)} {esc(kind)}</span>'
            f'<span class="lg-st lg-{STATE_CLASS.get(st, "mute")}">{esc(STATE_WORDS.get(st, st))}</span>{mode}'
            f'<span class="lg-why">{esc(why[:160])}</span></summary>'
            f'<div class="lg-body">{_row_detail(cfg, r, now)}</div></details>')


def _actions_html(cfg: C.Config, q: dict, now: datetime) -> Tuple[str, bool]:
    got = actions(cfg, q, now)
    f = got["filters"]
    shorts = {a.slug: _short(a) for a in _accounts(cfg)}
    bar = ['<div class="lg-filters" data-x-form>',
           _select("acct", "Account", [("all", "All accounts")] + [(a.slug, a.name) for a in _accounts(cfg)],
                   f["acct"]),
           _select("platform", "Platform", [("", "Every platform")] + list(PLATFORMS), f["platform"]),
           _select("kind", "Kind", [("", "Every kind")] + [(k, w.capitalize()) for k, w in KIND_WORDS.items()],
                   f["kind"]),
           _select("state", "State", [("", "Every state")] + list(STATE_WORDS.items()), f["state"]),
           _seg("mode", (("", "Live and practice"), ("live", "Live"), ("practice", "Practice")), f["mode"],
                "Live or practice"),
           _seg("window", [(k, w) for k, w, _ in WINDOWS], f["window"], "How far back"),
           "</div>"]
    if f["feature"]:
        words = PARTS.get(f["feature"], (f["feature"],))[0]
        bar.append(f'<p class="lg-only">Only the rows of {esc(words)}. '
                   f'<button type="button" class="link" data-f="feature" data-v="">Show every row</button></p>')
    if not REG.present("outward"):
        body = '<p class="sx-empty">The log is not built here yet (clipfactory/outward.py).</p>'
    elif not got["rows"]:
        body = '<p class="sx-empty">Nothing in the log for these filters.</p>'
    else:
        more = (f'<p class="lg-more">The newest {ROWS_SHOWN} of {got["total"]}. Pick a filter to see the rest.</p>'
                if got["total"] > ROWS_SHOWN else "")
        body = ('<div class="lg-list" role="list">' + "".join(_row_html(cfg, r, shorts, now) for r in got["rows"])
                + "</div>" + more)
    head = (f'<div class="sx-head"><h2>Actions</h2><span class="sx-muted">{got["total"]} '
            f'row{"s" if got["total"] != 1 else ""}, newest first</span></div>')
    return head + "".join(bar) + body, bool(got["busy"])


# ------------------------------------------------------------------------------------------------------ Health
def _board_days(a: C.Account, since: datetime, now: datetime) -> List[Tuple[date, dict]]:
    from . import board as B
    lo, hi = (since - timedelta(days=2)).date(), (now + timedelta(days=2)).date()
    out = []
    for p in B.all_boards(a):
        try:
            d = date.fromisoformat(p.stem)
        except ValueError:
            continue
        if not lo <= d <= hi:
            continue
        bd = store.read(p, {})
        if isinstance(bd, dict):
            out.append((d, bd))
    return out


def board_posts(cfg: C.Config, a: C.Account, since: datetime, now: datetime) -> Tuple[List[dict], List[dict]]:
    """The factory's posts from the boards in the window: (posted, gave_up), each {platform, outlet, day, slot, at,
    why, href, url}. A platform is posted when it has a post or video id; gave up when the poster gave up on it."""
    posted, gave = [], []
    for d, bd in _board_days(a, since, now):
        for k, s in sorted((bd.get("slots") or {}).items()):
            if not isinstance(s, dict):
                continue
            for outlet, rec in sorted((s.get("outlets") or {}).items()):
                if not isinstance(rec, dict):
                    continue
                pid = rec.get("post_id") or rec.get("video_id")
                if pid:
                    t = _when(rec.get("published_at") or rec.get("publish_at") or rec.get("at"))
                elif rec.get("gave_up"):
                    t = _when(rec.get("tried_at") or rec.get("at") or s.get("publish_at"))
                else:
                    continue
                if t is None or not since <= t <= now:
                    continue
                one = {"platform": _plat(outlet), "outlet": outlet, "day": d.isoformat(), "slot": k,
                       "at": _iso(t), "href": _clip_href(a.slug, d.isoformat(), k), "url": str(rec.get("url") or ""),
                       "why": str(rec.get("why") or rec.get("last_error") or "gave up")[:300]}
                (posted if pid else gave).append(one)
    return posted, gave


def _cards_made(a: C.Account, since: datetime, now: datetime) -> int:
    """Cards drawn in the window (state/<slug>/cards/<date>.json: a card with an image or a video)."""
    folder = store.path(a.slug, "cards")
    n = 0
    if not folder.is_dir():
        return 0
    for p in sorted(folder.glob("*.json")):
        try:
            d = date.fromisoformat(p.stem)
        except ValueError:
            continue
        if not (since - timedelta(days=1)).date() <= d <= now.date():
            continue
        bd = store.read(p, {})
        for c in ((bd.get("cards") or {}).values() if isinstance(bd, dict) else []):
            files = c.get("files") if isinstance(c, dict) and isinstance(c.get("files"), dict) else {}
            if files.get("images") or files.get("videos"):
                t = _when((c.get("render") or {}).get("at") if isinstance(c.get("render"), dict) else None) or \
                    _when(c.get("at"))
                if t is None or since <= t <= now + timedelta(days=1):
                    n += 1
    return n


def _scorecards(a: C.Account, since: datetime, now: datetime) -> int:
    """Written scorecards in the window (state/<slug>/coach/<day>/<HHMM>.json with "written")."""
    folder = store.path(a.slug, "coach")
    n = 0
    if not folder.is_dir():
        return 0
    for day in sorted(folder.iterdir()):
        try:
            d = date.fromisoformat(day.name)
        except ValueError:
            continue
        if not day.is_dir() or not (since - timedelta(days=1)).date() <= d <= now.date() + timedelta(days=1):
            continue
        for p in day.glob("*.json"):
            sc = store.read(p, {})
            w = sc.get("written") if isinstance(sc, dict) else None
            if isinstance(w, dict) and (w.get("scores") or w.get("by")):
                t = _when(w.get("at"))
                if t is None or since <= t <= now:
                    n += 1
    return n


def _usage(a: C.Account) -> Dict[str, dict]:
    g = store.read(store.path(a.slug, "outward", "guard.json"), {})
    u = g.get("usage") if isinstance(g, dict) else None
    return {k: v for k, v in u.items() if isinstance(v, dict)} if isinstance(u, dict) else {}


def health(cfg: C.Config, q: dict, now: Optional[datetime] = None) -> dict:
    """The Health view's numbers for the accounts picked and the window."""
    now = _now(now)
    acct = _pick(cfg, q.get("acct"))
    w, days = _window(q)
    since = now - timedelta(days=days)
    by: Dict[Tuple[str, str], Dict[str, int]] = {}
    sent = practice = 0
    saved: Dict[str, int] = {k: 0 for k, _, _ in MINUTES}
    failed_sends: List[dict] = []
    failed_posts: List[dict] = []
    usage: List[dict] = []
    for a in _accounts(cfg, acct):
        for r in _rows(cfg, a, since, now):
            kind = str(r.get("kind") or "")
            if kind == "post":                   # the boards count the posts (the guard's post rows would twice)
                continue
            plat = _plat(r.get("platform"))
            st = str(r.get("state") or "")
            if str(r.get("mode") or "live") == "practice" or st == "practice":
                practice += 1
                continue
            cell = by.setdefault((plat, kind), {"done": 0, "failed": 0, "gave_up": 0})
            if st in cell:
                cell[st] += 1
            if st == "done":
                sent += 1
                key = SAVES_AS.get(kind, kind)
                if key in saved:
                    saved[key] += 1
            if st in ("failed", "gave_up"):
                failed_sends.append(dict(r, acct=a.slug))
        posted, gave = board_posts(cfg, a, since, now)
        for p in posted:
            by.setdefault((p["platform"], "post"), {"done": 0, "failed": 0, "gave_up": 0})["done"] += 1
        for p in gave:
            by.setdefault((p["platform"], "post"), {"done": 0, "failed": 0, "gave_up": 0})["gave_up"] += 1
            failed_posts.append(dict(p, acct=a.slug, name=a.name))
        saved["post"] += len(posted)
        saved["card_made"] += _cards_made(a, since, now)
        saved["scorecard"] += _scorecards(a, since, now)
        for plat, u in sorted(_usage(a).items()):
            usage.append({"acct": a.slug, "name": a.name, "platform": plat, "buc_pct": u.get("buc_pct"),
                          "regain_min": u.get("regain_min"), "app_pct": u.get("app_pct"), "at": u.get("at")})
    rates = []
    tot = {"done": 0, "failed": 0, "gave_up": 0}
    for (plat, kind), c in sorted(by.items()):
        rates.append({"platform": plat, "kind": kind, **c, "rate": _rate(c["done"], c["failed"], c["gave_up"])})
        for k in tot:
            tot[k] += c[k]
    minutes = sum(saved[k] * m for k, _, m in MINUTES)
    yt = {"used": 0, "budget": int(SW.global_settings().get("youtube_units") or 0), "by": {}}
    used = REG.fn("outward:yt_used")
    if used is not None:
        try:
            yt["used"] = int(used(now))
            raw = store.read(store.path("social", "youtube-units.json"), {})
            if isinstance(raw, dict) and isinstance(raw.get("by"), dict) and yt["used"]:
                yt["by"] = {str(k): v for k, v in raw["by"].items()}
        except Exception as e:  # noqa: BLE001
            REG._log_error("logpage:yt_used", e)
    failed_posts.sort(key=lambda p: p["at"], reverse=True)
    failed_sends.sort(key=lambda r: str(r.get("t") or ""), reverse=True)
    return {"acct": acct, "window": w, "days": days, "sent": sent, "practice": practice, "rates": rates,
            "total": dict(tot, rate=_rate(tot["done"], tot["failed"], tot["gave_up"])),
            "failed_posts": failed_posts, "failed_sends": failed_sends[:50], "usage": usage, "youtube": yt,
            "saved": saved, "minutes": minutes}


def _span(minutes: float) -> str:
    m = int(round(minutes))
    if m < 60:
        return f"{m} min"
    h, m = divmod(m, 60)
    return f"{h} h {m} min" if m else f"{h} h"


def _rate_words(r: Optional[float]) -> str:
    return "nothing sent" if r is None else f"{round(100 * r)}%"


def _health_html(cfg: C.Config, q: dict, now: datetime) -> str:
    h = health(cfg, q, now)
    yt = h["youtube"]
    kpis = [("Sent", str(h["sent"]), "comments, replies, messages, hides, stories, cards"),
            ("Practice", str(h["practice"]), "written in the log, nothing sent"),
            ("Success", _rate_words(h["total"]["rate"]),
             f'{h["total"]["done"]} done, {h["total"]["failed"]} failed, {h["total"]["gave_up"]} gave up'),
            ("Failed posts", str(len(h["failed_posts"])), "the poster gave up on these"),
            ("Time saved", _span(h["minutes"]), "Our estimate (the formula is below)"),
            ("YouTube units today", f'{yt["used"]:,} of {yt["budget"]:,}', "this machine's social budget")]
    out = ['<div class="sx-head"><h2>Health</h2>',
           _seg("window", [(k, w) for k, w, _ in WINDOWS], h["window"], "How far back"), "</div>",
           '<div class="kpis lg-kpis">' + "".join(
               f'<div class="kpi"><div class="k">{esc(k)}</div><div class="v">{esc(v)}</div>'
               f'<div class="d">{esc(d)}</div></div>' for k, v, d in kpis) + "</div>"]
    if h["rates"]:
        rows = "".join(
            f'<tr><td>{esc(PLAT_WORDS.get(r["platform"], r["platform"]))}</td>'
            f'<td>{esc(KIND_WORDS.get(r["kind"], r["kind"]))}</td><td class="r">{r["done"]}</td>'
            f'<td class="r">{r["failed"]}</td><td class="r">{r["gave_up"]}</td>'
            f'<td class="r"><b>{esc(_rate_words(r["rate"]))}</b></td></tr>' for r in h["rates"])
        out.append('<section class="panel lg-panel"><h3>Success by platform and kind</h3>'
                   '<p class="sx-muted">Success is done out of done, failed and gave up. Posts come from the boards '
                   '(posted against gave up).</p><div class="tbl"><table><thead><tr><th>Platform</th><th>Kind</th>'
                   '<th class="r">Done</th><th class="r">Failed</th><th class="r">Gave up</th><th class="r">Success'
                   f'</th></tr></thead><tbody>{rows}</tbody></table></div></section>')
    else:
        out.append('<section class="panel lg-panel"><h3>Success by platform and kind</h3>'
                   '<p class="sx-empty">Nothing was sent or posted in this window.</p></section>')
    fp = "".join(
        f'<li><span class="lg-st lg-bad">Gave up</span> {esc(p["name"])} {esc(PLAT_WORDS.get(p["platform"], p["platform"]))} '
        f'{esc(p["day"])} {esc(p["slot"])}: {esc(p["why"])} {_link(p["href"], "open the clip")}</li>'
        for p in h["failed_posts"])
    out.append('<section class="panel lg-panel"><h3>Failed posts</h3>'
               + (f'<ul class="lg-ul">{fp}</ul>' if fp else '<p class="sx-muted">None in this window.</p>')
               + "</section>")
    if h["failed_sends"]:
        shorts = {a.slug: _short(a) for a in _accounts(cfg)}
        fs = "".join(_row_html(cfg, r, shorts, now) for r in h["failed_sends"][:20])
        out.append('<section class="panel lg-panel"><h3>Sends that did not go</h3>'
                   f'<div class="lg-list">{fs}</div>'
                   f'<p class="lg-more">{_link(_q(view="actions", state="gave_up", window=h["window"]), "Every row that gave up")}'
                   "</p></section>")
    if h["usage"]:
        rows = "".join(
            f'<tr><td>{esc(u["name"])}</td><td>{esc(PLAT_WORDS.get(u["platform"], u["platform"]))}</td>'
            f'<td class="r">{esc("" if u["buc_pct"] is None else str(u["buc_pct"]) + "%")}</td>'
            f'<td class="r">{esc(str(u["regain_min"]) + " min" if u["regain_min"] else "")}</td>'
            f'<td class="r">{esc("" if u["app_pct"] is None else str(u["app_pct"]) + "%")}</td>'
            f'<td>{esc(_local(cfg, _when(u["at"]), now))}</td></tr>' for u in h["usage"])
        out.append('<section class="panel lg-panel"><h3>Meta usage per account</h3>'
                   '<p class="sx-muted">From the last answer Meta gave (the business use header). At 75% the layer '
                   'slows down for an hour; at 90% it stops reading for an hour.</p><div class="tbl"><table><thead>'
                   '<tr><th>Account</th><th>Platform</th><th class="r">Business use</th><th class="r">Regain</th>'
                   f'<th class="r">App</th><th>Read</th></tr></thead><tbody>{rows}</tbody></table></div></section>')
    else:
        out.append('<section class="panel lg-panel"><h3>Meta usage per account</h3>'
                   '<p class="sx-muted">No usage read yet: it is kept from Meta\'s answers once a part is on.</p>'
                   "</section>")
    if yt["by"]:
        out.append('<p class="sx-muted lg-yt">YouTube units today by call: ' + esc(", ".join(
            f"{k} {v}" for k, v in sorted(yt["by"].items()))) + ".</p>")
    formula = " + ".join(f"{w} x {('%g' % m)}" for _, w, m in MINUTES)
    parts = "".join(f'<tr><td>{esc(w.capitalize())}</td><td class="r">{h["saved"][k]}</td>'
                    f'<td class="r">{"%g" % m}</td><td class="r">{esc(_span(h["saved"][k] * m))}</td></tr>'
                    for k, w, m in MINUTES)
    out.append('<section class="panel lg-panel"><h3>Time saved: Our estimate</h3>'
               f'<p class="sx-muted">In minutes: {esc(formula)}. Only what really went out counts (no practice).</p>'
               '<div class="tbl"><table><thead><tr><th>What</th><th class="r">How many</th>'
               f'<th class="r">Minutes each</th><th class="r">Saved</th></tr></thead><tbody>{parts}</tbody></table>'
               "</div></section>")
    return "".join(out)


# ------------------------------------------------------------------------------------------------------ Holds
def _holds_file(a: C.Account) -> dict:
    d = store.read(store.path(a.slug, "outward", "holds.json"), {})
    return d if isinstance(d, dict) else {}


def holds(cfg: C.Config, a: C.Account, now: Optional[datetime] = None) -> List[Tuple[str, dict]]:
    """The holds still running for the account, longest first."""
    now = _now(now)
    got = _holds_file(a).get("holds")
    out = []
    for k, h in (got.items() if isinstance(got, dict) else []):
        if not isinstance(h, dict):
            continue
        until = _when(h.get("until"))
        if until is not None and until > now:
            out.append((str(k), h))
    return sorted(out, key=lambda kh: str(kh[1].get("until")), reverse=True)


def _org(plat: str) -> str:
    return "YouTube" if plat == "youtube" else "Meta"


def _hold_what(a: C.Account, key: str) -> str:
    plat, _, fam = key.partition("|")
    what = FAMILY_WORDS.get(fam) or KIND_PLURAL.get(fam) or fam.replace("_", " ")
    return f"{PLAT_WORDS.get(plat, plat)} {what}"


def _hold_first(cfg: C.Config, key: str, h: dict, now: datetime) -> str:
    """Who paused it, with the code and the time: we did (3 failures), the sign-in stopped working, or Meta or
    YouTube asked us to wait."""
    plat = key.partition("|")[0]
    at = _local(cfg, _when(h.get("at")), now)
    code = h.get("code")
    said = " ".join(x for x in (f"code {code}" if code not in (None, "") else "", f"at {at}" if at else "") if x)
    said = f" ({said})" if said else ""
    kind = str(h.get("kind") or "")
    if kind == "failures":
        return f"We paused this ourselves after 3 failures in an hour{said}."
    if kind == "auth":
        return f"The sign-in stopped working{said}."
    return f"{_org(plat)} asked us to wait{said}."


def release_ask(cfg: C.Config, a: C.Account, key: str, h: dict, now: Optional[datetime] = None) -> str:
    """The confirm before Release: who paused it (us, the sign-in, or Meta or YouTube), the code and the time, and
    what may go out again."""
    now = _now(now)
    first = _hold_first(cfg, key, h, now)
    if str(h.get("kind") or "") == "auth":
        plat = key.partition("|")[0]
        how = "connect youtube" if plat == "youtube" else "connect meta"
        return (f"{first} Release early? {C.possessive(a.name)} {_hold_what(a, key)} would try again right away, "
                f"and they fail until you connect again (factory.py {how} {a.slug}).")
    return f"{first} Release early? {C.possessive(a.name)} {_hold_what(a, key)} can go out again right away."


def _holds_html(cfg: C.Config, q: dict, now: datetime) -> str:
    acct = _pick(cfg, q.get("acct"))
    out = ['<div class="sx-head"><h2>Holds</h2><span class="sx-muted">What is paused, per account and platform: '
           'by Meta or YouTube, or by us after failures</span></div>']
    for a in _accounts(cfg, acct):
        items = []
        for key, h in holds(cfg, a, now):
            until = _local(cfg, _when(h.get("until")), now)
            plat = key.partition("|")[0]
            why = str(h.get("why") or "").strip().rstrip(".")
            bits = [f"set {_local(cfg, _when(h.get('at')), now)}" if h.get("at") else "",
                    f"code {h['code']}" if h.get("code") not in (None, "") else "",
                    f"by {h['by']}" if h.get("by") else "", f"kind {h['kind']}" if h.get("kind") else ""]
            if h.get("releasable", True):
                act = (f'<button type="button" class="lg-release" data-op="log-release" data-account="{esc(a.slug)}" '
                       f'data-hold="{esc(key)}" data-ask="{esc(release_ask(cfg, a, key, h, now))}">Release early'
                       "</button>")
            else:
                act = (f'<p class="lg-fixed">{esc(_org(plat))} asked us to stop: this one cannot be released early. '
                       f'It ends by itself at {esc(until)}.</p>')
            items.append(f'<div class="lg-hold"><div class="lg-hold-h"><b>{esc(_hold_what(a, key))} paused until '
                         f'{esc(until)}</b></div>' + (f"<p>{esc(why)}.</p>" if why else "")
                         + f'<p class="sx-muted">{esc(", ".join(b for b in bits if b))}</p>{act}</div>')
        ended = [x for x in (_holds_file(a).get("history") or []) if isinstance(x, dict)][-5:]
        past = "".join(
            f'<li>{esc(_hold_what(a, str(x.get("key") or "")))}: {esc(str(x.get("why") or "").rstrip("."))}'
            f'{esc(", released by " + str(x["released_by"])) if x.get("released_by") else ""}'
            f'{esc(", ended " + _local(cfg, _when(x.get("until")), now)) if x.get("ended") else ""}</li>'
            for x in reversed(ended))
        out.append(f'<section class="panel lg-panel" aria-label="{esc(a.name)} holds"><h3>{esc(a.name)}</h3>'
                   + ("".join(items) if items else '<p class="sx-muted">Nothing is paused.</p>')
                   + (f'<details class="lg-past" data-k="past-{esc(a.slug)}"><summary>Earlier holds</summary>'
                      f'<ul class="lg-ul">{past}</ul></details>' if past else "") + "</section>")
    if len(out) == 1:
        out.append('<p class="sx-empty">No account works on this machine.</p>')
    return "".join(out)


# ------------------------------------------------------------------------------------------------------ Switches
def _built(key: str) -> bool:
    mods = PARTS.get(key, ("", (), ""))[1]
    return not mods or any(REG.present(m) for m in mods)


def _missing(a: C.Account, pairs, how: str) -> List[str]:
    """The permissions a live value lacks (capabilities.missing): with "any", only when every platform lacks it."""
    f = REG.fn("capabilities:missing")
    if f is None:
        return ["(the permission check, clipfactory/capabilities.py, is not built yet)"]
    per = []
    for plat, need in pairs:
        try:
            per.append([str(x) for x in (f(a, plat, need) or [])])
        except Exception as e:  # noqa: BLE001  (an unreadable permission file is a missing permission)
            per.append([f"(cannot read the permissions: {type(e).__name__})"])
    if not per:
        return []
    if how == "any" and any(not m for m in per):
        return []
    out: List[str] = []
    for m in per:
        out += [x for x in m if x not in out]
    return out


def _needs(a: C.Account, key: str) -> Tuple[str, list]:
    if key in SW.CHECKLIST:
        f = getattr(SW, "_needs", None)
        try:
            pairs = list(f(a, key)) if callable(f) else list(SW.CHECKLIST[key]["needs"])
        except Exception:  # noqa: BLE001
            pairs = list(SW.CHECKLIST[key]["needs"])
        return "all", pairs
    if key == "engage.read":
        plats = SW.settings(a).get("engage.platforms") or []
        return "any", [(p, "read_comments") for p in plats]  # type: ignore
    return MODE_NEEDS.get(key, ("all", []))


def switch_state(cfg: C.Config, a: C.Account, key: str, now: Optional[datetime] = None) -> dict:
    """One switch as the Switches view draws it: its value, each value and why it is not offered, the checklist,
    what it waits for."""
    now = _now(now)
    cur = SW.mode(a, key)
    built = _built(key)
    checklist = SW.checklist(cfg, a, key, now) if key in SW.CHECKLIST else []
    open_items = [w for done, w in checklist if not done]
    how, pairs = _needs(a, key)
    lacks = _missing(a, pairs, how) if pairs else []
    values = []
    for v in SW.MODES[key]:
        why = ""
        if v != cur and v != SW.OFF:
            if not built:
                why = "its part is not built here yet"
            elif v in SW.LIVE and lacks:
                why = "waits for permission: " + ", ".join(lacks)
            elif v in SW.LIVE and open_items:
                why = "waits for the checklist below"
        values.append({"v": v, "words": MODE_WORDS.get(v, v), "why": why})
    waits = []
    if not built:
        waits.append("its part to be built here")
    if lacks and cur not in SW.LIVE:
        waits.append("permission: " + ", ".join(lacks))
    if key in SW.CHECKLIST and cur not in SW.LIVE:
        since = SW.practice_since(a, key)
        if since is None:
            waits.append("practice first")
        elif not checklist or not checklist[0][0]:
            waits.append(_practice_short(cfg, a, key, since, now))
            if len(checklist) > 2 and not checklist[2][0]:
                waits.append('"I read these"')
    return {"key": key, "value": cur, "values": values, "checklist": checklist, "open": open_items,
            "waits": waits, "built": built, "practising": SW.practice_since(a, key) is not None}


def _practice_short(cfg: C.Config, a: C.Account, key: str, since: datetime, now: datetime) -> str:
    """What the practice still lacks, from the counts: the 2 day minimum, or the rows (or the longer days)."""
    spec = SW.CHECKLIST[key]
    days = int((now - since).total_seconds() // 86400)
    f = getattr(SW, "_feature_rows", None)
    try:
        rows = len(f(cfg, a, key, since)) if callable(f) else _practice_rows(cfg, a, key, now)
    except Exception:  # noqa: BLE001  (an unreadable log counts no rows)
        rows = 0
    least = int(getattr(SW, "PRACTICE_MIN_DAYS", 2))
    n_rows, n_days = int(spec["rows"]), int(spec["days"])
    rows_words = f"{rows} of {n_rows} row{'s' if n_rows != 1 else ''}"
    if days < least:
        out = f"practice days ({days} of {least})"
        if rows < n_rows:
            out += f", then {rows_words} or {n_days} days"
        return out
    return f"practice: {rows_words}, or {n_days} days ({days} so far)"


def _switch_ask(a: C.Account, key: str, old: str, new: str) -> str:
    words = PARTS.get(key, (key,))[0]
    return (f"Switch {words} for {a.name} from {MODE_WORDS.get(old, old)} to {MODE_WORDS.get(new, new)}? "
            f"{_means(key, new)}").strip()


def _means(key: str, value: str) -> str:
    if key == "guard" and value in GUARD_MEANS:
        return GUARD_MEANS[value]
    if value == "on" and key in SENDS:
        return "It goes out for real."
    return MODE_MEANS.get(value, "")


def _read_ask(a: C.Account, key: str, n: int) -> str:
    words = PARTS.get(key, (key,))[0]
    return (f"Say you read the {n} practice row{'s' if n != 1 else ''} of {words} for {a.name}? Going live still "
            f"needs every other line of the checklist. The rows open next.")


def _practice_rows(cfg: C.Config, a: C.Account, key: str, now: datetime) -> int:
    since = SW.practice_since(a, key)
    if since is None:
        return 0
    return sum(1 for r in _rows(cfg, a, since) if str(r.get("mode") or "") == "practice" and key in feature_of(r))


def _switch_html(cfg: C.Config, a: C.Account, key: str, now: datetime) -> str:
    st = switch_state(cfg, a, key, now)
    words, _, does = PARTS.get(key, (key, (), ""))
    segs = []
    for v in st["values"]:
        pressed = v["v"] == st["value"]
        dis = bool(v["why"]) and not pressed
        segs.append(f'<button type="button" data-op="log-switch" data-account="{esc(a.slug)}" data-key="{esc(key)}" '
                    f'data-value="{esc(v["v"])}" aria-pressed="{str(pressed).lower()}"'
                    + (f' disabled title="{esc(v["why"])}" aria-label="{esc(v["words"])}: {esc(v["why"])}"' if dis
                       else "")
                    + ("" if pressed else f' data-ask="{esc(_switch_ask(a, key, st["value"], v["v"]))}"')
                    + f'>{esc(v["words"])}</button>')
    out = [f'<div class="lg-sw" id="sw-{esc(a.slug)}-{esc(key.replace(".", "-"))}">',
           f'<div class="lg-sw-h"><div><b>{esc(words)}</b> <span class="sx-muted">{esc(key)}</span>'
           f'<p class="sx-muted">{esc(does)}</p></div>'
           f'<div class="seg lg-seg" role="group" aria-label="{esc(words)} for {esc(a.name)}">{"".join(segs)}</div>'
           "</div>"]
    if st["waits"]:
        out.append(f'<p class="lg-waits"><b>Waits for:</b> {esc("; ".join(st["waits"]))}.</p>')
    if st["checklist"]:
        lines = "".join(f'<li class="{"lg-done" if done else "lg-todo"}"><span class="lg-tick">'
                        f'{"done" if done else "to do"}</span> {esc(w)}</li>' for done, w in st["checklist"])
        n = _practice_rows(cfg, a, key, now)
        rows_href = _q(view="actions", acct=a.slug, feature=key, mode="practice", window="30d")
        read = ""
        if st["practising"]:
            read = (f'<button type="button" class="go" data-op="log-read-practice" data-account="{esc(a.slug)}" '
                    f'data-feature="{esc(key)}" data-then="{esc(rows_href)}" '
                    f'data-ask="{esc(_read_ask(a, key, n))}">I read these</button>')
        out.append(f'<div class="lg-check"><p class="lg-check-h">Before it goes live</p><ul>{lines}</ul>'
                   f'<div class="lg-check-b"><a href="{esc(rows_href)}">Show the {n} practice row'
                   f'{"s" if n != 1 else ""}</a>{read}</div></div>')
    out.append("</div>")
    return "".join(out)


def _settings_html(a: C.Account) -> str:
    s = SW.settings(a)
    rows = "".join(
        f'<tr><td><code>{esc(k)}</code></td><td><code>{esc(json.dumps(v))}</code></td>'
        f"<td><code>factory.py social set {esc(a.slug)} {esc(k)} '{esc(json.dumps(v))}'</code></td></tr>"
        for k, v in s.items())
    return (f'<details class="lg-settings" data-k="settings-{esc(a.slug)}"><summary>Settings (read only here)</summary>'
            '<p class="sx-muted">Change one with the line beside it in Terminal. Caps may only lower the built in '
            'numbers.</p><div class="tbl"><table><thead><tr><th>Setting</th><th>Now</th><th>To change it</th></tr>'
            f"</thead><tbody>{rows}</tbody></table></div></details>")


def _switches_html(cfg: C.Config, q: dict, now: datetime) -> str:
    acct = _pick(cfg, q.get("acct"))
    out = ['<div class="sx-head"><h2>Switches</h2><span class="sx-muted">Every part starts off. Practice runs the '
           "whole way and sends nothing. On needs the checklist.</span></div>"]
    listed = {k for _, ks in GROUPS for k in ks}
    groups = list(GROUPS) + ([("Other", tuple(k for k in SW.MODES if k not in listed))]
                             if any(k not in listed for k in SW.MODES) else [])
    for a in _accounts(cfg, acct):
        wu = None
        try:
            wu = SW.warmup_until(cfg, a)
        except Exception:  # noqa: BLE001
            wu = None
        created = SW.settings(a).get("created")
        warm = (f"automated replies practise until {wu.isoformat()}" if wu is not None else
                f"no creation date yet (factory.py social created {a.slug} YYYY-MM-DD)" if not created else
                "waits for the factory's first post here")
        body = []
        for title, keys in groups:
            keys = [k for k in keys if k in SW.MODES]
            if keys:
                body.append(f'<div class="lg-group"><h4>{esc(title)}</h4>'
                            + "".join(_switch_html(cfg, a, k, now) for k in keys) + "</div>")
        out.append(f'<section class="panel lg-panel lg-acct" aria-label="{esc(a.name)} switches">'
                   f'<h3>{esc(a.name)}</h3><p class="sx-muted">Warm-up: {esc(warm)}.</p>{"".join(body)}'
                   f"{_settings_html(a)}</section>")
    if len(out) == 1:
        out.append('<p class="sx-empty">No account works on this machine.</p>')
    return "".join(out)


# ------------------------------------------------------------------------------------------------------ the page
def _view(q: dict) -> str:
    v = str(q.get("view") or "actions")
    return v if v in dict(VIEWS) else "actions"


def _view_html(cfg: C.Config, q: dict, now: datetime) -> Tuple[str, bool]:
    v = _view(q)
    if v == "health":
        return _health_html(cfg, q, now), False
    if v == "holds":
        return _holds_html(cfg, q, now), False
    if v == "switches":
        return _switches_html(cfg, q, now), False
    return _actions_html(cfg, q, now)


def _stopped(cfg: C.Config) -> str:
    f = REG.fn("outward:stopped")
    try:
        return str(f(cfg) or "") if f is not None else ""
    except Exception:  # noqa: BLE001
        return ""


def _hold_count(cfg: C.Config, acct: str, now: datetime) -> int:
    return sum(len(holds(cfg, a, now)) for a in _accounts(cfg, acct))


def data(cfg: C.Config, q: dict, now: Optional[datetime] = None) -> dict:
    """/log/data: the view's HTML (the page swaps it in), whether something is on its way (poll faster), whether Stop
    everything is on, the holds count. Files only; ends with the stamp."""
    now = _now(now)
    q = dict(q or {})
    html, busy = _view_html(cfg, q, now)
    return SX.stamp({"view": _view(q), "html": html, "busy": busy, "stopped": _stopped(cfg),
                     "holds": _hold_count(cfg, _pick(cfg, q.get("acct")), now)})


CSS = """
body.sx-log main{max-width:1180px}
.lg-filters{display:flex;flex-wrap:wrap;gap:8px 10px;align-items:flex-end}
.lg-f{display:flex;flex-direction:column;gap:3px;font-size:12px;color:var(--sub);flex:1 1 150px;min-width:0;max-width:240px}
.lg-f select{font:inherit;font-size:13px;height:32px;padding:0 6px;border:1px solid var(--line);border-radius:6px;background:var(--card);color:var(--ink);width:100%;min-width:0}
.lg-seg{max-width:100%}
.lg-only,.lg-more{font-size:13px;color:var(--sub);margin:0}
.lg-list{background:var(--card);border:1px solid var(--line);border-radius:8px;overflow:hidden;min-width:0}
.lg-row{border-bottom:1px solid var(--line);min-width:0}
.lg-row:last-child{border-bottom:0}
.lg-row>summary{display:flex;flex-wrap:wrap;align-items:center;gap:4px 10px;padding:9px 12px;cursor:pointer;list-style:none;min-width:0}
.lg-row>summary::-webkit-details-marker{display:none}
.lg-row>summary:hover{background:var(--bg)}
.lg-row[open]>summary{background:var(--bluebg)}
.lg-when{font-variant-numeric:tabular-nums;color:var(--sub);font-size:12.5px;min-width:44px}
.lg-acct{font-weight:600;font-size:12.5px}
.lg-what{font-size:13.5px}
.lg-why{flex:1 1 220px;min-width:0;color:var(--sub);font-size:12.5px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.lg-st,.lg-mode{font-size:11.5px;font-weight:600;padding:1px 7px;border-radius:4px;line-height:18px;white-space:nowrap}
.lg-ok{color:var(--ok);background:var(--okbg)}
.lg-bad{color:var(--bad);background:var(--badbg)}
.lg-warn{color:var(--warn);background:var(--warnbg)}
.lg-go{color:var(--blue);background:var(--bluebg)}
.lg-mute,.lg-mode{color:var(--sub);background:var(--mute)}
.lg-body{padding:4px 12px 12px;min-width:0}
.lg-dl{display:grid;grid-template-columns:110px minmax(0,1fr);gap:6px 12px;margin:0;font-size:13px}
.lg-dl dt{color:var(--sub)}
.lg-dl dd{margin:0;min-width:0;overflow-wrap:anywhere}
.lg-dl code,.lg-trace code{font-size:12px;overflow-wrap:anywhere}
.lg-words{white-space:pre-wrap}
.lg-msg{display:block;color:var(--sub);margin-top:2px}
.lg-trace{display:inline-flex;flex-wrap:wrap;gap:8px;align-items:center}
.lg-panel{display:flex;flex-direction:column;gap:10px;margin:0}
.lg-panel h3{font-size:15px;margin:0}
.lg-panel h4{font-size:13px;margin:6px 0 2px;color:var(--sub);font-weight:600}
.lg-ul{margin:0;padding-left:18px;font-size:13px;display:flex;flex-direction:column;gap:6px}
.lg-hold{border:1px solid var(--line);border-radius:6px;padding:10px 12px;display:flex;flex-direction:column;gap:4px}
.lg-hold p{margin:0;font-size:13px}
.lg-fixed{color:var(--bad);font-weight:600}
.lg-release{align-self:flex-start}
.lg-stop{color:var(--bad);border-color:var(--bad);font-weight:600}
.lg-start{color:var(--ok);border-color:var(--ok);font-weight:600}
.lg-group{display:flex;flex-direction:column;gap:8px}
.lg-sw{border-top:1px solid var(--line);padding-top:10px;display:flex;flex-direction:column;gap:6px;min-width:0}
.lg-sw-h{display:flex;flex-wrap:wrap;justify-content:space-between;gap:8px 16px;align-items:flex-start}
.lg-sw-h>div:first-child{flex:1 1 240px;min-width:0}
.lg-sw-h p{margin:2px 0 0}
.lg-sw .seg button[disabled]{opacity:.45;cursor:not-allowed}
.lg-waits{margin:0;font-size:13px;color:var(--sub)}
.lg-waits b{color:var(--ink);font-weight:600}
.lg-check{background:var(--bg);border:1px solid var(--line);border-radius:6px;padding:10px 12px;display:flex;flex-direction:column;gap:6px}
.lg-check-h{margin:0;font-weight:600;font-size:13px}
.lg-check ul{margin:0;padding:0;list-style:none;display:flex;flex-direction:column;gap:4px;font-size:13px}
.lg-tick{display:inline-block;min-width:44px;font-size:11.5px;font-weight:600;padding:0 6px;border-radius:4px;text-align:center}
.lg-done .lg-tick{color:var(--ok);background:var(--okbg)}
.lg-todo .lg-tick{color:var(--warn);background:var(--warnbg)}
.lg-check-b{display:flex;flex-wrap:wrap;gap:8px 14px;align-items:center;font-size:13px}
.lg-check-b a,.lg-dl a,.lg-ul a,.lg-more a{color:var(--blue)}
.lg-settings summary{cursor:pointer;font-size:13px;color:var(--sub)}
.lg-settings code{font-size:12px;overflow-wrap:anywhere}
.lg-past summary{cursor:pointer;font-size:13px;color:var(--sub)}
.lg-kpis .kpi .v{font-size:20px}
@media (max-width:600px){
.lg-f{flex:1 1 130px;max-width:none}
.lg-f select{height:40px;font-size:16px}
.lg-row>summary{min-height:40px;padding:8px 12px}
.lg-why{flex-basis:100%;white-space:normal}
.lg-dl{grid-template-columns:minmax(0,1fr)}
.lg-dl dt{margin-top:4px}
.lg-sw-h>div:first-child{flex-basis:100%}
.lg-copy,.lg-release,.lg-stop,.lg-start{min-height:40px}
.lg-check-b a,.lg-more a,.lg-ul a,.lg-dl a{display:inline-flex;align-items:center;min-height:40px}
.lg-settings summary,.lg-past summary{min-height:40px;display:flex;align-items:center}
.lg-panel{padding:12px}}
"""

JS = r"""
(() => {
'use strict';
let every = 15000, pager = null;
function view() { return document.getElementById('lg-view'); }
function header(d) {
  const stop = document.querySelector('button[data-op="social-stop"]'), start = document.querySelector('button[data-op="social-start"]');
  if (stop) stop.hidden = !!d.stopped;
  if (start) start.hidden = !d.stopped;
}
/* a redraw waits while a control in the view has focus (it would be replaced under the finger), except right after
   a filter changed: then the new rows are what was asked for. One that waited is drawn when the focus leaves. */
let forced = false, waiting = null;
function draw(d) {
  const v = view(); if (!v || !d) return;
  header(d);
  const a = document.activeElement;
  if (!forced && a && v.contains(a) && /^(SELECT|INPUT|TEXTAREA)$/.test(a.tagName)) { waiting = d; return; }
  forced = false; waiting = null;
  const keep = new Set([...v.querySelectorAll('details[open][data-k]')].map(x => x.dataset.k));
  if (v.dataset.html !== d.html) {
    v.innerHTML = d.html; v.dataset.html = d.html;
    v.querySelectorAll('details[data-k]').forEach(x => { if (keep.has(x.dataset.k)) x.open = true; });
  }
  const want = d.busy ? 5000 : 15000;
  if (pager && want !== every) { every = want; pager.every(want); }
}
async function copy(text) {
  try { await navigator.clipboard.writeText(text); SX.toast('Copied.'); return; } catch (e) {}
  const ta = document.createElement('textarea');
  ta.value = text; ta.setAttribute('readonly', ''); ta.style.cssText = 'position:fixed;left:-9999px;top:0';
  document.body.append(ta); ta.select();
  let ok = false; try { ok = document.execCommand('copy'); } catch (e) {}
  ta.remove();
  SX.toast(ok ? 'Copied.' : 'Could not copy: select it and copy it by hand.', !ok);
}
document.addEventListener('change', e => {
  const s = e.target.closest('select[data-f]'); if (!s) return;
  if (s.dataset.f === 'acct') {
    const u = new URL(location.href);
    if (s.value === 'all') u.searchParams.delete('acct'); else u.searchParams.set('acct', s.value);
    location.href = u.pathname + u.search; return;
  }
  SX.setq(s.dataset.f, s.value); s.blur(); refilter();
});
/* a filter changed: draw the rows for it now, and once more shortly (a read already on its way when it changed
   was for the old filters, and the poller skips a second read while one is running) */
function refilter() {
  if (!pager) return;
  forced = true; pager.now();
  setTimeout(() => { forced = true; pager.now(); }, 700);
}
document.addEventListener('focusout', () => {
  setTimeout(() => { const d = waiting; if (d) draw(d); }, 0);
});
document.addEventListener('click', async e => {
  const f = e.target.closest('button[data-f]');
  if (f) { SX.setq(f.dataset.f, f.dataset.v); refilter(); return; }
  const c = e.target.closest('button[data-copy]');
  if (c) { e.preventDefault(); copy(c.dataset.copy); return; }
  const b = e.target.closest('button[data-op]');
  if (!b || b.disabled || b.getAttribute('aria-pressed') === 'true') return;
  if (!b.dataset.ask || !SX.ask(b.dataset.ask)) return;
  const body = {op: b.dataset.op};
  for (const k of ['account', 'key', 'value', 'hold', 'feature']) if (b.dataset[k] != null) body[k] = b.dataset[k];
  const r = await SX.act(body, b);
  if (r.ok && b.dataset.then) { setTimeout(() => { location.href = b.dataset.then; }, 600); return; }
  if (pager) pager.now();
});
const v = view(); if (v) v.dataset.html = v.innerHTML;
pager = SX.poll(() => '/log/data' + location.search, every, draw);
})();
"""


def page(cfg: C.Config, tok: str, base_css: str, tabs_html: str, switch_html: str, acct: str, q: dict) -> str:
    """The Log tab: its views, the header's Stop everything (or Start again), the view drawn now (the page then
    re-reads /log/data)."""
    now = _now()
    q = dict(q or {})
    acct = _pick(cfg, acct if acct and acct != "all" else q.get("acct"))
    if acct != "all":
        q["acct"] = acct
    v = _view(q)
    n = _hold_count(cfg, acct, now)
    own = [(k, _q(view=k), (w + (f" ({n})" if k == "holds" and n else ""))) for k, w in VIEWS]
    sub = SX.subnav("log", v, acct, own=own)
    stopped = bool(_stopped(cfg))
    head = (f'<button type="button" class="lg-stop" data-op="social-stop" data-ask="{esc(STOP_ASK)}"'
            f'{" hidden" if stopped else ""}>Stop everything</button>'
            f'<button type="button" class="lg-start" data-op="social-start" data-ask="{esc(START_ASK)}"'
            f'{"" if stopped else " hidden"}>Start again</button>')
    html, _ = _view_html(cfg, q, now)
    main = f'<div id="lg-view" class="lg-view" data-view="{esc(v)}" aria-live="polite">{html}</div>'
    return SX.shell(cfg, "log", "Log", tok, base_css, tabs_html, switch_html, acct, main, css=CSS, js=JS,
                    subnav=sub, head_extra=head)


# ------------------------------------------------------------------------------------------------------ clicks
def _tries_path(a: C.Account):
    return store.path(a.slug, "social", TRIES)


def _note_try(a: C.Account, key: str, value: str, by: str, why: str, now: datetime) -> None:
    """He tried to put a part live from the Log page and it was refused only for the practice rows not read yet."""
    with store.edit(_tries_path(a), dict) as d:
        d[key] = {"at": _iso(now), "by": by, "value": value, "why": why[:400]}


def _clear_try(a: C.Account, key: str) -> None:
    p = _tries_path(a)
    if p.exists():
        with store.edit(p, dict) as d:
            d.pop(key, None)


def _by(cfg: C.Config, body: dict) -> str:
    by = str(body.get("by") or "").strip()
    return by if by in (cfg.approvers or []) else (cfg.approvers[0] if cfg.approvers else "Clip Review")


def act(cfg: C.Config, body: dict, page: bool = False, now: Optional[datetime] = None) -> Tuple[bool, str]:
    """log-release, log-switch and log-read-practice: each only from a click on the page."""
    now = _now(now)
    op = str(body.get("op") or "")
    by = _by(cfg, body)
    if op == "log-release":
        SX.need_page(page, "release a hold")
        a = _own(cfg, body.get("account"))
        key = str(body.get("hold") or "")
        running = dict(holds(cfg, a, now))
        h = running.get(key)
        if h is None:
            raise SystemExit(f"{a.name}: {key or 'that hold'} is not running now (it may have ended by itself)")
        if not h.get("releasable", True):
            raise SystemExit(f"{_org(key.partition('|')[0])} asked us to stop: this one cannot be released early. "
                             f"It ends by itself at {_local(cfg, _when(h.get('until')), now)}.")
        f = REG.fn("outward:release")
        if f is None:
            raise SystemExit("Release is not built yet (clipfactory/outward.py)")
        return True, str(f(cfg, a, key, by, now))
    if op == "log-switch":
        SX.need_page(page, "flip a switch")
        a = _own(cfg, body.get("account"))
        key, value = str(body.get("key") or ""), str(body.get("value") or "").strip().lower()
        try:
            said = SW.set_mode(cfg, a, key, value, by, page=True, now=now)
        except SystemExit as e:
            if value in SW.LIVE and key in SW.CHECKLIST:
                items = SW.checklist(cfg, a, key, now)
                if len(items) > 2 and not items[2][0]:
                    _note_try(a, key, value, by, str(e), now)
            raise
        if SW.mode(a, key) in SW.LIVE or SW.mode(a, key) == SW.OFF:
            _clear_try(a, key)
        return True, said
    if op == "log-read-practice":
        SX.need_page(page, "say the practice rows were read")
        a = _own(cfg, body.get("account"))
        feature = str(body.get("feature") or "")
        said = SW.ack_practice(cfg, a, feature, by, page, now=now)
        return True, said + ". The rows open now."
    raise SystemExit(f"{op or 'that'}: not a click the Log tab knows")


# ------------------------------------------------------------------------------------------------------ doctor
def summary_7d(cfg: C.Config, now: Optional[datetime] = None) -> Tuple[int, Optional[float]]:
    """(actions in 7 days, success) over every account: the log's live rows that finished, plus the boards' posts."""
    now = _now(now)
    h = health(cfg, {"window": "7d"}, now)
    n = h["total"]["done"] + h["total"]["failed"] + h["total"]["gave_up"] + h["practice"]
    return n, h["total"]["rate"]


def doctor(cfg: C.Config, now=None) -> List[Tuple[Optional[bool], str, str]]:
    """'--  log: N actions in 7 days, success 96%', and a NO for each part whose practice rows wait only for "I read
    these" (waits_for_reading). Read only."""
    now = _now(now)
    out: List[Tuple[Optional[bool], str, str]] = []
    n, rate = summary_7d(cfg, now)
    if n:
        out.append((None, f"log: {n} action{'s' if n != 1 else ''} in 7 days, success "
                          f"{'n/a' if rate is None else str(round(100 * rate)) + '%'}", ""))
    else:
        out.append((None, "log: no actions in 7 days", ""))
    for a in _accounts(cfg):
        for key in waits_for_reading(cfg, a, now):
            out.append((False, f'{a.name}: practice rows wait for "I read these" ({key})',
                        f"Clip Review, Log, Switches: read the practice rows of {key}, then press I read these"))
    return out


def waits_for_reading(cfg: C.Config, a: C.Account, now: Optional[datetime] = None) -> List[str]:
    """The parts that wait for "I read these" and for nothing else that he does: practising, with every other line
    of the checklist done (so On would be offered), or tried live from the Log page while the rows were unread."""
    now = _now(now)
    tries = store.read(_tries_path(a), {})
    tried = set(tries) if isinstance(tries, dict) else set()
    out = []
    for key in SW.CHECKLIST:
        if key not in SW.MODES or SW.mode(a, key) in SW.LIVE:
            continue
        practising = SW.practice_since(a, key) is not None
        if not practising and key not in tried:
            continue
        try:
            items = SW.checklist(cfg, a, key, now)
        except Exception:  # noqa: BLE001
            continue
        if len(items) <= 2 or items[2][0]:
            continue
        if key in tried:
            out.append(key)
        elif all(done for i, (done, _) in enumerate(items) if i != 2) and _unread(cfg, a, key):
            out.append(key)
    return sorted(out)


def _unread(cfg: C.Config, a: C.Account, key: str) -> bool:
    """There are practice rows to read (a part that practised days with no row has nothing to read yet)."""
    since = SW.practice_since(a, key)
    f = getattr(SW, "_feature_rows", None)
    if since is None or not callable(f):
        return False
    try:
        return bool(f(cfg, a, key, since))
    except Exception:  # noqa: BLE001
        return False
