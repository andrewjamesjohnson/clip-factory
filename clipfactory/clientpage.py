"""A client's own page, served by the review server under that account's secret path:

    http://127.0.0.1:8781/c/<secret>/        factory.py account page <slug> prints it (--new makes a new one)

It shows ONE account: its clips waiting for a sign-off (Approve or Decline), what is coming up and what went out, and
its numbers (this month against last, clips made against the plan, the top posts, every post). Never money, never
another account, never a path on the Mac, never the owner's notes. Nothing here exists until a client account is
added (accounts.py): with none, every request goes on to the owner's pages exactly as before.

Who signs: the client's own names on the account's approvers list (accounts.client_signers). With none there, the
page is view only and the owner signs in Clip Review. The signature is the factory's own (signoff.approve: the
clip, its words and its time, as the page showed them), so an Approve here is the same as one in Clip Review.

Safety:
  - the secret is 43 random characters, compared in constant time; a wrong one is not this page's business (the
    request goes on to the owner's page, which answers it as it always has);
  - it answers only on this Mac's own address, plus the account's own "page_hosts" (a tunnel's name, which the owner
    adds on purpose: CLIENTS.md). While any client has one, a request that came through a proxy (it carries a
    forwarding header) is refused on every one of the owner's pages;
  - a write needs the page's own token (derived from the secret), a JSON body and a same-origin request, as Clip
    Review's do; files are served by date and slot from that account's own board only;
  - no-referrer and noindex, so the secret never leaves in a link.
"""
from __future__ import annotations

import hashlib
import hmac
import html
import json
import re
import urllib.parse
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from . import accounts as AC
from . import board as B
from . import config as C
from . import copytext as T
from . import outlets as O

PROXY_HEADERS = ("X-Forwarded-For", "X-Forwarded-Host", "X-Forwarded-Proto", "X-Real-IP", "Forwarded",
                 "CF-Connecting-IP", "CF-Ray", "True-Client-IP", "Tailscale-User-Login", "Tailscale-Funnel-Request",
                 "Via")
POLL_SECONDS = 20
CSP = ("default-src 'self'; style-src 'unsafe-inline'; script-src 'unsafe-inline'; media-src 'self'; img-src 'self'; "
       "base-uri 'none'; form-action 'none'; frame-ancestors 'none'")
HEADERS = {"Content-Security-Policy": CSP, "Referrer-Policy": "no-referrer", "X-Robots-Tag": "noindex, nofollow",
           "X-Frame-Options": "DENY"}
NAMES = {"youtube_shorts": "YouTube", "tiktok": "TikTok", "facebook_reels": "Facebook", "instagram_reels": "Instagram"}
DASH = {"youtube": "YouTube", "tiktok": "TikTok", "facebook": "Facebook", "instagram": "Instagram"}


def esc(s: object) -> str:
    return html.escape(str(s), quote=True)


# ------------------------------------------------------------------------------------------------ routing
def tunnelled(cfg: C.Config) -> bool:
    return any(AC.page_hosts(a) for a in AC.clients(cfg))


def from_outside(handler, cfg: C.Config) -> bool:
    """A request that came through a proxy (a client's tunnel) while a client page is reachable from outside."""
    return tunnelled(cfg) and any(handler.headers.get(h) for h in PROXY_HEADERS)


def match(cfg: C.Config, path: str) -> Optional[Tuple[C.Account, str]]:
    """(the client account whose secret opens this path, the rest of the path) or None."""
    if not path.startswith("/c/"):
        return None
    secret, _, rest = path[3:].partition("/")
    if len(secret) < 32:
        return None
    hit = None
    for a in AC.clients(cfg):
        s = AC.secret(a)
        if s and hmac.compare_digest(s.encode(), secret.encode()):
            hit = a
    return (hit, "/" + rest) if hit else None


def allowed_hosts(acct: C.Account, hosts) -> set:
    return {h.lower() for h in hosts} | set(AC.page_hosts(acct))


def act_token(acct: C.Account) -> str:
    return hmac.new(AC.secret(acct).encode(), b"client-page-act", hashlib.sha256).hexdigest()[:32]


