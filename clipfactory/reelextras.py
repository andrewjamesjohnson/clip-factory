"""Instagram reel extras: the poll on a Reel and the trial-reel flag, both signed with the clip (extras.py).

    poll    two to four options (1 to 25 characters each, 200 together at most), written by the nightly run or typed
            on the card while the clip waits for Approve. Instagram shows the reel's caption as the poll's question,
            so the card shows the first 80 characters of the Instagram caption beside the options.
    trial   a trial reel (shown to people who do not follow the account first) only for a clip that is an arm of a
            signed experiment (state/<account>/learn/experiments.json); graduation MANUAL or SS_PERFORMANCE.

The reel's container takes them in direct.ig_post through extras.ig_form, which gives them only while the switch is
on and the clip was signed with exactly this file: off and practice post the reel as it always went. Practice writes
a row on the Log ("would add the poll Yes / No") once the reel is up, so the go-live checklist can count it.

    factory.py extras poll gaming --date 2026-10-20 --slot 07:00 --options "Yes|No"
    factory.py extras trial gaming --date 2026-10-20 --slot 07:00 --experiment e-0003 --arm B
    factory.py social on gaming poll --practice
"""
from __future__ import annotations

import html
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from . import board as B
from . import config as C
from . import extras as EX
from . import store
from . import switches as SW

OUTLET = "instagram_reels"
POLL, TRIAL = "poll", "trial_reels"
GRADUATION = EX.GRADUATION                     # MANUAL, SS_PERFORMANCE
MIN_OPTS, MAX_OPTS, OPT_CHARS, POLL_CHARS = 2, 4, 25, 200
QUESTION_CHARS = 80
DASHES = ("\u2014", "\u2013")
ENDED = ("done", "concluded", "stopped", "declined", "expired", "closed")
FIELDS = ("poll_a", "poll_b", "poll_c", "poll_d")


def esc(s: object) -> str:
    return html.escape(str(s), quote=True)


def _slug(acct) -> str:
    return str(getattr(acct, "slug", acct) or "")


def _utc(now: Optional[datetime] = None) -> str:
    t = now or datetime.now(timezone.utc)
    if t.tzinfo is None:
        t = t.replace(tzinfo=timezone.utc)
    return t.astimezone(timezone.utc).replace(microsecond=0).isoformat()


def has_reels(acct) -> bool:
    """Does this account post Instagram Reels through the API (the only way a poll or a trial can ride on one)?"""
    from . import outlets as O
    return OUTLET in (getattr(acct, "platforms", None) or []) and O.mode(acct, OUTLET) == "api"


# ------------------------------------------------------------------------------------------------------ checking
def parse_options(text: str) -> List[str]:
    """ "Yes|No" or "Yes | No | Maybe" as a list (the CLI's form)."""
    return [o.strip() for o in str(text or "").split("|")]


def check_options(options) -> List[str]:
    """The poll's options, cleaned, or SystemExit saying what is wrong: two to four, 1 to 25 characters each, all
    of them 200 characters at most as Instagram reads them, no two the same, no long dash."""
    if not isinstance(options, (list, tuple)):
        raise SystemExit("poll: the options are a list (in the CLI: --options \"Yes|No\")")
    opts = [str(o or "").strip() for o in options]
    opts = [o for o in opts if o]
    if not MIN_OPTS <= len(opts) <= MAX_OPTS:
        raise SystemExit(f"poll: {MIN_OPTS} to {MAX_OPTS} options, not {len(opts)}")
    long = [o for o in opts if len(o) > OPT_CHARS]
    if long:
        raise SystemExit(f"poll: each option is 1 to {OPT_CHARS} characters ({long[0]!r} is {len(long[0])})")
    if any(d in o for o in opts for d in DASHES):
        raise SystemExit("poll: no long dashes in the options")
    if len({o.lower() for o in opts}) != len(opts):
        raise SystemExit("poll: two options are the same")
    if len(EX._poll_json(opts)) > POLL_CHARS:
        raise SystemExit(f"poll: the options are too long together ({POLL_CHARS} characters at most)")
    return opts


