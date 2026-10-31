"""The Inbox's own files: the comments and messages read from each platform, the people behind them, saved replies,
where each poll stopped, which post is which clip, and the Part 2 waitlist. One account's files, under
state/<account>/engage/, each with its own lock (store.edit), so the poll, a click and the automations never wait on
each other for long and never write half a file:

    items-YYYY-MM.json   {"v": 1, "items": {id: item}}          a comment or a message, in the month of its own "at"
    threads.json         {"v": 1, "threads": {tid: thread}}     a DM conversation and its last 20 messages
    contacts.json        {"v": 1, "contacts": {key: contact}}   a person: when they wrote, when we answered, STOP
    saved.json           {"items": [{id, label, text, platforms, updated}]}
    cursor.json          {"v": 1, "cursor": {platform: {...}}}  where each poll stopped (the Inbox's own fields)
    posts.json           {"v": 1, "updated", "posts": {platform_id: {outlet, day, slot, slug, title, url, ...}}}
    rules.json           the automations' rules (rules.py edits it through edit(acct, "rules"))
    waitlist.json        {"v": 1, "waitlist": {source_key: [{item, contact, at, expires, sent}]}}
    forgotten.json       {"v": 1, "forgotten": {sha256 of a person's key: at}, "stops": {sha256: at}}  who forget
                         removed (never the key): the poll drops whatever they write or wrote, so a forget is never
                         read back in; "stops" are the forgotten who had said STOP, so their STOP outlives the forget
    runs-YYYY-MM.jsonl   one line per automation run {at, rule, item, result, why}; past the days kept a run keeps
                         what happened and loses the person (RUN_PERSON_KEYS)

Ids: a comment is "ig:" / "fb:" / "yt:" plus the platform's id, a DM message "igm:" / "fbm:", a thread "igt:" /
"fbt:" (item_id, thread_id). A person is "<platform>:<their id>" (author.key, the contact's key).

What the platform says (text, likes, hidden) is written over on every poll; what we did (state, answered, replies,
auto, suggested_reply) is kept. Platform data stays small, local and deletable: prune keeps 45 days, and forget
removes a person from these files and from the outward log (outward.forget). Nothing here reaches the network.
"""
from __future__ import annotations

import contextlib
import hashlib
import json
import os
import re
import secrets
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, Iterator, List, Optional, Sequence, Tuple

from . import store

NAMES = ("threads", "contacts", "saved", "cursor", "posts", "rules", "waitlist", "forgotten")
STATES = ("new", "answered", "done", "hidden", "gone")
KINDS = ("comment", "reply", "dm")
LOCAL = ("state", "answered", "replies", "auto", "suggested_reply")      # ours: a poll never writes over them
PREFIX = {"instagram": "ig", "facebook": "fb", "youtube": "yt"}
THREAD_MESSAGES = 20                     # the last messages a thread keeps
WINDOW_H = 24                            # Meta's answer window after their last message
OUTS_DAYS = 90                           # how long a contact keeps the times we wrote to them
KEEP_DAYS = 45                           # the longest the Inbox keeps a comment (engage.keep_days may only lower it)
_MONTH = re.compile(r"^items-\d{4}-\d{2}$")
RUN_PERSON_KEYS = ("text", "suggested", "contact", "to", "name")  # a run's fields that name or quote the person
STOP_WHY = "wrote STOP"                  # a STOP's reason never carries their words (they would outlive the prune)


def _slug(acct) -> str:
    return str(getattr(acct, "slug", acct) or "")


def _iso(t: datetime) -> str:
    return t.astimezone(timezone.utc).isoformat(timespec="seconds")


def _now(now: Optional[datetime]) -> datetime:
    if now is None:
        return datetime.now(timezone.utc)
    return now if now.tzinfo else now.replace(tzinfo=timezone.utc)


def item_id(platform: str, platform_id: str, kind: str = "comment") -> str:
    """"ig:1789..." for a comment or reply, "igm:..." for a DM message."""
    pf = PREFIX.get(platform)
    if not pf:
        raise SystemExit(f"{platform}: not a platform the Inbox reads ({', '.join(PREFIX)})")
    return f"{pf}{'m' if kind == 'dm' else ''}:{platform_id}"


def thread_id(platform: str, platform_id: str) -> str:
    """"igt:..." / "fbt:..." for a DM conversation."""
    pf = PREFIX.get(platform)
    if pf not in ("ig", "fb"):
        raise SystemExit(f"{platform}: messages are read only on Instagram and Facebook")
    return f"{pf}t:{platform_id}"


