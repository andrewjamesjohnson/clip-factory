"""The page kit of the social tabs (Inbox, Cards, Reports, Log, Week): one look, one header, one set of helpers.

Every social tab is drawn by socialui.shell with review.CSS + statspage.CSS as its base, so it looks like Clips and
Stats: the same tokens, flat 1px lines, 6 to 8px corners, the underline on the current tab, no glows or gradients.
New classes carry the sx- prefix (and each page its own: ib- au- cd- rp- id- lg- wk-), so nothing collides with the
Clips page's generic names (.top, .card, .grid ...). Phones from 280px: one column, 40px targets, 16px inputs.

window.SX (JS below) is the page side: SX.act posts a click to /act with the page's token exactly as the Clips page
does (a tab open across a restart reloads itself), SX.poll re-reads a page's /data and redraws only when its stamp
changed, and the rest are small helpers (el, esc, ago, toast, ask, seg, chips, qs, setq).

CLIPS_CSS is what the Clips page itself gains: the Today band (.sx-brief), the Decline and Make again chooser (.dx),
the calendar's Week link and card strip, and the phone rules for the tabs.
"""
from __future__ import annotations

import hashlib
import html
import json
import os
import urllib.parse
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

from . import config as C


def esc(s: object) -> str:
    return html.escape(str(s), quote=True)


# ------------------------------------------------------------------------------------------------------ the look
CSS = """
body.sx main{display:flex;flex-direction:column;gap:16px}
.sx-banner{padding:9px 20px;font-size:13.5px;border-bottom:1px solid var(--line)}
.sx-banner.bad{color:var(--bad);background:var(--badbg);font-weight:600}
.sx-banner.warn{color:var(--warn);background:var(--warnbg)}
.sx-subnav{display:flex;gap:2px;flex-wrap:wrap;padding:6px 20px 0;border-bottom:1px solid var(--line)}
.sx-subnav a{display:inline-flex;align-items:center;height:32px;padding:0 10px;color:var(--sub);text-decoration:none;font-size:13.5px;font-weight:550;border-bottom:2px solid transparent;white-space:nowrap}
.sx-subnav a:hover{color:var(--ink)}
.sx-subnav a[aria-current=page]{color:var(--ink);border-bottom-color:var(--ink)}
.sx-head{display:flex;align-items:center;gap:10px;flex-wrap:wrap;min-height:34px}
.sx-head h2{font-size:18px}
.sx-split{display:grid;grid-template-columns:minmax(280px,400px) minmax(0,1fr);gap:16px;align-items:start}
.sx-list{background:var(--card);border:1px solid var(--line);border-radius:8px;overflow:hidden;display:flex;flex-direction:column;min-width:0}
.sx-row{display:flex;gap:10px;align-items:flex-start;padding:10px 12px;border:0;border-bottom:1px solid var(--line);background:none;color:var(--ink);text-align:left;text-decoration:none;font:inherit;min-width:0;width:100%;height:auto;border-radius:0}
.sx-row:last-child{border-bottom:0}
.sx-row:hover{background:var(--bg)}
.sx-row[aria-selected=true],.sx-row[aria-current]{background:var(--bluebg)}
.sx-detail{background:var(--card);border:1px solid var(--line);border-radius:8px;padding:16px;min-width:0;display:flex;flex-direction:column;gap:12px}
.sx-chips{display:flex;flex-wrap:wrap;gap:6px}
.sx-chip{display:inline-flex;align-items:center;gap:5px;height:28px;padding:0 9px;border:1px solid var(--line);border-radius:6px;background:var(--card);color:var(--ink);font-size:12.5px;font-weight:550;text-decoration:none;white-space:nowrap}
.sx-chip:hover{border-color:var(--sub)}
.sx-chip[aria-pressed=true]{background:var(--mute);border-color:var(--ink)}
.sx-chip.warn{color:var(--warn);border-color:var(--warn)}
.sx-chip.bad{color:var(--bad);border-color:var(--bad);background:var(--badbg)}
.sx-empty{color:var(--sub);font-size:13.5px;padding:28px 16px;text-align:center}
.sx-form{display:flex;flex-direction:column;gap:10px}
.sx-form label{display:flex;flex-direction:column;gap:4px;font-size:12.5px;color:var(--sub)}
.sx-form input:not([type=checkbox]):not([type=radio]),.sx-form textarea,.sx-form select{font:inherit;font-size:13px;padding:6px 8px;border:1px solid var(--line);border-radius:6px;background:var(--card);color:var(--ink);width:100%}
.sx-form input:not([type=checkbox]):not([type=radio]){height:32px}
.sx-form textarea{min-height:96px;resize:vertical}
.sx-form .sx-check{flex-direction:row;align-items:center;gap:7px;color:var(--ink)}
.sx-muted{color:var(--sub);font-size:12.5px}
@media (max-width:600px){.sx-banner{padding:9px 16px}
.sx-subnav{padding:4px 16px 0;flex-wrap:nowrap;overflow-x:auto;scrollbar-width:none}.sx-subnav::-webkit-scrollbar{display:none}
.sx-subnav a{height:40px;flex:0 0 auto}
.sx-split{grid-template-columns:minmax(0,1fr)}.sx-detail{padding:14px}
.sx-chip{height:40px;min-width:40px}.sx-row{min-height:40px}
body.sx button:not(.sx-row){height:auto;min-height:40px;min-width:40px;font-size:16px}
body.sx select{height:40px;min-width:40px;font-size:16px}
body.sx input:not([type=checkbox]):not([type=radio]),body.sx .sx-form input:not([type=checkbox]):not([type=radio]){height:40px;font-size:16px}
body.sx textarea,body.sx .sx-form textarea{font-size:16px}
body.sx .seg button{height:40px}}
"""

