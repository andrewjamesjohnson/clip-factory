"""Clip Review's Reports tab: the learner's daily report, the week, proposals, experiments and the playbook.

    /reports?view=today|week|proposals|experiments|playbook[&acct=<slug>]    the page (Ideas sits beside them)
    /reports/data?view=...                                                  its JSON: {"view", "html", "stamp"},
                                                                            re-read every 60 seconds

Every view reads files only (learn.py and playbook.py write them); nothing here reaches a platform. The clicks are
Andrew's own and only from the page itself, each with a confirm that names the exact words and where they go:

    learn-approve {account, id}             a proposal goes into the account's playbook (or starts its experiment)
    learn-decline {account, id, reason, note}   with a reason chip; "Test it first" makes an experiment instead
    learn-revert {account, version}         the playbook as it was at that version
    learn-exp-sign {account, id}            start a draft experiment
    learn-exp-stop {account, id}            stop one
"""
from __future__ import annotations

import hashlib
import html
import json
from datetime import date, datetime
from typing import List, Optional, Tuple

from . import config as C
from . import learn as L
from . import playbook as PB
from . import socialui as SX
from . import store
from . import switches as SW
from . import verdicts as VD

VIEWS = (("today", "Today"), ("week", "Week"), ("proposals", "Proposals"), ("experiments", "Experiments"),
         ("playbook", "Playbook"))
VIEW_KEYS = tuple(k for k, _ in VIEWS)
OPS = ("learn-approve", "learn-decline", "learn-revert", "learn-exp-sign", "learn-exp-stop")
KIND_WORDS = {"factor": "Scout factor", "coach": "Coach", "runbook_note": "Runbook line", "advice": "Advice",
              "experiment": "Experiment", "draft_rule": "Draft automation", "revert": "Take back"}
ASSIGN_WORDS = {"slot_parity": "by the slot's place in the day", "day_parity": "by the day",
                "alternate": "taking turns", "trial_reel": "taking turns, B as an Instagram trial reel"}


def esc(s: object) -> str:
    return html.escape(str(s if s is not None else ""), quote=True)


def _view(q: dict) -> str:
    v = str((q or {}).get("view") or "today")
    return v if v in VIEW_KEYS else "today"


def _acct(q: dict) -> str:
    return str((q or {}).get("acct") or "all")


def _ow(o) -> str:
    return L.OUTLET_WORDS.get(o, str(o or ""))


def _day_words(d: str) -> str:
    try:
        x = date.fromisoformat(d)
    except (TypeError, ValueError):
        return str(d or "")
    return f"{L.DAY_NAMES[x.weekday()][:3]} {x:%m.%d}"


def seen(p: dict) -> str:
    """What the page showed of a proposal (its words, change and evidence): a click on a changed one is refused."""
    raw = json.dumps({k: p.get(k) for k in ("words", "key", "to", "evidence", "state")}, sort_keys=True, default=str)
    return hashlib.sha1(raw.encode()).hexdigest()[:12]


# ------------------------------------------------------------------------------------------------------ pieces
def _btn(label: str, op: str, account: str, ask: str = "", go: bool = False, **data) -> str:
    extra = "".join(f' data-{k}="{esc(v)}"' for k, v in data.items() if v is not None)
    ask_attr = f' data-ask="{esc(ask)}"' if ask else ""
    return (f'<button type="button"{" class=go" if go else ""} data-op="{esc(op)}" data-account="{esc(account)}"'
            f'{ask_attr}{extra}>{esc(label)}</button>')


def _kpi(k: str, v: str, d: str = "") -> str:
    return f'<div class="kpi"><div class="k">{esc(k)}</div><div class="v">{esc(v)}</div>' + (
        f'<div class="d">{esc(d)}</div>' if d else "") + "</div>"


def _empty(words: str) -> str:
    return f'<div class="sx-empty">{esc(words)}</div>'


def _head(a, sub: str) -> str:
    return f'<div class="sx-head"><h2>{esc(a.name)}</h2><span class="sx-muted">{esc(sub)}</span></div>'


def _latest_report(a, now: datetime, cfg: C.Config) -> dict:
    d = store.path(a.slug, "learn")
    files = sorted(d.glob("report-*.json")) if d.is_dir() else []
    for f in reversed(files):
        r = store.read(f, None)
        if isinstance(r, dict) and r.get("day"):
            return r
    return {}


def _switch_words(a, made_local: str = "") -> str:
    """When the report was made: "made at 04:41" while the 04:40 job is on; by hand otherwise."""
    at = f" at {made_local}" if made_local else ""
    if SW.mode(a, "learn") == "on":
        return f"made{at}" if at else "made by the 04:40 job"
    return f"the 04:40 report is off; this one was made by hand{at}"


