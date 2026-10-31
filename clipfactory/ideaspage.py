"""Clip Review, Reports, Ideas (/reports/ideas): what is winning in each account's niche (clipfactory/ideas.py).

For each of the owner's accounts: the best Shorts of the benchmark channels this week by views per hour (title linked
out, channel, views per hour, and the length only when yt-dlp gives one, which its flat Shorts listing does not), the
title patterns of the winners against the bottom half and against our own titles (flat bars), and the channel list
with an Add form, Remove, and "Add the scout's channels" (the scout's own YouTube channels, suggested, never added
without his click).

Files only: the page, its /data and its /part never run yt-dlp or reach a platform. Every click that changes the list
(ideas-add, ideas-remove, ideas-seed, ideas-release) needs the page itself (review._from_page) and a confirm that names
the exact channel and the account it goes to. Classes carry the id- prefix; phones from 280px get stacked rows, 40px
targets and 16px inputs (socialui.CSS).
"""
from __future__ import annotations

import os
from datetime import datetime
from typing import List, Optional, Tuple

from . import config as C
from . import ideas as I
from . import registry as REG
from . import socialui as SX

esc = SX.esc
DATA_URL = "/reports/ideas/data"
PART_URL = "/reports/ideas/part"

CSS = """
.id-top{display:flex;flex-wrap:wrap;gap:8px 16px;align-items:center;justify-content:space-between}
.id-top p{margin:3px 0 0;color:var(--sub);font-size:12.5px;max-width:760px}
#id-root{display:flex;flex-direction:column;gap:16px}
.id-acct{display:flex;flex-direction:column;gap:16px;min-width:0}
.id-acct[hidden]{display:none}
.id-panel{background:var(--card);border:1px solid var(--line);border-radius:8px;padding:16px;min-width:0}
.id-panel h3{font-size:14px;font-weight:650;margin:0 0 4px}
.id-panel .id-sub{color:var(--sub);font-size:12.5px;margin:0 0 12px}
.id-state{display:flex;flex-wrap:wrap;gap:6px 14px;align-items:center;font-size:13.5px;color:var(--sub)}
.id-state b{color:var(--ink);font-weight:600}
.id-state .warn{color:var(--warn)}.id-state .bad{color:var(--bad)}
.id-grid{display:grid;grid-template-columns:minmax(0,1.4fr) minmax(0,1fr);gap:16px;align-items:start}
.id-win{display:flex;flex-direction:column}
.id-wr{display:grid;grid-template-columns:minmax(0,1fr) minmax(90px,170px) 104px 64px;gap:10px;align-items:baseline;padding:8px 0;border-bottom:1px solid var(--line);font-size:13.5px;min-width:0}
.id-wr:last-child{border-bottom:0}
.id-win.nolen .id-wr{grid-template-columns:minmax(0,1fr) minmax(90px,170px) 104px}
.id-wh{color:var(--sub);font-size:12px;font-weight:600;padding-top:0}
.id-wr .n{text-align:right;font-variant-numeric:tabular-nums;white-space:nowrap}
.id-wr .c{color:var(--sub);overflow-wrap:anywhere}
.id-wr a{color:var(--ink);text-decoration:none;overflow-wrap:anywhere;min-width:0}
.id-wr a:hover{text-decoration:underline}
.id-new{display:inline-block;margin-left:6px;padding:0 5px;border:1px solid var(--line);border-radius:4px;color:var(--sub);font-size:11.5px;font-weight:550;vertical-align:1px}
.id-legend{display:flex;flex-wrap:wrap;gap:6px 14px;font-size:12px;color:var(--sub);margin:0 0 8px}
.id-legend span{display:inline-flex;align-items:center;gap:6px}
.id-legend i{width:10px;height:10px;border-radius:2px;flex:none}
.id-f{display:grid;grid-template-columns:minmax(110px,180px) minmax(0,1fr);gap:4px 12px;padding:7px 0;border-bottom:1px solid var(--line);font-size:13px}
.id-f:last-child{border-bottom:0}
.id-bars{display:flex;flex-direction:column;gap:3px;min-width:0}
.id-b{display:grid;grid-template-columns:minmax(0,1fr) 38px;gap:8px;align-items:center;font-size:12px;color:var(--sub);font-variant-numeric:tabular-nums}
.id-b s{display:block;height:6px;background:var(--mute);border-radius:2px;overflow:hidden;text-decoration:none}
.id-b s i{display:block;height:100%}
.id-k-top{background:var(--blue)}.id-k-bot{background:var(--axis,#c3c9d2)}.id-k-ours{background:var(--ok)}
.id-ch{display:flex;gap:10px;align-items:center;justify-content:space-between;padding:9px 0;border-bottom:1px solid var(--line)}
.id-ch:last-child{border-bottom:0}
.id-ch .w{min-width:0;display:flex;flex-direction:column;gap:2px;font-size:13.5px}
.id-ch .w span{overflow-wrap:anywhere;min-width:0}
.id-ch .w a{color:var(--blue);text-decoration:none;overflow-wrap:anywhere;font-size:12.5px}
.id-ch .w a:hover{text-decoration:underline}
.id-ch .m{color:var(--sub);font-size:12px}
.id-ch button{flex:none}
.id-add{margin-top:14px;padding-top:14px;border-top:1px solid var(--line)}
.id-add .row{display:grid;grid-template-columns:minmax(0,2fr) minmax(0,1fr);gap:10px}
.id-btns{display:flex;gap:8px;flex-wrap:wrap;align-items:center}
.id-sug h4{font-size:13px;font-weight:650;margin:16px 0 4px}
@media (max-width:980px){.id-grid{grid-template-columns:minmax(0,1fr)}}
@media (max-width:600px){.id-panel{padding:14px}
.id-wh{display:none}.id-wr,.id-win.nolen .id-wr{grid-template-columns:minmax(0,1fr);gap:2px}.id-wr .n{text-align:left}
.id-wr [data-l]::before{content:attr(data-l) ": ";color:var(--sub)}
.id-f{grid-template-columns:minmax(0,1fr)}
.id-ch{flex-direction:column;align-items:stretch}.id-add .row{grid-template-columns:minmax(0,1fr)}
body.sx .id-btns button{height:auto;min-height:40px;padding:8px 12px;white-space:normal;text-align:center}
.id-ch .w a,.id-wr a{display:inline-flex;align-items:center;min-height:40px}}
"""

