#!/usr/bin/env python3
"""
health_morning_brief_gate.py — the consolidated morning brief, SELF-CONTAINED.

This is a SCRIPT-MODE Hermes cron (no agent half). It runs every ~15 min in the
morning window and decides, ONCE per day, whether to send the brief — event-driven
on last night's sleep landing, with a weekday-09:00 / weekend-11:00 fallback.

When it fires it does EVERYTHING synchronously, so there is no failure-detection
problem and nothing can silently vanish:
  1. refresh the Apple Watch data (hae_process -> metrics.csv, hae_daily_ingest
     -> today's daily-note frontmatter), logging to sync.log (not stdout).
  2. GATHER facts: last night's sleep + yesterday's activity (from metrics.csv),
     today's Schedule + Tasks (from the daily note), Hard Deadlines + this-week
     (from Action Items).
  3. COMPOSE the brief:
       - try compose_rich(): one focused, retryable OpenAI API call (small ~3-4K
         context, immune to the openai-codex big-context broken-pipe that kills
         agent-mode cron runs).
       - on any failure -> compose_templated(): a pure-Python brief from the same
         facts. ALWAYS works, so the morning message ALWAYS lands.
  4. SEND the brief itself via the Telegram Bot API, then print {"wakeAgent": false}
     so Hermes' cron skips the agent entirely (no LLM turn, nothing it could hijack
     or drop). If the direct send fails, it prints the brief instead so Hermes' agent
     delivers it as a fallback. When NOT firing it prints the wake-gate and is silent.

Fire-once is tracked in brief_state.json (last_brief_date), marked only AFTER a
CONFIRMED send, so a delivery failure (or mid-run crash) retries on the next tick.

  --dry-run         force-fire, compose + PRINT, do NOT mark state or refresh-gate
  --no-llm          skip compose_rich (test the templated path only)
"""
from __future__ import annotations

import csv
import datetime
import json
import os
import re
import subprocess
import sys
import urllib.request
import urllib.error
from pathlib import Path
from zoneinfo import ZoneInfo

HOME = Path.home()
SCRIPTS = HOME / ".hermes" / "scripts"
HEALTH = HOME / ".hermes" / "health" / "hae"
STATE = HEALTH / "brief_state.json"
LOG = HEALTH / "sync.log"
ENV_FILE = HOME / ".hermes" / ".env"


def _default_vault() -> Path:
    """HERMES_VAULT wins; else the VPS path if it exists (production), else the
    Mac dev path — same code in both places, no split-brain."""
    env = os.environ.get("HERMES_VAULT")
    if env:
        return Path(env)
    vps = Path("/home/hermes/vault")
    if vps.exists():
        return vps
    return Path.home() / "Documents" / "School Vault - UofT"


VAULT = _default_vault()

# Printed as the final stdout line to skip the Hermes cron agent entirely
# (run_job honors {"wakeAgent": false} → no LLM turn, nothing delivered). The
# brief sends itself via the Bot API, so the agent layer is pure waste + a
# hijack risk; this is the real "silent, $0" gate ([SILENT] is an agent marker,
# not a script-stdout one).
WAKE_GATE_SKIP = '{"wakeAgent": false}'
CSVP = VAULT / "07 - Health" / "Metrics" / "metrics.csv"
DAILY_DIR = VAULT / "04 - Daily Notes"
ACTION_ITEMS = VAULT / "00 - Dashboard" / "Action Items.md"
TZ = ZoneInfo("America/Toronto")
CHAT_ID = "696500863"  # Sparsh

WEEKDAY_CUTOFF = datetime.time(9, 0)
WEEKEND_CUTOFF = datetime.time(11, 0)

# Migrated off OpenRouter 2026-08-26 — one vendor (Anthropic).
ANTHROPIC_MODEL = "claude-haiku-4-5"
ANTHROPIC_URL = "https://api.anthropic.com/v1/messages"

DRY_RUN = "--dry-run" in sys.argv
NO_LLM = "--no-llm" in sys.argv
FORCE = "--force" in sys.argv   # on-demand: refresh data + send NOW, ignore fire-once/cutoff