# ------------------------------------------------------------------------------------------------------ files
def root(acct) -> Path:
    """C.STATE/<account>/engage (read at call time)."""
    return store.path(_slug(acct), "engage")


def _file(acct, name: str) -> Path:
    if name not in NAMES and not _MONTH.match(name):
        raise SystemExit(f"{name}: not an Inbox file ({', '.join(NAMES)}, or items-YYYY-MM)")
    return root(acct) / f"{name}.json"


@contextlib.contextmanager
def edit(acct, name: str) -> Iterator[dict]:
    """One of the account's Inbox files (NAMES, or "items-YYYY-MM"), locked, read and written back whole."""
    with store.edit(_file(acct, name), dict) as d:
        yield d


def _has(acct, name: str) -> bool:
    return _file(acct, name).is_file()


def read(acct, name: str, default=None):
    """The file's JSON (a dict), or `default` when it is missing or broken."""
    got = store.read(_file(acct, name), None)
    return got if isinstance(got, dict) else default


def _section(d: dict, key: str) -> dict:
    """d[key] as a dict (made when missing or of another type), with "v" set."""
    d.setdefault("v", 1)
    if not isinstance(d.get(key), dict):
        d[key] = {}
    return d[key]


# ------------------------------------------------------------------------------------------------------ items
def _month_of(item: dict, now: datetime) -> str:
    t = store._when(item.get("at"))
    return (t or now).astimezone(timezone.utc).strftime("%Y-%m")


def _months(acct) -> List[str]:
    """The item month files there are, newest first ("2026-10", ...)."""
    d = root(acct)
    if not d.is_dir():
        return []
    return sorted((f.stem[len("items-"):] for f in d.glob("items-*.json") if _MONTH.match(f.stem)), reverse=True)


def _items_of(acct, month: str) -> dict:
    got = read(acct, f"items-{month}", {})
    items = got.get("items") if isinstance(got, dict) else None
    return items if isinstance(items, dict) else {}


def _where(acct, iid: str) -> Optional[str]:
    """The month file an item is in."""
    for m in _months(acct):
        if iid in _items_of(acct, m):
            return m
    return None


def _fresh(item: dict, now: datetime) -> dict:
    out = dict(item)
    out.setdefault("seen_at", _iso(now))
    out.setdefault("tags", [])
    out.setdefault("hidden", False)
    out["state"] = out.get("state") if out.get("state") in STATES else "new"
    out.setdefault("answered", None)
    out["replies"] = list(out.get("replies") or [])
    out.setdefault("auto", None)
    out.setdefault("suggested_reply", None)
    return out


def upsert_items(acct, items: Sequence[dict], now: Optional[datetime] = None) -> Tuple[List[str], List[str]]:
    """Write what a poll read: (new ids, changed ids). A new item starts "new"; one already here takes the
    platform's fields and keeps ours (state, answered, replies, auto, suggested_reply). Items go in the month of their
    own "at", each month file under its own lock."""
    now = _now(now)
    by_month: Dict[str, List[dict]] = {}
    for it in items or []:
        if not isinstance(it, dict) or not it.get("id"):
            continue
        iid = str(it["id"])
        by_month.setdefault(_where(acct, iid) or _month_of(it, now), []).append(it)
    new: List[str] = []
    changed: List[str] = []
    for month, group in sorted(by_month.items()):
        with edit(acct, f"items-{month}") as d:
            have = _section(d, "items")
            for it in group:
                iid = str(it["id"])
                old = have.get(iid)
                if not isinstance(old, dict):
                    have[iid] = _fresh(it, now)
                    new.append(iid)
                    continue
                merged = dict(old)
                merged.update({k: v for k, v in it.items() if k not in LOCAL and k != "seen_at"})
                if merged != old:
                    have[iid] = merged
                    changed.append(iid)
    return new, changed


def get_item(acct, iid: str) -> Optional[dict]:
    """One item, or None."""
    m = _where(acct, str(iid))
    return _items_of(acct, m).get(str(iid)) if m else None


