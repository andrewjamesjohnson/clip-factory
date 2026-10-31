# The hook coach (clipfactory/coach.py, prompts/coach.md)

A scorecard for every clip that is ready to sign, in two layers, shown on its card on Clip Review as a fold headed
"Coach" with the written total ("Coach 6.4 of 10") and an EXPECTED line from the learner's records. Off for every
account until `factory.py social on <account> coach`. It never reaches the network and
never queues a render.

| Layer | Who writes it | When | What |
|---|---|---|---|
| measured | the factory, in code | at collect (files only), and the studio job every 10 minutes from 06:26 to 20:45 (ffmpeg) | time to the first word, words a second in the first 3 s, silence, the longest hole, picture changes in the first 3 s, the hook on screen, the YouTube title's features, the length against the best band, the creator's, format's, slot's and length's record; scores 0 to 100 for hook (.4), keep watching (.2), no sound (.15), density (.15) and their total, shown as one Mechanics line |
| written | the Claude task clip-coach (prompts/coach.md) | about 05:30 and 12:30 (cron `20 5,12 * * *`) | five scores 1 to 10 that judge the moment, the hook words and the payoff (keys hook, keep_watching, no_sound, density, share; the card words them Hook words, Payoff, Reads muted, Moment, Share), one fix, three other titles, why; their mean is the headline's total |

Stored in `state/<account>/coach/<YYYY-MM-DD>/<HHMM>.json` (never in a delivery or Clip Drafts folder). A scorecard
is stale once its `clip_sha` is not the slot's clip (a new clip in the slot), or once a platform's own cut joins or
leaves the slot after it was measured (the seconds an outlet gets changed); collect or the studio job writes a fresh
one, keeping the written layer when the clip is the same. The inputs that may vanish (the srt, the GTA pack, the GTA grade block from RUN.md or build.log, the Politics
render.log lines for silence removed, tempo and name cards, from the newest render log and for this clip only, a GTA
reactions words.json) are copied at collect into
`state/<account>/coach/inputs/<sha16>/`, because `/private/tmp/clipwork` empties itself.

Its best length band and weights come from the learner's playbook (`state/<account>/playbook.json` coach block)
only when `learn.playbook` is on; otherwise GTA 30 to 45 s and the Politics the band each platform takes. Creator,
format, slot and length records come from `state/<account>/learn/features.json` when the learner has written it
(the length record is the clip's length band, learn.LENGTH_BANDS, on the platforms that get the clip itself). The
learner never pools platforms, so the EXPECTED line reads each record one platform at a time, as a multiple of that
platform's usual: worded only from 5 posts on one platform ("MrBossFTW clips: usually 1.3x on Facebook, 9 posts"),
otherwise "Gaggle clips: 4 posts, too few to say". A record raises a flag only on strong evidence: 8 posts or more
on one platform that usually did under half the usual, three in four of them under it. The skills already cut clean
mechanics (the Politics talks at about 0.4 s, 4 to 5 words a second, little silence), so the measured total sits at 95
to 100 and is one line inside the fold ("Mechanics 97: talks at 0.4 s, 4.7 words a second": the number and the two
most telling signals, a weak one first); a mechanic raises a flag only when it is weak. A past day's card, or a clip
collected before the coach was on, shows no fold.

## README: "Your day" rows

| when | what |
|---|---|
| every 10 min, 06:26 to 20:45 | the studio job: the coach measures new clips and reads their picture changes (and the cards) |
| about 05:30 and 12:30 | the clip-coach task writes each ready clip's scorecard: five scores, one fix, three titles |

## README: section text (for "## The social layer", the coach part)

**The hook coach.** Every clip ready to sign gets a coach fold on its card. Its headline is "Coach" with the
clip-coach task's written total ("Coach 6.4 of 10") and what to expect from the account's records: this creator,
format, slot and length, each with its number of posts ("MrBossFTW clips: usually 1.3x on Facebook, 9 posts", or
"too few posts to say" under 5 on a platform). Inside: those records, the task's five scores (the moment, the hook
words, the payoff, how it reads muted, share), one fix and three other YouTube titles, then one Mechanics line
("Mechanics 97: talks at 0.4 s, 4.7 words a second"). A flag shows only when a mechanic is weak ("The first word
comes at 1.8s", "A 4.0s hole with no words at 12s") or a record is strong evidence (8 posts or more on a platform,
usually under half the usual). "Use this title" (only while the clip waits for
your sign-off) changes only the YouTube title in that clip's youtube_shorts.txt: GTA keeps its " | credit" ending when
it fits in 100 characters, the Politics's TITLE section is replaced. Nothing goes out: Approve signs the words as they
are then. "(undo)" puts the old words back. The task never invents a fact: its titles use only words from the
transcript, the slate or a verified cast, and the coach refuses a title with a name or number that is not there
(the first word too, unless it is a word that opens a sentence), a Politics name no person verified (anyone in the job's
cast.json, card or not), or < or > (YouTube does not take them). "Use this title" checks the title the same way. The scorecards are in state/<account>/coach/; the learner checks them against real
views. Switch on: `factory.py social on <account> coach`.

## README: commands

```
python3 factory.py coach pending --date today                       ready clips without a written scorecard
python3 factory.py coach card <acct> --date D --slot HH:MM          what the clip-coach task reads for one clip
python3 factory.py coach write <acct> --date D --slot HH:MM --file F --by clip-coach
                                                                    the written layer (validated; READY clips only)
python3 factory.py coach measure [<acct>] [--date D] [--slot HH:MM] [--scenes]
                                                                    measure again from the files (--scenes: ffmpeg,
                                                                    06:26 to 20:45 only)
python3 factory.py coach show <acct> --date D [--slot HH:MM] [--json]   a day's scorecards
```

