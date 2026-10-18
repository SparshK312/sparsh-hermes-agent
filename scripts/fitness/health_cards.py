#!/usr/bin/env python3
"""
health_cards.py — the visual layer for health: a MORNING card, the WEEKLY review cards
and a MONTHLY calendar card, built first from what the band records on its own.

Why this exists (2026-10-17). The text messages were being ignored, and the existing
charts were blank: the trends card read Apple-only columns (`active_kcal`, `hrv_ms`) that
went empty when the Fitbit Air replaced the watch on Oct 8, and every food/workout card
needs hand logging, which had happened on 2 of the last 30 days. So these cards are built
around the data that arrives every day with no effort (sleep, stages, bed/wake times,
steps, Active Zone Minutes, resting HR, HRV, SpO2, breathing) and ADD a food / body panel
only when he has logged something. Both cases are complete cards.

Rules the code keeps (each is a real mistake made before):
  * A missing day is drawn as a gap, never as zero (the old trends card drew `s or 0`,
    so a night with no data looked like a 0 h night).
  * HRV is the Air's nightly RMSSD only. Apple's SDNN (`hrv_ms`) is a different measure
    and is never plotted on the same axis or used in the same baseline.
  * Resting HR baselines use one source only (`rhr_source`), for the same reason.
  * "Normal" means HIS normal: a 28-day personal baseline, shown only once it has
    BASELINE_MIN_DAYS nights. Before that the card says "baseline building".
  * A single night outside the band is labelled, never alarmed (amber at worst).

The data half is pure stdlib so coach.py (system python, no cairosvg) can import it for
the coach's evidence. Rendering builds an SVG (no emoji: cairosvg has no emoji font) and
converts it with cairosvg, the same stack as nutrition_card.py, so it needs the fitness
venv only for PNG output.

  health_cards.py morning [--date YYYY-MM-DD] [--sleep-state final|partial|none] [--send] [--out DIR]
  health_cards.py week    [--end YYYY-MM-DD] [--send] [--caption TEXT] [--no-extras] [--out DIR]
  health_cards.py month   [--month YYYY-MM] [--send] [--caption-file PATH] [--out DIR]
"""
from __future__ import annotations

import argparse
import calendar
import csv
import datetime
import math
import os
import re
import sys
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parent))
from nutrition_card import (  # noqa: E402  (shared visual kit; no cairosvg at import)
    F, GREEN, AMBER, RED, BLUE, PURPLE, WK_TXT, WK_MUTED, WK_FAINT, WK_GRID,
    WK_PANEL_STROKE, _defs, _panel, KCAL_TARGET, PROTEIN_TARGET,
)

TZ = ZoneInfo("America/Toronto")


def now() -> datetime.datetime:
    """The one clock (tests replace it; nothing here calls datetime.now directly)."""
    return datetime.datetime.now(TZ)


def _default_vault() -> Path:
    env = os.environ.get("HERMES_VAULT")
    if env:
        return Path(env)
    vps = Path("/home/hermes/vault")
    return vps if vps.exists() else Path.home() / "Documents" / "School Vault - UofT"


def vault() -> Path:
    return _default_vault()


def metrics_path() -> Path:
    return vault() / "07 - Health" / "Metrics" / "metrics.csv"


def charts_dir() -> Path:
    return vault() / "07 - Health" / "Charts"


SLEEP_TARGET_H = 7.0
BASELINE_DAYS = 28
BASELINE_MIN_DAYS = 7
WAKE_GAP_MAX_MIN = 150          # same rule as health_morning_brief_gate.sleep_state
LATE_BED_HOUR = 25.0            # 1:00 AM on the night-hour scale (Coach Memory watch flag)
MIN_COMPARE_DAYS = 4            # a "vs last week" needs real coverage on BOTH sides
STEP_COLOR, AZM_COLOR = "#5b8fd6", "#e8a33d"
DEEP_C, REM_C, LIGHT_C, AWAKE_C = "#4b5fd1", "#9c6dab", "#6fa6e2", "#f1a340"
W = 1180
M = 56


# ===================================================================== data (pure stdlib)
def load_metrics(path: Path | None = None) -> dict:
    """{date: row} from metrics.csv, empty cells dropped (blank = not recorded)."""
    p = path or metrics_path()
    out = {}
    if not p.exists():
        return out
    with p.open(newline="") as fh:
        for r in csv.DictReader(fh):
            d = r.get("date")
            if d:
                out[d] = {k: v for k, v in r.items() if k and v not in ("", None)}
    return out


def fnum(r: dict | None, k: str):
    try:
        return float((r or {}).get(k))
    except (TypeError, ValueError):
        return None


def parse_ts(s) -> datetime.datetime | None:
    """Air writes '2026-10-17T06:15:00Z'; Apple writes '2026-09-23 00:31:12 -0400'."""
    if not s:
        return None
    s = str(s).strip()
    try:
        dt = datetime.datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        try:
            dt = datetime.datetime.strptime(s, "%Y-%m-%d %H:%M:%S %z")
        except ValueError:
            return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=TZ)
    return dt.astimezone(TZ)


def night_hour(dt: datetime.datetime | None):
    """Clock time on a night scale: 22.5 = 10:30 PM, 25.0 = 1:00 AM, 32.0 = 8:00 AM."""
    if dt is None:
        return None
    h = dt.hour + dt.minute / 60
    return h + 24 if h < 16 else h


def fmt_clock(h) -> str:
    if h is None:
        return "—"
    h = h % 24
    hh, mm = int(h), int(round((h - int(h)) * 60))
    if mm == 60:
        hh, mm = (hh + 1) % 24, 0
    suf = "AM" if hh < 12 else "PM"
    return f"{(hh % 12) or 12}:{mm:02d} {suf}"


def fmt_dur(h) -> str:
    """6.98 -> '6h 59m' (a decimal like '7.0h' read as 'met the 7 h target' when it hadn't)."""
    if h is None:
        return "—"
    mins = int(round(h * 60))
    return f"{mins // 60}h {mins % 60:02d}m"


def _mean(xs):
    xs = [x for x in xs if x is not None]
    return sum(xs) / len(xs) if xs else None


def _sd(xs):
    xs = [x for x in xs if x is not None]
    if len(xs) < 2:
        return None
    m = sum(xs) / len(xs)
    return math.sqrt(sum((x - m) ** 2 for x in xs) / len(xs))


def _d(s) -> datetime.date:
    return s if isinstance(s, datetime.date) else datetime.date.fromisoformat(s)


def _days(end: datetime.date, n: int) -> list[str]:
    """n ISO dates ending at `end`, oldest first."""
    return [(end - datetime.timedelta(days=i)).isoformat() for i in range(n - 1, -1, -1)]


def baseline(rows: dict, col: str, before, days: int = BASELINE_DAYS,
             min_n: int = BASELINE_MIN_DAYS, same=None, floor: float = 0.0,
             floor_frac: float = 0.0) -> dict | None:
    """His normal for `col`: mean ± sd over the `days` calendar days BEFORE `before`,
    using only rows where same(row) holds. None until min_n values exist."""
    vals = []
    for d in _days(_d(before) - datetime.timedelta(days=1), days):
        r = rows.get(d)
        if r is None or (same is not None and not same(r)):
            continue
        v = fnum(r, col)
        if v is not None:
            vals.append(v)
    if len(vals) < min_n:
        return {"n": len(vals), "building": True}
    m, sd = _mean(vals), _sd(vals) or 0.0
    half = max(sd, floor, floor_frac * abs(m))
    return {"n": len(vals), "building": False, "mean": m, "sd": sd, "lo": m - half, "hi": m + half}


def versus(value, base: dict | None, higher_is_better: bool = True) -> tuple[str, str]:
    """(label, colour) for one reading against his band. One night is a reading, not a
    verdict: the worst it gets is amber."""
    if value is None:
        return "no reading", WK_FAINT
    if not base or base.get("building"):
        n = (base or {}).get("n", 0)
        return f"baseline building ({n}/{BASELINE_MIN_DAYS} nights)", WK_MUTED
    if base["lo"] <= value <= base["hi"]:
        return "in your normal range", WK_MUTED
    above = value > base["hi"]
    good = above == higher_is_better
    return ("above your normal" if above else "below your normal"), (GREEN if good else AMBER)


def sleep_state(row: dict | None) -> str:
    """'none' | 'partial' | 'final' — the brief gate's rule (Fitbit grows a night in
    pieces; an Air night is finished once the band sees him walking within 150 min)."""
    if not row or fnum(row, "sleep_total_h") is None:
        return "none"
    if (row.get("sleep_source") or "") != "air":
        return "final"
    gap = fnum(row, "wake_gap_min")
    return "final" if gap is not None and gap <= WAKE_GAP_MAX_MIN else "partial"


