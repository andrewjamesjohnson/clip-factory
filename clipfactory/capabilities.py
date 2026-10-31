"""What each account's sign-ins may do: the permission names Meta and Google granted, kept in state with no secret.

A social part asks `missing(acct, platform, need)` before it goes live (switches.checklist) and before it sends
(outward.check): [] when the sign-in has every permission the need takes, else the names it lacks. The names come from
the sign-in itself, saved at `connect` (direct.connect_meta, outlets.save_youtube) and read again once a day for an
account with a social part on (refresh_due, the social tick). A refusal Meta gives anyway (code 10 or 200-299: the app
lacks the access, whatever the token says) marks the need missing until the next connect (mark_missing).

    state/<slug>/capabilities.json
      {"meta":    {"scopes": [...], "at": ..., "source": "connect|refresh"},
       "youtube": {"scopes": ["youtube.readonly", "youtube.upload", ...], "at": ..., "source": ...},
       "probes":  {name: {"value": ..., "at": ...}},          what a probe by hand found (and "refresh": when read)
       "missing": {"instagram|comment": {"at": ..., "why": ...}}}

    factory.py social probe <account> scopes|ig-comments|fb-comments|yt-comments|messages|publish-limit|yt-analytics

Only refresh and probe reach the network (through graph.py); missing, the doctor and the page read the file only.
"""
from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone
from typing import Dict, List, Optional, Tuple

from . import config as C
from . import store
from . import switches as SW

NEED_NAMES = ("read_comments", "comment", "moderate", "message", "publish", "publish_story", "analytics")
NEEDS: Dict[Tuple[str, str], Tuple[str, ...]] = {
    ("instagram", "read_comments"): ("instagram_basic", "instagram_manage_comments", "pages_read_engagement"),
    ("instagram", "comment"): ("instagram_manage_comments",),
    ("instagram", "moderate"): ("instagram_manage_comments",),
    ("instagram", "message"): ("instagram_manage_messages",),
    ("instagram", "publish"): ("instagram_content_publish",),
    ("instagram", "publish_story"): ("instagram_content_publish",),
    ("instagram", "analytics"): ("instagram_manage_insights",),
    ("facebook", "read_comments"): ("pages_read_engagement", "pages_read_user_content"),
    ("facebook", "comment"): ("pages_manage_engagement",),
    ("facebook", "moderate"): ("pages_manage_engagement",),
    ("facebook", "message"): ("pages_messaging", "pages_manage_metadata"),
    ("facebook", "publish"): ("pages_manage_posts",),
    ("facebook", "publish_story"): ("pages_manage_posts",),
    ("facebook", "analytics"): ("read_insights",),
    ("youtube", "read_comments"): ("youtube.force-ssl",),     # readonly is refused: 403 insufficient scopes
    ("youtube", "comment"): ("youtube.force-ssl",),
    ("youtube", "moderate"): ("youtube.force-ssl",),
    ("youtube", "publish"): ("youtube.upload",),
    ("youtube", "analytics"): ("yt-analytics.readonly",),
}
GROUP = {"instagram": "meta", "facebook": "meta", "meta": "meta", "youtube": "youtube"}
# a Google scope that holds another: force-ssl reads as well as writes; plain "youtube" is the whole account
IMPLIES = {"youtube.force-ssl": ("youtube.readonly",),
           "youtube": ("youtube.readonly", "youtube.force-ssl", "youtube.upload")}
# the need behind each outward kind (outward's tripwire marks the need its kind was refused for)
NEED_OF_KIND = {"first_comment": "comment", "reply_public": "comment", "reply_private": "message", "dm": "message",
                "hide": "moderate", "unhide": "moderate", "yt_moderate": "moderate", "like": "moderate", "story": "publish_story",
                "card_post": "publish", "post": "publish", "read": "read_comments"}
REFRESH_EVERY = timedelta(hours=24)
WHATS = ("scopes", "ig-comments", "fb-comments", "yt-comments", "messages", "publish-limit", "yt-analytics")
# what a probe tries, as (platform, need) pairs: a refusal for a permission marks that need missing
PROBE_NEEDS = {"ig-comments": (("instagram", "read_comments"),), "fb-comments": (("facebook", "read_comments"),),
               "yt-comments": (("youtube", "read_comments"),),
               "messages": (("instagram", "message"), ("facebook", "message")),
               "publish-limit": (("instagram", "publish"),), "yt-analytics": (("youtube", "analytics"),)}