def options(slot: dict) -> List[str]:
    """The poll options in the slot's extras file ([] when it has none)."""
    ent = (EX.read(slot).get("poll") or {}).get(OUTLET)
    opts = ent.get("options") if isinstance(ent, dict) else None
    return [str(o) for o in opts] if isinstance(opts, list) else []


def trial(slot: dict) -> dict:
    """The trial flag in the slot's extras file ({} when it has none)."""
    ent = (EX.read(slot).get("trial") or {}).get(OUTLET)
    return dict(ent) if isinstance(ent, dict) else {}


def question(slot: dict) -> str:
    """What Instagram will show as the poll's question: the first 80 characters of the reel's caption ('' when the
    clip has no Instagram words)."""
    from . import copytext as T
    c = (slot.get("copy") or {}).get(OUTLET) if isinstance(slot, dict) else None
    p = c.get("path") if isinstance(c, dict) else None
    if not p:
        return ""
    try:
        cap = T.for_platform(Path(str(p)).read_text(), OUTLET).get("caption") or ""
    except (OSError, ValueError, KeyError):
        return ""
    cap = " ".join(cap.split())
    return cap if len(cap) <= QUESTION_CHARS else cap[:QUESTION_CHARS].rstrip() + "..."


# ------------------------------------------------------------------------------------------------------ experiments
def _experiments(acct) -> Dict[str, dict]:
    raw = store.read(store.path(_slug(acct), "learn", "experiments.json"), {})
    if isinstance(raw, dict) and isinstance(raw.get("experiments"), dict):
        raw = raw["experiments"]
    return {str(k): v for k, v in raw.items() if isinstance(v, dict)} if isinstance(raw, dict) else {}


def _arms(exp: dict) -> List[str]:
    arms = exp.get("arms")
    if isinstance(arms, dict):
        return [str(k) for k in arms]
    out = []
    for a in arms if isinstance(arms, list) else []:
        if isinstance(a, dict):
            a = a.get("id") or a.get("arm") or a.get("name")
        if a:
            out.append(str(a))
    return out


def arm_problem(acct, experiment: str, arm: str) -> str:
    """'' when `arm` is an arm of a signed, running experiment of this account; else why not (a trial reel is only
    ever an experiment's arm, signed by a person on the Reports tab)."""
    exp = _experiments(acct).get(str(experiment or ""))
    if exp is None:
        return (f"no experiment {experiment!r} in state/{_slug(acct)}/learn/experiments.json (trial reels are only "
                f"for an experiment's arm)")
    if not exp.get("signed"):
        return f"experiment {experiment} is not signed yet: approve it on the Reports tab first"
    if str(exp.get("state") or "").lower() in ENDED:
        return f"experiment {experiment} is {exp.get('state')}: no new trial reels for it"
    arms = _arms(exp)
    if str(arm or "") not in arms:
        return f"experiment {experiment} has no arm {arm!r} (its arms: {', '.join(arms) or 'none'})"
    return ""


# ------------------------------------------------------------------------------------------------------ writing
def set_poll(cfg: C.Config, acct, day: date, slot_key: str, opts, by: str, how: str = "edited") -> str:
    """Put a poll on the clip's Instagram reel while it waits for Approve (SystemExit when it is signed: Pull it)."""
    if not has_reels(acct):
        raise SystemExit(f"{acct.name} does not post Instagram Reels through the API: a poll has nothing to ride on")
    clean = check_options(list(opts or []))
    with EX.edit(cfg, acct, day, slot_key, by, "poll") as x:
        x["poll"] = {OUTLET: {"options": clean, "from": how if how in EX.FROM else "edited"}}
    return f"{acct.name} {day} {C.normal_slot(slot_key)}: the Instagram poll is {' / '.join(clean)}"