# ------------------------------------------------------------------------------------------------------ today
def _problems(rep: dict) -> str:
    probs = L.shown_problems(rep)
    if not probs:
        return _empty("No problems found for the day.")
    items = []
    for p in probs[:10]:
        lost = L.lost_words(p)
        overlap = (" Not added into views lost: it is about how posts did, over more days than this one."
                   if p.get("overlaps") and p.get("views_lost") else "")
        which = L.which_words(p)
        items.append(f'<li class="rp-item"><div class="rp-top"><span class="rp-w">{esc(p["words"])}</span>'
                     f'<span class="rp-n">{esc(lost)}</span></div>'
                     f'<div class="rp-fix">Fix: {esc(p.get("fix"))}{esc(overlap)}</div>'
                     + (f'<div class="rp-sure">Which: {esc(which)}.</div>' if which else "")
                     + (f'<a class="rp-link" href="{esc(p["href"])}">Open</a>' if p.get("href") else "") + "</li>")
    return '<ol class="rp-list">' + "".join(items) + "</ol>"


def _posts(items: List[dict], none: str) -> str:
    if not items:
        return _empty(none)
    out = []
    for b in items:
        est = " (rough estimate)" if b.get("v24_src") == "estimate" else ""
        usual = f", the usual {L._int_words(b['baseline_24h'])}" if b.get("baseline_24h") is not None else ""
        link = (f'<a class="rp-w rp-ttl" href="{esc(b["url"])}" rel="noreferrer" target="_blank">'
                f'{esc(str(b.get("title") or "")[:110])}</a>' if str(b.get("url") or "").startswith("https://")
                else f'<span class="rp-w">{esc(str(b.get("title") or "")[:110])}</span>')
        chips = "".join(f'<span class="rp-tag">{esc(w)}</span>' for w in b.get("why") or [])
        out.append(f'<li class="rp-item">{link}<div class="rp-fix">{esc(_ow(b.get("outlet")))}: '
                   f'{esc(L._int_words(b.get("views_24h")))} at 24 hours{esc(est)}{esc(usual)}. '
                   f'{esc(_day_words(b.get("board")))} {esc(b.get("slot"))}</div>'
                   f'<div class="rp-tags">{chips}</div></li>')
    return '<ol class="rp-list">' + "".join(out) + "</ol>"


def _works(rep: dict) -> str:
    works = rep.get("works") or []
    if not works:
        need = ", ".join(f"{_ow(o)} {n} of {L.CLAIM_MIN}" for o, n in
                         sorted(((rep.get("kpis") or {}).get("posts_24h") or {}).items()))
        return _empty("Nothing stands out yet." + (f" Posts with a 24-hour number: {need}." if need else ""))
    out = []
    for w in works[:14]:
        conf = w.get("confidence") or ""
        cls = " ok" if conf in ("strong", "likely") else ""
        out.append(f'<li class="rp-item"><div class="rp-top"><span class="rp-w">'
                   f'{esc(L.upper_first(w["words"]))} on {esc(_ow(w["outlet"]))}</span>'
                   f'<span class="rp-n">{esc(L.x_words(w["lift"]))} the usual</span></div>'
                   f'<div class="rp-tags"><span class="rp-tag{cls}">{esc(L.CONF_WORDS.get(conf, conf))}</span></div>'
                   f'<div class="rp-sure">How sure: {esc(L.sure_words(w))}.</div></li>')
    note = [_ow(o) for o, ok in sorted((rep.get("claims") or {}).items()) if not ok]
    tail = (f'<p class="rp-sure">No claims yet for {esc(", ".join(note))}: fewer than {L.CLAIM_MIN} posts with a '
            f'24-hour number.</p>' if note else "")
    return '<ul class="rp-list">' + "".join(out) + "</ul>" + tail