COMMENT_PROBES = ("ig-comments", "fb-comments", "yt-comments")
PROBE_POSTS = 50        # the comments probes look this far back for a post that has comments to read


# ------------------------------------------------------------------------------------------------------ the file
def _slug(acct) -> str:
    return str(getattr(acct, "slug", acct) or "")


def file_path(acct):
    """state/<slug>/capabilities.json (at call time)."""
    return store.path(_slug(acct), "capabilities.json")


def _read(acct) -> dict:
    d = store.read(file_path(acct), {})
    return d if isinstance(d, dict) else {}


def _now(now: Optional[datetime] = None) -> datetime:
    return (now or datetime.now(timezone.utc)).astimezone(timezone.utc).replace(microsecond=0)


def _norm(platform: str, scopes) -> List[str]:
    """Scope names as the NEEDS table writes them: a Google scope URL becomes its last part
    (https://www.googleapis.com/auth/youtube.force-ssl is youtube.force-ssl)."""
    out = set()
    for s in scopes or []:
        s = str(s).strip()
        if not s:
            continue
        if GROUP.get(platform) == "youtube":
            s = s.rstrip("/").rsplit("/", 1)[-1]
        out.add(s)
    return sorted(out)


def save_scopes(acct, platform: str, scopes, source: str, now: Optional[datetime] = None) -> List[str]:
    """Keep the permission names a sign-in has ("meta" or "youtube"; instagram and facebook are meta). A connect is a
    new sign-in, so it also clears what a refusal marked missing on that platform; a daily refresh does not (Meta can
    refuse what the token lists)."""
    group = GROUP.get(platform)
    if group is None:
        raise SystemExit(f"{platform}: not a platform with permissions here (meta, youtube)")
    names = _norm(group, scopes)
    with store.edit(file_path(acct), dict) as d:
        d[group] = {"scopes": names, "at": _now(now).isoformat(), "source": str(source)}
        if source == "connect":
            miss = d.get("missing") if isinstance(d.get("missing"), dict) else {}
            d["missing"] = {k: v for k, v in miss.items() if GROUP.get(k.partition("|")[0]) != group}
    return names


def scopes(acct, platform: str) -> Optional[List[str]]:
    """The permission names the platform's sign-in has, or None when they were never read here."""
    group = GROUP.get(platform)
    entry = _read(acct).get(group) if group else None
    got = entry.get("scopes") if isinstance(entry, dict) else None
    return [str(x) for x in got] if isinstance(got, list) else None


def _have(names: List[str]) -> set:
    out = set(names)
    for n in names:
        out.update(IMPLIES.get(n, ()))
    return out


def missing(acct, platform: str, need: str) -> List[str]:
    """What the account lacks for this need on this platform: [] when it has it all. `acct` is an Account or a slug.
    Never read yet: one line saying how to read them. A need the platform has not (YouTube messages): one line too,
    so it never counts as present."""
    slug = _slug(acct)
    want = NEEDS.get((platform, need))
    if want is None:
        return [f"({platform} has no {need.replace('_', ' ')} here)"]
    have = scopes(slug, platform)
    if have is None:
        return [f"(not read yet: factory.py social probe {slug} scopes)"]
    got = _have(have)
    out = [s for s in want if s not in got]
    miss = _read(slug).get("missing")
    mark = miss.get(f"{platform}|{need}") if isinstance(miss, dict) else None
    if isinstance(mark, dict):
        when = str(mark.get("at") or "")[:10]
        out.append(f"({platform} refused it{' on ' + when if when else ''}: connect {GROUP[platform]} again)")
    return out


def mark_missing(acct, platform: str, need: str, why: str = "", now: Optional[datetime] = None) -> None:
    """A refusal for a permission (Meta code 10 or 200-299, or a probe's): that need waits for the next connect.
    `need` may also be an outward kind (first_comment is a comment)."""
    need = NEED_OF_KIND.get(need, need)
    with store.edit(file_path(acct), dict) as d:
        miss = d.get("missing") if isinstance(d.get("missing"), dict) else {}
        miss[f"{platform}|{need}"] = {"at": _now(now).isoformat(), "why": str(why)[:300]}
        d["missing"] = miss


