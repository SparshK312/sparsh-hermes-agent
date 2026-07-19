#!/usr/bin/env python3
"""
nutrition_card.py — the "daily fuel" card: today's eating, as one dark infographic.

The food side of the muscle map. Parses the per-meal Food Log + daily-note totals and
renders a single card that answers "how's my day of eating going, right now":

  - a CALORIE RING (consumed vs the 2,400 target) colored by pace, with a tick at
    "where you should be by now" (time-of-day pacing, same logic as the post-log nudge);
  - MACRO bars — Protein is the hero (vs the locked 140 g target, low-flagged), Carbs +
    Fat shown in grams (only kcal + protein are locked targets in Profile.md);
  - a MEAL TIMELINE across the eating window, each block placed by clock time, sized by
    calories, and GLOWING GREEN by protein density — so you see *where* protein comes from;
  - a WATER bar (vs 2.5 L) and a one-line coach read.

Render stack mirrors the muscle card: build an SVG (no emoji — cairosvg has no emoji font;
those live in the Telegram caption), cairosvg→PNG, deliver via fitness_report.send_photo.

  nutrition_card.py [--date YYYY-MM-DD] [--no-send] [--out PATH]
"""
from __future__ import annotations

import argparse
import datetime
import os
import re
import sys
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parent))


def _default_vault() -> Path:
    env = os.environ.get("HERMES_VAULT")
    if env:
        return Path(env)
    vps = Path("/home/hermes/vault")
    if vps.exists():
        return vps
    return Path.home() / "Documents" / "School Vault - UofT"


VAULT = _default_vault()
FOODLOG_DIR = VAULT / "07 - Health" / "Food Log"
DAILY_DIR = VAULT / "04 - Daily Notes"
CHARTS = VAULT / "07 - Health" / "Charts"
TZ = ZoneInfo("America/Toronto")

# Targets (Profile.md, locked 2026-05-21). Only kcal + protein + water are real targets;
# carbs/fat are shown in grams against a visual reference, not labeled as goals.
KCAL_TARGET = 2400
PROTEIN_TARGET = 140
WATER_TARGET = 2.5
EAT_WINDOW = (7.0, 23.0)          # eating day used for time-of-day pacing
CARB_REF, FAT_REF = 300, 90       # bar-scaling references only (NOT targets)

# palette — matches muscle_heatmap
BG, PANEL, TRACK = "#0f1117", "#161922", "#2b303b"
TXT, MUTED, FAINT = "#e6e6e6", "#9aa0aa", "#6b7280"
RED, AMBER, GREEN, BLUE, PURPLE = "#e15759", "#f1a340", "#59a14f", "#4e79a7", "#9c6dab"
PROT_C, CARB_C, FAT_C = "#59a14f", "#4e79a7", "#f1a340"
F = "font-family='DejaVu Sans, sans-serif'"


# ----------------------------------------------------------------- parsing
_MEAL_RE = re.compile(r"^##\s*(\d{1,2}):(\d{2})\s*(AM|PM)\s*·\s*(\w+)", re.M | re.I)
_MACRO_RE = re.compile(
    r"\*\*Macros:\*\*\s*(\d+)\s*kcal.*?(\d+)g protein.*?(\d+)g carbs.*?(\d+)g fat",
    re.S,
)


def _to_hour(h: int, m: int, mer: str) -> float:
    h = h % 12
    if mer.upper() == "PM":
        h += 12
    return h + m / 60.0


def _fm_int(text: str, key: str) -> int:
    m = re.search(rf"^{key}:\s*(\d+)", text, re.M)
    return int(m.group(1)) if m else 0


def _read_water(date: str) -> float:
    note = DAILY_DIR / f"{date}.md"
    try:
        m = re.search(r"^water_l:\s*([\d.]+)", note.read_text(encoding="utf-8"), re.M)
        return float(m.group(1)) if m else 0.0
    except Exception:  # noqa: BLE001
        return 0.0


def parse_day(date: str) -> dict | None:
    """Return the day's fuel picture, or None if nothing's been logged yet.
    {totals:{kcal,protein,carbs,fat}, meals:[{hour,label,type,kcal,protein,carbs,fat}], water}"""
    path = FOODLOG_DIR / f"{date}.md"
    if not path.exists():
        # fall back to daily-note totals (rare — a meal logged before the food-log file)
        note = DAILY_DIR / f"{date}.md"
        if not note.exists():
            return None
        t = note.read_text(encoding="utf-8")
        tot = {k: _fm_int(t, src) for k, src in
               (("kcal", "kcal"), ("protein", "protein_g"), ("carbs", "carbs_g"), ("fat", "fat_g"))}
        if tot["kcal"] == 0:
            return None
        return {"totals": tot, "meals": [], "water": _read_water(date)}

    txt = path.read_text(encoding="utf-8")
    totals = {"kcal": _fm_int(txt, "total_kcal"), "protein": _fm_int(txt, "total_protein_g"),
              "carbs": _fm_int(txt, "total_carbs_g"), "fat": _fm_int(txt, "total_fat_g")}

    # split body into meal sections, pair each header with its macro line
    meals = []
    heads = list(_MEAL_RE.finditer(txt))
    for i, h in enumerate(heads):
        seg = txt[h.start():(heads[i + 1].start() if i + 1 < len(heads) else len(txt))]
        mm = _MACRO_RE.search(seg)
        if not mm:
            continue
        meals.append({
            "hour": _to_hour(int(h.group(1)), int(h.group(2)), h.group(3)),
            "label": f"{h.group(1)}:{h.group(2)} {h.group(3).upper()}",
            "type": h.group(4).lower(),
            "kcal": int(mm.group(1)), "protein": int(mm.group(2)),
            "carbs": int(mm.group(3)), "fat": int(mm.group(4)),
        })
    meals.sort(key=lambda m: m["hour"])
    if totals["kcal"] == 0 and not meals:
        return None
    return {"totals": totals, "meals": meals, "water": _read_water(date)}


