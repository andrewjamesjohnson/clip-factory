"""The automatic sign-off (the owner, 2026-10-19: "I want to make sure everything is on auto ... I don't want to wake
up and then clips aren't posted because stuff isn't approved", then "Yes. Build the automatic sign-off: every clip
that passed its checks signs itself 30 minutes before its slot, signed as the factory, not me. Clips naming 2 or more
people nobody checked still wait for me.").

A clip still waiting at `ready` signs itself `lead_minutes` before its slot, so it posts at its time whether or not
anyone pressed Approve. It is Approve's own signature (signoff.approve: the clip's bytes, every platform's words and
the time, re-checked before every post), with the factory's name on it, never a person's. Approve, Pull, Decline and
Make again work as before: Approve sooner signs it in the owner's name, and he can still Pull it up to its slot.

It never signs:
  - a clip a person took back with Pull (held until a person approves it; a new clip in the slot is free again),
  - a clip naming two or more people whose cast no person checked ("cast_unchecked": "one", the default; "all" signs
    those too, "none" holds every clip with an unchecked cast). One person named in the source's own title signs;
  - a clip held by a Not them in Casts, a stand-in test clip, or one Approve itself refuses (a file gone, outside the
    band): such a refusal is noted once and not tried again until the clip or its words change,
  - a slot whose time passed more than `late_hours` ago (the day's news has moved on; a person decides),
  - a breaking clip whose platform cuts the desk is still making (breaking.cuts_wait: until `breaking go`).
It never writes `verified` into a cast.json: a sign-off nobody watched is no person's cast check.

config/accounts.json "auto_sign": {"on": true, "lead_minutes": 30, "late_hours": 3, "cast_unchecked": "one"};
an account's own "auto_sign" block changes any of those keys for it. No block, or "on" not true, is the old way
(Approve only). A client account (accounts.py, "owner": "client") signs itself only by its own block.
The poster runs it at the top of every post-due (every 10 minutes), so a clip signs itself 20 to 30 minutes ahead.
The coach gate (the owner, 2026-10-30, the loops thesis item 1: "yes, I would like to build all those"): the hook
coach gives each clip one word, PASS, FIX or KILL. "coach_gate": "shadow" only writes down the word of each clip
that signs itself (state/<slug>/coach/gate-shadow.jsonl, and a log line for a FIX or KILL) and holds nothing; "off"
(the default) does not look. "on" (the loops thesis item 17, 2026-10-31: "17 and 18") keeps a clip whose fresh
scorecard says FIX or KILL from signing itself (gate_hold): it waits for the fix and the coach's new look, or for a
person's Approve; a slot held twice waits for a person. Switch it on only when `factory.py judge` shows the coach
agrees with Andrew (judge.py).
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional

from . import board as B
from . import config as C

BY = "Clip Factory (auto)"          # the name on an automatic signature: the factory's, never a person's
DEFAULTS = {"on": False, "lead_minutes": 30, "late_hours": 3, "cast_unchecked": "one", "coach_gate": "off"}
CAST_RULES = ("one", "all", "none")
GATES = ("off", "shadow", "on")     # on: a FIX or KILL waits (gate_hold); only once `factory.py judge` agrees


def _top(cfg: C.Config) -> dict:
    try:
        v = json.loads(Path(cfg.path).read_text()).get("auto_sign")
    except (OSError, ValueError, AttributeError):
        return {}
    return v if isinstance(v, dict) else {}


def settings(cfg: C.Config, acct: C.Account, top: Optional[dict] = None) -> dict:
    """The account's auto sign-off settings: the top-level block, then the account's own over it. Off unless "on" is
    exactly true; a client account takes only its own block."""
    own = (acct.raw or {}).get("auto_sign")
    own = own if isinstance(own, dict) else {}
    base = {} if (acct.raw or {}).get("owner") == "client" else (top if top is not None else _top(cfg))
    st = dict(DEFAULTS)                 # one key in both blocks ("on"): the account's wins (dict(**a, **b) raised)
    st.update(base)
    st.update(own)
    st["on"] = st.get("on") is True
    try:
        st["lead_minutes"] = max(0.0, float(st["lead_minutes"]))
        st["late_hours"] = max(0.0, float(st["late_hours"]))
    except (TypeError, ValueError):
        st.update(lead_minutes=DEFAULTS["lead_minutes"], late_hours=DEFAULTS["late_hours"])
    if st.get("cast_unchecked") not in CAST_RULES:
        st["cast_unchecked"] = DEFAULTS["cast_unchecked"]
    if st.get("coach_gate") not in GATES:
        st["coach_gate"] = DEFAULTS["coach_gate"]
    return st


def on(cfg: C.Config, acct: C.Account) -> bool:
    return settings(cfg, acct)["on"]


def slot_at(s: dict) -> Optional[datetime]:
    try:
        return datetime.fromisoformat(str(s.get("publish_at")))
    except (TypeError, ValueError):
        return None


def sign_at(s: dict, st: dict) -> Optional[datetime]:
    """When this slot's clip signs itself."""
    at = slot_at(s)
    return at - timedelta(minutes=st["lead_minutes"]) if at else None


