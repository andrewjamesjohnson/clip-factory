"""The Lab tab of Clip Review (/lab, /lab/live): where Andrew records Lab's tests and says what happened.

    the status strip     OBS (connected / not open / password refused), the capture card's picture, free space,
                         what renders now, the recording hours, and "switched off" while the account is off
    recording now        the test, the take, a timer the page ticks itself, Mark (the space bar too) and Stop
    record for <day>     the next posting day's tests (lab.deal), batched by area: the setup in large type, the cheat
                         numbers to dial, HUD on or off, the safety notes, the expected answer folded ("what the wiki
                         says; the recording decides")
    waiting for you      takes with no mark or no outcome: the take's player, Mark here, the marks, As expected /
                         Different / Redo; Look again (relook): the task's why and the frames it read
    untagged             recordings no test owns (Capture/_obs, Capture/inbox): "This is T0xx"
    holds                held tests: Allow / Drop
    coming up            scripted, rendered and placed tests, and the stock against the day's rule

Every tap goes to /act like the Clips tab's (the page's token, loopback, same origin); the outcome, a tag, Allow and
Drop are taken only from this page in a browser (lab.act, review._from_page). /lab/live is polled every 5 s while a
take records, else every 15 s; a section is swapped only when it changed, and never under a playing take or a box
being typed in.
"""
from __future__ import annotations

import hashlib
import html
import json
import time
import urllib.parse
from datetime import datetime
from pathlib import Path
from typing import Dict, Optional

from . import config as C
from . import lab as L

POLL_REC, POLL_IDLE = 5, 15
_LAST_SCAN: Dict[str, float] = {}
_LAST_OBS: Dict[str, float] = {}
SCAN_EVERY_S, OBS_EVERY_S = 60.0, 30.0


def esc(s: object) -> str:
    return html.escape(str(s), quote=True)


def _url(p: str, frag: str = "") -> str:
    return f"/file?p={urllib.parse.quote(str(p))}" + frag


def _wrap(pid: str, inner: str, cls: str = "lab-part") -> str:
    h = hashlib.sha1(inner.encode()).hexdigest()[:12]
    return f'<section class="{cls}" id="{esc(pid)}" data-h="{h}">{inner}</section>'


def _btn(op: str, words: str, acct: str, test: str = "", extra: str = "", cls: str = "") -> str:
    klass = f' class="{esc(cls)}"' if cls else ""
    return (f'<button{klass} data-op="{esc(op)}" data-acct="{esc(acct)}" data-test="{esc(test)}"{extra}>'
            f'{esc(words)}</button>')


def _strip(cfg: C.Config, acct: C.Account, now: datetime) -> str:
    from . import renderlock as RL
    st = L.settings(acct)
    h = L.health(acct)
    if not L.obs_path(acct).is_file():
        obs_w = (f"OBS: not set up (factory.py lab obs-setup {acct.slug} in the Mac's own Terminal)", "warn")
    elif h.get("ok"):
        obs_w = (f"OBS connected ({h.get('obs') or '?'})", "ok")
    elif h.get("kind") == "password":
        obs_w = ("OBS refused the password in obs.json (Tools > WebSocket Server Settings)", "bad")
    elif h:
        obs_w = ("OBS is not open", "bad")
    else:
        obs_w = ("OBS: not checked yet", "warn")
    pic = {"ok": ("picture OK", "ok"), "black": ("no picture from the card", "bad"),
           "washed": ("washed out (HDR on?)", "warn"), "missing": ("no capture card source", "bad")}.get(
        str(h.get("picture") or ""), ("picture: not checked", "warn"))
    try:
        root = L.capture_root(acct)
        gb = L.free_gb(root)
        free = (f"{gb:.0f} GB free", "ok" if gb >= float(st["free_gb"]["warn"]) else
                "warn" if gb >= float(st["free_gb"]["refuse"]) else "bad")
    except L.LabError as e:
        free = (str(e), "bad")
    hours = st["capture_hours"]
    open_ = str(hours.get("from")) <= now.strftime("%H:%M") < str(hours.get("until"))
    rec = (f"recording open until {hours.get('until')}" if open_ else
           f"recording opens at {hours.get('from')} (lab.capture_hours)", "ok" if open_ else "warn")
    try:
        rl = RL.lines(cfg)[0]
    except Exception:  # noqa: BLE001  (the strip never fails over the queue)
        rl = ""
    items = [obs_w, pic, free, rec] + ([(rl, "mute")] if rl else [])
    off = ("" if acct.active else '<div class="lab-off">Lab is switched off: takes are kept and scripted, '
                                 'nothing is rendered or posted.</div>')
    btn = "" if h.get("ok") else _btn("lab-open-obs", "Open OBS", acct.slug)
    return (off + '<div class="lab-strip">' + "".join(f'<span class="{c}">{esc(w)}</span>' for w, c in items)
            + btn + "</div>")