# ----------------------------------------------------------------- pacing / coaching
def _pace_frac(now: datetime.datetime) -> float:
    h = now.hour + now.minute / 60
    return max(0.0, min(1.0, (h - EAT_WINDOW[0]) / (EAT_WINDOW[1] - EAT_WINDOW[0])))


def _cal_status(kcal: int, expected: int) -> tuple[str, str]:
    """(label, color) for the ring, blending time-of-day pace with the daily ceiling."""
    if kcal > KCAL_TARGET * 1.12:
        return "over target", RED      # coral — matches the g-coral ring/bar gradient
    if kcal >= KCAL_TARGET * 0.95:
        return "on target", GREEN
    if kcal >= expected - 300:
        return "on pace", GREEN
    if kcal >= expected - 600:
        return "a bit behind", AMBER
    return "behind pace", RED


def _coach_line(day: dict, now: datetime.datetime) -> str:
    tot = day["totals"]
    frac = _pace_frac(now)
    expected = round(KCAL_TARGET * frac)
    rem_k, rem_p = KCAL_TARGET - tot["kcal"], PROTEIN_TARGET - tot["protein"]
    if tot["kcal"] > KCAL_TARGET * 1.12:
        return f"{tot['kcal'] - KCAL_TARGET} kcal over — ease back tomorrow"
    if rem_p > 25:
        nxt = f"{min(rem_k, 800)} kcal left" if rem_k > 0 else "calories are there"
        return f"{rem_p}g protein to go — {nxt}, make it lean"
    if tot["kcal"] < expected - 300:
        return f"~{expected - tot['kcal']} kcal behind pace — eat something"
    if rem_p <= 0 and rem_k >= 0:
        return "protein hit, calories on track — dialed in"
    return "on track — keep it steady"


# ----------------------------------------------------------------- svg helpers
# status hex (from _cal_status) → the matching bar/ring gradient id
GRAD_OF = {GREEN: "url(#g-green)", AMBER: "url(#g-amber)", RED: "url(#g-coral)",
           PURPLE: "url(#g-coral)", BLUE: "url(#g-blue)"}


def _ring(cx, cy, r, sw, frac, grad, tick_frac=None) -> list[str]:
    """Gradient progress ring with a soft glow + an optional 'pace' tick."""
    import math
    C = 2 * math.pi * r
    fill = max(0.0, min(1.0, frac)) * C
    out = [
        f"<circle cx='{cx}' cy='{cy}' r='{r}' fill='none' stroke='#1a1f29' stroke-width='{sw}'/>",
        f"<circle cx='{cx}' cy='{cy}' r='{r}' fill='none' stroke='{grad}' stroke-width='{sw}' "
        f"stroke-linecap='round' stroke-dasharray='{fill:.1f} {C:.1f}' "
        f"transform='rotate(-90 {cx} {cy})' filter='url(#wk-glow)'/>",
    ]
    if tick_frac is not None:
        ang = -90 + 360 * max(0.0, min(1.0, tick_frac))
        a = math.radians(ang)
        x1, y1 = cx + (r - sw / 2 - 4) * math.cos(a), cy + (r - sw / 2 - 4) * math.sin(a)
        x2, y2 = cx + (r + sw / 2 + 4) * math.cos(a), cy + (r + sw / 2 + 4) * math.sin(a)
        out.append(f"<line x1='{x1:.1f}' y1='{y1:.1f}' x2='{x2:.1f}' y2='{y2:.1f}' "
                   f"stroke='{WK_TXT}' stroke-width='3.5'/>")
    return out


def _bar(x, y, w, h, frac, grad, label, value, marker_frac=None) -> list[str]:
    """Gradient progress bar with a glossy top highlight + an optional glowing target tick."""
    fillw = max(0.0, min(1.0, frac)) * w
    out = [
        f"<text x='{x}' y='{y - 16}' fill='{WK_MUTED}' font-size='27' {F}>{label}</text>",
        f"<text x='{x + w}' y='{y - 16}' fill='{WK_TXT}' font-size='27' font-weight='bold' "
        f"{F} text-anchor='end'>{value}</text>",
        f"<rect x='{x}' y='{y}' width='{w}' height='{h}' rx='{h/2}' fill='#0e1118'/>",
        f"<rect x='{x}' y='{y}' width='{fillw:.1f}' height='{h}' rx='{h/2}' fill='{grad}'/>",
    ]
    if fillw > 12:
        out.append(f"<rect x='{x+4}' y='{y+4}' width='{max(0.0, fillw-8):.1f}' height='7' rx='3.5' "
                   f"fill='#ffffff' opacity='0.20'/>")
    if marker_frac is not None:
        mx = x + max(0.0, min(1.0, marker_frac)) * w
        out.append(f"<line x1='{mx:.1f}' y1='{y - 8}' x2='{mx:.1f}' y2='{y + h + 8}' "
                   f"stroke='{WK_TXT}' stroke-width='3' filter='url(#wk-glow)'/>")
    return out