JS = r"""
(() => {
'use strict';
const S = window.SX, root = document.getElementById('id-root');
if (!S || !root) return;
const pick = document.getElementById('id-pick');
let cur = '';
function accts() { return [...root.querySelectorAll('[data-id-acct]')].map(s => ({id: s.dataset.idAcct, l: s.dataset.idName || s.dataset.idAcct})); }
function show() {
  const all = accts();
  if (!all.some(a => a.id === cur)) cur = all.some(a => a.id === S.qs('a')) ? S.qs('a') : (all[0] ? all[0].id : '');
  root.querySelectorAll('[data-id-acct]').forEach(s => { s.hidden = s.dataset.idAcct !== cur; });
  if (pick && all.length > 1) S.seg(pick, all, cur, id => { cur = id; S.setq('a', id); show(); });
}
show();
function busy() {
  const a = document.activeElement;
  if (a && root.contains(a) && /^(INPUT|TEXTAREA|SELECT)$/.test(a.tagName)) return true;
  return [...root.querySelectorAll('input')].some(i => i.value.trim());
}
async function redraw(force) {
  if (!force && busy()) return;
  try {
    const q = S.ACCT && S.ACCT !== 'all' ? '?acct=' + encodeURIComponent(S.ACCT) : '';
    const r = await fetch('__PART__' + q, {cache: 'no-store'});
    if (r.ok) { root.innerHTML = await r.text(); show(); }
  } catch (e) {}
}
let seen = false;
S.poll('__DATA__' + (S.ACCT && S.ACCT !== 'all' ? '?acct=' + encodeURIComponent(S.ACCT) : ''), 60000,
  () => { if (seen) redraw(false); seen = true; });
root.addEventListener('submit', e => e.preventDefault());
root.addEventListener('click', async e => {
  const b = e.target.closest('button[data-op]');
  if (!b || !root.contains(b)) return;
  const sec = b.closest('[data-id-acct]');
  const account = sec ? sec.dataset.idAcct : '', whose = sec ? (sec.dataset.idOwn || account) : account;
  const body = {op: b.dataset.op, account};
  let ask = b.dataset.ask || '';
  const form = b.closest('[data-id-form]');
  if (form && !b.dataset.url) {
    const val = n => { const i = form.querySelector('[name=' + n + ']'); return i ? i.value.trim() : ''; };
    body.url = val('url'); body.name = val('name'); body.why = val('why');
    if (!body.url) { S.toast('Type the channel link first, such as https://www.youtube.com/@Handle', true); return; }
    ask = 'Add ' + body.url + (body.name ? ' (' + body.name + ')' : '') + ' to ' + whose +
      " benchmark channels? Ideas reads its Shorts once a day at 14:20: titles and view counts only.";
  } else {
    for (const k of ['url', 'name', 'why']) if (b.dataset[k]) body[k] = b.dataset[k];
  }
  if (!ask || !S.ask(ask)) return;
  const r = await S.act(body, b);
  if (r.ok) { if (form) form.reset(); redraw(true); }
});
})();
"""


