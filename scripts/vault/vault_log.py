#!/usr/bin/env python3
"""
vault_log.py — the deterministic vault writer for the health-logging skills.

WHY THIS EXISTS (read before "simplifying" it back to inline edits):
The log-* skills used to free-hand their vault writes — the LLM would read the
daily note, then edit the YAML frontmatter with `patch`, inline `python3 -c`,
heredocs, or `execute_code`. Three problems, all real and all observed in the
live logs:

  1. FRICTION — `python3 -c …`, `python … <<EOF`, `bash -c …` and friends all
     match Hermes' dangerous-command patterns (`tools/approval.py`), so every
     single meal/water/weight log popped an "approve this command?" prompt.
  2. CRON-BLOCKED — `execute_code` is hard-blocked in cron ("Cron jobs run
     without a user present to approve"), so the same logic can't run unattended.
  3. UNRELIABLE — hand-patching repeated `key:` lines drifts (the live logs show
     "Found 10 matches for old_string" / "old and new are identical" failures).

Invoked as `python3 /abs/path/vault_log.py <cmd> <flags>` this script matches NO
dangerous pattern (verified against tools.approval.detect_dangerous_command), so
it runs with ZERO approval friction in chat AND in cron, and it does a single
safe line-targeted frontmatter edit (the proven hae_daily_ingest pattern) that
never clobbers unrelated fields or the note body.

The LLM still does the smart part (parse the meal, run vision, look up macros,
confirm with the user). This script does only the deterministic part: the write.

Subcommands (every one defaults --date to today, America/Toronto):
  food      append a meal to the Food Log file + add its macros to the daily note
  water     add water_l to the daily note
  weight    set weight (a snapshot, replaces)
  sleep     set sleep_hours (+ optional --quality)
  vitamins  set vitamins_taken true (+ optional --supplements a,b,c)
  show      print the daily note's health frontmatter (read-only)

Stdlib only -> runs on the system python3 (no venv needed). Prints a one-line
confirmation the calling skill can relay; --json prints a machine-readable line.
"""
from __future__ import annotations

import argparse
import datetime
import json
import re
import sys
from pathlib import Path

def _default_vault() -> Path:
    """Resolve the vault root — same contract as hae_daily_ingest (kept INLINE, not
    imported, because deploy.sh flattens scripts/hae/ into ~/.hermes/scripts/ so a
    cross-module import breaks on the VPS). HERMES_VAULT wins; else the VPS path if
    it exists (production); else the Mac dev path — so the same code runs in both
    places with no env var and read/write paths never split-brain."""
    import os
    env = os.environ.get("HERMES_VAULT")
    if env:
        return Path(env)
    vps = Path("/home/hermes/vault")
    if vps.exists():
        return vps
    return Path.home() / "Documents" / "School Vault - UofT"


VAULT = _default_vault()
DAILY_DIR = VAULT / "04 - Daily Notes"
FOODLOG_DIR = VAULT / "07 - Health" / "Food Log"
TEMPLATE = VAULT / "Templates" / "Daily Note.md"

try:
    from zoneinfo import ZoneInfo
    TZ = ZoneInfo("America/Toronto")
except Exception:  # noqa: BLE001 — zoneinfo always present on 3.9+, belt-and-suspenders
    TZ = None

# Daily-note frontmatter field types — drives formatting + add-vs-set semantics.
INT_FIELDS = {"kcal", "protein_g", "carbs_g", "fat_g"}
FLOAT1_FIELDS = {"water_l"}

# Targets (Profile.md baseline) — for the optional post-log coach nudge.
KCAL_TARGET = 2400
PROTEIN_TARGET = 140
EAT_WINDOW = (7.0, 23.0)   # eating day used to compute "expected by now"


# ----------------------------------------------------------------- helpers
def today() -> str:
    now = datetime.datetime.now(TZ) if TZ else datetime.datetime.now()
    return now.strftime("%Y-%m-%d")


def now_time() -> str:
    now = datetime.datetime.now(TZ) if TZ else datetime.datetime.now()
    return now.strftime("%-I:%M %p")