def set_trial(cfg: C.Config, acct, day: date, slot_key: str, experiment: str, arm: str, by: str,
              graduation: str = "SS_PERFORMANCE") -> str:
    """Mark the clip's reel as a trial reel for an experiment's arm (only a signed experiment's)."""
    if not has_reels(acct):
        raise SystemExit(f"{acct.name} does not post Instagram Reels through the API: no trial reel")
    g = str(graduation or "").upper()
    if g not in GRADUATION:
        raise SystemExit(f"trial: graduation is {' or '.join(GRADUATION)}")
    why = arm_problem(acct, experiment, arm)
    if why:
        raise SystemExit(f"trial: {why}")
    with EX.edit(cfg, acct, day, slot_key, by, "trial") as x:
        x["trial"] = {OUTLET: {"graduation_strategy": g, "experiment": str(experiment), "arm": str(arm)}}
        exp = dict(x.get("experiment") or {})
        exp[str(experiment)] = str(arm)
        x["experiment"] = exp
    return (f"{acct.name} {day} {C.normal_slot(slot_key)}: a trial reel for experiment {experiment}, arm {arm} "
            f"({g})")


def clear(cfg: C.Config, acct, day: date, slot_key: str, what: str, by: str) -> str:
    """Take the poll (what="poll") or the trial flag (what="trial") off a clip that waits for Approve."""
    if what not in ("poll", "trial"):
        raise SystemExit("clear: poll or trial")
    with EX.edit(cfg, acct, day, slot_key, by, f"clear {what}") as x:
        had = bool(x.pop(what, None))
        if what == "trial":
            x.pop("experiment", None)
    word = "poll" if what == "poll" else "trial reel flag"
    return (f"{acct.name} {day} {C.normal_slot(slot_key)}: " + (f"the {word} is off" if had else f"had no {word}"))


# ------------------------------------------------------------------------------------------------------ after the post
def _marker(rec: dict, name: str) -> dict:
    soc = rec.get("social") if isinstance(rec, dict) else None
    m = soc.get(name) if isinstance(soc, dict) else None
    return m if isinstance(m, dict) else {}


def _note(cfg: C.Config, acct, day: date, slot_key: str, name: str, upd: dict) -> None:
    """outlets.instagram_reels.social.<poll|trial> on the board (its own nested marker: so a row is written once)."""
    try:
        with B.edit(cfg, acct, day, create=False) as bd:
            s = (bd.get("slots") or {}).get(slot_key)
            rec = ((s or {}).get("outlets") or {}).get(OUTLET)
            if not isinstance(rec, dict):
                return
            soc = rec.get("social") if isinstance(rec.get("social"), dict) else {}
            soc[name] = dict(upd)
            rec["social"] = soc
    except SystemExit:
        return