# ------------------------------------------------------------------------------------------------------ small words
def _num(v) -> str:
    if v is None:
        return ""
    v = float(v)
    if v >= 1e6:
        return f"{v / 1e6:.1f}M".replace(".0M", "M")
    if v >= 1e4:
        return f"{v / 1e3:.0f}K"
    if v >= 1e3:
        return f"{v / 1e3:.1f}K".replace(".0K", "K")
    return f"{v:.0f}"


def _len(dur) -> str:
    try:
        s = int(round(float(dur)))
    except (TypeError, ValueError):
        return ""
    return f"{s // 60}:{s % 60:02d}"


def _pct(v) -> str:
    return "" if v is None else f"{round(float(v) * 100)}%"


def _local(cfg: C.Config, iso) -> Optional[datetime]:
    t = I._when(iso)
    return I._local(cfg, t) if t is not None else None


def _accounts(cfg: C.Config, acct: str) -> List[C.Account]:
    return [a for a in SX.own_accounts(cfg, acct or "all") if a.slug in cfg.accounts]


# ------------------------------------------------------------------------------------------------------ the parts
def _state(cfg: C.Config, a: C.Account, now: datetime) -> str:
    on = I.is_on(a)
    bits = [f"<span>Ideas is <b>{'on' if on else 'off'}</b> for {esc(a.name)}</span>"]
    lr = I.last(a)
    at = _local(cfg, lr.get("at"))
    today = I._local(cfg, now).date().isoformat()
    if at is not None:
        unread = int(lr.get("unread") or 0)
        bits.append(f"<span>Last read {esc(at.strftime('%b %d %H:%M'))}: {int(lr.get('answered') or 0)} of "
                    f"{int(lr.get('channels') or 0)} channels answered"
                    + (f", {unread} not reached" if unread else "") + "</span>")
        if int(lr.get("read") or 0) > 0 and not int(lr.get("answered") or 0):
            bits.append('<span class="bad">No channel answered: yt-dlp may need an update (brew upgrade yt-dlp)'
                        '</span>')
    note = I._today_note(lr, today)
    if note:
        bits.append(f'<span class="warn">{esc(note[:1].upper() + note[1:])}</span>')
    elif lr.get("stood_down") and lr.get("down_day") and lr.get("down_day") != lr.get("day"):
        down = _local(cfg, lr.get("down_at"))
        if down is not None and (at is None or down > at):
            bits.append(f'<span class="warn">Stood down on {esc(down.strftime("%b %d"))}: the scouts saw a YouTube '
                        f'bot check</span>')
    h = I.hold(a, now)
    if h is not None:
        until = _local(cfg, h.get("until"))
        bits.append(f'<span class="warn">Paused until {esc(until.strftime("%b %d %H:%M") if until else "later")}: '
                    f'{esc(h.get("why") or "a hold")}</span>')
    if not on:
        bits.append(f"<span>It reads nothing until it is switched on: factory.py social on {esc(a.slug)} ideas</span>")
    return '<div class="id-state">' + "".join(bits) + "</div>"


