#!/usr/bin/env python3
"""
loop_extract.py — read the vault log into the open-loops store. Runs once a day.

Most of his planning happens in Claude Code sessions on the Mac, and every one of them
writes the vault log ("escalation shelved unless she is silent past Wednesday"). This is
the one-way path from that log to Hermes's loop list: the Mac never writes the store, so
there is nothing for Obsidian Sync to conflict on.

TWO LANES
  tagged  A log summary that carries   ⏳ who=<name>; by=<YYYY-MM-DD>; then=<next step>
          becomes a loop by regex. No model, no judgment. Mac sessions write this tag.
  prose   Everything else new is numbered (E1, E2, …) and sent in ONE model call that
          returns proposed operations, each citing the entry id it came from. Code then
          validates every operation; a bad one is dropped and logged, never "fixed".

WHAT THE CODE WILL NOT TAKE FROM THE MODEL
  * an operation citing an entry id it was not given;
  * a kind, date or loop id that does not parse / does not exist;
  * a CLOSE unless the cited entry is not a `decision`, is not older than the loop,
    contains a completion word, and the "evidence" is a verbatim piece of that entry.
    (A false close hides a real obligation. A false add costs one question.)

NEW = not seen before, tracked as a set of hashes of the entry header, because Mac entries
reach the VPS late and out of order through Sync; a timestamp cursor would skip them.
If the model call fails, the prose entries stay unseen and are retried next run.

A cap that does not announce itself is a silent data-loss bug: when there are more new
entries than MAX_ENTRIES_PER_RUN, the oldest are processed first and the count left over
is printed.

  loop_extract.py [--days 7] [--dry-run] [--no-llm]
Stdlib only.
"""
from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
for _cand in (HERE.parent / "vault", Path.home() / ".hermes" / "scripts" / "vault"):
    if (_cand / "done_facts.py").exists():
        sys.path.insert(0, str(_cand))
        break

import done_facts  # noqa: E402
import today_actions  # noqa: E402
import llm  # noqa: E402
import loops  # noqa: E402

MAX_ENTRIES_PER_RUN = 40
SUMMARY_CHARS = 500
SEEN_KEEP = 6000
TAG = re.compile(r"⏳\s*who=(?P<who>[^;]+);\s*by=(?P<by>\d{4}-\d{2}-\d{2});\s*then=(?P<then>.+)$")
# What makes an entry evidence that something FINISHED. Wider than done_facts.DONE_WORD on
# purpose (rejections and withdrawals end a wait too), and only ever used together with
# the other close checks.
FINISHED = re.compile(r"✅|(?i:\b(sent|submitted|done|confirmed|accepted|paid|filed|replied|completed|"
                      r"received|signed|closed|rejected|withdrawn|cancelled|canceled|resolved|"
                      r"nothing owed)\b)")


def log(msg: str) -> None:
    print(f"[loop-extract] {msg}", file=sys.stderr)


def entry_hash(e: dict) -> str:
    return hashlib.sha1(f"{e['date']}|{e['action']}|{e['scope']}|{e['summary']}".encode()).hexdigest()[:14]


def new_entries(vault: Path, seen: set, days: int, today: datetime.date) -> list[dict]:
    out = []
    for e in done_facts.recent_entries(vault, days=days, today=today, include_machine=False):
        h = entry_hash(e)
        if h not in seen:
            out.append(dict(e, hash=h))
    return out


ACTION_ITEMS_REL = Path("00 - Dashboard") / "Action Items.md"
AI_SCOPE = "Action Items / THIS WEEK"


