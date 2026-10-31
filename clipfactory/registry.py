"""The plug board of the social layer: the one list of every place a social part plugs into the factory.

Clip Review's Inbox, Cards, Reports and Log tabs, the card parts on each clip, the Today band, the doctor lines, the
morning sentences, the jobs and the commands are all named here as text ("module:function"), never imported at the
top of a shared file. A part is used only once its module is built and imports: a missing module is simply not there
(no tab, no line, "not built yet" for its command), and a module that fails to import hides only its own tab, card
part, doctor line and command, with its error kept in BROKEN (the doctor says it). So the review page, the poster and
the morning notice never go down over a social part, and each part ships as new files only.

    load("engage")                 the module, or None (missing, or broken: BROKEN["engage"] says why)
    fn("engage:health")            the function, or None
    calls(HEALTH, cfg, now, accts) [(spec, result)] for each part that is there and ran; an error is logged once
    route("/inbox/data")           ("data", Page, "") etc.: what the review page serves at a path

Errors from calls() go to state/logs/social-errors.log, one line each, written once per process.
"""
from __future__ import annotations

import importlib
import importlib.util
import sys
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable, Dict, List, Optional, Tuple

from . import config as C


@dataclass(frozen=True)
class Page:
    """A tab (parent None) or a page under a tab (parent is the tab's key; "clips" for a page under Clips)."""
    key: str
    url: str
    label: str
    module: str
    parent: Optional[str]


# ------------------------------------------------------------------------------------------------------ the plug lists
PAGES = (Page("inbox", "/inbox", "Inbox", "engagepage", None),
         Page("rules", "/inbox/rules", "Automations", "rulespage", "inbox"),
         Page("cards", "/cards", "Cards", "cardspage", None),
         Page("reports", "/reports", "Reports", "reportspage", None),
         Page("ideas", "/reports/ideas", "Ideas", "ideaspage", "reports"),
         Page("log", "/log", "Log", "logpage", None),
         Page("week", "/week", "Week", "weekpage", "clips"))
SUBROUTES = {"/stats/series": "checkpoints:get_series"}
ACT_PREFIXES = (("inbox-", "engagepage"), ("saved-", "engagepage"), ("rule-", "rulespage"), ("extra-", "firstcomment"),
                ("coach-", "coach"), ("card-", "cardspage"), ("learn-", "reportspage"), ("ideas-", "ideaspage"),
                ("log-", "logpage"), ("social-", "social"))
CARD_PARTS = ("learn:card_part", "coach:card_part", "firstcomment:card_part", "checkpoints:card_part")
CARD_CSS = ("learn", "coach", "firstcomment", "checkpoints")
HEALTH = ("outward:health", "social:extras_health", "engage:health", "cards:health", "learn:health")
SUMMARIES = ("outward:summary", "engage:summary", "cards:summary", "checkpoints:summary", "learn:summary",
             "breaking:summary")
MORNING_HOLDS = ("outward:morning_lines",)
MORNING = ("engage:morning_line", "cards:morning_line", "learn:morning_line")
DOCTORS = ("switches", "capabilities", "outward", "social", "engage", "rules", "firstcomment", "cards", "coach",
           "checkpoints", "learn", "ideas", "logpage", "vscard", "covers", "coverart", "prehook")
AFTER_TAKE = ("firstcomment:on_collect", "learn:on_collect", "coach:on_collect")
FOLLOW_UPS = ("firstcomment:on_posted", "cards:on_posted", "rules:on_posted")
AFTER_POLL = ("rules:on_items",)
EXECUTORS = {"first_comment": "firstcomment", "reply_public": "engage", "reply_private": "engage", "dm": "engage",
             "hide": "engage", "unhide": "engage", "yt_moderate": "engage", "story": "cards", "card_post": "cards"}
JOBS = {"tick": ("outward:drain_job", "capabilities:refresh_due", "engage:tick", "firstcomment:sweep", "outward:drain_job",
                 "outward:prune_job"),
        "studio": ("cards:tick", "coach:tick"), "checkpoints": ("checkpoints:run",), "learn": ("learn:daily",),
        "ideas": ("ideas:run",)}