def _today(cfg: C.Config, a, now: datetime) -> str:
    rep = _latest_report(a, now, cfg)
    if not rep:
        return (f'<section class="rp-acct">{_head(a, "no report yet")}'
                + _empty(f"No report yet. It is made at 04:40 once Reports is switched on for {a.name} "
                         f"(factory.py social on {a.slug} learn), or by hand: factory.py learn daily --account "
                         f"{a.slug}.") + "</section>")
    k = rep.get("kpis") or {}
    ch = k.get("change")
    ch_words = (f"{'up' if ch >= 0 else 'down'} {abs(ch) * 100:.0f}% on the day before" if ch is not None else "")
    rate = k.get("success_rate")
    sub = f"report for {_day_words(rep['day'])}, {_switch_words(a, str(rep.get('made_local') or ''))}"
    est = "".join(f'<p class="rp-sure">{esc(t)}</p>' for t in [L.estimate_words(rep)] + L.usual_lines(rep) if t)
    kpis = ('<div class="kpis rp-kpis">'
            + _kpi("Views on the day's posts", L.k_words(k.get("views_day")), ch_words)
            + _kpi("Posts out", str(k.get("posts_day", 0)), "every platform")
            + _kpi("Views lost", L.k_words(k.get("views_lost")), "estimated")
            + _kpi("Slots signed", f"{k.get('slots_signed', 0)} of {k.get('slots_planned', 0)}")
            + _kpi("Went through", f"{rate * 100:.0f}%" if rate is not None else "none due", "posts that went out")
            + _kpi("Open proposals", str(sum(1 for p in L.proposals(a).values() if p.get("state") == "open")))
            + "</div>")
    hl = rep.get("health") or {}
    vc = rep.get("verdicts") or {}
    reasons = ", ".join(f"{VD.LABELS.get(c, c)} {n}" for c, n in (vc.get("reasons") or {}).items())
    ca = rep.get("coach_accuracy") or {}
    win = rep.get("window") or {}
    span = f"posts that went up {_day_words(win.get('from'))} to {_day_words(win.get('to'))}"
    return (f'<section class="rp-acct" aria-label="{esc(a.name)}">{_head(a, sub)}{kpis}{est}'
            f'<div class="panel"><div class="sec-head"><div><h2>Biggest problems</h2><p>By views lost, with the fix'
            f'</p></div></div>{_problems(rep)}</div>'
            f'<div class="two rp-two"><div class="panel"><div class="sec-head"><div><h2>Best posts</h2>'
            f'<p>{esc(span)}, against the usual</p></div></div>'
            + (f'<p class="rp-sure">{esc(L.left_out_words(rep))}</p>' if L.left_out_words(rep) else "")
            + f'{_posts(rep.get("best") or [], L.none_words(rep, True))}</div>'
            f'<div class="panel"><div class="sec-head"><div><h2>Worst posts</h2><p>{esc(span)}</p></div></div>'
            f'{_posts(rep.get("worst") or [], L.none_words(rep, False))}</div></div>'
            f'<div class="panel" id="works"><div class="sec-head"><div><h2>What works</h2><p>Each platform on its '
            f'own; strong and likely can be proposed, an early sign is only shown</p></div></div>{_works(rep)}</div>'
            f'<div class="three rp-three">'
            f'<div class="panel"><h3>Coach</h3><p class="rp-fix">{esc(ca.get("words", ""))}</p></div>'
            f'<div class="panel"><h3>Publishing</h3><p class="rp-fix">{esc(hl.get("words", ""))}.</p></div>'
            f'<div class="panel"><h3>Decisions, 7 days</h3><p class="rp-fix">Approved {vc.get("approve", 0)}, '
            f'declined {vc.get("decline", 0)}, made again {vc.get("remake", 0)}'
            f'{esc(": " + reasons) if reasons else ""}.</p></div></div></section>')


# ------------------------------------------------------------------------------------------------------ week
def _week(cfg: C.Config, a, now: datetime) -> str:
    w = L.week(cfg, a, now=now)
    sub = f"week {w['week']}, {_day_words(w['from'])} to {_day_words(w['to'])}, {w['reports']} daily reports"
    if not w["reports"] and not w["posts"]:
        return (f'<section class="rp-acct">{_head(a, sub)}'
                + _empty("No reports this week yet.") + "</section>")
    kpis = ('<div class="kpis rp-kpis">' + _kpi("Posts", str(w["posts"]), "every platform")
            + _kpi("Views so far", L.k_words(w["views"])) + _kpi("Views at 24 hours", L.k_words(w["views_24h"]))
            + _kpi("Views lost", L.k_words(sum(p["views_lost"] for p in w["problems"] if not p.get("overlaps"))),
                   "estimated") + _kpi("Open proposals", str(w["proposals"]["open"]))
            + _kpi("Playbook", f"version {w['playbook']['version']}", f"{w['playbook']['rules']} rules") + "</div>")
    probs = "".join(f'<li class="rp-item"><div class="rp-top"><span class="rp-w">{esc(p["words"])}</span>'
                    f'<span class="rp-n">about {esc(L._int_words(p["views_lost"]))} views lost</span></div>'
                    f'<div class="rp-fix">On {p["days"]} day{"" if p["days"] == 1 else "s"}. Fix: {esc(p.get("fix"))}'
                    f'</div></li>' for p in w["problems"][:8])
    reasons = "".join(f'<span class="rp-tag">{esc(VD.LABELS.get(VD.code_of(c), c))} {n}</span>'
                      for c, n in (w["verdicts"].get("reasons") or {}).items())
    wk = L.weekly_dir(a)
    files = sorted(wk.glob("*.md")) if wk.is_dir() else []
    written = ""
    if files:
        try:
            text = files[-1].read_text()[:40000]
        except OSError:
            text = ""
        written = (f'<div class="panel"><div class="sec-head"><div><h2>The weekly written report</h2><p>'
                   f'{esc(files[-1].stem)}</p></div></div><div class="rp-md">{esc(text)}</div></div>')
    return (f'<section class="rp-acct" aria-label="{esc(a.name)}">{_head(a, sub)}{kpis}'
            f'<div class="panel"><div class="sec-head"><div><h2>Problems this week</h2><p>Added up over the daily '
            f'reports</p></div></div>' + (f'<ol class="rp-list">{probs}</ol>' if probs else _empty("None."))
            + '</div><div class="two rp-two"><div class="panel"><div class="sec-head"><div><h2>Best posts</h2></div>'
            f'</div>{_posts(w["best"], "None yet.")}</div><div class="panel"><div class="sec-head"><div><h2>'
            f'Worst posts</h2></div></div>{_posts(w["worst"], "None yet.")}</div></div>'
            f'<div class="panel"><h3>Why clips were declined</h3><div class="rp-tags">'
            f'{reasons or esc("No reasons given this week.")}</div></div>{written}</section>')


