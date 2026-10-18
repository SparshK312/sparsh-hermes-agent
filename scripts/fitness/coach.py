#!/usr/bin/env python3
"""
coach.py — the Phase 4b AI coach (multi-mode entry point on coach_engine).

Modes (--mode):
  weekly         check-in (Sunday = weekly, other days = mid-week): one win, one focus, one
                 action, validated, sent to Telegram + the focus written back to Coach Memory.
  monthly        the month's check-in (sent after the calendar card by health_monthly.sh).
  meal-rescue    midday under-eating rescue — fires ONLY if behind pace + budget allows.
  workout-rescue missed-workout rescue — fires ONLY if idle 2+ days & behind this week.
  preview        pre-workout preview ("beat last time") — on demand.

All compute is done deterministically in coach_engine (no hallucinated numbers).
Self-sends via the Bot API, then prints {"wakeAgent": false} so Hermes skips the
agent. Rescues stay SILENT (just the gate) when not warranted — no over-nudging.

  coach.py [--mode weekly|meal-rescue|workout-rescue|preview] [--days N] [--dry-run] [--no-llm]
"""
from __future__ import annotations

import datetime
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import coach_engine as E  # noqa: E402

DRY = "--dry-run" in sys.argv
NO_LLM = "--no-llm" in sys.argv
MODE = "weekly"
if "--mode" in sys.argv:
    try:
        MODE = sys.argv[sys.argv.index("--mode") + 1]
    except IndexError:
        pass
DAYS = 7
if "--days" in sys.argv:
    try:
        DAYS = int(sys.argv[sys.argv.index("--days") + 1])
    except (ValueError, IndexError):
        pass


def _emit(msg: str, category: str, *, gate_when_silent=True) -> int:
    """Deliver a coaching message: dry-run prints; else send + gate, fallback to agent."""
    if DRY:
        print(msg)
        return 0
    if E.send_message(msg):
        E.record_send(category)
        print(E.WAKE_GATE)
        return 0
    print(msg)   # send failed → let Hermes deliver
    return 1


def _silent() -> int:
    print(E.WAKE_GATE if not DRY else "[silent — trigger not warranted]")
    return 0


# ----------------------------------------------------------------- weekly / mid-week / monthly check-in
# 2026-10-17 rewrite (his ask: "tell me things that are going well and what to work on").
# The old JSON was bottleneck + up to 4 evidence bullets + 2 "today" actions, built on a
# packet that only knew hand-logged food/lifts/weight — which he had logged on 2, 1 and 0
# of the last 30 days. So nearly every check-in was "walk into the gym today" (~20 of 32).
# Now: one WIN, one FOCUS, one ACTION, from the band data first, against his own numbers.
CHECKIN_SYS = (
    "You are Sparsh's health & fitness coach writing his {period} check-in for Telegram. "
    "He's a busy university student / intern, a near-beginner lifter on a LEAN BULK; the two "
    "lifelong failure modes are training consistency collapsing under load and under-eating. "
    "Voice: plain English, blunt but warm, numbers-first, no hype, no lecturing, no therapy voice.\n\n"
    "DATA RULES (hard):\n"
    "- Use ONLY numbers in the evidence packet. `band` is what the Fitbit recorded automatically; "
    "compare `this_period` with `previous_period` and his own normal range — never population norms. "
    "Profile targets (7h sleep, 2400 kcal, 140 g protein, 4 sessions/week) are fine to cite.\n"
    "- A comparison is only valid when BOTH periods have at least 4 days of that metric. A null in "
    "`previous_period` (see `not_comparable`) means there is NOTHING to compare against: describe "
    "this period on its own, never as a jump, drop or 'fixed itself'.\n"
    "- Recovery: never react to one night. Only mention HRV / resting HR if `recovery_trend` says the "
    "week's average is outside his normal range; 'baseline building' means say nothing about it.\n"
    "- `band_auto_detected_sessions` are things the Fitbit detected (mostly walks), NOT lifting.\n"
    "- Food, weight and lifts are hand-logged. 0 / blank means NOT LOGGED — never 'you didn't eat' or "
    "'you didn't train'. When they're not logged, coach on what the band shows. You may mention "
    "logging in at most one short clause. Logging is NEVER the win or the focus (his call: the "
    "check-in must work whether or not he logs). The win and the focus must each come from an area "
    "with data THIS period: no food logged -> neither is about food or nutrition; no lifts logged -> "
    "neither is about training. Then pick from sleep length, bedtime regularity, nights past 1 AM, "
    "steps, zone minutes or recovery.\n"
    "- If `flags` has lifts_not_logged, you do NOT know whether he trained. Don't make training the "
    "focus, and never say 'zero sessions', 'untrained', 'haven't lifted' or 'gym at zero'. You may ask "
    "about training in question_for_user.\n"
    "- Logging happens by messaging Hermes on Telegram. Never mention MacroFactor, Hevy or any app.\n"
    "- RECENT PRIORITIES lists what you told him before. Do not repeat a focus with the same wording. "
    "If the same problem is still there, change the approach (a smaller step, a different time or "
    "trigger) and say that it's a new angle.\n\n"
    "Return a JSON object EXACTLY in this shape:\n"
    "{{\n"
    '  "headline": str,            // the {period} in <= 10 words\n'
    '  "risk_level": "green"|"yellow"|"red",\n'
    '  "win":   {{"title": str, "evidence": str}},   // title <= 8 words; evidence <= 25 words: the number vs before\n'
    '  "focus": {{"title": str, "evidence": str}},   // the ONE thing to work on; same limits\n'
    '  "action": str,              // ONE specific action for the next {horizon} (what, when, how much), <= 30 words\n'
    '  "fallback_if_busy": str,    // the minimum version of that action, <= 15 words\n'
    '  "question_for_user": str    // optional, <= 20 words; "" if none. Ask only if the answer changes next {period}\n'
    "}}\n"
    "Gain-framed: say what doing the action gets him. The win must be real; if nothing improved, name "
    "the most stable good thing honestly. Short sentences; it is read on a phone."
)


