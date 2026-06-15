#!/usr/bin/env python3
"""
coach_engine.py — shared coaching engine for the Phase 4b AI coach.

The deterministic + IO layer every coaching trigger reuses (the report's
architecture: metrics engine -> compose w/ structured output + validator ->
send -> memory write-back -> message budget). Keeping it in one module means the
weekly review, the midday under-eating rescue, the missed-workout rescue, and the
pre-workout preview all share the same grounded evidence packet and guardrails.

Pure stdlib (urllib) so it runs on system python. Compute the numbers HERE in
code; hand the model a compact evidence packet so it never invents metrics.
"""
from __future__ import annotations

import datetime
import json
import os
import re
import sys
import urllib.request
import urllib.parse
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parent))
from muscle_volume import parse_workouts, LANDMARKS, ORDER, VAULT, _norm  # noqa: E402

HOME = Path.home()
ENV_FILE = HOME / ".hermes" / ".env"
DAILY_DIR = VAULT / "04 - Daily Notes"
HEALTH = VAULT / "07 - Health"
WORKOUTS = HEALTH / "Workouts"
CSVP = HEALTH / "Metrics" / "metrics.csv"
STATE = HOME / ".hermes" / "health" / "coach_state.json"   # message budget / dedup
TZ = ZoneInfo("America/Toronto")
CHAT_ID = "696500863"

OPENAI_URL = "https://api.openai.com/v1/chat/completions"
COACH_MODEL = "gpt-5.5"

# Locked targets (Profile.md baseline)
TARGETS = {"kcal": 2400, "protein_g": 140, "water_l": 2.5, "sleep_h": 7.0, "training_days_per_week": 4}
UNDEREAT_KCAL = 1500


# ---------------------------------------------------------------- env / io
def env(key: str) -> str | None:
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


def now() -> datetime.datetime:
    return datetime.datetime.now(TZ)


def read(path: Path, limit: int = 4000) -> str:
    try:
        return path.read_text(encoding="utf-8")[:limit]
    except Exception:  # noqa: BLE001
        return ""


def daily_fm(date: str) -> dict:
    """Top-level frontmatter of a daily note as {key: str}."""
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


def meals_logged(date: str) -> set:
    """Meal types present in a day's food log (breakfast/lunch/dinner/snack)."""
    try:
        txt = (HEALTH / "Food Log" / f"{date}.md").read_text(encoding="utf-8")
    except Exception:  # noqa: BLE001
        return set()
    return {m.lower() for m in re.findall(r"^##\s+.*·\s*(\w+)", txt, re.M)}


def _f(d: dict, k: str):
    try:
        return float(d.get(k))
    except (TypeError, ValueError):
        return None


def _avg(xs):
    xs = [x for x in xs if x is not None]
    return round(sum(xs) / len(xs), 1) if xs else None


def _dates(days: int, end: datetime.date | None = None) -> list[str]:
    end = end or now().date()
    return [(end - datetime.timedelta(days=i)).isoformat() for i in range(days)]


# ---------------------------------------------------------------- per-lift progression
def lift_progression(days: int) -> list[dict]:
    """Per-exercise top-set trend over the window: last vs previous session."""
    import glob
    hist: dict[str, list[tuple[str, float]]] = {}
    today = now().date()
    start = today - datetime.timedelta(days=days - 1)
    for fpath in sorted(glob.glob(str(WORKOUTS / "*.md"))):
        date_s = Path(fpath).stem
        try:
            d = datetime.date.fromisoformat(date_s)
        except ValueError:
            continue
        if not (start <= d <= today):
            continue
        txt = read(Path(fpath), limit=20000)
        fm = txt.split("---")[1] if txt.count("---") >= 2 else txt
        cur, weights = None, []

        def flush():
            if cur and weights:
                hist.setdefault(_norm(cur), []).append((date_s, max(weights)))
        for ln in fm.split("\n"):
            m = re.match(r"^  - name:\s*(.+)$", ln)
            if m:
                flush()
                cur, weights = m.group(1).strip(), []
            else:
                wm = re.search(r"weight_lb:\s*([\d.]+)", ln)
                if cur and wm:
                    try:
                        weights.append(float(wm.group(1)))
                    except ValueError:
                        pass
        flush()
    out = []
    for lift, sessions in hist.items():
        sessions.sort()
        last = sessions[-1][1]
        prev = sessions[-2][1] if len(sessions) >= 2 else None
        status = "new" if prev is None else ("up" if last > prev else "down" if last < prev else "flat")
        out.append({"lift": lift, "last_top_lb": last, "prev_top_lb": prev, "status": status})
    return out


