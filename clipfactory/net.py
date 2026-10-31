"""One gate in front of every call that reaches a real account (upload, post, sign-in, token refresh).

It refuses when CLIP_FACTORY_OFFLINE=1 (every test module sets it) or when the process is a test run at all
(`unittest` is loaded: the factory itself never imports it). On 2026-10-16 two test runs uploaded 2-second dummies to
the real YouTube channels before the tests were cut off from the keys; the broken entries sat in Studio as
"still copying" and used up the new Politics channel's daily upload cap. A key in the wrong place must not be enough.

It also refuses any run whose state is not the live one (~/clip-factory/state). The keys are the same for every copy
(~/.config/clip-factory), so a clone of the boards or a scratch copy of the code would post today's slots a second
time on the real accounts (2026-10-17 audit). A deliberate CLIP_FACTORY_ALLOW_NETWORK_FROM_COPY=1 lets a copy through.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path


class Offline(RuntimeError):
    pass


def _live_state() -> Path:
    """The installed factory's own state folder: the only boards that may reach a real account."""
    from . import livestate
    return livestate._home() / "clip-factory" / "state"


def on_live_state() -> bool:
    """Is this run's state (config.STATE) the installed factory's live folder itself (not a clone, not a copy's)?"""
    from . import config as C
    live, here = _live_state(), Path(C.STATE)
    try:
        if os.path.realpath(here).casefold() == os.path.realpath(live).casefold():
            return True
        a, b = os.stat(here), os.stat(live)
        return (a.st_dev, a.st_ino) == (b.st_dev, b.st_ino)
    except (OSError, ValueError):
        return False


def why_not() -> str:
    """'' when this run may reach a real account, else a few words on why not."""
    if os.environ.get("CLIP_FACTORY_OFFLINE") == "1":
        return "offline"
    if "unittest" in sys.modules and os.environ.get("CLIP_FACTORY_ALLOW_NETWORK_IN_TESTS") != "1":
        return "a test run"
    if os.environ.get("CLIP_FACTORY_ALLOW_NETWORK_FROM_COPY") != "1" and not on_live_state():
        return "not the live state, a copy"
    return ""


def allowed() -> bool:
    return not why_not()


def guard(what: str) -> None:
    why = why_not()
    if why.startswith("not the live"):
        raise Offline(f"refused: {what} would reach a real account from a copy of the state ({_here()}); only "
                      f"{_live_state()} posts (CLIP_FACTORY_ALLOW_NETWORK_FROM_COPY=1 lets a copy through on purpose)")
    if why:
        raise Offline(f"refused: {what} would reach a real account from a test or an offline run")


def _here() -> str:
    from . import config as C
    return str(C.STATE)
