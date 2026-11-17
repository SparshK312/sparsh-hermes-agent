#!/usr/bin/env python3
"""
daily_prefill.py — set up today's daily note. Replaces the `daily-note-prefill` AGENT cron.

WHY A SCRIPT (2026-11-16): the agent job was 76% of Hermes's tracked spend (~$11 a month,
~200 Sonnet calls) for work that is almost entirely mechanical, and its prompt spent most
of its words stopping the model from improvising ("run EXACTLY this ONE command", "do NOT
invent flags", "copy it VERBATIM"). It also hard-coded a -04:00 calendar window, wrong
since the clocks changed on Nov 1.

Same shape as email_triage.py: the mechanical part is code, the one part that needs
judgment is ONE small model call.

  mechanical  create the note from the template / merge into a stub / leave a real day alone
              calendar events -> the Schedule table (offset computed, not typed)
              the 06:40 inbox read -> a section, verbatim
  judgment    turning the long [TODAY] rows of Action Items into short checkbox lines
              (Haiku; if the call fails the rows are shortened by rule instead, so a bad
              morning costs wording, never tasks)

It never overwrites a day that is already underway, and it re-reads the note right
before writing so a wearable sync landing mid-run is not lost.

  daily_prefill.py [--date YYYY-MM-DD] [--dry-run] [--no-llm]

Stdout stays EMPTY on success (the cron is --no-agent: stdout is delivered verbatim).
Anything printed is a failure he should see. Diagnostics go to stderr -> the log.
"""
from __future__ import annotations

import argparse
import datetime
import json
import os
import re
import subprocess
import sys
import urllib.request
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parent))
import today_actions  # noqa: E402

TZ = ZoneInfo("America/New_York")
HERMES = Path.home() / ".hermes"
VAULT = Path(os.environ.get("HERMES_VAULT", "/home/hermes/vault"))
DAILY_DIR = VAULT / "04 - Daily Notes"
TEMPLATE = VAULT / "Templates" / "Daily Note.md"
ACTION_ITEMS = VAULT / "00 - Dashboard" / "Action Items.md"
TRIAGE_MD = HERMES / "health" / "email_triage.md"
ENV_FILE = HERMES / ".env"
FRAMEWORK_PY = HERMES / "hermes-agent" / "venv" / "bin" / "python"
GOOGLE_API = HERMES / "skills" / "productivity" / "google-workspace" / "scripts" / "google_api.py"

MODEL = "claude-haiku-4-5"
ANTHROPIC_URL = "https://api.anthropic.com/v1/messages"
TASK_MAX = 120
HEALTH_KEYS = ["weight", "sleep_hours", "sleep_quality", "kcal", "protein_g", "carbs_g",
               "fat_g", "water_l", "lifted", "energy", "mood"]
SECTIONS = ["Schedule", "Tasks", "Health", "Notes", "End of Day Reflection"]
INBOX_PLACEHOLDER = "Nothing needing attention."


def log(msg: str) -> None:
    print(f"[prefill] {msg}", file=sys.stderr)


# ----------------------------------------------------------------- note shape
def h1_for(d: datetime.date) -> str:
    return f"{d.strftime('%A, %B')} {d.day}, {d.year}"


def from_template(tpl: str, d: datetime.date) -> str:
    out = tpl.replace('"{{date}}"', f'"{d.isoformat()}"')
    out = re.sub(r"\{\{date:[^}]+\}\}", h1_for(d), out)
    return out.replace("{{date}}", d.isoformat())


def _split_fm(text: str):
    """(frontmatter_lines, body) or (None, text) when there is no frontmatter."""
    lines = text.split("\n")
    if not lines or lines[0].strip() != "---":
        return None, text
    end = next((i for i in range(1, len(lines)) if lines[i].strip() == "---"), None)
    if end is None:
        return None, text
    return lines[1:end], "\n".join(lines[end + 1:])


def _section(text: str, name: str):
    """Body of `## <name>` up to the next `## ` heading, or None when the heading is absent."""
    m = re.search(rf"(?m)^## {re.escape(name)}[ \t]*\n", text)
    if not m:
        return None
    nxt = re.search(r"(?m)^## ", text[m.end():])
    return text[m.end(): m.end() + nxt.start()] if nxt else text[m.end():]