def held_by_pull(s: dict) -> bool:
    """A person pulled THIS clip: it waits for a person (a new clip in the slot is not held)."""
    h = s.get("auto_hold") or {}
    return bool(h) and h.get("clip") == (s.get("clip") or {}).get("sha256")


def hold(s: dict, who: str) -> None:
    """Pull's mark: this clip never signs itself again; a person's Approve clears it."""
    s["auto_hold"] = {"by": who, "at": B.utcnow(), "clip": (s.get("clip") or {}).get("sha256")}


def clear(s: dict) -> None:
    s.pop("auto_hold", None)
    s.pop("auto_refused", None)


def unchecked(cfg: Optional[C.Config], s: dict) -> List[str]:
    """The people this clip names whose cast no person has checked (CAST TO CONFIRM, or a bot's `verified`)."""
    cc = s.get("cast_check") or {}
    people = [str(p.get("name") or p.get("id") or "?") for p in cc.get("people") or []]
    if not people:
        return []
    from . import castqueue as CQ
    try:
        _, checked = CQ.cast_now(cc, cfg)
    except Exception:  # noqa: BLE001  (a cast file that cannot be read counts as not checked)
        checked = []
    return [p for p in people if p not in checked]


def cast_waits(cfg: Optional[C.Config], s: dict, st: dict) -> str:
    """Why the cast rule keeps this clip for a person ('' when it does not)."""
    left = unchecked(cfg, s)
    rule = st["cast_unchecked"]
    if not left or rule == "all":
        return ""
    if rule == "none":
        return f"its cast is not checked by a person ({', '.join(left)})"
    if len(left) >= 2:
        return f"it names {len(left)} people nobody checked ({', '.join(left)})"
    return ""


def refused_before(s: dict) -> bool:
    """Approve refused this clip's automatic sign-off and nothing about it changed since."""
    r = s.get("auto_refused")
    return bool(r) and r == B.seen_key(s)


def ending_unheard(acct: C.Account, s: dict) -> str:
    """'CHECK THE ENDING: ...' when the Politics render could not confirm the clip ends on a finished sentence in a real
    pause (viral-clip-engine out_point, "ending": "pause": tmp/NN-<slug>.ending.json with verified false). Such a clip
    waits for a person's Approve. 2026-10-26 (Andrew: "Yes, build all 6"): the audit counted 44 of 95 endings flagged
    and the automatic sign-off shipped every one. '' when the check passed, or nothing on disk says (the render's work
    folder is under /private/tmp and does not outlive a restart)."""
    if acct.maker != "politics" or not s.get("slug"):
        return ""
    from . import bench as BN
    try:
        sched = BN._read(acct.account_dir / "Publish" / "schedule.json")
        job = BN._job_of(acct, s, sched, str(s.get("publish_at") or "")[:10])
        if not job:
            return ""
        proj = json.loads((acct.account_dir / "Jobs" / job / "project.json").read_text())
        work = Path(str(proj.get("source") or "")).parent
        hits = sorted(work.glob(f"tmp/[0-9][0-9]-{s['slug']}.ending.json"))
        rec = json.loads(hits[-1].read_text()) if hits else {}
    except (OSError, ValueError, TypeError):
        return ""
    if rec.get("rule") != "pause" or rec.get("verified") is not False:
        return ""
    word = (rec.get("today") or {}).get("word")
    return ("CHECK THE ENDING: the render found no finished sentence ending in a real pause"
            + (f" (it ends after \"{word}\")" if word else "") + "; listen to the last second, then Approve")


