"""DM automations, creative and never flagged: rules that answer what people actually ask under a clip.

A rule fires on a new top-level comment (or, once Meta's review allows it, a message) that asks something it knows,
by the comment's intents (the Inbox's classifier: asks_source, asks_full, asks_who, asks_which, asks_when, asks_part2,
question) or by whole words. Its answer is made only from the clip's own facts on disk (answers.py): never an opinion,
never engagement bait ("comment X to get Y" is refused at save), never a link in public. A fact that is not there
means no answer.

    state/<slug>/engage/rules.json      {"v": 1, "rules": {id: rule}, "seeded": {"by", "at"}}
    state/<slug>/engage/runs-YYYY-MM.jsonl   one row per comment a rule fired on: what it would send, to whom, when,
                                        and why it was held or skipped
    state/<slug>/engage/waitlist.json   who asked for a Part 2, by the source they wait on

A rule's life: a draft practises (unsigned rules never send; practice runs the whole path and writes the exact words
with no platform call). Andrew signs it on the review page (Inbox, Automations): the signature is the sha256 of its
platforms, trigger, texts and limits, so any edit voids it (a mode change does not). Testers mode sends only to the
people with a role on the Meta app; live needs the signature, 24 hours of practice, the account's own automations
switch at least as far on (its go-live checklist ran when it was switched), and the warm-up (outward). Every send goes
through outward: its caps, quiet hours, windows, holds and Stop everything.

    rules, get, rule_hash, signed_state, problems, warnings          the rules and their checks
    save, sign, set_mode, duplicate, delete, seed                    what the page's clicks do
    on_items(cfg, acct, items, now)       after each Inbox poll (registry.AFTER_POLL): fire on the new comments
    on_posted(cfg, acct, day, slot, outlet, rec, now)   a later clip from the same source: the Part 2 message
    still_signed(cfg, acct, action)       the sender's last look: the rule is still signed as it was queued
    try_text, replay                      "Try it" and "Test on the last 7 days": pure, no writes, no network
    analytics, doctor, cli                per rule numbers; the doctor's lines; factory.py rules ...

Limits: one automated message per person per 24 hours across every rule, one per person per rule in person_days,
the rule's own hour and day (never above the hard caps), one run per comment. A rule pauses itself back to practice
when two people answer its messages with STOP or "bot" or "spam" in 7 days, or when one of its replies is hidden.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import random
import re
import secrets
from datetime import date, datetime, timedelta, timezone
from typing import Callable, Dict, Iterable, List, NamedTuple, Optional, Sequence, Tuple

from . import answers as AN
from . import config as C
from . import registry as REG
from . import store
from . import switches as SW

RNG = random.Random()                 # which variant: tests seed it

# ------------------------------------------------------------------------------------------------------ the words
RULE_MODES = ("practice", "testers", "live", "paused")
ACCOUNT_RANK = {"off": 0, "practice": 1, "testers": 2, "on": 3}
RULE_RANK = {"paused": 0, "practice": 1, "testers": 2, "live": 3}
MODE_WORDS = {"practice": "Practice", "testers": "Testers", "live": "Live", "paused": "Paused"}
PLATFORMS = ("instagram", "facebook", "youtube")
PLAT_WORDS = {"instagram": "Instagram", "facebook": "Facebook", "youtube": "YouTube"}
TRIGGERS = ("comment", "message")
INTENTS = ("asks_source", "asks_full", "asks_who", "asks_which", "asks_when", "asks_part2", "question")
INTENT_WORDS = {"asks_source": "Asks where it is from", "asks_full": "Asks for the full video",
                "asks_who": "Asks who that is", "asks_which": "Asks which hearing or show",
                "asks_when": "Asks when", "asks_part2": "Asks for part 2", "question": "Any question"}
MATCHES = ("any", "all")            # any: an ask OR one of the words; all: an ask AND one of the words (anchored)
# why a run was skipped, in plain words (the page and the command show these, never the codes)
SKIP_WORDS = {"no_fact": "a fact is missing", "text": "the words did not pass the checks",
              "contact_24h": "already answered them today", "person_days": "this rule answered them lately",
              "person_7d": "they had a private reply in the last 7 days", "rule_hour": "the hour's limit",
              "rule_day": "the day's limit", "stop": "they said stop", "youtube": "YouTube: a suggestion only",
              "repeat": "the same words went out today", "cap": "a hard cap", "window": "too late for it",
              "no_private": "no private reply allowed", "already": "already queued", "held": "held",
              "no_action": "nothing to send", "quiet": "quiet hours", "outward": "the sender is not built",
              "error": "a check failed", "other": "other"}
URL_INTENTS = ("asks_source", "asks_full")          # a link may go only in a message that answers these (and part 2)
PUBLIC_MAX = 300
PRIVATE_MAX_BYTES = 900
PUBLIC_VARIANTS = (3, 6)
PRIVATE_VARIANTS = (1, 3)
JACCARD_MAX = 0.8
DISCLOSURE = "Automated reply: "
DISCLOSE_GAP_DAYS = 30              # an automated message after this long a gap says it is automated again
PRIVATE_PERSON_DAYS = 7             # Meta: one private reply per person in 7 days, manual or automated, every rule
PRACTICE_HOURS = 24
CONTACT_HOURS = 24
PART2_DAYS = 6
SELF_PAUSE_N = 2
SELF_PAUSE_DAYS = 7
DEFAULT_LIMITS = {"per_hour": 6, "per_day": 30, "person_days": 7}
DEFAULT_MAX_AGE_H = 24
# the Part 2 notes: fixed words the factory writes itself (no rule edits them), shown in the Sign confirm and covered by
# the rule's hash when its Part 2 is on. Rotated like a rule's variants (never the one used last, Jaccard < 0.8), so
# a clip with many waiters never sends one body to all of them.
PART2_PRIVATE: Tuple[str, ...] = (
    "Part 2 is up: {part2_url}",
    "The next part is posted now: {part2_url}",
)
PART2_PUBLIC: Tuple[str, ...] = (
    "{mention} part 2 is up on the page now.",
    "{mention} the next part just went up on our page.",
    "{mention} you asked for more, and part 2 is posted on the page now.",
)
PRIVATE_KINDS = ("reply_private", "dm")
DASHES = ("\u2014", "\u2013")
_URL = re.compile(r"(https?://|www\.)\S+", re.I)
_TAG = re.compile(r"(^|\s)#\w")
KIND_WORDS = {"reply_public": "public reply", "reply_private": "private reply", "dm": "message reply"}

# engagement bait: one list (answers.BAIT) for the automations' texts and the first comment
BAIT = AN.BAIT
# the Politics stays neutral: source facts only, never an opinion or a reaction to the politics
OPINION = re.compile(r"\b(great|amazing|awesome|terrible|awful|disgrace\w*|shameful|shame|liars?|lies|lying|lied|"
                     r"wrong|radical|corrupt\w*|traitors?|heroe?s?|idiots?|stupid|clowns?|based|woke|destroy\w*|"
                     r"slam\w*|owned|epic|brilliant|pathetic|insane|crazy|evil|best|worst|love|hate|agree|"
                     r"disagree|finally|exposed|caught|lol|lmao|wow|sad|disgusting|hilarious|savage)\b", re.I)
GENERIC_WORDS = {"link", "info", "yes", "no", "this", "that", "lol", "what", "who", "where", "when", "please", "pls",
                 "more", "it", "the", "video", "clip", "ok", "wow", "why", "how"}
# a reply to one of our messages that means stop, or calls it a bot: two in 7 days pause the rule
PUSHBACK = re.compile(r"^\s*(stop|unsubscribe|no more|leave me alone|dont message me|don't message me)\b|"
                      r"\b(bot|bots|spam|spammer|spamming|scam|automated garbage)\b", re.I)
_STOP = re.compile(r"^\s*(stop|unsubscribe|no more|leave me alone|dont message me|don't message me)\b", re.I)

# a small stand-in classifier for "Try it" and items with no tags while the Inbox's classify.py is not built
_FALLBACK = (
    ("asks_source", r"\b(source|sauce|where\W*s this from|where is this from|where (was|is) (this|it) from|"
                    r"what (video|stream|channel|show|clip) is this|link)\b"),
    ("asks_full", r"\b(full (video|stream|clip|hearing|interview|thing|version|vid)|whole (video|thing|stream|"
                  r"hearing|interview)|longer version|uncut)\b"),
    ("asks_who", r"\b(who\W*s (that|this|he|she|the guy|the lady)|who is (that|this|he|she|the)|"
                 r"who was (that|this)|whose (stream|channel|video)|what\W*s (his|her) name|name of (the|this))\b"),
    ("asks_when", r"\b(when (does|is|will|did|was)|release date|come out|comes out|coming out|what date|"
                  r"what day)\b"),
    ("asks_which", r"\b(what|which) (hearing|committee|show|interview|briefing|event|channel|network)\b"),
    ("asks_part2", r"\b(part\s*(2|two|ii)|pt\.?\s*2|next part|part 2 please|more of this)\b"),
    ("praise", r"\b(love (this|it)|great|amazing|fire|goat|legend|lol|lmao)\b"),
)


class Check(NamedTuple):
    """What outward (or the replay's stand-in) says about one action: ok, live or practice, why, when it would go."""
    ok: bool
    mode: str
    why: str
    not_before: Optional[datetime]
    code: str


# ------------------------------------------------------------------------------------------------------ small helpers
def _now(now: Optional[datetime] = None) -> datetime:
    t = now or datetime.now(timezone.utc)
    if t.tzinfo is None:
        t = t.replace(tzinfo=timezone.utc)
    return t.astimezone(timezone.utc)


def _iso(t: datetime) -> str:
    return _now(t).replace(microsecond=0).isoformat()


def _when(v) -> Optional[datetime]:
    return store._when(v) if v not in (None, "") else None


def _slug(acct) -> str:
    return str(getattr(acct, "slug", acct) or "")


def _is_client(acct) -> bool:
    raw = getattr(acct, "raw", None)
    return isinstance(raw, dict) and str(raw.get("owner") or "self") == "client"


def _account(cfg: C.Config, acct) -> C.Account:
    if isinstance(acct, str):
        a = cfg.accounts.get(acct) or cfg.all_accounts.get(acct)
        if a is None:
            raise SystemExit(f"{acct}: not an account in config/accounts.json")
    else:
        a = acct
    if _is_client(a):
        raise SystemExit(f"{a.name} is a client's account: automations are for the owner's own accounts")
    return a


def _own(cfg: C.Config) -> List[C.Account]:
    """The accounts automations work for: switched on, on this machine, never a client's."""
    return [a for a in cfg.accounts.values() if not _is_client(a)]


def _by(cfg: C.Config, by=None) -> str:
    return str(by or (cfg.approvers[0] if cfg.approvers else "factory.py"))


def _SS():
    from . import socialstore as SS
    return SS


def _need_page(page: bool, what: str) -> None:
    if not page:
        raise SystemExit("Only a click on the review page can " + what + ".")


def tags_for(text: str, maker: str = "") -> List[str]:
    """The comment's intents: the Inbox's classifier (classify.tags) when it is built, else a small stand-in."""
    f = REG.fn("classify:tags")
    if f is not None:
        try:
            got = f(str(text or ""), maker)
            if isinstance(got, (list, tuple)):
                return [str(t) for t in got]
        except Exception as e:  # noqa: BLE001
            REG._log_error("classify:tags", e)
    low = str(text or "").lower()
    out = [k for k, rx in _FALLBACK if re.search(rx, low)]
    if "?" in low or re.match(r"^\s*(who|what|where|when|which|why|how|is|are|does|do|can|was)\b", low):
        out.insert(0, "question")
    if _STOP.search(low):
        out.append("stop")
    return out


def _words(s: str) -> List[str]:
    return re.findall(r"[a-z0-9']+|\{[a-z0-9_]+\}", str(s or "").lower())


def jaccard(a: str, b: str) -> float:
    x, y = set(_words(a)), set(_words(b))
    if not x and not y:
        return 1.0
    return len(x & y) / max(1, len(x | y))


def bait(text: str) -> str:
    """Why the words are engagement bait ('' when they are not)."""
    return AN.bait(text)


# ------------------------------------------------------------------------------------------------------ the file
def _file(acct) -> dict:
    d = _SS().read(acct, "rules", {}) or {}
    return d if isinstance(d, dict) else {}


def rules(acct) -> List[dict]:
    """The account's rules, in their order (the first that matches a comment wins)."""
    got = _file(acct).get("rules")
    return [dict(r, id=r.get("id") or k) for k, r in got.items() if isinstance(r, dict)] if isinstance(got, dict) \
        else []


def get(acct, rid: str) -> Optional[dict]:
    return next((r for r in rules(acct) if r.get("id") == rid), None)


def _must(acct, rid: str) -> dict:
    r = get(acct, str(rid or ""))
    if r is None:
        raise SystemExit(f"{getattr(acct, 'name', acct)}: no rule {rid!r} (reload the page)")
    return r


def _hash_parts(rule: dict) -> dict:
    out = {k: rule.get(k) for k in ("platforms", "trigger", "action", "limits")}
    if (((rule.get("action") or {}) if isinstance(rule.get("action"), dict) else {}).get("part2") or {}).get("on"):
        out["part2_words"] = [list(PART2_PRIVATE), list(PART2_PUBLIC)]     # the factory's own words it signs
    return out


def rule_hash(rule: dict) -> str:
    """sha256 of the rule's platforms, trigger, texts and limits (never its name, mode or numbers)."""
    blob = json.dumps(_hash_parts(rule), sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def signed_state(rule: dict) -> str:
    """signed (the signature is on these exact words), changed (signed once, edited since) or unsigned."""
    so = rule.get("signoff") if isinstance(rule.get("signoff"), dict) else None
    if not so or not so.get("hash"):
        prev = rule.get("signoff_prev") if isinstance(rule.get("signoff_prev"), dict) else None
        return "changed" if prev and prev.get("hash") else "unsigned"
    return "signed" if so.get("hash") == rule_hash(rule) else "changed"


def is_signed(rule: dict) -> bool:
    return signed_state(rule) == "signed"


# ------------------------------------------------------------------------------------------------------ the shape
def _strs(v, n: int = 20, size: int = 60) -> List[str]:
    if isinstance(v, str):
        v = re.split(r"[\n,]", v)
    out: List[str] = []
    for x in v if isinstance(v, (list, tuple)) else []:
        s = " ".join(str(x or "").split())[:size]
        if s and s.lower() not in [o.lower() for o in out]:
            out.append(s)
    return out[:n]


def _texts(v, n: int) -> List[str]:
    out = []
    for x in v if isinstance(v, (list, tuple)) else []:
        s = " ".join(str(x or "").split())
        if s:
            out.append(s)
    return out[:n]


def _int(v, default: int) -> int:
    try:
        if isinstance(v, bool):
            raise ValueError
        return int(v)
    except (TypeError, ValueError):
        return default


def clean(raw: dict) -> dict:
    """A rule's editable parts in their one shape (what the page and the command send), nothing else."""
    raw = raw if isinstance(raw, dict) else {}
    tr = raw.get("trigger") if isinstance(raw.get("trigger"), dict) else {}
    ac = raw.get("action") if isinstance(raw.get("action"), dict) else {}
    li = raw.get("limits") if isinstance(raw.get("limits"), dict) else {}
    pub = ac.get("public") if isinstance(ac.get("public"), dict) else {}
    pri = ac.get("private") if isinstance(ac.get("private"), dict) else {}
    p2 = ac.get("part2") if isinstance(ac.get("part2"), dict) else {}
    plats = [p for p in (raw.get("platforms") or []) if p in PLATFORMS] if isinstance(raw.get("platforms"), list) \
        else []
    return {
        "name": " ".join(str(raw.get("name") or "").split())[:80],
        "platforms": list(dict.fromkeys(plats)),
        "trigger": {"on": str(tr.get("on") or "comment"),
                    "posts": "list" if tr.get("posts") == "list" else "any",
                    "post_ids": _strs(tr.get("post_ids"), 50, 40),
                    "intents": [i for i in dict.fromkeys(tr.get("intents") or []) if isinstance(i, str)][:8],
                    "keywords": _strs(tr.get("keywords")),
                    "exclude": _strs(tr.get("exclude")),
                    "max_age_h": _int(tr.get("max_age_h"), DEFAULT_MAX_AGE_H),
                    **({"match": "all"} if tr.get("match") == "all" else {})},
        "action": {"public": {"on": bool(pub.get("on")), "variants": _texts(pub.get("variants"), 10)},
                   "private": {"on": bool(pri.get("on")), "variants": _texts(pri.get("variants"), 10)},
                   "part2": {"on": bool(p2.get("on"))}},
        "limits": {"per_hour": _int(li.get("per_hour"), DEFAULT_LIMITS["per_hour"]),
                   "per_day": _int(li.get("per_day"), DEFAULT_LIMITS["per_day"]),
                   "person_days": _int(li.get("person_days"), DEFAULT_LIMITS["person_days"])},
    }


def _kinds(rule: dict) -> List[str]:
    """The outward kinds a rule sends."""
    ac = rule.get("action") or {}
    if (rule.get("trigger") or {}).get("on") == "message":
        return ["dm"] if (ac.get("private") or {}).get("on") else []
    out = []
    if (ac.get("private") or {}).get("on") or (ac.get("part2") or {}).get("on"):
        out.append("reply_private")
    if (ac.get("public") or {}).get("on") or (ac.get("part2") or {}).get("on"):
        out.append("reply_public")
    return out


def ceilings(rule: dict) -> Tuple[int, int]:
    """The highest per hour and per day a rule may set: the lowest hard cap of the kinds it sends."""
    hs, ds = [], []
    for k in _kinds(rule) or ["reply_public"]:
        h, d, _, _ = SW.HARD.get((k, "auto"), (6, 30, 0, 0))
        hs.append(h if h is not None else 6)
        ds.append(d)
    return min(hs), min(ds)


def _text_problems(t: str, where: str, maker: str, public: bool, url_ok: bool) -> List[str]:
    out = []
    names = AN.placeholders(t)
    bad = [n for n in names if n not in AN.PLACEHOLDERS]
    if bad:
        out.append(f"{where}: {{{bad[0]}}} is not a fact a text may use")
    if any(d in t for d in DASHES):
        out.append(f"{where}: no long dashes")
    if t.lower().startswith(DISCLOSURE.lower().strip()):
        out.append(f"{where}: leave out \"{DISCLOSURE.strip()}\" (it is added to the first message to each person)")
    b = bait(t)
    if b:
        out.append(f"{where}: engagement bait ({b}): answer what people ask instead")
    if not [n for n in names if n not in ("mention",)]:
        out.append(f"{where}: needs at least one fact from the clip, such as {{host}} or {{source_title}}")
    if "part2_url" in names:
        out.append(f"{where}: {{part2_url}} is only for the Part 2 message the factory writes itself")
    if public:
        if "mention" not in names:
            out.append(f"{where}: starts or ends with {{mention}}, so each reply names the person it answers")
        if _URL.search(t) or "source_url" in names:
            out.append(f"{where}: no link in a public reply (Facebook and Instagram push those down)")
        if _TAG.search(t):
            out.append(f"{where}: no hashtags in a reply")
        if len(t) > PUBLIC_MAX:
            out.append(f"{where}: {len(t)} characters; {PUBLIC_MAX} at the most")
    else:
        if (_URL.search(t) or "source_url" in names) and not url_ok:
            out.append(f"{where}: a link only goes in a message that answers an ask for the source or the full "
                       f"video (add the intent: where it is from, or the full video)")
        if len(t.encode("utf-8")) + len(DISCLOSURE) > PRIVATE_MAX_BYTES:
            out.append(f"{where}: {len(t.encode('utf-8'))} bytes; {PRIVATE_MAX_BYTES - len(DISCLOSURE)} at the most")
    if maker == "politics":
        m = OPINION.search(t)
        if m:
            out.append(f"{where}: the Politics stays neutral, source facts only (take out \"{m.group(0)}\")")
        if "!" in t:
            out.append(f"{where}: the Politics stays neutral (no exclamation marks)")
    return out


def problems(acct, rule: dict) -> List[str]:
    """Why the rule cannot be saved as it is ([] when it can)."""
    r = clean(rule)
    maker = str(getattr(acct, "maker", "") or "")
    out: List[str] = []
    tr, ac, li = r["trigger"], r["action"], r["limits"]
    if not r["name"]:
        out.append("give the rule a name")
    if not r["platforms"]:
        out.append("pick at least one platform")
    if tr["on"] not in TRIGGERS:
        out.append("the trigger is a comment or a message")
    bad = [i for i in tr["intents"] if i not in INTENTS]
    if bad:
        out.append(f"{bad[0]}: not an intent the Inbox reads")
    if not tr["intents"] and not tr["keywords"]:
        out.append("say what it answers: an intent, or at least one keyword")
    elif tr.get("match") == "all" and not (tr["intents"] and tr["keywords"]):
        out.append("an ask and a word needs both: at least one intent and at least one keyword")
    if tr["posts"] == "list" and not tr["post_ids"]:
        out.append("list the posts it watches, or set it to any post")
    if not 1 <= tr["max_age_h"] <= 168:
        out.append("comments up to 1 to 168 hours old")
    if tr["on"] == "message":
        if ac["public"]["on"]:
            out.append("a message has no public reply: switch the public reply off")
        if ac["part2"]["on"]:
            out.append("Part 2 works on comments only")
        if not ac["private"]["on"]:
            out.append("a message rule answers with a message: switch the reply on")
    if not (ac["public"]["on"] or ac["private"]["on"] or ac["part2"]["on"]):
        out.append("switch on a public reply, a private reply or the Part 2 note")
    if ac["part2"]["on"] and "asks_part2" not in tr["intents"]:
        out.append('the Part 2 note needs the intent "Asks for part 2"')
    url_ok = any(i in URL_INTENTS for i in tr["intents"])
    for name, lo_hi, public in (("public", PUBLIC_VARIANTS, True), ("private", PRIVATE_VARIANTS, False)):
        part = ac[name]
        vs = part["variants"]
        if not part["on"] and not vs:
            continue
        lo, hi = lo_hi
        label = "public reply" if public else ("message" if tr["on"] == "message" else "private reply")
        if part["on"] and not lo <= len(vs) <= hi:
            out.append(f"the {label} needs {lo} to {hi} ways of saying it ({len(vs)} now)")
        for i, t in enumerate(vs):
            out += _text_problems(t, f"{label} {i + 1}", maker, public, url_ok)
        for i in range(len(vs)):
            for j in range(i + 1, len(vs)):
                if jaccard(vs[i], vs[j]) >= JACCARD_MAX:
                    out.append(f"{label} {i + 1} and {j + 1} are too alike: say it a different way "
                               f"(the same words over and over reads as a bot)")
    ph, pd = ceilings(r)
    if not 1 <= li["per_hour"] <= ph:
        out.append(f"per hour: 1 to {ph} (the hard cap)")
    if not 1 <= li["per_day"] <= pd:
        out.append(f"per day: 1 to {pd} (the hard cap)")
    if li["per_hour"] > li["per_day"]:
        out.append("per hour cannot be more than per day")
    if not 1 <= li["person_days"] <= 30:
        out.append("days between replies to one person: 1 to 30")
    elif li["person_days"] < PRIVATE_PERSON_DAYS and (ac["private"]["on"] or ac["part2"]["on"] or
                                                      tr["on"] == "message"):
        out.append(f"days between replies to one person: {PRIVATE_PERSON_DAYS} to 30 for a rule that sends private "
                   f"replies (Meta allows one private reply per person in {PRIVATE_PERSON_DAYS} days)")
    return out


def warnings(acct, rule: dict) -> List[str]:
    """What is allowed but worth a second look."""
    r = clean(rule)
    out = []
    for k in r["trigger"]["keywords"]:
        if k.lower() in GENERIC_WORDS or len(k) < 3:
            out.append(f'"{k}" is a very common word: it may answer comments that did not ask')
    if "youtube" in r["platforms"]:
        out.append("YouTube takes no automated replies: on YouTube a match only shows a suggested reply in the Inbox")
    if r["action"]["private"]["on"]:
        out.append("private replies reach only the app's testers until Meta's review (Advanced Access)")
    return out


# ------------------------------------------------------------------------------------------------------ writing rules
def _new_id(name: str, have: Iterable[str]) -> str:
    base = "r-" + (re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:24] or secrets.token_hex(3))
    rid, n = base, 1
    while rid in set(have):
        n += 1
        rid = f"{base}-{n}"
    return rid


def _put(acct, rule: dict) -> None:
    with _SS().edit(acct, "rules") as d:
        d["v"] = 1
        rs = d.get("rules") if isinstance(d.get("rules"), dict) else {}
        rs[rule["id"]] = rule
        d["rules"] = rs


def save(cfg: C.Config, acct, raw: dict, by: str, page: bool = False, now: Optional[datetime] = None,
         seen_version=None) -> Tuple[str, str]:
    """Save a rule (a new one, or a new version of one): checked, version +1, the signature dropped, back to
    practice (a paused rule stays paused). (its id, what it says)."""
    _need_page(page, "change an automation")
    a = _account(cfg, acct)
    now = _now(now)
    body = clean(raw)
    probs = problems(a, body)
    if probs:
        raise SystemExit("Not saved: " + "; ".join(probs[:6]) + ("." if len(probs) <= 6 else f"; and {len(probs) - 6} more."))
    rid = str((raw or {}).get("id") or "")
    with _SS().edit(a, "rules") as d:
        d["v"] = 1
        rs = d.get("rules") if isinstance(d.get("rules"), dict) else {}
        old = rs.get(rid) if rid else None
        if rid and not isinstance(old, dict):
            raise SystemExit(f"{a.name}: no rule {rid!r} to change (reload the page)")
        if old is not None and seen_version is not None and _int(seen_version, -1) != _int(old.get("version"), 0):
            raise SystemExit(f"{a.name}: {old.get('name')} changed since the page showed it (version "
                             f"{old.get('version')}): reload, then edit it again")
        if old is None:
            rid = _new_id(body["name"], rs)
            rule = {"id": rid, **body, "mode": "practice", "practice_since": _iso(now), "practice_version": 1,
                    "version": 1, "signoff": None, "flags": [], "starter": False, "created_at": _iso(now),
                    "updated_at": _iso(now), "created_by": str(by or "")}
        else:
            rule = dict(old)
            same = rule_hash({**old, **body}) == rule_hash(old)
            rule.update(body)
            if same:
                rule["updated_at"] = _iso(now)
                rs[rid] = rule
                d["rules"] = rs
                return rid, f"{a.name}: {rule['name']}: nothing in its trigger, words or limits changed (name saved)"
            rule["version"] = _int(old.get("version"), 0) + 1
            if isinstance(old.get("signoff"), dict) and old["signoff"].get("hash"):
                rule["signoff_prev"] = old["signoff"]          # "Changed after signing" until he signs again
            rule["signoff"] = None
            if rule.get("mode") in ("testers", "live"):
                rule["mode"] = "practice"
            # new words practise from now, whatever the mode: a paused rule's old clock never counts for them
            rule["practice_since"] = _iso(now)
            rule["practice_version"] = rule["version"]
            rule["starter"] = False
            rule["updated_at"] = _iso(now)
            rule["updated_by"] = str(by or "")
        rs[rid] = rule
        d["rules"] = rs
    mode = MODE_WORDS.get(rule["mode"], rule["mode"]).lower()
    return rid, (f"{a.name}: {rule['name']} saved as version {rule['version']}, in {mode} and unsigned. "
                 f"Read the words, then sign it." + (" Set it to Practice: a paused rule practises nothing, and "
                                                     "these words need 24 hours of practice." if mode == "paused"
                                                     else ""))


def _overlap(a: dict, b: dict) -> List[str]:
    """What two rules both answer, on a platform they share (intents and keywords)."""
    if (a.get("trigger") or {}).get("on") != (b.get("trigger") or {}).get("on"):
        return []
    if not set(a.get("platforms") or []) & set(b.get("platforms") or []):
        return []
    ta, tb = a.get("trigger") or {}, b.get("trigger") or {}
    both = [INTENT_WORDS.get(i, i).lower() for i in set(ta.get("intents") or []) & set(tb.get("intents") or [])
            if i != "question"]
    both += [f'"{k}"' for k in {x.lower() for x in ta.get("keywords") or []} &
             {x.lower() for x in tb.get("keywords") or []}]
    return sorted(both)


def sign_words(acct, rule: dict) -> str:
    """The whole rule in words: what the Sign button's confirm shows (and what the signature covers)."""
    tr, ac, li = rule.get("trigger") or {}, rule.get("action") or {}, rule.get("limits") or {}
    plats = " and ".join(PLAT_WORDS.get(p, p) for p in rule.get("platforms") or [])
    on = "messages to" if tr.get("on") == "message" else "comments on"
    posts = "any post" if tr.get("posts") != "list" else f"these posts: {', '.join(tr.get('post_ids') or [])}"
    lines = [f"Sign \"{rule.get('name')}\" for {getattr(acct, 'name', acct)}?",
             f"It answers {on} {C.possessive(getattr(acct, 'name', acct))} {plats} ({posts}), up to "
             f"{tr.get('max_age_h', DEFAULT_MAX_AGE_H)} hours old, when the "
             f"{'message' if tr.get('on') == 'message' else 'comment'}:"]
    if tr.get("intents"):
        lines.append("  " + "; or ".join(INTENT_WORDS.get(i, i).lower() for i in tr["intents"]))
    if tr.get("keywords"):
        joint = "  and uses one of the words: " if tr.get("match") == "all" else "  or uses the words: "
        lines.append((joint if tr.get("intents") else "  uses the words: ") + ", ".join(tr["keywords"]))
    if tr.get("exclude"):
        lines.append("  never when it uses: " + ", ".join(tr["exclude"]))
    pub, pri = ac.get("public") or {}, ac.get("private") or {}
    if pub.get("on"):
        lines.append(f"Public reply under the comment, from {getattr(acct, 'name', acct)}, one of:")
        lines += [f"  {v}" for v in pub.get("variants") or []]
    if pri.get("on"):
        lines.append(("Message reply" if tr.get("on") == "message" else "Private reply") +
                     f", one of (the first to each person starts \"{DISCLOSURE.strip()}\"):")
        lines += [f"  {v}" for v in pri.get("variants") or []]
    if (ac.get("part2") or {}).get("on"):
        lines.append("Part 2: they wait for the next clip from the same source (6 days), then get ONE note, within "
                     "the limits below, in a private reply, one of:")
        lines += [f"  {v}" for v in PART2_PRIVATE]
        lines.append("  or in public while messages wait for Meta's review, one of:")
        lines += [f"  {v}" for v in PART2_PUBLIC]
    lines.append(f"At most {li.get('per_hour')} an hour, {li.get('per_day')} a day, one per person every "
                 f"{li.get('person_days')} days, one message per person a day across every rule.")
    lines.append("It practises first: nothing reaches anyone until you set it to Testers or Live.")
    return "\n".join(lines)


def sign(cfg: C.Config, acct, rid: str, seen_hash: str, by: str, page: bool = False,
         now: Optional[datetime] = None) -> str:
    """Andrew signs the rule as the page showed it (seen_hash): the signature covers its platforms, trigger, words
    and limits. Refused for YouTube, for a rule that would not save, and for one that answers what another signed
    rule already answers."""
    _need_page(page, "sign an automation")
    a = _account(cfg, acct)
    now = _now(now)
    with _SS().edit(a, "rules") as d:
        rs = d.get("rules") if isinstance(d.get("rules"), dict) else {}
        rule = rs.get(str(rid or ""))
        if not isinstance(rule, dict):
            raise SystemExit(f"{a.name}: no rule {rid!r} (reload the page)")
        h = rule_hash(rule)
        if not seen_hash or str(seen_hash) != h:
            raise SystemExit(f"{a.name}: {rule.get('name')} changed since the page showed it: reload and read it "
                             f"again before signing")
        if "youtube" in (rule.get("platforms") or []):
            raise SystemExit("YouTube takes no automated replies (only his own clicks): take YouTube off this rule "
                             "to sign it. A YouTube match shows as a suggested reply in the Inbox.")
        probs = problems(a, rule)
        if probs:
            raise SystemExit("Not signed: " + "; ".join(probs[:4]))
        for other in rs.values():
            if not isinstance(other, dict) or other.get("id") == rule.get("id") or other.get("mode") == "paused":
                continue
            if signed_state(other) != "signed":
                continue
            both = _overlap(rule, other)
            if both:
                raise SystemExit(f"Not signed: \"{other.get('name')}\" already answers {', '.join(both)} on the same "
                                 f"platform. Take those out of one of them, or pause the other.")
        rule["signoff"] = {"by": str(by or ""), "at": _iso(now), "hash": h, "version": rule.get("version")}
        rule.pop("signoff_prev", None)
        rule["updated_at"] = _iso(now)
        rs[rule["id"]] = rule
        d["rules"] = rs
    return (f"{a.name}: {rule.get('name')} is signed (version {rule.get('version')}). It stays in "
            f"{MODE_WORDS.get(rule.get('mode'), rule.get('mode')).lower()} until you set it to Testers or Live.")


def _practised_h(rule: dict, now: datetime) -> float:
    """Hours these exact words practised: from practice_since (set again by every edit), only for the version they
    belong to, and never the time the rule sat paused."""
    pv = rule.get("practice_version")
    if pv is not None and _int(pv, -1) != _int(rule.get("version"), 0):
        return 0.0
    since = _when(rule.get("practice_since"))
    if since is None:
        return 0.0
    end = now
    if rule.get("mode") == "paused":
        pa = _when(rule.get("paused_at"))
        end = min(now, pa) if pa is not None else since
    return max(0.0, (end - since).total_seconds() / 3600)


def live_problems(cfg: C.Config, acct, rule: dict, mode: str, now: Optional[datetime] = None) -> List[str]:
    """What still stands between the rule and Testers or Live ([] when nothing)."""
    now = _now(now)
    a = acct
    out: List[str] = []
    st = signed_state(rule)
    if st != "signed":
        out.append("sign it first" if st == "unsigned" else "it changed after you signed it: sign it again")
    if "youtube" in (rule.get("platforms") or []):
        out.append("take YouTube off it")
    h = _practised_h(rule, now)
    if h < PRACTICE_HOURS:
        out.append(f"practise it for {PRACTICE_HOURS} hours first ({int(h)} so far)")
    am = SW.mode(a, "automations")
    want = {"testers": "testers", "live": "on"}[mode]
    if ACCOUNT_RANK.get(am, 0) < ACCOUNT_RANK[want]:
        out.append(f"{C.possessive(a.name)} automations switch is {am}: it needs {want} first (Log, Switches; that runs the "
                   f"go-live checklist)")
    if mode == "testers" and not SW.settings(a).get("testers"):
        out.append("add a tester first (factory.py social tester ... or This is me in the Inbox)")
    return out


def set_mode(cfg: C.Config, acct, rid: str, mode: str, by: str, page: bool = False,
             now: Optional[datetime] = None) -> str:
    """Practice, Testers, Live or Paused. Pausing and practising are always allowed (the command can do those);
    Testers and Live are a click on the page and need the signature on these words, 24 hours of practice and the
    account's own switch that far on. A mode change never voids the signature."""
    a = _account(cfg, acct)
    now = _now(now)
    mode = str(mode or "").strip().lower()
    if mode not in RULE_MODES:
        raise SystemExit(f"{mode or 'that'}: a rule is in practice, testers, live or paused")
    if mode in ("testers", "live"):
        _need_page(page, "let an automation reach people")
    with _SS().edit(a, "rules") as d:
        rs = d.get("rules") if isinstance(d.get("rules"), dict) else {}
        rule = rs.get(str(rid or ""))
        if not isinstance(rule, dict):
            raise SystemExit(f"{a.name}: no rule {rid!r} (reload the page)")
        old = rule.get("mode") or "practice"
        if old == mode:
            return f"{a.name}: {rule.get('name')} is already {MODE_WORDS[mode].lower()}"
        if mode in ("testers", "live"):
            probs = live_problems(cfg, a, rule, mode, now)
            if probs:
                raise SystemExit(f"{rule.get('name')} stays {MODE_WORDS.get(old, old).lower()}: " + "; ".join(probs))
            rule["flags"] = [f for f in rule.get("flags") or [] if not (isinstance(f, dict) and
                                                                         f.get("key") == "self_pause")]
        if mode == "practice" and (old == "paused" or not rule.get("practice_since")):
            rule["practice_since"] = _iso(now)
            rule["practice_version"] = rule.get("version")
        if mode == "paused":
            rule["paused_at"] = _iso(now)
        if mode in ("testers", "live") and old not in ("testers", "live"):
            rule["reach_since"] = _iso(now)            # anything queued before this belongs to an older go-ahead
        rule["mode"] = mode
        hist = rule.get("history") if isinstance(rule.get("history"), list) else []
        hist.append({"at": _iso(now), "by": str(by or ""), "from": old, "to": mode})
        rule["history"] = hist[-50:]
        rule["updated_at"] = _iso(now)
        rs[rule["id"]] = rule
        d["rules"] = rs
    return f"{a.name}: {rule.get('name')} is now {MODE_WORDS[mode].lower()} (was {MODE_WORDS.get(old, old).lower()})"


def duplicate(cfg: C.Config, acct, rid: str, to, by: str, page: bool = False, now: Optional[datetime] = None) -> str:
    """A copy of the rule on another account, as a draft: practice, unsigned, version 1."""
    _need_page(page, "copy an automation")
    a = _account(cfg, acct)
    b = _account(cfg, to)
    now = _now(now)
    src = _must(a, rid)
    body = clean(src)
    have = [r["id"] for r in rules(b)]
    new = {"id": _new_id(body["name"], have), **body, "mode": "practice", "practice_since": _iso(now),
           "practice_version": 1, "version": 1,
           "signoff": None, "flags": [], "starter": False, "created_at": _iso(now), "updated_at": _iso(now),
           "created_by": str(by or ""), "copied_from": f"{a.slug}/{src['id']}@v{src.get('version')}"}
    probs = problems(b, new)
    _put(b, new)
    tail = f" Before you sign it: {'; '.join(probs[:3])}." if probs else ""
    return f"{b.name}: {new['name']} copied from {a.name} as a draft (practice, unsigned).{tail}"


def delete(cfg: C.Config, acct, rid: str, by: str, page: bool = False) -> str:
    """Remove a draft. A rule that was ever signed stays (pause it): its record is part of what went out."""
    _need_page(page, "delete an automation")
    a = _account(cfg, acct)
    with _SS().edit(a, "rules") as d:
        rs = d.get("rules") if isinstance(d.get("rules"), dict) else {}
        rule = rs.get(str(rid or ""))
        if not isinstance(rule, dict):
            raise SystemExit(f"{a.name}: no rule {rid!r} (reload the page)")
        if rule.get("signoff") or rule.get("signoff_prev") or any(
                isinstance(h, dict) and h.get("to") in ("testers", "live") for h in rule.get("history") or []):
            raise SystemExit(f"{rule.get('name')} was signed: pause it instead (its record stays with what it sent)")
        rs.pop(rule["id"], None)
        d["rules"] = rs
    return f"{a.name}: the draft {rule.get('name')} is deleted"


# ------------------------------------------------------------------------------------------------------ starters
def starters(acct) -> List[dict]:
    """The starter drafts for an account's maker: answers to what people naturally ask, from facts on disk. Each is
    anchored (an ask AND one of its words), so a broad "when" or "who" never draws an answer to a question it was
    not asked: "when is part 2 coming out" is not a GTA 6 date, "who is paying for this" is not the speaker."""
    maker = str(getattr(acct, "maker", "") or "")
    lim = dict(DEFAULT_LIMITS)
    both = ["instagram", "facebook"]

    def r(rid, name, intents, keywords, public, private=None, part2=False, exclude=None, match="all"):
        tr = {"on": "comment", "posts": "any", "post_ids": [], "intents": intents, "keywords": keywords,
              "exclude": exclude or [], "max_age_h": DEFAULT_MAX_AGE_H}
        if match == "all":
            tr["match"] = "all"
        return {"id": rid, "name": name, "platforms": list(both), "trigger": tr,
                "action": {"public": {"on": True, "variants": public},
                           "private": {"on": False, "variants": private or []}, "part2": {"on": part2}},
                "limits": dict(lim)}
    not_part2 = ["part 2", "part two", "pt 2", "next part", "next one", "next video", "next clip"]
    if maker == "politics":
        return [
            r("r-src", "Where is this from", ["asks_source", "asks_full"],
              ["source", "full video", "full hearing", "full interview", "full speech", "full thing", "uncut",
               "where is this from", "where's this from", "what channel", "which channel", "what network", "link",
               "original"],
              ["{mention} this is from {host}, {date}: {source_title}.",
               "{mention} it comes from {source_title}, aired {date} on {host}.",
               "{mention} source: {host} on {date}. The full {kind} link is {first_comment}.",
               "{mention} the full {kind} is {source_title} from {host}, {date}."],
              ["Here is the full {kind}: {source_url}", "The full {kind} from {host}: {source_url}"]),
            r("r-who", "Who is that", ["asks_who"],
              ["who is that", "who is this", "who's that", "who's this", "whos that", "whos this", "who was that",
               "who is speaking", "who is talking", "who is he", "who is she", "who's he", "who's she",
               "who is the senator", "who is the congressman", "who is the congresswoman", "who is the guy",
               "who is the man", "who is the woman", "who is the lady", "who is the speaker", "his name",
               "her name"],
              ["{mention} that is {speaker}, {speaker_title}.",
               "{mention} the speaker is {speaker} ({speaker_title}), from {source_title}.",
               "{mention} you are hearing {speaker}. This aired {date} on {host}."]),
            r("r-hearing", "What hearing is this", ["asks_which", "asks_when"],
              ["hearing", "committee", "when was this", "when did this", "when was it", "what show", "which show",
               "what interview", "which interview", "what day was", "what date"],
              ["{mention} this is from the {hearing} hearing on {hearing_date}.",
               "{mention} {hearing}, {hearing_date}.",
               "{mention} it aired {date} on {host}: {source_title}."],
              exclude=not_part2 + ["next hearing", "next election"]),
        ]
    if maker == "gta":
        return [
            r("r-stream", "Whose stream is this", ["asks_who", "asks_source", "asks_full"],
              ["whose stream", "whose channel", "whose video", "who is this", "who is that", "who's this",
               "who's that", "whos this", "whos that", "who is he", "who is the streamer", "what stream",
               "which stream", "what channel", "streamer", "source", "sauce", "full video", "full stream",
               "where is this from", "where's this from", "link"],
              ["{mention} this is {host}, from {source_title}.",
               "{mention} that is the {kind} by {host}: {source_title}.",
               "{mention} it comes from {host}. The full {kind} link is {first_comment}.",
               "{mention} clip from {host}, out of the {kind} {source_title}."],
              ["Full video from {host}: {source_url}", "Here is the full {kind} by {host}: {source_url}"],
              exclude=["ready"]),
            r("r-gta6", "When does GTA 6 come out", ["asks_when"],
              ["gta 6", "gta vi", "gta6", "gtavi", "release date"],
              ["{mention} GTA 6 comes out {gta6_date}, {days_to_gta6} from now.",
               "{mention} {gta6_date}. That is {days_to_gta6} away.",
               "{mention} the release date is {gta6_date}, so {days_to_gta6} to go."],
              exclude=not_part2 + ["trailer 3", "stream", "streamed", "this video", "full video"]),
            r("r-part2", "Part 2 please", ["asks_part2"], ["part 2", "part two", "pt 2", "next part"],
              ["{mention} noted. If a part 2 from {host} goes up, we will let you know.",
               "{mention} got it: you are on the list for the next {kind} clip from {host}.",
               "{mention} thanks for asking. We will tell you if more of this {kind} gets posted."],
              part2=True, match="any"),
        ]
    return []


def seed(cfg: C.Config, acct, by: str, now: Optional[datetime] = None) -> str:
    """The starter drafts, once per account: practice, unsigned, marked starter (they send nothing until he signs
    them). Never again after the first time (a rule deleted stays deleted)."""
    a = _account(cfg, acct)
    now = _now(now)
    st = starters(a)
    if not st:
        return f"{a.name}: no starter rules for its maker ({a.maker})"
    with _SS().edit(a, "rules") as d:
        if isinstance(d.get("seeded"), dict):
            when = str(d["seeded"].get("at") or "")[:10]
            raise SystemExit(f"{a.name}: the starter rules were added on {when}: make a new rule by hand instead")
        d["v"] = 1
        rs = d.get("rules") if isinstance(d.get("rules"), dict) else {}
        added = []
        for r in st:
            rid = r["id"] if r["id"] not in rs else _new_id(r["name"], rs)
            rs[rid] = {**r, "id": rid, "mode": "practice", "practice_since": _iso(now), "practice_version": 1,
                       "version": 1,
                       "signoff": None, "flags": [], "starter": True, "created_at": _iso(now),
                       "updated_at": _iso(now), "created_by": str(by or "")}
            added.append(r["name"])
        d["rules"] = rs
        d["seeded"] = {"by": str(by or ""), "at": _iso(now)}
    return (f"{a.name}: {len(added)} starter rules added in practice, unsigned: {', '.join(added)}. Read their words, "
            f"then sign the ones you want.")


def draft_from(cfg: C.Config, acct, intent: str, words: str, by: str, now: Optional[datetime] = None) -> str:
    """The learner's approved draft automation (learn.approve of a draft_rule proposal, through the registry): the
    starter answer to that question as a new rule in practice and unsigned, so it sends nothing until Andrew reads
    its words and signs it on Inbox, Automations. Nothing when a rule already answers the question. What it did."""
    a = _account(cfg, acct)
    now = _now(now)
    intent = str(intent or "")
    asked = INTENT_WORDS.get(intent) or " ".join(str(words or intent).split())[:80]
    if intent not in INTENTS:
        raise SystemExit(f"{intent!r}: not a question an automation answers ({', '.join(INTENTS)})")
    have = [r for r in rules(a) if intent in ((r.get("trigger") or {}).get("intents") or [])]
    if have:
        return f"{a.name}: {have[0].get('name')} already answers \"{asked}\": nothing new drafted"
    pick = next((r for r in starters(a) if intent in r["trigger"]["intents"]), None)
    if pick is None:
        raise SystemExit(f"{a.name}: no starter answer for \"{asked}\" here: make the rule on Inbox, Automations")
    body = clean(pick)
    probs = problems(a, body)
    if probs:
        raise SystemExit("Not drafted: " + "; ".join(probs[:4]))
    with _SS().edit(a, "rules") as d:
        d["v"] = 1
        rs = d.get("rules") if isinstance(d.get("rules"), dict) else {}
        rid = _new_id(body["name"], rs)
        rs[rid] = {"id": rid, **body, "mode": "practice", "practice_since": _iso(now), "practice_version": 1,
                   "version": 1, "signoff": None, "flags": [], "starter": False, "created_at": _iso(now),
                   "updated_at": _iso(now), "created_by": str(by or ""), "from": "learn"}
        d["rules"] = rs
    return (f"{a.name}: {body['name']} drafted on Inbox, Automations, in practice and unsigned: it sends nothing "
            f"until you read its words and sign it there")


# ------------------------------------------------------------------------------------------------------ matching
def _plain(text: str) -> str:
    """Curly apostrophes as straight ones (phones type ’), so "who’s that" meets the word "who's that"."""
    return str(text or "").replace("\u2019", "'").replace("\u2018", "'")


def _phrase_rx(words: str) -> re.Pattern:
    parts = [re.escape(w) for w in _plain(words).lower().split()]
    return re.compile(r"(?<![\w])" + r"\s+".join(parts) + r"s?(?![\w])", re.I)


def keyword_hits(text: str, keywords: Sequence[str]) -> List[str]:
    """The keywords in the text: whole words, any case, a plural s, a phrase across line breaks."""
    t = _plain(text)
    return [k for k in keywords or [] if str(k).strip() and _phrase_rx(k).search(t)]


def match(rule: dict, item: dict, tags: Sequence[str], now: datetime) -> Optional[dict]:
    """{intents, keywords} the item matched the rule on, or None (an exclusion vetoes; too old; not its post)."""
    tr = rule.get("trigger") or {}
    text = str(item.get("text") or "")
    if keyword_hits(text, tr.get("exclude") or []):
        return None
    if tr.get("posts") == "list":
        pid = str((item.get("post") or {}).get("platform_id") or "")
        if pid not in [str(x) for x in tr.get("post_ids") or []]:
            return None
    at = _when(item.get("at"))
    if at is not None and now - at > timedelta(hours=_int(tr.get("max_age_h"), DEFAULT_MAX_AGE_H)):
        return None
    intents = [i for i in tr.get("intents") or [] if i in (tags or [])]
    kws = keyword_hits(text, tr.get("keywords") or [])
    if not intents and not kws:
        return None
    if tr.get("match") == "all" and not (intents and kws):
        return None                                   # anchored: the ask AND one of its words
    return {"intents": intents, "keywords": kws}


def _is_trigger(item: dict) -> bool:
    """A comment at the top of a post, or a message to us: never ours, never a reply, never hidden or gone."""
    if not isinstance(item, dict) or not item.get("id"):
        return False
    a = item.get("author") if isinstance(item.get("author"), dict) else {}
    if a.get("is_us") or item.get("hidden") or item.get("state") in ("hidden", "gone"):
        return False
    if item.get("kind") == "dm":
        return True
    return item.get("kind") in (None, "comment") and not item.get("parent_id")


# ------------------------------------------------------------------------------------------------------ the decision
class _Hist:
    """The runs so far (the file's, or the replay's own), for one run per comment, the people and the rule's limits,
    and the variant used last."""

    def __init__(self, rows: Iterable[dict]):
        self.rows = [r for r in rows if isinstance(r, dict)]

    def add(self, row: dict) -> None:
        self.rows.append(row)

    def has_item(self, iid: str) -> bool:
        return any(r.get("item") == iid for r in self.rows)

    def _since(self, since: datetime, results: Sequence[str], rid: Optional[str] = None,
               person: Optional[str] = None, kinds: Optional[Sequence[str]] = None) -> List[dict]:
        out = []
        for r in self.rows:
            if r.get("result") not in results:
                continue
            if rid is not None and r.get("rule") != rid:
                continue
            if person is not None and r.get("to") != person:
                continue
            if kinds is not None and not any(x.get("kind") in kinds and x.get("ok")
                                             for x in r.get("actions") or [] if isinstance(x, dict)):
                continue
            t = _when(r.get("at"))
            if t is not None and t >= since:
                out.append(r)
        return out

    def count(self, rid: str, results: Sequence[str], since: datetime) -> int:
        return len(self._since(since, results, rid=rid))

    def to_person(self, person: str, results: Sequence[str], since: datetime, rid: Optional[str] = None) -> int:
        return len(self._since(since, results, rid=rid, person=person))

    def private_before(self, person: str, results: Sequence[str]) -> bool:
        return bool(self._since(datetime(1970, 1, 1, tzinfo=timezone.utc), results, person=person,
                                kinds=("reply_private", "dm")))

    def last_variant(self, rid: Optional[str], kind: str, part2: bool = False) -> Optional[int]:
        """The variant used last: the rule's own (rid), or with part2 the Part 2 note's, across every rule (its
        words are the factory's, the same for each rule)."""
        for r in reversed(self.rows):
            if (r.get("on") == "part2") != part2 or (not part2 and r.get("rule") != rid):
                continue
            for x in r.get("actions") or []:
                if isinstance(x, dict) and x.get("kind") == kind and isinstance(x.get("variant"), int):
                    return x["variant"]
        return None


def rendered_problem(text: str, kind: str, maker: str, url_ok: bool = False) -> str:
    """Why the words as they would go out (the facts filled in) must not go ('' when they may). The facts come from
    titles people wrote, so a hashtag, a dash, a shout or an over-long title is caught here, after the fill."""
    t = str(text or "")
    if any(d in t for d in DASHES):
        return "the filled-in words have a long dash"
    if kind == "reply_public":
        if len(t) > PUBLIC_MAX:
            return f"the filled-in reply is {len(t)} characters; {PUBLIC_MAX} at the most"
        if _URL.search(t):
            return "the filled-in reply has a link"
        if _TAG.search(t):
            return "the filled-in reply has a hashtag"
        b = bait(t)
        if b:
            return f"the filled-in reply reads as engagement bait ({b})"
        if maker == "politics":
            m = OPINION.search(t)
            if m:
                return f"the filled-in reply is not neutral (\"{m.group(0)}\")"
            if "!" in t:
                return "the filled-in reply has an exclamation mark"
    else:
        if len((DISCLOSURE + t).encode("utf-8")) > PRIVATE_MAX_BYTES:
            return f"the filled-in message is over {PRIVATE_MAX_BYTES} bytes"
        if _URL.search(t) and not url_ok:
            return "a link only goes in a message that answers an ask for the source; this comment asked something else"
    return ""


def _pick(rule: dict, kind: str, variants: Sequence[str], fx: Dict[str, str], hist: _Hist, maker: str = "",
          url_ok: bool = False, part2: bool = False) -> Tuple[Optional[int], Optional[str], str]:
    """A variant the facts can fill and whose filled-in words pass the checks, never the one used last when there is
    another: (index, words, why). why is "" with words; "no_fact" when no variant can be filled; else what failed."""
    ok, bad = [], ""
    for i, v in enumerate(variants or []):
        got = AN.render(v, fx)
        if not got:
            continue
        why = rendered_problem(got, kind, maker, url_ok)
        if why:
            bad = bad or why
            continue
        ok.append((i, got))
    if not ok:
        return None, None, (bad or "no_fact")
    last = hist.last_variant(rule.get("id"), kind, part2=part2)
    pool = [x for x in ok if x[0] != last] or ok
    i, got = RNG.choice(pool)
    return i, got, ""


def _cid(item: dict) -> str:
    return str(item.get("platform_comment_id") or str(item.get("id") or "").partition(":")[2])


def _force(cfg: C.Config, a, rule: dict, person: str) -> Tuple[bool, str]:
    """(practice no matter what, why): an unsigned or changed rule, one in practice, testers mode for a non-tester."""
    st = signed_state(rule)
    if st == "unsigned":
        return True, "the rule is not signed: practice"
    if st == "changed":
        return True, "the rule changed after it was signed: practice"
    mode = rule.get("mode") or "practice"
    if mode == "practice":
        return True, "the rule is in practice"
    if mode == "testers" and not SW.is_tester(a, person):
        return True, "testers mode: not a tester"
    return False, ""


def _signed_for(rule: dict) -> dict:
    so = rule.get("signoff") if isinstance(rule.get("signoff"), dict) else {}
    return {"what": f"rule:{rule.get('id')}@v{rule.get('version')}", "by": str(so.get("by") or ""),
            "at": str(so.get("at") or ""), "hash": rule_hash(rule)}


def _outward_check(cfg: C.Config, a, rule: dict, platform: str, kind: str, text: str, target: dict,
                   now: datetime, force_practice: bool) -> Check:
    """outward.check for one action (reads its files only: no network)."""
    f = REG.fn("outward:check")
    if f is None:
        return Check(False, "", "the sender (clipfactory/outward.py) is not built", None, "outward")
    try:
        v = f(cfg, a, platform, kind, origin={"by": "rule", "rule": rule.get("id")}, text=text, target=target,
              manual=False, signed=_signed_for(rule), now=now, force_practice=force_practice)
    except (Exception, SystemExit) as e:  # noqa: BLE001
        return Check(False, "", f"could not check ({type(e).__name__}: {e})"[:200], None, "error")
    return Check(bool(v.ok), str(v.mode or ""), str(v.why or ""), v.not_before, str(v.code or ""))


class _Sim:
    """The replay's stand-in for outward: the hard caps per kind, family and all engagement, quiet hours, the windows,
    the words, counted on the replay's own would-sends (no holds: the past has none to read)."""

    def __init__(self, cfg: C.Config, a):
        self.cfg, self.a = cfg, a
        self.sent: List[Tuple[str, str, float]] = []        # (platform, kind, epoch)
        self.texts: Dict[str, float] = {}

    def check(self, rule: dict, platform: str, kind: str, text: str, target: dict, now: datetime,
              force_practice: bool) -> Check:
        from . import outward as OW
        notes: List[str] = []
        try:
            from . import capabilities as CAP
            miss = CAP.missing(self.a, platform, CAP.NEED_OF_KIND.get(kind, "comment"))
        except Exception:  # noqa: BLE001
            miss = []
        if miss:
            notes.append("would wait for " + ", ".join(miss))
        if kind in ("reply_private", "dm") and SW.settings(self.a).get("messages_access") != "advanced" \
                and not SW.is_tester(self.a, str(target.get("to") or "")):
            notes.append("DMs reach only app testers until Meta's review")
        at = _when(target.get("at"))
        if kind == "reply_private" and at is not None and now - at > timedelta(days=OW.PRIVATE_REPLY_DAYS):
            return Check(False, "practice", "a private reply only within 6 days of the comment", None, "window")
        t = now
        q = SW.quiet(self.cfg, self.a, now)
        why = ""
        if q is not None:
            t, why = _now(q), "quiet hours"
        if any(d in text for d in DASHES):
            return Check(False, "practice", "the words have a long dash", None, "text")
        if OW._URL.search(text) and not (kind in ("reply_private", "dm") and target.get("intent") in OW.URL_INTENTS):
            return Check(False, "practice", "a link only goes in a message that answers an ask for the source",
                         None, "text")
        ts = t.timestamp()
        sha = OW._text_sha(text)
        if sha in self.texts and ts - self.texts[sha] < 86400:
            return Check(False, "practice", "the same words went out in the last 24 hours", None, "repeat")
        ph, pd, gap, _ = SW.caps_for(self.a, kind, False)
        fam = OW.FAMILY_OF.get(kind, "")
        groups = [((kind,), ph, pd, OW.KIND_WORDS.get(kind, kind))]
        if fam in SW.FAMILY_HARD:
            groups.append((OW.FAMILIES[fam],) + SW.FAMILY_HARD[fam] + (OW.FAMILY_WORDS.get(fam, fam),))
        groups.append((OW._kinds_of("engagement"),) + SW.FAMILY_HARD["engagement"] + ("all engagement",))
        for kinds, per_h, per_d, what in groups:
            stamps = sorted(e for p, k, e in self.sent if p == platform and k in kinds and 0 <= ts - e < 86400)
            if per_d is not None and len(stamps) >= per_d:
                return Check(False, "practice", f"{what}: {per_d} a day is the most", None, "cap")
            hour = [e for e in stamps if ts - e < 3600]
            if per_h is not None and len(hour) >= per_h:
                nt = datetime.fromtimestamp(hour[0] + 3600, timezone.utc)
                if nt > t:
                    t, why = nt, f"{what}: {per_h} an hour is the most"
        last = max([e for p, k, e in self.sent if p == platform and OW.FAMILY_OF.get(k) == fam] or [0])
        if gap and last and last + gap > t.timestamp():
            t, why = datetime.fromtimestamp(last + gap, timezone.utc), why or "spaced out"
        return Check(True, "practice", "; ".join([w for w in [why] + notes if w]), t if t > now else None,
                     "quiet" if why == "quiet hours" else ("cap" if "most" in why else ""))

    def note(self, platform: str, kind: str, text: str, at: datetime) -> None:
        from . import outward as OW
        self.sent.append((platform, kind, at.timestamp()))
        self.texts[OW._text_sha(text)] = at.timestamp()


def _warming(cfg: C.Config, a, now: datetime) -> bool:
    """Is the account still in its warm-up (outward makes every automated action practice then)?"""
    try:
        wu = SW.warmup_until(cfg, a)
    except Exception:  # noqa: BLE001
        return True
    return wu is None or wu > now.astimezone(cfg.tz()).date()


def _private_log(cfg: C.Config, a, now: datetime, cache: Optional[dict]) -> List[Tuple[datetime, str, bool]]:
    """The private replies and messages that really went (or wait in the queue to go) in the last DISCLOSE_GAP_DAYS
    days, his clicks and the rules' alike: (when, person, automated). Read once per poll (the cache)."""
    if cache is not None and "_privlog" in cache:
        return cache["_privlog"]
    out: List[Tuple[datetime, str, bool]] = []
    since = now - timedelta(days=DISCLOSE_GAP_DAYS)
    f = REG.fn("outward:rows")
    if f is not None:
        try:
            for row in f(cfg, a.slug, since=since, kinds=PRIVATE_KINDS, states=("done",)):
                t = _when(row.get("t"))
                to = str((row.get("target") or {}).get("to") or "")
                if t is None or not to or t > now or row.get("mode") == "practice":
                    continue
                out.append((t, to, (row.get("origin") or {}).get("by") == "rule"))
        except Exception as e:  # noqa: BLE001
            REG._log_error("rules:private_log", e)
    try:
        from . import outward as OW
        for it in OW._items(a.slug).values():
            if it.get("kind") not in PRIVATE_KINDS or it.get("state") not in ("queued", "sending") or \
                    it.get("force_practice"):
                continue
            to = str((it.get("target") or {}).get("to") or "")
            if to:
                out.append((_when(it.get("at")) or now, to, (it.get("origin") or {}).get("by") == "rule"))
    except Exception as e:  # noqa: BLE001
        REG._log_error("rules:private_log", e)
    if cache is not None:
        cache["_privlog"] = out
    return out


def _private_recent(cfg: C.Config, a, person: str, now: datetime, hist: _Hist, sim: bool, replay: bool,
                    cache: Optional[dict]) -> bool:
    """Did the person get a private reply or a message, from any rule or by his own click, in the last 7 days (or is
    one waiting to go)? Meta allows one private reply per person in 7 days."""
    if not person:
        return False
    since = now - timedelta(days=PRIVATE_PERSON_DAYS)
    if hist._since(since, ("queued", "practice") if sim else ("queued",), person=person, kinds=PRIVATE_KINDS):
        return True
    if replay:
        return False
    return any(to == person and t >= since for t, to, _ in _private_log(cfg, a, now, cache))


def _needs_disclosure(cfg: C.Config, a, person: str, now: datetime, hist: _Hist, sim: bool, replay: bool,
                      cache: Optional[dict]) -> bool:
    """Does this automated message start with "Automated reply: "? Yes at the start (no automated message to them
    in DISCLOSE_GAP_DAYS days), after that long a gap, and after a person wrote to them by hand since the last one
    (a human to bot switch). Only automated history counts: his own replies never stand in for the disclosure."""
    if not person:
        return True
    since = now - timedelta(days=DISCLOSE_GAP_DAYS)
    times: List[datetime] = []
    if sim or replay:
        times += [t for t in (_when(r.get("at")) for r in hist._since(since, ("queued", "practice"), person=person,
                                                                       kinds=PRIVATE_KINDS)) if t is not None]
    if not replay:
        times += [t for t, to, auto in _private_log(cfg, a, now, cache) if auto and to == person and t >= since]
    if not times:
        return True
    last = max(times)
    if replay:
        return False
    try:
        c = _SS().contact(a, person)
    except Exception:  # noqa: BLE001
        return True
    lo, la = _when(c.get("last_out_at")), _when(c.get("last_auto_out_at"))
    manual_since = lo is not None and lo > last + timedelta(seconds=1) and (la is None or lo > la)
    return manual_since


def decide(cfg: C.Config, a, item: dict, rule_list: Sequence[dict], now: datetime, hist: _Hist,
           cache: Optional[dict] = None, check: Optional[Callable] = None) -> Optional[dict]:
    """What the rules do with one comment or message: None when it is not a trigger or no rule matches; otherwise
    the run {rule, result queued|practice|skipped, why, note, to, actions[{kind, key, text, variant, mode, would_at,
    why, code, ok}], part2, ...}. Writes nothing: on_items, Try it and the replay each act on it their own way."""
    if not _is_trigger(item):
        return None
    replay = check is not None
    platform = str(item.get("platform") or "")
    maker = str(getattr(a, "maker", "") or "")
    tags = list(item.get("tags") or []) or tags_for(str(item.get("text") or ""), maker)
    on = "message" if item.get("kind") == "dm" else "comment"
    cands = []
    for r in rule_list:
        if r.get("mode") == "paused" or platform not in (r.get("platforms") or []):
            continue
        if (r.get("trigger") or {}).get("on", "comment") != on:
            continue
        m = match(r, item, tags, now)
        if m is not None:
            cands.append((r, m))
    if not cands:
        return None
    if "asks_part2" in tags:          # "when is part 2 coming out" is a Part 2 ask before it is a "when"
        cands.sort(key=lambda rm: 0 if "asks_part2" in rm[1]["intents"] or
                   ((rm[0].get("action") or {}).get("part2") or {}).get("on") else 1)
    author = item.get("author") if isinstance(item.get("author"), dict) else {}
    person = str(author.get("key") or "")
    base = {"id": "ru-" + now.strftime("%Y%m%dT%H%M%S") + "-" + secrets.token_hex(2), "at": _iso(now),
            "item": str(item.get("id")), "platform": platform, "on": on, "to": person,
            "name": str(author.get("name") or ""), "tags": tags,
            "candidates": [r.get("id") for r, _ in cands]}
    fx = AN.item_facts(cfg, a, item, now, cache)
    if platform == "youtube":
        r, m = cands[0]
        idx, text, _ = _pick(r, "reply_public", (r.get("action") or {}).get("public", {}).get("variants") or [], fx,
                             hist, maker)
        return {**base, "rule": r.get("id"), "version": r.get("version"), "matched": m, "result": "skipped",
                "why": "youtube", "note": "YouTube takes no automated replies: a suggested reply for the Inbox",
                "text": {"suggested": text} if text else {}, "suggested": text, "actions": []}
    live = [(r, m) for r, m in cands if r.get("mode") in ("testers", "live") and is_signed(r)]
    rule, m = (live or cands)[0]
    run = {**base, "rule": rule.get("id"), "version": rule.get("version"), "matched": m, "actions": [],
           "text": {}, "part2": False}

    def skip(code: str, note: str) -> dict:
        run.update(result="skipped", why=code, note=note)
        return run
    if "stop" in tags:
        return skip("stop", "they said stop")
    try:
        if person and _SS().stopped(a, person):
            return skip("stop", "they are on the STOP list")
    except Exception:  # noqa: BLE001
        pass
    force, force_why = _force(cfg, a, rule, person)
    sim = replay or force or SW.mode(a, "automations") == "practice" or _warming(cfg, a, now)
    ac = rule.get("action") or {}
    # what the comment itself asked: never the rule's own intents standing in for a comment that matched a word only
    own = [t for t in dict.fromkeys(list(m["intents"]) + list(tags)) if t in INTENTS and t != "question"]
    intent = (own or [""])[0]
    url_intent = next((i for i in own if i in URL_INTENTS), "")
    part2 = bool((ac.get("part2") or {}).get("on")) and on == "comment" and (
        "asks_part2" in m["intents"] or ("asks_part2" in tags and "asks_part2" in
                                         ((rule.get("trigger") or {}).get("intents") or [])))
    plan: List[Tuple[str, List[str]]] = []
    if on == "message":
        if (ac.get("private") or {}).get("on"):
            plan.append(("dm", (ac.get("private") or {}).get("variants") or []))
    else:
        if (ac.get("private") or {}).get("on") and not part2:
            plan.append(("reply_private", (ac.get("private") or {}).get("variants") or []))
        if (ac.get("public") or {}).get("on"):
            plan.append(("reply_public", (ac.get("public") or {}).get("variants") or []))
    if part2:
        sk = AN.post_source_key(cfg, a, item.get("post") or {})
        if not sk:            # nobody can wait for it: a reply saying "we will tell you" would not be true
            return skip("no_fact", "no source for this clip: nobody can wait for its part 2")
        run["part2"] = True
        run["source"] = sk
    if not plan and not run["part2"]:
        return skip("no_action", "nothing to send for this comment")
    cid = _cid(item)
    pends: List[dict] = []
    missing_fact = False
    for kind, variants in plan:
        if kind in PRIVATE_KINDS:
            if item.get("can_reply_privately") is False:
                pends.append({"kind": kind, "ok": False, "code": "no_private",
                              "why": "Meta allows no private reply to this comment"})
                continue
            pu = _when(item.get("private_until"))
            if kind == "reply_private" and pu is not None and pu <= now:
                pends.append({"kind": kind, "ok": False, "code": "window",
                              "why": "the time for a private reply has passed"})
                continue
            if _private_recent(cfg, a, person, now, hist, sim, replay, cache):
                pends.append({"kind": kind, "ok": False, "code": "person_7d",
                              "why": f"they had a private reply or message in the last {PRIVATE_PERSON_DAYS} days "
                                     f"(one per person in {PRIVATE_PERSON_DAYS} days, by hand or automated)"})
                continue
        idx, text, why = _pick(rule, kind, variants, fx, hist, maker, bool(url_intent))
        if text is None:
            if why == "no_fact":
                missing_fact = True
            else:
                pends.append({"kind": kind, "ok": False, "code": "text", "why": why})
            continue
        if kind in PRIVATE_KINDS and _needs_disclosure(cfg, a, person, now, hist, sim, replay, cache):
            text = DISCLOSURE + text
        if kind == "dm":
            tid = str(item.get("thread_id") or "").partition(":")[2] or str(item.get("thread_id") or "")
            key = f"dm:{platform}:{tid}:{cid}"
            target = {"thread_id": str(item.get("thread_id") or ""), "to": person, "to_id": author.get("id"),
                      "intent": url_intent or intent, "item": item.get("id"), "last_in_at": item.get("at"),
                      "at": item.get("at")}
        else:
            key = f"{'pr' if kind == 'reply_private' else 'rp'}:{platform}:{cid}"
            post = item.get("post") if isinstance(item.get("post"), dict) else {}
            target = {"comment_id": cid, "post_id": str(post.get("platform_id") or ""), "to": person,
                      "to_id": author.get("id"), "intent": (url_intent or intent) if kind != "reply_public" else intent,
                      "item": item.get("id"), "at": item.get("at"), "outlet": str(post.get("outlet") or "")}
        pends.append({"kind": kind, "key": key, "text": text, "variant": idx, "target": target})
    if missing_fact and not any(p.get("text") for p in pends) and not run["part2"]:
        if not any(p.get("code") for p in pends):
            return skip("no_fact", _gap_words(fx))
    chk = check or (lambda r_, p_, k_, t_, tg_, n_, f_: _outward_check(cfg, a, r_, p_, k_, t_, tg_, n_, f_))
    for p in pends:
        if "target" not in p:
            continue
        v = chk(rule, platform, p["kind"], p["text"], p["target"], now, force)
        p.update(ok=bool(v.ok), mode=v.mode or ("practice" if force else "live"), code=v.code,
                 why="; ".join(w for w in [v.why, force_why if force else ""] if w),
                 would_at=_iso(v.not_before) if v.not_before else _iso(now))
    oks = [p for p in pends if p.get("ok")]
    practice = force or any(p.get("mode") == "practice" for p in oks)
    results = ("queued", "practice") if practice else ("queued",)
    li = rule.get("limits") or {}
    limit = ""
    if (oks or run["part2"]) and person:
        if hist.to_person(person, results, now - timedelta(hours=CONTACT_HOURS)) or (not practice and _recent_auto(
                a, person, now)):
            limit = ("contact_24h", "one automated message per person a day, across every rule")
        elif hist.to_person(person, results, now - timedelta(days=_int(li.get("person_days"), 7)), rid=rule["id"]):
            limit = ("person_days", f"this rule answered them in the last {li.get('person_days')} days")
    if (oks or run["part2"]) and not limit:
        if hist.count(rule["id"], results, now - timedelta(hours=1)) >= _int(li.get("per_hour"), 6):
            limit = ("rule_hour", f"the rule's {li.get('per_hour')} an hour are used")
        elif hist.count(rule["id"], results, now - timedelta(days=1)) >= _int(li.get("per_day"), 30):
            limit = ("rule_day", f"the rule's {li.get('per_day')} a day are used")
    if limit:
        for p in oks:
            p.update(ok=False, code=limit[0], why=limit[1])
        oks = []
        if run["part2"]:
            run["part2"] = False
            run["part2_note"] = "not on the waitlist: " + limit[1]
    run["actions"] = [{k: p.get(k) for k in ("kind", "key", "variant", "mode", "would_at", "why", "code", "ok")}
                      for p in pends]
    run["text"] = {p["kind"]: p["text"] for p in pends if p.get("text")}
    run["_pending"] = pends
    if oks:
        run["result"] = "practice" if all(p.get("mode") == "practice" for p in oks) else "queued"
        run["why"] = "; ".join(sorted({p["why"] for p in oks if p.get("why")}))
        held = [p for p in oks if _when(p.get("would_at")) and _when(p["would_at"]) > now]
        run["note"] = ("held: " + "; ".join(p["why"] for p in held)) if held else ""
        run["mode"] = "practice" if run["result"] == "practice" else "live"
        if missing_fact:
            run["note"] = (run["note"] + "; " if run["note"] else "") + "a text skipped for a missing fact"
    elif run["part2"]:
        run.update(result="practice" if practice else "queued", why="part 2: waiting for the next clip",
                   note="they wait for the next clip from this source", mode="practice" if practice else "live")
    else:
        first = next((p for p in pends if p.get("code")), None)
        if limit and first is None:
            first = {"code": limit[0], "why": limit[1]}
        run.update(result="skipped", why=(first or {}).get("code") or ("no_fact" if missing_fact else "held"),
                   note=(first or {}).get("why") or (_gap_words(fx) if missing_fact else ""))
    return run


def _gap_words(fx: Dict[str, str]) -> str:
    try:
        gaps = json.loads(fx.get("_why") or "{}")
    except ValueError:
        gaps = {}
    if not fx.get("mention"):
        gaps = {"mention": "the person has no name to answer", **gaps}
    return "a fact is missing: " + ("; ".join(f"{k}: {v}" for k, v in list(gaps.items())[:3]) or "see the clip")


def _recent_auto(a, person: str, now: datetime) -> bool:
    try:
        t = _when(_SS().contact(a, person).get("last_auto_out_at"))
    except Exception:  # noqa: BLE001
        return False
    return t is not None and now - t < timedelta(hours=CONTACT_HOURS)


# ------------------------------------------------------------------------------------------------------ writing runs
def _row(run: dict) -> dict:
    return {k: v for k, v in run.items() if not k.startswith("_")}


def _item_note(a, iid: str, **fields) -> None:
    """Our own fields on an Inbox item (auto, suggested_reply): the poll keeps them. An item pruned or forgotten
    meanwhile is simply not noted."""
    try:
        _SS().set_local(a, iid, **fields)
    except SystemExit:
        pass


def _queue(cfg: C.Config, a, rule: dict, run: dict, p: dict, force: bool, now: datetime) -> None:
    f = REG.fn("outward:queue")
    if f is None:
        p.update(ok=False, code="outward", why="the sender (clipfactory/outward.py) is not built")
        return
    try:
        p["id"] = f(cfg, a, run["platform"], p["kind"], key=p["key"], text=p["text"], target=p["target"],
                    origin={"by": "rule", "rule": rule.get("id"), "run": run.get("id")}, signed=_signed_for(rule),
                    not_before=_when(p.get("would_at")), force_practice=force, now=now)
    except SystemExit as e:
        p.update(ok=False, code="already", why=str(e)[:200])


def _apply(cfg: C.Config, a, rule: dict, run: dict, now: datetime) -> dict:
    """Queue what the run decided, put a Part 2 asker on the waitlist, write the run and the item's note."""
    force = _force(cfg, a, rule, run.get("to") or "")[0]
    pends = run.pop("_pending", [])
    for p in pends:
        if p.get("ok"):
            _queue(cfg, a, rule, run, p, force, now)
    if run.get("result") in ("queued", "practice"):
        oks = [p for p in pends if p.get("ok")]
        if not oks and not run.get("part2"):
            first = next((p for p in pends if p.get("code")), {})
            run.update(result="skipped", why=first.get("code") or "already", note=first.get("why") or "")
        elif oks:
            run["result"] = "practice" if all(p.get("mode") == "practice" for p in oks) else "queued"
    run["actions"] = [{k: p.get(k) for k in ("kind", "key", "variant", "mode", "would_at", "why", "code", "ok", "id")}
                      for p in pends]
    if run.get("part2") and run.get("result") != "skipped" and run.get("source"):
        try:
            item = _SS().get_item(a, run["item"]) or {}
            at = _when(item.get("at")) or now
            _SS().waitlist_add(a, run["source"], run["item"], run.get("to") or "", now,
                               expires=at + timedelta(days=PART2_DAYS))
        except (Exception, SystemExit) as e:  # noqa: BLE001
            run["part2_note"] = f"could not add to the waitlist ({type(e).__name__})"
    row = _row(run)
    _SS().append_run(a, row, now)
    try:
        _item_note(a, run["item"], auto={"rule": run.get("rule"), "run": run.get("id"), "result": run.get("result"),
                                         "why": run.get("why")})
    except (Exception, SystemExit) as e:  # noqa: BLE001
        REG._log_error("rules:item_note", e)
    return row


def _hist(a, now: datetime) -> _Hist:
    return _Hist(_SS().runs(a, since=now - timedelta(days=31)))


def on_items(cfg: C.Config, acct, items, now: Optional[datetime] = None) -> List[str]:
    """After each Inbox poll (registry.AFTER_POLL): the rules fire on the new top-level comments and messages. Off
    (the account's automations switch) does nothing at all. What happened, one line, or []."""
    if _is_client(acct):
        return []
    a = _account(cfg, acct)
    if SW.mode(a, "automations") == SW.OFF:
        return []
    rl = rules(a)
    if not rl:
        return []
    now = _now(now)
    lines = self_pause(cfg, a, now)
    try:
        lines += part2_sweep(cfg, a, now)
    except (Exception, SystemExit) as e:  # noqa: BLE001  (a sweep never stops the comments' own run)
        REG._log_error("rules:part2_sweep", e)
    rl = rules(a)
    hist = _hist(a, now)
    cache: dict = {}
    n: Dict[str, int] = {}
    for it in sorted([x for x in items or [] if isinstance(x, dict)], key=lambda x: str(x.get("at") or "")):
        iid = str(it.get("id") or "")
        if not iid:
            continue
        cur = _SS().get_item(a, iid) or it
        if cur.get("auto") or hist.has_item(iid):
            continue                                  # one run per comment
        run = decide(cfg, a, cur, rl, now, hist, cache)
        if run is None:
            continue
        rule = next((r for r in rl if r.get("id") == run.get("rule")), None)
        if run.get("why") == "youtube":
            run.pop("_pending", None)
            if run.get("suggested"):
                try:
                    _item_note(a, iid, suggested_reply={
                        "text": run["suggested"], "rule": run.get("rule"), "at": _iso(now),
                        "why": f"from the automation \"{(rule or {}).get('name') or run.get('rule')}\" (YouTube "
                               f"replies are only ever your click)"})
                except (Exception, SystemExit) as e:  # noqa: BLE001
                    REG._log_error("rules:item_note", e)
            row = _row(run)
            _SS().append_run(a, row, now)
            hist.add(row)
            n["suggested"] = n.get("suggested", 0) + 1
            continue
        if rule is None:
            continue
        row = _apply(cfg, a, rule, run, now)
        hist.add(row)
        n[row["result"]] = n.get(row["result"], 0) + 1
    if n:
        lines.append(f"{a.name} automations: " + ", ".join(f"{v} {k}" for k, v in sorted(n.items())))
    return lines


# ------------------------------------------------------------------------------------------------------ Part 2
def on_posted(cfg: C.Config, acct, day, slot: dict, outlet: str, rec: dict,
              now: Optional[datetime] = None) -> Optional[str]:
    """A clip went live: the people who asked for a Part 2 of its source within 6 days get ONE note on the same
    platform: a private reply with the link when messages are allowed (and they had no private reply in 7 days), else
    a public reply, in one of the factory's rotating Part 2 variants. Never twice: each waiter is claimed under the
    waitlist's lock BEFORE anything is queued (sent = "sending:<key>", then the action id), so a second process
    reading the same waiter, or a run killed between the queue and the mark, never queues a second note of the other
    kind (the private and the public note have different politics keys). A claim whose note was not queued is given
    back. Never past the limits either: the rule's own hour and day, and one automated message per person a day
    across every rule (the rule's own answer to the very comment that asked does not count: the note is its follow
    up). Whoever those leave out stays on the waitlist for the next poll's sweep (part2_sweep) or the next clip.
    Only queues."""
    if _is_client(acct):
        return None
    a = _account(cfg, acct)
    if SW.mode(a, "automations") == SW.OFF or not isinstance(slot, dict):
        return None
    now = _now(now)
    from . import outward as OW
    plat = OW.plat_of(outlet)
    if plat not in ("instagram", "facebook"):
        return None
    sk = AN.source_key(cfg, a, slot, str(day))
    if not sk:
        return None
    url = str((rec or {}).get("url") or (rec or {}).get("permalink") or "")
    waiting = _SS().waitlist(a, sk, now)
    if not waiting:
        _part2_pending(a, sk, outlet, None)
        return None
    rl = {r["id"]: r for r in rules(a)}
    hist = _hist(a, now)
    cache: dict = {}
    said = []
    left = 0
    maker = str(getattr(a, "maker", "") or "")
    sim_acct = SW.mode(a, "automations") == "practice" or _warming(cfg, a, now)
    for w in waiting:
        item = _SS().get_item(a, str(w.get("item") or ""))
        if not item or item.get("platform") != plat:
            continue
        post = item.get("post") or {}
        if str(post.get("board") or "") == str(day) and str(post.get("slot") or "") == str(slot.get("slot") or ""):
            continue                                       # the clip they asked under
        asked = _when(w.get("at")) or now
        if now - asked > timedelta(days=PART2_DAYS):
            _SS().waitlist_mark(a, sk, item["id"], "expired: more than 6 days")
            continue
        rule = rl.get(str((item.get("auto") or {}).get("rule") or ""))
        if rule is None or rule.get("mode") == "paused" or not ((rule.get("action") or {}).get("part2") or {}).get("on"):
            continue
        person = str((item.get("author") or {}).get("key") or "")
        if person and _SS().stopped(a, person):
            _SS().waitlist_mark(a, sk, item["id"], "skipped: they said stop")
            continue
        force, force_why = _force(cfg, a, rule, person)
        sim = force or sim_acct
        results = ("queued", "practice") if sim else ("queued",)
        li = rule.get("limits") or {}
        if hist.count(rule["id"], results, now - timedelta(hours=1)) >= _int(li.get("per_hour"), 6) or \
                hist.count(rule["id"], results, now - timedelta(days=1)) >= _int(li.get("per_day"), 30):
            left += 1                                      # the rule's own limits: they wait for the sweep
            continue
        if person and _contacted_today(cfg, a, person, item["id"], now, hist, results, sim):
            left += 1                                      # one automated message a day: they wait for the sweep
            continue
        fx = AN.item_facts(cfg, a, item, now)
        fx["part2_url"] = url
        messages_ok = (SW.settings(a).get("messages_access") == "advanced" or SW.is_tester(a, person)) and \
            item.get("can_reply_privately") is not False and url and \
            not _private_recent(cfg, a, person, now, hist, sim, False, cache)
        cid = _cid(item)
        base = {"comment_id": cid, "post_id": str(post.get("platform_id") or ""), "to": person,
                "to_id": (item.get("author") or {}).get("id"), "item": item["id"], "at": item.get("at"),
                "outlet": str(post.get("outlet") or "")}
        if messages_ok:
            kind, key = "reply_private", f"pr:{plat}:{cid}"     # also the platform's one private reply per comment
            variant, text, bad = _pick(rule, kind, PART2_PRIVATE, fx, hist, maker, url_ok=True, part2=True)
            if text and _needs_disclosure(cfg, a, person, now, hist, sim, False, cache):
                text = DISCLOSURE + text
        else:
            kind, key = "reply_public", f"rp:{plat}:{cid}:part2"
            variant, text, bad = _pick(rule, kind, PART2_PUBLIC, fx, hist, maker, part2=True)
        target = {**base, "intent": "part2"}
        run = {"id": "ru-" + now.strftime("%Y%m%dT%H%M%S") + "-" + secrets.token_hex(2), "at": _iso(now),
               "item": item["id"], "platform": plat, "on": "part2", "to": person,
               "name": str((item.get("author") or {}).get("name") or ""), "rule": rule["id"],
               "version": rule.get("version"), "source": sk, "part2_of": f"{day} {slot.get('slot')}"}
        if not text:
            run.update(result="skipped", why="no_fact" if bad in ("", "no_fact") else "text",
                       note="no link or name for the Part 2 note" if bad in ("", "no_fact") else bad, actions=[],
                       text={})
            _SS().append_run(a, run, now)
            hist.add(run)
            continue
        claim = f"sending:{key}"
        if not _waitlist_claim(a, sk, item["id"], claim):
            continue                                       # another run took this waiter (or noted it) meanwhile
        try:
            v = _outward_check(cfg, a, rule, plat, kind, text, target, now, force)
        except BaseException:
            _waitlist_claim(a, sk, item["id"], claim, release=True)    # nothing queued: they still wait
            raise
        p = {"kind": kind, "key": key, "text": text, "variant": variant, "target": target, "ok": v.ok,
             "mode": v.mode or ("practice" if force else "live"), "code": v.code,
             "why": "; ".join(x for x in [v.why, force_why if force else ""] if x),
             "would_at": _iso(v.not_before) if v.not_before else _iso(now)}
        if p["ok"]:
            _queue(cfg, a, rule, run, p, force, now)    # from here the claim stays, even if this dies half way
        run["actions"] = [{k: p.get(k) for k in ("kind", "key", "variant", "mode", "would_at", "why", "code", "ok",
                                                  "id")}]
        run["text"] = {kind: text}
        if p.get("ok"):
            run.update(result="practice" if p["mode"] == "practice" else "queued", why=p.get("why") or "part 2",
                       note=f"part 2 is up: {url}")
            said.append(item["id"])
            try:
                _SS().waitlist_mark(a, sk, item["id"], ("practice:" if p["mode"] == "practice" else "") +
                                    str(p.get("id")))
            except (Exception, SystemExit) as e:  # noqa: BLE001  (the claim already keeps it from a second note)
                REG._log_error("rules:part2_mark", e)
        else:
            run.update(result="skipped", why=p.get("code") or "held", note=p.get("why") or "")
            if p.get("code") in ("already", "window", "stop"):
                _SS().waitlist_mark(a, sk, item["id"], f"skipped: {p.get('why')}"[:200])
            else:
                _waitlist_claim(a, sk, item["id"], claim, release=True)
        _SS().append_run(a, run, now)
        hist.add(run)
    _part2_pending(a, sk, outlet, {"day": str(day), "slot": str(slot.get("slot") or ""), "url": url,
                                   "post_id": str((rec or {}).get("post_id") or ""), "at": _iso(now)}
                   if left else None)
    words = []
    if said:
        words.append(f"part 2 noted for {len(said)} people")
    if left:
        words.append(f"{left} more wait for the rule's hour or day limit, or their one message a day")
    return f"{a.name}: " + ", ".join(words) if words else None


def _waitlist_claim(a, sk: str, iid: str, claim: str, release: bool = False) -> bool:
    """Take a waiting comment for one Part 2 note before anything is queued, under the waitlist's lock: False when
    it is no longer waiting (another run claimed or noted it). release=True gives back our own claim (only while it
    is still ours) when the note was not queued."""
    with _SS().edit(a, "waitlist") as d:
        sec = d.get("waitlist") if isinstance(d.get("waitlist"), dict) else {}
        for w in sec.get(str(sk)) or []:
            if not isinstance(w, dict) or w.get("item") != iid:
                continue
            if release:
                if w.get("sent") != claim:
                    return False
                w["sent"] = None
                return True
            if w.get("sent"):
                return False
            w["sent"] = claim
            return True
    return False


def _contacted_today(cfg: C.Config, a, person: str, iid: str, now: datetime, hist: _Hist, results: Sequence[str],
                     sim: bool) -> bool:
    """One automated message per person a day, across every rule, for a Part 2 note: a run to them in the last 24
    hours (or, live, an automated message the sender sent them), except the rule's answer to this very comment."""
    since = now - timedelta(hours=CONTACT_HOURS)
    if any(r.get("item") != iid for r in hist._since(since, results, person=person)):
        return True
    if sim:
        return False
    f = REG.fn("outward:rows")
    if f is None:
        return False
    try:
        for row in f(cfg, a.slug, since=since, kinds=("reply_public",) + PRIVATE_KINDS, states=("done",)):
            tg = row.get("target") if isinstance(row.get("target"), dict) else {}
            if row.get("mode") == "practice" or (row.get("origin") or {}).get("by") != "rule":
                continue
            t = _when(row.get("t"))
            if str(tg.get("to") or "") == person and str(tg.get("item") or "") != iid and t is not None and \
                    since <= t <= now:
                return True
    except Exception as e:  # noqa: BLE001
        REG._log_error("rules:part2_contact", e)
    return False


def _part2_pending(a, sk: str, outlet: str, ent: Optional[dict]) -> None:
    """Remember (or forget) a posted Part 2 whose waiters the rule's limits left out, for part2_sweep."""
    key = f"{sk}|{outlet}"
    try:
        have = (_file(a).get("part2_pending") or {})
        if ent is None and key not in have:
            return
        with _SS().edit(a, "rules") as d:
            pend = d.get("part2_pending") if isinstance(d.get("part2_pending"), dict) else {}
            if ent is None:
                pend.pop(key, None)
            else:
                pend[key] = {**ent, "source": sk, "outlet": outlet}
            d["part2_pending"] = pend
    except (Exception, SystemExit) as e:  # noqa: BLE001
        REG._log_error("rules:part2_pending", e)


def part2_sweep(cfg: C.Config, acct, now: Optional[datetime] = None) -> List[str]:
    """Each poll: a Part 2 that went up while the rule's hour or day limit was used gets its notes out now, within
    the same limits and the same 6 days."""
    from . import board as B
    a = _account(cfg, acct)
    now = _now(now)
    pend = _file(a).get("part2_pending")
    if not isinstance(pend, dict) or not pend:
        return []
    out = []
    for key, ent in list(pend.items()):
        if not isinstance(ent, dict):
            continue
        sk, outlet = str(ent.get("source") or ""), str(ent.get("outlet") or "")
        at = _when(ent.get("at"))
        slot = None
        try:
            bd = B.read(a, date.fromisoformat(str(ent.get("day"))[:10])) or {}
            slot = (bd.get("slots") or {}).get(str(ent.get("slot") or ""))
        except (OSError, ValueError):
            slot = None
        if at is None or now - at > timedelta(days=PART2_DAYS) or not isinstance(slot, dict):
            _part2_pending(a, sk, outlet, None)
            continue
        slot = dict(slot, slot=slot.get("slot") or ent.get("slot"))
        got = on_posted(cfg, a, ent.get("day"), slot, outlet, {"url": ent.get("url"), "post_id": ent.get("post_id")},
                        now)
        if got:
            out.append(got)
    return out


# ------------------------------------------------------------------------------------------------------ the sender
def still_signed(cfg: C.Config, acct, action: dict) -> str:
    """The sender's last look before a rule's action goes live: the rule is there, signed, its words are the ones
    that were queued, it is still in Testers (and they are a tester) or Live, it was not put back in practice or
    paused itself since, and nobody answered the comment by hand meanwhile. '' when it may go; the words why not
    otherwise. Practice needs no signature."""
    origin = action.get("origin") if isinstance(action.get("origin"), dict) else {}
    if origin.get("by") != "rule":
        return ""
    if action.get("force_practice"):
        return ""
    a = _account(cfg, acct)
    rule = get(a, str(origin.get("rule") or ""))
    if rule is None:
        return "its automation was deleted"
    name = rule.get("name") or rule.get("id")
    mode = rule.get("mode") or "practice"
    if mode == "paused":
        return f"{name} is paused"
    if mode not in ("testers", "live"):
        return f"{name} is in {MODE_WORDS.get(mode, mode).lower()} now: it sends nothing"
    if not is_signed(rule):
        return f"{name} changed after it was signed: sign it again"
    want = str((action.get("signed") or {}).get("hash") or "")
    if want and want != rule_hash(rule):
        return f"{name} changed after this reply was queued"
    target = action.get("target") if isinstance(action.get("target"), dict) else {}
    person = str(target.get("to") or "")
    if mode == "testers" and not (person and SW.is_tester(a, person)):
        return f"{name} answers only the app's testers now"
    queued = _when(action.get("at"))
    if queued is not None:
        reach = _when(rule.get("reach_since"))
        if reach is not None and queued < reach:
            return f"{name} was queued before it was last switched on: it does not go"
        for f in rule.get("flags") or []:
            t = _when(f.get("at")) if isinstance(f, dict) and f.get("key") == "self_pause" else None
            if t is not None and t > queued:
                return f"{name} went back to practice by itself after this was queued"
    iid = str(target.get("item") or "")
    if iid and action.get("kind") != "dm":
        try:
            it = _SS().get_item(a, iid)
        except Exception:  # noqa: BLE001
            it = None
        if isinstance(it, dict):
            if it.get("hidden") or it.get("state") in ("hidden", "gone"):
                return "the comment is hidden or gone"
            if target.get("intent") != "part2":
                if it.get("state") == "done":
                    return "the comment was marked done in the Inbox"
                if it.get("state") == "answered" and it.get("state_by") not in ("rule", None, ""):
                    return "the comment was answered by hand meanwhile"
                for rp in it.get("replies") or []:
                    if isinstance(rp, dict) and rp.get("by") != "rule" and rp.get("action") != action.get("id") and \
                            rp.get("state") not in ("failed", "refused", "skipped", "gave_up", "practice"):
                        return "the comment was answered by hand meanwhile"
    return ""


# ------------------------------------------------------------------------------------------------------ self-pause
def self_pause(cfg: C.Config, acct, now: Optional[datetime] = None) -> List[str]:
    """A rule that reaches people pauses itself back to practice (and says so) when, within 7 days of its messages,
    two people answer them with STOP, "bot" or "spam", or one of its replies is hidden."""
    a = _account(cfg, acct)
    now = _now(now)
    since = now - timedelta(days=SELF_PAUSE_DAYS)
    active = {r["id"]: r for r in rules(a) if r.get("mode") in ("testers", "live")}
    if not active:
        return []
    SS = _SS()
    sent = [r for r in SS.runs(a, since=since) if r.get("result") == "queued" and r.get("rule") in active]
    if not sent:
        return []
    recent = SS.items(a, since=since)
    by_rule: Dict[str, Dict[str, datetime]] = {}
    for r in sent:
        t = _when(r.get("at"))
        if t is not None and r.get("to"):
            first = by_rule.setdefault(r["rule"], {})
            first[r["to"]] = min(first.get(r["to"], t), t)
    hidden_ids: Dict[str, set] = {}
    f = REG.fn("outward:rows")
    if f is not None:
        try:
            for row in f(cfg, a.slug, since=since, kinds=("reply_public",), states=("done",)):
                rid = str((row.get("origin") or {}).get("rule") or "")
                rid_res = str((row.get("result") or {}).get("id") or "")
                if rid in active and rid_res:
                    hidden_ids.setdefault(rid, set()).add(rid_res)
        except Exception as e:  # noqa: BLE001
            REG._log_error("rules:self_pause", e)
    out = []
    for rid, rule in active.items():
        people = by_rule.get(rid, {})
        hits = set()
        for person, t in people.items():
            c = SS.contact(a, person)
            st = _when(c.get("stop_at"))
            if c.get("stop") and st is not None and st >= t:
                hits.add(person)
                continue
            for it in recent:
                au = it.get("author") if isinstance(it.get("author"), dict) else {}
                w = it.get("with") if isinstance(it.get("with"), dict) else {}
                if person not in (au.get("key"), w.get("key")):
                    continue
                ti = _when(it.get("at"))
                if ti is not None and ti > t and PUSHBACK.search(str(it.get("text") or "")):
                    hits.add(person)
                    break
        hid = 0
        ids = hidden_ids.get(rid, set())
        if ids:
            for it in recent:
                if str(it.get("platform_comment_id") or "") in ids and it.get("hidden"):
                    hid += 1
                for rp in it.get("replies") or []:
                    if isinstance(rp, dict) and str(rp.get("id") or "") in ids and rp.get("hidden"):
                        hid += 1
        why = ""
        if len(hits) >= SELF_PAUSE_N:
            why = f"{len(hits)} people answered its messages with stop, bot or spam in {SELF_PAUSE_DAYS} days"
        elif hid:
            why = "one of its replies was hidden"
        if not why:
            continue
        with SS.edit(a, "rules") as d:
            rs = d.get("rules") if isinstance(d.get("rules"), dict) else {}
            cur = rs.get(rid)
            if not isinstance(cur, dict) or cur.get("mode") not in ("testers", "live"):
                continue
            old = cur["mode"]
            cur["mode"] = "practice"
            cur["practice_since"] = _iso(now)
            flags = [x for x in cur.get("flags") or [] if isinstance(x, dict) and x.get("key") != "self_pause"]
            flags.append({"key": "self_pause", "at": _iso(now), "why": why})
            cur["flags"] = flags
            hist = cur.get("history") if isinstance(cur.get("history"), list) else []
            hist.append({"at": _iso(now), "by": "self-pause", "from": old, "to": "practice", "why": why})
            cur["history"] = hist[-50:]
            rs[rid] = cur
            d["rules"] = rs
        out.append(f"{a.name}: {rule.get('name')} went back to practice by itself: {why}")
    return out


# ------------------------------------------------------------------------------------------------------ the numbers
def analytics(cfg: C.Config, acct, now: Optional[datetime] = None) -> Dict[str, dict]:
    """Per rule, for the last 24 hours and all the time kept: triggered, sent, would send (practice), skipped by
    reason, failed, and the last run."""
    a = _account(cfg, acct)
    now = _now(now)
    day = now - timedelta(days=1)
    blank = lambda: {"triggered": 0, "sent": 0, "practice": 0, "queued": 0, "skipped": 0, "failed": 0,  # noqa: E731
                     "why": {}}
    out: Dict[str, dict] = {}
    for r in _SS().runs(a):
        rid = str(r.get("rule") or "")
        if not rid:
            continue
        o = out.setdefault(rid, {"h24": blank(), "all": blank(), "last_run": None})
        t = _when(r.get("at"))
        for span in (["h24", "all"] if t is not None and t >= day else ["all"]):
            s = o[span]
            s["triggered"] += 1
            res = r.get("result")
            if res in ("practice", "queued", "skipped"):
                s[res] += 1
            if res == "skipped":
                w = str(r.get("why") or "other")
                s["why"][w] = s["why"].get(w, 0) + 1
        if t is not None and (o["last_run"] is None or str(r.get("at")) > o["last_run"]):
            o["last_run"] = str(r.get("at"))
    f = REG.fn("outward:rows")
    if f is not None:
        try:
            failed: Dict[Tuple[str, str], set] = {}
            for row in f(cfg, a.slug, kinds=("reply_public", "reply_private", "dm"),
                         states=("done", "failed", "gave_up")):
                rid = str((row.get("origin") or {}).get("rule") or "")
                if not rid or row.get("mode") == "practice":
                    continue
                o = out.setdefault(rid, {"h24": blank(), "all": blank(), "last_run": None})
                t = _when(row.get("t"))
                for span in (["h24", "all"] if t is not None and t >= day else ["all"]):
                    if row.get("state") == "done":
                        o[span]["sent"] += 1
                    else:                         # a try that failed, or gave up: one per action
                        failed.setdefault((rid, span), set()).add(str(row.get("id") or row.get("key") or ""))
            for (rid, span), ids in failed.items():
                out[rid][span]["failed"] = len(ids)
        except Exception as e:  # noqa: BLE001
            REG._log_error("rules:analytics", e)
    return out


# ------------------------------------------------------------------------------------------------------ try it, replay
def recent_posts(cfg: C.Config, acct, days: int = 14, now: Optional[datetime] = None) -> List[dict]:
    """The account's posts of the last days that a comment could be on (newest first): what "Try it" can use."""
    from . import board as B
    a = _account(cfg, acct)
    now = _now(now)
    today = now.astimezone(cfg.tz()).date()
    out = []
    for i in range(days):
        d = today - timedelta(days=i)
        try:
            bd = B.read(a, d) or {}
        except (OSError, ValueError):
            continue
        for k, s in sorted((bd.get("slots") or {}).items(), reverse=True):
            if not isinstance(s, dict):
                continue
            for outlet, o in (s.get("outlets") or {}).items():
                pid = (o or {}).get("post_id") or (o or {}).get("platform_id") or (o or {}).get("video_id")
                plat = {"facebook_reels": "facebook", "instagram_reels": "instagram",
                        "youtube_shorts": "youtube"}.get(outlet)
                if not pid or not plat:
                    continue
                out.append({"platform_id": str(pid), "outlet": outlet, "platform": plat, "board": d.isoformat(),
                            "slot": k, "url": str((o or {}).get("url") or ""),
                            "title": f"{d.strftime('%b %d')} {k} {s.get('slug') or ''}".strip()})
    return out


def _fake_item(text: str, platform: str, post: Optional[dict], now: datetime, name: str = "someone") -> dict:
    return {"id": "try:1", "platform": platform, "kind": "comment", "platform_comment_id": "try",
            "author": {"key": f"{platform}:try", "id": "try", "name": name or "someone", "is_us": False},
            "text": str(text or ""), "at": _iso(now), "post": dict(post or {}), "can_reply_privately": True,
            "state": "new"}


def _public_run(run: dict, comment: str = "") -> dict:
    out = _row(run)
    out.pop("candidates", None)
    if comment:
        out["comment"] = comment[:300]
    return out


def try_text(cfg: C.Config, acct, text: str, platform: str = "instagram", post_id: str = "",
             rule: Optional[dict] = None, now: Optional[datetime] = None, name: str = "someone") -> dict:
    """Try it: a comment typed on the page, the rule it would match, the exact words, and what the caps, windows or
    holds would do to it right now. Pure: nothing written, nothing sent, no network."""
    a = _account(cfg, acct)
    now = _now(now)
    platform = platform if platform in PLATFORMS else "instagram"
    posts = [p for p in recent_posts(cfg, a, now=now) if p["platform"] == platform]
    post = next((p for p in posts if p["platform_id"] == str(post_id or "")), None) or (posts[0] if posts else {})
    item = _fake_item(text, platform, {k: post.get(k) for k in ("platform_id", "outlet", "board", "slot", "url",
                                                              "title")} if post else {}, now, name)
    maker = str(getattr(a, "maker", "") or "")
    item["tags"] = tags_for(text, maker)
    draft = None
    if rule is not None:
        draft = {**clean(rule), "id": str(rule.get("id") or "draft"), "mode": "practice", "version": 0}
        rl = [draft]
    else:
        rl = rules(a)
    fx = AN.item_facts(cfg, a, item, now)
    hist = _hist(a, now)
    run = decide(cfg, a, item, rl, now, _Hist(hist.rows), {}, None)
    cands = []
    for r in rl:
        m = match(r, item, item["tags"], now) if platform in (r.get("platforms") or []) else None
        cands.append({"rule": r.get("id"), "name": r.get("name"), "mode": r.get("mode"),
                      "platform": platform in (r.get("platforms") or []), "matched": m,
                      "excluded": keyword_hits(text, (r.get("trigger") or {}).get("exclude") or [])})
    facts_shown = {k: v for k, v in fx.items() if not k.startswith("_")}
    try:
        gaps = json.loads(fx.get("_why") or "{}")
    except ValueError:
        gaps = {}
    return {"account": a.slug, "text": text, "platform": platform, "tags": item["tags"], "post": post,
            "posts": posts[:20], "facts": facts_shown, "missing": gaps, "candidates": cands,
            "run": _public_run(run) if run else None,
            "problems": problems(a, draft) if draft else [], "warnings": warnings(a, draft) if draft else []}


def replay(cfg: C.Config, acct, days: int = 7, rid: str = "", rule: Optional[dict] = None,
           now: Optional[datetime] = None) -> dict:
    """Test on the last 7 days: every stored comment and message of those days through the rules (or one rule, or
    the editor's unsaved draft), in time order, with the caps, quiet hours, windows and limits as they would have
    been: what would have been sent, to whom, when, and why anything was held or skipped. Pure: nothing written,
    nothing sent, no network."""
    a = _account(cfg, acct)
    now = _now(now)
    days = max(1, min(int(days or 7), 30))
    since = now - timedelta(days=days)
    if rule is not None:
        rl = [{**clean(rule), "id": str(rule.get("id") or "draft"), "mode": "practice", "version": 0}]
    elif rid:
        r = _must(a, rid)
        rl = [{**r, "mode": "practice" if r.get("mode") == "paused" else r.get("mode")}]
    else:
        rl = [r for r in rules(a) if r.get("mode") != "paused"]
    try:
        items = _SS().items(a, since=since, until=now)
    except Exception:  # noqa: BLE001
        items = []
    items = sorted([i for i in items if _is_trigger(i)], key=lambda i: str(i.get("at") or ""))
    sim = _Sim(cfg, a)
    hist = _Hist([])
    cache: dict = {}
    rows: List[dict] = []
    tally = {"comments": len(items), "matched": 0, "would_send": 0, "held": 0, "practice": 0, "skipped": 0,
             "suggested": 0, "why": {}, "people": 0}
    people = set()

    def check(r_, p_, k_, t_, tg_, n_, f_):
        return sim.check(r_, p_, k_, t_, tg_, n_, f_)
    for it in items:
        at = _when(it.get("at")) or now
        run = decide(cfg, a, it, rl, at, hist, cache, check)
        if run is None:
            continue
        tally["matched"] += 1
        pends = run.pop("_pending", [])
        if run.get("why") == "youtube":
            tally["suggested"] += 1
        elif run.get("result") in ("queued", "practice"):
            sent = [p for p in pends if p.get("ok")]
            for p in sent:
                wa = _when(p.get("would_at")) or at
                sim.note(run["platform"], p["kind"], p["text"], wa)
            if sent:
                tally["would_send"] += 1
                people.add(run.get("to"))
                if any((_when(p.get("would_at")) or at) > at for p in sent):
                    tally["held"] += 1
            hist.add(dict(_row(run), result="practice"))      # what the limits count, as practice would
            run["result"] = "would_send"
        else:
            tally["skipped"] += 1
            w = str(run.get("why") or "other")
            tally["why"][w] = tally["why"].get(w, 0) + 1
            hist.add(_row(run))
        rows.append(_public_run(run, str(it.get("text") or "")))
    tally["people"] = len(people)
    names = {r.get("id"): r.get("name") for r in rl}
    for r in rows:
        r["rule_name"] = names.get(r.get("rule"), r.get("rule"))
    return {"account": a.slug, "days": days, "since": _iso(since), "until": _iso(now),
            "rule": rid or (rl[0].get("id") if rule is not None else ""), "tally": tally,
            "rows": list(reversed(rows))[:200]}


# ------------------------------------------------------------------------------------------------------ doctor
def doctor(cfg: C.Config, now=None) -> List[Tuple[Optional[bool], str, str]]:
    """Per account: the automations switch, rules signed, would-sends in 7 days; a NO for a rule changed after it was
    signed, a rule that paused itself, a live rule its account cannot carry; what private replies wait for. Read only,
    never raises."""
    out: List[Tuple[Optional[bool], str, str]] = []
    try:
        now = _now(now if isinstance(now, datetime) else None)
        any_private = False
        for a in _own(cfg):
            try:
                rl = rules(a)
                m = SW.mode(a, "automations")
                if m == SW.OFF and not rl:
                    out.append((None, f"{a.name} automations: off, no rules yet (factory.py rules seed {a.slug}, then "
                                      f"factory.py social on {a.slug} automations --practice)", ""))
                    continue
                signed = [r for r in rl if is_signed(r)]
                runs7 = [r for r in _SS().runs(a, since=now - timedelta(days=7))]
                would = sum(1 for r in runs7 if r.get("result") == "practice")
                sent = sum(1 for r in runs7 if r.get("result") == "queued")
                words = (f"{a.name} automations: {m}, {len(signed)} rule{'s' if len(signed) != 1 else ''} signed, "
                         f"{would} would-send{'s' if would != 1 else ''} in 7 days")
                if sent:
                    words += f", {sent} sent"
                if m == SW.OFF:
                    words += f" (factory.py social on {a.slug} automations --practice)"
                out.append((None, words, ""))
                for r in rl:
                    st = signed_state(r)
                    if st == "changed":
                        out.append((False, f"{a.name}: rule {r['id']} changed after signing: sign it again (Inbox, "
                                           f"Automations)", "Clip Review, Inbox, Automations: read it, then Sign"))
                    if r.get("mode") in ("testers", "live") and st != "signed":
                        out.append((False, f"{a.name}: rule {r['id']} is {r['mode']} without a signature: it only "
                                           f"practises", "Clip Review, Inbox, Automations: sign it"))
                    if r.get("mode") in ("testers", "live") and "youtube" in (r.get("platforms") or []):
                        out.append((False, f"{a.name}: rule {r['id']} lists YouTube: YouTube matches stay "
                                           f"suggestions", "take YouTube off the rule"))
                    want = {"testers": "testers", "live": "on"}.get(r.get("mode") or "")
                    if want and ACCOUNT_RANK.get(m, 0) < ACCOUNT_RANK[want]:
                        out.append((None, f"{a.name}: rule {r['id']} is {r['mode']}, but the account's automations "
                                          f"switch is {m}: it practises", ""))
                    for f in r.get("flags") or []:
                        if isinstance(f, dict) and f.get("key") == "self_pause":
                            out.append((False, f"{a.name}: rule {r['id']} went back to practice by itself on "
                                               f"{str(f.get('at'))[:10]}: {f.get('why')}",
                                        "read the replies in the Inbox, then set it to Testers or Live again"))
                    if (r.get("action") or {}).get("private", {}).get("on") or \
                            ((r.get("action") or {}).get("part2") or {}).get("on"):
                        any_private = any_private or SW.settings(a).get("messages_access") != "advanced"
            except Exception as e:  # noqa: BLE001
                out.append((False, f"{a.name} automations: could not read its rules ({type(e).__name__}: {e})",
                            f"look at state/{a.slug}/engage/rules.json"))
        if any_private:
            out.append((None, "private replies: wait for Meta's review; only app testers get them", ""))
    except Exception as e:  # noqa: BLE001
        out.append((False, f"automations: the check failed ({type(e).__name__}: {e})", "look at clipfactory/rules.py"))
    return out


# ------------------------------------------------------------------------------------------------------ the command
def _parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="factory.py rules", description="DM automations and natural answers "
                                                                       "(clipfactory/rules.py)")
    sub = ap.add_subparsers(dest="action", required=True)
    q = sub.add_parser("list", help="each account's rules: mode, signature, numbers")
    q.add_argument("account", nargs="?")
    q = sub.add_parser("show", help="one rule in full, with its hash")
    q.add_argument("account")
    q.add_argument("rule")
    q = sub.add_parser("seed", help="add the starter drafts (practice, unsigned), once per account")
    q.add_argument("account", nargs="?")
    q.add_argument("--all", action="store_true")
    q.add_argument("--by")
    q = sub.add_parser("try", help="Try it: which rule a comment would match, the exact words, what would hold it")
    q.add_argument("account")
    q.add_argument("comment")
    q.add_argument("--platform", default="instagram", choices=list(PLATFORMS))
    q.add_argument("--post", default="")
    q.add_argument("--name", default="someone")
    q = sub.add_parser("replay", help="Test on the last 7 days: what the rules would have sent, to whom, when")
    q.add_argument("account")
    q.add_argument("--rule", default="")
    q.add_argument("--days", type=int, default=7)
    q = sub.add_parser("runs", help="the latest runs: what each rule did or would have done")
    q.add_argument("account")
    q.add_argument("--rule", default="")
    q.add_argument("--limit", type=int, default=30)
    for name, what in (("pause", "pause a rule"), ("practice", "put a rule back in practice")):
        q = sub.add_parser(name, help=what + " (signing, testers and live are clicks on the review page)")
        q.add_argument("account")
        q.add_argument("rule")
        q.add_argument("--by")
    for name in ("sign", "live", "testers"):
        q = sub.add_parser(name, help="only on the review page (Inbox, Automations)")
        q.add_argument("rest", nargs=argparse.REMAINDER)
    sub.add_parser("doctor", help="the doctor's automation lines alone")
    return ap