def _meal_blend(meal: dict) -> str:
    """Block fill: grey→green by protein density (protein kcal / total kcal). Protein-rich
    meals glow; carb/fat-heavy meals stay muted. Teaches where protein actually comes from."""
    if meal["kcal"] <= 0:
        return "#2b303b"
    dens = (meal["protein"] * 4) / meal["kcal"]      # ~0.05 (junk) … ~0.5 (lean protein)
    t = max(0.0, min(1.0, (dens - 0.08) / 0.32))
    base = (58, 65, 80)          # #3a4150 grey-blue
    tgt = (89, 161, 79)          # #59a14f green
    r, g, b = (round(base[i] + (tgt[i] - base[i]) * t) for i in range(3))
    return f"#{r:02x}{g:02x}{b:02x}"


def _meal_timeline(x0, x1, y, day, now, is_today) -> list[str]:
    """Clock-positioned meal blocks across the eating window; width ∝ calories, fill by
    protein density (glossy top). A faint 'now' line marks the current time."""
    out = [f"<text x='{x0}' y='{y - 30}' fill='{WK_TXT}' font-size='29' font-weight='bold' {F}>Meals through the day</text>",
           f"<text x='{x1}' y='{y - 30}' fill='{WK_FAINT}' font-size='21' {F} text-anchor='end'>greener = more protein</text>"]
    lo, hi = EAT_WINDOW
    span = x1 - x0
    track_y, track_h = y, 90

    def px(hour):
        return x0 + (max(lo, min(hi, hour)) - lo) / (hi - lo) * span

    out.append(f"<rect x='{x0}' y='{track_y}' width='{span}' height='{track_h}' rx='16' fill='#0e1118'/>")
    # hour ticks (noon, 6pm)
    for hr, lab in ((12, "12p"), (18, "6p")):
        tx = px(hr)
        out.append(f"<line x1='{tx:.0f}' y1='{track_y+8}' x2='{tx:.0f}' y2='{track_y + track_h-8}' "
                   f"stroke='{WK_GRID}' stroke-width='2'/>")
        out.append(f"<text x='{tx:.0f}' y='{track_y + track_h + 32}' fill='{WK_FAINT}' font-size='22' "
                   f"{F} text-anchor='middle'>{lab}</text>")

    meals = day["meals"]
    maxk = max((m["kcal"] for m in meals), default=1) or 1
    for m in meals:
        cx = px(m["hour"])
        bw = 34 + 72 * (m["kcal"] / maxk)            # 34–106 px wide by calories
        bx = max(x0 + 4, min(x1 - bw - 4, cx - bw / 2))
        bh = track_h - 22
        fill = _meal_blend(m)
        out.append(f"<rect x='{bx:.0f}' y='{track_y + 11}' width='{bw:.0f}' height='{bh}' rx='11' fill='{fill}'/>")
        out.append(f"<rect x='{bx+4:.0f}' y='{track_y + 15}' width='{bw-8:.0f}' height='6' rx='3' fill='#ffffff' opacity='0.16'/>")
        out.append(f"<text x='{bx + bw/2:.0f}' y='{track_y + track_h/2 + 9:.0f}' fill='{WK_TXT}' "
                   f"font-size='22' font-weight='bold' {F} text-anchor='middle'>{m['kcal']}</text>")
    # now marker (only meaningful on a live, same-day card)
    nh = now.hour + now.minute / 60
    if is_today and lo <= nh <= hi:
        nx = px(nh)
        out.append(f"<line x1='{nx:.0f}' y1='{track_y - 6}' x2='{nx:.0f}' y2='{track_y + track_h + 6}' "
                   f"stroke='{AMBER}' stroke-width='2.5' stroke-dasharray='3 6' stroke-linecap='round'/>")
        out.append(f"<text x='{nx:.0f}' y='{track_y - 14}' fill='{AMBER}' font-size='19' {F} text-anchor='middle'>now</text>")
    return out


