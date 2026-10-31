# The Log tab, the Week page and moving an account (log)

Files: `clipfactory/logpage.py` (the Log tab), `clipfactory/weekpage.py` (the Week page under Clips),
`clipfactory/socialmove.py` (`factory.py social export|import`). Tests: `tests/test_log_page.py`,
`tests/test_week_page.py`, `tests/test_social_move.py`. Nothing here calls a platform: every view reads files only.

The integrator folds the sections below into the README, POSTING-APIS.md, SETUP-POSTING.md and NEW-ACCOUNTS.md.

## README: the section text

### The Log tab (http://127.0.0.1:8781/log)

Every outward action of the social layer in one place, with its result. Four views:

- **Actions**: every comment, reply, private reply, message, hide, story, card and (with the guard on) post, newest
  first. Filters: account, platform, kind, state, live or practice, 24 hours, 7 days or 30 days. A row opens to show
  where it went (the call's shape, such as `POST /{id}/comments`), what it answered (the comment or post id, a link),
  the words (practice rows keep the exact words; live rows only their digest), Meta's code and subcode, the trace
  id with a Copy button (paste it to Meta support), and the usage Meta reported.
- **Health**: sent, practised, the success rate (done out of done, failed and gave up) per platform and kind, posts
  counted from the boards (posted against gave up), the failed posts with why and a link to the clip, Meta's usage
  per account, this machine's YouTube units today, and the time saved. Time saved is labelled "Our estimate", with
  its formula in minutes: a post 3, a first comment 1, a public reply 0.5, a private reply or message 1, a hide
  0.25, a story 2, a card post 3, a card made 10, a written scorecard 3. Only what really went out counts.
- **Holds**: what Meta or YouTube asked us to pause, per account and platform, with why and until when. "Release
  early" shows only on a hold that may be released; its confirm names the code, the time and what can go out again
  ("Meta asked us to wait (code 368 at 14:02). Release early?"). Any other hold says "Meta asked us to stop: this one
  cannot be released early" and when it ends by itself.
- **Switches**: every part per account, with its value (off, watch, practice, testers, on) as a row of buttons. A
  value that cannot be chosen yet is greyed with the reason, and one line under each part says what it waits for:
  its part to be built here, a permission (from the sign-in's own permission list), practice days, or "I read
  these". A part that speaks for the account (first comments, automations, card and story posts, the poll, trial
  reels) shows its go-live checklist; "I read these" says you read its practice rows and opens them. Going live is
  refused while any checklist line is open. Every click asks first and names the change. The settings are shown
  read only, each with the `factory.py social set` line that changes it.

The header has **Stop everything** (it stops every comment, reply, message, story and card on every account; signed
clips keep posting) and, while stopped, **Start again**. Each asks first.

### The Week page (http://127.0.0.1:8781/week)

Opened from the Week link on the Clips calendar. Monday first, read only: on a computer, accounts down the side and
the seven days across; each day lists its clips, cards and stories by time with a status (Posted, Scheduled, Needs
you, Problem, Empty, Practice) and a link to the clip or the Cards tab. On a phone the days stack, today first. The
strip on top counts the month per account: posted, scheduled, to sign, empty, failed, cards posted. Clients and
switched off accounts are not on it.

### Commands

    factory.py social export <account> --to DIR      copy the account's state and stats series into an empty folder
    factory.py social import DIR [--replace]         check every file's sha256, then take them into this machine
    factory.py social import --from DIR [--replace]  the same

## POSTING-APIS.md: lines

- The Log tab and the Week page read files only (the outward log, holds.json, guard.json, the boards, the cards
  boards, the coach files, youtube-units.json): no API call is made to draw them, and no timer belongs to them.
- Release early calls nothing either: it ends the hold in `state/<slug>/outward/holds.json` (outward.release), and
  only for a hold marked releasable. A block (368) on comments, replies, messages or hides, a regain time, Meta's
  90% usage, and YouTube's quota hold (until 00:15 Pacific) are never releasable. The Release confirm says who paused
  it: Meta or YouTube, the factory itself after 3 failures in an hour, or a sign-in that stopped working (releasing
  that one only brings more failures until you connect again).
- The trace id on a failed row is Meta's `fbtrace_id`: quote it to Meta developer support.

## SETUP-POSTING.md: lines

- After switching a part to practice (Log, Switches), let it practise; then read its rows (Log, Switches, "Show the
  practice rows") and press "I read these". Going live needs every checklist line done, the permission included.
- A greyed On button names the permission it waits for. After you add it in Meta (or in the YouTube sign-in), run
  `factory.py connect meta <account>` (or `connect youtube <account> --with comments`) so the factory reads it.

## NEW-ACCOUNTS.md: moving an account to the Mac Mini

1. On the main Mac, in config/accounts.json, set `"active": false` for the account; commit, and `git pull` on both
   Macs. Neither runs it now.
2. On the main Mac: `python3 factory.py social export <account> --to /Volumes/<drive>/move-<account>` (an empty
   folder, a full path). It refuses while the account is still switched on there (step 1), so nothing queued can go
   out from both Macs. It copies `state/<account>/` and its stats series and writes `manifest.json` with each
   file's sha256. Lock files are left out. Sign-ins never move this way.
3. Copy the folder to the Mac Mini.
4. On the Mac Mini: `python3 factory.py social import /Volumes/<drive>/move-<account>`. It checks every file first
   and imports nothing if one differs. If the Mini already has state for the account, add `--replace`: every file is
   copied and checked first, then the old state is moved (never deleted) to
   `state/config-backups/move-<account>-<stamp>/`; if that swap fails, the old state goes back where it was.
5. On the Mac Mini, connect the sign-ins again (`factory.py connect meta <account>`, `connect youtube <account>`).
6. In config/accounts.json set `"active": true` and `"machine": "<the Mini's CLIP_FACTORY_MACHINE>"`; commit, pull on
   both. On the Mini: `python3 factory.py timer install`, then `python3 factory.py doctor`.
7. Each social switch moved with the account (they live in its state). Stop everything does not: it is per machine.
   The YouTube unit budget is per machine too.

## Go-live steps for this part

- Nothing to install: the Log tab and the Week page appear once the merge is in and Clip Review restarts
  (`launchctl kickstart -k gui/$(id -u)/com.clipfactory.review`). No timer, no scheduled task.
- Check at 280, 375, 768, 1024 and 1700 px: Log (each of its four views) and Week. No page scrolls sideways.
- Doctor shows `--  log: N actions in 7 days, success 96%`, and a `NO  <account>: practice rows wait for "I read
  these" (<part>)` line when a part in practice has every other checklist line done (enough practice, nothing over a
  cap, the permission) and practice rows nobody has read yet: pressing "I read these" is all that is left before On.

## Notes for the integrator (done 2026-10-20)

- The five seam pins that wrote "not built yet" for the Log tab, the Week link and `social export` were re-pinned to
  the built world in the integration: the tabs and the calendar's keys are computed from `REG.present`, and off means
  off still proves the Clips page body, `/live` and posting are unchanged apart from the tab links and card CSS.
- `social import --from DIR` works: `social.cli` and `factory.py` hand a part's words on whole before any parsing
  (argparse's REMAINDER refused a first word starting with `--`). `social import DIR` still works.