def _setup(t: dict) -> str:
    steps = "".join(f"<li>{esc(s)}</li>" for s in t.get("setup") or [])
    cheats = "".join(f'<li><b>{esc(c["what"])}</b>: dial <code>{esc(c["phone"])}</code></li>'
                     for c in t.get("cheats") or [])
    exp = t.get("expected") or {}
    check = str(exp.get("check") or "")
    doubt = (f'<div class="doubt">Check: {esc(check)}. The recording decides.</div>' if check in L.CHECK_OPEN else
             f'<div class="calm">Check: {esc(check)}.</div>')
    safety = t.get("safety") or {}
    sn = "".join(f'<div class="{"bad" if safety.get("level") == "caution" else "calm"}">{esc(x)}</div>'
                 for x in safety.get("notes") or [])
    return (f'<ol class="steps">{steps}</ol>' + (f'<ul class="cheats">{cheats}</ul>' if cheats else "")
            + f'<div class="calm">HUD and radar <b>{esc(t.get("hud") or "off")}</b>'
            + (" (the stars are the evidence)" if t.get("hud") == "on" else "") + ". Test save, Music volume 0, HDR off.</div>"
            + sn + doubt
            + f'<details><summary>What the wiki says (the recording decides)</summary><div>{esc(exp.get("answer") or "")}'
            + (f'<br><span class="calm">{esc(exp.get("check_note") or "")}</span>' if exp.get("check_note") else "")
            + "</div></details>")


def _record_card(acct: C.Account, t: dict, rec: Optional[dict], extra: bool = False) -> str:
    head = (f'<div class="lab-head"><b>{esc(t["id"])}</b> <span class="calm">{esc(t.get("game"))} · '
            f'{esc(t.get("area"))} · {esc(t.get("kind"))}</span></div><h3>{esc(t.get("question"))}</h3>')
    cheats = ' data-cheats="1"' if t.get("cheats") else ""
    if rec:
        btns = '<span class="calm">One take at a time: Stop the one recording first.</span>'
    else:
        btns = _btn("lab-record", "Record", acct.slug, t["id"], cheats, "go big")
    btns += _btn("lab-skip", "Skip today", acct.slug, t["id"]) + _btn("lab-drop", "Drop", acct.slug, t["id"])
    return (f'<article class="lab-card{" extra" if extra else ""}" data-test="{esc(t["id"])}">{head}{_setup(t)}'
            f'<div class="btns">{btns}</div></article>')