# ----------------------------------------------------------------- build
def build_daily_svg(day: dict, date: str, now: datetime.datetime) -> str:
    tot = day["totals"]
    W, H = 1180, 1040
    frac = _pace_frac(now)
    expected = round(KCAL_TARGET * frac)
    cal_status, cal_color = _cal_status(tot["kcal"], expected)
    is_today = date == now.strftime("%Y-%m-%d")
    try:
        day_label = datetime.date.fromisoformat(date).strftime("%a %b %-d")
    except ValueError:
        day_label = date

    W, H, M = 1180, 1024, 56
    cal_grad = GRAD_OF.get(cal_color, "url(#g-green)")
    P = [
        f"<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 {W} {H}' width='{W}' height='{H}'>",
        _defs(),
        f"<rect x='0' y='0' width='{W}' height='{H}' fill='url(#wk-bg)'/>",
        f"<text x='{M}' y='94' fill='{WK_TXT}' font-size='60' font-weight='bold' {F}>Today's Fuel</text>",
        f"<text x='{M+2}' y='140' fill='{WK_MUTED}' font-size='29' {F}>{day_label}   ·   lean bulk   ·   {KCAL_TARGET:,} kcal / {PROTEIN_TARGET}g protein</text>",
    ]

    # ── hero row: calorie ring panel (left) + macros panel (right) ──
    hero_y, hero_h = 172, 448
    rpw = 432
    P.append(_panel(M, hero_y, rpw, hero_h))
    cx, cy, r, sw = M + rpw / 2, hero_y + 196, 138, 40
    P += _ring(cx, cy, r, sw, tot["kcal"] / KCAL_TARGET, cal_grad, tick_frac=frac)
    P += [
        f"<text x='{cx}' y='{cy - 2}' fill='{WK_TXT}' font-size='66' font-weight='bold' {F} text-anchor='middle'>{tot['kcal']:,}</text>",
        f"<text x='{cx}' y='{cy + 42}' fill='{WK_MUTED}' font-size='29' {F} text-anchor='middle'>/ {KCAL_TARGET:,} kcal</text>",
    ]
    pill_w, pill_y = 248, cy + r + 22
    P += [
        f"<rect x='{cx - pill_w/2}' y='{pill_y}' width='{pill_w}' height='46' rx='23' fill='{cal_color}' opacity='0.16'/>",
        f"<text x='{cx}' y='{pill_y + 32}' fill='{cal_color}' font-size='28' font-weight='bold' {F} text-anchor='middle'>{cal_status}</text>",
    ]

    mpx = M + rpw + 28
    mpw = W - M - mpx
    P.append(_panel(mpx, hero_y, mpw, hero_h))
    P.append(f"<text x='{mpx+34}' y='{hero_y+50}' fill='{WK_TXT}' font-size='31' font-weight='bold' {F}>Macros &amp; water</text>")
    bx, bw, bh = mpx + 34, mpw - 68, 40
    prot_color = GREEN if tot["protein"] >= PROTEIN_TARGET else (AMBER if tot["protein"] >= PROTEIN_TARGET * 0.7 else RED)
    prot_flag = "" if tot["protein"] >= PROTEIN_TARGET else "   low"
    P += _bar(bx, hero_y + 116, bw, bh, tot["protein"] / PROTEIN_TARGET, GRAD_OF[prot_color],
              "Protein", f"{tot['protein']} / {PROTEIN_TARGET} g{prot_flag}", marker_frac=1.0)
    P += _bar(bx, hero_y + 200, bw, bh, tot["carbs"] / CARB_REF, "url(#g-blue)", "Carbs", f"{tot['carbs']} g")
    P += _bar(bx, hero_y + 284, bw, bh, tot["fat"] / FAT_REF, "url(#g-gold)", "Fat", f"{tot['fat']} g")
    P.append(f"<line x1='{bx}' y1='{hero_y+356}' x2='{bx+bw}' y2='{hero_y+356}' stroke='{WK_PANEL_STROKE}' stroke-width='1'/>")
    water = day.get("water", 0.0)
    P += _bar(bx, hero_y + 392, bw, bh, water / WATER_TARGET, "url(#g-blue)", "Water",
              f"{water:.1f} / {WATER_TARGET:g} L")

    # ── meal timeline panel ──
    tp_y, tp_h = 648, 250
    P.append(_panel(M, tp_y, W - 2 * M, tp_h))
    P += _meal_timeline(M + 34, W - M - 34, tp_y + 96, day, now, is_today)

    # ── coach callout ──
    cl = _coach_line(day, now)
    cc_y = 912
    P += [
        f"<rect x='{M}' y='{cc_y}' width='{W-2*M}' height='72' rx='18' fill='#14324a' stroke='#1f4a66' stroke-width='1.5'/>",
        f"<text x='{M+28}' y='{cc_y+46}' fill='#9ecbff' font-size='30' font-weight='bold' {F}>&#8594; {cl}</text>",
        f"<text x='{M}' y='{H-22}' fill='{WK_FAINT}' font-size='20' {F}>Hermes · nutrition · protein is the hero metric</text>",
        "</svg>",
    ]
    return "\n".join(P)


