#!/usr/bin/env python3
"""
coach.py — proactive strength & nutrition coach (Phase 4b).

The "real assistant" layer: instead of just logging, it READS the week's actual
data (training volume, nutrition adherence, sleep/recovery, bodyweight trend),
reads the coaching context (Profile + Training Plan + Coach Memory), and composes
a blunt, execution-first coaching narrative via a frontier model — then sends it
to Telegram. Templated fallback so it always lands.

Runs as a SCRIPT-MODE cron (sends itself via the Bot API, then prints the
{"wakeAgent": false} gate so Hermes skips the agent — same pattern as the brief).

  coach.py [--days N] [--dry-run] [--no-llm]
    --dry-run   compose + PRINT, do not send
    --no-llm    force the templated fallback (test offline)

Reads OPENAI_API_KEY + TELEGRAM_BOT_TOKEN from ~/.hermes/.env (like the brief).
Frontier model is configurable below; default GPT-5.5 (reuses the existing key).
"""
from __future__ import annotations

import datetime
import json
import os
import sys
import urllib.request
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parent))
from muscle_volume import parse_workouts, LANDMARKS, ORDER, VAULT  # noqa: E402

HOME = Path.home()
ENV_FILE = HOME / ".hermes" / ".env"
DAILY_DIR = VAULT / "04 - Daily Notes"
HEALTH = VAULT / "07 - Health"
TZ = ZoneInfo("America/Toronto")
CHAT_ID = "696500863"

# Frontier model for coaching judgment. GPT-5.5 via the OpenAI key the brief
# already uses; swap to a Claude model + base_url later if desired.
OPENAI_URL = "https://api.openai.com/v1/chat/completions"
COACH_MODEL = "gpt-5.5"

# Targets (from Profile.md — the locked baseline)
T_KCAL, T_PROTEIN, T_WATER, T_SLEEP, T_TRAIN_WK = 2400, 140, 2.5, 7.0, 4
UNDEREAT_KCAL = 1500   # a logged day below this trips the under-eating failure mode

DRY_RUN = "--dry-run" in sys.argv
NO_LLM = "--no-llm" in sys.argv
DAYS = 7
if "--days" in sys.argv:
    try:
        DAYS = int(sys.argv[sys.argv.index("--days") + 1])
    except (ValueError, IndexError):
        pass


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


def _read(path: Path, limit: int = 4000) -> str:
    try:
        return path.read_text(encoding="utf-8")[:limit]
    except Exception:  # noqa: BLE001
        return ""


def _daily_fm(date: str) -> dict:
    try:
        txt = (DAILY_DIR / f"{date}.md").read_text(encoding="utf-8")
    except Exception:  # noqa: BLE001
        return {}
    parts = txt.split("---")
    if len(parts) < 3:
        return {}
    fm = {}
    for ln in parts[1].splitlines():
        if ":" in ln and not ln.startswith((" ", "\t", "#")):
            k, v = ln.split(":", 1)
            fm[k.strip()] = v.strip()
    return fm


def _fnum(d: dict, k: str):
    try:
        return float(d.get(k))
    except (TypeError, ValueError):
        return None


def _avg(xs):
    xs = [x for x in xs if x is not None]
    return round(sum(xs) / len(xs), 1) if xs else None


