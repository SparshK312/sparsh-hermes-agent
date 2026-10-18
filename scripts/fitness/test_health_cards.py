#!/usr/bin/env python3
"""
test_health_cards.py — invariants for the health cards and the coach's check-in
(2026-10-17). Wired into deploy.sh (Step 0f): a failure aborts the deploy.

Every section pins a defect that actually happened, or the rule that replaced it:
  HC1  a day with no data is a GAP, never a zero bar (the old trends card drew `s or 0`)
  HC2  baselines are personal, same-source, and "building" until 7 nights
  HC3  the card's sleep_state is the brief gate's rule (they must never disagree)
  HC4  "vs last week" needs 4+ days on both sides (Oct 11-17 read "+538%" zone minutes
       against a week the band only saw for 3 days)
  HC5  the morning card is complete with AND without hand logging
  HC6  week + month cards render as valid SVG; the calendar puts days on the right weekday
  HC7  the coach sees whole docs, its own recent priorities, and the band's evidence
  HC8  the check-in validator rejects ungrounded numbers, a list of actions, dropped apps
  HC9  no lift files is "not logged", not a consistency failure; meal rescue is silent
       on an untracked day (nagging cut)
  HC10 --dry-run never writes Coach Memory

Offline and stdlib-only: SVG is parsed, never rasterised, so it runs without cairosvg.
"""
from __future__ import annotations

import datetime
import json
import os
import shutil
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path

HERE = Path(__file__).resolve().parent
PASSES, FAILS, _TMP = 0, [], []


def check(label, got, want):
    global PASSES
    if got == want:
        PASSES += 1
    else:
        FAILS.append(label)
        print(f"  ✗ {label}\n      got:  {got!r}\n      want: {want!r}")


COLS = ["date", "sleep_total_h", "sleep_core_h", "sleep_deep_h", "sleep_rem_h", "sleep_awake_h",
        "sleep_start", "sleep_end", "steps", "resting_hr", "rhr_source", "hrv_ms", "hrv_rmssd_ms",
        "azm", "distance_air_km", "total_kcal_air", "exercise_air_count", "exercise_air_min",
        "spo2_avg", "respiratory_rate", "sleep_source", "wake_gap_min", "exercise_min"]
END = datetime.date(2026, 10, 17)


def _night_row(d: datetime.date, hours: float, bed_utc_h: int = 4):
    start = datetime.datetime(d.year, d.month, d.day, bed_utc_h, 0)
    end = start + datetime.timedelta(hours=hours + 0.3)
    return {"sleep_total_h": f"{hours:.2f}", "sleep_core_h": f"{hours * 0.7:.2f}",
            "sleep_deep_h": f"{hours * 0.12:.2f}", "sleep_rem_h": f"{hours * 0.18:.2f}",
            "sleep_awake_h": "0.30", "sleep_start": start.strftime("%Y-%m-%dT%H:%M:00Z"),
            "sleep_end": end.strftime("%Y-%m-%dT%H:%M:00Z"), "sleep_source": "air", "wake_gap_min": "40"}