CLIPS_CSS = """
.sx-brief{display:flex;flex-wrap:wrap;gap:6px;margin-bottom:20px}
.sx-brief a,.sx-brief span{display:inline-block;height:30px;line-height:28px;max-width:100%;overflow:hidden;text-overflow:ellipsis;padding:0 10px;border:1px solid var(--line);border-radius:6px;background:var(--card);color:var(--ink);font-size:13px;font-weight:550;text-decoration:none;white-space:nowrap}
.sx-brief a:hover{border-color:var(--sub)}
.sx-brief .warn{color:var(--warn);border-color:var(--warn)}
.sx-brief .bad{color:var(--bad);border-color:var(--bad);background:var(--badbg)}
.dx{display:flex;flex-direction:column;gap:8px;padding:10px;border:1px solid var(--line);border-radius:6px;background:var(--bg)}
.dx-h{font-weight:600;font-size:13px}
.dx-chips{display:flex;flex-wrap:wrap;gap:6px}
.dx-r{height:28px;padding:0 9px;font-size:12.5px;font-weight:550;color:var(--sub)}
.dx-r[aria-pressed=true]{color:var(--ink);background:var(--mute);border-color:var(--ink)}
.dx-note{font:inherit;font-size:13px;height:32px;width:100%;padding:0 8px;border:1px solid var(--line);border-radius:6px;background:var(--card);color:var(--ink)}
.dx-btns{display:flex;gap:6px;flex-wrap:wrap}
.cal-weeklink{font-size:13px;color:var(--blue);text-decoration:none;white-space:nowrap}
.cal-weeklink:hover{text-decoration:underline}
.strip2{display:flex;gap:1px;flex:1;height:3px}
.strip2 b{flex:1;background:var(--mute)}
.row2 .strip2{height:3px}
@media (max-width:600px){.tabs{overflow-x:auto;flex-wrap:nowrap;max-width:100%;scrollbar-width:none}
.tabs::-webkit-scrollbar{display:none}
.tabs a{height:40px;flex:0 0 auto}
.cal-head{flex-wrap:wrap}.cal-head button,.dx-r{height:40px;min-width:40px}
.cal-weeklink{display:inline-flex;align-items:center;justify-content:center;height:40px;min-width:40px}
.switch a{height:40px}
input,textarea,.tt textarea,.dx-note{font-size:16px}.dx-note{height:40px}
.sx-brief a,.sx-brief span{height:40px;line-height:38px}
.days{flex:1 1 100%;flex-wrap:wrap;min-width:0}.days select{flex:1 1 90px;min-width:0;max-width:100%}
#needs-grid,.grid,.grid.big{grid-template-columns:minmax(0,1fr)}}
"""

