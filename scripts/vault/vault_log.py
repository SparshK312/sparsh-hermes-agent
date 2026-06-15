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

# Reuse the single source of truth for the vault root resolution (HERMES_VAULT
# wins, else the VPS path, else the Mac dev path) so writes never split-brain.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "hae"))
from hae_daily_ingest import _default_vault  # noqa: E402

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


# ----------------------------------------------------------------- helpers
def today() -> str:
    now = datetime.datetime.now(TZ) if TZ else datetime.datetime.now()
    return now.strftime("%Y-%m-%d")


def now_time() -> str:
    now = datetime.datetime.now(TZ) if TZ else datetime.datetime.now()
    return now.strftime("%-I:%M %p")


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
        total = _num(cur_value(k)) + float(delta)
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
    return {
        "ok": True, "cmd": "food", "date": date, "meal_type": a.meal_type,
        "meal_kcal": int(round(macros["kcal"])), "meal_protein_g": int(round(macros["protein_g"])),
        "daily_kcal": int(_num(daily["kcal"])), "daily_protein_g": int(_num(daily["protein_g"])),
        "daily_carbs_g": int(_num(daily["carbs_g"])), "daily_fat_g": int(_num(daily["fat_g"])),
        "food_log_total_kcal": file_totals["kcal"],
        "msg": (f"✓ Logged {a.meal_type}: {int(round(macros['kcal']))} kcal · "
                f"{int(round(macros['protein_g']))}g protein. "
                f"Today: {int(_num(daily['kcal']))}/2400 kcal · {int(_num(daily['protein_g']))}/140g P."),
    }


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
    f.add_argument("--source", default="estimated", help="template:<name> | mcp:food-tracker | estimated")
    f.set_defaults(func=cmd_food)

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