def action_item_entries(vault: Path, seen: set, today: datetime.date) -> list[dict]:
    """The OPEN rows of the live THIS WEEK table in Action Items, as entries for the prose
    lane (When — What — Note, since the Note column is where the condition usually sits:
    "escalate only if she goes silent past Wed"). Added 2026-11-16: the first version read
    only the log and missed threads that lived in this table (his point: it is all in the
    vault already, so he should never have to say what is missing).
    Finished rows (✅ / 🗄️ / struck through) are skipped. A row whose text changes gets a new
    hash and is read again; the title match in loops.add keeps that from minting a twin.
    Unreadable file or no THIS WEEK table -> [] (announced), never an error."""
    try:
        lines = (vault / ACTION_ITEMS_REL).read_text(encoding="utf-8").splitlines()
    except OSError as e:
        log(f"Action Items not read: {e}")
        return []
    out, inside, found = [], False, False
    for ln in lines:
        if re.match(r"^#{1,4}\s", ln):
            if inside and out:
                break
            inside = bool(today_actions.THIS_WEEK.match(ln)) and not today_actions.ARCHIVED.search(ln)
            found = found or inside
            continue
        if not inside or not ln.startswith("|"):
            continue
        cells = [re.sub(r"\s+", " ", c).strip() for c in ln.strip().strip("|").split("|")]
        if len(cells) < 2 or set(cells[0]) <= set("-: ") or cells[0] == "When":
            continue
        if today_actions.DONE_LEAD.match(cells[0]):
            continue
        summary = f"{cells[0]} — {cells[1]}" + (f" — note: {cells[2]}" if len(cells) > 2 and cells[2] else "")
        e = {"date": today.isoformat(), "action": "update", "scope": AI_SCOPE, "summary": summary, "machine": False}
        e["hash"] = entry_hash(e | {"date": ""})          # the row, not the day it was read
        if e["hash"] not in seen:
            out.append(e)
    if not found:
        log("Action Items: no live THIS WEEK table found; nothing read from it")
    return out


def _source(e: dict) -> dict:
    return {"date": e["date"], "action": e["action"], "scope": e["scope"], "hash": e["hash"]}


def _entry_dt(e: dict) -> datetime.datetime:
    return datetime.datetime.combine(datetime.date.fromisoformat(e["date"]), datetime.time(12, 0), loops.TZ)


# ----------------------------------------------------------------- tagged lane
def apply_tagged(data: dict, entries: list[dict]) -> tuple[list[str], list[dict]]:
    """Entries carrying the ⏳ tag become loops with no model involved.
    Returns (hashes handled, the remaining entries for the prose lane)."""
    handled, rest = [], []
    for e in entries:
        m = TAG.search(e["summary"])
        if not m:
            rest.append(e)
            continue
        title = re.sub(r"\s*⏳.*$", "", e["summary"]).strip(" .;—-") or e["scope"]
        loops.add(data, title=f"{e['scope']}: {title}"[:120], kind="waiting_on",
                  who=m["who"].strip(), next_step=m["then"].strip(), due=m["by"],
                  check_after=m["by"], source=_source(e), at=_entry_dt(e))
        handled.append(e["hash"])
    return handled, rest


# ----------------------------------------------------------------- prose lane
SYSTEM = """You maintain the list of OPEN LOOPS for one person's assistant, from his own activity log.

An open loop is something unfinished that has a next moment to look at it again:
- waiting_on: he is waiting for a person or organisation to do or send something;
- my_move: he owes a concrete step by a date;
- conditional_plan: he decided "if X has not happened by D, do Y".

You get (1) the loops already tracked and (2) NEW log entries, numbered E1, E2, …
Return ONLY a JSON object: {"ops": [ ... ]}. Each op cites the ONE entry it comes from.

  {"op": "add", "entry": "E3", "title": "<short name of the thread>", "kind": "waiting_on|my_move|conditional_plan",
   "who": "<person or organisation, as written in the entry, or empty>",
   "next_step": "<what happens next / what he does if nothing arrives; from the entry>",
   "due": "YYYY-MM-DD or null", "check_after": "YYYY-MM-DD or null"}
  {"op": "update", "loop": "<existing loop id>", "entry": "E5", "next_step": "...", "due": "...", "check_after": "...", "note": "<what changed>"}
  {"op": "close", "loop": "<existing loop id>", "entry": "E7", "evidence": "<words copied EXACTLY from that entry>"}

RULES
- Entries whose scope is "Action Items / THIS WEEK" are rows of his live task table, written "When — What — note: …". A dated step he still has to do is a my_move (due = that date). A row that says who he is waiting on and what happens if they stay silent is a waiting_on or conditional_plan. A "Waiting, nothing owed" row lists several names: add ONE waiting_on per name, each with next_step "Nothing owed; await a reply". Appointments and events (a haircut, a hackathon) are not loops.
- Most entries are NOT loops: finished work, research, purchases, notes. Return no op for them. An empty ops list is a common, correct answer.
- Prefer updating an existing loop over adding a second loop for the same thread.
- Only add a loop when the entry itself states the wait, the owed step, or the condition. Never infer one.
- Use only names, dates and facts written in the cited entry. If the entry gives no date, use null.
- Dates in entries are written like "Wed Nov 18": convert using the entry's own date for the year.
- close ONLY when the cited entry says the thing actually happened or the thread ended (replied, received, signed, sent, rejected, withdrawn, "closed", "nothing owed"). A plan or a decision to do something later never closes a loop. `evidence` must be copied exactly from the entry.
- No prose, no code fence."""