# ---------------------------------------------------------------- metrics.csv (recovery)
def _metrics_rows() -> list[dict]:
    import csv
    if not CSVP.exists():
        return []
    with CSVP.open(newline="") as fh:
        return list(csv.DictReader(fh))


def recovery_baseline() -> dict:
    rows = _metrics_rows()
    if not rows:
        return {}
    today = now().date().isoformat()
    recent = [r for r in rows if r.get("date", "") < today][-28:]

    def col(k):
        return [v for r in recent if (v := _f(r, k)) is not None]
    hrv, rhr = col("hrv_ms"), col("resting_hr")
    last = rows[-1] if rows else {}
    out = {}
    if hrv:
        base = sum(hrv) / len(hrv)
        cur = _f(last, "hrv_ms")
        out["hrv_ms"] = cur
        out["hrv_vs_baseline_pct"] = round((cur - base) / base * 100) if (cur and base) else None
    if rhr:
        base = sum(rhr) / len(rhr)
        cur = _f(last, "resting_hr")
        out["resting_hr"] = cur
        out["rhr_vs_baseline_bpm"] = round(cur - base) if (cur and base) else None
    return out


# ---------------------------------------------------------------- nutrition pace (midday rescue)
def todays_intake() -> dict:
    """How much has been eaten SO FAR today (partial-day, for the midday rescue)."""
    fm = daily_fm(now().date().isoformat())
    return {
        "kcal": _f(fm, "kcal") or 0,
        "protein_g": _f(fm, "protein_g") or 0,
        "meals": sorted(meals_logged(now().date().isoformat())),
    }


def intake_pace(n: datetime.datetime | None = None) -> dict:
    """Expected vs actual intake by this point of the eating day (07:00–23:00 window)."""
    n = n or now()
    intake = todays_intake()
    win_start, win_end = 7.0, 23.0
    h = n.hour + n.minute / 60
    frac = max(0.0, min(1.0, (h - win_start) / (win_end - win_start)))
    exp_kcal = round(TARGETS["kcal"] * frac)
    exp_prot = round(TARGETS["protein_g"] * frac)
    return {
        "time": n.strftime("%H:%M"),
        "kcal_so_far": int(intake["kcal"]), "kcal_expected_by_now": exp_kcal, "kcal_target": TARGETS["kcal"],
        "protein_so_far_g": int(intake["protein_g"]), "protein_expected_by_now_g": exp_prot, "protein_target_g": TARGETS["protein_g"],
        "meals_logged": intake["meals"],
        "behind_kcal": int(intake["kcal"]) < exp_kcal - 300,
        "behind_protein": int(intake["protein_g"]) < exp_prot - 25,
    }