def items(acct, since=None, until=None, states: Optional[Sequence[str]] = None,
          platforms: Optional[Sequence[str]] = None) -> List[dict]:
    """The items, newest first; `since` / `until` (a datetime, a date or an ISO time) on the item's "at"."""
    lo = store._when(since) if since is not None else None
    hi = store._when(until) if until is not None else None
    out: List[dict] = []
    for m in _months(acct):
        if lo is not None and m < lo.astimezone(timezone.utc).strftime("%Y-%m"):
            continue
        for it in _items_of(acct, m).values():
            if not isinstance(it, dict):
                continue
            if states is not None and it.get("state") not in states:
                continue
            if platforms is not None and it.get("platform") not in platforms:
                continue
            t = store._when(it.get("at"))
            if (lo is not None and (t is None or t < lo)) or (hi is not None and (t is None or t >= hi)):
                continue
            out.append(it)
    out.sort(key=lambda i: str(i.get("at") or ""), reverse=True)
    return out


@contextlib.contextmanager
def _item_edit(acct, iid: str) -> Iterator[dict]:
    m = _where(acct, str(iid))
    if m is None:
        raise SystemExit(f"{iid}: not in the Inbox (it may have been pruned or forgotten)")
    with edit(acct, f"items-{m}") as d:
        it = _section(d, "items").get(str(iid))
        if not isinstance(it, dict):
            raise SystemExit(f"{iid}: not in the Inbox (it may have been pruned or forgotten)")
        yield it


def set_state(acct, iid: str, state: str, by: str, now: Optional[datetime] = None, answered=None) -> dict:
    """Mark an item new, answered, done, hidden or gone (gone: deleted on the platform). The item after."""
    if state not in STATES:
        raise SystemExit(f"{state}: an item is one of {', '.join(STATES)}")
    now = _now(now)
    with _item_edit(acct, iid) as it:
        it["state"] = state
        it["state_by"] = str(by or "")
        it["state_at"] = _iso(now)
        if answered is not None:
            it["answered"] = answered
        if state == "hidden":
            it["hidden"] = True
        return dict(it)


def unhide(acct, iid: str, by: str, state: str = "new", now: Optional[datetime] = None) -> dict:
    """The comment shows again (an unhide went through): hidden cleared and the item back to `state` (new, or
    answered when we had replied). The item after. A poll that reads it hidden on the platform marks it again."""
    if state not in STATES or state in ("hidden", "gone"):
        raise SystemExit(f"{state}: an unhidden item is new, answered or done")
    now = _now(now)
    with _item_edit(acct, iid) as it:
        it["hidden"] = False
        it["state"] = state
        it["state_by"] = str(by or "")
        it["state_at"] = _iso(now)
        return dict(it)


SETTABLE = ("auto", "suggested_reply")      # our own notes on an item that set_local may write (kept over polls)


def set_local(acct, iid: str, **fields) -> dict:
    """Write our own notes on an item (auto: what an automation did with it; suggested_reply): kept over every
    poll. Only SETTABLE fields; the state goes through set_state, replies through add_reply. The item after
    (SystemExit when it is not in the Inbox)."""
    bad = sorted(set(fields) - set(SETTABLE))
    if bad:
        raise SystemExit(f"set_local: {', '.join(bad)} cannot be set here (only {', '.join(SETTABLE)})")
    with _item_edit(acct, iid) as it:
        it.update(fields)
        return dict(it)


def add_reply(acct, iid: str, reply: dict) -> dict:
    """Our reply on an item ({id, by, text, at, action}); the same action or id again updates it instead of adding
    a second one. The item after."""
    if not isinstance(reply, dict):
        raise SystemExit("a reply is {id, by, text, at, action}")
    with _item_edit(acct, iid) as it:
        rs = it["replies"] = [r for r in (it.get("replies") or []) if isinstance(r, dict)]
        for r in rs:
            if (reply.get("action") and r.get("action") == reply["action"]) or (reply.get("id") and
                                                                                 r.get("id") == reply["id"]):
                r.update(reply)
                break
        else:
            rs.append(dict(reply))
        return dict(it)