# ------------------------------------------------------------------------------------------------------ the page side
JS = r"""
(() => {
'use strict';
const TOKEN = __TOKEN__, ACCT = __ACCT__, WHO = __WHO__;
let toastT = 0;
const ESC = {'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'};
function esc(s) { return String(s == null ? '' : s).replace(/[&<>"']/g, c => ESC[c]); }
function el(tag, attrs, kids) {
  const n = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v == null || v === false) continue;
    if (k === 'class') n.className = v;
    else if (k === 'style') n.style.cssText = v;
    else if (k.startsWith('on')) n.addEventListener(k.slice(2), v);
    else n.setAttribute(k, v === true ? '' : v);
  }
  for (const c of [].concat(kids == null ? [] : kids)) if (c != null) n.append(c.nodeType ? c : document.createTextNode(String(c)));
  return n;
}
function toast(msg, bad) {
  const t = document.getElementById('toast'); if (!t) return;
  t.textContent = msg; t.className = bad ? 'bad' : ''; t.hidden = false;
  clearTimeout(toastT); toastT = setTimeout(() => { t.hidden = true; }, bad ? 15000 : 7000);
}
const toastEl = document.getElementById('toast');
if (toastEl) toastEl.addEventListener('click', () => { toastEl.hidden = true; });
function ask(words) { return !words || window.confirm(words); }
function ago(iso) {
  const t = Date.parse(iso || ''); if (isNaN(t)) return '';
  const s = Math.max(0, (Date.now() - t) / 1000);
  if (s < 60) return 'just now';
  if (s < 3600) return Math.round(s / 60) + ' min ago';
  if (s < 86400) { const h = Math.round(s / 3600); return h + (h === 1 ? ' hour ago' : ' hours ago'); }
  const d = Math.round(s / 86400); return d + (d === 1 ? ' day ago' : ' days ago');
}
/* one click to /act, as the Clips page sends it: the page's token, who signs, a tab from before a restart reloads */
async function act(body, btn) {
  const box = btn ? (btn.closest('[data-x-form]') || btn.closest('.sx-detail') || btn.parentElement) : null;
  const btns = box ? [...box.querySelectorAll('button')] : [];
  btns.forEach(b => { b.disabled = true; });
  toast('Working...');
  try {
    const r = await fetch('/act', {method: 'POST', headers: {'Content-Type': 'application/json', 'X-Token': TOKEN},
      body: JSON.stringify(Object.assign({by: WHO}, body))});
    let d; try { d = await r.json(); } catch (e) { d = {ok: false, message: 'The page sent an answer it could not read. Reload it.'}; }
    if (r.status === 403 && d.stale) {
      toast('This tab was open before the page restarted. Reloading...'); setTimeout(() => location.reload(), 900);
      return {ok: false, message: d.message || ''};
    }
    const ok = !!(r.ok && d.ok);
    toast(d.message || (r.ok ? 'Done.' : 'That did not work.'), !ok);
    return {ok, message: d.message || ''};
  } catch (e) {
    const m = 'Could not reach the review page (it may be restarting). Try again in a few seconds.';
    toast(m, true); return {ok: false, message: m};
  } finally { btns.forEach(b => { b.disabled = false; }); }
}
/* re-read a page's JSON every ms (url may be a function), and call onData only when its stamp changed */
function poll(url, ms, onData) {
  let stamp = null, busy = false, timer = 0, every = ms;
  async function load(force) {
    if (busy || (document.hidden && !force)) return;
    busy = true;
    try {
      const r = await fetch(typeof url === 'function' ? url() : url, {cache: 'no-store'});
      if (r.ok) { const d = await r.json(); if (force || !d || d.stamp !== stamp) { stamp = d ? d.stamp : null; onData(d); } }
    } catch (e) {}
    finally { busy = false; }
  }
  function arm() { clearInterval(timer); timer = setInterval(() => load(false), every); }
  document.addEventListener('visibilitychange', () => { if (!document.hidden) load(true); });
  arm(); load(true);
  return {now: () => load(true), every: n => { every = n; arm(); }};
}
/* a segmented switch (Stats' .seg): opts [{id, l}], one pressed */
function seg(root, opts, cur, pick) {
  root.replaceChildren(...opts.map(o => el('button', {type: 'button', 'aria-pressed': String(o.id === cur), onclick: () => pick(o.id)}, o.l)));
  reveal(root);
}
/* a row that scrolls sideways on a phone has no scrollbar, so bring its current item into view (sideways only:
   the page itself never moves) */
function reveal(row) {
  try {
    if (!row || row.scrollWidth <= row.clientWidth) return;
    const on = row.querySelector('[aria-current],[aria-pressed="true"]'); if (!on) return;
    const r = row.getBoundingClientRect(), b = on.getBoundingClientRect();
    row.scrollLeft += (b.left + b.width / 2) - (r.left + r.width / 2);
  } catch (e) {}
}
/* chips that switch on and off: opts [{id, l}], set holds the ones on */
function chips(root, opts, set, toggle) {
  root.replaceChildren(...opts.map(o => el('button', {type: 'button', class: 'sx-chip', 'aria-pressed': String(set.has(o.id)), onclick: () => toggle(o.id)}, o.l)));
}
function qs(name) { try { return new URLSearchParams(location.search).get(name) || ''; } catch (e) { return ''; } }
function setq(name, v) {
  try {
    const u = new URL(location.href);
    if (v == null || v === '') u.searchParams.delete(name); else u.searchParams.set(name, v);
    history.replaceState(null, '', u.pathname + u.search + u.hash);
  } catch (e) {}
}
try {
  if (window.matchMedia('(max-width:600px)').matches) {
    const t = document.querySelector('.tabs a[aria-current]'); if (t) t.scrollIntoView({block: 'nearest', inline: 'center'});
    document.querySelectorAll('.sx-subnav').forEach(reveal);
  }
} catch (e) {}
window.SX = {TOKEN, ACCT, WHO, act, poll, el, esc, ago, toast, ask, seg, chips, reveal, qs, setq};
})();
"""


