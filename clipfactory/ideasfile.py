"""The account's idea politics, read by code (the owner, 2026-10-31: "make sure everything ... is optimal, set up"). The
loops thesis item 8 put one IDEAS.md in each account's folder (~/Desktop/CLIP FACTORY/<Account>/IDEAS.md) and asked the
night to read it first; its runs since never did (`factory.py path` counted 0 reads in 3 runs). Google's harness course:
rules are enforced by code, not by asking the model. So `shift begin`, which every night run calls, prints the account's
DEAD ideas with their "only retry if". Reading never fails a caller: no file, no lines.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import List, Optional, Tuple

ROOT_ENV = "CLIP_FACTORY_IDEAS_ROOT"     # tests point it at a throwaway folder
MOST = 12


def root() -> Optional[Path]:
    """The account folders' home; None in a test run (CLIP_FACTORY_OFFLINE) that named no folder of its own."""
    if os.environ.get(ROOT_ENV):
        return Path(os.environ[ROOT_ENV])
    if os.environ.get("CLIP_FACTORY_OFFLINE") == "1":     # a test never reads the real Desktop
        return None
    return Path.home() / "Desktop" / "CLIP FACTORY"


def path(acct) -> Optional[Path]:
    r = root()
    return r / str(getattr(acct, "name", acct)) / "IDEAS.md" if r else None


def dead(acct) -> List[Tuple[str, str]]:
    """(idea, only retry if) for each row of the DEAD table."""
    p = path(acct)
    try:
        text = p.read_text() if p else ""
    except OSError:
        return []
    out, inside = [], False
    for ln in text.splitlines():
        if ln.startswith("## "):
            inside = ln.strip().upper() == "## DEAD"
            continue
        if not inside or not ln.startswith("|"):
            continue
        cells = [c.strip() for c in ln.strip().strip("|").split("|")]
        if len(cells) < 3 or cells[0] in ("Idea", "") or set(cells[0]) <= set("-: "):
            continue
        out.append((cells[0], cells[2]))
    return out


def lines(acct, most: int = MOST) -> List[str]:
    rows = dead(acct)
    if not rows:
        return []
    out = [f"  ideas already tried ({path(acct).parent.name}/IDEAS.md): never build on one of these unless its retry "
           f"rule is met"]
    out += [f"    - {idea} (only retry if: {retry or 'never'})" for idea, retry in rows[:most]]
    if len(rows) > most:
        out.append(f"    ... and {len(rows) - most} more in the file")
    return out


def has(acct) -> bool:
    p = path(acct)
    return bool(p and p.is_file())