# ----------------------------------------------------------------- weekly
def parse_week(end_date: str, days: int = 7) -> dict:
    """The N days ending at end_date (inclusive). Each day: date, weekday, totals.
    Unlogged days are kept with kcal=0 so the chart shows the gap honestly."""
    end = datetime.date.fromisoformat(end_date)
    out = []
    for i in range(days - 1, -1, -1):
        d = end - datetime.timedelta(days=i)
        ds = d.isoformat()
        day = parse_day(ds)
        tot = day["totals"] if day else {"kcal": 0, "protein": 0, "carbs": 0, "fat": 0}
        out.append({"date": ds, "wd": d.strftime("%a")[0], "dom": d.day, **tot})
    logged = [d for d in out if d["kcal"] > 0]
    n = len(logged) or 1
    avg = {k: round(sum(d[k] for d in logged) / n) for k in ("kcal", "protein", "carbs", "fat")}
    on_target = sum(1 for d in logged if KCAL_TARGET * 0.92 <= d["kcal"] <= 2500 * 1.04)
    prot_hits = sum(1 for d in logged if d["protein"] >= PROTEIN_TARGET)
    return {"days": out, "logged": len(logged), "avg": avg,
            "on_target": on_target, "prot_hits": prot_hits, "window": (out[0]["date"], out[-1]["date"])}


# ── visual kit for the weekly recap (gradients · panels · donut · glow) ──────
WK_TXT, WK_MUTED, WK_FAINT = "#f2f4f7", "#8b919e", "#565c69"
WK_PANEL_STROKE, WK_GRID = "#242b39", "#1d222c"
# gradient id -> (top, bottom) — richer at the base gives the bars depth
GRAD = {
    "g-green": ("#83dd5e", "#3f9a2e"),
    "g-amber": ("#f8cd54", "#dd8e1c"),
    "g-coral": ("#f5806f", "#d23a2a"),
    "g-blue":  ("#6fa6e2", "#3d6cb0"),
    "g-gold":  ("#f8aa40", "#e07d18"),
}


def _defs() -> str:
    out = ["<defs>",
           "<linearGradient id='wk-bg' x1='0' y1='0' x2='0' y2='1'>"
           "<stop offset='0' stop-color='#12151c'/><stop offset='1' stop-color='#0a0c11'/></linearGradient>",
           "<linearGradient id='wk-panel' x1='0' y1='0' x2='0' y2='1'>"
           "<stop offset='0' stop-color='#181c25'/><stop offset='1' stop-color='#10131a'/></linearGradient>"]
    for gid, (c0, c1) in GRAD.items():
        out.append(f"<linearGradient id='{gid}' x1='0' y1='0' x2='0' y2='1'>"
                   f"<stop offset='0' stop-color='{c0}'/><stop offset='1' stop-color='{c1}'/></linearGradient>")
    out.append("<filter id='wk-glow' x='-70%' y='-70%' width='240%' height='240%'>"
               "<feGaussianBlur stdDeviation='5' result='b'/>"
               "<feMerge><feMergeNode in='b'/><feMergeNode in='SourceGraphic'/></feMerge></filter>")
    out.append("</defs>")
    return "".join(out)


def _panel(x, y, w, h) -> str:
    return (f"<rect x='{x}' y='{y}' width='{w}' height='{h}' rx='24' fill='url(#wk-panel)' "
            f"stroke='{WK_PANEL_STROKE}' stroke-width='1.5'/>")


def _chip(x, y, w, h, label, value, accent) -> list[str]:
    """Stat card: gradient panel, an accent status dot, big white value, muted label."""
    return [
        f"<rect x='{x}' y='{y}' width='{w}' height='{h}' rx='20' fill='url(#wk-panel)' stroke='{WK_PANEL_STROKE}' stroke-width='1.5'/>",
        f"<circle cx='{x + 32}' cy='{y + 34}' r='8' fill='{accent}'/>",
        f"<text x='{x + w/2}' y='{y + 70}' fill='{WK_TXT}' font-size='54' font-weight='bold' {F} text-anchor='middle'>{value}</text>",
        f"<text x='{x + w/2}' y='{y + 104}' fill='{WK_MUTED}' font-size='24' {F} text-anchor='middle'>{label}</text>",
    ]


def _donut(cx, cy, r, sw, segs, center_top, center_bot) -> list[str]:
    """Premium ring chart for the macro split — rounded arc ends, small gaps, center label."""
    import math
    C = 2 * math.pi * r
    out = [f"<circle cx='{cx}' cy='{cy}' r='{r}' fill='none' stroke='#222936' stroke-width='{sw}'/>"]
    total = sum(v for _, v, _ in segs) or 1
    acc = 0.0
    GAP = 0.014
    for _lab, v, col in segs:
        frac = v / total
        seg = max(0.0, frac - GAP) * C
        out.append(f"<circle cx='{cx}' cy='{cy}' r='{r}' fill='none' stroke='{col}' stroke-width='{sw}' "
                   f"stroke-linecap='round' stroke-dasharray='{seg:.1f} {C - seg:.1f}' "
                   f"stroke-dashoffset='{-acc * C:.1f}' transform='rotate(-90 {cx} {cy})'/>")
        acc += frac
    out.append(f"<text x='{cx}' y='{cy - 2}' fill='{WK_TXT}' font-size='40' font-weight='bold' {F} text-anchor='middle'>{center_top}</text>")
    out.append(f"<text x='{cx}' y='{cy + 32}' fill='{WK_MUTED}' font-size='22' {F} text-anchor='middle'>{center_bot}</text>")
    return out


