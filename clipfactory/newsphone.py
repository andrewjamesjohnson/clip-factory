"""The news desk on Andrew's phone (2026-10-29): both questions as ntfy pushes with buttons (clipfactory/phone.py).

Why: the desk asked with Claude's PushNotification, which never reached the phone (the same as the breaking desk),
and a task session cannot be answered from the phone (no Remote Control). Now:

  1. The desk checks the request first, then `cards news ask <account> <file> --what "<line>"`: the request waits
     in state/<slug>/cards/news-asks/<day>/<id>.json and the phone gets NEWS POST? with Yes / No.
  2. Yes (`phone poll`): `add_news` exactly as the desk would after a chat yes; once it is drawn, the phone gets
     the two slides and READY with Post it / No. The Post it button carries the hash of THOSE pictures.
  3. Post it: `approve` with that hash, signed "Andrew (phone): Post it (the button)". A card that changed since
     the pictures went out is refused, as on Clip Review.

A chat answer still works the old way (`cards news add` refuses an id the phone already made).
"""
from __future__ import annotations

import json
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import List, Optional

from . import cards as K
from . import config as C
from . import phone
from . import store

ASK_TTL = timedelta(hours=3)        # the desk's own rule: an answer after 3 hours is too late, the story moved on


def asks_dir(acct, day) -> Path:
    return store.path(K._slug(acct), "cards", "news-asks", str(day)[:10])


def _read(acct, day, nid: str) -> Optional[dict]:
    p = asks_dir(acct, day) / f"{nid}.json"
    return json.loads(p.read_text()) if p.exists() else None


def _mark(acct, day, nid: str, **kw) -> None:
    with store.edit(asks_dir(acct, day) / f"{nid}.json", dict) as d:
        d.update(kw)


def ask(cfg: C.Config, acct, req: dict, what: str, now: Optional[datetime] = None) -> List[str]:
    """Park a checked request and ask on the phone: NEWS POST? <line>. Make it? Yes / No."""
    now = K._now(cfg, now)
    day = K._local(cfg, now).date()
    bad, _ = K.check_news(acct, req)
    if bad:
        raise SystemExit("Not asked: " + "; ".join(bad))
    nid = str(req["id"])
    folder = asks_dir(acct, day)
    store._inside(folder)
    folder.mkdir(parents=True, exist_ok=True)
    if (folder / f"{nid}.json").exists():
        raise SystemExit(f"Not asked: news {nid} was asked already today")
    _mark(acct, day, nid, request=req, what=what[:200], asked_at=K._iso(now), status="asked")
    ref = f"{K._slug(acct)} {day.isoformat()} {nid}"
    ok, why = phone.send(f"NEWS POST? · {acct.name}", f"{what.strip().rstrip('.')}.\nGoes out the moment "
                         "you tap Post it. Make it?", tags="newspaper",
                         buttons=[("Yes, make it", f"news yes {ref}"), ("No", f"news no {ref}")])
    return [f"  news {nid}: asked, waiting for Andrew",
            "  PHONE: " + (f"sent ({why})" if ok else f"not sent ({why})")]


def _send_ready(cfg: C.Config, acct, day, nid: str) -> Optional[str]:
    """The drawn slides to the phone, the last with Post it / No. None while it is not drawn yet."""
    card = (K.read_board(acct, day).get("cards") or {}).get(f"news-{nid}")
    if not isinstance(card, dict):
        return None
    bad = K.file_problems(card, cached=False)
    if bad:
        phone.send("News post not ready", f"{nid}: " + "; ".join(bad)[:300], priority="default")
        return "problems"
    imgs = [Path(i.get("path")) for i in ((card.get("files") or {}).get("images") or []) if i.get("path")]
    where = ", ".join({"instagram_carousel": "Instagram", "facebook_feed": "Facebook"}.get(t, t)
                      for t in card.get("targets") or []) or "nowhere"
    head = (card.get("words") or {}).get("headline") or nid
    for i, img in enumerate(imgs[:-1]):
        phone.send(f"Slide {i + 1}", head, image=img, priority="default")
    ref = f"{K._slug(acct)} {str(day)[:10]} {nid} {K.card_hash(card)}"
    ok, why = phone.send(f"READY · {acct.name}", f"{head}.\nGoes to {where} right away. Post it?",
                         image=imgs[-1] if imgs else None, tags="newspaper",
                         buttons=[("Post it", f"news post {ref}"), ("No", f"news skip {ref}")])
    return "sent" if ok else f"not sent ({why})"


