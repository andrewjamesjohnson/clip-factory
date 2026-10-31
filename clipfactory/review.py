"""The review page: every clip for a day, every account, and one click to send each one out.

    http://127.0.0.1:8781        always on (launchd com.clipfactory.review); `factory.py review` runs it by hand

Approve signs the clip (signoff.approve hashes the clip, its words and its time) and schedules it in the same click.
It then goes out by itself at its slot on every platform that posts by itself (YouTube, Facebook, Instagram) and lands
in the day's Ready to post folder for a platform posted by hand (TikTok, until TikTok approves the app). Pull takes it
back before it goes out.

Decline and Pull ask why, one tap each (reasons.py: WRONG NAME, FRAMING, CUT, AUDIO, LENGTH, REPEAT, INCOMPLETE,
TEXT, HOOK, RIGHTS, Other with words; keys 1 to 9 and 0 on the card last clicked, then Enter; A approves it, P opens
its Pull). Every Decline and a Pull with a reason put the clip on the account's kill list; "Pull, no reason" is the
way to take a clip back to watch a platform's cut.

Each card with TikTok words carries TikTok's own posting screen (_tt_panel), as TikTok's Direct Post rules ask: the
account's nickname, the caption (editable until signed), who can see it (no default), comments/duet/stitch (off until
ticked), the commercial content disclosure and TikTok's consent line. A signed clip gets "Post to TikTok now", which
sends it at once in its own process (poster.tiktok_now); until TikTok approves the app that works only with "Only
me", from an account set to private. Each card says, per platform, what happened: waiting for its time, uploaded, posted (with the link), or
why not.

The page keeps itself current: every 15 seconds it asks /live what changed and swaps in only that, so a video being
watched never restarts. Loopback only. Writes need the page's token (kept in state/review-token, so a restart of the
page never breaks an open tab), a loopback Host and a same-origin request: another web page cannot sign for you.

The Stats tab (/stats, statspage.py) is every post's numbers for every account: views, the best times and slots, what
works, the factory's own counts and the money, with Update now to read them fresh.

Casts to check (castqueue.py), at the very top of the Clips page whenever anyone waits: each person a Politics
source names whom nobody has checked, with the stills at the times to look at, and "That's <name>" / "Not them".
His tap is the cast check (it writes `verified` in his name). Those taps come only from this page in a browser
(_from_page: its Origin), never from a script, and each sends the item's seen key like Approve.

The account switch at the top (All, then each account: ?acct=) picks whose clips and numbers both tabs show.
The Clips tab opens on a month calendar (each day, each account's slots as one strip: posted, scheduled, to review, a
problem, no clip; click a day to open it), then the clips that need a sign-off, large, then the rest of the day.
Each account's header links to its profile on every platform it posts to (accounts.json "links").

The social layer plugs in through registry.py only (its tabs, the Today band at the top of Clips, a part on each
card, health lines above Needs you, cards and stories on the calendar, the Week page), each part lazily and inside a
try: a part not built yet is simply not there, and one that breaks hides only itself. With no part built the page is
the same as before. Make again opens a row of reasons (verdicts.CHIPS: the codes of reasons.py) and a note instead of a
prompt; Decline and Pull are one tap on a code (see Decline and Pull above; Andrew's pick, 2026-10-20).
"""
from __future__ import annotations

import errno
import hashlib
import html
import json
import mimetypes
import os
import secrets
import subprocess
import urllib.parse
from datetime import date, datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from . import bandcheck as BC
from . import board as B
from . import castqueue as CQ
from . import collect as K
from . import config as C
from . import copytext as T
from . import look as LK
from . import direct as D
from . import moments as MO
from . import outlets as O
from . import poster as P
from . import reasons as R
from . import registry as REG
from . import signoff as S
from . import socialui as SX
from . import statspage as SP

POLL_SECONDS = 15


def esc(s: object) -> str:
    return html.escape(str(s), quote=True)