def _grad_columns(x0, x1, top, bot, days, key, ymax, fill_fn, grid_step, grid_fmt) -> list[str]:
    """7-day gradient column chart with faint gridlines + left axis labels, glossy bar
    tops, value labels, and a dashed hollow stub for unlogged days (a real empty-state,
    not a lonely dot). fill_fn(value)->gradient url."""
    out = []
    plot_h = bot - top

    def vy(v):
        return bot - max(0.0, min(1.0, v / ymax)) * plot_h

    gv = grid_step
    while gv < ymax * 0.98:
        gy = vy(gv)
        out.append(f"<line x1='{x0}' y1='{gy:.1f}' x2='{x1}' y2='{gy:.1f}' stroke='{WK_GRID}' stroke-width='1'/>")
        out.append(f"<text x='{x0 - 16:.0f}' y='{gy + 7:.0f}' fill='{WK_FAINT}' font-size='20' {F} text-anchor='end'>{grid_fmt(gv)}</text>")
        gv += grid_step
    out.append(f"<line x1='{x0}' y1='{bot}' x2='{x1}' y2='{bot}' stroke='{WK_PANEL_STROKE}' stroke-width='2'/>")

    n = len(days)
    slot = (x1 - x0) / n
    cw = min(78.0, slot * 0.56)
    for i, d in enumerate(days):
        cx = x0 + slot * (i + 0.5)
        v = d[key]
        if v > 0:
            ct = vy(v)
            out.append(f"<rect x='{cx - cw/2:.1f}' y='{ct:.1f}' width='{cw:.1f}' height='{bot - ct:.1f}' rx='11' fill='{fill_fn(v)}'/>")
            out.append(f"<rect x='{cx - cw/2:.1f}' y='{ct:.1f}' width='{cw:.1f}' height='10' rx='5' fill='#ffffff' opacity='0.22'/>")
            out.append(f"<text x='{cx:.0f}' y='{ct - 16:.0f}' fill='{WK_TXT}' font-size='24' font-weight='bold' {F} text-anchor='middle'>{v:,}</text>")
        else:
            out.append(f"<rect x='{cx - cw/2:.1f}' y='{bot - 46:.1f}' width='{cw:.1f}' height='46' rx='11' "
                       f"fill='none' stroke='{WK_PANEL_STROKE}' stroke-width='2' stroke-dasharray='3 7'/>")
        out.append(f"<text x='{cx:.0f}' y='{bot + 38:.0f}' fill='{WK_MUTED}' font-size='24' {F} text-anchor='middle'>{d['wd']}{d['dom']}</text>")
    return out


def _cal_col_color(v):
    if v > 2500 * 1.07:
        return "url(#g-coral)"          # over the bulk ceiling
    if v >= KCAL_TARGET * 0.92:
        return "url(#g-green)"          # in / near the target band
    return "url(#g-amber)"              # under — need more


def _prot_col_color(v):
    if v >= PROTEIN_TARGET:
        return "url(#g-green)"
    if v >= PROTEIN_TARGET * 0.7:
        return "url(#g-amber)"
    return "url(#g-coral)"


def _macro_insight(avg: dict) -> str:
    pk, ck, fk = avg["protein"] * 4, avg["carbs"] * 4, avg["fat"] * 9
    tk = (pk + ck + fk) or 1
    pp, cp = round(pk / tk * 100), round(ck / tk * 100)
    if avg["protein"] >= PROTEIN_TARGET:
        return "Protein dialed in — split's where you want it."
    lead = "Carbs-led" if ck >= pk and ck >= fk else ("Fat-led" if fk >= pk else "Protein-led")
    return f"{lead} ({cp}% C) — protein at {pp}% is the gap to close."


