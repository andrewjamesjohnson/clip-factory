# Checkpoint stats (clipfactory/checkpoints.py, checkpoints_api.py)

Each posted clip is read again at 1h, 3h, 6h, 24h, 72h, 7d and 30d after it went public, and each post keeps a short
series: how it grew. Clip Review then says early that a clip is running hot or starting slow, the Stats tab draws how
each post grew against the account's usual, and the learner has the same points for every post.

Off for every account until `factory.py social on <account> checkpoints`. Off means off: no platform call, no file,
no change on any page.

The notes below are for the integrator to fold into README.md, POSTING-APIS.md and SETUP-POSTING.md.

## README.md

**"Your day" row** (after the 03:30 stats row):

| when | what happens | who |
| --- | --- | --- |
| every 15 minutes | each posted clip's numbers again at 1h, 3h, 6h, 24h, 72h, 7d and 30d after it went public; the Today band says which clip is running hot or starting slow (off until switched on per account) | launchd `com.clipfactory.checkpoints` |

**Section text** (under "## The social layer"):

### Checkpoint stats

The nightly read keeps only each post's latest numbers. The checkpoints keep how it grew: every 15 minutes
(`com.clipfactory.checkpoints`, `factory.py social checkpoints --quiet`) each post whose next checkpoint is due is
read again by its id, at 1h, 3h, 6h, 24h, 72h, 7d and 30d after it went public. A YouTube upload counts from when it
went public, never from its upload. Each post waits a few minutes past its time, the same every time for that post,
so ten posts of one slot are not asked about in the same minute. When the Mac slept through several checkpoints there
is one read, stored under the latest with its true age; the others are written as missed, never made up. A read
that came late like that is kept as late: it is shown with its age and is never compared with the usual or counted
in it. A post that will not read (Meta refuses its numbers, or a YouTube video is still private) is tried 4 times per
checkpoint, then that checkpoint is written as missed.

- **On a posted card:** "3h: 1.2K, 2.4x your usual on Facebook". The usual is the median of the account's last 30
  posts on that platform at the same checkpoint, once 8 posts have one.
- **The Today band:** "Running hot: <title> 2.4x at 3h" (twice the usual or more at 1h, 3h or 6h), and the slowest
  start of the day (half the usual or less). One chip per clip.
- **The Stats tab:** a Top clips row opens "How it grew": the post's line against the account's usual, the extra
  numbers, and the retention line. Top clips can rank by Avg watch, Skip rate, Reach and Saves once posts have them.
- **The board's numbers** move between the nightly reads too (only when newer than what the board has). The nightly
  read's own files (pulled, history, read.lock) are never touched.
- **Extra numbers** (`factory.py social on <account> checkpoints.extra`) at 24h, 7d and 30d, in calls of their own:
  Instagram reach, saves, interactions, average watch, watch time, skip rate; Facebook plays with replays, replays,
  reach, average watch, watch time, follows, and its retention graph at 7d. A number a platform will not give is
  asked alone once and then left alone for 30 days. A number a platform did not send is left out, never written as 0.
- **YouTube Analytics** (`factory.py social on <account> checkpoints.yt_analytics`, and the yt-analytics.readonly
  permission) at 72h, 7d and 30d: engaged views (the count YouTube pays on), average view, average percentage viewed,
  shares, subscribers gained; the retention line at 7d. Its numbers lag a day or two, so the early checkpoints use the
  Data API's view count.
- **Backfill:** `factory.py checkpoints backfill` gives older posts their points from the nightly read's snapshots
  (state/stats/history), marked as such. Run it once after switching checkpoints on.
- **YouTube's 30-day rule (III.E.4):** a YouTube number read 30 days ago or more is deleted here (the views, likes,
  comments and Analytics numbers of that checkpoint; the checkpoint's name and age stay, so it is not read again). A
  checkpoint is how a post stood at that hour, so it cannot be refreshed, only deleted. This is a files-only step of
  the 15-minute run and of the backfill, and it runs for every account, with checkpoints on or off. While the switch
  is on, each YouTube video's current numbers are also asked for again every 29 days (50 videos a unit), and a video
  YouTube no longer has loses all its numbers here. Doctor flags any YouTube numbers older than 30 days (the timer
  is not running). So the Stats tab's How it grew shows a YouTube post's line for about a month.

Files: `state/stats/series/<account>__<outlet>__<YYYY-MM>.json` (one per account, platform and month the post went
public, keyed by the platform's id), `state/stats/series/status.json` (last run, reads and missed per day, last
errors), `state/stats/series/unsupported.json` (numbers a platform will not give). One run at a time
(`state/stats/series/.lock`).

**Commands:**

    python3 factory.py social on <account> checkpoints          switch it on (checkpoints.extra, checkpoints.yt_analytics too)
    python3 factory.py checkpoints run                          read what is due now (the timer does this)
    python3 factory.py checkpoints backfill                     older posts' points from the nightly snapshots
    python3 factory.py checkpoints show <account> <id>          one post's series (the YouTube video id, the Reel id)
    python3 factory.py checkpoints status [account]             switches, posts tracked, last read, missed, hot and slow

## POSTING-APIS.md

**Timers row:**

| com.clipfactory.checkpoints | every 15 minutes | each post's numbers again at 1h, 3h, 6h, 24h, 72h, 7d and 30d (`factory.py social checkpoints --quiet`; off until switched on per account) |