def _js_value(v: object) -> str:
    return json.dumps(v).replace("</", "<\\/")


def shell(cfg: C.Config, key: str, title: str, tok: str, base_css: str, tabs_html: str, switch_html: str, acct: str,
          main_html: str, css: str = "", js: str = "", subnav: str = "", head_extra: str = "") -> str:
    """A whole social tab: the Clip Review header (its tabs and account switch), the Stop and holds banner, the
    tab's own views, its main and the toast. `js` runs after window.SX is set."""
    who = cfg.approvers[0] if cfg.approvers else ""
    kit = (JS.replace("__TOKEN__", _js_value(tok)).replace("__ACCT__", _js_value(acct))
           .replace("__WHO__", _js_value(who)))
    return ("<!doctype html><html lang=en><head><meta charset=utf-8>"
            "<meta name=viewport content='width=device-width,initial-scale=1'>"
            f"<title>Clip Review · {esc(title)}</title><style>{base_css}{CSS}{css}</style></head>"
            f'<body class="sx sx-{esc(key)}">'
            f"<header><h1>Clip Review</h1>{tabs_html}{switch_html}<span class=grow></span>{head_extra}</header>"
            f"{banner(cfg)}{subnav}<main>{main_html}</main>"
            '<div id="toast" role="status" aria-live="polite" hidden></div>'
            f"<script>{kit}{js}</script></body></html>")


# ------------------------------------------------------------------------------------------------------ the banner
FAMILY_WORDS = {"publish": "posts and stories", "comment": "comments and replies", "message": "messages",
                "moderation": "hiding comments", "read": "reading", "*": "everything social"}
PLAT_WORDS = {"facebook": "Facebook", "instagram": "Instagram", "youtube": "YouTube"}