def _period_label(n: datetime.date, monthly: bool = False) -> tuple[str, str, str]:
    """(period word, horizon, header) — Sunday is the weekly review, other days mid-week."""
    if monthly:
        return "month", "month", "🗓 *Monthly check-in*"
    if n.weekday() == 6:
        return "week", "7 days", "💪 *Weekly check-in*"
    return "week so far", "few days", "💪 *Mid-week check-in*"


def _render_checkin(p: dict, header: str, rng: str = "") -> str:
    risk = {"red": "🔴", "yellow": "🟡", "green": "🟢"}.get(p.get("risk_level", ""), "")
    win, focus = p.get("win") or {}, p.get("focus") or {}
    L = [f"{header}{' · ' + rng if rng else ''} {risk}".strip()]
    if p.get("headline"):
        L.append(f"_{p['headline']}_")
    L.append("")
    L.append(f"✅ *Going well:* {win.get('title', '')}")
    if win.get("evidence"):
        L.append(f"     {win['evidence']}")
    L.append(f"🎯 *Work on:* {focus.get('title', '')}")
    if focus.get("evidence"):
        L.append(f"     {focus['evidence']}")
    L += ["", f"→ *Next:* {p.get('action', '')}"]
    if p.get("fallback_if_busy"):
        L.append(f"_If it gets busy:_ {p['fallback_if_busy']}")
    q = (p.get("question_for_user") or "").strip()
    if q:
        L += ["", q]
    return "\n".join(L)


def _templated_checkin(ev: dict, header: str) -> str:
    """Deterministic fallback — band first, logging second, never asserts a zero."""
    b = (ev.get("band") or {}).get("this_period") or {}
    t, n = ev["training"], ev["nutrition"]
    L = [header, ""]
    if b.get("sleep_avg_h") is not None:
        L.append(f"{'✅' if b['sleep_avg_h'] >= E.TARGETS['sleep_h'] else '⚠️'} Sleep avg *{b['sleep_avg_h']:.1f}h* "
                 f"({b.get('nights_at_least_7h', 0)}/{b.get('nights_with_sleep_data', 0)} nights at 7h+).")
    if b.get("steps_avg") is not None:
        azm = b.get("active_zone_min_total")
        L.append(f"🚶 *{int(b['steps_avg']):,}* steps/day" + (f" · {int(azm)} zone min." if azm is not None else "."))
    if t["sessions_done"]:
        L.append(f"🏋️ {t['sessions_done']} lift session(s) logged.")
    if n["avg_kcal"] is not None:
        L.append(f"🍽 Avg *{int(n['avg_kcal'])} kcal* / {int(n['avg_protein_g'] or 0)}g protein on logged days.")
    L += ["", "_(plain summary — the coach model was unavailable)_"]
    return "\n".join(L)


def _checkin_user(ev: dict, docs: dict, days: int, extra: str = "") -> str:
    pri = E.recent_priorities(6)
    return (
        f"## Profile\n{docs['profile']}\n\n## Training Plan\n{docs['training_plan']}\n\n"
        f"## Coach Memory\n{docs['coach_memory']}\n\n"
        "## RECENT PRIORITIES (newest first — do not repeat these)\n"
        + ("\n".join(f"- {x}" for x in pri) if pri else "- none yet") + "\n\n"
        f"## Evidence packet (last {days} days)\n{json.dumps(ev, indent=2, default=str)}\n\n"
        f"{extra}Write the check-in JSON."
    )