def get(handler, cfg: C.Config, hosts) -> bool:
    """The review server's GET, first: True when this answered it (a client page, or a proxied request refused)."""
    u = urllib.parse.urlparse(handler.path)
    hit = match(cfg, u.path)
    if hit is None:
        if from_outside(handler, cfg):         # through a client's tunnel: never the owner's pages
            handler._send(403, b"loopback only")
            return True
        return False
    acct, rest = hit
    if str(handler.headers.get("Host", "")).lower() not in allowed_hosts(acct, hosts):
        handler._send(403, b"not here")
        return True
    q = dict(urllib.parse.parse_qsl(u.query))
    try:
        if rest == "/":
            handler._send(200, page(cfg, acct).encode(), "text/html; charset=utf-8", HEADERS)
        elif rest == "/live":
            main = body(cfg, acct)
            handler._json(200, {"stamp": hashlib.sha1(main.encode()).hexdigest()[:16], "html": main})
        elif rest == "/file":
            p = file_for(acct, q.get("d", ""), q.get("s", ""), q.get("k", ""))
            if p is None:
                handler._send(404, b"not found")
            else:
                handler._file(p, versioned=bool(q.get("v")))
        elif rest == "/favicon.ico":
            handler._send(204, b"")
        else:
            handler._send(404, b"not found")
    except Exception as e:  # noqa: BLE001  (no detail of the Mac in the answer)
        handler._send(500, f"error ({type(e).__name__})".encode())
    return True


def post(handler, cfg: C.Config, hosts) -> bool:
    """The review server's POST, first: True when this answered it."""
    path = urllib.parse.urlparse(handler.path).path
    hit = match(cfg, path)
    if hit is None:
        if from_outside(handler, cfg):
            handler._json(403, {"ok": False, "message": "refused: not from the review page"})
            return True
        return False
    acct, rest = hit
    host = str(handler.headers.get("Host", "")).lower()
    origin = handler.headers.get("Origin", "")
    if host not in allowed_hosts(acct, hosts) or (origin and urllib.parse.urlparse(origin).netloc.lower() != host):
        handler._json(403, {"ok": False, "message": "refused: not from this page"})
        return True
    if not hmac.compare_digest(str(handler.headers.get("X-Token", "")), act_token(acct)):
        handler._json(403, {"ok": False, "stale": True, "message": "This page is out of date. Reloading..."})
        return True
    if rest != "/act":
        handler._json(404, {"ok": False, "message": "not found"})
        return True
    n = int(handler.headers.get("Content-Length") or 0)
    if n > 4096:
        handler._json(413, {"ok": False, "message": "too large"})
        return True
    try:
        data = json.loads(handler.rfile.read(n) or b"{}")
        if not isinstance(data, dict):
            raise ValueError("not an object")
        ok, msg = act(cfg, acct, data)
    except SystemExit as ex:
        return _said(handler, 400, False, str(ex))
    except (KeyError, ValueError, TypeError):
        return _said(handler, 400, False, "That did not work. Reload the page and try again.")
    except Exception as ex:  # noqa: BLE001
        return _said(handler, 500, False, f"That did not work ({type(ex).__name__}). Try again in a minute.")
    return _said(handler, 200, ok, msg)


def _said(handler, code: int, ok: bool, msg: str) -> bool:
    handler._json(code, {"ok": ok, "message": plain(msg)})
    return True


def plain(msg: str) -> str:
    """Words for the client: no paths on the Mac, no command lines."""
    msg = re.sub(r"(~|/Users|/private|/var|/tmp)/\S+", "(a file on the Mac)", str(msg))
    msg = re.sub(r"`?(python3 )?\S*factory\.py[^`\n]*`?", "", msg)
    return " ".join(msg.split()) or "Done."