def _winners(a: C.Account) -> str:
    rows = I.winners(a, n=10)
    pat = I.patterns(a)
    out = ['<section class="id-panel" aria-label="Winners"><h3>Winning Shorts this week</h3>']
    if not rows:
        out.append('<p class="id-sub">Nothing read yet. The first read is at 14:20 on a day Ideas is on.</p></section>')
        return "".join(out)
    by_views = all(r.get("vph") is None for r in rows)
    out.append('<p class="id-sub">' + ("Views per hour come after the second day's read; ranked by views for now."
                                      if by_views else
                                      f"Ranked by views per hour, the change between two days' reads. "
                                      f"{int(pat.get('n') or 0)} Shorts measured.") + "</p>")
    if not by_views and any(r.get("approx") for r in rows):
        out.append('<p class="id-sub">YouTube shows these counts rounded (12K, 1.2M), so views per hour are close, '
                   'not exact; a Short whose rounded count has not moved is left out.</p>')
    has_len = any(r.get("dur") is not None for r in rows)
    out.append(f'<div class="id-win{"" if has_len else " nolen"}" role="table" aria-label="Winning Shorts">'
               '<div class="id-wr id-wh" role="row"><span role="columnheader">Title</span>'
               '<span role="columnheader">Channel</span><span role="columnheader" class="n">Views per hour</span>'
               + ('<span role="columnheader" class="n">Length</span>' if has_len else "") + '</div>')
    for r in rows:
        url = str(r.get("url") or "")
        new = '<span class="id-new">new</span>' if r.get("new") and r.get("vph") is not None else ""
        title = esc(r.get("title") or r.get("id") or "") + new
        if url.startswith("https://www.youtube.com/"):
            link = f'<a role="cell" href="{esc(url)}" target="_blank" rel="noopener noreferrer">{title}</a>'
        else:
            link = f'<span role="cell">{title}</span>'
        speed = (_num(r.get("vph")) if r.get("vph") is not None
                 else (f"{_num(r.get('views'))} views" if r.get("views") is not None else ""))
        out.append(f'<div class="id-wr" role="row">{link}'
                   f'<span role="cell" class="c" data-l="Channel">{esc(r.get("channel") or "")}</span>'
                   f'<span role="cell" class="n" data-l="Views per hour">{esc(speed)}</span>'
                   + (f'<span role="cell" class="n" data-l="Length">{esc(_len(r.get("dur")))}</span>' if has_len
                      else "") + '</div>')
    out.append("</div></section>")
    return "".join(out)


def _bar(cls: str, v, label: str) -> str:
    w = 0 if v is None else max(0, min(100, round(float(v) * 100)))
    return (f'<div class="id-b" title="{esc(label)}"><s><i class="{cls}" style="width:{w}%"></i></s>'
            f'<span>{esc(_pct(v) or "none")}</span></div>')