def coach_nudge(daily_kcal: int, daily_protein: int) -> str:
    """A deterministic, instant post-log reaction grounded in real numbers + time of
    day — the lightweight 'coach reacts when I log' feedback. (Deep coaching stays in
    /coach + the rescues; this is the always-on one-liner, no LLM call, no latency.)"""
    n = datetime.datetime.now(TZ) if TZ else datetime.datetime.now()
    h = n.hour + n.minute / 60
    frac = max(0.0, min(1.0, (h - EAT_WINDOW[0]) / (EAT_WINDOW[1] - EAT_WINDOW[0])))
    exp_k = round(KCAL_TARGET * frac)
    rem_k, rem_p = KCAL_TARGET - daily_kcal, PROTEIN_TARGET - daily_protein
    behind = daily_kcal < exp_k - 300
    if behind:
        status = f"⚠️ behind pace — ~{exp_k - daily_kcal} kcal short of where you should be by now"
    elif rem_k <= 0:
        status = "✅ at target"
    else:
        status = "✅ on pace"
    bits = [f"📊 {daily_kcal}/{KCAL_TARGET} kcal · {daily_protein}/{PROTEIN_TARGET}g P", status]
    if rem_p > 0 and (behind or rem_p > PROTEIN_TARGET * (1 - frac) + 20):
        nxt = f"next meal {min(rem_k, 900)} kcal+, protein-heavy" if rem_k > 0 else "still need protein"
        bits.append(f"→ {rem_p}g protein to go — {nxt}")
    return "  ".join(bits)


def _valid_date(d: str) -> str:
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", d):
        raise SystemExit(f"refusing malformed date: {d!r}")
    return d


def _fmt(field: str, value: float) -> str:
    if field in INT_FIELDS:
        return str(int(round(value)))
    if field in FLOAT1_FIELDS:
        return f"{value:.1f}"
    # generic numeric: drop trailing .0
    return f"{value:g}"


def _num(s: str) -> float:
    """Parse a frontmatter value to a number; empty/None/non-numeric -> 0.0."""
    if s is None:
        return 0.0
    s = s.strip().strip('"').strip("'")
    try:
        return float(s)
    except ValueError:
        return 0.0


def ensure_daily_note(date: str) -> Path:
    """Return the daily-note path, creating a minimal valid one if it's missing.

    The 6:50 AM daily-note-prefill cron normally creates the rich note; this is a
    fallback for logging before that fires (or on a backfill date). We seed from
    the template when present (substituting the date), else a minimal frontmatter
    stub — either way every health field exists so later edits are line-targeted.
    """
    note = DAILY_DIR / f"{date}.md"
    if note.exists():
        return note
    DAILY_DIR.mkdir(parents=True, exist_ok=True)
    if TEMPLATE.exists():
        body = TEMPLATE.read_text(encoding="utf-8")
        # Templater placeholders -> the literal date (best-effort; harmless if absent).
        body = body.replace('"{{date}}"', f'"{date}"').replace("{{date}}", date)
        body = re.sub(r"\{\{date:[^}]+\}\}", date, body)
    else:
        body = (
            "---\n"
            f'date: "{date}"\n'
            "type: daily\n"
            "weight: \nsleep_hours: \nsleep_quality: \n"
            "kcal: \nprotein_g: \ncarbs_g: \nfat_g: \nwater_l: \n"
            "lifted: \nenergy: \nmood: \n"
            "---\n\n"
            f"# {date}\n\n## Notes\n"
        )
    note.write_text(body, encoding="utf-8")
    return note


