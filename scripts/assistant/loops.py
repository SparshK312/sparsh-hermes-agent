#!/usr/bin/env python3
"""
loops.py — the open threads Hermes is keeping track of for him.

A "loop" is something that is not finished and has a next moment to look at it again:
he is waiting on a person, he owes someone a step, or he made a plan with a condition
("if no reply by Wednesday, escalate"). Until 2026-11-16 these lived only as prose in the
vault log; nothing re-read them, so nothing followed up.

THE STORE is one JSON file on the VPS (~/.hermes/assistant/loops.json): one writer, atomic
writes, and a file that exists but cannot be read is REFUSED, never treated as "no loops".
`.json` does not sync, on purpose: the Mac never writes it. What he and the Mac sessions
see is a rendered, read-only view in the vault (00 - Dashboard/Open Loops.md).

Loops arrive from the vault log (loop_extract.py) and from chat (the open-loops skill runs
this CLI). They leave three ways: closed with evidence, expired (every loop has an expiry;
default 14 days), or dormant after being asked about twice with no answer.

  loops.py list [--all]                 loops.py due
  loops.py add --title … --kind waiting_on --who … [--next-step …] [--due YYYY-MM-DD] [--check-after YYYY-MM-DD]
  loops.py update <id> [--next-step …] [--due …] [--check-after …] [--note …]
  loops.py close <id> --evidence "…"    loops.py snooze <id> --until YYYY-MM-DD
  loops.py render                       (rewrite the vault view if it changed)

Stdlib only.
"""
from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import os
import re
import sys
from pathlib import Path
from zoneinfo import ZoneInfo

TZ = ZoneInfo("America/New_York")
KINDS = ("waiting_on", "my_move", "conditional_plan")
STATUSES = ("open", "dormant", "closed")
DEFAULT_EXPIRY_DAYS = 14
MAX_ASKS = 2
VIEW_REL = Path("00 - Dashboard") / "Open Loops.md"
CHECK_HOUR = 10          # a date-only "check after" means that day at 10:00 Eastern


class LoopsError(RuntimeError):
    """loops.json exists and cannot be read. Never treated as an empty store."""


def base_dir() -> Path:
    return Path(os.environ.get("HERMES_ASSISTANT_DIR", str(Path.home() / ".hermes" / "assistant")))


def store_path() -> Path:
    return base_dir() / "loops.json"


def vault() -> Path:
    return Path(os.environ.get("HERMES_VAULT", "/home/hermes/vault"))


def now() -> datetime.datetime:
    return datetime.datetime.now(TZ)


def _iso(dt: datetime.datetime) -> str:
    return dt.astimezone(TZ).isoformat(timespec="minutes")


def parse_day(s) -> datetime.date | None:
    try:
        return datetime.date.fromisoformat(str(s).strip()[:10])
    except (ValueError, TypeError):
        return None


def at_check_hour(d: datetime.date) -> datetime.datetime:
    return datetime.datetime.combine(d, datetime.time(CHECK_HOUR, 0), TZ)


# ----------------------------------------------------------------- store
def load() -> dict:
    p = store_path()
    if not p.exists():
        return {"version": 1, "loops": {}, "seen": []}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        if not isinstance(data.get("loops"), dict) or not isinstance(data.get("seen"), list):
            raise ValueError("missing 'loops' object or 'seen' list")
        return data
    except Exception as e:  # noqa: BLE001
        raise LoopsError(f"{p} is unreadable ({type(e).__name__}: {e}); not touching it") from e


