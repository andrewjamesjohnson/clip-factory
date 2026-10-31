"""Direct to each platform's own API: post a signed clip, and read every recent video's numbers back.
No scheduler in the middle, nothing to pay. Sign-ins are the owner's, once per account and platform:

    factory.py connect youtube   <account> --client client.json     Google OAuth (upload, read, watch time)
    factory.py connect meta      <account>                          Facebook Page + its linked Instagram account
    factory.py connect tiktok    <account> --client-key KEY         TikTok Login Kit for Desktop (PKCE)
    factory.py connect upload-post <account> [--profile NAME]       TikTok through Upload-Post (what posts today)

Tokens live in ~/.config/clip-factory/<account>/ (mode 600). Secrets are typed at a hidden prompt, never passed on
the command line, never written into the repo.

What each platform allows (checked against their docs, 2026-10-16):
  YouTube    uploads from an unverified Google project are no longer locked private. videos.insert has its own
             bucket: 100 calls a day per Google project, and both channels share one project (politics), so
             the 100 is shared. That quota has never been the limit: each channel's own daily upload cap is
             (uploadLimitExceeded, about 10 a day until the channel is phone-verified; outlets.yt_hold).
             Reading your own channel's numbers needs no audit. Watch time and retention come from YouTube
             Analytics (yt-analytics.readonly, asked since 2026-10-19; a sign-in from before has no watch time).
  Instagram  Reels from a local file (resumable upload, graph.facebook.com, Page token). Needs a Business or
  Facebook   Creator IG account linked to the Page. While the Meta app is in Development mode, its posts are
             visible only to people with a role on the app: switch the app to Live before turning the outlet on.
  TikTok     until the app passes TikTok's audit, posts are SELF_ONLY, the account must be private, five users a day.
             So the TikTok outlet refuses to run until `api_app_audited` is true. Reading numbers (video.list)
             uses Login Kit; whether a sandbox app may read is not documented, so `connect` tells you.
             TikTok's own guidelines refuse that audit to "a utility tool to help upload contents to the
             account(s) you or your team manages" (checked 2026-10-17), which is what this is. So TikTok goes
             through Upload-Post (`"via": "upload-post"`), a posting service whose TikTok app passed that audit:
             each brand is a profile there with its TikTok connected, and one API key reaches them all.
"""
from __future__ import annotations

import base64
import contextlib
import fcntl
import re
import getpass
import hashlib
import json
import math
import os
import secrets
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, List, Optional

from . import config as C
from .stats import NotConnected

TOKENS = Path(os.path.expanduser("~/.config/clip-factory"))
GRAPH = "https://graph.facebook.com/v25.0"     # v25 is supported until 2028; v26 is current
RUPLOAD_VER = "v25.0"
TT = "https://open.tiktokapis.com/v2"


class PlatformError(Exception):
    """A platform said no, or did not answer. The message is what it has always been (the poster and the tests read
    it); what the platform said in numbers rides along as attributes, None when it did not say: the HTTP status, the
    host, Meta's numeric error code, subcode, type, is_transient and fbtrace_id, and Google's errors[0].reason. The
    social guard (graph.classify) reads these, never the words."""

    INFO = ("status", "host", "meta_code", "meta_subcode", "meta_type", "transient", "trace", "reason")

    def __init__(self, *args, **info):
        super().__init__(*args)
        for k in self.INFO:
            setattr(self, k, info.get(k))


class Refused(PlatformError):
    """A no that trying again cannot change (outside the length band, not connected, TikTok not audited): the poster
    gives up on that platform at once instead of using its three tries."""


class Pending(PlatformError):
    """The platform has the post and is still working on it: not a failure and not one of the tries. The next run
    looks again (sending it again could post it twice)."""


class Stopped(PlatformError):
    """The slot was pulled (or signed again) while this post was on its way, and the step that cannot be undone had
    not been taken yet: nothing went out, nothing is recorded, and it is not one of the tries."""


class LookupFailed(PlatformError):
    """What an earlier try left on the platform could not be checked (an error that is not "it does not exist", a
    timeout). One of the tries; after the last one a person looks, and Try again sends it fresh (2026-10-17 review:
    a lookup that always failed kept the slot from ever posting)."""


class Unreadable(PlatformError):
    """This Mac could not read a clip's length (ffprobe gave no answer in LENGTH_WAIT, did not start, or printed no
    length). Not a Refused: the poster checks the clip's bytes against the signature first, and ffprobe measured those
    same bytes at collect, so it is this Mac, not the clip. One of the tries, on the long spacing (poster.STALLS).
    2026-10-30 06:39-07:15: ffprobe hung on three accounts' clips while the poster could read their bytes, each slot
    gave up at once, and Gaming's 07:00 posted nowhere until Try again at 15:18."""


LENGTH_WAIT = 60     # seconds ffprobe gets to say a clip's length before in_band calls it Unreadable


def _mark(slot: dict, upd: dict) -> None:
    """Tell the poster what the platform holds now. It is written on the board at once, so a crash or a lost answer
    after this point is looked up on the next run instead of being posted a second time."""
    f = slot.get("_mark")
    if f:
        f(upd)


def _still_signed(slot: dict, what: str) -> None:
    """Right before a step that cannot be undone (Facebook's finish, Instagram's publish): stop if the slot was pulled
    or signed again meanwhile (the 2026-10-17 audit: a Pull during a post still let it go public)."""
    f = slot.get("_still")
    if f is not None and not f():
        raise Stopped(f"not posted: it was pulled or signed again while {what}")


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _gone(e: Exception) -> bool:
    """Meta's answer for an id it does not have (deleted, never made). Meta's usual sentence for that also says "or
    cannot be loaded due to missing permissions": then it is not known, a person looks (LookupFailed), never a second
    post on a guess (2026-10-17 second review)."""
    return "does not exist" in str(e) and "missing permission" not in str(e)


def _sent(slot: dict, plat: str) -> dict:
    """The hashes of the file and the words this try sends, written down with it: a reel an earlier signature's try
    made is then known for the version it is (2026-10-17 review)."""
    from . import poster as P
    return P._shas(slot, plat)


def _redact(text: str) -> str:
    """Never echo a key: Meta repeats a bad token back inside its error message."""
    return re.sub(r"(EAA|ya29\.|act\.|eyJ)[A-Za-z0-9_\-\.]{8,}", "[a key, hidden]", str(text))


def _one_token(raw: str) -> str:
    """A paste pressed twice gives the key twice: keep the first copy."""
    raw = "".join(raw.split())
    i = raw.find("EAA", 3)
    return raw[:i] if raw.startswith("EAA") and i > 0 else raw


# ------------------------------------------------------------------------------------------------ plumbing
_LAST = threading.local()       # this thread's last usage headers (the poster and the social sender read them back)
_USAGE_HEADERS = (("X-Business-Use-Case-Usage", "buc"), ("X-App-Usage", "app"), ("X-Page-Usage", "page"))


def last_usage() -> dict:
    """What the last platform answer in this thread said about its rate limits: {} when it said nothing. Keys, each
    only when its header came: host, buc_pct and regain_min (X-Business-Use-Case-Usage: the highest of call_count,
    total_cputime and total_time, and of estimated_time_to_regain_access, over every entry), app_pct (X-App-Usage),
    page_pct (X-Page-Usage), retry_after (seconds)."""
    got = getattr(_LAST, "usage", None)
    return dict(got) if isinstance(got, dict) else {}


def _header(headers, name: str):
    """One header's value from an HTTPMessage, a dict (any case) or None."""
    if headers is None:
        return None
    try:
        v = headers.get(name)
    except Exception:  # noqa: BLE001
        v = None
    if v is None and isinstance(headers, dict):
        low = name.lower()
        v = next((x for k, x in headers.items() if str(k).lower() == low), None)
    return v


def _pct(entry) -> Optional[float]:
    if not isinstance(entry, dict):
        return None
    vals = [entry.get(k) for k in ("call_count", "total_cputime", "total_time")]
    nums = [float(x) for x in vals if isinstance(x, (int, float)) and not isinstance(x, bool)]
    return max(nums) if nums else None


def _note_usage(host: str, headers) -> None:
    """Keep what an answer's headers said about Meta's rate limits (and Retry-After) for last_usage(). Never raises:
    a header that is missing, not JSON or of another shape is simply not there."""
    out: dict = {}
    try:
        for name, key in _USAGE_HEADERS:
            raw = _header(headers, name)
            if not raw:
                continue
            try:
                data = json.loads(raw)
            except (TypeError, ValueError):
                continue
            if key == "buc":
                entries = []
                for v in (data.values() if isinstance(data, dict) else []):
                    entries += [e for e in (v if isinstance(v, list) else [v]) if isinstance(e, dict)]
                pcts = [p for p in (_pct(e) for e in entries) if p is not None]
                regain = [float(e["estimated_time_to_regain_access"]) for e in entries
                          if isinstance(e.get("estimated_time_to_regain_access"), (int, float))
                          and not isinstance(e.get("estimated_time_to_regain_access"), bool)]
                if pcts:
                    out["buc_pct"] = max(pcts)
                if regain:
                    out["regain_min"] = max(regain)
            else:
                p = _pct(data)
                if p is not None:
                    out[f"{key}_pct"] = p
        ra = _header(headers, "Retry-After")
        if ra is not None:
            try:
                out["retry_after"] = int(str(ra).strip())
            except ValueError:
                pass
        if out:
            out["host"] = str(host or "")
    except Exception:  # noqa: BLE001  (a header never breaks a call)
        out = {}
    _LAST.usage = out


def _http(method: str, url: str, params: Optional[dict] = None, form: Optional[dict] = None,
          body: Optional[dict] = None, data: Optional[bytes] = None, headers: Optional[dict] = None,
          timeout: int = 120) -> dict:
    if params:
        url += ("&" if "?" in url else "?") + urllib.parse.urlencode(params)
    h = dict(headers or {})
    payload = data
    if form is not None:
        payload = urllib.parse.urlencode(form).encode()
        h.setdefault("Content-Type", "application/x-www-form-urlencoded")
    elif body is not None:
        payload = json.dumps(body).encode()
        h.setdefault("Content-Type", "application/json; charset=UTF-8")
    req = urllib.request.Request(url, data=payload, method=method, headers=h)
    from . import net
    host = urllib.parse.urlparse(url).netloc
    net.guard(f"{method} {host}")
    _LAST.usage = {}
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read()
            _note_usage(host, getattr(r, "headers", None))
    except urllib.error.HTTPError as e:
        _note_usage(host, getattr(e, "headers", None))
        raw = e.read()
        msg = raw[:300].decode(errors="ignore")
        info: dict = {"status": e.code, "host": host}
        try:
            err = json.loads(raw)
            e2 = err.get("error")
            msg = (e2.get("message") or e2.get("code") if isinstance(e2, dict) else e2) or err.get("error_description") \
                or err.get("message") or msg
            if isinstance(e2, dict) and isinstance(e2.get("code"), str) and e2["code"] not in str(msg):
                msg = f"{msg} [{e2['code']}]"            # TikTok names its refusals (Meta's codes are numbers)
            if err.get("error_code"):            # Upload-Post names its refusals; the poster reads the name
                msg = f"{msg} [{err['error_code']}]"
            info.update(_error_info(host, e2))
        except Exception:  # noqa: BLE001
            pass
        raise PlatformError(f"HTTP {e.code} from {host}: {_redact(msg)}", **info) from None
    return json.loads(raw) if raw.strip() else {}