def edit_frontmatter(note: Path, *, sets: dict | None = None, adds: dict | None = None) -> dict:
    """Line-targeted frontmatter edit: `sets` replaces a value, `adds` increments a
    numeric value (missing/empty treated as 0). Every other line stays byte-identical.
    Returns the final values of all touched fields.

    Mirrors hae_daily_ingest.update_frontmatter's safety contract (no YAML reflow,
    no pyyaml, never touches the body) but adds the increment mode the log skills need.
    """
    sets = sets or {}
    adds = adds or {}
    text = note.read_text(encoding="utf-8")
    lines = text.split("\n")
    if not lines or lines[0].strip() != "---":
        raise SystemExit(f"{note.name}: no frontmatter; refusing to write (avoid corruption)")
    end = next((i for i in range(1, len(lines)) if lines[i].strip() == "---"), None)
    if end is None:
        raise SystemExit(f"{note.name}: unterminated frontmatter; refusing to write")

    fm = lines[1:end]
    index = {}
    for idx, ln in enumerate(fm):
        if ln and not ln.startswith((" ", "\t", "#")) and ":" in ln:
            index[ln.split(":", 1)[0].strip()] = idx

    final: dict[str, str] = {}

    def cur_value(key: str) -> str:
        return fm[index[key]].split(":", 1)[1].strip() if key in index else ""

    for k, v in sets.items():
        val = str(v)
        if k in index:
            fm[index[k]] = f"{k}: {val}"
        else:
            fm.append(f"{k}: {val}")
            index[k] = len(fm) - 1
        final[k] = val

    for k, delta in adds.items():
        total = max(0.0, _num(cur_value(k)) + float(delta))   # clamp ≥0 (undo subtracts)
        val = _fmt(k, total)
        if k in index:
            fm[index[k]] = f"{k}: {val}"
        else:
            fm.append(f"{k}: {val}")
            index[k] = len(fm) - 1
        final[k] = val

    note.write_text("\n".join([lines[0]] + fm + lines[end:]), encoding="utf-8")
    return final


# ----------------------------------------------------------------- food log file
def _parse_item(spec: str) -> dict:
    """Parse a --item spec 'name|kcal|protein|carbs|fat' (macros optional)."""
    parts = [p.strip() for p in spec.split("|")]
    name = parts[0]
    nums = [(_num(p) if p else 0.0) for p in parts[1:]]
    nums += [0.0] * (4 - len(nums))
    return {"name": name, "kcal": nums[0], "protein_g": nums[1], "carbs_g": nums[2], "fat_g": nums[3]}


def append_food_log(date: str, meal_type: str, time_str: str, items: list[dict],
                    macros: dict, source: str) -> dict:
    """Append a meal section to the Food Log file and recompute its totals.
    Returns the file-level totals after the append."""
    FOODLOG_DIR.mkdir(parents=True, exist_ok=True)
    path = FOODLOG_DIR / f"{date}.md"
    if not path.exists():
        try:
            day_label = datetime.date.fromisoformat(date).strftime("%a %b %-d, %Y")
        except ValueError:
            day_label = date
        path.write_text(
            "---\n"
            "type: food-log\n"
            f"date: {date}\n"
            "total_kcal: 0\ntotal_protein_g: 0\ntotal_carbs_g: 0\ntotal_fat_g: 0\n"
            f"last_updated: {date}\n"
            "---\n\n"
            f"# Food Log — {day_label}\n",
            encoding="utf-8",
        )

    section = [f"\n## {time_str} · {meal_type}", "", "**Items:**"]
    section += [f"- {it['name']}" for it in items] if items else ["- (see macros)"]
    section += [
        "",
        f"**Macros:** {int(round(macros['kcal']))} kcal · {int(round(macros['protein_g']))}g protein "
        f"· {int(round(macros['carbs_g']))}g carbs · {int(round(macros['fat_g']))}g fat",
        f"**Source:** {source}",
        "",
    ]
    text = path.read_text(encoding="utf-8").rstrip("\n") + "\n" + "\n".join(section)

    # Re-sum ALL macro lines (don't trust an incremental counter — pitfall #7).
    totals = {"kcal": 0, "protein_g": 0, "carbs_g": 0, "fat_g": 0}
    for m in re.finditer(
        r"\*\*Macros:\*\*\s*(\d+)\s*kcal.*?(\d+)g protein.*?(\d+)g carbs.*?(\d+)g fat",
        text,
    ):
        totals["kcal"] += int(m.group(1))
        totals["protein_g"] += int(m.group(2))
        totals["carbs_g"] += int(m.group(3))
        totals["fat_g"] += int(m.group(4))

    # Rewrite the file-level frontmatter totals (line-targeted).
    lines = text.split("\n")
    end = next((i for i in range(1, len(lines)) if lines[i].strip() == "---"), None)
    if end:
        repl = {
            "total_kcal": totals["kcal"], "total_protein_g": totals["protein_g"],
            "total_carbs_g": totals["carbs_g"], "total_fat_g": totals["fat_g"],
            "last_updated": date,
        }
        for i in range(1, end):
            key = lines[i].split(":", 1)[0].strip() if ":" in lines[i] else ""
            if key in repl:
                lines[i] = f"{key}: {repl[key]}"
        text = "\n".join(lines)
    path.write_text(text if text.endswith("\n") else text + "\n", encoding="utf-8")
    return totals