def compose_checkin(ev: dict, days: int, monthly: bool = False, rng: str = "",
                    extra: str = "") -> tuple[str, dict | None]:
    """(message, parsed-or-None). Validated, with the templated fallback.
    Numbers are grounded against the evidence + targets + recent priorities only — NOT the
    ~23K characters of docs, where ~43% of the integers 10-200 already appear (review)."""
    period, horizon, header = _period_label(E.now().date(), monthly)
    if not NO_LLM:
        user = _checkin_user(ev, E.context_docs(), days, extra)
        grounding = (json.dumps(ev, default=str) + json.dumps(E.TARGETS)
                     + "\n".join(E.recent_priorities(6)))
        reason = ""
        for _attempt in range(2):   # one retry, told why the first answer was rejected
            ask = user if not reason else (user + f"\n\nYour previous answer was REJECTED: {reason}. "
                                                   "Write it again and fix that.")
            parsed = E.compose_json(CHECKIN_SYS.format(period=period, horizon=horizon), ask)
            ok, reason = E.validate_checkin(parsed, grounding, ev) if parsed else (False, "no response")
            if ok:
                return _render_checkin(parsed, header, rng), parsed
            print(f"check-in: structured compose rejected ({reason})", file=sys.stderr)
        print("check-in: falling back to the template", file=sys.stderr)
    return _templated_checkin(ev, header), None


def _range_label(days: int) -> str:
    end = E.now().date() - datetime.timedelta(days=1)
    start = end - datetime.timedelta(days=days - 1)
    return f"{start:%b %-d}–{end:%-d}" if start.month == end.month else f"{start:%b %-d}–{end:%b %-d}"


def run_weekly() -> int:
    ev = E.build_evidence(DAYS)
    msg, parsed = compose_checkin(ev, DAYS, rng=_range_label(DAYS))
    rc = _emit(msg, "weekly")
    if rc == 0 and not DRY and parsed:
        E.memory_append(f"Weekly focus: {parsed['focus']['title']} → {parsed['action']}")
    return rc


def run_monthly() -> int:
    """The month's check-in, sent right after the calendar card (health_monthly.sh).
    --month YYYY-MM (default: last month)."""
    ym = None
    if "--month" in sys.argv:
        try:
            ym = sys.argv[sys.argv.index("--month") + 1]
        except IndexError:
            ym = None
    today = E.now().date()
    if ym:
        y, m = (int(x) for x in ym.split("-"))
    else:
        last = today.replace(day=1) - datetime.timedelta(days=1)
        y, m = last.year, last.month
    import calendar as _cal
    end = min(datetime.date(y, m, _cal.monthrange(y, m)[1]), today)
    days = (end - datetime.date(y, m, 1)).days + 1
    ev = E.build_evidence(days, end=end)      # food / lifts / weight AND band, month-aligned
    msg, parsed = compose_checkin(ev, days, monthly=True, rng=datetime.date(y, m, 1).strftime("%B"))
    rc = _emit(msg, "monthly")
    if rc == 0 and parsed and not DRY:
        E.memory_append(f"Monthly focus ({y}-{m:02d}): {parsed['focus']['title']} → {parsed['action']}")
    return rc


# ----------------------------------------------------------------- midday under-eating rescue
MEAL_SYS = (
    "You are Sparsh's coach sending a MIDDAY UNDER-EATING RESCUE. His #1 failure mode is "
    "drifting to ~1 meal/day when busy, which kills his lean bulk. He's behind pace on intake "
    "RIGHT NOW. Send ONE short, blunt message (<60 words, plain markdown): state where he is vs "
    "where he should be by now (use the exact numbers), give ONE concrete easy fix he can get in "
    "the next hour (a specific protein-dense anchor — shake, sandwich, the office AYCE, eggs), and "
    "end with a quick either/or question. No lecture, no hype. Use ONLY the numbers given."
)


# Days-dark checkpoints at which the re-engagement nudge is allowed to speak.
# Deliberately sparse: nagging daily about not-tracking is what made the channel
# noise in the first place (4 false alarms/day for 24 days, 0 replies).
DARK_CHECKPOINTS = (2, 5, 10, 20)
DARK_TAIL_EVERY = 14   # past the last checkpoint, speak at most fortnightly


def _dark_speaks(dark: int) -> bool:
    """Checkpoint hit, or the fortnightly long tail. The tail matters: with
    checkpoints alone, a lapse that lands between two of them (24 days, say)
    would sit silent for weeks — which is how "quiet" becomes "abandoned"."""
    return dark in DARK_CHECKPOINTS or (
        dark > max(DARK_CHECKPOINTS) and dark % DARK_TAIL_EVERY == 0)


def run_tracking_dark() -> int:
    """Owns the 'he isn't logging' case that the intake nudges now decline.

    One message at each checkpoint instead of a daily calorie alarm built on an
    empty log — ~5 messages over five weeks rather than ~35."""
    dark = E.logging_dark_days()
    if not _dark_speaks(dark):
        return _silent()
    ok, _why = E.budget_ok("tracking-dark")
    if not ok and not DRY:
        return _silent()
    # logging_dark_days returns the offset of the last day that HAS a log, so the dark
    # stretch begins the day AFTER it. Without the +1 the message named the last day he
    # actually logged as the first day he didn't.
    since = (E.now().date() - datetime.timedelta(days=dark - 1)).strftime("%b %-d")
    if dark <= 5:
        msg = (f"📉 *Food tracking has been dark since {since}* ({dark} days). "
               f"I've muted the intake nudges because I genuinely don't know what you've "
               f"eaten — they'd be guessing. Log one meal and they come back on.")
    else:
        msg = (f"📉 *{dark} days without a food log* (since {since}). Nothing downstream "
               f"works without it — no fuel card, no muscle map, no weekly review. "
               f"Restart with one meal today; the pantry remembers your usuals so it's fast.")
    return _emit(msg, "tracking-dark")


