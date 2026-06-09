#!/usr/bin/env python3
"""
muscle_heatmap.py — anatomical muscle heat map (front + back) from training volume.

Renders a body silhouette with each muscle shaded by how hard it was trained this
week (sets vs its productive-volume target). Untrained muscles stay grey, so the
image answers "what got hit, what's lacking" at a glance — the visual the bar chart
couldn't. Body SVG paths are from react-native-body-highlighter (MIT), bundled in
assets/muscle_paths.json; the COLOR of each region comes from our own volume
analysis (muscle_volume.py).

  muscle_heatmap.py [--days N] [--out PATH] [--mode heat|status]
    heat   = intensity (grey→hot) — how much each muscle was worked  (default)
    status = vs target (grey/red/amber/green/blue) — the diff view
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from muscle_volume import parse_workouts, LANDMARKS, ORDER as ORDER_ALL  # noqa: E402

ASSETS = Path(__file__).resolve().parent / "assets" / "muscle_paths.json"
VAULT = Path(os.environ.get("HERMES_VAULT", str(Path.home() / "Documents" / "School Vault - UofT")))

# Body-region slug  →  our tracked muscle group(s).  Per view (front vs back),
# because the library reuses one "deltoids"/"trapezius" slug for both sides.
FRONT_SLUG = {
    "chest": ["Chest"],
    "deltoids": ["Front Delts", "Side Delts"],
    "biceps": ["Biceps"],
    "triceps": ["Triceps"],
    "trapezius": ["Mid-Back"],
    "quadriceps": ["Quads"],
    "calves": ["Calves"],
}
BACK_SLUG = {
    "deltoids": ["Rear Delts"],
    "upper-back": ["Lats"],
    "trapezius": ["Mid-Back"],
    "triceps": ["Triceps"],
    "hamstring": ["Hamstrings"],
    "gluteal": ["Glutes"],
    "calves": ["Calves"],
}

BG = "#0f1117"
BODY = "#2b303b"          # untrained muscle / silhouette
STROKE = "#161922"


def _heat_color(muscles, vol):
    """Intensity: grey at 0 → warm as volume approaches the productive ceiling."""
    import matplotlib.cm as cm
    import matplotlib.colors as mc
    vals = [vol.get(m, 0) / LANDMARKS[m][2] for m in muscles if m in LANDMARKS]  # /MAV_high
    if not vals:
        return BODY
    h = sum(vals) / len(vals)
    if h <= 0.02:
        return BODY
    h = min(h, 1.2) / 1.2
    return mc.to_hex(cm.get_cmap("YlOrRd")(0.25 + 0.7 * h))


def _status_color(muscles, vol):
    """Vs target: the diff view."""
    tot = sum(vol.get(m, 0) for m in muscles)
    mev = sum(LANDMARKS[m][0] for m in muscles if m in LANDMARKS)
    lo = sum(LANDMARKS[m][1] for m in muscles if m in LANDMARKS)
    hi = sum(LANDMARKS[m][2] for m in muscles if m in LANDMARKS)
    mrv = sum(LANDMARKS[m][3] for m in muscles if m in LANDMARKS)
    if tot <= 0.02:
        return BODY
    if tot < mev:
        return "#e15759"      # red — lacking
    if tot < lo:
        return "#f1a340"      # amber — maintenance
    if tot <= hi:
        return "#59a14f"      # green — productive
    if tot <= mrv:
        return "#4e79a7"      # blue — high
    return "#9c6dab"          # purple — over


def _bodies_svg(vol, color) -> list[str]:
    """Just the front+back path elements (caller wraps in <svg>/<g>)."""
    data = json.loads(ASSETS.read_text())
    out = []
    for view, slugmap in (("front", FRONT_SLUG), ("back", BACK_SLUG)):
        for slug, paths in data[view].items():
            fill = color(slugmap[slug], vol) if slug in slugmap else BODY
            if slug == "hair":
                fill = "#1a1d25"
            for d in paths:
                out.append(f'<path d="{d}" fill="{fill}" stroke="{STROKE}" stroke-width="1.2"/>')
    return out


def build_svg(vol, mode: str) -> str:
    color = _heat_color if mode == "heat" else _status_color
    parts = [
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1448 1448" width="1200" height="1200">',
        f'<rect x="0" y="0" width="1448" height="1448" fill="{BG}"/>',
        *_bodies_svg(vol, color),
        '<text x="362" y="1430" fill="#9aa0aa" font-size="34" font-family="sans-serif" text-anchor="middle">FRONT</text>',
        '<text x="1086" y="1430" fill="#9aa0aa" font-size="34" font-family="sans-serif" text-anchor="middle">BACK</text>',
        "</svg>",
    ]
    return "\n".join(parts)


# muscle → actionable bucket vs its own landmark
def _bucket(m, vol):
    mev, lo, hi, mrv = LANDMARKS[m]
    v = vol.get(m, 0)
    if v == 0:
        return "gap"
    if v < mev:
        return "grow"
    if v > hi:
        return "high"
    return "solid"


def build_card(vol, window, n) -> str:
    """Full infographic: title + legend + status-colored bodies + goals panel."""
    start, today = window
    F = "font-family='DejaVu Sans, sans-serif'"
    P = [
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1448 2090" width="1180" height="1703">',
        '<rect x="0" y="0" width="1448" height="2090" fill="#0f1117"/>',
        # header
        f"<text x='60' y='82' fill='#ffffff' font-size='58' font-weight='bold' {F}>Muscle Coverage</text>",
        f"<text x='62' y='130' fill='#9aa0aa' font-size='31' {F}>{start:%b %-d}–{today:%b %-d}  ·  {n} workouts  ·  weekly sets vs target</text>",
        "<line x1='60' y1='158' x2='1388' y2='158' stroke='#272c36' stroke-width='2'/>",
        # legend (swatch + label)
    ]
    legend = [("#e15759", "Under target"), ("#f1a340", "Maintenance"),
              ("#59a14f", "On target"), ("#4e79a7", "High"), (BODY, "Not trained")]
    x = 60
    for c, lab in legend:
        P.append(f"<rect x='{x}' y='188' width='30' height='30' rx='6' fill='{c}'/>")
        P.append(f"<text x='{x+40}' y='211' fill='#cfd3da' font-size='26' {F}>{lab}</text>")
        x += 90 + len(lab) * 15
    # bodies, pushed below the header
    P.append("<g transform='translate(0,250)'>")
    P += _bodies_svg(vol, _status_color)
    P.append("<text x='362' y='1425' fill='#9aa0aa' font-size='34' font-family='sans-serif' text-anchor='middle'>FRONT</text>")
    P.append("<text x='1086' y='1425' fill='#9aa0aa' font-size='34' font-family='sans-serif' text-anchor='middle'>BACK</text>")
    P.append("</g>")
    # goals panel
    grow = [m for m in ORDER_ALL if _bucket(m, vol) == "grow"]
    gaps = [m for m in ORDER_ALL if _bucket(m, vol) == "gap"]
    solid = [m for m in ORDER_ALL if _bucket(m, vol) == "solid"]
    high = [m for m in ORDER_ALL if _bucket(m, vol) == "high"]
    y = 1760
    P.append("<line x1='60' y1='1715' x2='1388' y2='1715' stroke='#272c36' stroke-width='2'/>")
    def row(label, col, items):
        nonlocal y
        if not items:
            return
        P.append(f"<text x='60' y='{y}' fill='{col}' font-size='30' font-weight='bold' {F}>{label}</text>")
        P.append(f"<text x='400' y='{y}' fill='#e6e6e6' font-size='30' {F}>{'  ·  '.join(items)}</text>")
        y += 56
    row("GROW — under target", "#e15759", grow)
    row("GAPS — no volume", "#9aa0aa", gaps)
    row("SOLID — on track", "#59a14f", solid)
    row("HIGH", "#4e79a7", high)
    # recommendation line
    rec_bits = []
    if grow:
        rec_bits.append("prioritize " + " + ".join(grow[:2]).lower())
    if any(m in gaps for m in ("Quads", "Hamstrings", "Glutes")):
        rec_bits.append("add a leg session")
    if rec_bits:
        y += 8
        P.append(f"<rect x='60' y='{y-38}' width='1328' height='66' rx='12' fill='#15324a'/>")
        P.append(f"<text x='84' y='{y+6}' fill='#9ecbff' font-size='30' font-weight='bold' {F}>→ This week: {'; '.join(rec_bits)}.</text>")
    P.append("</svg>")
    return "\n".join(P)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=7)
    ap.add_argument("--mode", choices=["heat", "status"], default="heat")
    ap.add_argument("--plain", action="store_true", help="bare body only (no card/legend/panel)")
    ap.add_argument("--out", default=str(VAULT / "07 - Health" / "Charts" / "muscle-card.png"))
    args = ap.parse_args()

    vol, n, unmapped, window, used = parse_workouts(args.days)

    import cairosvg
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    if args.plain:
        svg = build_svg(vol, args.mode)
        cairosvg.svg2png(bytestring=svg.encode(), write_to=str(out), output_width=1200, output_height=1200)
    else:
        svg = build_card(vol, window, n)
        cairosvg.svg2png(bytestring=svg.encode(), write_to=str(out), output_width=1180, output_height=1703)
    start, today = window
    print(f"{'plain '+args.mode if args.plain else 'card'} · {start:%b %-d}–{today:%b %-d} ({n} workouts) → {out}")
    if unmapped:
        print("unmapped exercises:", ", ".join(unmapped))


if __name__ == "__main__":
    main()
