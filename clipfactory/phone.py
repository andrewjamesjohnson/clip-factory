"""Andrew's phone (2026-10-29): a real push with the picture and Yes / No buttons, sent by the factory itself.

Why: the breaking desk asked with Claude's PushNotification, and that never reached the phone. The tool skips
the push whenever the Mac's app counts as "active" ("Not sent - this terminal is active", 07:34 and 08:34 on
10-29), and scheduled task sessions never join Remote Control. So the Epstein ask sat on the Mac all morning.

How: ntfy (ntfy.sh, free, no account). The free ntfy app on the iPhone subscribes to the ASK topic. Each ask is
one push: the title, the line, the card as the picture, and two buttons. A button posts "<verb> <args>" to the
REPLY topic; the launchd job `phone poll` (every minute) reads the replies and records the answer
(`breaking yes` / `breaking pass`), and the breaking desk's next run (every 30 minutes) cuts on a yes.

The topics are long random names kept in state/phone.json (made on first use; "on": false stops every push).
Only headlines and the card go out, nothing private.
"""
from __future__ import annotations

import base64
import json
import os
import secrets
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

from . import config as C
from . import store

SERVER = "https://ntfy.sh"
TIMEOUT = 20


def path() -> Path:
    return C.STATE / "phone.json"


def settings(make: bool = False) -> dict:
    """state/phone.json: {"on", "server", "topic", "reply", "since"}; make=True writes new random topics."""
    p = path()
    if p.exists():
        return json.loads(p.read_text())
    if not make:
        return {}
    d = {"on": True, "server": SERVER, "topic": "politics-ask-" + secrets.token_hex(10),
         "reply": "politics-reply-" + secrets.token_hex(10), "since": ""}
    with store.edit(p, dict) as s:
        s.update(d)
    return d


def _h(v: str) -> str:
    """A header value: plain one-line ASCII as it is, anything else RFC 2047 (ntfy reads both). A line break raw in
    a header makes urllib refuse the whole push (2026-10-29: the breaking ask's three lines never reached the phone)."""
    try:
        v.encode("ascii")
        if "\r" not in v and "\n" not in v:
            return v
    except UnicodeEncodeError:
        pass
    return "=?UTF-8?B?" + base64.b64encode(v.encode()).decode() + "?="


def _q(v: str) -> str:
    """One button value in quotes. ntfy splits the Actions header on commas, so a bare "Yes, post it" made the label
    "Yes" and the URL "post it", and ntfy refused the whole push (400 "actions invalid": every breaking and news ask
    on 10-30 never reached the phone). ntfy takes double or single quotes."""
    return f"'{v}'" if '"' in v else f'"{v}"'


def _actions(s: dict, buttons: List[Tuple[str, str]]) -> str:
    url = f"{s['server']}/{s['reply']}"
    return "; ".join(f"http, {_q(label)}, {url}, method=POST, body={_q(body)}, clear=true" for label, body in buttons)


def send(title: str, message: str, image: Optional[Path] = None,
         buttons: Optional[List[Tuple[str, str]]] = None, tags: str = "", priority: str = "high") -> Tuple[bool, str]:
    """One push to the phone. Never raises: (False, why) when it did not go."""
    if os.environ.get("CLIP_FACTORY_OFFLINE"):
        return False, "offline"
    s = settings()
    if not s or not s.get("on"):
        return False, "phone pushes are off (state/phone.json)"
    headers = {"Title": _h(title), "Priority": priority}
    if tags:
        headers["Tags"] = tags
    if buttons:
        headers["Actions"] = _h(_actions(s, buttons))
    url = f"{s['server']}/{s['topic']}"
    if image and Path(image).exists():
        headers.update(Message=_h(message), Filename=Path(image).name)
        req = urllib.request.Request(url, data=Path(image).read_bytes(), headers=headers, method="PUT")
    else:
        req = urllib.request.Request(url, data=message.encode(), headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            return True, json.loads(r.read() or b"{}").get("id", "sent")
    except Exception as e:  # noqa: BLE001  (a push that fails must never stop the desk)
        return False, f"{type(e).__name__}: {e}"


def replies(s: dict) -> List[dict]:
    """The REPLY topic's messages since the last read (12 hours back the first time)."""
    since = s.get("since") or "12h"
    url = f"{s['server']}/{s['reply']}/json?poll=1&since={urllib.parse.quote(since)}"
    with urllib.request.urlopen(url, timeout=TIMEOUT) as r:
        rows = [json.loads(ln) for ln in r.read().decode().splitlines() if ln.strip()]
    return [m for m in rows if m.get("event") == "message"]


def poll(handlers: Dict[str, Callable[[List[str]], List[str]]]) -> List[str]:
    """Read the button presses and hand each to its verb's handler ("breaking" -> its args). The read point moves
    only past messages handled, so a crash reads them again."""
    if os.environ.get("CLIP_FACTORY_OFFLINE"):
        return []
    s = settings()
    if not s or not s.get("on"):
        return []
    out: List[str] = []
    for m in replies(s):
        words = (m.get("message") or "").split()
        if words and words[0] in handlers:
            out += handlers[words[0]](words[1:])
        else:
            out.append(f"  phone: skipped {m.get('message')!r}")
        with store.edit(path(), dict) as d:
            d["since"] = m["id"]
    return out
