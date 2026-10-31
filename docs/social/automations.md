# Automations (clipfactory/rules.py, answers.py, rulespage.py)

Rules that answer what people actually ask under a clip, with facts from the clip itself, and never get the account
flagged: "where is this from", "full video?", "who is that", "what hearing is this", "when does GTA 6 come out",
"part 2 please". Each rule practises first and writes exactly what it would have sent, to whom, when, and why anything
was held. Nothing reaches anyone until Andrew signs that rule on the review page, the account's own automations switch
is on, and the caps, quiet hours, windows and warm-up allow it.

Off for every account until `factory.py social on <account> automations --practice`. Off means off: the rules never
run, no file is written, no platform is called, and no existing page changes.

The notes below are for the integrator to fold into README.md, POSTING-APIS.md and SETUP-POSTING.md.

## README.md

**Section text** (under "## The social layer", after the Inbox):

### Automations (Clip Review, Inbox, Automations)

A rule fires on a new top-level comment (or, once Meta's review allows messages, a message) that asks something it
knows: by the comment's intents as the Inbox reads them (asks where it is from, asks for the full video, asks who that
is, asks which hearing or show, asks when, asks for part 2, any question) or by whole words (case does not matter, a
plural s counts, a phrase may run across lines; words in its "never when" list veto it). A rule set to "An ask and a
word" needs both (the starters all are, except Part 2): "when is part 2 coming out" asks when, but it is not about
the GTA 6 release, and "who is paying for this" asks who, but it is not about the speaker. A comment that asks for a
part 2 goes to the Part 2 rule first. The first rule in order that matches answers, once per comment. It answers only
from facts on disk (`answers.py`):

- **GTA:** the board's host, source title and source link, set to start at the clip's own second (`pick_span`), else
  the job's project.json; days to GTA 6 from the account's kit.json `launch.date`.
- **Politics:** the job's project.json (source title, link, date, host or channel); the hearing from cast.json
  `hearing{body, date}`; the speaker only from a cast a person verified (a one-person cast, or the clip's quote inside
  that person's name card). A provisional or bot-written cast never names anyone. Politics answers stay neutral: source
  facts only, never an opinion or a reaction to the politics (the editor refuses opinion words and exclamation marks
  on the Politics).
- **Both:** "in the first comment" only once the post's first comment went (its marker on the board) and its words
  hold a link (never on Instagram, whose first comment carries none). Source titles lose their hashtags and long
  dashes before a reply may use them.

A fact that is not there means no answer (the run is skipped with `no_fact`). Never invented.

**The words.** A public reply is 3 to 6 ways of saying it, 300 characters at most, starting or ending with
`{mention}`, never a link, a hashtag, a long dash, or an ask to comment, tag, follow or share. Ways of saying it must
differ (too alike is refused at save: the same words over and over reads as a bot); the one used last is never used
next; the same words never go out twice in a day. A private reply (or a message answer) is 1 to 3 ways, 900 bytes at
most; a link only in a message that answers an ask for the source or the full video; the first message to each person
starts "Automated reply: ", and so does the first after 30 days without an automated message, and the first
after Andrew wrote to them by hand (his own replies never count as the disclosure). The words are checked again once
the facts are filled in (300 characters, no hashtag or link in public, the Politics neutral): a way of saying it that
fails is passed over, and the run is skipped (`text`) when none passes. A comment that matched only by a word never
gets a link. Engagement bait ("comment X to get Y", "tag a friend", "type LINK below") is refused at
save: Facebook demotes it and it reads as a bot.

**The limits.** One automated message per person a day across every rule; one private reply or message per person
in 7 days across every rule and his own clicks (Meta allows one; `person_7d`); one per person per rule in its
`person_days` (7, never under 7 for a rule that sends private replies); the rule's own hour and day (never above the hard caps: 6 public replies an hour and 30 a day, 8
private replies an hour and 40 a day); then everything outward applies: quiet hours 00:30 to 07:00 (a reply waits for
the morning), random spacing, the family and engagement caps, a private reply only within 6 days of the comment, a
message only within 23 hours of theirs, holds, Stop everything. A person who says STOP never hears from an automation
again.