def _take_card(acct: C.Account, t: dict, relook: bool = False) -> str:
    tk = L.current_take(t) or {}
    sc_path = Path(tk.get("sidecar") or "/nonexistent")
    sc = L.read_json(sc_path)
    if not sc:
        return (f'<article class="lab-card" data-test="{esc(t["id"])}"><b>{esc(t["id"])}</b>: its take\'s sidecar is '
                f'gone ({esc(sc_path)})</article>')
    f = L.take_file(sc, sc_path)
    seen = L.seen_key(sc, sc_path)
    marks = "".join(f'<li>mark {esc(m["n"])} at {esc(L._clock(float(m["t"])))}'
                    + (f' ({esc(m.get("label"))})' if m.get("label") else "") + f' <span class="calm">{esc(m.get("by"))}</span> '
                    + _btn("lab-unmark", "Remove", acct.slug, t["id"], f' data-n="{esc(m["n"])}"', "link") + "</li>"
                    for m in sc.get("marks") or [])
    ch = sc.get("checks") or {}
    notes = []
    if L.checks_pending(sc):
        notes.append('<div class="calm">Checking the take (sha256, black spans, sound)...</div>')
    if ch.get("black_spans"):
        notes.append(f'<div class="bad">Black spans: {esc(", ".join(f"{L._clock(a)}-{L._clock(b)}" for a, b in ch["black_spans"]))}'
                     f' (the card lost the picture). Redo if the moment is in one.</div>')
    if ch.get("dropped_frames") is not None:
        lim = L.settings(acct)["dropped_frames"]
        d = float(ch["dropped_frames"])
        if d > float(lim["warn"]):
            notes.append(f'<div class="{"bad" if d > float(lim["redo"]) else "warn"}">OBS dropped {d:.1%} of the frames'
                         + (": Redo suggested." if d > float(lim["redo"]) else ".") + "</div>")
    if (sc.get("probe") or {}).get("hdr"):
        notes.append('<div class="bad">HDR footage: turn HDR off on the console, then Redo.</div>')
    exp = t.get("expected") or {}
    check = str(exp.get("check") or "")
    expect = (f'<div class="doubt">Check: {esc(check)}. The recording decides.</div>' if check in L.CHECK_OPEN else "")
    expect += f'<div class="calm">Expected: {esc(exp.get("answer") or "(nothing known: the recording decides)")}</div>'
    rl = ""
    if relook:
        r = t.get("relook") or {}
        frs = "".join(f'<img src="{esc(_url(str(sc_path.parent / "frames" / sc_path.stem / x.split(":", 1)[-1])))}" '
                      f'alt="frame" loading="lazy">' for x in r.get("frames") or [])
        rl = (f'<div class="relook"><b>Look again</b>: {esc(r.get("why") or "")}<div class="lab-ev">{frs}</div></div>')
    mark_ok = bool(sc.get("marks"))
    dis = "" if mark_ok else " disabled"
    btns = (_btn("lab-mark", "Mark here", acct.slug, t["id"], ' data-here="1"', "go")
            + (_btn("lab-relook-answer", "What happened was...", acct.slug, t["id"], f' data-seen="{esc(seen)}"{dis}')
               if relook else
               _btn("lab-outcome", "As expected", acct.slug, t["id"], f' data-kind="as_expected" data-seen="{esc(seen)}"{dis}', "go")
               + _btn("lab-outcome", "Different...", acct.slug, t["id"], f' data-kind="different" data-seen="{esc(seen)}"{dis}'))
            + _btn("lab-outcome", "Redo", acct.slug, t["id"], f' data-kind="redo" data-seen="{esc(seen)}"'))
    first = (sc.get("marks") or [{}])[0].get("t")
    video = (f'<video controls playsinline preload="metadata" src="{esc(_url(str(f), "#t=" + format(float(first), ".1f")) if first is not None else _url(str(f)))}" '
             f'aria-label="{esc(t["id"])} take {esc(tk.get("n"))}"></video>')
    return (f'<article class="lab-card take" data-test="{esc(t["id"])}" data-seen="{esc(seen)}">'
            f'<div class="lab-head"><b>{esc(t["id"])}</b> <span class="calm">take {esc(tk.get("n"))} · '
            f'{esc(tk.get("date"))} · {esc(L._clock(float((sc.get("probe") or {}).get("duration_s") or 0)))}</span></div>'
            f'<h3>{esc(t.get("question"))}</h3>{rl}{video}<ul class="marks">{marks or "<li class=calm>No mark yet: play it, pause on the moment, Mark here.</li>"}</ul>'
            + "".join(notes) + expect + f'<div class="btns">{btns}</div></article>')


