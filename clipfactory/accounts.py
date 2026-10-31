"""Client accounts: add one with a single command, give it its own approvers, its own folders and its own page.

    factory.py account add <slug> --name "Client Name" [--handle @x] [--maker generic] [--seconds 30-60]
                           [--slots 09:00,13:00,19:00] [--approvers "Jane Doe"] [--category 22]
                           [--timezone America/New_York] [--rights owned] [--folder DIR] [--dry-run]
    factory.py account list
    factory.py account page <slug> [--new] [--host clips.example.com]   the client's own page (its secret link)
    factory.py account job <slug> <url|file> --name "Job" [--source-url URL] [--format F] [--dry-run]
    factory.py account remove <slug> [--yes]

Nothing here runs until an account is added: Gaming and Politics have no "owner" and no "approvers" of their
own, and every helper below answers for them exactly as the factory did before (the global approvers list, the global
drafts folder, the owner's review page).

A client account (accounts.json "owner": "client"):
  - lives in its own folder, ~/Desktop/Andrew Johnson/Clients/<Name>/ (Jobs, Delivered, Drafts, Brand, Sources,
    Contract, Publish), never under Clipping/: the Politics engine stamps the Politics look on any job delivered there,
    and the Politics's hourly collect would take a clip from the Politics's delivery folder (audit CL-1);
  - is made by the generic maker (generic.py): viral-clip-engine in its neutral look, the client's own name on it;
  - posts nothing by itself: every outlet starts as "export" (files in its Drafts folder) until that platform is
    connected and tested (CLIENTS.md);
  - has its own approvers. Only a name on THAT list signs its clips (signoff._who), and the owner's names sign it
    only when they are on it (audit CL-3);
  - has its own page on the review server, /c/<secret>/ (clientpage.py): its clips to approve or decline and its
    numbers, nothing of any other account, never money.
"""
from __future__ import annotations

import dataclasses
import json
import os
import re
import secrets
import shutil
import tempfile
import time
from datetime import date
from pathlib import Path
from typing import List, Optional, Tuple

from . import config as C

SLUG = re.compile(r"^[a-z0-9][a-z0-9-]{1,38}[a-z0-9]$")
# names already used for something else under state/ or on the review server
RESERVED = {"all", "logs", "stats", "clients", "scout", "delivered", "work", "standin", "config-backups", "c", "file",
            "live", "act",
            # the social layer's folders and pages (state/social, Clip Review's tabs, clipfactory/social.py)
            "inbox", "engage", "cards", "extras", "coach", "learn", "outward", "series", "ideas", "playbook",
            "reports", "log", "social", "render", "prompts", "rules", "week", "verdicts", "capabilities", "docs"}
CLIENTS_ROOT = "~/Desktop/Andrew Johnson/Clients"
ENGINE = "~/.claude/skills/viral-clip-engine"
MAKERS = ("generic",)                  # what `account add` may set; gta and politics belong to the owner's accounts
PLATFORMS = ["youtube_shorts", "tiktok", "facebook_reels", "instagram_reels"]
FOLDERS = ("Jobs", "Delivered", "Drafts", "Brand", "Sources", "Contract", "Publish")
RIGHTS = ("owned", "licensed", "permission", "campaign")
# the Politics's homes: a client's folders may never sit inside them (clipkit._politics_job, the Politics's collect)
POLITICS_HOMES = (Path.home() / "Desktop" / "Andrew Johnson" / "Clipping", Path.home() / "Desktop" / "Clipping")
PAGE_FILE = "client-page.json"


# ------------------------------------------------------------------------------------------------ who and where
def is_client(acct: C.Account) -> bool:
    return str(acct.raw.get("owner") or "self") == "client"


def clients(cfg: C.Config) -> List[C.Account]:
    return [a for a in cfg.accounts.values() if is_client(a)]


def approvers(cfg: C.Config, acct: Optional[C.Account]) -> List[str]:
    """Who may sign this account's clips: its own "approvers" list, else the global one (Gaming, Politics)."""
    own = (acct.raw.get("approvers") if acct is not None else None) or []
    return [str(x) for x in own] if own else list(cfg.approvers)


