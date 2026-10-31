import os, tempfile; os.environ["CLIP_FACTORY_OFFLINE"] = "1"; os.environ.setdefault("CLIP_FACTORY_STATE", tempfile.mkdtemp())  # noqa: E401,E702
# Stand-ins the social layer's tests share (2026-10-19): fake Meta Graph and YouTube answers in place of
# direct._http, a fake card engine and a fake yt-dlp, a clock, a throwaway config, clips made ready or posted on a
# board, and the review page served on a free port. Not a test file itself (unittest only collects test*.py). Nothing
# here can reach the network: direct._http is replaced, and an unexpected call raises AssertionError.
#
#     g = FakeGraph().on("GET", r"/17890/comments$", {"data": []})
#     g.on("POST", r"/comments$", FakeGraph.meta_error(368, msg="(#368) Temporarily blocked"))
#     patch_http(self, graph=g, yt=FakeYouTube())          # until the test ends
#     cfg = cfg_in_tmp(self)                               # self.cfg, self.tmp; C.STATE and the key folders moved
#     slot_posted(self, cfg.account("gaming"), DAY, "07:00")
#     get, post = serve(self, cfg)

import copy
import json
import re
import shutil
import subprocess
import sys
import threading
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, Dict, List, Optional, Sequence
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from clipfactory import board as B          # noqa: E402
from clipfactory import config as C         # noqa: E402
from clipfactory import direct as D         # noqa: E402
from clipfactory import outlets as O        # noqa: E402
from clipfactory import registry as REG     # noqa: E402

META_HOST = "graph.facebook.com"
YT_HOST = "www.googleapis.com"
YT_TOKEN = "fake-yt-token"
OUTLETS = ("facebook_reels", "instagram_reels", "youtube_shorts")


# ------------------------------------------------------------------------------------------------------ fake platforms
from politics_slots import pin_politics  # noqa: E402  the Politics day these tests were written on

class _FakeApi:
    """Answers in place of direct._http for one platform. `.on(method, regex, answer)` adds a route; the regex is
    searched in the call's path (the API version taken off: "/17890/comments") and then in its whole URL; the first
    route that matches answers. An answer is a dict (returned as a copy), a callable (called with the call's record;
    what it returns is answered the same way) or an Exception (raised). `times=1` answers once. Every call is kept in
    `.calls`; a DELETE, or a call no route answers, raises AssertionError (the social layer never deletes, and a test
    names every call it expects). Each answer's headers go through direct._note_usage, so direct.last_usage() says
    what a real answer with those headers would."""

    HOST = ""
    STRIP: Sequence[str] = ()

    def __init__(self):
        self.routes: List[dict] = []
        self.calls: List[dict] = []
        self.unmatched: List[str] = []

    def on(self, method: str, pattern: str, answer, headers: Optional[dict] = None,
           times: Optional[int] = None) -> "_FakeApi":
        self.routes.append({"method": method.upper(), "re": re.compile(pattern), "answer": answer,
                            "headers": dict(headers or {}), "left": times})
        return self

    def _path(self, path: str) -> str:
        for p in self.STRIP:
            if path.startswith(p):
                return path[len(p):] or "/"
        return path

    def __call__(self, method: str, url: str, params: Optional[dict] = None, form: Optional[dict] = None,
                 body: Optional[dict] = None, data: Optional[bytes] = None, headers: Optional[dict] = None,
                 timeout: int = 120) -> dict:
        method = method.upper()
        u = urllib.parse.urlparse(url)
        q = dict(urllib.parse.parse_qsl(u.query))
        q.update(params or {})
        call = {"method": method, "url": url.split("?")[0], "host": u.netloc, "path": self._path(u.path),
                "params": q, "form": dict(form) if form is not None else None, "body": copy.deepcopy(body),
                "data": data, "headers": dict(headers or {}), "timeout": timeout}
        self.calls.append(call)
        if method == "DELETE":
            raise AssertionError(f"{type(self).__name__}: the social layer never deletes ({call['path']})")
        for r in self.routes:
            if r["method"] != method or r["left"] == 0:
                continue
            if not (r["re"].search(call["path"]) or r["re"].search(call["url"])):
                continue
            if r["left"] is not None:
                r["left"] -= 1
            D._note_usage(u.netloc, r["headers"])
            return self._answer(r["answer"], call)
        self.unmatched.append(f"{method} {call['path']}")
        D._note_usage(u.netloc, {})
        raise AssertionError(f"{type(self).__name__}: no answer for {method} {call['path']}")

    def _answer(self, ans, call: dict):
        if isinstance(ans, BaseException):
            raise ans
        if callable(ans):
            return self._answer(ans(call), call)
        return {} if ans is None else copy.deepcopy(ans)

    def paths(self, method: Optional[str] = None) -> List[str]:
        """The calls' paths, in order ("GET /17890/comments")."""
        return [f"{c['method']} {c['path']}" for c in self.calls if method is None or c["method"] == method]


