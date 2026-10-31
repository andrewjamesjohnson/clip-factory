"""What a comment or a message asks, and whether it looks like spam or abuse: words only, no network, no state.

The Inbox (engage.py) tags every new comment and message here, and the automations (rules.py) match on the tags. Two
answers per text:

    tags(text, maker)                    ["question", "asks_source", ...] in TAGS order
    suggest(text, author, history, maker)  {"hide": bool, "kind": "spam"|"toxic"|"", "why": "..."}

A suggestion is only ever a suggestion: hiding a comment is always Andrew's own click on the Inbox. Only three things
are suggested for hiding: spam (a link or "check my page" from someone new, giveaway and money-scheme words, the same
words three times), slurs and threats. Rudeness and disagreement never are: on the Politics, people arguing about
politics is the conversation, not abuse, and the GTA crowd's trash talk is not hidden either.

The maker ("gta" or "politics") adds the questions each audience asks: whose stream, when GTA 6 comes out, which server
on GTA; which hearing, who is speaking, which network on the Politics.
"""
from __future__ import annotations

import codecs
import re
from typing import Dict, List, Optional, Sequence

TAGS = ("question", "asks_source", "asks_full", "asks_who", "asks_when", "asks_which", "asks_part2", "praise", "stop")
ASKS = ("asks_source", "asks_full", "asks_who", "asks_when", "asks_which", "asks_part2")

# STOP: the first words of a message that ends every automated message to that person (architecture section 4)
STOP_RE = re.compile(r"^(stop|unsubscribe|no more|leave me alone|dont message me|don't message me)\b", re.I)


def _rx(*parts: str) -> "re.Pattern[str]":
    return re.compile("|".join(f"(?:{p})" for p in parts), re.I)


_QUESTION_WORDS = ("who", "whos", "who's", "whose", "what", "whats", "what's", "when", "where", "wheres", "where's",
                   "why", "how", "which", "is", "are", "does", "do", "did", "can", "could", "will", "would", "should",
                   "was", "were", "anyone", "any")

_SOURCE = _rx(r"\bsource\b", r"\bsauce\b", r"\bsrc\b", r"\blink\b", r"\boriginal (video|clip|stream|post)\b",
              r"\bwhere(?:'?s| is| was| did| does)? (this|that|it)(?: \w+)? (from|come from)\b",
              r"\bwhat(?:'?s| is) (this|that) from\b",
              r"\b(what|which) (video|show|podcast|stream|channel|vod|clip) is (this|that)\b")
_FULL = _rx(r"\bfull (video|vid|clip|stream|vod|interview|hearing|speech|episode|thing|version|exchange|segment|"
            r"conversation|podcast|answer)\b",
            r"\bwhole (video|thing|clip|stream|hearing|interview|speech|exchange|conversation)\b",
            r"\blonger version\b", r"\buncut\b", r"\bunedited\b", r"\bextended (version|cut|clip)\b",
            r"\bwhere can i (watch|see|find) (the )?(rest|full|whole)\b",
            r"\brest of (it|the (video|stream|hearing|interview|clip|speech))\b")
_WHO = _rx(r"\bwho(?:'?s| is| was| are| were)\b(?! (cares|asked|knows))",
           r"\bwhat(?:'?s| is) (his|her|their|the (guy|man|woman|streamer|lady)'?s) name\b",
           r"\bname of (the|this|that) (guy|man|woman|lady|streamer|senator|congressman|congresswoman|reporter|"
           r"speaker|witness)\b")
_WHEN = _rx(r"\bwhen (is|was|does|did|will|do|are|were)\b", r"\bwhen'?s\b", r"\bwhat (date|day|year)\b",
            r"\brelease date\b", r"\bhow long (until|till|til)\b", r"\bwhen\b[^.?]{0,40}\?")
_WHICH = _rx(r"\b(which|what) (hearing|committee|game|server|mod|car|map|mission|city|show|network|outlet|episode|"
             r"interview|podcast|bill|case|trial|court|song|music|track|briefing|press conference|station)\b",
             r"\bwhat (song|music|track) is (this|that)\b")
_PART2 = _rx(r"\b(part|pt)\.? ?(2|two|ii|3|three)\b", r"\bnext part\b", r"\bpart 2\b", r"\bmore of (this|these)\b",
             r"\bwe need more\b", r"\bmore please\b", r"\bsequel\b", r"\bcontinuation\b")
_PRAISE = _rx(r"\b(love|loved|loving)\b", r"\b(great|awesome|amazing|incredible|brilliant|fantastic|excellent)\b",
              r"\b(goat|legend|legendary|banger|underrated|hilarious|based)\b", r"\bwell said\b",
              r"\bthank(s| you)\b", r"\bgood (one|job|clip|stuff|work|content)\b", r"\b(lmao|lmfao|lol)\b",
              r"\b(huge|massive|big) w\b", r"\bso funny\b", r"\bthis is fire\b", r"\bfire\b",
              "\U0001F525", "\U0001F602", "\U0001F923", "❤", "\U0001F44F", "\U0001F4AF")