# ------------------------------------------------------------------------------------------------------ proposals
def _evidence_words(p: dict) -> str:
    ev = p.get("evidence") or {}
    if ev.get("metric") == "unanswered":
        return f"asked {ev.get('n')} times in {ev.get('window', '7 days')} with no answer"
    if not ev.get("n"):
        return str(ev.get("metric") or "")
    w = {"n": ev.get("n"), "n_rest": ev.get("n_rest"), "ci90": ev.get("ci90"), "split_half": ev.get("split_half")}
    conf = ev.get("confidence")
    return (f"{L.CONF_WORDS.get(conf, conf) + ': ' if conf else ''}{L.sure_words(w)}"
            + (f", {ev.get('window')}" if ev.get("window") else ""))


def _backtest_words(bt) -> str:
    if not isinstance(bt, dict):
        return ""
    if not bt.get("boards"):
        return "No saved scout boards to try it on" + (f" ({bt['why']})" if bt.get("why") else "") + "."
    ex = "; ".join(str(x) for x in bt.get("examples") or [])
    return (f"On the last {bt['boards']} scout boards: {bt.get('into_top10', 0)} finds move into the top 10 and "
            f"{bt.get('out_of_top10', 0)} move out." + (f" Into it: {ex}." if ex else ""))


def _list(items: List[str]) -> str:
    return "; ".join(items) if items else "nothing"


def _off_words(a) -> str:
    return "" if SW.mode(a, "learn.playbook") == "on" else " once the playbook switch is on (it is off now)"


def _where(a, p: dict, version: int) -> str:
    """Where an approved proposal goes, true to what approve does."""
    kind = p.get("kind")
    if kind in ("factor", "coach", "runbook_note"):
        reader = {"factor": "the scout", "coach": "the coach",
                  "runbook_note": "the Claude runbooks (learn brief)"}[kind]
        return (f"It goes into {C.possessive(a.name)} playbook as version {version + 1} for 60 days, read by {reader}"
                f"{_off_words(a)}.")
    if kind == "draft_rule":
        return (f"It is kept in {C.possessive(a.name)} playbook as version {version + 1} for 60 days as a request for "
                f"an automation; {L.draft_words(a)}.")
    if kind == "experiment":
        mode = SW.mode(a, "learn.experiments")
        tail = ("" if mode == "on" else
                " The experiments switch is practice: arms are only written down until it is on." if mode == "practice"
                else " The experiments switch is off: no clip gets an arm until it is on.")
        return f"It starts the experiment on {a.name}, signed by you.{tail}"
    if kind == "revert":
        rid = L.take_back_rule(p)
        rules = PB.read(a)["rules"]
        gone = [PB.rule_words(r) for r in rules if str(r.get("id")) == rid]
        keep = [PB.rule_words(r) for r in rules if str(r.get("id")) != rid]
        if not gone:
            return "That rule is no longer in the playbook: approving it changes nothing (decline it)."
        return (f"{C.possessive(a.name)} playbook becomes version {version + 1}. Takes out: {_list(gone)}. "
                f"Keeps: {_list(keep)}.")
    return "Advice: approving it notes it; nothing changes by itself."


def _proposal(a, p: dict, version: int) -> str:
    words = str(p.get("words") or "")
    kind = KIND_WORDS.get(p.get("kind"), p.get("kind"))
    where = _where(a, p, version)
    ends = str(p.get("expires") or "")[:10]
    bt = _backtest_words(p.get("backtest"))
    ask_ok = f'Approve for {a.name}: "{words}". {where}'
    ask_no = f'Decline for {a.name}: "{words}".'
    return (f'<li class="rp-item rp-prop" data-id="{esc(p["id"])}">'
            f'<div class="rp-tags"><span class="rp-tag">{esc(kind)}</span>'
            + (f'<span class="rp-tag">{esc(p.get("risk") == "andrew_only" and "Only you decide" or "Safe")}</span>')
            + f'<span class="sx-muted">ends {esc(ends)}</span></div>'
            f'<div class="rp-w">{esc(words)}</div>'
            f'<div class="rp-sure">Evidence: {esc(_evidence_words(p))}.</div>'
            + (f'<div class="rp-sure">{esc(bt)}</div>' if bt else "")
            + f'<div class="rp-fix">{esc(where)}</div>'
            f'<div class="rp-btns" data-x-form>'
            + _btn("Approve", "learn-approve", a.slug, ask_ok, go=True, id=p["id"], seen=seen(p))
            + _btn("Decline", "learn-decline-open", a.slug, ask_no, id=p["id"], seen=seen(p),
                   testable="1" if L.experiment_for(p) is not None else "0")
            + "</div></li>")