# ----------------------------------------------------------------- subcommands
def cmd_food(a) -> dict:
    date = _valid_date(a.date)
    items = [_parse_item(s) for s in (a.item or [])]
    # Totals: explicit flags win; else sum the items.
    if a.kcal is not None:
        macros = {"kcal": a.kcal, "protein_g": a.protein or 0,
                  "carbs_g": a.carbs or 0, "fat_g": a.fat or 0}
    elif items:
        macros = {k: sum(it[k] for it in items) for k in ("kcal", "protein_g", "carbs_g", "fat_g")}
    else:
        raise SystemExit("food: need --kcal (with optional --protein/--carbs/--fat) or --item specs")

    time_str = a.time or now_time()
    file_totals = append_food_log(date, a.meal_type, time_str, items, macros, a.source or "estimated")
    daily = edit_frontmatter(
        ensure_daily_note(date),
        adds={"kcal": macros["kcal"], "protein_g": macros["protein_g"],
              "carbs_g": macros["carbs_g"], "fat_g": macros["fat_g"]},
    )

    # Auto-remember every item with macros to the pantry, so the next time he logs it
    # there's no lookup — the cache fills itself from confirmed logs (no extra agent call).
    if not a.no_remember:
        try:
            sys.path.insert(0, str(Path(__file__).resolve().parent))
            import food_pantry  # noqa: E402
            for it in items:
                if it["kcal"] or it["protein_g"]:
                    food_pantry.remember(it["name"], it["kcal"], it["protein_g"],
                                         it["carbs_g"], it["fat_g"], source=a.source or "logged")
        except Exception:  # noqa: BLE001 — caching is best-effort, never block a log
            pass

    dk, dp = int(_num(daily["kcal"])), int(_num(daily["protein_g"]))
    msg = (f"✓ Logged {a.meal_type}: {int(round(macros['kcal']))} kcal · "
           f"{int(round(macros['protein_g']))}g protein. Today: {dk}/{KCAL_TARGET} kcal · {dp}/{PROTEIN_TARGET}g P.")
    if a.coach:
        msg += "\n" + coach_nudge(dk, dp)
    return {
        "ok": True, "cmd": "food", "date": date, "meal_type": a.meal_type,
        "meal_kcal": int(round(macros["kcal"])), "meal_protein_g": int(round(macros["protein_g"])),
        "daily_kcal": dk, "daily_protein_g": dp,
        "daily_carbs_g": int(_num(daily["carbs_g"])), "daily_fat_g": int(_num(daily["fat_g"])),
        "food_log_total_kcal": file_totals["kcal"], "msg": msg,
    }