def run_meal_rescue() -> int:
    # Nothing logged today => intake is UNKNOWN, not zero. Asserting "you're 1385 kcal
    # short" off an empty log is how this fired 18/18 days into a dead channel. Hand the
    # case to the re-engagement nudge; this one only speaks when it has real data.
    if not E.intake_is_tracked():
        # 2026-10-17 (his call): the "food tracking has been dark" checkpoint nudges are cut
        # with the rest of the nagging; the cards and the weekly check-in work without logs.
        return _silent()
    pace = E.intake_pace()
    if not (pace["behind_kcal"] or pace["behind_protein"]):
        return _silent()
    ok, why = E.budget_ok("meal-rescue")
    if not ok and not DRY:
        return _silent()
    msg = None
    if not NO_LLM:
        msg = E.compose_text(MEAL_SYS, f"Intake pace right now:\n{json.dumps(pace, indent=2)}", max_tokens=400)
    if not msg:
        gap = pace["kcal_expected_by_now"] - pace["kcal_so_far"]
        msg = (f"🍽️ *Behind pace.* {pace['kcal_so_far']} kcal / {pace['protein_so_far_g']}g protein by "
               f"{pace['time']} — ~{gap} kcal short of where you should be. Get a protein anchor in within "
               f"the hour (shake + sandwich, or hit the AYCE). Café or grocery?")
    return _emit(msg, "meal-rescue")


# ----------------------------------------------------------------- missed-workout rescue
WORKOUT_SYS = (
    "You are Sparsh's coach sending a MISSED-WORKOUT RESCUE. His #2 failure mode is training "
    "consistency collapsing under load. No workout has been LOGGED for a couple of days and the week is "
    "behind. That is a gap in the log, not proof he skipped (2026-10-08: workouts are often logged the "
    "next day), so say 'no workout logged since …' and ask, never 'you haven't lifted'. "
    "Do NOT guilt-trip. Offer the 25-minute fallback session (one compound push, one compound pull, "
    "one leg movement — keep it minimal-viable) so the week stays alive, and ask him to reply 'done' "
    "when finished. <50 words, blunt and supportive, plain markdown. Use ONLY the numbers given."
)


def run_workout_rescue() -> int:
    ev = E.build_evidence(DAYS)
    t = ev["training"]
    today = E.now().date().isoformat()
    # A workout file dated today means he lifted today.
    trained_today = (E.WORKOUTS / f"{today}.md").exists()
    # `or 0` used to collapse None -> 0 here, which silenced this nudge permanently
    # once he passed the evidence window without lifting. days_since_last_lift is now
    # unbounded (coach_engine.last_lift_days_ago); None means "never logged a workout",
    # which still warrants the nudge rather than suppressing it.
    raw_idle = t.get("days_since_last_lift", t.get("days_since_last_logged_lift"))
    idle = DAYS if raw_idle is None else raw_idle
    warranted = (not trained_today) and idle >= 2 and t["sessions_done"] < t["planned_per_week"]
    if not warranted:
        return _silent()
    ok, why = E.budget_ok("workout-rescue")
    if not ok and not DRY:
        return _silent()
    facts = {"days_since_last_lift": idle, "sessions_this_week": t["sessions_done"],
             "planned_per_week": t["planned_per_week"], "untrained": t.get("untrained", [])}
    msg = None
    if not NO_LLM:
        msg = E.compose_text(WORKOUT_SYS, f"Training state:\n{json.dumps(facts, indent=2)}", max_tokens=300)
    if not msg:
        msg = (f"🏋️ *No workout logged in {idle} days* — {t['sessions_done']}/{t['planned_per_week']} logged this week. "
               f"If you trained and didn't log it, reply with what you did. Otherwise, "
               f"keep the week alive with the 25-min fallback: squat variant + bench/press + a row. "
               f"Reply *done* when finished.")
    return _emit(msg, "workout-rescue")


# ----------------------------------------------------------------- pre-workout preview
def run_preview() -> int:
    ev = E.build_evidence(DAYS)
    prog = ev["training"].get("lift_progression") or []
    if not prog:
        return _silent()
    lines = []
    for p in prog[:6]:
        if p["status"] == "up":
            lines.append(f"• {p['lift']}: last {p['last_top_lb']:g} lb (↑ from {p['prev_top_lb']:g}) — push for more.")
        elif p["status"] in ("flat", "down"):
            lines.append(f"• {p['lift']}: last {p['last_top_lb']:g} lb ({p['status']}) — beat it today.")
        else:
            lines.append(f"• {p['lift']}: last {p['last_top_lb']:g} lb — add reps or +2.5-5 lb if warm-ups feel good.")
    msg = "🏋️ *Today — beat last time:*\n" + "\n".join(lines)
    return _emit(msg, "preview")