def _night(rows: dict, date: str) -> dict:
    r = rows.get(date, {})
    st, en = parse_ts(r.get("sleep_start")), parse_ts(r.get("sleep_end"))
    total = fnum(r, "sleep_total_h")
    return {"date": date, "total": total, "bed_h": night_hour(st) if total is not None else None,
            "wake_h": night_hour(en) if total is not None else None,
            "deep": fnum(r, "sleep_deep_h"), "rem": fnum(r, "sleep_rem_h"),
            "light": fnum(r, "sleep_core_h"), "awake": fnum(r, "sleep_awake_h"),
            "source": r.get("sleep_source")}


def daily_fm(date: str) -> dict:
    try:
        txt = (vault() / "04 - Daily Notes" / f"{date}.md").read_text(encoding="utf-8")
    except Exception:  # noqa: BLE001
        return {}
    parts = txt.split("---")
    if len(parts) < 3:
        return {}
    fm = {}
    for ln in parts[1].splitlines():
        if ":" in ln and not ln.startswith((" ", "\t", "#")):
            k, v = ln.split(":", 1)
            if v.strip():
                fm[k.strip()] = v.strip()
    return fm


def lift_logged(date: str) -> bool:
    return (vault() / "07 - Health" / "Workouts" / f"{date}.md").exists()


def latest_weight(end: datetime.date, lookback: int = 14):
    for d in reversed(_days(end, lookback)):
        w = fnum(daily_fm(d), "weight")
        if w is not None:
            return {"date": d, "lb": w}
    return None


def _day_activity(rows: dict, date: str) -> dict:
    r = rows.get(date, {})
    return {"date": date, "steps": fnum(r, "steps"), "azm": fnum(r, "azm"),
            "km": fnum(r, "distance_air_km"), "kcal_out": fnum(r, "total_kcal_air"),
            "ex_n": fnum(r, "exercise_air_count"), "ex_min": fnum(r, "exercise_air_min"),
            "lift": lift_logged(date)}


def _food(date: str) -> dict:
    fm = daily_fm(date)
    return {"kcal": fnum(fm, "kcal"), "protein": fnum(fm, "protein_g")}


def morning_facts(rows: dict, today: datetime.date, state: str | None = None) -> dict:
    t, y = today.isoformat(), (today - datetime.timedelta(days=1)).isoformat()
    row = rows.get(t, {})
    night = _night(rows, t)
    night["state"] = state or sleep_state(row)
    nights = [_night(rows, d) for d in _days(today, 7)]
    # the 7 nights BEFORE last night (the label says 7-night avg; nights[:-1] was 6)
    prev7 = [_night(rows, d)["total"] for d in _days(today - datetime.timedelta(days=1), 7)]
    hrv_b = baseline(rows, "hrv_rmssd_ms", t, floor_frac=0.05)
    src = row.get("rhr_source") or "apple"
    rhr_b = baseline(rows, "resting_hr", t, same=lambda r: (r.get("rhr_source") or "apple") == src,
                     floor=1.5)
    spark_days = _days(today, 14)
    act = [_day_activity(rows, d) for d in _days(today - datetime.timedelta(days=1), 7)]
    prev_steps = [fnum(rows.get(d), "steps") for d in _days(today - datetime.timedelta(days=2), 7)]
    prev_azm = [fnum(rows.get(d), "azm") for d in _days(today - datetime.timedelta(days=2), 7)]
    food7 = [_food(d) for d in _days(today - datetime.timedelta(days=1), 7)]
    return {
        "date": t, "yesterday": y,
        "night": night, "nights": nights,
        "sleep_avg7": _mean(prev7), "sleep_avg7_n": len([x for x in prev7 if x is not None]),
        "hrv": {"value": fnum(row, "hrv_rmssd_ms"), "base": hrv_b,
                "spark": [fnum(rows.get(d), "hrv_rmssd_ms") for d in spark_days]},
        "rhr": {"value": fnum(row, "resting_hr"), "source": src, "base": rhr_b,
                "spark": [fnum(rows.get(d), "resting_hr")
                          if (rows.get(d, {}).get("rhr_source") or "apple") == src else None
                          for d in spark_days]},
        "spo2": fnum(row, "spo2_avg"), "resp": fnum(row, "respiratory_rate"),
        "activity": act[-1], "activity7": act,
        "steps_avg_prev7": _mean(prev_steps), "azm_avg_prev7": _mean(prev_azm),
        "food": food7[-1], "food_days7": sum(1 for f in food7 if f["kcal"] is not None),
        "weight": latest_weight(today - datetime.timedelta(days=1)),
    }


def period_summary(rows: dict, end: datetime.date, days: int) -> dict:
    """Aggregates for the `days` days ending at `end` (inclusive). Counts say how many
    days actually carried data, so 'avg 7.1 h over 3 nights' can't pass for a full week."""
    dates = _days(end, days)
    nights = [_night(rows, d) for d in dates]
    acts = [_day_activity(rows, d) for d in dates]
    foods = [_food(d) for d in dates]
    sl = [n["total"] for n in nights if n["total"] is not None]
    beds = [n["bed_h"] for n in nights if n["bed_h"] is not None]
    wakes = [n["wake_h"] for n in nights if n["wake_h"] is not None]
    steps = [a["steps"] for a in acts if a["steps"] is not None]
    azm = [a["azm"] for a in acts if a["azm"] is not None]
    kc = [f["kcal"] for f in foods if f["kcal"] is not None]
    pr = [f["protein"] for f in foods if f["protein"] is not None]
    srcs = [n["source"] or "apple" for n in nights if n["total"] is not None]
    sleep_src = max(set(srcs), key=srcs.count) if srcs else None
    st_srcs = [(rows.get(d, {}).get("steps_source") or "apple") for d, a in zip(dates, acts) if a["steps"] is not None]
    steps_src = max(set(st_srcs), key=st_srcs.count) if st_srcs else None
    return {
        "start": dates[0], "end": dates[-1], "days": days,
        "sleep_avg_h": _mean(sl), "sleep_nights": len(sl),
        "nights_7h": sum(1 for x in sl if x >= SLEEP_TARGET_H),
        "bed_avg_h": _mean(beds), "bed_sd_h": _sd(beds), "wake_avg_h": _mean(wakes),
        "late_nights": sum(1 for b in beds if LATE_BED_HOUR <= b < 30.0),
        "sleep_src": sleep_src, "sleep_src_nights": srcs.count(sleep_src) if sleep_src else 0,
        "steps_src": steps_src, "steps_src_days": st_srcs.count(steps_src) if steps_src else 0,
        "band_data_days": sum(1 for a in acts if a["ex_n"] is not None or a["azm"] is not None),
        "steps_avg": _mean(steps), "steps_days": len(steps),
        "azm_total": sum(azm) if azm else None, "azm_days": len(azm),
        "band_sessions": int(sum(a["ex_n"] or 0 for a in acts)),
        "band_session_min": int(sum(a["ex_min"] or 0 for a in acts)),
        "lifts_logged": sum(1 for a in acts if a["lift"]),
        "food_days": len(kc), "kcal_avg": _mean(kc), "protein_avg": _mean(pr),
        "protein_hits": sum(1 for x in pr if x >= PROTEIN_TARGET),
    }


def recovery_series(rows: dict, end: datetime.date, days: int = 28) -> dict:
    """HRV (Air RMSSD) and resting HR (one source) over `days` nights, the 7-night
    rolling mean, and his band from the 28 days before the window's last week."""
    dates = _days(end, days)
    hrv = [fnum(rows.get(d), "hrv_rmssd_ms") for d in dates]
    src = (rows.get(end.isoformat(), {}) or {}).get("rhr_source") or "air"
    rhr = [fnum(rows.get(d), "resting_hr") if (rows.get(d, {}).get("rhr_source") or "apple") == src
           else None for d in dates]

    def roll(xs):
        out = []
        for i in range(len(xs)):
            win = [x for x in xs[max(0, i - 6):i + 1] if x is not None]
            out.append(sum(win) / len(win) if len(win) >= 3 else None)
        return out
    week_start = end - datetime.timedelta(days=6)
    hb = baseline(rows, "hrv_rmssd_ms", week_start, floor_frac=0.05)
    rb = baseline(rows, "resting_hr", week_start, same=lambda r: (r.get("rhr_source") or "apple") == src,
                  floor=1.5)
    last7 = lambda xs: _mean(xs[-7:])  # noqa: E731
    n7 = lambda xs: sum(1 for x in xs[-7:] if x is not None)  # noqa: E731
    return {"dates": dates, "hrv": hrv, "hrv_roll": roll(hrv), "hrv_base": hb,
            "hrv_week_avg": last7(hrv), "hrv_week_n": n7(hrv), "rhr": rhr, "rhr_roll": roll(rhr),
            "rhr_base": rb, "rhr_week_avg": last7(rhr), "rhr_week_n": n7(rhr), "rhr_source": src}