# ------------------------------------------------------------------------------------------------ actions
def act(cfg: C.Config, acct: C.Account, data: dict, now: Optional[datetime] = None) -> Tuple[bool, str]:
    from . import poster as P
    from . import signoff as S
    now = now or cfg.now()
    op = str(data.get("op") or "")
    day = date.fromisoformat(str(data.get("date") or ""))
    slot = C.normal_slot(str(data.get("slot") or ""))
    signers = AC.client_signers(cfg, acct)
    if not signers:
        return False, f"{_signs(AC.approvers(cfg, acct))} these clips. Tell them if one should not go out."
    who = signers[0]
    s = ((B.read(acct, day) or {}).get("slots") or {}).get(slot)
    if not s or not s.get("clip"):
        return False, "That clip is not here any more. Reload the page."
    if s.get("status") not in B.SIGNABLE:
        return False, "That clip is not waiting for you any more. Reload the page."
    if op == "approve":
        if _too_late(s, now):
            return False, "Its time has passed: it can no longer go out."
        lines = S.approve(cfg, acct, day, [slot], who, now=now, seen={slot: str(data.get("seen") or "")})
        sent = [ln for ln in lines if "goes out" in ln]
        if sent:
            P.kick(cfg)
            return True, f"Approved. It goes out {P.goes_out(s, now)}" + _local_note(cfg, acct) + "."
        why = " ".join(ln.split("NOT signed,", 1)[-1].strip() for ln in lines if "NOT" in ln)
        if "changed since the page showed it" in why:
            return False, "It changed since this page showed it. Watch it again, then approve."
        return False, "Not approved: " + (why or "it could not be signed. Reload the page.")
    if op == "decline":
        reason = " ".join(str(data.get("reason") or "").split())[:500]
        lines = S.decline(cfg, acct, day, [slot], who, reason)
        ok = any(ln.endswith("declined") for ln in lines)
        return ok, ("Declined. It will not go out." if ok else "It could not be declined. Reload the page.")
    return False, "Unknown action."


def _signs(names: List[str]) -> str:
    """'Andrew signs', 'Andrew and Jane sign'."""
    return f"{' and '.join(names)} {'signs' if len(names) == 1 else 'sign'}"


def _local_note(cfg: C.Config, acct: C.Account) -> str:
    tz = AC.timezone_of(cfg, acct)
    return "" if tz == cfg.timezone else f" ({cfg.timezone.split('/')[-1].replace('_', ' ')} time)"


# ------------------------------------------------------------------------------------------------ the data
def _tz(cfg: C.Config, acct: C.Account):
    try:
        from zoneinfo import ZoneInfo
        return ZoneInfo(AC.timezone_of(cfg, acct))
    except Exception:  # noqa: BLE001
        return cfg.tz()


def _when(cfg: C.Config, acct: C.Account, iso: str) -> str:
    try:
        d = datetime.fromisoformat(str(iso).replace("Z", "+00:00")).astimezone(_tz(cfg, acct))
    except (TypeError, ValueError):
        return ""
    return f"{d.strftime('%a %b')} {d.day}, {d.strftime('%H:%M')}"


def _too_late(s: dict, now: datetime) -> bool:
    from . import poster as P
    try:
        return now - datetime.fromisoformat(s["publish_at"]) > P.LATE_LIMIT
    except (KeyError, TypeError, ValueError):
        return True


def _title(s: dict) -> str:
    copy = s.get("copy") or {}
    c = copy.get("youtube_shorts") or next(iter(copy.values()), None)
    if c:
        try:
            t = T.parse(Path(c["path"]).read_text())["title"].strip()
            if t:
                return t
        except (OSError, KeyError, TypeError):
            pass
    return str(s.get("slug") or "").replace("-", " ")


def _missing(s: dict) -> bool:
    if not Path((s.get("clip") or {}).get("path", "")).is_file():
        return True
    return any(not Path(c.get("path", "")).is_file() for c in (s.get("copy") or {}).values())


def boards(acct: C.Account, first: date, last: date) -> List[Tuple[date, dict]]:
    out = []
    for p in B.all_boards(acct):
        try:
            d = date.fromisoformat(p.stem)
        except ValueError:
            continue
        if first <= d <= last:
            try:
                out.append((d, json.loads(p.read_text())))
            except (OSError, ValueError):
                continue
    return out


