#!/usr/bin/env python3
"""
fitness_report.py — render the muscle-coverage card + a coached caption, send to Telegram.

The automated face of the fitness visuals. Runs the volume analysis, renders the
infographic card (muscle_heatmap.build_card -> cairosvg PNG), composes a short
COACH read via a focused OpenAI call (templated fallback so it always sends), and
delivers the photo to Telegram via the Bot API. Prints [SILENT] so Hermes (when this
runs as a script-mode cron) does not also post stdout.

  fitness_report.py [--days N] [--no-send]   (default 7-day window)

Reads TELEGRAM_BOT_TOKEN + OPENAI_API_KEY from ~/.hermes/.env (like the brief gate).
"""
from __future__ import annotations

import argparse
import datetime
import json
import os
import sys
import urllib.request
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parent))
from muscle_volume import parse_workouts, LANDMARKS, ORDER  # noqa: E402
from muscle_heatmap import build_card, _bucket  # noqa: E402

HOME = Path.home()
ENV_FILE = HOME / ".hermes" / ".env"
VAULT = Path(os.environ.get("HERMES_VAULT", "/home/hermes/vault"))
CHARTS = VAULT / "07 - Health" / "Charts"
TZ = ZoneInfo("America/Toronto")
CHAT_ID = "696500863"
OPENAI_MODEL = "gpt-5.4-mini"


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


def _analysis_facts(vol, window, n) -> dict:
    start, today = window
    buckets = {"grow": [], "gap": [], "solid": [], "high": []}
    detail = {}
    for m in ORDER:
        b = _bucket(m, vol)
        buckets[b].append(m)
        if vol.get(m, 0) > 0:
            detail[m] = f"{vol[m]:.0f} sets ({b})"
    return {"window": f"{start:%b %-d}–{today:%b %-d}", "workouts": n,
            "per_muscle": detail, **buckets}


# ---- coached caption: rich (OpenAI) with a deterministic templated fallback ----
COACH_SYSTEM = (
    "You are Sparsh's strength coach. Given his weekly muscle-volume analysis (sets "
    "per muscle vs RP volume landmarks), write a SHORT punchy Telegram caption: what's "
    "solid, the clearest gap, and the single priority for next week. He's an experienced "
    "lifter — be direct and specific (name muscles + numbers), NO greeting, NO fluff, "
    "plain text with *bold* for emphasis. Max ~55 words."
)


def _templated_note(f: dict) -> str:
    bits = []
    if f["grow"] or f["gap"]:
        lacking = f["grow"] + (["legs"] if any(m in f["gap"] for m in ("Quads", "Hamstrings", "Glutes")) else [])
        bits.append("⚠️ Lagging: *" + "*, *".join(lacking) + "*")
    if f["solid"]:
        bits.append("✅ On track: " + ", ".join(f["solid"]))
    rec = []
    if f["grow"]:
        rec.append("add sets to " + " + ".join(f["grow"][:2]).lower())
    if any(m in f["gap"] for m in ("Quads", "Hamstrings", "Glutes")):
        rec.append("train legs")
    if rec:
        bits.append("→ Next week: " + "; ".join(rec) + ".")
    return "\n".join(bits)


def compose_note(f: dict) -> str:
    key = _env("OPENAI_API_KEY")
    if not key:
        return _templated_note(f)
    body = json.dumps({
        "model": OPENAI_MODEL,
        "messages": [{"role": "system", "content": COACH_SYSTEM},
                     {"role": "user", "content": "Weekly analysis:\n" + json.dumps(f, indent=2)}],
        "max_completion_tokens": 1200,
    }).encode()
    for _ in range(3):
        try:
            req = urllib.request.Request(
                "https://api.openai.com/v1/chat/completions", data=body,
                headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=45) as r:
                out = json.loads(r.read())["choices"][0]["message"]["content"].strip()
            if out:
                return out
        except Exception:  # noqa: BLE001
            continue
    return _templated_note(f)


def send_photo(png: Path, caption: str) -> bool:
    token = _env("TELEGRAM_BOT_TOKEN")
    if not token:
        print("no TELEGRAM_BOT_TOKEN", file=sys.stderr)
        return False
    # multipart/form-data by hand (stdlib only)
    boundary = "----fitnessreport7be3"
    parts = []
    for k, v in (("chat_id", CHAT_ID), ("caption", caption), ("parse_mode", "Markdown")):
        parts.append(f"--{boundary}\r\nContent-Disposition: form-data; name=\"{k}\"\r\n\r\n{v}\r\n")
    head = "".join(parts).encode()
    photo_head = (f"--{boundary}\r\nContent-Disposition: form-data; name=\"photo\"; "
                  f"filename=\"card.png\"\r\nContent-Type: image/png\r\n\r\n").encode()
    tail = f"\r\n--{boundary}--\r\n".encode()
    data = head + photo_head + png.read_bytes() + tail
    req = urllib.request.Request(
        f"https://api.telegram.org/bot{token}/sendPhoto", data=data,
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read()).get("ok", False)
    except Exception as e:  # noqa: BLE001
        print(f"send failed: {e}", file=sys.stderr)
        return False


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=7)
    ap.add_argument("--no-send", action="store_true")
    args = ap.parse_args()

    vol, n, unmapped, window, used = parse_workouts(args.days)
    if n == 0:
        print("[SILENT]")  # no workouts in window — nothing to report
        return 0

    import cairosvg
    CHARTS.mkdir(parents=True, exist_ok=True)
    today = datetime.datetime.now(TZ).strftime("%Y-%m-%d")
    png = CHARTS / f"coverage-{today}.png"
    cairosvg.svg2png(bytestring=build_card(vol, window, n).encode(),
                     write_to=str(png), output_width=1180, output_height=1703)

    note = compose_note(_analysis_facts(vol, window, n))
    caption = f"💪 *Weekly Muscle Coverage*\n{note}"

    if args.no_send:
        print(note)
        print(f"(card → {png})")
        return 0
    ok = send_photo(png, caption[:1020])
    print("[SILENT]" if ok else "send-failed")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