# ----------------------------------------------------------------------------- refresh
def _refresh() -> None:
    """Run the data pipeline; keep its noise out of stdout (-> sync.log)."""
    try:
        with open(LOG, "a") as lf:
            lf.write(f"\n=== {datetime.datetime.now(TZ).isoformat()} brief-gate refresh ===\n")
            for s in ("hae_process.py", "hae_daily_ingest.py"):
                try:
                    subprocess.run([sys.executable, str(SCRIPTS / s)],
                                   stdout=lf, stderr=lf, timeout=120)
                except Exception as e:  # noqa: BLE001
                    lf.write(f"{s} error: {e}\n")
    except Exception:  # noqa: BLE001
        pass


def _log(msg: str) -> None:
    try:
        with open(LOG, "a") as lf:
            lf.write(f"{datetime.datetime.now(TZ).isoformat()} brief: {msg}\n")
    except Exception:  # noqa: BLE001
        pass


# ----------------------------------------------------------------------------- state + csv
def _load_state() -> dict:
    try:
        return json.loads(STATE.read_text())
    except Exception:  # noqa: BLE001
        return {}


def _save_state(state: dict) -> None:
    try:
        STATE.write_text(json.dumps(state))
    except Exception:  # noqa: BLE001
        _log("WARN: could not write brief_state.json")


def _row_for(date: str) -> dict:
    if not CSVP.exists():
        return {}
    with CSVP.open(newline="") as fh:
        for r in csv.DictReader(fh):
            if r.get("date") == date:
                return r
    return {}


def _fnum(r: dict, k: str):
    try:
        return float(r.get(k))
    except (TypeError, ValueError):
        return None


# ----------------------------------------------------------------------------- gather: vault
def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except Exception:  # noqa: BLE001
        return ""


def _section(text: str, header: str) -> str:
    """Return the lines under a '## header' up to the next '## ' (or '# ') header."""
    lines = text.split("\n")
    out, capturing = [], False
    hl = header.lower()
    for ln in lines:
        if capturing:
            if re.match(r"^#{1,2} ", ln):
                break
            out.append(ln)
        else:
            # Exact header match (allowing trailing text after a space), so
            # "## Schedule" does NOT also capture "## Scheduled Maintenance".
            hs = ln.strip().lower()
            if hs == hl or hs.startswith(hl + " "):
                capturing = True
    return "\n".join(out).strip()


def gather_schedule(today: str) -> list[str]:
    """Non-empty rows of the daily note's '## Schedule' markdown table."""
    note = DAILY_DIR / f"{today}.md"
    sec = _section(_read(note), "## schedule")
    rows = []
    for ln in sec.split("\n"):
        if not ln.strip().startswith("|"):
            continue
        cells = [c.strip() for c in ln.strip().strip("|").split("|")]
        if len(cells) < 2:
            continue
        if cells[0].lower() in ("time", "") and cells[1].lower() in ("activity", ""):
            continue  # header / separator / empty
        if set("".join(cells)) <= set("-: "):
            continue  # separator row
        if any(c for c in cells):
            t, a = cells[0], " ".join(cells[1:]).strip()
            rows.append(f"{t} — {a}".strip(" —") if t else a)
    return [r for r in rows if r]


def gather_tasks(today: str) -> list[str]:
    """Unchecked '- [ ]' items under the daily note's '## Tasks'."""
    note = DAILY_DIR / f"{today}.md"
    sec = _section(_read(note), "## tasks")
    out = []
    for ln in sec.split("\n"):
        m = re.match(r"^\s*-\s*\[ \]\s*(.+)$", ln)
        if m and m.group(1).strip():
            out.append(m.group(1).strip())
    return out


def _clip(s: str, limit: int) -> str:
    """Trim to ~limit chars but cut on a line boundary so no item is sliced mid-line."""
    if len(s) <= limit:
        return s
    cut = s.rfind("\n", 0, limit)
    return s[: cut if cut > 0 else limit].rstrip()


# NOTE: "~~" is deliberately NOT a marker. Striking part of a heading is a normal edit
# in this vault ("### Sign the ~~extension~~ deferral form by Aug 17"), and treating it
# as resolution silently dropped live items. Both genuinely-resolved blocks carry an
# explicit ✅ marker anyway, so nothing is lost.
_RESOLVED_MARKERS = ("✅ resolved", "✅ dropped", "✅ done", "— done", "no longer relevant",
                     "moot", "cancelled", "canceled", "superseded")

