"""Move one account's state to another Mac (the Mac Mini): factory.py social export and social import.

    factory.py social export <account> --to DIR          copy state/<account>/ and its stats series into DIR
    factory.py social import DIR [--replace]             check every file, then put them in this machine's state/
                                                         (--from DIR works too when this is called directly)

Export copies every file under state/<account>/ (boards, the outward log and politics, the inbox store, cards, coach,
learn, the switches and settings), plus state/stats/series/<account>__* and state/stats/pulled/<account>__*, into an
empty folder, with manifest.json: {"v": 1, "account", "files": {path: sha256}, "at", "machine"}. Lock files and
half written .tmp files are left out (they belong to the running machine). Sign-ins never move this way: they live in
~/.config/clip-factory and are connected again on the new machine. Export refuses while the account is switched on
here: its queue and politics would keep moving after the copy, and the other Mac would send the same thing again.

Import checks every file against the manifest first, and writes nothing if one differs or is missing. It refuses
while the account is switched on here (accounts.json "active" and "machine": this machine already runs it), and when
the account already has state here, unless --replace: then every file is first copied into a stage and checked,
and only then is the state that is here moved (never deleted) into state/config-backups/move-<account>-<stamp>/ and
the stage put in its place; a swap that fails puts the old state back. No network is used at any step.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Dict, List, Optional, Tuple

from . import config as C

MANIFEST = "manifest.json"
SKIP_SUFFIXES = (".lock", ".tmp")
STATS = ("series", "pulled")


def _sha(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _is_client(acct) -> bool:
    raw = getattr(acct, "raw", None)
    return isinstance(raw, dict) and str(raw.get("owner") or "self") == "client"


def _account(cfg: C.Config, slug: str) -> C.Account:
    a = cfg.account(slug)
    if _is_client(a):
        raise SystemExit(f"{a.name} is a client's account: social export and import are for your own accounts")
    return a


def _folder(raw: str, what: str) -> Path:
    p = Path(os.path.expanduser(str(raw or "")))
    if not str(raw or "").strip() or not p.is_absolute():
        raise SystemExit(f"{what}: a full path, such as ~/Desktop/move-gaming (a short one depends on the folder "
                         f"you are in)")
    return p


def _inside(p: Path, top: Path) -> bool:
    a, b = os.path.realpath(p), os.path.realpath(top)
    return a == b or a.startswith(b.rstrip(os.sep) + os.sep)


def _skip(p: Path) -> bool:
    return p.name.endswith(SKIP_SUFFIXES) or p.name == ".DS_Store"


def files_of(slug: str) -> List[Tuple[str, Path]]:
    """(path in the move, file here) for every file that moves with the account."""
    state = Path(C.STATE)
    out: List[Tuple[str, Path]] = []
    root = state / slug
    if root.is_dir():
        for dirpath, dirnames, names in os.walk(root, followlinks=False):
            dirnames.sort()
            for n in sorted(names):
                f = Path(dirpath) / n
                if f.is_symlink() or not f.is_file() or _skip(f):
                    continue
                out.append((f.relative_to(state).as_posix(), f))
    for sub in STATS:
        d = state / "stats" / sub
        if d.is_dir():
            for f in sorted(d.glob(f"{slug}__*")):
                if f.is_file() and not f.is_symlink() and not _skip(f):
                    out.append((f.relative_to(state).as_posix(), f))
    return out


def _safe_rel(rel: str, slug: str) -> PurePosixPath:
    """A path from a manifest, only where an account's files belong: state/<slug>/..., stats/series|pulled/<slug>__*."""
    p = PurePosixPath(str(rel))
    if p.is_absolute() or ".." in p.parts or not p.parts:
        raise SystemExit(f"the manifest names {rel!r}: not a path inside the move")
    if p.parts[0] == slug and len(p.parts) > 1:
        return p
    if len(p.parts) == 3 and p.parts[0] == "stats" and p.parts[1] in STATS and p.parts[2].startswith(slug + "__"):
        return p
    raise SystemExit(f"the manifest names {rel!r}: not one of {slug}'s files")


