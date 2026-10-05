#!/usr/bin/env python3
"""
log_append.py — the ONE way to write the vault's event log (since 2026-10-05).

WHY THIS EXISTS. The log used to be one append-only file, `Log.md`. By Oct 2026 it was
3.2 MB, and Obsidian Sync stores a FULL copy of a note on every save: ~14 saves a day
re-uploaded ~45 MB/day, 462 versions filled the 1 GiB Standard quota by themselves, and
sync jammed in BOTH directions from Sep 14 (Hermes ran on a 3-day-old vault). The log is
now one small file per day:

    Log/YYYY/YYYY-MM/YYYY-MM-DD.md      (date = the date in the entry header)

and `Log.md` is a stub whose only entry is a MOVED sentinel. This script writes an
entry to the right day file, safely:
  * O_APPEND + an exclusive flock, so parallel Claude sessions and Hermes crons can
    never interleave or clobber each other (the old way was read-modify-write via Edit);
  * the header format is enforced: `## [YYYY-MM-DD] <action> | <scope> — <summary>`;
  * the action must be in the vocabulary (ingest update decision archive lint schema).

It also SWEEPS: any entry that something appended to the `Log.md` stub out of habit is
moved into its day file and the stub restored (`--sweep`; catchup.py runs it too).

Pure stdlib, so it runs under any python on the Mac or the VPS. Invoked as
`python3 /abs/path/log_append.py …`, it matches no Hermes dangerous-command pattern.

USAGE
  log_append.py update "Curated Board" "one-line summary" --body "markdown body"
  log_append.py update "Curated Board" "summary" --body-file /tmp/body.md
  echo "body" | log_append.py lint "Vault" "summary" --body -
  log_append.py --sweep
  log_append.py --path-for 2026-10-05        # print the day file path
"""
from __future__ import annotations

import argparse
import fcntl
import os
import re
import sys
from datetime import datetime
from pathlib import Path

ACTIONS = ("ingest", "update", "decision", "archive", "lint", "schema")
HEADER_RE = re.compile(r"^## \[(\d{4}-\d{2}-\d{2})[^\]]*\]")
SENTINEL_DATE = "9999-12-31"
STUB_SENTINEL = (f"## [{SENTINEL_DATE}] schema | Log.md — MOVED. The log is one file per day "
                 f"under Log/YYYY/YYYY-MM/YYYY-MM-DD.md. Write with Scripts/log_append.py; "
                 f"read with Scripts/catchup.py or Scripts/recall.py; grep with `grep -rh '^## \\[' Log/`.")


def vault_root() -> Path:
    """Same resolution as store_paths.vault_root: HERMES_VAULT, else the VPS vault,
    else the Mac vault. A symlinked copy in the vault's Scripts/ resolves the vault from
    its OWN location (not the symlink target) so it can never write into the repo."""
    env = os.environ.get("HERMES_VAULT")
    if env:
        return Path(env)
    here = Path(sys.argv[0]).absolute().parent.parent
    if (here / "CLAUDE.md").exists() and (here / "Log.md").exists():
        return here
    if Path("/home/hermes/vault").exists():
        return Path("/home/hermes/vault")
    return Path.home() / "Documents" / "School Vault - UofT"


def today() -> str:
    try:
        from zoneinfo import ZoneInfo
        return datetime.now(ZoneInfo("America/Toronto")).strftime("%Y-%m-%d")
    except Exception:  # noqa: BLE001
        return datetime.now().strftime("%Y-%m-%d")


def day_file(vault: Path, date: str) -> Path:
    y, m, _ = date.split("-")
    return vault / "Log" / y / f"{y}-{m}" / f"{date}.md"


def _frontmatter(date: str) -> str:
    return f"---\ntype: log\ndate: {date}\n---\n"


def append_entry(vault: Path, date: str, text: str) -> Path:
    """Append one already-formatted entry (header line + body) to its day file."""
    p = day_file(vault, date)
    p.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(p, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o644)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        if os.fstat(fd).st_size == 0:
            os.write(fd, _frontmatter(date).encode())
        os.write(fd, ("\n" + text.strip("\n") + "\n").encode())
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)
    return p