# A line worth keeping in the digest: it names a date or a hard deadline.
_DATE_RE = re.compile(
    r"\b(\d{4}-\d{2}-\d{2}"
    r"|(?:mon|tue|wed|thu|fri|sat|sun)[a-z]*\.?,?\s+"
    r"(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?\s*\d{1,2}"
    r"|(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?\s*\d{1,2}"
    r"|\bdeadline\b|\bdue\b|\bby \d{1,2}\s*(?:am|pm)\b)",
    re.I,
)


_MONTHS = {m: i + 1 for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"])}


def _line_date(line: str, today: datetime.date):
    """The date a line is ABOUT: its earliest UPCOMING date, else its earliest date.

    A plain min() over every date in the line was wrong — these rows cite their history
    inline ("Tue Aug 4 (action) → Wed Aug 19 (hard deadline)", or the exam row that also
    mentions "confirmed Aug 3" and "runway Aug 12–21"). min() returned the past
    reference, so the row was classified as past and the CSC384 final and the Aug 19
    registrar deadline were both dropped from the brief."""
    found = []
    for m in re.finditer(r"\b(\d{4})-(\d{2})-(\d{2})\b", line):
        try:
            found.append(datetime.date(int(m.group(1)), int(m.group(2)), int(m.group(3))))
        except ValueError:
            pass
    for m in re.finditer(r"\b(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?\s*(\d{1,2})\b",
                         line, re.I):
        try:
            found.append(datetime.date(today.year, _MONTHS[m.group(1).lower()], int(m.group(2))))
        except ValueError:
            pass
    if not found:
        return None
    future = [d for d in found if d >= today]
    return min(future) if future else min(found)


def _trim_line(line: str, limit: int = 170) -> str:
    """Shorten a kept line to its gist, cutting on a word boundary.
    For a markdown table row, keep the first two cells (the date and what it is) and
    drop the trailing notes column, which is where the length lives."""
    s = line.strip()
    if s.startswith("|"):
        cells = [c.strip() for c in s.strip("|").split("|")]
        if len(cells) >= 2:
            s = f"| {cells[0]} | {cells[1]}"
    if len(s) <= limit:
        return s
    cut = s.rfind(" ", 0, limit)
    return s[: cut if cut > 40 else limit].rstrip() + "…"


def _condense_deadlines(section: str, per_block: int = 14) -> str:
    """Heading + the few dated lines under it, per block.

    Dropping resolved blocks alone did NOT achieve the goal. Measured against the real
    Action Items: after pruning, the Aug 19 / Aug 15 / Aug 17 / Aug 22 items sat at
    offsets 4385-6466, all past the 2600-char clip — one live block's 18 lines of
    supporting argument consumed the entire budget, and the clip ended up containing
    NONE of those dates (strictly worse than before for "Aug 22").

    The brief needs the dated headlines, not the reasoning behind them. Keeping each
    block's heading plus its dated lines fits every block inside the budget."""
    blocks: list[list[str]] = [[]]
    for line in section.split("\n"):
        if line.startswith("### "):
            blocks.append([line])
        else:
            blocks[-1].append(line)

    out: list[str] = []
    for b in blocks:
        if not b:
            continue
        heading = b[0] if b[0].startswith("### ") else ""
        if heading and any(mk in heading.lower() for mk in _RESOLVED_MARKERS):
            continue                                   # settled — drop the whole block
        # Prefer UPCOMING dates. These blocks are chronological, so taking the first N
        # dated lines pulled JULY rows out of a Jul–Aug table and still surfaced none of
        # the deadlines that matter (Aug 15/17/19/22).
        cands = [ln for ln in b[1:] if ln.strip() and _DATE_RE.search(ln)]
        today = datetime.datetime.now(TZ).date()
        upcoming = []
        for ln in cands:
            d = _line_date(ln, today)
            if d and d >= today:
                upcoming.append((d, ln))
        upcoming.sort(key=lambda x: x[0])
        # ONLY upcoming lines. Topping up with past/undated prose let one block's
        # 600-char argument paragraphs back in and pushed the later deadlines out of
        # the clip again. A block with nothing upcoming contributes just its heading,
        # which is the right amount of signal for a morning brief.
        # Truncate each line. Some collisions rows carry a 500-char notes column, so 17
        # kept lines came to 5,492 chars and the 2600 clip then dropped the back half —
        # which is how Aug 17/19/21 kept falling out even once they were selected. The
        # brief needs the date and the gist, not the full rationale.
        body = [_trim_line(ln) for _, ln in upcoming[:per_block]]
        if heading:
            out.append(heading)
        elif not body:
            continue                                   # preamble with nothing dated
        out.extend(body)
    return "\n".join(out).strip()


def gather_action_items() -> dict:
    """Hard Deadlines section text + a trimmed 'this week' slice from Action Items."""
    text = _read(ACTION_ITEMS)
    hard = _section(text, "## 🔴 hard deadlines") or _section(text, "## hard deadlines")
    pruned = _condense_deadlines(hard)
    # Only accept the pruned version if it kept something — never let a heading-format
    # change silently empty the most important input to the brief.
    if pruned:
        hard = pruned
    # 'this week' = the dated plan section if present, else the streams' urgent slice
    plan = ""
    m = re.search(r"^##\s*🗓️.*$", text, re.MULTILINE)
    if m:
        start = m.start()
        nxt = re.search(r"\n##\s", text[start + 3:])
        plan = text[start: start + 3 + (nxt.start() if nxt else 2600)]
    return {"hard_deadlines": _clip(hard, 2600), "this_week": _clip(plan, 2600)}


def gather_facts(today: str, yesterday: str) -> dict:
    trow = _row_for(today)
    yrow = _row_for(yesterday)
    sleep_present = bool(trow.get("sleep_total_h"))

    sleep = {}
    if sleep_present:
        for k in ("sleep_total_h", "sleep_core_h", "sleep_deep_h", "sleep_rem_h",
                  "sleep_awake_h", "resting_hr", "hrv_ms"):
            v = _fnum(trow, k)
            if v is not None:
                sleep[k] = v
        with CSVP.open(newline="") as fh:
            recent = [v for r in csv.DictReader(fh)
                      if r.get("date", "") < today and (v := _fnum(r, "sleep_total_h")) is not None]
        if recent[-7:]:
            sleep["avg7_h"] = round(sum(recent[-7:]) / len(recent[-7:]), 1)

    activity = {}
    for k in ("steps", "active_kcal", "exercise_min"):
        v = _fnum(yrow, k)
        if v is not None:
            activity[k] = int(v)

    ai = gather_action_items()
    return {
        "date": today,
        "sleep_synced": sleep_present,
        "sleep": sleep,
        "yesterday_activity": activity,
        "schedule": gather_schedule(today),
        "tasks": gather_tasks(today),
        "inbox": gather_inbox(),
        "board": gather_board(),
        "hard_deadlines": ai["hard_deadlines"],
        "this_week": ai["this_week"],
    }


# ----------------------------------------------------------------------------- compose: shared sleep block
def _sleep_lines(s: dict, synced: bool) -> list[str]:
    """The sleep + recovery bullet lines — shared by the morning brief AND the
    later sleep-follow-up (so both render identically). Returns the lines (or a
    'didn't sync yet' note when sleep isn't in the archive)."""
    if not (synced and s):
        return ["_(Apple Watch sleep hasn't synced yet — `/sleep <hrs>` to log manually.)_"]
    out = []
    tot = s.get("sleep_total_h")
    flag = "✅" if (tot or 0) >= 7 else "⚠️"
    line = f"{flag} Slept *{tot:.1f}h*" if tot is not None else "Sleep:"
    if "avg7_h" in s:
        line += f"  _(7-day avg {s['avg7_h']:.1f}h)_"
    out.append(line)
    extras = []
    for k, lab, fmt in (("sleep_deep_h", "deep", "{:.1f}h"), ("sleep_rem_h", "REM", "{:.1f}h"),
                        ("resting_hr", "RHR", "{:.0f}"), ("hrv_ms", "HRV", "{:.0f}ms")):
        if k in s:
            extras.append(f"{lab} " + fmt.format(s[k]))
    if extras:
        out.append("   " + " · ".join(extras))
    if tot is not None and tot < 7:
        out.append("   _Under your 7h floor — guard sleep tonight._")
    return out


# ----------------------------------------------------------------------------- compose: templated (always works)
def compose_templated(facts: dict) -> str:
    now = datetime.datetime.now(TZ)
    parts = [f"🌅 *Morning, Sparsh.* {now.strftime('%a %b %-d')}.", ""]
    parts += _sleep_lines(facts["sleep"], facts["sleep_synced"])

    a = facts["yesterday_activity"]
    if a:
        bits = []
        if "steps" in a:
            bits.append(f"{a['steps']:,} steps")
        if "active_kcal" in a:
            bits.append(f"{a['active_kcal']} active kcal")
        if "exercise_min" in a:
            bits.append(f"{a['exercise_min']} exercise min")
        if bits:
            parts.append("")
            parts.append("Yesterday: " + " · ".join(bits))

    if facts["schedule"]:
        parts += ["", "*Today*"] + [f"• {r}" for r in facts["schedule"]]
    if facts["tasks"]:
        parts += ["", "*Due today*"] + [f"• {t}" for t in facts["tasks"]]

    # hard deadlines: pull the bolded item lines, SKIPPING anything already done
    hd = []
    for ln in facts["hard_deadlines"].split("\n"):
        if "✅" in ln or "~~" in ln or re.match(r"^\s*-\s*\[x\]", ln, re.I):
            continue  # completed — never surface as upcoming
        m = re.search(r"\*\*(.+?)\*\*(.*)", ln)
        if m:
            tail = re.sub(r"\s+", " ", m.group(2)).strip(" —-")
            hd.append(f"• {m.group(1)}" + (f" — {tail[:80]}" if tail else ""))
    if hd:
        parts += ["", "*This week*"] + hd[:4]

    parts += ["", "_(plain brief — rich compose was unavailable this morning)_"]
    return "\n".join(parts).strip()


# ----------------------------------------------------------------------------- compose: rich (OpenAI, retryable)
def _env(key: str) -> str | None:
    v = os.environ.get(key)
    if v:
        return v
    try:
        for ln in ENV_FILE.read_text().splitlines():
            if ln.startswith(f"{key}="):
                return ln.split("=", 1)[1].strip().strip('"').strip("'")
    except Exception:  # noqa: BLE001
        pass
    return None


def _anthropic_key() -> str | None:
    return _env("ANTHROPIC_API_KEY")


def send_message(text: str) -> bool:
    """Send the brief straight to Telegram via the Bot API — so NO Hermes agent layer
    can hijack, rewrite, or drop it (the obsidian-vault-write hijack on 2026-06-08).
    Tries Markdown, falls back to plain text. Returns True on a confirmed send."""
    import urllib.parse
    token = _env("TELEGRAM_BOT_TOKEN")
    if not token:
        _log("send_message: no TELEGRAM_BOT_TOKEN")
        return False
    for pm in ("Markdown", None):
        payload = {"chat_id": CHAT_ID, "text": text}
        if pm:
            payload["parse_mode"] = pm
        try:
            req = urllib.request.Request(
                f"https://api.telegram.org/bot{token}/sendMessage",
                data=urllib.parse.urlencode(payload).encode())
            with urllib.request.urlopen(req, timeout=20) as r:
                if json.loads(r.read()).get("ok"):
                    _log(f"send_message: sent ({pm or 'plain'})")
                    return True
        except Exception as e:  # noqa: BLE001
            _log(f"send_message ({pm}) failed: {e}")
    return False


# ── inbox + board facts ──────────────────────────────────────────────────────
# Added 2026-09-04. The brief was composing from schedule + tasks + sleep only, so it
# was confidently stale: it listed "Figma → Point72 → NVIDIA ×2 → SpaceX" on Sep 3 AND
# Sep 4 when Figma and SpaceX had been applied on Aug 29, and it never mentioned that a
# TikTok CodeSignal OA had landed — the single most important thing in the inbox that
# week. The model was fine; it was being handed a to-do list scraped from a daily note.
TRIAGE_JSON = Path.home() / ".hermes" / "health" / "email_triage.json"


def gather_inbox() -> list:
    """Today's email triage, already classified by the 06:40 job."""
    try:
        d = json.loads(TRIAGE_JSON.read_text())
    except Exception:  # noqa: BLE001
        return []
    items = d.get("items") or []
    out = []
    for it in items:
        out.append({k: it.get(k) for k in
                    ("category", "company", "summary", "action", "urgency",
                     "status_change", "matched_application") if it.get(k)})
    return out


def gather_board() -> dict:
    """Live job board — what is open, what is new, what he has ALREADY applied to."""
    try:
        sys.path.insert(0, str(Path.home() / ".hermes" / "scripts" / "internship"))
        from board_facts import board_facts
        return board_facts()
    except Exception as e:  # noqa: BLE001 - a brief without a board section is fine
        _log(f"gather_board failed: {type(e).__name__}: {e}")
        return {}


SYSTEM_PROMPT = (
    "You compose Sparsh's terse morning brief for Telegram (plain markdown: *bold*, "
    "_italic_, bullets render). Sections, SKIPPING any that are empty:\n"
    "1. Header: '🌅 Morning, Sparsh. <Day Mon D> — <the single main thing today, one short phrase>.'\n"
    "2. Sleep & recovery — from SLEEP facts: hours (⚠️ flag if under his 7h floor), deep/REM, "
    "RHR, HRV, 7-day avg. If sleep didn't sync, say so and suggest `/sleep <hrs>`. Add ONE short "
    "coaching line ONLY if sleep is notably low or a clear trend.\n"
    "3. Today — events from the Schedule (time + short title). Skip if none.\n"
    "4. Due today — today's Tasks. Skip if none.\n"
    "5. 📬 Inbox — ONLY if `inbox` is non-empty. Lead with anything urgency=high or a "
    "status_change (an OA, an interview invite, a rejection). Say what actually happened "
    "in a sentence and what it means, then the action. This is NEWS, not a checklist — if "
    "an assessment invite arrived, that leads the whole brief, not a 'review practice' line.\n"
    "6. 🎯 Apply today — ONLY if `board` is non-empty. Name 2-4 SPECIFIC roles from "
    "board.top_targets (company + short role + fit). Prefer board.new_last_2_days when it "
    "has anything — newly-opened roles at good companies are the whole point of applying "
    "early. 🔴 NEVER suggest a company in board.applied_recent or board.live_pipeline; "
    "those are already done and suggesting them destroys trust in the brief. If "
    "board.applied_last_7_days is 0, say so plainly in one line.\n"
    "7. This week — 2-4 most time-sensitive items from Hard Deadlines / the plan, with explicit dates.\n"
    "8. Closing one-liner: the single highest-leverage focus, or an urgent flag.\n"
    "STYLE: terse, no padding, bullets not paragraphs, no 'In summary' / 'Hope this helps'. "
    "Scale length to content (quiet day 80-150 words; packed day up to ~400). Use ONLY the facts "
    "given; never invent events or deadlines. ⚠️ The Tasks list can be days old and is NOT "
    "authoritative about the job search — `board` is. If a task says to apply somewhere that "
    "`board` shows as already applied, DROP it silently and use a real target instead."
)


def compose_rich(facts: dict) -> str | None:
    if NO_LLM:
        return None
    key = _anthropic_key()
    if not key:
        _log("compose_rich: no ANTHROPIC_API_KEY")
        return None

    user = json.dumps(facts, ensure_ascii=False, indent=2)
    # Anthropic: system is a top-level field (not a message role), the token
    # budget is max_tokens, and the reply text is content[0].text.
    body = json.dumps({
        "model": ANTHROPIC_MODEL,
        "max_tokens": 2000,
        "system": SYSTEM_PROMPT,
        "messages": [{"role": "user",
                      "content": f"Compose today's brief from these facts:\n{user}"}],
    }).encode("utf-8")

    last_err = None
    for attempt in range(1, 4):
        try:
            req = urllib.request.Request(
                ANTHROPIC_URL, data=body,
                headers={"x-api-key": key, "anthropic-version": "2023-06-01",
                         "content-type": "application/json"})
            with urllib.request.urlopen(req, timeout=45) as resp:
                data = json.loads(resp.read())
            # Cut off at max_tokens still returns HTTP 200 with valid-looking
            # text; a mid-sentence brief would be sent with nothing to catch it.
            if data.get("stop_reason") == "max_tokens":
                last_err = "truncated at max_tokens"
                _log(f"compose_rich: attempt {attempt} truncated at max_tokens")
                continue
            out = data["content"][0]["text"].strip()
            if out:
                _log(f"compose_rich: ok (attempt {attempt}, {len(out)} chars)")
                return out
            last_err = "empty content"
        except Exception as e:  # noqa: BLE001
            last_err = str(e)
            _log(f"compose_rich: attempt {attempt} failed: {last_err}")
    _log(f"compose_rich: giving up after 3 tries ({last_err}) -> templated")
    return None


# ----------------------------------------------------------------------------- compose: sleep follow-up
def compose_followup(facts: dict, brief_steps) -> str:
    """The short 'sleep just landed' follow-up — sent later in the morning when the
    brief had to go out before the watch's sleep session synced (it ends when you
    wake ~9am, so HAE often pushes it AFTER the 9am brief). Also corrects yesterday's
    step total if the morning re-sync filled it in materially higher than the brief had."""
    parts = ["😴 *Sleep synced.*", ""]
    parts += _sleep_lines(facts["sleep"], facts["sleep_synced"])
    cur = facts["yesterday_activity"].get("steps")
    if cur is not None and brief_steps is not None and cur > brief_steps + 500:
        parts += ["", f"📊 Yesterday's steps updated to *{cur:,}* "
                      f"_(brief had {brief_steps:,} — data was still syncing)._"]
    return "\n".join(parts).strip()


# ----------------------------------------------------------------------------- main
def main() -> int:
    if not DRY_RUN:
        _refresh()
    now = datetime.datetime.now(TZ)
    today = now.strftime("%Y-%m-%d")
    yesterday = (now - datetime.timedelta(days=1)).strftime("%Y-%m-%d")
    is_weekend = now.strftime("%a") in ("Sat", "Sun")
    cutoff = WEEKEND_CUTOFF if is_weekend else WEEKDAY_CUTOFF

    # --dry-run / --force always compose+deliver the FULL brief immediately.
    if DRY_RUN or FORCE:
        facts = gather_facts(today, yesterday)
        brief = compose_rich(facts) or compose_templated(facts)
        if DRY_RUN:
            print(brief)
            return 0
        if send_message(brief):
            st = _load_state()
            st.update({"last_brief_date": today, "brief_had_sleep": facts["sleep_synced"],
                       "brief_steps": facts["yesterday_activity"].get("steps")})
            _save_state(st)
            print(WAKE_GATE_SKIP)
        else:
            print(brief)
        return 0

    state = _load_state()
    trow = _row_for(today)
    sleep_present = bool(trow.get("sleep_total_h"))

    # ---- Case A: the brief hasn't gone out today yet ----
    if state.get("last_brief_date") != today:
        if not (sleep_present or now.time() >= cutoff):
            print(WAKE_GATE_SKIP)   # still waiting for sleep to land / the cutoff
            return 0
        facts = gather_facts(today, yesterday)
        brief = compose_rich(facts) or compose_templated(facts)
        # Deliver DIRECTLY via Bot API (no agent layer can hijack/drop it). Only mark
        # the day done on a confirmed send, so a failure re-fires on the next tick.
        if send_message(brief):
            state.update({"last_brief_date": today, "brief_had_sleep": sleep_present,
                          "brief_steps": facts["yesterday_activity"].get("steps")})
            _save_state(state)
            print(WAKE_GATE_SKIP)
        else:
            print(brief)
        return 0

    # ---- Case B: brief already went out WITHOUT sleep, and sleep has since landed ----
    # Fire ONE short follow-up so last night's sleep (+ any corrected step total) still
    # reaches him — fixes the race where the brief fires at the 9am cutoff but the watch
    # only pushes its sleep session ~9:40 (it ends when he wakes).
    if (not state.get("brief_had_sleep")
            and sleep_present
            and state.get("sleep_followup_date") != today):
        facts = gather_facts(today, yesterday)
        if send_message(compose_followup(facts, state.get("brief_steps"))):
            state["sleep_followup_date"] = today
            _save_state(state)
        print(WAKE_GATE_SKIP)
        return 0

    print(WAKE_GATE_SKIP)   # nothing to do this tick
    return 0


if __name__ == "__main__":
    sys.exit(main())
