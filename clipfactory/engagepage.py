"""Clip Review's Inbox tab: comments and messages on the accounts' posts, Andrew's replies, hides and saved replies.

    http://127.0.0.1:8781/inbox                     Comments · Messages · Saved replies (and Automations, once built)
    http://127.0.0.1:8781/inbox/data                the page's JSON, re-read every 15 seconds (5 while sending)
    http://127.0.0.1:8781/inbox/suggest?acct=&item= a suggested answer from the facts on disk (answers.py)

Everything here reads files only: the comments and messages the social tick read (engage.py, socialstore), the posts
index, the switches. Nothing in a GET reaches a platform. Every op that speaks for an account (a reply, a private
reply, a message, a hide) is a click on this page (review._from_page) after a confirm that quotes the exact words and
where they go; it is queued as a signed click through outward (the one door out), and social.kick starts the sender so
it goes now instead of at the next tick.

    inbox-reply    {account, item, text, private, force}   a reply under the comment, or the one private reply
    inbox-dm       {account, thread, text, force}          an answer in a conversation (inside 23 hours)
    inbox-hide / inbox-unhide {account, item}              Instagram and Facebook hide; YouTube holds for review
    inbox-done / inbox-undone {account, item | thread}
    inbox-stop     {account, person, undo}                 no automated message to this person, ever (or again)
    inbox-forget   {account, person}                       their comments, messages and record removed
    inbox-tester   {account, item | thread}                "This is me (tester)": an app tester for testers mode
    inbox-check    {}                                      read now (at most every 2 minutes)
    saved-set / saved-delete {account, id, label, text, platforms}
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple

from . import config as C
from . import engage as E
from . import outward as OW
from . import registry as REG
from . import socialstore as SS
from . import socialui as SX
from . import switches as SW

LIMIT = 400                          # items a page shows per account (newest first)
SAVED_LABEL, SAVED_TEXT = 40, 2000
CHAR_LIMIT = dict(OW.CHAR_LIMIT)     # a comment's characters per platform
MESSAGE_BYTES = OW.MESSAGE_BYTES     # a private reply or a message
DM_HOURS = 23                        # outward.DM_WINDOW: Meta allows 24
PERSON_PRIVATE_DAYS = 7              # one private reply per person a week (architecture section 5)
USED = ("queued", "sending", "done")  # a private reply that may really have gone (practice never did)
PLAT_WORDS = E.PLAT_WORDS
TAG_ORDER = ("asks_source", "asks_full", "asks_who", "asks_when", "asks_which", "asks_part2")


def _now(now: Optional[datetime] = None) -> datetime:
    return E._now(now)


def _iso(t: datetime) -> str:
    return E._iso(t)


# ------------------------------------------------------------------------------------------------------ the data
def _clip_link(post: dict, slug: str) -> str:
    if not post.get("board") or not post.get("slot"):
        return ""
    hhmm = str(post["slot"]).replace(":", "")
    return f"/?date={post['board']}&acct={slug}#c-{slug}-{hhmm}"


def _cover(idx: Dict[str, dict], pid: str) -> str:
    p = idx.get(str(pid)) or {}
    cover = p.get("cover")
    return SX.file_url(str(cover)) if cover else ""


def _private_times(items) -> Dict[str, datetime]:
    """{person key: when the last private reply to them was queued or went} over these items."""
    out: Dict[str, datetime] = {}
    for it in items or []:
        if not isinstance(it, dict):
            continue
        key = str((it.get("author") or {}).get("key") or "")
        for r in it.get("replies") or []:
            if isinstance(r, dict) and r.get("kind") == "reply_private" and r.get("state") in USED:
                t = E._when(r.get("at"))
                if key and t is not None and (key not in out or t > out[key]):
                    out[key] = t
    return out


def _queued_private(cfg: C.Config, a: C.Account, now: datetime) -> Dict[str, datetime]:
    """{person key: when} from the sender itself: private replies that went for real in the last week (the log) and
    those waiting in the queue, his clicks and the rules' alike. A rule's private reply held by quiet hours is in the
    queue but not yet in its item's replies (those are written once it lands), so item replies alone miss it. The
    same source rules._private_log reads, so a click and a rule each see the other's."""
    out: Dict[str, datetime] = {}

    def put(to: str, t: Optional[datetime]) -> None:
        if to and t is not None and (to not in out or t > out[to]):
            out[to] = t
    since = now - timedelta(days=PERSON_PRIVATE_DAYS)
    try:
        for row in OW.rows(cfg, a.slug, since=since, kinds=("reply_private",), states=("done",)):
            if row.get("mode") != "practice":
                put(str((row.get("target") or {}).get("to") or ""), E._when(row.get("t")))
        for q in OW._items(a.slug).values():
            if q.get("kind") == "reply_private" and q.get("state") in ("queued", "sending") and \
                    not q.get("force_practice"):
                put(str((q.get("target") or {}).get("to") or ""), E._when(q.get("at")) or now)
    except Exception as e:  # noqa: BLE001
        REG._log_error("engagepage:queued_private", e)
    return out


def _merge_times(*maps: Dict[str, datetime]) -> Dict[str, datetime]:
    out: Dict[str, datetime] = {}
    for m in maps:
        for k, t in m.items():
            if k not in out or t > out[k]:
                out[k] = t
    return out


def _private(it: dict, now: datetime, last: Optional[Dict[str, datetime]] = None,
             cfg: Optional[C.Config] = None) -> Tuple[bool, str]:
    """May this comment get its one private reply now? (yes, why not) `last` is _private_times over the account's
    last week: one private reply per person a week."""
    if it.get("platform") not in E.META:
        return False, "YouTube has no private replies"
    if it.get("kind") != "comment":
        return False, "a private reply answers a top-level comment"
    if it.get("can_reply_privately") is False:
        return False, "Facebook says this comment cannot get a private reply"
    t = E._when(it.get("at"))
    if t is not None and now - t > timedelta(days=E.PRIVATE_DAYS):
        return False, f"a private reply only within {E.PRIVATE_DAYS} days of the comment"
    if any(isinstance(r, dict) and r.get("kind") == "reply_private" and r.get("state") in USED
           for r in it.get("replies") or []):
        return False, "this comment had its one private reply"
    key = str((it.get("author") or {}).get("key") or "")
    t = (last or {}).get(key)
    if t is not None and now - t < timedelta(days=PERSON_PRIVATE_DAYS):
        nxt = t + timedelta(days=PERSON_PRIVATE_DAYS)
        when = E._hhmm(cfg, nxt) if cfg is not None else E._iso(nxt)
        return False, f"one private reply per person a week: the next to them can go {when}"
    return True, ""


def _reply_why(it: dict) -> str:
    if it.get("state") == "gone":
        return f"deleted on {PLAT_WORDS.get(it.get('platform'), 'the platform')}"
    if (it.get("author") or {}).get("is_us"):
        return "your own comment"
    if it.get("platform") == "instagram" and it.get("hidden"):
        return "Instagram takes no reply to a hidden comment: unhide it first"
    if it.get("platform") == "youtube" and it.get("can_reply") is False:
        return "YouTube does not allow replies here"
    return ""


class _People:
    """The account's contacts and testers, read once per page build (not once per commenter)."""

    def __init__(self, a: C.Account):
        got = (SS.read(a, "contacts", {}) or {}).get("contacts")
        self.contacts = got if isinstance(got, dict) else {}
        try:
            got_t = SW.settings(a)["testers"]
            self.testers = {(str(t.get("platform")), str(t.get("id"))) for t in got_t  # type: ignore
                            if isinstance(t, dict)}
        except Exception:  # noqa: BLE001
            self.testers = set()
        self.seen: Dict[str, dict] = {}

    def of(self, author: dict) -> dict:
        key = str(author.get("key") or "")
        if key not in self.seen:
            plat, _, pid = key.partition(":")
            c = self.contacts.get(key)
            self.seen[key] = {"key": key, "id": str(author.get("id") or ""),
                              "name": str(author.get("name") or "") or "Someone",
                              "initials": E.initials(str(author.get("name") or "")), "is_us": bool(author.get("is_us")),
                              "tester": bool(pid) and (plat, pid) in self.testers,
                              "stop": bool(key) and isinstance(c, dict) and bool(c.get("stop"))}
        return self.seen[key]


def _replies(it: dict) -> List[dict]:
    out = []
    for r in it.get("replies") or []:
        if not isinstance(r, dict):
            continue
        out.append({"action": r.get("action") or r.get("id"), "text": str(r.get("text") or ""),
                    "state": str(r.get("state") or ""), "at": r.get("at"), "why": str(r.get("why") or ""),
                    "kind": str(r.get("kind") or "reply_public"), "private": bool(r.get("private")),
                    "by": str(r.get("by") or "")})
    return out


def _item_view(cfg: C.Config, a: C.Account, it: dict, idx: Dict[str, dict], now: datetime, people: _People,
               last: Dict[str, datetime]) -> dict:
    post = it.get("post") if isinstance(it.get("post"), dict) else {}
    author = it.get("author") if isinstance(it.get("author"), dict) else {}
    ok, why = _private(it, now, last, cfg)
    plat = str(it.get("platform") or "")
    return {"id": it.get("id"), "acct": a.slug, "acct_name": a.name, "platform": plat, "kind": it.get("kind"),
            "parent": SS.item_id(plat, str(it["parent_id"])) if it.get("parent_id") and plat in SS.PREFIX else "",
            "author": people.of(author), "text": str(it.get("text") or ""), "at": it.get("at"),
            "tags": [t for t in it.get("tags") or [] if isinstance(t, str)],
            "suggest": it.get("suggest") if isinstance(it.get("suggest"), dict) else {},
            "state": it.get("state") or "new", "hidden": bool(it.get("hidden")),
            "likes": int(it.get("like_count") or 0),
            "post": {"title": str(post.get("title") or ""), "url": str(post.get("url") or ""),
                     "clip": _clip_link(post, a.slug), "cover": _cover(idx, str(post.get("platform_id") or "")),
                     "id": str(post.get("platform_id") or "")},
            "replies": _replies(it), "private_ok": ok, "private_why": why, "reply_why": _reply_why(it),
            "limit": CHAR_LIMIT.get(plat, 2200), "suggested_reply": it.get("suggested_reply") or None}