**Part 2.** A rule with the Part 2 note puts whoever asks for a part 2 on a waitlist keyed by the source (the GTA
source video, the Politics job) and answers in public if its public reply is on. When a later clip from the same source
goes live within 6 days of their comment, they get ONE note on the same platform: a private reply with the link while
messages are open ("Part 2 is up: <link>", or one other wording) and they had no private reply in 7 days, else a
public reply ("@name part 2 is up on the page now.", or one of two other wordings). The wordings rotate like a rule's
variants (never the one used last), are the factory's own, and are covered by the rule's signature. Never twice: the
waiter is claimed on the waitlist before the note is queued, so two jobs at once (or one killed half way) never send a
second note. Never past the rule's own hour and day, nor one automated message per person a day (the rule's answer to
the comment that asked does not count): whoever those leave out stays on the waitlist, and each poll's sweep sends
their notes as the limits allow, inside the same 6 days. The first answer
never spends the one private reply Meta allows per comment, never promises a part 2 is coming (only that they will
hear if one goes up), and a clip with no known source puts nobody on the waitlist (`no_fact`).

**Modes, per rule.** Practice (writes what it would send; unsigned rules only ever practise), Testers (only the
people with a role on the Meta app get it; everyone else stays practice), Live, Paused. Testers and Live need the
rule signed on these exact words, 24 hours of practice of these words (every edit starts the clock again, whatever
the mode, and time spent paused is not practice), and the account's own automations switch at least that far on
(the account switch runs the go-live checklist: practice for 2 days and 20 rows or 7 days, no practice row over a cap
or a hold, "I read these" on the Log page, the comments permission, the account's creation date). The warm-up keeps
every automated reply in practice for the account's first 28 days (and 14 days after the factory's first post).

**Signing.** The signature is the sha256 of the rule's platforms, trigger, words and limits. Any edit voids it and puts
the rule back in practice (the list then says "Changed after signing" and the doctor says NO until he signs again);
a mode change never does. Sign shows the whole rule in its confirm. Two signed rules may not
answer the same intent or words on the same platform. YouTube is refused at sign: YouTube takes no automated replies;
a YouTube comment that matches a rule only gets a suggested reply in the Inbox. The sender looks again right before
each live send: the rule still signed on the queued words, still Testers (and the person still a tester) or Live, not
switched back to practice, paused or self-paused since it was queued, and the comment not answered by hand, marked
done, hidden or gone meanwhile. A reply held overnight by quiet hours never goes once any of that changed.

**Self-pause.** A rule that reaches people goes back to practice by itself, with a red line on its row and a doctor
NO, when two people answer its messages with STOP, "bot" or "spam" within 7 days, or one of its replies is hidden.

**The page** (Inbox, Automations, `/inbox/rules`). Each account's automations switch, warm-up and go-live checklist;
the rules list (Rule, Account, Mode, Runs 24h, Sent, Would send, Skipped, Failed, Last run, Signed or "Changed after
signing"); the editor (name, platforms, trigger, intent chips, words, never-when words, the ways of saying each reply
with counters, the limits under the hard caps, Save, Sign, the mode, Duplicate to another account as a draft, Delete
for drafts); **Test on the last 7 days** (every stored comment of the week through the rule, in time order, with the
caps, quiet hours and limits: what it would have sent, to whom, when, why anything was held; nothing sent, nothing
saved); **Try it** (type a comment: the rule it matches, the exact words, what the caps, quiet hours or holds would do
right now); what the rule did lately. "Add the starter rules" adds, once per account, drafts that answer the natural
questions: Politics "Where is this from", "Who is that", "What hearing is this"; GTA "Whose stream is this", "When does
GTA 6 come out", "Part 2 please" (practice, unsigned).

Files: `state/<slug>/engage/rules.json` (`{"v": 1, "rules": {id: rule}, "seeded"}`), `engage/runs-YYYY-MM.jsonl` (one
row per comment a rule fired on: `{at, rule, item, result: queued|practice|skipped, why, note, to, name, text,
actions[{kind, key, variant, mode, would_at, why, code, ok, id}]}`), `engage/waitlist.json` (Part 2). Forget removes a
person's words and name from the runs too.