def why_not(cfg: C.Config, acct: C.Account, s: dict, now: datetime, st: Optional[dict] = None,
            board: Optional[dict] = None) -> str:
    """Why this ready clip will NOT sign itself ('' when it will, at sign_at). Only for a slot at `ready`. With the
    slot's `board`, also a twin (moments.py: the same moment on another slot of the day)."""
    st = st or settings(cfg, acct)
    if not st["on"]:
        return "the automatic sign-off is off"
    if s.get("status") != B.READY or not s.get("clip"):
        return "nothing ready to sign"
    from . import poster as P
    from . import signoff as S
    at = slot_at(s)
    if at is None:
        return "its slot has no time"
    if now - at > timedelta(hours=st["late_hours"]):
        return f"its time passed more than {st['late_hours']:g} hours ago"
    if P.standin(s):
        return "it is a stand-in test clip"
    if held_by_pull(s):
        h = s.get("auto_hold") or {}
        return f"{h.get('by', 'a person')} pulled it"
    held = S._said_not_them(s, cfg)
    if held:
        return held
    waits = cast_waits(cfg, s, st)
    if waits:
        return waits
    if refused_before(s):
        return f"Approve refused it ({B.last_note(s)})"
    unheard = ending_unheard(acct, s)
    if unheard:
        return unheard
    gate = gate_hold(acct, s, board, st)  # the coach's FIX or KILL, only when the gate is on (the loops thesis item 17)
    if gate:
        return gate
    from . import moments as MO         # the night marked it stale; or the same moment is on another slot today
    if MO.stale(s):
        return MO.stale_words(s) + ": only its own Approve signs it"
    if board is not None and s.get("slot") in (board.get("slots") or {}):
        pairs = MO.twins(cfg, acct, None, board).get(s["slot"], [])
        why = MO.refuse_second(board, pairs) or MO.bulk_skip(board, s["slot"], pairs)
        if why:
            return why
    from . import breaking as BK         # a breaking clip goes the moment its platform cuts are made, not before
    return BK.cuts_wait(acct, s, now)


def due(cfg: C.Config, acct: C.Account, s: dict, now: datetime, st: dict, board: Optional[dict] = None) -> bool:
    if s.get("status") != B.READY or why_not(cfg, acct, s, now, st, board):
        return False
    when = sign_at(s, st)
    return when is not None and now >= when


def coach_word(acct: C.Account, day, slot: str, s: dict) -> str:
    """The coach's one word on the clip now in the slot: PASS, FIX or KILL ('' when it wrote none, or when its
    scorecard is of an earlier clip)."""
    from . import coach as CO
    card = CO.load(acct, day, slot)
    if not CO.fresh(card, s):
        return ""
    w = str(((card or {}).get("written") or {}).get("verdict") or "").upper()
    return w if w in CO.VERDICTS else ""


def gate_hold(acct: C.Account, s: dict, board: Optional[dict], st: dict) -> str:
    """coach_gate "on": why this clip waits for the coach's FIX or KILL ('' when the gate lets it through). The first
    hold of each clip is written to state/<slug>/coach/gate-holds.jsonl; a slot held for two different clips waits for
    a person, and a clip that changed after a hold waits until the coach has judged the new one."""
    if st.get("coach_gate") != "on" or not isinstance(board, dict) or not board.get("date"):
        return ""
    from . import coach as CO
    day, slot = str(board["date"]), str(s.get("slot") or "")
    sha = str((s.get("clip") or {}).get("sha256") or "")
    p = CO.card_path(acct, day, slot).parent.parent / "gate-holds.jsonl"
    try:
        rows = [json.loads(x) for x in p.read_text().splitlines() if x.strip()]
    except (OSError, ValueError):
        rows = []
    held = {r.get("sha") for r in rows if r.get("day") == day and r.get("slot") == slot}
    word = coach_word(acct, day, slot, s)
    if word not in ("FIX", "KILL"):
        if not word and held and sha not in held:
            return "the clip changed after the coach held it: it waits for the coach to judge the new clip"
        return ""
    if sha and sha not in held:
        try:
            p.parent.mkdir(parents=True, exist_ok=True)
            with p.open("a") as f:
                f.write(json.dumps({"at": datetime.now().astimezone().isoformat(timespec="seconds"), "day": day,
                                    "slot": slot, "sha": sha, "verdict": word}) + "\n")
        except OSError:
            pass
        held.add(sha)
    w = (CO.load(acct, day, slot) or {}).get("written") or {}
    what = " ".join(str((w.get("fix") if word == "FIX" else w.get("why")) or "").split())[:140]
    if len(held) >= 2:
        tail = "; held twice, so it waits for you (Approve signs it)"
    elif word == "FIX":
        tail = "; it waits for that fix, or for your Approve"
    else:
        tail = "; it waits for you: Approve, or a bench clip in its place"
    return f"the coach said {word}" + (f": {what}" if what else "") + tail


