"""The playbook: what Andrew approved from the learner's proposals, per account, read by the scout, the coach and the
Claude runbooks. Nothing goes in by itself: every rule comes from a proposal he approved (Reports tab, Proposals, or
`factory.py learn approve`), and every rule ends 60 days after it was approved unless he approves it again.

    state/<slug>/playbook.json
        {"version": 3, "updated": "...", "by": "Andrew", "account": "gaming", "family": "gta",
         "scout": {"channels": {"mrbossftw": 1.3}, "people": {}, "heat_words": {}},          factors 0.5 to 1.6
         "coach": {"length_best": {"youtube_shorts": [30, 45]}, "first_word_max_s": 1.0, "weights": {},
                   "prefer": ["a number in the title"], "avoid": []},
         "runbook": ["Lead with the answer, not the question."],                             8 lines at most
         "rules": [{"id", "key", "value", "approved", "expires", "by", "proposal", "version"}]}
    state/<slug>/learn/playbook-history.jsonl     one line per change, with the whole playbook after it (revert)

The scout, coach and runbook blocks are written from the rules still in force. What reads them:

    apply_to_scout(acct, scfg)    scout.run, after load() (so dated phases win): scfg["learned"], only while the
                                  account's learn.playbook switch is on and a scout rule is in force
    learned_factor(f, scfg)       scout.score: exactly 1.0 when scfg has no "learned" (and on any error)
    coach_block(acct)             the coach: {} while learn.playbook is off
    brief(acct)                   `factory.py learn brief <acct>`: 40 lines at most, for the runbooks

A new account in a family (its accounts.json "family", else its maker) with no scout rules of its own starts from
the family's playbook, each factor pulled toward 1.0 by n/(n + 12), n being its own posts with a 24-hour number:
at 0 posts it takes the family's factor whole, at 12 posts half of it (in log terms), and less after that.
"""
from __future__ import annotations

import json
import math
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from . import config as C
from . import store
from . import switches as SW

FACTOR_MIN, FACTOR_MAX = 0.5, 1.6
RULE_DAYS = 60
RUNBOOK_MAX = 8
RUNBOOK_CHARS = 200
WORD_CHARS = 80
SHRINK = 12.0
COACH_CATS = ("hook", "keep_watching", "no_sound", "density", "share")
OUTLETS = ("youtube_shorts", "facebook_reels", "instagram_reels", "tiktok")
SCOUT_PARTS = ("channels", "people", "heat_words")
DASHES = ("\u2014", "\u2013")
# what a proposal may change (everything else is advice: noted, never applied)
WHITELIST = ("scout.channels.", "scout.people.", "scout.heat_words.", "coach.length_best.", "coach.first_word_max_s",
             "coach.weights.", "coach.prefer.", "coach.avoid.", "runbook.", "draft_rule.")


# ------------------------------------------------------------------------------------------------------ where
def _slug(acct) -> str:
    return str(getattr(acct, "slug", acct) or "")


def path(acct) -> Path:
    return store.path(_slug(acct), "playbook.json")


def history_path(acct) -> Path:
    return store.path(_slug(acct), "learn", "playbook-history.jsonl")


def family_of(acct) -> str:
    raw = getattr(acct, "raw", None) or {}
    return str((raw.get("family") if isinstance(raw, dict) else "") or getattr(acct, "maker", "") or "")


def _now(now: Optional[datetime] = None) -> datetime:
    return (now or datetime.now(timezone.utc)).astimezone(timezone.utc)


def _iso(t: datetime) -> str:
    return _now(t).replace(microsecond=0).isoformat()