def _code_num(v) -> Optional[int]:
    """An error body's number (Meta's code, error_subcode): an int only, never a code given as text. Not _num, the
    watch-time reader's float parser further down (one name for both made "1" a code)."""
    return v if isinstance(v, int) and not isinstance(v, bool) else None


def _error_info(host: str, e2) -> dict:
    """The numbers in an error body, for PlatformError's attributes: Meta's code, error_subcode, type, is_transient
    and fbtrace_id (only when its code is a number: TikTok's are names), Google's errors[0].reason."""
    if not isinstance(e2, dict):
        return {}
    h = host.lower()
    if h.endswith("facebook.com") or h.endswith("instagram.com"):
        if _code_num(e2.get("code")) is None:
            return {}
        t = e2.get("is_transient")
        return {"meta_code": _code_num(e2.get("code")), "meta_subcode": _code_num(e2.get("error_subcode")),
                "meta_type": str(e2["type"]) if e2.get("type") else None,
                "transient": t if isinstance(t, bool) else None,
                "trace": str(e2["fbtrace_id"]) if e2.get("fbtrace_id") else None}
    if h.endswith("googleapis.com"):
        errs = e2.get("errors")
        if isinstance(errs, list) and errs and isinstance(errs[0], dict) and errs[0].get("reason"):
            return {"reason": str(errs[0]["reason"])}
    return {}


def _tok_path(acct: C.Account, name: str) -> Path:
    return TOKENS / acct.slug / f"{name}.json"


def _load(acct: C.Account, name: str, how: str) -> dict:
    p = _tok_path(acct, name)
    if not p.exists():
        verb, _, rest = how.partition(" ")
        raise NotConnected(f"run: factory.py connect {verb} {acct.slug} {rest}".rstrip())
    return json.loads(p.read_text())


def _save(acct: C.Account, name: str, data: dict) -> Path:
    p = _tok_path(acct, name)
    write_private(p, json.dumps(data, indent=1))
    return p


def write_private(path: Path, text: str) -> None:
    """Write a key file whole or not at all: a temp file beside it (mode 600 from the start), then a rename. Several
    processes read these files (the poster, the stats read, the page); a rewrite in place could hand one of them half
    a file, which then counts as a failed post (2026-10-17 audit)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    fd = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        with os.fdopen(fd, "w") as f:
            f.write(text)
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def held_by(root: Path, slug: str, name: str, key: str, value) -> str:
    """The slug of another account whose sign-in file (<root>/<slug>/<name>.json) already holds this id, '' when none.
    connect refuses then (review 2026-10-19): a Google login or a system user can reach more than one GTA account's
    channel or Page, so one wrong pick at a prompt would sign Stunts in as Gaming and post its clips there.
    Only the id is compared; nothing of the file is printed."""
    if not value:
        return ""
    for p in sorted(Path(root).glob(f"*/{name}.json")):
        if p.parent.name == slug:
            continue
        try:
            d = json.loads(p.read_text())
        except (OSError, ValueError):
            continue
        if isinstance(d, dict) and str(d.get(key) or "") == str(value):
            return p.parent.name
    return ""


@contextlib.contextmanager
def token_lock(path: Path):
    """One refresh of a sign-in at a time, across processes. TikTok gives a new refresh token on each refresh, so two
    refreshes at once (two tabs, the page and Post to TikTok now) could save one that is already dead."""
    lock = path.with_name(path.name + ".lock")
    lock.parent.mkdir(parents=True, exist_ok=True)
    with open(lock, "a") as lf:
        fcntl.flock(lf, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lf, fcntl.LOCK_UN)


def _iso(ts) -> str:
    if isinstance(ts, (int, float)):
        return datetime.fromtimestamp(ts, timezone.utc).isoformat()
    return datetime.fromisoformat(str(ts).replace("Z", "+00:00").replace("+0000", "+00:00")).isoformat()


def _upload_bytes(url: str, token: str, clip: Path) -> dict:
    data = clip.read_bytes()
    return _http("POST", url, data=data, timeout=1800, headers={
        "Authorization": f"OAuth {token}", "offset": "0", "file_size": str(len(data))})


# ------------------------------------------------------------------------------------------------ Meta
def connect_meta(acct: C.Account, cfg: Optional[C.Config] = None) -> str:
    """Paste a system user token from Business Settings (your app, expiry Never, these permissions). Never the
    Graph API Explorer: its popup failed twice (SETUP-POSTING.md section 2). A short-lived token still works: it
    is swapped for a long one. The Page's token does not expire, and its linked Instagram account is found."""
    print("In business.facebook.com/settings -> System users -> the brand's poster -> Generate token: your app,\n"
          "expiry Never, tick these permissions, Generate, Copy (never the Graph API Explorer):\n"
          "  pages_show_list, pages_read_engagement, pages_manage_posts, pages_manage_engagement, read_insights,\n"
          "  instagram_basic, instagram_content_publish, instagram_manage_insights, business_management\n"
          "for comments (Clip Review's Inbox and first comments), tick too: instagram_manage_comments, "
          "pages_read_user_content\n"
          "for messages, only after Meta's review: instagram_manage_messages, pages_messaging, pages_manage_metadata\n")
    short = _one_token(getpass.getpass("Access token (hidden, press Command+V ONCE, then Enter): "))
    if not short.startswith("EAA"):
        raise SystemExit("that does not look like a Meta access token (they start with EAA); copy it again and rerun")
    me = _http("GET", f"{GRAPH}/debug_token", params={"input_token": short, "access_token": short}).get("data", {})
    if not me.get("is_valid"):
        raise SystemExit("Meta says that token is not valid (revoked or expired); generate a new one and rerun")
    app_id, app_secret = str(me.get("app_id", "")), ""
    if me.get("expires_at"):            # a short-lived Explorer token: swap it for a long one (needs the secret)
        app_secret = getpass.getpass("App secret (hidden): ").strip()
        long = _http("GET", f"{GRAPH}/oauth/access_token", params={
            "grant_type": "fb_exchange_token", "client_id": app_id, "client_secret": app_secret, "fb_exchange_token": short})
    else:                               # a system user token from Business Settings never expires: use it as it is
        long = {"access_token": short}
    print(f"  app {app_id}, token never expires" if not me.get("expires_at") else f"  app {app_id}, swapped for a long token")
    fields = "id,name,access_token,instagram_business_account{id,username}"
    pages = _http("GET", f"{GRAPH}/me/accounts", params={
        "fields": fields, "access_token": long["access_token"], "limit": 100}).get("data", [])
    if not pages:
        # Facebook Login for Business (a configuration) can leave /me/accounts empty: the Pages the person picked
        # are listed as target_ids on the token's granular scopes instead. Ask for each of those Pages directly.
        dbg = _http("GET", f"{GRAPH}/debug_token", params={
            "input_token": long["access_token"],
            "access_token": f"{app_id}|{app_secret}" if app_secret else long["access_token"]}).get("data", {})
        granted = sorted({s["scope"] for s in dbg.get("granular_scopes", [])} | set(dbg.get("scopes", [])))
        ids = []
        for s in dbg.get("granular_scopes", []):
            if s.get("scope", "").startswith("pages_"):
                ids += [t for t in s.get("target_ids", []) if t not in ids]
        for pid in ids:
            try:
                pages.append(_http("GET", f"{GRAPH}/{pid}", params={"fields": fields, "access_token": long["access_token"]}))
            except PlatformError as e:
                print(f"  could not open Page {pid}: {e}")
        pages = [p for p in pages if p.get("access_token")]
        if not pages:
            raise SystemExit("that sign-in gives this app no Facebook Pages.\n"
                             f"  permissions granted: {', '.join(granted) or 'none'}\n"
                             f"  Pages picked in the sign-in: {len(ids)}\n"
                             "  Generate the token again and tick both Pages (and both Instagram accounts) in the popup.")
    for i, p in enumerate(pages, 1):
        ig = (p.get("instagram_business_account") or {}).get("username")
        print(f"  {i}. {p['name']}" + (f"  (Instagram @{ig})" if ig else "  (no Instagram account linked)"))
    pick = pages[int(input(f"Which Page is {acct.name}? number: ").strip()) - 1]
    ig = pick.get("instagram_business_account") or _page_instagram(pick["id"], pick["access_token"])
    why = meta_refusal(acct, pick, ig, cfg)
    if why:
        raise SystemExit(f"nothing saved: {why}")
    out = _save(acct, "meta", {"page_id": pick["id"], "page_name": pick["name"], "page_token": pick["access_token"],
                               "ig_user_id": ig.get("id", ""), "ig_username": ig.get("username", ""), "app_id": app_id})
    try:                                # what the token may do, for the social parts (no secret: only the names)
        from . import capabilities as CAP
        granted = set(me.get("scopes") or []) | {str(g.get("scope")) for g in me.get("granular_scopes") or []
                                                 if isinstance(g, dict) and g.get("scope")}
        CAP.save_scopes(acct, "meta", sorted(granted), "connect")
    except (Exception, SystemExit):  # noqa: BLE001  (a sign-in is never lost over its permission list)
        pass
    return (f"connected {acct.name} to Facebook Page {pick['name']}"
            + (f" and Instagram @{ig['username']}" if ig.get("username") else " (no Instagram linked: link it in the Page's settings)")
            + f"; token at {out} (mode 600)" + meta_unlisted(acct, pick, ig))


def meta_refusal(acct: C.Account, page: dict, ig: dict, cfg: Optional[C.Config] = None) -> str:
    """'' when the picked Page and its Instagram are no other account's; else why not. Refused: a Page or an Instagram
    another account's sign-in holds (held_by), and, with cfg, one another account's "handle" or "links" name (its
    facebook_reels link by the Page's id or name, its @handle or instagram_reels link by the Instagram's username,
    in any case). The account's own links count as its own: Stunts' Page 1000000000000001 and @stunts."""
    other = held_by(TOKENS, acct.slug, "meta", "page_id", page.get("id"))
    if other:
        return (f"the Page {page.get('name')} is the one {other} is connected to. Run connect meta {acct.slug} again "
                f"and pick {C.possessive(acct.name)} own Page (give it to the system user first if it is not listed)")
    other = held_by(TOKENS, acct.slug, "meta", "ig_user_id", (ig or {}).get("id"))
    if other:
        return (f"the Instagram @{(ig or {}).get('username')} linked to {page.get('name')} is "
                f"{C.possessive(other)}. Link {C.possessive(acct.name)} own Instagram to its Page, then connect again")
    names = {str(page.get(k) or "").strip().lower() for k in ("id", "username")} - {""}
    user = str((ig or {}).get("username") or "").strip().lstrip("@").lower()
    for o in ((cfg.all_accounts or cfg.accounts) if cfg else {}).values():
        if o.slug == acct.slug:
            continue
        theirs = o.handles()
        if names & theirs:
            theirs_said = C.possessive(o.name)
            return (f"the Page {page.get('name')} is {theirs_said} (config/accounts.json names it in {theirs_said} "
                    f"links). Run connect meta {acct.slug} again and pick {C.possessive(acct.name)} own Page")
        if user and user in theirs:
            return (f"the Instagram @{user} linked to {page.get('name')} is {C.possessive(o.name)} "
                    f"(config/accounts.json). Link {C.possessive(acct.name)} own Instagram to its Page, then connect "
                    f"again")
    return ""