_ONLY_W = re.compile(r"^\s*(w|dub|ww+)\s*[!.]*\s*$", re.I)

MAKER_EXTRA: Dict[str, Dict[str, "re.Pattern[str]"]] = {
    "gta": {
        "asks_source": _rx(r"\b(whose|which|what) (stream|streamer|channel|vod)\b",
                           r"\bwho(?:'?s| is) (the|this|that) streamer\b", r"\bstreamer'?s? name\b"),
        "asks_who": _rx(r"\b(whose|which) (stream|streamer|channel)\b", r"\bstreamer'?s? name\b"),
        "asks_when": _rx(r"\b(gta ?(6|vi)|it)\b[^.?]{0,30}\b(come out|coming out|out|release|drop|launch)\w*\b[^.]*\?",
                         r"\b(come|comes|coming) out\b", r"\bnovember 19\b"),
        "asks_which": _rx(r"\b(what|which) (server|rp server|mod|car|gun|weapon|map|mission|heist)\b"),
    },
    "politics": {
        "asks_source": _rx(r"\b(what|which) (hearing|network|outlet|interview|show|podcast|press conference|"
                           r"briefing|channel) (is|was) (this|that)\b"),
        "asks_who": _rx(r"\bwho(?:'?s| is| was) (that|this|the) (senator|congressman|congresswoman|rep|"
                        r"representative|lady|guy|man|woman|reporter|secretary|witness|judge|journalist|host)\b",
                        r"\bwhat(?:'?s| is) (his|her) (name|title)\b"),
        "asks_when": _rx(r"\bwhen was this (hearing|said|recorded|filmed|taped)\b", r"\bhow old is this\b",
                         r"\bis this (new|old|recent)\b"),
        "asks_which": _rx(r"\b(what|which) (hearing|committee|senator|congressman|state|district)\b"),
    },
}
_RES = (("asks_source", _SOURCE), ("asks_full", _FULL), ("asks_who", _WHO), ("asks_when", _WHEN),
        ("asks_which", _WHICH), ("asks_part2", _PART2))


def _norm(text: object) -> str:
    """Lower case, curly quotes straightened, spaces evened out."""
    t = str(text or "").replace("’", "'").replace("‘", "'").replace("“", '"').replace("”", '"')
    return " ".join(t.split()).lower()


def is_stop(text: object) -> bool:
    """Does the message open with STOP (or unsubscribe, leave me alone ...)?"""
    return bool(STOP_RE.match(_norm(text).lstrip(" \"'*.-")))


def _is_question(t: str) -> bool:
    if "?" in t:
        return True
    if re.match(r"who (cares|asked|knows)\b", t):         # a shrug, not a question
        return False
    words = re.findall(r"[\w']+", t)
    return len(words) >= 3 and words[0] in _QUESTION_WORDS


def tags(text: object, maker: str = "") -> List[str]:
    """What the words ask or say, in TAGS order: question, asks_source, asks_full, asks_who, asks_when, asks_which,
    asks_part2, praise, stop. [] for words that ask nothing (most comments)."""
    t = _norm(text)
    if not t:
        return []
    got = set()
    if _is_question(t):
        got.add("question")
    extra = MAKER_EXTRA.get(str(maker or ""), {})
    for tag, rx in _RES:
        if rx.search(t) or (tag in extra and extra[tag].search(t)):
            got.add(tag)
    if "asks_when" in got and "question" not in got and not re.search(r"\bwhen\b", t):
        got.discard("asks_when")                 # "come out" alone is not asking when
    if "asks_who" in got and re.search(r"\bwho (cares|asked|knows)\b", t) and len(got) == 1:
        got.discard("asks_who")
    if _PRAISE.search(t) or _ONLY_W.match(t):
        got.add("praise")
    if is_stop(t):
        got.add("stop")
    if got & set(ASKS):
        got.add("question")
    return [x for x in TAGS if x in got]


# ------------------------------------------------------------------------------------------------------ suggestions
# Slurs, kept out of plain sight in the source (rot13): matched as whole words, plural s allowed.
_SLURS = tuple(codecs.decode(w, "rot13") for w in (
    "avttre", "avttn", "snttbg", "snt", "ergneq", "xvxr", "fcvp", "puvax", "genaal", "jrgonpx", "tbbx", "pbba",
    "gbjryurnq", "enturnq", "ornare", "qlxr"))
_SLUR_RE = re.compile(r"\b(" + "|".join(re.escape(w) for w in _SLURS) + r")(s|z)?\b", re.I)
_LEET = str.maketrans({"1": "i", "!": "i", "3": "e", "0": "o", "@": "a", "$": "s", "4": "a", "5": "s", "7": "t"})
_THREAT = _rx(r"\bkill (yo)?urself\b", r"\bkys\b", r"\bi(?:'ll| will| am going to|'m going to|m gonna|'m gonna) "
              r"(kill|find|hurt|shoot|stab|beat|murder) (you|u|him|her|them)\b",
              r"\bhope (you|u|he|she|they) (die|dies|get shot|gets shot|get killed|gets killed)\b",
              r"\b(you|u)(?:'re| are| r) (dead|going to die|gonna die)\b", r"\bwatch your back\b",
              r"\bi know where (you|u) live\b",
              r"\bshould (be|get) (shot|hanged|hung|executed|killed|lynched)\b",
              r"\b(shoot|hang|lynch|execute) (him|her|them|this guy)\b")