def _proposals(cfg: C.Config, a, now: datetime) -> str:
    props = L.proposals(a)
    version = PB.read(a)["version"]
    open_ = sorted((p for p in props.values() if p.get("state") == "open"), key=lambda p: str(p.get("made")),
                   reverse=True)
    done = sorted((p for p in props.values() if p.get("state") != "open"),
                  key=lambda p: str((p.get("decided") or {}).get("at") or p.get("made")), reverse=True)[:15]
    sub = f"{len(open_)} open; approved ones go into the playbook (version {version} now)"
    body = ('<ul class="rp-list">' + "".join(_proposal(a, p, version) for p in open_) + "</ul>") if open_ else \
        _empty("Nothing to decide. Proposals come from the daily report when something is strong or likely.")
    past = "".join(f'<li class="rp-item"><div class="rp-top"><span>{esc(p.get("words"))}</span><span class="rp-n">'
                   f'{esc(p.get("state"))}</span></div><div class="rp-sure">'
                   + esc(" ".join(x for x in (
                       (p.get("decided") or {}).get("by", ""),
                       str((p.get("decided") or {}).get("at") or "")[:10],
                       L.DECLINE_WORDS.get((p.get("decided") or {}).get("reason") or "", ""),
                       (p.get("decided") or {}).get("why") or "") if x))
                   + "</div></li>" for p in done)
    return (f'<section class="rp-acct" aria-label="{esc(a.name)}">{_head(a, sub)}<div class="panel">{body}</div>'
            + (f'<div class="panel"><h3>Decided</h3><ul class="rp-list">{past}</ul></div>' if past else "")
            + "</section>")


# ------------------------------------------------------------------------------------------------------ experiments
def _exp_waits(a, e: dict, mode: str) -> str:
    """What keeps an experiment from changing clips now ('' when nothing does)."""
    if e.get("kind") not in L.EXP_KINDS:
        return "A label only: arm B changes nothing about the clip, so it never names a winner."
    if mode == "off":
        return "The experiments switch is off: no clip gets an arm until it is on."
    if mode == "practice":
        return "The experiments switch is practice: arms are written down, the clips do not change and do not count."
    if e.get("kind") == "trial_reel" and SW.mode(a, "trial_reels") != "on":
        return ("It waits for the trial reels switch: until that is on, the Instagram post goes as a normal reel and "
                "the clip is only written down.")
    return ""


def _experiments(cfg: C.Config, a, now: datetime) -> str:
    exps = L.experiments(a)
    mode = SW.mode(a, "learn.experiments")
    sub = f"the experiments switch is {mode}"
    note = ("" if mode == "on" else
            f'<p class="rp-sure">{"Practice: arms are written down, the clips are not changed." if mode == "practice" else f"Off: no clip gets an arm (factory.py social on {a.slug} learn.experiments --practice)."}</p>')
    if not exps:
        return (f'<section class="rp-acct">{_head(a, sub)}{note}'
                + _empty("No experiments. Press Test it first on a proposal to make one.") + "</section>")
    items = []
    for eid, e in sorted(exps.items(), reverse=True):
        arms = e.get("arms") or {}
        counts = {arm: len((e.get("posts") or {}).get(arm) or []) for arm in ("A", "B")}
        done = e.get("counts") or {}
        res = (e.get("result") or {}).get("words")
        waits = _exp_waits(a, e, mode)
        btns = ""
        if e.get("state") == "draft":
            how = ("signed with each clip" if not waits else "written down only")
            btns = _btn("Start it", "learn-exp-sign", a.slug,
                        f"Start experiment {eid} on {a.name}: {e.get('question')} New clips get arm A "
                        f"({arms.get('A')}) or B ({arms.get('B')}) {ASSIGN_WORDS.get(e.get('assign'), '')}, {how}."
                        + (f" {waits}" if waits else ""), go=True, id=eid)
        elif e.get("state") == "running":
            btns = _btn("Stop it", "learn-exp-stop", a.slug,
                        f"Stop experiment {eid} on {a.name}? Clips collected from now on get no arm.", id=eid)
        items.append(f'<li class="rp-item"><div class="rp-top"><span class="rp-w">{esc(eid)}: '
                     f'{esc(e.get("question"))}</span><span class="rp-n">{esc(e.get("state"))}</span></div>'
                     f'<div class="rp-fix">A: {esc(arms.get("A"))}. B: {esc(arms.get("B"))}. Assigned '
                     f'{esc(ASSIGN_WORDS.get(e.get("assign"), e.get("assign")))}; {counts["A"]} and {counts["B"]} '
                     f'clips so far, {int(done.get("A") or 0)} and {int(done.get("B") or 0)} counted (changed by '
                     f'the test, with a 24-hour number); ends at {e.get("min_n", 10)} counted clips an arm or '
                     f'{e.get("max_days", 21)} days.</div>'
                     + (f'<div class="rp-sure">{esc(waits)}</div>' if waits and e.get("state") in ("draft", "running")
                        else "")
                     + (f'<div class="rp-sure">{esc(res)}</div>' if res else "")
                     + (f'<div class="rp-btns" data-x-form>{btns}</div>' if btns else "") + "</li>")
    return (f'<section class="rp-acct" aria-label="{esc(a.name)}">{_head(a, sub)}{note}<div class="panel">'
            f'<ul class="rp-list">{"".join(items)}</ul></div></section>')