class FakeGraph(_FakeApi):
    """Meta's Graph API (graph.facebook.com and rupload.facebook.com)."""

    HOST = META_HOST
    STRIP = ("/" + D.GRAPH.rsplit("/", 1)[-1],)

    @staticmethod
    def meta_error(code: int, sub: Optional[int] = None, msg: str = "An unexpected error has occurred.",
                   http: int = 400, trace: str = "AfakeTrace0001", transient: bool = False,
                   kind: str = "OAuthException") -> D.PlatformError:
        """The PlatformError direct._http raises for a Meta error body with these numbers."""
        return D.PlatformError(f"HTTP {http} from {META_HOST}: {msg}", status=http, host=META_HOST, meta_code=code,
                               meta_subcode=sub, meta_type=kind, transient=transient, trace=trace)

    @staticmethod
    def usage(buc_pct: Optional[float] = None, regain_min: Optional[float] = None, app_pct: Optional[float] = None,
              page_pct: Optional[float] = None, retry_after: Optional[int] = None) -> Dict[str, str]:
        """Answer headers that say these rate-limit numbers (pass them as `headers=` to on())."""
        h: Dict[str, str] = {}
        if buc_pct is not None or regain_min is not None:
            entry = {"type": "pages", "call_count": buc_pct or 0, "total_cputime": 0, "total_time": 0,
                     "estimated_time_to_regain_access": regain_min or 0}
            h["X-Business-Use-Case-Usage"] = json.dumps({"1234": [entry]})
        if app_pct is not None:
            h["X-App-Usage"] = json.dumps({"call_count": app_pct, "total_cputime": 0, "total_time": 0})
        if page_pct is not None:
            h["X-Page-Usage"] = json.dumps({"call_count": page_pct, "total_cputime": 0, "total_time": 0})
        if retry_after is not None:
            h["Retry-After"] = str(retry_after)
        return h


class FakeYouTube(_FakeApi):
    """The YouTube Data API (www.googleapis.com/youtube/v3) and YouTube Analytics (/v2/reports)."""

    HOST = YT_HOST
    STRIP = ("/youtube/v3",)

    @staticmethod
    def google_error(reason: str, http: int = 403, msg: str = "") -> D.PlatformError:
        """The PlatformError direct._http raises for a Google error body with errors[0].reason."""
        return D.PlatformError(f"HTTP {http} from {YT_HOST}: {msg or reason}", status=http, host=YT_HOST,
                               reason=reason)


meta_error = FakeGraph.meta_error
google_error = FakeYouTube.google_error