# ----------------------------------------------------------------- two-way chat (on demand)
CHAT_SYS = (
    "You are Sparsh's strength & nutrition coach answering his message DIRECTLY and "
    "conversationally over Telegram. Near-beginner lifter on a lean bulk; the two failure "
    "modes are training-consistency collapse and under-eating. COACH ADHERENCE, not the program. "
    "Blunt, numbers-first, plain English, no lecturing, no therapy voice. Answer HIS actual "
    "question using the evidence packet + context below; quote his real numbers. If he's slipping, "
    "say so plainly and give the next concrete action. If the data needed isn't present, say so — "
    "never invent numbers. Keep it tight for chat (a few lines)."
)


def run_chat() -> int:
    # message via --message-file (preferred — the skill writes his text to a temp
    # file with write_file, no shell quoting and no heredoc that would trip the
    # dangerous-command approval gate), then --message, then stdin.
    msg = ""
    if "--message-file" in sys.argv:
        try:
            msg = Path(sys.argv[sys.argv.index("--message-file") + 1]).read_text(encoding="utf-8").strip()
        except Exception:  # noqa: BLE001
            msg = ""
    if not msg and "--message" in sys.argv:
        try:
            msg = sys.argv[sys.argv.index("--message") + 1]
        except IndexError:
            msg = ""
    if not msg:
        try:
            msg = sys.stdin.read().strip()
        except Exception:  # noqa: BLE001
            msg = ""
    if not msg:
        print("Ask me something about your training, food, sleep, or weight.")
        return 0
    ev = E.build_evidence(DAYS)
    docs = E.context_docs()
    user = (
        f"## Profile\n{docs['profile']}\n\n## Training Plan\n{docs['training_plan']}\n\n"
        f"## Coach Memory\n{docs['coach_memory']}\n\n"
        f"## Evidence packet (last {DAYS} days)\n{json.dumps(ev, indent=2, default=str)}\n\n"
        f"## Sparsh's message\n{msg}\n\nRespond as the coach."
    )
    out = None if NO_LLM else E.compose_text(CHAT_SYS, user, max_tokens=1200)
    if not out:
        t, n = ev["training"], ev["nutrition"]
        out = (f"Quick read: {t['sessions_done']}/{t['planned_per_week']} sessions, "
               f"avg {int(n['avg_kcal'] or 0)} kcal / {int(n['avg_protein_g'] or 0)}g protein"
               f"{', '+str(n['undereating_days'])+' under-eating days' if n['undereating_days'] else ''}. "
               f"(Coach model unavailable right now — ask again in a bit.)")
    print(out)   # the /coach skill relays this verbatim to the user
    return 0


# ----------------------------------------------------------------- water check-in
def run_water_check() -> int:
    """Mid-afternoon hydration nudge — fires ONLY if behind the paced 2.5L/day target."""
    fm = E.daily_fm(E.now().date().isoformat())
    # Blank water_l means unlogged, not zero litres. Reporting "0.0L by 4:14 PM" off an
    # absent field is a fabricated measurement.
    if not E.has_value(fm, "water_l"):
        return _silent()
    water = E._f(fm, "water_l") or 0.0
    n = E.now()
    h = n.hour + n.minute / 60
    frac = max(0.0, min(1.0, (h - 7.0) / (23.0 - 7.0)))   # eating/waking window
    expected = E.TARGETS["water_l"] * frac
    if water >= expected - 0.6:                            # on/near pace → silent
        return _silent()
    ok, _why = E.budget_ok("water-check")
    if not ok and not DRY:
        return _silent()
    short = max(0.0, round(expected - water, 1))
    msg = (f"💧 *Water check:* {water:.1f}L by {n.strftime('%-I:%M %p')} — about {short:.1f}L behind "
           f"pace (target 2.5L/day). Fill a bottle now and sip through the afternoon.")
    return _emit(msg, "water-check")


# ----------------------------------------------------------------- dinner check-in
def run_dinner_check() -> int:
    """Evening under-eating guard — fires ONLY if no dinner logged AND day is light on kcal."""
    today = E.now().date().isoformat()
    # Same rule as meal-rescue: an empty log is unknown intake, not a light day.
    if not E.intake_is_tracked(today):
        return _silent()
    meals = E.meals_logged(today)
    kcal = int(E._f(E.daily_fm(today), "kcal") or 0)
    if "dinner" in meals or kcal >= 1800:                  # ate dinner or already well-fed → silent
        return _silent()
    ok, _why = E.budget_ok("dinner-check")
    if not ok and not DRY:
        return _silent()
    gap = E.TARGETS["kcal"] - kcal
    msg = (f"🍽️ *Dinner check:* only {kcal} kcal logged today and no dinner yet — that's the "
           f"under-eating failure mode. Get a real, protein-forward dinner in (~{gap} kcal to target). "
           f"What are you having?")
    return _emit(msg, "dinner-check")


# ----------------------------------------------------------------- internship accountability
INTERN_STATE = E.HOME / ".hermes" / "health" / "internship_state.json"