def answer(cfg: C.Config, words: List[str], now: Optional[datetime] = None) -> List[str]:
    """A button: yes|no <account> <day> <id>, or post|skip <account> <day> <id> <hash>."""
    now = K._now(cfg, now)
    if len(words) < 4 or words[0] not in ("yes", "no", "post", "skip"):
        return [f"  phone: not a news answer {words}"]
    verb, slug, d, nid = words[:4]
    acct, day = cfg.account(slug), date.fromisoformat(d)
    a = _read(acct, day, nid)
    if not a:
        return [f"  news {nid}: no such ask on {day}"]
    if verb == "no":
        _mark(acct, day, nid, status="no", no_at=K._iso(now))
        return [f"  news {nid}: Andrew said no on his phone"]
    if verb == "skip":
        _mark(acct, day, nid, status="skipped", skip_at=K._iso(now))
        return [f"  news {nid}: Andrew said no to the pictures; it expires unsigned"]
    if verb == "yes":
        if a.get("status") != "asked":
            return [f"  news {nid}: {a.get('status')} already"]
        if now - K._when(a["asked_at"]) > ASK_TTL:
            _mark(acct, day, nid, status="too late")
            phone.send("Too late for that one", f"{a.get('what')}: asked over 3 hours ago, the story moved on.",
                       priority="default")
            return [f"  news {nid}: yes came after 3 hours, not made"]
        try:
            lines = K.add_news(cfg, acct, a["request"], by="news desk (Andrew's phone yes)", now=now)
        except SystemExit as e:
            _mark(acct, day, nid, status="not made", why=str(e))
            phone.send("News post not made", str(e)[:300], priority="default")
            return [f"  news {nid}: {e}"]
        _mark(acct, day, nid, status="made", yes_at=K._iso(now))
        sent = _send_ready(cfg, acct, day, nid)
        if sent:
            _mark(acct, day, nid, ready=sent)
        else:
            phone.send("✅ Making it", "The pictures come in a few minutes.", priority="default")
        return lines + [f"  news {nid}: made on Andrew's phone yes; pictures {sent or 'when drawn'}"]
    # post
    if len(words) != 5:
        return [f"  news {nid}: Post it without the pictures' hash"]
    why = cfg.why_off(acct)
    try:
        if why:
            raise SystemExit(why)
        said = K.approve(cfg, acct, day, f"news-{nid}", words[4], "Andrew (phone): Post it (the button)", now=now)
    except SystemExit as e:
        phone.send("Not signed", str(e)[:300], priority="default")
        return [f"  news {nid}: not signed, {e}"]
    _mark(acct, day, nid, status="signed", signed_at=K._iso(now))
    phone.send("✅ Signed", said, priority="default")
    return [f"  news {nid}: {said}"]


def tick(cfg: C.Config, now: Optional[datetime] = None) -> List[str]:
    """Each minute: a post made on a phone yes whose pictures were not drawn yet gets them sent once drawn."""
    now = K._now(cfg, now)
    day = K._local(cfg, now).date()
    out: List[str] = []
    for acct in cfg.accounts.values():
        folder = asks_dir(acct, day)
        for p in sorted(folder.glob("*.json")) if folder.is_dir() else []:
            a = json.loads(p.read_text())
            if a.get("status") == "made" and not a.get("ready"):
                sent = _send_ready(cfg, acct, day, p.stem)
                if sent:
                    _mark(acct, day, p.stem, ready=sent)
                    out.append(f"  news {p.stem}: pictures {sent}")
    return out
