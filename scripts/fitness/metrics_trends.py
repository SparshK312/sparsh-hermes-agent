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
import sys
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parent))
from muscle_volume import VAULT  # noqa: E402 — single shared vault root

CSV_DEFAULT = VAULT / "07 - Health" / "Metrics" / "metrics.csv"
TZ = ZoneInfo("America/Toronto")
BG, FG, GRID = "#0f1117", "#e6e6e6", "#272c36"


def _f(s):
    try:
        return float(s)
    except (TypeError, ValueError):
        return None


def load(csv_path: Path, days: int):
    if not csv_path.exists():
        return [], []
    with csv_path.open(newline="") as fh:
        rows = list(csv.DictReader(fh))
    cutoff = (datetime.datetime.now(TZ).date() - datetime.timedelta(days=days - 1)).isoformat()
    rows = [r for r in rows if r.get("date", "") >= cutoff]
    rows.sort(key=lambda r: r.get("date", ""))
    dates = [datetime.date.fromisoformat(r["date"]) for r in rows if r.get("date")]
    return rows, dates


def _avg7(vals):
    out = []
    for i in range(len(vals)):
        win = [v for v in vals[max(0, i - 6):i + 1] if v is not None]
        out.append(sum(win) / len(win) if win else None)
    return out


def _mean(vals):
    xs = [v for v in vals if v is not None]
    return sum(xs) / len(xs) if xs else None


def build_caption(rows: list, days: int) -> str:
    """Short summary caption for the trend card (templated — no LLM needed)."""
    sleep = _mean([_f(r.get("sleep_total_h")) for r in rows])
    steps = _mean([_f(r.get("steps")) for r in rows])
    akcal = _mean([_f(r.get("active_kcal")) for r in rows])
    rhr = _mean([_f(r.get("resting_hr")) for r in rows])
    hrv = _mean([_f(r.get("hrv_ms")) for r in rows])
    L = [f"📊 *Trends — last {days} days*"]
    if sleep is not None:
        flag = "✅" if sleep >= 7 else "⚠️"
        L.append(f"{flag} Sleep avg *{sleep:.1f}h*")
    if steps is not None:
        L.append(f"👣 Steps avg *{int(round(steps)):,}/day*")
    if akcal is not None:
        L.append(f"🔥 Active avg *{int(round(akcal))} kcal/day*")
    rec = []
    if rhr is not None:
        rec.append(f"RHR {int(round(rhr))}")
    if hrv is not None:
        rec.append(f"HRV {int(round(hrv))}ms")
    if rec:
        L.append("❤️ " + " · ".join(rec))
    return "\n".join(L)


def render(rows: list, dates: list, out: Path, days: int) -> Path:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import matplotlib.dates as mdates

    sleep = [_f(r.get("sleep_total_h")) for r in rows]
    steps = [_f(r.get("steps")) for r in rows]
    akcal = [_f(r.get("active_kcal")) for r in rows]
    rhr = [_f(r.get("resting_hr")) for r in rows]
    hrv = [_f(r.get("hrv_ms")) for r in rows]

    fig, axes = plt.subplots(4, 1, figsize=(10, 11), dpi=150, sharex=True)
    fig.patch.set_facecolor(BG)
    d0, d1 = dates[0], dates[-1]
    fig.suptitle(f"Sleep · Activity · Recovery  ·  {d0:%b %-d}–{d1:%b %-d}",
                 color="#fff", fontsize=17, fontweight="bold", y=0.99)

    def style(ax, title):
        ax.set_facecolor(BG)
        ax.set_title(title, color=FG, fontsize=12, loc="left", pad=6)
        ax.tick_params(colors="#9aa0aa", labelsize=9)
        for s in ax.spines.values():
            s.set_color(GRID)
        ax.grid(True, color=GRID, lw=0.6, alpha=0.6)

    # sleep (top — the priority metric) + 7h target line + 7-day avg
    ax = axes[0]
    ax.bar(dates, [s or 0 for s in sleep], color="#9b8cff", width=0.8)
    ax.plot(dates, _avg7(sleep), color="#ffd166", lw=2, label="7-day avg")
    ax.axhline(7, color="#59a14f", lw=1, ls="--", alpha=0.7, label="7h target")
    style(ax, "Sleep (hours)")
    ax.legend(facecolor="#181b22", edgecolor=GRID, labelcolor=FG, fontsize=8, loc="upper left")

    # steps
    ax = axes[1]
    ax.bar(dates, [s or 0 for s in steps], color="#4e79a7", width=0.8)
    ax.plot(dates, _avg7(steps), color="#ffd166", lw=2, label="7-day avg")
    style(ax, "Steps")
    ax.legend(facecolor="#181b22", edgecolor=GRID, labelcolor=FG, fontsize=8, loc="upper left")

    # active kcal
    ax = axes[2]
    ax.bar(dates, [a or 0 for a in akcal], color="#f28e2b", width=0.8)
    style(ax, "Active energy (kcal)")

    # RHR + HRV
    ax = axes[3]
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
    fig.tight_layout(rect=[0, 0, 1, 0.97])
    out.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out, facecolor=BG, bbox_inches="tight")
    plt.close(fig)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=30)
    ap.add_argument("--csv", default=str(CSV_DEFAULT))
    ap.add_argument("--out", default=str(VAULT / "07 - Health" / "Charts" / "trends.png"))
    ap.add_argument("--send", action="store_true", help="send the chart to Telegram via the Bot API")
    args = ap.parse_args()

    rows, dates = load(Path(args.csv), args.days)
    if not rows:
        msg = f"no metrics in the last {args.days} days at {args.csv} — nothing to chart"
        print(msg)
        return

    out = render(rows, dates, Path(args.out), args.days)
    print(f"→ {out}")

    if args.send:
        # Reuse the proven Bot-API multipart sender (imported lazily so render-only
        # runs + tests don't need cairosvg, which fitness_report pulls in).
        from fitness_report import send_photo  # noqa: E402
        caption = build_caption(rows, args.days)
        print("sent" if send_photo(out, caption) else "send FAILED")


if __name__ == "__main__":
    main()
