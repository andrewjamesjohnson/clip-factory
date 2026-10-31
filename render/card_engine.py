"""The Clip Factory's card drawer: stills and story frames in each account's own look, from real footage, no AI.

The factory itself is stdlib Python 3.9 and never imports Pillow. It writes a spec (a JSON file) and runs this script
under the engine's own Python, exactly as the skills run their scripts:

    /usr/bin/python3 -B render/card_engine.py <spec.json>        (or CLIP_ENGINE_PYTHON)
    /usr/bin/python3 -B render/card_engine.py --selftest [--skill <viral-clip-engine dir>]

The spec: {"v": 1, "look": "politics" | "gta", "kind", "skill_dir", "kit", "size": [1080, 1920] | [1080, 1350],
"slides": [{"layout", ...words and a still}], "outputs": [one .jpg path per slide]}. Each output is written as
<name>.part.jpg and then moved into place, so a half-drawn file is never there. It prints one JSON line:
{"ok": true, "engine": "card_engine 1", "files": [{"path", "w", "h"}]} or {"ok": false, "error": "..."}.

Politics: the viral-clip-engine's politicslook (Helvetica Neue with its own kerning, the POLITICS / POLITICS
masthead, the chyron, the two-tier name plate), configured from presets/hearing.json, then run with NO accent: a card is black, white and greys only (Andrew 10.30,
"I don't want the gold text"), so figures are full bone and the rest as before. The clips keep their own accent. Gaming: the gaming kit (assets/brand/<kit>/kit.json, read directly, never through qm_config): Impact for
display, DIN Condensed for the small type, an ink outline made with MaxFilter and the accent as a hard offset shadow.

Every word drawn comes from the spec (the factory takes it from the transcript, the slate, the signed copy or the
verified cast). Emoji are stripped (Pillow here has no colour emoji). JPEG quality 92 with no chroma subsampling. No
glows, gradients, node dots or dotted paths: flat ink, flat bone, hard edges.

Where things sit. A card is centred on the CANVAS (x 540), never on a Reel's safe zone: a story or a feed post has no
action rail on the right, so the Reels zone (x 65..916) would put everything left of centre. Type keeps inside
symmetric margins, x 72..1008 (the Politics grid's X_L and X_R). A story (9:16) keeps its top 250 and its bottom 340
clear (Instagram and Facebook draw the progress bar and the name at the top, the reply bar at the bottom) and its
content is balanced, as one block, in the band between (y 250..1580); a feed post (4:5) in its own frame less an 80
margin. The Politics keeps its flush-left broadcast grid (masthead, quote, chyron and plates at X_L) inside those
margins; a cover centres the lockup.
"""
from __future__ import annotations

import json
import math
import os
import re
import sys
from pathlib import Path

ENGINE = "card_engine 1"
MARGIN_X = 72                        # symmetric: content keeps to x 72..1008 on a 1080 frame
STORY_TOP, STORY_BOTTOM = 250, 340   # a story's own bars: keep these clear
FEED_MARGIN = 80                     # a feed post (4:5) has no bars over it
# The Politics's news post (2026-10-22, Andrew's three rounds on the samples). The ground is the post's own picture,
# blurred, greyed and darkened ("a blurred image ... in the background ... very subtle", never the plain black), a step
# quieter than the clips' blurred room. A short word never ends a row. The sources are the outlets' own logos ("it makes
# it more colorful"): a black or grey part turns bone so a black wordmark reads on the dark ground, every colour stays.
NEWS_BLUR, NEWS_SAT, NEWS_LUMA = 5, 0.50, 0.32
NEWS_SEAL_GROUND = 1.25              # a seal under the frame, this times its long side, so its ring and centre fill it
NEWS_GLUE = {"a", "an", "the", "of", "to", "in", "on", "at", "for", "by", "with", "and", "or", "as", "from"}
NEWS_TRACK = -0.005
NEWS_ROW_BOTTOM = 200               # slide 1's sources row ends at least this far above the bottom edge, as slide 2's does
LOGO_AREA, LOGO_MAX_H, LOGO_GAP = 7200, 58, 44   # each logo about the same ink area; a square bug and a wordmark weigh the same
FONTS = {"helvetica": "/System/Library/Fonts/HelveticaNeue.ttc",
         "impact": "/System/Library/Fonts/Supplemental/Impact.ttf",
         "din": "/System/Library/Fonts/Supplemental/DIN Condensed Bold.ttf"}
_EMOJI = re.compile("[\U0001F000-\U0001FFFF☀-➿︎️‍⃣\U000E0000-\U000E007F]")
_FIGURE = re.compile(r"[0-9]")


def clean(text) -> str:
    """The words with emoji taken out and the spacing evened (Pillow here would draw an emoji as a box)."""
    return " ".join(_EMOJI.sub("", str(text or "")).split())


def is_figure(word: str) -> bool:
    """A number, a dollar figure or a percentage: the words a card sets in full bone."""
    return bool(_FIGURE.search(word))


# ------------------------------------------------------------------------------------------------------ shared
def is_story(size) -> bool:
    """A 9:16 frame (a story); else a 4:5 feed post."""
    return int(size[1]) >= int(size[0]) * 16 // 9 - 4


def band(size):
    """(y0, y1): where a card's content is balanced. A story keeps its top 250 and bottom 340 clear; a feed post its
    80 margin."""
    h = int(size[1])
    return (STORY_TOP, h - STORY_BOTTOM) if is_story(size) else (FEED_MARGIN, h - FEED_MARGIN)


def centred_top(y0: float, y1: float, height: float) -> float:
    """The top that centres a block `height` tall between y0 and y1 (y0 when it is taller than the band)."""
    return y0 + max(0.0, ((y1 - y0) - height) / 2)


def _load(path):
    from PIL import Image
    im = Image.open(str(path))
    im.load()
    return im


def _fit(im, w: int, h: int):
    """The picture cut to fill w x h, centred (no bars)."""
    from PIL import ImageOps
    return ImageOps.fit(im.convert("RGB"), (int(w), int(h)), method=3, centering=(0.5, 0.5))