# ------------------------------------------------------------------------------------------------------ playbook
def _hist_words(h: dict) -> str:
    """A history line in plain words: what that version changed."""
    what, key = h.get("what"), str(h.get("key") or "")
    if what == "apply":
        snap = (h.get("playbook") or {}).get("rules") if isinstance(h.get("playbook"), dict) else None
        r = next((r for r in snap or [] if isinstance(r, dict) and r.get("key") == key), None)
        return PB.rule_words(r) if r else PB.words_of(key, h.get("to"))
    if what == "take back":
        return f"took back {PB.words_of(key, h.get('from'))}"
    if what == "revert":
        return f"back to version {h.get('to')}"
    return f"{what or 'changed'}"


def _revert_ask(a, v: int, current: int) -> str:
    ch = PB.revert_change(a, v)
    reads = ("the scout, the coach and the runbooks read it from their next run" if SW.mode(a, "learn.playbook") == "on"
             else "nothing reads it while the playbook switch is off")
    return (f"Make {C.possessive(a.name)} playbook version {v} again? It becomes version {current + 1}. "
            f"Takes out: {_list(ch['out'])}. Brings back: {_list(ch['back'])}. Keeps: {_list(ch['keep'])}. "
            f"Then {reads}.")


def _playbook(cfg: C.Config, a, now: datetime) -> str:
    pb = PB.read(a)
    on = SW.mode(a, "learn.playbook") == "on"
    sub = (f"version {pb['version']}, " + ("read by the scout, the coach and the runbooks" if on else
                                          "switched off: the scout, the coach and the runbooks read none of it"))
    did = L.did_it_work_of(a)
    rules = []
    for r in pb["rules"]:
        d = did.get(str(r.get("id") or ""))
        check = (f'. Did it work: {d.get("verdict")}' + ("" if d.get("verdict") in (L.NOT_MEASURABLE, L.NOT_IN_FORCE)
                                                         else " (weak evidence)") if d else "")
        rules.append(f'<li class="rp-item"><div class="rp-top"><span class="rp-w">'
                     f'{esc(L.upper_first(PB.rule_words(r)))}</span><span class="rp-n">until '
                     f'{esc(str(r.get("expires"))[:10])}</span></div><div class="rp-sure">Approved by '
                     f'{esc(r.get("by"))} {esc(str(r.get("approved"))[:10])}{esc(check)}</div></li>')
    hist = PB.history(a)[-15:]
    rows = []
    for h in reversed(hist):
        v = h.get("version")
        btn = ""
        if isinstance(v, int) and v < pb["version"]:
            btn = _btn(f"Back to version {v}", "learn-revert", a.slug, _revert_ask(a, v, pb["version"]), version=v)
        rows.append(f'<li class="rp-item"><div class="rp-top"><span>Version {esc(v)}: '
                    f'{esc(_hist_words(h))}</span><span class="rp-n">{esc(str(h.get("at"))[:10])}</span></div>'
                    f'<div class="rp-sure">by {esc(h.get("by"))}</div>'
                    + (f'<div class="rp-btns" data-x-form>{btn}</div>' if btn else "") + "</li>")
    if pb["version"] > 0:
        rows.append('<li class="rp-item"><div class="rp-top"><span>Version 0: the empty playbook</span></div>'
                    '<div class="rp-btns" data-x-form>'
                    + _btn("Back to version 0", "learn-revert", a.slug, _revert_ask(a, 0, pb["version"]), version=0)
                    + "</div></li>")
    return (f'<section class="rp-acct" aria-label="{esc(a.name)}">{_head(a, sub)}'
            f'<div class="panel"><h3>In force</h3>' if on else
            f'<section class="rp-acct" aria-label="{esc(a.name)}">{_head(a, sub)}'
            f'<div class="panel"><h3>Approved, waiting for the playbook switch</h3>') + (
            (f'<ul class="rp-list">{"".join(rules)}</ul>' if rules else _empty("Nothing approved yet."))
            + '</div><div class="panel"><h3>For the runbooks</h3><div class="rp-md">'
            f'{esc(PB.brief(a, now))}</div></div>'
            + (f'<div class="panel"><h3>History</h3><ul class="rp-list">{"".join(rows)}</ul></div>' if rows else "")
            + "</section>")


RENDER = {"today": _today, "week": _week, "proposals": _proposals, "experiments": _experiments,
          "playbook": _playbook}


def render(cfg: C.Config, view: str, acct: str = "all", now: Optional[datetime] = None) -> str:
    now = now or cfg.now()
    accts = SX.own_accounts(cfg, acct)
    if not accts:
        return _empty("No account to show here.")
    out = []
    for a in accts:
        try:
            out.append(RENDER[view](cfg, a, now))
        except Exception as e:  # noqa: BLE001  (one account's broken file never blanks the tab)
            out.append(f'<section class="rp-acct">{_head(a, "")}'
                       + _empty(f"Could not draw this ({type(e).__name__}: {e}).") + "</section>")
    return "".join(out)