def _clear_missing(acct, platform: str, need: str) -> None:
    with store.edit(file_path(acct), dict) as d:
        miss = d.get("missing") if isinstance(d.get("missing"), dict) else {}
        miss.pop(f"{platform}|{need}", None)
        d["missing"] = miss


def put_probe(acct, name: str, value, now: Optional[datetime] = None) -> None:
    """Keep what a probe found (the cards' Instagram story test, a publish limit read ...)."""
    with store.edit(file_path(acct), dict) as d:
        probes = d.get("probes") if isinstance(d.get("probes"), dict) else {}
        probes[str(name)] = {"value": value, "at": _now(now).isoformat()}
        d["probes"] = probes


def probe_result(acct, name: str) -> Optional[dict]:
    """{"value", "at"} of the last probe of that name, or None when it never ran here."""
    probes = _read(acct).get("probes")
    got = probes.get(name) if isinstance(probes, dict) else None
    return dict(got) if isinstance(got, dict) else None


# ------------------------------------------------------------------------------------------------------ the network
def _err(e: BaseException) -> str:
    return str(e) or type(e).__name__


def _changes(label: str, old: Optional[List[str]], new: List[str]) -> List[str]:
    if old is None or set(old) == set(new):
        return []
    gained, lost = sorted(set(new) - set(old)), sorted(set(old) - set(new))
    return [f"{label} permissions changed:" + (f" now has {', '.join(gained)}" if gained else "")
            + (";" if gained and lost else "") + (f" lost {', '.join(lost)}" if lost else "")]


def _refresh(cfg: C.Config, acct, now: datetime) -> Tuple[List[str], List[str]]:
    """(what it read, what changed) for both sign-ins. Each platform on its own: one that fails leaves the other."""
    from . import direct as D
    from . import graph as G
    from .stats import NotConnected
    name = getattr(acct, "name", _slug(acct))
    lines: List[str] = []
    changed: List[str] = []
    try:
        keys = G.meta_keys(acct)
        dbg = (G.gget(acct, "debug_token", {"input_token": keys.page_token}) or {}).get("data") or {}
        if not dbg.get("is_valid"):
            lines.append(f"{name} Meta: the Page token is not valid: run factory.py connect meta {_slug(acct)}")
            changed += lines[-1:]
        else:
            granular = [g for g in dbg.get("granular_scopes") or [] if isinstance(g, dict) and g.get("scope")]
            granted = set(dbg.get("scopes") or []) | {str(g["scope"]) for g in granular}
            old = scopes(acct, "meta")
            new = save_scopes(acct, "meta", sorted(granted), "refresh", now)
            lines.append(f"{name} Meta: {len(new)} permissions: {', '.join(new) or 'none'}")
            changed += _changes(f"{name} Meta", old, new)
    except NotConnected:
        lines.append(f"{name} Meta: not connected")
    except Exception as e:  # noqa: BLE001  (one platform never stops the other)
        lines.append(f"{name} Meta: could not read the permissions: {_err(e)}"[:400])
    try:
        p = D._tok_path(acct, "youtube_token")
        if not p.exists():
            lines.append(f"{name} YouTube: not connected")
        else:
            said = str(json.loads(p.read_text()).get("scope") or "").split()
            if not said:
                lines.append(f"{name} YouTube: the sign-in does not say its permissions (connect youtube again)")
            else:
                old = scopes(acct, "youtube")
                new = save_scopes(acct, "youtube", said, "refresh", now)
                lines.append(f"{name} YouTube: {', '.join(new)}")
                changed += _changes(f"{name} YouTube", old, new)
    except Exception as e:  # noqa: BLE001
        lines.append(f"{name} YouTube: could not read the permissions: {_err(e)}"[:400])
    put_probe(acct, "refresh", {"lines": lines}, now)
    return lines, changed


def refresh(cfg: C.Config, acct) -> List[str]:
    """Read both sign-ins' permissions again (Meta: debug_token with the Page token; YouTube: the token's scope
    string) and keep them. Reaches the network."""
    return _refresh(cfg, acct, _now())[0]


def refresh_due(cfg: C.Config, now: Optional[datetime] = None, quiet: bool = True) -> List[str]:
    """The social tick's step: refresh each owner account with a social part on, once a day. Nothing from a test, an
    offline run or a copy of the state (net.why_not). Quiet, it says only what changed."""
    from . import net
    if net.why_not():
        return []
    now = _now(now)
    out: List[str] = []
    for acct in cfg.accounts.values():
        if SW._is_client(acct) or not SW.any_on(acct):
            continue
        last = store._when((probe_result(acct, "refresh") or {}).get("at"))
        if last is not None and now - last < REFRESH_EVERY:
            continue
        lines, changed = _refresh(cfg, acct, now)
        out += changed if quiet else lines
    return out