def _fmt_run(r: dict) -> str:
    acts = r.get("actions") or []
    texts = r.get("text") if isinstance(r.get("text"), dict) else {}
    head = f"{str(r.get('at'))[:16]} {r.get('rule')} {PLAT_WORDS.get(r.get('platform'), r.get('platform'))} " \
           f"{r.get('name') or r.get('to') or ''}: {r.get('result')}"
    w = SKIP_WORDS.get(str(r.get("why") or ""), r.get("why")) if r.get("result") == "skipped" else r.get("why")
    why = f" ({w}{': ' + r['note'] if r.get('note') else ''})" if w or r.get("note") else ""
    lines = [head + why]
    for x in acts:
        if not isinstance(x, dict):
            continue
        t = texts.get(x.get("kind")) if isinstance(texts, dict) else ""
        lines.append(f"    {KIND_WORDS.get(x.get('kind'), x.get('kind'))}: {t or ''} "
                     f"[{'would go ' + str(x.get('would_at'))[:16] if x.get('ok') else x.get('why') or ''}]")
    return "\n".join(lines)


def cli(cfg: C.Config, argv: List[str]) -> None:
    """factory.py rules list|show|seed|try|replay|runs|pause|practice|doctor (sign, testers and live are page clicks)."""
    a = _parser().parse_args([x for x in argv if x != "--quiet"])
    act = a.action
    if act in ("sign", "live", "testers"):
        raise SystemExit(f"rules {act}: only a click on the review page (Clip Review, Inbox, Automations) can "
                         f"{'sign a rule' if act == 'sign' else 'let a rule reach people'}")
    if act == "doctor":
        for good, text, fix in doctor(cfg):
            print(f"  {'-- ' if good is None else 'ok ' if good else 'NO '} {text}"
                  + (f"\n        fix: {fix}" if fix and good is False else ""))
        return
    if act == "list":
        accts = [_account(cfg, a.account)] if a.account else _own(cfg)
        for acct in accts:
            stats = analytics(cfg, acct)
            rl = rules(acct)
            print(f"{acct.name}: automations {SW.mode(acct, 'automations')}, {len(rl)} rules")
            for r in rl:
                st = stats.get(r["id"]) or {}
                print(f"  {r['id']:<14} {MODE_WORDS.get(r.get('mode'), r.get('mode')):<8} v{r.get('version')} "
                      f"{signed_state(r):<8} {r.get('name')}")
                for label, span in (("24h", "h24"), ("all", "all")):
                    s = st.get(span) or {}
                    why = ", ".join(f"{SKIP_WORDS.get(k, k)} {v}" for k, v in sorted((s.get("why") or {}).items()))
                    print(f"      {label}: {s.get('triggered', 0)} runs, {s.get('sent', 0)} sent, "
                          f"{s.get('practice', 0)} would send, {s.get('skipped', 0)} skipped"
                          f"{' (' + why + ')' if why else ''}, {s.get('failed', 0)} failed")
        return
    acct = _account(cfg, a.account) if getattr(a, "account", None) else None
    if act == "show":
        r = _must(acct, a.rule)
        print(json.dumps({**r, "hash": rule_hash(r), "signed": signed_state(r)}, indent=1, ensure_ascii=False))
        print(sign_words(acct, r))
    elif act == "seed":
        accts = _own(cfg) if a.all else [acct] if acct else []
        if not accts:
            raise SystemExit("rules seed <account>, or --all")
        for x in accts:
            try:
                print(seed(cfg, x, _by(cfg, a.by)))
            except SystemExit as e:
                print(str(e))
    elif act == "try":
        got = try_text(cfg, acct, a.comment, a.platform, a.post, name=a.name)
        print(f"tags: {', '.join(got['tags']) or 'none'}; post: {(got['post'] or {}).get('title') or 'none'}")
        for c in got["candidates"]:
            print(f"  {c['rule']}: {'matches ' + json.dumps(c['matched']) if c['matched'] else 'no match'}")
        print(_fmt_run(got["run"]) if got["run"] else "no rule would answer it")
        if got["missing"]:
            print("missing facts: " + "; ".join(f"{k}: {v}" for k, v in got["missing"].items()))
    elif act == "replay":
        got = replay(cfg, acct, a.days, a.rule)
        t = got["tally"]
        print(f"{acct.name}, last {got['days']} days: {t['comments']} comments, {t['matched']} matched, "
              f"{t['would_send']} would have been answered ({t['held']} held a while), {t['skipped']} skipped "
              f"({', '.join(f'{SKIP_WORDS.get(k, k)} {v}' for k, v in t['why'].items()) or 'none'}), "
              f"{t['suggested']} YouTube suggestions")
        for r in got["rows"][:40]:
            print(_fmt_run(r))
    elif act == "runs":
        rows = [r for r in _SS().runs(acct) if not a.rule or r.get("rule") == a.rule][-max(1, a.limit):]
        for r in rows:
            print(_fmt_run(r))
    elif act in ("pause", "practice"):
        print(set_mode(cfg, acct, a.rule, "paused" if act == "pause" else "practice", _by(cfg, a.by)))