def file_for(acct: C.Account, day: str, slot: str, kind: str) -> Optional[Path]:
    """A clip or cover of THIS account's board, by date and slot: never a path from the request."""
    try:
        d = date.fromisoformat(day)
        k = C.normal_slot(slot)
    except (ValueError, SystemExit):
        return None
    s = ((B.read(acct, d) or {}).get("slots") or {}).get(k) or {}
    p = (s.get("clip") or {}).get("path") if kind == "clip" else s.get("cover") if kind == "cover" else None
    if not p:
        return None
    p = Path(p)
    return p if p.is_file() and p.suffix.lower() in (".mp4", ".mov", ".m4v", ".jpg", ".jpeg", ".png") else None


def _furl(acct_secret: str, day: date, s: dict, kind: str) -> str:
    p = (s.get("clip") or {}).get("path") if kind == "clip" else s.get("cover")
    try:
        st = Path(str(p)).stat()
        v = f"{st.st_size:x}{st.st_mtime_ns:x}"
    except (OSError, TypeError):
        v = "0"
    q = urllib.parse.urlencode({"d": day.isoformat(), "s": s["slot"], "k": kind, "v": v})
    return f"/c/{acct_secret}/file?{q}"


def _out(s: dict) -> Dict[str, dict]:
    return {p: r for p, r in (s.get("outlets") or {}).items() if isinstance(r, dict) and (r.get("post_id") or r.get("video_id"))}


def report(cfg: C.Config, acct: C.Account, now: Optional[datetime] = None) -> dict:
    """The account's numbers, from its own boards and the nightly read (03:30). No money, no other account."""
    from . import stats as ST
    now = now or cfg.now()
    tz = _tz(cfg, acct)
    one = AC.only(cfg, acct)
    try:
        posts = ST.posts_from_boards(one, days=3650)
        seen = {p["id"] for p in posts}
        posts += [p for p in ST.pulled_posts(one) if p["id"] not in seen]
    except Exception:  # noqa: BLE001  (the page shows its clips even when the numbers cannot be read)
        posts = []
    today = now.astimezone(tz).date()
    m0 = today.replace(day=1)
    m_last = (m0 - timedelta(days=1)).replace(day=1)

    def month_of(p: dict) -> Optional[date]:
        try:
            return datetime.fromisoformat(str(p.get("at")).replace("Z", "+00:00")).astimezone(tz).date().replace(day=1)
        except (TypeError, ValueError):
            return None

    def totals(month: date) -> Dict[str, Dict[str, int]]:
        out: Dict[str, Dict[str, int]] = {}
        for p in posts:
            if month_of(p) != month:
                continue
            t = out.setdefault(p["platform"], {"posts": 0, "views": 0, "likes": 0, "comments": 0, "shares": 0})
            t["posts"] += 1
            for k in ("views", "likes", "comments", "shares"):
                t[k] += int(p.get(k) or 0)
        return out

    plan = {"slots": 0, "made": 0, "signed": 0, "out": 0}
    for d, bd in boards(acct, m0, today):
        for s in (bd.get("slots") or {}).values():
            plan["slots"] += 1
            plan["made"] += 1 if s.get("clip") else 0
            plan["signed"] += 1 if s.get("status") in B.SIGNED else 0
            plan["out"] += 1 if _out(s) else 0
    ahead = {"scheduled": 0, "waiting": 0, "open": 0}
    for d, bd in boards(acct, today, today + timedelta(days=7)):
        for s in (bd.get("slots") or {}).values():
            st = s.get("status")
            if st in B.SIGNED:
                ahead["scheduled"] += 1
            elif st in B.SIGNABLE and s.get("clip"):
                ahead["waiting"] += 1
            elif st in (B.OPEN, B.MAKING, B.FAILED, B.REFUSED):
                ahead["open"] += 1
    this = [p for p in posts if month_of(p) == m0]
    top = sorted(this or posts, key=lambda p: -int(p.get("views") or 0))[:5]
    every = sorted(posts, key=lambda p: str(p.get("at") or ""), reverse=True)[:60]
    slots: Dict[str, List[int]] = {}
    for p in posts:
        if p.get("slot"):
            slots.setdefault(str(p["slot"]), []).append(int(p.get("views") or 0))
    best = max(((k, sum(v) / len(v)) for k, v in slots.items() if len(v) >= 2), key=lambda x: x[1], default=None)
    return {"this": totals(m0), "last": totals(m_last), "month": m0, "last_month": m_last, "plan": plan,
            "ahead": ahead, "top": top, "every": every, "best_slot": best, "tz": AC.timezone_of(cfg, acct)}