def _band_state(avg, base, higher_is_better=True, n: int = 7) -> str:
    """For the coach: is the WEEK's average outside his band? Never one night: with fewer
    than MIN_COMPARE_DAYS nights in the week there is no verdict at all."""
    if avg is None:
        return "no data"
    if n < MIN_COMPARE_DAYS:
        return f"too few nights this week ({n}) to judge"
    if not base or base.get("building"):
        return f"baseline building ({(base or {}).get('n', 0)}/{BASELINE_MIN_DAYS})"
    if base["lo"] <= avg <= base["hi"]:
        return "within his normal range"
    above = avg > base["hi"]
    return ("above" if above else "below") + " his normal range" + \
        (" (good direction)" if above == higher_is_better else " (watch)")


def _r(x, n=1):
    return None if x is None else round(x, n)


def wearable_evidence(rows: dict | None = None, end: datetime.date | None = None, days: int = 7) -> dict:
    """The band's side of the coach's evidence packet: this period vs the one before,
    the week's recovery against his band, and what is / isn't being logged. `end`
    defaults to yesterday (the last COMPLETE day of steps)."""
    rows = load_metrics() if rows is None else rows
    end = end or (now().date() - datetime.timedelta(days=1))
    cur = period_summary(rows, end, days)
    prev = period_summary(rows, end - datetime.timedelta(days=days), days)
    rec = recovery_series(rows, end)

    def clean(p):
        return {"window": f"{p['start']}..{p['end']}",
                "sleep_avg_h": _r(p["sleep_avg_h"], 2), "nights_with_sleep_data": p["sleep_nights"],
                "nights_at_least_7h": p["nights_7h"],
                "avg_bedtime": fmt_clock(p["bed_avg_h"]) if p["bed_avg_h"] else None,
                "bedtime_spread_h": _r(p["bed_sd_h"], 1), "avg_wake": fmt_clock(p["wake_avg_h"]) if p["wake_avg_h"] else None,
                "nights_bed_after_1am": p["late_nights"],
                "steps_avg": _r(p["steps_avg"], 0), "days_with_steps": p["steps_days"],
                "active_zone_min_total": _r(p["azm_total"], 0),
                "band_auto_detected_sessions": p["band_sessions"],
                "band_session_minutes": p["band_session_min"],
                "lift_sessions_logged": p["lifts_logged"],
                "food_logged_days": p["food_days"], "avg_kcal_logged_days": _r(p["kcal_avg"], 0),
                "avg_protein_g_logged_days": _r(p["protein_avg"], 0)}
    prev_c = clean(prev)
    # Enforce the comparison rule in the DATA, not only in the prompt: on 2026-10-17 the
    # model was told "compare only with 4+ days on both sides" and still wrote "sleep jumped
    # from 5.6 h to 8.1 h" against a week with ONE night of band data. A previous-period
    # metric without coverage is withheld, so there is nothing to compare against.
    gated = {"sleep": ("sleep_avg_h", "nights_at_least_7h", "avg_bedtime", "bedtime_spread_h",
                       "avg_wake", "nights_bed_after_1am"),
             "steps": ("steps_avg",), "azm": ("active_zone_min_total",)}
    withheld = []
    for metric, keys in gated.items():
        ok, why = comparable(cur, prev, metric)
        if not ok:
            withheld.append(f"{metric} ({why})")
            for k in keys:
                prev_c[k] = None
    if withheld:
        prev_c["not_comparable"] = ("withheld, not comparable with this period: " + ", ".join(withheld)
                                    + " (the Fitbit Air replaced the Apple Watch on 2026-10-08)")
    return {
        "this_period": clean(cur), "previous_period": prev_c,
        "recovery_trend": {
            "hrv_rmssd_week_avg_ms": _r(rec["hrv_week_avg"], 1),
            "hrv_vs_his_normal": _band_state(rec["hrv_week_avg"], rec["hrv_base"], True, rec["hrv_week_n"]),
            "hrv_normal_range_ms": None if rec["hrv_base"].get("building") else
            [_r(rec["hrv_base"]["lo"]), _r(rec["hrv_base"]["hi"])],
            "resting_hr_week_avg": _r(rec["rhr_week_avg"], 1),
            "resting_hr_vs_his_normal": _band_state(rec["rhr_week_avg"], rec["rhr_base"], False, rec["rhr_week_n"]),
        },
        "notes": ["band_auto_detected_sessions are workouts the Fitbit detected on its own "
                  "(usually walks); they are NOT lifting sessions",
                  "a blank or 0 food/lift count means NOT LOGGED, not 'did not eat / did not train'"],
    }


def month_facts(rows: dict, year: int, month: int, today: datetime.date | None = None) -> dict:
    today = today or now().date()
    if datetime.date(year, month, 1) > today:
        raise ValueError(f"{year}-{month:02d} has not started yet")
    ndays = calendar.monthrange(year, month)[1]
    first = datetime.date(year, month, 1)
    last = min(datetime.date(year, month, ndays), today)
    days = []
    for i in range(ndays):
        d = first + datetime.timedelta(days=i)
        ds = d.isoformat()
        if d > today:
            days.append({"date": ds, "future": True})
            continue
        n, a, f = _night(rows, ds), _day_activity(rows, ds), _food(ds)
        ex = a["ex_min"] if a["ex_min"] is not None else fnum(rows.get(ds), "exercise_min")
        days.append({"date": ds, "future": False, "sleep": n["total"], "steps": a["steps"],
                     "azm": a["azm"], "ex_min": ex, "lift": a["lift"],
                     "kcal": f["kcal"], "protein": f["protein"]})
    span = (last - first).days + 1
    cur = period_summary(rows, last, span)
    pm_last = first - datetime.timedelta(days=1)
    prev = period_summary(rows, pm_last, calendar.monthrange(pm_last.year, pm_last.month)[1])
    return {"year": year, "month": month, "days": days, "summary": cur, "prev": prev,
            "partial": last < datetime.date(year, month, ndays)}


# ===================================================================== svg helpers
def esc(s) -> str:
    return str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _txt(x, y, s, size=26, fill=WK_TXT, bold=False, anchor="start", opacity=None) -> str:
    b = " font-weight='bold'" if bold else ""
    o = f" opacity='{opacity}'" if opacity is not None else ""
    return (f"<text x='{x:.1f}' y='{y:.1f}' fill='{fill}' font-size='{size}'{b}{o} {F} "
            f"text-anchor='{anchor}'>{esc(s)}</text>")


def _svg_open(h) -> list[str]:
    return [f"<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 {W} {h}' width='{W}' height='{h}'>",
            _defs(), f"<rect x='0' y='0' width='{W}' height='{h}' fill='url(#wk-bg)'/>"]


def _header(title, sub) -> list[str]:
    return [_txt(M, 94, title, 58, bold=True), _txt(M + 2, 138, sub, 28, WK_MUTED)]


def _pill(x, y, text, color, size=24, anchor="start") -> list[str]:
    w = max(60, len(text) * size * 0.56 + 36)
    x0 = x if anchor == "start" else (x - w if anchor == "end" else x - w / 2)
    return [f"<rect x='{x0:.1f}' y='{y:.1f}' width='{w:.1f}' height='{size + 20}' rx='{(size + 20) / 2}' "
            f"fill='{color}' opacity='0.17'/>",
            _txt(x0 + w / 2, y + size + 4, text, size, color, bold=True, anchor="middle")]


def _delta_text(cur, prev, unit="", pct=False, higher_is_better=True, digits=1):
    """('+0.4h vs last week', colour) — grey when the change is small or unknown."""
    if cur is None or prev is None:
        return None, WK_MUTED
    diff = cur - prev
    if pct:
        if not prev:
            return None, WK_MUTED
        p = diff / prev * 100
        if abs(p) < 3:
            return "about the same", WK_MUTED
        s = f"{'+' if p > 0 else '−'}{abs(p):.0f}%"
        good = (p > 0) == higher_is_better
    else:
        if abs(diff) < 10 ** -digits / 2:
            return "about the same", WK_MUTED
        s = f"{'+' if diff > 0 else '−'}{abs(diff):.{digits}f}{unit}"
        good = (diff > 0) == higher_is_better
    return s, (GREEN if good else AMBER)