def cmd_undo(a) -> dict:
    """Remove the LAST meal section from the Food Log + subtract its macros from the
    daily note — so a correction is undo+re-log (two clean calls), never a hand-edit."""
    date = _valid_date(a.date)
    path = FOODLOG_DIR / f"{date}.md"
    if not path.exists():
        raise SystemExit(f"undo: no food log for {date}")
    text = path.read_text(encoding="utf-8")
    starts = [m.start() for m in re.finditer(r"(?m)^## ", text)]
    if not starts:
        raise SystemExit(f"undo: no meal entries in {date}'s food log")
    last = text[starts[-1]:]
    mm = re.search(r"\*\*Macros:\*\*\s*(\d+)\s*kcal.*?(\d+)g protein.*?(\d+)g carbs.*?(\d+)g fat", last)
    removed = {"kcal": int(mm.group(1)), "protein_g": int(mm.group(2)),
               "carbs_g": int(mm.group(3)), "fat_g": int(mm.group(4))} if mm else {k: 0 for k in INT_FIELDS}
    header = re.search(r"(?m)^## (.+)$", last)
    label = header.group(1) if header else "last meal"

    new_text = text[:starts[-1]].rstrip() + "\n"
    # re-sum remaining macro lines into the file frontmatter totals
    tot = {k: 0 for k in INT_FIELDS}
    for m in re.finditer(r"\*\*Macros:\*\*\s*(\d+)\s*kcal.*?(\d+)g protein.*?(\d+)g carbs.*?(\d+)g fat", new_text):
        tot["kcal"] += int(m.group(1)); tot["protein_g"] += int(m.group(2))
        tot["carbs_g"] += int(m.group(3)); tot["fat_g"] += int(m.group(4))
    lines = new_text.split("\n")
    end = next((i for i in range(1, len(lines)) if lines[i].strip() == "---"), None)
    if end:
        repl = {"total_kcal": tot["kcal"], "total_protein_g": tot["protein_g"],
                "total_carbs_g": tot["carbs_g"], "total_fat_g": tot["fat_g"], "last_updated": date}
        for i in range(1, end):
            key = lines[i].split(":", 1)[0].strip() if ":" in lines[i] else ""
            if key in repl:
                lines[i] = f"{key}: {repl[key]}"
        new_text = "\n".join(lines)
    path.write_text(new_text if new_text.endswith("\n") else new_text + "\n", encoding="utf-8")

    note = DAILY_DIR / f"{date}.md"
    if note.exists():
        edit_frontmatter(note, adds={k: -removed[k] for k in INT_FIELDS})
    return {"ok": True, "cmd": "undo", "date": date, "removed": removed,
            "msg": f"↩️ Removed {label} ({removed['kcal']} kcal · {removed['protein_g']}g P). Re-log the corrected version."}


def cmd_water(a) -> dict:
    date = _valid_date(a.date)
    liters = a.liters if a.liters is not None else (a.ml or 0) / 1000.0
    if liters <= 0:
        raise SystemExit("water: need --ml or --liters (> 0)")
    daily = edit_frontmatter(ensure_daily_note(date), adds={"water_l": liters})
    total = float(daily["water_l"])
    return {"ok": True, "cmd": "water", "date": date, "added_l": round(liters, 2),
            "daily_water_l": total,
            "msg": f"💧 +{liters:.1f}L. Today: {total:.1f}L / 2.5L target."}


def cmd_weight(a) -> dict:
    date = _valid_date(a.date)
    if a.lb is None:
        raise SystemExit("weight: need --lb")
    if not (50 <= a.lb <= 500):
        raise SystemExit(f"weight: {a.lb} lb outside sane range 50–500; pass a corrected value")
    daily = edit_frontmatter(ensure_daily_note(date), sets={"weight": f"{a.lb:g}"})
    return {"ok": True, "cmd": "weight", "date": date, "weight": daily["weight"],
            "msg": f"⚖️ {a.lb:g} lb logged."}


def cmd_sleep(a) -> dict:
    date = _valid_date(a.date)
    if a.hours is None:
        raise SystemExit("sleep: need --hours")
    if not (0 <= a.hours <= 14):
        raise SystemExit(f"sleep: {a.hours}h outside sane range 0–14")
    sets = {"sleep_hours": f"{a.hours:g}"}
    if a.quality is not None:
        sets["sleep_quality"] = str(int(a.quality))
    daily = edit_frontmatter(ensure_daily_note(date), sets=sets)
    flag = "✓ good" if a.hours >= 7 else ("🚨 under 6h" if a.hours < 6 else "⚠ under target")
    return {"ok": True, "cmd": "sleep", "date": date, "sleep_hours": daily["sleep_hours"],
            "msg": f"😴 {a.hours:g}h logged. {flag}"}


def cmd_vitamins(a) -> dict:
    date = _valid_date(a.date)
    sets = {"vitamins_taken": "true"}
    if a.supplements:
        names = [s.strip() for s in a.supplements.split(",") if s.strip()]
        sets["supplements_today"] = "[" + ", ".join(names) + "]"
    edit_frontmatter(ensure_daily_note(date), sets=sets)
    return {"ok": True, "cmd": "vitamins", "date": date,
            "msg": "💊 Vitamins logged."}