# ------------------------------------------------------------------------------------------------ the page
def _n(x: int) -> str:
    return f"{x:,}"


def _card(cfg: C.Config, acct: C.Account, day: date, s: dict, signer: str) -> str:
    sec = AC.secret(acct)
    cover = s.get("cover")
    poster = f' poster="{esc(_furl(sec, day, s, "cover"))}"' if cover and Path(cover).is_file() else ""
    video = (f'<video controls playsinline preload="{"none" if poster else "metadata"}"{poster} '
             f'src="{esc(_furl(sec, day, s, "clip"))}" aria-label="{esc(_title(s))}"></video>')
    secs = float((s.get("clip") or {}).get("seconds") or 0)
    words = []
    for plat, c in (s.get("copy") or {}).items():
        try:
            w = T.for_platform(Path(c["path"]).read_text(), plat)
        except (OSError, KeyError, TypeError):
            continue
        head = f"{w['title']}\n\n" if w.get("title") else ""
        words.append(f'<div class="copy"><b>{esc(NAMES.get(plat, plat))}</b>{esc(head + w["caption"])}</div>')
    btns = ""
    if signer:
        btns = ('<div class="btns"><button class="go" data-op="approve">Approve</button>'
                '<button data-op="decline">Decline</button></div>')
    return (f'<article class="card" data-date="{day.isoformat()}" data-slot="{esc(s["slot"])}" '
            f'data-key="{esc(B.seen_key(s))}">{video}<div class="body"><div class="title">{esc(_title(s))}</div>'
            f'<div class="meta">{esc(_when(cfg, acct, s["publish_at"]))} · {secs:.0f}s</div>{btns}'
            + (f'<details><summary>Words for each platform</summary>{"".join(words)}</details>' if words else "")
            + "</div></article>")


def _row(cfg: C.Config, acct: C.Account, s: dict) -> str:
    outs = _out(s)
    bits = []
    for plat in acct.platforms:
        r = outs.get(plat)
        name = NAMES.get(plat, plat)
        if r:
            url = str(r.get("url") or "")
            bits.append(f'<a href="{esc(url)}" target="_blank" rel="noopener noreferrer">{esc(name)}</a>'
                        if url.startswith("https://") else f"<span>{esc(name)} posted</span>")
        elif O.covers_slot(acct, plat, s):
            bits.append(f"<span>{esc(name)} at its time</span>")
        else:
            bits.append(f"<span>{esc(name)} by hand</span>")
    return (f'<li><span class="when">{esc(_when(cfg, acct, s["publish_at"]))}</span>'
            f'<span class="t">{esc(_title(s))}</span><span class="plats">{" · ".join(bits)}</span></li>')


