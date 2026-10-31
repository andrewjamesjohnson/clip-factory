import os, tempfile; os.environ["CLIP_FACTORY_OFFLINE"] = "1"; os.environ.setdefault("CLIP_FACTORY_STATE", tempfile.mkdtemp())  # noqa: E401,E702
# The page kit (socialui.py) on phones: a row that scrolls sideways with no scrollbar (the tab's views, the Inbox
# filters) brings its current item into view, and a kit button whose label wraps grows instead of spilling.
# Review fixes 2026-10-20. Pure strings plus one node run; nothing reaches the network.

import json
import shutil
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from clipfactory import engagepage as EP   # noqa: E402
from clipfactory import socialui as SX     # noqa: E402


def _phone(css: str) -> str:
    return css.split("@media (max-width:600px){", 1)[1]


def _js_fn(js: str, name: str) -> str:
    """The text of `function name(...) {...}` out of a script, by brace count."""
    i = js.index(f"function {name}(")
    depth, j = 0, js.index("{", i)
    while True:
        c = js[j]
        depth += c == "{"
        depth -= c == "}"
        j += 1
        if depth == 0:
            return js[i:j]


class PhoneButtons(unittest.TestCase):
    def test_a_wrapped_label_grows_the_button(self):
        # 280 px: 'Test on the last 7 days' wrapped to two lines inside a fixed 40 px box, so 'days' hung below it
        phone = _phone(SX.CSS)
        self.assertIn("body.sx button:not(.sx-row){height:auto;min-height:40px;min-width:40px;font-size:16px}", phone)
        self.assertNotIn("body.sx button:not(.sx-row),body.sx select{height:40px", SX.CSS)
        self.assertIn("body.sx select{height:40px;min-width:40px;font-size:16px}", phone)   # a select never wraps
        self.assertIn("body.sx .seg button{height:40px}", phone)                            # one-line segments stay put


class SidewaysRows(unittest.TestCase):
    def test_the_kit_reveals_the_current_view_and_the_pressed_filter(self):
        js = SX.JS
        self.assertIn("reveal,", js[js.index("window.SX = {"):])
        phone = js[js.index("if (window.matchMedia('(max-width:600px)').matches) {"):js.index("window.SX = {")]
        self.assertIn("document.querySelectorAll('.sx-subnav').forEach(reveal);", phone)
        self.assertIn("reveal(root);", _js_fn(js, "seg"))
        # the Inbox filter row is rebuilt on every draw (data refresh, the minute timer), so it reveals every time
        bar = _js_fn(EP.JS, "drawBar")
        self.assertLess(bar.index("bar.replaceChildren(seg, chips);"), bar.index("SX.reveal(seg);"))
        self.assertIn("SX.reveal(bar.querySelector('.seg'));", _js_fn(EP.JS, "back"))

    def test_reveal_centres_the_current_item_sideways_only(self):
        node = shutil.which("node")
        if not node:
            self.skipTest("node is not installed")
        script = _js_fn(SX.JS, "reveal") + r"""
const box = (left, width) => ({left, width});
function row(left, cw, sw, item) {
  return {scrollLeft: 0, clientWidth: cw, scrollWidth: sw, asked: '',
    getBoundingClientRect() { return box(left, cw); },
    querySelector(sel) { this.asked = sel; return item; }};
}
const out = {};
// /inbox?filter=hide at 280 px: row 17..263, 'Suggested hide' at 445..597
let r = row(17, 246, 714, {getBoundingClientRect() { return box(445, 152); }});
reveal(r); out.hide = r.scrollLeft; out.sel = r.asked;
// /reports/ideas at 375 px: row 0..375, 'Ideas' at 405..460
r = row(0, 375, 476, {getBoundingClientRect() { return box(405, 55); }});
reveal(r); out.ideas = r.scrollLeft;
r = row(0, 375, 375, {getBoundingClientRect() { return box(405, 55); }});
reveal(r); out.fits = r.scrollLeft;
r = row(0, 375, 476, null);
reveal(r); out.none = r.scrollLeft;
reveal(null); reveal(undefined);
console.log(JSON.stringify(out));
"""
        with tempfile.TemporaryDirectory() as d:
            f = Path(d) / "reveal.js"
            f.write_text(script)
            p = subprocess.run([node, str(f)], capture_output=True, text=True)
        self.assertEqual(p.returncode, 0, p.stderr)
        out = json.loads(p.stdout)
        self.assertEqual(out["hide"], (445 + 76) - (17 + 123))    # its middle lands on the row's middle
        self.assertEqual(out["ideas"], (405 + 27.5) - 187.5)
        self.assertEqual(out["sel"], '[aria-current],[aria-pressed="true"]')
        self.assertEqual(out["fits"], 0)                          # a row that fits is left alone
        self.assertEqual(out["none"], 0)                          # nothing current, nothing moves
        self.assertNotIn("scrollIntoView", _js_fn(SX.JS, "reveal"))   # the page never jumps on a redraw


if __name__ == "__main__":
    unittest.main()
