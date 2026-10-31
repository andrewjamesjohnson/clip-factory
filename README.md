# The Clip Factory

A fully agentic media operation and recursive self improvement loop against platform algorithms. Four accounts post via their own developer apps, an evaluator agent reads the results, and the next clips adapt. I am the only human in the loop: I watch each clip and approve it.

## The loop

```
 scouts ──► night shift ──► review page ──► my approval ──► official APIs ──► results
   ▲        (cut, title,     (every clip,     (phone or       (YouTube, Facebook,     │
   │         cover, QA)       one page)        desk)           Instagram, TikTok)     │
   │                                                                                  │
   └──── coach + judge read the numbers ◄──── change ledger + run journal ◄───────────┘
```

Every night the loop runs by itself. Every change to it is written down, replayed against real past nights, and kept only if it helps.

## What it learned

- Facebook gives a Page this size **six Reels a day**. The first two draw 94% of the views, so the best clip leads.
- A **daily cap per platform**, set from the account's own numbers, not a guess.
- **Hooks** that open on the clip's own punch line beat number cards.

## How it is built

| Part | What it does |
|---|---|
| `factory.py` | One command line for everything: `scout`, `prep`, `review`, `post-due`, `breaking`, `replay`, `usage`, `doctor` |
| `clipfactory/` | The engine: scouts, makers, poster, coach, judge, timers, the review page |
| `clipfactory/graph.py`, `poster.py`, `direct.py` | Meta Graph (Facebook + Instagram Reels), YouTube Data API, TikTok Content Posting API. Official APIs only, no paid scheduler |
| `render/card_engine.py` | Covers and cards, drawn in code |
| `prompts/` | The agents' standing orders: coach, fixer, news desk, weekly report |
| `tests/` | 1,641 tests, every outside call faked |

## Rules it keeps

- Nothing posts that I have not approved, or that changed after I saw it.
- A post that may have gone out is looked up, never sent twice.
- It watches its own AI budget and stands down before it runs out.
- Every live change has an undo.

## Not in this repo

The accounts' real setup, keys, the creator roster, and the cutting engine. The account names are replaced with plain ones (politics, gaming, stunts, cars). Tests that need the private files are skipped.

```bash
python3 -m pytest -q
```

---

All rights reserved. Shared to show my work.