def gather(days: int) -> dict:
    now = datetime.datetime.now(TZ)
    today = now.date()
    dates = [(today - datetime.timedelta(days=i)).isoformat() for i in range(days)]

    kcals, proteins, waters, sleeps, weights = [], [], [], [], []
    undereat, logged_food_days, no_food_days = 0, 0, 0
    for d in dates:
        fm = _daily_fm(d)
        is_today = d == today.isoformat()
        k = _fnum(fm, "kcal")
        if k is not None and not is_today:        # today is partial — don't judge it
            kcals.append(k)
            logged_food_days += 1
            if k < UNDEREAT_KCAL:
                undereat += 1
        elif k is None and not is_today:
            no_food_days += 1
        p = _fnum(fm, "protein_g")
        if p is not None and not is_today:
            proteins.append(p)
        w = _fnum(fm, "water_l")
        if w is not None and not is_today:
            waters.append(w)
        s = _fnum(fm, "sleep_hours")
        if s is not None:
            sleeps.append(s)
        bw = _fnum(fm, "weight")
        if bw is not None:
            weights.append((d, bw))

    # training volume
    vol, n_workouts, unmapped, window, used_dates = parse_workouts(days)
    under = [m for m in ORDER if 0 < vol.get(m, 0) < LANDMARKS[m][0]]   # below MEV
    gaps = [m for m in ORDER if vol.get(m, 0) == 0]
    strong = {m: round(vol[m], 1) for m in ORDER if vol.get(m, 0) > 0}

    weight_trend = None
    if len(weights) >= 2:
        weights.sort()
        weight_trend = round(weights[-1][1] - weights[0][1], 1)  # last - first over window

    return {
        "window_days": days,
        "training": {
            "sessions": n_workouts,
            "target_per_week": T_TRAIN_WK,
            "sets_by_muscle": strong,
            "below_MEV": under,
            "untrained": gaps,
        },
        "nutrition": {
            "avg_kcal": _avg(kcals), "target_kcal": T_KCAL,
            "avg_protein_g": _avg(proteins), "target_protein_g": T_PROTEIN,
            "days_logged": logged_food_days,
            "days_no_food_logged": no_food_days,
            "undereating_days": undereat, "undereating_threshold_kcal": UNDEREAT_KCAL,
            "avg_water_l": _avg(waters), "target_water_l": T_WATER,
        },
        "recovery": {
            "avg_sleep_h": _avg(sleeps), "target_sleep_h": T_SLEEP,
            "nights_logged": len([s for s in sleeps if s is not None]),
        },
        "bodyweight": {
            "trend_lb_over_window": weight_trend,
            "latest_lb": weights[-1][1] if weights else None,
            "target_gain_per_week_lb": "0.25-0.5",
        },
    }


COACH_SYSTEM = (
    "You are Sparsh's strength & nutrition coach. He's a high-execution operator and a "
    "near-beginner lifter on a LEAN BULK — underweight (~BMI 18.4), building 15-25 lb of "
    "mostly muscle over 6-12 months. The WHOLE system exists to defeat his two lifelong "
    "failure modes: (1) training consistency collapsing under load (school/work crunch), and "
    "(2) under-eating in busy seasons (drops to ~1 meal/day, loses muscle). COACH THE "
    "ADHERENCE, NOT THE PROGRAM — a decent plan he runs beats a perfect plan he quits.\n\n"
    "STYLE (non-negotiable): execution-first, blunt, numbers + plain English. NO lecturing, "
    "NO padding, NO therapy-mode, no 'great job!' filler — he already knows the theory. "
    "Surface the data, name the trend, give the single next concrete action. Flag drop-off "
    "(missed training, under-eating, short sleep, stalled weight) EARLY and hard. ~120-200 words, "
    "plain markdown. Use ONLY the data + context provided; never invent numbers.\n\n"
    "Structure: 1) one-line verdict on the week, 2) what's working (brief), 3) the clearest "
    "problem tied to a failure mode, 4) the ONE priority + concrete action for next week."
)