def _ground(still, w: int, h: int):
    """The Politics's blur ground (politicslook GROUND_SAT 0.25, GROUND_LUMA 0.45): the picture, blurred, greyed and
    darkened evenly. A flat treatment of the picture itself, no gradient."""
    from PIL import ImageEnhance, ImageFilter
    small = _fit(still, max(1, w // 8), max(1, h // 8)).filter(ImageFilter.GaussianBlur(5))
    big = small.resize((w, h), resample=3)
    big = ImageEnhance.Color(big).enhance(0.25)
    return ImageEnhance.Brightness(big).enhance(0.45)


def _save(img, out: str, size) -> dict:
    """Write the slide as JPEG (q92, no chroma subsampling) to <out>.part.jpg, then move it into place."""
    w, h = int(size[0]), int(size[1])
    if img.size != (w, h):
        img = img.crop((0, 0, w, h))
    img = img.convert("RGB")
    p = Path(out)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(p.stem + ".part.jpg")
    img.save(str(tmp), "JPEG", quality=92, subsampling=0, optimize=True)
    os.replace(str(tmp), str(p))
    return {"path": str(p), "w": w, "h": h}


# ------------------------------------------------------------------------------------------------------ the Politics
class Politics:
    """Politics's broadcast look, drawn with the skill's own politicslook module."""

    WIN_STORY = 608      # the picture's window on a story: the whole 16:9 frame, edge to edge (the broadcaster's own
    #                      lower third stays whole)
    WIN_FEED = 640       # on a feed slide (4:5): a touch taller (each side gives up 2.5%)

    def __init__(self, skill_dir: str):
        scripts = str(Path(skill_dir) / "scripts")
        if scripts not in sys.path:
            sys.path.insert(0, scripts)
        import politicslook as L  # noqa: E402  (the skill's own look)
        self.L = L
        preset = Path(skill_dir) / "presets" / "hearing.json"
        try:
            cfg = json.loads(preset.read_text())
        except (OSError, ValueError):
            cfg = {"look_accent": "#C9A24A", "masthead": "POLITICS"}
        L.configure(cfg)
        # A card has no action rail on its right (that is a Reel's): the chyron and the plates may run to X_R, so the
        # grid is X_L..X_R, symmetric on the frame. This process draws cards only.
        L.RAIL_X1 = L.X_R
        # No gold on a card (Andrew 10.30): politicslook's monochrome mode (ACCENT None draws PAPER), for this card
        # process only; presets/hearing.json and the clips are untouched.
        L.ACCENT = None
        self.ACCENT = L.PAPER

    # the plates, as the clips draw them (each draw function wrapped in L.D for Element/paint)
    def _paint(self, base, elements):
        L = self.L
        els = [L.Element(p, 0.0, None, 0.0, 0.0, L.D(fn)) for p, fn in elements]
        return L.paint(els, 10.0, base)

    def _shift(self, plate, dy: float, dx: float = 0.0):
        """Move a plate (its layers and the boxes its drawing reads) by dx, dy."""
        dx, dy = int(round(dx)), int(round(dy))
        for k, (img, x, y) in list(plate.layers.items()):
            plate.layers[k] = (img, x + dx, y + dy)
        for key in ("lock", "bar", "tab", "name", "title", "num_box", "window"):
            v = plate.meta.get(key)
            if isinstance(v, tuple) and len(v) == 4:
                plate.meta[key] = (v[0] + dx, v[1] + dy, v[2] + dx, v[3] + dy)
        for key, d in (("seam", dy), ("rule_y", dy), ("src_x", dx), ("left", dx)):
            if isinstance(plate.meta.get(key), (int, float)):
                plate.meta[key] += d
        if isinstance(plate.meta.get("slots"), list):
            plate.meta["slots"] = [(a + dx, b + dy, c + dx, d + dy) for a, b, c, d in plate.meta["slots"]]
        return plate

    @staticmethod
    def _ink(plate, skip=("ground",)):
        """(x0, y0, x1, y1) of what a plate actually inks (its layers' alpha, a shadow's padding left out)."""
        boxes = []
        for name, (img, x, y) in plate.layers.items():
            if name in skip:
                continue
            b = img.getchannel("A").point(lambda v: 255 if v > 24 else 0).getbbox()
            if b:
                boxes.append((x + b[0], y + b[1], x + b[2], y + b[3]))
        if not boxes:
            return (0, 0, 0, 0)
        return (min(b[0] for b in boxes), min(b[1] for b in boxes), max(b[2] for b in boxes),
                max(b[3] for b in boxes))

    def _canvas(self, still, w: int, h: int):
        from PIL import Image
        L = self.L
        if still is not None:
            return _ground(still, w, h).convert("RGBA")
        return Image.new("RGBA", (w, h), L.INK + (255,))

    def _window(self, canvas, still, top: float, height: int) -> int:
        """The picture, sharp, in a window the width of the frame. Its bottom edge."""
        from PIL import ImageDraw
        if still is not None:
            canvas.alpha_composite(_fit(still, canvas.width, height).convert("RGBA"), (0, int(round(top))))
            ImageDraw.Draw(canvas).rectangle([0, int(round(top)) - 2, canvas.width, int(round(top)) - 1],
                                             fill=self.L.PAPER + (110,))
        return int(round(top)) + height

    def _words(self, d, x: float, base: float, words, face: str, size: int, colour, track: float = -0.005) -> None:
        """One row, word by word, all in bone."""
        L = self.L
        space = L.measure(" ", face, size, track)
        for w in words:
            fill = (self.ACCENT if is_figure(w) else L.PAPER) + (255,)
            x = L.draw_text(d, x, base, w, face, size, fill if colour is None else colour, track) + space

    def _kicker(self, d, x: float, base: float, text: str) -> float:
        L = self.L
        k = clean(text).upper()
        end = L.draw_text(d, x, base, k, "bold", 19, L.PAPER + (175,), 0.20)
        return end

    def _quote_rows(self, body: str, height: float):
        """The line as large as it will go across the full measure (X_L..X_R) in a few rows that fit `height`, then
        evened out so no row is a lone word. Never a word dropped: the quote is verbatim. (rows, size)."""
        L = self.L
        room = L.X_R - L.X_L
        for most, low in ((4, 52), (6, 42), (9, 32)):
            for size in range(76, low - 1, -2):
                rows = L.wrap(body, "medium", size, room, -0.005)
                tall = L.cap_height("medium", size) + (len(rows) - 1) * round(size * 1.22) + \
                    L.descent("medium", size)
                if len(rows) <= most and tall <= height:
                    return L._balanced_rows(body, "medium", size, room, -0.005), size
        return L.wrap(body, "medium", 32, room, -0.005), 32

    def quote(self, s: dict, size):
        """The line, verbatim, under the masthead, then the speaker on the clean still with the verified name
        plate: one block, balanced in the story's band (clear of its top and bottom bars). 1080x1920."""
        from PIL import ImageDraw
        L = self.L
        w, h = int(size[0]), int(size[1])
        y0, y1 = band(size)
        still = _load(s["still"]) if s.get("still") else None
        c = self._canvas(still, w, h)
        mast = L.masthead(clean(s.get("source")), True)
        m_top, m_bot = mast.meta["lock"][1], mast.meta["lock"][3]
        # the block, measured from the masthead's top (0): kicker, the line, the picture
        kb = (m_bot - m_top) + 58 + L.cap_height("bold", 19)
        win_h = self.WIN_STORY if still is not None else 0
        rows, qsize = self._quote_rows("“" + clean(s.get("quote")) + "”",
                                       (y1 - y0) - (kb + 34) - (48 + win_h if still is not None else 0))
        lead = round(qsize * 1.22)
        base0 = kb + 34 + L.cap_height("medium", qsize)
        text_bottom = base0 + (len(rows) - 1) * lead + L.descent("medium", qsize)
        win_top = text_bottom + 48
        block = win_top + win_h if still is not None else text_bottom
        top = centred_top(y0, y1, block)
        c = self._paint(c, [(self._shift(mast, top - m_top), L.draw_masthead)])
        d = ImageDraw.Draw(c)
        kend = self._kicker(d, L.X_L, top + kb, "On the record")
        d.rectangle([int(kend + 16), int(top + kb - 10), int(kend + 64), int(top + kb - 9)], fill=L.PAPER + (150,))
        hang = L.font("medium", qsize).getlength("“")
        for i, row in enumerate(rows):
            x = L.X_L - (hang if i == 0 else 0)
            self._words(d, x, top + base0 + i * lead, row.split(" "), "medium", qsize, None)
        if still is not None:
            wt = top + win_top
            wb = self._window(c, still, wt, win_h)
            name, title = clean(s.get("name")), clean(s.get("title"))
            if name:
                # above a broadcaster's own lower third (the bottom quarter of a news frame)
                plate = L.nameplate(name, title, int(round(wt + 0.70 * (wb - wt))))
                c = self._paint(c, [(plate, L.draw_nameplate)])
        return c

    def docket_cover(self, s: dict, size):
        """The cover of a carousel: THE DOCKET (or THE WEEK'S BEST), the count as the figure, the date. The lockup
        centred at the top, the figure centred on the frame, the date at the foot, the same distance from the edge."""
        from PIL import ImageDraw
        L = self.L
        w, h = int(size[0]), int(size[1])
        y0, y1 = band(size)
        c = self._canvas(None, w, h)
        fig = L.figure_full(clean(s.get("figure")), clean(s.get("sub")), clean(s.get("label")), centre_y=h // 2)
        fx0, fy0, fx1, fy1 = self._ink(fig)
        fig.layers.pop("ground", None)                     # its ground is the flat ink of the cover
        c = self._hold(c, fig, h / 2 - (fy0 + fy1) / 2)     # the figure at rest, its ink centred on the frame
        mast = L.masthead("", False)
        lx0, ly0, lx1, _ = mast.meta["lock"]
        c = self._paint(c, [(self._shift(mast, y0 - ly0, w / 2 - (lx0 + lx1) / 2), L.draw_masthead)])
        d = ImageDraw.Draw(c)
        date = clean(s.get("date")).upper()
        if date:
            tw = L.measure(date, "medium", 26, 0.12) - 0.12 * 26
            L.draw_text(d, w / 2 - tw / 2, y1, date, "medium", 26, L.PAPER + (205,), 0.12)
        return c

    def _hold(self, c, plate, dy: float):
        """A plate's layers as they rest (no motion), moved by dy."""
        self._shift(plate, dy)
        for img, x, y in plate.layers.values():
            self.L._put(c, img, x, y)
        return c

    def docket_item(self, s: dict, size):
        """One clip: the masthead with its source, the clean still in its window, its title as the chyron, the time
        and the source under it, and the views in bone when they are known; one block, balanced in the slide.
        1080x1350."""
        from PIL import ImageDraw
        L = self.L
        w, h = int(size[0]), int(size[1])
        y0, y1 = band(size)
        still = _load(s["still"]) if s.get("still") else None
        c = self._canvas(still, w, h)
        mast = L.masthead(clean(s.get("source")), still is not None)
        m_top, m_bot = mast.meta["lock"][1], mast.meta["lock"][3]
        y = m_bot - m_top                                  # the block, from the masthead's top (0)
        win_top, win_h = None, 0
        if still is not None:
            win_top, win_h = y + 48, self.WIN_FEED
            y = win_top + win_h
        ch = L.chyron(clean(s.get("title")), clean(s.get("tab")) or "THE DOCKET", 1000)
        if still is None:
            # no picture to span the frame: the bar takes the full measure, so the slide still sits square
            bx0, by0, _, by1 = ch.meta["bar"]
            ch.layers["bar"] = (L._rect(L.X_R - L.X_L, by1 - by0, L.PAPER), bx0, by0)
            ch.meta["bar"] = (bx0, by0, L.X_R, by1)
        c_top = (ch.meta.get("tab") or ch.meta["bar"])[1]
        c_h = ch.meta["bar"][3] - c_top
        chy_top = y + 44
        y = chy_top + c_h
        line = " · ".join(x for x in (clean(s.get("time")).upper(), clean(s.get("host")).upper()) if x)
        line_base = y + 52 + L.cap_height("medium", 26) if line else None
        y = line_base if line else y
        fig = clean(s.get("figure"))
        fig_base = (y + (70 if line else 56 + L.cap_height("cond", 56))) if fig else None
        block = fig_base if fig else y
        top = centred_top(y0, y1, block)
        c = self._paint(c, [(self._shift(mast, top - m_top), L.draw_masthead)])
        if still is not None:
            self._window(c, still, top + win_top, win_h)
        c = self._paint(c, [(self._shift(ch, top + chy_top - c_top), L.draw_chyron)])
        d = ImageDraw.Draw(c)
        if line:
            L.draw_text(d, L.X_L, top + line_base, line, "medium", 26, L.PAPER + (220,), 0.10)
        if fig:
            self._words(d, L.X_L, top + fig_base, fig.upper().split(" "), "cond", 56, None, 0.0)
        return c

    # ---------------------------------------------------------------------------------------------- the news post
    def _news_ground(self, pic: dict, w: int, h: int):
        """The post's own picture, blurred, greyed and darkened to fill the frame, the same on both slides so the
        carousel reads as one piece. A seal is laid on ink first, larger than the frame."""
        from PIL import Image, ImageEnhance, ImageFilter
        L = self.L
        im = _load(pic["path"])
        if im.mode in ("RGBA", "LA", "P"):
            im = im.convert("RGBA")
            flat = Image.new("RGBA", im.size, L.INK + (255,))
            flat.alpha_composite(im)
            im = flat
        im = im.convert("RGB")
        if pic.get("kind") == "seal":
            side = int(max(w, h) * NEWS_SEAL_GROUND)
            big = Image.new("RGB", (w, h), L.INK)
            big.paste(im.resize((side, side), Image.LANCZOS), ((w - side) // 2, (h - side) // 2))
            im = big
        else:
            im = _fit(im, w, h)
        small = im.resize((max(1, w // 8), max(1, h // 8)), Image.BILINEAR).filter(ImageFilter.GaussianBlur(NEWS_BLUR))
        out = ImageEnhance.Color(small.resize((w, h), Image.BICUBIC)).enhance(NEWS_SAT)
        return ImageEnhance.Brightness(out).enhance(NEWS_LUMA).convert("RGBA")

    def _news_rows(self, text: str, face: str, size: int, room: float):
        """politicslook's balanced wrap (fewest rows, then evened out), but a short word (a, the, of ...) always travels
        with the word after it, so no row ends on one."""
        L = self.L
        words, toks, i = text.split(), [], 0
        while i < len(words):
            t = words[i]
            while t.split(" ")[-1].lower().lstrip("“‘") in NEWS_GLUE and i + 1 < len(words):
                i += 1
                t += " " + words[i]
            toks.append(t)
            i += 1

        def greedy(limit: float):
            rows, cur = [], ""
            for t in toks:
                trial = f"{cur} {t}".strip()
                if cur and L.measure(trial, face, size, NEWS_TRACK) > limit:
                    rows.append(cur)
                    cur = t
                else:
                    cur = trial
            return rows + [cur] if cur else rows

        rows = greedy(room)
        if len(rows) < 2:
            return rows
        lo, hi, best = room * 0.4, room, rows
        for _ in range(18):
            mid = (lo + hi) / 2
            trial = greedy(mid)
            if len(trial) <= len(rows):
                best, hi = trial, mid
            else:
                lo = mid
        return best

    def _news_fit(self, text: str, face: str, start: int, low: int, most: int, lead_k: float):
        """(rows, size, lead): the text as large as it goes (start down to low) in at most `most` rows across X_L..X_R."""
        room = self.L.X_R - self.L.X_L
        for size in range(start, low - 1, -2):
            rows = self._news_rows(text, face, size, room)
            if len(rows) <= most:
                return rows, size, round(size * lead_k)
        return self._news_rows(text, face, low, room), low, round(low * lead_k)

    def _news_kick(self, d, base: float, text: str) -> None:
        """The kicker alone, tracked caps: no rule after it (a short rule there reads as a dash)."""
        self.L.draw_text(d, self.L.X_L, base, clean(text).upper(), "bold", 20, self.L.PAPER + (210,), 0.20)

    def _news_dim(self, d, x: float, base: float, words, face: str, size: int, alpha: int) -> None:
        """One row: the words in a quieter bone, the figures in full bone."""
        L = self.L
        space = L.measure(" ", face, size, NEWS_TRACK)
        for w in words:
            fill = self.ACCENT + (255,) if is_figure(w) else L.PAPER + (alpha,)
            x = L.draw_text(d, x, base, w, face, size, fill, NEWS_TRACK) + space

    def _news_mast(self, c, s: dict, size):
        """The masthead with the day and date beside the lockup, pinned at one place on every slide (it never moves
        as the carousel swipes). (canvas, the top of the room under it)."""
        L = self.L
        y0, _ = band(size)
        mast = L.masthead(clean(s.get("dateline")), True)
        m_top, m_bot = mast.meta["lock"][1], mast.meta["lock"][3]
        top = y0 + 16
        c = self._paint(c, [(self._shift(mast, top - m_top), L.draw_masthead)])
        return c, top + (m_bot - m_top) + 56

    def _news_label(self, eyebrow: str, name: str, title: str, room: float):
        """The atlas card's words beside its picture: eyebrow, name, a full-column rule, title. (height, draw)."""
        L = self.L
        nsize, nrows = 54, [name]
        for sz in range(54, 39, -2):
            nrows, nsize = L.wrap(name, "bold", sz, room, NEWS_TRACK), sz
            if len(nrows) <= 2 and all(L.measure(r, "bold", sz, NEWS_TRACK) <= room for r in nrows):
                break
        nlead = round(nsize * 1.08)
        trows = L.wrap(title.upper(), "medium", 22, room, 0.12) if title else []
        k_base = L.cap_height("bold", 21) if eyebrow else None
        n_base = (k_base + 30 if eyebrow else 0) + L.cap_height("bold", nsize)
        rule_y = n_base + (len(nrows) - 1) * nlead + 30
        t_base = rule_y + 2 + 30 + L.cap_height("medium", 22)
        height = (t_base + (len(trows) - 1) * 34 + L.descent("medium", 22)) if trows else rule_y + 2

        def draw(d, x: float, top: float) -> None:
            if eyebrow:
                L.draw_text(d, x, top + k_base, eyebrow.upper(), "bold", 21, L.PAPER + (175,), 0.20)
            for i, row in enumerate(nrows):
                L.draw_text(d, x, top + n_base + i * nlead, row, "bold", nsize, L.PAPER + (255,), NEWS_TRACK)
            d.rectangle([int(x), int(top + rule_y), int(L.X_R), int(top + rule_y) + 1], fill=L.PAPER + (230,))
            for i, row in enumerate(trows):
                L.draw_text(d, x, top + t_base + i * 34, row, "medium", 22, L.PAPER + (235,), 0.12)
        return height, draw

    def _news_picture(self, pic: dict, side: int = 440):
        """A portrait (the atlas's 500x650 shape, drawn smaller, never enlarged) or a seal at the left, its words
        beside it; `side` is the portrait's width (the seal's longest side). (height, draw(canvas, top))."""
        from PIL import Image, ImageDraw
        L = self.L
        im = _load(pic["path"])
        if pic.get("kind") == "portrait":
            art = _fit(im, side, round(side * 1.3)).convert("RGBA")
            box_w = side
        else:
            im = im.convert("RGBA")
            k = side / max(im.size)
            art = im.resize((round(im.width * k), round(im.height * k)), resample=Image.LANCZOS)
            box_w = art.width
        col_x = L.X_L + box_w + 48
        col_h, col_draw = self._news_label(clean(pic.get("eyebrow")), clean(pic.get("name")), clean(pic.get("title")),
                                           L.X_R - col_x)
        height = max(art.height, col_h)

        def draw(c, top: float) -> None:
            c.alpha_composite(art, (int(L.X_L), int(round(top + (height - art.height) / 2))))
            col_draw(ImageDraw.Draw(c), col_x, top + (height - col_h) / 2)
        return height, draw

    def _logo(self, src: dict):
        """One outlet's logo: trimmed to its ink; "recolour" turns its black and grey parts bone, "bone" all of it, "keep"
        draws it as it is; sized to the shared ink area, times its own scale."""
        import numpy as np
        from PIL import Image
        im = _load(src["path"]).convert("RGBA")
        box = im.getchannel("A").point(lambda v: 255 if v > 8 else 0).getbbox()
        if box:
            im = im.crop(box)
        how = src.get("how") or "recolour"
        if how in ("recolour", "bone"):
            a = np.asarray(im).astype(np.float32)
            rgb = a[..., :3]
            if how == "bone":
                turn = a[..., 3] > 0
            else:
                lum = rgb @ np.array([0.2126, 0.7152, 0.0722], dtype=np.float32)
                turn = (a[..., 3] > 0) & (lum < 110) & ((rgb.max(axis=2) - rgb.min(axis=2)) < 48)
            rgb[turn] = np.array(self.L.PAPER, dtype=np.float32)
            im = Image.fromarray(np.clip(a, 0, 255).astype(np.uint8), "RGBA")
        ratio = im.width / im.height
        h = min(LOGO_MAX_H, math.sqrt(LOGO_AREA / ratio)) * float(src.get("scale") or 1.0)
        return im.resize((max(1, round(h * ratio)), max(1, round(h))), Image.LANCZOS)

    def _logo_row(self, sources):
        """SOURCES, then each outlet's logo, on one line, flush left; a logo that would pass X_R is left out."""
        from PIL import ImageDraw
        L = self.L
        logos = [self._logo(x) for x in sources or [] if isinstance(x, dict) and x.get("path")]
        height = max([lg.height for lg in logos] + [L.cap_height("medium", 21)])

        def draw(c, top: float) -> None:
            mid = top + height / 2
            x = L.draw_text(ImageDraw.Draw(c), L.X_L, mid + L.cap_height("medium", 21) / 2, "SOURCES", "medium", 21,
                            L.PAPER + (190,), 0.12) + 34
            for lg in logos:
                if x + lg.width > L.X_R:
                    break
                c.alpha_composite(lg, (int(round(x)), int(round(mid - lg.height / 2))))
                x += lg.width + LOGO_GAP
        return height, draw

    def news(self, s: dict, size):
        """Slide 1: the masthead with the date, the picture and its words, the kicker, the headline, one line under it,
        the sources' logos; one block, centred under the masthead. 1080x1350."""
        from PIL import ImageDraw
        L = self.L
        w, h = int(size[0]), int(size[1])
        _, y1 = band(size)
        c, room_top = self._news_mast(self._news_ground(s["picture"], w, h), s, size)
        lrows, lsize, llead = self._news_fit(clean(s.get("line")), "medium", 32, 28, 3, 1.34)
        row_h, row_draw = self._logo_row(s.get("sources"))
        # The picture, then the headline, give way so the sources row keeps slide 2's bottom margin (2026-10-29: a
        # three-row headline ran the logos off the bottom edge): the picture 440 wide down to 340, then the headline
        # from 70 down to 56.
        tries = [(side, 70) for side in range(440, 339, -4)] + [(340, hs) for hs in range(68, 55, -2)]
        for side, hstart in tries:
            hrows, hsize, hlead = self._news_fit(clean(s.get("headline")), "bold", hstart, min(50, hstart), 3, 1.10)
            pic_h, pic_draw = self._news_picture(s["picture"], side)   # the block, from the picture's top (0)
            k_base = pic_h + 72 + L.cap_height("bold", 19)
            h_base = k_base + 34 + L.cap_height("bold", hsize)
            y = h_base + (len(hrows) - 1) * hlead + L.descent("bold", hsize)
            l_base = y + 34 + L.cap_height("medium", lsize)
            y = l_base + (len(lrows) - 1) * llead + L.descent("medium", lsize)
            row_top = y + 64
            if room_top + row_top + row_h <= h - NEWS_ROW_BOTTOM:
                break
        top = max(room_top, min(centred_top(room_top, y1, row_top + row_h), h - NEWS_ROW_BOTTOM - row_top - row_h))
        pic_draw(c, top)
        d = ImageDraw.Draw(c)
        self._news_kick(d, top + k_base, s.get("kicker"))
        for i, row in enumerate(hrows):
            self._words(d, L.X_L, top + h_base + i * hlead, row.split(" "), "bold", hsize, None, NEWS_TRACK)
        for i, row in enumerate(lrows):
            self._news_dim(d, L.X_L, top + l_base + i * llead, row.split(" "), "medium", lsize, 215)
        row_draw(c, top + row_top)
        return c

    def _news_figure(self, hero: dict):
        """THE POLITICS's number as the clips set it (condensed, bone), flush left and as large as the width allows,
        what it counts under it in plain words. (height, draw)."""
        L = self.L
        t = clean(hero.get("figure")).upper()
        size = 230
        while size > 96 and L.measure(t, "cond", size, -0.01) > L.X_R - L.X_L:
            size -= 4
        f_base = L.cap_height("cond", size)
        rows, lsize, llead = self._news_fit(clean(hero.get("label")), "medium", 36, 30, 3, 1.30)
        l_base = f_base + 56 + L.cap_height("medium", lsize)
        height = l_base + (len(rows) - 1) * llead + L.descent("medium", lsize)

        def draw(c, top: float):
            from PIL import ImageDraw
            d = ImageDraw.Draw(c)
            L.draw_text(d, L.X_L - 4, top + f_base, t, "cond", size, self.ACCENT + (255,), -0.01)
            for i, row in enumerate(rows):
                L.draw_text(d, L.X_L, top + l_base + i * llead, row, "medium", lsize, L.PAPER + (235,), NEWS_TRACK)
            return c
        return height, draw

    def _news_quote(self, hero: dict):
        """ON THE RECORD: the words verbatim with the opening mark hung in the margin, the speaker's two tier name
        plate under them, as the clips show a speaker. (height, draw)."""
        L = self.L
        rows, qsize, qlead = self._news_fit("“" + clean(hero.get("quote")) + "”", "medium", 64, 44, 4, 1.18)
        q_base = L.cap_height("medium", qsize)
        q_bottom = q_base + (len(rows) - 1) * qlead + L.descent("medium", qsize)
        plate_bottom = q_bottom + 52 + L.NP_NAME_H + (L.NP_TITLE_H if hero.get("title") else 0)
        hang = L.font("medium", qsize).getlength("“")

        def draw(c, top: float):
            from PIL import ImageDraw
            d = ImageDraw.Draw(c)
            for i, row in enumerate(rows):
                L.draw_text(d, L.X_L - (hang if i == 0 else 0), top + q_base + i * qlead, row, "medium", qsize,
                            L.PAPER + (255,), NEWS_TRACK)
            plate = L.nameplate(clean(hero.get("name")), clean(hero.get("title")), int(round(top + plate_bottom)),
                                L.X_L, L.X_R)
            return self._paint(c, [(plate, L.draw_nameplate)])
        return plate_bottom, draw

    def news_more(self, s: dict, size):
        """Slide 2: one of the clips' own broadcast pieces (THE POLITICS's number, or ON THE RECORD with the name
        plate), the story as one short paragraph, the sources' logos. It must stand alone: Instagram can show a
        carousel again from its second slide. 1080x1350."""
        from PIL import ImageDraw
        L = self.L
        w, h = int(size[0]), int(size[1])
        _, y1 = band(size)
        c, room_top = self._news_mast(self._news_ground(s["picture"], w, h), s, size)
        hero = s.get("hero") or {}
        quote = hero.get("kind") == "quote"
        k_base = L.cap_height("bold", 20)                          # the block, from the kicker's top (0)
        hero_top = k_base + 44
        hero_h, hero_draw = (self._news_quote if quote else self._news_figure)(hero)
        srows, ssize, slead = self._news_fit(clean(s.get("story")), "medium", 36, 30, 8, 1.40)
        s_base = hero_top + hero_h + 72 + L.cap_height("medium", ssize)
        y = s_base + (len(srows) - 1) * slead + L.descent("medium", ssize)
        row_h, row_draw = self._logo_row(s.get("sources"))
        row_top = y + 72
        top = centred_top(room_top, y1, row_top + row_h)
        self._news_kick(ImageDraw.Draw(c), top + k_base, "On the record" if quote else "The Politics")
        c = hero_draw(c, top + hero_top) or c
        d = ImageDraw.Draw(c)
        for i, row in enumerate(srows):
            L.draw_text(d, L.X_L, top + s_base + i * slead, row, "medium", ssize, L.PAPER + (225,), NEWS_TRACK)
        row_draw(c, top + row_top)
        return c

    def draw(self, s: dict, size):
        layout = s.get("layout")
        if layout == "quote":
            return self.quote(s, size)
        if layout in ("docket_cover", "best_cover"):
            return self.docket_cover(s, size)
        if layout in ("docket_item", "best_item"):
            return self.docket_item(s, size)
        if layout == "news":
            return self.news(s, size)
        if layout == "news_more":
            return self.news_more(s, size)
        raise ValueError(f"the Politics has no layout {layout!r}")


# ------------------------------------------------------------------------------------------------------ GTA
# The one-line lockup: its name, rule and tagline share one centre line, so it sits square on a centred card (the
# stacked lockup sets the name flush left over a full-width tagline and reads left of centre).
LOCKUP = "wordmark-oneline.png"


class Gta:
    """A GTA account's own kit (Gaming's is "gta"): its lockup, Impact with an ink outline and an accent shadow, DIN
    Condensed, on a frame of real footage under a flat ink dim, or on the kit's flat ink. Everything is centred on
    the canvas (x 540) and kept inside x 72..1008."""

    def __init__(self, skill_dir: str, kit: str):
        root = Path(skill_dir) / "assets" / "brand"
        folder = root / (kit or "gta")
        if not (folder / "kit.json").is_file():   # never another account's brand on this account's card
            raise ValueError(f"no brand kit {kit or 'gta'!r} in the skill ({folder / 'kit.json'})")
        self.folder = folder
        self.fonts_dir = Path(skill_dir) / "assets" / "fonts"
        self.kit = json.loads((folder / "kit.json").read_text())
        pal = self.kit.get("palette") or {}
        self.ink = tuple(pal.get("ink") or (10, 7, 19))
        self.cream = tuple(pal.get("cream") or (255, 243, 222))
        self.accent = tuple(pal.get("accent") or (22, 224, 200))
        fonts = self.kit.get("fonts") or {}
        self.display = self._chain(fonts.get("display") or [FONTS["impact"]])
        self.ui = self._chain(fonts.get("ui") or [FONTS["din"]])
        self.chip_r = int(((self.kit.get("radius") or {}).get("chip")) or 8)
        self.plate_r = int(((self.kit.get("radius") or {}).get("plate")) or 16)
        self.on_ink = True               # the card's ground is the kit's flat ink (False: a picture under a dim)

    def _chain(self, chain):
        """The first face of a kit chain that is on this machine: (path, face name or None)."""
        for ref in chain:
            path, _, face = str(ref).partition("#")
            p = Path(path) if path.startswith("/") else self.fonts_dir / path
            if p.is_file():
                return (str(p), face or None)
        return (FONTS["impact"], None)

    def font(self, which, size: int):
        from PIL import ImageFont
        path, face = which
        size = max(8, int(round(size)))
        if not face:
            return ImageFont.truetype(path, size)
        if face.isdigit():
            return ImageFont.truetype(path, size, index=int(face))
        for i in range(32):              # a .ttc face is named, never numbered (a macOS update can reorder them)
            try:
                f = ImageFont.truetype(path, size, index=i)
            except OSError:
                break
            if f.getname()[1].lower() == face.lower():
                return f
        return ImageFont.truetype(path, size)

    def brand(self, name: str):
        p = self.folder / name
        return _load(p) if p.is_file() else None

    def base(self, w: int, h: int, still=None, dim: float = 0.0):
        """The card's ground: a frame of real footage cut to w x h under a flat ink layer, or the kit's flat ink. Never
        the kit's card backdrop (a gradient: the house look has none)."""
        from PIL import Image
        if still is None:
            return Image.new("RGBA", (w, h), self.ink + (255,))
        img = _fit(still, w, h).convert("RGBA")
        if dim > 0:
            img.alpha_composite(Image.new("RGBA", (w, h), self.ink + (int(255 * dim),)))
        return img

    @staticmethod
    def room(w: int) -> int:
        """The width type may take: the frame less the symmetric margins."""
        return int(w) - 2 * MARGIN_X

    def type(self, canvas, text: str, font, cx: float, top: float, fill, outline: float, shadow: float,
             track: float = 0.0) -> float:
        """One line: an ink outline grown with MaxFilter, the accent as a hard offset shadow, then the fill; what the
        eye sees of it centred on cx, so it sits square on the frame: all of it over a picture, the fill and the shadow
        on the kit's flat ink (the ink outline does not show there). The line's bottom."""
        from PIL import Image, ImageChops, ImageDraw, ImageFilter
        text = clean(text)
        adv = [font.getlength(ch) + track * font.size for ch in text]
        width = sum(adv) - (track * font.size if text else 0)
        asc, dsc = font.getmetrics()
        pad = int(outline + shadow + 6)
        w, h = int(math.ceil(width)) + 2 * pad, asc + dsc + 2 * pad
        mask = Image.new("L", (w, h), 0)
        md = ImageDraw.Draw(mask)
        x = pad
        for ch, a in zip(text, adv):
            md.text((x, pad), ch, font=font, fill=255)
            x += a
        grow = int(round(outline))
        ring = mask.filter(ImageFilter.MaxFilter(2 * grow + 1)) if grow > 0 else mask
        layer = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        off = int(round(shadow))
        if off > 0:
            sh = Image.new("RGBA", (w - off, h - off), self.accent + (255,))
            layer.paste(sh, (off, off), ring.crop((0, 0, w - off, h - off)))
        layer.paste(Image.new("RGBA", (w, h), self.ink + (255,)), (0, 0), ring)
        layer.paste(Image.new("RGBA", (w, h), tuple(fill)[:3] + (255,)), (0, 0), mask)
        seen = layer.getchannel("A")
        if self.on_ink:
            cast = Image.new("L", (w, h), 0)
            if off > 0:
                cast.paste(ring.crop((0, 0, w - off, h - off)), (off, off))
            seen = ImageChops.lighter(mask, cast)
        ink = seen.getbbox() or (0, 0, w, h)
        canvas.alpha_composite(layer, (int(round(cx - (ink[0] + ink[2]) / 2)), int(round(top - pad))))
        return top + asc + dsc

    def fit_size(self, which, text: str, room: float, start: int, low: int) -> int:
        size = start
        while size > low and self.font(which, size).getlength(clean(text)) > room:
            size -= 4
        return size

    def rows(self, which, text: str, size: int, room: float, most: int):
        """Greedy wrap into at most `most` rows, shrinking the size until it fits."""
        words = clean(text).upper().split()
        while True:
            f = self.font(which, size)
            rows, cur = [], ""
            for w in words:
                t = f"{cur} {w}".strip()
                if cur and f.getlength(t) > room:
                    rows.append(cur)
                    cur = w
                else:
                    cur = t
            if cur:
                rows.append(cur)
            if len(rows) <= most or size <= 40:
                return rows[:most], size
            size -= 6

    def chip(self, canvas, text: str, cx: float, y: float, size: int = 40) -> float:
        """A label on an accent chip (radius from the kit), centred on cx. Its bottom."""
        from PIL import ImageDraw
        f = self.font(self.ui, size)
        t = clean(text).upper()
        w = int(f.getlength(t)) + 36
        h = int(size * 1.25)
        x = int(round(cx - w / 2))
        d = ImageDraw.Draw(canvas)
        d.rounded_rectangle([x, int(y), x + w, int(y) + h], radius=self.chip_r, fill=self.accent + (255,))
        d.text((x + w / 2, y + h / 2), t, font=f, fill=self.ink + (255,), anchor="mm")
        return y + h

    def wordmark(self, canvas, name: str, cx: float, top: float, width: int) -> float:
        """The kit's lockup (its own transparent PNG, cut to its ink so it centres), width px wide. Its bottom."""
        im = self.brand(name)
        if im is None:
            return top
        im = im.convert("RGBA")
        box = im.getchannel("A").getbbox()
        if box:
            im = im.crop(box)
        hh = int(round(im.height * width / im.width))
        im = im.resize((width, hh), resample=3)
        canvas.alpha_composite(im, (int(round(cx - width / 2)), int(round(top))))
        return top + hh

    def text(self, canvas, t: str, size: int, cx: float, top: float, colour) -> float:
        """A line of the kit's small type (DIN Condensed), its ink centred on cx. Its bottom."""
        from PIL import ImageDraw
        t = clean(t).upper()
        if not t:
            return top
        f = self.font(self.ui, size)
        x0, _, x1, _ = f.getbbox(t)
        ImageDraw.Draw(canvas).text((cx - (x0 + x1) / 2, top), t, font=f, fill=tuple(colour)[:3] + (255,))
        return top + size

    def plate(self, canvas, still, cx: float, top: float, max_w: int, max_h: int) -> float:
        """The frame on a plate with the kit's radius and an ink edge, in its own shape (a 16:9 source stays 16:9),
        centred on cx. Its bottom."""
        from PIL import Image, ImageDraw
        sw, sh = still.size
        k = min(max_w / sw, max_h / sh)
        pw, ph = int(round(sw * k)), int(round(sh * k))
        pic = _fit(still, pw, ph).convert("RGBA")
        r = self.plate_r
        mask = Image.new("L", (pw, ph), 0)
        ImageDraw.Draw(mask).rounded_rectangle([0, 0, pw - 1, ph - 1], radius=r, fill=255)
        edge = Image.new("RGBA", (pw + 12, ph + 12), (0, 0, 0, 0))
        ImageDraw.Draw(edge).rounded_rectangle([0, 0, pw + 11, ph + 11], radius=r + 6, fill=self.ink + (255,))
        x = int(round(cx - pw / 2))
        canvas.alpha_composite(edge, (x - 6, int(round(top)) - 6))
        canvas.paste(pic, (x, int(round(top))), mask)
        return top + ph

    def block(self, canvas, layer, y0: float, y1: float) -> None:
        """Put what was drawn on `layer` centred between y0 and y1 (by its ink)."""
        box = layer.getchannel("A").getbbox()
        if not box:
            return
        part = layer.crop((0, box[1], layer.width, box[3]))
        canvas.alpha_composite(part, (0, int(round(centred_top(y0, y1, part.height)))))

    def _layer(self, w: int, h: int):
        from PIL import Image
        return Image.new("RGBA", (w, h * 2), (0, 0, 0, 0))

    def countdown(self, s: dict, size):
        """N DAYS to the launch (OUT TODAY on the day) in the account's own look alone: the lockup on the kit's flat
        ink. Never a frame of anyone's footage, even when a spec hands one over: the countdown is not about one clip
        (and a creator's frame can carry a countdown of its own)."""
        w, h = int(size[0]), int(size[1])
        c = self.base(w, h)
        self.on_ink = True
        cx, room = w / 2, self.room(w)
        lay = self._layer(w, h)
        y = self.wordmark(lay, LOCKUP, cx, 0, 600) + 64
        launch = self.kit.get("launch") or {}
        days = s.get("days")
        if isinstance(days, int) and days > 0:
            big = str(days)
            n = self.fit_size(self.display, big, room, 400, 160)
            y = self.type(lay, big, self.font(self.display, n), cx, y, self.cream, n * 0.062, n * 0.058) - n * 0.10
            label = clean(s.get("label") or launch.get("label") or "DAYS TO GTA 6").upper()
            if days == 1:
                label = re.sub(r"\bDAYS\b", "DAY", label)
            ls = self.fit_size(self.display, label, room, 112, 56)
            y = self.type(lay, label, self.font(self.display, ls), cx, y, self.cream, ls * 0.062, ls * 0.058)
        else:
            for line in ("OUT", "TODAY"):
                n = self.fit_size(self.display, line, room, 300, 120)
                y = self.type(lay, line, self.font(self.display, n), cx, y, self.cream, n * 0.062,
                              n * 0.058) - n * 0.06
        y = self.text(lay, s.get("date"), 50, cx, y + 34, self.cream)
        self.text(lay, s.get("platforms") or launch.get("platforms"), 40, cx, y + 16, self.accent)
        self.block(c, lay, *band(size))
        return c

    def clip_of_day(self, s: dict, size):
        """The label chip, a clean frame of the clip's own source on a plate, the clip's own title, the creator's
        credit. With no clean frame: the lockup, the chip, the title and the credit on the kit's flat ink (never a
        frame of our own clip: it carries our captions and hook)."""
        w, h = int(size[0]), int(size[1])
        c = self.base(w, h)
        self.on_ink = True
        cx, room = w / 2, self.room(w)
        lay = self._layer(w, h)
        still = _load(s["still"]) if s.get("still") else None
        y = 0.0
        if still is None:
            y = self.wordmark(lay, LOCKUP, cx, y, 600) + 80
        y = self.chip(lay, s.get("label") or "Clip of the day", cx, y) + 40
        if still is not None:
            y = self.plate(lay, still, cx, y + 6, room, 720) + 6 + 48
        rows, sz = self.rows(self.display, s.get("hook") or "", 104 if still is not None else 118, room - 24, 3)
        for r in rows:
            self.type(lay, r, self.font(self.display, sz), cx, y, self.cream, sz * 0.062, sz * 0.058)
            y += sz * 1.06
        self.text(lay, s.get("credit"), 52, cx, y + 44, self.accent)
        self.block(c, lay, *band(size))
        return c

    def best_cover(self, s: dict, size):
        w, h = int(size[0]), int(size[1])
        c = self.base(w, h)
        self.on_ink = True
        cx, room = w / 2, self.room(w) - 36
        lay = self._layer(w, h)
        y = self.wordmark(lay, LOCKUP, cx, 0, 560) + 64
        for line in (s.get("label") or "THIS WEEK'S", s.get("figure") or "TOP 5"):
            t = clean(line).upper()
            n = self.fit_size(self.display, t, room, 190, 80)
            y = self.type(lay, t, self.font(self.display, n), cx, y, self.cream, n * 0.062, n * 0.058) + 6
        self.text(lay, s.get("date"), 46, cx, y + 30, self.accent)
        self.block(c, lay, *band(size))
        return c

    def best_item(self, s: dict, size):
        """One of the week's best on the kit's flat ink: its rank, its title, the creator's credit, its views."""
        w, h = int(size[0]), int(size[1])
        c = self.base(w, h)
        self.on_ink = True
        cx, room = w / 2, self.room(w) - 36
        lay = self._layer(w, h)
        y = 0.0
        rank = clean(s.get("rank"))
        if rank:
            n = 220
            y = self.type(lay, rank, self.font(self.display, n), cx, y, self.accent, n * 0.05, 0) + 24
        rows, sz = self.rows(self.display, s.get("title") or "", 100, room, 4)
        for r in rows:
            self.type(lay, r, self.font(self.display, sz), cx, y, self.cream, sz * 0.062, sz * 0.058)
            y += sz * 1.06
        y = self.text(lay, s.get("credit"), 50, cx, y + 40, self.accent)
        self.text(lay, s.get("figure"), 58, cx, y + 22, self.cream)
        self.block(c, lay, *band(size))
        return c

    def draw(self, s: dict, size):
        layout = s.get("layout")
        f = {"countdown": self.countdown, "clip_of_day": self.clip_of_day, "best_cover": self.best_cover,
             "best_item": self.best_item, "docket_cover": self.best_cover, "docket_item": self.best_item}.get(layout)
        if f is None:
            raise ValueError(f"GTA has no layout {layout!r}")
        return f(s, size)


# ------------------------------------------------------------------------------------------------------ the run
def render(spec: dict) -> dict:
    size = spec.get("size") or [1080, 1920]
    slides = spec.get("slides") or []
    outs = spec.get("outputs") or []
    if not slides or len(outs) != len(slides):
        raise ValueError("the spec needs one output per slide")
    look = spec.get("look")
    drawer = Politics(spec["skill_dir"]) if look == "politics" else Gta(spec["skill_dir"], spec.get("kit") or "gta")
    files = []
    for s, out in zip(slides, outs):
        img = drawer.draw(s, size)
        files.append(_save(img, out, size))
    return {"ok": True, "engine": ENGINE, "files": files}


def selftest(argv) -> dict:
    """Can this Python draw cards here: Pillow, fontTools and numpy import, the brand fonts are there, and (with
    --skill) the Politics look imports."""
    out = {"ok": True, "engine": ENGINE, "python": sys.version.split()[0]}
    try:
        import PIL  # noqa: F401
        import fontTools  # noqa: F401
        import numpy  # noqa: F401
        out["pil"] = PIL.__version__
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": f"cannot import Pillow, fontTools or numpy: {type(e).__name__}: {e}"}
    missing = [p for p in FONTS.values() if not os.path.exists(p)]
    if missing:
        return {"ok": False, "error": "fonts missing: " + ", ".join(missing)}
    if "--skill" in argv:
        i = argv.index("--skill")
        if i + 1 < len(argv):
            try:
                Politics(argv[i + 1])
                out["politicslook"] = True
            except Exception as e:  # noqa: BLE001
                return {"ok": False, "error": f"the Politics look will not load: {type(e).__name__}: {e}"}
    return out


def main(argv) -> int:
    try:
        if "--selftest" in argv:
            res = selftest(argv)
        else:
            if not argv:
                raise ValueError("usage: card_engine.py <spec.json> | --selftest")
            res = render(json.loads(Path(argv[-1]).read_text()))
    except Exception as e:  # noqa: BLE001  (one line the factory can read, never a traceback)
        res = {"ok": False, "error": f"{type(e).__name__}: {e}"[:600]}
    print(json.dumps(res))
    return 0 if res.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