def body(cfg: C.Config, acct: C.Account, now: Optional[datetime] = None) -> str:
    now = now or cfg.now()
    today = now.date()
    signers = AC.client_signers(cfg, acct)
    signer = signers[0] if signers else ""
    waiting, coming, out = [], [], []
    for d, bd in boards(acct, today - timedelta(days=30), date.max):
        for s in sorted((bd.get("slots") or {}).values(), key=lambda x: str(x.get("slot", ""))):
            try:
                if not s.get("clip"):
                    continue
                st = s.get("status")
                if st in B.SIGNABLE and not _too_late(s, now) and not _missing(s) and d >= today - timedelta(days=1):
                    waiting.append(_card(cfg, acct, d, s, signer))
                elif st in B.SIGNED:
                    at = datetime.fromisoformat(s["publish_at"])
                    (coming if at > now else out).append((s["publish_at"], _row(cfg, acct, s)))
            except (KeyError, TypeError, ValueError, OSError):
                continue
    who = (f"Signing as <b>{esc(signer)}</b>. Approve sends a clip out at its time; Decline keeps it off."
           if signer else f"{esc(_signs(AC.approvers(cfg, acct)))} these clips. Tell them if one should not go out.")
    parts = [f'<section class="block"><h2>Waiting for you <span class="n">{len(waiting)}</span></h2>'
             f'<p class="sub">{who}</p>'
             + (f'<div class="grid">{"".join(waiting)}</div>' if waiting else '<p class="calm">Nothing waits for you.</p>')
             + "</section>"]
    coming.sort()
    out.sort(reverse=True)
    parts.append('<section class="block"><h2>Coming up</h2>'
                 + (f'<ul class="rows">{"".join(r for _, r in coming)}</ul>' if coming else
                    '<p class="calm">Nothing is scheduled yet.</p>') + "</section>")
    parts.append('<section class="block"><h2>Earlier</h2>'
                 + (f'<ul class="rows">{"".join(r for _, r in out[:30])}</ul>' if out else
                    '<p class="calm">Nothing signed has reached its time yet.</p>') + "</section>")
    parts.append(_numbers(cfg, acct, now))
    return "".join(parts)


def _numbers(cfg: C.Config, acct: C.Account, now: datetime) -> str:
    r = report(cfg, acct, now)
    m, lm = r["month"], r["last_month"]
    plats = [p for p in ("youtube", "tiktok", "facebook", "instagram") if p in r["this"] or p in r["last"]]

    def cell(t: Dict[str, Dict[str, int]], p: str, k: str) -> str:
        return _n((t.get(p) or {}).get(k, 0))

    rows = "".join(f'<tr><th scope="row">{esc(DASH[p])}</th><td>{cell(r["this"], p, "posts")}</td>'
                   f'<td>{cell(r["this"], p, "views")}</td><td>{cell(r["last"], p, "views")}</td>'
                   f'<td>{cell(r["this"], p, "likes")}</td><td>{cell(r["this"], p, "comments")}</td>'
                   f'<td>{cell(r["this"], p, "shares")}</td></tr>' for p in plats)
    table = (f'<div class="tw"><table><thead><tr><th></th><th>Posts</th><th>Views {esc(m.strftime("%b"))}</th>'
             f'<th>Views {esc(lm.strftime("%b"))}</th><th>Likes</th><th>Comments</th><th>Shares</th></tr></thead>'
             f'<tbody>{rows}</tbody></table></div>' if rows else '<p class="calm">No numbers yet: they are read each night '
             'once a platform posts by itself.</p>')
    pl, ah = r["plan"], r["ahead"]
    tiles = (f'<div class="tiles"><div><b>{pl["made"]} of {pl["slots"]}</b><span>clips made this month</span></div>'
             f'<div><b>{pl["out"]}</b><span>gone out this month</span></div>'
             f'<div><b>{ah["scheduled"]}</b><span>scheduled, next 7 days</span></div>'
             f'<div><b>{ah["waiting"]}</b><span>waiting for a sign-off</span></div></div>')
    top = "".join(f'<li><span class="when">{esc(_n(int(p.get("views") or 0)))} views</span>'
                  f'<span class="t">{esc(p.get("title") or "")}</span><span class="plats">'
                  + (f'<a href="{esc(p["url"])}" target="_blank" rel="noopener noreferrer">{esc(DASH.get(p["platform"], p["platform"]))}</a>'
                     if str(p.get("url") or "").startswith("https://") else esc(DASH.get(p["platform"], p["platform"])))
                  + "</span></li>" for p in r["top"])
    every = "".join(f'<li><span class="when">{esc(_when(cfg, acct, p.get("at")))}</span>'
                    f'<span class="t">{esc(p.get("title") or "")}</span><span class="plats">'
                    + (f'<a href="{esc(p["url"])}" target="_blank" rel="noopener noreferrer">{esc(DASH.get(p["platform"], p["platform"]))}</a>'
                       if str(p.get("url") or "").startswith("https://") else esc(DASH.get(p["platform"], p["platform"])))
                    + f' · {esc(_n(int(p.get("views") or 0)))}</span></li>' for p in r["every"])
    best = (f'<p class="sub">Best time so far: {esc(r["best_slot"][0])} (about {_n(int(r["best_slot"][1]))} views a post).</p>'
            if r["best_slot"] else "")
    return (f'<section class="block" id="numbers"><h2>Numbers</h2><p class="sub">{esc(m.strftime("%B %Y"))} so far, '
            f'against {esc(lm.strftime("%B"))}. Read each night; times are {esc(r["tz"].split("/")[-1].replace("_", " "))} '
            f'time.</p>{tiles}{table}{best}'
            + (f'<h3>Top posts</h3><ul class="rows">{top}</ul>' if top else "")
            + (f'<details><summary>Every post</summary><ul class="rows">{every}</ul></details>' if every else "")
            + "</section>")


