#!/usr/bin/env python3
"""
muscle_volume.py — weekly muscle-group volume analysis + chart (Phase 4a tracer).

Reads the structured log-workout v2 files (07 - Health/Workouts/<date>.md), maps
each exercise to the muscle groups it trains, sums SETS per muscle over a window,
and compares against RP-style weekly volume landmarks (MEV / MAV / MRV). Emits:
  - a text summary (reusable by the coach engine + as a Telegram caption)
  - a horizontal-bar chart PNG: actual weekly sets vs the productive (MAV) zone,
    color-coded by status, with MEV (floor) and MRV (ceiling) markers.

Volume currency = SETS (reps are often unlogged). Primary muscle = 1.0 set,
secondary = 0.5 set (standard fractional-counting convention).

  muscle_volume.py [--days N] [--out PATH] [--no-chart]
"""
from __future__ import annotations

import argparse
import datetime
import glob
import os
import re
from collections import defaultdict
from pathlib import Path
from zoneinfo import ZoneInfo

def _default_vault() -> Path:
    """Single source of truth for the vault root across the fitness package
    (heatmap/report/trends import this). HERMES_VAULT wins; else the VPS path if
    it exists (production), else the Mac dev path — so read and write paths can
    never split-brain."""
    env = os.environ.get("HERMES_VAULT")
    if env:
        return Path(env)
    vps = Path("/home/hermes/vault")
    if vps.exists():
        return vps
    return Path.home() / "Documents" / "School Vault - UofT"


VAULT = _default_vault()
WORKOUTS = VAULT / "07 - Health" / "Workouts"
TZ = ZoneInfo("America/Toronto")

# ── Exercise → muscle groups (primary, secondary). Keyed by normalized name. ──
# primary gets 1.0 set credit, secondary 0.5. Extend as new exercises are logged.
MAP: dict[str, tuple[list[str], list[str]]] = {
    "cable fly": (["Chest"], ["Front Delts"]),
    "chest-supported one-arm row machine": (["Lats", "Mid-Back"], ["Rear Delts", "Biceps"]),
    "chest-supported row": (["Lats", "Mid-Back"], ["Rear Delts", "Biceps"]),
    "db hammer curl": (["Biceps"], []),
    "diverging seated row": (["Lats", "Mid-Back"], ["Rear Delts", "Biceps"]),
    "dumbbell shoulder press": (["Front Delts"], ["Side Delts", "Triceps"]),
    "front mts pulldown": (["Lats"], ["Biceps"]),
    "hammer rope curl": (["Biceps"], []),
    "incline chest press machine": (["Chest"], ["Front Delts", "Triceps"]),
    "incline dumbbell bench press": (["Chest"], ["Front Delts", "Triceps"]),
    "incline dumbbell curl": (["Biceps"], []),
    "incline smith machine press": (["Chest"], ["Front Delts", "Triceps"]),
    "smith machine incline press": (["Chest"], ["Front Delts", "Triceps"]),
    "smith machine bench press": (["Chest"], ["Front Delts", "Triceps"]),
    "barbell bench press": (["Chest"], ["Front Delts", "Triceps"]),
    "kelso shrug": (["Mid-Back"], ["Rear Delts"]),
    "lat pulldown": (["Lats"], ["Biceps"]),
    "lateral raise": (["Side Delts"], []),
    "lateral raise machine": (["Side Delts"], []),
    "overhead tricep extension": (["Triceps"], []),
    "overhead triceps extension": (["Triceps"], []),
    "pec deck": (["Chest"], []),
    "preacher curl": (["Biceps"], []),
    "rear delt cable": (["Rear Delts"], []),
    "rear delt fly": (["Rear Delts"], []),
    "rope hammer curl": (["Biceps"], []),
    "rope pushdown": (["Triceps"], []),
    "shoulder press": (["Front Delts"], ["Side Delts", "Triceps"]),
    "single-arm low pushdown": (["Triceps"], []),
    "single-arm underhand triceps extension": (["Triceps"], []),
    "standing preacher curl": (["Biceps"], []),
}