def make_env(days_air=24, food=False, lifts=False, gap_dates=(), memory_text=None):
    """A temp vault with `days_air` days of Air data ending END, Apple RHR before that."""
    root = Path(tempfile.mkdtemp(prefix="hc-"))
    _TMP.append(root)
    v = root / "vault"
    for sub in ("07 - Health/Metrics", "04 - Daily Notes", "07 - Health/Workouts", "07 - Health/Food Log"):
        (v / sub).mkdir(parents=True)
    rows = []
    for i in range(60, -1, -1):
        d = END - datetime.timedelta(days=i)
        ds = d.isoformat()
        r = {"date": ds}
        if ds in gap_dates:
            rows.append(r)
            continue
        if i < days_air:
            r.update(_night_row(d, 7.0 + (i % 3) * 0.5))
            r.update({"steps": str(8000 + i * 37), "resting_hr": str(58 + (i % 2)), "rhr_source": "air",
                      "hrv_rmssd_ms": str(100 + (i % 5)), "azm": str(20 + i), "distance_air_km": "6.1",
                      "total_kcal_air": "2400", "exercise_air_count": "1", "exercise_air_min": "25",
                      "spo2_avg": "97.1", "respiratory_rate": "16.4"})
        else:
            r.update({"steps": "7000", "resting_hr": "66", "rhr_source": "apple", "hrv_ms": "45"})
        rows.append(r)
    with (v / "07 - Health/Metrics/metrics.csv").open("w") as fh:
        fh.write(",".join(COLS) + "\n")
        for r in rows:
            fh.write(",".join(str(r.get(c, "")) for c in COLS) + "\n")
    for i in range(0, 10):
        d = (END - datetime.timedelta(days=i)).isoformat()
        fm = "type: daily\n"
        if food and i in (1, 2, 3):
            fm += "kcal: 2310\nprotein_g: 128\n"
        (v / "04 - Daily Notes" / f"{d}.md").write_text(f"---\n{fm}---\n# {d}\n")
    if lifts:
        (v / "07 - Health/Workouts" / f"{(END - datetime.timedelta(days=2)).isoformat()}.md").write_text(
            "---\ntype: workout\nexercises:\n  - name: Bench Press\n    sets:\n      - weight_lb: 95\n---\n")
    for name in ("Profile.md", "Training Plan.md"):
        (v / "07 - Health" / name).write_text(f"# {name}\nBench 777 lb (a number only the docs hold)\n"
                                               + ("x" * 5000) + "\nEND-OF-" + name + "\n")
    mem = memory_text if memory_text is not None else (
        "# Coach Memory\n" + ("filler line\n" * 700) + "## Coach-logged (auto)\n"
        "- (2026-10-14) Weekly priority set: Walk into the gym today\n"
        "- (2026-10-11) Weekly priority set: Log your bodyweight\n\nEND-OF-MEMORY\n")
    (v / "07 - Health" / "Coach Memory.md").write_text(mem)
    os.environ["HERMES_VAULT"] = str(v)
    for m in ("health_cards", "nutrition_card", "coach_engine", "coach", "muscle_volume", "fitness_report",
              "muscle_heatmap", "exercise_playbook"):
        sys.modules.pop(m, None)
    sys.path.insert(0, str(HERE))
    import health_cards as HC
    HC.now = lambda: datetime.datetime(2026, 10, 18, 9, 0, tzinfo=HC.TZ)   # never the wall clock
    return HC, v


def svg_ok(svg: str) -> bool:
    try:
        ET.fromstring(svg)
        return True
    except ET.ParseError as e:
        print(f"      svg parse error: {e}")
        return False


def texts(svg: str) -> str:
    return " ".join(t.text or "" for t in ET.fromstring(svg).iter("{http://www.w3.org/2000/svg}text"))


# ------------------------------------------------------------------ HC1
def test_gap_is_not_zero():
    print("HC1. a day with no data is a gap: no bar, a dashed stub, and it is not counted")
    gap = (END - datetime.timedelta(days=2)).isoformat()
    HC, _ = make_env(gap_dates={gap})
    rows = HC.load_metrics()
    s = HC.period_summary(rows, END, 7)
    check("the gap day is not counted as a night or a step day", (s["sleep_nights"], s["steps_days"]), (6, 6))
    items = [{"value": 7.0, "label": "a"}, {"value": None, "label": "b"}]
    out = "".join(HC._columns(0, 200, 0, 100, items, 10, lambda v: "#fff", str))
    check("None renders as a dashed outline, not a filled bar",
          (out.count("stroke-dasharray='3 7'"), out.count("fill='#fff'")), (1, 1))