## POSTING-APIS lines

- The coach reaches no platform. Its only outside program is ffmpeg on the delivered clip, 3 seconds, at most 8 clips
  every 10 minutes, from 06:26 to 20:45, 20 s timeout each, never in the night shift, never in the render queue.
- Timer row: `com.clipfactory.studio`, every 600 s, `factory.py social studio --quiet` (cards:tick, coach:tick).

## SETUP-POSTING lines

- None: the coach needs no permission, token or key. It needs ffmpeg (`brew install ffmpeg`; doctor says when it is
  missing).

## Doctor lines

```
--  coach: off (it waits for factory.py social on <account> coach)
ok  coach: Gaming: 18 of 20 ready clips measured (15 with picture changes), 12 written today
--  coach: Gaming: no written scorecards yet (create the clip-coach task at go-live)
NO  coach: ffmpeg is not found, so picture changes are not measured        fix: brew install ffmpeg
```

## The clip-coach task (created at go-live, with Andrew's OK)

- Name `clip-coach`, cron `20 5,12 * * *` (it fires about 05:30 and 12:30; moved from 06:25 on 2026-10-22, because every clip was signed before it ran and it wrote nothing), saved in Auto mode on the account the clip
  tasks follow, body: "Read and follow ~/clip-factory/prompts/coach.md".
- It runs `factory.py coach pending --date today`, then for each clip `coach card`, judges, and `coach write ... --by
  clip-coach`; the Instagram poll with `factory.py extras poll` only when `extras status` shows it practice or on.
- It never registers a shift, never renders, never edits a board or a words file; `hermes.TASK_PROMPTS` already lists
  `clip-coach` on both slugs, so doctor's one-truth check reads its prompt once it exists.
- Calibration it is told: 5 ordinary, 7 good, 8 strong, 9 rare, 10 never.

## The studio agent

`com.clipfactory.studio` (timer.AGENTS "studio", every 600 s, `factory.py social studio --quiet`) runs `cards:tick`
then `coach:tick`, each under its own lock (`state/social/locks/coach-tick.lock`). `coach:tick` does nothing at all
before 06:26 or from 20:45, or while every account has the coach off. It is installed by `factory.py timer install` at
go-live.

## Go-live steps for the coach

1. After the merge and `factory.py timer install`: `factory.py doctor` shows `--  coach: off ...`.
2. Day 0 (Switch-on sequence): `factory.py social on gaming coach` and `factory.py social on politics coach`.
   Clips collected from then on get a scorecard at collect; `factory.py coach measure gaming --date today` and
   `... --date tomorrow` measure the clips already on the boards (files only; add `--scenes` between 06:26 and 20:45).
3. With Andrew's OK, create the `clip-coach` task (above). Run it once by hand and check `factory.py coach show
   <acct> --date today` and the "Coach" fold on a ready card at 375 px and 1280 px.
4. Doctor then says `ok  coach: <account>: N of M ready clips measured ..., K written today`.
5. Learner: `coach:total(acct, day, slot)` gives the measured total for its features rows; `learn.playbook` on lets
   the approved coach block (length_best, first_word_max_s, weights) reach the scores.

## Contract notes (for the integrator)

- New fields, nothing renamed (the learner and the story-repost rule in cards.py read `measured.total` and
  `written.scores`): `headline` = `{written, words, expected}` (written: the mean of the five
  scores, out of 10), `expected` = one row per record `{what, label, n, enough, outlets: [{outlet, n, median_rel}],
  words}`, `mechanics` = `{total, signals: [two], weak: [flag keys], words}`, `signals.records` = `{creator|format|
  slot|length: {label, n, median_rel, by_outlet: {outlet: {n, median_rel, under}}}}`, `signals.length_record`, and
  `kind` (`mechanics` or `record`) on each flag. A scorecard measured before `signals.records` existed is measured
  again by the studio job (files only, the written layer kept).
- Scorecard file keys as architecture 3.7, plus: `signals.length_by` (seconds each outlet gets, `stats._sent_secs`),
  `signals.longest_gap_at`, `signals.wps_all`, `signals.words`, `signals.render` (Politics), `signals.grade` (GTA),
  `signals.cast` (Politics: status and the verified names), `signals.fit`/`fit_y`, `signals.scene_times`,
  `scene_tries`, `scene_error`, and `title_log` (the last 10 title swaps). `length_best` and `length_vs_best` are
  per outlet: `{outlet: [lo, hi]}` and `{outlet: seconds outside the band, 0 inside}`.
- `used_title` = `{title, was, by, at, path, before, after_sha}` (`before` is the whole words file before the swap,
  so "(undo)" puts it back byte for byte, and only when nothing else changed it since).
- The click body comes from the Clips page's generic `data-x` branch. The act reads the day from `fields.date` (a
  hidden field the fold sends) first, and from `body.date` (which the page fills from the card's `data-date`) when it
  is missing. The act also checks `seen` against `board.seen_key`.
- Seam, done 2026-10-20: `review.py` reconcile() now carries a rebuilt card's open folds across (`details[open]
  [data-k]`), so the Coach fold stays open after "Use this title" and its "(undo)" line shows; the data-x branch
  posts the card's own `data-date` (the hidden `fields.date` still wins in the act); test_social_registry test_6
  checks `card_css()` with parts that are not built.