# ── Weekly volume landmarks (sets/week): (MEV, MAV_low, MAV_high, MRV) ──
# MEV=minimum effective, MAV=max-adaptive (the productive zone), MRV=max recoverable.
LANDMARKS = {
    "Chest":       (10, 12, 20, 22),
    "Lats":        (10, 14, 20, 25),
    "Mid-Back":    (10, 14, 22, 25),
    "Front Delts": (6, 8, 12, 16),
    "Side Delts":  (8, 16, 22, 26),
    "Rear Delts":  (6, 10, 18, 22),
    "Biceps":      (8, 14, 20, 22),
    "Triceps":     (6, 10, 16, 18),
    "Quads":       (8, 12, 18, 20),
    "Hamstrings":  (6, 10, 16, 20),
    "Glutes":      (0, 6, 12, 16),
    "Calves":      (8, 12, 16, 20),
}
# render order (push→pull→arms→legs)
ORDER = ["Chest", "Front Delts", "Side Delts", "Triceps", "Lats", "Mid-Back",
         "Rear Delts", "Biceps", "Quads", "Hamstrings", "Glutes", "Calves"]


def _norm(name: str) -> str:
    return re.sub(r"\s+", " ", name.strip().lower())


def parse_workouts(days: int):
    today = datetime.datetime.now(TZ).date()
    start = today - datetime.timedelta(days=days - 1)
    vol = defaultdict(float)          # muscle -> weighted sets
    n_workouts = 0
    unmapped = set()
    used_dates = []
    for f in sorted(glob.glob(str(WORKOUTS / "*.md"))):
        date_s = Path(f).stem
        try:
            d = datetime.date.fromisoformat(date_s)
        except ValueError:
            continue
        if not (start <= d <= today):
            continue
        with open(f, encoding="utf-8") as fh:
            txt = fh.read()
        fm = txt.split("---")[1] if txt.count("---") >= 2 else txt
        file_sets = 0  # raw sets logged in this file (mapped or not)
        cur, cur_sets = None, 0
        def flush(name, sets):
            if not name or sets == 0:
                return
            key = _norm(name)
            if key not in MAP:
                unmapped.add(name)
                return
            prim, sec = MAP[key]
            for m in prim:
                vol[m] += sets
            for m in sec:
                vol[m] += 0.5 * sets
        for ln in fm.split("\n"):
            m = re.match(r"^  - name:\s*(.+)$", ln)
            if m:
                flush(cur, cur_sets)
                cur, cur_sets = m.group(1).strip(), 0
            elif cur and re.match(r"^\s*-\s*\{?\s*weight_lb:", ln):
                cur_sets += 1
                file_sets += 1
        flush(cur, cur_sets)
        # Only count a file as a workout if it actually logged sets — a
        # retrospective "exercises only, total_sets: 0" stub must not inflate
        # the headline workout count or skew the coach's framing.
        if file_sets > 0:
            n_workouts += 1
            used_dates.append(date_s)
    return vol, n_workouts, sorted(unmapped), (start, today), used_dates


def status(sets: float, lm) -> tuple[str, str]:
    mev, mav_lo, mav_hi, mrv = lm
    if sets < mev:
        return "under MEV", "#e15759"       # red — undertrained
    if sets < mav_lo:
        return "maintenance", "#f1a340"     # amber
    if sets <= mav_hi:
        return "productive", "#59a14f"      # green — in the zone
    if sets <= mrv:
        return "high", "#4e79a7"            # blue
    return "OVER MRV", "#9c6dab"            # purple — overreaching