CSS = """
:root{color-scheme:light dark;--bg:#f4f5f7;--card:#fff;--ink:#14171c;--sub:#5b6472;--line:#e1e4e9;--blue:#2459e0;
--ok:#157347;--bad:#b3261e}
@media (prefers-color-scheme:dark){:root{--bg:#0d1015;--card:#161a21;--ink:#e7ebf0;--sub:#9ba4b1;--line:#272e38;
--blue:#7aa2ff;--ok:#5cc28a;--bad:#ff8a80}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:15px/1.5 -apple-system,BlinkMacSystemFont,
"Helvetica Neue",Arial,sans-serif}header{padding:20px 16px 8px;max-width:1100px;margin:0 auto}h1{margin:0;font-size:22px}
header p{margin:4px 0 0;color:var(--sub)}main{max-width:1100px;margin:0 auto;padding:0 16px 48px}
.block{margin-top:22px}h2{font-size:17px;margin:0 0 6px;display:flex;gap:8px;align-items:center}h3{font-size:15px;margin:18px 0 6px}
.n{font-size:13px;color:var(--sub);font-weight:500}.sub,.calm{color:var(--sub);margin:0 0 10px}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(260px,1fr));gap:14px}
.card{background:var(--card);border:1px solid var(--line);border-radius:6px;overflow:hidden}
.card video{width:100%;aspect-ratio:9/16;max-height:70vh;object-fit:contain;background:#000;display:block}.body{padding:10px 12px 12px}
.title{font-weight:600}.meta{color:var(--sub);font-size:13px;margin:2px 0 8px}
.btns{display:flex;gap:8px;margin:6px 0}button{font:inherit;padding:7px 14px;border-radius:6px;border:1px solid var(--line);
background:var(--card);color:var(--ink);cursor:pointer}button.go{background:var(--blue);border-color:var(--blue);color:#fff}
button:disabled{opacity:.5}details{margin-top:8px}summary{cursor:pointer;color:var(--sub)}
.copy{white-space:pre-wrap;font-size:13px;border-top:1px solid var(--line);padding:6px 0}.copy b{display:block}
.rows{list-style:none;margin:0;padding:0;background:var(--card);border:1px solid var(--line);border-radius:6px}
.rows li{display:grid;grid-template-columns:150px 1fr auto;gap:10px;padding:8px 12px;border-top:1px solid var(--line)}
.rows li:first-child{border-top:0}.when{color:var(--sub);font-size:13px}.plats{font-size:13px;color:var(--sub)}
a{color:var(--blue)}.tiles{display:grid;grid-template-columns:repeat(auto-fill,minmax(160px,1fr));gap:10px;margin:8px 0 12px}
.tiles div{background:var(--card);border:1px solid var(--line);border-radius:6px;padding:10px 12px}
.tiles b{display:block;font-size:20px}.tiles span{color:var(--sub);font-size:13px}
.tw{overflow-x:auto}table{width:100%;border-collapse:collapse;background:var(--card);border:1px solid var(--line);border-radius:6px;font-size:14px}
th,td{padding:7px 10px;text-align:right;border-top:1px solid var(--line)}th:first-child{text-align:left}
thead th{color:var(--sub);font-weight:500;border-top:0}
#toast{position:fixed;left:50%;bottom:18px;transform:translateX(-50%);background:var(--ink);color:var(--bg);padding:10px 16px;
border-radius:6px;max-width:90vw}#toast.bad{background:var(--bad);color:#fff}
@media (max-width:600px){.rows li{grid-template-columns:1fr;gap:2px}.grid{grid-template-columns:1fr}
th,td{padding:6px 6px;font-size:13px}}
"""