def _untagged(acct: C.Account, bank: dict) -> str:
    items = L.untagged(acct)
    if not items:
        return ""
    opts = "".join(f'<option value="{esc(t["id"])}">{esc(t["id"])} {esc(t["question"][:60])}</option>'
                   for t in bank.get("tests") or [] if t.get("status") in L.RECORDABLE)
    rows = "".join(f'<li data-file="{esc(x["file"])}"><b>{esc(x["name"])}</b> <span class="calm">{esc(x["where"])}, '
                   f'{x["bytes"] / 1e9:.1f} GB</span> <select aria-label="Which test">{opts}</select>'
                   + _btn("lab-tag", "This is it", acct.slug, "", "", "go") + "</li>" for x in items)
    return f'<h2>Untagged recordings</h2><ul class="untagged">{rows}</ul>'


def _holds(acct: C.Account, bank: dict) -> str:
    held = [t for t in bank.get("tests") or [] if t.get("status") == "held"]
    if not held:
        return ""
    rows = "".join(f'<li><b>{esc(t["id"])}</b> {esc(t.get("question"))} <span class="calm">{esc((t.get("hold") or {}).get("why"))}'
                   f'</span> ' + _btn("lab-allow", "Allow", acct.slug, t["id"]) + _btn("lab-drop", "Drop", acct.slug, t["id"])
                   + "</li>" for t in held)
    return f'<h2>Holds <span class="n">{len(held)}</span></h2><ul class="holds">{rows}</ul>'


def _coming(acct: C.Account, bank: dict, deal: dict) -> str:
    rows = []
    for t in bank.get("tests") or []:
        if t.get("status") not in ("verified", "scripted", "rendered", "placed"):
            continue
        where = (f'{(t.get("placed") or {}).get("day")} {(t.get("placed") or {}).get("slot")}' if t.get("placed") else "")
        rows.append(f'<li><b>{esc(t["id"])}</b> {esc(t.get("status"))} {esc(where)} '
                    f'<span class="calm">{esc(t.get("question"))}</span></li>')
    head = (f'<h2>Coming up</h2><div class="calm">{esc(deal["day"])}: {len(deal["slots"])} slots'
            + (f' ({esc(deal["rule"])})' if deal.get("rule") else "") + f'; {deal["filled"]} filled, {deal["stock"]} '
            f'on the way.</div>')
    return head + (f'<ul class="coming">{"".join(rows)}</ul>' if rows else '<div class="calm">Nothing scripted yet.</div>')


def parts(cfg: C.Config, acct: C.Account, now: datetime) -> Dict[str, str]:
    """Each part of one Lab account's page, by id (the page swaps a part only when its hash changed)."""
    pre = f"lab-{acct.slug}-"
    out = {pre + "strip": _strip(cfg, acct, now)}
    try:
        bank = L.bank_read(acct)
        deal = L.deal(cfg, acct, now=now)
    except L.LabError as e:
        out[pre + "record"] = f'<div class="bad">{esc(e)}</div>'
        return out
    rec = L.recording(acct)
    if rec:
        marks = "".join(f'<li>mark {esc(m["n"])} at {esc(L._clock(float(m["t"])))} '
                        + _btn("lab-unmark", "Remove", acct.slug, rec["test"], f' data-n="{esc(m["n"])}"', "link") + "</li>"
                        for m in rec.get("marks") or [])
        warn, stop_m = L.settings(acct)["take_warn_minutes"], L.settings(acct)["take_stop_minutes"]
        out[pre + "rec"] = (f'<div class="lab-rec" data-started="{esc(rec.get("started_ts"))}" data-warn="{esc(warn)}" '
                            f'data-stop="{esc(stop_m)}"><b>Recording {esc(rec["test"])}</b> take {esc(rec.get("take"))} '
                            f'<span class="timer">0:00</span> '
                            + _btn("lab-mark", "Mark (space)", acct.slug, rec["test"], ' data-live="1"', "go big")
                            + _btn("lab-stop", "Stop", acct.slug, rec["test"], "", "big")
                            + f'<ul class="marks">{marks}</ul></div>')
    else:
        out[pre + "rec"] = ""
    by_id = {t["id"]: t for t in bank.get("tests") or []}
    rec_cards = "".join(_record_card(acct, by_id[i], rec) for i in deal["record"] if i in by_id)
    extra = "".join(_record_card(acct, by_id[i], rec, extra=True) for i in deal["extra"] if i in by_id)
    out[pre + "record"] = (f'<h2>Record for {esc(deal["day"])}: {len(deal["record"])}</h2>'
                           + (f'<div class="grid lab-grid">{rec_cards}</div>' if rec_cards else
                              '<div class="calm">Nothing more is needed for that day.</div>')
                           + (f'<h3 class="calm">More if you have time</h3><div class="grid lab-grid">{extra}</div>'
                              if extra else ""))
    w = L.waiting(acct, bank)
    takes = "".join(_take_card(acct, t) for t in w["outcome"]) + "".join(_take_card(acct, t, relook=True)
                                                                       for t in w["relook"])
    out[pre + "waiting"] = ((f'<h2>Waiting for you <span class="n">{len(w["outcome"]) + len(w["relook"])}</span></h2>'
                             f'<div class="grid lab-grid">{takes}</div>') if takes else "")
    out[pre + "untagged"] = _untagged(acct, bank)
    out[pre + "holds"] = _holds(acct, bank)
    out[pre + "coming"] = _coming(acct, bank, deal)
    return out


