#!/usr/bin/env python3
"""
metrics_trends.py — Activity & Recovery trend chart from the Apple-Watch archive.

Reads 07 - Health/Metrics/metrics.csv and renders a dark multi-panel trend over the
last N days: daily steps (+7-day avg), active energy, and resting-HR / HRV (recovery).
matplotlib -> PNG. The fitness venv needs matplotlib for this one (the muscle card
only needed cairosvg).

  metrics_trends.py [--days N] [--csv PATH] [--out PATH]
"""
from __future__ import annotations

import argparse
import csv
import datetime
import os
from pathlib import Path

VAULT = Path(os.environ.get("HERMES_VAULT", "/home/hermes/vault"))
CSV_DEFAULT = VAULT / "07 - Health" / "Metrics" / "metrics.csv"
BG, FG, GRID = "#0f1117", "#e6e6e6", "#272c36"


def _f(s):
    try:
        return float(s)
    except (TypeError, ValueError):
        return None


def load(csv_path: Path, days: int):
    rows = list(csv.DictReader(csv_path.open()))
    cutoff = (datetime.date.today() - datetime.timedelta(days=days - 1)).isoformat()
    rows = [r for r in rows if r.get("date", "") >= cutoff]
    rows.sort(key=lambda r: r["date"])
    dates = [datetime.date.fromisoformat(r["date"]) for r in rows]
    return rows, dates


def _avg7(vals):
    out = []
    for i in range(len(vals)):
        win = [v for v in vals[max(0, i - 6):i + 1] if v is not None]
        out.append(sum(win) / len(win) if win else None)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=30)
    ap.add_argument("--csv", default=str(CSV_DEFAULT))
    ap.add_argument("--out", default=str(VAULT / "07 - Health" / "Charts" / "activity-recovery.png"))
    args = ap.parse_args()

    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import matplotlib.dates as mdates

    rows, dates = load(Path(args.csv), args.days)
    steps = [_f(r.get("steps")) for r in rows]
    akcal = [_f(r.get("active_kcal")) for r in rows]
    rhr = [_f(r.get("resting_hr")) for r in rows]
    hrv = [_f(r.get("hrv_ms")) for r in rows]

    fig, axes = plt.subplots(3, 1, figsize=(10, 9), dpi=150, sharex=True)
    fig.patch.set_facecolor(BG)
    d0, d1 = dates[0], dates[-1]
    fig.suptitle(f"Activity & Recovery  ·  {d0:%b %-d}–{d1:%b %-d}",
                 color="#fff", fontsize=17, fontweight="bold", y=0.98)

    def style(ax, title):
        ax.set_facecolor(BG)
        ax.set_title(title, color=FG, fontsize=12, loc="left", pad=6)
        ax.tick_params(colors="#9aa0aa", labelsize=9)
        for s in ax.spines.values():
            s.set_color(GRID)
        ax.grid(True, color=GRID, lw=0.6, alpha=0.6)

    # steps
    ax = axes[0]
    ax.bar(dates, [s or 0 for s in steps], color="#4e79a7", width=0.8)
    a7 = _avg7(steps)
    ax.plot(dates, a7, color="#ffd166", lw=2, label="7-day avg")
    style(ax, "Steps")
    ax.legend(facecolor="#181b22", edgecolor=GRID, labelcolor=FG, fontsize=8, loc="upper left")

    # active kcal
    ax = axes[1]
    ax.bar(dates, [a or 0 for a in akcal], color="#f28e2b", width=0.8)
    style(ax, "Active energy (kcal)")

    # RHR + HRV
    ax = axes[2]
    ax.plot(dates, rhr, color="#e15759", lw=2, marker="o", ms=3, label="Resting HR (bpm)")
    ax.set_ylabel("RHR", color="#e15759", fontsize=10)
    style(ax, "Recovery — Resting HR (lower better) · HRV (higher better)")
    ax2 = ax.twinx()
    ax2.plot(dates, hrv, color="#59a14f", lw=2, marker="o", ms=3, label="HRV (ms)")
    ax2.set_ylabel("HRV", color="#59a14f", fontsize=10)
    ax2.tick_params(colors="#59a14f", labelsize=9)
    for s in ax2.spines.values():
        s.set_color(GRID)
    lines = ax.get_lines() + ax2.get_lines()
    ax.legend(lines, [l.get_label() for l in lines], facecolor="#181b22",
              edgecolor=GRID, labelcolor=FG, fontsize=8, loc="upper left")

    axes[-1].xaxis.set_major_formatter(mdates.DateFormatter("%b %-d"))
    fig.autofmt_xdate(rotation=0, ha="center")
    fig.tight_layout(rect=[0, 0, 1, 0.96])
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out, facecolor=BG, bbox_inches="tight")
    plt.close(fig)
    print(f"→ {out}")


if __name__ == "__main__":
    main()