def _find_worklist():
    """Locate the Apply-Now Worklist, tolerating vault reorganisation.

    This was a hardcoded path to '06 - Internships/Apply Now Worklist - Jun 2026.md'.
    The 2026-06-26 vault reorg moved the file into '06 - Internships/Job Search/'
    and fixed the paths in curate.py and fit_pass.py but not this one. _read()
    returned "" -> _worklist_backlog() returned 0 -> run_internship_check()
    took its `if not count: return _silent()` branch on EVERY weekday for six
    weeks. The nudge never once fired and nothing surfaced, because "no backlog"
    and "cannot find the file" were the same code path.

    2026-08-18 — THAT FIX REINTRODUCED THE SAME BUG ONE LEVEL UP. It pinned the
    *June* filename first, and the June file still exists, so the glob fallback
    was never reached. Every weekday at 19:00 this read a worklist superseded on
    Aug 10 and named `Amazon — AWS Data Services SDE Intern` as the top priority:
    a Fall 2026 req, for a cycle the Shopify extension had already filled.

    Pinning ANY filename is the bug. Resolve to the NEWEST worklist by mtime, so
    writing a new one is all it takes to retarget the nudge."""
    base = E.VAULT / "06 - Internships"
    hits = sorted((p for p in base.glob("**/Apply Now Worklist*.md") if p.is_file()),
                  key=lambda p: p.stat().st_mtime, reverse=True) if base.is_dir() else []
    if hits:
        return hits[0]
    return base / "Job Search" / "Apply Now Worklist.md"  # canonical; for the error path


WORKLIST = _find_worklist()


def _intern_applied_today(board: dict | None = None) -> bool:
    """Did an application go out today? ASK THE BOARD FIRST — it is the record.

    This used to consult only INTERN_STATE, a sentinel written exclusively by
    run_internship_applied(), i.e. only when he replies "applied: X" to Hermes in
    Telegram. He applies with board.py from Claude Code, so the file has never existed
    on the VPS (verified 2026-09-25) and this returned False every single day — which is
    why the 7 PM nudge opened "No application logged today" on 2026-09-21, a day with
    EIGHT submissions on the board. The sentinel stays as a fallback so replying still
    silences the nudge immediately, before the Sheet write lands.
    """
    if board and board.get("applied_today"):
        return True
    try:
        return json.loads(INTERN_STATE.read_text()).get("applied_date") == E.now().date().isoformat()
    except Exception:  # noqa: BLE001
        return False


def _worklist_backlog() -> tuple[int, str | None]:
    """(count, top_role) parsed from the Apply-Now Worklist markdown tables. Robust to the
    other agent's edits — reads whatever rows are present; silent if the file/format is gone.

    2026-08-18 — column position is NOT stable across worklist rewrites. The old
    regex assumed `| <number> | Company | Role |`; the Aug worklist's ranked table
    is `| Fit | Cycle | Company | Role |`, so it would have reported the *cycle*
    as the company ("Summer 2027 — Roblox"). Locate the Company/Role columns by
    HEADER NAME per table instead, and fall back to the old shape only when a
    table has no usable header."""
    txt = E.read(WORKLIST, limit=40000)
    if not txt:
        return 0, None

    rows: list[tuple[str, str]] = []
    ci = ri = None
    for line in txt.splitlines():
        s = line.strip()
        if not s.startswith("|"):
            ci = ri = None                      # table ended
            continue
        cells = [c.strip().replace("*", "") for c in s.strip("|").split("|")]
        low = [c.lower() for c in cells]
        if "company" in low and "role" in low:  # header row -> learn the layout
            ci, ri = low.index("company"), low.index("role")
            continue
        if set("".join(low)) <= set("-: "):     # separator row
            continue
        if ci is not None and ri is not None and max(ci, ri) < len(cells):
            company, role = cells[ci], cells[ri]
        elif len(cells) >= 3 and re.fullmatch(r"\d+[a-z]?", low[0] or ""):
            company, role = cells[1], cells[2]  # legacy `| n | Company | Role |`
        else:
            continue
        # markdown links -> plain text: "[SWE Intern](https://…)" -> "SWE Intern"
        role = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", role).strip()
        company = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", company).strip()
        if not company or not role or company.lower() == "company":
            continue
        rows.append((company, role))

    if not rows:
        return 0, None
    return len(rows), f"{rows[0][0]} — {rows[0][1]}"


def _board_targets():
    """Live board facts, or None. See scripts/internship/board_facts.py."""
    try:
        sys.path.insert(0, str(Path.home() / ".hermes" / "scripts" / "internship"))
        from board_facts import board_facts
        return board_facts(top_n=5) or None
    except Exception as e:  # noqa: BLE001
        print(f"[coach] board_facts unavailable: {type(e).__name__}: {e}", file=sys.stderr)
        return None