def on_posted(cfg: C.Config, acct, day: date, slot: dict, rec: dict, now: datetime) -> Optional[str]:
    """After the Instagram reel is up: in practice, a Log row saying what the reel would have carried (the poll, the
    trial flag); switched on, a row saying what it went with. Once per reel (its marker on the outlet record). Writes
    nothing while both switches are off, and never calls a platform."""
    pm, tm = SW.mode(acct, POLL), SW.mode(acct, TRIAL)
    if pm == SW.OFF and tm == SW.OFF:
        return None
    if not isinstance(slot, dict) or slot.get("status") not in B.SIGNED or not (slot.get("extras") or {}).get("path"):
        return None
    board_rec = ((slot.get("outlets") or {}).get(OUTLET) or {})
    r = dict(rec or {})
    r.update(board_rec if isinstance(board_rec, dict) else {})
    pid = str(r.get("post_id") or "")
    if not pid or r.get("provider", "direct") != "direct":
        return None
    from . import outward as OW
    sent = EX.ig_form(slot, acct)
    target = {"post_id": pid, "board": day.isoformat(), "slot": slot.get("slot"), "outlet": OUTLET,
              "url": r.get("url") or ""}
    said: List[str] = []
    opts = options(slot)
    if pm != SW.OFF and opts and not _marker(r, "poll"):
        words = " / ".join(opts)
        if pm == "practice":
            OW.record(cfg, acct, "instagram", "poll", state="practice", mode="practice", outlet=OUTLET, feature=POLL,
                      key=f"poll:instagram:{pid}", text=f"would add the poll {words}", target=target,
                      why="poll is practice: the reel went without it", now=now)
            _note(cfg, acct, day, slot["slot"], "poll", {"state": "practice", "at": _utc(now)})
            said.append(f"poll practice: would add {words}")
        elif "poll_attachment" in sent:
            OW.record(cfg, acct, "instagram", "poll", state="done", mode="live", outlet=OUTLET, feature=POLL,
                      key=f"poll:instagram:{pid}", text=f"went with the poll {words}", target=target,
                      result={"id": pid, "url": r.get("url") or ""}, now=now)
            _note(cfg, acct, day, slot["slot"], "poll", {"state": "done", "at": _utc(now)})
            said.append(f"poll: went with {words}")
    tr = trial(slot)
    if tm != SW.OFF and tr and not _marker(r, "trial"):
        what = (f"experiment {tr.get('experiment') or '?'}, arm {tr.get('arm') or '?'}, "
                f"{tr.get('graduation_strategy') or '?'}")
        if tm == "practice":
            OW.record(cfg, acct, "instagram", "trial", state="practice", mode="practice", outlet=OUTLET,
                      feature=TRIAL, key=f"trial:instagram:{pid}", text=f"would post as a trial reel ({what})",
                      target=target, why="trial_reels is practice: it went as an ordinary reel", now=now)
            _note(cfg, acct, day, slot["slot"], "trial", {"state": "practice", "at": _utc(now)})
            said.append(f"trial practice: {what}")
        elif "trial_params" in sent:
            OW.record(cfg, acct, "instagram", "trial", state="done", mode="live", outlet=OUTLET, feature=TRIAL,
                      key=f"trial:instagram:{pid}", text=f"went as a trial reel ({what})", target=target,
                      result={"id": pid, "url": r.get("url") or ""}, now=now)
            _note(cfg, acct, day, slot["slot"], "trial", {"state": "done", "at": _utc(now)})
            said.append(f"trial reel: {what}")
    return "; ".join(said) or None


# ------------------------------------------------------------------------------------------------------ the card
def _mode_chip(m: str) -> str:
    cls = " on" if m == "on" else " warn" if m == "practice" else ""
    return f'<span class="fc-m{cls}">{esc(m)}</span>'