def comparable(cur: dict, prev: dict, metric: str) -> tuple[bool, str]:
    """Can this period be compared with that one? BOTH need MIN_COMPARE_DAYS days of the
    metric AND the same device. Oct 11-17 vs Oct 4-10 read "+538%" zone minutes (7 Air days
    against 3), and an Apple-Watch week against a Fitbit week reads a device change as
    progress (review, 2026-10-17)."""
    if metric == "sleep":
        ok = (cur["sleep_src"] is not None and cur["sleep_src"] == prev["sleep_src"]
              and cur["sleep_src_nights"] >= MIN_COMPARE_DAYS and prev["sleep_src_nights"] >= MIN_COMPARE_DAYS)
        why = "different device" if cur["sleep_src"] and prev["sleep_src"] and cur["sleep_src"] != prev["sleep_src"] else "not enough data"
    elif metric == "steps":
        ok = (cur["steps_src"] is not None and cur["steps_src"] == prev["steps_src"]
              and cur["steps_src_days"] >= MIN_COMPARE_DAYS and prev["steps_src_days"] >= MIN_COMPARE_DAYS)
        why = "different device" if cur["steps_src"] and prev["steps_src"] and cur["steps_src"] != prev["steps_src"] else "not enough data"
    else:  # zone minutes: Air only
        ok = cur["azm_days"] >= MIN_COMPARE_DAYS and prev["azm_days"] >= MIN_COMPARE_DAYS
        why = "not enough data"
    return ok, ("" if ok else why)


def _cmp(cur, prev, ok_why, suffix, **kw):
    """Delta text only when comparable() says so; otherwise say why, in grey."""
    ok, why = ok_why
    if not ok:
        when = suffix.replace("vs ", "")
        return (f"{why} {when}, no comparison" if why == "different device"
                else f"not enough data {when} to compare"), WK_FAINT
    t, c = _delta_text(cur, prev, **kw)
    if t and t != "about the same":
        t = f"{t} {suffix}"
    elif t:
        t = f"about the same as {suffix.replace('vs ', '')}"
    return t, c


def _sleep_color(h):
    if h is None:
        return WK_FAINT
    return GREEN if h >= SLEEP_TARGET_H else (AMBER if h >= 6.0 else RED)


def _spark(x, y, w, h, vals, base, color, label_fmt=lambda v: f"{v:.0f}") -> list[str]:
    """Line over the last N nights with HIS normal band shaded behind it. Gaps stay gaps."""
    pts = [(i, v) for i, v in enumerate(vals) if v is not None]
    out = [f"<rect x='{x}' y='{y}' width='{w}' height='{h}' rx='10' fill='#0e1118'/>"]
    if not pts:
        out.append(_txt(x + w / 2, y + h / 2 + 8, "no readings yet", 20, WK_FAINT, anchor="middle"))
        return out
    lo_v = min(v for _, v in pts)
    hi_v = max(v for _, v in pts)
    if base and not base.get("building"):
        lo_v, hi_v = min(lo_v, base["lo"]), max(hi_v, base["hi"])
    pad = max((hi_v - lo_v) * 0.18, 1.0)
    lo_v, hi_v = lo_v - pad, hi_v + pad
    n = max(len(vals) - 1, 1)

    def px(i):
        return x + 14 + (w - 28) * i / n

    def py(v):
        return y + h - 10 - (h - 20) * (v - lo_v) / (hi_v - lo_v)
    if base and not base.get("building"):
        y_hi, y_lo = py(base["hi"]), py(base["lo"])
        out.append(f"<rect x='{x + 6}' y='{y_hi:.1f}' width='{w - 12}' height='{max(2, y_lo - y_hi):.1f}' "
                   f"rx='6' fill='{GREEN}' opacity='0.12'/>")
    seg = []
    for i, v in enumerate(vals):
        if v is None:
            if len(seg) > 1:
                out.append(f"<polyline points='{' '.join(seg)}' fill='none' stroke='{color}' "
                           f"stroke-width='4' stroke-linejoin='round' stroke-linecap='round'/>")
            seg = []
            continue
        seg.append(f"{px(i):.1f},{py(v):.1f}")
    if len(seg) > 1:
        out.append(f"<polyline points='{' '.join(seg)}' fill='none' stroke='{color}' "
                   f"stroke-width='4' stroke-linejoin='round' stroke-linecap='round'/>")
    for i, v in pts:
        out.append(f"<circle cx='{px(i):.1f}' cy='{py(v):.1f}' r='{6 if i == pts[-1][0] else 3.5}' "
                   f"fill='{color if i == pts[-1][0] else '#0e1118'}' stroke='{color}' stroke-width='2.5'/>")
    return out


def _columns(x0, x1, top, bot, items, ymax, color_fn, fmt, refs=(), label_key="label",
             show_values=True, value_size=22) -> list[str]:
    """Daily columns; a day with no data is a dashed outline, never a zero bar.
    refs: [(value, text, colour, dashed)] horizontal reference lines."""
    out, plot_h = [], bot - top

    def vy(v):
        return bot - max(0.0, min(1.0, v / ymax)) * plot_h
    out.append(f"<line x1='{x0}' y1='{bot}' x2='{x1}' y2='{bot}' stroke='{WK_PANEL_STROKE}' stroke-width='2'/>")
    n = len(items)
    slot = (x1 - x0) / max(n, 1)
    cw = min(70.0, slot * 0.58)
    for i, it in enumerate(items):
        cx = x0 + slot * (i + 0.5)
        v = it.get("value")
        if v is not None and v > 0:
            ct = vy(v)
            op = "1" if it.get("highlight", True) else "0.55"
            out.append(f"<rect x='{cx - cw / 2:.1f}' y='{ct:.1f}' width='{cw:.1f}' height='{bot - ct:.1f}' "
                       f"rx='10' fill='{color_fn(v)}' opacity='{op}'/>")
            if show_values:
                out.append(_txt(cx, ct - 12, fmt(v), value_size, WK_TXT, bold=True, anchor="middle"))
        else:
            out.append(f"<rect x='{cx - cw / 2:.1f}' y='{bot - 40:.1f}' width='{cw:.1f}' height='40' rx='10' "
                       f"fill='none' stroke='{WK_PANEL_STROKE}' stroke-width='2' stroke-dasharray='3 7'/>")
        out.append(_txt(cx, bot + 34, it.get(label_key, ""), 22,
                        WK_TXT if it.get("today") else WK_MUTED, bold=bool(it.get("today")), anchor="middle"))
    for v, text, col, dashed in refs:
        if v is None:
            continue
        ly = vy(v)
        dash = " stroke-dasharray='4 10'" if dashed else ""
        out.append(f"<line x1='{x0}' y1='{ly:.1f}' x2='{x1}' y2='{ly:.1f}' stroke='{col}' stroke-width='3'"
                   f"{dash} stroke-linecap='round'/>")
    return out


def _legend(x_right, y, items) -> list[str]:
    """Right-aligned line-swatch legend for reference lines: [(label, colour, dashed)]."""
    out, x = [], x_right
    for label, col, dashed in reversed([i for i in items if i]):
        tw = len(label) * 11.5
        x -= tw
        out.append(_txt(x, y + 7, label, 20, WK_MUTED))
        dash = " stroke-dasharray='4 7'" if dashed else ""
        out.append(f"<line x1='{x - 44}' y1='{y}' x2='{x - 10}' y2='{y}' stroke='{col}' stroke-width='3'{dash}/>")
        x -= 44 + 26
    return out


def _rhythm(x, y, w, h, nights, today_date=None) -> list[str]:
    """Bed → wake floating bars per night, on a clock axis (top = evening). Shows how
    regular the schedule is at a glance; a missing night is an empty slot."""
    out = [f"<rect x='{x}' y='{y}' width='{w}' height='{h}' rx='14' fill='#0e1118'/>"]
    ok = [n for n in nights if n.get("bed_h") is not None and n.get("wake_h") is not None
          and 16 <= n["bed_h"] < n["wake_h"] <= 42]
    beds = [n["bed_h"] for n in ok]
    wakes = [n["wake_h"] for n in ok]
    lo = math.floor(min(beds + [22.0]) - 0.5)
    hi = math.ceil(max(wakes + [32.0]) + 0.5)
    ax_x = x + 92
    pw = w - 92 - 18

    def py(hr):
        return y + 18 + (h - 96) * (hr - lo) / (hi - lo)
    for hr in range(lo + (lo % 2), hi + 1, 2):
        gy = py(hr)
        out.append(f"<line x1='{ax_x}' y1='{gy:.1f}' x2='{x + w - 18}' y2='{gy:.1f}' stroke='{WK_GRID}' stroke-width='1.5'/>")
        hh = hr % 24
        lab = "12a" if hh == 0 else ("12p" if hh == 12 else (f"{hh % 12}{'a' if hh < 12 else 'p'}"))
        out.append(_txt(ax_x - 14, gy + 7, lab, 20, WK_FAINT, anchor="end"))
    slot = pw / max(len(nights), 1)
    bw = min(56.0, slot * 0.5)
    for i, n in enumerate(nights):
        cx = ax_x + slot * (i + 0.5)
        lab = datetime.date.fromisoformat(n["date"]).strftime("%a")
        is_t = n["date"] == today_date
        out.append(_txt(cx, y + h - 44, lab, 21, WK_TXT if is_t else WK_MUTED, bold=is_t, anchor="middle"))
        tot = n.get("total")
        out.append(_txt(cx, y + h - 16, f"{tot:.1f}h" if tot is not None else "", 20,
                        _sleep_color(tot) if is_t else WK_MUTED, bold=is_t, anchor="middle"))
        b_, w_ = n.get("bed_h"), n.get("wake_h")
        if b_ is None or w_ is None or not (16 <= b_ < w_ <= 42):
            out.append(_txt(cx, py((lo + hi) / 2), "—", 24, WK_FAINT, anchor="middle"))
            continue
        y0, y1 = py(n["bed_h"]), py(n["wake_h"])
        col = _sleep_color(n.get("total"))
        out.append(f"<rect x='{cx - bw / 2:.1f}' y='{y0:.1f}' width='{bw:.1f}' height='{max(6, y1 - y0):.1f}' "
                   f"rx='{bw / 2:.1f}' fill='{col}' opacity='{1 if is_t else 0.72}'/>")
    return out