def meta_unlisted(acct: C.Account, page: dict, ig: dict) -> str:
    """What connect adds when the account's own links name another Page or Instagram than the one it saved: said, not
    refused, so a reconnect whose link is written another way (a Page's name for its id) still works. '' when they
    match or the account has no such link."""
    links = (acct.raw or {}).get("links") or {}
    said = []
    fb = C.handles_in([links.get("facebook_reels")])
    if fb and not fb & ({str(page.get(k) or "").strip().lower() for k in ("id", "username")} - {""}):
        said.append(f"links.facebook_reels names {', '.join(sorted(fb))}, not this Page ({page.get('id')})")
    mine = C.handles_in([links.get("instagram_reels")])
    user = str((ig or {}).get("username") or "").strip().lstrip("@").lower()
    if mine and user and user not in mine:
        said.append(f"links.instagram_reels names @{', @'.join(sorted(mine))}, not @{user}")
    return f". CHECK it is {C.possessive(acct.name)}: " + "; ".join(said) if said else ""


def _page_instagram(page_id: str, page_token: str) -> dict:
    """The Page's linked Instagram, asked with the Page's own token (a Business Login user token can hide it)."""
    try:
        r = _http("GET", f"{GRAPH}/{page_id}", params={
            "fields": "instagram_business_account{id,username}", "access_token": page_token})
        return r.get("instagram_business_account") or {}
    except PlatformError:
        return {}


def link_instagram(acct: C.Account, cfg: Optional[C.Config] = None) -> str:
    """Find the connected Page's Instagram again and save it, with no new sign-in. Another account's Instagram (its
    sign-in, or with cfg its handle or links) is refused, as connect meta refuses it."""
    m = _load(acct, "meta", "meta")
    ig = _page_instagram(m["page_id"], m["page_token"])
    if not ig.get("id"):
        return (f"{acct.name}: the Page {m.get('page_name')} shows no linked Instagram. Link it in the Page's settings "
                "(Linked accounts), then run this again.")
    other = held_by(TOKENS, acct.slug, "meta", "ig_user_id", ig["id"])
    if not other and cfg is not None:
        user = str(ig.get("username") or "").strip().lstrip("@").lower()
        other = next((o.name for o in (cfg.all_accounts or cfg.accounts).values()
                      if o.slug != acct.slug and user and user in o.handles()), "")
    if other:
        return (f"{acct.name}: nothing saved: the Instagram @{ig.get('username')} on the Page {m.get('page_name')} is "
                f"{C.possessive(other)}. Link {C.possessive(acct.name)} own Instagram to its Page, then run this "
                f"again.")
    m.update(ig_user_id=ig["id"], ig_username=ig.get("username", ""))
    _save(acct, "meta", m)
    return f"{acct.name}: Facebook Page {m.get('page_name')} and Instagram @{ig.get('username')} are connected"


def in_band(acct: C.Account, clip: Path, plat: Optional[str] = None, cut: bool = False) -> None:
    """
    THE LAST WALL before a real platform: a file outside the account's length band never leaves the Mac.
    2026-10-16: the test suite, run before its keys were walled off, uploaded eight 2-second black dummies
    to the real Gaming channel. Every real upload and post calls this first, so a dummy, a stand-in or a
    truncated render is refused here whatever called it. With `plat`, the band is that platform's own when its
    outlet sets one (outlets.band: the Politics's Instagram cut is 60s or less). A length it cannot read is Unreadable
    (one of the tries, said with why), not a refusal: nothing leaves the Mac either way (2026-10-30).
    """
    import subprocess
    why = ""
    try:
        r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0",
                            str(clip)], capture_output=True, text=True, timeout=LENGTH_WAIT)
        secs = float(r.stdout.strip())
    except subprocess.TimeoutExpired:
        why = f"ffprobe gave no answer in {LENGTH_WAIT}s"
    except (OSError, subprocess.SubprocessError) as e:
        why = f"ffprobe did not run: {e}"
    except ValueError:
        why = "ffprobe printed no length" + (f": {r.stderr.strip()[:120]}" if r.stderr.strip() else "")
    if why:
        raise Unreadable(f"could not read the length of {clip.name} ({why})")
    from . import outlets as O
    if re.match(r"^\d{2}-standin-\d{4}\.mp4$", clip.name):     # makers.make_standin's test clips are 35s: in band
        raise Refused(f"not posted: {clip.name} is a stand-in test clip, never for a real account")
    lo, hi = ((O.band(acct, plat, cut=True) if cut else O.band(acct, plat)) if plat   # cut: the platform's own cut
              else (acct.min_seconds, acct.max_seconds))
    if not lo - 0.05 <= secs <= hi + 0.05:
        raise Refused(f"not posted: {clip.name} is {secs:.1f}s, outside {C.possessive(acct.name)} "
                      f"{lo:.0f}-{hi:.0f}s band" + (f" for {O.NAMES.get(plat, plat)}" if plat else ""))


def ig_post(acct: C.Account, clip: Path, words: dict, slot: dict) -> dict:
    """One signed clip to Instagram Reels. The container is written on the board before it is published, and the reel
    the moment Meta publishes it: a lost answer, a failed read of the link or a crash after that is looked up on the
    next run (slot["_attempt"]), never published a second time (2026-10-17 audit)."""
    in_band(acct, clip, "instagram_reels")
    m = _load(acct, "meta", "meta")
    if not m.get("ig_user_id"):
        raise Refused("no Instagram account is linked to the Page")
    tok, ig = m["page_token"], m["ig_user_id"]
    sig = str((slot.get("signoff") or {}).get("hash") or "")
    att = slot.get("_attempt") or {}
    cid = ""
    cover: dict = {}                       # what the cover did (covers.py), only when this try makes the container
    if att.get("container_id"):
        state, mid = _ig_state(tok, ig, str(att["container_id"]), words, att)
        if state == "published":
            slot["_from_attempt"] = att            # the poster records what that try sent, not the clip as it is now
            return _ig_result(tok, mid, str(att["container_id"]), old=bool(sig and att.get("hash") and att["hash"] != sig))
        if state == "working":
            raise Pending(f"Instagram has this reel ({att['container_id']}) and is still working on it; the next run looks again")
        if state == "ready" and (not att.get("hash") or att.get("hash") == sig):
            cid = str(att["container_id"])         # made and processed, never published: publish that one
    if not cid:
        form = {"media_type": "REELS", "upload_type": "resumable", "caption": words["caption"][:2200],
                "share_to_feed": "true", "access_token": tok}
        try:                            # the signed poll and trial-reel fields, when that part is on (extras.py)
            from . import registry as REG
            f = REG.fn("extras:ig_form")
            more = f(slot, acct) if f is not None else {}
            if isinstance(more, dict):
                form.update(more)
        except Exception:  # noqa: BLE001  (a reel never fails over its extras: it goes as it always went)
            pass
        form.update(_cover_ig(acct, slot, clip, cover))     # the art's link and/or the cover's frame, when on
        try:
            c = _http("POST", f"{GRAPH}/{ig}/media", form=form)
        except PlatformError as e:
            if "thumb_offset" not in form and "cover_url" not in form:
                raise
            # a container refused with a cover is asked for once more without it: the cover never costs the reel
            # (nothing is published by a container, so a lost answer to the first leaves nothing to post twice)
            form.pop("thumb_offset", None)
            form.pop("cover_url", None)
            cover.clear()
            cover["none"] = f"Instagram refused the container with the cover, so it went without: {e}"[:300]
            c = _http("POST", f"{GRAPH}/{ig}/media", form=form)
        cid = c["id"]
        try:
            _upload_bytes(c.get("uri") or f"https://rupload.facebook.com/ig-api-upload/{RUPLOAD_VER}/{cid}", tok, clip)
        except PlatformError as e:
            raise PlatformError(f"{e}{_ig_upload_said(tok, cid)}") from None
        for _ in range(40):                                   # up to ~6.5 minutes
            st = _http("GET", f"{GRAPH}/{cid}", params={"fields": "status_code,status", "access_token": tok})
            if st.get("status_code") == "FINISHED":
                break
            if st.get("status_code") in ("ERROR", "EXPIRED"):
                if "cover_url" in form:         # the art's link may be why (coverart.py): the next try goes without
                    try:
                        from . import covers as CV
                        CV.refused(acct, slot, str(st.get("status") or st.get("status_code")))
                    except Exception:  # noqa: BLE001
                        pass
                raise PlatformError(f"Instagram could not process the clip: {st.get('status') or st.get('status_code')}")
            time.sleep(10)
        else:
            raise PlatformError("Instagram was still processing after 6 minutes")
    _still_signed(slot, "Instagram was getting it ready")
    _mark(slot, {"attempt": {"container_id": cid, "hash": sig, "at": _now(), **_sent(slot, "instagram_reels")}})
    pub = _http("POST", f"{GRAPH}/{ig}/media_publish", form={"creation_id": cid, "access_token": tok})
    done = {"post_id": pub["id"], "platform_id": pub["id"], "url": "", "published_at": _now()}
    if cover:
        done["cover"] = cover
    _mark(slot, done)                       # it is public: from here on nothing sends it again
    try:
        info = _http("GET", f"{GRAPH}/{pub['id']}", params={"fields": "permalink,timestamp", "access_token": tok})
        return {**done, "url": info.get("permalink", ""),
                "published_at": _iso(info["timestamp"]) if info.get("timestamp") else done["published_at"]}
    except Exception:  # noqa: BLE001  (posted; the link comes with the next stats read)
        return done


def _ig_upload_said(tok: str, cid: str) -> str:
    """What a container whose upload failed says, for the board: ' (container C: upload error, 0 bytes, OIL
    Error[FILE_NOT_FOUND: 6]...)', '' when it cannot be read. Meta's answer to the upload itself says only
    ProcessingFailedError; the container says whether any bytes landed (2026-10-17 tests: none ever did)."""
    try:
        st = _http("GET", f"{GRAPH}/{cid}", params={"fields": "status_code,video_status", "access_token": tok}, timeout=30)
    except Exception:  # noqa: BLE001  (only words for the board: the failure is said either way)
        return ""
    up = (st.get("video_status") or {}).get("uploading_phase") or {}
    if not up:
        return ""
    errs = "; ".join(str(x.get("message", ""))[:80] for x in up.get("errors") or [] if isinstance(x, dict))
    return f" (container {cid}: upload {up.get('status')}, {up.get('bytes_transferred')} bytes" + \
        (f", {errs}" if errs else "") + ")"


