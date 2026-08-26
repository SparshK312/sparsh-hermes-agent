#!/usr/bin/env python3
"""
fitness_report.py — render the muscle-coverage card + a coached caption, send to Telegram.

The automated face of the fitness visuals. Runs the volume analysis, renders the
infographic card (muscle_heatmap.build_card -> cairosvg PNG), composes a short
COACH read via a focused OpenAI call (templated fallback so it always sends), and
delivers the photo to Telegram via the Bot API. Prints [SILENT] so Hermes (when this
runs as a script-mode cron) does not also post stdout.

  fitness_report.py [--days N] [--no-send]   (default 7-day window)

Reads TELEGRAM_BOT_TOKEN + ANTHROPIC_API_KEY from ~/.hermes/.env (like the brief gate).
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
from muscle_volume import parse_workouts, LANDMARKS, ORDER, VAULT  # noqa: E402
from muscle_heatmap import build_card, build_svg, _bucket  # noqa: E402

HOME = Path.home()
ENV_FILE = HOME / ".hermes" / ".env"
CHARTS = VAULT / "07 - Health" / "Charts"
TZ = ZoneInfo("America/Toronto")
CHAT_ID = "696500863"
# Migrated off OpenRouter 2026-08-26 — one vendor (Anthropic), and the OpenRouter
# key that lived in plaintext on the Mac is no longer needed by anything.
ANTHROPIC_MODEL = "claude-haiku-4-5"
ANTHROPIC_URL = "https://api.anthropic.com/v1/messages"


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
    lacking = f["grow"] + (["legs"] if any(m in f["gap"] for m in ("Quads", "Hamstrings", "Glutes")) else [])
    if lacking:
        bits.append("⚠️ Lagging: *" + "*, *".join(lacking) + "*")
    if f["solid"]:
        bits.append("✅ On track: " + ", ".join(f["solid"]))
    if f["high"]:
        bits.append("🔵 High volume: " + ", ".join(f["high"]))
    rec = []
    if f["grow"]:
        rec.append("add sets to " + " + ".join(f["grow"][:2]).lower())
    if any(m in f["gap"] for m in ("Quads", "Hamstrings", "Glutes")):
        rec.append("train legs")
    if rec:
        bits.append("→ Next week: " + "; ".join(rec) + ".")
    if not bits:
        bits.append("Solid week — volume in range across the board.")
    return "\n".join(bits)


def compose_note(f: dict) -> str:
    key = _env("ANTHROPIC_API_KEY")
    if not key:
        return _templated_note(f)
    # Anthropic takes the system prompt as a top-level field, not a message role,
    # and uses max_tokens (not max_completion_tokens). Response text is at
    # content[0].text rather than choices[0].message.content.
    body = json.dumps({
        "model": ANTHROPIC_MODEL,
        "max_tokens": 1200,
        "system": COACH_SYSTEM,
        "messages": [{"role": "user",
                      "content": "Weekly analysis:\n" + json.dumps(f, indent=2)}],
    }).encode()
    for _ in range(3):
        try:
            req = urllib.request.Request(
                ANTHROPIC_URL, data=body,
                headers={"x-api-key": key, "anthropic-version": "2023-06-01",
                         "content-type": "application/json"})
            with urllib.request.urlopen(req, timeout=45) as r:
                out = json.loads(r.read())["content"][0]["text"].strip()
            if out:
                return out
        except Exception:  # noqa: BLE001
            continue
    return _templated_note(f)


def _post_photo(token: str, png_bytes: bytes, caption: str, parse_mode: str | None) -> bool:
    # multipart/form-data by hand (stdlib only)
    boundary = "----fitnessreport7be3"
    fields = [("chat_id", CHAT_ID), ("caption", caption)]
    if parse_mode:
        fields.append(("parse_mode", parse_mode))
    parts = [f"--{boundary}\r\nContent-Disposition: form-data; name=\"{k}\"\r\n\r\n{v}\r\n"
             for k, v in fields]
    head = "".join(parts).encode()
    photo_head = (f"--{boundary}\r\nContent-Disposition: form-data; name=\"photo\"; "
                  f"filename=\"card.png\"\r\nContent-Type: image/png\r\n\r\n").encode()
    tail = f"\r\n--{boundary}--\r\n".encode()
    data = head + photo_head + png_bytes + tail
    req = urllib.request.Request(
        f"https://api.telegram.org/bot{token}/sendPhoto", data=data,
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read()).get("ok", False)


def send_photo(png: Path, caption: str) -> bool:
    token = _env("TELEGRAM_BOT_TOKEN")
    if not token:
        print("no TELEGRAM_BOT_TOKEN", file=sys.stderr)
        return False
    png_bytes = png.read_bytes()
    # Try Markdown; on ANY failure (most often a stray '*' tripping entity
    # parsing) retry once as plain text so the whole week's card never gets
    # dropped over a caption-formatting glitch.
    for pm in ("Markdown", None):
        try:
            if _post_photo(token, png_bytes, caption, pm):
                return True
        except Exception as e:  # noqa: BLE001
            print(f"send failed (parse_mode={pm}): {e}", file=sys.stderr)
    return False


def session_card_png(date: str) -> Path | None:
    """Render TODAY's single-session muscle card in HEAT mode (which muscles were worked,
    by intensity — NOT the weekly vs-target view, which would misread one session as
    'undertrained everywhere'). Returns the PNG path, or None if no workout today."""
    vol, n, unmapped, window, used = parse_workouts(1)   # today only
    if n == 0:
        return None
    import cairosvg
    CHARTS.mkdir(parents=True, exist_ok=True)
    png = CHARTS / f"session-{date}.png"
    cairosvg.svg2png(bytestring=build_svg(vol, "heat").encode(), write_to=str(png), output_width=1180)
    return png


def session_caption(date: str) -> str:
    vol, n, unmapped, window, used = parse_workouts(1)
    hit = ", ".join(m for m in ORDER if vol.get(m, 0) > 0) or "—"
    cap = f"💪 *Today's session — muscles worked*\n{hit}"
    if unmapped:
        cap += f"\n⚠️ not in the muscle map (undercounted): {', '.join(sorted(unmapped))}"
    return cap


def send_session_card(date: str) -> bool:
    """Render + send the single-session heat card to Telegram. Returns True on send."""
    png = session_card_png(date)
    if not png:
        return False
    return send_photo(png, session_caption(date)[:1020])


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=7)
    ap.add_argument("--session", action="store_true",
                    help="render TODAY's single session in heat mode (which muscles you hit) instead of the weekly vs-target card")
    ap.add_argument("--no-send", action="store_true")
    args = ap.parse_args()

    if args.session:
        today = datetime.datetime.now(TZ).strftime("%Y-%m-%d")
        png = session_card_png(today)
        if not png:
            # NOT [SILENT]: that marker means "delivered, stay quiet". Using it here
            # made "nothing to render" indistinguishable from success, so this cron
            # logged a clean exit for weeks while producing no card at all.
            print("[NO-DATA] no workout logged today")
            return 0
        if args.no_send:
            print(session_caption(today))
            print(f"(card → {png})")
            return 0
        ok = send_photo(png, session_caption(today)[:1020])
        print("[SILENT]" if ok else "send-failed")
        return 0 if ok else 1

    vol, n, unmapped, window, used = parse_workouts(args.days)
    if n == 0:
        # See above: distinct marker so a permanently-empty renderer is visible.
        # fitness-weekly last produced a card on 2026-07-19 and reported ok every
        # Sunday after that.
        print(f"[NO-DATA] no workouts in the last {args.days} days")
        return 0

    import cairosvg
    CHARTS.mkdir(parents=True, exist_ok=True)
    today = datetime.datetime.now(TZ).strftime("%Y-%m-%d")
    png = CHARTS / f"coverage-{today}.png"
    cairosvg.svg2png(bytestring=build_card(vol, window, n).encode(),
                     write_to=str(png), output_width=1180, output_height=1703)

    note = compose_note(_analysis_facts(vol, window, n))
    caption = f"💪 *Weekly Muscle Coverage*\n{note}"
    if unmapped:
        # Make a mapping gap VISIBLE instead of silently undercounting — these
        # exercises contributed 0 volume to the card. Add them to muscle_volume.MAP.
        caption += "\n\n⚠️ Not in the muscle map (volume undercounted): " + ", ".join(unmapped)

    if args.no_send:
        print(note)
        print(f"(card → {png})")
        return 0
    ok = send_photo(png, caption[:1020])
    print("[SILENT]" if ok else "send-failed")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