# ------------------------------------------------------------------ HC2
def test_baselines():
    print("HC2. baselines: personal, same-source, 'building' until 7 nights")
    HC, _ = make_env(days_air=5)
    rows = HC.load_metrics()
    b = HC.baseline(rows, "hrv_rmssd_ms", END.isoformat())
    check("5 Air nights -> HRV baseline still building", (b.get("building"), b.get("n")), (True, 4))
    src_air = lambda r: (r.get("rhr_source") or "apple") == "air"  # noqa: E731
    rb = HC.baseline(rows, "resting_hr", END.isoformat(), same=src_air)
    check("RHR baseline ignores the Apple month (no fake 'below normal')", rb.get("building"), True)
    lab, _c = HC.versus(59, rb, higher_is_better=False)
    check("while building, the card says so instead of judging", lab.startswith("baseline building"), True)
    HC, _ = make_env(days_air=24)
    rows = HC.load_metrics()
    b = HC.baseline(rows, "hrv_rmssd_ms", END.isoformat())
    check("24 Air nights -> a real band", (b["building"], b["lo"] < b["mean"] < b["hi"]), (False, True))
    lab, col = HC.versus(b["lo"] - 20, b, higher_is_better=True)
    check("one low night is labelled amber, never red", (lab, col), ("below your normal", HC.AMBER))
    check("one night in the week -> no verdict on the week's recovery",
          HC._band_state(70, b, True, 1).startswith("too few nights"), True)
    flat = {d: dict(r, hrv_rmssd_ms="101") if r.get("hrv_rmssd_ms") else r for d, r in rows.items()}
    fb = HC.recovery_series(flat, END)["hrv_base"]
    check("a flat HRV history still gets a band at least ±5% wide (weekly matches morning)",
          round(fb["hi"] - fb["lo"], 1) >= round(0.1 * fb["mean"], 1), True)
    r7 = {d: dict(r) for d, r in rows.items()}
    r7[(END - datetime.timedelta(days=7)).isoformat()]["sleep_total_h"] = "4.00"   # make the 7th night count
    want = sum(float(r7[d]["sleep_total_h"]) for d in HC._days(END - datetime.timedelta(days=1), 7)) / 7
    check("the morning '7-night avg' averages the 7 nights before last night (was 6)",
          round(HC.morning_facts(r7, END)["sleep_avg7"], 3), round(want, 3))
    f = HC.morning_facts(rows, END)
    check("HRV on the card is the Air RMSSD series (Apple SDNN never plotted)",
          all(x is None or x >= 100 for x in f["hrv"]["spark"]), True)


# ------------------------------------------------------------------ HC3
def test_sleep_state_matches_gate():
    print("HC3. the card's sleep_state is the brief gate's rule")
    HC, _ = make_env()
    sys.path.insert(0, str(HERE.parent / "hae"))
    sys.modules.pop("health_morning_brief_gate", None)
    import health_morning_brief_gate as G
    cases = [{}, {"sleep_total_h": "4.6", "sleep_source": "air"},
             {"sleep_total_h": "8.1", "sleep_source": "air", "wake_gap_min": "129"},
             {"sleep_total_h": "8.1", "sleep_source": "air", "wake_gap_min": "151"},
             {"sleep_total_h": "8.1", "sleep_source": "air", "wake_gap_min": "150"},
             {"sleep_total_h": "6.5", "sleep_source": "apple"}]
    check("identical on every case", [HC.sleep_state(c) for c in cases], [G.sleep_state(c) for c in cases])
    check("constants agree", HC.WAKE_GAP_MAX_MIN, G.WAKE_GAP_MAX_MIN)


# ------------------------------------------------------------------ HC4
def test_compare_needs_coverage():
    print("HC4. 'vs last week' only when both weeks have 4+ days of the metric")
    HC, _ = make_env()
    t, _c = HC._cmp(676, 106, (False, "not enough data"), "vs last week", pct=True)
    check("not comparable -> no percentage (the +538% case)", ("%" in (t or ""), "not enough data" in (t or "")), (False, True))
    t, _c = HC._cmp(9941, 9467, (True, ""), "vs last week", pct=True)
    check("comparable -> a real delta", t, "+5% vs last week")
    t, _c = HC._cmp(7.9, 7.88, (True, ""), "vs last week", unit="h")
    check("a tiny change reads 'about the same'", t, "about the same as last week")
    rows = HC.load_metrics()
    cur = HC.period_summary(rows, END, 7)
    a7 = {d: dict(r, sleep_source="apple", azm=None) for d, r in rows.items()}
    a7 = {d: {k: v for k, v in r.items() if v is not None} for d, r in a7.items()}
    prev_apple = HC.period_summary(a7, END - datetime.timedelta(days=7), 7)
    ok, why = HC.comparable(cur, prev_apple, "sleep")
    check("an Apple-Watch week vs a Fitbit week is NOT comparable (device change != progress)",
          (ok, why), (False, "different device"))
    check("Fitbit vs Fitbit with full weeks is comparable", HC.comparable(cur, HC.period_summary(rows, END - datetime.timedelta(days=7), 7), "sleep")[0], True)
    HC, _ = make_env(days_air=9)          # band 9 days: this week full, last week 2 nights
    ev = HC.wearable_evidence(HC.load_metrics(), END, 7)
    p = ev["previous_period"]
    check("the coach's packet withholds last week's sleep when it had < 4 nights (no '5.6 -> 8.1 h jump')",
          (p["sleep_avg_h"], p["avg_bedtime"], "sleep" in (p.get("not_comparable") or "")), (None, None, True))
    check("this week's own numbers are kept", ev["this_period"]["sleep_avg_h"] is not None, True)