def classify(text) -> str:
    """missing | stub | empty | populated.

    stub      = no template sections yet (in practice the Claude-session logger's note)
    empty     = the template's sections are there and nothing has been filled in
    populated = a real day is underway -> never touched"""
    if text is None or not text.strip():
        return "missing"
    sched, tasks = _section(text, "Schedule"), _section(text, "Tasks")
    if sched is None or tasks is None:
        return "stub"
    rows = [ln for ln in sched.split("\n") if ln.strip().startswith("|")][2:]   # past header + rule
    if any(c.strip() for ln in rows for c in ln.strip().strip("|").split("|")):
        return "populated"
    if any(re.sub(r"^-\s*\[.\]", "", ln.strip()).strip() for ln in tasks.split("\n") if ln.strip()):
        return "populated"
    if (_section(text, "Notes") or "").strip():
        return "populated"
    refl = _section(text, "End of Day Reflection") or ""
    if re.sub(r"\*\*[^*]+\*\*", "", refl).strip():
        return "populated"
    return "empty"


def merge_stub(existing: str, tpl: str, d: datetime.date) -> str:
    """Keep every existing line; add the health frontmatter keys, the H1 and the template
    sections that are missing (appended after the existing content)."""
    fm, body = _split_fm(existing)
    filled = from_template(tpl, d)
    tfm, tbody = _split_fm(filled)
    if fm is None:
        fm, body = [f'date: "{d.isoformat()}"', "type: daily"], existing
    have = {ln.split(":", 1)[0].strip() for ln in fm if ":" in ln and not ln.startswith((" ", "\t", "#"))}
    fm = fm + [f"{k}: " for k in HEALTH_KEYS if k not in have]
    if not re.search(r"(?m)^# ", body):
        body = f"\n# {h1_for(d)}\n" + ("\n" + body.lstrip("\n") if body.strip() else "")
    add = []
    for name in SECTIONS:
        if _section(body, name) is None:
            sec = _section(tbody or "", name)
            if sec is not None:
                add.append(f"## {name}\n{sec.rstrip()}\n")
    body = body.rstrip("\n") + "\n\n" + "\n".join(add) if add else body
    return "---\n" + "\n".join(fm) + "\n---\n" + body


# ----------------------------------------------------------------- calendar
def day_window(d: datetime.date) -> tuple[str, str]:
    """ISO start/end of the Eastern day WITH THE REAL OFFSET (-04:00 in summer, -05:00 in
    winter). The old prompt typed -04:00, so from Nov 1 the window was an hour off."""
    start = datetime.datetime.combine(d, datetime.time(0, 0), TZ)
    end = datetime.datetime.combine(d, datetime.time(23, 59, 59), TZ)
    return start.isoformat(), end.isoformat()


def fetch_events(d: datetime.date):
    """List of events, or None when the calendar could not be read (NOT the same as [])."""
    start, end = day_window(d)
    try:
        r = subprocess.run([str(FRAMEWORK_PY), str(GOOGLE_API), "calendar", "list",
                            "--start", start, "--end", end],
                           capture_output=True, text=True, timeout=60)
        if r.returncode != 0:
            log(f"calendar: exit {r.returncode}: {r.stderr.strip()[-300:]}")
            return None
        data = json.loads(r.stdout[r.stdout.index("["):])
        if not isinstance(data, list):
            raise ValueError("not a list")
        return data
    except Exception as e:  # noqa: BLE001
        log(f"calendar: {type(e).__name__}: {e}")
        return None


def schedule_rows(events: list, d: datetime.date) -> list[str]:
    rows = []
    for ev in events:
        if (ev.get("status") or "").lower() == "cancelled":
            continue
        title = re.sub(r"\s+", " ", str(ev.get("summary") or "(no title)")).replace("|", "/").strip()
        start = str(ev.get("start") or "")
        try:
            dt = datetime.datetime.fromisoformat(start)
            if dt.tzinfo is None:                       # a bare date = all-day event
                raise ValueError
            dt = dt.astimezone(TZ)
            key = dt if dt.date() == d else datetime.datetime.combine(d, datetime.time(0, 0), TZ)
            label = dt.strftime("%I:%M %p").lstrip("0") if dt.date() == d else "(cont.)"
        except ValueError:
            key, label = datetime.datetime.combine(d, datetime.time(0, 0), TZ), "All day"
        rows.append((key, f"| {label} | {title} |"))
    return [r for _k, r in sorted(rows, key=lambda x: x[0])]


def fill_schedule(text: str, rows: list[str]) -> str:
    if not rows:
        return text
    m = re.search(r"(?m)^## Schedule[ \t]*\n(\|.*\n\|[-| ]+\|[ \t]*\n)((?:\|.*\n?)*)", text)
    if not m:
        raise RuntimeError("Schedule table not found in the note")
    return text[:m.start(2)] + "\n".join(rows) + "\n" + text[m.end(2):]