# ------------------------------------------------------------------------------------------------------ threads
def upsert_thread(acct, thread: dict, messages: Sequence[dict], now: Optional[datetime] = None) -> List[str]:
    """Write a DM conversation and the messages read from it: the ids of the messages new here. A new message from
    them opens the answer window (24 hours) and makes a done thread new again; the thread keeps its last 20."""
    if not isinstance(thread, dict) or not thread.get("id"):
        raise SystemExit("a thread needs its id (igt:... or fbt:...)")
    now = _now(now)
    tid = str(thread["id"])
    with edit(acct, "threads") as d:
        have = _section(d, "threads")
        t = have.get(tid) if isinstance(have.get(tid), dict) else {"state": "new", "unread": 0, "messages": []}
        t.update({k: v for k, v in thread.items() if k not in ("messages", "state", "unread")})
        old = {str(m.get("id")): m for m in t.get("messages") or [] if isinstance(m, dict) and m.get("id")}
        fresh: List[str] = []
        for m in messages or []:
            if not isinstance(m, dict) or not m.get("id"):
                continue
            mid = str(m["id"])
            if mid not in old:
                fresh.append(mid)
                if not m.get("from_us"):
                    t["unread"] = int(t.get("unread") or 0) + 1
                    t["state"] = "new"
            old[mid] = dict(m)
        msgs = sorted(old.values(), key=lambda m: str(m.get("at") or ""))
        t["messages"] = msgs[-THREAD_MESSAGES:]
        ins = [store._when(m.get("at")) for m in msgs if not m.get("from_us")]
        ins = [x for x in ins if x is not None]
        if ins:
            last = max(ins)
            t["last_in_at"] = _iso(last)
            t["window_until"] = _iso(last + timedelta(hours=WINDOW_H))
        stamps = [x for x in (store._when(m.get("at")) for m in msgs) if x is not None]
        t["updated"] = _iso(max(stamps)) if stamps else str(t.get("updated") or _iso(now))
        t.setdefault("state", "new")
        have[tid] = t
    return fresh


def thread(acct, tid: str) -> Optional[dict]:
    """One conversation, or None."""
    got = (read(acct, "threads", {}) or {}).get("threads")
    t = got.get(str(tid)) if isinstance(got, dict) else None
    return t if isinstance(t, dict) else None


def threads(acct, states: Optional[Sequence[str]] = None, platforms: Optional[Sequence[str]] = None) -> List[dict]:
    """The conversations, the one written to last first."""
    got = (read(acct, "threads", {}) or {}).get("threads")
    out = [dict(t, id=t.get("id") or k) for k, t in (got.items() if isinstance(got, dict) else [])
           if isinstance(t, dict) and (states is None or t.get("state") in states)
           and (platforms is None or t.get("platform") in platforms)]
    out.sort(key=lambda t: str(t.get("updated") or ""), reverse=True)
    return out


def set_thread_state(acct, tid: str, state: str, by: str, now: Optional[datetime] = None) -> dict:
    """Mark a conversation new or done (done also reads it: unread 0). The thread after."""
    if state not in ("new", "done"):
        raise SystemExit(f"{state}: a conversation is new or done")
    with edit(acct, "threads") as d:
        t = _section(d, "threads").get(str(tid))
        if not isinstance(t, dict):
            raise SystemExit(f"{tid}: not in the Inbox")
        t.update(state=state, state_by=str(by or ""), state_at=_iso(_now(now)))
        if state == "done":
            t["unread"] = 0
        return dict(t)


# ------------------------------------------------------------------------------------------------------ contacts
def _blank_contact() -> dict:
    return {"name": "", "first_at": None, "last_in_at": None, "last_out_at": None, "last_auto_out_at": None,
            "outs": [], "private_replied": [], "stop": False, "stop_at": None, "stop_why": ""}


def contact(acct, key: str) -> dict:
    """A person's record (an empty one when they are not known yet)."""
    got = (read(acct, "contacts", {}) or {}).get("contacts")
    c = got.get(str(key)) if isinstance(got, dict) else None
    return {**_blank_contact(), **c} if isinstance(c, dict) else _blank_contact()


def touch_contact(acct, key: str, now: Optional[datetime] = None, *, name: str = "", inbound: bool = False,
                  outbound: bool = False, auto: bool = False, private_reply: str = "") -> dict:
    """Note that a person wrote (inbound) or that we wrote to them (outbound; auto when an automation did, with
    the comment id when it was a private reply). The record after; outs keep 90 days."""
    if not str(key or "").strip():
        raise SystemExit("a contact needs its key, platform:id")
    now = _now(now)
    at = _iso(now)
    with edit(acct, "contacts") as d:
        have = _section(d, "contacts")
        c = {**_blank_contact(), **(have.get(key) if isinstance(have.get(key), dict) else {})}
        c["first_at"] = c.get("first_at") or at
        if name:
            c["name"] = str(name)
        if inbound:
            c["last_in_at"] = at
        if outbound or auto or private_reply:
            c["last_out_at"] = at
            cut = now - timedelta(days=OUTS_DAYS)
            c["outs"] = [x for x in c.get("outs") or [] if (store._when(x) or now) >= cut] + [at]
        if auto:
            c["last_auto_out_at"] = at
        if private_reply and private_reply not in (c.get("private_replied") or []):
            c["private_replied"] = list(c.get("private_replied") or []) + [str(private_reply)]
        have[key] = c
        return dict(c)