# ------------------------------------------------------------------ HC5
def test_morning_card_both_cases():
    print("HC5. morning card: complete without logging; food & body panel only when logged")
    HC, _ = make_env(food=False)
    f = HC.morning_facts(HC.load_metrics(), END)
    svg = HC.build_morning_svg(f)
    t = texts(svg) if svg_ok(svg) else ""
    check("valid SVG, sleep + recovery + yesterday, no food panel",
          (svg_ok(svg), "Sleep" in t, "Recovery" in t, "Yesterday" in t, "Food & body" in t), (True, True, True, True, False))
    check("no emoji in the SVG (cairosvg has no emoji font)", any(ord(ch) > 0x1F000 for ch in svg), False)
    HC, _ = make_env(food=True)
    f = HC.morning_facts(HC.load_metrics(), END)
    t = texts(HC.build_morning_svg(f))
    check("logged food -> the panel shows yesterday's calories", ("Food & body" in t, "2,310" in t), (True, True))
    f2 = HC.morning_facts(HC.load_metrics(), END, state="partial")
    t2 = texts(HC.build_morning_svg(f2))
    check("a partial night says 'still syncing' and makes no target judgement",
          ("still syncing" in t2, "short" in t2), (True, False))
    check("durations read as h/m, so 6.98 h is never shown as '7.0h'", HC.fmt_dur(6.98), "6h 59m")


# ------------------------------------------------------------------ HC6
def test_week_and_month_cards():
    print("HC6. week + month cards render; calendar weekdays are right; empty food month is explained")
    HC, _ = make_env(days_air=10)
    rows = HC.load_metrics()
    s1, s2 = HC.build_week_svgs(HC.week_facts(rows, END))
    check("both weekly cards are valid SVG", (svg_ok(s1), svg_ok(s2)), (True, True))
    check("a sparse previous week is not compared", "not enough data last week" in texts(s1), True)
    mf = HC.month_facts(rows, 2026, 10, today=END)
    svg = HC.build_month_svg(mf)
    check("month card valid", svg_ok(svg), True)
    t = texts(svg)
    check("no food logged -> the calendar says so instead of an empty grid", "Not logged this month." in t, True)
    out = HC._calendar(0, 0, 10, 0, 2026, 10, lambda d: ("#fff", False), "x", [])
    first = next(x for x in out if "<rect" in x)
    check("Oct 1 2026 (a Thursday) sits in the 4th column", "x='30'" in first, True)
    check("future days are counted as future, not as missing data",
          sum(1 for d in mf["days"] if d.get("future")), 14)
    out_dir = tempfile.mkdtemp(); _TMP.append(Path(out_dir))
    import contextlib, io
    with contextlib.redirect_stdout(io.StringIO()):
        rc = HC.main(["month", "--month", "2026-12", "--svg", "--out", out_dir])
    check("a month that has not started -> a clean refusal, not a crash", rc, 2)
    rows2 = dict(rows)
    rows2[END.isoformat() if False else (END - datetime.timedelta(days=1)).isoformat()] = dict(
        rows[(END - datetime.timedelta(days=1)).isoformat()], exercise_air_min=None)
    rows2 = {d: {k: v for k, v in r.items() if v is not None} for d, r in rows2.items()}
    check("a band session count with no minutes still renders", svg_ok(HC.build_morning_svg(HC.morning_facts(rows2, END))), True)