# ----------------------------------------------------------------- tasks
def today_rows(d: datetime.date):
    """([TODAY] rows, readable?). Rows flagged stale are dropped. readable=False means the
    task list could not be read, which must surface as a task, never as 'nothing to do'."""
    try:
        out = today_actions.build(ACTION_ITEMS.read_text(encoding="utf-8"), d)
    except Exception as e:  # noqa: BLE001
        log(f"tasks: {type(e).__name__}: {e}")
        return [], False
    if "UNKNOWN" in out:
        return [], False
    rows = [ln[len("- [TODAY]"):].strip() for ln in out.split("\n")
            if ln.startswith("- [TODAY]") and "⚠️ STALE" not in ln]
    return rows, True


def _plain(s: str) -> str:
    s = re.sub(r"\[\[([^\]|]+\|)?([^\]]+)\]\]", r"\2", s)          # [[a|b]] -> b
    s = re.sub(r"[*_`~]", "", s)
    return re.sub(r"\s+", " ", s).strip(" —-·")


def shorten_by_rule(row: str) -> str:
    """No-model fallback: the ⬜ step if the row has one, else the row's bold phrases."""
    if "⬜" in row:
        cand = row.split("⬜", 1)[1]
    else:
        bold = re.findall(r"\*\*(.+?)\*\*", row)
        cand = " — ".join(bold[:2]) if bold else row
    cand = _plain(cand)
    return cand if len(cand) <= TASK_MAX else cand[:TASK_MAX - 1].rstrip() + "…"


def _env(key: str):
    v = os.environ.get(key)
    if v:
        return v
    try:
        for ln in ENV_FILE.read_text().splitlines():
            if ln.startswith(f"{key}="):
                return ln.split("=", 1)[1].strip().strip('"').strip("'")
    except OSError:
        pass
    return None


def anthropic_text(system: str, user: str, max_tokens: int = 1200):
    """One Messages call. Returns the reply text or None, and LOGS why on failure. The
    text is the first block whose type is "text": with thinking on, block 0 is a thinking
    block, which is what silently broke every `content[0]["text"]` client."""
    key = _env("ANTHROPIC_API_KEY")
    if not key:
        log("llm: no ANTHROPIC_API_KEY")
        return None
    body = json.dumps({"model": MODEL, "max_tokens": max_tokens, "system": system,
                       "messages": [{"role": "user", "content": user}]}).encode()
    try:
        req = urllib.request.Request(ANTHROPIC_URL, data=body, headers={
            "x-api-key": key, "anthropic-version": "2023-06-01", "content-type": "application/json"})
        with urllib.request.urlopen(req, timeout=45) as r:
            raw = json.loads(r.read())
        if raw.get("stop_reason") == "max_tokens":
            log("llm: reply cut off at max_tokens")
            return None
        text = next((b.get("text", "") for b in raw.get("content", []) if b.get("type") == "text"), "")
        return text.strip() or None
    except Exception as e:  # noqa: BLE001
        log(f"llm: {type(e).__name__}: {e}")
        return None


CONDENSE_SYS = (
    "You turn rows from a personal task tracker into short checklist lines for today's "
    "daily note. For each numbered row write ONE plain line, at most 100 characters: the "
    "action he has to take, with any deadline or time from the row. If the row marks a "
    "remaining step with ⬜, that step is the task. No markdown, no emoji, no commentary, "
    "and never invent a detail that is not in the row. Return ONLY a JSON array of strings "
    "with EXACTLY one string per row, in the same order.")


def condense(rows: list[str], use_llm: bool = True, call=anthropic_text) -> list[str]:
    """One short task per row. A reply of the wrong shape is rejected whole and the rule
    takes over: a dropped row would be a dropped task."""
    if not rows:
        return []
    fallback = [shorten_by_rule(r) for r in rows]
    if not use_llm:
        return fallback
    reply = call(CONDENSE_SYS, "\n".join(f"{i}. {r}" for i, r in enumerate(rows, 1)))
    if not reply:
        return fallback
    try:
        arr = json.loads(reply[reply.index("["): reply.rindex("]") + 1])
        if (not isinstance(arr, list) or len(arr) != len(rows)
                or not all(isinstance(x, str) and x.strip() for x in arr)):
            raise ValueError(f"wrong shape: {len(arr) if isinstance(arr, list) else type(arr).__name__} for {len(rows)} rows")
    except Exception as e:  # noqa: BLE001
        log(f"llm: reply rejected ({e}); using the rule")
        return fallback
    return [_plain(x)[:TASK_MAX + 20] for x in arr]