def patch_http(test, graph: Optional[FakeGraph] = None, yt: Optional[FakeYouTube] = None) -> Callable:
    """direct._http answered by these fakes until the test ends: a facebook.com or instagram.com host goes to
    `graph`, a googleapis.com host to `yt`, anything else (or a fake not given) raises AssertionError. YouTube's
    token refresh (outlets._yt_access_token) gives a fixed fake token. The router, with .graph and .yt."""
    def route(method, url, *a, **kw):
        host = urllib.parse.urlparse(url).netloc.lower()
        if host.endswith("facebook.com") or host.endswith("instagram.com"):
            fake = graph
        elif host.endswith("googleapis.com"):
            fake = yt
        else:
            fake = None
        if fake is None:
            raise AssertionError(f"patch_http: no fake for {method} {host}")
        return fake(method, url, *a, **kw)
    route.graph, route.yt = graph, yt
    for p in (mock.patch.object(D, "_http", route), mock.patch.object(O, "_yt_access_token", lambda f: YT_TOKEN)):
        p.start()
        test.addCleanup(p.stop)
    return route


def write_tokens(acct, meta: bool = True, youtube: bool = True, page_id: str = "1000001",
                 ig_user_id: str = "17841400000000001", channel_id: str = "UCfakechannel0001") -> None:
    """Fake sign-in files in D.TOKENS (a test's tmp folder): the Meta Page and Instagram, the YouTube token."""
    folder = Path(D.TOKENS) / acct.slug
    folder.mkdir(parents=True, exist_ok=True)
    if meta:
        (folder / "meta.json").write_text(json.dumps({
            "page_id": page_id, "page_token": "EAAfakefakefakefake", "ig_user_id": ig_user_id,
            "ig_username": acct.slug.replace("-", ""), "app_id": "999"}))
    if youtube:
        (folder / "youtube_token.json").write_text(json.dumps({
            "access_token": YT_TOKEN, "expires_at": 4102444800, "refresh_token": "r", "client_id": "c",
            "client_secret": "s", "channel_id": channel_id,
            "scope": ("https://www.googleapis.com/auth/youtube.upload "
                      "https://www.googleapis.com/auth/youtube.readonly")}))


# ------------------------------------------------------------------------------------------------------ fake tools
_ENGINE = r'''
import json, os, sys
from pathlib import Path
here = Path(__file__)
args = sys.argv[1:]
spec = {}
for a in args:
    if a.endswith(".json") and Path(a).is_file():
        try:
            spec = json.loads(Path(a).read_text())
        except ValueError:
            spec = {}
with open(str(here) + ".calls.jsonl", "a") as log:
    log.write(json.dumps({"argv": args, "spec": spec}) + "\n")
if "--selftest" in args:
    print(json.dumps({"ok": True, "selftest": True}))
    sys.exit(0)
outs = []
for key in ("out", "outputs"):
    v = spec.get(key)
    outs += [v] if isinstance(v, str) else [x for x in (v or []) if isinstance(x, str)]
for v in spec.get("files") or []:
    p = v.get("path") if isinstance(v, dict) else v
    if isinstance(p, str):
        outs.append(p)
if spec.get("out_dir"):
    outs.append(os.path.join(spec["out_dir"], "1.jpg"))
MAGIC = {".jpg": b"\xff\xd8\xff\xe0fake card\xff\xd9", ".jpeg": b"\xff\xd8\xff\xe0fake card\xff\xd9",
         ".png": b"\x89PNG\r\n\x1a\nfake card", ".mp4": b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 16}
for p in outs:
    Path(p).parent.mkdir(parents=True, exist_ok=True)
    Path(p).write_bytes(MAGIC.get(Path(p).suffix.lower(), b"fake"))
print(json.dumps({"ok": True, "files": outs}))
'''