_LINK = re.compile(r"(https?://|www\.)\S+|\b[\w-]+\.(com|net|org|io|co|me|ly|gg|xyz|app|link|shop|store)(/\S*)?\b"
                   r"|\bdot com\b", re.I)
_SELF_PROMO = _rx(r"\bcheck (out )?my (page|profile|channel|bio|account|ig|insta|instagram|tiktok|link|videos?|"
                  r"content)\b",
                  r"\b(follow|sub|subscribe) (me|to me|to my|my)\b", r"\bfollow (4|for) follow\b", r"\bf4f\b",
                  r"\bfollow back\b", r"\bvisit my\b", r"\blink in (my )?bio\b", r"\bdm (me|for) (for )?(promo|collab|"
                  r"business|more)\b", r"\bpromo(tion)? (available|offer)\b")
_SCAM = _rx(r"\bgive ?away\b", r"\b(you('ve| have)|u have) (been )?(won|selected|chosen)\b",
            r"\bclaim (your|ur) (prize|reward|gift)\b",
            r"\bfree (followers|likes|views|iphone|robux|money|cash|v-?bucks|shark ?cards?|gift ?cards?)\b",
            r"\b(shark ?card|money) (generator|glitch for sale|hack)\b",
            r"\bcrypto (signals?|investment|trading (platform|expert)|mining)\b",
            r"\bbitcoin (investment|mining|signals?|profit)\b", r"\bbinary options? (trading|signals?|broker)\b",
            r"\binvestment (manager|platform|plan|expert|opportunity)\b",
            r"\bearn \$?\d[\d,]* ?(k )?(daily|weekly|a day|per day|a week|every day)\b",
            r"\bwa\.me\b", r"\bt\.me/", r"\bsugar (daddy|mommy)\b", r"\brecover (your|my) (hacked )?account\b")
# Words that are news on the Politics ("the TikTok and Telegram ban", "WhatsApp encryption", "a forex manipulation
# case") and spam only beside a way to reach the writer: a handle, a number, "dm me", "add me on", a link.
_SCAM_SOFT = _rx(r"\bwhats ?app\b", r"\btelegram\b", r"\bcash ?app\b", r"\bforex\b", r"\bbinary options?\b",
                 r"\baccount manager\b", r"\bonlyfans\b", r"\bhacked account\b")
_CONTACT = _rx(r"(^|\s)@[\w.]{3,}", r"\+\d[\d ()-]{7,}\d", r"\b\d{3}[ .-]?\d{3}[ .-]?\d{4}\b",
               r"\b(dm|message|text|msg|inbox|contact|reach( out to)?|write( to)?|chat with|hit( me)? up)"
               r"( me| us| him| her)\b", r"\badd me\b", r"\bmy (number|handle|username|contact|details)\b",
               r"\b(on|via|at) (whats ?app|telegram)( me)?:? ?[@+\d]", r"(https?://|www\.)\S+",
               r"\b[\w-]+\.(com|net|org|io|co|me|ly|gg|xyz|app|link)\b")


def _same_text(a: str) -> str:
    return re.sub(r"[^\w]+", " ", _norm(a)).strip()


def suggest(text: object, author: Optional[dict] = None, history: Sequence[str] = (), maker: str = "") -> dict:
    """Should Andrew think about hiding this? {"hide", "kind", "why"}: spam (a link or "check my page" from
    someone who never wrote before, giveaway or money-scheme words, the same words three times), or toxic (a slur, a
    threat). Never for our own words, never for disagreement or rudeness. `author` is the item's author, with
    "known" True when this person wrote to the account before; `history` is the same person's earlier words."""
    author = author if isinstance(author, dict) else {}
    none = {"hide": False, "kind": "", "why": ""}
    if author.get("is_us"):
        return none
    raw = _norm(text)
    if not raw:
        return none
    plain = raw.translate(_LEET)
    if _SLUR_RE.search(raw) or _SLUR_RE.search(plain):
        return {"hide": True, "kind": "toxic", "why": "a slur"}
    if _THREAT.search(raw):
        return {"hide": True, "kind": "toxic", "why": "a threat"}
    if _SCAM.search(raw) or (_SCAM_SOFT.search(raw) and _CONTACT.search(raw)):
        return {"hide": True, "kind": "spam", "why": "giveaway or money-scheme words"}
    stranger = not author.get("known")
    if stranger and _LINK.search(raw):
        return {"hide": True, "kind": "spam", "why": "a link from someone new"}
    if stranger and _SELF_PROMO.search(raw):
        return {"hide": True, "kind": "spam", "why": "asks people to visit their page"}
    me = _same_text(raw)
    if me and len(me) >= 4 and sum(1 for h in history or () if _same_text(h) == me) >= 2:
        return {"hide": True, "kind": "spam", "why": "the same words three times"}
    return none