def stop_contact(acct, key: str, now: Optional[datetime] = None, why: str = "", on: bool = True) -> dict:
    """STOP: nothing automated ever goes to this person again (on=False takes it back, by hand only)."""
    now = _now(now)
    with edit(acct, "contacts") as d:
        have = _section(d, "contacts")
        c = {**_blank_contact(), **(have.get(key) if isinstance(have.get(key), dict) else {})}
        c["first_at"] = c.get("first_at") or _iso(now)
        c.update(stop=bool(on), stop_at=_iso(now) if on else None, stop_why=str(why or "") if on else "")
        have[key] = c
    if not on and _has(acct, "forgotten"):              # taken back by hand: a STOP kept past a forget goes too
        with edit(acct, "forgotten") as d:
            _section(d, "stops").pop(_person_sha(key), None)
    return dict(c)


def stopped(acct, key: str) -> bool:
    """Did this person say STOP? (Also when they were forgotten since: forget keeps the STOP, as a sha256.)"""
    if contact(acct, key).get("stop"):
        return True
    if not str(key or "").strip() or not _has(acct, "forgotten"):
        return False
    got = read(acct, "forgotten", {}) or {}
    stops = got.get("stops") if isinstance(got, dict) else None
    return isinstance(stops, dict) and _person_sha(key) in stops


# ------------------------------------------------------------------------------------------------------ saved replies
def saved(acct) -> List[dict]:
    """The saved replies, in the order they were made."""
    got = (read(acct, "saved", {}) or {}).get("items")
    return [dict(s) for s in got if isinstance(s, dict)] if isinstance(got, list) else []


def saved_put(acct, label: str, text: str, platforms: Optional[Sequence[str]] = None, sid: str = "",
              now: Optional[datetime] = None) -> str:
    """Add a saved reply, or change the one with this id. Its id."""
    label, text = str(label or "").strip(), str(text or "").strip()
    if not label or not text:
        raise SystemExit("a saved reply needs a name and its words")
    plats = [p for p in (platforms or list(PREFIX)) if p in PREFIX]
    with edit(acct, "saved") as d:
        rows = d["items"] = [s for s in (d.get("items") or []) if isinstance(s, dict)]
        row = next((s for s in rows if sid and s.get("id") == sid), None)
        if row is None:
            if sid:
                raise SystemExit(f"{sid}: no saved reply has that id")
            sid = "sr-" + secrets.token_hex(4)
            row = {"id": sid}
            rows.append(row)
        row.update(label=label, text=text, platforms=plats, updated=_iso(_now(now)))
    return sid


def saved_delete(acct, sid: str) -> bool:
    """Remove a saved reply; False when there was none with that id."""
    with edit(acct, "saved") as d:
        rows = [s for s in (d.get("items") or []) if isinstance(s, dict)]
        d["items"] = [s for s in rows if s.get("id") != sid]
        return len(d["items"]) != len(rows)


# ------------------------------------------------------------------------------------------------------ cursor, posts
def cursor(acct, platform: Optional[str] = None) -> dict:
    """Where the polls stopped: one platform's fields, or all of them."""
    got = (read(acct, "cursor", {}) or {}).get("cursor")
    got = got if isinstance(got, dict) else {}
    if platform is None:
        return got
    one = got.get(platform)
    return one if isinstance(one, dict) else {}


def set_cursor(acct, platform: str, upd: dict) -> dict:
    """Merge these fields into one platform's cursor. The cursor after."""
    with edit(acct, "cursor") as d:
        cur = _section(d, "cursor")
        one = cur.get(platform) if isinstance(cur.get(platform), dict) else {}
        one.update(upd or {})
        cur[platform] = one
        return dict(one)


def posts_index(acct) -> dict:
    """{platform_id: {outlet, day, slot, slug, title, url, cover, went, factory}}: which post is which clip."""
    got = (read(acct, "posts", {}) or {}).get("posts")
    return got if isinstance(got, dict) else {}


def set_posts_index(acct, index: dict, now: Optional[datetime] = None) -> None:
    """Replace the posts index (the Inbox rebuilds it from the boards on each tick)."""
    with edit(acct, "posts") as d:
        d.clear()
        d.update(v=1, updated=_iso(_now(now)), posts=dict(index or {}))