def run_internship_check() -> int:
    """Weekday-evening accountability — fires ONLY if nothing applied today AND a backlog exists.

    🔴 READS THE BOARD, NOT THE MARKDOWN WORKLIST (changed 2026-09-04). It used to parse
    `Apply Now Worklist - *.md`, and on 2026-09-03 that made it recommend *"top of the
    list: ⭐ Cohere"* — a role applied to on 2026-06-23. The worklist file was last
    modified Aug 26; the board knew. Suggesting a company he already applied to is the
    fastest way to make him stop reading the nudge.

    The worklist stays as a fallback only for when the Sheet is unreachable.
    """
    if E.now().weekday() >= 5:                 # Sat/Sun off
        return _silent()

    board = _board_targets()
    if _intern_applied_today(board):
        return _silent()
    if board and board.get("top_targets"):
        # Prefer something newly opened — applying early is the entire thesis.
        pool = board.get("new_last_2_days") or board["top_targets"]
        pick = pool[0]
        count = board.get("open_roles_total", len(pool))
        fit = f", fit {pick['fit']}" if pick.get("fit") is not None else ""
        fresh = " *(opened in the last 2 days)*" if pool is board.get("new_last_2_days") else ""
        wk = board.get("applied_last_7_days", 0)
        pace = (f" You're at *{wk} this week*." if wk else
                " *Nothing applied in the last 7 days.*")
        ok, _why = E.budget_ok("internship-check")
        if not ok and not DRY:
            return _silent()
        msg = (f"📋 *No application logged today.* {count} open roles on the board.{pace}\n\n"
               f"Best one to knock out now: *{pick['company']} — {pick['role']}*{fit}{fresh}\n\n"
               f"Reply 'applied: {pick['company']}' and I'll mark it.")
        return _emit(msg, "internship-check")

    # ── fallback: the old worklist path, only when the board is unreachable ──
    if not WORKLIST.is_file():
        print(f"[coach] WARNING: board unreachable AND no worklist at {WORKLIST} — "
              f"internship accountability cannot run.", file=sys.stderr)
        return _silent()
    print("[coach] WARNING: board unreachable, falling back to the markdown worklist "
          "(may name already-applied roles)", file=sys.stderr)
    count, top = _worklist_backlog()
    if not count:
        return _silent()
    ok, _why = E.budget_ok("internship-check")
    if not ok and not DRY:
        return _silent()
    msg = (f"📋 *No internship application logged today.* ~{count} roles on your Apply-Now Worklist. "
           f"Knock out ONE before bed — top of the list: *{top}*. "
           f"Reply 'applied: <company>' and I'll mark it done.")
    return _emit(msg, "internship-check")


def run_internship_applied() -> int:
    """Mark that an application went out today (silences the evening accountability nudge).
    The agent calls this when Sparsh reports applying — see the MEMORY.md rule."""
    INTERN_STATE.parent.mkdir(parents=True, exist_ok=True)
    INTERN_STATE.write_text(json.dumps({"applied_date": E.now().date().isoformat()}))
    print("✓ Logged an internship application for today — evening accountability nudge silenced.")
    return 0


# ----------------------------------------------------------------- post-workout session review
SESSION_SYS = (
    "You are Sparsh's strength coach giving a SHORT post-workout readout of the session he "
    "JUST finished. Near-beginner on a lean bulk; COACH ADHERENCE + progressive overload. "
    "Blunt, numbers-first, plain English, no fluff, no fake hype. Using ONLY the session JSON: "
    "(1) one line naming what he did (split · N exercises · M sets); (2) call out lifts that beat "
    "/ matched / dropped vs last time from `lift_vs_last` (cite the lb numbers; status 'new' = "
    "first time logged); (3) ONE concrete push for next time (add reps, or +2.5–5 lb on a flat "
    "lift). If a major muscle group is missing, note it for next session. ≤5 short lines. Honest "
    "but end on a real positive. NEVER invent numbers — use only what's in the JSON."
)


def run_session_review() -> int:
    """Post-workout: a coaching readout of TODAY's session + the per-session muscle card.
    Invoked by log-workout on FINALIZE (workout clearly done). Run under the FITNESS venv —
    the muscle card render needs cairosvg."""
    today = E.now().date().isoformat()
    ev = E.session_evidence(today)
    if not ev:
        return _silent()   # no workout logged today — nothing to review
    msg = None
    if not NO_LLM:
        # GPT-5.5 is a reasoning model — it spends tokens on hidden reasoning BEFORE the
        # visible answer, so a low cap returns EMPTY. Give it real headroom (≥2500).
        msg = E.compose_text(SESSION_SYS, json.dumps(ev, indent=2, default=str), max_tokens=3000)
    if not msg:
        lines = [f"🏋️ *{ev['split']}* — {len(ev['exercises'])} exercises, {ev['total_sets']} sets."]
        for ex in ev["exercises"][:8]:
            w = ex.get("top_lb")
            lines.append(f"• {ex['name']}: {ex['sets']} sets" + (f" @ {w:g} lb" if w else ""))
        if ev["muscles_worked"]:
            lines.append("Hit: " + ", ".join(ev["muscles_worked"].keys()) + ".")
        msg = "\n".join(lines)
    rc = _emit(msg, "session-review")
    # send the per-session muscle card (heat mode) — needs cairosvg (fitness venv)
    if not DRY:
        try:
            sys.path.insert(0, str(Path(__file__).resolve().parent))
            import fitness_report as FR  # noqa: E402
            FR.send_session_card(today)
        except Exception as e:  # noqa: BLE001
            print(f"session card send failed: {e}", file=sys.stderr)
    return rc