def _touch(cfg: C.Config, acct: C.Account, now: datetime) -> None:
    """On a draw: the take watchdog, the inbox (every 60 s) and OBS's health (every 30 s)."""
    L.watchdog(cfg, acct, now)
    t = time.time()
    if t - _LAST_SCAN.get(acct.slug, 0) >= SCAN_EVERY_S:
        _LAST_SCAN[acct.slug] = t
        try:
            L.inbox_scan(cfg, acct, now, background=True)
        except Exception:  # noqa: BLE001  (the page never fails over the inbox)
            pass
    if L.obs_path(acct).is_file() and not L.recording(acct) and t - _LAST_OBS.get(acct.slug, 0) >= OBS_EVERY_S:
        _LAST_OBS[acct.slug] = t
        try:
            L.obs_check(acct)
        except Exception:  # noqa: BLE001
            pass


def live(cfg: C.Config, now: Optional[datetime] = None) -> dict:
    now = now or cfg.now()
    out: Dict[str, str] = {}
    recording = False
    for acct in L.lab_accounts(cfg):
        _touch(cfg, acct, now)
        out.update(parts(cfg, acct, now))
        recording = recording or bool(L.recording(acct))
    wrapped = {k: _wrap(k, v) for k, v in out.items()}
    stamp = hashlib.sha1(json.dumps(wrapped, sort_keys=True).encode()).hexdigest()[:16]
    return {"stamp": stamp, "parts": wrapped, "order": list(wrapped), "poll": POLL_REC if recording else POLL_IDLE}


CSS = """
.lab-strip{display:flex;flex-wrap:wrap;gap:6px 14px;align-items:center;padding:8px 0 14px;font-size:13px}
.lab-strip .ok{color:var(--ok)}.lab-strip .warn{color:var(--warn)}.lab-strip .bad{color:var(--bad)}
.lab-strip .mute{color:var(--sub)}.lab-off{padding:8px 12px;border:1px solid var(--warn);border-radius:6px;
color:var(--warn);margin-bottom:10px}
.lab-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(340px,1fr));gap:16px}
.lab-card{background:var(--card);border:1px solid var(--line);border-radius:6px;padding:12px 14px}
.lab-card.extra{opacity:.85}.lab-card h3{font-size:16px;margin:6px 0 8px;line-height:1.3}
.lab-card .steps{font-size:16px;line-height:1.45;padding-left:22px;margin:6px 0}
.lab-card .cheats{font-size:14px;margin:4px 0;padding-left:18px}.lab-card code{font-size:15px}
.lab-card video{width:100%;aspect-ratio:16/9;background:#000;border-radius:4px;margin:6px 0}
.lab-card .marks{margin:4px 0;padding-left:18px;font-size:13px}
.calm{color:var(--sub);font-size:13px}.doubt{color:var(--warn);font-weight:600;font-size:13px;margin:4px 0}
.bad{color:var(--bad)}.warn{color:var(--warn)}
.lab-rec{position:sticky;top:0;z-index:5;background:var(--badbg);border:1px solid var(--bad);border-radius:6px;
padding:10px 14px;margin-bottom:14px;display:flex;flex-wrap:wrap;gap:10px;align-items:center}
.lab-rec .timer{font-variant-numeric:tabular-nums;font-size:20px;font-weight:650}.lab-rec .timer.late{color:var(--bad)}
.lab-rec .marks{flex-basis:100%;margin:0;padding-left:18px;font-size:13px}
.btns{display:flex;flex-wrap:wrap;gap:8px;margin-top:10px}button.big{font-size:15px;padding:9px 18px}
button.link{background:none;border:0;color:var(--blue);padding:0;cursor:pointer}
.relook{border-left:3px solid var(--warn);padding:4px 8px;margin:6px 0}
.lab-ev{display:flex;gap:4px;flex-wrap:wrap}.lab-ev img{width:31%;aspect-ratio:16/9;object-fit:cover;border-radius:3px}
.untagged li,.holds li,.coming li{margin:6px 0}.lab-part{margin-bottom:22px}
"""