# ------------------------------------------------------------------------------------------------------ Part 2 waitlist
def waitlist_add(acct, source_key: str, item: str, contact_key: str, now: Optional[datetime] = None,
                 expires: Optional[datetime] = None) -> bool:
    """Someone asked for Part 2 of this source: they wait for its next clip (7 days unless `expires`). False when
    this comment is already waiting."""
    if not source_key or not item:
        raise SystemExit("the waitlist needs the source and the comment")
    now = _now(now)
    with edit(acct, "waitlist") as d:
        lst = _section(d, "waitlist").setdefault(str(source_key), [])
        if any(isinstance(w, dict) and w.get("item") == item for w in lst):
            return False
        lst.append({"item": str(item), "contact": str(contact_key or ""), "at": _iso(now),
                    "expires": _iso(expires or now + timedelta(days=7)), "sent": None})
        return True


def waitlist(acct, source_key: Optional[str] = None, now: Optional[datetime] = None) -> List[dict]:
    """Who waits for this source (or for any, each with its "source"), not yet sent and not expired."""
    now = _now(now)
    got = (read(acct, "waitlist", {}) or {}).get("waitlist")
    got = got if isinstance(got, dict) else {}
    out: List[dict] = []
    for key, lst in got.items():
        if source_key is not None and key != source_key:
            continue
        for w in lst if isinstance(lst, list) else []:
            if not isinstance(w, dict) or w.get("sent"):
                continue
            exp = store._when(w.get("expires"))
            if exp is not None and exp <= now:
                continue
            out.append(dict(w, source=key))
    return out


def waitlist_mark(acct, source_key: str, item: str, sent: str) -> bool:
    """Note what went to a waiting comment (an action id, or why it was not sent). False when it was not waiting."""
    with edit(acct, "waitlist") as d:
        for w in _section(d, "waitlist").get(str(source_key)) or []:
            if isinstance(w, dict) and w.get("item") == item:
                w["sent"] = str(sent or "") or "sent"
                return True
    return False


# ------------------------------------------------------------------------------------------------------ automation runs
def _runs_path(acct, month: str) -> Path:
    return root(acct) / f"runs-{month}.jsonl"


def append_run(acct, run: dict, now: Optional[datetime] = None) -> None:
    """One automation run ({at, rule, item, result, why}) in the month of its "at"."""
    now = _now(now)
    row = dict(run or {})
    row.setdefault("at", _iso(now))
    store.append(_runs_path(acct, _month_of(row, now)), row)


def runs(acct, since=None, rule: Optional[str] = None) -> List[dict]:
    """The automation runs, oldest first."""
    d = root(acct)
    out: List[dict] = []
    for f in sorted(d.glob("runs-*.jsonl")) if d.is_dir() else []:
        out += [r for r in store.rows(f, since=since) if rule is None or r.get("rule") == rule]
    return out


# ------------------------------------------------------------------------------------------------------ prune, forget
def keep_for(keep_days) -> int:
    """engage.keep_days as the days actually kept: 1 to KEEP_DAYS."""
    try:
        return max(1, min(int(keep_days or KEEP_DAYS), KEEP_DAYS))
    except (TypeError, ValueError):
        return KEEP_DAYS


def kept(at, now: datetime, keep_days) -> bool:
    """Is something written at `at` still inside the days kept? (No time: kept, as prune reads it.) The poll stores
    nothing prune would drop, so a pruned comment on a post still read is never read back in as new."""
    t = store._when(at)
    return t is None or t >= _now(now) - timedelta(days=keep_for(keep_days))


def prune(acct, now: Optional[datetime] = None, keep_days: int = KEEP_DAYS) -> int:
    """Drop what is older than keep_days (45 at most): comments and messages by their "at", conversations by their
    last message, expired waitlist entries, the runs' month files (a run past the cut in a month still kept loses
    the person and the words), and people with no word either way for 90 days (a STOP is kept). How many records
    went or were stripped."""
    now = _now(now)
    keep = keep_for(keep_days)
    cut = now - timedelta(days=keep)
    gone = 0
    if not root(acct).is_dir():
        return 0
    for m in _months(acct):
        p = _file(acct, f"items-{m}")
        with edit(acct, f"items-{m}") as d:
            have = _section(d, "items")
            old = [k for k, it in have.items() if not isinstance(it, dict) or (store._when(it.get("at")) or now) < cut]
            for k in old:
                have.pop(k, None)
            gone += len(old)
            empty = not have
        if empty and m < now.strftime("%Y-%m"):
            p.unlink(missing_ok=True)
    for name in ("threads", "waitlist", "contacts"):
        if _has(acct, name):
            gone += _PRUNE[name](acct, now, cut)
    gone += len(store.prune_months(root(acct), "runs-", keep, now=now))
    d0 = root(acct)
    for f in sorted(d0.glob("runs-*.jsonl")):            # a month file goes whole only once all of it is past the cut:
        gone += _strip_runs(f, cut)                        # till then a run past it loses the person, row by row
    return gone