def _probe_one(cfg: C.Config, acct, what: str) -> Tuple[bool, str, dict]:
    """(ok, words, extra fields) of one probe. Raises what the platform raised."""
    from . import direct as D
    from . import graph as G
    if what in ("ig-comments", "publish-limit"):
        k = G.meta_keys(acct)
        if not k.ig_user_id:
            return False, "no Instagram account is linked to the Page", {}
        if what == "publish-limit":
            row = ((G.gget(acct, f"{k.ig_user_id}/content_publishing_limit", {"fields": "config,quota_usage"})
                    .get("data") or [{}])[0]) or {}
            total = (row.get("config") or {}).get("quota_total")
            used = row.get("quota_usage")
            return True, f"Instagram publishes: {used} of {total} used in the last 24 hours", \
                {"quota_total": total, "quota_usage": used}
        # Instagram answers a token without instagram_manage_comments with the comments' ids and leaves out the words
        # and names: so ask for them, on a post that has comments, and a stripped answer is a refusal
        media = G.gget(acct, f"{k.ig_user_id}/media", {"fields": "id,comments_count", "limit": PROBE_POSTS}
                       ).get("data") or []
        media = [m for m in media if isinstance(m, dict) and m.get("id")]
        if not media:
            return False, "Instagram has no post to read comments from yet", {"proven": False}
        return _read_comments(acct, "instagram", media, "comments_count", "id,text,username,timestamp")
    if what == "fb-comments":
        # Facebook reads a reel with no comments for any token and refuses one that has some without
        # pages_read_user_content ((#200) Missing Permissions): so read a reel that has comments
        k = G.meta_keys(acct)
        q = {"fields": "id,comments.summary(true).limit(0)", "limit": PROBE_POSTS}
        vids = [v for v in G.gget(acct, f"{k.page_id}/video_reels", q).get("data") or []
                if isinstance(v, dict) and v.get("id")]
        if not vids:
            return False, "the Page has no reel to read comments from yet", {"proven": False}
        for v in vids:
            summ = (v.get("comments") or {}).get("summary") if isinstance(v.get("comments"), dict) else None
            v["_count"] = (summ or {}).get("total_count") if isinstance(summ, dict) else None
        return _read_comments(acct, "facebook", vids, "_count", "id,from{id,name},message,created_time")
    if what == "messages":
        k = G.meta_keys(acct)
        said = []
        for plat in ("instagram", "messenger"):
            got = G.gget(acct, f"{k.page_id}/conversations", {"platform": plat, "fields": "id", "limit": 1})
            said.append(f"{'Instagram' if plat == 'instagram' else 'Facebook'} {len(got.get('data') or [])}")
        return True, "messages are readable (conversations on the first page: " + ", ".join(said) + ")", {}
    if what == "yt-comments":
        cid = str(D._load(acct, "youtube_token", f"youtube {_slug(acct)} --client client.json").get("channel_id")
                  or "")
        if not cid:
            items = G.yt_get(acct, "channels", {"part": "id", "mine": "true"}, cfg).get("items") or []
            cid = str((items[0] if items else {}).get("id") or "")
        if not cid:
            return False, "the YouTube sign-in shows no channel", {}
        got = G.yt_get(acct, "commentThreads", {"part": "id", "allThreadsRelatedToChannelId": cid, "maxResults": 5},
                       cfg).get("items") or []
        return True, f"YouTube comments are readable ({len(got)} on the first page)", {}
    if what == "yt-analytics":
        end = date.today()
        got = G.yta_get(acct, {"ids": "channel==MINE", "startDate": (end - timedelta(days=7)).isoformat(),
                               "endDate": end.isoformat(), "metrics": "views"}, cfg)
        rows = got.get("rows") or []
        views = rows[0][0] if rows and rows[0] else 0
        return True, f"YouTube Analytics is readable ({views} views in the last 7 days)", {}
    raise SystemExit(f"{what}: not a probe; the probes are: {', '.join(WHATS)}")