def token() -> str:
    """The page's write token, kept across restarts (launchd restarts the page; an open tab must keep working)."""
    p = C.STATE / "review-token"
    try:
        t = p.read_text().strip()
        if len(t) >= 24:
            return t
    except OSError:
        pass
    p.parent.mkdir(parents=True, exist_ok=True)
    t = secrets.token_urlsafe(24)
    fd = os.open(p, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(t)
    return t


# ------------------------------------------------------------------------------------------------------ words
def _span(td: timedelta) -> str:
    """timedelta -> '1h 40m', '25m', '2d 3h'."""
    m = int(td.total_seconds() // 60)
    if m < 1:
        return "under a minute"
    d, rest = divmod(m, 1440)
    h, mi = divmod(rest, 60)
    if d:
        return f"{d}d {h}h" if h else f"{d}d"
    if h:
        return f"{h}h {mi:02d}m" if mi else f"{h}h"
    return f"{mi}m"


def _hhmm(cfg: C.Config, iso: str) -> str:
    return datetime.fromisoformat(iso).astimezone(cfg.tz()).strftime("%H:%M")


def _local(cfg: C.Config, iso: str) -> str:
    try:
        return datetime.fromisoformat(iso).astimezone(cfg.tz()).strftime("%b %d, %H:%M")
    except (TypeError, ValueError):
        return str(iso)


def _day_label(d: date, today: date) -> str:
    rel = {0: " · today", 1: " · tomorrow", -1: " · yesterday"}.get((d - today).days, "")
    return f"{d.strftime('%a %b')} {d.day}{rel}"


def _title(s: dict) -> str:
    """The clip's own title (YouTube's), not its folder slug."""
    copy = s.get("copy") or {}
    c = copy.get("youtube_shorts") or next(iter(copy.values()), None)
    if c:
        try:
            t = T.parse(Path(c["path"]).read_text())["title"].strip()
            if t:
                return t
        except (OSError, KeyError):
            pass
    return (s.get("slug") or "").replace("-", " ")


def _hold_words(acct: C.Account) -> Tuple[str, str]:
    """(class, words) when YouTube cannot upload for this account right now, else ('', '')."""
    if "youtube_shorts" not in O.auto_platforms(acct):
        return "", ""
    if not O.yt_connected(acct):
        return "warn", f"YouTube is not connected (factory.py connect youtube {acct.slug} --client <client.json>)"
    hold = O.yt_hold(acct)
    if not hold:
        return "", ""
    short = hold.split(" after:")[0].strip()
    if "uploadLimitExceeded" in hold:
        short += " (YouTube's daily upload cap; the factory keeps under it with the daily budget)"
    return "warn", short


def _meta_connected(acct: C.Account) -> bool:
    return (O.TOKENS / acct.slug / "meta.json").exists()


def _short(acct: C.Account) -> str:
    """The account's short name, for the calendar ('GTA', 'Politics', 'Clips', 'RP')."""
    return str(acct.short or acct.raw.get("short") or acct.name.split()[0])


def _accts(cfg: C.Config, pick: str) -> List[C.Account]:
    """The accounts the switch shows: all of them, or the one picked."""
    if pick and pick != "all" and pick in cfg.accounts:
        return [cfg.accounts[pick]]
    from . import accounts as AC        # a client the owner does not sign shows under its own name only (accounts.py)
    return [a for a in cfg.accounts.values() if AC.signs_here(cfg, a)]


def pick_acct(cfg: C.Config, raw: Optional[str]) -> str:
    """?acct= as the page uses it: an account's slug, or 'all'."""
    return raw if raw in cfg.accounts else "all"


def _links(acct: C.Account) -> str:
    """The account's profile on each platform it posts to, as one row of plain links."""
    links = acct.raw.get("links") or {}
    items = [f'<a href="{esc(links[p])}" target="_blank" rel="noopener">{esc(O.NAMES.get(p, p))}</a>'
             for p in acct.platforms if str(links.get(p, "")).startswith("https://")]
    return f'<nav class="plinks" aria-label="{esc(acct.name)} profiles">{"".join(items)}</nav>' if items else ""


# ------------------------------------------------------------------------------------------------------ one slot
def _late(s: dict, now: datetime) -> timedelta:
    return now - datetime.fromisoformat(s["publish_at"])


def _too_late(s: dict, now: datetime) -> bool:
    return _late(s, now) > P.LATE_LIMIT


def _plat_rows(cfg: C.Config, acct: C.Account, s: dict, now: datetime) -> List[Tuple[str, str, str, str, str]]:
    """Per platform of a signed slot: (class, platform, what happened, link, link words)."""
    rows = []
    due = _late(s, now) >= timedelta(0)
    at = _hhmm(cfg, s["publish_at"])
    yt_cls, yt_hold = _hold_words(acct)
    meta_hold = O.meta_hold(acct)
    off = BC.slot_misses(acct, s, now)          # a cut outside its outlet's band as it is now (bandcheck.py)
    for plat in O.auto_platforms(acct) + O.hand_platforms(acct):    # what posts by itself first
        name = O.NAMES.get(plat, plat)
        rec = (s.get("outlets") or {}).get(plat) or {}
        if plat == "tiktok" and rec.get("post_id") and rec.get("privacy") == "SELF_ONLY":
            rows.append(("hand", name, "private test post (Only me): post it by hand", "", ""))
        elif plat == "tiktok" and rec.get("post_id"):
            seen = D.PRIVACY_NAMES.get(rec.get("privacy", ""), "")
            rows.append(("done", name, f"posted{f' ({seen})' if seen else ''}", rec.get("url", ""), "open"))
        elif plat == "tiktok" and rec.get("waiting"):
            rows.append(("wait", name, "posting now: TikTok can take a few minutes to process it", "", ""))
        elif plat == "tiktok" and rec.get("last_error") and not O.covers_slot(acct, plat, s):
            rows.append(("warn", name, f"not posted: {rec['last_error'][:220]}", "", ""))
        elif plat == "tiktok" and O.covers_slot(acct, plat, s) and D.tiktok_needs_choice(acct, s) \
                and not rec.get("gave_up"):
            rows.append(("warn", name, "waits: choose who can see it (below)", "", ""))
        elif not O.covers_slot(acct, plat, s):
            rows.append(("hand", name, "by hand", "", ""))
        elif plat == "youtube_shorts" and rec.get("video_id"):
            if datetime.fromisoformat(rec.get("publish_at") or s["publish_at"]) > now:
                rows.append(("ok", name, f"uploaded, goes public at {at}" + _cut_words(s, plat),
                             f"https://studio.youtube.com/video/{rec['video_id']}/edit", "Studio"))
            else:
                rows.append(("done", name, "posted", rec.get("url") or f"https://www.youtube.com/shorts/{rec['video_id']}", "open"))
        elif rec.get("post_id"):
            rows.append(("done", name, "posted", rec.get("url", ""), "open"))
        elif rec.get("capped"):
            # the outlet's daily_cap (poster.CAP_WHY): by design, not a failure, and no Try again
            rows.append(("wait", name, "skipped: " + str(rec.get("why") or "the day's cap"), "", ""))
        elif plat in off:
            # its cut no longer fits the band (10-21: Instagram 50 -> 40s): sending it again is refused again, so no
            # Try again for it; a new cut (igcut --pick), then Pull and Approve
            rows.append(("bad", name, BC.words(off[plat], acct), "", ""))
        elif rec.get("gave_up") and _no_cut(acct, s, plat) and not str(rec.get("why") or "").startswith("no "):
            # it gave up on the whole clip for another reason (Meta's upload error before its cut existed, 2026-10-17):
            # only its own cut can go now, which Try again cannot send. A "no ... cut" refusal says so itself
            rows.append(("bad", name, f"won't post: {_no_cut(acct, s, plat)}", "", ""))
        elif rec.get("gave_up") and P.upload_failed(rec.get("last_error") or ""):
            rows.append(("bad", name, f"not posted: Meta's upload service failed it {rec.get('tries')} times, on "
                                      f"their side. Try again sends the same file again", "", ""))
        elif rec.get("gave_up"):
            said = str(rec.get("why") or rec.get("last_error") or "gave up")
            said = said[len("not posted: "):] if said.startswith("not posted: ") else said   # said once, not twice
            rows.append(("bad", name, f"not posted: {said}", "", ""))
        elif _no_cut(acct, s, plat) and not due:
            # its time is still ahead: the night makes the cut and it joins the clip by itself (10-29, collect's late
            # cuts), so it waits in amber. 10-29 (Andrew: "a lot of red alerts"): every long Politics card was red all
            # evening, hours before the cuts were made
            rows.append(("warn", name, f"waits: {_no_cut(acct, s, plat)}. It joins this clip by itself when it is made",
                         "", ""))
        elif _no_cut(acct, s, plat):
            rows.append(("bad", name, f"won't post: {_no_cut(acct, s, plat)}", "", ""))
        elif plat in O.META and meta_hold:
            rows.append(("warn", name, "waits: Meta has paused this account's posts (see above)", "", ""))
        elif rec.get("waiting"):
            rows.append(("wait", name, f"posting: {name} is still publishing it", "", ""))
        elif rec.get("tries"):
            nxt = P.next_try(rec)
            when = f" about {_hhmm(cfg, nxt.isoformat())}" if nxt and nxt > now else ""
            rows.append(("warn", name, f"try {rec['tries']} of {P.max_tries(rec)} failed, tries again{when}: "
                                       f"{_fail_words(rec)}", "", ""))
        elif plat == "youtube_shorts" and yt_hold:
            rows.append((yt_cls, name, "waits: " + yt_hold.split(" after:")[0], "", ""))
        elif plat in ("facebook_reels", "instagram_reels") and not _meta_connected(acct):
            rows.append(("warn", name, f"waits: not connected (factory.py connect meta {acct.slug})", "", ""))
        elif plat == "youtube_shorts":
            rows.append(("wait", name, P.yt_wait_words(cfg, acct, s, now) + _cut_words(s, plat), "", ""))
        elif not due:
            rows.append(("wait", name, f"posts at {at}" + _cut_words(s, plat), "", ""))
        else:
            rows.append(("wait", name, "due: posts within 10 minutes" + _cut_words(s, plat), "", ""))
    return rows


def _fail_words(rec: dict) -> str:
    """A failed try in plain words: Meta's upload service failing an upload on its side is not the clip's fault."""
    err = str(rec.get("last_error") or "")
    if P.upload_failed(err):
        return "Meta's upload service failed it on their side (the same file usually goes through on a later try)"
    return err[:160]


def _waiting_cuts(s: dict, acct: Optional[C.Account] = None) -> List[str]:
    """Platforms whose own cut arrived after the clip was signed and is not on the slot yet (Pull, watch, Approve).
    With the account, a cut on the slot that no longer fits its band does not count as had: its new cut waits."""
    if s.get("status") not in B.SIGNED:
        return []
    off = BC.slot_misses(acct, s) if acct is not None else {}
    have = {O.NAMES.get(k, k) for k in (s.get("cuts") or {}) if k not in off}
    out = []
    for n in s.get("notes") or []:
        if "cut arrived after signing" in n:
            name = n.split(" a new ", 1)[-1].split(" cut arrived", 1)[0].strip()
            if name and name not in have and name not in out:
                out.append(name)
    return out


def _retryable(s: dict, acct: Optional[C.Account] = None) -> bool:
    """A platform gave up for a reason trying again can change: not a missing cut (that needs Pull and Approve). With
    the account, also not a platform that gave up on a whole clip it can only take as its own cut: Try again would send
    that clip again and be refused (2026-10-17: Instagram failed on the uncut clip, its cut waited, Try again re-sent
    the 76s clip; signoff.retry leaves such a platform alone)."""
    off = BC.slot_misses(acct, s) if acct is not None else {}
    for plat, rec in (s.get("outlets") or {}).items():
        if isinstance(rec, dict) and rec.get("gave_up") and not rec.get("post_id"):
            if rec.get("capped"):               # the day's cap, by design: Try again would be refused again
                continue
            why = str(rec.get("why") or rec.get("last_error") or "")
            if why.startswith("no ") and " cut" in why:
                continue
            if acct is not None and _no_cut(acct, s, plat):
                continue
            if plat in off:                     # its cut is outside its band now: it needs a new cut, not a resend
                continue
            return True
    return False


def _cut_words(s: dict, plat: str) -> str:
    """' (its own 58s cut)' when this platform gets a cut of its own."""
    cut = (s.get("cuts") or {}).get(plat)
    return f" (its own {cut['seconds']:.0f}s cut)" if cut and cut.get("seconds") else ""


def _no_cut(acct: C.Account, s: dict, plat: str) -> str:
    """Why this platform cannot take the slot's clip ('' when it can): too long for it, and no cut of its own."""
    if (s.get("cuts") or {}).get(plat):
        return ""
    lo, hi = O.band(acct, plat)
    secs = float((s.get("clip") or {}).get("seconds") or 0)
    if secs and not lo - 0.05 <= secs <= hi + 0.05:
        return f"needs its own {hi:.0f}s cut (the clip is {secs:.0f}s)"
    why = O.cut_rule(acct, s, plat)            # the Politics's YouTube: a network or C-SPAN clip over 60s
    if why:
        return f"needs its own {O.band(acct, plat, cut=True)[1]:.0f}s cut ({why})"
    return ""


def _state(cfg: C.Config, acct: C.Account, s: dict, now: datetime,
           day: Optional[date] = None) -> Tuple[str, str, str, str]:
    """(tag class, tag words, the time words, time class) for the card's top row."""
    st = s["status"]
    late = _late(s, now)
    if st == B.READY:
        from . import autosign as A        # the automatic sign-off: when this clip signs itself (autosign.py)
        auto = A.card_words(cfg, acct, s, now, _board_of(acct, day) if day else None).get("when")
        if late < timedelta(0):
            return "t-ready", "Ready", (f"{auto}, posts in {_span(-late)}" if auto else f"posts in {_span(-late)}"), ""
        if late <= P.LATE_LIMIT:
            if auto:
                return "t-ready", "Ready", f"its time passed: {auto}", "warn"
            return "t-ready", "Ready", "its time passed: goes out as soon as you approve", "warn"
        return "t-mute", "Ready", "too late to post", "bad"
    if st in B.SIGNED:
        rows = [r for r in _plat_rows(cfg, acct, s, now) if r[0] != "hand"]
        done = [r for r in rows if r[0] == "done"]
        bad = [r for r in rows if r[0] == "bad"]           # final: gave up
        if rows and len(done) == len(rows):
            return "t-done", "Posted", "", ""
        if bad and len(done) + len(bad) == len(rows):
            return "t-bad", "Not all posted", "", ""
        if late < timedelta(0):
            return "t-ok", "Scheduled", f"in {_span(-late)}", ""
        pending = [r for r in rows if r[0] not in ("done", "bad")]
        if pending and all(r[0] == "warn" for r in pending):
            return "t-warn", "Waiting", "", ""      # every platform still to go is held up: the rows say why
        return "t-ok", "Posting", "", ""
    if st == B.DECLINED:
        return "t-mute", "Declined", "", ""
    if st in (B.REFUSED, B.FAILED):
        return "t-bad", st.capitalize(), "", ""
    return "t-mute", st.capitalize(), "", ""


def _wrap(tag: str, cls: str, inner: str, attrs: str = "") -> str:
    """An element the page can swap on its own: data-h is a hash of everything in it."""
    h = hashlib.sha1(f"{cls}|{attrs}|{inner}".encode()).hexdigest()[:12]
    return f'<{tag} class="{cls}" data-h="{h}"{attrs}>{inner}</{tag}>'


def _top(cfg: C.Config, acct: C.Account, s: dict, now: datetime, where: str = "", day: Optional[date] = None) -> str:
    cls, words, when, wcls = _state(cfg, acct, s, now, day)
    when_html = f'<span class="when {wcls}">{esc(when)}</span>' if when else ""
    where_html = f'<span class="where">{esc(where)}</span>' if where else ""
    from . import bench as BN           # a spare clip the 06:18 fill put in: says so beside its state
    bench_html = '<span class="tag t-mute" title="a spare clip from the bench">Bench</span>' if BN.from_bench(s) else ""
    secs = (s.get("clip") or {}).get("seconds")
    signed = s.get("status") in B.SIGNED
    tile = ((f'<span class="len">{float(secs):.0f}s</span>' if secs else "")
            + LK.squares(_plat_rows(cfg, acct, s, now) if signed else [], signed, s, now))
    return _wrap("div", "top", f'{where_html}<span class="slot">{esc(s["slot"])}</span><span class="tag {cls}">{esc(words)}</span>{bench_html}{when_html}{tile}')


def _board_of(acct: C.Account, day: date) -> dict:
    """The slot's board, read once per change (the card asks it for the day's other slots)."""
    p = B.board_path(acct, day)
    try:
        m = p.stat().st_mtime_ns
    except OSError:
        return {"slots": {}}
    hit = _BOARDS.get(str(p))
    if hit and hit[0] == m:
        return hit[1]
    try:
        bd = json.loads(p.read_text())
    except (OSError, ValueError):
        return {"slots": {}}
    if len(_BOARDS) > 40:
        _BOARDS.clear()
    _BOARDS[str(p)] = (m, bd)
    return bd


_BOARDS: Dict[str, tuple] = {}


def _moment_words(cfg: C.Config, acct: C.Account, day: date, s: dict) -> Tuple[str, str, bool]:
    """(the big red stale block, the twin alert, whether Approve must not be offered) for one card."""
    st = s.get("status")
    big = ""
    if MO.stale(s):
        m = MO.stale(s)
        if st == B.READY:
            big = (f"STALE: DO NOT SIGN. {m.get('why') or ''} (marked by {m.get('by') or 'the night'}). Approve all "
                   f"and the automatic sign-off skip it; Decline it, or approve it on its own only if you are sure.")
        elif st in B.SIGNED:
            big = (f"STALE, marked after it was signed: {m.get('why') or ''} (by {m.get('by') or 'the night'}). "
                   f"Pull it to stop it.")
    twin, block = "", False
    try:
        board = _board_of(acct, day)
        pairs = MO.twins(cfg, acct, day, board).get(s.get("slot"), [])
    except Exception:  # noqa: BLE001  (a card never breaks over its twins)
        board, pairs = {"slots": {}}, []
    if pairs and st == B.READY:
        first = MO.signed_twin(board, pairs)
        other = MO.open_twin(board, pairs)
        if first:
            twin = (f"Same clip as {first[0]} ({first[1]}), which is signed. This one cannot be signed too: one "
                    f"moment posts once a day. Decline it.")
            block = True
        elif other:
            twin = (f"Same clip as {other[0]} ({other[1]}), also waiting. Only one of them can be signed: approve the "
                    f"one you want on its own. Approve all and the automatic sign-off skip both.")
    elif pairs and st in B.SIGNED:
        both = [j for j, _ in pairs if (board["slots"].get(j) or {}).get("status") in B.SIGNED]
        if both:
            twin = (f"Same clip as {', '.join(both)} ({pairs[0][1]}), which is signed too: both go out unless you Pull "
                    f"one.")
        else:
            twin = (f"Same clip as {', '.join(j for j, _ in pairs)} ({pairs[0][1]}), which cannot be signed now: only "
                    f"this one goes out.")
    return big, twin, block


def _bottom(cfg: C.Config, acct: C.Account, day: date, s: dict, now: datetime) -> str:
    st = s["status"]
    a, k = esc(acct.slug), esc(s["slot"])
    out: List[str] = []
    big, twin, no_approve = _moment_words(cfg, acct, day, s)
    if big:                          # the night's "stale, do not sign": big and red, first (moments.py)
        out.append(f'<div class="stale">{esc(big)}</div>')
    if twin:                         # one moment, one slot a day: it names the first slot
        out.append(f'<div class="alert">{esc(twin)}</div>')
    newer = MO.newer_waiting(s) if st in B.SIGNED else {}
    if newer and all(((s.get("outlets") or {}).get(p) or {}).get("post_id")
                     or ((s.get("outlets") or {}).get(p) or {}).get("video_id")
                     for p in O.auto_platforms(acct) if O.covers_slot(acct, p, s)):
        newer = {}                   # everything it posts by itself is out: a newer render has nothing left to replace
    if newer and not _too_late(s, now):
        out.append(f'<div class="ask">A newer render of this clip is ready '
                   f'({float(newer.get("seconds") or 0):.0f}s). Press Pull, watch it, then Approve: that sends it. '
                   f'Otherwise the signed one goes out.</div>')
    rp = s.get("replaced") or {}
    if st == B.READY and rp and rp.get("was_sha256") != (s.get("clip") or {}).get("sha256"):
        out.append(f'<div class="note">Replaced by a newer render of the same clip at {esc(_local(cfg, rp.get("at", "")))}'
                   f' (the earlier one was {float(rp.get("was_seconds") or 0):.0f}s). Watch it again.</div>')
    flag = P.flag_words(s)          # a platform got this clip in a way a person must look at (a Pull while it was sent)
    if flag:
        out.append(f'<div class="alert">{esc(flag)}</div>')
    said = _reason_words(s)         # why this clip was pulled or declined (reasons.py), and the kill list
    if said:
        out.append(f'<div class="note">{esc(said)}</div>')
    if s.get("clip") and st in B.SIGNED:
        problems = B.verify(s, cached=True)
        if problems:
            out.append(f'<div class="alert">Changed after signing: {esc("; ".join(problems))}. It will not post. '
                       f'Pull it, look at it, and approve it again.</div>')
    elif s.get("clip") and st == B.READY and _missing(acct, s):     # an edit before signing is fine: Approve signs
        out.append(f'<div class="alert">{esc(_missing(acct, s))} Make again to have it made fresh.</div>')
    if st == B.READY and s.get("clip") and not _too_late(s, now):
        from . import autosign as A        # a clip the automatic sign-off leaves for a person, and why
        waits = A.card_words(cfg, acct, s, now, _board_of(acct, day)).get("waits")
        if waits:
            out.append(f'<div class="ask">It will not sign itself: {esc(waits)}. Approve it to post it.</div>')
    cc = s.get("cast_check") or {}
    held, checked = CQ.cast_now(cc, cfg) if cc.get("people") and st == B.READY else ([], [])
    if held:
        note = CQ.hold_note(cc, cfg)
        out.append(f'<div class="alert">{esc(note[:1].upper() + note[1:])}</div>')
    elif checked and len(checked) == len(cc.get("people") or []):
        out.append(f'<div class="note">Cast checked by you since it was cut: {esc(", ".join(checked))}.</div>')
    if cc.get("people") and st == B.READY and not held and len(checked) < len(cc.get("people") or []):
        who = "; ".join(f'{p.get("name")} ({p.get("title")}), '
                        + ('marked checked by a bot, not by you' if p.get("by_bot") and not p.get("named_as")
                           else f'named in the title as "{p.get("named_as")}"')
                        for p in cc["people"])
        out.append(f'<div class="alert">Cast not checked yet: {esc(who)}. Watch it: is that who is on screen? '
                   f'Approving it is your cast check.</div>')
    elif cc.get("confirmed") and st in B.SIGNED:
        out.append(f'<div class="note">Cast confirmed at sign-off by {esc(cc["confirmed"].get("by", "?"))}: '
                   f'{esc(", ".join(str(p.get("name")) for p in cc.get("people") or []))}</div>')
    if s.get("lab"):                     # a Lab test: what Andrew saw next to what the clip says (lab.py)
        from . import lab as L
        out.append(L.card_lines(cfg, acct, s))
    from . import bench as BN
    bn = BN.from_bench(s)
    if bn and st in (B.READY,) + B.SIGNED:
        out.append(f'<div class="note">From the bench: cut {esc(str(bn.get("made_on") or "?"))}'
                   + (f' from {esc(str(bn.get("host")))}' if bn.get("host") else "")
                   + f', good until {esc(str(bn.get("evergreen_until") or "?"))}. Watch it as you would a new one.</div>')
    waiting = _waiting_cuts(s, acct)
    if waiting and not _too_late(s, now):
        out.append(f'<div class="ask">{esc(O._names(waiting))} cut ready. Press Pull, watch it, then Approve: '
                   f'that sends it (Try again cannot).</div>')
    plats_html = ""
    if st in B.SIGNED:
        so = s.get("signoff") or {}
        auto_ = " (automatic: nobody pressed Approve before its time)" if so.get("auto") else ""
        out.append(f'<div class="note">Signed by {esc(so.get("by", "?"))}, {esc(_local(cfg, so.get("at", "")))}'
                   f'{esc(auto_)}</div>')
        items = []
        recs = s.get("outlets") or {}
        by_name = {O.NAMES.get(p_, p_): p_ for p_ in recs}
        for cls, name, what, link, lw in _plat_rows(cfg, acct, s, now):
            rec = recs.get(by_name.get(name, ""), {}) if isinstance(recs.get(by_name.get(name, ""), {}), dict) else {}
            views = (rec.get("metrics") or {}).get("views")
            if link:      # the platform's name is the link
                head = f'<b><a href="{esc(link)}" target="_blank" rel="noopener" title="{esc(lw)}">{esc(name)}</a></b>'
            else:
                head = f'<b>{esc(name)}</b>'
            what_ = what + (f" · {_views(views)}" if isinstance(views, int) and cls == "done" else "")
            tail = ""
            if cls == "hand":
                tail = f'<button class="link" data-op="reveal" data-acct="{a}" data-slot="{k}">Show in Finder</button>'
            items.append(f'<li class="{cls}">{head}<span>{esc(what_)}</span>{tail}</li>')
        plats_html = f'<ul class="plats">{"".join(items)}</ul>'
    elif st in (B.DECLINED, B.REFUSED, B.FAILED) and B.last_note(s) and not said:
        out.append(f'<div class="note">{esc(B.last_note(s))}</div>')
    if st == B.READY and not _too_late(s, now):     # before signing: a platform this clip will not reach, and why
        off = BC.slot_misses(acct, s, now)
        ahead = _late(s, now) < timedelta(0)     # a cut still to come joins by itself (10-29): amber, not red
        nocut = [(O.NAMES.get(p, p), ("warn", f"waits: {_no_cut(acct, s, p)}. It joins this clip by itself when it is "
                                              "made") if ahead else ("bad", "won't post: " + _no_cut(acct, s, p)))
                 for p in O.auto_platforms(acct) if _no_cut(acct, s, p)]
        nocut += [(O.NAMES.get(p, p), ("bad", "won't post: " + BC.words(m, acct))) for p, m in off.items()]
        if nocut:
            plats_html = '<ul class="plats">' + "".join(
                f'<li class="{c}"><b>{esc(n)}</b><span>{esc(w)}</span></li>' for n, (c, w) in nocut) + '</ul>'
    panel = _tt_panel(acct, s)
    btns: List[str] = []
    panels: List[str] = []
    future = _late(s, now) < timedelta(0)
    if st == B.READY and not _too_late(s, now) and not _missing(acct, s) and not held and not no_approve:
        btns.append(f'<button class="go" data-op="approve" data-acct="{a}" data-slot="{k}">Approve</button>')
    if st == B.READY:
        btns.append(f'<button data-op="why" data-why="decline" data-acct="{a}" data-slot="{k}">Decline</button>')
        panels.append(_why_panel("decline", a, k, f"Decline {s['slot']}: why?"))
    if st in (B.READY, B.DECLINED, B.REFUSED, B.FAILED) and future:
        btns.append(f'<button data-op="remake" data-acct="{a}" data-slot="{k}">Make again</button>')
    if st in B.SIGNED:
        rows = _plat_rows(cfg, acct, s, now)
        if any(r[0] == "bad" for r in rows) and _retryable(s, acct) and not _too_late(s, now) and not B.verify(s, cached=True):
            btns.append(f'<button class="go" data-op="retry" data-acct="{a}" data-slot="{k}">Try again</button>')
        wants_pull = bool(waiting or newer) and not _too_late(s, now)
        if any(r[0] not in ("done", "bad", "hand") for r in rows) or wants_pull:
            go = ' class="go"' if wants_pull else ""
            btns.append(f'<button{go} data-op="why" data-why="pull" data-acct="{a}" data-slot="{k}">Pull</button>')
            panels.append(_why_panel("pull", a, k, _pull_ask(s), plain_first=wants_pull))
    if btns and acct.raw.get("owner") == "client":
        from . import accounts as AC        # a client the owner does not sign: its buttons are on its own page
        if not AC.signs_here(cfg, acct):
            out.append(f'<div class="note">Signed by {esc(", ".join(AC.approvers(cfg, acct)))} on its own page.</div>')
            btns, panels = [], []
    if btns:
        out.append(f'<div class="btns">{"".join(btns)}</div>')
    out += panels
    out.append(plats_html)
    if panel:          # TikTok's posting screen, folded: open it to post or to choose who can see it
        open_ = " open" if st in B.SIGNED and D.tiktok_needs_choice(acct, s) and O.covers_slot(acct, "tiktok", s) else ""
        out.append(f'<details class="tt-wrap" data-k="tt"{open_}><summary>TikTok</summary>{panel}</details>')
    out.append(_soc("card_parts", "", cfg, acct, day, s, now))     # the social parts (coach, first comment ...)
    return _wrap("div", "bottom", "".join(out))


def _reason_words(s: dict) -> str:
    """'Pulled by Andrew: HOOK (slow open). On the kill list (...): ...' for the clip in the slot now ('' when it
    was not pulled or declined with a reason, or the slot holds another clip since)."""
    r = s.get("reason") or {}
    if not r or r.get("clip") != (s.get("clip") or {}).get("sha256") or s.get("status") not in (B.READY, B.DECLINED):
        return ""
    verb = {"pull": "Pulled", "decline": "Declined"}.get(str(r.get("op")))
    if not verb or (verb == "Pulled" and s.get("status") != B.READY):
        return ""
    w = R.words(R.BY_KEY.get(str(r.get("code") or "")), str(r.get("text") or ""))
    kill = str(r.get("kill") or "")
    return (f"{verb} by {r.get('by', '?')}" + (f": {w}" if w else "") + "."
            + (f" {kill[:1].upper()}{kill[1:]}." if kill else ""))


def _why_panel(op: str, a: str, k: str, ask: str, plain_first: bool = False) -> str:
    """The reasons under Decline or Pull (reasons.CODES): one tap each, or keys 1 to 9 and 0 on the card, then Enter.
    Folded until its button opens it; the page keeps it open through its 15-second refresh (data-k)."""
    verb = "Decline" if op == "decline" else "Pull"
    chips = []
    for i, c in enumerate(x for x in R.CODES if x.key != "other"):
        n = (i + 1) % 10
        gate = R.GATES.get(c.gate, ("",))[0]
        tip = f"{c.what}. The check that should have caught it: the {gate}" + (". Counts as hard" if c.hard else "")
        chips.append(f'<button type="button" class="code{" hard" if c.hard else ""}" data-op="{op}" '
                     f'data-code="{c.key}" data-n="{n}" data-acct="{a}" data-slot="{k}" title="{esc(tip)}">'
                     f'<kbd>{n}</kbd>{esc(c.label)}</button>')
    go = ' class="go"' if plain_first else ""
    plain = (f'<button{go} data-op="pull" data-acct="{a}" data-slot="{k}" type="button">Pull, no reason</button>'
             if op == "pull" else "")
    return (f'<details class="why" data-k="why-{op}"><summary>{verb}</summary>'
            f'<div class="why-head">{esc(ask)}</div><div class="codes">{"".join(chips)}</div>'
            f'<div class="why-other"><input type="text" maxlength="400" placeholder="Other: say what" '
            f'aria-label="Other reason to {verb.lower()}"><button type="button" data-op="{op}" data-code="other" '
            f'data-acct="{a}" data-slot="{k}">{verb}</button></div>'
            f'<div class="why-foot">{plain}<button type="button" class="link" data-op="why-close">Cancel</button>'
            f'<span class="why-keys">Keys: a number, then Enter</span></div></details>')


def _views(n: int) -> str:
    """1234 -> '1.2K views'."""
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M views".replace(".0M", "M")
    if n >= 10_000:
        return f"{n / 1000:.0f}K views"
    if n >= 1000:
        return f"{n / 1000:.1f}K views".replace(".0K", "K")
    return f"{n} view{'s' if n != 1 else ''}"


def _tt_panel(acct: C.Account, s: dict) -> str:
    """TikTok's posting screen for one clip, as TikTok's Direct Post rules ask: the account's nickname, the caption
    (editable until signed), who can see it (no default), comments/duet/stitch (all off until ticked), the commercial
    content disclosure, TikTok's consent line, and Post to TikTok now on a signed clip. '' when TikTok isn't
    connected for the account or the clip has no TikTok words."""
    st = s["status"]
    c = (s.get("copy") or {}).get("tiktok")
    rec = (s.get("outlets") or {}).get("tiktok") or {}
    if not c or not s.get("clip") or st not in B.SIGNABLE + B.SIGNED or rec.get("post_id") or rec.get("waiting") \
            or not D.tiktok_connected(acct):
        return ""
    a, k = esc(acct.slug), esc(s["slot"])
    try:
        info = D.creator_info(acct)
    except Exception as e:  # noqa: BLE001  (offline, expired sign-in, TikTok says not now)
        return (f'<div class="tt" data-acct="{a}" data-slot="{k}"><div class="tt-head"><b>TikTok</b></div>'
                f'<div class="tt-why">Can\'t reach TikTok for this account right now: {esc(str(e)[:200])}</div></div>')
    choice = s.get("tiktok_post") or {}
    audited = bool(acct.outlets.get("tiktok", {}).get("api_app_audited"))
    try:
        caption = T.for_platform(Path(c["path"]).read_text(), "tiktok")["caption"]
    except OSError:
        caption = ""
    nick = info.get("creator_nickname") or info.get("creator_username") or "?"
    handle = f' @{esc(info["creator_username"])}' if info.get("creator_username") else ""
    out = [f'<div class="tt-head"><b>TikTok</b><span>Posting as <strong>{esc(nick)}</strong>{handle}</span></div>']
    if st in B.SIGNABLE:
        out.append(f'<textarea class="tt-cap" maxlength="2200" aria-label="TikTok caption">{esc(caption)}</textarea>'
                   f'<div class="tt-row"><button class="link" data-op="tt-caption" data-acct="{a}" data-slot="{k}">'
                   f'Save caption</button><span class="tt-off">{len(caption)}/2200</span></div>')
    else:
        out.append(f'<div class="tt-capro">{esc(caption)}</div>')
    priv = str(choice.get("privacy_level") or "")
    branded = bool(choice.get("commercial") and choice.get("brand_content"))
    opts = [f'<option value=""{" selected" if not priv else ""} disabled>Choose who can see this video</option>']
    for o in info.get("privacy_level_options") or []:
        dis = ' disabled title="Branded content visibility cannot be set to private."' if branded and o == "SELF_ONLY" else ""
        opts.append(f'<option value="{esc(o)}"{" selected" if o == priv else ""}{dis}>'
                    f'{esc(D.PRIVACY_NAMES.get(o, o))}</option>')
    out.append(f'<label class="tt-field"><span>Who can see this video</span>'
               f'<select data-tt="privacy_level" aria-label="Who can see this video">{"".join(opts)}</select></label>')
    for flag, label, off in (("allow_comment", "Allow comments", "comment_disabled"),
                             ("allow_duet", "Allow Duet", "duet_disabled"), ("allow_stitch", "Allow Stitch", "stitch_disabled")):
        if info.get(off):
            out.append(f'<label class="tt-check tt-greyed" title="Turned off in this account\'s TikTok settings">'
                       f'<input type="checkbox" disabled> {esc(label)}</label>')
        else:
            out.append(f'<label class="tt-check"><input type="checkbox" data-tt="{flag}"'
                       f'{" checked" if choice.get(flag) else ""}> {esc(label)}</label>')
    out.append(f'<label class="tt-check"><input type="checkbox" data-tt="commercial"'
               f'{" checked" if choice.get("commercial") else ""}> Disclose video content</label>')
    if choice.get("commercial"):
        out.append(f'<div class="tt-sub"><label class="tt-check"><input type="checkbox" data-tt="brand_organic"'
                   f'{" checked" if choice.get("brand_organic") else ""}> Your brand</label>'
                   f'<div class="tt-off">You are promoting yourself or your own business.</div>'
                   f'<label class="tt-check"><input type="checkbox" data-tt="brand_content"'
                   f'{" checked" if choice.get("brand_content") else ""}> Branded content</label>'
                   f'<div class="tt-off">You are promoting another brand or a third party.</div>')
        if choice.get("brand_content"):
            out.append('<div class="tt-label">Your video will be labeled as \'Paid partnership\'</div>')
        elif choice.get("brand_organic"):
            out.append('<div class="tt-label">Your video will be labeled as \'Promotional content\'</div>')
        out.append('</div>')
    music = f'<a href="{D.MUSIC_URL}" target="_blank" rel="noopener">Music Usage Confirmation</a>'
    if branded:
        legal = (f'By posting, you agree to TikTok\'s <a href="{D.BRANDED_URL}" target="_blank" rel="noopener">'
                 f'Branded Content Policy</a> and {music}.')
    else:
        legal = f"By posting, you agree to TikTok's {music}."
    out.append(f'<div class="tt-legal">{legal}</div>')
    problems = D.tiktok_problems(choice, info, float((s.get("clip") or {}).get("seconds") or 0), audited)
    if st in B.SIGNED:
        why = " ".join(problems)
        out.append(f'<span class="tt-go" title="{esc(why)}"><button class="go" data-op="tt-now" data-acct="{a}" '
                   f'data-slot="{k}"{" disabled" if problems else ""}>Post to TikTok now</button></span>')
        if problems:
            out.append(f'<div class="tt-why">{esc(why)}</div>')
        if rec.get("last_error"):
            out.append(f'<div class="tt-why">Last try: {esc(rec["last_error"][:300])}</div>')
    elif problems and priv:
        out.append(f'<div class="tt-why">{esc(" ".join(problems))}</div>')
    return f'<div class="tt" data-acct="{a}" data-slot="{k}">{"".join(out)}</div>'


def _missing(acct: C.Account, s: dict) -> str:
    """'' when the clip and every platform's words are on disk; else one plain sentence saying what is not."""
    if not Path((s.get("clip") or {}).get("path", "")).is_file():
        return "The clip's file is missing from the drafts."
    gone = [O.NAMES.get(p, p) for p, c in sorted((s.get("copy") or {}).items()) if not Path(c.get("path", "")).is_file()]
    return f"The words for {O._names(gone)} are missing from the drafts." if gone else ""


def _pull_ask(s: dict) -> str:
    ask = f"Pull {s['slot']}? Nothing more of it goes out, and it is ready to sign again."
    outl = s.get("outlets") or {}
    if (outl.get("youtube_shorts") or {}).get("video_id"):
        ask += " YouTube already has it: make it private in YouTube Studio (the link is on the card)."
    posted = [O.NAMES.get(p, p) for p, r in outl.items() if r.get("post_id") and p != "youtube_shorts"]
    if posted:
        ask += f" Already posted on {', '.join(posted)}: that stays up."
    return ask


def _file_url(p: str) -> str:
    try:
        st = os.stat(p)
        v = f"{st.st_size:x}{st.st_mtime_ns:x}"
    except OSError:
        v = "0"
    return f"/file?p={urllib.parse.quote(p)}&v={v}"


def _card(cfg: C.Config, acct: C.Account, day: date, s: dict, now: datetime, band: bool = False) -> dict:
    cid = (f"n-{acct.slug}-{day.strftime('%Y%m%d')}-{s['slot'].replace(':', '')}" if band
           else f"c-{acct.slug}-{s['slot'].replace(':', '')}")
    top = _top(cfg, acct, s, now, (acct.name + " · " + _day_label(day, now.date())) if band else "", day)
    # The seen key FIRST, before anything below reads the slot's files (the card parts read the extras file): a write
    # landing while the card renders then leaves the page holding a stale key, and Approve refuses it.
    key = B.seen_key(s)
    bottom = _bottom(cfg, acct, day, s, now)
    clip = s["clip"]["path"]
    art = (s.get("cover_art") or {}).get("path") if isinstance(s.get("cover_art"), dict) else None
    cover = art if art and Path(art).is_file() else s.get("cover")       # the designed cover, else the poster
    if cover and Path(cover).is_file():
        video = (f'<video controls playsinline preload="none" poster="{esc(_file_url(cover))}" '
                 f'src="{esc(_file_url(clip))}" aria-label="{esc(acct.name)} {esc(s["slot"])}"></video>')
    else:   # no cover: let the browser fetch enough to show a frame
        video = (f'<video controls playsinline preload="metadata" src="{esc(_file_url(clip))}#t=0.1" '
                 f'aria-label="{esc(acct.name)} {esc(s["slot"])}"></video>')
    meta = [s.get("format", "")]
    if s.get("clip", {}).get("seconds"):
        meta.append(f"{s['clip']['seconds']:.0f}s")
    if s.get("host"):
        meta.append(s["host"])
    words = []
    for plat, c in (s.get("copy") or {}).items():
        try:
            w = T.for_platform(Path(c["path"]).read_text(), plat)
        except OSError:
            w = {"title": "", "caption": "(the words file is missing)", "tags": ""}
        head = f"{w['title']}\n\n" if w.get("title") else ""
        tags = f"\n\nTags: {w['tags']}" if plat == "youtube_shorts" and w.get("tags") else ""
        words.append(f'<div class="copy"><b>{esc(O.NAMES.get(plat, plat))}</b>{esc(head + w["caption"] + tags)}</div>')
    cuts = []
    for plat, cut in sorted((s.get("cuts") or {}).items()):      # a platform's own shorter cut: watch it before signing
        cuts.append(f'<details class="cut"><summary>{esc(O.NAMES.get(plat, plat))} gets its own '
                    f'{float(cut.get("seconds") or 0):.0f}s cut: watch it</summary>'
                    f'<video controls playsinline preload="none" src="{esc(_file_url(cut["path"]))}" '
                    f'aria-label="{esc(acct.name)} {esc(s["slot"])} {esc(O.NAMES.get(plat, plat))} cut"></video></details>')
    still = f'<div class="title">{esc(_title(s))}</div><div class="meta">{esc(" · ".join(m for m in meta if m))}</div>'
    after = ("".join(cuts)
             + (f'<details><summary>Words for each platform</summary>{"".join(words)}</details>' if words else ""))
    whole = (f'<article class="card{" band" if band else ""}" id="{cid}" data-key="{key}" data-slot="{esc(s["slot"])}" '
             f'data-acct="{esc(acct.slug)}" data-date="{day.isoformat()}">'
             f'{top}{video}<div class="body">{still}{bottom}{after}</div></article>')
    return {"id": cid, "key": key, "top": top, "bottom": bottom, "html": whole, "big": _needs_you(s, now)}


def _needs_you(s: dict, now: datetime) -> bool:
    """A clip waiting for the sign-off that can still go out: shown large, at the top of its account."""
    try:
        return s["status"] in B.SIGNABLE and not _too_late(s, now)
    except (KeyError, TypeError, ValueError):
        return False


def _readable(s: dict) -> bool:
    try:
        datetime.fromisoformat(s["publish_at"])
        return True
    except (KeyError, TypeError, ValueError):
        return False


def _safe_card(cfg: C.Config, acct: C.Account, day: date, s: dict, now: datetime, band: bool = False) -> dict:
    """A card, or, when one slot on the board is broken, a card that says so: one bad slot never takes the page down."""
    try:
        return _card(cfg, acct, day, s, now, band)
    except (Exception, SystemExit) as e:  # noqa: BLE001  (a part's refusal on one card is said on that card)
        cid = (f"n-{acct.slug}-{day.strftime('%Y%m%d')}-" if band else f"c-{acct.slug}-") + str(s.get('slot', '')).replace(':', '')
        top = _wrap("div", "top", f'<span class="slot">{esc(s.get("slot", "?"))}</span>'
                                  f'<span class="tag t-bad">Cannot show</span>')
        bottom = _wrap("div", "bottom", f'<div class="alert">This slot could not be shown '
                                        f'({esc(type(e).__name__)}: {esc(str(e)[:200])}). Its board is '
                                        f'state/{esc(acct.slug)}/{day}.json.</div>')
        key = "broken-" + hashlib.sha1(str(e).encode()).hexdigest()[:8]
        html_ = (f'<article class="card" id="{cid}" data-key="{key}" data-slot="{esc(s.get("slot", ""))}" '
                 f'data-acct="{esc(acct.slug)}" data-date="{day.isoformat()}">{top}<div class="body">{bottom}</div></article>')
        return {"id": cid, "key": key, "top": top, "bottom": bottom, "html": html_, "big": False}


# ------------------------------------------------------------------------------------------------------ one account
def _section(cfg: C.Config, acct: C.Account, day: date, now: datetime) -> dict:
    bd = B.read(acct, day)
    sid = f"acct-{acct.slug}"
    today = now.date()
    slots = sorted((bd or {}).get("slots", {}).values(), key=lambda s: s["slot"])
    slots = [s for s in slots if not B.empty_spare(s)]      # an empty spare slot (16 to 20) is fine: never "No clip"
    with_clip = [s for s in slots if s.get("clip") and s["status"] not in (B.OPEN, B.MAKING)]
    cards = [_safe_card(cfg, acct, day, s, now) for s in with_clip]
    # the head: counts and Approve all
    pairs = MO.twins(cfg, acct, day, bd) if bd else {}
    ready = [s for s in slots if s["status"] in B.SIGNABLE and _readable(s) and not _too_late(s, now)
             and not _missing(acct, s) and not MO.bulk_skip(bd, s["slot"], pairs.get(s["slot"], []))
             and not MO.refuse_second(bd, pairs.get(s["slot"], []))]
    groups: Dict[str, int] = {}
    for s in slots:
        if s["status"] in B.SIGNED:
            g = "posted" if _readable(s) and _state(cfg, acct, s, now)[1] == "Posted" else "scheduled"
        elif s["status"] in (B.OPEN, B.MAKING):
            g = "no clip"
        elif s["status"] == B.READY:
            g = "to review"
        else:
            g = s["status"]
        groups[g] = groups.get(g, 0) + 1
    order = ["to review", "scheduled", "posted", "declined", "refused", "failed", "no clip"]
    counts = " · ".join(f"{groups[g]} {g}" for g in order if groups.get(g))
    ask = (f"Approve all {len(ready)} {acct.name} clips? Each one goes out by itself at its time "
           f"({O.routes(acct)}).")
    btn = (f'<button class="go" data-op="approve-all" data-acct="{esc(acct.slug)}" data-date="{day.isoformat()}" '
           f'data-ask="{esc(ask)}">Approve all {len(ready)}</button>' if len(ready) > 1 else "")
    head = _wrap("div", "acct-head", f'{LK.logo(acct)}<h2>{esc(acct.name)}</h2>'
                                     f'{LK.progress(groups.get("posted", 0), len(with_clip))}'
                                     f'<span class="counts">{esc(counts)}</span>{_links(acct)}<span class="grow"></span>{btn}')
    # where it goes, and anything stopping it
    notes = [f'<span>{esc(O.routes(acct))}.</span>']
    cls, hold = _hold_words(acct)
    if hold:
        notes.append(f'<span class="{cls}">{esc(hold)}.</span>')
    if O.meta_hold(acct):
        notes.append(f'<span class="bad">{esc(O.meta_hold(acct))}</span>')
    band = BC.summary(acct, BC.misses(cfg, acct, [day], now)) if bd else ""
    if band:                         # cuts the band no longer takes (bandcheck.py): before the bot's note
        notes.append(f'<span class="bad">{esc(band)}</span>')
    if any(O.mode(acct, p) == "api" for p in ("facebook_reels", "instagram_reels")) and not _meta_connected(acct):
        notes.append(f'<span class="bad">Facebook and Instagram are not connected (factory.py connect meta {esc(acct.slug)}).</span>')
    if bd and bd.get("report"):
        note = " ".join(str(bd["report"]).split())
        first, _, rest = note.partition(". ")
        if rest and len(note) > 180:      # the first sentence shows (CAST TO CONFIRM leads it), the rest on a click
            notes.append(f'<details class="report"><summary>The bot\'s note: {esc(first)}.</summary>'
                         f'<div>{esc(rest)}</div></details>')
        else:
            notes.append(f'<span class="report">The bot\'s note: {esc(note)}</span>')
    routes = _wrap("div", "routes", " ".join(notes))
    # the slots with no clip, on one line
    empty = [s for s in slots if s["status"] in (B.OPEN, B.MAKING)]
    tomorrow = today + timedelta(days=1)
    drop = f"drop a finished clip in Clip Drop › {acct.name}"      # an account nothing cuts for (2026-10-29)
    ds = acct.day_shift()
    run = f"the {ds[0]} day shift" if ds else "tonight's run"      # an account that cuts in the day (2026-10-29)
    if not bd:
        inner = (f"Nothing cuts for {acct.name} by itself yet: {drop}." if acct.fed_by_hand() and day >= today else
                 f"No board for this day yet: {run} makes it." if day == tomorrow else
                 "Not planned yet." if day > tomorrow else "Nothing was planned for this day.")
    elif empty:
        why = (drop if acct.fed_by_hand() and day >= today else
               f"{run} makes them" if day == tomorrow else "not made yet" if day > tomorrow else
               "not made" if day == today else "nothing was made for these")
        items = " · ".join(f'<span title="{esc(B.last_note(s))}">{esc(s["slot"])}'
                           f'{" (being made)" if s["status"] == B.MAKING else ""}</span>' for s in empty)
        inner = f"<b>No clip</b> {items} <span>({why})</span>"
    else:
        inner = ""
    return {"id": sid, "head": head, "routes": routes, "cards": cards, "empty": _wrap("div", "empties", inner)}


def _sections(cfg: C.Config, day: date, now: datetime, acct: str = "all") -> List[dict]:
    return [_section(cfg, a, day, now) for a in _accts(cfg, acct)]


# ------------------------------------------------------------------------------------------------------ needs you
def _need_why(cfg: C.Config, acct: C.Account, s: dict, now: datetime) -> str:
    """Why this slot needs the person now ('' when it does not): sign it, fix it, or take a waiting cut."""
    st = s.get("status")
    try:
        if not s.get("clip") or _too_late(s, now):
            return ""
        if st in B.SIGNABLE:
            return "make" if _missing(acct, s) else "sign"
        if st in B.SIGNED:
            if not any(r[0] not in ("done", "hand") for r in _plat_rows(cfg, acct, s, now)):
                return ""                       # everything it can post is out: nothing left to do
            if B.verify(s, cached=True):
                return "changed"
            if _waiting_cuts(s, acct) or MO.newer_waiting(s):
                return "cut"
            if _retryable(s, acct) and any(r[0] == "bad" for r in _plat_rows(cfg, acct, s, now)):
                return "retry"
    except (KeyError, TypeError, ValueError, OSError):
        return ""
    return ""


def _needs(cfg: C.Config, now: datetime, acct: str = "all") -> dict:
    """Everything that needs the person, from yesterday's boards on (a slot can still post 20h late), as big cards;
    plus, when there is nothing, when the next clip goes out."""
    today = now.date()
    cards, groups, nxt = [], [], None
    for a in _accts(cfg, acct):
        for path in B.all_boards(a):
            day = date.fromisoformat(path.stem)
            if day < today - timedelta(days=1):
                continue
            bd = B.read(a, day) or {}
            slots = sorted((bd.get("slots") or {}).values(), key=lambda x: str(x.get("slot", "")))
            sign = []
            for x in slots:
                why = _need_why(cfg, a, x, now)
                if why:
                    c = _safe_card(cfg, a, day, x, now, band=True)
                    c.update(acct=a.slug, day=day.isoformat(), slot=x["slot"], why=why)
                    cards.append(c)
                    if why == "sign":
                        sign.append(x["slot"])
                elif x.get("status") in B.SIGNED and _readable(x):
                    at = datetime.fromisoformat(x["publish_at"])
                    if at > now and (nxt is None or at < nxt[0]):
                        nxt = (at, a.name)
            if len(sign) > 1:
                ask = (f"Approve all {len(sign)} {a.name} clips for {_day_label(day, today)}? Each one goes out by itself "
                       f"at its time ({O.routes(a)}).")
                groups.append(f'<button class="go" data-op="approve-all" data-acct="{esc(a.slug)}" '
                              f'data-date="{day.isoformat()}" data-ask="{esc(ask)}">Approve all {len(sign)} · '
                              f'{esc(_short(a))} {esc(_day_label(day, today).split(" · ")[0])}</button>')
    cards.sort(key=lambda c: (c["day"], c["slot"], c["acct"]))
    if cards:
        head = (f'<h2>Needs you <span class="n">{len(cards)}</span></h2><span class="grow"></span>' + "".join(groups))
    else:
        when = f"Next post {nxt[0].astimezone(cfg.tz()).strftime('%H:%M')}, {nxt[1]}." if nxt else "Nothing is scheduled."
        head = f'<h2>Needs you</h2><span class="calm">All signed. {esc(when)}</span>'
    try:                                    # what is rendering now; red only when a holder is past its limit
        from . import renderlock as RL
        level, busy = RL.health_words(cfg)
    except Exception:  # noqa: BLE001  (the page never goes down over the render queue)
        level, busy = "", ""
    if busy:
        head = (f'<div class="alert health" role="alert">{esc(busy)}</div>' if level == "stuck"
                else f'<div class="calm health">{esc(busy)}</div>') + head
    try:                                    # the scouts' last run: how many sources answered (scouthealth.py)
        from . import scouthealth as SH
        scout_level, scout_words = SH.review_line(cfg, now, _accts(cfg, acct))
    except Exception:  # noqa: BLE001  (the page never goes down over the scout)
        scout_level, scout_words = "", ""
    if scout_words:
        head = (f'<div class="alert health" role="alert">{esc(scout_words)}</div>' if scout_level == "bad"
                else f'<div class="calm health">{esc(scout_words)}</div>') + head
    for line in _soc("health_lines", [], cfg, now, _accts(cfg, acct)):    # the social layer: holds, a stop, the inbox
        try:
            level, words = line
        except (TypeError, ValueError):
            continue
        if words:
            head = (f'<div class="alert health" role="alert">{esc(words)}</div>' if level == "bad"
                    else f'<div class="calm health">{esc(words)}</div>') + head
    broke = P.health_words(cfg, now)        # posting stopped or broke: one red line above everything
    if broke:
        head = f'<div class="alert health" role="alert">{esc(broke)}</div>' + head
    return {"head": _wrap("div", "needs-head", head), "cards": cards,
            "keys": sorted(f'{c["acct"]}|{c["day"]}|{c["slot"]}' for c in cards)}


# ------------------------------------------------------------------------------------------------------ the social layer
def _soc(name: str, default, *a):
    """social.<name>(*a) (social.py: card parts, health lines, the Today band, calendar items), or `default` when the
    social layer is not built, raises (logged once in state/logs/social-errors.log) or answers in another shape: the
    Clips page never goes down over it."""
    try:
        got = REG.calls(("social:" + name,), *a)
    except (Exception, SystemExit):  # noqa: BLE001  (the page never goes down over the social layer)
        return default
    if not got or not isinstance(got[0][1], type(default)):
        return default
    return got[0][1]


def _brief(cfg: C.Config, now: datetime, acct: str) -> str:
    """The Today band at the top of Clips (social.brief's chips, each a link), or '' when it has nothing to say."""
    chips = _soc("brief", [], cfg, now, acct)
    inner = SX.brief_html(chips, acct)
    return _wrap("section", "sx-brief", inner, ' id="brief" aria-label="Today"') if inner else ""


def _reasons() -> list:
    """The reasons Decline and Make again offer ([[code, label]], verdicts.CHIPS), or none until that part is built."""
    mod = REG.load("verdicts")
    chips = getattr(mod, "CHIPS", None) if mod is not None else None
    if not isinstance(chips, (list, tuple)):
        return []
    return [[str(c[0]), str(c[1])] for c in chips if isinstance(c, (list, tuple)) and len(c) == 2]


def _decline(cfg: C.Config, acct: C.Account, day: date, slots: List[str], by: str, reason: str, remake: bool,
             reasons: List[str], code: Optional[str] = None) -> List[str]:
    """signoff.decline with the code he tapped (Decline) or the chips he picked (Make again); an unknown code is
    refused there, before anything changes."""
    return S.decline(cfg, acct, day, slots, by, reason, remake=remake, reasons=reasons, code=code)


# ------------------------------------------------------------------------------------------------------ the calendar
def _slot_kind(cfg: C.Config, acct: C.Account, s: dict, now: datetime) -> str:
    """One slot as the calendar paints it: posted, scheduled, review, problem or empty."""
    st = s.get("status")
    try:
        if st in B.SIGNED:
            words = _state(cfg, acct, s, now)[1]
            return {"Posted": "posted", "Not all posted": "problem"}.get(words, "scheduled")
        if st in B.SIGNABLE and s.get("clip"):
            return "problem" if _too_late(s, now) else "review"
    except (KeyError, TypeError, ValueError):
        return "problem"
    if st in (B.REFUSED, B.FAILED):
        return "problem"
    return "empty"                  # open, being made or declined: nothing goes out in it


def calendar(cfg: C.Config, now: datetime, acct: str = "all") -> dict:
    """Every day with a board: per account, its slots in clock order as kinds (the strip the calendar draws)."""
    days: Dict[str, dict] = {}
    for a in _accts(cfg, acct):
        for path in B.all_boards(a):
            try:
                bd = B.read(a, date.fromisoformat(path.stem)) or {}
            except (OSError, ValueError):
                continue
            slots = sorted((bd.get("slots") or {}).values(), key=lambda x: str(x.get("slot", "")))
            kinds = [_slot_kind(cfg, a, x, now) for x in slots if not B.empty_spare(x)]   # an empty spare is no gap
            if kinds:
                days.setdefault(path.stem, {})[a.slug] = kinds
    out = {"days": days, "today": now.date().isoformat(),
           "accounts": [{"id": a.slug, "short": _short(a), "name": a.name} for a in _accts(cfg, acct)]}
    items = _soc("cal_items", {}, cfg, now, _accts(cfg, acct))    # cards and stories: a thin second strip
    if items:
        out["items"] = items
    if any(p.key == "week" for p in REG.children("clips")):     # the Week page is on the plug board and built
        out["week"] = True
    return out


def _stamp(sections: List[dict], cal: Optional[dict] = None, needs: Optional[dict] = None,
           casts: Optional[dict] = None, brief: Optional[str] = None) -> str:
    parts = [sections, cal, needs] + ([casts] if casts is not None else []) + ([brief] if brief else [])
    return hashlib.sha1(json.dumps(parts, sort_keys=True).encode()).hexdigest()[:16]


def tabs(current: str, acct: str = "all", cfg: Optional[C.Config] = None) -> str:
    """Clips | Stats | each social tab that is built (registry.py), at the top of every page (the account picked goes
    with them). A page under a tab (Week under Clips, Automations under Inbox) marks its tab. Lab too when an account
    whose maker is "lab" runs on this machine, switched on or not (recording builds its stock before switch-on)."""
    q = "" if acct == "all" else "?acct=" + urllib.parse.quote(acct)
    top = current if current in ("calendar", "money") else REG.top_of(current)
    links = [(key, url + q, words) for key, url, words in REG.tab_pages()]
    links.insert(1, ("calendar", "/calendar" + q, "Calendar"))       # the month, each day's clips and views (look.py)
    at = next((i for i, x in enumerate(links) if x[0] == "stats"), 1)
    links.insert(at + 1, ("money", "/money" + q, "Money"))         # what pays, and the coach (moneypage.py)
    if cfg is not None:
        from . import lab as L
        if L.lab_accounts(cfg):
            links.append(("lab", "/lab", "Lab"))
    return ('<nav class="tabs" aria-label="Pages">' + "".join(
        f'<a href="{esc(href)}"{" aria-current=page" if key == top else ""}>{words}</a>' for key, href, words in links)
        + "</nav>")


def switch(cfg: C.Config, current: str, acct: str, day: Optional[date] = None) -> str:
    """All | each account (Gaming | Politics ...): whose clips and numbers the page shows. It keeps the page
    it is on, and its day when the page has one (Clips, the Week page)."""
    base = {"calendar": "/calendar", "money": "/money"}.get(current) or REG.url_of(current)   # the switch keeps its page

    def href(a: str) -> str:
        q = {} if a == "all" else {"acct": a}
        if day is not None:
            q["date"] = day.isoformat()
        return base + ("?" + urllib.parse.urlencode(q) if q else "")
    opts = [("all", "All")] + [(a.slug, a.name) for a in cfg.accounts.values()]
    return ('<nav class="switch" aria-label="Account">' + "".join(
        f'<a href="{esc(href(k))}"{" aria-current=true" if k == acct else ""}>{esc(w)}</a>' for k, w in opts)
        + "</nav>")


def _days(cfg: C.Config, day: date, today: date) -> List[date]:
    days = {day, today, today + timedelta(days=1)}
    for a in cfg.accounts.values():
        for p in B.all_boards(a):
            days.add(date.fromisoformat(p.stem))
    return sorted(days, reverse=True)


# ------------------------------------------------------------------------------------------------------ the page
CSS = """
:root{color-scheme:light;--bg:#ffffff;--card:#ffffff;--ink:#0e1726;--sub:#4a5568;--line:#e4e7ec;--blue:#2a78d6;
--bluebg:#eaf2fc;--ok:#138a4b;--okbg:#e7f4ec;--bad:#c8312a;--badbg:#fcebea;--warn:#b45309;--warnbg:#fff4e5;--mute:#f3f4f6}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);font:14px/1.45 -apple-system,BlinkMacSystemFont,"Inter","Segoe UI",sans-serif;font-feature-settings:"tnum" 0;-webkit-font-smoothing:antialiased}
header{position:sticky;top:0;z-index:5;background:var(--bg);border-bottom:1px solid var(--line);padding:10px 20px;display:flex;gap:10px;align-items:center;flex-wrap:wrap}
h1{font-size:16px;margin:0 6px 0 0;font-weight:650}
.tabs{display:flex;gap:2px;margin-right:6px}
.tabs a{display:inline-flex;align-items:center;height:32px;padding:0 10px;color:var(--sub);text-decoration:none;font-size:14px;font-weight:550;border-bottom:2px solid transparent}
.tabs a:hover{color:var(--ink)}
.tabs a[aria-current=page]{color:var(--ink);border-bottom-color:var(--ink)}
.days{display:flex;gap:6px;align-items:center}
.step,.today{display:inline-flex;align-items:center;justify-content:center;height:32px;min-width:32px;padding:0 10px;border:1px solid var(--line);border-radius:6px;background:var(--card);color:var(--ink);text-decoration:none;font-size:13px}
.step:hover,.today:hover,button:hover{border-color:var(--sub)}
.grow{flex:1}
.who{color:var(--sub);font-size:13px}.who b{color:var(--ink);font-weight:600}
select{font:inherit;font-size:13px;height:32px;padding:0 8px;border-radius:6px;border:1px solid var(--line);background:var(--card);color:var(--ink)}
main{padding:20px;max-width:1520px;margin:0 auto}
section{margin-bottom:40px}
.acct-head{display:flex;align-items:center;gap:12px;flex-wrap:wrap;min-height:34px}
h2{font-size:18px;margin:0;font-weight:650}
.counts{color:var(--sub);font-size:13px}
.routes{color:var(--sub);font-size:12.5px;margin:4px 0 14px;display:flex;gap:6px;flex-wrap:wrap}
.routes .warn{color:var(--warn)}.routes .bad{color:var(--bad)}.routes .report{flex-basis:100%;color:var(--ink)}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(240px,1fr));gap:16px;align-items:start}
.card{background:var(--card);border:1px solid var(--line);border-radius:8px;overflow:hidden}
.top{display:flex;align-items:center;gap:8px;padding:9px 12px;min-height:40px}
.slot{font-weight:650;font-size:15px;font-variant-numeric:tabular-nums}
.tag{font-size:11.5px;font-weight:600;padding:1px 7px;border-radius:4px;line-height:18px;white-space:nowrap}
.t-ready{color:var(--blue);background:var(--bluebg)}.t-ok{color:var(--ok);background:var(--okbg)}
.t-done{color:#fff;background:var(--ok)}.t-bad{color:var(--bad);background:var(--badbg)}.t-mute{color:var(--sub);background:var(--mute)}
.t-warn{color:var(--warn);background:var(--warnbg)}
.when{margin-left:auto;color:var(--sub);font-size:12px;text-align:right;font-variant-numeric:tabular-nums}
.when.warn{color:var(--warn)}.when.bad{color:var(--bad)}
video{display:block;width:100%;aspect-ratio:9/16;max-height:460px;background:#000}
.cut video{margin-top:8px;max-height:380px;border-radius:4px}
.body{padding:10px 12px 12px;display:flex;flex-direction:column;gap:8px}
.title{font-weight:600;line-height:1.35}
.meta,.note{color:var(--sub);font-size:12.5px}
summary{cursor:pointer;color:var(--sub);font-size:12.5px}
.copy{white-space:pre-wrap;font-size:12.5px;max-height:260px;overflow:auto;overflow-wrap:anywhere}
.copy b{display:block;font-size:11px;color:var(--sub);font-weight:650;text-transform:uppercase;letter-spacing:.05em;margin-top:10px}
.bottom{display:flex;flex-direction:column;gap:8px}
.bottom:empty{display:none}
.plats{list-style:none;margin:0;padding:8px 0 0;border-top:1px solid var(--line);display:grid;gap:5px;font-size:12.5px}
.plats li{display:flex;gap:8px;align-items:baseline}
.plats b{font-weight:600;flex:0 0 72px}
.plats span{flex:1;overflow-wrap:anywhere}
.plats .done span,.plats .ok span{color:var(--ok)}.plats .bad span{color:var(--bad)}.plats .warn span{color:var(--warn)}
.plats .wait span,.plats .hand span{color:var(--sub)}
.plats a{color:var(--blue);text-decoration:none;white-space:nowrap}.plats a:hover{text-decoration:underline}
.alert{font-size:12.5px;color:var(--bad);background:var(--badbg);padding:7px 9px;border-radius:6px}
.btns{display:flex;gap:6px;flex-wrap:wrap}
button{font:inherit;font-size:13px;height:32px;padding:0 12px;border-radius:6px;border:1px solid var(--line);background:var(--card);color:var(--ink);cursor:pointer}
button.go{background:var(--blue);border-color:var(--blue);color:#fff;font-weight:600}
button.go:hover{filter:brightness(1.08)}
button.link{height:auto;padding:0;border:0;background:none;color:var(--blue);font-size:12.5px;white-space:nowrap}
button:disabled,button.go:disabled{opacity:1;background:var(--mute);border-color:var(--line);color:var(--sub);cursor:default;filter:none}
:focus-visible{outline:2px solid var(--blue);outline-offset:2px}
.switch{display:flex;gap:2px;padding-left:10px;margin-left:2px;border-left:1px solid var(--line)}
.switch a{display:inline-flex;align-items:center;height:32px;padding:0 10px;color:var(--sub);text-decoration:none;font-size:14px;font-weight:550;border-bottom:2px solid transparent;white-space:nowrap}
.switch a:hover{color:var(--ink)}
.switch a[aria-current]{color:var(--ink);border-bottom-color:var(--ink)}
.plinks{display:flex;gap:12px;font-size:12.5px}
.plinks a{color:var(--blue);text-decoration:none}.plinks a:hover{text-decoration:underline}
.plinks a::after{content:" ↗";font-size:11px}
details.report{flex-basis:100%;color:var(--ink)}details.report summary{color:var(--ink);font-size:12.5px}
details.report div{margin-top:4px;white-space:pre-wrap}
.needs{margin-bottom:26px}
.needs-head{display:flex;align-items:center;gap:10px;flex-wrap:wrap;min-height:34px;margin-bottom:12px}
.needs-head h2{font-size:18px}.needs-head .n{color:var(--warn);font-variant-numeric:tabular-nums}
.needs-head .calm{color:var(--sub);font-size:13.5px}
.needs-head .health{flex-basis:100%;font-size:13.5px}
#needs-grid{grid-template-columns:repeat(auto-fill,minmax(330px,1fr));gap:20px}
#needs-grid video{max-height:min(640px,72vh)}
#needs-grid .card{border-color:var(--warn)}
#needs-grid .title{font-size:16px}
.where{font-size:12px;color:var(--sub);font-weight:600;flex-basis:100%}
.top{flex-wrap:wrap}
.ask{font-size:12.5px;color:var(--warn);background:var(--warnbg);padding:7px 9px;border-radius:6px;font-weight:600}
.stale{font-size:16px;line-height:1.35;color:#fff;background:var(--bad);padding:11px 12px;border-radius:6px;font-weight:800;letter-spacing:.01em}
details.tt-wrap{border-top:1px solid var(--line);padding-top:8px}
details.tt-wrap>summary{font-size:11px;font-weight:650;color:var(--sub);text-transform:uppercase;letter-spacing:.05em}
details.tt-wrap .tt{border-top:0;padding-top:6px}
.plats b a{color:var(--ink);text-decoration:none;border-bottom:1px solid var(--line)}.plats b a:hover{color:var(--blue);border-color:var(--blue)}
.grid.rest video{max-height:380px}
.grid.big{grid-template-columns:repeat(auto-fill,minmax(330px,1fr));gap:20px;margin-bottom:18px}
.grid.big video{max-height:min(720px,78vh)}
.grid.big .card{border-color:var(--warn)}
.grid.big .title{font-size:16px}
.grid:empty{display:none}
.cal{background:var(--card);border:1px solid var(--line);border-radius:8px;padding:10px 12px 10px;margin-bottom:24px}
.cal.closed .cal-grid{display:none}.cal.closed .cal-head{margin-bottom:0}
.cal-head .fold{margin-left:8px}
.cal-head{display:flex;align-items:center;gap:8px;margin-bottom:8px}
.cal-head h2{font-size:15px;margin:0 4px 0 0}
.cal-head button{height:28px;min-width:28px;padding:0 8px}
.cal-key{display:flex;gap:12px;flex-wrap:wrap;margin-left:auto;color:var(--sub);font-size:12px}
.cal-key span{display:inline-flex;align-items:center;gap:5px}
.cal-key i{display:inline-block;width:10px;height:6px}
.cal-grid{display:grid;grid-template-columns:repeat(7,minmax(0,1fr));gap:4px}
.cal-dow{color:var(--sub);font-size:11.5px;font-weight:600;padding:0 6px 2px}
.cal-day{display:flex;flex-direction:column;gap:3px;min-height:46px;padding:4px 6px 5px;border:1px solid var(--line);border-radius:6px;color:var(--ink);text-decoration:none;background:var(--bg)}
.cal-day:hover{border-color:var(--sub)}
.cal-day.blank{border-color:transparent;background:none}
.cal-day.is-today .dn{color:var(--blue)}
.cal-day[aria-current]{border-color:var(--ink);box-shadow:inset 0 0 0 1px var(--ink)}
.cal-day .dn{font-size:12.5px;font-weight:650;font-variant-numeric:tabular-nums;display:flex;justify-content:space-between;gap:4px}
.cal-day .need{color:var(--warn);font-weight:600;font-size:11.5px;white-space:nowrap}
.cal-day .row{display:flex;align-items:center;gap:5px}
.cal-day .lab{font-size:10.5px;color:var(--sub);width:38px;flex:0 0 38px;overflow:hidden;text-overflow:clip;white-space:nowrap}
.strip{display:flex;gap:1px;flex:1;height:6px}
.strip b{flex:1;background:var(--mute)}
.k-posted{background:var(--ok)!important}.k-scheduled{background:var(--blue)!important}
.k-review{background:var(--warn)!important}.k-problem{background:var(--bad)!important}.k-empty{background:var(--mute)!important}
.empties{margin-top:12px;font-size:12.5px;color:var(--sub)}.empties:empty{display:none}
.empties b{color:var(--ink);font-weight:600;margin-right:4px}
#toast{position:fixed;left:50%;bottom:20px;transform:translateX(-50%);z-index:9;max-width:min(680px,calc(100vw - 32px));background:var(--ink);color:var(--bg);padding:10px 14px;border-radius:8px;font-size:13px;white-space:pre-wrap;box-shadow:0 6px 24px rgba(0,0,0,.25);cursor:pointer}
#toast.bad{background:var(--bad);color:#fff}
.tt{border-top:1px solid var(--line);padding-top:9px;display:flex;flex-direction:column;gap:7px;font-size:12.5px}
.tt-head{display:flex;gap:8px;align-items:baseline;flex-wrap:wrap}
.tt-head b{font-size:11px;color:var(--sub);font-weight:650;text-transform:uppercase;letter-spacing:.05em}
.tt-head strong{font-weight:650}
.tt textarea{font:inherit;font-size:12.5px;width:100%;min-height:96px;padding:6px 8px;border:1px solid var(--line);border-radius:6px;background:var(--card);color:var(--ink);resize:vertical;box-sizing:border-box}
.tt-row{display:flex;justify-content:space-between;align-items:center}
.tt-capro{white-space:pre-wrap;overflow-wrap:anywhere;max-height:140px;overflow:auto;color:var(--ink)}
.tt-field{display:flex;flex-direction:column;gap:4px}.tt-field span{color:var(--sub)}
.tt-field select{width:100%}
.tt-check{display:flex;gap:7px;align-items:center;cursor:pointer}
.tt-check input{margin:0;width:15px;height:15px}
.tt-greyed{color:var(--sub);cursor:default}
.tt-sub{display:flex;flex-direction:column;gap:5px;padding-left:22px}
.tt-off{color:var(--sub);font-size:12px}
.tt-label{color:var(--ink);font-weight:600}
.tt-legal{color:var(--sub)}.tt-legal a{color:var(--blue)}
.tt-why{color:var(--warn)}
.tt-go{display:inline-block}
details.why{border-top:1px solid var(--line);padding-top:9px}
details.why>summary{display:none}details.why:not([open]){display:none}
.why-head{font-size:12.5px;font-weight:600;margin-bottom:7px}
.codes{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:5px}
button.code{display:flex;align-items:center;gap:7px;height:30px;padding:0 8px;font-size:12px;font-weight:600;letter-spacing:.02em;text-align:left;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
button.code kbd{font:inherit;font-size:11px;color:var(--sub);font-variant-numeric:tabular-nums}
button.code.hard{color:var(--bad);background:var(--badbg);border-color:var(--badbg)}
button.code.hard kbd{color:var(--bad);opacity:.75}
button.code.sel,button.code.sel:hover{border-color:var(--blue);box-shadow:inset 0 0 0 1px var(--blue)}
.why-other{display:flex;gap:6px;margin-top:6px}
.why-other input{flex:1;min-width:0;font:inherit;font-size:12.5px;height:30px;padding:0 8px;border:1px solid var(--line);border-radius:6px;background:var(--card);color:var(--ink)}
.why-other button{height:30px}
.why-foot{display:flex;align-items:center;gap:12px;margin-top:8px;flex-wrap:wrap}
.why-keys{margin-left:auto;color:var(--sub);font-size:11.5px}
.card.kbd{border-color:var(--blue);box-shadow:0 0 0 1px var(--blue)}
@media (max-width:600px){header{padding:10px 16px}main{padding:16px}.grid,.grid.big{grid-template-columns:1fr}
.switch{border-left:0;padding-left:0;margin-left:0;flex-basis:100%;overflow-x:auto}.cal{padding:10px 8px}
.cal-day{min-height:46px;padding:3px 3px 4px;gap:3px}.cal-day .lab,.cal-day .need{display:none}.cal-key{margin-left:0;flex-basis:100%}
header{position:static}#needs-grid video,.grid.big video{max-height:58vh}.grid.rest video{max-height:52vh}
button,.step,.today,select{height:40px;min-width:40px;font-size:16px}.tt select{font-size:16px}
button.code,.why-other button,.why-other input{height:40px;font-size:15px}.why-keys{display:none}}
"""
CSS += CQ.CSS      # Casts to check (castqueue.py)
CSS += SX.CLIPS_CSS      # the Today band, the Decline chooser, the calendar's Week link, tabs on phones (socialui.py)
from .lab import CARD_CSS as _LAB_CARD_CSS    # noqa: E402  (a Lab slot's box on its Clips card, lab.py)
CSS += _LAB_CARD_CSS
CSS += LK.CSS      # the 10-29 look: lanes, tiles, the right column, the Calendar tab (look.py)

JS = """
const TOKEN=__TOKEN__, DAY=__DAY__, POLL=__POLL__, ACCT=__ACCT__;
let CAL=__CAL__, calMonth=DAY.slice(0,7);
let STAMP=__STAMP__, inflight=null, queued=false, toastTimer=0;
const REASONS=__REASONS__;
const toastEl=document.getElementById('toast');
function who(){const w=document.getElementById('who');return w?w.value:''}
function toast(msg,bad){toastEl.textContent=msg;toastEl.className=bad?'bad':'';toastEl.hidden=false;
  clearTimeout(toastTimer);toastTimer=setTimeout(()=>{toastEl.hidden=true},bad?15000:7000)}
toastEl.addEventListener('click',()=>{toastEl.hidden=true});
function frag(h){const t=document.createElement('template');t.innerHTML=h.trim();return t.content.firstElementChild}
function swap(node,h){if(!node)return;const f=document.activeElement;
  if(f&&/^(TEXTAREA|SELECT|INPUT)$/.test(f.tagName)&&node.contains(f))return;  // never wipe a choice being made
  if(node.querySelector&&node.querySelector('.dx-open'))return;  // nor a Decline being explained
  const n=frag(h);if(!n||node.dataset.h===n.dataset.h)return;
  node.querySelectorAll('details[open][data-k]').forEach(d=>{const m=n.querySelector('details[data-k="'+d.dataset.k+'"]');if(m)m.open=true});
  node.replaceWith(n)}
function reconcile(grid,cards){
  const want=new Set(cards.map(c=>c.id));
  [...grid.children].forEach(el=>{if(!want.has(el.id))el.remove()});
  let prev=null;
  for(const c of cards){
    let el=document.getElementById(c.id);
    if(el&&el.dataset.key!==c.key){  /* the card changed (a new title): rebuilt, its open folds kept open */
      const open=[...el.querySelectorAll('details[open][data-k]')].map(d=>d.dataset.k);el.remove();el=frag(c.html);
      if(el)open.forEach(k=>{const m=el.querySelector('details[data-k="'+k+'"]');if(m)m.open=true})}
    else if(!el){el=frag(c.html)}else{swap(el.querySelector('.top'),c.top);swap(el.querySelector('.bottom'),c.bottom)}
    if(el&&c.id===KBD)el.classList.add('kbd');       // the card the keys go to keeps its ring when it is rebuilt
    const at=prev?prev.nextElementSibling:grid.firstElementChild;
    if(at!==el)grid.insertBefore(el,at);
    prev=el;
  }
}
function apply(d){
  if(d.stamp===STAMP)return;STAMP=d.stamp;
  const bf=document.getElementById('brief');
  if(d.brief){if(bf)swap(bf,d.brief);else{const n=frag(d.brief),mn=document.querySelector('main');if(n&&mn)mn.prepend(n)}}
  else if(bf)bf.remove();
  if(d.cal){CAL=d.cal;drawCal()}
  for(const k of ['kpis','breaking','rail']){if(d[k]!==undefined){const e=document.getElementById(k);if(e)swap(e,d[k])}}
  if(d.casts){const cb=document.getElementById('casts');if(cb){cb.hidden=!d.casts.items.length;
    swap(cb.querySelector('.casts-head'),d.casts.head);reconcile(document.getElementById('casts-grid'),d.casts.items)}}
  if(d.needs){const nb=document.getElementById('needs');if(nb){swap(nb.querySelector('.needs-head'),d.needs.head);
    reconcile(document.getElementById('needs-grid'),d.needs.cards)}}
  const inband=new Set((d.needs&&d.needs.keys)||[]);
  for(const s of d.sections){for(const c of s.cards)c.inband=inband.has(s.id.slice(5)+'|'+DAY+'|'+c.id.slice(-4,-2)+':'+c.id.slice(-2))}
  for(const s of d.sections){
    const el=document.getElementById(s.id);if(!el)continue;
    swap(el.querySelector('.acct-head'),s.head);swap(el.querySelector('.routes'),s.routes);
    reconcile(el.querySelector('.grid.big'),s.cards.filter(c=>c.big&&!c.inband));
    reconcile(el.querySelector('.grid.rest'),s.cards.filter(c=>!c.big&&!c.inband));
    swap(el.querySelector('.empties'),s.empty);
  }
  if(typeof afterApply==='function')afterApply();
}
/* the calendar: each day, each account's slots as one strip */
const KINDS=[['posted','Posted'],['scheduled','Scheduled'],['review','To review'],['problem','Problem'],['empty','No clip']];
const MONTHS=['January','February','March','April','May','June','July','August','September','October','November','December'];
function mk(tag,cls,txt){const n=document.createElement(tag);if(cls)n.className=cls;if(txt!=null)n.textContent=txt;return n}
function dayHref(iso){return '/?date='+iso+(ACCT==='all'?'':'&acct='+encodeURIComponent(ACCT))}
function calOpen(){try{return localStorage.getItem('clip-review-cal')!=='closed'}catch(e){return true}}
function drawCal(){
  const box=document.getElementById('cal');if(!box||!CAL)return;
  const y=+calMonth.slice(0,4),m=+calMonth.slice(5,7)-1;
  const head=mk('div','cal-head');
  const prev=mk('button','','‹');prev.type='button';prev.title='Previous month';prev.setAttribute('aria-label','Previous month');
  const next=mk('button','','›');next.type='button';next.title='Next month';next.setAttribute('aria-label','Next month');
  prev.onclick=()=>{const d=new Date(Date.UTC(y,m-1,1));calMonth=d.toISOString().slice(0,7);drawCal()};
  next.onclick=()=>{const d=new Date(Date.UTC(y,m+1,1));calMonth=d.toISOString().slice(0,7);drawCal()};
  const key=mk('div','cal-key');
  for(const [k,w] of KINDS){const s=mk('span');const i=mk('i','k-'+k);s.append(i,w);key.append(s)}
  const fold=mk('button','fold',calOpen()?'Hide':'Show calendar');fold.type='button';
  fold.onclick=()=>{try{localStorage.setItem('clip-review-cal',calOpen()?'closed':'open')}catch(e){}drawCal()};
  head.append(mk('h2','',MONTHS[m]+' '+y),prev,next);
  if(CAL.week){const wl=mk('a','cal-weeklink','Week');wl.href='/week?date='+DAY+(ACCT==='all'?'':'&acct='+encodeURIComponent(ACCT));head.append(wl)}
  head.append(key,fold);
  box.classList.toggle('closed',!calOpen());
  const grid=mk('div','cal-grid');
  for(const w of ['Mon','Tue','Wed','Thu','Fri','Sat','Sun'])grid.append(mk('div','cal-dow',w));
  const first=new Date(Date.UTC(y,m,1)),lead=(first.getUTCDay()+6)%7,len=new Date(Date.UTC(y,m+1,0)).getUTCDate();
  for(let i=0;i<lead;i++)grid.append(mk('div','cal-day blank'));
  for(let dd=1;dd<=len;dd++){
    const iso=calMonth+'-'+String(dd).padStart(2,'0'),info=CAL.days[iso]||{};
    const a=mk('a','cal-day'+(iso===CAL.today?' is-today':''));a.href=dayHref(iso);
    if(iso===DAY)a.setAttribute('aria-current','date');
    const dn=mk('span','dn');dn.append(mk('span','',String(dd)));
    let need=0;for(const acc of CAL.accounts)need+=(info[acc.id]||[]).filter(k=>k==='review').length;
    if(need)dn.append(mk('span','need',need+' to review'));
    a.append(dn);
    const label=[];
    for(const acc of CAL.accounts){
      const kinds=info[acc.id],its=((CAL.items||{})[iso]||{})[acc.id]||[];if(!kinds&&!its.length)continue;
      const parts=[];
      if(kinds){
        const row=mk('span','row');row.append(mk('span','lab',acc.short));
        const strip=mk('span','strip');for(const k of kinds)strip.append(mk('b','k-'+k));
        row.append(strip);a.append(row);
        const c={};for(const k of kinds)c[k]=(c[k]||0)+1;
        parts.push(KINDS.filter(([k])=>c[k]).map(([k,w])=>c[k]+' '+w.toLowerCase()).join(', '));
      }
      if(its.length){  /* cards and stories: a thin second strip under the clips */
        const r2=mk('span','row row2');r2.append(mk('span','lab',kinds?'':acc.short));
        const s2=mk('span','strip2');for(const it of its)s2.append(mk('b','k-'+it.s));
        r2.append(s2);a.append(r2);
        const nc=its.filter(it=>it.k==='card').length,ns=its.length-nc;
        parts.push([nc?nc+(nc===1?' card':' cards'):'',ns?ns+(ns===1?' story':' stories'):''].filter(x=>x).join(', '));
      }
      label.push(acc.name+': '+parts.join('; '));
    }
    a.title=label.length?label.join(String.fromCharCode(10)):'Nothing planned';
    a.setAttribute('aria-label',iso+(label.length?'. '+label.join('. '):'. Nothing planned'));
    grid.append(a);
  }
  box.replaceChildren(head,grid);
}
drawCal();
function refresh(force){
  if(document.hidden&&!force)return Promise.resolve();
  if(inflight){queued=true;return inflight}
  inflight=(async()=>{
    try{const r=await fetch('/live?date='+encodeURIComponent(DAY)+'&acct='+encodeURIComponent(ACCT),{cache:'no-store'});if(r.ok)apply(await r.json())}
    catch(e){}
    finally{inflight=null;if(queued){queued=false;refresh(true)}}
  })();
  return inflight;
}
/* Approve signs the first comment and poll SAVED on disk. A words box (data-w) changed but not saved would be signed
   as the old words, so Approve stops and says which clip, and the box keeps what was typed. */
function unsaved(card){
  if(!card)return [];
  return [...card.querySelectorAll('[data-x-form] [data-w]')].filter(f=>String(f.value||'').trim()!==String(f.defaultValue||'').trim());
}
function holdUnsaved(cards){
  const slots=[];let first=null;
  for(const c of cards){const u=unsaved(c);if(u.length){if(!slots.includes(c.dataset.slot))slots.push(c.dataset.slot);if(!first)first=u[0]}}
  if(!slots.length)return false;
  toast('Not approved: '+slots.join(', ')+(slots.length>1?' have':' has')+' words you changed but did not save (first comment or poll). Press Save, or put the saved words back, then Approve.',true);
  try{first.focus()}catch(e){}
  return true;
}
async function act(body,btn){
  const box=btn.closest('.card')||btn.closest('section')||btn.parentElement;
  const holder=btn.closest('[data-date]');if(holder&&!body.date)body.date=holder.dataset.date;
  const btns=[...box.querySelectorAll('button')];btns.forEach(b=>b.disabled=true);
  toast('Working...');
  try{
    const r=await fetch('/act',{method:'POST',headers:{'Content-Type':'application/json','X-Token':TOKEN},
      body:JSON.stringify(Object.assign({date:DAY,by:who()},body))});
    let d;try{d=await r.json()}catch(e){d={ok:false,message:'The page sent an answer it could not read. Reload it.'}}
    if(r.status===403&&d.stale){toast('This tab was open before the page restarted. Reloading...');setTimeout(()=>location.reload(),900);return}
    toast(d.message||(r.ok?'Done.':'That did not work.'),!(r.ok&&d.ok));
  }catch(e){toast('Could not reach the review page (it may be restarting). Try again in a few seconds.',true)}
  finally{btns.forEach(b=>b.disabled=false)}
  STAMP='';await refresh(true);
}
document.addEventListener('click',ev=>{
  const b=ev.target.closest('button[data-op]');if(!b||b.disabled)return;
  const op=b.dataset.op,account=b.dataset.acct,slot=b.dataset.slot;
  if(op.startsWith('cast-')){const c=b.closest('.cast');if(!c)return;if(b.dataset.ask&&!confirm(b.dataset.ask))return;
    act({op,job:c.dataset.job,file:c.dataset.file,person:c.dataset.person,seen:c.dataset.key},b);return}
  if(op==='approve-all'){
    const day=b.dataset.date||DAY,seen={},cards=[...document.querySelectorAll('.card[data-acct="'+account+'"][data-date="'+day+'"]')];
    if(holdUnsaved(cards))return;
    if(!confirm(b.dataset.ask))return;
    cards.forEach(c=>{seen[c.dataset.slot]=c.dataset.key});
    act({op:'approve',account,slots:null,seen,date:day},b);return}
  if(op==='approve'){const c=b.closest('.card');if(holdUnsaved([c]))return;act({op,account,slots:[slot],seen:{[slot]:c.dataset.key}},b);return}
  if(op==='reveal'||op==='retry'){act({op,account,slots:[slot]},b);return}
  if(op==='tt-caption'){const t=b.closest('.tt').querySelector('textarea');act({op,account,slots:[slot],text:t?t.value:''},b);return}
  if(op==='tt-now'){act({op,account,slots:[slot]},b);return}
  if(op==='why'){const c=b.closest('.card');if(!c)return;openWhy(c,b.dataset.why,'toggle');return}
  if(op==='why-close'){const d=b.closest('details.why');if(d)closeWhy(d);return}
  if(op==='decline'||op==='pull'){   /* a code under Decline or Pull (reasons.py), or Pull with no reason */
    const code=b.dataset.code||'';let reason='';
    if(code==='other'){const i=b.closest('.why-other').querySelector('input');reason=(i&&i.value||'').trim();
      if(!reason){toast('Say what was wrong, then press '+(op==='pull'?'Pull':'Decline')+'.',true);if(i)i.focus();return}}
    act(code?{op,account,slots:[slot],code,reason}:{op,account,slots:[slot]},b);return}
  if(b.dataset.x!==undefined){  /* a card part's button: the named fields of its form, the card's own day and seen key */
    const form=b.closest('[data-x-form]'),fields={},words=[],c=b.closest('.card');
    if(form)form.querySelectorAll('[name]').forEach(f=>{if(f.type==='radio'&&!f.checked)return;fields[f.name]=f.type==='checkbox'?f.checked:f.value});
    if(form)form.querySelectorAll('[data-w]').forEach(f=>{const v=String(f.value||'').trim();if(v)words.push(v)});
    let ask=b.dataset.askT||b.dataset.ask||'';  /* {words}: what is in the form's data-w fields as it is sent */
    if(ask.indexOf('{words}')>=0)ask=ask.split('{words}').join(words.join(' / '));
    if(ask&&!confirm(ask))return;
    const sl=slot||(c?c.dataset.slot:''),body={op,account:account||(c?c.dataset.acct:''),slots:sl?[sl]:[],fields,seen:c?c.dataset.key:''};
    if(c&&c.dataset.date)body.date=c.dataset.date;
    act(body,b);return}
  if(op==='remake'){openChooser(b,op,account,slot);return}
});
/* the reasons under Decline and Pull, and the keys: the card last clicked (or tabbed into) takes them */
let KBD='';
function setTarget(c){document.querySelectorAll('.card.kbd').forEach(x=>{if(x!==c)x.classList.remove('kbd')});
  if(c){c.classList.add('kbd');KBD=c.id}}
function target(){const c=KBD?document.getElementById(KBD):null;if(c)c.classList.add('kbd');return c}
function closeWhy(d){d.open=false;d.querySelectorAll('button.code.sel').forEach(x=>x.classList.remove('sel'))}
function openWhy(c,which,how){
  const d=c.querySelector('details.why[data-k="why-'+which+'"]');if(!d)return null;
  c.querySelectorAll('details.why[open]').forEach(x=>{if(x!==d)closeWhy(x)});
  if(how==='toggle'&&d.open){closeWhy(d);return null}
  d.open=true;setTarget(c);return d}
document.addEventListener('pointerdown',ev=>{const c=ev.target.closest&&ev.target.closest('.card');if(c)setTarget(c)},true);
document.addEventListener('focusin',ev=>{const c=ev.target.closest&&ev.target.closest('.card');if(c)setTarget(c)});
document.addEventListener('keydown',ev=>{
  if(ev.metaKey||ev.ctrlKey||ev.altKey||ev.defaultPrevented)return;
  const t=ev.target,k=ev.key;
  if(t&&(/^(INPUT|TEXTAREA|SELECT)$/.test(t.tagName)||t.isContentEditable)){
    if(k==='Escape'){const d=t.closest('details.why');if(d){closeWhy(d);ev.preventDefault()}}
    if(k==='Enter'&&t.closest('.why-other')){const b=t.closest('.why-other').querySelector('button');if(b){ev.preventDefault();b.click()}}
    return}
  const c=target();if(!c)return;
  const open=c.querySelector('details.why[open]');
  if(k==='Escape'&&open){closeWhy(open);ev.preventDefault();return}
  if(/^[0-9]$/.test(k)){
    let d=open;
    if(!d){const b=c.querySelector('button[data-op=why][data-why=decline]:not(:disabled)')||c.querySelector('button[data-op=why][data-why=pull]:not(:disabled)');
      if(!b)return;d=openWhy(c,b.dataset.why,'open');if(!d)return}
    const chip=d.querySelector('button.code[data-n="'+k+'"]');if(!chip||chip.disabled)return;
    ev.preventDefault();
    if(chip.classList.contains('sel')){chip.click();return}       // the same number twice: that reason
    d.querySelectorAll('button.code.sel').forEach(x=>x.classList.remove('sel'));chip.classList.add('sel');
    chip.scrollIntoView({block:'nearest'});return}
  if(k==='Enter'&&open){const sel=open.querySelector('button.code.sel');if(sel&&!sel.disabled){ev.preventDefault();sel.click()}return}
  if(k==='a'||k==='A'){const b=c.querySelector('button[data-op=approve]');if(b&&!b.disabled){ev.preventDefault();b.click()}return}
  if(k==='p'||k==='P'){const b=c.querySelector('button[data-op=why][data-why=pull]');if(b&&!b.disabled){ev.preventDefault();openWhy(c,'pull','open')}return}
});
/* Make again: the reasons (verdicts.CHIPS, the same codes) as chips and a note, under the card's buttons */
/* Make again: the reasons (verdicts.CHIPS, the same codes) as chips and a note, under the card's buttons (no
   browser prompt). Decline and Pull are one tap on a code, above (reasons.py: its gate, the kill list). */
function openChooser(b,op,account,slot){
  const host=b.closest('.btns')||b;
  const old=host.parentElement&&host.parentElement.querySelector('.dx-open');if(old)old.remove();
  const remake=op==='remake',picked=new Set();
  const box=mk('div','dx dx-open');box.setAttribute('role','group');
  const h=mk('div','dx-h',remake?'Make '+slot+' again. What should change?':'Decline '+slot+'. Why?');box.append(h);
  box.setAttribute('aria-label',h.textContent);
  if(REASONS.length){
    const cs=mk('div','dx-chips');
    for(const [code,label] of REASONS){
      const r=mk('button','dx-r',label);r.type='button';r.setAttribute('aria-pressed','false');
      r.onclick=()=>{if(picked.has(code))picked.delete(code);else picked.add(code);r.setAttribute('aria-pressed',String(picked.has(code)))};
      cs.append(r);
    }
    box.append(cs);
  }
  const note=mk('input','dx-note');note.type='text';note.maxLength=300;
  note.placeholder=remake?'What should change (optional)':'Anything else (optional)';note.setAttribute('aria-label',note.placeholder);
  const go=mk('button','go',remake?'Make again':'Decline');go.type='button';
  const no=mk('button','','Cancel');no.type='button';
  const send=()=>{box.remove();act({op:'decline',account,slots:[slot],reason:note.value.trim(),reasons:[...picked],remake},b)};
  go.onclick=send;no.onclick=()=>{box.remove();b.focus()};
  note.addEventListener('keydown',ev=>{if(ev.key==='Enter'){ev.preventDefault();send()}else if(ev.key==='Escape'){box.remove();b.focus()}});
  const bb=mk('div','dx-btns');bb.append(go,no);box.append(note,bb);
  host.after(box);note.focus();
}
document.addEventListener('change',async ev=>{
  const el=ev.target.closest('[data-tt]');if(!el)return;const box=el.closest('.tt');
  const value=el.type==='checkbox'?el.checked:el.value;
  try{const r=await fetch('/act',{method:'POST',headers:{'Content-Type':'application/json','X-Token':TOKEN},
      body:JSON.stringify({op:'tt-set',date:(box.closest('[data-date]')||{dataset:{}}).dataset.date||DAY,by:who(),account:box.dataset.acct,slots:[box.dataset.slot],field:el.dataset.tt,value})});
    let d={};try{d=await r.json()}catch(e){}
    if(!(r.ok&&d.ok))toast(d.message||'That did not save.',true);else if(d.message&&d.message!=='Saved.')toast(d.message);
  }catch(e){toast('Could not reach the review page. Try again in a few seconds.',true)}
  STAMP='';await refresh(true);
});
document.addEventListener('input',ev=>{const t=ev.target;if(!t.classList||!t.classList.contains('tt-cap'))return;
  const n=t.closest('.tt').querySelector('.tt-row .tt-off');if(n)n.textContent=t.value.length+'/2200'});
document.addEventListener('play',ev=>{document.querySelectorAll('video').forEach(v=>{if(v!==ev.target&&!v.paused)v.pause()})},true);
const dayEl=document.getElementById('day');if(dayEl)dayEl.addEventListener('change',ev=>{location.href=dayHref(ev.target.value)});
document.addEventListener('visibilitychange',()=>{if(!document.hidden)refresh(true)});
setInterval(()=>refresh(false),POLL*1000);
try{if(window.matchMedia('(max-width:600px)').matches){const t=document.querySelector('.tabs a[aria-current]');if(t)t.scrollIntoView({block:'nearest',inline:'center'})}}catch(e){}
"""


def _parts(cfg: C.Config, day: date, now: datetime, acct: str, needs: dict) -> dict:
    """The 10-29 look's parts around the lanes, each one the page swaps on its own: the day's four numbers, the
    breaking line (the Today band's breaking chips) and the right column (the rest of the band, with yesterday, the
    best clips, the early numbers and what needs a look). The right column never takes the page down."""
    chips = _soc("brief", [], cfg, now, acct)
    brk, rest = LK.split_chips(chips)
    accts = _accts(cfg, acct)
    posted = coming = missed = 0
    for a in accts:
        for s in (B.read(a, day) or {}).get("slots", {}).values():
            if not s.get("clip") or s.get("status") in (B.OPEN, B.MAKING):
                continue
            kind = _slot_kind(cfg, a, s, now)
            posted += kind == "posted"
            missed += kind == "problem"
            coming += kind in ("scheduled", "review")
    words = "today" if day == now.date() else f"{day:%b} {day.day}"
    try:
        rail = LK.rail(cfg, now, accts, rest, acct)
    except Exception:  # noqa: BLE001  (the right column never takes the page down)
        rail = ""
    return {"kpis": _wrap("section", "kpis", LK.kpis(len(needs["cards"]), posted, coming, missed, words),
                          ' id="kpis" aria-label="The day in numbers"'),
            "breaking": _wrap("div", "breaking", LK.breaking(brk), ' id="breaking"'),
            "rail": _wrap("aside", "rail", rail, ' id="rail" aria-label="How it is doing"')}


def page(cfg: C.Config, day: date, now: Optional[datetime] = None, tok: str = "", acct: str = "all") -> str:
    now = now or cfg.now()
    today = now.date()
    acct = pick_acct(cfg, acct)
    secs = _sections(cfg, day, now, acct)
    cal = calendar(cfg, now, acct)
    aq = "" if acct == "all" else "&acct=" + urllib.parse.quote(acct)
    nav = (f'<nav class="days"><a class="step" href="/?date={day - timedelta(days=1)}{aq}" aria-label="Previous day" '
           f'title="Previous day">&#8249;</a><button class="step dbtn" id="dbtn" type="button" aria-label="Pick a day" '
           f'title="Pick a day">{LK.CAL_ICON}{esc(_day_label(day, today))}</button>'
           f'<a class="step" href="/?date={day + timedelta(days=1)}{aq}" aria-label="Next day" title="Next day">&#8250;</a>'
           + ('' if day == today else f'<a class="today" href="/?date={today}{aq}">Today</a>')
           + '<div class="minical" id="minical"></div></nav>')
    if len(cfg.approvers) == 1:
        signer = f'<input type="hidden" id="who" value="{esc(cfg.approvers[0])}">'

    else:
        people = "".join(f"<option>{esc(p)}</option>" for p in cfg.approvers)
        signer = f'<span class="who">Signing as</span><select id="who" aria-label="Signing as">{people}</select>'
    needs = _needs(cfg, now, acct)
    casts = CQ.section(cfg, now, acct)
    parts = _parts(cfg, day, now, acct, needs)
    inband = set(needs["keys"])
    q = "" if acct == "all" else "?acct=" + urllib.parse.quote(acct)
    lanes = [f'<section class="needs lane" id="needs" aria-label="Needs you"><div class="who">{needs["head"]}</div>'
             f'<div class="lane-main"><div class="lane-row"><div class="grid" id="needs-grid">'
             f'{"".join(c["html"] for c in needs["cards"])}</div></div></div></section>']
    by_id = {f"acct-{a.slug}": a for a in _accts(cfg, acct)}
    for s in secs:
        shown = [c for c in s["cards"]
                 if f'{s["id"][5:]}|{day.isoformat()}|{c["id"][-4:-2]}:{c["id"][-2:]}' not in inband]
        big = "".join(c["html"] for c in shown if c.get("big"))
        rest = "".join(c["html"] for c in shown if not c.get("big"))
        a = by_id.get(s["id"])
        before = after = ""
        if a is not None:
            before = LK.lane_end(a, day - timedelta(days=1), "Yesterday" if day == today else "Day before", today, q)
            after = LK.lane_end(a, day + timedelta(days=1), "Tomorrow" if day == today else "Day after", today, q)
        lanes.append(f'<section id="{s["id"]}" class="lane"><div class="who">{s["head"]}</div><div class="lane-main">'
                     f'<div class="lane-row">{before}<div class="grid big">{big}</div><div class="grid rest">{rest}</div>'
                     f'{after}</div>{s["routes"]}{s["empty"]}</div></section>')
    body = [parts["kpis"], parts["breaking"],
            f'<div class="wrap"><div class="lanes">{CQ.section_html(casts)}<div class="lanes-h"><span class="label">Clips</span>{LK.legend()}'
            f'<div class="seg" id="dens" role="group" aria-label="Show"><button type="button" data-d="pic" '
            f'aria-pressed="true">Pictures</button><button type="button" data-d="list" aria-pressed="false">List</button>'
            f'</div></div>{"".join(lanes)}</div>{parts["rail"]}</div>',
            '<section class="cal calbox" id="cal" aria-label="Calendar" hidden></section>']
    js = (JS.replace("__TOKEN__", json.dumps(tok)).replace("__DAY__", json.dumps(day.isoformat()))
          .replace("__POLL__", str(POLL_SECONDS))
          .replace("__STAMP__", json.dumps(_stamp(secs, cal, needs, casts, parts["kpis"] + parts["breaking"] + parts["rail"])))
          .replace("__ACCT__", json.dumps(acct)).replace("__CAL__", json.dumps(cal).replace("</", "<\\/"))
          .replace("__REASONS__", json.dumps(_reasons()).replace("</", "<\\/")))
    js += "\nconst MINI=" + LK.mini(_accts(cfg, acct), day).replace("</", "<\\/") + ";\n" + LK.JS
    return ("<!doctype html><html lang=en><head><meta charset=utf-8>"
            "<meta name=viewport content='width=device-width,initial-scale=1'>"
            f"<title>Clip Review · {esc(_day_label(day, today))}</title><style>{CSS + REG.card_css()}</style></head><body class=v3>"
            f"<header><h1>Clip Review</h1>{tabs('clips', acct, cfg)}{switch(cfg, 'clips', acct, day)}{nav}"
            f"<span class=grow></span>{signer}</header>"
            f'<main>{"".join(body)}</main><div id="toast" role="status" aria-live="polite" hidden></div>'
            '<div class="scrim" id="scrim"></div><button class="btn icon drawer-x" id="drawer-x" type="button" '
            'aria-label="Close">&times;</button>'
            f"<script>{js}{REG.card_js()}</script></body></html>")


def live(cfg: C.Config, day: date, now: Optional[datetime] = None, acct: str = "all") -> dict:
    now = now or cfg.now()
    acct = pick_acct(cfg, acct)
    secs = _sections(cfg, day, now, acct)
    cal = calendar(cfg, now, acct)
    needs = _needs(cfg, now, acct)
    casts = CQ.section(cfg, now, acct)
    parts = _parts(cfg, day, now, acct, needs)
    return {"stamp": _stamp(secs, cal, needs, casts, parts["kpis"] + parts["breaking"] + parts["rail"]),
            "sections": secs, "cal": cal, "needs": needs, "casts": casts, **parts}


# ------------------------------------------------------------------------------------------------------ actions
def act(cfg: C.Config, body: dict, now: Optional[datetime] = None, page: bool = False) -> Tuple[bool, str]:
    """One click: approve | pull | retry | decline | reveal | schedule | stats-update | stats-settings, and the
    Casts taps (cast-confirm | cast-reject | cast-undo | cast-stills: castqueue.act, which takes them only when
    `page`, a browser request from this page itself). An op with a social prefix (inbox-, card-, log- ...) goes to
    its own part first (registry.act_module). (did it do what was asked, what to say)"""
    now = now or cfg.now()
    mod = REG.act_module(str(body.get("op") or ""))     # a social tab's own clicks (registry.ACT_PREFIXES)
    if mod is not None:
        return mod.act(cfg, body, page=page)
    if str(body.get("op") or "").startswith("cast-"):
        return CQ.act(cfg, body, page=page)
    if str(body.get("op") or "").startswith("lab-"):  # the Lab tab (lab.act: the outcome and the tags only from the page)
        from . import lab as L
        return L.act(cfg, body, page=page)
    if body.get("op") == "stats-update":          # the Stats tab: read the numbers again
        return SP.start_update(cfg)
    if body.get("op") == "stats-settings":        # the Stats tab: payout rates and the monthly goal
        return SP.save_settings(body)
    acct = cfg.account(str(body.get("account", "")))
    day = date.fromisoformat(str(body.get("date", "")))
    by = str(body.get("by") or (cfg.approvers[0] if cfg.approvers else ""))
    slots = body.get("slots")
    if slots is not None and (not isinstance(slots, list) or not all(isinstance(x, str) for x in slots)):
        raise SystemExit("slots must be a list of times")
    op = body.get("op")
    if op == "approve":
        everything = not slots
        if not slots:     # Approve all: every ready clip whose time has not gone past posting
            bd = B.read(acct, day) or {"slots": {}}
            pairs = MO.twins(cfg, acct, day, bd)
            slots = [k for k, s in sorted(bd["slots"].items())
                     if s["status"] in B.SIGNABLE and not _too_late(s, now) and not _missing(acct, s)
                     and not MO.bulk_skip(bd, k, pairs.get(k, [])) and not MO.refuse_second(bd, pairs.get(k, []))]
            if not slots:
                return False, "Nothing is waiting for a sign-off (a clip marked stale, or one of two clips of one moment, waits for its own Approve)."
        seen = body.get("seen")
        if not isinstance(seen, dict):
            raise SystemExit("the page must say what it showed (reload it)")
        lines = S.approve(cfg, acct, day, slots, by, now=now, seen={str(k): str(v) for k, v in seen.items()},
                          everything=everything)
        sent = [ln for ln in lines if "goes out" in ln]
        if sent:
            P.kick(cfg)
            lines.append(f"{O.routes(acct)}.")
        return bool(sent) and len(sent) == len(slots), _say(lines)
    if op == "pull":
        lines = S.pull(cfg, acct, day, slots or [], by, code=str(body.get("code") or "") or None,
                       reason=" ".join(str(body.get("reason") or "").split())[:400])
        return any(": pulled" in ln for ln in lines), _say(lines)
    if op == "retry":
        lines = S.retry(cfg, acct, day, slots or [], by)
        ok = any("tried again" in ln and "NOT" not in ln for ln in lines)
        if ok:
            P.kick(cfg)
        return ok, _say(lines)
    if op == "decline":
        reasons = body.get("reasons") or []
        if not isinstance(reasons, list) or not all(isinstance(x, str) for x in reasons):
            raise SystemExit("reasons must be a list of reason codes")
        lines = _decline(cfg, acct, day, slots or [], by, " ".join(str(body.get("reason") or "").split())[:400],
                         bool(body.get("remake")), reasons, code=str(body.get("code") or "") or None)
        return any(ln.endswith(("declined", "a new clip")) for ln in lines), _say(lines)
    if op == "reveal":
        return _reveal(cfg, acct, day, (slots or [""])[0])
    if op == "schedule":
        lines = O.schedule(cfg, acct, day)
        P.kick(cfg)
        return True, _say(lines)
    if op in ("tt-set", "tt-caption", "tt-now"):
        return _tt_act(cfg, acct, day, op, C.normal_slot((slots or [""])[0]), body, now)
    raise SystemExit(f"unknown action {op!r}")


def _tt_act(cfg: C.Config, acct: C.Account, day: date, op: str, slot: str, body: dict, now: datetime) -> Tuple[bool, str]:
    """The TikTok panel: a choice (tt-set), the caption before signing (tt-caption), Post to TikTok now (tt-now)."""
    s = ((B.read(acct, day) or {}).get("slots") or {}).get(slot)
    if not s or not s.get("clip"):
        return False, "No clip in that slot."
    if op == "tt-set":
        field, value = str(body.get("field") or ""), body.get("value")
        if field == "privacy_level":
            value = str(value or "")
            if value and value not in D.PRIVACY_NAMES:
                return False, "That is not one of TikTok's choices."
            ch = s.get("tiktok_post") or {}
            if value == "SELF_ONLY" and ch.get("commercial") and ch.get("brand_content"):
                return False, "Branded content visibility cannot be set to private."
        elif field in D.TT_FLAGS:
            value = bool(value)
        else:
            return False, "Unknown TikTok setting."
        said = "Saved."
        with B.edit(cfg, acct, day, create=False) as bd:
            ch = bd["slots"][slot].setdefault("tiktok_post", {})
            ch[field] = value
            if field == "brand_content" and value and ch.get("privacy_level") == "SELF_ONLY":
                ch["privacy_level"] = ""     # TikTok: branded content can't be private, so the person chooses again
                said = "Branded content can't be private, so 'Only me' was cleared: choose who can see it again."
            ch["at"] = B.utcnow()
        return True, said
    if op == "tt-caption":
        if s["status"] not in B.SIGNABLE:
            return False, "It is signed: the caption can't change now. Pull it to change the caption."
        c = (s.get("copy") or {}).get("tiktok")
        if not c:
            return False, "This clip has no TikTok words."
        text = str(body.get("text") or "").replace("\r\n", "\n").strip()
        if not text:
            return False, "The caption is empty."
        if len(text) > 2200:
            return False, f"TikTok captions stop at 2,200 characters (this one is {len(text)})."
        Path(c["path"]).write_text(text + "\n")
        return True, "Caption saved. Approve signs it as it is now."
    # tt-now
    if s["status"] not in B.SIGNED or not s.get("signoff"):
        return False, "Approve it first: Post to TikTok now sends only signed clips."
    if ((s.get("outlets") or {}).get("tiktok") or {}).get("post_id"):
        return False, "It is already on TikTok."
    if P.tiktok_busy((s.get("outlets") or {}).get("tiktok") or {}, now):
        return False, "It is already on its way to TikTok; this card says when it is up."
    problems = B.verify(s, cached=True)
    if problems:
        return False, "Changed after signing: " + "; ".join(problems)
    try:
        info = D.creator_info(acct, max_age=0)
    except Exception as e:  # noqa: BLE001
        return False, f"Can't reach TikTok for this account right now: {str(e)[:200]}"
    audited = bool(acct.outlets.get("tiktok", {}).get("api_app_audited"))
    why = D.tiktok_problems(s.get("tiktok_post") or {}, info, float((s.get("clip") or {}).get("seconds") or 0), audited)
    if why:
        return False, " ".join(why)
    if not P.spawn_tiktok_now(cfg, acct, day, slot):
        return False, "Posting is switched off here (offline or a test)."
    return True, ("Sent to TikTok. TikTok can take a few minutes to process it before it shows on your profile; "
                  "this card says when it is up.")


def _say(lines: List[str]) -> str:
    return "\n".join(ln.strip() for ln in lines if ln.strip() and not ln.startswith("  export")) or "Done."


def _reveal(cfg: C.Config, acct: C.Account, day: date, slot: str) -> Tuple[bool, str]:
    """Show the clip in Finder: its copy in the Ready to post folder of the first hand platform, else the draft."""
    s = ((B.read(acct, day) or {}).get("slots") or {}).get(C.normal_slot(slot)) if slot else None
    if not s or not s.get("clip"):
        return False, "No clip in that slot."
    target = Path(s["clip"]["path"])
    for plat in [p for p in acct.platforms if not O.covers_slot(acct, p, s)]:
        p = K.drafts_dir(cfg, acct, day) / "Ready to post" / plat / f"{s['slot'].replace(':', '')} {s.get('slug', '')}.mp4"
        if p.is_file():
            target = p
            break
    if not _allowed_file(cfg, target):
        return False, "That file is outside the drafts."
    subprocess.run(["open", "-R", str(target)], capture_output=True, timeout=15)
    return True, f"Shown in Finder: {target.parent.name}/{target.name}"


STATE_FILES = (".mp4", ".jpg", ".jpeg", ".png", ".srt")   # what /file may show from state/ (cards, covers, captions)
LAB_FILES = (".mp4", ".mov", ".webm", ".jpg")


def _allowed_file(cfg: C.Config, p: Path) -> bool:
    """May /file serve this path: a file in the drafts, a cast still, a client's drafts, or under state/ only a
    video, a picture or captions (never a JSON file, a log or the page's own review-token)."""
    try:
        p = p.resolve()
        if p.is_file() and cfg.drafts_root.resolve() in p.parents:
            return True
        if p.is_file() and C.STATE.resolve() in p.parents:
            return p.suffix.lower() in STATE_FILES and p.name != "review-token"
        if CQ.still_ok(p):                  # a cast still in a job's castcheck folder (Casts, castqueue.py)
            return True
        if p.is_file() and p.suffix.lower() in LAB_FILES:     # a Lab take and its frames, under its capture root
            from . import lab as L
            for a in L.lab_accounts(cfg):
                try:
                    if L.capture_root(a).resolve() in p.parents:
                        return True
                except (L.LabError, OSError):
                    continue
        from . import accounts as AC        # a client account's own drafts folder (accounts.py)
        return p.is_file() and any(r.resolve() in p.parents for r in AC.drafts_roots(cfg))
    except OSError:
        return False


# ------------------------------------------------------------------------------------------------------ the server
class _Live:
    """The config, reloaded when accounts.json changes (the page runs for days; slots and outlets do change)."""

    def __init__(self, cfg: C.Config):
        self.cfg, self.mtime = cfg, self._mtime(cfg.path)

    @staticmethod
    def _mtime(p: Path) -> float:
        try:
            return p.stat().st_mtime
        except OSError:
            return 0.0

    def get(self) -> C.Config:
        m = self._mtime(self.cfg.path)
        if m != self.mtime:
            try:
                self.cfg = C.load(self.cfg.path)
                self.mtime = m
            except Exception:  # noqa: BLE001  (a half-saved file: keep the last good one, try again next time)
                pass
        return self.cfg


def _from_page(headers, hosts) -> bool:
    """A browser on this page sent it: an Origin of the page (a browser sends one with every POST; a script by hand
    does not, unless it fakes it) and, when the browser says, a same-origin fetch. Only the Casts taps ask for it
    (castqueue.act): a cast is checked by a person on the page, never by a bot or a command."""
    origin = headers.get("Origin", "")
    if not origin or urllib.parse.urlparse(origin).netloc not in hosts:
        return False
    return headers.get("Sec-Fetch-Site", "same-origin") == "same-origin"


def make_handler(cfg: C.Config):
    port = cfg.review_port
    hosts = {f"127.0.0.1:{port}", f"localhost:{port}"}
    tok = token()
    conf = _Live(cfg)
    csp = ("default-src 'self'; style-src 'unsafe-inline'; script-src 'unsafe-inline'; media-src 'self'; "
           "img-src 'self'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'")

    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _send(self, code: int, body: bytes, ctype: str = "text/plain; charset=utf-8", extra: Optional[dict] = None):
            try:
                self.send_response(code)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self.send_header("X-Content-Type-Options", "nosniff")
                for k, v in (extra or {}).items():
                    self.send_header(k, v)
                self.end_headers()
                self.wfile.write(body)
            except (BrokenPipeError, ConnectionResetError):
                pass

        def _json(self, code: int, obj: dict):
            self._send(code, json.dumps(obj).encode(), "application/json; charset=utf-8")

        def do_GET(self):  # noqa: N802
            from . import clientpage as CP         # a client's own page, /c/<its secret>/ (clientpage.py)
            if CP.get(self, conf.get(), hosts):
                return
            if self.headers.get("Host") not in hosts:
                return self._send(403, b"loopback only")
            u = urllib.parse.urlparse(self.path)
            q = dict(urllib.parse.parse_qsl(u.query))
            c = conf.get()
            try:
                acct = pick_acct(c, q.get("acct"))
                if u.path == "/":
                    day = C.parse_day(q.get("date") or "today", c)
                    return self._send(200, page(c, day, tok=tok, acct=acct).encode(), "text/html; charset=utf-8",
                                      {"Content-Security-Policy": csp, "Referrer-Policy": "no-referrer"})
                if u.path == "/calendar":
                    try:
                        month = date.fromisoformat((q.get("month") or "")[:7] + "-01")
                    except ValueError:
                        month = c.now().date().replace(day=1)
                    aq = "" if acct == "all" else "?acct=" + urllib.parse.quote(acct)
                    return self._send(200, LK.calendar_page(c, c.now(), _accts(c, acct), month, tok, CSS,
                                                            tabs("calendar", acct, c), switch(c, "calendar", acct),
                                                            aq).encode(), "text/html; charset=utf-8",
                                      {"Content-Security-Policy": csp, "Referrer-Policy": "no-referrer"})
                if u.path == "/money":
                    from . import moneypage as MP
                    return self._send(200, MP.page(c, c.now(), tok, CSS, tabs("money", acct, c), switch(c, "money", acct),
                                                   acct).encode(), "text/html; charset=utf-8",
                                      {"Content-Security-Policy": csp, "Referrer-Policy": "no-referrer"})
                if u.path.startswith("/logo/") and u.path.endswith(".jpg"):
                    lf = LK.logo_file(u.path[len("/logo/"):-len(".jpg")])
                    if lf is None:
                        return self._send(404, b"no picture")
                    return self._send(200, lf.read_bytes(), "image/jpeg")
                if u.path == "/live":
                    day = C.parse_day(q.get("date") or "today", c)
                    return self._json(200, live(c, day, acct=acct))
                if u.path == "/stats":
                    return self._send(200, SP.page(tok, CSS, tabs("stats", acct, c), switch(c, "stats", acct), acct).encode(),
                                      "text/html; charset=utf-8",
                                      {"Content-Security-Policy": csp, "Referrer-Policy": "no-referrer"})
                if u.path == "/stats/data":
                    return self._json(200, SP.data(c))
                hit = REG.route(u.path)             # a social tab, its /data, its own GETs (registry.py)
                if hit:
                    return self._social(c, hit, q, acct)
                if u.path == "/lab":                   # Lab's tab (labpage.py): record, mark, say what happened
                    from . import labpage as LP
                    return self._send(200, LP.page(c, tok, CSS, tabs("lab", "all", c)).encode(),
                                      "text/html; charset=utf-8",
                                      {"Content-Security-Policy": csp, "Referrer-Policy": "no-referrer"})
                if u.path == "/lab/live":
                    from . import labpage as LP
                    return self._json(200, LP.live(c))
                if u.path == "/file":
                    p = Path(q.get("p", ""))
                    if not _allowed_file(c, p):
                        return self._send(404, b"not found")
                    return self._file(p, versioned=bool(q.get("v")))
                if u.path == "/favicon.ico":
                    return self._send(204, b"")
            except ValueError as e:
                return self._send(400, f"bad request: {e}".encode())
            except SystemExit as e:             # a refusal in plain words (a social page's own GET): said, never a
                return self._send(400, str(e).encode())     # dropped connection
            except Exception as e:  # noqa: BLE001
                return self._send(500, f"error: {type(e).__name__}: {e}".encode())
            return self._send(404, b"not found")

        def _social(self, c: C.Config, hit: tuple, q: dict, acct: str):
            """A social part's GET: its page (with the CSP), its /data JSON, its own <url>/<rest>, or a SUBROUTES
            path. A part not built yet is a 404."""
            kind, pg, rest = hit
            page_h = {"Content-Security-Policy": csp, "Referrer-Policy": "no-referrer"}

            def raw(answer):
                code, ctype, body = answer
                body = body.encode() if isinstance(body, str) else bytes(body)
                return self._send(int(code), body, str(ctype), page_h if str(ctype).startswith("text/html") else None)
            if kind == "sub":
                f = REG.fn(rest)
                return raw(f(c, q)) if f is not None else self._send(404, b"not built yet")
            mod = REG.load(pg.module)
            if mod is None:
                if pg.module in REG.BROKEN:
                    return self._send(500, f"error: {REG.BROKEN[pg.module]}".encode())
                return self._send(404, b"not built yet")
            if kind == "page":
                try:                            # a page with its own day (Week: ?date=) keeps it on the switch
                    day = date.fromisoformat(q["date"]) if q.get("date") else None
                except ValueError:
                    day = None
                out = mod.page(c, tok, CSS + SP.CSS, tabs(pg.key, acct), switch(c, pg.key, acct, day), acct, q)
                return self._send(200, out.encode(), "text/html; charset=utf-8", page_h)
            if kind == "data":
                return self._json(200, mod.data(c, q))
            if not callable(getattr(mod, "get", None)):
                return self._send(404, b"not found")
            return raw(mod.get(c, rest, q))

        def _file(self, p: Path, versioned: bool):
            size = p.stat().st_size
            ctype = mimetypes.guess_type(str(p))[0] or "application/octet-stream"
            start, end = 0, size - 1
            rng = self.headers.get("Range", "")
            partial = False
            if rng.startswith("bytes=") and "," not in rng:      # one range (a list of ranges gets the whole file)
                a, _, b = rng[6:].strip().partition("-")
                try:
                    if a:
                        start, end = int(a), min(int(b), size - 1) if b else size - 1
                    elif b:
                        start = max(0, size - int(b))
                    partial = True
                except ValueError:
                    partial = False
                if partial and (start >= size or start > end):
                    return self._send(416, b"", extra={"Content-Range": f"bytes */{size}"})
            length = end - start + 1 if size else 0
            try:
                self.send_response(206 if partial else 200)
                self.send_header("Content-Type", ctype)
                self.send_header("Accept-Ranges", "bytes")
                self.send_header("Content-Length", str(length))
                # the URL carries the file's size and time (?v=), so a changed file is a new URL: cache the rest
                self.send_header("Cache-Control", "private, max-age=86400" if versioned else "no-store")
                if partial:
                    self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
                self.end_headers()
                with open(p, "rb") as f:
                    f.seek(start)
                    left = length
                    while left > 0:
                        chunk = f.read(min(1 << 20, left))
                        if not chunk:
                            break
                        self.wfile.write(chunk)
                        left -= len(chunk)
            except (BrokenPipeError, ConnectionResetError):
                return

        def do_POST(self):  # noqa: N802
            from . import clientpage as CP         # a client's own page, /c/<its secret>/ (clientpage.py)
            if CP.post(self, conf.get(), hosts):
                return
            origin = self.headers.get("Origin", "")
            if self.headers.get("Host") not in hosts or (origin and urllib.parse.urlparse(origin).netloc not in hosts):
                return self._json(403, {"ok": False, "message": "refused: not from the review page"})
            if self.headers.get("X-Token") != tok:
                return self._json(403, {"ok": False, "stale": True, "message": "refused: this tab is out of date"})
            if urllib.parse.urlparse(self.path).path != "/act":
                return self._json(404, {"ok": False, "message": "not found"})
            n = int(self.headers.get("Content-Length") or 0)
            if n > 16384:
                return self._json(413, {"ok": False, "message": "too large"})
            try:
                body = json.loads(self.rfile.read(n) or b"{}")
                if not isinstance(body, dict):
                    raise SystemExit("the request must be a JSON object")
                ok, message = act(conf.get(), body, page=_from_page(self.headers, hosts))
            except SystemExit as ex:              # the factory's own refusals: said to the person as they are
                return self._json(400, {"ok": False, "message": str(ex)})
            except (KeyError, ValueError, TypeError) as ex:
                return self._json(400, {"ok": False, "message": f"bad request: {ex}"})
            except Exception as ex:  # noqa: BLE001
                return self._json(500, {"ok": False, "message": f"error: {type(ex).__name__}: {ex}"})
            return self._json(200, {"ok": ok, "message": message})

    return H


class Server(ThreadingHTTPServer):
    daemon_threads = True          # a stalled video request never holds the page up on restart
    allow_reuse_address = True


def serve(cfg: C.Config) -> None:
    try:
        srv = Server(("127.0.0.1", cfg.review_port), make_handler(cfg))
    except OSError as e:
        if e.errno == errno.EADDRINUSE:
            raise SystemExit(f"The review page is already running at http://127.0.0.1:{cfg.review_port}/ "
                             "(launchd keeps it on). Open that.") from None
        raise
    print(f"Clip review: http://127.0.0.1:{cfg.review_port}/   (Ctrl-C to stop)", flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
