# The Inbox (Clip Review's Inbox tab): for the integrator to fold in

Files: `clipfactory/engage.py` (the poll, the executor, the doctor, the command), `clipfactory/engage_api.py` (the
only Inbox file that reaches a platform, always through `graph.py`), `clipfactory/classify.py` (tags and
suggestions, words only), `clipfactory/engagepage.py` (the tab). State: `state/<account>/engage/` (socialstore).
The module, its state and its command say "engage" because "inbox" already means the Desktop Clip Drop folder; only
the tab's label says Inbox.

## README: a section "## The Inbox (comments and messages)"

Clip Review's Inbox tab (http://127.0.0.1:8781/inbox) shows the comments on the accounts' Instagram, Facebook and
YouTube posts, and their Instagram and Facebook messages once Meta allows them, in one list:

- **Comments**: filters Unanswered, Questions, Asks for the source, Suggested hide, Done, All, and the platforms.
  Each comment shows who wrote it (initials, never a picture), when, its words, what it asks (the source, the full
  video, who, when, which, part 2), and the post it is on with its cover, a link to the post and a link to the clip on
  the Clips page.
- **Messages**: the conversations, with how long you can still answer (Meta allows 24 hours after their last
  message; the Inbox stops at 23).
- **Saved replies**: answers you give often, per account and platform; they show as chips under every reply box.
- **Automations** (once built): the rules page sits beside these views.

What you can do on a comment: Reply, Reply privately (Meta allows one private message per comment, within 7 days;
the Inbox stops at 6, and sends one private reply per person a week; a practised one never counts), Hide or Unhide (YouTube: Hold for review or Publish again), Mark done, Stop automations to
this person, This is me (tester), Forget this person, and Suggest (an answer written from the clip's facts on disk,
once the automations part is built). Every one of these is your click on the page, with a confirm that quotes the
exact words and where they go. A reply is queued through the social layer's one door out (outward: caps, spacing,
holds, Stop everything), and the sender is started at once, so it goes in seconds. The page shows Sending, then
Sent, Practice or why not. The same words twice in a day are refused with Send anyway.

The Inbox never hides anything by itself. It suggests hiding only spam (a link or "check my page" from someone new,
giveaway and money-scheme words, the same words three times), slurs and threats. WhatsApp, Telegram, Cash App, forex
and the like count only beside a way to reach the writer (a handle, a number, "dm me", a link): on the Politics they are
often the news itself. Disagreement and rudeness are
never suggested: on the Politics, people arguing about politics is the conversation.

There are no webhooks (Clip Review is loopback only), so the social tick reads every 5 minutes for each account
whose `engage.read` switch is on:

- Instagram's posts of the last 7 days and the Page's reels with their comment counts, then comments only where the
  count changed (read first, at the next read) or the decay is due (under 48 hours every read, or every 30 minutes when
  the count did not move; 2 to 7 days hourly; 7 to 30 days every 6 hours; the most overdue first, 25 posts a read);
- a post that will not read (deleted by hand, taken down) waits on its own, from 1 or 6 hours up to 3 days, and a
  deleted one is dropped with its comments; it never stops the other posts nor turns the Clips page red;
- YouTube's comment threads every 15 minutes (1 unit a page, only while this machine has more than 100 units left);
- messages (`engage.messages` on) every 15 minutes on Instagram and every 30 on Facebook, never faster than 2 calls a
  second, and an unchanged conversation is not read again; three failed reads in an hour rest the message reads for 6
  hours without pausing your own messages;
- at 75% of Meta's limit every interval is four times longer; a reading hold or the poster's Meta hold skips it.

A comment deleted on Instagram or Facebook becomes "Deleted" at the next read of its post. On YouTube it does only when
a read reaches the channel's last thread (a small channel); on a busy channel a deleted YouTube comment stays in the
list until it is marked done or the 45 days pass.