def _patterns(a: C.Account) -> str:
    pat = I.patterns(a)
    out = ['<section class="id-panel" aria-label="Title patterns"><h3>What the winners\' titles do</h3>']
    if not pat or not pat.get("n_top"):
        out.append('<p class="id-sub">Patterns come after two days of reads (views per hour need two).</p></section>')
        return "".join(out)
    out.append(f'<p class="id-sub">Share of titles with each feature: the top quarter by views per hour '
               f'({int(pat.get("n_top") or 0)}), the bottom half ({int(pat.get("n_bottom") or 0)}) and our own '
               f'signed clips this week ({int(pat.get("n_ours") or 0)}).</p>')
    out.append('<div class="id-legend"><span><i class="id-k-top"></i>Winners</span>'
               '<span><i class="id-k-bot"></i>Bottom half</span><span><i class="id-k-ours"></i>Ours</span></div>')
    for f in pat.get("features") or []:
        words = f.get("words") or I.FEATURE_WORDS.get(f.get("feature"), f.get("feature"))
        out.append(f'<div class="id-f"><span>{esc(words)}</span><div class="id-bars">'
                   + _bar("id-k-top", f.get("share_top"), "Winners")
                   + _bar("id-k-bot", f.get("share_bottom"), "Bottom half")
                   + _bar("id-k-ours", f.get("share_ours"), "Ours") + "</div></div>")
    ln = pat.get("length") or {}
    if ln.get("top_median_s") is not None and ln.get("bottom_median_s") is not None:
        out.append(f'<p class="id-sub" style="margin:10px 0 0">Middle length: winners {esc(_len(ln["top_median_s"]))}, '
                   f'bottom half {esc(_len(ln["bottom_median_s"]))}.</p>')
    out.append("</section>")
    return "".join(out)


def _channels(a: C.Account) -> str:
    rows = I.channels(a)
    limit = I.max_channels(a)
    out = [f'<section class="id-panel" aria-label="Benchmark channels"><h3>Benchmark channels</h3>'
           f'<p class="id-sub">{len(rows)} of {limit}. Ideas reads each one\'s newest {I.PLAYLIST_END} Shorts once a '
           f'day at 14:20: titles and view counts (as YouTube rounds them), nothing else.</p>']
    if not rows:
        out.append('<p class="sx-muted">No channels yet.</p>')
    for r in rows:
        url = str(r.get("url") or "")
        name = str(r.get("name") or url)
        added = _added(r)
        ask = f"Remove {name} ({url}) from {C.possessive(a.name)} benchmark channels?"
        out.append(f'<div class="id-ch"><div class="w"><span>{esc(name)}</span>'
                   f'<a href="{esc(url)}" target="_blank" rel="noopener noreferrer">{esc(url)}</a>'
                   + (f'<span class="m">{esc(r.get("why"))}</span>' if r.get("why") else "")
                   + (f'<span class="m">{esc(added)}</span>' if added else "")
                   + f'</div><button type="button" data-op="ideas-remove" data-url="{esc(url)}" '
                     f'data-ask="{esc(ask)}">Remove</button></div>')
    if len(rows) < limit:
        out.append('<form class="sx-form id-add" data-id-form autocomplete="off"><div class="row">'
                   '<label>YouTube channel link<input name="url" type="text" inputmode="url" spellcheck="false" '
                   'placeholder="https://www.youtube.com/@Handle" maxlength="300"></label>'
                   '<label>Name (optional)<input name="name" type="text" maxlength="80"></label></div>'
                   '<label>Why it is a benchmark (optional)<input name="why" type="text" maxlength="160"></label>'
                   '<div class="id-btns"><button type="button" class="go" data-op="ideas-add">Add channel</button>'
                   '</div></form>')
    out.append(_suggest(a, len(rows), limit))
    h = I.hold(a)
    if h is not None:
        ask = (f"End the Ideas hold for {a.name} now? It reads YouTube again at the next 14:20, though YouTube asked "
               f"us to wait ({h.get('why') or 'a hold'}).")
        out.append(f'<div class="id-btns" style="margin-top:12px"><button type="button" data-op="ideas-release" '
                   f'data-ask="{esc(ask)}">End the hold</button></div>')
    out.append("</section>")
    return "".join(out)


def _added(r: dict) -> str:
    t = I._when(r.get("added_at"))
    by = str(r.get("added_by") or "")
    if t is None and not by:
        return ""
    return "Added" + (f" by {by}" if by else "") + (f" on {t:%b %d}" if t is not None else "")