def signs_here(cfg: C.Config, acct: C.Account) -> bool:
    """Does a name on the global approvers list (the owner) sign this account? Always for the owner's own accounts; for
    a client only when the owner is on its list. Decides what the owner's All view and morning notice carry."""
    mine = {a.lower() for a in cfg.approvers}
    return any(a.lower() in mine for a in approvers(cfg, acct))


def client_signers(cfg: C.Config, acct: C.Account) -> List[str]:
    """The client's own names on its approvers list (not the owner's): who signs on the client's page. [] = the page
    only shows the clips, and the owner signs them in Clip Review."""
    mine = {a.lower() for a in cfg.approvers}
    return [a for a in approvers(cfg, acct) if a.lower() not in mine] if is_client(acct) else []


def own_view(cfg: C.Config) -> C.Config:
    """The config without client accounts: what the owner's Stats tab and dashboard count (a client's views are not
    the owner's money, audit CL-9). The same object when there is no client."""
    if not clients(cfg):
        return cfg
    return dataclasses.replace(cfg, accounts={k: a for k, a in cfg.accounts.items() if not is_client(a)})


def only(cfg: C.Config, acct: C.Account) -> C.Config:
    """The config with this one account in it (a client's report reads nothing else)."""
    return dataclasses.replace(cfg, accounts={acct.slug: acct})


def drafts_root(cfg: C.Config, acct: C.Account) -> Path:
    return C.expand(acct.raw["drafts_root"]) if acct.raw.get("drafts_root") else cfg.drafts_root


def drafts_roots(cfg: C.Config) -> List[Path]:
    """Every account's own drafts folder that is not the global one (Clip Review may play files from them)."""
    out = []
    for a in cfg.accounts.values():
        r = drafts_root(cfg, a)
        if r != cfg.drafts_root and r not in out:
            out.append(r)
    return out


def timezone_of(cfg: C.Config, acct: C.Account) -> str:
    """The zone the client reads times in (its page and report). Slots themselves stay on the factory's clock."""
    return str((acct.raw.get("client") or {}).get("timezone") or cfg.timezone)


# ------------------------------------------------------------------------------------------------ the root guard
def _real(p: Path) -> Path:
    try:
        return p.expanduser().resolve()
    except OSError:
        return p.expanduser()


def _inside(p: Path, top: Path) -> bool:
    p, top = _real(p), _real(top)
    return p == top or top in p.parents


def root_problems(cfg: C.Config) -> List[str]:
    """Two accounts that could take each other's clips (audit CL-1): a delivery folder shared by two accounts, one
    account delivering inside another's account folder, or a client's folders under the Politics's homes."""
    probs: List[str] = []
    accts = list(cfg.accounts.values())
    day = date(2031, 1, 2)
    for i, a in enumerate(accts):
        mine = [str(_real(p)) for p in a.delivered_dirs(day)]
        for b in accts[i + 1:]:
            theirs = [str(_real(p)) for p in b.delivered_dirs(day)]
            same = sorted(set(mine) & set(theirs))
            if same:
                probs.append(f"{a.name} and {b.name} deliver into the same folder ({same[0]}): each would take the "
                             "other's clips")
    for a in accts:
        for b in accts:
            if a is b or not (is_client(a) or is_client(b)):
                continue              # the owner's two accounts are as they always were
            for p in a.delivered_dirs(day):
                if _inside(p, b.account_dir):
                    owner = C.possessive(b.name)
                    probs.append(f"{a.name} delivers into {owner} folder ({p}): {owner} tools would take it")
    for a in clients(cfg):
        named = [("account folder", a.account_dir), ("drafts folder", drafts_root(cfg, a))]
        named += [("delivery folder", p) for p in a.delivered_dirs(day)]
        for what, p in named:
            home = next((h for h in POLITICS_HOMES if _inside(p, h)), None)
            if home is not None:
                probs.append(f"{C.possessive(a.name)} {what} {p} is inside the Politics's {home}: the engine would put "
                             "the Politics's look and watermark on it")
    return list(dict.fromkeys(probs))


