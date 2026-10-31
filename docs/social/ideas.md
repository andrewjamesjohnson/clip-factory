# Ideas: what wins in each niche

Code: `clipfactory/ideas.py` (the reads, the snapshots, the patterns, doctor, the command) and
`clipfactory/ideaspage.py` (Clip Review, Reports, Ideas at `/reports/ideas`). Tests: `tests/test_ideas.py`.

Each account keeps a short list of benchmark YouTube channels (at most 20, the `ideas.max_channels` setting). Once a
day at 14:20 the `ideas` timer lists each channel's Shorts tab through yt-dlp, metadata only (titles and view counts;
nothing is downloaded), and keeps the day's numbers as a snapshot. Views per hour are the change between two days'
reads of the same Short (the newest earlier read up to 4 days back), so the first day has none. From a week of snapshots, Ideas sets the title patterns of the winners (the
top quarter by views per hour) against the bottom half and against our own signed clips. The coach (`coach card`), the
learner and the weekly report read them through `ideas.patterns(acct)` and `ideas.winners(acct)`; the weekly task reads
`factory.py ideas patterns --json`.

What yt-dlp's flat Shorts listing really gives (yt-dlp 2026.8.19, checked in its source): a title and YouTube's
ROUNDED view label ("1.2M views" reads 1,200,000; "12K views" 12,000), and no length. So:

- Views per hour are close, not exact. A count only moves in steps of its label (100 under 10K, 1,000 under 1M,
  100,000 under 10M ...); views per hour are set only when a count moved by 3 steps or more. Less than that is
  unknown (left out of the winners and the patterns), never 0.
- Length: the brief asked for a length column. The flat listing has none, and reading each Short on its own would
  multiply the YouTube requests that bring the bot check (which also stops the scouts). So the length shows only when
  yt-dlp gives one, which today it does not: the Length column and "Middle length" stay hidden until then.

Nothing is chosen for Andrew. The list starts empty; the Ideas page suggests the scout's own YouTube channels
(config/scout.json `youtube_channels`) and he adds them one at a time or all at once ("Add the scout's channels").

## README section text (for the integrator to fold in)

**Your day** row:

| When | What | Agent |
|---|---|---|
| 14:20 | Ideas reads the benchmark channels' Shorts (accounts with Ideas on only) | `com.clipfactory.ideas` |

**The social layer, Ideas:**

> Reports, Ideas (`/reports/ideas`) shows, per account, the Shorts winning this week on the benchmark channels (title,
> channel, views per hour; YouTube rounds the counts, so these are close, not exact), what their titles do against the bottom half and against ours (a question, a
> number, a named person, a quote, capitals, "reacts", the length band), and the channel list. Add a channel by its
> YouTube link (@handle, /channel/UC..., /c/Name), remove one, or add the scout's channels with one click. It reads
> YouTube once a day at 14:20, only for accounts with Ideas on, never at night (20:30 to 06:15) and never at :13 to
> :17 or :38 to :52 (the collect and the hourly scouts): a run that reaches those minutes waits them out and goes on,
> for an hour at most, and the accounts take turns going first. A YouTube bot check or a 429 pauses it for the whole
> Mac until just before the next day's 14:20; a day the scouts already saw a bot check, it stands down without asking.

**Commands:**

```
python3 $F ideas status [account]                    # channels, last read, hold, the week's best
python3 $F ideas channels|suggest <account>          # the list; the scout's channels not on it
python3 $F ideas add <account> <link> --by NAME [--name N] [--why W]
python3 $F ideas remove <account> <link> --by NAME
python3 $F ideas seed <account> --by NAME            # add the scout's YouTube channels (up to 20)
python3 $F ideas winners <account> [--days 7] [-n 10] [--json]
python3 $F ideas patterns [account] [--json]         # what the coach, the learner and the weekly report read
python3 $F ideas run [account]                       # read now (daytime rules apply; shares the 14:20 run's lock)
python3 $F ideas release <account> --by NAME         # end a bot-check hold early, for that account
python3 $F social ideas --quiet                      # the timer's own run
```

## State (under state/<account>/ideas/, all local, all deletable)