def build_user(data: dict, entries: list[dict]) -> tuple[str, dict]:
    ids = {f"E{i}": e for i, e in enumerate(entries, 1)}
    tracked = [f"- {lid} | {lp['kind']} | {lp['title']} | who: {lp['who'] or '-'} | next: {lp['next_step'] or '-'}"
               for lid, lp in loops.open_loops(data)] or ["(none)"]
    lines = ["LOOPS ALREADY TRACKED:", *tracked, "", "NEW LOG ENTRIES:"]
    for k, e in ids.items():
        lines.append(f"{k} | {e['date']} | {e['action']} | {e['scope']} — {e['summary'][:SUMMARY_CHARS]}")
    return "\n".join(lines), ids


def _valid_day(s, ref: datetime.date) -> str | None:
    d = loops.parse_day(s)
    if d and ref - datetime.timedelta(days=7) <= d <= ref + datetime.timedelta(days=180):
        return d.isoformat()
    return None


def apply_ops(data: dict, ops, ids: dict, today: datetime.date) -> dict:
    """Validate and apply each proposed op. Returns counts + the reasons anything was dropped."""
    res = {"added": 0, "updated": 0, "closed": 0, "rejected": []}

    def reject(op, why):
        res["rejected"].append(f"{why}: {json.dumps(op, ensure_ascii=False)[:160]}")

    if not isinstance(ops, list):
        reject(ops, "ops is not a list")
        return res
    for op in ops:
        if not isinstance(op, dict):
            reject(op, "op is not an object"); continue
        e = ids.get(str(op.get("entry", "")))
        if e is None:
            reject(op, "cites an entry that was not given"); continue
        kind = op.get("op")
        if kind == "add":
            title = str(op.get("title") or "").strip()
            if op.get("kind") not in loops.KINDS or not title:
                reject(op, "add without a valid kind/title"); continue
            who = str(op.get("who") or "").strip()
            hay = (e["summary"] + " " + e["scope"]).lower()
            if any(w not in hay for w in re.findall(r"[a-z0-9]{3,}", who.lower())):
                reject(op, "who is not named in the cited entry"); continue
            before = len(data["loops"])
            loops.add(data, title=title, kind=op["kind"], who=who,
                      next_step=str(op.get("next_step") or ""), due=_valid_day(op.get("due"), today),
                      check_after=_valid_day(op.get("check_after"), today),
                      source=_source(e), at=_entry_dt(e))
            res["added" if len(data["loops"]) > before else "updated"] += 1
        elif kind in ("update", "close"):
            lid = str(op.get("loop") or "")
            if lid not in data["loops"]:
                # A loop ADDED earlier in this same batch has no id the model could know;
                # it names it by title. Resolve that to the open loop with the same title.
                lid = loops.find_open(data, lid) or lid
            lp = data["loops"].get(lid)
            if lp is None or lp["status"] == "closed":
                reject(op, "names a loop that is not open"); continue
            if kind == "update":
                loops.update(data, lid, next_step=op.get("next_step"), due=_valid_day(op.get("due"), today),
                             check_after=_valid_day(op.get("check_after"), today), note=op.get("note"),
                             source=_source(e), at=_entry_dt(e))
                res["updated"] += 1
                continue
            ev = str(op.get("evidence") or "").strip()
            if e["action"] == "decision":
                reject(op, "a decision entry never closes a loop"); continue
            if e["date"] < lp["opened_at"][:10]:
                reject(op, "closing entry is older than the loop"); continue
            if len(ev) < 6 or ev.lower() not in e["summary"].lower():
                reject(op, "evidence is not a verbatim piece of the cited entry"); continue
            if not FINISHED.search(e["summary"]):
                reject(op, "cited entry does not say anything finished"); continue
            loops.close(data, lid, f"log {e['date']} ({e['scope']}): {ev}", at=_entry_dt(e))
            res["closed"] += 1
        else:
            reject(op, "unknown op")
    return res