COMMANDS = {"engage": "engage", "rules": "rules", "extras": "firstcomment", "cards": "cards", "coach": "coach",
            "checkpoints": "checkpoints", "learn": "learn", "ideas": "ideas"}
SOCIAL_ACTIONS = {"export": "socialmove:cli_export", "import": "socialmove:cli_import"}
CAL_ITEMS = ("cards:cal_items",)

TOPS = (("clips", "/", "Clips"), ("stats", "/stats", "Stats"))     # the two tabs that are not social parts

# ------------------------------------------------------------------------------------------------------ loading
BROKEN: Dict[str, str] = {}         # module name -> "Type: message" of the import that failed
_CACHE: Dict[str, object] = {}      # module name -> the module, or None (not built, or broken)
_LOCK = threading.Lock()
_LOGGED: set = set()                # error lines already written this process


def load(name: str):
    """The part's module (clipfactory.<name>), or None when it is not built yet or will not import (BROKEN says
    why). Never raises: a part that fails to load is simply not there."""
    full = "clipfactory." + name
    mod = sys.modules.get(full)
    if mod is not None:             # already imported, or a test put a stand-in there
        return mod
    with _LOCK:
        if name in _CACHE:
            return _CACHE[name]
    try:
        spec = importlib.util.find_spec(full)
    except (ValueError, ImportError):
        spec = None
    if spec is None:
        got = None
    else:
        try:
            got = importlib.import_module(full)
        except (Exception, SystemExit) as e:  # noqa: BLE001  (a broken part hides only itself)
            with _LOCK:
                BROKEN[name] = f"{type(e).__name__}: {e}"[:300]
            got = None
    with _LOCK:
        _CACHE[name] = got
        if got is not None:
            BROKEN.pop(name, None)
    return got


def present(name: str) -> bool:
    """Is this part built here, and does it import?"""
    return load(name) is not None


def reset() -> None:
    """Forget what was loaded (tests swap stand-in modules in and out)."""
    with _LOCK:
        _CACHE.clear()
        BROKEN.clear()
        _LOGGED.clear()


def fn(spec: str) -> Optional[Callable]:
    """The function a "module:attr" names, or None when its part is not there (or has no such function)."""
    name, _, attr = spec.partition(":")
    mod = load(name)
    if mod is None or not attr:
        return None
    f = getattr(mod, attr, None)
    return f if callable(f) else None