def _ig_state(tok: str, ig: str, cid: str, words: dict, att: dict) -> tuple:
    """What became of a container an earlier try made: ('published', media id or '') | ('ready', '') |
    ('working', '') | ('gone', ''). A failed read is raised (one of the tries): it never means 'post again'."""
    try:
        st = _http("GET", f"{GRAPH}/{cid}", params={"fields": "status_code,status", "access_token": tok})
    except PlatformError as e:
        if _gone(e):
            return "gone", ""
        raise LookupFailed(f"could not check the reel Instagram was making ({cid}): {e}") from None
    except Exception as e:  # noqa: BLE001  (a timeout, no network)
        raise LookupFailed(f"could not check the reel Instagram was making ({cid}): {type(e).__name__}: {e}") from None
    code = st.get("status_code")
    if code == "PUBLISHED":
        return "published", _ig_find(tok, ig, words.get("caption", ""), str(att.get("at") or ""))
    if code == "FINISHED":
        return "ready", ""
    if code == "IN_PROGRESS":
        return "working", ""
    return "gone", ""                        # ERROR or EXPIRED: that container can never be published


def _ig_find(tok: str, ig: str, caption: str, since: str) -> str:
    """The published reel of a container whose answer was lost: the newest with the same caption since that try."""
    try:
        r = _http("GET", f"{GRAPH}/{ig}/media", params={"fields": "id,caption,timestamp", "limit": 10, "access_token": tok})
    except PlatformError:
        return ""
    want = caption[:2200].strip()
    for row in r.get("data") or []:
        if (row.get("caption") or "").strip() != want:
            continue
        if since and row.get("timestamp") and datetime.fromisoformat(_iso(row["timestamp"])) < \
                datetime.fromisoformat(since) - timedelta(minutes=10):
            continue
        return str(row["id"])
    return ""


def _ig_result(tok: str, mid: str, cid: str, old: bool = False) -> dict:
    out = {"post_id": mid or cid, "platform_id": mid, "url": "", "published_at": _now()}
    if mid:
        try:
            info = _http("GET", f"{GRAPH}/{mid}", params={"fields": "permalink,timestamp", "access_token": tok})
            out.update(url=info.get("permalink", ""), published_at=_iso(info["timestamp"]) if info.get("timestamp")
                       else out["published_at"])
        except Exception:  # noqa: BLE001
            pass
    if old:
        out["flag"] = "Instagram has the version from before it was signed again: delete it there if it should not stay"
    return out


def _cover_ig(acct: C.Account, slot: dict, clip: Path, rec: dict) -> dict:
    """The Instagram container's cover field: {"thumb_offset": ms} at the frame of `clip` that is the clip's cover
    (covers.ig_form), {} while the account's cover switch is off or when anything about it goes wrong. What it did
    goes into `rec`, for the board."""
    try:
        from . import covers as CV
        form, got = CV.ig_form(acct, slot, clip)
        rec.update(got)
        return form
    except Exception as e:  # noqa: BLE001  (a reel never fails over its cover: it goes as it always went)
        rec.update({"none": f"{type(e).__name__}: {e}"[:200]})
        return {}


def fb_post(acct: C.Account, clip: Path, words: dict, slot: dict) -> dict:
    """One signed clip to Facebook Reels (_fb_post), then its cover, when the account's cover switch is on
    (covers.fb_thumbnail: the frame of the clip that is its cover, to the reel's thumbnails). The cover goes only
    after the reel is up and written on the board, so a cover Meta refuses, or one that fails in any way, never
    touches the post: it stays up, recorded as it always was, and "cover" on its record says what happened. Not on
    a reel an earlier signature's try left up (its "flag"): that is not the clip on disk now."""
    out = _fb_post(acct, clip, words, slot)
    if out.get("flag") or not out.get("post_id"):
        return out
    try:
        from . import covers as CV
        rec = CV.fb_thumbnail(acct, slot, clip, str(out["post_id"]))
    except Exception as e:  # noqa: BLE001  (posted: the cover is never worth a failed post)
        rec = {"none": f"{type(e).__name__}: {e}"[:200]}
    return {**out, "cover": rec} if rec else out


def _fb_post(acct: C.Account, clip: Path, words: dict, slot: dict) -> dict:
    """One signed clip to Facebook Reels. The video is written on the board before the publish ("finish") is sent,
    and the reel the moment Facebook takes the publish: after that a failed status read only leaves the link for
    later. The next run looks a written-down video up (slot["_attempt"]) instead of posting a second reel: on
    2026-10-17 a status read failing after the publish made the poster send the reel again (audit, case C)."""
    in_band(acct, clip, "facebook_reels")
    m = _load(acct, "meta", "meta")
    tok, page = m["page_token"], m["page_id"]
    sig = str((slot.get("signoff") or {}).get("hash") or "")
    att = slot.get("_attempt") or {}
    vid = ""
    if att.get("video_id"):
        state, info = _fb_state(tok, str(att["video_id"]))
        if state == "published":
            slot["_from_attempt"] = att            # the poster records what that try sent, not the clip as it is now
            return _fb_result(str(att["video_id"]), info, old=bool(sig and att.get("hash") and att["hash"] != sig))
        if state == "working":
            raise Pending(f"Facebook has this reel ({att['video_id']}) and is still publishing it; the next run looks again")
        if state == "ready" and (not att.get("hash") or att.get("hash") == sig):
            vid = str(att["video_id"])          # uploaded, never published: publish that one, not a second copy
    if not vid:
        st = _http("POST", f"{GRAPH}/{page}/video_reels", form={"upload_phase": "start", "access_token": tok})
        vid = st["video_id"]
        _upload_bytes(st.get("upload_url") or f"https://rupload.facebook.com/video-upload/{RUPLOAD_VER}/{vid}", tok, clip)
    _still_signed(slot, "Facebook had it uploaded")
    _mark(slot, {"attempt": {"video_id": vid, "hash": sig, "at": _now(), **_sent(slot, "facebook_reels")}})
    _http("POST", f"{GRAPH}/{page}/video_reels", form={
        "upload_phase": "finish", "video_id": vid, "video_state": "PUBLISHED", "description": words["caption"][:5000],
        "access_token": tok})
    done = {"post_id": vid, "platform_id": vid, "url": "", "published_at": _now()}
    _mark(slot, done)                       # published: from here on nothing sends it again
    info: dict = {}
    try:
        for _ in range(30):
            info = _http("GET", f"{GRAPH}/{vid}", params={"fields": "status,permalink_url,created_time", "access_token": tok})
            ph = ((info.get("status") or {}).get("publishing_phase") or {}).get("status")
            if (info.get("status") or {}).get("video_status") == "error":
                # Facebook took the publish but failed the video: it is not up. The next run sees it failed and posts
                # a new one (the record is un-marked first, so this is one of the tries, not a post)
                _mark(slot, {"post_id": None, "platform_id": None, "url": None, "published_at": None})
                raise PlatformError(f"Facebook could not process the reel: {json.dumps(info.get('status'))[:300]}")
            if ph == "complete" or info.get("permalink_url"):
                break
            time.sleep(10)
    except PlatformError as e:
        if "could not process" in str(e):
            raise
        return done                         # posted; the link comes with the next stats read
    except Exception:  # noqa: BLE001
        return done
    return _fb_result(vid, info)


def _fb_state(tok: str, vid: str) -> tuple:
    """What became of a video an earlier try uploaded: ('published' | 'working' | 'ready' | 'failed' | 'gone', info).
    A failed read is raised (one of the tries): it never means 'post again'."""
    try:
        info = _http("GET", f"{GRAPH}/{vid}", params={"fields": "status,permalink_url,created_time", "access_token": tok})
    except PlatformError as e:
        if _gone(e):
            return "gone", {}
        raise LookupFailed(f"could not check the reel Facebook had ({vid}): {e}") from None
    except Exception as e:  # noqa: BLE001  (a timeout, no network)
        raise LookupFailed(f"could not check the reel Facebook had ({vid}): {type(e).__name__}: {e}") from None
    st = info.get("status") or {}
    ph = (st.get("publishing_phase") or {}).get("status")
    if st.get("video_status") == "error" or ph == "error":
        return "failed", info
    if ph == "complete":
        return "published", info
    if ph == "in_progress":
        return "working", info
    if ph == "not_started":
        return "ready", info
    return ("published" if info.get("permalink_url") else "working"), info


def _fb_result(vid: str, info: dict, old: bool = False) -> dict:
    url = info.get("permalink_url") or ""
    if url.startswith("/"):
        url = "https://www.facebook.com" + url
    out = {"post_id": vid, "platform_id": vid, "url": url,
           "published_at": _iso(info["created_time"]) if info.get("created_time") else _now()}
    if old:
        out["flag"] = "Facebook has the version from before it was signed again: delete it there if it should not stay"
    return out


def _pages(url: str, params: dict, stop) -> List[dict]:
    out: List[dict] = []
    nxt: Optional[str] = url
    p: Optional[dict] = params
    while nxt:
        r = _http("GET", nxt, params=p)
        rows = r.get("data", [])
        out += rows
        if not rows or stop(rows[-1]):
            break
        nxt, p = (r.get("paging") or {}).get("next"), None
    return out


# ------------------------------------------------------------------------------------------------ watch time
# How long people watched, read beside the counts (2026-10-19: the counts alone could not say why Gaming's YouTube
# fell from a median of 1,528 views a Short to 19). Every field is optional on a video: a platform that has no such
# number, a post too new to have it, or a sign-in without the permission leaves it out, and the post still counts.
# Names checked 2026-10-19 against Meta's IG Media Insights and Video Insights references and YouTube Analytics'
# channel reports. The keys are stats.WATCH; stats.watch adds watch_pct for Instagram and Facebook (from the length
# the platform was sent) and left_3s_pct (from the retention curve).
#   watch_s        average seconds watched a play    IG ig_reels_avg_watch_time, FB post_video_avg_time_watched (ms),
#                                                    YT averageViewDuration
#   watch_total_s  seconds watched in all            IG ig_reels_video_view_total_time, FB post_video_view_time (ms),
#                                                    YT estimatedMinutesWatched
#   watch_pct      average share of the clip watched, % (YT averageViewPercentage). Over 100: watched again
#   skip_pct       share of views that skipped in the first 3 s, % (IG reels_skip_rate: Meta's estimate, in development)
#   engaged        YouTube engaged views (counted as views were before Shorts counted every start); engaged_pct of views
#   reach          people who saw it (IG reach, an estimate; Facebook refuses post_impressions_unique on Reels, v25
#                  and v26, whatever its docs say: checked on both Pages 2026-10-20)
#   saved          Instagram saves; plays_total and replays: FB fb_reels_total_plays and fb_reels_replay_count
#   curve          retention: plays still watching at 0, 5, ... 100% of the clip, as a share (21 points; FB
#                  post_video_retention_graph, YT audienceWatchRatio, which passes 1 where people watch again)
IG_COUNTS = ("views", "likes", "comments", "shares")
IG_WATCH = ("reach", "saved", "ig_reels_avg_watch_time", "ig_reels_video_view_total_time", "reels_skip_rate")
FB_COUNTS = ("blue_reels_play_count", "post_video_likes_by_reaction_type", "post_video_social_actions")
FB_WATCH = ("post_video_avg_time_watched", "post_video_view_time", "fb_reels_total_plays", "fb_reels_replay_count",
            "post_video_retention_graph")