def text_summary(vol, n_workouts, window, used_dates):
    start, today = window
    lines = [f"Muscle volume — {start:%b %-d}–{today:%b %-d} ({n_workouts} workouts)"]
    trained = {m: vol.get(m, 0) for m in ORDER if vol.get(m, 0) > 0}
    gaps = [m for m in LANDMARKS if vol.get(m, 0) < LANDMARKS[m][0]]
    for m in ORDER:
        s = vol.get(m, 0)
        if s == 0:
            continue
        st, _ = status(s, LANDMARKS[m])
        lines.append(f"  {m:12} {s:4.1f} sets  ({st})")
    lines.append("")
    untrained = [m for m in LANDMARKS if vol.get(m, 0) == 0]
    if untrained:
        lines.append("No volume logged: " + ", ".join(untrained))
    return "\n".join(lines), trained, gaps


def render(vol, window, used_dates, out: Path):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.patches import Patch

    start, today = window
    muscles = [m for m in ORDER if vol.get(m, 0) > 0 or m in ("Chest", "Lats", "Side Delts", "Rear Delts", "Biceps", "Triceps", "Front Delts", "Mid-Back")]
    muscles = [m for m in ORDER if m in muscles]
    y = range(len(muscles))
    fig, ax = plt.subplots(figsize=(9, 0.55 * len(muscles) + 1.6), dpi=150)
    fig.patch.set_facecolor("#0f1117")
    ax.set_facecolor("#0f1117")

    for i, m in enumerate(muscles):
        mev, mav_lo, mav_hi, mrv = LANDMARKS[m]
        s = vol.get(m, 0)
        # productive (MAV) zone band
        ax.barh(i, mav_hi - mav_lo, left=mav_lo, height=0.62, color="#2f6b34", alpha=0.30, zorder=1)
        # the actual volume bar
        _, c = status(s, LANDMARKS[m])
        ax.barh(i, s, height=0.46, color=c, zorder=3)
        # MEV floor + MRV ceiling ticks
        ax.plot([mev, mev], [i - 0.31, i + 0.31], color="#8a8f98", lw=1, ls=":", zorder=2)
        ax.plot([mrv, mrv], [i - 0.31, i + 0.31], color="#d0d3d8", lw=1, ls="--", zorder=2)
        ax.text(s + 0.3, i, f"{s:.0f}" if s == int(s) else f"{s:.1f}", va="center",
                color="#e6e6e6", fontsize=9, zorder=4)

    ax.set_yticks(list(y)); ax.set_yticklabels(muscles, color="#e6e6e6", fontsize=10)
    ax.invert_yaxis()
    ax.set_xlabel("sets this week", color="#b8bcc4", fontsize=10)
    ax.tick_params(colors="#b8bcc4")
    for sp in ax.spines.values():
        sp.set_color("#2a2e37")
    ax.set_xlim(0, max([LANDMARKS[m][3] for m in muscles]) + 3)
    ax.set_title(f"Weekly Muscle Volume  ·  {start:%b %-d}–{today:%b %-d}",
                 color="#ffffff", fontsize=13, fontweight="bold", pad=12)
    legend = [
        Patch(color="#e15759", label="under MEV (grow this)"),
        Patch(color="#59a14f", label="productive (MAV zone)"),
        Patch(color="#9c6dab", label="over MRV"),
        Patch(facecolor="#2f6b34", alpha=0.3, label="target zone"),
    ]
    ax.legend(handles=legend, loc="lower right", fontsize=8, facecolor="#181b22",
              edgecolor="#2a2e37", labelcolor="#e6e6e6")
    fig.tight_layout()
    out.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out, facecolor=fig.get_facecolor(), bbox_inches="tight")
    plt.close(fig)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=7)
    ap.add_argument("--out", default=str(VAULT / "07 - Health" / "Charts" / "muscle-volume.png"))
    ap.add_argument("--no-chart", action="store_true")
    args = ap.parse_args()

    vol, n, unmapped, window, used_dates = parse_workouts(args.days)
    summary, trained, gaps = text_summary(vol, n, window, used_dates)
    print(summary)
    if unmapped:
        print("\n⚠ unmapped exercises (add to MAP):", ", ".join(unmapped))
    if not args.no_chart:
        out = render(vol, window, used_dates, Path(args.out))
        print(f"\nchart → {out}")


if __name__ == "__main__":
    main()