JS = """
const TOKEN=__TOKEN__, BASE=__BASE__, POLL=__POLL__;
let STAMP=__STAMP__, busy=false, tt=0;
const toastEl=document.getElementById('toast');
function toast(m,bad){toastEl.textContent=m;toastEl.className=bad?'bad':'';toastEl.hidden=false;clearTimeout(tt);
  tt=setTimeout(()=>{toastEl.hidden=true},bad?12000:6000)}
function playing(){return [...document.querySelectorAll('video')].some(v=>!v.paused)}
async function refresh(force){
  if(busy||(!force&&(document.hidden||playing())))return;
  try{const r=await fetch(BASE+'live',{cache:'no-store'});if(!r.ok)return;const d=await r.json();
    if(d.stamp!==STAMP){STAMP=d.stamp;document.getElementById('main').innerHTML=d.html}}catch(e){}
}
async function act(body,btn){
  const card=btn.closest('.card');const btns=[...card.querySelectorAll('button')];btns.forEach(b=>b.disabled=true);
  busy=true;toast('Working...');
  try{const r=await fetch(BASE+'act',{method:'POST',headers:{'Content-Type':'application/json','X-Token':TOKEN},
      body:JSON.stringify(body)});
    let d;try{d=await r.json()}catch(e){d={ok:false,message:'Reload the page and try again.'}}
    if(r.status===403&&d.stale){toast(d.message);setTimeout(()=>location.reload(),900);return}
    toast(d.message||(r.ok?'Done.':'That did not work.'),!(r.ok&&d.ok));
  }catch(e){toast('Could not reach the page. Try again in a minute.',true)}
  finally{busy=false;btns.forEach(b=>b.disabled=false)}
  await refresh(true);
}
document.addEventListener('click',ev=>{
  const b=ev.target.closest('button[data-op]');if(!b||b.disabled)return;
  const c=b.closest('.card'),base={date:c.dataset.date,slot:c.dataset.slot};
  if(b.dataset.op==='approve'){act(Object.assign(base,{op:'approve',seen:c.dataset.key}),b);return}
  if(b.dataset.op==='decline'){const why=prompt('Decline this clip. What is wrong with it? (optional)','');
    if(why===null)return;act(Object.assign(base,{op:'decline',reason:why}),b)}
});
document.addEventListener('play',ev=>{document.querySelectorAll('video').forEach(v=>{if(v!==ev.target&&!v.paused)v.pause()})},true);
document.addEventListener('visibilitychange',()=>{if(!document.hidden)refresh(true)});
setInterval(()=>refresh(false),POLL*1000);
"""


def page(cfg: C.Config, acct: C.Account, now: Optional[datetime] = None) -> str:
    main = body(cfg, acct, now)
    js = (JS.replace("__TOKEN__", json.dumps(act_token(acct))).replace("__BASE__", json.dumps(f"/c/{AC.secret(acct)}/"))
          .replace("__POLL__", str(POLL_SECONDS))
          .replace("__STAMP__", json.dumps(hashlib.sha1(main.encode()).hexdigest()[:16])))
    return ("<!doctype html><html lang=en><head><meta charset=utf-8>"
            "<meta name=viewport content='width=device-width,initial-scale=1'><meta name=robots content=noindex>"
            f"<title>{esc(acct.name)} clips</title><style>{CSS}</style></head><body>"
            f"<header><h1>{esc(acct.name)}</h1><p>Your clips: watch each one, then approve it or decline it.</p></header>"
            f'<main id="main">{main}</main><div id="toast" role="status" aria-live="polite" hidden></div>'
            f"<script>{js}</script></body></html>")