| File | What |
|---|---|
| channels.json | `{"channels": [{url, name, why, added_by, added_at}]}` |
| snapshots/YYYY-MM-DD.json | `{at, updated, channels: [{url, ok, n, why, at, retry?}], shorts: [{id, channel, curl, title, views, dur, url, at, vph, approx, new}]}`; a re-read the same day replaces only the channels that answered; kept 35 days |
| patterns.json | `{window_days: 7, n, n_top, n_bottom, n_ours, features: [{feature, words, share_top, share_bottom, share_ours, n}], length, winners: [top 20 by vph]}` |
| hold.json | `{until, why, at, kind: bot or 429}` (23 hours: it ends before the next day's 14:20), or `{until: null, released: {by, at}}` |
| last.json | the last read `{at, day, channels, read, answered, unread, shorts, stopped, error}`, with beside it, when a day did not read, `{stood_down, where, down_day, down_at}` or `{skipped: {day, at, why}}` |

And one file for the whole Mac: `state/social/ideas-hold.json` (the same hold; it covers an account switched on after
the bot check too, until it ends or that account is released).

## POSTING-APIS lines

- Ideas (2026-10-20, clipfactory/ideas.py) is not a posting API and posts nothing. It reads public YouTube Shorts
  metadata with yt-dlp (`--flat-playlist --dump-single-json --playlist-end 30 <channel>/shorts`), one channel at a
  time with 5 to 15 seconds between, at most 20 channels per account, once a day at 14:20. Each call goes through
  `net.guard("ideas: yt-dlp")`, so a test, an offline run or a copy of the state never reaches YouTube. No key, no
  token, no YouTube Data API unit is used.
- A bot check ("Sign in to confirm you're not a bot") or a 429 holds Ideas for every account on this Mac, whichever
  account the run was for, until just before the next day's 14:20 (23 hours); `factory.py ideas release <account>
  --by NAME` ends it early for that account. A run by hand takes the 14:20 run's lock, so two listings never overlap. When the day's scout log or board already shows a
  bot check, Ideas stands down for the day: the scouts' reads matter more.

## SETUP-POSTING lines

- Ideas needs only yt-dlp (`brew install yt-dlp`, the same one the scouts use). Nothing to sign in to.

## Go-live steps (Ideas)

1. Merge (outside 21:00 to 08:00, never 06:15 to 08:00, with Andrew's go-ahead), restart `com.clipfactory.review`.
2. `python3 ~/clip-factory/factory.py timer install` (adds `com.clipfactory.ideas` at 14:20 with the other social
   agents), then `timer status`.
3. On Clip Review, Reports, Ideas, per account: "Add the scout's channels" (or add channels by link). Gaming's
   scout lists 6 YouTube channels; the Politics's lists 52, of which the first 20 fit. Stunts and Roleplay
   list none: add channels by link.
4. `python3 ~/clip-factory/factory.py social on <account> ideas` for each account that should read.
5. Next day after 14:20: `factory.py doctor` shows "Gaming ideas: N channels, read 14:20, N answered"; views per
   hour and the patterns appear after the second day's read.

## Doctor lines

- `--  ideas: off for Gaming (0 channels), ...; it waits for its switch (factory.py social on <account> ideas), after Reports, Ideas: Add the scout's channels`
- `--  Gaming ideas: on, no benchmark channels yet (Reports, Ideas: Add the scout's channels)`
- `--  Gaming ideas: 6 channels, not read yet (the first read is at 14:20)`
- `--  Gaming ideas: 6 channels, read 14:20, 6 answered` (`read Oct 19 14:20` when not today)
- `--  Gaming ideas: 6 channels, read Oct 19 14:20, 6 answered; not read today (did not read, not at :38 to :52 ...)`
- `--  Gaming ideas: 6 channels, not read today (stopped at 15:20, out of time ...)`
- `--  Gaming ideas: paused until Oct 21 13:22 (YouTube's bot check)`
- `--  ideas: stood down today (the scouts saw a YouTube bot check)`
- `--  Gaming ideas: 6 channels, last read Oct 19 14:20; stood down Oct 20 (the scouts saw a YouTube bot check)`
- `NO  Gaming ideas: 6 channels, last read Oct 17 14:20` (fix: `factory.py timer status`, then `ideas run`)
- `NO  Gaming ideas: none of the 6 channels read 14:20 answered (<yt-dlp's error>)` (fix: `brew upgrade yt-dlp`,
  then `factory.py ideas run <account>`)
- `NO  ideas: yt-dlp is not installed` (only when an account has Ideas on)

## Later

- The Reports page may show `ideas.section(cfg, acct)` (the same HTML as the Ideas page) on its Today view.
- Benchmarks on other platforms (TikTok, Reels) would need their own reader; not built.
