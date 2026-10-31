# Reports and the learner (learn)

The recursive loop: every morning the factory reads what it already has on disk and writes, per account, what cost
views, the best and worst posts with why, what works with how sure it is, and proposals Andrew approves or declines.
Approved proposals go into a playbook the scout, the coach and the Claude runbooks read. Nothing in this part
reaches a platform: it reads boards, the stats snapshots and checkpoint series, coach scorecards, verdicts, scout
decisions and the extras files, and writes JSON and Markdown under `state/<slug>/learn/` and `state/<slug>/playbook.json`.

## Files

| Path | What |
|---|---|
| clipfactory/learn.py | rows, baselines, findings, problems (views lost), best and worst, coach accuracy, publishing health, proposals, experiments, did it work, the daily job, the plugs, `factory.py learn` |
| clipfactory/statmath.py | median, quantile, bootstrap range, Mann-Whitney, Benjamini-Hochberg, Spearman, Fisher range, split-half (stdlib) |
| clipfactory/playbook.py | the playbook: `read`, `apply`, `revert`, `take_back`, `revert_change`, `scout_block`, `apply_to_scout`, `learned_factor`, `coach_block`, `brief` |
| clipfactory/reportspage.py | Clip Review's Reports tab (Today, Week, Proposals, Experiments, Playbook; Ideas beside them) and its clicks |
| clipfactory/scout.py | three guarded lines: `run()` adds the playbook after `load()`, `score()` multiplies by the learned factor, `rank()` shows it on a find |
| prompts/weekly-report.md | the Sunday task `clip-weekly-report` |
| prompts/results-coach.md | the morning task `clip-results-coach`: what happened in the best and worst clips, at most two proposals an account |

State, per account: `learn/features.json`, `learn/report-YYYY-MM-DD.{json,md}`, `learn/notes-YYYY-MM-DD.json`, `learn/proposals.json`,
`learn/experiments.json`, `learn/did-it-work.json`, `learn/playbook-history.jsonl`, `learn/weekly/YYYY-Www.md`,
`playbook.json`. Reports and rows older than 120 days are pruned.

## How it reads the numbers

- **The 24-hour number** comes from the post's checkpoint series (`state/stats/series/`) when checkpoints are on.
  Until then it is read off the stats snapshots (`state/stats/history/`) and the board's latest metrics: between the
  two readings around 24 hours, or, with readings on one side only, stretched along a gentle log curve and never
  below a reading before 24 hours or above one after. The report says so ("an estimate").