def inbox_actions(md: str) -> list[str]:
    """`▶️` action lines from the inbox read, skipping any item marked already handled."""
    out, block = [], []

    def flush():
        joined = "\n".join(block)
        if "✅ already handled" not in joined:
            for ln in block:
                if "▶️" in ln:
                    out.append(_plain(ln.split("▶️", 1)[1]))
    for ln in md.split("\n"):
        if ln.startswith("- "):
            flush()
            block = [ln]
        elif block:
            block.append(ln)
    flush()
    return [a for a in out if a]


def fill_tasks(text: str, tasks: list[str]) -> str:
    if not tasks:
        return text
    m = re.search(r"(?m)^## Tasks[ \t]*\n", text)
    if not m:
        raise RuntimeError("Tasks section not found in the note")
    rest = text[m.end():]
    blank = re.match(r"-\s*\[ \][ \t]*\n?", rest)
    rest = rest[blank.end():] if blank else rest
    return text[:m.end()] + "".join(f"- [ ] {t}\n" for t in tasks) + rest


# ----------------------------------------------------------------- inbox
def read_inbox():
    try:
        md = TRIAGE_MD.read_text(encoding="utf-8").strip()
    except OSError:
        return ""
    return "" if not md or INBOX_PLACEHOLDER in md and len(md) < 200 else md


def insert_inbox(text: str, md: str) -> str:
    """Verbatim, as its own section before `## Health` (where it has always sat), else
    before `## Notes`, else at the end. Never inserted twice."""
    if not md or "## 📬 Inbox" in text:
        return text
    block = md.rstrip("\n") + "\n\n"
    for anchor in (r"(?m)^## Health", r"(?m)^## Notes"):
        m = re.search(anchor, text)
        if m:
            return text[:m.start()] + block + text[m.start():]
    return text.rstrip("\n") + "\n\n" + block


# ----------------------------------------------------------------- main
def build_note(current, tpl: str, d: datetime.date, events, tasks: list[str], inbox_md: str):
    """(new_text, state). new_text is None when the note must be left alone."""
    state = classify(current)
    if state == "populated":
        return None, state
    text = from_template(tpl, d) if state == "missing" else (
        merge_stub(current, tpl, d) if state == "stub" else current)
    text = fill_schedule(text, schedule_rows(events or [], d))
    text = fill_tasks(text, tasks)
    text = insert_inbox(text, inbox_md)
    return text, state


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="daily_prefill")
    ap.add_argument("--date", default=None)
    ap.add_argument("--dry-run", action="store_true", help="print the note instead of writing it")
    ap.add_argument("--no-llm", action="store_true")
    a = ap.parse_args(argv)
    d = datetime.date.fromisoformat(a.date) if a.date else datetime.datetime.now(TZ).date()
    note = DAILY_DIR / f"{d.isoformat()}.md"

    current = note.read_text(encoding="utf-8") if note.exists() else None
    state = classify(current)
    if state == "populated":
        log(f"{note.name}: a real day is already underway; left alone")
        return 0

    tpl = TEMPLATE.read_text(encoding="utf-8")
    events = fetch_events(d)
    rows, readable = today_rows(d)
    tasks = condense(rows, use_llm=not a.no_llm)
    if not readable:
        tasks = ["⚠️ Could not read the task list — check Action Items"]
    if events is None:
        tasks.append("⚠️ Could not read the calendar this morning — check it")
    inbox_md = read_inbox()
    tasks += [t for t in inbox_actions(inbox_md) if t not in tasks]

    for _attempt in range(3):
        new_text, state = build_note(current, tpl, d, events, tasks, inbox_md)
        if new_text is None:
            log(f"{note.name}: became a real day while this ran; left alone")
            return 0
        if a.dry_run:
            sys.stderr.write(new_text)
            return 0
        latest = note.read_text(encoding="utf-8") if note.exists() else None
        if latest != current:                 # a sync or a log landed mid-run: redo on top of it
            current = latest
            continue
        DAILY_DIR.mkdir(parents=True, exist_ok=True)
        tmp = note.with_name(note.name + ".prefill.tmp")
        tmp.write_text(new_text if new_text.endswith("\n") else new_text + "\n", encoding="utf-8")
        os.replace(tmp, note)
        log(f"{note.name}: {state} -> filled ({len(events or [])} events, {len(tasks)} tasks, "
            f"inbox {'yes' if inbox_md else 'no'})")
        return 0
    raise RuntimeError(f"{note.name} kept changing underneath the prefill; gave up after 3 tries")


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as e:  # noqa: BLE001
        log(f"FAILED: {type(e).__name__}: {e}")
        print(f"⚠️ Daily note prefill failed: {type(e).__name__}: {e}")
        sys.exit(1)