def _strip_runs(f: Path, cut: datetime) -> int:
    """The runs in this file older than `cut` keep what happened (rule, item, result, why) and lose the person and
    the words (RUN_PERSON_KEYS), under the file's lock. How many rows changed."""
    p = store._inside(f)
    n = 0
    with store._locked(p):
        rows = store.rows(p)
        for r in rows:
            t = store._when(r.get("at"))
            if t is None or t >= cut or r.get("pruned"):
                continue
            for k in RUN_PERSON_KEYS:
                r.pop(k, None)
            r["pruned"] = True
            n += 1
        if n:
            tmp = p.with_name(p.name + ".tmp")
            tmp.write_text("".join(json.dumps(r, sort_keys=False, default=str) + "\n" for r in rows))
            os.replace(tmp, p)
    return n


def _prune_threads(acct, now: datetime, cut: datetime) -> int:
    gone = 0
    with edit(acct, "threads") as d:
        have = _section(d, "threads")
        for k, t in list(have.items()):
            if not isinstance(t, dict) or (store._when(t.get("updated")) or now) < cut:
                have.pop(k, None)
                gone += 1
                continue
            msgs = [m for m in t.get("messages") or [] if (store._when(m.get("at")) or now) >= cut]
            gone += len(t.get("messages") or []) - len(msgs)
            t["messages"] = msgs
    return gone


def _prune_waitlist(acct, now: datetime, cut: datetime) -> int:
    gone = 0
    with edit(acct, "waitlist") as d:
        wl = _section(d, "waitlist")
        for key, lst in list(wl.items()):
            keep_rows = [w for w in (lst if isinstance(lst, list) else []) if isinstance(w, dict)
                         and (store._when(w.get("expires")) or now) > now - timedelta(days=1)]
            gone += (len(lst) if isinstance(lst, list) else 0) - len(keep_rows)
            if keep_rows:
                wl[key] = keep_rows
            else:
                wl.pop(key, None)
    return gone


def _prune_contacts(acct, now: datetime, cut: datetime) -> int:
    gone = 0
    with edit(acct, "contacts") as d:
        have = _section(d, "contacts")
        idle = now - timedelta(days=OUTS_DAYS)
        for k, c in list(have.items()):
            if not isinstance(c, dict):
                have.pop(k, None)
                continue
            c["outs"] = [x for x in c.get("outs") or [] if (store._when(x) or now) >= idle]
            if str(c.get("stop_why") or "").startswith("wrote: "):      # an older STOP kept their words: not now
                c["stop_why"] = STOP_WHY
            last = max([t for t in (store._when(c.get(f)) for f in ("first_at", "last_in_at", "last_out_at"))
                        if t is not None] or [now])
            if last < idle and not c.get("stop"):
                have.pop(k, None)
                gone += 1
            elif last < idle and c.get("name"):                       # a STOP stays (by key), their name goes
                c["name"] = ""
    return gone


_PRUNE = {"threads": _prune_threads, "waitlist": _prune_waitlist, "contacts": _prune_contacts}


def _theirs(it: dict, person: str) -> bool:
    a = it.get("author") if isinstance(it.get("author"), dict) else {}
    w = it.get("with") if isinstance(it.get("with"), dict) else {}
    return person in (str(a.get("key") or ""), str(w.get("key") or ""))


def _our_reply_to(it: dict, ids: set) -> bool:
    """Is this our own reply (read back from the platform) under one of these comments?"""
    a = it.get("author") if isinstance(it.get("author"), dict) else {}
    parent = str(it.get("parent_id") or "")
    if not a.get("is_us") or not parent or it.get("platform") not in PREFIX:
        return False
    return item_id(str(it["platform"]), parent) in ids


def _person_sha(person: str) -> str:
    return hashlib.sha256(str(person or "").strip().encode("utf-8")).hexdigest()