def _log_error(spec: str, e: BaseException) -> None:
    """One line in state/logs/social-errors.log, once per process for the same error: a part that keeps failing
    every 15 seconds on the page never fills the disk."""
    line = f"{spec}: {type(e).__name__}: {str(e)[:300]}".replace("\n", " ")
    with _LOCK:
        if line in _LOGGED:
            return
        _LOGGED.add(line)
    try:
        p = C.STATE / "logs" / "social-errors.log"
        p.parent.mkdir(parents=True, exist_ok=True)
        with open(p, "a") as f:
            f.write(datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ") + " " + line + "\n")
    except OSError:
        pass


def calls(specs, *a, **kw) -> List[Tuple[str, object]]:
    """Run each "module:function" that is there, in order, each on its own: [(spec, result)] for every one that ran.
    One that raises is left out and its error logged; one whose part is not built is skipped. A SystemExit (this
    codebase's way to refuse, e.g. outward.queue on a key already queued) is caught the same way: a part's refusal
    never ends the post-due run or the page that asked."""
    out: List[Tuple[str, object]] = []
    for spec in specs:
        f = fn(spec)
        if f is None:
            continue
        try:
            out.append((spec, f(*a, **kw)))
        except (Exception, SystemExit) as e:  # noqa: BLE001  (one part never stops the others)
            _log_error(spec, e)
    return out


# ------------------------------------------------------------------------------------------------------ pages
def _page(key: str) -> Optional[Page]:
    for p in PAGES:
        if p.key == key:
            return p
    return None


def tab_pages() -> List[Tuple[str, str, str]]:
    """The tabs along the top, in order: Clips, Stats, then each social tab that is built and imports."""
    return list(TOPS) + [(p.key, p.url, p.label) for p in PAGES if p.parent is None and present(p.module)]


def top_of(key: str) -> str:
    """The tab a page sits under (a page under Inbox marks Inbox as the current tab)."""
    seen = set()
    p = _page(key)
    while p is not None and p.parent and p.key not in seen:
        seen.add(p.key)
        key = p.parent
        p = _page(key)
    return key


def url_of(key: str) -> str:
    """Where a page lives ("/" for Clips)."""
    for k, url, _ in TOPS:
        if k == key:
            return url
    p = _page(key)
    return p.url if p is not None else "/"


def children(parent: str) -> List[Page]:
    """The built pages under a tab, in order (Automations under Inbox, Ideas under Reports, Week under Clips)."""
    return [p for p in PAGES if p.parent == parent and present(p.module)]


def route(path: str):
    """What the review page serves at a path, or None when no social part claims it:
    ("sub", None, "module:function") for a SUBROUTES path, ("page", Page, "") for the page itself, ("data", Page, "")
    for <url>/data and ("get", Page, rest) for any other <url>/<rest>. Longer urls are tried first, so /inbox/rules is
    the Automations page and not part of Inbox. Whether the module is built is the caller's question."""
    if path in SUBROUTES:
        return ("sub", None, SUBROUTES[path])
    for p in sorted(PAGES, key=lambda x: len(x.url), reverse=True):
        if path == p.url:
            return ("page", p, "")
        if path == p.url + "/data":
            return ("data", p, "")
        if path.startswith(p.url + "/") and len(path) > len(p.url) + 1:
            return ("get", p, path[len(p.url) + 1:])
    return None


def act_module(op: str):
    """The module that takes a click whose op starts with one of ACT_PREFIXES; None for any other op. A known
    prefix whose part is not built (or will not import) is refused in plain words."""
    for prefix, name in ACT_PREFIXES:
        if op.startswith(prefix):
            mod = load(name)
            if mod is None or not callable(getattr(mod, "act", None)):
                raise SystemExit("that part of Clip Review is not built yet")
            return mod
    return None


def card_css() -> str:
    """The CSS every built card part adds to the Clips page (its module's CARD_CSS)."""
    out = []
    for name in CARD_CSS:
        mod = load(name)
        css = getattr(mod, "CARD_CSS", "") if mod is not None else ""
        if isinstance(css, str):
            out.append(css)
    return "".join(out)


def card_js() -> str:
    """The script every built card part adds to the Clips page (its module's CARD_JS, run after the page's own: act,
    toast, refresh and who are the page's functions it may call). Each in its own block, so one part's error never
    stops another's."""
    out = []
    for name in CARD_CSS:
        mod = load(name)
        js = getattr(mod, "CARD_JS", "") if mod is not None else ""
        if isinstance(js, str) and js.strip():
            out.append("\ntry{" + js + "}catch(e){console.error('" + name + "', e)}")
    return "".join(out)


# ------------------------------------------------------------------------------------------------------ commands
def run_cli(cfg: C.Config, name: str, argv: List[str]) -> None:
    """factory.py <name> ...: "social" goes to social.cli, any other COMMANDS name to its module's cli(cfg, argv).
    A part not built yet says so (nothing with --quiet, so a timer never logs it) and exits 0; a broken one prints
    its error and exits 1."""
    mod_name = "social" if name == "social" else COMMANDS.get(name, "")
    mod = load(mod_name) if mod_name else None
    if mod is None or not callable(getattr(mod, "cli", None)):
        if mod_name and mod_name in BROKEN:
            print(f"{name}: could not load clipfactory/{mod_name}.py: {BROKEN[mod_name]}")
            raise SystemExit(1)
        if "--quiet" not in argv:
            print(f"{name}: not built yet")
        return None
    mod.cli(cfg, list(argv))
    return None