def _suggest(a: C.Account, have: int, limit: int) -> str:
    sug = I.suggestions(a)
    if not sug:
        return ('<div class="id-sug"><h4>From the scout</h4><p class="sx-muted">The scout lists no other YouTube '
                'channel for this account. Add one by its link.</p></div>')
    room = max(0, limit - have)
    take = sug[:room]
    names = ", ".join(s["name"] for s in take)
    out = ['<div class="id-sug"><h4>From the scout</h4>'
           f'<p class="sx-muted">{len(sug)} of the scout\'s own YouTube channels for {esc(a.name)} '
           f'{"is" if len(sug) == 1 else "are"} not on the list. Nothing is added until you add it.</p>']
    if take:
        more = f" ({len(sug) - len(take)} more do not fit: {limit} is the most)" if len(sug) > len(take) else ""
        ask = (f"Add {len(take)} of the scout's channels to {C.possessive(a.name)} benchmark channels: {names}{more}? "
               f"Ideas reads their Shorts once a day at 14:20: titles and view counts only.")
        out.append(f'<div class="id-btns" style="margin:6px 0 4px"><button type="button" data-op="ideas-seed" '
                   f'data-ask="{esc(ask)}">Add the scout\'s channels ({len(take)})</button></div>')
    for s in sug[:40]:
        btn = ""
        if room:
            ask = (f"Add {s['name']} ({s['url']}) to {C.possessive(a.name)} benchmark channels? Ideas reads its "
                   f"Shorts once a day at 14:20: titles and view counts only.")
            btn = (f'<button type="button" data-op="ideas-add" data-url="{esc(s["url"])}" data-name="{esc(s["name"])}"'
                   f' data-why="{esc(s["why"])}" data-ask="{esc(ask)}">Add</button>')
        out.append(f'<div class="id-ch"><div class="w"><span>{esc(s["name"])}</span>'
                   f'<a href="{esc(s["url"])}" target="_blank" rel="noopener noreferrer">{esc(s["url"])}</a>'
                   f'<span class="m">{esc(s["why"])}</span></div>{btn}</div>')
    if len(sug) > 40:
        out.append(f'<p class="sx-muted">And {len(sug) - 40} more.</p>')
    out.append("</div>")
    return "".join(out)


def section_html(cfg: C.Config, a: C.Account, now: Optional[datetime] = None, hidden: bool = False) -> str:
    """One account's Ideas: its state, the winners, the patterns and the channel list."""
    now = now or cfg.now()
    return (f'<div class="id-acct" data-id-acct="{esc(a.slug)}" data-id-name="{esc(a.name)}" '
            f'data-id-own="{esc(C.possessive(a.name))}"'
            f'{" hidden" if hidden else ""}>' + _state(cfg, a, now)
            + '<div class="id-grid">' + _winners(a) + _patterns(a) + "</div>" + _channels(a) + "</div>")


def _sections(cfg: C.Config, acct: str, pick: str = "", now: Optional[datetime] = None) -> str:
    accts = _accounts(cfg, acct)
    if not accts:
        return '<div class="sx-empty">No account of yours works on this machine.</div>'
    first = pick if any(a.slug == pick for a in accts) else accts[0].slug
    return "".join(section_html(cfg, a, now, hidden=a.slug != first) for a in accts)


# ------------------------------------------------------------------------------------------------------ the plug
def page(cfg: C.Config, tok: str, base_css: str, tabs_html: str, switch_html: str, acct: str, q: dict) -> str:
    """The Ideas page (under Reports)."""
    own = []
    rp = REG.load("reportspage")
    views = getattr(rp, "VIEWS", None) if rp is not None else None
    if isinstance(views, (list, tuple)) and views and all(isinstance(v, (list, tuple)) and len(v) in (2, 3)
                                                          for v in views):
        # (key, label) is the Reports page's own shape (its links are /reports?view=<key>); (key, href, label) too
        own = [(str(v[0]), f"/reports?view={v[0]}", str(v[1])) if len(v) == 2 else tuple(str(x) for x in v)
               for v in views]
    elif rp is not None:
        own = [("reports", "/reports", "Reports")]
    nav = SX.subnav("reports", "ideas", acct, own)
    accts = _accounts(cfg, acct)
    main = ('<div class="id-top"><div><h2>Ideas</h2><p>What is winning in each niche: the benchmark channels\' Shorts, '
            'read once a day, and what their titles do that ours do not. The coach and the reports read it too.</p>'
            '</div>' + ('<div class="seg" id="id-pick" role="group" aria-label="Account"></div>' if len(accts) > 1
                        else "") + "</div>"
            f'<div id="id-root">{_sections(cfg, acct, str(q.get("a") or ""))}</div>')
    js = JS.replace("__PART__", PART_URL).replace("__DATA__", DATA_URL)
    return SX.shell(cfg, "ideas", "Ideas", tok, base_css, tabs_html, switch_html, acct, main, css=CSS, js=js,
                    subnav=nav)


