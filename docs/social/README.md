# The social layer

The social layer is everything Clip Review and the factory do around a posted clip besides posting it: the Inbox
(comments and messages), Automations, the first comment, cards and stories, the hook coach, checkpoint stats, the
reports and the learner, Ideas, and the Log with its guard. The code is in `clipfactory/` (one module per part); this
folder holds the notes each part ships with (`docs/social/<part>.md`).

Every part ships **off** for every account. Its switches live in `state/<slug>/social/settings.json`, never in
config/accounts.json; `factory.py social status` shows them, `factory.py social doctor` says what each one waits for.
With every switch off, the factory is what it was before the social layer: the same pages, the same morning notice,
no extra platform call, no social file written (tests/test_social_off.py holds that).

The shared files (review.py, poster.py, collect.py, factory.py) reach a part only through the plug board,
`clipfactory/registry.py`. A part that is not built is not there; a part that will not import hides only its own tab,
card part, doctor line and command, and the doctor names it.

This page copies the module map, the plug board and the contracts (sections 1, 2 and 3.1 to 3.4 of the design) as
they were fixed for the builders. Keep every name in them exact.

## 1. Module map

**Seam modules**

| Path | Public |
|---|---|
| clipfactory/registry.py | the plug lists (1.4); `load`, `present`, `fn`, `calls`, `route`, `act_module`, `top_of`, `url_of`, `tab_pages`, `children`, `card_css`, `card_js` (each part's CARD_JS, 2026-10-20), `run_cli`, `BROKEN`, `reset` |
| clipfactory/store.py | `path`, `read`, `edit` (flock + tmp + os.replace, refuses outside C.STATE), `append`, `rows`, `prune_months`, `try_lock` |
| clipfactory/switches.py | `MODES`, `SETTINGS`, `HARD`, `FAMILY_HARD`, `CHECKLIST`, `mode`, `settings`, `set_mode`, `set_setting`, `ack_practice`, `checklist`, `add_tester`, `is_tester`, `warmup_until`, `quiet`, `caps_for`, `global_settings`, `problems`, `doctor` |
| clipfactory/capabilities.py | `NEEDS`, `save_scopes`, `scopes`, `missing`, `put_probe`, `probe_result`, `mark_missing`, `refresh`, `refresh_due`, `probe`, `doctor` |
| clipfactory/graph.py | `meta_keys`, `gget`, `gpost`, `gpages`, `rupload` (bytes to an upload host, the token in the header only), `multipart`, `yt_get`, `yt_write` (allowlist), `yta_get`, `YT_COST`, `ErrorInfo`, `classify`, `endpoint` |
| clipfactory/outward.py | contract 3.2 |
| clipfactory/extras.py | contract 3.3 |
| clipfactory/verdicts.py | `REASONS`, `CHIPS`, `check`, `words`, `record`, `rows` |
| clipfactory/socialstore.py | contract 3.4 |
| clipfactory/social.py | `accounts`, `CollectCtx`, `run_job`, `kick`, `on_posted`, `after_take`, `card_parts`, `health_lines`, `brief`, `morning_lines`, `cal_items`, `extras_health`, `doctor`, `act`, `cli` |
| clipfactory/socialui.py | `CSS`, `JS` (`window.SX`), `CLIPS_CSS`, `shell`, `banner`, `subnav`, `own_accounts`, `need_page`, `stamp`, `file_url`, `brief_html` |
| tests/social_fakes.py | `FakeGraph`, `FakeYouTube`, `patch_http`, `FakeEngine`, `fake_ytdlp`, `Clock`, `cfg_in_tmp`, `slot_ready`, `slot_posted`, `serve` |

**Feature modules (new files only)**

| Key | Creates | Single-owner shared edit |
|---|---|---|
| inbox | engage.py, engage_api.py, classify.py, engagepage.py | none |
| automations | rules.py, answers.py, rulespage.py | none |
| first-comment | firstcomment.py, reelextras.py | none |
| cards | cards.py, cards_api.py, cardspage.py, render/card_engine.py | none |
| coach | coach.py, prompts/coach.md | none |
| checkpoints | checkpoints.py, checkpoints_api.py | statspage.py |
| learn | learn.py, statmath.py, playbook.py, reportspage.py, prompts/weekly-report.md | scout.py (three guarded hunks) |
| ideas | ideas.py, ideaspage.py | none |
| log | logpage.py, weekpage.py, socialmove.py | none |

**Existing-file edits (seam only)**

| File:line | Change |
|---|---|
| review.py:819-825 `tabs()` | links = `REG.tab_pages()` (Clips, Stats, present top pages); a child page marks its parent current |
| review.py:828-840 `switch()` | `base = REG.url_of(current)` |
| review.py:1451 `do_GET` | after `/stats/data`: `REG.route` gives page (CSP + no-referrer), `/data`, module `get`, or SUBROUTES; a missing module gives 404 |
| review.py:1214 `act()` | first: `REG.act_module(op)` then `mod.act(cfg, body, page=page)`; decline passes `reasons` |
| review.py:426 `_bottom` | `out.append(SOC.card_parts(cfg, acct, day, s, now))` before `_wrap` |
| review.py:772 `_needs` | `SOC.health_lines` prepended after the scout block, before `broke` |
| review.py `page`/`live`/`_stamp` | Today band first and `"brief"` only when non-empty; `_stamp(..., brief=None)` appends only when non-empty |
| review.py:796 `calendar()` | `items` (CAL_ITEMS) only when non-empty; `week: true` only when the Week page is on the plug board and built (`REG.children("clips")`) |
| review.py JS | `swap()` skips a node holding `.dx-open`; `apply()` brief branch; generic `data-x` button branch (the card's own date, account and slot; `{words}` in `data-ask-t` from the form's `data-w` fields); `reconcile()` keeps a rebuilt card's open folds; Make again chooser (`__REASONS__`, the reason codes) replaces `prompt()` (Decline keeps `prompt()` until the Reason codes part lands); drawCal second strip and Week link; current tab scrolled into view on phones. No backslashes |
| review.py CSS | `CSS += SX.CLIPS_CSS + REG.card_css()`, and `REG.card_js()` after the page's script; phone: `.tabs` scroll, 40px tab links, `.cal-head` buttons 40px, inputs 16px, the day picker wraps, grids `minmax(0,1fr)` (the page fits 280 px) |
| review.py:1352 `_allowed_file` | under C.STATE only .mp4 .jpg .jpeg .png .srt; never review-token |
| poster.py:280 | `if _guard_waits(cfg, acct, plat, now): return None` |
| poster.py:322 | `_guard_trip` gives a "waits" result recorded as last_error, not a try; `_record(..., error=e)` |
| poster.py:351 | `_after_post(...)` after the success `_record` |
| poster.py:862 `_record` | kwarg `error=None`; after the edit, `outward.record_post(...)` in try/except |
| direct.py:58 PlatformError | attributes `status`, `host`, `meta_code`, `meta_subcode`, `meta_type`, `transient`, `trace`, `reason` (default None); message unchanged |
| direct.py:130-164 `_http` | `_note_usage` on success and HTTPError; `last_usage()`; signature unchanged |
| direct.py:255, 306 `connect_meta` | permission lines (comments now; messages after review); `capabilities.save_scopes` |
| direct.py:438 `ig_post` | `form.update(EX.ig_form(slot, acct))` in try/except |
| outlets.py:653-703 | `connect_youtube(..., extra=())` adds force-ssl and/or yt-analytics.readonly; `net.guard` before the token exchange; `save_youtube` saves scopes |
| board.py:135/148/175 | extras in `content_hash`/`verify`/`seen_key` only when present |
| signoff.py:87 `_as_is` | adopt an extras edit; a missing file is dropped with a note |
| signoff.py:308 | approve verdict row |
| signoff.py:448 `decline` | `reasons=`; verdict before pops; remake clears extras; note unchanged without reasons |
| collect.py:282, 312 `_take` | `EX.clear(slot)`; `SOC.after_take(cfg, acct, slot, folder)` |
| stats.py ~337, followers.py ~131 | skip a platform when `outward.read_paused` (guard on) |
| accounts.py:45 RESERVED | + inbox, engage, cards, extras, coach, learn, outward, series, ideas, playbook, reports, log, social, render, prompts, rules, week, verdicts, capabilities, docs |
| timer.py:49-81 | 5 agents and docstring lines |
| hermes.py:224 TASK_PROMPTS | + clip-coach, clip-weekly-report on both slugs (skipped until they exist) |
| factory.py | docstring lines; `social` plus lazy commands via `REG.run_cli`; `connect --with`; `decline --reasons`; doctor social section after the bench; morning lines after `BN.low_lines` |
| config/accounts.json | top-level `_social_how` only |
| tests/test_review_layout.py:104 | expected string computed exactly from present pages |
| tests/test_new_accounts.py:54 | AGENTS_BEFORE + 5 |

### 1.4 Registries (registry.py, the only list of plug points)
```python
PAGES = (Page("inbox","/inbox","Inbox","engagepage",None), Page("rules","/inbox/rules","Automations","rulespage","inbox"),
  Page("cards","/cards","Cards","cardspage",None), Page("reports","/reports","Reports","reportspage",None),
  Page("ideas","/reports/ideas","Ideas","ideaspage","reports"), Page("log","/log","Log","logpage",None),
  Page("week","/week","Week","weekpage","clips"))
SUBROUTES = {"/stats/series": "checkpoints:get_series"}
ACT_PREFIXES = (("inbox-","engagepage"),("saved-","engagepage"),("rule-","rulespage"),("extra-","firstcomment"),
  ("coach-","coach"),("card-","cardspage"),("learn-","reportspage"),("ideas-","ideaspage"),("log-","logpage"),("social-","social"))
CARD_PARTS = ("learn:card_part","coach:card_part","firstcomment:card_part","checkpoints:card_part")
CARD_CSS = ("learn","coach","firstcomment","checkpoints")
HEALTH = ("outward:health","social:extras_health","engage:health","cards:health","learn:health")
SUMMARIES = ("outward:summary","engage:summary","cards:summary","checkpoints:summary","learn:summary")
MORNING_HOLDS = ("outward:morning_lines",); MORNING = ("engage:morning_line","cards:morning_line","learn:morning_line")
DOCTORS = ("switches","capabilities","outward","social","engage","rules","firstcomment","cards","coach","checkpoints","learn","ideas","logpage")
AFTER_TAKE = ("firstcomment:on_collect","learn:on_collect","coach:on_collect")
FOLLOW_UPS = ("firstcomment:on_posted","cards:on_posted","rules:on_posted")
AFTER_POLL = ("rules:on_items",)
EXECUTORS = {"first_comment":"firstcomment","reply_public":"engage","reply_private":"engage","dm":"engage",
  "hide":"engage","unhide":"engage","yt_moderate":"engage","story":"cards","card_post":"cards"}
JOBS = {"tick":("outward:drain_job","capabilities:refresh_due","engage:tick","firstcomment:sweep","outward:drain_job","outward:prune_job"),
  "studio":("cards:tick","coach:tick"), "checkpoints":("checkpoints:run",), "learn":("learn:daily",), "ideas":("ideas:run",)}
COMMANDS = {"engage":"engage","rules":"rules","extras":"firstcomment","cards":"cards","coach":"coach",
  "checkpoints":"checkpoints","learn":"learn","ideas":"ideas"}
SOCIAL_ACTIONS = {"export":"socialmove:cli_export","import":"socialmove:cli_import"}
CAL_ITEMS = ("cards:cal_items",)
```
- **Agents** (timer.AGENTS; all `"quiet": True`, `"twins": []`):
  - `social`: `["social","tick","--quiet"]`, interval 300
  - `checkpoints`: `["social","checkpoints","--quiet"]`, interval 900
  - `studio`: `["social","studio","--quiet"]`, interval 600
  - `learn`: `["social","learn","--quiet"]`, at 04:40
  - `ideas`: `["social","ideas","--quiet"]`, at 14:20
- **Job steps:** each step has its own `state/social/locks/<step>.lock` (LOCK_NB, skipped if busy) and a try/except.

## 2. State and settings

```
state/social/  settings.json {"v":1,"youtube_units":2500}   stop.json {"by","at","why"}
               youtube-units.json {"day"(Pacific),"reads","writes","units","by":{method:units}}   locks/   tick.json
state/stats/series/<acct>__<outlet>__<YYYY-MM>.json   (own lock state/stats/series/.lock)
state/<slug>/social/settings.json   capabilities.json
  outward/{queue,keys,holds,guard}.json actions-YYYY-MM.jsonl .lock
  engage/{items-YYYY-MM,threads,contacts,saved,cursor,posts,rules,waitlist}.json runs-YYYY-MM.jsonl
  extras/<YYYY-MM-DD>/<HHMM>.json    coach/<YYYY-MM-DD>/<HHMM>.json coach/inputs/<sha16>/
  cards/<YYYY-MM-DD>.json cards/<YYYY-MM-DD>/<id>/{1.jpg,1.mp4,spec.json} cards/rules.json
  learn/{features,proposals,experiments}.json report-YYYY-MM-DD.{json,md} weekly/YYYY-Www.md verdicts.jsonl playbook-history.jsonl
  playbook.json   ideas/{channels,patterns}.json ideas/snapshots/YYYY-MM-DD.json
```
`B.all_boards` matches only `YYYY-MM-DD.json` (board.py:197), so subfolders are safe. Switches live in state, not accounts.json: no tracked file is dirtied, ff-only merges stay clean, and they move with the account. Stats settings (`state/stats/settings.json`) set the precedent.

**Modes**: `guard` off/watch/on · `engage.read` off/on · `engage.messages` off/on · `replies` off/practice/on · `automations` off/practice/testers/on · `first_comment.{facebook_reels,instagram_reels,youtube_shorts}` off/practice/on · `cards.make` off/on · `cards.post` off/practice/on · `cards.story_repost` off/practice/on · `coach` off/on · `checkpoints` off/on · `checkpoints.extra` off/on · `checkpoints.yt_analytics` off/on · `learn` off/on · `learn.playbook` off/on · `learn.experiments` off/practice/on · `ideas` off/on · `poll` off/practice/on · `trial_reels` off/practice/on.

**Settings** (defaults in code):

| Setting | Default |
|---|---|
| `created` | "" |
| `warmup_days` | 28 (floor 14) |
| `quiet_hours` | ["00:30","07:00"] |
| `testers` | [] of {platform,id,name} |
| `messages_access` | "standard" |
| `caps` | {} (lower only) |
| `engage.platforms` | [instagram,facebook,youtube] |
| `engage.poll_minutes` | 10 (≥5) |
| `engage.youtube_minutes` | 15 |
| `engage.dm_minutes` | 15 (Facebook ≥30) |
| `engage.keep_days` | 45 (≤45) |
| `first_comment.delay_s` | [90,300] |
| `first_comment.youtube_delay_s` | [600,1200] |
| `cards.kinds` | Politics [quote,docket,week_best]; GTA [countdown,clip_of_day,week_best] |
| `cards.targets` | [facebook_story] |
| `cards.times` | {countdown "09:00", clip_of_day "20:30", docket "12:00", quote_after_min [60,90], week_best "Mon 19:00"} |
| `cards.story_per_day` | 4 |
| `ideas.max_channels` | 20 |
| `learn.min_n` | 12 |

**Checklist** before a live value (on or testers) of automations, first_comment.*, cards.post, cards.story_repost, poll or trial_reels:
1. Practice for at least 2 days AND (rows ≥ N OR 7 days). N: automations 20, first comment 10, cards 4, poll/trial 3 (14 days).
2. Zero would-sends over a cap or a hold in the last 2 days.
3. "I read these" pressed on the Log page after the last rows (page only).
4. The permission is present.
5. Automations only: `created` is set.

Every write has a history row and a dated copy in `state/config-backups/social-<slug>-<stamp>.json`.

## 3. Contracts

### 3.1 Plug contracts
```python
def page(cfg, tok, base_css, tabs_html, switch_html, acct, q) -> str          # via socialui.shell
def data(cfg, q, now=None) -> dict                                             # files only, ends with "stamp"
def get(cfg, sub, q) -> Tuple[int, str, bytes]                                 # optional GET <url>/<sub>
def act(cfg, body, page=False, now=None) -> Tuple[bool, str]                   # SystemExit("next step") refuses
def card_part(cfg, acct, day, s, now) -> str ; CARD_CSS: str                   # "" when off; <details data-k="..">
def health(cfg, now, accts) -> Tuple[str, str]                                 # ("bad"|"calm"|"", words)
def summary(cfg, now, accts) -> List[dict]  # {"key","words","href","level":"calm|warn|bad","order"}
def morning_line(cfg, now) -> str           # fragment or ""
def doctor(cfg, now=None) -> List[Tuple[Optional[bool], str, str]]           # read-only, no network
def <job step>(cfg, now=None, quiet=True) -> List[str]
def on_collect(ctx) -> None   # ctx: cfg, acct, day, slot (live dict in B.edit), folder, dest, extras (dict to build)
def on_posted(cfg, acct, day, slot, outlet, rec, now) -> Optional[str]        # only queue or write own state
def on_items(cfg, acct, items, now) -> List[str]
def cal_items(cfg, now, accts) -> {"YYYY-MM-DD": {slug: [{"k":"card|story","s":"posted|scheduled|review|problem"}]}}
def execute(cfg, acct, action) -> dict ; def find(cfg, acct, action) -> Optional[dict]
def still_signed(cfg, acct, action) -> str ; def on_state(cfg, acct, action, state, result) -> None
def cli(cfg, argv: List[str]) -> None
```

### 3.2 outward
- **Guard platforms:** facebook, instagram, youtube (`plat_of`: facebook_reels to facebook, and so on).
- **Families:**

| Family | Kinds |
|---|---|
| publish | post, story, card_post |
| comment | first_comment, reply_public |
| message | reply_private, dm |
| moderation | hide, unhide, yt_moderate |
| read | read |

- **Manual only:** hide, unhide, yt_moderate.

Action (queue item and log row):
```json
{"id":"oa-20261019T153201-7f3a","key":"fc:facebook:934844222787108","acct":"gaming","platform":"facebook",
 "outlet":"facebook_reels","kind":"first_comment","runner":"social",
 "origin":{"by":"poster","rule":null,"via":null},
 "signed":{"what":"clip","by":"Andrew","at":"…","hash":"b5b3…","extras_sha":"9c1d…","text_sha":"…"},
 "target":{"post_id":"934844222787108","comment_id":null,"thread_id":null,"to":null,"intent":null,"board":"2026-10-19","slot":"08:45","item":null},
 "text":"Full video from MrBossFTW: https://www.youtube.com/watch?v=uSQfYvi-NfY&t=190s","text_sha":"…",
 "force_practice":false,"force":false,"not_before":"…","expires":"…","state":"queued","tries":0,"lookups":0,"lost_retry":0}
```
- **`signed.what`:** `clip` | `rule:<id>@v<n>` | `card:<date>/<id>` | `cards-rule:story_repost@v<n>` | `click`.
- **Log row:** the action plus `t`, `mode` (live|practice) and `state`. State is one of: queued, practice, sending, done, failed, skipped, lookup, gave_up, held, watch, refused. Optional fields: `result{id,url}`, `error{http,code,subcode,reason,kind,msg,trace}`, `usage{buc_pct,regain_min,app_pct}`, `why`, `would_at`, `endpoint`.
- **Post rows** (from the poster): `kind:"post"`, key `post:<outlet>:<day> <slot>`, `target{board,slot,post_id}`.
- **Politics** `keys.json` `{key:{id,state,at,platform,kind,result_id}}`, pruned after 120 days. Keys:

| Key | Action |
|---|---|
| `fc:<p>:<post>` | first comment |
| `rp:<p>:<comment>` | automated public reply |
| `rpm:<p>:<comment>:<n>` | manual public reply |
| `pr:<p>:<comment>` | private reply (one per comment, ever) |
| `dm:<p>:<thread>:<inbound mid>` | DM answer |
| `dmm:<p>:<thread>:<n>` | manual DM |
| `hide:<p>:<comment>:<on/off>:<n>` | hide or unhide |
| `card:<p>:<date>/<id>:<target>` | card post |
| `story:<p>:<date> <slot>` | story repost |

- **Holds** `{"instagram|comment":{"until","why","code","at","by","releasable","kind"}}`. Families plus `*`.
- **guard.json:** `recent{"p|kind|manual/auto":[epochs]}`, `practice{…}`, `next{"p|family":epoch}`, `next_practice{}`, `texts{sha:epoch}`, `usage{p:{buc_pct,regain_min,app_pct,at}}`, `slow_until{p}`, `fails{"p|kind":[epochs]}`.

```python
class Verdict(NamedTuple): ok: bool; mode: str; why: str; not_before: Optional[datetime]; code: str
def check(cfg, acct, platform, kind, *, origin, text="", target=None, manual=False, force=False, signed=None, now=None) -> Verdict
def queue(cfg, acct, platform, kind, *, key, text, target, origin, signed, outlet="", runner="social",
          not_before=None, expires=None, force_practice=False, force=False, now=None) -> str   # refuses a key queued/sending/done
def due(cfg, acct, now=None, runner="social") -> List[dict]
def drain(cfg, now=None, budget_s=180) -> List[str]            # state/social/locks/send.lock LOCK_NB
def send(cfg, acct, action, now=None) -> dict
def record(cfg, acct, platform, kind, *, state, **fields) -> None ; def record_post(cfg, acct, day, slot, outlet, upd, text, error=None) -> None
def post_gate(cfg, acct, outlet, now) -> str ; def post_tripwire(cfg, acct, outlet, err, now) -> str
def read_paused(cfg, acct, outlet_or_platform, now=None) -> str      # "" unless guard on and a read hold or O.meta_hold
def tripwire(cfg, acct, platform, kind, err, now=None) -> Optional[dict] ; def note_usage(cfg, acct, platform, usage, now=None) -> str
def slow(cfg, acct, platform, now=None) -> bool ; def held(cfg, acct, platform, kind, now=None) -> str
def hold(cfg, acct, platform, family, seconds, why, code=None, releasable=True, by="guard", now=None) -> dict
def release(cfg, acct, hold_key, by, now=None) -> str           # refuses releasable False
def stop(cfg, by, why="") -> str ; def start(cfg, by) -> str ; def stopped(cfg) -> str
def rows(cfg, slug=None, since=None, until=None, kinds=None, states=None) -> Iterator[dict]
def counts(cfg, acct, platform, kind_or_family, window_s, now=None, practice=False) -> int
def yt_spend(cfg, units, method, write, now=None) ; def yt_left(cfg, now=None) -> int
def forget(cfg, acct, person_key) -> int ; def drain_job ; def prune_job
def health ; def summary ; def morning_lines ; def doctor
RNG = random.Random()   # tests seed it
```
**`check()` order**
1. Stop.
2. Mode: off refuses; practice gives practice.
3. Kind rules:
   - An automated origin on a manual-only kind is refused.
   - An automated YouTube write is refused unless kind == first_comment and `signed.what=="clip"`.
4. Capability. Live: refused "waits for <permission>". Practice: the row notes it.
5. Messages: with `messages_access` standard and a target not a tester, the action is practice ("DMs reach only app testers until Meta's review").
6. Testers mode: an automated action whose target is not a tester is practice.
7. Warm-up: automated engagement before `max(created + warmup_days, first factory post + 14 d)` is practice. Missing `created` is practice.
8. Holds and `O.meta_hold` (Meta writes).
9. YouTube units.
10. Windows:
    - quiet hours (automated) set `not_before`;
    - a private reply after 6 days is skipped;
    - a DM outside 23 h is refused;
    - a YouTube comment before public waits.
11. Caps:
    - kind hour, then family, then engagement total, then IG publish quota.
    - Hour full: wait.
    - Day full: automated is skipped; manual is refused with the next time. `force` is allowed only for manual caps.
12. Spacing gives `not_before = next + jitter`.
13. Text, automated only:
    - byte and character limits;
    - no em or en dash;
    - URLs only in DMs for asks_source/asks_full/part2;
    - the same words in 24 h are skipped. Manual: refused unless forced. first_comment is exempt.

**`send()`**
1. Lock the account outward lock and re-read the action.
2. Check the politics:
   - done: skip.
   - sending under 10 min: leave it.
   - sending 10 min or more: the lost-answer path.
3. Run `check()`. A wait updates `not_before`; a hard refusal is skipped or refused.
4. Executor `still_signed`.
5. Practice: write the row and the practice spacing, then return.
6. Write politics `sending` and the log row, set spacing, and unlock.
7. `execute()` outside any lock.
8. Lock again:
   - done: write the result and `note_usage(D.last_usage())` (only this platform's own numbers: `usage_for`; the thread's last numbers are cleared before each `execute()`).
   - PlatformError: classify; on a tripwire, the action is re-queued after the hold, not a try; otherwise clear the key, tries+1, and gave_up at 3. The words' 24-hour digest comes off again (`_unmark_text`): they did not go out.
   - Timeout or URLError, and a PlatformError of HTTP 500 or over or one Meta calls transient: keep `sending` (unknown), so it is looked up before anything is sent again.
   - `net.Offline` (it never left this Mac) and `direct.Pending` (the platform is not ready for it yet, e.g. a Short not public): back in the queue in 5 minutes, not a try, and the send it began comes off the caps (`_uncount`). Pending gives up after 36 looks (3 hours).
9. `on_state` runs after unlock.

**Lost answer:** `find()`.
- Found: done.
- Not found: first_comment, reply_public and hide resend once. reply_private, dm, story and card_post give up with "Not sure it went: look on <platform>, then press Send again".
- Find error, or an executor with no `find`: lookups+1; gave_up at 3. Nothing is resent without a look.

**Refusals in a part:** `registry.calls`, the poster's hooks, the review page's social calls, `still_signed`, `find`, `on_state` and the drain catch `SystemExit` (this codebase's refusal) like any error: one part's refusal never ends a post-due run, the page, the doctor or the morning notice. A refusal from a social page's own GET is answered as a 400 with its words.

**Never hold the outward lock and a board lock together.**

### 3.3 Extras and the post hook
- **File:** `state/<slug>/extras/<day>/<HHMM>.json`, written `json.dumps(…, indent=1, sort_keys=True)`:
```json
{"v":1,"account":"gaming","day":"2026-10-20","slot":"08:45","clip_sha":"…",
 "first_comment":{"facebook_reels":{"text":"…","from":"default|edited|task"}},
 "poll":{"instagram_reels":{"options":["Yes","No"],"from":"task"}},
 "trial":{"instagram_reels":{"graduation_strategy":"SS_PERFORMANCE","experiment":"e-0003","arm":"B"}},
 "experiment":{"e-0003":"B"},"edited":[{"by","at","what"}]}
```
- **Slot pointer:** `slot["extras"]={"path","sha256"}` (sha256 of the file bytes).
- **Written at collect** only when some contributing switch is not off. Edited only while READY.
- **Board marker:** `outlets[outlet]["social"]["first_comment"]={"state","action","id","text_sha","at","why"}` (nested; `_record`'s drop tuple, MATCHED and `_carry` never touch it).
- **Post hook:** the poster calls `_after_post`, which calls `social.on_posted` after the success `_record`. The hook reads the board fresh and runs FOLLOW_UPS.
- **live_at:**
  - Meta: `rec.published_at` (never `rec.at`), and only `provider=="direct"`.
  - YouTube: `max(publish_at, upload at)`, and only records carrying `clip_sha`.
- **`firstcomment.sweep`** catches misses within 20 h.

```python
def path(acct, day, slot_key) -> Path ; def read(slot) -> dict ; def write_locked(acct, day, slot, data, by, what) -> None
@contextmanager def edit(cfg, acct, day, slot_key, by, what) -> Iterator[dict]     # takes B.edit; READY only
def hash_parts(slot) -> Optional[str] ; def problems(slot, cached=False) -> List[str] ; def seen_parts(slot)
def adopt(slot) -> List[str] ; def clear(slot) -> None ; def first_comment(slot, outlet) -> str
def ig_form(slot, acct) -> Dict[str,str]   # poll_attachment only if signed + poll=="on"; trial_params only if trial_reels=="on"
```

### 3.4 Engage store (socialstore.py)
Item `items-YYYY-MM.json` `{"v":1,"items":{iid:item}}`:
```json
{"id":"ig:17890011223344556","platform":"instagram","kind":"comment","platform_comment_id":"17890011223344556",
 "parent_id":null,"thread_id":null,"post":{"platform_id":"17950654665054548","outlet":"instagram_reels","board":"2026-10-19",
 "slot":"08:45","url":"…","title":"…"},"author":{"key":"instagram:1784…","id":"1784…","name":"some_user","is_us":false},
 "text":"source?","at":"…","seen_at":"…","like_count":0,"hidden":false,"can_reply_privately":null,
 "tags":["question","asks_source"],"suggest":{"hide":false,"why":""},"state":"new","answered":null,
 "private_until":"…","replies":[{"id","by":"us","text","at","action"}],"auto":{"rule","run","result","why"},"suggested_reply":null}
```
- **ids:**
  - comments `ig:`/`fb:`/`yt:` plus the platform id;
  - DMs `igm:`/`fbm:`;
  - threads `igt:`/`fbt:`.
- **state:** new | answered | done | hidden | gone.
- **Threads** `{tid:{platform,with{key,id,name},updated,last_in_at,window_until,messages[≤20 {id,at,from_us,text}],unread,state}}`.
- **Contacts** `{key:{name,first_at,last_in_at,last_out_at,last_auto_out_at,outs[90 d],private_replied[],stop,stop_at,stop_why}}`.
- **saved** `{"items":[{id,label,text,platforms,updated}]}`.
- **cursor** per platform.
- **posts** index `{platform_id:{outlet,day,slot,slug,title,url,cover,went,factory}}`.
- **waitlist** `{source_key:[{item,contact,at,expires,sent}]}`.

```python
def edit(acct, name) ; def read(acct, name, default=None) ; def upsert_items(acct, items, now) -> Tuple[List[str], List[str]]
def get_item ; def items(acct, since=None, until=None, states=None, platforms=None) ; def set_state(acct, iid, state, by, now, answered=None)
def add_reply ; def upsert_thread(acct, thread, messages, now) -> List[str] ; def thread ; def threads
def contact ; def touch_contact ; def stop_contact ; def stopped ; def saved ; def saved_put ; def saved_delete
def cursor ; def set_cursor ; def posts_index ; def set_posts_index ; def waitlist_add ; def waitlist ; def waitlist_mark
def append_run ; def runs ; def prune(acct, now, keep_days=45) ; def forget(acct, person_key, by) -> int
```
`upsert` keeps the local fields: state, answered, replies, auto, suggested_reply.

## The engage store as built (socialstore.py)

The names in 3.4 with their arguments. `acct` is an Account or a slug; times are datetimes (UTC when naive); every
file is under `state/<slug>/engage/` with its own lock.

```python
NAMES = ("threads", "contacts", "saved", "cursor", "posts", "rules", "waitlist")    # plus items-YYYY-MM
def item_id(platform, platform_id, kind="comment") -> str     # "ig:..", "fbm:.." for a DM message
def thread_id(platform, platform_id) -> str                   # "igt:..", "fbt:.."
def root(acct) -> Path ; def edit(acct, name) -> ContextManager[dict] ; def read(acct, name, default=None)
def upsert_items(acct, items, now=None) -> Tuple[List[str], List[str]]          # (new ids, changed ids)
def get_item(acct, iid) -> Optional[dict]
def items(acct, since=None, until=None, states=None, platforms=None) -> List[dict]   # newest first
def set_state(acct, iid, state, by, now=None, answered=None) -> dict
def unhide(acct, iid, by, state="new", now=None) -> dict      # hidden cleared, back to new/answered/done (2026-10-20)
def set_local(acct, iid, **fields) -> dict                    # our own notes: auto, suggested_reply only (2026-10-20)
def add_reply(acct, iid, reply) -> dict                       # the same action or id updates, never twice
def upsert_thread(acct, thread, messages, now=None) -> List[str]                 # new message ids
def thread(acct, tid) -> Optional[dict] ; def threads(acct, states=None, platforms=None) -> List[dict]
def set_thread_state(acct, tid, state, by, now=None) -> dict  # new | done
def contact(acct, key) -> dict
def touch_contact(acct, key, now=None, *, name="", inbound=False, outbound=False, auto=False,
                  private_reply="") -> dict
def stop_contact(acct, key, now=None, why="", on=True) -> dict ; def stopped(acct, key) -> bool
def saved(acct) -> List[dict] ; def saved_put(acct, label, text, platforms=None, sid="", now=None) -> str
def saved_delete(acct, sid) -> bool
def cursor(acct, platform=None) -> dict ; def set_cursor(acct, platform, upd) -> dict
def posts_index(acct) -> dict ; def set_posts_index(acct, index, now=None) -> None
def waitlist_add(acct, source_key, item, contact_key, now=None, expires=None) -> bool   # 7 days by default
def waitlist(acct, source_key=None, now=None) -> List[dict]   # waiting: not sent, not expired
def waitlist_mark(acct, source_key, item, sent) -> bool
def append_run(acct, run, now=None) -> None ; def runs(acct, since=None, rule=None) -> List[dict]
def prune(acct, now=None, keep_days=45) -> int
def forget(acct, person_key, by, cfg=None) -> int             # also outward.forget(cfg, acct, person_key)
```

## Tests

- Every test file starts with the line that switches the network off and moves the state:
  `import os, tempfile; os.environ["CLIP_FACTORY_OFFLINE"] = "1"; os.environ.setdefault("CLIP_FACTORY_STATE", tempfile.mkdtemp())  # noqa: E401,E702`
- `tests/social_fakes.py` has the shared stand-ins: `FakeGraph` and `FakeYouTube` (answers in place of `direct._http`;
  a DELETE or an unexpected call raises), `patch_http`, `write_tokens`, `FakeEngine` (a card engine that writes
  placeholder files), `fake_ytdlp`, `Clock`, `cfg_in_tmp` (a throwaway config with the state and key folders moved),
  `slot_ready`, `slot_posted` and `serve` (Clip Review on a free port). Put the tests folder on `sys.path` to import it:
  `sys.path.insert(0, str(Path(__file__).resolve().parent))`.
- A test that puts a stand-in at `sys.modules["clipfactory.<part>"]` restores the original afterwards (never pops it),
  then calls `registry.reset()`.
- `/file` shows from `state/` only `.mp4`, `.jpg`, `.jpeg`, `.png` and `.srt` (never a JSON file, a log or the
  review-token): a part keeps the pictures and videos it shows under `state/<slug>/<part>/` and links them with
  `socialui.file_url`.