class FakeEngine:
    """A stand-in for render/card_engine.py: run as `python -B <script> <spec.json>`, it writes a placeholder file
    at each path the spec names ("out", "outputs", "files", or "1.jpg" in "out_dir") and prints {"ok": true, ...};
    `--selftest` prints {"ok": true}. Each run is noted in <script>.calls.jsonl (FakeEngine.calls)."""

    @staticmethod
    def script(tmp) -> Path:
        p = Path(tmp) / "fake_card_engine.py"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(_ENGINE.lstrip())
        return p

    @staticmethod
    def calls(tmp) -> List[dict]:
        f = Path(tmp) / "fake_card_engine.py.calls.jsonl"
        try:
            return [json.loads(ln) for ln in f.read_text().splitlines() if ln.strip()]
        except OSError:
            return []


class _FakeRun:
    def __init__(self, entries, others: Optional[Callable] = None):
        self.entries = entries
        self.others = others
        self.calls: List[List[str]] = []

    def _for(self, argv: List[str]):
        if not isinstance(self.entries, dict):
            return self.entries
        url = argv[-1] if argv else ""
        for k, v in self.entries.items():
            if k in url:
                return v
        return []

    def __call__(self, args, *a, **kw):
        argv = [str(x) for x in (args if isinstance(args, (list, tuple)) else str(args).split())]
        if not argv or not Path(argv[0]).name.startswith("yt-dlp"):
            if self.others is not None:
                return self.others(args, *a, **kw)
            raise AssertionError(f"fake_ytdlp: not yt-dlp: {argv[:2]}")
        self.calls.append(argv)
        got = self._for(argv)
        text = kw.get("text") or kw.get("universal_newlines") or kw.get("encoding")
        if isinstance(got, str):
            out, err, code = "", got, 1
        else:
            got = [dict(e) for e in (got or [])]
            if "--dump-single-json" in argv or "-J" in argv:
                out = json.dumps({"id": "fake", "title": "fake", "entries": got})
            elif "--dump-json" in argv or "-j" in argv:
                out = "".join(json.dumps(e) + "\n" for e in got)
            else:
                out = "".join(str(e.get("url") or e.get("id") or "") + "\n" for e in got)
            err, code = "", 0
        if not text:
            out, err = out.encode(), err.encode()
        if kw.get("check") and code:
            raise subprocess.CalledProcessError(code, argv, out, err)
        return subprocess.CompletedProcess(argv, code, out, err)


def fake_ytdlp(entries, others: Optional[Callable] = None) -> _FakeRun:
    """A stand-in for subprocess.run that answers yt-dlp: `entries` is the list of video dicts every call gets, or
    {url part: list} per channel; a str instead of a list is an error (exit 1, the str on stderr, "Sign in to
    confirm you're not a bot" for a bot check). --dump-single-json gives {"entries": [...]}, --dump-json one line
    each. Any other command goes to `others`, or raises AssertionError. `.calls` keeps each argv."""
    return _FakeRun(entries, others)


class Clock:
    """A clock a test moves by hand: clock() / clock.now() is the time, advance(minutes=5) moves it, at("06:30")
    sets the time of day, patch(test, cfg) makes cfg.now() read it until the test ends."""

    def __init__(self, start: datetime, tz=None):
        if start.tzinfo is None:
            start = start.replace(tzinfo=tz or timezone.utc)
        self.t = start

    def __call__(self) -> datetime:
        return self.t

    def now(self) -> datetime:
        return self.t

    def advance(self, seconds: float = 0, minutes: float = 0, hours: float = 0, days: float = 0) -> datetime:
        self.t = self.t + timedelta(seconds=seconds, minutes=minutes, hours=hours, days=days)
        return self.t

    def set(self, t: datetime) -> datetime:
        self.t = t if t.tzinfo else t.replace(tzinfo=self.t.tzinfo)
        return self.t

    def at(self, hhmm: str, day: Optional[date] = None) -> datetime:
        h, m = (int(x) for x in hhmm.split(":"))
        d = day or self.t.date()
        self.t = datetime(d.year, d.month, d.day, h, m, tzinfo=self.t.tzinfo)
        return self.t

    def patch(self, test, cfg: C.Config) -> "Clock":
        p = mock.patch.object(cfg, "now", self.now)
        p.start()
        test.addCleanup(p.stop)
        return self