- **The usual** (the baseline) of a post is the median 24-hour views of the same account and platform over the 14
  days before it (30 when fewer than 5), counting only posts that went up BEFORE it, and it needs at least 5 of
  them. Never the post itself, never a later post, never another platform. `rel_24h = ln((v24 + 1) / (baseline +
  1))`. A post with no usual yet (a platform's first five) stays out of best, worst, slow starts, what works and
  every views-lost figure, and the report says so: "No usual yet on Instagram: 4 posts with a 24-hour number so far,
  and it takes 5", and how many of the window's posts were left out of best and worst for it.
- **Platforms are never pooled.** A finding is one factor level against the rest of that platform's posts: lift =
  exp(median rel in the group minus median rel in the rest), a 90% bootstrap range (2000 resamples, seeded), the
  Mann-Whitney test, Benjamini-Hochberg across the day's tests, and the same direction in the older and newer half.
- **How sure**: strong (20 posts a side, the range leaves out 1x, q at most 0.1, the halves agree); likely (12 a
  side, settings `learn.min_n`, q at most 0.2); early sign (6 a side, shown only, never proposed). Nothing is
  claimed for a platform until it has 30 posts with a 24-hour number.
- **Views lost**: empty, declined, refused, failed and unsigned slots at the usual views times the platforms; a
  signed clip that failed, gave up or never reached a platform at that platform's usual; a late post at the share of
  the first day it missed (hours late / 24). Slow starts (under half the usual, over the day and the day before)
  and strong or likely weak factors are shown as the sum of the usual minus the actual; they overlap the other lines
  (the same posts) and are not added into the total. A platform with no usual yet is left out of a figure and named
  ("not counting Instagram (no usual yet there)"); a late post there says "views lost not known".
- **A sudden drop** (per account and platform): the median 24-hour views of the last N posts with a 24-hour number
  (N is 5, or 4 with 8 or 9 posts; 4 a side at least) under a third of the median of the N before them, the last of
  them in the report's window. "YouTube views dropped: the last 5 posts (10.18 to 10.19) got 6 at 24 hours, the 5
  before (10.16 to 10.18) got 1,040", ranked by its views lost (the N before's median less each post's views), with
  a fix that lists what to check (a copyright claim or a limited video in YouTube Studio, the upload cut, the title,
  the posting time) and the two groups side by side in numbers. Its posts are not listed again as slow starts.
- **A platform gap** (per account): a platform whose usual is under a tenth of the account's best platform's, each
  resting on 8 posts or more. "YouTube is Gaming's weakest platform: 13 views is usual there, 253 on Facebook",
  with a fix that sets the best platform's length, own cut, title length and posting time beside the weak one's. Not
  one day's loss: it has no views-lost figure and comes after the ones that do.
- **Which**: the slots or posts a problem is about; a list cut short ends "and N more", so it agrees with the count.

## Proposals and the playbook

- Kinds: `factor` (scout channels, people, heat words: 0.5 to 1.6, half the measured lift), `coach` (best length
  per platform, first word, weights, prefer and avoid), `runbook_note`, `experiment`, `draft_rule` (the Inbox's most
  frequent unanswered question with no automation), `revert` (a take-back of one rule); everything else is
  `advice` (approving it changes nothing). A factor proposal carries a backtest: `scout.rank()` over the last 7
  saved scout boards.
- An approved `draft_rule` is kept in the playbook as a request, and nothing is sent. Since the integration
  (2026-10-20) the automations part offers
  `rules.draft_from(cfg, acct, intent, words, by, now) -> str`: approve hands the request over through the registry
  and the starter answer to that question becomes a rule in practice, unsigned (nothing when a rule already answers
  it); the confirm says so. Without the automations part the confirm says "noted only".
- A proposal ends after 14 days. A declined one comes back only when its evidence has doubled; an approved one not
  while its rule stands in the playbook (once taken back or ended, it may come again; approved advice holds 60 days).
- "Test it first" makes an experiment instead, which Andrew starts on Experiments, and only for a change the factory
  can carry out clip by clip (today: a first comment kept or left off). A scout factor, a coach setting, a runbook
  line, a draft automation or an hour cannot be split clip by clip: the chip is not offered and the command refuses.
- An approved rule lasts 60 days. 14 days after it came into force (approved while `learn.playbook` was on, or the
  day the switch went on after), "did it work" compares its posts before and after (weak evidence, and labelled so);
  worse by 10% files a take-back of that one rule (rules approved after it stay). A rule approved while the switch
  is off reads "not in force" and is checked once the switch has been on 14 days. Scout channels, coach lengths, the
  first word and coach prefer or avoid marks are measured; runbook lines, coach weights, scout people and heat words
  and draft automations read "not measurable".
- "Back to version N" on the Playbook view restores that whole version; its confirm lists what comes out, what comes
  back and what stays.
- `learn brief` (for the runbooks) holds the rules and runbook notes only while `learn.playbook` is on, and lists
  only strong and likely findings, each with its platform; early signs are counted, not listed.
- Experiments count clips, not posts: a clip's value is its mean rel_24h over the experiment's platforms, and an
  experiment ends at 10 counted clips an arm (or 21 days). A clip the test could not change (practice, no first
  comment to leave off, trial reels switched off) is written down and not counted. A clip collected again into a
  slot (Make again, a new delivery) keeps the slot's arm.
- A new account in a family (accounts.json `family`, else its maker) with no scout rules of its own starts from the
  family's factors, pulled toward 1.0 by n/(n + 12).

## The morning file and the results coach (2026-10-20)

- **Every clip, best to worst.** The report ranks every clip of the window (posts that went up the day before and on
  the day, with a 24-hour number: at 04:40 that is the day before's clips, so each clip shows up once, the morning
  after its first day) with its platforms side by side. A clip's x is its 24-hour views on the platforms with a usual
  over the sum of those usuals, so the platform that brings the most views weighs the most; a clip with no usual on
  any platform comes last. Each post shows its views at 24 hours, its x, and the board's latest watch numbers where
  the platform gives them (`watch_pct`, `watch_s`, `skip_pct`, `reach`: Instagram all four, Facebook % watched,
  YouTube none until YouTube Analytics is read, TikTok views only). The usual watch numbers are each platform's
  median over 14 days, from 5 posts.
- **Held back.** A post under 0.1x on a platform whose usual is 20 or more, while another platform took the same
  clip at 0.5x or more, is marked held back: the platform held it (a claim, a limited reel, or YouTube holding a
  Short for a day: four Politics Shorts of 10.19 sat at 0 to 16 views for about 27 hours, then were released), not the
  clip. The report keeps the 24-hour number: it is what the clip's first day got.
- **The morning file.** `config/accounts.json` `"report_desk"` names a folder; the 04:40 job writes
  `Clip report MM.DD.YYYY.md` there (named for the morning it is read), both accounts in one file: "In one minute"
  (each account's clips against the usual, the best and the worst clip, posts held back, the biggest problem, the coach's
  summary, the changes waiting), then each account's clips best to worst and its problems. No key, no file. A test
  or an offline run never writes outside a temp folder, whatever the key says. Doctor: `morning file: ...`.
- **The results coach** (`prompts/results-coach.md`, Claude task `clip-results-coach`, about 05:05): reads
  `learn clips <acct>` (the ranking with every platform's numbers, the coach's scorecard from before the clip posted,
  and the whole transcript), writes what happened in the best and worst clips with `learn notes` (checked: each clip
  must be in the ranking, plain words, 300 characters a note; kept as `learn/notes-<day>.json`, shown in the report
  and the morning file, kept when the report is made again), and proposes at most two changes an account with
  `learn propose --by clip-results-coach`. Andrew approves each on Reports; with `learn.playbook` on, an approved
  runbook note reaches the night's makers through `learn brief`, which the bot and scout runbooks of both accounts
  read before choosing (step 2 of each; the GTA bot's hand picks).

    python3 factory.py learn clips A [--date D] [--json]   the ranking with numbers and transcripts (read only)
    python3 factory.py learn notes A --file F [--date D] [--by clip-results-coach]
    python3 factory.py learn desk [--date D]                write the morning file again from the reports on disk

## Switches (all off; `factory.py social on <acct> <switch>`)

| Switch | Values | What it does |
|---|---|---|
| `learn` | off, on | the 04:40 report and proposals for the account; its Today band chips, morning fragment and calm line |
| `learn.playbook` | off, on | the scout reads the approved factors (`scfg["learned"]`); `playbook.coach_block` gives the coach its block; `learn brief` gives the runbooks the rules and runbook notes |
| `learn.experiments` | off, practice, on | running experiments give each collected clip an arm: practice writes the arm down only; on puts it in the clip's signed extras and does what it says (arm B of a first-comment test drops the first comment; of a trial-reel test makes the Instagram post a trial reel) |

`factory.py learn daily` by hand needs no switch and works offline on any copy of the state.

## README rows (for the integrator)

Timers table:

| job | command | when |
|---|---|---|
| learn | `factory.py social learn --quiet` (launchd `com.clipfactory.learn`) | 04:40: yesterday's report for each account whose `learn` switch is on |

Scheduled Claude tasks table:

| task | prompt | cron | writes |
|---|---|---|---|
| clip-weekly-report | prompts/weekly-report.md | `0 17 * * 0` (Sunday 17:00) | `~/Desktop/Clip Drafts/Reports/<Account> week MM.DD.YYYY.md` and `state/<slug>/learn/weekly/YYYY-Www.md`; proposals through `learn propose` only |

Commands:

    python3 factory.py learn daily [--account A] [--date D] [--json] [--no-proposals]   the report (by hand: no switch)
    python3 factory.py learn week A [--json]            the last 7 reports together (the weekly task reads it)
    python3 factory.py learn brief A                    40 lines for the runbooks
    python3 factory.py learn proposals A [--all]        the proposals
    python3 factory.py learn approve A ID --by NAME     put it in the playbook (the Reports tab does the same)
    python3 factory.py learn decline A ID --reason not_enough|against_rule|test_first|wrong_reading|other [--note N]
    python3 factory.py learn propose A --file F         the weekly task's proposals (runbook_note, advice, experiment, draft_rule)
    python3 factory.py learn playbook A [--json]        the playbook in force
    python3 factory.py learn revert A VERSION --by NAME the playbook as it was (the whole version)
    python3 factory.py learn backtest A ID              re-rank the last 7 scout boards with a factor proposal
    python3 factory.py learn experiments A [--sign ID | --stop ID]
    python3 factory.py learn features A [--json]        the per-post rows
    python3 factory.py learn weekly-save A --file F     keep the weekly report under state/A/learn/weekly/

## SETUP-POSTING.md and POSTING-APIS.md lines

None needed: the learner makes no platform call and needs no permission. One line for POSTING-APIS.md's "what
reads the numbers" list: "The learner (clipfactory/learn.py) reads the boards, state/stats/history and
state/stats/series only; it never calls a platform."

## The runbook line (added at go-live, in every file that states the choosing step)

    Before choosing, read: python3 ~/clip-factory/factory.py learn brief <acct>

Where it goes (both the OLD and the -auto copies of each scheduled task, then `factory.py hermes sync` and
`factory.py doctor`): gaming-scout SKILL.md "The run" step 2; politics-scout step 2; politics-bot
step 2; stunts-scout and roleplay-scout step 1; the scheduled prompts gaming-morning-sweep step 3,
gta-reactions-daily-auto step 4, politics-prep-auto step 4, bench-builder-auto steps 1, 3 and 7.

## Go-live, in order

1. Merge (outside 21:00 to 08:00, with Andrew's go-ahead), `factory.py timer install` (adds `com.clipfactory.learn`
   at 04:40), restart `com.clipfactory.review`.
2. By hand, once: `python3 ~/clip-factory/factory.py learn daily` and read both reports
   (`state/<slug>/learn/report-<yesterday>.md`, or Clip Review, Reports). No switch is needed for this.
3. `factory.py social on gaming learn` and `factory.py social on politics learn`: the 04:40 report, the
   Today band's chips, "1 proposal" in the 06:30 notice. Doctor: `ok  Gaming reports: made 04:40, ...`.
4. Create the Claude task `clip-weekly-report` from prompts/weekly-report.md (cron `0 17 * * 0`), with Andrew's OK.
5. Add the runbook line above (one truth: every file, then `hermes sync` and `doctor`).
6. When Andrew has approved a scout or coach proposal he wants used: `factory.py social on <acct> learn.playbook`.
   The scout reads it from its next hourly run; doctor says `--  playbook: on, version N`.
7. Experiments: `factory.py social on <acct> learn.experiments --practice` first; read the arms on the Experiments
   view for a few days; then `on`. Trial-reel experiments also need `trial_reels` on (the first-comment part):
   until then their clips are written down and not counted.
8. The coach builder reads the coach block through `playbook.coach_block(acct)` (empty while `learn.playbook` is
   off); reading `playbook.json` directly would ignore the switch.