YTA = "https://youtubeanalytics.googleapis.com/v2/reports"
YT_ANALYTICS = "yt-analytics.readonly"         # the scope connect youtube asks for since 2026-10-19
YT_WATCH = "views,engagedViews,averageViewDuration,averageViewPercentage,estimatedMinutesWatched"
YT_ONE_MAX = 60        # one-video reads when YouTube refuses the list (newest first; the rest wait for the next read)
CURVE_AFTER = 2        # days: YouTube Analytics runs about two days behind, so a newer Short has no curve yet
CURVE_DAYS = 28        # curves are read for Shorts up to this old; an older one keeps the last curve read
CURVE_EVERY = 20 * 3600            # seconds: a curve read more recently than this is not read again
CURVE_MAX = 25         # curve reads per channel per read (one call per Short)
NOTES: Dict[str, List[str]] = {}   # 'account__platform' -> what the last read could not get; stats.py prints them


def note(acct: C.Account, plat: str, text: str) -> None:
    said = NOTES.setdefault(f"{acct.slug}__{plat}", [])
    if text not in said:
        said.append(text)


def _num(x) -> Optional[float]:
    if isinstance(x, bool):
        return None
    try:
        n = float(x)
    except (TypeError, ValueError):
        return None
    return n if math.isfinite(n) else None


def _value(row: dict):
    return (row.get("values") or [{}])[0].get("value", (row.get("total_value") or {}).get("value"))


def _insights(acct: C.Account, plat: str, url: str, tok: str, counts: tuple, extras: tuple, memo: dict,
              **extra) -> dict:
    """name -> value from one Graph insights call: the counts and the watch metrics (extras) together. Meta refuses
    the whole call when one name is not there for this account or version, and does not say which ("The value must be
    a valid insights metric"). So the first such no in a read asks each extra on its own once, keeps the ones that
    answer and drops the rest for the read (memo, with a note); any other no is asked again with the counts alone (all
    the read asked for before 2026-10-19): a watch metric can never cost a post its views. Raises when that fails."""
    bad = memo.setdefault("bad", set())

    def ask(names: List[str]) -> dict:
        ins = _http("GET", url, params={"metric": ",".join(names), "access_token": tok, **extra})
        return {row.get("name"): _value(row) for row in ins.get("data", []) if isinstance(row, dict)}

    full = list(counts) + [m for m in extras if m not in bad]
    try:
        return ask(full)
    except PlatformError as e:
        if len(full) > len(counts) and "metric" in str(e).lower() and not memo.get("probed"):
            memo["probed"] = True
            for m in full[len(counts):]:
                try:
                    ask([m])
                except PlatformError as one:
                    if "metric" in str(one).lower():
                        bad.add(m)
            if bad:
                note(acct, plat, f"{plat} refused {', '.join(sorted(bad))}: {str(e)[:160]}")
                full = list(counts) + [m for m in extras if m not in bad]
                try:
                    return ask(full)
                except PlatformError:
                    pass
        if len(full) == len(counts):
            raise
        return ask(list(counts))


def _ms(x) -> Optional[float]:
    n = _num(x)
    return None if n is None else n / 1000.0


def ig_watch(got: dict) -> dict:
    """The watch fields from an Instagram insights answer."""
    w: Dict[str, object] = {}
    for key, n in (("watch_s", _ms(got.get("ig_reels_avg_watch_time"))),
                   ("watch_total_s", _ms(got.get("ig_reels_video_view_total_time"))),
                   ("reach", _num(got.get("reach"))), ("saved", _num(got.get("saved"))),
                   ("skip_pct", _num(got.get("reels_skip_rate")))):
        if n is not None:
            w[key] = round(n, 1) if key in ("watch_s", "skip_pct") else int(round(n))
    return w


def fb_watch(got: dict) -> dict:
    """The watch fields from a Facebook Reel's video_insights answer."""
    w: Dict[str, object] = {}
    for key, n in (("watch_s", _ms(got.get("post_video_avg_time_watched"))),
                   ("watch_total_s", _ms(got.get("post_video_view_time"))),
                   ("plays_total", _num(got.get("fb_reels_total_plays"))),
                   ("replays", _num(got.get("fb_reels_replay_count")))):
        if n is not None:
            w[key] = round(n, 1) if key == "watch_s" else int(round(n))
    graph = got.get("post_video_retention_graph")
    if isinstance(graph, dict):       # {"0": 1.0, "1": 0.83, ... "40": 0.12}: segment k of the last key
        pts = [(int(k), _num(v)) for k, v in graph.items() if str(k).isdigit() and _num(v) is not None]
        top = max((k for k, _ in pts), default=0)
        c = curve([(k / top, v) for k, v in pts]) if top else []
        if c:
            w["curve"] = c
    return w


def curve(points: List[tuple]) -> List[float]:
    """(share of the clip 0-1, share still watching) points -> 21 values at 0, 5, ... 100% of the clip. Each value is
    the last point at or before that place (YouTube's points mark the end of each hundredth), else the first."""
    pts = sorted((float(r), float(v)) for r, v in points if _num(r) is not None and _num(v) is not None)
    if not pts:
        return []
    out = []
    for i in range(21):
        at = i / 20
        val = pts[0][1]
        for r, v in pts:
            if r <= at + 1e-9:
                val = v
            else:
                break
        out.append(round(val, 3))
    return out


def ig_list(acct: C.Account, since: datetime) -> List[dict]:
    m = _load(acct, "meta", "meta")
    if not m.get("ig_user_id"):
        raise NotConnected("no Instagram account linked to the Page")
    tok, ig = m["page_token"], m["ig_user_id"]
    rows = _pages(f"{GRAPH}/{ig}/media", {
        "fields": "id,caption,media_type,media_product_type,permalink,timestamp,like_count,comments_count",
        "since": int(since.timestamp()), "limit": 50, "access_token": tok},
        stop=lambda r: datetime.fromisoformat(_iso(r["timestamp"])) < since)
    out, memo = [], {}
    for r in rows:
        if r.get("media_product_type") != "REELS" and r.get("media_type") != "VIDEO":
            continue
        at = _iso(r["timestamp"])
        if datetime.fromisoformat(at) < since:
            continue
        v = {"platform_id": r["id"], "url": r.get("permalink", ""), "text": r.get("caption", ""), "published_at": at,
             "dur": 0, "views": 0, "likes": r.get("like_count", 0), "comments": r.get("comments_count", 0), "shares": 0}
        # the watch metrics are Reels' own: an older feed video is asked for its counts alone
        extras = IG_WATCH if r.get("media_product_type") == "REELS" else ()
        try:
            got = _insights(acct, "instagram_reels", f"{GRAPH}/{r['id']}/insights", tok, IG_COUNTS, extras, memo)
        except PlatformError:
            got = {}   # too new, or insights not granted: likes and comments from the media fields still count
        for k in IG_COUNTS:
            if k in got:
                v[k] = int(_num(got[k]) or 0)
        v.update(ig_watch(got))
        out.append(v)
    return out


def fb_list(acct: C.Account, since: datetime) -> List[dict]:
    m = _load(acct, "meta", "meta")
    tok, page = m["page_token"], m["page_id"]
    rows = _pages(f"{GRAPH}/{page}/video_reels", {
        "fields": "id,description,created_time,permalink_url,length,comments.summary(true).limit(0)",
        "since": int(since.timestamp()), "limit": 50, "access_token": tok},
        stop=lambda r: "created_time" in r and datetime.fromisoformat(_iso(r["created_time"])) < since)
    out, memo = [], {}
    for r in rows:
        at = _iso(r.get("created_time") or r.get("updated_time"))
        if datetime.fromisoformat(at) < since:
            continue
        url = r.get("permalink_url", "")
        comments = int(((r.get("comments") or {}).get("summary") or {}).get("total_count") or 0)
        v = {"platform_id": r["id"], "url": ("https://www.facebook.com" + url) if url.startswith("/") else url,
             "text": r.get("description", ""), "published_at": at, "dur": r.get("length") or 0,
             "views": 0, "likes": 0, "comments": comments, "shares": 0}
        try:
            got = _insights(acct, "facebook_reels", f"{GRAPH}/{r['id']}/video_insights", tok, FB_COUNTS, FB_WATCH, memo,
                            period="lifetime")
        except PlatformError:
            got = {}
        for name, val in got.items():
            if name == "blue_reels_play_count":
                v["views"] = int(_num(val) or 0)
            elif name == "post_video_likes_by_reaction_type":
                v["likes"] = int(sum(val.values()) if isinstance(val, dict) else val or 0)
            elif name == "post_video_social_actions":
                if isinstance(val, dict):   # {"COMMENT": n, "SHARE": n} when split
                    low = {k.lower(): n for k, n in val.items()}
                    v["shares"] = int(low.get("share", 0))
                    v["comments"] = max(comments, int(low.get("comment", 0)))
                else:                        # one combined number: what is not a comment is a share
                    v["shares"] = max(0, int(val or 0) - comments)
        v.update(fb_watch(got))
        out.append(v)
    return out


# ------------------------------------------------------------------------------------------------ TikTok
def connect_tiktok(acct: C.Account, client_key: str) -> str:
    """Login Kit for Desktop: a loopback redirect on 127.0.0.1 with PKCE (TikTok's challenge is hex SHA-256)."""
    import http.server
    import socket
    import threading
    import webbrowser
    client_secret = getpass.getpass("TikTok client secret (hidden): ").strip()
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    redirect = f"http://127.0.0.1:{port}/callback/"
    verifier = secrets.token_urlsafe(64)[:96]
    challenge = hashlib.sha256(verifier.encode()).hexdigest()
    state = secrets.token_urlsafe(16)
    got: Dict[str, str] = {}

    class H(http.server.BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            q = dict(urllib.parse.parse_qsl(urllib.parse.urlparse(self.path).query))
            if q.get("state") == state and "code" in q:
                got["code"] = q["code"]
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"Connected. You can close this tab.")

        def log_message(self, *a):
            pass

    srv = http.server.HTTPServer(("127.0.0.1", port), H)
    threading.Thread(target=srv.handle_request, daemon=True).start()
    scopes = acct.outlets.get("tiktok", {}).get("scopes") or "user.info.basic,video.list,video.publish"
    url = "https://www.tiktok.com/v2/auth/authorize/?" + urllib.parse.urlencode({
        "client_key": client_key, "response_type": "code", "scope": scopes, "redirect_uri": redirect, "state": state,
        "code_challenge": challenge, "code_challenge_method": "S256"})
    print(f"Sign in as {C.possessive(acct.name)} TikTok account in the browser that opens:\n{url}")
    webbrowser.open(url)
    for _ in range(600):
        if "code" in got:
            break
        time.sleep(0.5)
    srv.server_close()
    if "code" not in got:
        raise SystemExit("no sign-in within five minutes")
    tok = _http("POST", f"{TT}/oauth/token/", form={
        "client_key": client_key, "client_secret": client_secret, "code": got["code"], "grant_type": "authorization_code",
        "redirect_uri": redirect, "code_verifier": verifier})
    if "access_token" not in tok:
        raise SystemExit(f"TikTok refused the sign-in: {tok}")
    other = held_by(TOKENS, acct.slug, "tiktok", "open_id", tok.get("open_id"))
    if other:
        raise SystemExit(f"nothing saved: that TikTok account is the one {other} is signed in to. Log out of it in the "
                         f"browser, log in as {C.possessive(acct.name)}, then connect again")
    try:                                 # who signed in, to read before TikTok is switched to api (user.info.basic)
        who = (_http("GET", f"{TT}/user/info/", params={"fields": "open_id,display_name"}, headers={
            "Authorization": f"Bearer {tok['access_token']}"}).get("data") or {}).get("user", {}).get("display_name")
    except Exception:  # noqa: BLE001
        who = ""
    out = _save(acct, "tiktok", {"client_key": client_key, "client_secret": client_secret, **tok,
                                 "expires_at": time.time() + int(tok.get("expires_in", 86400))})
    try:
        n = len(tiktok_list(acct, datetime.fromtimestamp(time.time() - 30 * 86400, timezone.utc)))
        check = f"reading works: {n} videos in the last 30 days"
    except Exception as e:  # noqa: BLE001
        check = f"signed in, but reading videos failed ({e}); a sandbox app may not be allowed video.list"
    return (f"connected {acct.name} to TikTok" + (f" as {who}" if who else "")
            + f"; token at {out} (mode 600); {check}")