# ------------------------------------------------------------------------------------------------------ config, boards
def cfg_in_tmp(test, tmp=None, outlets: Optional[dict] = None) -> C.Config:
    """A copy of config/accounts.json in a throwaway folder, as test_review makes it: review port 0, drafts,
    delivered folders, account folders and skill folders (empty: never the real skill) in the tmp folder, clips
    of 1 to 3 seconds, Meta and YouTube by API, TikTok by hand. C.STATE and the key folders (O.TOKENS, D.TOKENS)
    move into it and the plug board starts fresh; all of it is put back when the test ends. Sets
    test.tmp, test.cfg_path and test.cfg; the config."""
    tmp = Path(tmp) if tmp is not None else Path(tempfile.mkdtemp(prefix="clipfactory-social-"))
    tmp.mkdir(parents=True, exist_ok=True)
    old_state, old_tokens = C.STATE, (O.TOKENS, D.TOKENS)

    def put_back():
        C.STATE = old_state
        O.TOKENS, D.TOKENS = old_tokens
        REG.reset()
    test.addCleanup(put_back)
    C.STATE = tmp / "state"
    O.TOKENS = D.TOKENS = tmp / "keys"
    raw = pin_politics(json.loads((ROOT / "config" / "accounts.json").read_text()))
    raw["drafts_root"] = str(tmp / "drafts")
    raw["review_port"] = 0
    for a in raw["accounts"].values():
        a["delivered"] = [str(tmp / "delivered" / a["name"] / "{YYYY}{MM}{DD}")]
        a["account_dir"] = str(tmp / "accounts" / a["name"])
        # the skill too: a test writes work/<job>/ under it, and the real one is the live maker's (a test that
        # needs the real kit reads it by its own path, as test_cards' each_kind_draws does)
        a["skill_dir"] = str(tmp / "skills" / Path(os.path.expanduser(a["skill_dir"])).name)
        a["seconds"] = {"min": 1, "max": 3}
        a["outlets"] = dict(outlets or {"youtube_shorts": {"mode": "api"}, "facebook_reels": {"mode": "api"},
                                        "instagram_reels": {"mode": "api"}, "tiktok": {"mode": "export"}})
    path = tmp / "accounts.json"
    path.write_text(json.dumps(raw))
    REG.reset()
    cfg = C.load(path)
    test.tmp, test.cfg_path, test.cfg = tmp, path, cfg
    return cfg


def _master(tmp: Path) -> Path:
    p = Path(tmp) / "master.mp4"
    if not p.is_file():
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", "color=c=black:s=108x192:d=2",
                        "-f", "lavfi", "-i", "anullsrc=r=22050:cl=mono", "-t", "2", "-c:v", "libx264",
                        "-preset", "ultrafast", "-c:a", "aac", "-shortest", str(p)], check=True)
    return p


def slot_ready(test, acct, day: date, *slots: str) -> Dict[str, dict]:
    """A clip in each slot, READY to sign: a delivered folder with a 2-second clip and its words, then collect
    (test.cfg, from cfg_in_tmp). The slots from the board."""
    from clipfactory import collect as K
    master = _master(test.tmp)
    root = acct.delivered_dirs(day)[0]
    for k in slots:
        d = root / f"{k.replace(':', '')} clip-{k.replace(':', '')}"
        d.mkdir(parents=True, exist_ok=True)
        shutil.copy(master, d / "01-x.mp4")
        for p in ("youtube", "tiktok", "facebook", "reels"):
            (d / f"{p}.txt").write_text(f"Clip at {k} | Creator\n\nWords for {p} at {k}.\n")
    K.collect(test.cfg, acct, day)
    got = (B.read(acct, day) or {}).get("slots") or {}
    return {k: got[k] for k in slots if k in got}