def build_weekly_svg(week: dict) -> str:
    W, H, M = 1180, 1420, 56
    days, avg = week["days"], week["avg"]
    s, e = week["window"]
    try:
        rng = f"{datetime.date.fromisoformat(s):%b %-d}–{datetime.date.fromisoformat(e):%b %-d}"
    except ValueError:
        rng = f"{s}–{e}"
    ph, nlog = week["prot_hits"], week["logged"]

    P = [
        f"<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 {W} {H}' width='{W}' height='{H}'>",
        _defs(),
        f"<rect x='0' y='0' width='{W}' height='{H}' fill='url(#wk-bg)'/>",
        f"<text x='{M}' y='94' fill='{WK_TXT}' font-size='60' font-weight='bold' {F}>Week in Food</text>",
        f"<text x='{M+2}' y='140' fill='{WK_MUTED}' font-size='29' {F}>{rng}   ·   {nlog} days logged   ·   lean bulk</text>",
    ]

    # ── stat chips ──
    gap = 28
    cw = (W - 2 * M - 2 * gap) / 3
    cy0, chh = 174, 124
    kcal_acc = GREEN if avg['kcal'] >= KCAL_TARGET * 0.92 else AMBER
    prot_acc = GREEN if avg['protein'] >= PROTEIN_TARGET else (AMBER if avg['protein'] >= PROTEIN_TARGET * 0.7 else RED)
    hit_acc = GREEN if ph >= nlog * 0.6 else (AMBER if ph else RED)
    P += _chip(M, cy0, cw, chh, "avg kcal / day", f"{avg['kcal']:,}", kcal_acc)
    P += _chip(M + cw + gap, cy0, cw, chh, "avg protein / day", f"{avg['protein']}g", prot_acc)
    P += _chip(M + 2 * (cw + gap), cy0, cw, chh, f"days protein ≥ {PROTEIN_TARGET}g", f"{ph}/{nlog}", hit_acc)

    # ── calories panel ──
    cp_y, cp_h = 342, 376
    P.append(_panel(M, cp_y, W - 2 * M, cp_h))
    P.append(f"<text x='{M+34}' y='{cp_y+52}' fill='{WK_TXT}' font-size='33' font-weight='bold' {F}>Calories / day</text>")
    P.append(f"<rect x='{W-M-296}' y='{cp_y+26}' width='276' height='36' rx='18' fill='{GREEN}' opacity='0.15'/>")
    P.append(f"<text x='{W-M-158}' y='{cp_y+50}' fill='#9fe08a' font-size='22' {F} text-anchor='middle'>target band {KCAL_TARGET}–{2500}</text>")
    cx0, cx1 = M + 92, W - M - 34
    ctop, cbot = cp_y + 100, cp_y + cp_h - 58
    cal_ymax = max(3000, max((d["kcal"] for d in days), default=0) + 300)
    yb_hi = cbot - min(1, 2500 / cal_ymax) * (cbot - ctop)
    yb_lo = cbot - min(1, KCAL_TARGET / cal_ymax) * (cbot - ctop)
    P.append(f"<rect x='{cx0}' y='{yb_hi:.0f}' width='{cx1-cx0}' height='{yb_lo-yb_hi:.0f}' rx='5' fill='{GREEN}' opacity='0.11'/>")
    P += _grad_columns(cx0, cx1, ctop, cbot, days, "kcal", cal_ymax, _cal_col_color,
                       1000, lambda v: f"{v//1000}k")

    # ── protein panel (the leak) — target line drawn ON TOP of the bars ──
    pp_y, pp_h = 742, 376
    P.append(_panel(M, pp_y, W - 2 * M, pp_h))
    P.append(f"<text x='{M+34}' y='{pp_y+52}' fill='{WK_TXT}' font-size='33' font-weight='bold' {F}>Protein / day</text>")
    P.append(f"<text x='{M+320}' y='{pp_y+52}' fill='{RED}' font-size='25' {F}>the leak — {ph}/{nlog} days hit {PROTEIN_TARGET}g</text>")
    px0, px1 = M + 92, W - M - 34
    ptop, pbot = pp_y + 100, pp_y + pp_h - 58
    prot_ymax = max(170, max((d["protein"] for d in days), default=0) + 30)
    P += _grad_columns(px0, px1, ptop, pbot, days, "protein", prot_ymax, _prot_col_color, 50, str)
    ly = pbot - min(1, PROTEIN_TARGET / prot_ymax) * (pbot - ptop)
    P.append(f"<line x1='{px0}' y1='{ly:.0f}' x2='{px1}' y2='{ly:.0f}' stroke='{GREEN}' stroke-width='3' "
             f"stroke-dasharray='2 9' stroke-linecap='round' filter='url(#wk-glow)'/>")
    P.append(f"<rect x='{px1-154}' y='{ly-40:.0f}' width='154' height='32' rx='16' fill='{GREEN}' opacity='0.18'/>")
    P.append(f"<text x='{px1-77}' y='{ly-17:.0f}' fill='#9fe08a' font-size='21' {F} text-anchor='middle'>{PROTEIN_TARGET}g target</text>")

    # ── macro donut panel — insight on the title row so it can't crowd the legend ──
    mp_y, mp_h = 1142, 232
    P.append(_panel(M, mp_y, W - 2 * M, mp_h))
    P.append(f"<text x='{M+34}' y='{mp_y+48}' fill='{WK_TXT}' font-size='30' font-weight='bold' {F}>Avg macro split</text>")
    P.append(f"<text x='{W-M-34}' y='{mp_y+48}' fill='{WK_MUTED}' font-size='24' {F} text-anchor='end'>{_macro_insight(avg)}</text>")
    pk, ck, fk = avg["protein"] * 4, avg["carbs"] * 4, avg["fat"] * 9
    tk = (pk + ck + fk) or 1
    dcx, dcy, dr, dsw = M + 142, mp_y + 138, 70, 28
    segs = [("P", pk, "url(#g-green)"), ("C", ck, "url(#g-blue)"), ("F", fk, "url(#g-gold)")]
    P += _donut(dcx, dcy, dr, dsw, segs, f"{round(pk/tk*100)}%", "protein")
    legx = dcx + 168
    leg = [("Protein", pk, "#6cc24a"), ("Carbs", ck, "#5b8fd6"), ("Fat", fk, "#f0992e")]
    for i, (lab, kv, col) in enumerate(leg):
        yy = mp_y + 106 + i * 46
        P.append(f"<rect x='{legx}' y='{yy-24}' width='28' height='28' rx='8' fill='{col}'/>")
        P.append(f"<text x='{legx+44}' y='{yy}' fill='{WK_TXT}' font-size='28' {F}>{lab}</text>")
        P.append(f"<text x='{legx+440}' y='{yy}' fill='{WK_MUTED}' font-size='28' {F} text-anchor='end'>{round(kv/tk*100)}%   ·   {round(kv):,} kcal</text>")

    P.append(f"<text x='{M}' y='{H-24}' fill='{WK_FAINT}' font-size='20' {F}>Hermes · nutrition · protein is the hero metric</text>")
    P.append("</svg>")
    return "\n".join(P)