def _when(v: object) -> Optional[datetime]:
    try:
        t = datetime.fromisoformat(str(v).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    return t if t.tzinfo else t.replace(tzinfo=timezone.utc)


def _read(p: Path) -> dict:
    try:
        d = json.loads(p.read_text())
    except (OSError, ValueError):
        return {}
    return d if isinstance(d, dict) else {}


def banner(cfg: C.Config, now: Optional[datetime] = None) -> str:
    """The red line when Stop everything is on, and one line per hold still running (Meta or YouTube asked us to
    wait). '' when there is none, when the outward part is not built, or on any error: a banner never breaks a page."""
    try:
        from . import registry as REG
        ow = REG.load("outward")
        if ow is None:
            return ""
        out: List[str] = []
        stopped = getattr(ow, "stopped", None)
        words = stopped(cfg) if callable(stopped) else ""
        if words:
            out.append(f'<div class="sx-banner bad" role="alert">{esc(words)}</div>')
        now = now or datetime.now(timezone.utc)
        tz = cfg.tz()
        for a in own_accounts(cfg, "all"):
            holds = _read(C.STATE / a.slug / "outward" / "holds.json").get("holds") or {}
            for hk, h in sorted(holds.items()):
                if not isinstance(h, dict):
                    continue
                until = _when(h.get("until"))
                if until is not None and until <= now:
                    continue
                plat, _, fam = str(hk).partition("|")
                what = f"{a.name} on {PLAT_WORDS.get(plat, plat)}: {FAMILY_WORDS.get(fam, fam)} paused"
                what += f" until {until.astimezone(tz).strftime('%b %d %H:%M')}" if until is not None else ""
                why = str(h.get("why") or "").strip().rstrip(".")
                out.append(f'<div class="sx-banner warn">{esc(what + "." + (" " + why + "." if why else ""))}</div>')
        return "".join(out)
    except Exception:  # noqa: BLE001  (a page never goes down over its banner)
        return ""


# ------------------------------------------------------------------------------------------------------ helpers
def _with_acct(href: str, acct: str) -> str:
    """A local link that keeps the account picked (before any #part of it)."""
    if not acct or acct == "all" or "acct=" in href:
        return href
    path, hash_, frag = href.partition("#")
    return path + ("&" if "?" in path else "?") + "acct=" + urllib.parse.quote(acct) + hash_ + frag


def subnav(parent: str, current: str, acct: str, own: Sequence[Tuple[str, str, str]] = ()) -> str:
    """The row of views under a tab: the tab's own views (own: (key, href, label)) and then each built page under
    it (Automations under Inbox, Ideas under Reports). The account picked goes with every link. '' for one or none."""
    from . import registry as REG
    items = [(str(k), str(h), str(w)) for k, h, w in own] + [(p.key, p.url, p.label) for p in REG.children(parent)]
    if len(items) < 2:
        return ""
    return ('<nav class="sx-subnav" aria-label="Views">' + "".join(
        f'<a href="{esc(_with_acct(h, acct))}"{" aria-current=page" if k == current else ""}>{esc(w)}</a>'
        for k, h, w in items) + "</nav>")


def own_accounts(cfg: C.Config, acct: str = "all") -> List[C.Account]:
    """The owner's own accounts a social tab shows: never a client's (test_clients holds the owner's pages equal
    with a client added), only accounts the owner signs, and only the one picked with ?acct= when one is."""
    from . import accounts as AC
    mine = [a for a in AC.own_view(cfg).accounts.values() if AC.signs_here(cfg, a)]
    if acct and acct != "all":
        return [a for a in mine if a.slug == acct]
    return mine


def need_page(page: bool, what: str) -> None:
    """Refuse anything that speaks for the owner unless a person clicked it on the page (review._from_page)."""
    if not page:
        raise SystemExit("Only a click on the review page can " + what + ".")


def stamp(body: Dict) -> Dict:
    """Put a page's data stamp on it (a hash of everything else in it): the page redraws only when it changes."""
    body.pop("stamp", None)
    body["stamp"] = hashlib.sha1(json.dumps(body, sort_keys=True, default=str).encode()).hexdigest()[:16]
    return body


def file_url(p: str) -> str:
    """/file?p=<path>&v=<size and time>: the URL a file under state/ is shown by (img-src 'self' allows no other)."""
    try:
        st = os.stat(p)
        v = f"{st.st_size:x}{st.st_mtime_ns:x}"
    except OSError:
        v = "0"
    return f"/file?p={urllib.parse.quote(str(p))}&v={v}"


def brief_html(chips: Sequence[dict], acct: str = "all") -> str:
    """The Today band's chips, each a link where it has one (keeping the account picked): '' when there is nothing to
    say."""
    out = []
    for c in chips or []:
        if not isinstance(c, dict) or not str(c.get("words") or "").strip():
            continue
        level = str(c.get("level") or "")
        cls = f' class="{level}"' if level in ("warn", "bad") else ""
        href = str(c.get("href") or "")
        w = esc(c["words"])                      # the whole words on hover: a long chip is cut with an ellipsis
        if href.startswith("/"):
            out.append(f'<a{cls} href="{esc(_with_acct(href, acct))}" title="{w}">{w}</a>')
        else:
            out.append(f'<span{cls} title="{w}">{w}</span>')
    return "".join(out)