def _thread_view(a: C.Account, t: dict, people: _People) -> dict:
    w = t.get("with") if isinstance(t.get("with"), dict) else {}
    msgs = [{"id": m.get("id"), "at": m.get("at"), "us": bool(m.get("from_us")), "text": str(m.get("text") or "")}
            for m in t.get("messages") or [] if isinstance(m, dict)]
    pend = [{"action": p.get("action"), "text": str(p.get("text") or ""), "state": str(p.get("state") or ""),
             "at": p.get("at"), "why": str(p.get("why") or "")} for p in t.get("pending") or [] if isinstance(p, dict)]
    return {"id": t.get("id"), "acct": a.slug, "acct_name": a.name, "platform": t.get("platform"),
            "with": people.of(w), "updated": t.get("updated"), "last_in_at": t.get("last_in_at"),
            "window_until": t.get("window_until"), "messages": msgs, "unread": int(t.get("unread") or 0),
            "state": t.get("state") or "new", "pending": pend}


def _acct_view(cfg: C.Config, a: C.Account) -> dict:
    st = SW.settings(a)
    plats = [p for p in (st.get("engage.platforms") or []) if p in E.PLATS]  # type: ignore
    waits = [{"platform": plat, "what": what, "bad": failed, "words": words[:200]}
             for plat, what, words, failed in E.notes(a)]
    last = E.last_read(a)
    return {"slug": a.slug, "name": a.name, "maker": str(getattr(a, "maker", "") or ""),
            "read": SW.mode(a, "engage.read"), "messages": SW.mode(a, "engage.messages"),
            "replies": SW.mode(a, "replies"), "plats": plats, "last_read": _iso(last) if last else None,
            "access": str(st.get("messages_access") or "standard"), "waits": waits}


def data(cfg: C.Config, q: Optional[dict] = None, now: Optional[datetime] = None) -> dict:
    """The Inbox page's JSON: the accounts and their switches, the comments (newest first, with their post and clip),
    the conversations, the saved replies. Files only; ends with "stamp"."""
    q = q if isinstance(q, dict) else {}
    now = _now(now or cfg.now())
    accts = [a for a in SX.own_accounts(cfg, str(q.get("acct") or "all")) if a.slug in cfg.accounts]
    out_accts, items, threads, saved = [], [], [], {}
    for a in accts:
        out_accts.append(_acct_view(cfg, a))
        saved[a.slug] = [{"id": s.get("id"), "label": str(s.get("label") or ""), "text": str(s.get("text") or ""),
                          "platforms": [p for p in s.get("platforms") or [] if p in E.PLATS]} for s in SS.saved(a)]
        if not SS.root(a).is_dir():
            continue
        keep = int(SW.settings(a)["engage.keep_days"])  # type: ignore
        idx = SS.posts_index(a)
        people = _People(a)
        got = SS.items(a, since=now - timedelta(days=keep))
        last = _merge_times(_private_times(it for it in got
                                           if (E._when(it.get("at")) or now) >= now - timedelta(days=14)),
                            _queued_private(cfg, a, now))
        items += [_item_view(cfg, a, it, idx, now, people, last) for it in got if it.get("kind") != "dm"][:LIMIT]
        threads += [_thread_view(a, t, people) for t in SS.threads(a)][:LIMIT]
    items.sort(key=lambda i: str(i.get("at") or ""), reverse=True)
    threads.sort(key=lambda t: str(t.get("updated") or ""), reverse=True)
    body = {"accounts": out_accts, "items": items, "threads": threads, "saved": saved,
            "suggest": REG.present("answers"), "rules": REG.present("rulespage")}
    return SX.stamp(body)


def get(cfg: C.Config, sub: str, q: dict) -> Tuple[int, str, bytes]:
    """/inbox/suggest?acct=&item=: an answer written from the facts on disk (answers.suggest), never a platform."""
    if sub != "suggest":
        return 404, "text/plain; charset=utf-8", b"not found"
    body: dict
    try:
        a = E._account(cfg, str(q.get("acct") or ""))
        it = SS.get_item(a, str(q.get("item") or ""))
        f = REG.fn("answers:suggest")
        if it is None:
            body = {"ok": False, "message": "That comment is not in the Inbox any more."}
        elif f is None:
            body = {"ok": False, "message": "Suggestions come with the automations part, which is not built here yet."}
        else:
            got = f(cfg, a, it)
            if isinstance(got, dict) and str(got.get("text") or "").strip():
                body = {"ok": True, "text": str(got["text"]), "why": str(got.get("why") or "")}
            else:
                body = {"ok": False, "message": "No suggestion: the facts it needs are not on disk for this clip."}
    except SystemExit as e:
        body = {"ok": False, "message": str(e)}
    except Exception as e:  # noqa: BLE001  (a suggestion never breaks the page)
        body = {"ok": False, "message": f"No suggestion ({type(e).__name__})."}
    return 200, "application/json; charset=utf-8", json.dumps(body).encode()


# ------------------------------------------------------------------------------------------------------ the clicks
def _by(cfg: C.Config, body: dict) -> str:
    return str(body.get("by") or (cfg.approvers[0] if cfg.approvers else "") or "Andrew")[:60]


def _acct(cfg: C.Config, body: dict) -> C.Account:
    slug = str(body.get("account") or "")
    a = E._account(cfg, slug)
    if a.slug not in {x.slug for x in SX.own_accounts(cfg, "all")}:
        raise SystemExit(f"{a.name}: not an account you sign")
    return a


def _item(a: C.Account, body: dict) -> dict:
    iid = str(body.get("item") or "")
    it = SS.get_item(a, iid) if iid else None
    if it is None:
        raise SystemExit("That comment is not in the Inbox any more (it may have been pruned or forgotten). Reload.")
    return it


def _thread(a: C.Account, body: dict) -> dict:
    tid = str(body.get("thread") or "")
    t = SS.thread(a, tid) if tid else None
    if t is None:
        raise SystemExit("That conversation is not in the Inbox any more. Reload.")
    return dict(t, id=tid)


def _text(body: dict) -> str:
    text = str(body.get("text") or "").strip()
    if not text:
        raise SystemExit("Write the words first.")
    if any(d in text for d in OW.DASHES):
        raise SystemExit("The words have a long dash: use a colon or a comma instead.")
    return text


def _refuse(v, a: C.Account, plat: str) -> None:
    """outward.check said no: the same in words that say what to do next."""
    if v.ok:
        return
    why = str(v.why or "").strip()
    if v.code == "off":
        raise SystemExit(f"Replies are off for {a.name}: switch them to practice or on in Log, Switches "
                         f"(factory.py social on {a.slug} replies --practice).")
    if v.code in ("cap", "repeat"):
        raise SystemExit(f"Slow down: {why}.")
    if v.code == "stop":
        raise SystemExit(f"{why} Start again on the Log tab first.")
    if v.code == "perm":
        fix = (_yt_connect(a, "comments") if plat == "youtube" else
               f"tick it on the token (SETUP-POSTING), then factory.py connect meta {a.slug}")
        raise SystemExit(f"{PLAT_WORDS.get(plat, plat)}: {why} ({fix}).")
    raise SystemExit((why[:1].upper() + why[1:]).rstrip(".") + ".")


def _yt_connect(a: C.Account, *add: str) -> str:
    """The connect youtube line with the extras the account already has as well (one --with, nothing lost)."""
    f = REG.fn("capabilities:yt_connect_line")
    try:
        if f is not None:
            return str(f(a, *add))
    except Exception:  # noqa: BLE001  (a fix line never fails the answer)
        pass
    return f"factory.py connect youtube {a.slug} --client client.json --with {','.join(add)}"


def _said(cfg: C.Config, a: C.Account, plat: str, v, what: str, name: str = "") -> str:
    """What the page says after a send was queued: in practice, the step that would make it real."""
    where = PLAT_WORDS.get(plat, plat)
    if v.mode == "practice":
        head = f"Practice: nothing goes to {where}; the Log keeps the exact words"
        if SW.mode(a, "replies") == "practice":
            note = f" ({v.why})" if v.why else ""
            return f"{head}{note}. Switch replies on for {a.name} to send for real."
        if "testers" in str(v.why or ""):
            who = name or "them"
            return (f"{head}. Messages reach only app testers until Meta's review: if {who} is you, mark them a "
                    f"tester (This is me), or wait for Advanced Access.")
        why = str(v.why or "").strip().rstrip(".")
        return f"{head}" + (f" ({why})." if why else ".")
    if v.not_before is not None and v.not_before > _now():
        return f"Queued: {what} goes to {where} at {E._hhmm(cfg, v.not_before)} ({v.why})."
    return f"Sending {what} to {where} now."


def _kick(cfg: C.Config) -> None:
    from . import social as SOC
    try:
        SOC.kick(cfg)
    except Exception as e:  # noqa: BLE001  (the 5-minute tick sends it anyway)
        REG._log_error("social:kick", e)