def cmd_show(a) -> dict:
    date = _valid_date(a.date)
    note = DAILY_DIR / f"{date}.md"
    if not note.exists():
        return {"ok": True, "cmd": "show", "date": date, "msg": f"{date}: no daily note yet."}
    out = {}
    text = note.read_text(encoding="utf-8")
    lines = text.split("\n")
    end = next((i for i in range(1, len(lines)) if lines[i].strip() == "---"), None)
    for ln in lines[1:end or 1]:
        if ":" in ln and not ln.startswith((" ", "\t", "#")):
            k, v = ln.split(":", 1)
            out[k.strip()] = v.strip()
    return {"ok": True, "cmd": "show", "date": date, "frontmatter": out,
            "msg": f"{date}: " + ", ".join(f"{k}={v}" for k, v in out.items()
                                            if k in (INT_FIELDS | FLOAT1_FIELDS | {"weight", "sleep_hours", "vitamins_taken"}) and v)}


# ----------------------------------------------------------------- workout
# The log-workout SKILL (LLM) parses the message, classifies the action
# (APPEND_NEW_EXERCISE / APPEND_SET_TO_LATEST / UPDATE_SET / FINALIZE), reads the
# existing Workouts/<date>.md, and builds the FULL merged exercises[] array. This
# script does only the deterministic part: write that array as the canonical file
# (regenerate body from frontmatter), set the daily-note `lifted:` field, and best-
# effort update the `**Workout:**` back-compat line. Stdlib-only YAML emit (no pyyaml).
WORKOUTS_DIR = VAULT / "07 - Health" / "Workouts"
_SET_EXTRA_KEYS = ("side", "note", "each_arm")
_EX_SCALAR_KEYS = ("unilateral", "machine", "each_arm", "notes")


def _yv(v) -> str:
    """YAML block-scalar emit: numbers bare, None->null, bools lowercase, strings
    quoted only when needed (colon/special char/ambiguous)."""
    if v is None:
        return "null"
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, int):
        return str(v)
    if isinstance(v, float):
        return f"{v:g}"
    s = str(v)
    if s == "":
        return '""'
    if (re.search(r'[:#\[\]{}&*!|>%@`"\',]', s) or s[0] == " " or s[-1] == " "
            or s.lower() in ("null", "true", "false", "yes", "no")
            or re.fullmatch(r"-?\d+(\.\d+)?", s)):
        return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'
    return s


def _yv_flow(v) -> str:
    """YAML flow-scalar (inside {k: v, ...}) — also quote commas/braces/colons."""
    if v is None:
        return "null"
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, int):
        return str(v)
    if isinstance(v, float):
        return f"{v:g}"
    s = str(v)
    if s == "" or re.search(r'[,:{}\[\]"\']', s):
        return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'
    return s


def _yaml_exercises(exercises: list) -> str:
    """Render exercises[] as an indented YAML block (the source of truth for PR/volume)."""
    if not exercises:
        return "exercises: []"
    out = ["exercises:"]
    for ex in exercises:
        out.append(f"  - name: {_yv(ex.get('name', 'Exercise'))}")
        for k in _EX_SCALAR_KEYS:
            if k in ex and ex[k] not in (None, ""):
                out.append(f"    {k}: {_yv(ex[k])}")
        sets = ex.get("sets") or []
        if not sets:
            out.append("    sets: []")
            continue
        out.append("    sets:")
        for st in sets:
            pairs = [f"weight_lb: {_yv_flow(st.get('weight_lb'))}",
                     f"reps: {_yv_flow(st.get('reps'))}"]
            for k in _SET_EXTRA_KEYS:
                if k in st and st[k] is not None:
                    pairs.append(f"{k}: {_yv_flow(st[k])}")
            out.append("      - {" + ", ".join(pairs) + "}")
    return "\n".join(out)