JS = """
const TOKEN=__TOKEN__;let STAMP=__STAMP__,POLL=__POLL__,inflight=null,toastTimer=0;
const toastEl=document.getElementById('toast');
function who(){const w=document.getElementById('who');return w?w.value:''}
function toast(msg,bad){toastEl.textContent=msg;toastEl.className=bad?'bad':'';toastEl.hidden=false;
  clearTimeout(toastTimer);toastTimer=setTimeout(()=>{toastEl.hidden=true},bad?15000:7000)}
toastEl.addEventListener('click',()=>{toastEl.hidden=true});
function frag(h){const t=document.createElement('template');t.innerHTML=h.trim();return t.content.firstElementChild}
function busy(node){const f=document.activeElement;
  if(f&&/^(TEXTAREA|SELECT|INPUT)$/.test(f.tagName)&&node.contains(f))return true;
  return [...node.querySelectorAll('video')].some(v=>!v.paused)}
function apply(d){if(!d||d.stamp===STAMP)return;STAMP=d.stamp;POLL=d.poll||POLL;const main=document.getElementById('lab');
  let prev=null;for(const id of d.order){let el=document.getElementById(id);const n=frag(d.parts[id]);
    if(!el){el=n;if(prev)prev.after(el);else main.prepend(el)}
    else if(el.dataset.h!==n.dataset.h&&!busy(el)){el.replaceWith(n);el=n}
    prev=el}
  tick()}
async function refresh(){if(inflight)return inflight;inflight=(async()=>{try{const r=await fetch('/lab/live',{cache:'no-store'});
  if(r.ok)apply(await r.json())}catch(e){}finally{inflight=null}})();return inflight}
async function act(body,btn){const box=btn.closest('.lab-card')||btn.closest('section')||btn.parentElement;
  const btns=[...box.querySelectorAll('button')];btns.forEach(b=>b.disabled=true);toast('Working...');
  try{const r=await fetch('/act',{method:'POST',headers:{'Content-Type':'application/json','X-Token':TOKEN},
      body:JSON.stringify(Object.assign({by:who()},body))});
    let d;try{d=await r.json()}catch(e){d={ok:false,message:'The page sent an answer it could not read. Reload it.'}}
    if(r.status===403&&d.stale){toast('This tab was open before the page restarted. Reloading...');setTimeout(()=>location.reload(),900);return}
    toast(d.message||(r.ok?'Done.':'That did not work.'),!(r.ok&&d.ok))}
  catch(e){toast('Could not reach the review page (it may be restarting). Try again in a few seconds.',true)}
  finally{btns.forEach(b=>b.disabled=false)}STAMP='';await refresh()}
document.addEventListener('click',ev=>{const b=ev.target.closest('button[data-op]');if(!b||b.disabled)return;
  const op=b.dataset.op,account=b.dataset.acct,test=b.dataset.test,card=b.closest('.lab-card');
  if(op==='lab-record'){let ok=false;if(b.dataset.cheats){if(!confirm(test+' dials console cheats. Is the dedicated test save loaded?'))return;ok=true}
    act({op,account,test,cheats_ok:ok},b);return}
  if(op==='lab-mark'){if(b.dataset.here){const v=card&&card.querySelector('video');if(!v){toast('No take to mark.',true);return}
      const label=prompt('Mark at '+Math.floor(v.currentTime/60)+':'+String(Math.floor(v.currentTime%60)).padStart(2,'0')+'. A word for it? (optional)','');
      if(label===null)return;act({op,account,test,t:Math.round(v.currentTime*100)/100,label},b);return}
    act({op,account,test},b);return}
  if(op==='lab-unmark'){act({op,account,test,n:+b.dataset.n},b);return}
  if(op==='lab-outcome'){const kind=b.dataset.kind,seen=b.dataset.seen;let text='';
    if(kind==='different'){text=prompt('What happened? (it is what the clip will say)','');if(!text)return}
    if(kind==='redo'){text=prompt('Redo '+test+'. Why? (optional)','');if(text===null)return}
    act({op,account,test,kind,text,seen},b);return}
  if(op==='lab-relook-answer'){const text=prompt('What happened was...','');if(!text)return;act({op,account,test,text,seen:b.dataset.seen},b);return}
  if(op==='lab-drop'){const why=prompt('Drop '+test+'. Why?','');if(!why)return;act({op,account,test,why},b);return}
  if(op==='lab-tag'){const li=b.closest('li'),sel=li.querySelector('select');if(!sel||!sel.value)return;
    if(!confirm('File '+li.querySelector('b').textContent+' as '+sel.value+'?'))return;act({op,account,test:sel.value,file:li.dataset.file},b);return}
  act({op,account,test},b)});
document.addEventListener('keydown',ev=>{if(ev.key!==' '||/^(TEXTAREA|SELECT|INPUT|BUTTON|VIDEO)$/.test((ev.target||{}).tagName||''))return;
  const b=document.querySelector('button[data-op="lab-mark"][data-live]');if(b){ev.preventDefault();b.click()}});
document.addEventListener('play',ev=>{document.querySelectorAll('video').forEach(v=>{if(v!==ev.target&&!v.paused)v.pause()})},true);
function tick(){document.querySelectorAll('.lab-rec').forEach(r=>{const s=+r.dataset.started;if(!s)return;
  const el=r.querySelector('.timer'),t=Math.max(0,Date.now()/1000-s),m=Math.floor(t/60);
  el.textContent=m+':'+String(Math.floor(t%60)).padStart(2,'0');el.classList.toggle('late',m>=+r.dataset.warn)})}
setInterval(tick,1000);tick();
(function loop(){setTimeout(async()=>{if(!document.hidden)await refresh();loop()},POLL*1000)})();
document.addEventListener('visibilitychange',()=>{if(!document.hidden)refresh()});
"""