def compose_llm(facts: dict, profile: str, plan: str, memory: str) -> str | None:
    if NO_LLM:
        return None
    key = _env("OPENAI_API_KEY")
    if not key:
        return None
    user = (
        "COACHING CONTEXT (Profile / Training Plan / Coach Memory):\n"
        f"## Profile\n{profile}\n\n## Training Plan\n{plan}\n\n## Coach Memory\n{memory}\n\n"
        f"THIS WEEK'S DATA (last {facts['window_days']} days):\n{json.dumps(facts, indent=2)}\n\n"
        "Write this week's coaching message."
    )
    body = json.dumps({
        "model": COACH_MODEL,
        "messages": [{"role": "system", "content": COACH_SYSTEM},
                     {"role": "user", "content": user}],
        "max_completion_tokens": 2000,
    }).encode("utf-8")
    for _ in range(3):
        try:
            req = urllib.request.Request(
                OPENAI_URL, data=body,
                headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=60) as r:
                out = json.loads(r.read())["choices"][0]["message"]["content"].strip()
            if out:
                return out
        except Exception:  # noqa: BLE001
            continue
    return None


def compose_templated(f: dict) -> str:
    t, n, r, bw = f["training"], f["nutrition"], f["recovery"], f["bodyweight"]
    L = ["💪 *Weekly coach check-in*", ""]
    # training adherence (failure mode #1)
    flag = "⚠️" if t["sessions"] < t["target_per_week"] else "✅"
    L.append(f"{flag} Trained *{t['sessions']}/{t['target_per_week']}* sessions.")
    if t["below_MEV"]:
        L.append(f"   Under target: {', '.join(t['below_MEV'])}.")
    # nutrition (failure mode #2)
    if n["avg_kcal"] is not None:
        flag = "⚠️" if n["avg_kcal"] < T_KCAL - 200 else "✅"
        L.append(f"{flag} Avg *{int(n['avg_kcal'])} kcal* / {n['target_kcal']} · "
                 f"*{int(n['avg_protein_g'] or 0)}g* / {n['target_protein_g']} protein.")
    if n["undereating_days"]:
        L.append(f"   🚨 *{n['undereating_days']} under-eating day(s)* (<{UNDEREAT_KCAL} kcal) — the failure mode. Fix dinner.")
    if n["days_no_food_logged"]:
        L.append(f"   {n['days_no_food_logged']} day(s) with no food logged.")
    # recovery + weight
    if r["avg_sleep_h"] is not None:
        flag = "⚠️" if r["avg_sleep_h"] < T_SLEEP else "✅"
        L.append(f"{flag} Avg sleep *{r['avg_sleep_h']}h* / {T_SLEEP}.")
    if bw["trend_lb_over_window"] is not None:
        L.append(f"⚖️ Weight {'+' if bw['trend_lb_over_window'] >= 0 else ''}{bw['trend_lb_over_window']} lb "
                 f"over {f['window_days']}d (target +0.25-0.5/wk).")
    L += ["", "_(plain coach summary — rich compose was unavailable)_"]
    return "\n".join(L)


def send_message(text: str) -> bool:
    import urllib.parse
    token = _env("TELEGRAM_BOT_TOKEN")
    if not token:
        print("no TELEGRAM_BOT_TOKEN", file=sys.stderr)
        return False
    for pm in ("Markdown", None):
        payload = {"chat_id": CHAT_ID, "text": text}
        if pm:
            payload["parse_mode"] = pm
        try:
            req = urllib.request.Request(
                f"https://api.telegram.org/bot{token}/sendMessage",
                data=urllib.parse.urlencode(payload).encode())
            with urllib.request.urlopen(req, timeout=20) as r:
                if json.loads(r.read()).get("ok"):
                    return True
        except Exception:  # noqa: BLE001
            continue
    return False


def main() -> int:
    facts = gather(DAYS)
    profile = _read(HEALTH / "Profile.md")
    plan = _read(HEALTH / "Training Plan.md")
    memory = _read(HEALTH / "Coach Memory.md")
    msg = compose_llm(facts, profile, plan, memory) or compose_templated(facts)

    if DRY_RUN:
        print(msg)
        return 0
    if send_message(msg):
        print('{"wakeAgent": false}')   # sent itself → skip the agent
        return 0
    print(msg)                          # send failed → let Hermes deliver as fallback
    return 1


if __name__ == "__main__":
    sys.exit(main())