def doctor(cfg: C.Config) -> List[Tuple[bool, str, str]]:
    """(good, text, fix) for factory.py doctor. Nothing at all while there is no client and no shared folder."""
    out: List[Tuple[bool, str, str]] = []
    probs = root_problems(cfg)
    for p in probs:
        out.append((False, p, "give each account its own delivery folder (config/accounts.json delivered)"))
    cl = clients(cfg)
    if not cl:
        return out
    if not probs:
        out.append((True, "every account delivers into its own folder", ""))
    for a in cl:
        out.append((a.maker in MAKERS, f"{a.name}: maker {a.maker}", f"set maker to one of {', '.join(MAKERS)}"))
        out.append((bool(approvers(cfg, a)), f"{a.name}: approvers {', '.join(approvers(cfg, a)) or '(none)'}",
                    f"factory.py account add sets them; or edit {a.slug}.approvers in config/accounts.json"))
        out.append((a.account_dir.is_dir(), f"{a.name}: folder {a.account_dir}", f"mkdir -p '{a.account_dir}'"))
        yt = a.outlets.get("youtube_shorts") or {}
        out.append((bool(yt.get("category_id")), f"{a.name}: YouTube category {yt.get('category_id') or '(none)'}",
                    f"set outlets.youtube_shorts.category_id for {a.slug} (22 People & Blogs, 24 Entertainment...)"))
        out.append((bool(secret(a)), f"{a.name}: its own page is set up", f"factory.py account page {a.slug}"))
        end = str((a.raw.get("client") or {}).get("contract_end") or "")
        if end:
            try:
                left = (date.fromisoformat(end) - cfg.now().date()).days
            except ValueError:
                left = None
            out.append((left is not None and left >= 0, f"{a.name}: contract ends {end}"
                        + (f" ({left} days)" if left is not None and left >= 0 else ""),
                        f"renew it, or factory.py account remove {a.slug}"))
        auto = [p for p in a.platforms if (a.outlets.get(p) or {}).get("mode") == "api"]
        out.append((True, f"{a.name}: " + (f"{', '.join(auto)} post by themselves" if auto else
                                          "posts nothing by itself (every platform by hand)"), ""))
        if not a.skill_dir.is_dir():
            out.append((False, f"{a.name}: engine {a.skill_dir} is missing", "install viral-clip-engine"))
    return out


# ------------------------------------------------------------------------------------------------ the page secret
def _page_path(acct: C.Account) -> Path:
    return C.STATE / acct.slug / PAGE_FILE


def secret(acct: C.Account) -> str:
    """The client page's secret ('' when it has none). It lives in state/<slug>/client-page.json (mode 600), never
    in accounts.json, which is shared code."""
    if not is_client(acct):
        return ""
    try:
        s = str(json.loads(_page_path(acct).read_text()).get("secret") or "")
    except (OSError, ValueError, AttributeError):
        return ""
    return s if len(s) >= 32 else ""