# ------------------------------------------------------------------------------------------------------ the page
CSS = """
.rp-acct{display:flex;flex-direction:column;gap:16px;margin-bottom:28px;min-width:0}
.rp-kpis{grid-template-columns:repeat(6,minmax(0,1fr))}
.rp-list{list-style:none;margin:0;padding:0;display:flex;flex-direction:column;min-width:0}
.rp-item{padding:10px 0;border-bottom:1px solid var(--line);display:flex;flex-direction:column;gap:5px;min-width:0}
.rp-item:first-child{padding-top:0}.rp-item:last-child{border-bottom:0;padding-bottom:0}
.rp-top{display:flex;gap:10px;align-items:baseline;justify-content:space-between;min-width:0}
.rp-w{font-weight:600;overflow-wrap:anywhere;min-width:0}
a.rp-ttl{color:var(--ink);text-decoration:none}a.rp-ttl:hover{color:var(--blue);text-decoration:underline}
.rp-n{font-weight:650;font-variant-numeric:tabular-nums;white-space:nowrap;color:var(--ink)}
.rp-fix{color:var(--sub);font-size:13px;overflow-wrap:anywhere;margin:0}
.rp-sure{color:var(--sub);font-size:12px;overflow-wrap:anywhere;margin:0}
.rp-link{font-size:13px;color:var(--blue);text-decoration:none;align-self:flex-start}
.rp-link:hover{text-decoration:underline}
.rp-btns{display:flex;gap:6px;flex-wrap:wrap;margin-top:2px}
.rp-btns button,.rp-why button{height:32px;padding:0 12px;border:1px solid var(--line);border-radius:6px;background:var(--card);color:var(--ink);font:inherit;font-size:13px;font-weight:550;cursor:pointer}
.rp-btns button:hover,.rp-why button:hover{border-color:var(--sub)}
.rp-btns button.go,.rp-why button.go{background:var(--ink);color:var(--bg);border-color:var(--ink)}
.rp-btns button:disabled,.rp-why button:disabled{opacity:.5;cursor:default}
.rp-why{display:flex;flex-direction:column;gap:8px;padding:10px;border:1px solid var(--line);border-radius:6px;background:var(--bg)}
.rp-why .sx-chip{cursor:pointer;height:32px}
.rp-why input{font:inherit;font-size:13px;height:32px;padding:0 8px;border:1px solid var(--line);border-radius:6px;background:var(--card);color:var(--ink);width:100%}
.rp-tags{display:flex;flex-wrap:wrap;gap:5px;align-items:center}
.rp-tag{display:inline-flex;align-items:center;height:22px;padding:0 7px;border-radius:4px;background:var(--mute);color:var(--sub);font-size:12px;font-weight:550;white-space:nowrap;max-width:100%;overflow:hidden;text-overflow:ellipsis}
.rp-tag.ok{color:var(--ok);background:var(--okbg)}
.rp-md{white-space:pre-wrap;overflow-wrap:anywhere;font-size:13px;line-height:1.5;color:var(--ink)}
@media (max-width:1180px){.rp-kpis{grid-template-columns:repeat(3,minmax(0,1fr))}}
@media (max-width:600px){.rp-kpis{grid-template-columns:repeat(2,minmax(0,1fr))}
.rp-top{flex-direction:column;align-items:flex-start;gap:2px}.rp-n{white-space:normal}
.rp-btns button,.rp-why button{height:40px;min-width:40px;flex:1 1 auto;font-size:16px}
.rp-why .sx-chip{height:40px}.rp-why input{height:40px;font-size:16px}
.rp-link{min-height:40px;display:inline-flex;align-items:center}
a.rp-ttl{display:flex;align-items:center;min-height:40px}}
"""