def _add_ours(a: C.Account, it: dict, entry: dict) -> None:
    """Our click's entry on the item, written under the item's lock: when the sender already wrote what happened to
    this action (it went between the queue and this line), its state and why stay; only the words and who are added.
    Never a "queued" over a "done"."""
    t = E._when(it.get("at"))
    month = t.strftime("%Y-%m") if t is not None else ""
    if month and (SS.root(a) / f"items-{month}.json").is_file():
        with SS.edit(a, f"items-{month}") as d:
            have = d.get("items") if isinstance(d.get("items"), dict) else {}
            cur = have.get(str(it["id"]))
            if isinstance(cur, dict):
                rs = cur["replies"] = [r for r in cur.get("replies") or [] if isinstance(r, dict)]
                for r in rs:
                    if r.get("action") == entry.get("action"):
                        r.update({k: v for k, v in entry.items() if k not in ("state", "why", "at")})
                        break
                else:
                    rs.append(dict(entry))
                return
    SS.add_reply(a, it["id"], entry)


def _recent_private(cfg: C.Config, a: C.Account, now: datetime) -> Dict[str, datetime]:
    return _merge_times(_private_times(SS.items(a, since=now - timedelta(days=PERSON_PRIVATE_DAYS + 7))),
                        _queued_private(cfg, a, now))


def _n(entries, kinds) -> int:
    return 1 + sum(1 for r in entries or [] if isinstance(r, dict) and r.get("kind") in kinds)


def _reply(cfg: C.Config, body: dict, now: datetime) -> Tuple[bool, str]:
    a = _acct(cfg, body)
    who = _by(cfg, body)
    it = _item(a, body)
    text = _text(body)
    private = bool(body.get("private"))
    force = bool(body.get("force"))
    plat = str(it.get("platform") or "")
    kind = "reply_private" if private else "reply_public"
    if private:
        ok, why = _private(it, now, _recent_private(cfg, a, now), cfg)
        if not ok:
            raise SystemExit(why[:1].upper() + why[1:] + ".")
        if len(text.encode("utf-8")) > MESSAGE_BYTES:
            raise SystemExit(f"A private reply is {MESSAGE_BYTES} bytes at the most.")
    else:
        why = _reply_why(it)
        if why:
            raise SystemExit(why[:1].upper() + why[1:] + ".")
        if len(text) > CHAR_LIMIT.get(plat, 2200):
            raise SystemExit(f"A {PLAT_WORDS.get(plat, plat)} comment is {CHAR_LIMIT.get(plat, 2200)} characters "
                             f"at the most.")
    post = it.get("post") if isinstance(it.get("post"), dict) else {}
    author = it.get("author") if isinstance(it.get("author"), dict) else {}
    own = str(it.get("platform_comment_id") or "")
    cid = str(it.get("parent_id") or own) if it.get("kind") == "reply" else own
    intent = next((t for t in TAG_ORDER if t in (it.get("tags") or [])), "")
    target = {"comment_id": cid, "post_id": str(post.get("platform_id") or ""), "item": it["id"],
              "to": str(author.get("key") or ""), "to_id": str(author.get("id") or ""), "at": it.get("at"),
              "intent": intent, "board": post.get("board"), "slot": post.get("slot"), "post_url": post.get("url")}
    signed = {"what": "click", "by": who, "at": _iso(now), "text_sha": E._sha(text)}
    origin = {"by": who, "via": "page", "rule": None}
    v = OW.check(cfg, a, plat, kind, origin=origin, text=text, target=target, manual=True, force=force, signed=signed,
                 now=now)
    _refuse(v, a, plat)
    key = f"pr:{plat}:{cid}" if private else f"rpm:{plat}:{own}:{_n(it.get('replies'), ('reply_public',))}"
    try:
        aid = OW.queue(cfg, a, plat, kind, key=key, text=text, target=target, origin=origin, signed=signed,
                       force=force, now=now)
    except SystemExit as e:
        if private:
            raise SystemExit("This comment had its one private reply: Meta allows only one.") from e
        raise
    _add_ours(a, it, {"id": aid, "action": aid, "by": who, "text": text, "at": _iso(now), "kind": kind,
                      "private": private, "state": "queued", "why": str(v.why or "")})
    _kick(cfg)
    return True, _said(cfg, a, plat, v, "your private reply" if private else "your reply",
                       str(author.get("name") or ""))


def _dm(cfg: C.Config, body: dict, now: datetime) -> Tuple[bool, str]:
    a = _acct(cfg, body)
    who = _by(cfg, body)
    t = _thread(a, body)
    text = _text(body)
    force = bool(body.get("force"))
    plat = str(t.get("platform") or "")
    if len(text.encode("utf-8")) > MESSAGE_BYTES:
        raise SystemExit(f"A message is {MESSAGE_BYTES} bytes at the most.")
    last = E._when(t.get("last_in_at"))
    if last is None or now - last > timedelta(hours=DM_HOURS):
        raise SystemExit(f"You can answer only within {DM_HOURS} hours of their last message: this one waits until "
                         f"they write again.")
    w = t.get("with") if isinstance(t.get("with"), dict) else {}
    target = {"thread_id": str(t.get("platform_thread_id") or ""), "item": t["id"], "thread": t["id"],
              "to": str(w.get("key") or ""), "to_id": str(w.get("id") or ""), "last_in_at": t.get("last_in_at")}
    signed = {"what": "click", "by": who, "at": _iso(now), "text_sha": E._sha(text)}
    origin = {"by": who, "via": "page", "rule": None}
    v = OW.check(cfg, a, plat, "dm", origin=origin, text=text, target=target, manual=True, force=force, signed=signed,
                 now=now)
    _refuse(v, a, plat)
    key = f"dmm:{plat}:{target['thread_id'] or t['id']}:{int(now.timestamp())}"      # each click its own
    aid = OW.queue(cfg, a, plat, "dm", key=key, text=text, target=target, origin=origin, signed=signed, force=force,
                   now=now)
    mine = {"action": aid, "kind": "dm", "by": who, "text": text, "at": _iso(now), "state": "queued",
            "why": str(v.why or "")}
    with SS.edit(a, "threads") as d:
        th = (d.get("threads") or {}).get(t["id"])
        if isinstance(th, dict):
            pend = [p for p in th.get("pending") or [] if isinstance(p, dict)]
            for p in pend:
                if p.get("action") == aid:         # the sender wrote first: keep what happened, say it was a click
                    p.update({k: v2 for k, v2 in mine.items() if k not in ("state", "why", "at")})
                    break
            else:
                pend.append(mine)
            th["pending"] = pend[-10:]
    _kick(cfg)
    return True, _said(cfg, a, plat, v, "your message", str(w.get("name") or ""))


def _hide(cfg: C.Config, body: dict, now: datetime, on: bool) -> Tuple[bool, str]:
    a = _acct(cfg, body)
    who = _by(cfg, body)
    it = _item(a, body)
    plat = str(it.get("platform") or "")
    if it.get("state") == "gone":
        raise SystemExit(f"That comment was deleted on {PLAT_WORDS.get(plat, plat)}.")
    if (it.get("author") or {}).get("is_us"):
        raise SystemExit("That is your own comment: it always shows.")
    cid = str(it.get("platform_comment_id") or "")
    if plat == "youtube":
        kind, target = "yt_moderate", {"status": "heldForReview" if on else "published"}
    else:
        kind, target = ("hide" if on else "unhide"), {}
    target.update({"comment_id": cid, "item": it["id"], "to": str((it.get("author") or {}).get("key") or ""),
                   "post_id": str((it.get("post") or {}).get("platform_id") or "")})
    signed = {"what": "click", "by": who, "at": _iso(now), "text_sha": ""}
    origin = {"by": who, "via": "page", "rule": None}
    v = OW.check(cfg, a, plat, kind, origin=origin, target=target, manual=True, force=bool(body.get("force")),
                 signed=signed, now=now)
    _refuse(v, a, plat)
    n = _n(it.get("replies"), ("hide", "unhide", "yt_moderate"))
    aid = OW.queue(cfg, a, plat, kind, key=f"hide:{plat}:{cid}:{'on' if on else 'off'}:{n}", text="", target=target,
                   origin=origin, signed=signed, force=bool(body.get("force")), now=now)
    _add_ours(a, it, {"id": aid, "action": aid, "by": who, "text": "", "at": _iso(now), "kind": kind,
                      "hide": on, "state": "queued", "why": str(v.why or "")})
    _kick(cfg)
    what = ("holding the comment for review" if on else "publishing the comment again") if plat == "youtube" else \
        ("hiding the comment" if on else "showing the comment again")
    return True, _said(cfg, a, plat, v, what)


def _like(cfg: C.Config, body: dict, now: datetime) -> Tuple[bool, str]:
    a = _acct(cfg, body)
    who = _by(cfg, body)
    it = _item(a, body)
    plat = str(it.get("platform") or "")
    if plat != "facebook":
        raise SystemExit(f"{PLAT_WORDS.get(plat, plat)} does not let an app like a comment: like it in its own app.")
    if it.get("state") == "gone":
        raise SystemExit("That comment was deleted on Facebook.")
    if (it.get("author") or {}).get("is_us"):
        raise SystemExit("That is your own comment.")
    if any(r.get("kind") == "like" and r.get("state") != "failed" for r in (it.get("replies") or [])):
        return True, "Already liked."
    cid = str(it.get("platform_comment_id") or "")
    target = {"comment_id": cid, "item": it["id"], "to": str((it.get("author") or {}).get("key") or ""),
              "post_id": str((it.get("post") or {}).get("platform_id") or "")}
    signed = {"what": "click", "by": who, "at": _iso(now), "text_sha": ""}
    origin = {"by": who, "via": "page", "rule": None}
    v = OW.check(cfg, a, plat, "like", origin=origin, target=target, manual=True, force=bool(body.get("force")),
                 signed=signed, now=now)
    _refuse(v, a, plat)
    aid = OW.queue(cfg, a, plat, "like", key=f"like:{plat}:{cid}", text="", target=target,
                   origin=origin, signed=signed, force=bool(body.get("force")), now=now)
    _add_ours(a, it, {"id": aid, "action": aid, "by": who, "text": "", "at": _iso(now), "kind": "like",
                      "state": "queued", "why": str(v.why or "")})
    _kick(cfg)
    return True, _said(cfg, a, plat, v, "liking the comment")