**Commands:**

    python3 factory.py rules seed <account>|--all        the starter drafts (practice, unsigned), once per account
    python3 factory.py rules list [account]              each rule: mode, version, signed, the last 24 hours
    python3 factory.py rules show <account> <rule>       one rule in full, its hash, and the words Sign would show
    python3 factory.py rules try <account> "source?"     Try it from the command line (writes nothing)
    python3 factory.py rules replay <account> [--rule r-src] [--days 7]    the 7-day test (writes nothing)
    python3 factory.py rules runs <account> [--rule r-src]                 what each rule did or would have done
    python3 factory.py rules pause|practice <account> <rule>               the safe directions
    python3 factory.py rules doctor                      the doctor's automation lines

Signing, Testers and Live are clicks on the review page only.

**Doctor lines:**

    --  Politics automations: practice, 2 rules signed, 14 would-sends in 7 days
    NO  Politics: rule r-src changed after signing: sign it again (Inbox, Automations)
    NO  Gaming: rule r-stream went back to practice by itself on 2026-11-02: 2 people answered its messages ...
    --  private replies: wait for Meta's review; only app testers get them

## POSTING-APIS.md

Under "## Meta: Facebook Reels and Instagram Reels", a subsection **Automated replies (rules.py)**:

- Rules never call a platform themselves. They queue through `outward.queue` with `origin {"by": "rule", "rule":
  id}`, `signed {"what": "rule:<id>@v<n>", "hash"}` and `force_practice` while the rule practises; the Inbox's executor
  (`engage`) sends: Instagram public reply `POST /{comment-id}/replies`, Facebook public reply `POST
  /{comment-id}/comments`, private reply `POST /{page-id}/messages` with `recipient={"comment_id": ...}`, message
  answer `POST /{page-id}/messages` with `recipient={"id": ...}` and `messaging_type=RESPONSE`.
- Politics keys: `rp:<platform>:<comment>` (public), `pr:<platform>:<comment>` (private, one per comment ever),
  `rp:<platform>:<comment>:part2` (the Part 2 note in public), `dm:<platform>:<thread>:<message>`.
- Permissions: public replies need `instagram_manage_comments` (Instagram) and `pages_manage_engagement` (Facebook),
  which work on Standard Access for our own accounts. Private replies and message answers need
  `instagram_manage_messages`, `pages_messaging`, `pages_manage_metadata` and Advanced Access (Business Verification
  of each portfolio plus App Review); until then they reach only people with a role on the app (testers), and
  `messages_access` stays `standard`, so outward keeps them in practice for everyone else.
- Meta publishes no safe volume; ours sit far under the API ceilings (private replies 750 an hour per Instagram
  account): 6 public and 8 private automated replies an hour per account and platform, 30 and 40 a day, random
  spacing, one message per person a day.

## SETUP-POSTING.md

In section 2 (Instagram and Facebook), after the comments permissions the Inbox needs:

- **Automations need no new permission for public replies** beyond the Inbox's (`instagram_manage_comments`,
  `pages_manage_engagement`, `pages_read_user_content`). Private replies wait for Meta's review (below).
- **Conversation routing** (do this before any private reply, even for testers): Meta Business Suite, Settings,
  Business assets, the Page, Advanced messaging (or Business Settings, Integrations, Conversation routing). Make sure
  no other app (ManyChat or similar) is the default app or has "Take control of conversations" on the Page or the
  Instagram account. If another app controls the conversation, the first private reply still lands but every later
  message (the Part 2 note, a reply to their answer) fails. The Clip Review Inbox polls the conversations and does
  not take control of them, so Meta Business Suite's own inbox keeps working beside it.
- **"Allow access to messages"** in the Instagram app (Settings, Messages and story replies, Message controls,
  Connected tools) must be on for any message the app reads or sends.

## The App Review packet (for private replies and message answers)