def _stage_bar(x, y, w, h, night) -> list[str]:
    segs = [("Deep", night.get("deep"), DEEP_C), ("REM", night.get("rem"), REM_C),
            ("Light", night.get("light"), LIGHT_C), ("Awake", night.get("awake"), AWAKE_C)]
    segs = [(n, v, c) for n, v, c in segs if v]
    tot = sum(v for _, v, _ in segs)
    if not tot:
        return [_txt(x, y + h / 2 + 8, "sleep stages not available", 22, WK_FAINT)]
    out, acc = [], x
    for name, v, col in segs:
        sw = w * v / tot
        out.append(f"<rect x='{acc:.1f}' y='{y}' width='{max(2.0, sw - 4):.1f}' height='{h}' rx='{h / 2.6:.1f}' fill='{col}'/>")
        acc += sw
    for i, (name, v, col) in enumerate(segs):
        lx, ly = x + (i % 2) * (w / 2), y + h + 34 + (i // 2) * 34
        out.append(f"<circle cx='{lx + 9}' cy='{ly}' r='8' fill='{col}'/>")
        out.append(_txt(lx + 24, ly + 8, f"{name} {fmt_dur(v)}", 22, WK_MUTED))
    return out


def _stat(x, y, w, label, value, sub=None, sub_color=WK_MUTED, value_color=WK_TXT, size=46) -> list[str]:
    out = [_txt(x, y, label, 22, WK_MUTED), _txt(x, y + size + 8, value, size, value_color, bold=True)]
    if sub:
        out.append(_txt(x, y + size + 42, sub, 21, sub_color))
    return out


# ===================================================================== morning card
def build_morning_svg(f: dict) -> str:
    night = f["night"]
    d = datetime.date.fromisoformat(f["date"])
    show_food = f["food_days7"] > 0 or f["weight"] is not None
    SLEEP_H, REC_H, ACT_H, FOOD_H = 700, 300, 470, 200
    H = 172 + SLEEP_H + 30 + REC_H + 30 + ACT_H + (30 + FOOD_H if show_food else 0) + 70
    P = _svg_open(H)
    P += _header("Good morning", f"{d:%A, %b %-d}   ·   last night + yesterday")

    # ── sleep panel ──
    y0 = 172
    P.append(_panel(M, y0, W - 2 * M, SLEEP_H))
    P.append(_txt(M + 34, y0 + 52, "Sleep", 32, bold=True))
    if night["total"] is None:
        P.append(_txt(M + 34, y0 + 150, "No sleep recorded yet", 46, WK_MUTED, bold=True))
        P.append(_txt(M + 34, y0 + 196, "Fitbit usually syncs it within an hour of waking.", 24, WK_FAINT))
    else:
        partial = night["state"] == "partial"
        col = WK_MUTED if partial else _sleep_color(night["total"])
        P.append(_txt(M + 34, y0 + 150, fmt_dur(night["total"]), 96, col, bold=True))
        if partial:
            P += _pill(M + 400, y0 + 94, "still syncing", AMBER, 22)
        bed, wake = night["bed_h"], night["wake_h"]
        P.append(_txt(M + 36, y0 + 196, f"{fmt_clock(bed)}  →  {fmt_clock(wake)}", 28, WK_TXT))
        avg = f["sleep_avg7"]
        if avg is not None and not partial:
            dt, dcol = _delta_text(night["total"], avg, "h", higher_is_better=True)
            sub = f"7-night avg {fmt_dur(avg)}" + (f"   ·   {dt}" if dt and dt != "about the same" else "")
            P.append(_txt(M + 36, y0 + 236, sub, 24, WK_MUTED))
        # stages (right)
        P += _stage_bar(M + 600, y0 + 110, W - 2 * M - 600 - 34, 34, night)
        tgt = "7h target  ·  " + ("met" if night["total"] >= SLEEP_TARGET_H else
                                  f"{fmt_dur(SLEEP_TARGET_H - night['total'])} short")
        if not partial:
            P.append(_txt(M + 600, y0 + 262, tgt, 22, WK_FAINT))
    P.append(_txt(M + 34, y0 + 310, "Last 7 nights  ·  bedtime to wake-up", 24, WK_MUTED, bold=True))
    P += _rhythm(M + 24, y0 + 328, W - 2 * M - 48, SLEEP_H - 352, f["nights"], today_date=f["date"])

    # ── recovery panel ──
    y1 = y0 + SLEEP_H + 30
    P.append(_panel(M, y1, W - 2 * M, REC_H))
    P.append(_txt(M + 34, y1 + 50, "Recovery", 32, bold=True))
    P.append(_txt(W - M - 34, y1 + 50, "vs your own normal (shaded)", 21, WK_FAINT, anchor="end"))
    tw = (W - 2 * M - 34 * 2 - 40) / 3
    tiles = [
        ("HRV", f["hrv"], "ms", True, "#83dd5e"),
        ("Resting HR", f["rhr"], "bpm", False, "#f5806f"),
    ]
    for i, (name, blob, unit, hib, col) in enumerate(tiles):
        tx = M + 34 + i * (tw + 20)
        v = blob["value"]
        lab, lcol = versus(v, blob["base"], hib)
        P.append(_txt(tx, y1 + 98, name, 22, WK_MUTED))
        P.append(_txt(tx, y1 + 148, f"{v:.0f} {unit}" if v is not None else "—", 42, WK_TXT, bold=True))
        P.append(_txt(tx, y1 + 180, lab, 20, lcol))
        P += _spark(tx, y1 + 198, tw, 80, blob["spark"], blob["base"], col)
    tx = M + 34 + 2 * (tw + 20)
    P.append(_txt(tx, y1 + 98, "Blood oxygen · breathing", 22, WK_MUTED))
    P.append(_txt(tx, y1 + 148, f"{f['spo2']:.0f}%" if f["spo2"] is not None else "—", 42, WK_TXT, bold=True))
    P.append(_txt(tx, y1 + 186, f"{f['resp']:.1f} breaths/min" if f["resp"] is not None else "breathing —",
                  24, WK_MUTED))
    P.append(_txt(tx, y1 + 222, "HRV here is the band's nightly", 19, WK_FAINT))
    P.append(_txt(tx, y1 + 246, "RMSSD (not the Apple Watch's).", 19, WK_FAINT))

    # ── yesterday panel ──
    y2 = y1 + REC_H + 30
    a = f["activity"]
    yd = datetime.date.fromisoformat(f["yesterday"])
    P.append(_panel(M, y2, W - 2 * M, ACT_H))
    P.append(_txt(M + 34, y2 + 52, f"Yesterday  ·  {yd:%a %b %-d}", 32, bold=True))
    steps = a["steps"]
    P.append(_txt(M + 34, y2 + 140, f"{steps:,.0f}" if steps is not None else "—", 78, WK_TXT, bold=True))
    P.append(_txt(M + 36, y2 + 176, "steps", 24, WK_MUTED))
    dt, dcol = _delta_text(steps, f["steps_avg_prev7"], pct=True)
    if dt:
        P.append(_txt(M + 36, y2 + 212, f"{dt} vs your 7-day avg ({f['steps_avg_prev7']:,.0f})"
                      if dt != "about the same" else f"about your 7-day avg ({f['steps_avg_prev7']:,.0f})",
                      22, dcol))
    items = []
    for x in f["activity7"]:
        dd = datetime.date.fromisoformat(x["date"])
        items.append({"value": x["steps"], "label": dd.strftime("%a")[:2], "today": x["date"] == f["yesterday"],
                      "highlight": x["date"] == f["yesterday"]})
    smax = max([x["steps"] or 0 for x in f["activity7"]] + [f["steps_avg_prev7"] or 0, 4000]) * 1.18
    P += _columns(M + 520, W - M - 34, y2 + 70, y2 + 236, items, smax, lambda v: STEP_COLOR,
                  lambda v: f"{v / 1000:.1f}k", refs=[(f["steps_avg_prev7"], "", WK_MUTED, True)],
                  value_size=19)
    P += _legend(W - M - 34, y2 + 46, [("7-day avg", WK_MUTED, True)])
    # chips row
    cy = y2 + 326
    cw = (W - 2 * M - 68) / 4
    if a["ex_n"]:
        ex = f"{a['ex_n']:.0f} ({a['ex_min']:.0f} min)" if a["ex_min"] is not None else f"{a['ex_n']:.0f}"
    else:
        ex = "none" if a["ex_n"] is not None else "—"
    chips = [("Zone minutes", f"{a['azm']:.0f}" if a["azm"] is not None else "—",
              (f"avg {f['azm_avg_prev7']:.0f}" if f["azm_avg_prev7"] is not None else None)),
             ("Distance", f"{a['km']:.1f} km" if a["km"] is not None else "—", None),
             ("Calories burned", f"{a['kcal_out']:,.0f}" if a["kcal_out"] is not None else "—", None),
             ("Workouts", "lift logged" if a["lift"] else ex, "band-detected" if not a["lift"] and a["ex_n"] else None)]
    for i, (lab, val, sub) in enumerate(chips):
        P += _stat(M + 34 + i * cw, cy, cw, lab, val, sub, size=36)

    # ── food & body (only when he has logged something lately) ──
    if show_food:
        y3 = y2 + ACT_H + 30
        P.append(_panel(M, y3, W - 2 * M, FOOD_H))
        P.append(_txt(M + 34, y3 + 50, "Food & body", 32, bold=True))
        fd = f["food"]
        if fd["kcal"] is not None:
            kc_col = GREEN if fd["kcal"] >= KCAL_TARGET * 0.92 else AMBER
            P += _stat(M + 34, y3 + 86, 300, "Calories yesterday", f"{fd['kcal']:,.0f}",
                       f"target {KCAL_TARGET:,}", value_color=kc_col, size=40)
            pv = fd["protein"]
            if pv is not None:
                pc = GREEN if pv >= PROTEIN_TARGET else AMBER
                P += _stat(M + 380, y3 + 86, 300, "Protein", f"{pv:.0f} g", f"target {PROTEIN_TARGET} g",
                           value_color=pc, size=40)
        else:
            P += _stat(M + 34, y3 + 86, 600, "Food yesterday", "not logged",
                       f"logged {f['food_days7']} of the last 7 days", value_color=WK_MUTED, size=40)
        wt = f["weight"]
        if wt:
            wd = datetime.date.fromisoformat(wt["date"])
            P += _stat(M + 760, y3 + 86, 300, "Bodyweight", f"{wt['lb']:.1f} lb", f"{wd:%b %-d}", size=40)
    P.append(_txt(M, H - 26, "Hermes · health · band data from the Fitbit Air", 20, WK_FAINT))
    P.append("</svg>")
    return "\n".join(P)


def morning_caption(f: dict) -> str:
    n = f["night"]
    bits = []
    if n["total"] is not None:
        bits.append(f"slept {fmt_dur(n['total'])}" + (" so far" if n["state"] == "partial" else ""))
    if f["activity"]["steps"] is not None:
        bits.append(f"{f['activity']['steps']:,.0f} steps yesterday")
    return "☀️ *Morning card*" + (" · " + " · ".join(bits) if bits else "")


# ===================================================================== weekly cards
def _wk_label(s, e) -> str:
    a, b = datetime.date.fromisoformat(s), datetime.date.fromisoformat(e)
    return f"{a:%b %-d}–{b:%-d}" if a.month == b.month else f"{a:%b %-d}–{b:%b %-d}"


def week_facts(rows: dict, end: datetime.date) -> dict:
    cur = period_summary(rows, end, 7)
    prev = period_summary(rows, end - datetime.timedelta(days=7), 7)
    dates = _days(end, 7)
    return {"cur": cur, "prev": prev, "nights": [_night(rows, d) for d in dates],
            "acts": [_day_activity(rows, d) for d in dates], "rec": recovery_series(rows, end)}


def _chip_delta(x, y, w, h, label, value, delta, dcol) -> list[str]:
    return [f"<rect x='{x}' y='{y}' width='{w}' height='{h}' rx='20' fill='url(#wk-panel)' "
            f"stroke='{WK_PANEL_STROKE}' stroke-width='1.5'/>",
            _txt(x + 28, y + 44, label, 23, WK_MUTED),
            _txt(x + 28, y + 106, value, 52, WK_TXT, bold=True),
            _txt(x + 28, y + 142, delta or "no previous week to compare", 22, dcol if delta else WK_FAINT)]


def build_week_svgs(wf: dict) -> list[str]:
    cur, prev = wf["cur"], wf["prev"]
    rng = _wk_label(cur["start"], cur["end"])
    # ---------- card 1: the week ----------
    H1 = 172 + 2 * (164 + 24) + 10 + 420 + 30 + 420 + 70
    P = _svg_open(H1)
    P += _header("Week in review", f"{rng}   ·   compared with the week before")
    gap, cw, chh = 24, (W - 2 * M - 24) / 2, 164
    sl_d, sl_c = _cmp(cur["sleep_avg_h"], prev["sleep_avg_h"], comparable(cur, prev, "sleep"),
                      "vs last week", unit="h")
    st_d, st_c = _cmp(cur["steps_avg"], prev["steps_avg"], comparable(cur, prev, "steps"),
                      "vs last week", pct=True)
    az_d, az_c = _cmp(cur["azm_total"], prev["azm_total"], comparable(cur, prev, "azm"),
                      "vs last week", pct=True)
    chips = [
        ("Avg sleep", fmt_dur(cur["sleep_avg_h"]) if cur["sleep_avg_h"] else "—", sl_d, sl_c),
        ("Avg steps / day", f"{cur['steps_avg']:,.0f}" if cur["steps_avg"] else "—", st_d, st_c),
        ("Active Zone Minutes", f"{cur['azm_total']:,.0f}" if cur["azm_total"] is not None else "—", az_d, az_c),
        ("Workouts", (f"{cur['lifts_logged']} lift{'s' if cur['lifts_logged'] != 1 else ''}"
                      if cur["lifts_logged"] else "no lifts logged"),
         (f"band also saw {cur['band_sessions']} active sessions ({cur['band_session_min']} min)"
          if cur["band_data_days"] else "no band activity data this week"), WK_MUTED),
    ]
    for i, (lab, val, dt, dc) in enumerate(chips):
        cx = M + (i % 2) * (cw + gap)
        cyy = 172 + (i // 2) * (chh + gap)
        P += _chip_delta(cx, cyy, cw, chh, lab, val, dt, dc)

    # sleep columns
    sy = 172 + 2 * (chh + gap) + 10
    P.append(_panel(M, sy, W - 2 * M, 420))
    P.append(_txt(M + 34, sy + 52, "Sleep per night", 30, bold=True))
    sl_ok = comparable(cur, prev, "sleep")[0]
    if cur["sleep_nights"]:
        P.append(_txt(M + 300, sy + 52, f"{cur['nights_7h']}/{cur['sleep_nights']} nights at 7h+", 23,
                      GREEN if cur["nights_7h"] >= cur["sleep_nights"] * 0.7 else AMBER))
    else:
        P.append(_txt(M + 300, sy + 52, "no sleep recorded this week", 23, WK_FAINT))
    P += _legend(W - M - 34, sy + 44, [("7h target", GREEN, True),
                                        ("last week avg", WK_MUTED, True) if sl_ok else None])
    items = [{"value": n["total"], "label": datetime.date.fromisoformat(n["date"]).strftime("%a")}
             for n in wf["nights"]]
    ymax = max([n["total"] or 0 for n in wf["nights"]] + [9.0]) * 1.12
    P += _columns(M + 40, W - M - 40, sy + 92, sy + 360, items, ymax, _sleep_color,
                  lambda v: f"{v:.1f}", refs=[(SLEEP_TARGET_H, "", GREEN, True),
                                             (prev["sleep_avg_h"] if sl_ok else None, "", WK_MUTED, True)])
    # steps columns
    ty = sy + 450
    P.append(_panel(M, ty, W - 2 * M, 420))
    P.append(_txt(M + 34, ty + 52, "Steps per day", 30, bold=True))
    prev_ok = comparable(cur, prev, "steps")[0]
    P += _legend(W - M - 34, ty + 44, [("this week avg", STEP_COLOR, False),
                                        ("last week avg", WK_MUTED, True) if prev_ok else None])
    items = [{"value": a["steps"], "label": datetime.date.fromisoformat(a["date"]).strftime("%a")}
             for a in wf["acts"]]
    ymax = max([a["steps"] or 0 for a in wf["acts"]] + [prev["steps_avg"] or 0, 6000]) * 1.15
    P += _columns(M + 40, W - M - 40, ty + 92, ty + 360, items, ymax, lambda v: STEP_COLOR,
                  lambda v: f"{v / 1000:.1f}k",
                  refs=[(cur["steps_avg"], "", STEP_COLOR, False),
                        (prev["steps_avg"] if prev_ok else None, "", WK_MUTED, True)])
    P.append(_txt(M, H1 - 26, "Hermes · weekly review · 1 of 2", 20, WK_FAINT))
    P.append("</svg>")
    card1 = "\n".join(P)

    # ---------- card 2: recovery & rhythm ----------
    rec = dict(wf["rec"])
    first = next((i for i, (a, b) in enumerate(zip(rec["hrv"], rec["rhr"])) if a is not None or b is not None),
                 len(rec["dates"]))
    first = min(first, len(rec["dates"]) - 14)
    if first > 0:
        for k in ("dates", "hrv", "hrv_roll", "rhr", "rhr_roll"):
            rec[k] = rec[k][first:]
    H2 = 1500
    P = _svg_open(H2)
    P += _header("Recovery & rhythm", f"{rng}   ·   shaded band = your own normal")
    blocks = [("HRV (nightly RMSSD)", rec["hrv"], rec["hrv_roll"], rec["hrv_base"], rec["hrv_week_avg"],
               "ms", True, "#83dd5e"),
              ("Resting heart rate", rec["rhr"], rec["rhr_roll"], rec["rhr_base"], rec["rhr_week_avg"],
               "bpm", False, "#f5806f")]
    by = 172
    for title, raw, roll, base, wavg, unit, hib, col in blocks:
        P.append(_panel(M, by, W - 2 * M, 380))
        P.append(_txt(M + 34, by + 52, title, 30, bold=True))
        state = _band_state(wavg, base, hib, sum(1 for x in raw[-7:] if x is not None))
        stc = WK_MUTED if ("within" in state or "building" in state or state == "no data") else \
            (GREEN if "good" in state else AMBER)
        P.append(_txt(W - M - 34, by + 52, f"week avg {wavg:.0f} {unit}" if wavg is not None else "no readings",
                      26, WK_TXT, bold=True, anchor="end"))
        P.append(_txt(W - M - 34, by + 86, state.replace("his", "your"), 22, stc, anchor="end"))
        P += _spark(M + 34, by + 110, W - 2 * M - 68, 230, raw, base, col)
        # 7-night rolling mean overlay (the line that matters; single nights are dots)
        pts = [(i, v) for i, v in enumerate(roll) if v is not None]
        if pts and any(v is not None for v in raw):
            vals = [v for v in raw if v is not None] + [v for _, v in pts]
            if base and not base.get("building"):
                vals += [base["lo"], base["hi"]]
            lo_v, hi_v = min(vals), max(vals)
            pad = max((hi_v - lo_v) * 0.18, 1.0)
            lo_v, hi_v = lo_v - pad, hi_v + pad
            x, w, y, h = M + 34, W - 2 * M - 68, by + 110, 230
            n = max(len(raw) - 1, 1)
            pl = " ".join(f"{x + 14 + (w - 28) * i / n:.1f},{y + h - 10 - (h - 20) * (v - lo_v) / (hi_v - lo_v):.1f}"
                          for i, v in pts)
            P.append(f"<polyline points='{pl}' fill='none' stroke='{WK_TXT}' stroke-width='3' "
                     f"stroke-dasharray='2 8' stroke-linecap='round' opacity='0.8'/>")
        d0 = datetime.date.fromisoformat(rec["dates"][0])
        P.append(_txt(M + 34, by + 364, f"{d0:%b %-d}", 20, WK_FAINT))
        P.append(_txt(W - M - 34, by + 364, "dots = nights   ·   dotted = 7-night average", 20, WK_FAINT, anchor="end"))
        by += 410
    P.append(_panel(M, by, W - 2 * M, 470))
    P.append(_txt(M + 34, by + 52, "Bedtime & wake-up", 30, bold=True))
    sub = []
    if cur["bed_avg_h"] is not None:
        sub.append(f"avg bed {fmt_clock(cur['bed_avg_h'])}")
    if cur["wake_avg_h"] is not None:
        sub.append(f"wake {fmt_clock(cur['wake_avg_h'])}")
    if cur["bed_sd_h"] is not None:
        sub.append(f"bedtime varied ±{cur['bed_sd_h']:.1f}h")
    P.append(_txt(M + 34, by + 88, "   ·   ".join(sub) or "no sleep times this week", 23, WK_MUTED))
    if cur["late_nights"]:
        P += _pill(W - M - 34, by + 24, f"{cur['late_nights']} nights past 1 AM", AMBER, 22, anchor="end")
    P += _rhythm(M + 24, by + 108, W - 2 * M - 48, 340, wf["nights"])
    P.append(_txt(M, H2 - 26, "Hermes · weekly review · 2 of 2", 20, WK_FAINT))
    P.append("</svg>")
    return [card1, "\n".join(P)]


def week_caption(wf: dict) -> str:
    c = wf["cur"]
    bits = [f"📊 *Week in review* · {_wk_label(c['start'], c['end'])}"]
    if c["sleep_avg_h"]:
        bits.append(f"sleep {fmt_dur(c['sleep_avg_h'])} avg")
    if c["steps_avg"]:
        bits.append(f"{c['steps_avg']:,.0f} steps/day")
    return bits[0] + ("\n" + " · ".join(bits[1:]) if len(bits) > 1 else "") + "\n_Coach notes below._"


# ===================================================================== monthly card
def _calendar(x, y, cell, gap, year, month, colour_of, title, legend) -> list[str]:
    out = [_txt(x, y, title, 28, bold=True)]
    gy = y + 28
    for i, wd in enumerate("MTWTFSS"):
        out.append(_txt(x + i * (cell + gap) + cell / 2, gy + 20, wd, 18, WK_FAINT, anchor="middle"))
    first_wd = datetime.date(year, month, 1).weekday()
    nd = calendar.monthrange(year, month)[1]
    top = gy + 34
    for day in range(1, nd + 1):
        idx = first_wd + day - 1
        cx, cy = x + (idx % 7) * (cell + gap), top + (idx // 7) * (cell + gap)
        fill, outline = colour_of(datetime.date(year, month, day).isoformat())
        if outline:
            out.append(f"<rect x='{cx}' y='{cy}' width='{cell}' height='{cell}' rx='9' fill='none' "
                       f"stroke='{WK_PANEL_STROKE}' stroke-width='2' stroke-dasharray='3 5'/>")
        else:
            out.append(f"<rect x='{cx}' y='{cy}' width='{cell}' height='{cell}' rx='9' fill='{fill}'/>")
        out.append(_txt(cx + 7, cy + 20, day, 15, "#0b0d12" if not outline and fill not in ("#1a1f29",) else WK_FAINT,
                        bold=True))
    ly = top + 6 * (cell + gap) + 6
    lx = x
    for lab, col in legend:
        out.append(f"<rect x='{lx}' y='{ly}' width='20' height='20' rx='5' fill='{col}'/>")
        out.append(_txt(lx + 28, ly + 17, lab, 18, WK_MUTED))
        lx += 28 + len(lab) * 10 + 22
    return out


def _mix(c0, c1, t):
    a = [int(c0[i:i + 2], 16) for i in (1, 3, 5)]
    b = [int(c1[i:i + 2], 16) for i in (1, 3, 5)]
    return "#" + "".join(f"{round(a[i] + (b[i] - a[i]) * t):02x}" for i in range(3))


def build_month_svg(mf: dict) -> str:
    y, m = mf["year"], mf["month"]
    name = datetime.date(y, m, 1).strftime("%B %Y")
    s, p = mf["summary"], mf["prev"]
    by_date = {d["date"]: d for d in mf["days"]}
    food_any = s["food_days"] > 0
    H = 1640
    P = _svg_open(H)
    P += _header(f"{datetime.date(y, m, 1):%B} in review",
                 f"{name}{'  ·  so far' if mf['partial'] else ''}   ·   vs {datetime.date.fromisoformat(p['end']):%B}")
    # chips
    gap, cw, chh = 24, (W - 2 * M - 2 * 24) / 3, 164
    sl_d, sl_c = _cmp(s["sleep_avg_h"], p["sleep_avg_h"], comparable(s, p, "sleep"),
                      "vs last month", unit="h")
    st_d, st_c = _cmp(s["steps_avg"], p["steps_avg"], comparable(s, p, "steps"),
                      "vs last month", pct=True)
    chips = [("Avg sleep", fmt_dur(s["sleep_avg_h"]) if s["sleep_avg_h"] else "—", sl_d, sl_c),
             ("Nights at 7h+", f"{s['nights_7h']}/{s['sleep_nights']}" if s["sleep_nights"] else "—",
              f"of {s['sleep_nights']} nights recorded", WK_MUTED),
             ("Avg steps / day", f"{s['steps_avg']:,.0f}" if s["steps_avg"] else "—", st_d, st_c)]
    for i, (lab, val, dt, dc) in enumerate(chips):
        P += _chip_delta(M + i * (cw + gap), 172, cw, chh, lab, val, dt, dc)

    def day(ds):
        return by_date.get(ds, {"future": True})

    def sleep_col(ds):
        d = day(ds)
        if d.get("future") or d.get("sleep") is None:
            return None, True
        return _sleep_color(d["sleep"]), False
    st_vals = sorted(d["steps"] for d in mf["days"] if not d.get("future") and d.get("steps"))
    hi_steps = st_vals[int(len(st_vals) * 0.9) - 1] if len(st_vals) >= 3 else (st_vals[-1] if st_vals else 1)

    def steps_col(ds):
        d = day(ds)
        if d.get("future") or d.get("steps") is None:
            return None, True
        return _mix("#1a2a44", "#6fa6e2", max(0.0, min(1.0, d["steps"] / (hi_steps or 1)))), False

    def train_col(ds):
        d = day(ds)
        if d.get("future"):
            return None, True
        if d.get("lift"):
            return GREEN, False
        if (d.get("ex_min") or 0) >= 20:
            return "#3f7a39", False
        if (d.get("azm") or 0) >= 30:
            return "#2c4a2a", False
        if d.get("steps") is None:
            return None, True
        return "#1a1f29", False

    def food_col(ds):
        d = day(ds)
        if d.get("future") or d.get("kcal") is None:
            return None, True
        if d.get("protein") is not None and d["protein"] >= PROTEIN_TARGET and d["kcal"] >= KCAL_TARGET * 0.92:
            return GREEN, False
        return AMBER, False

    cell, cg = 62, 8
    calw = 7 * cell + 6 * cg
    lx, rx = M + 20, W - M - 20 - calw
    cy1, cy2 = 400, 1000
    P.append(_panel(M, cy1 - 52, W - 2 * M, 580))
    P += _calendar(lx, cy1, cell, cg, y, m, sleep_col, "Sleep",
                   [("7h+", GREEN), ("6–7h", AMBER), ("<6h", RED)])
    P += _calendar(rx, cy1, cell, cg, y, m, steps_col, "Steps",
                   [("fewer", "#1a2a44"), ("more", "#6fa6e2")])
    P.append(_panel(M, cy2 - 52, W - 2 * M, 580))
    P += _calendar(lx, cy2, cell, cg, y, m, train_col, "Training",
                   [("lift logged", GREEN), ("workout recorded", "#3f7a39"), ("active", "#2c4a2a")])
    if food_any:
        P += _calendar(rx, cy2, cell, cg, y, m, food_col, "Food logged",
                       [("hit kcal + protein", GREEN), ("logged, short", AMBER)])
    else:
        P.append(_txt(rx, cy2, "Food", 28, bold=True))
        P.append(_txt(rx, cy2 + 70, "Not logged this month.", 26, WK_MUTED))
        P.append(_txt(rx, cy2 + 108, "Log meals by messaging Hermes and", 22, WK_FAINT))
        P.append(_txt(rx, cy2 + 138, "this calendar fills in.", 22, WK_FAINT))
    pl = lambda n, w: f"{n} {w}" + ("" if n == 1 else "s")  # noqa: E731
    parts = [f"{pl(s['lifts_logged'], 'lift')} logged"]
    if s["band_data_days"]:
        parts.append(pl(s["band_sessions"], "band workout"))
    if s["sleep_nights"]:
        parts.append(f"{pl(s['late_nights'], 'night')} to bed after 1 AM")
    foot = "   ·   ".join(parts)
    P.append(_txt(M, H - 66, foot, 22, WK_MUTED))
    P.append(_txt(M, H - 26, "Hermes · monthly review", 20, WK_FAINT))
    P.append("</svg>")
    return "\n".join(P)


def month_caption(mf: dict) -> str:
    s = mf["summary"]
    name = datetime.date(mf["year"], mf["month"], 1).strftime("%B")
    bits = []
    if s["sleep_avg_h"]:
        bits.append(f"sleep {fmt_dur(s['sleep_avg_h'])} avg")
    if s["steps_avg"]:
        bits.append(f"{s['steps_avg']:,.0f} steps/day")
    return f"🗓 *{name} in review*" + ("\n" + " · ".join(bits) if bits else "")


# ===================================================================== render / send
def to_png(svg: str, name: str, out_dir: Path | None = None) -> Path:
    import cairosvg  # fitness venv only
    d = out_dir or charts_dir()
    d.mkdir(parents=True, exist_ok=True)
    p = d / name
    cairosvg.svg2png(bytestring=svg.encode(), write_to=str(p), output_width=W)
    return p


def week_extras(end: datetime.date, out_dir: Path | None) -> list[Path]:
    """The existing Week-in-Food and muscle-coverage cards, only when he logged that week."""
    extras = []
    try:
        import nutrition_card as NC
        if NC.parse_week(end.isoformat(), 7)["logged"] > 0:
            p = NC.render_weekly_png(end.isoformat(), 7)
            if p:
                extras.append(p)
    except Exception as e:  # noqa: BLE001
        print(f"week-food card skipped: {type(e).__name__}: {e}", file=sys.stderr)
    try:
        import fitness_report as FR
        p = FR.render_coverage_png(7)
        if p:
            extras.append(p)
    except Exception as e:  # noqa: BLE001
        print(f"coverage card skipped: {type(e).__name__}: {e}", file=sys.stderr)
    return extras


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("kind", choices=["morning", "week", "month"])
    ap.add_argument("--date")
    ap.add_argument("--end")
    ap.add_argument("--month")
    ap.add_argument("--sleep-state", choices=["final", "partial", "none"])
    ap.add_argument("--send", action="store_true")
    ap.add_argument("--caption")
    ap.add_argument("--caption-file")
    ap.add_argument("--no-extras", action="store_true")
    ap.add_argument("--svg", action="store_true", help="write SVG instead of PNG (no cairosvg needed)")
    ap.add_argument("--out", help="directory for the files (default: the vault Charts dir)")
    a = ap.parse_args(argv)
    today = now().date()
    rows = load_metrics()
    out_dir = Path(a.out) if a.out else None
    cap = a.caption
    if a.caption_file:
        try:
            cap = Path(a.caption_file).read_text(encoding="utf-8").strip() or cap
        except Exception:  # noqa: BLE001
            pass

    if a.kind == "morning":
        d = _d(a.date) if a.date else today
        f = morning_facts(rows, d, a.sleep_state)
        svgs, names, caption = [build_morning_svg(f)], [f"morning-{d}"], cap or morning_caption(f)
    elif a.kind == "week":
        end = _d(a.end) if a.end else today - datetime.timedelta(days=1)
        wf = week_facts(rows, end)
        svgs, names, caption = build_week_svgs(wf), [f"week-{end}", f"week-recovery-{end}"], cap or week_caption(wf)
    else:
        if a.month:
            yy, mm = (int(x) for x in a.month.split("-"))
        else:
            yy, mm = today.year, today.month
        try:
            mf = month_facts(rows, yy, mm, today)
        except ValueError as e:
            print(f"health_cards: {e}")
            return 2
        svgs, names, caption = [build_month_svg(mf)], [f"month-{yy}-{mm:02d}"], cap or month_caption(mf)

    if a.svg:
        d = out_dir or charts_dir()
        d.mkdir(parents=True, exist_ok=True)
        for s, n in zip(svgs, names):
            (d / f"{n}.svg").write_text(s, encoding="utf-8")
            print(d / f"{n}.svg")
        return 0
    pngs = [to_png(s, f"{n}.png", out_dir) for s, n in zip(svgs, names)]
    if a.kind == "week" and not a.no_extras:
        pngs += week_extras(_d(a.end) if a.end else today - datetime.timedelta(days=1), out_dir)
    for p in pngs:
        print(p)
    if not a.send:
        print(caption)
        return 0
    import fitness_report as FR
    ok = FR.send_photo(pngs[0], caption[:1020]) if len(pngs) == 1 else FR.send_album(pngs, caption[:1020])
    print("[SENT]" if ok else "send-failed")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