def _tt_token(acct: C.Account) -> str:
    t = _load(acct, "tiktok", "tiktok --client-key KEY")
    if t.get("expires_at", 0) > time.time() + 120:
        return t["access_token"]
    with token_lock(_tok_path(acct, "tiktok")):        # one refresh at a time: the refresh token rotates
        t = _load(acct, "tiktok", "tiktok --client-key KEY")
        if t.get("expires_at", 0) > time.time() + 120:
            return t["access_token"]                     # another process refreshed it while this one waited
        fresh = _http("POST", f"{TT}/oauth/token/", form={
            "client_key": t["client_key"], "client_secret": t["client_secret"], "grant_type": "refresh_token",
            "refresh_token": t["refresh_token"]})
        if "access_token" not in fresh:
            raise NotConnected(f"TikTok sign-in expired ({fresh.get('error_description') or fresh}); connect again")
        t.update(fresh, expires_at=time.time() + int(fresh.get("expires_in", 86400)))   # the refresh token may rotate
        _save(acct, "tiktok", t)
    return t["access_token"]


PRIVACY_NAMES = {"PUBLIC_TO_EVERYONE": "Everyone", "MUTUAL_FOLLOW_FRIENDS": "Friends",
                 "FOLLOWER_OF_CREATOR": "Followers", "SELF_ONLY": "Only me"}
MUSIC_URL = "https://www.tiktok.com/legal/page/global/music-usage-confirmation/en"
BRANDED_URL = "https://www.tiktok.com/legal/page/global/bc-policy/en"
TT_FLAGS = ("allow_comment", "allow_duet", "allow_stitch", "commercial", "brand_organic", "brand_content")
_CREATOR: Dict[str, tuple] = {}


def tiktok_connected(acct: C.Account) -> bool:
    return _tok_path(acct, "tiktok").exists()


_CREATOR_FAILED: Dict[str, tuple] = {}
CREATOR_FAIL_WAIT = 300.0     # seconds a failed creator_info is not asked again (except right before a post)


def creator_info(acct: C.Account, max_age: float = 60.0) -> dict:
    """The account as TikTok's posting screen must show it (nickname, the privacy levels it may use, interactions it
    has switched off, its longest video). Asked fresh at most once a minute; max_age=0 right before a post."""
    hit = _CREATOR.get(acct.slug)
    if hit and time.time() - hit[0] < max_age:
        return hit[1]
    bad = _CREATOR_FAILED.get(acct.slug)
    if bad and max_age and time.time() - bad[0] < CREATOR_FAIL_WAIT:   # the page asks every 15 s: not each time
        raise PlatformError(bad[1])
    try:
        tok = _tt_token(acct)
        r = _http("POST", f"{TT}/post/publish/creator_info/query/", body={}, headers={"Authorization": f"Bearer {tok}"},
                  timeout=20)
        err = r.get("error") or {}
        if err.get("code") not in (None, "", "ok"):       # e.g. spam_risk_too_many_posts: TikTok says not now
            raise PlatformError(f"TikTok says this account can't post right now, try again later "
                                f"({err.get('message') or err.get('code')})")
    except Exception as e:  # noqa: BLE001  (remembered, then raised as it was)
        _CREATOR_FAILED[acct.slug] = (time.time(), str(e))
        raise
    _CREATOR_FAILED.pop(acct.slug, None)
    data = r.get("data") or {}
    _CREATOR[acct.slug] = (time.time(), data)
    return data


def tiktok_needs_choice(acct: C.Account, slot: dict) -> bool:
    """True while TikTok (the account's own app) waits for the person to choose who can see this clip. Upload-Post
    holds its own posting screen, so its route never waits here."""
    return acct.outlets.get("tiktok", {}).get("via") != "upload-post" \
        and not (slot.get("tiktok_post") or {}).get("privacy_level")


def tiktok_problems(choice: dict, info: dict, seconds: float, audited: bool) -> List[str]:
    """What stops this post, in TikTok's own words where it has them. [] = it may go."""
    out: List[str] = []
    priv = str(choice.get("privacy_level") or "")
    opts = info.get("privacy_level_options") or []
    if not priv:
        out.append("Choose who can see this video.")
    elif opts and priv not in opts:
        out.append(f"TikTok doesn't offer '{PRIVACY_NAMES.get(priv, priv)}' for this account right now: choose again.")
    if choice.get("commercial") and not (choice.get("brand_organic") or choice.get("brand_content")):
        out.append("You need to indicate if your content promotes yourself, a third party, or both.")
    if choice.get("commercial") and choice.get("brand_content") and priv == "SELF_ONLY":
        out.append("Branded content visibility cannot be set to private.")
    longest = info.get("max_video_post_duration_sec")
    if longest and seconds and seconds > float(longest):
        out.append(f"This video is {seconds:.0f}s; this account can post videos up to {longest}s.")
    if not audited and priv and priv != "SELF_ONLY":
        out.append("Until TikTok approves AJJ Clip Factory, TikTok only takes 'Only me' posts, from an account set "
                   "to private.")
    return out