def page(cfg: C.Config, tok: str, base_css: str, tabs_html: str, now: Optional[datetime] = None) -> str:
    """The Lab tab as the browser first draws it (/live keeps it current)."""
    d = live(cfg, now)
    if len(cfg.approvers) == 1:
        signer = f'<input type="hidden" id="who" value="{esc(cfg.approvers[0])}">'
    else:
        people = "".join(f"<option>{esc(p)}</option>" for p in cfg.approvers)
        signer = f'<span class="who">Confirming as</span><select id="who" aria-label="Confirming as">{people}</select>'
    body = "".join(d["parts"][k] for k in d["order"]) or (
        '<div class="calm">No Lab account on this machine (an account whose maker is "lab" in config/accounts.json).</div>')
    js = (JS.replace("__TOKEN__", json.dumps(tok)).replace("__STAMP__", json.dumps(d["stamp"]))
          .replace("__POLL__", str(d["poll"])))
    return ("<!doctype html><html lang=en><head><meta charset=utf-8>"
            "<meta name=viewport content='width=device-width,initial-scale=1'>"
            f"<title>Clip Review · Lab</title><style>{base_css}{CSS}</style></head><body>"
            f"<header><h1>Clip Review</h1>{tabs_html}<span class=grow></span>{signer}</header>"
            f'<main id="lab">{body}</main><div id="toast" role="status" aria-live="polite" hidden></div>'
            f"<script>{js}</script></body></html>")