def _done(cfg: C.Config, body: dict, now: datetime, on: bool) -> Tuple[bool, str]:
    a = _acct(cfg, body)
    who = _by(cfg, body)
    if body.get("thread"):
        t = _thread(a, body)
        SS.set_thread_state(a, t["id"], "done" if on else "new", who, now)
        return True, "Marked done." if on else "Back in the list."
    it = _item(a, body)
    if on:
        SS.set_state(a, it["id"], "done", who, now)
        return True, "Marked done."
    back = "hidden" if it.get("hidden") else ("answered" if E._sent(it) or it.get("answered") else "new")
    SS.set_state(a, it["id"], back, who, now)
    return True, "Back in the list."


def _person_key(a: C.Account, body: dict) -> Tuple[str, str]:
    """(person key, name) from the body's person, item or thread."""
    if body.get("item"):
        w = _item(a, body).get("author") or {}
    elif body.get("thread"):
        w = _thread(a, body).get("with") or {}
    else:
        w = {"key": str(body.get("person") or ""), "name": ""}
    key = str(w.get("key") or body.get("person") or "")
    if ":" not in key or key.endswith(":"):
        raise SystemExit("Which person? (platform:id)")
    return key, str(w.get("name") or key)


def _stop(cfg: C.Config, body: dict, now: datetime) -> Tuple[bool, str]:
    a = _acct(cfg, body)
    who = _by(cfg, body)
    key, name = _person_key(a, body)
    undo = bool(body.get("undo"))
    SS.stop_contact(a, key, now, why=f"marked by {who} on the Inbox", on=not undo)
    if undo:
        return True, f"Automations may answer {name} again ({a.name})."
    return True, f"Nothing automated goes to {name} again ({a.name}). Your own replies still can."


def _forget(cfg: C.Config, body: dict, now: datetime) -> Tuple[bool, str]:
    a = _acct(cfg, body)
    who = _by(cfg, body)
    key, name = _person_key(a, body)
    n = SS.forget(a, key, who, cfg)
    return True, f"Forgotten: {name} ({n} records removed or blanked in {a.name})."


def _tester(cfg: C.Config, body: dict, now: datetime) -> Tuple[bool, str]:
    a = _acct(cfg, body)
    who = _by(cfg, body)
    key, name = _person_key(a, body)
    plat, _, pid = key.partition(":")
    if plat not in E.PLATS or not pid or pid.startswith(("@", "anon-")):
        raise SystemExit("The platform did not give this person's id: a tester needs it.")
    return True, SW.add_tester(cfg, a, plat, pid, name, who, now) + ". Testers mode answers them for real."


def _check(cfg: C.Config, body: dict, now: datetime) -> Tuple[bool, str]:
    return True, E.kick_check(cfg, now)


def _saved_set(cfg: C.Config, body: dict, now: datetime) -> Tuple[bool, str]:
    a = _acct(cfg, body)
    label = str(body.get("label") or "").strip()
    text = str(body.get("text") or "").strip()
    if not label or not text:
        raise SystemExit("A saved reply needs a name and its words.")
    if len(label) > SAVED_LABEL or len(text) > SAVED_TEXT:
        raise SystemExit(f"A saved reply's name is {SAVED_LABEL} characters at the most, its words {SAVED_TEXT}.")
    if any(d in label + text for d in OW.DASHES):
        raise SystemExit("The words have a long dash: use a colon or a comma instead.")
    plats = body.get("platforms")
    if plats is not None and (not isinstance(plats, list) or not all(p in E.PLATS for p in plats)):
        raise SystemExit("platforms: instagram, facebook, youtube")
    sid = SS.saved_put(a, label, text, plats or list(E.PLATS), str(body.get("id") or ""), now)
    return True, f"Saved for {a.name}: {label}." if sid else "Saved."


def _saved_delete(cfg: C.Config, body: dict, now: datetime) -> Tuple[bool, str]:
    a = _acct(cfg, body)
    if not SS.saved_delete(a, str(body.get("id") or "")):
        raise SystemExit("That saved reply is not there any more. Reload.")
    return True, "Deleted."


OPS = {"inbox-reply": (_reply, "reply as the account"), "inbox-dm": (_dm, "send a message as the account"),
       "inbox-hide": (lambda c, b, n: _hide(c, b, n, True), "hide a comment"),
       "inbox-unhide": (lambda c, b, n: _hide(c, b, n, False), "show a comment again"),
       "inbox-like": (_like, "like a comment"),
       "inbox-done": (lambda c, b, n: _done(c, b, n, True), "mark it done"),
       "inbox-undone": (lambda c, b, n: _done(c, b, n, False), "put it back in the list"),
       "inbox-stop": (_stop, "stop automations to a person"), "inbox-forget": (_forget, "forget a person"),
       "inbox-tester": (_tester, "add a tester"), "inbox-check": (_check, "read the Inbox now"),
       "saved-set": (_saved_set, "save a reply"), "saved-delete": (_saved_delete, "delete a saved reply")}


def act(cfg: C.Config, body: dict, page: bool = False, now: Optional[datetime] = None) -> Tuple[bool, str]:
    """One click on the Inbox (OPS). Every op needs the page itself (review._from_page): no script holding the token
    can reply, message, hide or forget."""
    op = str(body.get("op") or "")
    got = OPS.get(op)
    if got is None:
        raise SystemExit(f"{op or 'that'}: not a click the Inbox knows")
    fn, what = got
    SX.need_page(page, what)
    return fn(cfg, body, _now(now or cfg.now()))


# ------------------------------------------------------------------------------------------------------ the page
CSS = r"""
.ib{display:flex;flex-direction:column;gap:12px;min-width:0}
.ib-head{display:flex;align-items:flex-start;gap:10px;flex-wrap:wrap}
.ib-status{display:flex;flex-direction:column;gap:3px;flex:1 1 280px;min-width:0;font-size:13px;color:var(--sub)}
.ib-status b{color:var(--ink);font-weight:600}
.ib-status .bad{color:var(--bad)}
.ib-status .warn{color:var(--warn)}
.ib-bar{display:flex;align-items:center;gap:10px;flex-wrap:wrap}
.ib-bar .seg{max-width:100%}
.ib-bar .seg button span{color:var(--sub);font-weight:500;margin-left:5px}
.ib-split{grid-template-columns:minmax(240px,min(420px,40%)) minmax(0,1fr)}
.ib-list{max-height:calc(100vh - 220px);overflow-y:auto}
.ib-row{cursor:pointer}
.ib-av{width:32px;height:32px;border-radius:6px;background:var(--mute);color:var(--sub);display:inline-flex;align-items:center;justify-content:center;font-size:12px;font-weight:650;flex:0 0 auto;letter-spacing:.02em}
.ib-av.lg{width:40px;height:40px;font-size:14px}
.ib-rb{display:flex;flex-direction:column;gap:3px;min-width:0;flex:1}
.ib-r1{display:flex;gap:6px;align-items:baseline;flex-wrap:wrap;font-size:12.5px;color:var(--sub);min-width:0}
.ib-r1 b{color:var(--ink);font-size:13.5px;font-weight:600;overflow-wrap:anywhere}
.ib-txt{font-size:13.5px;line-height:1.4;overflow-wrap:anywhere;display:-webkit-box;-webkit-line-clamp:3;-webkit-box-orient:vertical;overflow:hidden}
.ib-r3{display:flex;gap:5px;flex-wrap:wrap;align-items:center;font-size:12px;color:var(--sub);min-width:0}
.ib-r3 .ib-post{overflow:hidden;text-overflow:ellipsis;white-space:nowrap;max-width:100%}
.ib-tag{font-size:11.5px;font-weight:600;padding:1px 7px;border-radius:4px;line-height:18px;background:var(--mute);color:var(--sub);max-width:100%;overflow-wrap:anywhere}
.ib-tag.ask{background:var(--bluebg);color:var(--blue)}
.ib-tag.warn{background:var(--warnbg);color:var(--warn)}
.ib-tag.bad{background:var(--badbg);color:var(--bad)}
.ib-tag.ok{background:var(--okbg);color:var(--ok)}
.ib-unread{min-width:20px;height:20px;border-radius:6px;background:var(--blue);color:#fff;font-size:11.5px;font-weight:650;display:inline-flex;align-items:center;justify-content:center;padding:0 5px}
.ib-dh{display:flex;gap:10px;align-items:flex-start;min-width:0}
.ib-dh .ib-rb{gap:2px}
.ib-full{font-size:14.5px;line-height:1.5;white-space:pre-wrap;overflow-wrap:anywhere}
.ib-note{font-size:12.5px;color:var(--sub)}
.ib-note.warn{color:var(--warn)}
.ib-note.bad{color:var(--bad)}
.ib-postbox{display:flex;gap:10px;align-items:flex-start;padding:10px;border:1px solid var(--line);border-radius:6px;background:var(--bg);min-width:0}
.ib-postbox img{width:54px;height:96px;object-fit:cover;border-radius:4px;background:var(--mute);flex:0 0 auto}
.ib-postbox .ib-rb a{color:var(--blue);text-decoration:none;font-size:13px}
.ib-postbox .ib-rb a:hover{text-decoration:underline}
.ib-links{display:flex;gap:12px;flex-wrap:wrap}
.ib-thr{display:flex;flex-direction:column;gap:8px;border-left:2px solid var(--line);padding-left:10px}
.ib-sub{display:flex;flex-direction:column;gap:2px;font-size:13px;min-width:0}
.ib-sub .ib-r1{font-size:12px}
.ib-sub .ib-full{font-size:13.5px}
.ib-comp{display:flex;flex-direction:column;gap:8px}
.ib-comp textarea{font:inherit;font-size:14px;min-height:92px;padding:8px 10px;border:1px solid var(--line);border-radius:6px;background:var(--card);color:var(--ink);width:100%;resize:vertical}
.ib-comp textarea:disabled{opacity:.55}
.ib-crow{display:flex;gap:8px;align-items:center;flex-wrap:wrap}
.ib-count{font-size:12px;color:var(--sub);margin-left:auto;font-variant-numeric:tabular-nums}
.ib-count.bad{color:var(--bad);font-weight:600}
.ib-check,.sx-form .ib-check{display:inline-flex;flex-direction:row;align-items:center;gap:7px;font-size:13px;color:var(--ink)}
.ib-check input{width:18px;height:18px}
.ib-acts{display:flex;gap:6px;flex-wrap:wrap;padding-top:4px;border-top:1px solid var(--line)}
.ib-acts button.danger{color:var(--bad)}
.ib-back{display:none}
.ib-msgs{display:flex;flex-direction:column;gap:6px}
.ib-msg{max-width:82%;padding:7px 10px;border:1px solid var(--line);border-radius:8px;background:var(--bg);font-size:13.5px;white-space:pre-wrap;overflow-wrap:anywhere;align-self:flex-start}
.ib-msg.us{align-self:flex-end;background:var(--bluebg);border-color:var(--bluebg)}
.ib-msg small{display:block;color:var(--sub);font-size:11.5px;margin-top:2px}
.ib-savedform{display:flex;flex-direction:column;gap:10px}
.ib-plats{display:flex;gap:12px;flex-wrap:wrap}
@media (max-width:600px){.ib-split{grid-template-columns:minmax(0,1fr)}
.ib-list{max-height:none;overflow:hidden}
.ib-split.ib-open .ib-list{display:none}.ib-split:not(.ib-open) .sx-detail{display:none}
.ib-back{display:inline-flex;align-items:center;align-self:flex-start}
.ib.ib-detailing .ib-head,.ib.ib-detailing .ib-bar{display:none}
.ib-check{min-height:40px}
.ib-comp textarea{font-size:16px}
.ib-check input{width:22px;height:22px}
.ib-msg{max-width:92%}
.ib-postbox .ib-rb a{display:inline-flex;align-items:center;min-height:40px}
.ib-bar .seg{flex-wrap:nowrap;overflow-x:auto;scrollbar-width:none}.ib-bar .seg::-webkit-scrollbar{display:none}
.ib-bar .seg button{flex:0 0 auto;white-space:nowrap}
.ib-tag{line-height:22px}}
"""