# ------------------------------------------------------------------------------------------------------ export
def export(cfg: C.Config, slug: str, to: Path, now: Optional[datetime] = None) -> List[str]:
    """Copy the account's files into the empty folder `to`, with manifest.json. What it says."""
    a = _account(cfg, slug)
    if a.slug in cfg.accounts:
        raise SystemExit(f"{a.name} is switched on here: export only once this machine has stopped running it, or "
                         f"its queue and log move while they still change and the other Mac sends the same thing "
                         f'again. Set "active": false for it in config/accounts.json first (step 1 of the move), '
                         f"then export")
    if _inside(to, Path(C.STATE)):
        raise SystemExit(f"{to}: inside the state folder; export to a folder outside it (a USB drive, the Desktop)")
    if to.exists() and (not to.is_dir() or any(to.iterdir())):
        raise SystemExit(f"{to} is not empty: export into a new or empty folder")
    files = files_of(a.slug)
    if not files:
        raise SystemExit(f"{a.name}: no state here to export (state/{a.slug}/ is empty)")
    to.mkdir(parents=True, exist_ok=True)
    manifest: Dict[str, str] = {}
    size = 0
    for rel, src in files:
        dest = to / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dest)
        sha = _sha(dest)
        if sha != _sha(src):                    # it changed while it was copied: copy it once more
            shutil.copy2(src, dest)
            sha = _sha(dest)
        manifest[rel] = sha
        size += dest.stat().st_size
    t = (now or datetime.now(timezone.utc)).astimezone(timezone.utc).replace(microsecond=0)
    body = {"v": 1, "account": a.slug, "name": a.name, "files": manifest, "at": t.isoformat(),
            "machine": cfg.machine}
    (to / MANIFEST).write_text(json.dumps(body, indent=1, sort_keys=True))
    return [f"Exported {a.name}: {len(manifest)} files ({size / 1e6:.1f} MB) to {to}",
            f"  {MANIFEST} lists each file's sha256; on the other Mac: python3 factory.py social import <that folder> "
            f"(add --replace when it already has state for {a.slug})"]


# ------------------------------------------------------------------------------------------------------ import
def _read_manifest(src: Path) -> dict:
    p = src / MANIFEST
    try:
        m = json.loads(p.read_text())
    except (OSError, ValueError):
        raise SystemExit(f"{src}: no readable {MANIFEST} (export with factory.py social export <account> --to DIR)")
    if not isinstance(m, dict) or not isinstance(m.get("files"), dict) or not m.get("account"):
        raise SystemExit(f"{p}: not an export's manifest")
    return m


def verify(src: Path) -> Tuple[dict, List[str]]:
    """The manifest and what is wrong with the folder against it ([] when every file is there with its sha256)."""
    m = _read_manifest(src)
    slug = str(m["account"])
    bad: List[str] = []
    for rel, sha in sorted(m["files"].items()):
        _safe_rel(rel, slug)
        f = src / rel
        if f.is_symlink() or not f.is_file():
            bad.append(f"{rel}: missing")
        elif _sha(f) != str(sha):
            bad.append(f"{rel}: changed since the export")
    return m, bad


def _here(slug: str) -> List[Path]:
    """What the account already has in this machine's state (its folder, its stats files)."""
    state = Path(C.STATE)
    out = []
    root = state / slug
    if root.is_dir() and any(root.iterdir()):
        out.append(root)
    for sub in STATS:
        d = state / "stats" / sub
        if d.is_dir():
            out += sorted(f for f in d.glob(f"{slug}__*") if f.is_file())
    return out