def tiktok_post(acct: C.Account, clip: Path, words: dict, slot: dict) -> dict:
    """One signed clip to TikTok with the choices the person made on its card (slot["tiktok_post"]): who can see it,
    comments/duet/stitch, the commercial content disclosure. Until TikTok approves the app it only runs from the
    card's own "Post to TikTok now" (slot["_manual"]), never on a schedule."""
    opts = acct.outlets.get("tiktok", {})
    if opts.get("via") == "upload-post":
        return up_tiktok_post(acct, clip, words, slot)
    audited = bool(opts.get("api_app_audited"))
    if not audited and not slot.get("_manual"):
        raise Refused("not posted on its schedule: TikTok has not approved AJJ Clip Factory yet. Until then only "
                      "'Post to TikTok now' on the card works, with 'Only me'.")
    in_band(acct, clip, "tiktok")
    choice = slot.get("tiktok_post") or {}
    info = creator_info(acct, max_age=0)                 # the latest, as TikTok asks, right before sending
    problems = tiktok_problems(choice, info, float((slot.get("clip") or {}).get("seconds") or 0), audited)
    if problems:
        raise Refused("not posted: " + " ".join(problems))
    tok = _tt_token(acct)
    auth = {"Authorization": f"Bearer {tok}"}
    commercial = bool(choice.get("commercial"))
    post_info = {"title": words["caption"][:2200], "privacy_level": choice["privacy_level"],
                 "disable_comment": not choice.get("allow_comment") or bool(info.get("comment_disabled")),
                 "disable_duet": not choice.get("allow_duet") or bool(info.get("duet_disabled")),
                 "disable_stitch": not choice.get("allow_stitch") or bool(info.get("stitch_disabled")),
                 "brand_organic_toggle": commercial and bool(choice.get("brand_organic")),
                 "brand_content_toggle": commercial and bool(choice.get("brand_content")),
                 "is_aigc": bool(opts.get("is_aigc", False))}
    size = clip.stat().st_size
    chunk = size if size <= 64 * 2**20 else 10 * 2**20     # one chunk up to 64 MB; else 10 MB chunks, last takes the rest
    count = max(1, size // chunk)
    try:
        init = _http("POST", f"{TT}/post/publish/video/init/", headers=auth, body={
            "post_info": post_info,
            "source_info": {"source": "FILE_UPLOAD", "video_size": size, "chunk_size": chunk, "total_chunk_count": count}})
    except PlatformError as e:
        if "unaudited_client_can_only_post_to_private_accounts" in str(e):
            raise Refused(f"not posted: until TikTok approves the app, it only takes posts from a private account. "
                          f"Set @{info.get('creator_username', '?')} to private in TikTok, then post again.") from None
        raise
    if (init.get("error") or {}).get("code") not in (None, "", "ok"):
        raise PlatformError(f"TikTok refused the post: {init['error'].get('message') or init['error'].get('code')}")
    data = init.get("data", {})
    with open(clip, "rb") as f:
        for i in range(count):
            start = i * chunk
            end = size - 1 if i == count - 1 else start + chunk - 1   # the last chunk takes the remainder
            f.seek(start)
            part = f.read(end - start + 1)
            req = urllib.request.Request(data["upload_url"], data=part, method="PUT", headers={
                "Content-Range": f"bytes {start}-{end}/{size}", "Content-Type": "video/mp4", "Content-Length": str(len(part))})
            from . import net
            net.guard("a TikTok upload")
            with urllib.request.urlopen(req, timeout=1800):
                pass
    st: dict = {}
    for _ in range(40):
        st = _http("POST", f"{TT}/post/publish/status/fetch/", headers=auth, body={"publish_id": data["publish_id"]}).get("data", {})
        if st.get("status") == "FAILED":
            raise PlatformError(f"TikTok failed the post: {st.get('fail_reason')}")
        if st.get("status") == "PUBLISH_COMPLETE":
            break
        time.sleep(10)
    ids = st.get("publicaly_available_post_id") or []      # TikTok's spelling; filled after moderation (never for Only me)
    pid = str(ids[0]) if ids else ""
    handle = info.get("creator_username") or ""
    url = (f"https://www.tiktok.com/@{handle}/video/{pid}" if pid and handle else
           f"https://www.tiktok.com/video/{pid}" if pid else f"https://www.tiktok.com/@{handle}" if handle else "")
    return {"post_id": pid or data["publish_id"], "publish_id": data["publish_id"], "platform_id": pid, "url": url,
            "privacy": choice["privacy_level"], "status": st.get("status", ""),
            "published_at": datetime.now(timezone.utc).isoformat()}


def tiktok_list(acct: C.Account, since: datetime) -> List[dict]:
    if acct.outlets.get("tiktok", {}).get("via") == "upload-post":
        return up_tiktok_list(acct, since)
    tok = _tt_token(acct)
    fields = "id,title,video_description,create_time,share_url,duration,view_count,like_count,comment_count,share_count"
    out, cursor = [], None
    for _ in range(50):
        body = {"max_count": 20, **({"cursor": cursor} if cursor else {})}
        r = _http("POST", f"{TT}/video/list/", params={"fields": fields}, body=body,
                  headers={"Authorization": f"Bearer {tok}"}).get("data", {})
        vids = r.get("videos", [])
        for v in vids:
            at = _iso(int(v["create_time"]))
            if datetime.fromisoformat(at) < since:
                continue
            out.append({"platform_id": str(v["id"]), "url": v.get("share_url", ""),
                        "text": v.get("title") or v.get("video_description", ""), "published_at": at,
                        "dur": v.get("duration", 0), "views": v.get("view_count", 0), "likes": v.get("like_count", 0),
                        "comments": v.get("comment_count", 0), "shares": v.get("share_count", 0)})
        if not r.get("has_more") or not vids or int(vids[-1]["create_time"]) < since.timestamp():
            break
        cursor = r.get("cursor")
    return out


# ------------------------------------------------------------------------------------------------ TikTok via Upload-Post
UP = "https://api.upload-post.com/api"
UP_POLL = 10          # seconds between status checks (Upload-Post caches status for a few seconds)
UP_POLLS = 36         # six minutes: TikTok downloads the file first, then publishes
UP_ATTEMPTS = 6       # attempt ids per slot; past that a person looks
UP_STATS_MAX = 80     # live per-post reads per stats run (Upload-Post allows 100 per 5 minutes)
UP_WORKING = {"pending", "queued", "processing", "in_progress", "completed"}   # completed: history lags a moment
UP_REFUSALS = ("tiktok_privacy_unavailable", "profile_platform_mapping_invalid", "tiktok_reconnect_required",
               "PROFILE_NOT_FOUND", "not available on the Free plan")
INBOX = "Video sent to Inbox"


def _up_key_path() -> Path:
    return TOKENS / "upload-post.json"      # one key for the Upload-Post account; each brand is a profile in it


def _up(method: str, path: str, **kw) -> dict:
    p = _up_key_path()
    if not p.exists():
        raise NotConnected("no Upload-Post key yet; run: factory.py connect upload-post <account>")
    auth = {"Authorization": f"Apikey {json.loads(p.read_text())['api_key']}"}
    return _http(method, f"{UP}{path}", headers={**auth, **kw.pop("headers", {})}, **kw)


def _up_profile(acct: C.Account) -> str:
    return acct.outlets.get("tiktok", {}).get("profile") or acct.slug


def _up_tiktok(profile: str) -> dict:
    """The TikTok account connected to that Upload-Post profile ({} when none is)."""
    r = _up("GET", f"/uploadposts/users/{urllib.parse.quote(profile)}")
    tt = ((r.get("profile") or {}).get("social_accounts") or {}).get("tiktok")
    return tt if isinstance(tt, dict) and tt.get("username") else {}


def connect_upload_post(acct: C.Account, profile: Optional[str] = None, new_key: bool = False) -> str:
    """Save the Upload-Post API key (once, hidden) and bind this account to its profile's TikTok. Says which TikTok
    it reaches and whether it may post publicly, so a wrong-account link shows up here, not on the first post."""
    kp = _up_key_path()
    if new_key or not kp.exists():
        key = "".join(getpass.getpass("Upload-Post API key (hidden): ").split())
        if not key:
            raise SystemExit("no key typed; nothing saved")
        try:
            me = _http("GET", f"{UP}/uploadposts/me", headers={"Authorization": f"Apikey {key}"})
        except PlatformError as e:
            raise SystemExit(f"Upload-Post refused that key: {e}") from None
        write_private(kp, json.dumps({"api_key": key, "plan": me.get("plan", ""), "saved_at": time.time()}))
        plan = me.get("plan", "")
        print(f"key saved at {kp} (mode 600); Upload-Post plan: {plan or 'unknown'}")
        if plan.lower() == "free":
            print("  TikTok needs a paid Upload-Post plan (Basic or above): posts will be refused until you upgrade")
    prof = profile or _up_profile(acct)
    try:
        tt = _up_tiktok(prof)
    except PlatformError as e:
        raise SystemExit(f"Upload-Post has no profile '{prof}' ({e}). Make it in Manage Users, then run this again "
                         f"(or pass --profile <its name>)") from None
    if not tt:
        raise SystemExit(f"profile '{prof}' has no TikTok connected: Manage Users > {prof} > TikTok, then run this again")
    if tt.get("reauth_required"):
        raise SystemExit(f"profile '{prof}': TikTok asks to be connected again (Manage Users > {prof} > TikTok)")
    opts = _up("GET", "/uploadposts/tiktok/settings", params={"profile": prof}).get("privacy_level_options") or []
    out = _save(acct, "uploadpost", {"profile": prof, "tiktok_id": tt["username"], "handle": tt.get("handle", ""),
                                     "name": tt.get("display_name", ""), "capabilities": tt.get("capabilities") or [],
                                     "connected_at": datetime.now(timezone.utc).isoformat()})
    who = f"@{tt['handle']}" if tt.get("handle") else f"'{tt.get('display_name', '?')}'"
    public = "PUBLIC_TO_EVERYONE" in opts
    return (f"connected {acct.name} to TikTok {who} ({tt.get('display_name', '')}) through Upload-Post profile '{prof}'; "
            f"saved at {out}\n  " + ("it may post publicly" if public else
            f"it may NOT post publicly ({', '.join(opts) or 'no options'}): set the TikTok account to public") +
            f"\n  If {who} is not {C.possessive(acct.name)} TikTok, disconnect it in Manage Users and connect the "
            f"right one.")


def _up_link(acct: C.Account) -> dict:
    link = _load(acct, "uploadpost", "upload-post")
    tt = _up_tiktok(link["profile"])
    if not tt:
        raise Refused(f"not posted: Upload-Post profile '{link['profile']}' has no TikTok connected now")
    if tt["username"] != link["tiktok_id"]:
        raise Refused(f"not posted: a different TikTok account (@{tt.get('handle', '?')}) is connected to "
                      f"'{link['profile']}' than the one checked at connect (@{link.get('handle', '?')}). "
                      f"If it is the right one: factory.py connect upload-post {acct.slug}")
    if tt.get("reauth_required"):
        raise NotConnected(f"TikTok asks to be connected again: Upload-Post Manage Users > {link['profile']} > TikTok")
    return {**link, "capabilities": tt.get("capabilities") or link.get("capabilities") or []}


def _multipart(fields: List[tuple], name: str, path: Path, filename: str = "clip.mp4",
               ctype: str = "video/mp4") -> tuple:
    b = "cf" + secrets.token_hex(16)
    head = b"".join(f'--{b}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n'.encode() for k, v in fields)
    file_head = f'--{b}\r\nContent-Disposition: form-data; name="{name}"; filename="{filename}"\r\n' \
                f'Content-Type: {ctype}\r\n\r\n'.encode()
    return head + file_head + path.read_bytes() + f"\r\n--{b}--\r\n".encode(), f"multipart/form-data; boundary={b}"


def _up_attempt(rid: str) -> tuple:
    """('posted', row) | ('failed', row) | ('working', None) | ('none', None) for one attempt id."""
    h = _up("GET", "/uploadposts/history", params={"request_id": rid, "limit": 10})
    rows = [r for r in h.get("history") or [] if r.get("platform") == "tiktok"]
    for r in rows:
        if r.get("success"):
            return "posted", r
    if rows:
        return "failed", rows[0]
    if h.get("in_progress"):
        return "working", None
    try:
        st = _up("GET", "/uploadposts/status", params={"request_id": rid})
    except PlatformError as e:
        if "HTTP 404" in str(e):
            return "none", None
        raise
    status = st.get("status", "")
    if status == "not_found":
        return "none", None
    if status in UP_WORKING:
        return "working", None
    res = [r for r in st.get("results") or [] if r.get("platform") == "tiktok"]
    return "failed", (res[0] if res else {"error_message": st.get("message") or status})


def _up_done(rid: str, row: dict) -> dict:
    """A posted row -> the poster's record. A post that went to the TikTok inbox as a draft is not live: a person
    publishes it in the app, and the poster must never send it again (it would make a second draft)."""
    url = str(row.get("post_url") or "")
    if row.get("fallback_to_inbox") or url.startswith(INBOX):
        raise Refused("sent to the TikTok app's inbox as a draft, not published (TikTok's daily cap on Upload-Post): "
                      "open TikTok on the phone and post it from the inbox")
    pid = row.get("platform_post_id")
    pid = str(pid[0] if isinstance(pid, list) and pid else pid or "")
    return {"post_id": pid or rid, "platform_id": pid, "url": url, "request_id": rid, "via": "upload-post",
            "published_at": _iso(row["upload_timestamp"]) if row.get("upload_timestamp")
            else datetime.now(timezone.utc).isoformat()}


def _up_fail(row: dict) -> PlatformError:
    why = str(row.get("error_message") or row.get("error") or row.get("message") or "no reason given")
    code = str(row.get("error_code") or "")
    text = f"Upload-Post: {why}" + (f" [{code}]" if code and code not in why else "")
    return Refused(text) if any(k in why or k == code for k in UP_REFUSALS) else PlatformError(text)


def up_tiktok_post(acct: C.Account, clip: Path, words: dict, slot: dict) -> dict:
    """Post one signed clip to TikTok, public, through Upload-Post. Each slot has fixed attempt ids, and an earlier
    attempt is always looked up before a new one is sent, so a slow publish is never sent twice."""
    in_band(acct, clip, "tiktok")
    link = _up_link(acct)
    prof = link["profile"]
    opts = _up("GET", "/uploadposts/tiktok/settings", params={"profile": prof}).get("privacy_level_options") or []
    if "PUBLIC_TO_EVERYONE" not in opts:
        raise Refused(f"not posted: TikTok @{link.get('handle', '?')} may not post publicly right now ({', '.join(opts)}); "
                      "is the account set to private?")
    stamp = re.sub(r"\D", "", str(slot.get("publish_at", "")))[:12] or datetime.now().strftime("%Y%m%d%H%M%S")
    sig = re.sub(r"[^A-Za-z0-9]", "", str((slot.get("signoff") or {}).get("hash", "")))[:10] or "unsigned"
    rid = ""
    for k in range(UP_ATTEMPTS):
        rid = f"cf-{acct.slug}-{stamp}-{sig}-{k}"
        state, row = _up_attempt(rid)
        if state == "posted":
            return _up_done(rid, row)
        if state == "working":
            raise Pending(f"Upload-Post has it ({rid}) and TikTok is still publishing; the next run looks again")
        if state == "none":
            break
    else:
        raise Refused(f"not posted: {UP_ATTEMPTS} attempts failed on Upload-Post; left for a person")
    fields = [("user", prof), ("platform[]", "tiktok"), ("title", words["caption"][:2200]), ("post_mode", "DIRECT_POST"),
              ("disable_inbox_fallback", "true"), ("async_upload", "true"), ("request_id", rid),
              ("external_id", f"{acct.slug} {slot.get('publish_at', '')}"[:255])]
    if "video_privacy" in link["capabilities"]:      # older connections take a privacy level; newer ones are public
        fields.append(("privacy_level", "PUBLIC_TO_EVERYONE"))
    if acct.outlets.get("tiktok", {}).get("is_aigc"):
        fields.append(("is_aigc", "true"))
    body, ctype = _multipart(fields, "video", clip)
    r = _up("POST", "/upload", data=body, timeout=1800, headers={"Content-Type": ctype, "Idempotency-Key": rid})
    tt = (r.get("results") or {}).get("tiktok") or {}
    if r.get("success") is False or tt.get("success") is False:
        raise _up_fail({**tt, "error_message": tt.get("error") or r.get("message")})
    for _ in range(UP_POLLS):
        time.sleep(UP_POLL)
        state, row = _up_attempt(rid)
        if state == "posted":
            return _up_done(rid, row)
        if state == "failed":
            raise _up_fail(row)
    raise Pending(f"Upload-Post has it ({rid}) and TikTok is still publishing; the next run looks again")


def up_tiktok_list(acct: C.Account, since: datetime) -> List[dict]:
    """Recent TikTok videos with their numbers, through Upload-Post: the list, then live numbers for the newest."""
    prof = _load(acct, "uploadpost", "upload-post")["profile"]
    vids, cursor = [], None
    for _ in range(25):
        r = _up("GET", "/uploadposts/media", params={"platform": "tiktok", "user": prof, "limit": 20,
                                                    **({"cursor": cursor} if cursor else {})})
        page = r.get("media") or []
        for m in page:
            at = _iso(m["timestamp"]) if m.get("timestamp") else ""
            if at and datetime.fromisoformat(at) >= since:
                vids.append({"platform_id": str(m["id"]), "url": m.get("permalink") or "", "text": m.get("caption") or "",
                             "published_at": at, "dur": 0, "views": 0, "likes": 0, "comments": 0, "shares": 0})
        more = (r.get("pagination") or {})
        cursor = more.get("next_cursor")
        old = any(m.get("timestamp") and datetime.fromisoformat(_iso(m["timestamp"])) < since for m in page)
        if not page or old or not more.get("has_more") or not cursor:
            break
    vids.sort(key=lambda v: v["published_at"], reverse=True)
    out = []
    for v in vids[:UP_STATS_MAX]:            # older ones keep the numbers they last had
        try:
            a = _up("GET", "/uploadposts/post-analytics", params={
                "platform_post_id": v["platform_id"], "platform": "tiktok", "user": prof})
        except PlatformError:
            continue
        m = ((a.get("platforms") or {}).get("tiktok") or {}).get("post_metrics") or {}
        out.append({**v, **{k: int(m.get(k) or 0) for k in ("views", "likes", "comments", "shares")}})
    return out


# ------------------------------------------------------------------------------------------------ YouTube
def _iso_dur(s: str) -> int:
    import re
    m = re.match(r"P(?:(\d+)D)?T?(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?", s or "")
    if not m:
        return 0
    d, h, mi, se = (int(x or 0) for x in m.groups())
    return d * 86400 + h * 3600 + mi * 60 + se


def yt_list(acct: C.Account, since: datetime) -> List[dict]:
    from . import outlets as O
    tf = TOKENS / acct.slug / "youtube_token.json"
    if not tf.exists():
        raise NotConnected(f"run: factory.py connect youtube {acct.slug} --client <client.json>")
    tok = json.loads(tf.read_text())
    if "youtube.readonly" not in tok.get("scope", "youtube.readonly"):
        raise NotConnected("the YouTube sign-in has no read permission; run connect youtube again")
    auth = {"Authorization": f"Bearer {O._yt_access_token(tf)}"}
    api = "https://www.googleapis.com/youtube/v3"
    ch = _http("GET", f"{api}/channels", params={"part": "contentDetails", "mine": "true"}, headers=auth)
    if not ch.get("items"):
        raise NotConnected("that Google sign-in has no YouTube channel")
    uploads = ch["items"][0]["contentDetails"]["relatedPlaylists"]["uploads"]
    ids, undated, page = [], set(), None
    for _ in range(40):
        r = _http("GET", f"{api}/playlistItems", headers=auth, params={
            "part": "contentDetails", "playlistId": uploads, "maxResults": 50, **({"pageToken": page} if page else {})})
        items = r.get("items", [])
        # an item with no date (a stub still processing, a private copy) says nothing about the window: it is read
        # (videos.list dates it below) and the paging goes on until an item WITH a date is older than the window
        old = False
        for i in items:
            when = i["contentDetails"].get("videoPublishedAt")
            if not when:
                undated.add(i["contentDetails"]["videoId"])
            elif datetime.fromisoformat(_iso(when)) < since:
                old = True
                continue
            ids.append(i["contentDetails"]["videoId"])
        page = r.get("nextPageToken")
        if not page or old:
            break
    out = []
    for i in range(0, len(ids), 50):
        r = _http("GET", f"{api}/videos", headers=auth, params={
            "part": "snippet,statistics,contentDetails,status", "id": ",".join(ids[i:i + 50]), "maxResults": 50})
        for v in r.get("items", []):
            st = v.get("statistics", {})
            up = datetime.fromisoformat(_iso(v["snippet"]["publishedAt"]))
            if v["id"] in undated and up < since:
                continue
            # a scheduled upload goes public at its publishAt, not at its upload time (the Stats tab showed
            # 23:00 clips as posted at 17:28)
            later = (v.get("status") or {}).get("publishAt")
            when = max(up, datetime.fromisoformat(_iso(later))) if later else up
            out.append({"platform_id": v["id"], "url": f"https://www.youtube.com/shorts/{v['id']}",
                        "text": v["snippet"].get("title", ""), "published_at": when.isoformat(),
                        "dur": _iso_dur(v.get("contentDetails", {}).get("duration", "")),
                        "views": int(st.get("viewCount", 0)), "likes": int(st.get("likeCount", 0)),
                        "comments": int(st.get("commentCount", 0)), "shares": 0})
    if YT_ANALYTICS not in tok.get("scope", ""):
        note(acct, "youtube_shorts", f"no watch time until the channel is signed in again "
                                     f"(factory.py connect youtube {acct.slug}): the sign-in has no {YT_ANALYTICS}")
    elif out:
        try:
            yt_watch(acct, auth, out)
        except PlatformError as e:     # the counts still count; the watch fields wait for the next read
            note(acct, "youtube_shorts", f"watch time not read: {str(e)[:200]}")
    return out


def _yta(auth: dict, start: str, end: str, **params) -> List[dict]:
    """One YouTube Analytics report as rows of {column: value}."""
    r = _http("GET", YTA, headers=auth, params={"ids": "channel==MINE", "startDate": start, "endDate": end, **params})
    heads = [h.get("name") for h in r.get("columnHeaders") or []]
    return [dict(zip(heads, row)) for row in r.get("rows") or [] if isinstance(row, list)]


def _yt_fields(d: dict) -> dict:
    w: Dict[str, object] = {}
    views, eng = _num(d.get("views")), _num(d.get("engagedViews"))
    if eng is not None:
        w["engaged"] = int(eng)
        if views:
            w["engaged_pct"] = round(eng / views * 100, 1)
    for key, n, nd in (("watch_s", _num(d.get("averageViewDuration")), 1),
                       ("watch_pct", _num(d.get("averageViewPercentage")), 1)):
        if n is not None:
            w[key] = round(n, nd)
    mins = _num(d.get("estimatedMinutesWatched"))
    if mins is not None:
        w["watch_total_s"] = int(round(mins * 60))
    return w


def _curves_path(acct: C.Account) -> Path:
    from . import stats as ST
    return ST.OUT / "curves" / f"{acct.slug}__youtube_shorts.json"


def yt_watch(acct: C.Account, auth: dict, videos: List[dict], now: Optional[datetime] = None) -> None:
    """Adds the watch fields to each YouTube video, from YouTube Analytics (the yt-analytics.readonly scope): one report
    for up to 200 videos (views, engaged views, average view duration and percentage, minutes watched), then the
    audience retention curve, one call per Short (YouTube allows one video in that report). Curves are kept in
    state/stats/curves/: a Short between CURVE_AFTER and CURVE_DAYS days old is read again once CURVE_EVERY has passed,
    at most CURVE_MAX a read, newest first; every other Short keeps the curve last read. YouTube Analytics runs about
    two days behind, so a new Short has no row yet and simply has no watch fields."""
    now = now or datetime.now(timezone.utc)
    end = now.date().isoformat()
    when = {v["platform_id"]: datetime.fromisoformat(v["published_at"]) for v in videos}
    first = (min(when.values()) - timedelta(days=2)).date().isoformat()   # its dates are Pacific; a day either way
    by = {v["platform_id"]: v for v in videos}
    ids = sorted(by, key=lambda i: when[i], reverse=True)
    try:
        for i in range(0, len(ids), 200):
            for d in _yta(auth, first, end, metrics=YT_WATCH, dimensions="video",
                          filters="video==" + ",".join(ids[i:i + 200]), maxResults=200, sort="-views"):
                v = by.get(str(d.get("video")))
                if v:
                    v.update(_yt_fields(d))
    except PlatformError as e:
        if "HTTP 400" not in str(e):
            raise
        note(acct, "youtube_shorts", f"YouTube refused the list of videos, read one at a time: {str(e)[:120]}")
        for vid in ids[:YT_ONE_MAX]:
            rows = _yta(auth, first, end, metrics=YT_WATCH, filters=f"video=={vid}")
            if rows:
                by[vid].update(_yt_fields(rows[0]))
    path = _curves_path(acct)
    try:
        kept = json.loads(path.read_text())
    except (OSError, ValueError):
        kept = {}
    kept = kept if isinstance(kept, dict) else {}
    read, stop = 0, False
    for vid in ids:
        age = (now - when[vid]).total_seconds() / 86400
        last = kept.get(vid) if isinstance(kept.get(vid), dict) else {}
        due = (CURVE_AFTER <= age <= CURVE_DAYS and now.timestamp() - float(last.get("at") or 0) >= CURVE_EVERY
               and (by[vid].get("engaged") or 0) > 0)
        if due and read < CURVE_MAX and not stop:
            read += 1
            try:
                rows = _yta(auth, first, end, metrics="audienceWatchRatio", dimensions="elapsedVideoTimeRatio",
                            filters=f"video=={vid}")
            except PlatformError as e:
                note(acct, "youtube_shorts", f"retention curve not read: {str(e)[:160]}")
                stop = True               # the curves already kept still go on their Shorts
                rows = []
            c = curve([(d.get("elapsedVideoTimeRatio"), d.get("audienceWatchRatio")) for d in rows])
            if c:
                kept[vid] = last = {"at": int(now.timestamp()), "curve": c}
        if last.get("curve"):
            by[vid]["curve"] = last["curve"]
    if read:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(kept, separators=(",", ":")))
        os.replace(tmp, path)
