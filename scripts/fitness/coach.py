#!/usr/bin/env python3
"""
coach.py — the Phase 4b AI coach (multi-mode entry point on coach_engine).

Modes (--mode):
  weekly         full review: structured analysis (JSON -> validated -> rendered),
                 sent to Telegram + a one-line priority written back to Coach Memory.
  meal-rescue    midday under-eating rescue — fires ONLY if behind pace + budget allows.
  workout-rescue missed-workout rescue — fires ONLY if idle 2+ days & behind this week.
  preview        pre-workout preview ("beat last time") — on demand.

All compute is done deterministically in coach_engine (no hallucinated numbers).
Self-sends via the Bot API, then prints {"wakeAgent": false} so Hermes skips the
agent. Rescues stay SILENT (just the gate) when not warranted — no over-nudging.

  coach.py [--mode weekly|meal-rescue|workout-rescue|preview] [--days N] [--dry-run] [--no-llm]
"""
from __future__ import annotations

import json
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


# ----------------------------------------------------------------- weekly review
WEEKLY_SYS = (
    "You are Sparsh's strength & nutrition coach. He's a high-execution operator and "
    "near-beginner lifter on a LEAN BULK (underweight, +15-25 lb muscle goal). The whole "
    "system exists to defeat two lifelong failure modes: (1) training consistency collapsing "
    "under work/school load, (2) under-eating in busy seasons (~1 meal/day). COACH ADHERENCE, "
    "NOT THE PROGRAM. Blunt, numbers-first, plain English, NO lecturing, no fake hype, no "
    "therapy voice. Praise only specific actions/real progress; never flatter missed targets. "
    "Use ONLY the numbers in the evidence packet — never invent metrics.\n\n"
    "Return a JSON object EXACTLY in this shape:\n"
    "{\n"
    '  "analysis": {"primary_bottleneck": str, "secondary_bottleneck": str,\n'
    '               "evidence": [str, ...],  // each cites a real number from the packet\n'
    '               "risk_level": "green"|"yellow"|"red", "confidence": "high"|"medium"|"low"},\n'
    '  "actions": {"today": [str] (1-2 max), "fallback_if_busy": str, "do_not_change_yet": [str]},\n'
    '  "conversation": {"question_for_user": str, "tone": "firm"|"normal"|"encouraging"}\n'
    "}\n"
    "The question must be behaviorally useful (earns a reply that changes next week)."
)


def _render_weekly(p: dict) -> str:
    a, ac, c = p.get("analysis", {}), p.get("actions", {}), p.get("conversation", {})
    risk = {"red": "🔴", "yellow": "🟡", "green": "🟢"}.get(a.get("risk_level", ""), "")
    L = [f"💪 *Weekly coach check-in* {risk}".strip(), ""]
    if a.get("primary_bottleneck"):
        L.append(f"*Bottleneck:* {a['primary_bottleneck']}")
    for e in (a.get("evidence") or [])[:4]:
        L.append(f"• {e}")
    today = ac.get("today") or []
    if today:
        L += ["", "*Do this week:*"] + [f"→ {t}" for t in today[:2]]
    if ac.get("fallback_if_busy"):
        L.append(f"_Busy-day fallback:_ {ac['fallback_if_busy']}")
    if c.get("question_for_user"):
        L += ["", c["question_for_user"]]
    return "\n".join(L)


def _templated_weekly(ev: dict) -> str:
    t, n, r, bw = ev["training"], ev["nutrition"], ev["recovery"], ev["bodyweight"]
    L = ["💪 *Weekly coach check-in*", ""]
    flag = "⚠️" if t["sessions_done"] < t["planned_per_week"] else "✅"
    L.append(f"{flag} Trained *{t['sessions_done']}/{t['planned_per_week']}*.")
    if n["avg_kcal"] is not None:
        flag = "⚠️" if n["avg_kcal"] < E.TARGETS["kcal"] - 200 else "✅"
        L.append(f"{flag} Avg *{int(n['avg_kcal'])} kcal* / {E.TARGETS['kcal']} · *{int(n['avg_protein_g'] or 0)}g* / {E.TARGETS['protein_g']} protein.")
    if n["undereating_days"]:
        L.append(f"🚨 *{n['undereating_days']} under-eating day(s)* — the failure mode. Fix dinner.")
    if r["avg_sleep_h"] is not None:
        L.append(f"{'⚠️' if r['avg_sleep_h'] < E.TARGETS['sleep_h'] else '✅'} Sleep avg *{r['avg_sleep_h']}h*.")
    if bw["trend_lb_over_window"] is not None:
        L.append(f"⚖️ Weight {'+' if bw['trend_lb_over_window'] >= 0 else ''}{bw['trend_lb_over_window']} lb / {ev['window_days']}d.")
    L += ["", "_(plain summary — rich compose unavailable)_"]
    return "\n".join(L)


def run_weekly() -> int:
    ev = E.build_evidence(DAYS)
    docs = E.context_docs()
    msg, priority = None, None
    if not NO_LLM:
        user = (
            f"## Profile\n{docs['profile']}\n\n## Training Plan\n{docs['training_plan']}\n\n"
            f"## Coach Memory\n{docs['coach_memory']}\n\n"
            f"## This week's evidence packet (last {DAYS} days)\n{json.dumps(ev, indent=2, default=str)}\n\n"
            "Write this week's coaching JSON."
        )
        parsed = E.compose_json(WEEKLY_SYS, user)
        # ground against the full corpus the model saw (evidence packet + context docs)
        ok, reason = E.validate(parsed, user) if parsed else (False, "no response")
        if ok:
            msg = _render_weekly(parsed)
            priority = (parsed.get("actions", {}).get("today") or [None])[0]
        else:
            print(f"weekly: structured compose rejected ({reason}); templated", file=sys.stderr)
    msg = msg or _templated_weekly(ev)
    rc = _emit(msg, "weekly")
    if rc == 0 and not DRY and priority:
        E.memory_append(f"Weekly priority set: {priority}")
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


def run_meal_rescue() -> int:
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
    "consistency collapsing under load. He hasn't lifted in a couple days and is behind this week. "
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
    idle = t.get("days_since_last_lift") or 0
    warranted = (not trained_today) and idle >= 2 and t["sessions_done"] < t["planned_per_week"]
    if not warranted:
        return _silent()
    ok, why = E.budget_ok("workout-rescue")
    if not ok and not DRY:
        return _silent()
    facts = {"days_since_last_lift": idle, "sessions_this_week": t["sessions_done"],
             "planned_per_week": t["planned_per_week"], "untrained": t["untrained"]}
    msg = None
    if not NO_LLM:
        msg = E.compose_text(WORKOUT_SYS, f"Training state:\n{json.dumps(facts, indent=2)}", max_tokens=300)
    if not msg:
        msg = (f"🏋️ *{idle} days since your last lift* — {t['sessions_done']}/{t['planned_per_week']} this week. "
               f"Keep the week alive with the 25-min fallback: squat variant + bench/press + a row. "
               f"Reply *done* when finished.")
    return _emit(msg, "workout-rescue")


# ----------------------------------------------------------------- pre-workout preview
def run_preview() -> int:
    ev = E.build_evidence(DAYS)
    prog = ev["training"]["lift_progression"]
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


def main() -> int:
    return {
        "weekly": run_weekly,
        "meal-rescue": run_meal_rescue,
        "workout-rescue": run_workout_rescue,
        "preview": run_preview,
    }.get(MODE, run_weekly)()


if __name__ == "__main__":
    sys.exit(main())
