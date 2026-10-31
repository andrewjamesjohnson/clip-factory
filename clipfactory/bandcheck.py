"""Cuts that no longer fit their outlet's band (2026-10-22, LESSONS "Build next" row 6).

At 11:33 on 10-21 the Politics's Instagram limit went from 50 to 40 seconds (497aad6). Four clips signed that morning
carried cuts made the night before for the old 30-50 band (43.6 to 49.6 s). Each was refused when its time came
("not posted: instagram_reels.mp4 is 46.7s, outside Politics's 30-40s band for Instagram"), and the card's
Try again only sent it to be refused again. A band is read when a cut is collected and when it posts, never in
between, so nothing said a word until the post was lost.

misses() lists every platform cut on a slot that still has to post (today's and tomorrow's boards) and sits outside
its outlet's band as config/accounts.json says it NOW, and any whole clip outside the account's own band. Read by
`factory.py doctor`, `factory.py bandcheck`, the morning notice and the review page (the account head and each
card's platform row, which then offers no Try again for that platform: signoff.retry leaves it alone too). The
way out is a new cut: `factory.py igcut <account> --date D --slug S --pick`; it joins an unsigned clip at once,
and a signed one when a person presses Pull, watches it and approves it again.

Nothing here reads a file but the boards and the config, and nothing is written.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Dict, Iterable, List, Optional

from . import board as B
from . import config as C
from . import outlets as O

YOUTUBE = "youtube_shorts"
SLACK = 0.05            # the same rounding every band check in the factory allows


def _num(x: float) -> str:
    """46.7 -> '46.7', 40.0 -> '40'."""
    return f"{x:.1f}".rstrip("0").rstrip(".") if abs(x - round(x)) > 0.049 else f"{x:.0f}"


def wall(acct: C.Account, plat: str, has_cut: bool) -> tuple:
    """(lo, hi): the band a file for this platform must sit in to post. A cut is admitted against the outlet's cut
    band (collect, sign-off) and posted against its post band (direct.ig_post / fb_post / the TikTok post check the
    platform's own band; YouTube checks the cut band when it has a cut): a cut must sit inside both."""
    if not has_cut:
        return O.band(acct, plat)
    lo, hi = O.band(acct, plat, cut=True)
    if plat != YOUTUBE:
        plo, phi = O.band(acct, plat)
        lo, hi = max(lo, plo), min(hi, phi)
    return lo, hi


def _posted(rec: dict) -> bool:
    return bool(rec.get("post_id") or rec.get("video_id"))


def _late(s: dict, now: Optional[datetime]) -> bool:
    """Past the hour the poster still posts a slot late (poster.LATE_LIMIT): nothing more goes out for it."""
    if now is None:
        return False
    from . import poster as P
    try:
        return now - datetime.fromisoformat(str(s.get("publish_at"))) > P.LATE_LIMIT
    except (TypeError, ValueError):
        return False


def slot_misses(acct: C.Account, s: dict, now: Optional[datetime] = None) -> Dict[str, dict]:
    """platform -> the miss, for one slot: each platform that still has to post this slot (posts by itself, not out
    yet, gave up or not) whose file sits outside the band it must post in now. {} when every one fits, the slot holds
    no clip, nobody will post it (declined, failed), or it is past posting."""
    if not s.get("clip") or s.get("status") not in B.SIGNABLE + B.SIGNED or _late(s, now):
        return {}
    out: Dict[str, dict] = {}
    cuts = s.get("cuts") or {}
    recs = s.get("outlets") or {}
    clip_secs = float((s.get("clip") or {}).get("seconds") or 0)
    for plat in O.auto_platforms(acct):
        rec = recs.get(plat) or {}
        if _posted(rec) or not O.covers_slot(acct, plat, s):
            continue
        cut = cuts.get(plat)
        if cut:
            secs = float(cut.get("seconds") or 0)
            lo, hi = wall(acct, plat, True)
            if secs and not lo - SLACK <= secs <= hi + SLACK:
                out[plat] = {"slot": s.get("slot"), "slug": s.get("slug"), "plat": plat, "what": "cut",
                             "seconds": secs, "lo": lo, "hi": hi, "signed": s.get("status") in B.SIGNED,
                             "gave_up": bool(rec.get("gave_up"))}
        elif clip_secs and not acct.min_seconds - SLACK <= clip_secs <= acct.max_seconds + SLACK:
            # the whole clip, outside the ACCOUNT's own band: collect admitted it, so that band changed since
            out[plat] = {"slot": s.get("slot"), "slug": s.get("slug"), "plat": plat, "what": "clip",
                         "seconds": clip_secs, "lo": acct.min_seconds, "hi": acct.max_seconds,
                         "signed": s.get("status") in B.SIGNED, "gave_up": bool(rec.get("gave_up"))}
    return out


def misses(cfg: C.Config, acct: C.Account, days: Optional[Iterable[date]] = None,
           now: Optional[datetime] = None) -> List[dict]:
    """Every miss on the account's boards for `days` (default: today and tomorrow), slot by slot."""
    now = now or cfg.now()
    days = list(days) if days is not None else [now.date(), now.date() + timedelta(days=1)]
    out: List[dict] = []
    for day in days:
        bd = B.read(acct, day) or {}
        for s in sorted((bd.get("slots") or {}).values(), key=lambda x: str(x.get("slot"))):
            for m in slot_misses(acct, s, now).values():
                out.append(dict(m, date=day.isoformat(), account=acct.slug, name=acct.name))
    return out


def words(m: dict, acct: Optional[C.Account] = None) -> str:
    """The card's plain words: 'Instagram cut is 46.7 s. Instagram takes 30 to 40 s here. It needs a new cut.'"""
    name = O.NAMES.get(m["plat"], m["plat"])
    if m["what"] == "clip":
        who = acct.name if acct is not None else "This account"
        return (f"The clip is {_num(m['seconds'])} s. {who} takes {_num(m['lo'])} to {_num(m['hi'])} s now. "
                f"It needs a new render.")
    return (f"{name} cut is {_num(m['seconds'])} s. {name} takes {_num(m['lo'])} to {_num(m['hi'])} s here. "
            f"It needs a new cut.")


def how(m: dict) -> str:
    """The command that makes the new cut, and what a signed slot then needs."""
    if m["what"] == "clip":
        return "make the clip again (Make again on an unsigned card; a signed one: Pull it first)"
    cmd = "ytcut" if m["plat"] == YOUTUBE else "igcut"
    tail = "; it is signed: then Pull, watch the new cut and Approve" if m.get("signed") else \
        "; it joins the unsigned clip by itself"
    return f"factory.py {cmd} {m['account']} --date {m['date']} --slug {m['slug']} --pick{tail}"


def _short(m: dict) -> str:
    return f"{m['slot']} {O.NAMES.get(m['plat'], m['plat'])} {_num(m['seconds'])}s"


def doctor_rows(cfg: C.Config, now: Optional[datetime] = None) -> List[tuple]:
    """(good, text, fix) per miss on today's and tomorrow's boards, or one good line."""
    rows = []
    for acct in cfg.accounts.values():
        for m in misses(cfg, acct, now=now):
            rows.append((False, f"{acct.name} {m['date']} {m['slot']} {m['slug']}: {words(m, acct)}"
                         + (" Signed: it will not post there as it is." if m["signed"] else ""), how(m)))
    if not rows:
        rows.append((True, "every cut on today's and tomorrow's boards fits its outlet's band as it is now", ""))
    return rows


def summary(acct: C.Account, ms: List[dict]) -> str:
    """One line for the morning notice and the review page: '' when nothing misses."""
    if not ms:
        return ""
    by_day: Dict[str, List[dict]] = {}
    for m in ms:
        by_day.setdefault(m["date"], []).append(m)
    parts = []
    for d, rows in sorted(by_day.items()):
        lo_hi = {(O.NAMES.get(m["plat"], m["plat"]), _num(m["lo"]), _num(m["hi"])) for m in rows}
        takes = "; ".join(f"{n} takes {lo} to {hi} s" for n, lo, hi in sorted(lo_hi))
        parts.append(f"{d[5:]}: " + ", ".join(_short(m) for m in rows) + f" ({takes} now)")
    n = len(ms)
    return (f"{acct.name}: {n} cut{'s' if n != 1 else ''} no longer fit{'s' if n == 1 else ''} the band, so "
            f"{'they' if n != 1 else 'it'} will not post as {'they are' if n != 1 else 'it is'}: "
            + "; ".join(parts) + ". Make new cuts (factory.py bandcheck says how).")


def morning_lines(cfg: C.Config, now: Optional[datetime] = None, accts=None,
                  days: Optional[Iterable[date]] = None) -> List[str]:
    """The morning notice's lines: one per account with a miss (on `days`, default today and tomorrow)."""
    out = []
    for acct in (accts if accts is not None else cfg.accounts.values()):
        line = summary(acct, misses(cfg, acct, days=list(days) if days is not None else None, now=now))
        if line:
            out.append(line)
    return out


def lines(cfg: C.Config, acct: Optional[C.Account] = None, days: Optional[Iterable[date]] = None,
          now: Optional[datetime] = None) -> List[str]:
    """`factory.py bandcheck`: each miss with the command that fixes it."""
    out = []
    for a in ([acct] if acct is not None else list(cfg.accounts.values())):
        ms = misses(cfg, a, days=days, now=now)
        if not ms:
            out.append(f"  {a.name}: every cut fits its outlet's band as it is now")
            continue
        for m in ms:
            out.append(f"  {a.name} {m['date']} {m['slot']} {m['slug']}: {words(m, a)}"
                       + (" (signed)" if m["signed"] else "") + (" (it gave up already)" if m["gave_up"] else ""))
            out.append(f"      {how(m)}")
    return out