# ---------------------------------------------------------------- full evidence packet (weekly/analysis)
def build_evidence(days: int = 7) -> dict:
    dates = _dates(days)
    today = now().date().isoformat()
    kcals, proteins, waters, sleeps, weights = [], [], [], [], []
    undereat, logged_food_days, no_food_days = 0, 0, 0
    for d in dates:
        fm = daily_fm(d)
        is_today = d == today
        k = _f(fm, "kcal")
        if k is not None and not is_today:
            kcals.append(k); logged_food_days += 1
            if k < UNDEREAT_KCAL:
                undereat += 1
        elif k is None and not is_today:
            no_food_days += 1
        if not is_today:
            p = _f(fm, "protein_g");  proteins.append(p) if p is not None else None
            w = _f(fm, "water_l");    waters.append(w) if w is not None else None
        s = _f(fm, "sleep_hours");    sleeps.append(s) if s is not None else None
        bw = _f(fm, "weight")
        if bw is not None:
            weights.append((d, bw))

    vol, n_workouts, unmapped, window, used_dates = parse_workouts(days)
    under = [m for m in ORDER if 0 < vol.get(m, 0) < LANDMARKS[m][0]]
    gaps = [m for m in ORDER if vol.get(m, 0) == 0]

    weights.sort()
    wt_trend = round(weights[-1][1] - weights[0][1], 1) if len(weights) >= 2 else None
    days_since_lift = None
    if used_dates:
        last = max(datetime.date.fromisoformat(d) for d in used_dates)
        days_since_lift = (now().date() - last).days

    flags = []
    if undereat or (kcals and _avg(kcals) and _avg(kcals) < TARGETS["kcal"] - 250):
        flags.append("under_eating_risk")
    if n_workouts < TARGETS["training_days_per_week"] or (days_since_lift or 0) >= 3:
        flags.append("consistency_risk")
    if _avg(sleeps) and _avg(sleeps) < TARGETS["sleep_h"] - 0.5:
        flags.append("sleep_debt")

    return {
        "window_days": days,
        "targets": TARGETS,
        "training": {
            "sessions_done": n_workouts, "planned_per_week": TARGETS["training_days_per_week"],
            "days_since_last_lift": days_since_lift,
            "sets_by_muscle": {m: round(vol[m], 1) for m in ORDER if vol.get(m, 0) > 0},
            "below_MEV": under, "untrained": gaps,
            "lift_progression": lift_progression(days),
        },
        "nutrition": {
            "avg_kcal": _avg(kcals), "avg_protein_g": _avg(proteins),
            "avg_water_l": _avg(waters),
            "days_logged": logged_food_days, "days_no_food_logged": no_food_days,
            "undereating_days": undereat, "undereating_threshold_kcal": UNDEREAT_KCAL,
        },
        "recovery": {"avg_sleep_h": _avg(sleeps), **recovery_baseline()},
        "bodyweight": {
            "trend_lb_over_window": wt_trend,
            "latest_lb": weights[-1][1] if weights else None,
            "target_gain_per_week_lb": "0.25-0.5",
        },
        "flags": flags,
    }


def context_docs() -> dict:
    return {
        "profile": read(HEALTH / "Profile.md"),
        "training_plan": read(HEALTH / "Training Plan.md"),
        "coach_memory": read(HEALTH / "Coach Memory.md"),
    }