def card_html(cfg: C.Config, acct, day: date, s: dict, ready: bool) -> str:
    """The poll and trial rows of the card's First comment fold ('' while both switches are off and the clip has
    neither)."""
    if not has_reels(acct):
        return ""
    pm, tm = SW.mode(acct, POLL), SW.mode(acct, TRIAL)
    opts, tr = options(s), trial(s)
    out: List[str] = []
    a, k = esc(acct.slug), esc(s.get("slot") or "")
    q = question(s)
    warn = (f'<div class="fc-warn">Instagram shows your caption as the poll\'s question: {esc(q)}</div>' if q else
            '<div class="fc-warn">Instagram shows the caption as the poll\'s question.</div>')
    if pm != SW.OFF or opts:
        if ready:
            boxes = "".join(f'<input name="{f}" value="{esc(opts[i]) if i < len(opts) else ""}" '
                            f'data-w="" maxlength="{OPT_CHARS}" aria-label="Poll option {i + 1}" '
                            f'placeholder="Option {i + 1}{"" if i < MIN_OPTS else " (optional)"}" autocomplete="off">'
                            for i, f in enumerate(FIELDS))
            from . import firstcomment as FC
            ask_t = (f"Put this poll on the Instagram reel of {acct.name} {s.get('slot')}: {FC.WORDS}? It goes with "
                     f"the reel only after you approve the clip, and only while the poll switch is on.")
            ask_clear = f"Take the poll off the Instagram reel of {acct.name} {s.get('slot')}?"
            clear = (f'<button type="button" data-op="extra-clear" data-x="" data-acct="{a}" data-slot="{k}" '
                     f'data-ask="{esc(ask_clear)}">Clear</button>' if opts else "")
            out.append(f'<div class="fc-o fc-poll" data-x-form="">'
                       f'<div class="fc-l">Instagram poll {_mode_chip(pm)}</div>'
                       f'<input type="hidden" name="part" value="poll"><div class="fc-opts">{boxes}</div>{warn}'
                       f'<div class="fc-b"><button type="button" data-op="extra-poll" data-x="" data-acct="{a}" '
                       f'data-slot="{k}" data-ask="{esc(ask_t.replace(FC.WORDS, " / ".join(opts)))}" '
                       f'data-ask-t="{esc(ask_t)}">Save poll</button>{clear}</div></div>')
        else:
            if not opts:
                state = "none"
            elif pm == SW.OFF:
                state = "off: the reel goes without it"
            elif pm == "practice":
                state = "practice: the reel goes without it; the Log notes it"
            else:
                m = _marker((s.get("outlets") or {}).get(OUTLET) or {}, "poll")
                state = "went with the reel" if m.get("state") == "done" else "goes with the reel"
            body = (f'<div class="fc-txt">{esc(" / ".join(opts))}</div>{warn}' if opts else "")
            out.append(f'<div class="fc-o fc-poll"><div class="fc-l">Instagram poll {_mode_chip(pm)}</div>{body}'
                       f'<div class="fc-s">{esc(state)}</div></div>')
    if tm != SW.OFF or tr:
        if tr:
            what = (f"experiment {tr.get('experiment') or '?'}, arm {tr.get('arm') or '?'}, "
                    f"{tr.get('graduation_strategy') or '?'}")
        else:
            what = "none (only an experiment's arm makes a trial reel: the learner sets it)"
        btn = ""
        if ready and tr:
            ask = f"Post {acct.name} {s.get('slot')} as an ordinary reel, not a trial reel?"
            btn = (f'<div class="fc-b"><input type="hidden" name="part" value="trial"><button type="button" '
                   f'data-op="extra-clear" data-x="" data-acct="{a}" data-slot="{k}" data-ask="{esc(ask)}">'
                   f'Clear</button></div>')
        form = ' data-x-form=""' if btn else ""
        out.append(f'<div class="fc-o fc-trial"{form}>'
                   f'<div class="fc-l">Trial reel {_mode_chip(tm)}</div><div class="fc-s">{esc(what)}</div>{btn}</div>')
    return "".join(out)


# ------------------------------------------------------------------------------------------------------ doctor
def doctor_rows(cfg: C.Config, acct, now: datetime) -> List[Tuple[Optional[bool], str, str]]:
    """The poll and trial lines of one account that posts Instagram Reels by the API: off and what it waits for,
    practice, or on."""
    rows: List[Tuple[Optional[bool], str, str]] = []
    if not has_reels(acct):
        return rows
    slug = getattr(acct, "slug", "")
    waits = {POLL: f"a practice week and one verified test Reel first (factory.py social on {slug} poll --practice)",
             TRIAL: f"a signed experiment arm (learn.experiments), a practice week and one verified test Reel "
                    f"(factory.py social on {slug} trial_reels --practice)"}
    for key, label in ((POLL, "Instagram polls"), (TRIAL, "Instagram trial reels")):
        m = SW.mode(acct, key)
        if m == SW.OFF:
            rows.append((None, f"{acct.name} {label}: off; it waits for {waits[key]}", ""))
            continue
        if m == "practice":
            rows.append((None, f"{acct.name} {label}: practice (the reels go without them; the Log notes what they "
                               f"would have carried)", ""))
        else:
            rows.append((True, f"{acct.name} {label}: on (only on clips signed with them)", ""))
    return rows