def shadow(acct: C.Account, day, bd: dict, keys: List[str], st: dict, now: datetime) -> List[str]:
    """coach_gate "shadow": the coach's word on every clip about to sign itself, written down and never acted on. A
    row per clip in state/<slug>/coach/gate-shadow.jsonl; a FIX or KILL also gets a line in the log."""
    if st.get("coach_gate") != "shadow" or not keys:
        return []
    from . import coach as CO
    lines, rows = [], []
    for k in keys:
        w = coach_word(acct, day, k, bd["slots"][k])
        rows.append({"at": now.isoformat(timespec="seconds"), "day": str(day), "slot": k, "verdict": w or "none"})
        if w in ("FIX", "KILL"):
            lines.append(f"  {acct.name} {day} {k}: the coach said {w} (shadow: it signs anyway)")
    p = CO.card_path(acct, day, keys[0]).parent.parent / "gate-shadow.jsonl"
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("a") as f:
        f.writelines(json.dumps(r) + "\n" for r in rows)
    return lines


def run(cfg: C.Config, now: Optional[datetime] = None, dry_run: bool = False) -> List[str]:
    """Sign every clip whose time has come, for every account working here. One account or day that breaks never
    stops the others (or the posting after it)."""
    from . import signoff as S
    now = now or cfg.now()
    top = _top(cfg)
    lines: List[str] = []
    for acct in cfg.accounts.values():
        st = settings(cfg, acct, top)
        if not st["on"]:
            continue
        for day in (now.date() - timedelta(days=1), now.date(), now.date() + timedelta(days=1)):
            try:
                bd = B.read(acct, day)
                if not bd:
                    continue
                keys = [k for k, s in sorted(bd["slots"].items()) if due(cfg, acct, s, now, st, bd)]
                if not keys:
                    continue
                if dry_run:
                    lines += [f"  {acct.name} {day} {k}: would sign itself now ({BY})" for k in keys]
                    continue
                try:
                    lines += shadow(acct, day, bd, keys, st, now)
                except Exception as e:  # noqa: BLE001  (the shadow only writes down: it never stops a signature)
                    lines.append(f"  {acct.name} {day}: the coach gate's shadow broke "
                                 f"({type(e).__name__}: {str(e)[:120]})")
                lines += [f"  {acct.name} {day}{ln[1:]}"
                          for ln in S.approve(cfg, acct, day, keys, by=BY, now=now, auto=True)]
            except Exception as e:  # noqa: BLE001
                lines.append(f"  {acct.name} {day}: automatic sign-off broke ({type(e).__name__}: {str(e)[:200]})")
    return lines


def card_words(cfg: C.Config, acct: C.Account, s: dict, now: datetime, board: Optional[dict] = None) -> Dict[str, str]:
    """For a ready card: {"when": "signs itself at 06:30"} or {"waits": why it will not}; {} when off."""
    st = settings(cfg, acct)
    if not st["on"] or s.get("status") != B.READY or not s.get("clip"):
        return {}
    why = why_not(cfg, acct, s, now, st, board)
    if why:
        return {"waits": why}
    when = sign_at(s, st)
    if when is None:
        return {}
    if when <= now:
        return {"when": "signs itself on the next posting run"}
    local = when.astimezone(cfg.tz()) if cfg.tz() else when
    return {"when": f"signs itself at {local:%H:%M}"}


def morning_words(cfg: C.Config, acct: C.Account, bd: dict, now: datetime) -> str:
    """The morning notice's words for an account's ready clips ('' when the sign-off is off)."""
    st = settings(cfg, acct)
    if not st["on"]:
        return ""
    ready = [s for s in bd["slots"].values() if s.get("status") == B.READY and s.get("clip")]
    waits = [s for s in ready if why_not(cfg, acct, s, now, st, bd)]
    words = f"they sign themselves {st['lead_minutes']:g} min before their slots"
    if waits:
        words += f"; {len(waits)} wait{'s' if len(waits) == 1 else ''} for you (" + ", ".join(
            f"{s['slot']}" for s in sorted(waits, key=lambda s: s["slot"])[:5]) + ")"
    return words


def doctor_line(cfg: C.Config) -> List[str]:
    """One line per account for factory.py doctor."""
    out = []
    for acct in cfg.accounts.values():
        st = settings(cfg, acct)
        if st["on"]:
            cast = {"one": "a clip naming 2 or more people nobody checked waits for a person",
                    "all": "every unchecked cast signs too",
                    "none": "a clip with any unchecked cast waits for a person"}[st["cast_unchecked"]]
            out.append(f"  ok  {acct.name}: automatic sign-off ON, {st['lead_minutes']:g} min before each slot "
                       f"(as {BY}; up to {st['late_hours']:g} h late; {cast})")
        else:
            out.append(f"  --  {acct.name}: automatic sign-off off (a clip posts only when a person approves it)")
    return out