def forgotten(acct) -> set:
    """The sha256 of each person forgotten on this account (never their keys)."""
    got = read(acct, "forgotten", {}) or {}
    have = got.get("forgotten") if isinstance(got, dict) else None
    return set(have) if isinstance(have, dict) else set()


def is_forgotten(person: str, shas: set) -> bool:
    """Was this person ("instagram:1784...") forgotten? `shas` is forgotten(acct)."""
    return bool(person) and bool(shas) and _person_sha(person) in shas


def forget(acct, person_key: str, by: str, cfg=None) -> int:
    """Forget a person ("instagram:1784..."): their comments (with our own replies under them, which @name them),
    messages, conversation, contact record and waitlist places are removed here (a STOP they said stays, as a
    sha256 only), the automation runs on their comments lose the words and the person, and outward.forget blanks
    them in the outward log and drops anything still queued for them. How many records changed. A line says who pressed
    it and how many (never whom) in engage/forgotten.jsonl; the sha256 of their key goes in forgotten.json, so the
    poll never reads them back in (their comments are still on the posts it reads)."""
    person = str(person_key or "").strip()
    if ":" not in person or person.endswith(":"):
        raise SystemExit("forget needs the person, platform:id")
    with edit(acct, "forgotten") as d:                  # first: a poll running meanwhile already drops them
        _section(d, "forgotten").setdefault(_person_sha(person), _iso(_now(None)))
    n = 0
    mine: List[str] = []
    for m in _months(acct):
        with edit(acct, f"items-{m}") as d:
            have = _section(d, "items")
            for k in [k for k, it in have.items() if isinstance(it, dict) and _theirs(it, person)]:
                have.pop(k, None)
                mine.append(k)
                n += 1
    if mine:                                            # our own replies under their comments name them (@them)
        theirs = set(mine)
        for m in _months(acct):
            with edit(acct, f"items-{m}") as d:
                have = _section(d, "items")
                for k in [k for k, it in have.items() if isinstance(it, dict) and _our_reply_to(it, theirs)]:
                    have.pop(k, None)
                    mine.append(k)
                    n += 1
    if _has(acct, "threads"):
        with edit(acct, "threads") as d:
            have = _section(d, "threads")
            for k in [k for k, t in have.items() if isinstance(t, dict) and _theirs(t, person)]:
                have.pop(k, None)
                mine.append(k)
                n += 1
    if _has(acct, "contacts"):
        with edit(acct, "contacts") as d:
            gone = _section(d, "contacts").pop(person, None)
        if gone is not None:
            n += 1
        if isinstance(gone, dict) and gone.get("stop"):     # their STOP outlives the forget: the sha256 only
            with edit(acct, "forgotten") as d:
                _section(d, "stops").setdefault(_person_sha(person), str(gone.get("stop_at") or _iso(_now(None))))
    if _has(acct, "waitlist"):
        with edit(acct, "waitlist") as d:
            wl = _section(d, "waitlist")
            for key, lst in list(wl.items()):
                rest = [w for w in (lst if isinstance(lst, list) else [])
                        if isinstance(w, dict) and w.get("contact") != person and w.get("item") not in mine]
                n += (len(lst) if isinstance(lst, list) else 0) - len(rest)
                if rest:
                    wl[key] = rest
                else:
                    wl.pop(key, None)
    d0 = root(acct)
    for f in sorted(d0.glob("runs-*.jsonl")) if d0.is_dir() else []:
        n += _forget_runs(f, set(mine), person)
    from . import registry as REG
    out = REG.fn("outward:forget")
    if out is not None:
        n += int(out(cfg, acct, person) or 0)
    store.append(root(acct) / "forgotten.jsonl", {"at": _iso(_now(None)), "by": str(by or ""), "records": n})
    return n


def _forget_runs(f: Path, ids: set, person: str) -> int:
    """The runs on this person's comments keep their result and lose the words and the person (read and written
    back under the file's lock, so a run appended meanwhile is never lost)."""
    p = store._inside(f)
    n = 0
    with store._locked(p):
        rows = store.rows(p)
        for r in rows:
            if r.get("item") in ids or r.get("contact") == person or r.get("to") == person:
                for k in RUN_PERSON_KEYS:
                    if r.get(k):
                        r[k] = "[forgotten]"
                n += 1
        if n:
            tmp = p.with_name(p.name + ".tmp")
            tmp.write_text("".join(json.dumps(r, sort_keys=False, default=str) + "\n" for r in rows))
            os.replace(tmp, p)
    return n