def new_secret(acct: C.Account) -> str:
    p = _page_path(acct)
    p.parent.mkdir(parents=True, exist_ok=True)
    s = secrets.token_urlsafe(32)
    tmp = p.with_name(p.name + ".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump({"secret": s, "made": time.strftime("%Y-%m-%d %H:%M:%S")}, f)
    os.replace(tmp, p)
    return s


def page_hosts(acct: C.Account) -> List[str]:
    """The host names the client reaches its page by, besides this Mac (a tunnel's name). Empty: this Mac only."""
    return [str(h).strip().lower() for h in (acct.raw.get("client") or {}).get("page_hosts") or [] if str(h).strip()]


def page_urls(cfg: C.Config, acct: C.Account) -> List[str]:
    s = secret(acct)
    if not s:
        return []
    return [f"http://127.0.0.1:{cfg.review_port}/c/{s}/"] + [f"https://{h}/c/{s}/" for h in page_hosts(acct)]


# ------------------------------------------------------------------------------------------------ the config file
def _read(cfg: C.Config) -> dict:
    return json.loads(Path(cfg.path).read_text())


def _write(cfg: C.Config, data: dict) -> Path:
    """accounts.json, atomically, after a dated copy of the file as it was (state/config-backups/)."""
    path = Path(cfg.path)
    back = C.STATE / "config-backups" / f"accounts-{time.strftime('%Y%m%d-%H%M%S')}.json"
    back.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(path, back)
    text = json.dumps(data, indent=2, ensure_ascii=False) + "\n"
    json.loads(text)
    tmp = path.with_name(f".{path.name}.tmp")
    tmp.write_text(text)
    os.chmod(tmp, os.stat(path).st_mode & 0o777)
    os.replace(tmp, path)
    return back


# ------------------------------------------------------------------------------------------------ add
def _band(s: str) -> Tuple[float, float]:
    m = re.fullmatch(r"\s*(\d+(?:\.\d+)?)\s*-\s*(\d+(?:\.\d+)?)\s*", s or "")
    if not m:
        raise SystemExit(f"--seconds {s!r}: use MIN-MAX, e.g. 30-60")
    lo, hi = float(m.group(1)), float(m.group(2))
    if not 5 <= lo < hi <= 180:
        raise SystemExit(f"--seconds {s!r}: the band must sit inside 5-180 seconds, low before high")
    return lo, hi


def _num(x: float):
    return int(x) if float(x).is_integer() else x


def block(cfg: C.Config, slug: str, name: str, *, handle: str = "", maker: str = "generic", seconds: str = "30-60",
          slots: str = "09:00,13:00,19:00", approvers_s: str = "", category: str = "22", tz: str = "",
          rights: str = "owned", folder: str = "", contact: str = "") -> dict:
    """The accounts.json block for a new client account (checked; nothing written)."""
    if not SLUG.match(slug or "") or slug in RESERVED:
        raise SystemExit(f"{slug!r}: a slug is 3-40 lowercase letters, digits and dashes (not {', '.join(sorted(RESERVED))})")
    every = cfg.all_accounts or cfg.accounts     # a switched-off account (or another machine's) is still an account
    if slug in every:
        raise SystemExit(f"there is already an account {slug!r}")
    name = " ".join(str(name or "").split())
    if not name or len(name) > 60 or any(ch in name for ch in '/\\:{}"'):
        raise SystemExit("--name: the client's name as it should read (no / \\ : { } or quotes, at most 60 characters)")
    if any(a.name.lower() == name.lower() for a in every.values()):
        raise SystemExit(f"an account is already called {name!r}")
    if maker not in MAKERS:
        raise SystemExit(f"--maker {maker!r}: a client account is made by {' or '.join(MAKERS)}")
    lo, hi = _band(seconds)
    times = sorted({C.normal_slot(x) for x in str(slots or "").split(",") if x.strip()})
    if not times:
        raise SystemExit("--slots: at least one time, e.g. 09:00,13:00,19:00")
    names = [" ".join(x.split()) for x in str(approvers_s or "").split(",") if x.strip()]
    if any(len(n) > 60 for n in names):
        raise SystemExit("--approvers: names of 60 characters at most, separated by commas")
    if not str(category or "").isdigit():
        raise SystemExit(f"--category {category!r}: YouTube's category number (22 People & Blogs, 24 Entertainment, "
                         "27 Education, 28 Science & Technology)")
    if tz:
        try:
            from zoneinfo import ZoneInfo
            ZoneInfo(tz)
        except Exception:  # noqa: BLE001
            raise SystemExit(f"--timezone {tz!r}: not a time zone (e.g. America/New_York)") from None
    if rights not in RIGHTS:
        raise SystemExit(f"--rights {rights!r}: one of {', '.join(RIGHTS)} (the client's own footage is owned)")
    if handle and not re.fullmatch(r"@?[A-Za-z0-9._]{2,40}", handle):
        raise SystemExit(f"--handle {handle!r}: the client's @handle")
    home = Path(os.path.expanduser(folder)) if folder else C.expand(CLIENTS_ROOT) / name
    tilde = _tilde(home)
    b = {
        "name": name,
        "handle": (("@" + handle.lstrip("@")) if handle else ""),
        "short": name.split()[0][:12],
        "owner": "client",
        "maker": maker,
        "skill_dir": ENGINE,
        "account_dir": tilde,
        "delivered": [f"{tilde}/Delivered/{{MM}}.{{DD}}.{{YYYY}}"],
        "drafts_root": f"{tilde}/Drafts",
        "slots": times,
        "daily_target": len(times),
        "seconds": {"min": _num(lo), "max": _num(hi)},
        "formats": ["clip"],
        "clips_per_source": 3,
        "platforms": list(PLATFORMS),
        "outlets": {
            "youtube_shorts": {"mode": "export", "category_id": str(category)},
            "tiktok": {"mode": "export", "api_app_audited": False},
            "facebook_reels": {"mode": "export"},
            "instagram_reels": {"mode": "export"},
        },
        "approvers": names or list(cfg.approvers),
        "client": {"contact": contact or (names[0] if names else ""), "rights": rights,
                   "timezone": tz or cfg.timezone, "page_hosts": [], "added": cfg.now().date().isoformat(),
                   "contract_end": None},
        "_client_note": ("A client account (factory.py account add). It posts nothing by itself: every outlet is "
                         "export (files in its Drafts folder) until that platform is connected and tested "
                         "(CLIENTS.md). Only the names in approvers sign its clips."),
    }
    return b


def _tilde(p: Path) -> str:
    home = str(Path.home())
    s = str(p)
    return "~" + s[len(home):] if s == home or s.startswith(home + "/") else s


RUNBOOK = """# {name}: how its clips are made

Clips are {lo} to {hi} seconds, {n} a day ({slots}, {tz_name} clock). Cut by viral-clip-engine in its
neutral look: no Politics masthead, no Politics look, the name on the picture is {mark}.

1. A new source: `python3 ~/clip-factory/factory.py account job {slug} "<url or file>" --name "<Job Name>"`.
   It transcribes it and writes `transcript_compact.txt` in `Jobs/<Job Name>/`.
2. Read that transcript. Write `Jobs/<Job Name>/slate.json` (the engine's slate: rank, slug, start/end or segments,
   mode, hook, titles.youtube, titles.tiktok, platform_caption, hashtags; never "template", which is a Politics
   Politics segment). More picks than slots; kill the weak ones.
   Each clip must say who and what it is without context, and land {lo} to {hi} seconds after silence removal.
3. The night run, `factory.py nightly {slug}`, starts a worker in the background and returns at once
   (`factory.py make {slug}` does the same in front of you). It fills the NEXT posting day: today's slots when it
   runs between midnight and 07:00, tomorrow's after that. It waits for the Mac's make lock, which the GTA night
   shift holds until about 06:15, then checks each slated clip with the engine's preflight, renders it,
   grades it, and puts it into the next open slot (`Delivered/MM.DD.YYYY/`, then its drafts). A clip the checks
   refuse is skipped, with the reason on the board. It never signs or posts.
   `Publish/schedule.json` is the record of which clip went where, written after a clip passes: never edit it.
4. {who_signs}

Never put a client job under ~/Desktop/Andrew Johnson/Clipping: the engine would stamp the Politics look on it.
"""

TASK = """Tonight's clips for {name} (clip factory). Follow {runbook}.
The clips go into the NEXT posting day: today's slots when this runs between midnight and 07:00, tomorrow's after
07:00. `nightly` with no --date picks that day by itself.
1. For each new source in {sources} (or the client's newest upload), run
   `python3 ~/clip-factory/factory.py account job {slug} "<url>" --name "<Job Name>"`.
2. Read each new job's transcript_compact.txt and write its slate.json in the same folder: {lo} to {hi} seconds,
   more picks than slots, the engine's own rules for the moment and the hook.
3. `python3 ~/clip-factory/factory.py nightly {slug}`. It starts a worker in the background and returns at once;
   the worker waits its turn behind any other render on this Mac, then cuts. Do not wait for it, and do not report
   `status` as its result: what it made, and why anything was refused, is on the board and the client's page later.
4. Report with `python3 ~/clip-factory/factory.py note {slug} "<the jobs and picks written tonight>"`.
Never approve, schedule or post. Never cut under ~/Desktop/Andrew Johnson/Clipping.
"""

README = """{name}

Jobs/        one folder per source: the transcript, slate.json (the picks), the renders
Delivered/   the finished clips, one dated folder a day; Clip Review takes them from here
Drafts/      the clips as signed, and Ready to post/ for every platform posted by hand
Brand/       the client's logo, colours and fonts
Sources/     the client's own footage, if they send files
Contract/    the signed agreement
Publish/     schedule.json: the record of which clip went into which slot (written by the factory)

Clips: {lo} to {hi} seconds, {n} a day ({slots}).
Signed by: {who}.
Posting: by hand until each platform is connected and tested (CLIENTS.md in ~/clip-factory).
"""


def add(cfg: C.Config, slug: str, name: str, dry_run: bool = False, **kw) -> List[str]:
    """Add a client account: its block in accounts.json, its folders, its state, its page. Prints the next steps."""
    from . import outlets as O
    b = block(cfg, slug, name, **kw)
    data = _read(cfg)
    if slug in data.get("accounts", {}):
        raise SystemExit(f"there is already an account {slug!r} in {cfg.path}")
    trial = dict(data, accounts=dict(data["accounts"], **{slug: b}))
    with tempfile.TemporaryDirectory(prefix="clipfactory-add-") as tmp:   # never beside the real config
        probe = Path(tmp) / "accounts.json"         # the new file must load, and no two accounts may share a folder
        probe.write_text(json.dumps(trial, indent=2, ensure_ascii=False) + "\n")
        new_cfg = C.load(probe)
    # every block, switched on or not: a client's folders inside a switched-off account's are refused as well
    probs = root_problems(dataclasses.replace(new_cfg, accounts=new_cfg.all_accounts or new_cfg.accounts))
    if probs:
        raise SystemExit("refused, nothing written:\n  " + "\n  ".join(probs))
    acct = new_cfg.account(slug)
    lo, hi = acct.min_seconds, acct.max_seconds
    who = ", ".join(b["approvers"])
    lines = [f"{'would add' if dry_run else 'added'} {b['name']} ({slug}): {lo:.0f} to {hi:.0f} seconds, "
             f"{len(b['slots'])} a day at {', '.join(b['slots'])}, signed by {who}",
             f"  folder {acct.account_dir}", f"  delivers into {acct.delivered[0]}", f"  drafts in {b['drafts_root']}",
             "  every platform by hand until it is connected (outlets: export)"]
    if dry_run:
        return lines + ["  (dry run: nothing written)"]
    back = _write(cfg, trial)
    lines.append(f"  config/accounts.json updated (the file as it was: {back})")
    for sub in FOLDERS:
        (acct.account_dir / sub).mkdir(parents=True, exist_ok=True)
    sched = acct.account_dir / "Publish" / "schedule.json"
    if not sched.exists():
        sched.write_text("{}\n")
    words = dict(name=b["name"], slug=slug, lo=f"{lo:.0f}", hi=f"{hi:.0f}", n=len(b["slots"]),
                 slots=", ".join(b["slots"]), tz_name=cfg.timezone, mark=b["handle"] or b["name"],
                 who=who, sources=acct.account_dir / "Sources", runbook=acct.account_dir / "RUNBOOK.md")
    signers = client_signers(new_cfg, acct)
    words["who_signs"] = (f"{' and '.join(signers)} {'approves' if len(signers) == 1 else 'approve'} each clip on "
                          f"the client page (`factory.py account page {slug}` prints its link)." if signers else
                          f"{' and '.join(b['approvers'])} {'signs' if len(b['approvers']) == 1 else 'sign'} each "
                          f"clip in Clip Review (the {b['name']} tab).")
    for fname, tmpl in (("README.txt", README), ("RUNBOOK.md", RUNBOOK), ("CLAUDE-TASK.md", TASK)):
        p = acct.account_dir / fname
        if not p.exists():
            p.write_text(tmpl.format(**words))
    (C.STATE / slug / "logs").mkdir(parents=True, exist_ok=True)
    (C.STATE / slug / "work").mkdir(parents=True, exist_ok=True)
    tok = O.TOKENS / slug
    tok.mkdir(parents=True, exist_ok=True)
    os.chmod(tok, 0o700)
    new_secret(acct)
    lines += [f"  folders made in {acct.account_dir} (README.txt, RUNBOOK.md, CLAUDE-TASK.md)",
              f"  sign-ins will live in {tok} (mode 700)",
              "", "next:",
              f"  1. its page: python3 ~/clip-factory/factory.py account page {slug}",
              f"  2. a source: python3 ~/clip-factory/factory.py account job {slug} \"<url>\" --name \"<Job Name>\"",
              f"  3. the nightly cut: a Claude task with the text in {acct.account_dir / 'CLAUDE-TASK.md'}, after 06:15",
              "     (after the night shift: the GTA worker holds the make lock 21:00 to about 06:15)",
              "  4. posting by itself, platform by platform, only after a test post: CLIENTS.md"]
    return lines


# ------------------------------------------------------------------------------------------------ remove
YT_STAYS = ("A YouTube upload already made stays scheduled on YouTube and still goes public at its time: make it "
            "private in YouTube Studio if it should not go out.")


def remove(cfg: C.Config, slug: str, yes: bool = False) -> List[str]:
    """Take a client account out of accounts.json (the block is kept in config/retired-accounts.json). Its sign-ins
    folder is moved aside, not deleted; its boards, drafts and folder stay where they are."""
    from . import outlets as O
    acct = cfg.account(slug)
    if not is_client(acct):
        raise SystemExit(f"{acct.name} is not a client account: it is never removed from here")
    if not yes:
        return [f"would remove {acct.name} ({slug}) from config/accounts.json; run again with --yes",
                "  the factory posts nothing more for it; its boards, drafts and folder stay",
                "  " + YT_STAYS]
    data = _read(cfg)
    gone = data["accounts"].pop(slug)
    ret = Path(cfg.path).with_name("retired-accounts.json")
    old = json.loads(ret.read_text()) if ret.exists() else {"retired": []}
    old.setdefault("retired", []).append({"slug": slug, "at": time.strftime("%Y-%m-%d %H:%M:%S"), "block": gone})
    ret.write_text(json.dumps(old, indent=2, ensure_ascii=False) + "\n")
    back = _write(cfg, data)
    lines = [f"removed {acct.name} ({slug}); its block is in {ret} (accounts.json as it was: {back})"]
    tok = O.TOKENS / slug
    if tok.exists():
        dest = O.TOKENS / ".retired" / f"{slug}-{time.strftime('%Y%m%d-%H%M%S')}"
        dest.parent.mkdir(parents=True, exist_ok=True)
        os.chmod(dest.parent, 0o700)
        tok.rename(dest)
        lines.append(f"  its sign-ins moved to {dest}: delete that folder when the contract says (rm -r)")
    lines.append("  " + YT_STAYS)
    lines += ["  on the platforms, the client takes the access back:",
              "    Meta: Business Settings > Partners > remove AJJ",
              "    Google: myaccount.google.com/permissions > remove the clip uploader",
              "    TikTok: Settings > Security > Manage app permissions > remove AJJ Clip Factory"]
    return lines


# ------------------------------------------------------------------------------------------------ the CLI
def cmd(cfg: C.Config, a) -> None:
    what = a.what
    if what == "add":
        if not a.name:
            raise SystemExit("account add needs --name \"Client Name\"")
        out = add(cfg, a.slug, a.name, dry_run=a.dry_run, handle=a.handle or "", maker=a.maker,
                  seconds=a.seconds, slots=a.slots, approvers_s=a.approvers or "", category=a.category,
                  tz=a.timezone or "", rights=a.rights, folder=a.folder or "", contact=a.contact or "")
    elif what == "list":
        out = []
        for acct in cfg.accounts.values():
            kind = "client" if is_client(acct) else "yours"
            out.append(f"  {acct.slug:<18} {acct.name:<22} {kind:<7} {acct.maker:<8} {acct.min_seconds:.0f}-"
                       f"{acct.max_seconds:.0f}s  {len(acct.active_slots())} a day  signed by "
                       f"{', '.join(approvers(cfg, acct)) or '(nobody)'}")
    elif what == "page":
        acct = cfg.account(a.slug or "")
        if not is_client(acct):
            raise SystemExit(f"{acct.name} is yours: its page is Clip Review itself")
        if a.host:
            data = _read(cfg)
            hosts = data["accounts"][acct.slug].setdefault("client", {}).setdefault("page_hosts", [])
            h = a.host.strip().lower()
            if not re.fullmatch(r"[a-z0-9.-]+(:\d+)?", h):
                raise SystemExit(f"--host {a.host!r}: a host name, e.g. clips.example.com")
            if h not in hosts:
                hosts.append(h)
                _write(cfg, data)
            cfg = C.load(cfg.path)
            acct = cfg.account(acct.slug)
        if a.new or not secret(acct):
            new_secret(acct)
        signers = client_signers(cfg, acct)
        out = [f"{C.possessive(acct.name)} page (only its own clips and numbers):"] + \
            [f"  {u}" for u in page_urls(cfg, acct)]
        out.append(f"  signs as {', '.join(signers)}" if signers else
                   f"  view only: {', '.join(approvers(cfg, acct))} sign(s) in Clip Review")
        if not page_hosts(acct):
            out.append("  this Mac only until a tunnel name is added (--host); see CLIENTS.md before you do")
        out.append("  --new makes a new link (the old one stops working)")
    elif what == "job":
        from . import generic as G
        out = G.new_job(cfg, cfg.account(a.slug or ""), a.source, a.name or "", source_url=a.source_url or "",
                        fmt=a.format or "", dry_run=a.dry_run)
    elif what == "remove":
        out = remove(cfg, a.slug or "", yes=a.yes)
    else:
        raise SystemExit(f"account {what}: use add, list, page, job or remove")
    for ln in out:
        print(ln)


def add_parser(sub) -> None:
    """factory.py account ... (wired in factory.py main)."""
    p = sub.add_parser("account", help="client accounts: add, list, page, job, remove (clipfactory/accounts.py)")
    p.add_argument("what", choices=["add", "list", "page", "job", "remove"])
    p.add_argument("slug", nargs="?")
    p.add_argument("source", nargs="?", help="job: a URL or a video file")
    p.add_argument("--name", help="add: the client's name; job: the job's name")
    p.add_argument("--handle", help="add: the client's @handle (the name on the picture)")
    p.add_argument("--maker", default="generic", help="add: generic (viral-clip-engine, neutral look)")
    p.add_argument("--seconds", default="30-60", help="add: the length band, MIN-MAX (default 30-60)")
    p.add_argument("--slots", default="09:00,13:00,19:00", help="add: the posting times (default 09:00,13:00,19:00)")
    p.add_argument("--approvers", help="add: who signs, comma separated (default: the owner)")
    p.add_argument("--contact", help="add: the client's contact name")
    p.add_argument("--category", default="22", help="add: YouTube category number (default 22, People & Blogs)")
    p.add_argument("--timezone", help="add: the client's time zone, for its page (default: the factory's)")
    p.add_argument("--rights", default="owned", help="add: owned | licensed | permission | campaign")
    p.add_argument("--folder", help="add: the account folder (default ~/Desktop/Andrew Johnson/Clients/<Name>)")
    p.add_argument("--source-url", help="job: the public link of a file source (the engine's provenance check)")
    p.add_argument("--format", help="job: the source's format for the engine (default: it detects)")
    p.add_argument("--host", help="page: a host name the client reaches the page by (a tunnel)")
    p.add_argument("--new", action="store_true", help="page: a new secret link (the old one stops working)")
    p.add_argument("--yes", action="store_true", help="remove: really remove it")
    p.add_argument("--dry-run", action="store_true")
    p.set_defaults(fn=lambda cfg, a: cmd(cfg, a))
