#!/usr/bin/env python3
"""Sample: the finer-granularity muscle card (vulovix 70+ region SVG). Comparison
render so Sparsh can decide heat-map style. If kept, folds into muscle_heatmap.py."""
import json, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
from muscle_volume import parse_workouts, LANDMARKS, ORDER
from muscle_heatmap import _status_color, _bucket, BODY

ASSETS = Path(__file__).resolve().parent / "assets" / "muscle_paths_fine.json"

FINE = {
    "Chest": ["chest-upper-left", "chest-lower-left", "chest-upper-right", "chest-lower-right"],
    "Front Delts": ["shoulder-front-left", "shoulder-front-right"],
    "Side Delts": ["shoulder-side-left", "shoulder-side-right"],
    "Rear Delts": ["deltoid-rear-left", "deltoid-rear-right"],
    "Triceps": ["triceps-long-left", "triceps-lateral-left", "triceps-long-right", "triceps-lateral-right"],
    "Biceps": ["biceps-left", "biceps-right"],
    "Lats": ["lats-upper-left", "lats-mid-left", "lats-lower-left", "lats-upper-right", "lats-mid-right", "lats-lower-right"],
    "Mid-Back": ["traps-upper-left", "traps-mid-left", "traps-lower-left", "traps-upper-right", "traps-mid-right", "traps-lower-right"],
    "Quads": ["quads-left", "quads-right"],
    "Hamstrings": ["hamstrings-medial-left", "hamstrings-lateral-left", "hamstrings-medial-right", "hamstrings-lateral-right"],
    "Glutes": ["gluteus-medius-left", "gluteus-maximus-left", "gluteus-medius-right", "gluteus-maximus-right"],
    "Calves": ["calves-gastroc-medial-left", "calves-gastroc-lateral-left", "calves-soleus-left",
               "calves-gastroc-medial-right", "calves-gastroc-lateral-right", "calves-soleus-right"],
}
ID2GROUP = {i: g for g, ids in FINE.items() for i in ids}


def main():
    vol, n, _, window, _ = parse_workouts(7)
    start, today = window
    data = json.loads(ASSETS.read_text())
    gcolor = {g: _status_color([g], vol) for g in ORDER}
    F = "font-family='DejaVu Sans, sans-serif'"

    P = ['<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1448 2090" width="1180" height="1703">',
         '<rect width="1448" height="2090" fill="#0f1117"/>',
         f"<text x='60' y='82' fill='#fff' font-size='58' font-weight='bold' {F}>Muscle Coverage</text>",
         f"<text x='62' y='130' fill='#9aa0aa' font-size='31' {F}>{start:%b %-d}–{today:%b %-d}  ·  {n} workouts  ·  finer anatomy (70+ regions)</text>",
         "<line x1='60' y1='158' x2='1388' y2='158' stroke='#272c36' stroke-width='2'/>"]
    x = 60
    for c, lab in [("#e15759", "Under target"), ("#f1a340", "Maintenance"), ("#59a14f", "On target"),
                   ("#4e79a7", "High"), (BODY, "Not trained")]:
        P.append(f"<rect x='{x}' y='188' width='30' height='30' rx='6' fill='{c}'/>")
        P.append(f"<text x='{x+40}' y='211' fill='#cfd3da' font-size='26' {F}>{lab}</text>")
        x += 90 + len(lab) * 15
    # fine bodies, scaled from the 0..72 x 0..93 space into the body region
    P.append("<g transform='translate(232,250) scale(14.2)'>")
    for view in ("front", "back"):
        for mid, d in data[view].items():
            g = ID2GROUP.get(mid)
            fill = gcolor[g] if g else BODY
            P.append(f'<path d="{d}" fill="{fill}" stroke="#0f1117" stroke-width="0.12"/>')
    P.append("</g>")
    P.append("<text x='480' y='1620' fill='#9aa0aa' font-size='30' text-anchor='middle' font-family='sans-serif'>FRONT</text>")
    P.append("<text x='990' y='1620' fill='#9aa0aa' font-size='30' text-anchor='middle' font-family='sans-serif'>BACK</text>")
    # goals panel
    grow = [m for m in ORDER if _bucket(m, vol) == "grow"]
    gaps = [m for m in ORDER if _bucket(m, vol) == "gap"]
    solid = [m for m in ORDER if _bucket(m, vol) == "solid"]
    P.append("<line x1='60' y1='1700' x2='1388' y2='1700' stroke='#272c36' stroke-width='2'/>")
    y = 1748
    for lab, col, items in [("GROW — under target", "#e15759", grow), ("GAPS — no volume", "#9aa0aa", gaps),
                            ("SOLID — on track", "#59a14f", solid)]:
        if items:
            P.append(f"<text x='60' y='{y}' fill='{col}' font-size='30' font-weight='bold' {F}>{lab}</text>")
            P.append(f"<text x='400' y='{y}' fill='#e6e6e6' font-size='30' {F}>{'  ·  '.join(items)}</text>")
            y += 56
    P.append("</svg>")

    import cairosvg
    out = Path("muscle-card-fine.png")  # sample render; writes to cwd
    cairosvg.svg2png(bytestring="\n".join(P).encode(), write_to=str(out), output_width=1180, output_height=1703)
    print(f"→ {out}")


if __name__ == "__main__":
    main()