A message or comment that starts with STOP (or unsubscribe, leave me alone, don't message me) stops every automated
message to that person (the STOP is kept for good, its reason never holds their words, and their name goes after
90 days). The Inbox keeps 45 days (`engage.keep_days` may lower it, and a poll never stores a comment older than
that); Forget removes a person at once, and the poll never reads them back in (only a sha256 of their key is kept).

Switches (Log, Switches, or the command): `engage.read` on, `engage.messages` on (after Meta's review),
`replies` off, practice or on (practice logs the exact words and sends nothing).

Commands:

    factory.py social on <account> engage.read            start reading comments
    factory.py social on <account> replies --practice     replies from the Inbox, written to the Log only
    factory.py social on <account> replies                replies go for real
    factory.py social set <account> engage.platforms '["facebook","youtube"]'
    factory.py engage status [account]                    what each account reads, when, what waits
    factory.py engage poll <account> [--force]            read now, by hand
    factory.py engage check                               one read of every account that reads, now
    factory.py engage list <account> [--state new] [--platform instagram] [--limit 20]
    factory.py engage posts <account>                     rebuild which post is which clip
    factory.py engage prune [account]
    factory.py engage forget <account> --person instagram:1784... --by NAME

Your day row: "every 5 minutes (the social timer): the Inbox reads new comments; replies you click go in seconds".

## POSTING-APIS.md lines

**Reads** (the social tick; never in a page):

| What | Call | Permission |
|---|---|---|
| Instagram posts and counts | `GET /{ig-user}/media?fields=id,comments_count,timestamp,permalink&since=<7 days>` | instagram_basic |
| Instagram comments | `GET /{media}/comments?fields=id,text,timestamp,username,from{id,username},like_count,hidden,replies{...}` | instagram_manage_comments, pages_read_engagement (without it Instagram still answers, with each comment's id, timestamp, hidden and like_count but no words or username: the read stores nothing and marks the permission missing) |
| Facebook reels and counts | `GET /{page}/video_reels?fields=id,created_time,comments.summary(true).limit(0)` | pages_read_engagement |
| Facebook comments | `GET /{video}/comments?filter=stream&order=reverse_chronological&fields=id,from{id,name},message,created_time,parent{id},is_hidden,can_reply_privately,like_count` | pages_read_user_content, the MODERATE task (without it a reel with no comments still reads; one with comments is refused, (#200) Missing Permissions) |
| YouTube comments | `commentThreads.list part=snippet,replies allThreadsRelatedToChannelId order=time` (1 unit) | youtube.force-ssl (a youtube.readonly token is refused: HTTP 403 "Request had insufficient authentication scopes", 2026-10-20) |
| Messages | `GET /{page}/conversations?platform=instagram\|messenger`, then `GET /{conversation}?fields=messages.limit(20){...}` | instagram_manage_messages; pages_messaging, pages_manage_metadata (Advanced Access) |

**Writes** (only a click on the Inbox, through outward; YouTube never automated):

| What | Call | Permission |
|---|---|---|
| Reply on Instagram | `POST /{comment}/replies message=` | instagram_manage_comments |
| Reply on Facebook | `POST /{comment}/comments message=` | pages_manage_engagement |
| Reply on YouTube | `comments.insert` (50 units) | youtube.force-ssl |
| Private reply | `POST /{page}/messages recipient={"comment_id"} message={"text"}` | instagram_manage_messages or pages_messaging (Advanced Access for the public) |
| Message | `POST /{page}/messages recipient={"id"} messaging_type=RESPONSE message={"text"}` | the same |
| Hide | Instagram `POST /{comment} hide=true\|false`; Facebook `POST /{comment} is_hidden=true\|false` | instagram_manage_comments; pages_manage_engagement |
| YouTube hold | `comments.setModerationStatus` heldForReview or published only, never banAuthor (50 units) | youtube.force-ssl |

A lost answer is looked up before anything is sent again: a reply by our reply under the comment with the same words,
a private reply or message in the conversation's last 20 messages, a hide by the comment's hidden state. A private
reply or a message that cannot be found is never resent by itself ("Not sure it went: look on Instagram, then press
Send again").

Open items: DMs and private replies to people without a role on the Meta app need Business Verification and App
Review (Advanced Access); until then they are practice, or reach app testers only. Reading YouTube comments, and
YouTube replies and holds, need the youtube.force-ssl opt-in.

## SETUP-POSTING.md lines

**Comments in the Inbox (Instagram and Facebook).** On the system user's token (Business Settings, Users, System
users, Generate new token), tick as well:

- `instagram_manage_comments` (Instagram comments: read, reply, hide)
- `pages_read_user_content` (Facebook comments: read them with who wrote them)

On the Page asset of the system user, give the **Community activity (MODERATE)** task as well: Facebook gives comment
ids only to a token with it. Then, in your own Terminal: `factory.py connect meta <account>` (paste the new token;
it is hidden), and `factory.py social probe <account> ig-comments` and `fb-comments` to see that they read. Each
probe reads a post that has comments and passes only when they come back with their words and names; with no
comment anywhere yet it says "not proven", and a refusal names what the token lacks ("missing
instagram_manage_comments: ...").

**Messages (later, after Meta's review).** Business Verification of each portfolio, then App Review for
`instagram_manage_messages`, `pages_messaging` and `pages_manage_metadata`. On the system user's Page asset add the
**Messages (MESSAGING)** task. In the Instagram app: Settings, Messages and story replies, Message controls,
Connected tools, turn on **Allow access to messages**. Then `factory.py connect meta <account>`,
`factory.py social probe <account> messages`, and `factory.py social set <account> messages_access advanced`. Until
then, `engage.messages` can stay off; answers to messages reach only people with a role on the Meta app (This is me
(tester) on the Inbox marks them).

**YouTube comments and replies (optional).** `factory.py connect youtube <account> --client client.json --with
comments` adds youtube.force-ssl (connect asks again for an extra the channel already has, so analytics is kept). It
also grants delete power over videos; the factory has no delete code and a test forbids it. Reading YouTube comments
needs it too: today's youtube.readonly is refused by commentThreads.list (HTTP 403 "Request had insufficient
authentication scopes", both channels, 2026-10-20), so without the opt-in the Inbox leaves YouTube out.

## Go-live steps for the Inbox

1. After the merge and the review restart, open http://127.0.0.1:8781/inbox at 280, 375, 768, 1024 and 1700 px: the
   tab is there, every account says "reading comments is off". The Clips and Stats tab bars gain the Inbox tab and
   nothing else on them changes.
2. `factory.py doctor`: the social section shows `--  <Account> inbox: off (factory.py social on <slug> engage.read);
   engage.messages and replies wait for it, ...`.
3. Day 1 (GO-LIVE switch-on sequence), after the comments permissions and `connect youtube --with comments`
   (above): `factory.py social set <account> engage.platforms '["facebook","youtube"]'`, `factory.py social probe
   <account> fb-comments` and `yt-comments` (each must say "readable", not "missing ..." or "not proven"), then
   `factory.py social on <account> engage.read`. Within 10 minutes the tab fills; `factory.py engage status` says
   when it read. With today's tokens (2026-10-20) all three comments probes say missing (instagram_manage_comments,
   pages_read_user_content, youtube.force-ssl): leave `engage.read` off until they pass.
4. `factory.py social on <account> replies --practice`: answer a few from the tab; the Log shows the exact words and
   nothing goes out. Then `factory.py social on <account> replies`.
5. After the comments permission (SETUP-POSTING above): add instagram to `engage.platforms`; the doctor's NO line for
   Instagram turns into an ok line.
6. Messages: only after Meta's review, `factory.py social on <account> engage.messages`. Before that, check once by
   hand (Graph API Explorer, the system user's token) whether `GET /{page}/conversations?platform=instagram&fields=
   id,updated_time,is_owner` is accepted; if it is, add `is_owner` to `engage_api.conversations` (the read already
   leaves a thread alone when the answer says `is_owner: false`).

Integration (done 2026-10-20): the three seam tests that pinned the Inbox as not built were re-pinned (the tab
strip from `REG.tab_pages()`, the doctor's broken-part test evicts the loaded `clipfactory.engage` first), and
socialstore gained `unhide` and `set_local`, so the unhide and the automations' notes no longer write the month
files by hand.

Doctor lines (each says what it waits for):

    --  Gaming inbox: off (factory.py social on gaming engage.read); engage.messages and replies wait for it, messages also for Meta's review (Advanced Access)
    NO  Politics inbox on Instagram: on, but the token lacks instagram_manage_comments
          fix: tick them on the system user's token (SETUP-POSTING, comments permissions), then factory.py connect meta politics
    ok  Gaming inbox (Facebook, YouTube): read 4 min ago, 6 unanswered
    --  Gaming messages: off; they wait for Meta's review (Advanced Access)
    --  Gaming replies from the Inbox: practice