# ----------------------------------------------------------------- coach-memory refresh
REFRESH_SYS = (
    "You maintain Sparsh's Coach Memory 'Adherence snapshot' from his REAL logged data. "
    "Given the evidence packet, output JSON: {\"snapshot\": \"<=20-word current-state line\", "
    "\"working\": [2-4 short bullets — patterns/behaviors that ARE driving adherence], "
    "\"leaking\": [2-4 short bullets — where adherence breaks]}. Ground EVERY bullet in the "
    "data and cite real numbers (sessions/week, avg kcal & protein, days logged, weight "
    "trend, and the band's sleep / steps / zone minutes). His #1 failure mode is under-eating + "
    "inconsistency; protein is the chronic miss. Be specific and blunt — no hype, no generic "
    "advice, NEVER invent numbers. A blank or 0 for food / lifts / weight means NOT LOGGED: "
    "say 'not logged', never 'didn't eat', 'didn't train', 'abandoned' or 'almost certainly "
    "under target'. If a field has no data, say so plainly rather than fabricating."
)


def run_refresh_memory() -> int:
    """Refresh the coach-owned 'Adherence snapshot (auto)' section of Coach Memory from the
    last 4 weeks of data + bump last_updated. Silent (file write only, no Telegram); meant to
    run weekly so the coaching memory never goes stale. Reuses the weekly evidence.
    ONE write point, behind ONE dry-run guard (2026-10-17: --dry-run used to write)."""
    ev = E.build_evidence(28)
    n, t, bw = ev["nutrition"], ev["training"], ev["bodyweight"]
    snap, working, leaking, how = None, [], [], "templated"
    if not NO_LLM:
        docs = E.context_docs()
        user = (f"## Profile\n{docs['profile']}\n\n## Coach Memory\n{docs['coach_memory']}\n\n"
                f"## Evidence packet (last {ev['window_days']} days)\n"
                f"{json.dumps(ev, indent=2, default=str)}\n\nOutput the adherence-snapshot JSON.")
        parsed = E.compose_json(REFRESH_SYS, user)
        if parsed and parsed.get("snapshot"):
            snap, working, leaking, how = (parsed.get("snapshot", ""), parsed.get("working", []),
                                           parsed.get("leaking", []), "model")
        else:
            print("refresh: structured compose failed; templated", file=sys.stderr)
    if snap is None:
        # deterministic fallback straight from the evidence numbers (blank = not logged)
        b = (ev.get("band") or {}).get("this_period") or {}
        snap = (f"{t['sessions_done']} lifts logged in {ev['window_days']}d · food logged "
                f"{n['days_logged']}/{ev['window_days']} days"
                + (f" · band sleep avg {b['sleep_avg_h']}h, {int(b['steps_avg']):,} steps/day"
                   if b.get("sleep_avg_h") and b.get("steps_avg") else ""))
        if b.get("nights_with_sleep_data"):
            working.append(f"Sleep {b.get('nights_at_least_7h', 0)}/{b['nights_with_sleep_data']} nights at 7h+ (band)")
        if t["sessions_done"]:
            working.append(f"{t['sessions_done']} sessions logged in the window")
        if n["avg_protein_g"] is not None and n["avg_protein_g"] < 140:
            leaking.append(f"Protein under target on logged days — averaging {n['avg_protein_g']}g vs 140g")
        if n["undereating_days"]:
            leaking.append(f"{n['undereating_days']} under-eating day(s) below {n['undereating_threshold_kcal']} kcal")
        if t.get("untrained"):
            leaking.append("Untrained muscle groups this window: " + ", ".join(t["untrained"]))
        if not t["sessions_done"] and not n["days_logged"]:
            leaking.append("Lifts and food not logged this window — unknown, not zero")
    if DRY:
        print(json.dumps({"snapshot": snap, "working": working, "leaking": leaking, "via": how}, indent=2))
        return 0
    ok = E.memory_refresh(snap, working, leaking, ev["window_days"])
    print(f"refreshed coach memory ({how})" if ok else "refresh write failed")
    return 0 if ok else 1


def main() -> int:
    return {
        "weekly": run_weekly,
        "monthly": run_monthly,
        "session-review": run_session_review,
        "refresh-memory": run_refresh_memory,
        "meal-rescue": run_meal_rescue,
        "tracking-dark": run_tracking_dark,
        "workout-rescue": run_workout_rescue,
        "preview": run_preview,
        "chat": run_chat,
        "water-check": run_water_check,
        "dinner-check": run_dinner_check,
        "internship-check": run_internship_check,
        "internship-applied": run_internship_applied,
    }.get(MODE, run_weekly)()


if __name__ == "__main__":
    sys.exit(main())
