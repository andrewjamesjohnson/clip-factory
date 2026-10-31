# First comments, the Instagram poll and trial reels

Code: `clipfactory/firstcomment.py`, `clipfactory/reelextras.py`. Tests: `tests/test_firstcomment.py`,
`tests/test_reelextras.py`. The integrator folds the sections below into README.md, SETUP-POSTING.md and
POSTING-APIS.md.

## README section (text to paste)

### First comment under every post

Each clip can carry a first comment per platform. It sits in its own file under
`state/<account>/extras/<day>/<HHMM>.json` (never in the words files) and is signed with the clip: Approve signs the
clip, its words and its first comments together, and an edit after that stops the clip until it is pulled and
approved again.

- **Written at collect** from the clip's real source, only for a platform whose switch is not off:
  - Gaming, Facebook and YouTube: `Full video from MrBossFTW: https://www.youtube.com/watch?v=...&t=190s` (the
    link opens at the moment the clip starts).
  - Gaming, Instagram: `From MrBossFTW's stream on YouTube.` (Instagram shows no links in comments).
  - A reactions clip of several creators: `Reactions from iShowSpeed, Kai Cenat.`
  - Politics, Facebook and YouTube: `Secy. of State Rubio defends Trump suspending Russian sanctions, C-SPAN,
    October 19, 2026. Full briefing: https://www.youtube.com/watch?v=...` (title, publisher and date from the job's
    project.json; "Full hearing", "Full interview", "Full briefing" or "Full video" by the source's format).
  - Politics, Instagram: the same citation without the link.
  - No source known: no first comment. Never a hashtag, an ask to comment or tag, a long dash, or the caption's own
    question.
- **On the card** (Clips tab, the First comment fold): while the clip waits for Approve, a box per platform with Save
  and Clear. After Approve: the words and how they went (off, practice, waits for a permission, queued, posted with a
  link, skipped with why).
- **Posted** once the post is live, through the social layer's one door out (outward): Facebook and Instagram 90 to
  300 seconds after the post went live, YouTube 10 to 20 minutes after the Short is public. Only on posts the factory
  made itself. Never twice: the politics, a marker on the post's record, and a look for our own comment when an answer
  is lost. Not posted 20 hours after the post went live: skipped.
- **Instagram poll and trial reels** (switches `poll`, `trial_reels`): two to four poll options (25 characters each)
  on the card or from the nightly run; Instagram shows the reel's caption as the poll's question, so the card shows
  that caption beside the options. A trial reel only for an arm of an experiment signed on the Reports tab.

Commands:

    factory.py extras status gaming [--date 2026-10-20]
    factory.py extras comment gaming --date 2026-10-20 --slot 07:00 --platform facebook --text-file words.txt
    factory.py extras defaults gaming --date 2026-10-20 --slot 07:00 [--write]
    factory.py extras poll gaming --date 2026-10-20 --slot 07:00 --options "Yes|No"
    factory.py extras trial gaming --date 2026-10-20 --slot 07:00 --experiment e-0003 --arm B
    factory.py extras clear gaming --date 2026-10-20 --slot 07:00 --platform instagram   (or --what poll|trial|all)

Every change is refused once the clip is signed ("It is signed: Pull it to change its first comment.").

Switches, each off until switched on, per account:

    factory.py social on gaming first_comment.facebook_reels --practice
    factory.py social on gaming first_comment.instagram_reels --practice
    factory.py social on gaming first_comment.youtube_shorts --practice
    factory.py social on gaming poll --practice
    factory.py social on gaming trial_reels --practice

Practice runs everything and writes the exact words on the Log with no platform call. Going on needs the go-live
checklist (Log tab, Switches): 2 days and 10 practice rows (or 7 days) for first comments, 3 rows (or 14 days) for
the poll and trial reels, no practice row over a cap or a hold, "I read these" pressed, and the permission.

## SETUP-POSTING lines (permissions)

- **Facebook first comments** work with today's Meta token (`pages_manage_engagement` is already granted).
- **Instagram first comments** need `instagram_manage_comments`. Regenerate the system-user token with it ticked
  (`factory.py connect meta <account>`; connect lists the permissions to tick). Until then the Instagram line says
  "waits: needs instagram_manage_comments" and practice still works.
- **YouTube first comments** need the `youtube.force-ssl` scope: `factory.py connect youtube <account> --client
  client.json --with comments`. Google's consent screen then also grants the power to delete videos and comments;
  the factory has no code that deletes anything (a test holds that), but the token could. Only opt in if that is
  acceptable. YouTube comments wait until the Short is public (the factory uploads Shorts private with a publish
  time).
- **Polls and trial reels** need nothing new (`instagram_content_publish`). Verify on one Reel first: put a poll on
  one clip with `poll` on, check the poll and its question (the caption) in the Instagram app.

## POSTING-APIS lines

- First comment, Facebook: `POST /{video-id}/comments` with `message`, the Page token. When Facebook answers
  "Unsupported post request" for a reel's video id, the factory reads `GET /{video-id}?fields=post_id` once, writes
  that post id on the board marker (`alt`), then posts there; a send again goes straight to it. Lost answer: `GET
  /{video-id}/comments?fields=id,message,from` (an "Unsupported get request" there counts as none) and, when the
  marker has an `alt`, `GET /{alt}/comments`; our Page's own comment with the same words counts as sent.
- First comment, Instagram: `POST /{ig-media-id}/comments` with `message` (`instagram_manage_comments`). Lost answer:
  `GET /{ig-media-id}/comments?fields=id,text,username,from`, our account's comment with the same words.
- First comment, YouTube: `videos.list part=status` (1 unit), then `commentThreads.insert part=snippet` with
  `snippet.videoId` and `snippet.topLevelComment.snippet.textOriginal` (50 units, `youtube.force-ssl`) once
  `privacyStatus` is public. Not public yet: that try is skipped and the marker waits; the 5-minute tick queues it
  again 20 minutes later, then 40, so a slow Short costs at most four looks in its two hours (each counts once
  toward the first-comment caps), not one every 5 minutes. Not public 2 hours after its time: skipped for good.
  Lost answer: `commentThreads.list videoId order=time maxResults=50` (1 unit, the newest threads; no word search,
  which does not reliably match a link), our channel's comment with the same words.
- No platform has a pin endpoint: pin by hand in the app if wanted.
- Poll on a reel: the container's `poll_attachment` (`{"option_a":"Yes","option_b":"No"}`), only while `poll` is on
  and the clip was signed with it (`extras.ig_form` in `direct.ig_post`). Trial reel: `trial_params`
  (`{"graduation_strategy":"SS_PERFORMANCE"}`), only while `trial_reels` is on and the clip is an experiment's arm.
- The board marker: `slots[HH:MM].outlets[<platform>].social.first_comment = {state, action, id, url, text_sha, at,
  not_before, mode, why, signed_at, signed_hash, called, alt, look_at, looks}`. `called` is set before any call
  for the post: a post with a call made is never queued again by itself. A comment skipped because the clip was
  pulled is queued again by the tick once the clip is signed again (a new signature), with the words signed then.
  `.social.poll` and `.social.trial` say a reel's practice or live row was written.

## Doctor lines

    --  Gaming first comments on Facebook: off (try it: factory.py social on gaming first_comment.facebook_reels --practice)
    --  Gaming first comments on Instagram: practice (waits for instagram_manage_comments)
    --  Gaming first comments on YouTube: off (opt in: connect youtube --with comments; that also grants delete power, which the factory never uses)
    --  Gaming first comments on YouTube: off (try it: factory.py social on gaming first_comment.youtube_shorts --practice)   (force-ssl already granted)
    NO  Gaming first comments on Facebook: on, but it waits for its permissions to be read (factory.py social probe gaming scopes)
        fix: factory.py social probe gaming scopes
    ok  Gaming first comments on Facebook: on, 9 posted in 7 days
    NO  Gaming first comments on Instagram: on, but it waits for instagram_manage_comments
    --  Gaming Instagram polls: off; it waits for a practice week and one verified test Reel first (factory.py social on gaming poll --practice)
    --  Gaming Instagram polls: practice (the reels go without them; the Log notes what they would have carried)

## Notes for the integrator (seam): done 2026-10-20

- The seam pins that said "firstcomment is not built" (`test_social_registry` test 6, `test_social_cli` test 7) now
  compute what they expect from the parts present.
- `outward` puts an action that never left this Mac (`net.Offline`) or that the platform is not ready for
  (`direct.Pending`) back in the queue without a try, and takes its send off the caps again.
- The Clips page's data-x handler fills `{words}` in a button's `data-ask-t` from the form's `data-w` fields, so the
  First comment and poll forms no longer carry an inline `oninput`.

## Go-live steps for this part (only with Andrew's go-ahead, outside 21:00 to 08:00)

1. Merge after the seam (MERGE-ORDER), restart `com.clipfactory.review`. Nothing changes while every switch is off.
2. Facebook, per account: `factory.py social on <account> first_comment.facebook_reels --practice`. Watch two days
   of practice rows on the Log tab (the exact words, the time each would have gone). Press "I read these".
3. `factory.py social on <account> first_comment.facebook_reels` once the checklist is all ticked. Check the first
   live comment under the Reel on Facebook.
4. Instagram: after the token has `instagram_manage_comments` (step 2 and 3 again with `instagram_reels`).
5. YouTube: only if Andrew opts in to `--with comments` (the delete power). Then practice, then on.
6. Poll and trial reels: practice for a week; then one test Reel with `poll` on; look at it in the app before
   leaving it on.
7. `factory.py doctor`: every first-comment line says what it waits for.