# ------------------------------------------------------------------ HC7
def test_coach_context_and_evidence():
    print("HC7. coach: whole docs, its own recent priorities, the band's evidence")
    HC, v = make_env()
    import coach_engine as E
    docs = E.context_docs()
    check("Coach Memory is read whole (was cut at 4,000 chars)", docs["coach_memory"].rstrip().endswith("END-OF-MEMORY"), True)
    check("Profile is read whole", docs["profile"].rstrip().endswith("END-OF-Profile.md"), True)
    check("recent priorities parsed, newest first",
          [p.split(")")[0] for p in E.recent_priorities()], ["(2026-10-14", "(2026-10-11"])
    E.now = lambda: datetime.datetime(2026, 10, 18, 18, 5, tzinfo=E.TZ)
    ev = E.build_evidence(7)
    band = ev.get("band") or {}
    check("the packet carries the band's this/previous period + recovery trend",
          sorted(k for k in band if k != "notes"), ["previous_period", "recovery_trend", "this_period"])
    check("band steps are there (the coach never saw them before)",
          (band["this_period"]["steps_avg"] or 0) > 0, True)
    check("sessions the band detected are labelled as NOT lifting", "NOT lifting" in json.dumps(band["notes"]), True)


# ------------------------------------------------------------------ HC8
def test_checkin_validator():
    print("HC8. check-in validator: grounded numbers, one action, no dropped apps")
    make_env()
    import coach_engine as E
    ev = {"band": {"this_period": {"steps_avg": 9941, "sleep_avg_h": 7.94}}}
    good = {"headline": "Steps up, sleep steady", "win": {"title": "Steps", "evidence": "9941 a day"},
            "focus": {"title": "Bedtime", "evidence": "2 nights past 1 AM"}, "action": "In bed by 12:30 on weeknights"}
    check("grounded check-in passes", E.validate_checkin(good, ev)[0], True)
    bad = dict(good, win={"title": "Steps", "evidence": "12,500 a day"})
    check("an invented number is rejected", E.validate_checkin(bad, ev)[0], False)
    check("a list of actions is rejected", E.validate_checkin(dict(good, action=["a", "b"]), ev)[0], False)
    check("an app he dropped is rejected",
          E.validate_checkin(dict(good, action="Log it in MacroFactor tonight"), ev)[0], False)
    check("logging as the focus is rejected (his call: works with or without logs)",
          E.validate_checkin(dict(good, focus={"title": "Food intake is invisible — log it", "evidence": "0 days"}), ev)[0], False)
    check("'on track' in a win is not mistaken for logging",
          E.validate_checkin(dict(good, win={"title": "Sleep on track", "evidence": "9941"}), ev)[0], True)
    ev0 = dict(ev, logging={"food_days": 0}, training={"sessions_done": 0})
    nut = dict(good, focus={"title": "Nutrition is completely unmanaged", "evidence": "no data"})
    check("no food logged -> a food focus is rejected however it is worded", E.validate_checkin(nut, ev, ev0)[0], False)
    gym = dict(good, focus={"title": "One gym session this week", "evidence": "x"})
    check("no lifts logged -> a training focus is rejected", E.validate_checkin(gym, ev, ev0)[0], False)
    ev1 = dict(ev, logging={"food_days": 3}, training={"sessions_done": 1})
    check("with food + lifts logged, those focuses are allowed",
          (E.validate_checkin(nut, ev, ev1)[0], E.validate_checkin(gym, ev, ev1)[0]), (True, True))
    check("a band-data focus passes with nothing logged", E.validate_checkin(good, ev, ev0)[0], True)
    check("missing focus is rejected", E.validate_checkin({k: v for k, v in good.items() if k != "focus"}, ev)[0], False)