def save(data: dict) -> None:
    base_dir().mkdir(parents=True, exist_ok=True)
    tmp = store_path().with_name("loops.json.tmp")
    tmp.write_text(json.dumps(data, indent=1, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, store_path())


def _slug(title: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")[:40].strip("-")
    return s or "loop"


def norm_title(title: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (title or "").lower()).strip()


def find_open(data: dict, title: str) -> str | None:
    """An OPEN or DORMANT loop with the same normalised title (so a re-extracted thread
    updates the existing loop instead of minting a twin)."""
    want = norm_title(title)
    for lid, lp in data["loops"].items():
        if lp["status"] != "closed" and norm_title(lp["title"]) == want:
            return lid
    return None


def add(data: dict, *, title: str, kind: str, who: str = "", next_step: str = "",
        due=None, check_after=None, source: dict | None = None, check: dict | None = None,
        at: datetime.datetime | None = None, expiry_days: int = DEFAULT_EXPIRY_DAYS) -> str:
    """Add a loop, or fold into the open loop that already has this title. Returns its id."""
    if kind not in KINDS:
        raise ValueError(f"kind must be one of {KINDS}, got {kind!r}")
    title = re.sub(r"\s+", " ", title or "").strip()
    if not title:
        raise ValueError("a loop needs a title")
    n = at or now()
    existing = find_open(data, title)
    if existing:
        update(data, existing, next_step=next_step or None, due=due, check_after=check_after,
               source=source, at=n)
        return existing
    due_d, chk_d = parse_day(due), parse_day(check_after)
    lid = f"{_slug(title)}-{hashlib.sha1(f'{title}|{_iso(n)}'.encode()).hexdigest()[:5]}"
    first_check = at_check_hour(chk_d or due_d) if (chk_d or due_d) else n + datetime.timedelta(days=2)
    horizon = max([d for d in (due_d, chk_d) if d] + [n.date()])
    data["loops"][lid] = {
        "title": title[:120], "kind": kind, "who": (who or "").strip()[:80],
        "next_step": (next_step or "").strip()[:240],
        "check": check or {"type": "manual"},
        "opened_at": _iso(n), "updated_at": _iso(n),
        "due": due_d.isoformat() if due_d else None,
        "next_check_at": _iso(first_check),
        # every loop dies on its own if nothing ever closes it
        "expires_at": _iso(at_check_hour(horizon) + datetime.timedelta(days=expiry_days)),
        "status": "open", "sources": [source] if source else [], "asks": [], "notes": [],
    }
    return lid


def update(data: dict, lid: str, *, next_step=None, due=None, check_after=None, note=None,
           source: dict | None = None, at: datetime.datetime | None = None) -> None:
    lp = data["loops"][lid]
    n = at or now()
    if next_step:
        lp["next_step"] = str(next_step).strip()[:240]
    if parse_day(due):
        lp["due"] = parse_day(due).isoformat()
    if parse_day(check_after):
        lp["next_check_at"] = _iso(at_check_hour(parse_day(check_after)))
        lp["expires_at"] = max(lp["expires_at"], _iso(at_check_hour(parse_day(check_after))
                                                      + datetime.timedelta(days=DEFAULT_EXPIRY_DAYS)))
    if note:
        lp.setdefault("notes", []).append({"at": _iso(n), "text": str(note)[:300]})
    if source and source not in lp["sources"]:
        lp["sources"].append(source)
    if lp["status"] == "dormant" and (next_step or due or check_after):
        lp["status"] = "open"; lp["asks"] = []           # new information revives it
    lp["updated_at"] = _iso(n)


def close(data: dict, lid: str, evidence: str, at: datetime.datetime | None = None) -> None:
    """Close with the evidence that it is finished. An empty evidence string is refused:
    a false close hides a real obligation, which is worse than one extra question."""
    if not (evidence or "").strip():
        raise ValueError("closing a loop needs evidence")
    lp = data["loops"][lid]
    lp.update(status="closed", closed_at=_iso(at or now()), closed_evidence=evidence.strip()[:400],
              updated_at=_iso(at or now()))


def snooze(data: dict, lid: str, until, at: datetime.datetime | None = None) -> None:
    d = parse_day(until)
    if not d:
        raise ValueError(f"snooze needs a YYYY-MM-DD date, got {until!r}")
    update(data, lid, check_after=d.isoformat(), at=at)


def note_ask(data: dict, lid: str, at: datetime.datetime | None = None) -> None:
    """Hermes asked him about this loop. After MAX_ASKS with nothing new it goes dormant;
    otherwise the next look is two days out."""
    lp = data["loops"][lid]
    n = at or now()
    lp["asks"].append(_iso(n))
    if len(lp["asks"]) >= MAX_ASKS:
        lp["status"] = "dormant"
    else:
        lp["next_check_at"] = _iso(n + datetime.timedelta(days=2))
    lp["updated_at"] = _iso(n)


def sweep(data: dict, at: datetime.datetime | None = None) -> list[str]:
    """Expire: an open loop past its expires_at goes dormant. Returns the ids changed."""
    n = _iso(at or now())
    changed = []
    for lid, lp in data["loops"].items():
        if lp["status"] == "open" and lp["expires_at"] <= n:
            lp["status"] = "dormant"; lp["updated_at"] = n
            lp.setdefault("notes", []).append({"at": n, "text": "expired with nothing new"})
            changed.append(lid)
    return changed


def due(data: dict, at: datetime.datetime | None = None) -> list[tuple[str, dict]]:
    """Open loops whose next look is now or past, oldest look first."""
    n = _iso(at or now())
    hits = [(lid, lp) for lid, lp in data["loops"].items()
            if lp["status"] == "open" and lp["next_check_at"] <= n]
    return sorted(hits, key=lambda x: x[1]["next_check_at"])


def open_loops(data: dict) -> list[tuple[str, dict]]:
    return sorted(((lid, lp) for lid, lp in data["loops"].items() if lp["status"] == "open"),
                  key=lambda x: (x[1]["due"] or "9999", x[1]["next_check_at"]))


# ----------------------------------------------------------------- vault view
def _cell(s) -> str:
    return re.sub(r"\s+", " ", str(s or "")).replace("|", "/").strip()


def _day(iso: str) -> str:
    try:
        d = datetime.datetime.fromisoformat(iso)
        return f"{d.strftime('%a %b')} {d.day}"
    except (ValueError, TypeError):
        return ""


def render(data: dict, at: datetime.datetime | None = None) -> str:
    n = at or now()
    kind_label = {"waiting_on": "Waiting on", "my_move": "My move", "conditional_plan": "Plan with a condition"}
    out = ["---", "type: tracker", "category: assistant", "status: active",
           f"last_updated: {n.date().isoformat()}", "tags: [hermes, open-loops]", "---", "",
           "# Open Loops", "",
           "> **Machine-written by Hermes** (`scripts/assistant/loops.py` on the VPS). Do not edit: "
           "your edit is overwritten on the next run. To change a thread, tell Hermes in Telegram "
           "(\"close the X thread\", \"still waiting on Y\", \"check Z again Friday\"), or record it in "
           "the log and it is picked up the next morning.",
           "> These are the threads Hermes follows up on. Each one cites the log line it came from. "
           "A thread leaves when it is closed with evidence, or goes quiet after two unanswered "
           "asks or 14 days with nothing new.", ""]
    rows = open_loops(data)
    out += [f"## Open ({len(rows)})", ""]
    if rows:
        out += ["| Thread | Type | Who | Next step | Look again | Due | From |", "|---|---|---|---|---|---|---|"]
        for lid, lp in rows:
            src = lp["sources"][-1] if lp["sources"] else {}
            frm = f"log {src.get('date', '')}: {_cell(src.get('scope', ''))}" if src.get("date") else "chat"
            due_s = _day(lp["due"] + "T00:00") if lp["due"] else ""
            out.append(f"| {_cell(lp['title'])} | {kind_label[lp['kind']]} | {_cell(lp['who'])} | "
                       f"{_cell(lp['next_step'])} | {_day(lp['next_check_at'])} | {due_s} | {frm} |")
    else:
        out.append("_Nothing open._")
    quiet = sorted(((lid, lp) for lid, lp in data["loops"].items() if lp["status"] == "dormant"),
                   key=lambda x: x[1]["updated_at"], reverse=True)[:15]
    if quiet:
        out += ["", f"## Gone quiet ({len(quiet)})", "", "| Thread | Who | Why |", "|---|---|---|"]
        for lid, lp in quiet:
            why = "asked twice, no answer" if len(lp["asks"]) >= MAX_ASKS else "nothing new for 14 days"
            out.append(f"| {_cell(lp['title'])} | {_cell(lp['who'])} | {why} |")
    cutoff = _iso(n - datetime.timedelta(days=7))
    done = sorted(((lid, lp) for lid, lp in data["loops"].items()
                   if lp["status"] == "closed" and lp.get("closed_at", "") >= cutoff),
                  key=lambda x: x[1]["closed_at"], reverse=True)
    if done:
        out += ["", f"## Closed in the last 7 days ({len(done)})", "", "| Thread | Closed | Evidence |", "|---|---|---|"]
        for lid, lp in done:
            out.append(f"| {_cell(lp['title'])} | {_day(lp['closed_at'])} | {_cell(lp['closed_evidence'])} |")
    return "\n".join(out) + "\n"


def write_view(data: dict, at: datetime.datetime | None = None) -> bool:
    """Write the vault view only when its content changed (a rewrite with the same content
    still costs a sync version). The last_updated line is ignored for that comparison."""
    path = vault() / VIEW_REL
    new = render(data, at)
    strip = lambda t: re.sub(r"(?m)^last_updated: .*$", "", t)   # noqa: E731
    if path.exists() and strip(path.read_text(encoding="utf-8")) == strip(new):
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(new, encoding="utf-8")
    os.replace(tmp, path)
    return True


# ----------------------------------------------------------------- cli
def _resolve(data: dict, frag: str) -> str:
    """A loop id, or a distinctive fragment of an id/title. Ambiguous or missing is refused
    with the candidates listed (never guessed)."""
    if frag in data["loops"]:
        return frag
    f = norm_title(frag)
    hits = [lid for lid, lp in data["loops"].items()
            if lp["status"] != "closed" and (f in norm_title(lp["title"]) or f in norm_title(lid))]
    if len(hits) == 1:
        return hits[0]
    listing = "; ".join(f"{lid} ({data['loops'][lid]['title']})" for lid in hits) or "none"
    raise SystemExit(f"loops: {len(hits)} open loops match {frag!r} (need exactly 1). Matches: {listing}")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="loops")
    sub = ap.add_subparsers(dest="cmd", required=True)
    ls = sub.add_parser("list"); ls.add_argument("--all", action="store_true")
    sub.add_parser("due"); sub.add_parser("render")
    a = sub.add_parser("add")
    a.add_argument("--title", required=True); a.add_argument("--kind", required=True, choices=KINDS)
    a.add_argument("--who", default=""); a.add_argument("--next-step", default="")
    a.add_argument("--due", default=None); a.add_argument("--check-after", default=None)
    u = sub.add_parser("update"); u.add_argument("loop")
    u.add_argument("--next-step", default=None); u.add_argument("--due", default=None)
    u.add_argument("--check-after", default=None); u.add_argument("--note", default=None)
    c = sub.add_parser("close"); c.add_argument("loop"); c.add_argument("--evidence", required=True)
    s = sub.add_parser("snooze"); s.add_argument("loop"); s.add_argument("--until", required=True)
    args = ap.parse_args(argv)
    try:
        data = load()
    except LoopsError as e:
        print(f"LOOPS STORE UNREADABLE: {e}")
        return 2

    if args.cmd in ("list", "due"):
        rows = due(data) if args.cmd == "due" else (
            sorted(data["loops"].items()) if args.all else open_loops(data))
        for lid, lp in rows:
            print(f"{lid} [{lp['status']}] {lp['title']} | who: {lp['who'] or '-'} | next: {lp['next_step'] or '-'} "
                  f"| look again {_day(lp['next_check_at'])} | due {lp['due'] or '-'}")
        if not rows:
            print("(none)")
        return 0
    if args.cmd == "add":
        lid = add(data, title=args.title, kind=args.kind, who=args.who, next_step=args.next_step,
                  due=args.due, check_after=args.check_after, source={"from": "chat"})
        msg = f"tracking: {data['loops'][lid]['title']} ({lid})"
    elif args.cmd == "update":
        lid = _resolve(data, args.loop)
        update(data, lid, next_step=args.next_step, due=args.due, check_after=args.check_after, note=args.note)
        msg = f"updated: {data['loops'][lid]['title']}"
    elif args.cmd == "close":
        lid = _resolve(data, args.loop)
        close(data, lid, args.evidence)
        msg = f"closed: {data['loops'][lid]['title']}"
    elif args.cmd == "snooze":
        lid = _resolve(data, args.loop)
        snooze(data, lid, args.until)
        msg = f"snoozed until {args.until}: {data['loops'][lid]['title']}"
    else:
        msg = "view checked"
    sweep(data)
    save(data)
    changed = write_view(data)
    print(msg + (" (view updated)" if changed else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
