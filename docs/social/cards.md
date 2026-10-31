# Cards and stories from real footage

Part: `cards` (clipfactory/cards.py, cards_api.py, cardspage.py, render/card_engine.py). Built 2026-10-20 on branch
`social/cards`. Ships **off** for every account.

Daily stills and stories in each account's own look, with no AI anywhere. A card that is not about one clip (the
countdown, a carousel cover) carries the account's own look alone, never anyone's footage. Every other picture is a
clean frame of the footage a clip was cut from (the public record for the Politics, a GTA clip's own master), never our own delivered
clip, its cuts or its cover (they carry our burned-in captions and hook); with no clean frame on this Mac a card goes
on the account's own lockup and flat kit ink (no gradient backdrop); every word comes from
the transcript (verbatim), the slate, the signed copy or the cast Andrew verified. Each card is signed on its own
board on the Cards tab, never in the clip boards. Story reposts of signed clips run under one standing rule that
Andrew signs.

## README section (for the integrator to fold into README.md)

### Cards and stories (the Cards tab)

Each morning after 06:26 the studio timer draws the day's cards (when `cards.make` is on for the account):

| Account | Card | From | When |
| --- | --- | --- | --- |
| Politics | Quote card (story) | the slate's quote, found word for word in the clip's captions; the speaker only when the cast is verified by a person (one person, or the line inside that person's name plate), never inferred; a clean frame of the source at that second (else the cast check still) | 60 to 90 minutes after its clip is live; skipped when the clip did not post |
| Politics | The Docket (carousel) | yesterday's posted clips, 3 or more, titles from their signed copy; each on a clean frame of its source (the master at 40% into the clip's span, else the cast check still; else no picture, never the cover) | 12:00 |
| Politics | Week's best (carousel) | Mondays: last week's five most watched | 19:00 |
| Gaming | GTA 6 countdown (story) | N DAYS in the account's own look alone: its lockup on the kit's flat ink, never anyone's footage (the countdown is not about one clip; a creator's frame once brought a fan map's own "32 DAYS TO GO" over our "30"); OUT TODAY on 2026-11-19, none after | 09:00 |
| Gaming | Clip of the day (story) | yesterday's most watched clip: a clean frame of its source at the clip's moment (the job's master, across the span the slate or the board's pick_span names: six small grey probes, and the one with the least flat UI (a browser, a web page, a map's fills) wins when it beats the 40% frame clearly; else 40%), its title, "From <creator>"; with no master on this Mac the lockup, the title and the credit on the kit's flat ink. Never our delivered clip, a hard link of it, its cover or anything in its drafts, delivery or rendered-clips folders | 20:30 |
| Gaming | Week's best (carousel) | Mondays: last week's five, on the kit's flat ink | 19:00 |

The Cards tab shows each card as it will post (a story at 9:16, a carousel as a strip you swipe), its time, where it
goes and where its picture and words come from ("speaker verified by Andrew 10-19"). **Approve** signs exactly what
the page shows (the card's hash covers its id, kind, time, targets, every file's SHA-256 and its words); Make again or
a new time voids the signature. Decline keeps its reasons. A card nobody signs expires 2 hours past its time.
A card already queued can still be declined or given a new time until it is on its way (the queued try is then
refused when it comes up). A card that was skipped, failed or expired comes back with Change time and a new Approve.
Under each account, "Not made this day" lists why a card was not made (no verified speaker, fewer than 3 clips, no
clean frame). When a Reel's 30 minutes push a card past its time, the card and its Approve say "Goes about 09:15 (the
08:45 Reel keeps 30 minutes clear)".

**Two card posts a day on each platform** (outward's `card_post` cap: 2 a day, 1 an hour, 3 hours apart). Approve
refuses a third card for the same platform on the same day and names the two that have it; Approve all signs the
earliest first and leaves the rest unsigned, and its confirm says so. A signed card that meets a full 24 hours waits
in the queue rather than being dropped; one that still cannot go by its expiry is shown as Skipped (grey), never as a
red failure. A queued card may wait up to 3 hours past its time plus one card spacing (3 h 20 min) and 30 minutes, so
a try that failed on a Meta error is tried again (outward's 3 tries) before the card is written off.

A signed card goes when its time comes (`cards.post`: practice logs what would go and sends nothing; on sends), one at
a time, 08:00 to 23:30, never within 30 minutes of a Reel on that platform:

- Facebook story: the JPEG as an unpublished photo, then a photo story (no public URL needed).
- Facebook post (a carousel): each slide as an unpublished photo, then one post with them all.
- Instagram story and Instagram carousel: Instagram takes images only from a public URL, which the factory does not
  have, so each card also gets a 6 second MP4 of the same picture (silent AAC track, yuv420p) and goes up through
  the same resumable upload the Reels use. Before any Instagram card the account's publishing limit is read: a card
  waits while the publishes used plus the Reels still due today reach min(25, quota_total / 2).

**Story reposts** (`cards.story_repost`, and the rule signed on the Cards tab): when a Reel is live on Facebook or
Instagram, the very bytes that platform was sent (its own cut, or the clip) go to that platform's story 30 to 60
minutes later, 60 seconds at the most, 4 a day at the most. A Politics clip over 60 seconds goes only with its signed
Instagram cut. The rule says which platforms, how many a day, how long after, and every signed clip or only the
coach's top ones; changing it makes a new version that must be signed again; Pause stops it.

Files: `state/<slug>/cards/<date>.json` (the cards board and the day's story reposts), `state/<slug>/cards/<date>/<id>/`
(the grabbed still, and one `r<N>/` folder per drawing: 1.jpg, 1.mp4, spec.json; Make again draws into a new `r<N>`
and swaps it in under the board's lock, so a signed or queued card's files are never drawn over), `state/<slug>/cards/rules.json` (the story rule), `state/<slug>/cards/limit.json`
(Instagram's publishing limit, kept 30 minutes). Nothing is ever written in a delivery folder or a Clip Drafts slot
folder (collect would take a JPEG there for the clip's cover).

Commands:

```
factory.py cards status [account] [--date D]     the day's cards, their state, the story rule
factory.py cards make <account> [--kind K] [--again]   draw now (06:26 to 20:45 only); --again voids signatures
factory.py cards probe <account>                 test Instagram stories (one unpublished container, network)
factory.py cards selftest                        can the engine Python draw a card here
factory.py cards tick                            the studio step, now
factory.py social on <account> cards.make        draw the cards each morning
factory.py social on <account> cards.post --practice   post signed cards in practice (logs only)
factory.py social on <account> cards.story_repost --practice
factory.py social set <account> cards.targets '["facebook_story","instagram_story","facebook_feed","instagram_carousel"]'
factory.py social set <account> cards.times '{"countdown":"09:00","clip_of_day":"20:30","docket":"12:00","quote_after_min":[60,90],"week_best":"Mon 19:00"}'
```

## The studio agent row (POSTING-APIS.md timers table)

| agent | when | what |
| --- | --- | --- |
| com.clipfactory.studio | every 600 s | `factory.py social studio --quiet`: draws the day's cards once after 06:26 (ffmpeg and PIL only 06:26 to 20:45), expires unsigned cards, queues signed cards and missed story reposts, sends one card or story (08:00 to 23:30); then the coach's numbers. The 06:30 notice draws the day's cards itself when the tick has not yet (one lock, never twice), so "N cards to sign" is in it |

## SETUP-POSTING note (Instagram and Facebook section)

**Stories and cards.** Facebook Page stories need `pages_manage_posts` (already there for Reels). Instagram stories by
API may be refused for a Creator account ("(#10) The user is not an Instagram Business"). Before switching
`instagram_story` on, run `factory.py cards probe <account>`: it makes one unpublished STORIES container (never
published; Instagram drops it after 24 hours) and keeps the answer; the doctor and the Cards tab show it. A refusal is
Andrew's call: switch that Instagram account to Business (it changes the music the app offers), or keep cards on
Facebook only.

## POSTING-APIS lines (Meta section)

- Cards: Facebook `POST /{page}/photos published=false` (multipart `source`) then `POST /{page}/photo_stories
  photo_id`; carousels `POST /{page}/feed attached_media[n]={"media_fbid": id}`; story reposts `POST
  /{page}/video_stories` start, rupload, finish (60 seconds at the most). Instagram `POST /{ig}/media
  media_type=STORIES upload_type=resumable`, rupload, status once a minute (5 at most), `media_publish`; carousels from
  video children (`is_carousel_item=true`) then the `CAROUSEL` parent. `GET /{ig}/content_publishing_limit` before
  each Instagram card (kept 30 minutes), read only after outward's own check says the card may go now (never past
  Stop everything, a hold or the poster's Meta hold). Every upload sends the bytes it read only when their SHA-256 is
  the one signed on the card (each carousel child too). A lost answer is looked up with `GET /{page}/stories`, `/{page}/feed`,
  `/{ig}/stories` or `/{ig}/media` by time, never sent again by itself. Through outward (kinds `card_post`, `story`;
  keys `card:<platform>:<date>/<id>:<target>`, `story:<platform>:<date> <slot>`; runner `studio`).

## The engine

`render/card_engine.py` is the only place Pillow is used. The factory writes `spec.json` and runs
`[CLIP_ENGINE_PYTHON or makers.PY_SKILLS, "-B", render/card_engine.py, spec.json]` with a 60 second timeout, exactly as
the skills' scripts run (never a bare `python3`: under launchd that is Homebrew's, with no Pillow). It prints one JSON
line. Politics draws with the viral-clip-engine's own `politicslook` (configured from `presets/hearing.json`, so
the brass `#C9A24A` follows the skill; brass only on figures). GTA reads `assets/brand/<kit>/kit.json` directly
(Impact, DIN Condensed, an ink outline grown with MaxFilter, the teal as a hard offset shadow; never `qm_config`).
An account whose kit folder is missing (Stunts' `stunts`, Roleplay's `roleplay`) gets no card at all, never
Gaming's brand; the kit's `card-backdrop.png` (a gradient) is not used: frames go under a flat ink dim, and with
no frame the ground is the kit's flat ink.
Emoji are stripped. Every card is centred on the canvas (x 540), never on a Reel's safe zone (x 65 to 916: a story or
a feed post has no action rail), with type inside symmetric margins (x 72 to 1008). A story keeps its top 250 and
bottom 340 clear (the platforms' own bars) and its content is balanced as one block between (y 250 to 1580); a feed
slide has even margins. The Politics keeps its flush-left grid inside those margins (the quote card: masthead, the line
set as large as it goes across the measure, then the whole 16:9 frame; a cover centres the lockup); GTA centres the
one-line lockup. JPEG quality 92, no chroma subsampling; each file is
written `.part` then moved into place. `--selftest [--skill <viral-clip-engine>]` checks Pillow, fontTools, numpy,
the fonts and the Politics look.

## Doctor

With every cards switch off: one `--` line per account (what to switch on, and the stories probe). Once a switch is
on: NO when the engine Python cannot import PIL and fontTools (fix: `/usr/bin/python3 -m pip install --user pillow
fonttools`), NO when Helvetica Neue, Impact or DIN Condensed is missing, the Instagram stories test (`--` not tested,
ok, NO for a Creator refusal with the Business choice as Andrew's call), `--` while the story rule waits for its
signature, NO for a signed card or story that did not go, NO for a GTA account with `cards.make` on and no brand kit
of its own in the skill.

## Go-live steps (cards)

1. Merge (outside 21:00 to 08:00 and never 06:15 to 08:00, with Andrew's go-ahead), restart com.clipfactory.review.
   The studio agent comes with the seam (`factory.py timer install` once).
2. `factory.py cards selftest` (ok), then `factory.py doctor`.
3. `factory.py cards probe gaming` and `factory.py cards probe politics`. A Creator refusal: Andrew decides
   Business, or keeps `cards.targets` to Facebook.
4. `factory.py social on <account> cards.make`: look at the next morning's cards on the Cards tab.
5. `factory.py social on <account> cards.post --practice` for 2 days and 4 practice rows (or 7 days); press "I read
   these" on the Log tab; then `factory.py social on <account> cards.post` (the go-live checklist checks it).
6. Sign the story repost rule on the Cards tab; `cards.story_repost --practice`, then on, the same way.
7. Add `instagram_story` / `facebook_feed` / `instagram_carousel` to `cards.targets` when wanted.

## For the integrator

- **Default card times sit inside the Reels' 30 minute gaps** (`switches.SETTINGS["cards.times"]`, a seam file): the
  countdown at 09:00 waits for the 08:45 Reel and goes about 09:15, the docket at 12:00 about 12:45, week's best at
  19:00 about 19:45, clip of the day at 20:30 about 21:30, each plus up to 20 minutes of card_post jitter. The page now
  shows the real time, but defaults in the gaps would be cleaner: proposed `{"countdown": "09:45", "docket": "13:15",
  "week_best": "Mon 18:30", "clip_of_day": "20:00"}`. Clip of the day at 20:30 also sits about 24 hours after the day
  before's, so the 2-a-day cap can make it wait; 20:00 helps there too.
- Done 2026-10-20: the two seam tests that pinned "cards not built" were re-pinned (the tab strip from
  `REG.tab_pages()`, "not built yet" proved with cards hidden). A card's video bytes now go through `graph.rupload`
  (the Page token in the Authorization header only, as a Reel's bytes go), not `gpost` with the token in the address.
  A try with no clear answer (a 5xx, a transient error) is looked up and never sent again blind (the seam's rule).
- The ARCHITECTURE line "The 06:30 notice says ... 2 cards to sign" now holds: `cards.morning_line` draws the day's
  cards when the tick has not yet (under `store.try_lock("cards-make-<slug>")`, which the tick takes too; it waits up
  to 4 minutes for a make that is running).