BODY = """<section class="ib" aria-label="Inbox">
<div class="ib-head"><div id="ib-status" class="ib-status" role="status"></div>
<button type="button" id="ib-check" title="Read the comments and messages now (at most every 2 minutes)">Check now</button></div>
<div id="ib-bar" class="ib-bar"></div>
<div id="ib-split" class="sx-split ib-split"><div id="ib-list" class="sx-list ib-list" role="list" aria-label="Comments"></div>
<div id="ib-detail" class="sx-detail ib-detail"></div></div>
</section>"""

JS = r"""
(() => {
'use strict';
const SX = window.SX, el = SX.el;
const VIEW = SX.qs('view') || 'comments';
const PW = {instagram: 'Instagram', facebook: 'Facebook', youtube: 'YouTube'};
const FILTERS = [{id: 'unanswered', l: 'Unanswered'}, {id: 'questions', l: 'Questions'}, {id: 'source', l: 'Asks for the source'},
  {id: 'hide', l: 'Suggested hide'}, {id: 'done', l: 'Done'}, {id: 'all', l: 'All'}];
const TAGW = {question: 'Question', asks_source: 'Asks for the source', asks_full: 'Asks for the full video', asks_who: 'Asks who',
  asks_when: 'Asks when', asks_which: 'Asks which', asks_part2: 'Wants part 2', praise: 'Praise', stop: 'Said stop'};
const ASKS = ['question', 'asks_source', 'asks_full', 'asks_who', 'asks_when', 'asks_which', 'asks_part2'];
const SENDING = ['queued', 'sending'];
const ST = {filter: SX.qs('filter') || 'unanswered', plats: new Set((SX.qs('plat') || '').split(',').filter(Boolean)),
  sel: SX.qs('item') || SX.qs('thread') || SX.qs('saved') || '', open: !!(SX.qs('item') || SX.qs('thread') || SX.qs('saved'))};
let D = null, poller = null, waiting = false;
const drafts = {};
const $ = id => document.getElementById(id);
const root = document.querySelector('section.ib'), list = $('ib-list'), detail = $('ib-detail'), split = $('ib-split'), bar = $('ib-bar'), status = $('ib-status');

function bytes(s) { try { return new TextEncoder().encode(s).length; } catch (e) { return s.length; } }
function acctOf(slug) { return (D.accounts || []).find(a => a.slug === slug) || {}; }
function many() { return (D.accounts || []).length > 1; }
function spans(xs) { return xs.filter(x => x != null && x !== '').map(x => typeof x === 'string' ? el('span', {}, x) : x); }
function tag(words, cls) { return el('span', {class: 'ib-tag' + (cls ? ' ' + cls : '')}, words); }
function av(p, big) { return el('span', {class: 'ib-av' + (big ? ' lg' : ''), 'aria-hidden': 'true'}, p.initials || '?'); }
function hoursLeft(iso) {
  const t = Date.parse(iso || ''); if (isNaN(t)) return -1;
  return (t + 23 * 3600e3 - Date.now()) / 3600e3;
}
function leftWords(h) {
  if (h >= 1.5) return Math.floor(h) + ' more hours';
  if (h >= 1) return 'about an hour more';
  return Math.max(1, Math.round(h * 60)) + ' more minutes';
}
function stateWords(r) {
  const w = {queued: 'Sending', sending: 'Sending', done: 'Sent', practice: 'Practice: not sent', failed: 'Failed, trying again',
    gave_up: 'Did not go', refused: 'Refused', skipped: 'Not sent', held: 'Waiting: paused by the platform'}[r.state] || r.state;
  return w + (r.why && r.state !== 'done' ? ': ' + r.why : '');
}
function stateCls(s) { return s === 'done' ? 'ok' : (SENDING.includes(s) || s === 'practice' || s === 'held') ? 'warn' : 'bad'; }

/* ---------- filters ---------- */
function asks(it) { return (it.tags || []).some(t => ASKS.includes(t)); }
function match(it, f) {
  if (it.author.is_us) return false;
  if (ST.plats.size && !ST.plats.has(it.platform)) return false;
  if (f === 'unanswered') return it.state === 'new' && !it.hidden && !(it.suggest || {}).hide;
  if (f === 'questions') return asks(it) && !['done', 'gone'].includes(it.state);
  if (f === 'source') return (it.tags || []).some(t => t === 'asks_source' || t === 'asks_full') && it.state !== 'gone';
  if (f === 'hide') return !!(it.suggest || {}).hide && !it.hidden && !['gone', 'done'].includes(it.state);
  if (f === 'done') return it.state === 'done';
  return true;
}
function setFilter(f) { ST.filter = f; SX.setq('filter', f === 'unanswered' ? '' : f); draw(); }
function togglePlat(p) { ST.plats.has(p) ? ST.plats.delete(p) : ST.plats.add(p); SX.setq('plat', [...ST.plats].join(',')); draw(); }

/* ---------- the status line ---------- */
function drawStatus() {
  const rows = [];
  for (const a of D.accounts || []) {
    const name = el('b', {}, a.name + ': ');
    if (a.read !== 'on') {
      rows.push(el('div', {}, [name, 'reading comments is off. Switch engage.read on in Log, Switches (factory.py social on ' + a.slug + ' engage.read).']));
      continue;
    }
    const bits = ['read ' + (a.last_read ? SX.ago(a.last_read) : 'not yet'), 'replies ' + a.replies];
    if (a.messages !== 'on') bits.push('messages off');
    rows.push(el('div', {}, [name, bits.join(', ') + '.']));
    for (const w of a.waits || []) rows.push(el('div', {class: w.bad ? 'bad' : 'warn'}, a.name + ' ' + PW[w.platform] + ' ' + w.what + ': ' + (w.bad ? 'the last read failed, ' : '') + w.words));
  }
  if (!rows.length) rows.push(el('div', {}, 'No account to show.'));
  status.replaceChildren(...rows);
}

/* ---------- comments ---------- */
function rowFor(it) {
  const a = it.author, r1 = [el('b', {}, a.name), PW[it.platform] || it.platform];
  if (many()) r1.push(it.acct_name);
  r1.push(SX.ago(it.at));
  const r3 = [];
  if (it.kind === 'reply') r3.push(tag('Reply'));
  if (it.state === 'gone') r3.push(tag('Deleted', 'bad'));
  else if (it.hidden) r3.push(tag('Hidden'));
  else if (it.state === 'done') r3.push(tag('Done', 'ok'));
  else if (it.state === 'answered') r3.push(tag('Answered', 'ok'));
  if ((it.suggest || {}).hide && !it.hidden) r3.push(tag('Suggested hide: ' + it.suggest.why, 'warn'));
  for (const t of it.tags || []) if (t !== 'question' || (it.tags || []).length === 1) r3.push(tag(TAGW[t] || t, ASKS.includes(t) ? 'ask' : ''));
  if ((it.replies || []).some(r => SENDING.includes(r.state))) r3.push(tag('Sending', 'warn'));
  if (it.post.title) r3.push(el('span', {class: 'ib-post'}, 'on ' + it.post.title));
  return el('button', {type: 'button', class: 'sx-row ib-row', role: 'listitem', 'aria-selected': String(ST.sel === it.id),
    onclick: () => pick(it.id)}, [av(a), el('div', {class: 'ib-rb'}, [el('div', {class: 'ib-r1'}, spans(r1)),
    el('div', {class: 'ib-txt'}, it.text || '(no words)'), r3.length ? el('div', {class: 'ib-r3'}, r3) : null])]);
}
function drawBar() {
  if (VIEW === 'saved') { bar.replaceChildren(el('button', {type: 'button', class: 'go', onclick: () => pick('new')}, 'New saved reply')); return; }
  if (VIEW === 'messages') { bar.replaceChildren(); return; }
  const seg = el('div', {class: 'seg', role: 'group', 'aria-label': 'Show'});
  const counts = {}; for (const f of FILTERS) counts[f.id] = D.items.filter(it => match(it, f.id)).length;
  seg.replaceChildren(...FILTERS.map(f => el('button', {type: 'button', 'aria-pressed': String(ST.filter === f.id), onclick: () => setFilter(f.id)},
    [f.l, el('span', {}, String(counts[f.id]))])));
  const chips = el('div', {class: 'sx-chips', role: 'group', 'aria-label': 'Platforms'});
  SX.chips(chips, ['instagram', 'facebook', 'youtube'].map(p => ({id: p, l: PW[p]})), ST.plats, togglePlat);
  bar.replaceChildren(seg, chips);
  SX.reveal(seg);
}
function pick(id) {
  ST.sel = id; ST.open = !!id;
  for (const k of ['item', 'thread', 'saved']) SX.setq(k, '');
  if (id) SX.setq(VIEW === 'messages' ? 'thread' : VIEW === 'saved' ? 'saved' : 'item', id);
  draw(true);
  if (id && window.matchMedia('(max-width:600px)').matches) window.scrollTo(0, 0);
}
function openDetail(on) { split.classList.toggle('ib-open', on); root.classList.toggle('ib-detailing', on); }
function back() { ST.open = false; openDetail(false); SX.setq('item', ''); SX.setq('thread', ''); SX.setq('saved', ''); SX.reveal(bar.querySelector('.seg')); }
function backBtn() { return el('button', {type: 'button', class: 'ib-back', onclick: back}, 'Back to the list'); }
function drawComments(full) {
  const shown = D.items.filter(it => match(it, ST.filter));
  list.setAttribute('aria-label', 'Comments');
  list.replaceChildren(...(shown.length ? shown.map(rowFor) : [el('div', {class: 'sx-empty'}, emptyWords())]));
  const it = D.items.find(x => x.id === ST.sel);
  if (full !== false) drawItem(it);
}
function emptyWords() {
  if (!(D.accounts || []).some(a => a.read === 'on')) return 'Reading comments is off. Switch engage.read on for an account in Log, Switches.';
  return {unanswered: 'Nothing waits for an answer.', questions: 'No open questions.', source: 'Nobody is asking for the source.',
    hide: 'Nothing is suggested for hiding.', done: 'Nothing is marked done.', all: 'No comments yet.'}[ST.filter] || 'Nothing here.';
}
function sub(it, words) {
  return el('div', {class: 'ib-sub'}, [el('div', {class: 'ib-r1'}, spans([el('b', {}, words || it.author.name), PW[it.platform], SX.ago(it.at)])),
    el('div', {class: 'ib-full'}, it.text)]);
}
function ours(r, it) {
  const what = r.kind === 'like' ? 'Like' : r.kind === 'hide' || r.kind === 'unhide' || r.kind === 'yt_moderate' ? (r.kind === 'unhide' ? 'Show again' : (r.kind === 'yt_moderate' ? 'Moderation' : 'Hide')) :
    (r.private ? 'Your private reply' : r.by === 'rule' ? 'Automated reply' : 'Your reply');
  return el('div', {class: 'ib-sub'}, [el('div', {class: 'ib-r1'}, spans([el('b', {}, what), r.at ? SX.ago(r.at) : '', tag(stateWords(r), stateCls(r.state))])),
    r.text ? el('div', {class: 'ib-full'}, r.text) : null]);
}
function drawItem(it) {
  openDetail(!!it && ST.open);
  if (!it) { detail.replaceChildren(el('div', {class: 'sx-empty'}, 'Pick a comment to read it whole, answer it, or hide it.')); return; }
  const a = it.author, acct = acctOf(it.acct), P = PW[it.platform] || it.platform;
  const kids = [backBtn()];
  const meta = [P, it.acct_name, SX.ago(it.at)];
  if (it.likes) meta.push(it.likes + (it.likes === 1 ? ' like' : ' likes'));
  kids.push(el('div', {class: 'ib-dh'}, [av(a, true), el('div', {class: 'ib-rb'}, [el('div', {class: 'ib-r1'}, spans([el('b', {}, a.name)].concat(meta))),
    el('div', {class: 'ib-r3'}, [a.tester ? tag('Tester', 'ok') : null, a.stop ? tag('No automations', 'warn') : null,
      it.state === 'gone' ? tag('Deleted on ' + P, 'bad') : it.hidden ? tag('Hidden') : null])])]));
  const parent = it.parent ? D.items.find(x => x.id === it.parent) : null;
  if (parent) kids.push(el('div', {class: 'ib-thr'}, [sub(parent, 'In reply to ' + parent.author.name)]));
  kids.push(el('div', {class: 'ib-full'}, it.text || '(no words)'));
  if ((it.suggest || {}).hide && !it.hidden) kids.push(el('div', {class: 'ib-note warn'}, 'Suggested: hide it (' + it.suggest.kind + ': ' + it.suggest.why + '). Only your click hides it.'));
  if ((it.tags || []).length) kids.push(el('div', {class: 'ib-r3'}, it.tags.map(t => tag(TAGW[t] || t, ASKS.includes(t) ? 'ask' : ''))));
  const p = it.post, links = [];
  if (p.url) links.push(el('a', {href: p.url, target: '_blank', rel: 'noopener noreferrer'}, 'Open on ' + P));
  if (p.clip) links.push(el('a', {href: p.clip}, 'The clip'));
  if (p.title || links.length) kids.push(el('div', {class: 'ib-postbox'}, [p.cover ? el('img', {src: p.cover, alt: '', loading: 'lazy'}) : null,
    el('div', {class: 'ib-rb'}, [el('div', {class: 'ib-note'}, 'On the post'), el('b', {}, p.title || 'A post not made by the factory'), el('div', {class: 'ib-links'}, links)])]));
  // the thread oldest first: the platform's replies, and ours; our own reply read back from the platform is the
  // same words as a Sent one here, so it shows once
  const said = (it.replies || []).filter(r => r.state === 'done' && !r.private && r.text).map(r => words(r.text));
  const kidsOf = D.items.filter(x => x.parent === it.id).filter(x => {
    if (!x.author.is_us) return true;
    const i = said.indexOf(words(x.text)); if (i < 0) return true;
    said.splice(i, 1); return false;
  });
  const thr = kidsOf.map(x => ({at: x.at, n: sub(x)})).concat((it.replies || []).map(r => ({at: r.at, n: ours(r, it)})));
  thr.sort((x, y) => (Date.parse(x.at || '') || 0) - (Date.parse(y.at || '') || 0));
  if (thr.length) kids.push(el('div', {class: 'ib-thr'}, thr.map(x => x.n)));
  kids.push(composer(it, acct));
  kids.push(actions(it, acct));
  detail.replaceChildren(...kids);
  restore('c:' + it.id);
}
function words(s) { return String(s || '').toLowerCase().split(/\s+/).filter(Boolean).join(' '); }
function savedFor(acct, plat) { return ((D.saved || {})[acct] || []).filter(s => !(s.platforms || []).length || s.platforms.includes(plat)); }
function counter(box, ta, limit, inBytes) {
  const n = inBytes ? bytes(ta.value) : ta.value.length;
  box.textContent = n + ' / ' + limit + (inBytes ? ' bytes' : '');
  box.classList.toggle('bad', n > limit);
  return n <= limit;
}
function composer(it, acct) {
  const P = PW[it.platform], why = it.reply_why;
  const ta = el('textarea', {id: 'ib-text', 'aria-label': 'Your reply', placeholder: why ? '' : 'Reply as ' + it.acct_name + ' on ' + P, maxlength: '10000'});
  const count = el('span', {class: 'ib-count', 'aria-live': 'polite'});
  const priv = (it.platform !== 'youtube') ? el('input', {type: 'checkbox', id: 'ib-private', disabled: !it.private_ok}) : null;
  if (priv && it.private_ok && drafts['p:' + it.id]) priv.checked = true;      // a redraw keeps the tick
  const send = el('button', {type: 'button', class: 'go', onclick: e => sendReply(it, e.currentTarget)}, 'Send');
  const upd = () => { drafts['c:' + it.id] = ta.value; const lim = priv && priv.checked ? 1000 : it.limit; send.disabled = !counter(count, ta, lim, priv && priv.checked) || !ta.value.trim() || !!why || acct.replies === 'off'; };
  ta.addEventListener('input', upd);
  if (priv) priv.addEventListener('change', () => { drafts['p:' + it.id] = priv.checked; upd(); });
  ta.addEventListener('blur', () => { if (pendingDetail) setTimeout(() => { if (!detail.contains(document.activeElement)) draw(); }, 50); });
  const chips = savedFor(it.acct, it.platform).map(s => el('button', {type: 'button', class: 'sx-chip', title: s.text,
    onclick: () => { ta.value = ta.value ? ta.value.replace(/\s*$/, ' ') + s.text : s.text; upd(); ta.focus(); }}, s.label));
  if (it.suggested_reply && it.suggested_reply.text) chips.unshift(el('button', {type: 'button', class: 'sx-chip', title: it.suggested_reply.why || '',
    onclick: () => { ta.value = it.suggested_reply.text; upd(); ta.focus(); }}, 'Use the suggested reply'));
  if (D.suggest) chips.push(el('button', {type: 'button', class: 'sx-chip', onclick: e => suggest(it, ta, upd, e.currentTarget)}, 'Suggest'));
  const notes = [];
  if (why) notes.push(el('div', {class: 'ib-note'}, 'No reply here: ' + why + '.'));
  else if (acct.replies === 'off') notes.push(el('div', {class: 'ib-note warn'}, 'Replies are off for ' + it.acct_name + ': switch them to practice or on in Log, Switches.'));
  else if (acct.replies === 'practice') notes.push(el('div', {class: 'ib-note warn'}, 'Replies are in practice for ' + it.acct_name + ': nothing is sent; the Log keeps the exact words.'));
  if (priv && !it.private_ok) notes.push(el('div', {class: 'ib-note'}, 'Private reply: ' + it.private_why + '.'));
  if (priv && it.private_ok && acct.access !== 'advanced' && !it.author.tester) notes.push(el('div', {class: 'ib-note'}, 'Private replies reach only app testers until Meta\'s review: this one is practice.'));
  if (it.kind === 'reply' && !ta.value && it.platform !== 'facebook') ta.value = '@' + it.author.name + ' ';
  ta.disabled = !!why;
  const row = [chips.length ? el('div', {class: 'sx-chips'}, chips) : null,
    el('div', {class: 'ib-crow'}, [priv ? el('label', {class: 'ib-check'}, [priv, 'Reply privately (one message, within 6 days)']) : null, count, send])];
  setTimeout(upd, 0);
  return el('div', {class: 'ib-comp'}, [el('label', {for: 'ib-text', class: 'ib-note'}, 'Reply')].concat([ta], row, notes));
}
async function suggest(it, ta, upd, btn) {
  btn.disabled = true;
  try {
    const r = await fetch('/inbox/suggest?acct=' + encodeURIComponent(it.acct) + '&item=' + encodeURIComponent(it.id), {cache: 'no-store'});
    const d = await r.json();
    if (!d.ok) { SX.toast(d.message || 'No suggestion.', true); return; }
    if (ta.value.trim() && !SX.ask('Replace your words with the suggestion?\n\n' + d.text)) return;
    ta.value = d.text; upd(); ta.focus();
    if (d.why) SX.toast('Suggested: ' + d.why);
  } catch (e) { SX.toast('No suggestion right now.', true); }
  finally { btn.disabled = false; }
}
async function post(body, btn) {
  let r = await SX.act(body, btn);
  if (!r.ok && /send it anyway/i.test(r.message || '') && SX.ask(r.message + '\n\nSend it anyway?')) r = await SX.act(Object.assign({}, body, {force: true}), btn);
  if (r.ok) { waiting = true; if (poller) { poller.every(5000); poller.now(); } }
  return r;
}
async function sendReply(it, btn) {
  const ta = $('ib-text'), text = (ta.value || '').trim(), priv = !!($('ib-private') || {}).checked;
  if (!text) { SX.toast('Write the reply first.', true); return; }
  const P = PW[it.platform], on = it.post.title ? ' on "' + it.post.title + '"' : '';
  const words = priv ? 'Send this private message on ' + P + ' to ' + it.author.name + ', as ' + it.acct_name + '? It is the one message Meta allows for this comment, and it cannot be taken back.\n\n' + text
    : 'Reply on ' + P + ' as ' + it.acct_name + ', under ' + it.author.name + '\'s comment' + on + '? Everyone can see it.\n\n' + text;
  if (!SX.ask(words)) return;
  const r = await post({op: 'inbox-reply', account: it.acct, item: it.id, text, private: priv}, btn);
  if (r.ok) { drafts['c:' + it.id] = ''; drafts['p:' + it.id] = false; ta.value = ''; }
}
function actions(it, acct) {
  const P = PW[it.platform], name = it.author.name, b = [];
  const yt = it.platform === 'youtube';
  b.push(el('button', {type: 'button', onclick: e => SX.act({op: it.state === 'done' ? 'inbox-undone' : 'inbox-done', account: it.acct, item: it.id}, e.currentTarget).then(() => poller.now())},
    it.state === 'done' ? 'Not done' : 'Mark done'));
  if (!it.author.is_us && it.state !== 'gone') {
    const on = !it.hidden;
    const label = yt ? (on ? 'Hold for review' : 'Publish again') : (on ? 'Hide' : 'Unhide');
    const ask = yt ? (on ? 'Hold ' + name + '\'s comment for review on YouTube (' + it.acct_name + ')? It stops showing until you publish it again.' : 'Publish ' + name + '\'s comment again on YouTube (' + it.acct_name + ')?')
      : (on ? 'Hide ' + name + '\'s comment on ' + P + ' (' + it.acct_name + ')? Others stop seeing it; ' + name + ' still sees it. You can unhide it later.' : 'Show ' + name + '\'s comment again on ' + P + ' (' + it.acct_name + ')?');
    b.push(el('button', {type: 'button', onclick: e => { if (SX.ask(ask + '\n\n"' + it.text.slice(0, 300) + '"')) post({op: on ? 'inbox-hide' : 'inbox-unhide', account: it.acct, item: it.id}, e.currentTarget); }}, label));
  }
  if (it.platform === 'facebook' && !it.author.is_us && it.state !== 'gone') {
    const liked = (it.replies || []).some(r => r.kind === 'like');
    b.push(el('button', {type: 'button', disabled: liked, onclick: e => post({op: 'inbox-like', account: it.acct, item: it.id}, e.currentTarget)}, liked ? 'Liked' : 'Like'));
  }
  b.push(...personButtons(it.acct, it.acct_name, it.author, P, {item: it.id}));
  return el('div', {class: 'ib-acts'}, b);
}
function personButtons(acct, acctName, person, P, ref) {
  const out = [], name = person.name;
  if (person.is_us) return out;
  out.push(el('button', {type: 'button', onclick: e => {
    const undo = person.stop;
    const ask = undo ? 'Let automations answer ' + name + ' on ' + P + ' again (' + acctName + ')?' : 'Stop every automated message to ' + name + ' on ' + P + ' (' + acctName + ')? Your own replies still go.';
    if (SX.ask(ask)) SX.act(Object.assign({op: 'inbox-stop', account: acct, undo}, ref), e.currentTarget).then(() => poller.now());
  }}, person.stop ? 'Allow automations again' : 'Stop automations to them'));
  if (!person.tester) out.push(el('button', {type: 'button', onclick: e => {
    if (SX.ask('Is ' + name + ' on ' + P + ' you (or someone with a role on the Meta app)? Mark them a tester of ' + acctName + ': testers mode answers testers for real, and messages reach testers before Meta\'s review.'))
      SX.act(Object.assign({op: 'inbox-tester', account: acct}, ref), e.currentTarget).then(() => poller.now());
  }}, 'This is me (tester)'));
  out.push(el('button', {type: 'button', class: 'danger', onclick: e => {
    if (SX.ask('Forget ' + name + ' on ' + P + ' (' + acctName + ')? Their comments, messages and record leave Clip Review, and the log keeps no words or name of theirs. This cannot be undone.'))
      SX.act(Object.assign({op: 'inbox-forget', account: acct}, ref), e.currentTarget).then(r => { if (r.ok) { back(); poller.now(); } });
  }}, 'Forget this person'));
  return out;
}

/* ---------- messages ---------- */
function threadRow(t) {
  const last = (t.messages || []).slice(-1)[0] || {};
  const r1 = [el('b', {}, t.with.name), PW[t.platform]];
  if (many()) r1.push(t.acct_name);
  r1.push(SX.ago(t.updated));
  if (t.unread && t.state === 'new') r1.push(el('span', {class: 'ib-unread', 'aria-label': t.unread + ' unread'}, String(t.unread)));
  return el('button', {type: 'button', class: 'sx-row ib-row', role: 'listitem', 'aria-selected': String(ST.sel === t.id), onclick: () => pick(t.id)},
    [av(t.with), el('div', {class: 'ib-rb'}, [el('div', {class: 'ib-r1'}, spans(r1)), el('div', {class: 'ib-txt'}, (last.us ? 'You: ' : '') + (last.text || ''))])]);
}
function drawMessages(full) {
  const shown = D.threads.filter(t => !ST.plats.size || ST.plats.has(t.platform));
  list.setAttribute('aria-label', 'Conversations');
  const off = !(D.accounts || []).some(a => a.messages === 'on');
  list.replaceChildren(...(shown.length ? shown.map(threadRow) : [el('div', {class: 'sx-empty'}, off ?
    'Messages are off: they wait for Meta\'s review (Advanced Access), then engage.messages in Log, Switches.' : 'No conversations yet.')]));
  if (full !== false) drawThread(D.threads.find(t => t.id === ST.sel));
}
function drawThread(t) {
  openDetail(!!t && ST.open);
  if (!t) { detail.replaceChildren(el('div', {class: 'sx-empty'}, 'Pick a conversation.')); return; }
  const acct = acctOf(t.acct), P = PW[t.platform], w = t.with;
  const kids = [backBtn(), el('div', {class: 'ib-dh'}, [av(w, true), el('div', {class: 'ib-rb'}, [el('div', {class: 'ib-r1'}, spans([el('b', {}, w.name), P, t.acct_name])),
    el('div', {class: 'ib-r3'}, [w.tester ? tag('Tester', 'ok') : null, w.stop ? tag('No automations', 'warn') : null, t.state === 'done' ? tag('Done', 'ok') : null])])])];
  kids.push(el('div', {class: 'ib-msgs'}, (t.messages || []).map(m => el('div', {class: 'ib-msg' + (m.us ? ' us' : '')}, [m.text || '(no words)', el('small', {}, (m.us ? 'You, ' : '') + SX.ago(m.at))]))));
  for (const p of t.pending || []) if (p.state !== 'done') kids.push(el('div', {class: 'ib-note ' + stateCls(p.state)}, 'Your message: ' + stateWords(p) + (p.text ? ': "' + p.text.slice(0, 80) + '"' : '')));
  const h = hoursLeft(t.last_in_at), open = h > 0;
  kids.push(el('div', {class: 'ib-note' + (open ? '' : ' warn')}, open ? 'You can answer for ' + leftWords(h) + '.' :
    'The answer window closed (23 hours after their last message): you can answer when they write again.'));
  if (open && acct.access !== 'advanced' && !w.tester) kids.push(el('div', {class: 'ib-note'}, 'Messages reach only app testers until Meta\'s review: this answer is practice.'));
  if (acct.replies !== 'on') kids.push(el('div', {class: 'ib-note warn'}, acct.replies === 'off' ? 'Replies are off for ' + t.acct_name + ': switch them on in Log, Switches.' : 'Replies are in practice: nothing is sent.'));
  const ta = el('textarea', {id: 'ib-text', 'aria-label': 'Your message', placeholder: open ? 'Answer as ' + t.acct_name : '', disabled: !open});
  const count = el('span', {class: 'ib-count'});
  const send = el('button', {type: 'button', class: 'go', disabled: !open, onclick: async e => {
    const text = ta.value.trim(); if (!text) return;
    if (!SX.ask('Send this message on ' + P + ' to ' + w.name + ', as ' + t.acct_name + '?\n\n' + text)) return;
    const r = await post({op: 'inbox-dm', account: t.acct, thread: t.id, text}, e.currentTarget);
    if (r.ok) { drafts['t:' + t.id] = ''; ta.value = ''; }
  }}, 'Send');
  const upd = () => { drafts['t:' + t.id] = ta.value; send.disabled = !open || !counter(count, ta, 1000, true) || !ta.value.trim() || acct.replies === 'off'; };
  ta.addEventListener('input', upd);
  ta.addEventListener('blur', () => { if (pendingDetail) setTimeout(() => { if (!detail.contains(document.activeElement)) draw(); }, 50); });
  const chips = savedFor(t.acct, t.platform).map(s => el('button', {type: 'button', class: 'sx-chip', title: s.text, disabled: !open,
    onclick: () => { ta.value = ta.value ? ta.value.replace(/\s*$/, ' ') + s.text : s.text; upd(); ta.focus(); }}, s.label));
  kids.push(el('div', {class: 'ib-comp'}, [ta, chips.length ? el('div', {class: 'sx-chips'}, chips) : null, el('div', {class: 'ib-crow'}, [count, send])]));
  const b = [el('button', {type: 'button', onclick: e => SX.act({op: t.state === 'done' ? 'inbox-undone' : 'inbox-done', account: t.acct, thread: t.id}, e.currentTarget).then(() => poller.now())},
    t.state === 'done' ? 'Not done' : 'Mark done')];
  b.push(...personButtons(t.acct, t.acct_name, w, P, {thread: t.id}));
  kids.push(el('div', {class: 'ib-acts'}, b));
  detail.replaceChildren(...kids);
  restore('t:' + t.id);
  setTimeout(upd, 0);
}

/* ---------- saved replies ---------- */
function allSaved() {
  const out = [];
  for (const a of D.accounts || []) for (const s of (D.saved || {})[a.slug] || []) out.push(Object.assign({acct: a.slug, acct_name: a.name}, s));
  return out;
}
function drawSaved(full) {
  const rows = allSaved();
  list.setAttribute('aria-label', 'Saved replies');
  list.replaceChildren(...(rows.length ? rows.map(s => el('button', {type: 'button', class: 'sx-row ib-row', role: 'listitem', 'aria-selected': String(ST.sel === s.id), onclick: () => pick(s.id)},
    [el('div', {class: 'ib-rb'}, [el('div', {class: 'ib-r1'}, spans([el('b', {}, s.label)].concat(many() ? [s.acct_name] : []).concat([(s.platforms || []).map(p => PW[p]).join(', ')]))),
      el('div', {class: 'ib-txt'}, s.text)])])) : [el('div', {class: 'sx-empty'}, 'No saved replies yet. Save the answers you give often: they show as chips under every reply.')]));
  if (full !== false) drawSavedForm(ST.sel === 'new' ? {} : rows.find(s => s.id === ST.sel));
}
function drawSavedForm(s) {
  openDetail(!!s && ST.open);
  if (!s) { detail.replaceChildren(el('div', {class: 'sx-empty'}, 'Pick a saved reply, or make a new one.')); return; }
  const accts = D.accounts || [];
  const sel = el('select', {id: 'ib-sacct', 'aria-label': 'Account'}, accts.map(a => el('option', {value: a.slug, selected: a.slug === (s.acct || (accts[0] || {}).slug)}, a.name)));
  if (s.id) sel.disabled = true;
  const label = el('input', {id: 'ib-slabel', maxlength: '40', value: s.label || ''});
  const text = el('textarea', {id: 'ib-stext', maxlength: '2000'}); text.value = s.text || '';
  const plats = ['instagram', 'facebook', 'youtube'].map(p => el('label', {class: 'ib-check'}, [el('input', {type: 'checkbox', value: p, checked: !s.platforms || !s.platforms.length || s.platforms.includes(p)}), PW[p]]));
  const save = el('button', {type: 'button', class: 'go', onclick: async e => {
    const ps = plats.map(l => l.querySelector('input')).filter(i => i.checked).map(i => i.value);
    const r = await SX.act({op: 'saved-set', account: sel.value, id: s.id || '', label: label.value, text: text.value, platforms: ps}, e.currentTarget);
    if (r.ok) { ST.sel = ''; back(); poller.now(); }
  }}, 'Save');
  const kids = [backBtn(), el('div', {class: 'sx-form ib-savedform'}, [
    accts.length > 1 ? el('label', {}, ['Account', sel]) : null,
    el('label', {}, ['Name (shows on the chip)', label]), el('label', {}, ['Words', text]),
    el('div', {class: 'ib-plats', role: 'group', 'aria-label': 'Platforms'}, plats),
    el('div', {class: 'ib-crow'}, [save, s.id ? el('button', {type: 'button', class: 'danger', onclick: async e => {
      if (!SX.ask('Delete the saved reply "' + s.label + '" (' + s.acct_name + ')?')) return;
      const r = await SX.act({op: 'saved-delete', account: s.acct, id: s.id}, e.currentTarget);
      if (r.ok) { ST.sel = ''; back(); poller.now(); }
    }}, 'Delete') : null])])];
  detail.replaceChildren(...kids);
}

/* ---------- drawing ---------- */
let pendingDetail = false;
function restore(key) {
  const ta = $('ib-text');
  if (ta && drafts[key] && !ta.disabled) ta.value = drafts[key];
}
function typing() {
  const a = document.activeElement;
  return !!a && detail.contains(a) && (a.tagName === 'TEXTAREA' || a.tagName === 'INPUT' || a.tagName === 'SELECT');
}
function draw(force) {
  if (!D) return;
  drawStatus(); drawBar();
  const full = force === true || !typing();
  pendingDetail = !full;
  if (VIEW === 'messages') drawMessages(full); else if (VIEW === 'saved') drawSaved(full); else drawComments(full);
}
function busy(d) {
  return (d.items || []).some(it => (it.replies || []).some(r => SENDING.includes(r.state))) ||
    (d.threads || []).some(t => (t.pending || []).some(p => SENDING.includes(p.state)));
}
$('ib-check').addEventListener('click', e => SX.act({op: 'inbox-check'}, e.currentTarget).then(() => setTimeout(() => poller.now(), 4000)));
const url = () => '/inbox/data' + (SX.ACCT && SX.ACCT !== 'all' ? '?acct=' + encodeURIComponent(SX.ACCT) : '');
poller = SX.poll(url, 15000, d => {
  D = d; draw();
  const b = busy(d);
  if (b !== waiting) { waiting = b; poller.every(b ? 5000 : 15000); }
});
setInterval(() => { if (D && !typing()) draw(); }, 60000);
})();
"""


def page(cfg: C.Config, tok: str, base_css: str, tabs_html: str, switch_html: str, acct: str,
         q: Optional[dict] = None) -> str:
    """The Inbox tab: its views (Comments, Messages, Saved replies, and Automations once built), the status line and
    Check now, the filters, the list and the detail. The JSON comes from /inbox/data."""
    q = q if isinstance(q, dict) else {}
    view = str(q.get("view") or "comments")
    if view not in ("comments", "messages", "saved"):
        view = "comments"
    own = [("comments", "/inbox", "Comments"), ("messages", "/inbox?view=messages", "Messages"),
           ("saved", "/inbox?view=saved", "Saved replies")]
    nav = SX.subnav("inbox", view, acct, own)
    return SX.shell(cfg, f"inbox ib-{view}", "Inbox", tok, base_css, tabs_html, switch_html, acct, BODY, css=CSS, js=JS,
                    subnav=nav)