def import_(cfg: C.Config, src: Path, replace: bool = False, now: Optional[datetime] = None) -> List[str]:
    """Check the folder against its manifest, then put the account's files in this machine's state. What it says."""
    m, bad = verify(src)
    slug = str(m["account"])
    if slug not in cfg.all_accounts:
        raise SystemExit(f"{slug}: not an account in config/accounts.json here; pull the code first (git pull), "
                         f"then import")
    a = _account(cfg, slug)
    if bad:
        raise SystemExit(f"{src}: {len(bad)} file{'s' if len(bad) != 1 else ''} differ from the manifest, nothing "
                         f"was imported: " + "; ".join(bad[:5]) + (" ..." if len(bad) > 5 else ""))
    if slug in cfg.accounts:
        raise SystemExit(f"{a.name} is switched on here: import only while this machine does not run it. Set "
                         f'"active": false (or another "machine") for it in config/accounts.json, import, then '
                         f"switch it on here")
    state = Path(C.STATE)
    here = _here(slug)
    if here and not replace:
        raise SystemExit(f"{a.name} already has state here ({here[0]}): add --replace to move it aside into "
                         f"state/config-backups/ first")
    t = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    said: List[str] = []
    stage = state / f".import-{slug}-{t.strftime('%Y%m%dT%H%M%S%f')}"
    try:
        # 1. every file into a stage beside state/, each checked again; nothing here has moved yet
        for rel, sha in sorted(m["files"].items()):
            r = _safe_rel(rel, slug)
            dest = stage / Path(*r.parts)
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src / rel, dest)
            if _sha(dest) != str(sha):
                raise SystemExit(f"{rel}: changed while it was copied, nothing was imported and the state here is "
                                 f"as it was; try again")
        # 2. only then the state that is here moves aside (never deleted), and 3. the stage takes its place
        backup = None
        moved: List[Tuple[Path, Path]] = []
        if here:
            backup = state / "config-backups" / f"move-{slug}-{t.strftime('%Y%m%dT%H%M%S')}"
            n = 1
            while backup.exists():
                n += 1
                backup = state / "config-backups" / f"move-{slug}-{t.strftime('%Y%m%dT%H%M%S')}-{n}"
        placed: List[Path] = []
        try:
            for p in here:
                dest = backup / p.relative_to(state)            # type: ignore[operator]
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(p), str(dest))
                moved.append((p, dest))
            if (stage / slug).is_dir():
                os.replace(stage / slug, state / slug)
                placed.append(state / slug)
            for sub in STATS:
                d = stage / "stats" / sub
                if d.is_dir():
                    (state / "stats" / sub).mkdir(parents=True, exist_ok=True)
                    for f in sorted(d.iterdir()):
                        os.replace(f, state / "stats" / sub / f.name)
                        placed.append(state / "stats" / sub / f.name)
        except BaseException as e:
            _undo(placed, moved)
            left = [str(b) for _, b in moved if b.exists()]
            raise SystemExit(f"{a.name}: the import stopped ({type(e).__name__}: {e}); nothing was imported"
                             + (f", and the state that was here could not all go back: it is in {backup}" if left
                                else ", and the state here is as it was")) from e
        if backup is not None:
            said.append(f"  the state that was here is in {backup}")
    finally:
        shutil.rmtree(stage, ignore_errors=True)
    return ([f"Imported {a.name}: {len(m['files'])} files from {src} (exported {m.get('at')} on "
             f"{m.get('machine') or 'the other machine'}), every sha256 checked"] + said +
            [f'  next: set "active": true and "machine" for {slug} in config/accounts.json, then factory.py timer '
             f"install and factory.py doctor"])


def _undo(placed: List[Path], moved: List[Tuple[Path, Path]]) -> None:
    """A failed swap: take out what was placed, then put the state that was here back where it was."""
    for p in reversed(placed):
        try:
            if p.is_dir() and not p.is_symlink():
                shutil.rmtree(p)
            elif p.exists():
                p.unlink()
        except OSError:
            pass
    for was, now_at in reversed(moved):
        try:
            if now_at.exists() and not was.exists():
                was.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(now_at), str(was))
        except OSError:
            pass


# ------------------------------------------------------------------------------------------------------ the commands
def _parser(name: str) -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog=f"factory.py social {name}")
    if name == "export":
        ap.add_argument("account")
        ap.add_argument("--to", required=True, help="an empty folder, as a full path")
    else:
        ap.add_argument("folder", nargs="?", help="the folder an export wrote (the same as --from)")
        ap.add_argument("--from", dest="src", help="the folder an export wrote")
        ap.add_argument("--replace", action="store_true", help="move the state that is here aside first")
    return ap


def cli_export(cfg: C.Config, argv: List[str]) -> None:
    """factory.py social export <account> --to DIR"""
    a = _parser("export").parse_args(list(argv))
    for line in export(cfg, a.account, _folder(a.to, "--to")):
        print(line)


def cli_import(cfg: C.Config, argv: List[str]) -> None:
    """factory.py social import DIR [--replace], or import --from DIR [--replace] (social.cli hands these words on
    whole; a leading "--" is taken as the usual end of options)."""
    argv = list(argv)
    if argv[:1] == ["--"]:
        argv = argv[1:]
    a = _parser("import").parse_args(argv)
    if a.src and a.folder and a.src != a.folder:
        raise SystemExit("import: one folder, either DIR or --from DIR")
    src = _folder(a.src or a.folder or "", "the folder (DIR or --from DIR)")
    if not src.is_dir():
        raise SystemExit(f"{src}: no such folder")
    for line in import_(cfg, src, replace=a.replace):
        print(line)