def _when(v) -> Optional[datetime]:
    try:
        t = datetime.fromisoformat(str(v).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    return t if t.tzinfo else t.replace(tzinfo=timezone.utc)


def name_key(s: str) -> str:
    """A name as the playbook keys it: lowercase, single spaces."""
    return re.sub(r"\s+", " ", str(s or "").strip().lower())


def slug_key(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", str(s or "").lower()).strip("-")[:40] or "x"


# ------------------------------------------------------------------------------------------------------ reading
def empty(acct=None) -> dict:
    return {"version": 0, "updated": "", "by": "", "account": _slug(acct) if acct is not None else "",
            "family": family_of(acct) if acct is not None else "",
            "scout": {p: {} for p in SCOUT_PARTS},
            "coach": {"length_best": {}, "first_word_max_s": None, "weights": {}, "prefer": [], "avoid": []},
            "runbook": [], "rules": []}


def _raw(acct) -> dict:
    d = store.read(path(acct), None)
    return d if isinstance(d, dict) else {}


def active_rules(rules, now: Optional[datetime] = None) -> List[dict]:
    """The rules still in force (each ends 60 days after it was approved)."""
    t = _now(now)
    out = []
    for r in rules if isinstance(rules, list) else []:
        if not isinstance(r, dict) or not isinstance(r.get("key"), str):
            continue
        end = _when(r.get("expires"))
        if end is not None and end <= t:
            continue
        out.append(r)
    return out


def materialize(rules: List[dict], base: Optional[dict] = None) -> dict:
    """The scout, coach and runbook blocks from the rules in force (the later rule wins for one key)."""
    pb = base if base is not None else empty()
    scout = {p: {} for p in SCOUT_PARTS}
    coach = {"length_best": {}, "first_word_max_s": None, "weights": {}, "prefer": [], "avoid": []}
    runbook: List[str] = []
    for r in sorted(rules, key=lambda x: str(x.get("approved") or "")):
        k, v = r["key"], r.get("value")
        try:
            if k.startswith("scout."):
                _, part, name = k.split(".", 2)
                if part in scout:
                    scout[part][name] = _clamp(float(v))
            elif k.startswith("coach.length_best."):
                coach["length_best"][k.split(".", 2)[2]] = [float(v[0]), float(v[1])]
            elif k == "coach.first_word_max_s":
                coach["first_word_max_s"] = float(v)
            elif k.startswith("coach.weights."):
                coach["weights"][k.split(".", 2)[2]] = float(v)
            elif k.startswith("coach.prefer.") and str(v) not in coach["prefer"]:
                coach["prefer"].append(str(v))
            elif k.startswith("coach.avoid.") and str(v) not in coach["avoid"]:
                coach["avoid"].append(str(v))
            elif k.startswith("runbook.") and str(v) not in runbook:
                runbook.append(str(v))
        except (TypeError, ValueError, IndexError):
            continue
    pb["scout"], pb["coach"], pb["runbook"] = scout, coach, runbook[-RUNBOOK_MAX:]
    return pb


def read(acct, now: Optional[datetime] = None) -> dict:
    """The account's playbook as it stands now: its file over the empty one, the blocks written again from the rules
    still in force (an expired rule is simply gone). Never raises."""
    pb = empty(acct)
    raw = _raw(acct)
    for k in ("version", "updated", "by"):
        if k in raw:
            pb[k] = raw[k]
    if not isinstance(pb["version"], int) or isinstance(pb["version"], bool):
        pb["version"] = 0
    rules = active_rules(raw.get("rules"), now)
    pb["rules"] = rules
    return materialize(rules, pb)


def history(acct) -> List[dict]:
    return store.rows(history_path(acct))


# ------------------------------------------------------------------------------------------------------ checking
def _clamp(x: float) -> float:
    return round(min(FACTOR_MAX, max(FACTOR_MIN, float(x))), 3)


def _text(v, most: int, what: str) -> str:
    if not isinstance(v, str) or not v.strip():
        raise SystemExit(f"{what}: some words")
    v = re.sub(r"\s+", " ", v.strip())
    if any(d in v for d in DASHES):
        raise SystemExit(f"{what}: no long dashes (use a colon or a comma)")
    if "!" in v:
        raise SystemExit(f"{what}: no exclamation marks")
    if len(v) > most:
        raise SystemExit(f"{what}: {len(v)} characters, {most} at most")
    return v


def check(key: str, value) -> Tuple[str, object]:
    """(key, value) as the playbook keeps them, clamped and cleaned; SystemExit for a key off the whitelist or a value
    it may not take. Factors are clamped to 0.5 to 1.6 (never refused for being large: a proposal is pulled in)."""
    key = str(key or "").strip()
    if not key.startswith(WHITELIST) and key != "coach.first_word_max_s":
        raise SystemExit(f"{key or 'that'}: the playbook takes scout factors, coach settings, runbook lines and draft "
                         f"rules only; anything else is advice")
    if key.startswith("scout."):
        _, part, name = (key.split(".", 2) + ["", ""])[:3]
        if part not in SCOUT_PARTS or not name_key(name):
            raise SystemExit(f"{key}: scout.channels|people|heat_words.<name>")
        try:
            x = float(value)
        except (TypeError, ValueError):
            raise SystemExit(f"{key}: a factor, such as 1.2") from None
        if not math.isfinite(x) or x <= 0:
            raise SystemExit(f"{key}: a factor above 0")
        return f"scout.{part}.{name_key(name)}", _clamp(x)
    if key.startswith("coach.length_best."):
        outlet = key.split(".", 2)[2]
        if outlet not in OUTLETS:
            raise SystemExit(f"{key}: the platform is one of {', '.join(OUTLETS)}")
        if not isinstance(value, (list, tuple)) or len(value) != 2:
            raise SystemExit(f"{key}: [shortest, longest] in seconds")
        try:
            lo, hi = float(value[0]), float(value[1])
        except (TypeError, ValueError):
            raise SystemExit(f"{key}: [shortest, longest] in seconds") from None
        if not (5 <= lo < hi <= 180):
            raise SystemExit(f"{key}: between 5 and 180 seconds, the shorter first")
        return key, [round(lo, 1), round(hi, 1)]
    if key == "coach.first_word_max_s":
        try:
            x = float(value)
        except (TypeError, ValueError):
            raise SystemExit(f"{key}: seconds, such as 1.0") from None
        if not 0.2 <= x <= 5:
            raise SystemExit(f"{key}: 0.2 to 5 seconds")
        return key, round(x, 2)
    if key.startswith("coach.weights."):
        cat = key.split(".", 2)[2]
        if cat not in COACH_CATS:
            raise SystemExit(f"{key}: the weights are {', '.join(COACH_CATS)}")
        try:
            x = float(value)
        except (TypeError, ValueError):
            raise SystemExit(f"{key}: 0 to 1") from None
        if not 0 <= x <= 1:
            raise SystemExit(f"{key}: 0 to 1")
        return key, round(x, 3)
    if key.startswith(("coach.prefer.", "coach.avoid.")):
        words = _text(value, WORD_CHARS, key)
        part = "prefer" if key.startswith("coach.prefer.") else "avoid"
        return f"coach.{part}.{slug_key(words)}", words
    if key.startswith("runbook."):
        line = _text(value, RUNBOOK_CHARS, "a runbook line")
        return f"runbook.{slug_key(line)}", line
    if key.startswith("draft_rule."):
        intent = key.split(".", 1)[1]
        if not re.fullmatch(r"[a-z0-9_]{2,40}", intent):
            raise SystemExit(f"{key}: draft_rule.<intent>")
        v = value if isinstance(value, dict) else {}
        return key, {"intent": intent, "n": int(v.get("n") or 0), "words": str(v.get("words") or "")[:200]}
    raise SystemExit(f"{key}: not a playbook key")


# ------------------------------------------------------------------------------------------------------ writing
def _write(cfg, acct, pb: dict, by: str, what: str, key: str, old, new, proposal: str, now: datetime) -> dict:
    pb["version"] = int(pb.get("version") or 0) + 1
    pb["updated"] = _iso(now)
    pb["by"] = str(by or "")
    pb["account"] = _slug(acct)
    pb["family"] = family_of(acct)
    out = materialize(pb["rules"], pb)
    store.write(path(acct), out)
    store.append(history_path(acct), {"version": out["version"], "at": out["updated"], "by": out["by"], "what": what,
                                      "key": key, "from": old, "to": new, "proposal": proposal,
                                      "playbook": json.loads(json.dumps(out))})
    return out


def _by(cfg, by) -> str:
    if by:
        return str(by)
    return str(cfg.approvers[0]) if getattr(cfg, "approvers", None) else "factory.py"


def apply(cfg, acct, proposal: dict, by: str, now: Optional[datetime] = None) -> str:
    """Put an approved proposal's change into the playbook: its key checked against the whitelist, its value clamped,
    a rule that ends in 60 days (one already there for the key is replaced), a new version and a history line. What
    it says."""
    now = _now(now)
    if not isinstance(proposal, dict):
        raise SystemExit("not a proposal")
    key, value = check(proposal.get("key"), proposal.get("to"))
    by = _by(cfg, by)
    pb = read(acct, now)
    rules = [r for r in pb["rules"] if r.get("key") != key]
    old = next((r.get("value") for r in pb["rules"] if r.get("key") == key), None)
    rule = {"id": f"r-{now:%Y%m%d%H%M%S}-{slug_key(key)[-16:]}", "key": key, "value": value,
            "approved": _iso(now), "expires": _iso(now + timedelta(days=RULE_DAYS)), "by": by,
            "proposal": str(proposal.get("id") or ""), "version": int(pb["version"]) + 1}
    shown = proposal.get("name")
    if isinstance(shown, str) and shown.strip() and key.startswith("scout.") \
            and name_key(shown) == key.split(".", 2)[2]:
        rule["name"] = re.sub(r"\s+", " ", shown.strip())[:WORD_CHARS]       # "MrBossFTW", not the key's "mrbossftw"
    pb["rules"] = rules + [rule]
    out = _write(cfg, acct, pb, by, "apply", key, old, value, rule["proposal"], now)
    name = getattr(acct, "name", _slug(acct))
    return (f"{name}: playbook version {out['version']}: {words_of(key, value, rule.get('name') or '')} "
            f"(until {rule['expires'][:10]})")


def revert(cfg, acct, version: int, by: str, now: Optional[datetime] = None) -> str:
    """Make the playbook what it was at `version` again (as a new version, so the history keeps both). Version 0 is
    the empty playbook."""
    now = _now(now)
    try:
        version = int(version)
    except (TypeError, ValueError):
        raise SystemExit("revert: a version number") from None
    by = _by(cfg, by)
    cur = read(acct, now)
    if version < 0 or version >= int(cur["version"] or 0):
        raise SystemExit(f"revert: version {version} is not an earlier version (the playbook is at {cur['version']})")
    if version == 0:
        snap = empty(acct)
    else:
        snap = next((r.get("playbook") for r in history(acct) if r.get("version") == version
                     and isinstance(r.get("playbook"), dict)), None)
        if snap is None:
            raise SystemExit(f"revert: version {version} is not in the history")
    pb = dict(cur)
    pb["rules"] = active_rules(snap.get("rules"), now)
    out = _write(cfg, acct, pb, by, "revert", f"version {version}", cur["version"], version, "", now)
    name = getattr(acct, "name", _slug(acct))
    return f"{name}: playbook version {out['version']} is version {version} again"


def revert_change(acct, version: int, now: Optional[datetime] = None) -> Dict[str, List[str]]:
    """What going back to `version` would do, in words: {"out": rules taken out, "back": rules brought back, "keep":
    rules that stay}. Empty lists when the version is not in the history."""
    cur = read(acct, now)
    if int(version) == 0:
        snap_rules: List[dict] = []
    else:
        snap = next((r.get("playbook") for r in history(acct) if r.get("version") == int(version)
                     and isinstance(r.get("playbook"), dict)), None)
        if snap is None:
            return {"out": [], "back": [], "keep": []}
        snap_rules = active_rules(snap.get("rules"), now)
    now_ids = {r.get("id"): r for r in cur["rules"]}
    then_ids = {r.get("id"): r for r in snap_rules}
    say = rule_words
    return {"out": [say(r) for i, r in now_ids.items() if i not in then_ids],
            "back": [say(r) for i, r in then_ids.items() if i not in now_ids],
            "keep": [say(r) for i, r in now_ids.items() if i in then_ids]}


def take_back(cfg, acct, rule_id: str, by: str, now: Optional[datetime] = None, proposal: str = "") -> str:
    """Take one rule out of the playbook (a "did it work" take-back): a new version with every other rule in force
    left as it is. Never a whole earlier version: rules approved after this one stay. What it says."""
    now = _now(now)
    by = _by(cfg, by)
    pb = read(acct, now)
    rule = next((r for r in pb["rules"] if str(r.get("id") or "") == str(rule_id or "")), None)
    if rule is None:
        raise SystemExit("take back: that rule is no longer in the playbook (it ended, or was replaced or taken out)")
    pb["rules"] = [r for r in pb["rules"] if r is not rule]
    out = _write(cfg, acct, pb, by, "take back", rule["key"], rule.get("value"), None, proposal, now)
    name = getattr(acct, "name", _slug(acct))
    left = len(out["rules"])
    return (f"{name}: playbook version {out['version']}: took back {rule_words(rule)}"
            f"; {left} other rule{'' if left == 1 else 's'} stay{'s' if left == 1 else ''}")


# ------------------------------------------------------------------------------------------------------ words
OUTLET_WORDS = {"youtube_shorts": "YouTube", "facebook_reels": "Facebook", "instagram_reels": "Instagram",
                "tiktok": "TikTok"}


def rule_words(r: dict) -> str:
    """A rule in force, in plain words, with the name it was approved under."""
    return words_of(str(r.get("key") or ""), r.get("value"), str(r.get("name") or ""))


def words_of(key: str, value, name: str = "") -> str:
    """A rule in plain words: "the scout ranks MrBossFTW's videos 1.3x" (`name` is how the rule shows its key's name,
    when it kept one)."""
    try:
        if key.startswith("scout."):
            _, part, keyname = key.split(".", 2)
            name = name if name and name_key(name) == keyname else keyname
            what = {"channels": f"{C.possessive(name)} videos", "people": f"videos naming {name}",
                    "heat_words": f"titles with \"{name}\""}[part]
            return f"the scout ranks {what} {float(value):g}x"
        if key.startswith("coach.length_best."):
            o = key.split(".", 2)[2]
            return f"the coach's best length on {OUTLET_WORDS.get(o, o)} is {value[0]:g} to {value[1]:g} seconds"
        if key == "coach.first_word_max_s":
            return f"the coach wants the first word within {float(value):g} seconds"
        if key.startswith("coach.weights."):
            return f"the coach weighs {key.split('.', 2)[2].replace('_', ' ')} at {float(value):g}"
        if key.startswith("coach.prefer."):
            return f"the coach prefers {value}"
        if key.startswith("coach.avoid."):
            return f"the coach marks down {value}"
        if key.startswith("runbook."):
            return f"the runbooks read: {value}"
        if key.startswith("draft_rule."):
            said = (value or {}).get("words") if isinstance(value, dict) else ""
            return f"a request for an automation that answers \"{said or key.split('.', 1)[1].replace('_', ' ')}\""
    except (TypeError, ValueError, KeyError, IndexError):
        pass
    return f"{key} = {value}"


# ------------------------------------------------------------------------------------------------------ readers
def _features_n(slug: str) -> int:
    rows = store.read(store.path(slug, "learn", "features.json"), {})
    rows = rows.get("rows") if isinstance(rows, dict) else rows
    return sum(1 for r in rows if isinstance(r, dict) and isinstance(r.get("views"), dict)
               and r["views"].get("24h") is not None) if isinstance(rows, list) else 0


def _family_block(acct, now: Optional[datetime] = None) -> Dict[str, Dict[str, float]]:
    """The scout factors of another account in the same family, pulled toward 1.0 by n/(n + 12)."""
    fam = family_of(acct)
    me = _slug(acct)
    if not fam:
        return {}
    best, best_n = None, -1
    root = Path(C.STATE)
    for p in sorted(root.glob("*/playbook.json")) if root.is_dir() else []:
        slug = p.parent.name
        if slug == me:
            continue
        other = store.read(p, None)
        if not isinstance(other, dict) or str(other.get("family") or "") != fam:
            continue
        rules = [r for r in active_rules(other.get("rules"), now) if str(r.get("key", "")).startswith("scout.")]
        if len(rules) > best_n and rules:
            best, best_n = materialize(rules)["scout"], len(rules)
    if not best:
        return {}
    n = _features_n(me)
    keep = SHRINK / (n + SHRINK)
    return {part: {k: round(math.exp(math.log(v) * keep), 3) for k, v in (best.get(part) or {}).items() if v > 0}
            for part in SCOUT_PARTS}


def scout_block(acct, now: Optional[datetime] = None) -> Dict[str, Dict[str, float]]:
    """The scout factors in force ({} when there are none), the family's when the account has none of its own."""
    own = read(acct, now)["scout"]
    if any(own.get(p) for p in SCOUT_PARTS):
        return {p: dict(own.get(p) or {}) for p in SCOUT_PARTS}
    fam = _family_block(acct, now)
    return fam if any(fam.get(p) for p in SCOUT_PARTS) else {}


def apply_to_scout(acct, scfg: dict) -> dict:
    """scout.run's read point, right after load() (phases applied): the rules with scfg["learned"] added, only while
    learn.playbook is on and a scout factor is in force. A dated phase that set "learned" itself wins. Never raises:
    any trouble leaves the rules as they were."""
    try:
        if not isinstance(scfg, dict) or "learned" in scfg:
            return scfg
        if SW.mode(acct, "learn.playbook") != "on":
            return scfg
        blk = scout_block(acct)
        if not any(blk.get(p) for p in SCOUT_PARTS):
            return scfg
        out = dict(scfg)
        out["learned"] = blk
        return out
    except Exception:  # noqa: BLE001  (the scout never stops over the playbook)
        return scfg


def learned_factor(f: dict, scfg: dict) -> float:
    """One find's factor from scfg["learned"]: its channel (slug or name), each person its title names and each heat
    word it hit, multiplied, the whole kept within 0.5 to 1.6. Exactly 1.0 when scfg has no "learned", and on any
    error."""
    try:
        L = scfg.get("learned") if isinstance(scfg, dict) else None
        if not L:
            return 1.0
        x = 1.0
        ch = {name_key(k): float(v) for k, v in (L.get("channels") or {}).items()}
        for k in (f.get("channel_slug"), f.get("channel")):
            if k and name_key(k) in ch:
                x *= _clamp(ch[name_key(k)])
                break
        people = {name_key(k): float(v) for k, v in (L.get("people") or {}).items()}
        for p in f.get("people") or []:
            if name_key(p) in people:
                x *= _clamp(people[name_key(p)])
        heat = {name_key(k): float(v) for k, v in (L.get("heat_words") or {}).items()}
        for w in f.get("heat_words") or []:
            if name_key(w) in heat:
                x *= _clamp(heat[name_key(w)])
        if not math.isfinite(x):
            return 1.0
        return 1.0 if x == 1.0 else min(FACTOR_MAX, max(FACTOR_MIN, x))
    except Exception:  # noqa: BLE001
        return 1.0


def coach_block(acct) -> dict:
    """What the coach reads: the coach block while learn.playbook is on, {} while it is off (the defaults hold)."""
    try:
        if SW.mode(acct, "learn.playbook") != "on":
            return {}
        c = read(acct)["coach"]
        return c if (c.get("length_best") or c.get("first_word_max_s") or c.get("weights") or c.get("prefer")
                     or c.get("avoid")) else {}
    except Exception:  # noqa: BLE001
        return {}


# ------------------------------------------------------------------------------------------------------ the brief
def _latest_report(slug: str) -> dict:
    d = store.path(slug, "learn")
    files = sorted(d.glob("report-*.json")) if d.is_dir() else []
    for f in reversed(files):
        r = store.read(f, None)
        if isinstance(r, dict):
            return r
    return {}


def _k(n) -> str:
    try:
        n = float(n)
    except (TypeError, ValueError):
        return "0"
    if n >= 1e6:
        return f"{n / 1e6:.1f}M"
    if n >= 1e4:
        return f"{n / 1e3:.0f}K"
    if n >= 1e3:
        return f"{n / 1e3:.1f}K"
    return f"{n:.0f}"


def _x(x) -> str:
    try:
        x = float(x)
    except (TypeError, ValueError):
        return "1.0x"
    return "under 0.1x" if x < 0.1 else f"{x:.0f}x" if x >= 10 else f"{x:.1f}x"


def brief(acct, now: Optional[datetime] = None) -> str:
    """For the Claude runbooks ("Before choosing, read: factory.py learn brief <acct>"): the playbook in force, what
    works, yesterday's biggest problems and the best recent posts. 40 lines at most; plain words."""
    slug = _slug(acct)
    name = getattr(acct, "name", slug)
    pb = read(acct, now)
    rep = _latest_report(slug)
    on = SW.mode(acct, "learn.playbook") == "on"
    lines = [f"{name}: what the learner knows" + (f" (report for {rep.get('day')})" if rep.get("day") else "")
             + f", playbook version {pb['version']}"]
    rules = [r for r in pb["rules"] if not str(r.get("key", "")).startswith("draft_rule.")]   # those are for Andrew
    if not on:
        # off means off for the runbooks too: no rule and no runbook note is handed to a night run as guidance
        n = len(rules)
        lines.append("The playbook switch is off: nothing in it is in force"
                     + (f" ({n} approved rule{' waits' if n == 1 else 's wait'} for the switch)" if n else "")
                     + ". Choose as the runbook says.")
    elif rules:
        lines.append("Playbook (approved by Andrew, in force):")
        for r in rules[-10:]:
            lines.append(f"- {rule_words(r)} (until {str(r.get('expires'))[:10]})")
        if pb["runbook"]:
            lines.append("Runbook notes:")
            lines += [f"- {x}" for x in pb["runbook"]]
    else:
        lines.append("Playbook: nothing approved yet.")
    works = [w for w in rep.get("works") or [] if w.get("confidence") in ("strong", "likely")]
    early = sum(1 for w in rep.get("works") or [] if w.get("confidence") == "early sign")
    if works:
        lines.append("What works (from the last report; each platform on its own):")
        for w in works[:6]:
            o = OUTLET_WORDS.get(w.get("outlet"), w.get("outlet") or "")
            lines.append(f"- {w.get('words')} on {o}: {_x(w.get('lift'))} the usual ({w.get('confidence')})")
    if early:
        lines.append(f"Not proven yet: {early} early sign{'' if early == 1 else 's'}, left out here "
                     f"(not enough posts).")
    probs = [p for p in rep.get("problems") or [] if p.get("views_lost")]
    if probs:
        lines.append("Biggest problems:")
        for p in probs[:4]:
            lines.append(f"- {p.get('words')}: about {_k(p.get('views_lost'))} views lost. Fix: {p.get('fix')}")
    best = rep.get("best") or []
    if best:
        lines.append("Best recent posts:")
        for b in best[:3]:
            lines.append(f"- \"{str(b.get('title') or '')[:70]}\" on {OUTLET_WORDS.get(b.get('outlet'), b.get('outlet'))}"
                         f": {_k(b.get('views_24h'))} at 24 hours ({_x(b.get('x'))} the usual)")
    if not rep:
        lines.append(f"No report yet: python3 ~/clip-factory/factory.py learn daily --account {slug}")
    return "\n".join(lines[:40])