def weekly_caption(week: dict) -> str:
    avg, ph = week["avg"], week["prot_hits"]
    s, e = week["window"]
    try:
        rng = f"{datetime.date.fromisoformat(s):%b %-d}–{datetime.date.fromisoformat(e):%b %-d}"
    except ValueError:
        rng = f"{s}–{e}"
    pf = "✅" if ph >= week["logged"] * 0.6 else "⚠️"
    return (f"📊 *Week in Food* · {rng}\nAvg {avg['kcal']:,} kcal · {avg['protein']}g protein/day  ·  "
            f"{pf} hit {PROTEIN_TARGET}g protein {ph}/{week['logged']} days")


def render_weekly_png(end_date: str, days: int = 7) -> Path | None:
    week = parse_week(end_date, days)
    if week["logged"] == 0:
        return None
    import cairosvg
    CHARTS.mkdir(parents=True, exist_ok=True)
    png = CHARTS / f"week-food-{end_date}.png"
    cairosvg.svg2png(bytestring=build_weekly_svg(week).encode(), write_to=str(png), output_width=1180)
    return png


def send_weekly(end_date: str, days: int = 7) -> bool:
    png = render_weekly_png(end_date, days)
    if not png:
        return False
    import fitness_report as FR
    return FR.send_photo(png, weekly_caption(parse_week(end_date, days))[:1020])


def render_daily_png(date: str) -> Path | None:
    day = parse_day(date)
    if not day:
        return None
    now = datetime.datetime.now(TZ)
    import cairosvg
    CHARTS.mkdir(parents=True, exist_ok=True)
    png = CHARTS / f"fuel-{date}.png"
    cairosvg.svg2png(bytestring=build_daily_svg(day, date, now).encode(),
                     write_to=str(png), output_width=1180)
    return png


def caption(date: str) -> str:
    day = parse_day(date)
    if not day:
        return "🍽 No food logged yet today."
    t = day["totals"]
    pf = "✅" if t["protein"] >= PROTEIN_TARGET else "⚠️"
    cap = (f"🍽 *Today's Fuel*\n{t['kcal']:,} / {KCAL_TARGET:,} kcal  ·  "
           f"{pf} {t['protein']}/{PROTEIN_TARGET}g protein  ·  {t['carbs']}C {t['fat']}F")
    if day.get("water"):
        cap += f"  ·  💧 {day['water']:.1f}L"
    return cap


def send_card(date: str) -> bool:
    png = render_daily_png(date)
    if not png:
        return False
    import fitness_report as FR  # reuse the Telegram plumbing (token, multipart, retry)
    return FR.send_photo(png, caption(date)[:1020])


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default=None, help="YYYY-MM-DD (default: today, America/Toronto)")
    ap.add_argument("--weekly", action="store_true", help="render the 7-day Week-in-Food report instead of the daily fuel card")
    ap.add_argument("--days", type=int, default=7, help="window for --weekly (default 7)")
    ap.add_argument("--no-send", action="store_true")
    ap.add_argument("--out", default=None)
    a = ap.parse_args()
    date = a.date or datetime.datetime.now(TZ).strftime("%Y-%m-%d")

    if a.weekly:
        week = parse_week(date, a.days)
        if week["logged"] == 0:
            print("[SILENT]")  # nothing logged in the window
            return 0
        if a.no_send or a.out:
            svg = build_weekly_svg(week)
            out = Path(a.out) if a.out else (CHARTS / f"week-food-{date}.svg")
            out.parent.mkdir(parents=True, exist_ok=True)
            if out.suffix == ".png":
                import cairosvg
                cairosvg.svg2png(bytestring=svg.encode(), write_to=str(out), output_width=1180)
            else:
                out.write_text(svg, encoding="utf-8")
            print(weekly_caption(week)); print(f"(card → {out})")
            return 0
        ok = send_weekly(date, a.days)
        print("[SILENT]" if ok else "send-failed")
        return 0 if ok else 1

    day = parse_day(date)
    if not day:
        print("[SILENT]")  # nothing logged — caller stays quiet
        return 0

    if a.no_send or a.out:
        now = datetime.datetime.now(TZ)
        svg = build_daily_svg(day, date, now)
        out = Path(a.out) if a.out else (CHARTS / f"fuel-{date}.svg")
        out.parent.mkdir(parents=True, exist_ok=True)
        if out.suffix == ".png":
            import cairosvg
            cairosvg.svg2png(bytestring=svg.encode(), write_to=str(out), output_width=1180)
        else:
            out.write_text(svg, encoding="utf-8")
        print(caption(date))
        print(f"(card → {out})")
        return 0

    ok = send_card(date)
    print("[SILENT]" if ok else "send-failed")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