# ---------------------------------------------------------------- frontier compose (+ structured/validate)
def compose_text(system: str, user: str, max_tokens: int = 2000) -> str | None:
    key = env("OPENAI_API_KEY")
    if not key:
        return None
    body = json.dumps({
        "model": COACH_MODEL,
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
        "max_completion_tokens": max_tokens,
    }).encode("utf-8")
    for _ in range(3):
        try:
            req = urllib.request.Request(OPENAI_URL, data=body, headers={
                "Authorization": f"Bearer {key}", "Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=60) as r:
                out = json.loads(r.read())["choices"][0]["message"]["content"].strip()
            if out:
                return out
        except Exception:  # noqa: BLE001
            continue
    return None


def compose_json(system: str, user: str, max_tokens: int = 2000) -> dict | None:
    """Frontier call constrained to a JSON object (response_format json_object)."""
    key = env("OPENAI_API_KEY")
    if not key:
        return None
    body = json.dumps({
        "model": COACH_MODEL,
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
        "response_format": {"type": "json_object"},
        "max_completion_tokens": max_tokens,
    }).encode("utf-8")
    for _ in range(3):
        try:
            req = urllib.request.Request(OPENAI_URL, data=body, headers={
                "Authorization": f"Bearer {key}", "Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=60) as r:
                raw = json.loads(r.read())["choices"][0]["message"]["content"]
            return json.loads(raw)
        except Exception:  # noqa: BLE001
            continue
    return None


def numbers_in(text: str) -> set:
    return set(re.findall(r"\d+(?:\.\d+)?", text or ""))


def validate(parsed: dict, evidence: dict) -> tuple[bool, str]:
    """Reject ungrounded/over-stuffed coaching. Returns (ok, reason)."""
    if not isinstance(parsed, dict):
        return False, "not a dict"
    analysis = parsed.get("analysis") or {}
    actions = parsed.get("actions") or {}
    conv = parsed.get("conversation") or {}
    today_actions = actions.get("today") or []
    if not analysis.get("primary_bottleneck"):
        return False, "no bottleneck"
    if not today_actions or len(today_actions) > 2:
        return False, f"actions count {len(today_actions)}"
    if not conv.get("question_for_user"):
        return False, "no question"
    # every number the model 'observed' must exist somewhere in the evidence packet
    ev_nums = numbers_in(json.dumps(evidence))
    claimed = numbers_in(" ".join(analysis.get("evidence", []) or []))
    ungrounded = [n for n in claimed if n not in ev_nums and len(n) >= 2]
    if ungrounded:
        return False, f"ungrounded numbers: {ungrounded[:5]}"
    return True, "ok"


# ---------------------------------------------------------------- send + budget + memory
def send_message(text: str) -> bool:
    token = env("TELEGRAM_BOT_TOKEN")
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


def _load_state() -> dict:
    try:
        return json.loads(STATE.read_text())
    except Exception:  # noqa: BLE001
        return {}


def _save_state(s: dict) -> None:
    try:
        STATE.parent.mkdir(parents=True, exist_ok=True)
        STATE.write_text(json.dumps(s))
    except Exception:  # noqa: BLE001
        pass


def budget_ok(category: str, red_alert: bool = False) -> tuple[bool, str]:
    """Message budget + dedup + quiet hours so the coach never over-nudges.
    Cap 2 proactive msgs/day (3 in red-alert), no same-category within 3h, quiet 22:30–07:00."""
    n = now()
    if n.hour >= 23 or n.hour < 7 or (n.hour == 22 and n.minute >= 30):
        return False, "quiet hours"
    s = _load_state()
    today = n.date().isoformat()
    day = s.get("day")
    sent = s.get("sent", []) if day == today else []
    cap = 3 if red_alert else 2
    if len(sent) >= cap:
        return False, f"daily cap {cap} reached"
    for e in sent:
        if e.get("category") == category:
            try:
                t = datetime.datetime.fromisoformat(e["at"])
                if (n - t).total_seconds() < 3 * 3600:
                    return False, "same category <3h ago"
            except Exception:  # noqa: BLE001
                pass
    return True, "ok"


def record_send(category: str) -> None:
    n = now()
    s = _load_state()
    today = n.date().isoformat()
    sent = s.get("sent", []) if s.get("day") == today else []
    sent.append({"category": category, "at": n.isoformat()})
    _save_state({"day": today, "sent": sent})


def memory_append(line: str) -> None:
    """Append a durable coaching fact to Coach Memory under a managed section."""
    path = HEALTH / "Coach Memory.md"
    try:
        txt = path.read_text(encoding="utf-8")
    except Exception:  # noqa: BLE001
        return
    stamp = now().strftime("%Y-%m-%d")
    entry = f"- ({stamp}) {line.strip()}"
    marker = "## Coach-logged (auto)"
    if marker in txt:
        txt = txt.replace(marker, f"{marker}\n{entry}", 1)
    else:
        txt = txt.rstrip() + f"\n\n{marker}\n{entry}\n"
    try:
        path.write_text(txt, encoding="utf-8")
    except Exception:  # noqa: BLE001
        pass


WAKE_GATE = '{"wakeAgent": false}'
