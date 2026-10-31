"""Clip Review, Inbox, Automations (/inbox/rules): the rules that answer what people ask, and everything about them.

The list: Rule, Account, Mode, Runs 24h, Sent, Would send, Skipped, Failed, Last run, Signed (or "Changed after
signing"). The editor: name, platforms, trigger (comments or messages, any post or listed ones), intent chips,
keywords, exclusions, the ways of saying each reply with counters (Instagram messages in bytes), the limits (never
above the hard caps), then Save, Sign (its confirm shows the whole rule), the mode (Practice, Testers, Live, Paused),
Duplicate to another account (as a draft) and Delete (drafts only). "Test on the last 7 days" runs the rule over the
stored comments of the week: what it would have sent, to whom, when, and why anything was held. "Try it" takes a
typed comment: the rule it matches, the exact words, what the caps or windows would do right now.

Every click needs the page (a browser request from this page) and a confirm that names the words and where they go.
Try it and the replay write nothing and reach no platform. The page reads files only (rules.json, the runs, the
outward log); it polls /inbox/rules/data every 15 seconds and redraws only what changed.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Tuple

from . import answers as AN
from . import config as C
from . import rules as RU
from . import socialui as SX
from . import switches as SW

TITLE = "Automations"


# ------------------------------------------------------------------------------------------------------ data
def _accounts(cfg: C.Config, acct: str) -> List[C.Account]:
    return [a for a in SX.own_accounts(cfg, acct or "all") if a.slug in cfg.accounts]


def _comments(a, since: datetime) -> Dict[str, str]:
    try:
        from . import socialstore as SS
        return {str(i.get("id")): str(i.get("text") or "")[:280] for i in SS.items(a, since=since)}
    except Exception:  # noqa: BLE001
        return {}


def _rule_view(cfg: C.Config, a, r: dict, stats: dict, now: datetime) -> dict:
    st = RU.signed_state(r)
    out = {k: r.get(k) for k in ("id", "name", "platforms", "trigger", "action", "limits", "mode", "version",
                                 "practice_since", "flags", "starter", "signoff", "created_at", "updated_at",
                                 "copied_from")}
    out.update(hash=RU.rule_hash(r), signed=st, practiced_h=int(RU._practised_h(r, now)),
               stats=stats or {}, sign_words=RU.sign_words(a, r), warnings=RU.warnings(a, r),
               problems=RU.problems(a, r),
               live_problems={m: RU.live_problems(cfg, a, r, m, now) for m in ("testers", "live")},
               ever_signed=bool(r.get("signoff")) or any(isinstance(h, dict) and h.get("to") in ("testers", "live")
                                                          for h in r.get("history") or []))
    return out


def data(cfg: C.Config, q: dict, now: Optional[datetime] = None) -> dict:
    """Everything the page draws, from files only (no network), ending with its stamp."""
    now = RU._now(now)
    q = q if isinstance(q, dict) else {}
    accts = _accounts(cfg, str(q.get("acct") or "all"))
    out_accts = []
    for a in accts:
        try:
            rl = RU.rules(a)
            stats = RU.analytics(cfg, a, now)
            texts = _comments(a, now - timedelta(days=35))
            from . import socialstore as SS
            runs = list(reversed(SS.runs(a, since=now - timedelta(days=14))))[:80]
            for r in runs:
                r["comment"] = texts.get(str(r.get("item") or ""), "")
            seeded = isinstance((SS.read(a, "rules", {}) or {}).get("seeded"), dict)
            s = SW.settings(a)
            wu = SW.warmup_until(cfg, a)
            out_accts.append({
                "slug": a.slug, "name": a.name, "maker": a.maker, "mode": SW.mode(a, "automations"),
                "checklist": [[bool(d), w] for d, w in SW.checklist(cfg, a, "automations", now)],
                "warmup": (f"Automated replies practise until {wu} (the warm-up)"
                           if wu and wu > now.astimezone(cfg.tz()).date() else "The warm-up is done" if wu else
                           "The warm-up waits for the account's creation date and the factory's first post"),
                "messages_access": s.get("messages_access"), "testers": len(s.get("testers") or []),
                "seeded": seeded, "starters": [r["name"] for r in RU.starters(a)] if not seeded else [],
                "rules": [_rule_view(cfg, a, r, stats.get(r["id"]) or {}, now) for r in rl],
                "runs": runs, "posts": RU.recent_posts(cfg, a, now=now)[:30]})
        except Exception as e:  # noqa: BLE001  (one account's broken file never blanks the page)
            out_accts.append({"slug": a.slug, "name": a.name, "maker": a.maker, "error": f"{type(e).__name__}: {e}",
                              "rules": [], "runs": [], "posts": [], "checklist": [], "mode": "off"})
    caps = {k: list(RU.ceilings({"trigger": {"on": "message" if k == "dm" else "comment"},
                                 "action": {"public": {"on": k == "public"}, "private": {"on": k != "public"}}}))
            for k in ("public", "private", "dm")}
    body = SX.stamp({"accounts": out_accts, "intents": [[k, v] for k, v in RU.INTENT_WORDS.items()],
                     "placeholders": [[k, v] for k, v in AN.PLACEHOLDERS.items()],
                     "all": [{"slug": a.slug, "name": a.name} for a in _accounts(cfg, "all")],
                     "caps": caps, "limits": {"public_max": RU.PUBLIC_MAX, "private_max": RU.PRIVATE_MAX_BYTES -
                                                                                         len(RU.DISCLOSURE)},
                     "defaults": RU.DEFAULT_LIMITS, "max_age_h": RU.DEFAULT_MAX_AGE_H,
                     "skip_words": RU.SKIP_WORDS, "part2_words": [list(RU.PART2_PRIVATE), list(RU.PART2_PUBLIC)],
                     "plat_words": RU.PLAT_WORDS})
    body["now"] = RU._iso(now)          # outside the stamp: the page redraws only when something changed
    return body


# ------------------------------------------------------------------------------------------------------ clicks
def _acct(cfg: C.Config, slug) -> C.Account:
    slug = str(slug or "")
    for a in _accounts(cfg, "all"):
        if a.slug == slug:
            return a
    raise SystemExit(f"{slug or 'that account'}: not one of your accounts here")


def act(cfg: C.Config, body: dict, page: bool = False, now: Optional[datetime] = None) -> Tuple[bool, str]:
    """rule-save | rule-sign | rule-mode | rule-dup | rule-delete | rule-try | rule-replay | rule-seed. Every one is
    a click on this page; Try it and the replay answer with their result as JSON and change nothing."""
    op = str(body.get("op") or "")
    SX.need_page(page, "do that")
    by = str(body.get("by") or (cfg.approvers[0] if cfg.approvers else "Andrew"))
    a = _acct(cfg, body.get("account"))
    rid = str(body.get("id") or "")
    if op == "rule-save":
        rule = body.get("rule")
        if not isinstance(rule, dict):
            raise SystemExit("the rule must be sent whole (reload the page)")
        if rid and not rule.get("id"):
            rule = dict(rule, id=rid)
        _, msg = RU.save(cfg, a, rule, by, page=page, now=now, seen_version=body.get("seen_version"))
        return True, msg
    if op == "rule-sign":
        return True, RU.sign(cfg, a, rid, str(body.get("seen_hash") or ""), by, page=page, now=now)
    if op == "rule-mode":
        return True, RU.set_mode(cfg, a, rid, str(body.get("mode") or ""), by, page=page, now=now)
    if op == "rule-dup":
        return True, RU.duplicate(cfg, a, rid, _acct(cfg, body.get("to")), by, page=page, now=now)
    if op == "rule-delete":
        return True, RU.delete(cfg, a, rid, by, page=page)
    if op == "rule-seed":
        return True, RU.seed(cfg, a, by, now=now)
    if op == "rule-try":
        rule = body.get("rule") if isinstance(body.get("rule"), dict) else None
        got = RU.try_text(cfg, a, str(body.get("text") or "")[:2000], str(body.get("platform") or "instagram"),
                          str(body.get("post") or ""), rule=rule, now=now, name=str(body.get("name") or "someone"))
        return True, json.dumps(got, default=str)
    if op == "rule-replay":
        rule = body.get("rule") if isinstance(body.get("rule"), dict) else None
        try:
            days = int(body.get("days") or 7)
        except (TypeError, ValueError):
            days = 7
        got = RU.replay(cfg, a, days, rid if rule is None else "", rule=rule, now=now)
        return True, json.dumps(got, default=str)
    raise SystemExit(f"{op or 'that'}: not a click the Automations page knows")


# ------------------------------------------------------------------------------------------------------ the page
CSS = """
.au-lede{color:var(--sub);font-size:13px;max-width:820px;margin:0}
.au-accts{display:flex;flex-wrap:wrap;gap:10px}
.au-acct{flex:1 1 300px;min-width:0;background:var(--card);border:1px solid var(--line);border-radius:8px;padding:10px 12px;display:flex;flex-direction:column;gap:6px}
.au-acct-h{display:flex;align-items:center;gap:8px;flex-wrap:wrap}
.au-acct-h b{font-weight:650}
.au-acct details summary{font-size:12.5px}
.au-check{list-style:none;margin:6px 0 0;padding:0;display:grid;gap:4px;font-size:12.5px}
.au-check li{display:flex;gap:6px;align-items:baseline;overflow-wrap:anywhere}
.au-check .au-yes{color:var(--ok)}.au-check .au-no{color:var(--sub)}
.au-btns{display:flex;flex-wrap:wrap;gap:6px;align-items:center}
.au-list{background:var(--card);border:1px solid var(--line);border-radius:8px;overflow:hidden;min-width:0}
.au-row{display:grid;grid-template-columns:minmax(150px,2.2fr) minmax(90px,1.2fr) 88px 62px 52px 76px 66px 56px 104px minmax(110px,1.3fr);gap:8px;align-items:center;padding:9px 12px;border:0;border-bottom:1px solid var(--line);background:none;color:var(--ink);text-align:left;font:inherit;font-size:13px;width:100%;height:auto;border-radius:0;min-width:0}
.au-row:last-child{border-bottom:0}
button.au-row:hover{background:var(--bg)}
.au-row[aria-selected=true]{background:var(--bluebg)}
.au-row.au-th{color:var(--sub);font-size:12px;font-weight:600;background:var(--bg)}
.au-row>span{min-width:0;overflow-wrap:anywhere}
.au-num{font-variant-numeric:tabular-nums}
.au-name{font-weight:600}
.au-flag{color:var(--bad);font-size:12px;font-weight:600}
.au-signed{color:var(--ok)}.au-changed{color:var(--bad);font-weight:600}.au-unsigned{color:var(--sub)}
.au-ed{display:grid;grid-template-columns:minmax(0,1fr) minmax(0,1.2fr);gap:18px;align-items:start}
.au-col{display:flex;flex-direction:column;gap:12px;min-width:0}
.au-f{display:flex;flex-direction:column;gap:5px;min-width:0}
.au-f>b{font-size:12.5px;color:var(--sub);font-weight:600}
.au-f input:not([type=checkbox]),.au-f textarea,.au-f select{font:inherit;font-size:13px;padding:6px 8px;border:1px solid var(--line);border-radius:6px;background:var(--card);color:var(--ink);width:100%;min-width:0}
.au-f input:not([type=checkbox]){height:32px}
.au-f textarea{min-height:58px;resize:vertical}
.au-var{display:flex;flex-direction:column;gap:3px}
.au-count{font-size:11.5px;color:var(--sub);text-align:right;font-variant-numeric:tabular-nums}
.au-count.au-over{color:var(--bad);font-weight:600}
.au-lim{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:8px}
.au-chk{display:flex;align-items:center;gap:7px;font-size:13px;color:var(--ink)}
.au-note{color:var(--sub);font-size:12.5px;overflow-wrap:anywhere}
.au-warn{color:var(--warn);font-size:12.5px;overflow-wrap:anywhere}
.au-bad{color:var(--bad);font-size:12.5px;overflow-wrap:anywhere}
.au-star{border:1px solid var(--line);border-radius:8px;padding:12px;display:flex;flex-direction:column;gap:8px;background:var(--bg)}
.au-star h3,.au-try h3,.au-hist h3{margin:0;font-size:14px;font-weight:650}
.au-try,.au-hist{border-top:1px solid var(--line);padding-top:12px;display:flex;flex-direction:column;gap:8px}
.au-tally{display:flex;flex-wrap:wrap;gap:6px}
.au-runs{display:flex;flex-direction:column;border:1px solid var(--line);border-radius:6px;background:var(--card);max-height:520px;overflow:auto}
.au-run{padding:8px 10px;border-bottom:1px solid var(--line);display:flex;flex-direction:column;gap:3px;font-size:12.5px;min-width:0}
.au-run:last-child{border-bottom:0}
.au-run-h{display:flex;gap:8px;align-items:baseline;flex-wrap:wrap;min-width:0}
.au-run-h b{min-width:0;overflow-wrap:anywhere}
.au-stats{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:10px;font-size:12.5px}
.au-stats>div{border:1px solid var(--line);border-radius:6px;padding:8px 10px;display:flex;flex-direction:column;gap:3px;min-width:0}
.au-stats b{font-weight:600}
.au-cap{padding:8px 12px}
.au-run-h time{color:var(--sub);font-variant-numeric:tabular-nums}
.au-q{color:var(--sub);overflow-wrap:anywhere}
.au-say{overflow-wrap:anywhere;white-space:pre-wrap}
.au-say b{font-weight:600;color:var(--sub)}
.au-back{display:none}
.au-ph{display:grid;grid-template-columns:auto 1fr;gap:2px 10px;font-size:12px;color:var(--sub);margin-top:6px}
.au-ph code{color:var(--ink)}
.au-row2{display:flex;gap:8px;flex-wrap:wrap;align-items:flex-end}
.au-row2>.au-f{flex:1 1 140px}
@media (max-width:980px){.au-ed{grid-template-columns:minmax(0,1fr)}}
@media (max-width:1000px){.au-row.au-th{display:none}
.au-row{display:flex;flex-wrap:wrap;gap:4px 12px;padding:10px 12px}
.au-row>span{font-size:12.5px;color:var(--sub)}
.au-row>span::before{content:attr(data-l) ": ";color:var(--sub)}
.au-row>span.au-name{flex-basis:100%;font-size:14px;color:var(--ink)}
.au-row>span.au-name::before{content:""}
.au-row>span.au-num,.au-row>span.au-sg{color:var(--ink)}}
@media (max-width:600px){.au-open .au-list,.au-open .au-accts,.au-open .au-lede{display:none}
.au-back{display:inline-flex;align-items:center;justify-content:center}
.au-acct details summary,#au details>summary{min-height:40px;display:flex;align-items:center}
.au-chk{min-height:40px}
.au-lim{grid-template-columns:minmax(0,1fr)}
.au-stats{grid-template-columns:minmax(0,1fr)}
.au-f input:not([type=checkbox]),.au-f select{height:40px;font-size:16px}
.au-f textarea{font-size:16px}
.au-chk input{width:22px;height:22px;flex:none}
body.sx button.au-row{height:auto;min-height:40px;font-size:13px}}
"""

JS = r"""
(() => {
'use strict';
const {el, esc, act, ask, toast, qs, setq} = SX;
const S = {d: null, sel: qs('rule') || '', selAcct: qs('in') || '', dirty: false, F: null, tryOut: null,
  replayOut: null, open: !!qs('rule'), T: {text: '', plat: 'instagram', post: '', name: 'someone'}, folds: new Set()};
const main = document.getElementById('au');
const MODES = [['practice', 'Practice'], ['testers', 'Testers'], ['live', 'Live'], ['paused', 'Paused']];
const PLATS = [['instagram', 'Instagram'], ['facebook', 'Facebook'], ['youtube', 'YouTube']];
const ACCT_MODE = {off: 'Off', practice: 'Practice', testers: 'Testers', on: 'On'};
const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
function when(iso) {
  const t = new Date(iso || ''); if (isNaN(t)) return '';
  const p = n => String(n).padStart(2, '0');
  return MONTHS[t.getMonth()] + ' ' + t.getDate() + ' ' + p(t.getHours()) + ':' + p(t.getMinutes());
}
function platName(p) { return ((S.d && S.d.plat_words) || {})[p] || p || ''; }
function skipWord(code) { return ((S.d && S.d.skip_words) || {})[code] || String(code || '').replace(/_/g, ' '); }
function fold(key, summary, kids) {
  const d = el('details', {'data-k': key}, [el('summary', {}, summary)].concat(kids));
  if (S.folds.has(key)) d.open = true;
  d.addEventListener('toggle', () => { d.open ? S.folds.add(key) : S.folds.delete(key); });
  return d;
}
function busy() {
  const f = document.activeElement, box = document.getElementById('au-detail');
  return !!(f && box && box.contains(f) && /^(INPUT|TEXTAREA|SELECT)$/.test(f.tagName));
}
function put(box, ...kids) { box.replaceChildren(...kids.filter(k => k != null && k !== false)); }
function poss(n) { return /s$/i.test(n) ? n + "'" : n + "'s"; }
function bytes(s) { try { return new TextEncoder().encode(s).length; } catch (e) { return s.length; } }
function acctOf(slug) { return ((S.d && S.d.accounts) || []).find(a => a.slug === slug) || null; }
function ruleOf(a, id) { return a ? (a.rules || []).find(r => r.id === id) || null : null; }
function cur() {
  const a = acctOf(S.selAcct);
  return {a, r: S.sel && !S.sel.startsWith('new') ? ruleOf(a, S.sel) : null};
}
const LABEL = Object.assign({}, ACCT_MODE, Object.fromEntries(MODES));
function modeTag(m) {
  const cls = {live: 't-ok', on: 't-ok', testers: 't-ready', practice: 't-warn', paused: 't-mute', off: 't-mute'}[m] || 't-mute';
  return el('span', {class: 'tag ' + cls}, LABEL[m] || m);
}
function signWords(r) {
  if (r.signed === 'signed') return el('span', {class: 'au-signed'}, 'Signed v' + r.version);
  if (r.signed === 'changed') return el('span', {class: 'au-changed'}, 'Changed after signing');
  return el('span', {class: 'au-unsigned'}, 'Not signed');
}
async function post(body) {
  try {
    const r = await fetch('/act', {method: 'POST', headers: {'Content-Type': 'application/json', 'X-Token': SX.TOKEN},
      body: JSON.stringify(Object.assign({by: SX.WHO}, body))});
    let d; try { d = await r.json(); } catch (e) { d = {ok: false, message: 'The page sent an answer it could not read. Reload it.'}; }
    if (r.status === 403 && d.stale) { toast('This tab was open before the page restarted. Reloading...'); setTimeout(() => location.reload(), 900); return null; }
    if (!r.ok || !d.ok) { toast(d.message || 'That did not work.', true); return null; }
    try { return JSON.parse(d.message); } catch (e) { return null; }
  } catch (e) { toast('Could not reach the review page (it may be restarting). Try again in a few seconds.', true); return null; }
}
async function click(body, btn, words, after) {
  if (!ask(words)) return;
  const got = await act(body, btn);
  if (got.ok) { if (after) after(); poller.now(); }
}

/* ------------------------------------------------------------------ the accounts */
function drawAccts() {
  const box = document.getElementById('au-accts');
  box.replaceChildren(...(S.d.accounts || []).map(a => {
    const open = (a.checklist || []).filter(c => !c[0]).length;
    const kids = [el('div', {class: 'au-acct-h'}, [el('b', {}, a.name), el('span', {class: 'au-note'}, 'automations'), modeTag(a.mode),
      el('span', {class: 'au-note'}, (a.rules || []).length + ' rules')])];
    if (a.error) kids.push(el('div', {class: 'au-bad'}, 'Could not read its rules: ' + a.error));
    kids.push(el('div', {class: 'au-note'}, a.warmup + '. Messages: ' + (a.messages_access === 'advanced' ? 'open (Meta review done)' : 'only the app\'s testers until Meta\'s review') + '. Testers: ' + (a.testers || 0) + '.'));
    if (a.mode === 'off') kids.push(el('div', {class: 'au-note'}, 'Off: nothing runs. Switch it to practice on the Log tab (Switches) or with factory.py social on ' + a.slug + ' automations --practice.'));
    kids.push(fold('check:' + a.slug, 'Go-live checklist: ' + (open ? open + ' open' : 'all done'),
      [el('ul', {class: 'au-check'}, (a.checklist || []).map(c => el('li', {}, [el('span', {class: c[0] ? 'au-yes' : 'au-no'}, c[0] ? 'Done' : 'Open'), el('span', {}, c[1])])))]));
    const btns = [el('button', {type: 'button', onclick: () => pick(a.slug, 'new')}, 'New rule')];
    if (!a.seeded && (a.starters || []).length) {
      btns.unshift(el('button', {type: 'button', class: 'go', onclick: e => click({op: 'rule-seed', account: a.slug}, e.target,
        'Add ' + a.starters.length + ' starter rules to ' + a.name + ', in practice and unsigned: ' + a.starters.join(', ') + '? Nothing is sent until you sign one and switch it on.')}, 'Add the starter rules'));
    }
    kids.push(el('div', {class: 'au-btns'}, btns));
    return el('div', {class: 'au-acct'}, kids);
  }));
}

/* ------------------------------------------------------------------ the list */
const COLS = [['Rule', 'au-name'], ['Account', ''], ['Mode', ''], ['Runs 24h', 'au-num'], ['Sent', 'au-num'], ['Would send', 'au-num'],
  ['Skipped', 'au-num'], ['Failed', 'au-num'], ['Last run', ''], ['Signed', 'au-sg']];
function drawList() {
  const box = document.getElementById('au-list');
  const rows = [el('div', {class: 'au-row au-th', role: 'row'}, COLS.map(c => el('span', {}, c[0])))];
  for (const a of S.d.accounts || []) for (const r of a.rules || []) {
    const s = (r.stats && r.stats.h24) || {};
    const flags = (r.flags || []).filter(f => f.key === 'self_pause');
    const cells = [
      el('span', {class: 'au-name', 'data-l': 'Rule'}, [r.name, flags.length ? el('span', {class: 'au-flag'}, ' paused itself') : null]),
      el('span', {'data-l': 'Account'}, a.name),
      el('span', {'data-l': 'Mode'}, modeTag(r.mode)),
      el('span', {class: 'au-num', 'data-l': 'Runs 24h'}, String(s.triggered || 0)),
      el('span', {class: 'au-num', 'data-l': 'Sent'}, String(s.sent || 0)),
      el('span', {class: 'au-num', 'data-l': 'Would send'}, String(s.practice || 0)),
      el('span', {class: 'au-num', 'data-l': 'Skipped'}, String(s.skipped || 0)),
      el('span', {class: 'au-num', 'data-l': 'Failed'}, String(s.failed || 0)),
      el('span', {'data-l': 'Last run'}, r.stats && r.stats.last_run ? when(r.stats.last_run) : 'never'),
      el('span', {class: 'au-sg', 'data-l': 'Signed'}, signWords(r))];
    rows.push(el('button', {type: 'button', class: 'au-row', role: 'row', 'aria-selected': String(S.sel === r.id && S.selAcct === a.slug),
      onclick: () => pick(a.slug, r.id)}, cells));
  }
  if (rows.length > 1) rows.push(el('div', {class: 'au-note au-cap'}, 'The numbers are for the last 24 hours. A rule\'s own page has them for all the time kept, with why each was skipped.'));
  if (rows.length === 1) rows.push(el('div', {class: 'sx-empty'}, 'No rules yet. Add the starter rules for an account above: they answer where a clip is from, who is speaking and the other things people ask, with facts from the clip itself.'));
  box.replaceChildren(...rows);
}
function pick(slug, id) {
  if (S.dirty && !ask('Leave this rule without saving your changes?')) return;
  S.sel = id; S.selAcct = slug; S.dirty = false; S.tryOut = null; S.replayOut = null; S.open = true;
  S.T = {text: '', plat: 'instagram', post: '', name: 'someone'};
  setq('rule', id === 'new' ? '' : id); setq('in', slug);
  draw(true);
  const d = document.getElementById('au-detail'); if (d) d.scrollIntoView({block: 'start', behavior: 'smooth'});
}

/* ------------------------------------------------------------------ the editor */
function blank(a) {
  const L = S.d.defaults || {per_hour: 6, per_day: 30, person_days: 7};
  return {id: '', name: '', platforms: ['instagram', 'facebook'], trigger: {on: 'comment', posts: 'any', post_ids: [], intents: [],
    keywords: [], exclude: [], max_age_h: S.d.max_age_h || 24, match: 'any'}, action: {public: {on: true, variants: ['', '', '']},
    private: {on: false, variants: []}, part2: {on: false}}, limits: Object.assign({}, L), mode: 'practice', version: 0, signed: 'unsigned'};
}
function field(label, input, extra) {
  const one = input && /^(INPUT|TEXTAREA|SELECT)$/.test(input.tagName || '');
  return el(one ? 'label' : 'div', {class: 'au-f', role: one ? null : 'group'}, [el('b', {}, label), input, extra || null]);
}
function variantsBox(kind, list, max, isBytes) {
  const box = el('div', {class: 'au-f'});
  const lab = el('b', {}, kind === 'public' ? 'Public reply: 3 to 6 ways of saying it' : (S.F.on === 'message' ? 'Message reply' : 'Private reply') + ': 1 to 3 ways of saying it');
  const wrap = el('div', {class: 'au-col'});
  const lim = kind === 'public' ? (S.d.limits.public_max || 300) : (S.d.limits.private_max || 883);
  function add(text) {
    const ta = el('textarea', {rows: 2, 'data-v': kind, 'aria-label': kind + ' reply'});
    ta.value = text || '';
    const cnt = el('div', {class: 'au-count'});
    const upd = () => { const n = isBytes ? bytes(ta.value) : ta.value.length; cnt.textContent = n + ' / ' + lim + (isBytes ? ' bytes' : ' characters'); cnt.className = 'au-count' + (n > lim ? ' au-over' : ''); };
    ta.addEventListener('input', () => { upd(); dirty(); }); upd();
    const rm = el('button', {type: 'button', class: 'link', onclick: () => { row.remove(); dirty(); }}, 'Remove');
    const row = el('div', {class: 'au-var'}, [ta, el('div', {class: 'au-btns'}, [cnt, rm])]);
    wrap.append(row);
  }
  (list && list.length ? list : (kind === 'public' ? ['', '', ''] : [''])).forEach(add);
  const more = el('button', {type: 'button', onclick: () => { if (wrap.children.length < max) { add(''); dirty(); } else toast('At most ' + max + '.'); }}, 'Add another way');
  box.append(lab, wrap, more);
  return box;
}
function chipsSet(opts, have, onChange) {
  const set = new Set(have || []);
  const root = el('div', {class: 'sx-chips'});
  const draw = () => SX.chips(root, opts.map(o => ({id: o[0], l: o[1]})), set, id => { set.has(id) ? set.delete(id) : set.add(id); draw(); onChange(); });
  draw();
  return {root, set};
}
function dirty() { S.dirty = true; const s = document.getElementById('au-state'); if (s) s.textContent = 'Changed: save it, then sign it again.'; }
function lines(v) { return (v || []).join('\n'); }
function collect() {
  const F = S.F;
  const vs = k => [...document.querySelectorAll('textarea[data-v=' + k + ']')].map(t => t.value.trim()).filter(Boolean);
  return {id: F.id, name: F.name.value.trim(), platforms: [...F.plats.set], trigger: {on: F.on, posts: F.posts, post_ids: F.postIds.value,
    intents: [...F.intents.set], keywords: F.kw.value, exclude: F.ex.value, max_age_h: +F.age.value || 24, match: F.match},
    action: {public: {on: F.pubOn.checked, variants: vs('public')}, private: {on: F.priOn.checked, variants: vs('private')},
      part2: {on: F.p2On ? F.p2On.checked : false}}, limits: {per_hour: +F.ph.value, per_day: +F.pd.value, person_days: +F.pp.value}};
}
function seg(opts, curv, onPick) {
  const root = el('div', {class: 'seg', role: 'group'});
  const draw = v => SX.seg(root, opts.map(o => ({id: o[0], l: o[1]})), v, id => { draw(id); onPick(id); });
  draw(curv);
  return root;
}
function drawDetail() {
  const box = document.getElementById('au-detail');
  const {a, r} = cur();
  if (!a || (!r && S.sel !== 'new')) {
    box.replaceChildren(el('div', {class: 'sx-empty'}, 'Pick a rule to read, test and sign it, or make a new one.'));
    return;
  }
  const R = r || blank(a);
  const caps = S.d.caps || {};
  S.F = {id: R.id, on: R.trigger.on || 'comment', posts: R.trigger.posts || 'any', match: R.trigger.match === 'all' ? 'all' : 'any'};
  const F = S.F;
  const head = el('div', {class: 'sx-head'}, [
    el('button', {type: 'button', class: 'au-back', onclick: () => { if (S.dirty && !ask('Leave without saving your changes?')) return; S.open = false; S.sel = ''; S.dirty = false; setq('rule', ''); draw(true); }}, 'Back'),
    el('h2', {}, r ? r.name : 'New rule for ' + a.name), r ? modeTag(r.mode) : null, r ? signWords(r) : null,
    el('span', {class: 'au-note', id: 'au-state'}, r ? ('version ' + r.version + (r.starter ? ', a starter rule' : '') + (r.copied_from ? ', copied from ' + r.copied_from : '')) : 'not saved yet')]);
  const flags = (R.flags || []).filter(f => f.key === 'self_pause').map(f => el('div', {class: 'au-bad'}, 'It went back to practice by itself on ' + when(f.at) + ': ' + f.why + '. Read the replies in the Inbox before you switch it back on.'));

  /* the star: the last 7 days */
  const replayBox = el('div', {id: 'au-replay'});
  const star = el('div', {class: 'au-star'}, [el('h3', {}, 'Test on the last 7 days'),
    el('div', {class: 'au-note'}, 'Runs ' + (r ? 'this rule' : 'these settings') + ' over every comment of the last 7 days, in order, with the caps, quiet hours and limits: what it would have sent, to whom, when, and why anything was held. Nothing is sent or saved.'),
    el('div', {class: 'au-btns'}, [el('button', {type: 'button', class: 'go', onclick: e => runReplay(e.target)}, 'Test on the last 7 days')]), replayBox]);

  /* the trigger */
  F.name = el('input', {type: 'text', maxlength: 80, value: R.name || '', oninput: dirty, placeholder: 'Where is this from'});
  F.plats = chipsSet(PLATS, R.platforms, dirty);
  const onSeg = seg([['comment', 'Comments'], ['message', 'Messages']], F.on, v => { F.on = v; dirty(); });
  const postsSeg = seg([['any', 'Any post'], ['list', 'These posts']], F.posts, v => { F.posts = v; dirty(); });
  F.postIds = el('textarea', {rows: 2, oninput: dirty, placeholder: 'Post ids, one a line'}); F.postIds.value = lines(R.trigger.post_ids);
  F.intents = chipsSet(S.d.intents || [], R.trigger.intents, dirty);
  const matchSeg = seg([['all', 'An ask and a word'], ['any', 'An ask or a word']], F.match, v => { F.match = v; dirty(); });
  F.kw = el('textarea', {rows: 2, oninput: dirty, placeholder: 'source, full video'}); F.kw.value = lines(R.trigger.keywords);
  F.ex = el('textarea', {rows: 2, oninput: dirty, placeholder: 'words that mean it is not a question for us'}); F.ex.value = lines(R.trigger.exclude);
  F.age = el('input', {type: 'number', min: 1, max: 168, value: R.trigger.max_age_h || 24, oninput: dirty});
  const left = el('div', {class: 'au-col'}, [
    field('Name', F.name),
    field('Platforms', F.plats.root, el('span', {class: 'au-note'}, 'YouTube takes no automated replies: a match there only shows a suggested reply in the Inbox.')),
    field('It answers', onSeg),
    field('On', postsSeg, F.postIds),
    field('When the comment asks', F.intents.root, el('span', {class: 'au-note'}, 'From the Inbox\'s reading of each comment.')),
    field('Answer when the comment has', matchSeg, el('span', {class: 'au-note'}, 'An ask and a word is safer: "when is part 2 coming out" asks when, but it is not about the release date.')),
    field('The words (whole words; one a line or with commas)', F.kw),
    field('Never when it uses', F.ex),
    field('Comments up to this many hours old', F.age)]);

  /* the words */
  F.pubOn = el('input', {type: 'checkbox', onchange: dirty}); F.pubOn.checked = !!R.action.public.on;
  F.priOn = el('input', {type: 'checkbox', onchange: dirty}); F.priOn.checked = !!R.action.private.on;
  F.p2On = el('input', {type: 'checkbox', onchange: dirty}); F.p2On.checked = !!(R.action.part2 || {}).on;
  const ph = (S.d.placeholders || []).flatMap(p => [el('code', {}, '{' + p[0] + '}'), el('span', {}, p[1])]);
  const pubCap = caps.public || [6, 30], priCap = (F.on === 'message' ? caps.dm : caps.private) || [8, 40];
  F.ph = el('input', {type: 'number', min: 1, max: Math.min(pubCap[0], priCap[0]), value: R.limits.per_hour, oninput: dirty});
  F.pd = el('input', {type: 'number', min: 1, max: Math.min(pubCap[1], priCap[1]), value: R.limits.per_day, oninput: dirty});
  F.pp = el('input', {type: 'number', min: 1, max: 30, value: R.limits.person_days, oninput: dirty});
  const right = el('div', {class: 'au-col'}, [
    el('label', {class: 'au-chk'}, [F.pubOn, 'Reply in public under the comment']),
    variantsBox('public', R.action.public.variants, 6, false),
    el('label', {class: 'au-chk'}, [F.priOn, F.on === 'message' ? 'Answer the message' : 'Reply privately (a message; the first one to each person starts "Automated reply: ")']),
    variantsBox('private', R.action.private.variants, 3, true),
    el('label', {class: 'au-chk'}, [F.p2On, 'Part 2: when someone asks for it, tell them when the next clip from the same source is up (within 6 days, once)']),
    fold('facts', 'Facts a text may use', [el('div', {class: 'au-ph'}, ph)]),
    el('div', {class: 'au-f'}, [el('b', {}, 'Limits (the hard caps: ' + pubCap[0] + ' an hour and ' + pubCap[1] + ' a day for public replies)'),
      el('div', {class: 'au-lim'}, [field('Per hour', F.ph), field('Per day', F.pd), field('Days between replies to one person', F.pp)])])]);

  /* the problems and the buttons */
  const msgs = [];
  for (const p of (r && r.problems) || []) msgs.push(el('div', {class: 'au-bad'}, p));
  for (const w of (r && r.warnings) || []) msgs.push(el('div', {class: 'au-warn'}, w));
  const btns = [el('button', {type: 'button', class: 'go', onclick: e => save(e.target)}, 'Save')];
  if (r) {
    btns.push(el('button', {type: 'button', disabled: r.signed === 'signed' ? true : null, onclick: e => {
      if (S.dirty) { toast('Save your changes first, then sign what you saved.', true); return; }
      click({op: 'rule-sign', account: a.slug, id: r.id, seen_hash: r.hash}, e.target, r.sign_words);
    }}, r.signed === 'signed' ? 'Signed' : 'Sign'));
    btns.push(seg(MODES, r.mode, m => {
      if (S.dirty) { toast('Save your changes first.', true); drawDetail(); return; }
      const why = (r.live_problems || {})[m] || [];
      if ((m === 'live' || m === 'testers') && why.length) { toast(r.name + ' stays ' + r.mode + ': ' + why.join('; '), true); drawDetail(); return; }
      const plats = (r.platforms || []).map(platName).join(' and ');
      const sayWords = () => {
        const out = [];
        if (r.action.public.on) out.push('In public:', ...r.action.public.variants.map(v => '  ' + v));
        if (r.action.private.on) out.push((r.trigger.on === 'message' ? 'In a message' : 'In a private reply') + ' (the first to each person starts "Automated reply: "):', ...r.action.private.variants.map(v => '  ' + v));
        if ((r.action.part2 || {}).on) { const pw = S.d.part2_words || []; out.push('Part 2, once the next clip from the same source is up, one of:', ...(pw[0] || []).map(v => '  ' + v), '  or in public, one of:', ...(pw[1] || []).map(v => '  ' + v)); }
        return out.join('\n');
      };
      const words = m === 'live' ? a.name + ': "' + r.name + '" goes live. Its replies go to people who comment on ' + poss(a.name) + ' ' + plats + ' posts, at most ' + r.limits.per_hour + ' an hour and ' + r.limits.per_day + ' a day, in these words:\n' + sayWords()
        : m === 'testers' ? a.name + ': "' + r.name + '" replies only to the app\'s testers on ' + plats + ' (everyone else stays in practice), at most ' + r.limits.per_hour + ' an hour and ' + r.limits.per_day + ' a day, in these words:\n' + sayWords()
        : m === 'paused' ? 'Pause "' + r.name + '" on ' + a.name + '? It stops answering anyone.'
        : 'Put "' + r.name + '" on ' + a.name + ' back in practice? It writes what it would send and sends nothing.';
      if (!ask(words)) { drawDetail(); return; }
      act({op: 'rule-mode', account: a.slug, id: r.id, mode: m}).then(g => { if (!g.ok) drawDetail(); poller.now(); });
    }));
    const others = (S.d.all || []).filter(x => x.slug !== a.slug);
    if (others.length) {
      const sel = el('select', {'aria-label': 'Copy to'}, others.map(o => el('option', {value: o.slug}, o.name)));
      btns.push(el('span', {class: 'au-btns'}, [sel, el('button', {type: 'button', onclick: e => {
        const to = others.find(o => o.slug === sel.value);
        click({op: 'rule-dup', account: a.slug, id: r.id, to: sel.value}, e.target, 'Copy "' + r.name + '" to ' + (to ? to.name : sel.value) + ' as a draft (practice, unsigned)? It sends nothing there until you sign it.');
      }}, 'Duplicate to')]));
    }
    if (!r.ever_signed) btns.push(el('button', {type: 'button', onclick: e => click({op: 'rule-delete', account: a.slug, id: r.id}, e.target,
      'Delete the draft "' + r.name + '" from ' + a.name + '? This cannot be undone.', () => { S.sel = ''; setq('rule', ''); })}, 'Delete'));
    if (S.dirty) btns.push(el('button', {type: 'button', class: 'link', onclick: () => { S.dirty = false; drawDetail(); }}, 'Undo my changes'));
  }
  const live = r && r.mode !== 'live' ? el('div', {class: 'au-note'}, 'Live needs: ' + (((r.live_problems || {}).live || []).join('; ') || 'nothing more')) : null;

  /* try it */
  const tryText = el('textarea', {rows: 2, placeholder: 'Type a comment, as someone would: where is this from?', 'aria-label': 'A comment to try'});
  const tryPlat = el('select', {'aria-label': 'Platform'}, PLATS.map(p => el('option', {value: p[0]}, p[1])));
  const posts = a.posts || [];
  const tryPost = el('select', {'aria-label': 'Post'}, [el('option', {value: ''}, 'The latest post')].concat(posts.map(p => el('option', {value: p.platform_id}, p.title + ' (' + platName(p.platform) + ')'))));
  const tryName = el('input', {type: 'text', value: 'someone', 'aria-label': 'Their name'});
  tryText.value = S.T.text; tryPlat.value = S.T.plat; tryPost.value = S.T.post; tryName.value = S.T.name;
  if (tryPost.value !== S.T.post) tryPost.value = '';
  tryText.addEventListener('input', () => { S.T.text = tryText.value; });
  tryPlat.addEventListener('change', () => { S.T.plat = tryPlat.value; });
  tryPost.addEventListener('change', () => { S.T.post = tryPost.value; });
  tryName.addEventListener('input', () => { S.T.name = tryName.value; });
  const tryBox = el('div', {id: 'au-tryout'});
  const tryIt = el('div', {class: 'au-try'}, [el('h3', {}, 'Try it'),
    el('div', {class: 'au-note'}, 'Type a comment: which rule answers it, the exact words, and what the caps, quiet hours or holds would do with it right now. Nothing is sent or saved.'),
    field('Comment', tryText), el('div', {class: 'au-row2'}, [field('Platform', tryPlat), field('Under', tryPost), field('Their name', tryName)]),
    el('div', {class: 'au-btns'}, [el('button', {type: 'button', onclick: async e => {
      e.target.disabled = true;
      const body = {op: 'rule-try', account: a.slug, text: tryText.value, platform: tryPlat.value, post: tryPost.value, name: tryName.value};
      if (!r || S.dirty) body.rule = collect();
      S.tryOut = await post(body); e.target.disabled = false; drawTry();
    }}, 'Try it')]), tryBox]);

  /* its numbers */
  const statBox = (label, x) => {
    x = x || {};
    const why = Object.entries(x.why || {}).map(([k, v]) => skipWord(k) + ' ' + v).join(', ');
    return el('div', {}, [el('b', {}, label), el('span', {}, (x.triggered || 0) + ' runs: ' + (x.sent || 0) + ' sent, ' + (x.practice || 0) + ' would send, ' + (x.skipped || 0) + ' skipped, ' + (x.failed || 0) + ' failed'),
      why ? el('span', {class: 'au-note'}, 'Skipped: ' + why) : null]);
  };
  const stats = r ? el('div', {class: 'au-stats'}, [statBox('Last 24 hours', (r.stats || {}).h24), statBox('All the time kept', (r.stats || {}).all)]) : null;

  /* what it did */
  const hist = el('div', {class: 'au-hist'}, [el('h3', {}, 'What it did lately'), runsList((a.runs || []).filter(x => r && x.rule === r.id).slice(0, 40), 'Nothing yet: it runs on new comments once the account\'s automations switch is on (practice or more).')]);

  put(box, head, ...flags, stats, star, el('div', {class: 'au-ed'}, [left, right]), ...msgs, el('div', {class: 'au-btns'}, btns), live, tryIt, hist);
  drawTry(); drawReplay();
}
function save(btn) {
  const {a, r} = cur();
  const rule = collect();
  const words = (r ? 'Save "' + rule.name + '" for ' + a.name + '? It goes back to practice and needs your signature again.' : 'Save the new rule "' + rule.name + '" for ' + a.name + '? It starts in practice, unsigned.');
  if (!ask(words)) return;
  act({op: 'rule-save', account: a.slug, id: r ? r.id : '', rule, seen_version: r ? r.version : null}, btn).then(g => {
    if (!g.ok) return;
    S.dirty = false;
    if (!r) { S.pendingName = rule.name; }
    poller.now();
  });
}
async function runReplay(btn) {
  const {a, r} = cur();
  btn.disabled = true;
  const body = {op: 'rule-replay', account: a.slug, id: r ? r.id : '', days: 7};
  if (!r || S.dirty) body.rule = collect();
  S.replayOut = await post(body); btn.disabled = false; drawReplay();
}
function textsOf(x) {
  const t = x.text || {};
  if (typeof t === 'string') return [el('div', {class: 'au-say'}, t)];
  const name = {reply_public: 'Public', reply_private: 'Private', dm: 'Message', suggested: 'Suggested'};
  return Object.keys(t).map(k => el('div', {class: 'au-say'}, [el('b', {}, (name[k] || k) + ': '), t[k]]));
}
function resultTag(x) {
  const m = {would_send: ['Would send', 't-ready'], practice: ['Would send', 't-ready'], queued: ['Sent to the queue', 't-ok'], skipped: [x.why === 'youtube' ? 'Suggested' : 'Skipped', 't-mute']}[x.result] || [x.result, 't-mute'];
  return el('span', {class: 'tag ' + m[1]}, m[0]);
}
function runsList(rows, empty) {
  if (!rows.length) return el('div', {class: 'sx-empty'}, empty);
  return el('div', {class: 'au-runs'}, rows.map(x => {
    const acts = (x.actions || []).filter(y => y);
    const ok = acts.filter(y => y.ok);
    const held = ok.filter(y => y.would_at && Date.parse(y.would_at) > Date.parse(x.at) + 60000);
    return el('div', {class: 'au-run'}, [
      el('div', {class: 'au-run-h'}, [el('time', {}, when(x.at)), resultTag(x), el('b', {}, x.name ? ('@' + x.name.replace(/^@/, '')) : (x.to || 'someone')),
        el('span', {class: 'au-note'}, platName(x.platform) + (x.rule_name ? ', ' + x.rule_name : ''))]),
      x.comment ? el('div', {class: 'au-q'}, '"' + x.comment + '"') : null,
      ...textsOf(x),
      ok.length ? el('div', {class: 'au-note'}, 'Would go ' + when(ok[0].would_at) + (held.length ? ', held: ' + held.map(y => y.why).join('; ') : (ok[0].why ? ' (' + ok[0].why + ')' : ''))) : null,
      x.result === 'skipped' || (!ok.length && acts.length) ? el('div', {class: 'au-note'}, 'Why: ' + (x.note || skipWord(x.why) || '')) : null]);
  }));
}
function drawReplay() {
  const box = document.getElementById('au-replay'); if (!box) return;
  const o = S.replayOut; if (!o) { box.replaceChildren(); return; }
  const t = o.tally || {};
  const n = (v, one, many) => (v || 0) + ' ' + ((v || 0) === 1 ? one : many);
  const chips = [n(t.comments, 'comment read', 'comments read'), n(t.matched, 'matched', 'matched'), n(t.would_send, 'would be answered', 'would be answered'),
    n(t.people, 'person', 'people'), n(t.held, 'held a while', 'held a while'), n(t.skipped, 'skipped', 'skipped'), n(t.suggested, 'YouTube suggestion', 'YouTube suggestions')];
  const why = Object.entries(t.why || {}).map(([k, v]) => skipWord(k) + ' ' + v).join(', ');
  put(box, el('div', {class: 'au-tally'}, chips.map(c => el('span', {class: 'sx-chip'}, c))),
    why ? el('div', {class: 'au-note'}, 'Skipped: ' + why) : null,
    runsList(o.rows || [], 'No comment of the last ' + (o.days || 7) + ' days would have matched.'));
}
function drawTry() {
  const box = document.getElementById('au-tryout'); if (!box) return;
  const o = S.tryOut; if (!o) { box.replaceChildren(); return; }
  const words = Object.fromEntries((S.d.intents || []).concat([['praise', 'Praise'], ['stop', 'Says stop']]));
  const kids = [el('div', {class: 'au-note'}, 'Read as: ' + ((o.tags || []).map(t => words[t] || t).join(', ') || 'nothing it asks') + '. Under: ' + ((o.post && o.post.title) || 'no post found (facts from a clip are missing)') + '.')];
  for (const c of o.candidates || []) kids.push(el('div', {class: 'au-note'}, c.name + ': ' + (!c.platform ? 'not on this platform' : c.excluded && c.excluded.length ? 'held back by "' + c.excluded.join('", "') + '"' : c.matched ? 'matches (' + [...c.matched.intents, ...c.matched.keywords.map(k => '"' + k + '"')].join(', ') + ')' : 'no match')));
  if (o.run) kids.push(runsList([o.run], ''));
  else kids.push(el('div', {class: 'au-note'}, 'No rule would answer this comment.'));
  const miss = Object.entries(o.missing || {});
  if (miss.length) kids.push(el('div', {class: 'au-warn'}, 'Facts this clip does not have: ' + miss.map(([k, v]) => k + ' (' + v + ')').join('; ')));
  for (const p of o.problems || []) kids.push(el('div', {class: 'au-bad'}, p));
  put(box, ...kids);
}

/* ------------------------------------------------------------------ drawing */
function draw(force) {
  if (!S.d) return;
  if (S.pendingName) {
    const a = acctOf(S.selAcct);
    const r = a && (a.rules || []).find(x => x.name === S.pendingName);
    if (r) { S.sel = r.id; setq('rule', r.id); S.pendingName = null; force = true; }
  }
  main.classList.toggle('au-open', !!S.open && !!S.sel);
  drawAccts(); drawList();
  if (force || (!S.dirty && !busy())) drawDetail();
}
const poller = SX.poll(() => '/inbox/rules/data' + (SX.ACCT && SX.ACCT !== 'all' ? '?acct=' + encodeURIComponent(SX.ACCT) : ''), 15000, d => {
  S.d = d;
  if (!S.selAcct && S.sel) { const a = (d.accounts || []).find(x => (x.rules || []).some(r => r.id === S.sel)); if (a) S.selAcct = a.slug; }
  draw(false);
});
window.addEventListener('beforeunload', e => { if (S.dirty) { e.preventDefault(); e.returnValue = ''; } });
})();
"""


def page(cfg: C.Config, tok: str, base_css: str, tabs_html: str, switch_html: str, acct: str, q: dict) -> str:
    """The Automations page under the Inbox tab (drawn by its script from /inbox/rules/data)."""
    from . import registry as REG
    own = (("comments", "/inbox", "Comments"), ("messages", "/inbox?view=messages", "Messages"),
           ("saved", "/inbox?view=saved", "Saved replies")) if REG.present("engagepage") else ()
    sub = SX.subnav("inbox", "rules", acct, own)
    main = ('<div id="au" class="au">'
            '<div class="sx-head"><h2>Automations</h2></div>'
            '<p class="au-lede">Rules answer what people ask under a clip (where it is from, who is speaking, which '
            'hearing, when GTA 6 comes out, part 2), only with facts from the clip itself. A rule practises first and '
            'writes what it would have sent. Nothing reaches anyone until you sign the rule, the account is switched '
            'on, and the caps, quiet hours and warm-up allow it.</p>'
            '<div id="au-accts" class="au-accts" aria-label="Accounts"></div>'
            '<div id="au-list" class="au-list" role="table" aria-label="Rules"></div>'
            '<div id="au-detail" class="sx-detail" aria-live="polite"><div class="sx-empty">Loading the rules.</div>'
            '</div></div>')
    return SX.shell(cfg, "rules", TITLE, tok, base_css, tabs_html, switch_html, acct, main, css=CSS, js=JS,
                    subnav=sub)