def _read_comments(acct, platform: str, posts: List[dict], count_field: str, fields: str) -> Tuple[bool, str, dict]:
    """A comments probe's read: the newest post that has comments (the newest of all when none has), its first page
    with the words and names asked for. Words left out raise graph.Withheld (a refusal for the permission); no comment
    to read proves nothing, and says so."""
    from . import graph as G
    words, thing = {"instagram": ("Instagram", "post"), "facebook": ("Facebook", "reel")}[platform]
    has = [p for p in posts if isinstance(p.get(count_field), int) and p[count_field] > 0]
    post = (has or posts)[0]
    n = post.get(count_field) if isinstance(post.get(count_field), int) else None
    q = {"fields": fields, "limit": 5}
    if platform == "facebook":
        q["filter"] = "stream"
    got = [r for r in G.gget(acct, f"{post['id']}/comments", q).get("data") or [] if isinstance(r, dict)]
    err = G.withheld(platform, got)
    if err is not None:
        raise err
    extra = {"proven": bool(got), "post": str(post["id"]), "comments": n}
    if got:
        return True, (f"{words} comments are readable ({len(got)} read with their words and names, on a {thing} "
                      f"with {n if n is not None else 'some'})"), extra
    if not any(isinstance(p.get(count_field), int) for p in posts):
        return False, f"not proven: {words} gave no comment counts, and the newest {thing} read 0", extra
    if not has:
        return False, (f"not proven: none of the newest {len(posts)} {thing}s has a comment to read yet (the newest "
                       f"read 0)"), extra
    return False, f"not proven: {words} counts {n} comments on {thing} {post['id']} but gave none back", extra


def _lacks(acct, what: str) -> Tuple[List[str], bool]:
    """(the permission names the kept sign-in lacks for this probe's needs, whether they were ever read)."""
    out: List[str] = []
    read = True
    for plat, need in PROBE_NEEDS.get(what, ()):
        if scopes(acct, plat) is None:
            read = False
            continue
        out += [m for m in missing(acct, plat, need) if not m.startswith("(") and m not in out]
    return out, read


def probe(cfg: C.Config, acct, what: str) -> List[str]:
    """Ask a platform what this account may do, by hand (factory.py social probe <account> <what>). It reaches the
    network, so the timers never run it. What it found is kept (probe_result); a refusal for a permission marks the
    need missing, and a success clears that mark. A comments probe passes only on comments read with their words
    and names: a read with none to read is "not proven" and clears nothing. A refusal or no proof names what the
    kept sign-in lacks ("missing pages_read_user_content: ...")."""
    from . import direct as D
    from . import graph as G
    from .stats import NotConnected
    if what not in WHATS:
        raise SystemExit(f"{what}: not a probe; the probes are: {', '.join(WHATS)}")
    if what == "scopes":
        lines = refresh(cfg, acct)
        for (plat, need) in NEEDS:
            miss = missing(acct, plat, need)
            lines.append(f"  {plat} {need.replace('_', ' ')}: " + ("ok" if not miss else "missing " + ", ".join(miss)))
        return lines
    try:
        ok, words, extra = _probe_one(cfg, acct, what)
        kind = "" if ok or extra.get("proven") is not False else "unproven"
    except NotConnected as e:
        ok, words, extra, kind = False, f"not connected: {e}", {}, "not_connected"
    except D.PlatformError as e:
        info = G.classify(e)
        ok, words, extra, kind = False, f"refused ({info.kind}): {e}"[:400], {"code": info.code}, info.kind
        if info.kind == "perm":
            for plat, need in PROBE_NEEDS.get(what, ()):
                mark_missing(acct, plat, need, str(e))
    except Exception as e:  # noqa: BLE001  (no answer, or refused before the network: said, never a traceback)
        kind = G.classify(e).kind
        ok, words, extra = False, f"could not ask ({kind}): {_err(e)}"[:400], {}
    if what in COMMENT_PROBES and kind in ("perm", "unproven"):   # a no, or no proof: name what the sign-in lacks
        lacks, read = _lacks(acct, what)
        if lacks:
            words = f"missing {', '.join(lacks)}: {words}"
        elif not read:
            words += f" (factory.py social probe {_slug(acct)} scopes names what the sign-in lacks)"
        elif kind == "perm":
            words += " (the sign-in lists every permission named for it: see SETUP-POSTING.md, the comments " \
                     "permissions, for the Page's task)"
        if lacks:
            extra = dict(extra, missing=lacks)
    put_probe(acct, what, dict({"ok": ok, "words": words, "kind": kind}, **extra))
    if ok:
        for plat, need in PROBE_NEEDS.get(what, ()):
            _clear_missing(acct, plat, need)
    return [f"{getattr(acct, 'name', _slug(acct))} {what}: {words}"]