def slot_posted(test, acct, day: date, *slots: str, outlets: Sequence[str] = OUTLETS,
                published: Optional[datetime] = None) -> Dict[str, dict]:
    """Each slot signed (scheduled) and posted by the factory itself: Facebook and Instagram records with
    provider "direct", a post id and published_at (the slot's time unless `published`), YouTube with its video_id
    and publish_at; each carries the clip's sha like the poster's. The slots from the board."""
    from clipfactory import poster as P
    have = (B.read(acct, day) or {}).get("slots") or {}
    need = [k for k in slots if not (have.get(k) or {}).get("clip")]
    if need:
        slot_ready(test, acct, day, *need)
    with B.edit(test.cfg, acct, day) as bd:
        for i, k in enumerate(slots):
            s = bd["slots"][k]
            s["signoff"] = {"by": "Andrew", "at": B.utcnow(), "hash": B.content_hash(s)}
            s["status"] = B.SCHEDULED
            when = published or datetime.fromisoformat(str(s["publish_at"]))
            at = when.astimezone(timezone.utc).replace(microsecond=0).isoformat()
            hhmm = k.replace(":", "")
            for plat in outlets:
                shas = P._shas(s, plat)
                if plat == "youtube_shorts":
                    vid = f"yt{day:%m%d}{hhmm}"
                    rec = {"video_id": vid, "url": f"https://youtube.com/shorts/{vid}", "publish_at": s["publish_at"],
                           "at": at}
                else:
                    pid = f"{1 + OUTLETS.index(plat)}{day:%y%m%d}{hhmm}{i:02d}"
                    url = (f"https://www.facebook.com/reel/{pid}" if plat == "facebook_reels"
                           else f"https://www.instagram.com/reel/{pid}/")
                    rec = {"post_id": pid, "platform_id": pid, "url": url, "published_at": at, "at": at,
                           "provider": "direct"}
                rec.update(shas)
                s.setdefault("outlets", {})[plat] = rec
    got = (B.read(acct, day) or {}).get("slots") or {}
    return {k: got[k] for k in slots}


# ------------------------------------------------------------------------------------------------------ the page
def serve(test, cfg: C.Config):
    """Clip Review on a free port until the test ends: (get, post). get(path, headers=None) -> (status, headers,
    body bytes); post(body, tok=None, page=True) -> (status, JSON) on /act with the page's token (R.token() unless
    `tok`), and, with page, the Origin a browser on the page sends (so a click that needs the page is allowed)."""
    from clipfactory import review as R
    srv = R.Server(("127.0.0.1", 0), R.make_handler(cfg))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    test.addCleanup(srv.server_close)
    test.addCleanup(srv.shutdown)
    port = srv.server_address[1]
    host = f"127.0.0.1:{cfg.review_port}"           # the handler checks the configured port
    direct = urllib.request.build_opener(urllib.request.ProxyHandler({}))   # past the test run's dead proxy

    def get(path: str, headers: Optional[dict] = None):
        req = urllib.request.Request(f"http://127.0.0.1:{port}{path}", headers={"Host": host, **(headers or {})})
        try:
            with direct.open(req, timeout=10) as r:
                return r.status, dict(r.headers), r.read()
        except urllib.error.HTTPError as e:
            return e.code, dict(e.headers), e.read()

    def post(body: dict, tok: Optional[str] = None, page: bool = True):
        h = {"Host": host, "Content-Type": "application/json", "X-Token": R.token() if tok is None else tok}
        if page:
            h.update({"Origin": f"http://{host}", "Sec-Fetch-Site": "same-origin"})
        req = urllib.request.Request(f"http://127.0.0.1:{port}/act", data=json.dumps(body).encode(), method="POST",
                                     headers=h)
        try:
            with direct.open(req, timeout=30) as r:
                return r.status, json.loads(r.read())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read())
    return get, post
