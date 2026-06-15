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

OPENAI_MODEL = "gpt-5.4-mini"
OPENAI_URL = "https://api.openai.com/v1/chat/completions"

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


def gather_action_items() -> dict:
    """Hard Deadlines section text + a trimmed 'this week' slice from Action Items."""
    text = _read(ACTION_ITEMS)
    hard = _section(text, "## 🔴 hard deadlines") or _section(text, "## hard deadlines")
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


def _openai_key() -> str | None:
    return _env("OPENAI_API_KEY")


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


SYSTEM_PROMPT = (
    "You compose Sparsh's terse morning brief for Telegram (plain markdown: *bold*, "
    "_italic_, bullets render). Sections, SKIPPING any that are empty:\n"
    "1. Header: '🌅 Morning, Sparsh. <Day Mon D> — <the single main thing today, one short phrase>.'\n"
    "2. Sleep & recovery — from SLEEP facts: hours (⚠️ flag if under his 7h floor), deep/REM, "
    "RHR, HRV, 7-day avg. If sleep didn't sync, say so and suggest `/sleep <hrs>`. Add ONE short "
    "coaching line ONLY if sleep is notably low or a clear trend.\n"
    "3. Today — events from the Schedule (time + short title). Skip if none.\n"
    "4. Due today — today's Tasks. Skip if none.\n"
    "5. This week — 2-4 most time-sensitive items from Hard Deadlines / the plan, with explicit dates.\n"
    "6. Closing one-liner: the single highest-leverage focus, or an urgent flag.\n"
    "STYLE: terse, no padding, bullets not paragraphs, no 'In summary' / 'Hope this helps'. "
    "Scale length to content (quiet day 80-150 words; packed day up to ~400). Use ONLY the facts "
    "given; never invent events or deadlines."
)


def compose_rich(facts: dict) -> str | None:
    if NO_LLM:
        return None
    key = _openai_key()
    if not key:
        _log("compose_rich: no OPENAI_API_KEY")
        return None

    user = json.dumps(facts, ensure_ascii=False, indent=2)
    body = json.dumps({
        "model": OPENAI_MODEL,
        "messages": [{"role": "system", "content": SYSTEM_PROMPT},
                     {"role": "user", "content": f"Compose today's brief from these facts:\n{user}"}],
        # gpt-5-class: uses max_completion_tokens (not max_tokens), and reserves
        # tokens for reasoning before output — keep this generous so the brief isn't
        # starved. temperature is omitted (these models only accept the default).
        "max_completion_tokens": 2000,
    }).encode("utf-8")

    last_err = None
    for attempt in range(1, 4):
        try:
            req = urllib.request.Request(
                OPENAI_URL, data=body,
                headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=45) as resp:
                data = json.loads(resp.read())
            out = data["choices"][0]["message"]["content"].strip()
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