# ------------------------------------------------------------------------------------------------------ doctor
def _wanted(acct) -> Dict[Tuple[str, str], bool]:
    """(platform, need) -> whether a switch that needs it is live (on or testers), for every switch not off."""
    plats = [p for p in (SW.settings(acct).get("engage.platforms") or []) if p in SW.PLATFORMS]
    out: Dict[Tuple[str, str], bool] = {}
    for key, value in SW.modes(acct).items():
        if value == SW.OFF:
            continue
        if key == "engage.read":
            needs = [(p, "read_comments") for p in plats]
        elif key == "engage.messages":
            needs = [(p, "message") for p in plats if p != "youtube"]
        elif key == "replies":
            needs = [(p, "comment") for p in plats]
        elif key == "checkpoints.yt_analytics":
            needs = [("youtube", "analytics")]
        elif key == "checkpoints.extra":
            needs = [("instagram", "analytics"), ("facebook", "analytics")]
        elif key in SW.CHECKLIST:
            needs = SW._needs(acct, key)
        else:
            needs = []
        for pn in needs:
            out[pn] = out.get(pn, False) or value in SW.LIVE
    return out


def _fix(acct, platform: str, need: str) -> str:
    if scopes(acct, platform) is None:
        return f"factory.py social probe {_slug(acct)} scopes"
    if platform == "youtube":
        return f"factory.py connect youtube {_slug(acct)} --client client.json --with comments,analytics"
    return (f"tick it on the system user's token (Business Settings), then factory.py connect meta {_slug(acct)}"
            + ("; messages need Meta's review first" if need == "message" else ""))


# connect youtube --with: each extra and the need it grants (outlets.YT_EXTRA holds the Google scope of each)
YT_WITH = (("comments", "comment"), ("analytics", "analytics"))


def yt_held(acct) -> List[str]:
    """The connect youtube --with extras the saved YouTube sign-in already has. A new connect asks for them again
    (outlets.connect_youtube), so a fix line naming one extra never costs the other."""
    have = scopes(acct, "youtube")
    if have is None:
        return []
    got = _have(have)
    return [w for w, need in YT_WITH if all(s in got for s in NEEDS[("youtube", need)])]


def yt_connect_line(acct, *add: str, client: str = "client.json") -> str:
    """The connect youtube command for a fix line: the extras asked for plus the ones the account already has, in
    one --with (two connects with one --with each would each ask Google for one)."""
    want = set(add) | set(yt_held(acct))
    ws = [w for w, _ in YT_WITH if w in want]
    return (f"factory.py connect youtube {_slug(acct)} --client {client}"
            + (f" --with {','.join(ws)}" if ws else ""))


def doctor(cfg: C.Config, now=None) -> List[Tuple[Optional[bool], str, str]]:
    """For each owner account with a social part on: what its switches need that its sign-ins lack (NO for a part
    that is on, "--" waits for, in practice), or one ok line. Reads the file only; nothing with every part off."""
    out: List[Tuple[Optional[bool], str, str]] = []
    for a in cfg.accounts.values():
        try:
            if SW._is_client(a) or not SW.any_on(a):
                continue
            wanted = _wanted(a)
            if not wanted:
                continue
            rows = []
            for (plat, need), live in sorted(wanted.items()):
                miss = missing(a, plat, need)
                if not miss:
                    continue
                words = f"{a.name} {plat} {need.replace('_', ' ')}: waits for {', '.join(miss)}"
                rows.append((False, words, _fix(a, plat, need)) if live else (None, words, ""))
            if rows:
                out += rows
            else:
                d = _read(a)
                when = ", ".join(f"{g} read {str((d.get(g) or {}).get('at') or '')[:10]}" for g in ("meta", "youtube")
                                 if isinstance(d.get(g), dict))
                out.append((True, f"{a.name}: every permission its social parts need is there ({when})", ""))
        except Exception as e:  # noqa: BLE001  (the doctor never stops over one account)
            out.append((False, f"{getattr(a, 'name', a)} permissions: could not read ({type(e).__name__}: {e})",
                        f"look at state/{_slug(a)}/capabilities.json"))
    return out