JS = r"""
(() => {
'use strict';
const DECLINE = __DECLINE__;
const main = document.getElementById('rp-main');
if (!main) return;
function busy() {
  const a = document.activeElement;
  return !!main.querySelector('.rp-why') || (a && main.contains(a) && (a.tagName === 'INPUT' || a.tagName === 'TEXTAREA'));
}
const poller = SX.poll(() => '/reports/data' + location.search, 60000, d => {
  if (d && typeof d.html === 'string' && !busy()) main.innerHTML = d.html;
});
function chooser(b) {
  const old = main.querySelector('.rp-why'); if (old) old.remove();
  const box = SX.el('div', {class: 'rp-why', 'data-x-form': ''});
  const chips = SX.el('div', {class: 'sx-chips'});
  let pick = '';
  const reasons = DECLINE.filter(([code]) => code !== 'test_first' || b.dataset.testable === '1');
  const draw = () => chips.replaceChildren(...reasons.map(([code, label]) => SX.el('button', {type: 'button',
    class: 'sx-chip', 'aria-pressed': String(code === pick), onclick: () => { pick = code; draw(); }}, label)));
  draw();
  const note = SX.el('input', {type: 'text', maxlength: '300', placeholder: 'A note (optional)', 'aria-label': 'Note'});
  const go = SX.el('button', {type: 'button', class: 'go', onclick: async () => {
    if (!pick) { SX.toast('Pick a reason first.', true); return; }
    const label = (DECLINE.find(x => x[0] === pick) || ['', pick])[1];
    const extra = pick === 'test_first' ? ' An experiment is made from it instead; you start it on Experiments.' : '';
    if (!SX.ask(b.dataset.ask + ' Reason: ' + label + '.' + extra)) return;
    const res = await SX.act({op: 'learn-decline', account: b.dataset.account, id: b.dataset.id, seen: b.dataset.seen,
      reason: pick, note: note.value}, go);
    if (res.ok) { box.remove(); poller.now(); }
  }}, 'Decline');
  const cancel = SX.el('button', {type: 'button', onclick: () => box.remove()}, 'Cancel');
  const why = b.dataset.testable === '1' ? 'Why decline it?' :
    'Why decline it? (Test it first is not offered: this change cannot be split clip by clip.)';
  box.append(SX.el('div', {class: 'rp-w'}, why), chips, note, SX.el('div', {class: 'rp-btns'}, [go, cancel]));
  b.closest('.rp-item').append(box);
  note.focus();
}
main.addEventListener('click', async ev => {
  const b = ev.target.closest('button[data-op]');
  if (!b || !main.contains(b)) return;
  const op = b.dataset.op;
  if (op === 'learn-decline-open') { chooser(b); return; }
  const body = {op, account: b.dataset.account};
  if (b.dataset.id) body.id = b.dataset.id;
  if (b.dataset.seen) body.seen = b.dataset.seen;
  if (b.dataset.version != null) body.version = Number(b.dataset.version);
  if (!SX.ask(b.dataset.ask)) return;
  const res = await SX.act(body, b);
  if (res.ok) poller.now();
});
})();
"""


def page(cfg: C.Config, tok: str, base_css: str, tabs_html: str, switch_html: str, acct: str, q: dict) -> str:
    view = _view(q)
    own = [(k, f"/reports?view={k}", label) for k, label in VIEWS]
    nav = SX.subnav("reports", view, acct, own)
    main = f'<div id="rp-main" data-view="{esc(view)}">{render(cfg, view, acct)}</div>'
    js = JS.replace("__DECLINE__", json.dumps([list(x) for x in L.DECLINE]).replace("</", "<\\/"))
    return SX.shell(cfg, "reports", "Reports", tok, base_css, tabs_html, switch_html, acct, main, css=CSS, js=js,
                    subnav=nav)


def data(cfg: C.Config, q: dict, now: Optional[datetime] = None) -> dict:
    view = _view(q)
    return SX.stamp({"view": view, "html": render(cfg, view, _acct(q), now)})


# ------------------------------------------------------------------------------------------------------ clicks
def _account(cfg: C.Config, body: dict):
    slug = str(body.get("account") or "")
    mine = {a.slug: a for a in SX.own_accounts(cfg, "all")}
    if slug not in mine:
        raise SystemExit(f"{slug or 'that account'}: not one of the accounts you sign here")
    return mine[slug]


def act(cfg: C.Config, body: dict, page: bool = False, now: Optional[datetime] = None) -> Tuple[bool, str]:
    """The Reports tab's clicks. Each one is Andrew's own: only from the page itself (with its confirm)."""
    op = str(body.get("op") or "")
    if op not in OPS:
        raise SystemExit(f"{op or 'that'}: not a Reports click")
    SX.need_page(page, {"learn-approve": "approve a proposal", "learn-decline": "decline a proposal",
                        "learn-revert": "change the playbook", "learn-exp-sign": "start an experiment",
                        "learn-exp-stop": "stop an experiment"}[op])
    a = _account(cfg, body)
    by = str(body.get("by") or (cfg.approvers[0] if cfg.approvers else ""))
    if op in ("learn-approve", "learn-decline"):
        pid = str(body.get("id") or "")
        p = L.proposals(a).get(pid)
        if p is None:
            raise SystemExit(f"{a.name}: no proposal {pid or ''}".rstrip())
        if body.get("seen") and body["seen"] != seen(p):
            raise SystemExit("That proposal changed since the page showed it: look again, then decide.")
        if op == "learn-approve":
            return True, L.approve(cfg, a, pid, by, now)
        return True, L.decline(cfg, a, pid, by, str(body.get("reason") or ""), str(body.get("note") or "")[:300], now)
    if op == "learn-revert":
        v = body.get("version")
        if isinstance(v, bool) or not isinstance(v, (int, float)) or int(v) != v:
            raise SystemExit("revert: a version number")
        return True, PB.revert(cfg, a, int(v), by, now)
    eid = str(body.get("id") or "")
    if op == "learn-exp-sign":
        return True, L.sign_experiment(cfg, a, eid, by, now)
    return True, L.stop_experiment(cfg, a, eid, by, now)