def _workout_body(date: str, split: str, exercises: list) -> str:
    """Regenerate the human-readable body from the array (never hand-edited)."""
    try:
        day_label = datetime.date.fromisoformat(date).strftime("%a %b %-d, %Y")
    except ValueError:
        day_label = date
    lines = [f"# Workout — {day_label} · {split}", "",
             "> Source: log-workout v2 (vault_log workout). Daily-note **Workout:** line links back here.", ""]
    for ex in exercises:
        sets = ex.get("sets") or []
        tags = ["unilateral"] if ex.get("unilateral") else []
        suffix = f" ({len(sets)} sets{', ' + ', '.join(tags) if tags else ''})" if sets else ""
        lines.append(f"## {ex.get('name', 'Exercise')}{suffix}")
        if ex.get("notes"):
            lines.append(f"*{ex['notes']}*")
        lines.append("")
        for i, st in enumerate(sets, 1):
            w, r = st.get("weight_lb"), st.get("reps")
            wtxt = f"{w:g} lb" if isinstance(w, (int, float)) else (str(w) if w else "—")
            rtxt = f"{r:g}" if isinstance(r, (int, float)) else "—"
            side = f" ({st['side']})" if st.get("side") else ""
            note = f" — {st['note']}" if st.get("note") else ""
            lines.append(f"- Set {i}: {wtxt} × {rtxt}{side}{note}")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def write_workout_file(date: str, split: str, duration_min, exercises: list) -> dict:
    WORKOUTS_DIR.mkdir(parents=True, exist_ok=True)
    path = WORKOUTS_DIR / f"{date}.md"
    total_sets = sum(len(ex.get("sets") or []) for ex in exercises)
    now_iso = (datetime.datetime.now(TZ) if TZ else datetime.datetime.now()).strftime("%Y-%m-%dT%H:%M:%S")
    fm = ["---", "type: workout", f"date: {date}", f"split: {_yv(split)}"]
    if duration_min:
        fm.append(f"duration_min: {int(duration_min)}")
    fm.append(f"total_sets: {total_sets}")
    fm.append(_yaml_exercises(exercises))
    fm.append(f"last_updated: {now_iso}")
    fm.append("---")
    path.write_text("\n".join(fm) + "\n\n" + _workout_body(date, split, exercises), encoding="utf-8")
    return {"path": path, "total_sets": total_sets, "exercise_count": len(exercises)}


def _update_daily_workout_line(note: Path, summary: str) -> None:
    """Best-effort: replace/insert the `- **Workout:**` bullet (under ## Health). The
    `lifted:` frontmatter is the primary record; this is just the quick-scan line."""
    try:
        lines = note.read_text(encoding="utf-8").split("\n")
        for i, ln in enumerate(lines):
            if re.match(r"\s*-?\s*\*\*Workout:\*\*", ln):
                lines[i] = f"- **Workout:** {summary}"
                note.write_text("\n".join(lines), encoding="utf-8")
                return
        for i, ln in enumerate(lines):
            if ln.strip().lower().startswith("## health"):
                lines.insert(i + 1, f"- **Workout:** {summary}")
                note.write_text("\n".join(lines), encoding="utf-8")
                return
    except Exception:  # noqa: BLE001 — best-effort; never fail the log over the scan line
        pass


def cmd_workout(a) -> dict:
    date = _valid_date(a.date)
    try:
        exercises = json.loads(a.exercises) if a.exercises else []
    except json.JSONDecodeError as e:
        raise SystemExit(f"workout: --exercises is not valid JSON: {e}")
    if not isinstance(exercises, list):
        raise SystemExit("workout: --exercises must be a JSON array of exercise objects")
    split = a.split or "Workout"
    info = write_workout_file(date, split, a.duration_min, exercises)

    note = ensure_daily_note(date)
    # lifted: append-with-+ if already populated with a different label (morning lift + later cardio)
    cur = ""
    try:
        for ln in note.read_text(encoding="utf-8").split("\n"):
            if ln.startswith("lifted:"):
                cur = ln.split(":", 1)[1].strip().strip('"').strip("'")
                break
    except Exception:  # noqa: BLE001
        pass
    new_lifted = f"{cur} + {split}" if (cur and split not in cur) else split
    edit_frontmatter(note, sets={"lifted": new_lifted})

    # back-compat **Workout:** quick-scan summary (best-effort)
    parts = []
    for ex in exercises:
        sets = ex.get("sets") or []
        w = next((s.get("weight_lb") for s in sets if s.get("weight_lb") is not None), None)
        wtxt = f"{w:g} lb × {len(sets)}" if isinstance(w, (int, float)) else f"{len(sets)} sets"
        parts.append(f"{ex.get('name', '?')} {wtxt}")
    if parts:
        _update_daily_workout_line(note, f"{split} — " + ", ".join(parts) + f". Full breakdown: [[Workouts/{date}]]")

    return {"ok": True, "cmd": "workout", "date": date, "split": split,
            "total_sets": info["total_sets"], "exercise_count": info["exercise_count"],
            "msg": f"🏋️ Logged: {split}. {info['exercise_count']} exercises, {info['total_sets']} total sets."}