# ------------------------------------------------------------------ HC9
def test_grounding_excludes_docs():
    print("HC8b. compose_checkin grounds numbers in the evidence, not the 23K chars of docs")
    HC, v = make_env()
    import coach_engine as E
    E.now = lambda: datetime.datetime(2026, 10, 18, 18, 5, tzinfo=E.TZ)
    old_argv = sys.argv
    sys.argv = ["coach.py"]
    try:
        sys.modules.pop("coach", None)
        import coach as C
    finally:
        sys.argv = old_argv
    calls = []

    def fake(system, user, max_tokens=2000):
        calls.append(user)
        return {"headline": "x", "win": {"title": "Bench strong", "evidence": "777 lb top set"},
                "focus": {"title": "Bedtime", "evidence": "late nights"}, "action": "bed by 12:30"}
    C.E.compose_json = fake
    msg, parsed = C.compose_checkin(C.E.build_evidence(7), 7)
    check("a number only the docs contain is rejected; one retry, told why; then the template",
          (parsed, len(calls), "REJECTED" in calls[-1], "plain summary" in msg), (None, 2, True, True))


def test_no_logs_is_not_failure():
    print("HC9. no lift files = not logged (not consistency_risk); untracked meal rescue is silent")
    HC, v = make_env(lifts=False)
    import coach_engine as E
    E.now = lambda: datetime.datetime(2026, 10, 18, 18, 5, tzinfo=E.TZ)
    flags = E.build_evidence(7)["flags"]
    check("no workout files -> lifts_not_logged, not consistency_risk",
          ("lifts_not_logged" in flags, "consistency_risk" in flags), (True, False))
    HC, v = make_env(lifts=True)
    import coach_engine as E2
    E2.now = lambda: datetime.datetime(2026, 10, 18, 18, 5, tzinfo=E2.TZ)
    flags = E2.build_evidence(7)["flags"]
    check("1 logged lift of 4 -> consistency_risk still fires", ("consistency_risk" in flags, "lifts_not_logged" in flags), (True, False))
    # last food log exactly 2 days ago = a "tracking dark" checkpoint, where the OLD code spoke
    from zoneinfo import ZoneInfo
    two = (datetime.datetime.now(ZoneInfo("America/Toronto")).date() - datetime.timedelta(days=2)).isoformat()
    (v / "04 - Daily Notes" / f"{two}.md").write_text("---\ntype: daily\nkcal: 1900\n---\n")
    out = subprocess.run([sys.executable, str(HERE / "coach.py"), "--mode", "meal-rescue", "--dry-run", "--no-llm"],
                         capture_output=True, text=True, env={**os.environ, "HERMES_VAULT": str(v)}).stdout
    check("nothing logged today -> meal rescue stays silent (no 'tracking dark' nag)",
          ("[silent" in out, "dark" in out.lower()), (True, False))


def test_dry_run_never_writes():
    print("HC10. coach --dry-run never writes Coach Memory (refresh-memory did, 2026-10-17)")
    HC, v = make_env()
    mem = v / "07 - Health" / "Coach Memory.md"
    before = mem.read_text()
    outs = []
    for mode in ("refresh-memory", "weekly"):
        r = subprocess.run([sys.executable, str(HERE / "coach.py"), "--mode", mode, "--dry-run", "--no-llm"],
                           capture_output=True, text=True, env={**os.environ, "HERMES_VAULT": str(v)})
        outs.append((r.returncode, r.stdout))
    check("both dry runs exit 0 and print what they WOULD write",
          [(rc, ('"snapshot"' in out) if i == 0 else ("check-in" in out)) for i, (rc, out) in enumerate(outs)],
          [(0, True), (0, True)])
    check("Coach Memory unchanged after dry runs", mem.read_text() == before, True)


def main() -> int:
    tests = [test_gap_is_not_zero, test_baselines, test_sleep_state_matches_gate, test_compare_needs_coverage,
             test_morning_card_both_cases, test_week_and_month_cards, test_coach_context_and_evidence,
             test_checkin_validator, test_grounding_excludes_docs, test_no_logs_is_not_failure,
             test_dry_run_never_writes]
    for t in tests:
        try:
            t()
        except Exception as e:  # noqa: BLE001
            import traceback
            traceback.print_exc()
            FAILS.append(f"{t.__name__} crashed: {type(e).__name__}: {e}")
    for d in _TMP:
        shutil.rmtree(d, ignore_errors=True)
    print(f"\n{PASSES} passed, {len(FAILS)} failed")
    for f in FAILS:
        print(f"  FAIL: {f}")
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