def format_entry(date: str, action: str, scope: str, summary: str, body: str = "") -> str:
    if action not in ACTIONS:
        raise SystemExit(f"log_append: action {action!r} not in {ACTIONS}")
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", date):
        raise SystemExit(f"log_append: date {date!r} is not YYYY-MM-DD")
    summary = " ".join(summary.split())
    if not scope.strip() or not summary:
        raise SystemExit("log_append: scope and summary are required")
    head = f"## [{date}] {action} | {scope.strip()} — {summary}"
    return head + ("\n" + body.strip("\n") if body.strip() else "")


def stub_text() -> str:
    return ("---\ntype: log\nstatus: archived-superseded\nmoved: 2026-10-05\n---\n\n"
            "# Vault Log — MOVED\n\n"
            "> The log is **one file per day**: `Log/YYYY/YYYY-MM/YYYY-MM-DD.md` (see "
            "[[Log/README]]). This file holds no entries. Anything appended here by habit is "
            "moved into its day file by `Scripts/log_append.py --sweep` (catchup.py runs it).\n\n"
            + STUB_SENTINEL + "\n")


def split_entries(text: str) -> tuple[str, list[str]]:
    """(preamble, [entry, ...]) — an entry is a `## [date` header plus everything up to
    the next one. Lines before the first header are the preamble."""
    lines = text.splitlines(keepends=True)
    pre, entries, cur = [], [], None
    for ln in lines:
        if HEADER_RE.match(ln):
            if cur is not None:
                entries.append("".join(cur))
            cur = [ln]
        elif cur is None:
            pre.append(ln)
        else:
            cur.append(ln)
    if cur is not None:
        entries.append("".join(cur))
    return "".join(pre), entries


def sweep(vault: Path) -> int:
    """Move any real entry found in the Log.md stub into its day file; restore the stub.
    Returns how many entries were moved. Holds the stub's lock throughout."""
    stub = vault / "Log.md"
    if not stub.exists():
        return 0
    fd = os.open(stub, os.O_RDWR)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        text = os.read(fd, os.fstat(fd).st_size or 1).decode("utf-8", "replace") \
            if os.fstat(fd).st_size else ""
        _, entries = split_entries(text)
        stray = [e for e in entries if not e.startswith(f"## [{SENTINEL_DATE}]")]
        for e in stray:
            date = HEADER_RE.match(e).group(1)
            append_entry(vault, date, e)
        if stray or text != stub_text():
            os.lseek(fd, 0, os.SEEK_SET)
            os.ftruncate(fd, 0)
            os.write(fd, stub_text().encode())
        return len(stray)
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("action", nargs="?")
    ap.add_argument("scope", nargs="?")
    ap.add_argument("summary", nargs="?")
    ap.add_argument("--body", default="")
    ap.add_argument("--body-file")
    ap.add_argument("--date", help="entry date (default: today, America/Toronto)")
    ap.add_argument("--vault", help="vault root (default: auto)")
    ap.add_argument("--sweep", action="store_true")
    ap.add_argument("--path-for", metavar="DATE")
    a = ap.parse_args(argv)
    vault = Path(a.vault) if a.vault else vault_root()
    if a.path_for:
        print(day_file(vault, a.path_for))
        return 0
    if a.sweep:
        n = sweep(vault)
        print(f"log_append: swept {n} stray entr{'y' if n == 1 else 'ies'} from Log.md")
        return 0
    if not (a.action and a.scope and a.summary):
        ap.error("action, scope and summary are required (or --sweep / --path-for)")
    body = a.body
    if a.body_file:
        body = sys.stdin.read() if a.body_file == "-" else Path(a.body_file).read_text()
    elif body == "-":
        body = sys.stdin.read()
    date = a.date or today()
    p = append_entry(vault, date, format_entry(date, a.action, a.scope, a.summary, body))
    print(p)
    return 0


if __name__ == "__main__":
    sys.exit(main())