def _mtime(p) -> str:
    try:
        st = os.stat(p)
        return f"{st.st_size:x}{st.st_mtime_ns:x}"
    except OSError:
        return ""


def data(cfg: C.Config, q: dict, now: Optional[datetime] = None) -> dict:
    """What the page polls (files only): each account's switch, list size, last read and hold, and the change marks
    of its files; the page redraws its part when the stamp moves."""
    now = now or cfg.now()
    out = []
    for a in _accounts(cfg, str((q or {}).get("acct") or "all")):
        f = I.folder(a)
        h = I.hold(a, now)
        lr = I.last(a)
        out.append({"slug": a.slug, "name": a.name, "mode": I.SW.mode(a, I.KEY), "channels": len(I.channels(a)),
                    "last": {k: lr.get(k) for k in ("at", "answered", "channels", "unread", "stood_down",
                                                    "down_day", "skipped", "stopped")},
                    "hold": {"until": h.get("until"), "why": h.get("why")} if h else None,
                    "files": [_mtime(f / n) for n in ("channels.json", "patterns.json", "last.json", "hold.json")]})
    return SX.stamp({"accounts": out, "day": I._local(cfg, now).date().isoformat()})


def get(cfg: C.Config, sub: str, q: dict) -> Tuple[int, str, bytes]:
    """/reports/ideas/part: the accounts' sections again (the page swaps them in when its data stamp moves)."""
    if sub == "part":
        body = _sections(cfg, str((q or {}).get("acct") or "all"), str((q or {}).get("a") or ""))
        return 200, "text/html; charset=utf-8", body.encode()
    return 404, "text/plain; charset=utf-8", b"not found"


# ------------------------------------------------------------------------------------------------------ clicks
OPS = {"ideas-add": "add a benchmark channel", "ideas-remove": "remove a benchmark channel",
       "ideas-seed": "add the scout's channels", "ideas-release": "end the Ideas hold"}


def act(cfg: C.Config, body: dict, page: bool = False, now: Optional[datetime] = None) -> Tuple[bool, str]:
    """ideas-add {account, url, name, why}, ideas-remove {account, url}, ideas-seed {account}, ideas-release
    {account}: only a click on the review page itself (the browser asked first, naming the channel and the account).
    Files only; nothing is read from YouTube here."""
    op = str(body.get("op") or "")
    if op not in OPS:
        raise SystemExit(f"{op or 'that'}: not a click the Ideas page knows")
    SX.need_page(page, OPS[op])
    slug = str(body.get("account") or "")
    mine = {a.slug: a for a in _accounts(cfg, "all")}
    if slug not in mine:
        raise SystemExit("Pick one of your own accounts first.")
    a = mine[slug]
    by = str(body.get("by") or (cfg.approvers[0] if cfg.approvers else "Clip Review"))
    if op == "ideas-add":
        return True, I.add(cfg, a, str(body.get("url") or ""), str(body.get("name") or ""), str(body.get("why") or ""),
                           by, now)
    if op == "ideas-remove":
        return True, I.remove(cfg, a, str(body.get("url") or ""), by, now)
    if op == "ideas-seed":
        return True, I.seed(cfg, a, by, now)
    return True, I.release(cfg, a, by, now)