**The calls added** (all through graph.py, so direct._http and net.guard; never in a page or a test):

| platform | call | when | cost |
| --- | --- | --- | --- |
| YouTube | `GET youtube/v3/videos?part=statistics,status&id=<up to 50>` | each checkpoint, and the 30-day refresh | 1 unit a call, counted in this machine's social units (`state/social/youtube-units.json`); 100 units kept for the signed first comments |
| YouTube Analytics | `GET youtubeanalytics.googleapis.com/v2/reports?ids=channel==MINE&filters=video==<id>&metrics=engagedViews,averageViewDuration,averageViewPercentage,shares,subscribersGained` | 72h, 7d, 30d (switch and permission) | its own quota |
| YouTube Analytics | same, `dimensions=elapsedVideoTimeRatio&metrics=audienceWatchRatio` | 7d | its own quota |
| Instagram | `GET /{media}/insights?metric=views,likes,comments,shares` | each checkpoint | one call a post |
| Instagram | `GET /{media}/insights?metric=reach,saved,total_interactions,ig_reels_avg_watch_time,ig_reels_video_view_total_time,reels_skip_rate` | 24h, 7d, 30d (checkpoints.extra) | one more call a post |
| Facebook | `GET /{video}/video_insights?metric=blue_reels_play_count,post_video_likes_by_reaction_type,post_video_social_actions&period=lifetime` | each checkpoint | one call a post |
| Facebook | `GET /{video}/video_insights?metric=fb_reels_total_plays,fb_reels_replay_count,post_impressions_unique,post_video_avg_time_watched,post_video_view_time,post_video_followers&period=lifetime` | 24h, 7d, 30d (checkpoints.extra) | one more call a post |
| Facebook | `GET /{video}/video_insights?metric=post_video_retention_graph&period=lifetime` | 7d (checkpoints.extra) | one more call a post |

**Load:** 10 posts a day on each platform is about 70 base reads a day per platform and account. With
checkpoints.extra on, add 30 a day on Instagram and 40 on Facebook (the 7d retention graph), and more on a day a set
is refused and its numbers are asked one at a time (up to 6 more calls a post, until each refused number is
remembered). At most 40 Meta reads a platform a tick (20 when Meta's usage header says 75% or more).
Before each Meta call the guard's holds and the poster's own Meta hold are asked; after it, Meta's usage headers go to
the guard (outward.note_usage). A block, a limit or a broken sign-in ends that platform's reads for the tick (and,
with the account's guard switch on, writes the guard's hold). A permission refusal or a timeout on one post's
numbers (Meta says "not enough viewers" for some new posts) is a failed try on that post only; two posts refused so in
one tick end that platform's reads for the tick. Two slots holding one post id (the nightly read matched a sibling's
post) are read once, under the slot that posted it. TikTok is never re-read (no by-id read on the
production app; an 'Only me' test is nobody's post).

**Permissions:** none new for Meta (read_insights and instagram_manage_insights are already on the token). YouTube
Analytics needs yt-analytics.readonly (below).

**Open items:** `reels_skip_rate` is marked "in development" by Meta; if Instagram refuses it, it is remembered and
asked again after 30 days. YouTube's reports page also says requests need youtube.readonly; the sign-in keeps it.

## SETUP-POSTING.md

**YouTube Analytics (optional, for the checkpoints):**
1. In the Google Cloud console of the politics project: APIs and services, Library, "YouTube Analytics API",
   Enable.
2. `python3 factory.py connect youtube <account> --client <client.json> --with analytics` (a new consent screen; it
   adds yt-analytics.readonly and keeps the upload and read permissions, and asks again for youtube.force-ssl if
   the channel has `--with comments`: leave it ticked, or connect names it as lost).
3. `python3 factory.py social probe <account> yt-analytics` (by hand: one call), then
   `python3 factory.py social on <account> checkpoints.yt_analytics`.

## Go-live (this part)

1. After the merge and `factory.py timer install` (it adds `com.clipfactory.checkpoints`), doctor shows per account
   `--  <Account> checkpoints: off (factory.py social on <account> checkpoints); checkpoints.extra and
   checkpoints.yt_analytics wait for it, ...`.
2. Day 0, per account: `factory.py social on <account> checkpoints`, then `factory.py checkpoints backfill`.
3. Within 15 minutes doctor shows `ok  <Account> checkpoints: N posts tracked, last read M min ago, 0 missed today`.
   `factory.py checkpoints status` lists what was read, what was missed, and the hot and slow posts.
4. A day later: `factory.py social on <account> checkpoints.extra`. If doctor names numbers "not given here", nothing
   is wrong; the platform does not give them for these posts.
5. YouTube Analytics only after the SETUP steps above: `factory.py social on <account> checkpoints.yt_analytics`.
   Until the permission is there doctor says `--  <Account> YouTube Analytics: needs yt-analytics.readonly ...`.
6. To stop: `factory.py social off <account> checkpoints` (no more platform reads; the files stay for the learner).
   Keep the timer installed: the 15-minute run still deletes YouTube numbers once they are 30 days old (files only,
   no call). To delete every stored YouTube number at once instead: remove
   `state/stats/series/<account>__youtube_shorts__*.json`.