# ----------------------------------------------------------------- cli
def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="vault_log", description="Deterministic vault writer for the health log-* skills.")
    sub = p.add_subparsers(dest="cmd", required=True)

    def add_common(sp):
        sp.add_argument("--date", default=None, help="YYYY-MM-DD (default: today, America/Toronto)")
        sp.add_argument("--json", action="store_true", help="print machine-readable JSON instead of the message")

    f = sub.add_parser("food", help="append a meal + add macros to the daily note")
    add_common(f)
    f.add_argument("--meal-type", required=True, choices=["breakfast", "lunch", "dinner", "snack", "shake"])
    f.add_argument("--time", default=None, help="e.g. '1:30 PM' (default: now)")
    f.add_argument("--kcal", type=float, default=None, help="total kcal (wins over summed items)")
    f.add_argument("--protein", type=float, default=None)
    f.add_argument("--carbs", type=float, default=None)
    f.add_argument("--fat", type=float, default=None)
    f.add_argument("--item", action="append", help="repeatable 'name|kcal|protein|carbs|fat' (macros optional)")
    f.add_argument("--source", default="estimated", help="template:<name> | mcp:food-tracker | pantry | estimated")
    f.add_argument("--coach", action="store_true", help="append a pace/protein nudge to the reply")
    f.add_argument("--no-remember", action="store_true", help="don't cache the items to the pantry")
    f.set_defaults(func=cmd_food)

    u = sub.add_parser("undo-last-meal", help="remove the last food-log meal + subtract its macros (for corrections)")
    add_common(u)
    u.set_defaults(func=cmd_undo)

    w = sub.add_parser("water", help="add water_l to the daily note")
    add_common(w)
    g = w.add_mutually_exclusive_group()
    g.add_argument("--ml", type=float, default=None)
    g.add_argument("--liters", type=float, default=None)
    w.set_defaults(func=cmd_water)

    wt = sub.add_parser("weight", help="set bodyweight (snapshot)")
    add_common(wt)
    wt.add_argument("--lb", type=float, required=True)
    wt.set_defaults(func=cmd_weight)

    s = sub.add_parser("sleep", help="set sleep_hours")
    add_common(s)
    s.add_argument("--hours", type=float, required=True)
    s.add_argument("--quality", type=int, default=None, help="1-10")
    s.set_defaults(func=cmd_sleep)

    v = sub.add_parser("vitamins", help="mark vitamins_taken true")
    add_common(v)
    v.add_argument("--supplements", default=None, help="comma-separated names")
    v.set_defaults(func=cmd_vitamins)

    wo = sub.add_parser("workout", help="write the structured workout file + daily-note lifted/Workout line")
    add_common(wo)
    wo.add_argument("--exercises", dest="exercises", required=True,
                    help="full exercises[] array as JSON. The skill reads the existing file, applies the action, and passes the merged array.")
    wo.add_argument("--split", required=True, help="category label, e.g. 'Push (chest/shoulders/triceps)'")
    wo.add_argument("--duration-min", dest="duration_min", type=int, default=None)
    wo.set_defaults(func=cmd_workout)

    sh = sub.add_parser("show", help="print today's health frontmatter (read-only)")
    add_common(sh)
    sh.set_defaults(func=cmd_show)
    return p


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    if args.date is None:
        args.date = today()
    result = args.func(args)
    if getattr(args, "json", False):
        print(json.dumps(result))
    else:
        print(result.get("msg", json.dumps(result)))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