def parse_reply(reply: str):
    try:
        obj = json.loads(reply[reply.index("{"): reply.rindex("}") + 1])
        ops = obj.get("ops") if isinstance(obj, dict) else None
        return ops if isinstance(ops, list) else None      # anything else = a failed call, retried
    except Exception:  # noqa: BLE001
        return None


def run(vault: Path, days: int = 7, today: datetime.date | None = None, use_llm: bool = True,
        dry_run: bool = False, call=None) -> dict:
    """One extraction pass. `call(system, user)` is injectable for tests."""
    today = today or loops.now().date()
    data = loops.load()                                  # LoopsError propagates: never overwrite a bad store
    seen_order = list(data["seen"])                      # insertion order: trimming drops the OLDEST
    seen = set(seen_order)
    fresh = new_entries(vault, seen, days, today) + action_item_entries(vault, seen, today)
    left_over = max(0, len(fresh) - MAX_ENTRIES_PER_RUN)
    batch = fresh[:MAX_ENTRIES_PER_RUN]                  # oldest first, so nothing is starved
    summary = {"new": len(fresh), "left_for_next_run": left_over, "tagged": 0, "model": "skipped",
               "added": 0, "updated": 0, "closed": 0, "rejected": []}
    if left_over:
        log(f"CAP: {len(fresh)} new entries, processing {len(batch)} now, {left_over} left for the next run")

    handled, prose = apply_tagged(data, batch)
    summary["tagged"] = len(handled)
    newly_seen = list(handled)

    if prose and use_llm:
        user, ids = build_user(data, prose)
        call = call or (lambda s, u: llm.call(s, u, purpose="loop-extract", model=llm.SONNET, max_tokens=3000))
        ops = parse_reply(call(SYSTEM, user) or "")
        if ops is None:
            summary["model"] = "failed"                  # entries stay unseen and are retried
            log("model call failed or returned no JSON; prose entries left for the next run")
        else:
            summary["model"] = "ok"
            res = apply_ops(data, ops, ids, today)
            for k in ("added", "updated", "closed"):
                summary[k] += res[k]
            summary["rejected"] = res["rejected"]
            newly_seen += [e["hash"] for e in prose]
    elif prose:
        summary["model"] = "skipped (--no-llm): prose entries left unseen"

    summary["expired"] = len(loops.sweep(data))
    # Never sort before trimming: hashes are random, so a sorted trim throws away an
    # arbitrary slice and those entries come back as "new" (the email_triage seen-set bug).
    data["seen"] = (seen_order + [h for h in newly_seen if h not in seen])[-SEEN_KEEP:]
    if dry_run:
        summary["dry_run"] = True
        return summary
    loops.save(data)
    summary["view_changed"] = loops.write_view(data)
    return summary


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="loop_extract")
    ap.add_argument("--days", type=int, default=7)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--no-llm", action="store_true")
    ap.add_argument("--batches", type=int, default=1, help="process up to N batches in one run (backfill)")
    a = ap.parse_args(argv)
    # --batches > 1 is for the first backfill only: each batch is one model call, and the
    # spend guard's per-run cap (llm.MAX_CALLS_PER_RUN) bounds it regardless.
    for _ in range(max(1, a.batches)):
        summary = run(loops.vault(), days=a.days, use_llm=not a.no_llm, dry_run=a.dry_run)
        for r in summary.pop("rejected"):
            log(f"rejected op — {r}")
        log(json.dumps(summary))
        if a.dry_run or not summary["left_for_next_run"] or summary["model"] != "ok":
            break
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as e:  # noqa: BLE001
        log(f"FAILED: {type(e).__name__}: {e}")
        print(f"⚠️ Open-loops extraction failed: {type(e).__name__}: {e}")
        sys.exit(1)
