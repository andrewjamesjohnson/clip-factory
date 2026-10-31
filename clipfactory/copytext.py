"""Read a skill's paste file into title / body / tags.

Two shapes exist:
  gaming           first line = the title, the rest = the body
  viral-clip-engine   `# NOTE` lines (not pasted), then sections headed TITLE / DESCRIPTION / CAPTION / TAGS
A hashtag line (#gta6) is words, not a note: a note is `#` followed by a space.
"""
from __future__ import annotations

import re
from typing import Dict

# The engine's hand-posting sheet writes '(no hashtags on facebook, on purpose)' inside CAPTION: a note for the
# person pasting, never words for the post (it went out on the Politics's Facebook reels on 10-17).
NOTE_LINE = re.compile(r"^\(no hashtags on [a-z _-]+, on purpose\)$", re.I)
HEADS = {"TITLE": "title", "DESCRIPTION": "body", "CAPTION": "body", "TEXT": "body", "BODY": "body", "TAGS": "tags",
         "FIRST COMMENT": "first_comment", "PINNED COMMENT": "first_comment", "ALT TEXT": "alt"}


def parse(text: str) -> Dict[str, str]:
    lines = [ln for ln in text.splitlines() if not re.match(r"^#\s", ln) and ln.strip() != "#"
             and not NOTE_LINE.match(ln.strip())]
    if any(ln.strip() in HEADS for ln in lines):
        out: Dict[str, list] = {}
        key = "body"
        for ln in lines:
            if ln.strip() in HEADS:
                key = HEADS[ln.strip()]
                continue
            out.setdefault(key, []).append(ln)
        res = {k: "\n".join(v).strip() for k, v in out.items()}
        res["caption"] = res.get("body", "")
    else:
        body = "\n".join(lines).strip()
        first, _, rest = body.partition("\n")
        res = {"title": first.strip(), "body": rest.strip() or first.strip(), "caption": body}
    res.setdefault("title", "")
    res.setdefault("body", "")
    return res


# The hook line leads (Andrew, 2026-10-17): on Facebook, Instagram and TikTok a Politics caption opens on the clip's
# TITLE line (its hook), then the CAPTION. The Politics clip whose caption led with its hook did about 10x the others.
# stage.py writes the hook once, as TITLE, and this puts it on top when the clip posts; the review card and the Ready
# to post files read the words through here, so they show what posts. YouTube keeps its title + description. A
# caption that already opens with its title (a gaming file, a TikTok caption saved on the review page) is left as
# it is, so the hook is never doubled.
HOOK_FIRST = ("facebook_reels", "instagram_reels", "tiktok")


def _key(s: str) -> str:
    return " ".join(re.sub(r"[^\w#@$%]+", " ", s.lower()).split())


def hook_first(title: str, caption: str) -> str:
    """The caption with its title line on top, unless it already opens with it."""
    t, c = title.strip(), caption.strip()
    if not t or (_key(c) + " ").startswith(_key(t) + " "):
        return caption
    return f"{t}\n\n{c}"


def for_platform(text: str, platform: str) -> Dict[str, str]:
    """(title, caption) as pasted: YouTube takes a title and a description; the others take one caption."""
    w = parse(text)
    if platform == "youtube_shorts":
        return {"title": w["title"], "caption": w["body"], "tags": w.get("tags", "")}
    caption = w["caption"] or w["body"]
    if platform in HOOK_FIRST:
        caption = hook_first(w["title"], caption)
    return {"title": "", "caption": caption, "tags": w.get("tags", "")}