Business Verification first, for each portfolio (gaming, Capital Politics, Stunts | GTA 6), then one App Review
per app asking Advanced Access for `instagram_manage_messages`, `pages_messaging` and `pages_manage_metadata`. Text
to paste (one per permission; change the account names to the app's):

**How the app uses instagram_manage_messages / pages_messaging**

> AJJ Clip Factory posts short clips to our own Instagram accounts and Facebook Pages and answers the questions people
> ask under them. When a person comments asking where a clip is from or for the full video, the app sends one private
> reply to that comment with a link to the original source, so the person can watch it in full and the original
> creator is credited. When a person asks for part 2 of a clip, the app sends one private reply when the next clip
> from the same source is posted, within 6 days of their comment. Every message is a reply to something the person
> wrote first; the app never starts a conversation, never sends promotions, and sends at most one automated message
> per person a day and one private reply per person in 7 days. Each automated conversation starts with "Automated
reply:" (again after a long gap or after a person on our side wrote by hand). Anyone who replies STOP is never
> messaged again. Each automation's words are approved by the account owner before it runs, and a person can answer
> any message by hand from the app's inbox.

**How the app uses pages_manage_metadata**

> The app reads the Page's conversations to show the account owner new messages in one inbox and to know whether a
> person asked to stop. It does not change Page settings.

**Screencast script** (about 2 minutes, a tester account commenting on one of our Reels):

1. Show the Automations page: the "Where is this from" rule, its words, its limits, and its signature.
2. From the tester's Instagram, comment "where is this from?" on a Reel of ours.
3. Show the Inbox reading the comment, the rule's run ("would send" in practice, then testers mode), and the private
   reply arriving in the tester's Instagram messages with the source link.
4. Reply STOP from the tester; show the person on the STOP list and no further message.
5. Show the Log: every message with its time and result.

**Data handling:** comments and messages are kept on the account owner's own Mac for 45 days at most, never shared or
sold, and a person can be forgotten (their comments, messages and words removed) from the Inbox. The privacy policy
is PRIVACY-POLICY.txt at the app's website.

## Go-live steps (automations)

1. `factory.py rules seed <account>` (or "Add the starter rules" on the page) for each account. Read every starter's
   words; change what reads wrong.
2. `factory.py social created <account> YYYY-MM-DD` (the warm-up clock) and `factory.py social on <account>
   automations --practice` (Log, Switches, or the command).
3. Let the rules practise for 7 days or more. Use "Test on the last 7 days" on each rule; read the practice rows on
   the Log page, then press "I read these".
4. Sign the rules you want (Inbox, Automations, Sign: its confirm shows the whole rule).
5. Testers: `factory.py social tester <account> --platform instagram --id <your IGSID> --name Andrew`, then the
   account switch to testers (`factory.py social on <account> automations --testers`), then the rule to Testers on the
   page. Comment from your own account; check the reply.
6. Live, public replies only: after the warm-up, the account switch on (`factory.py social on <account>
   automations`), then each rule to Live on the page.
7. Private replies and message answers: only after Business Verification and App Review give Advanced Access; then
   `factory.py social set <account> messages_access advanced`, reconnect Meta with the messaging permissions
   (`factory.py connect meta <account>`), check Conversation routing, and switch the rule's private reply on (a new
   version: sign it again).

If anything looks wrong: pause the rule on the page, or Log, Stop everything.

## For the integrator

- The Inbox's executor (`engage.still_signed`, cf-inbox) already hands an action whose `signed.what` starts
  `rule:` to `rules.still_signed(cfg, acct, action)`: the sender re-checks the rule's signature (the action's
  `signed.hash` against the rule's hash now; the rule still Testers or Live and not switched off and on since the
  action was queued; a tester still a tester; the comment not answered by hand, done, hidden or gone) right before
  every live send.
- `rules.on_items` also runs `rules.part2_sweep` each poll (Part 2 notes the rule's limits held back).
- `answers.suggest(cfg, acct, item)` returns `{text, why, intent}` for the Inbox's Suggest button;
  `rules.on_items` also writes `item.suggested_reply = {text, rule, at, why}` for a YouTube comment a rule matched, and
  `item.auto = {rule, run, result, why}` for every comment a rule fired on.
- `rules.tags_for(text, maker)` uses `classify.tags` when it is built, else a small stand-in, for items with no tags,
  Try it and the replay.
