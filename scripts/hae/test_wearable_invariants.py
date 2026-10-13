#!/usr/bin/env python3
"""
test_wearable_invariants.py — the Fitbit Air / Apple merge guarantees. Deploy gate (Step 0e).

Every case is a defect the 2026-10-08 plan review found or a rule he set. Synthetic
fixtures only (the repo is public). Runs under the Mac /usr/bin/python3 (3.9) and newer.
    python3 scripts/hae/test_wearable_invariants.py
"""
from __future__ import annotations

import contextlib
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import urllib.error
from pathlib import Path

HERE = Path(__file__).resolve().parent
FAILS = []
PASSES = 0


def check(label, got, want):
    global PASSES
    if got == want:
        PASSES += 1
    else:
        FAILS.append(label)
        print(f"  ✗ {label}\n      got:  {got!r}\n      want: {want!r}")


_TMP = []


def fresh_env():
    """A temp vault + health dir, and the modules re-imported against them."""
    root = Path(tempfile.mkdtemp(prefix="wearable-"))
    _TMP.append(root)
    (root / "vault" / "07 - Health" / "Metrics").mkdir(parents=True)
    (root / "vault" / "04 - Daily Notes").mkdir(parents=True)
    (root / "hae").mkdir()
    os.environ["HERMES_VAULT"] = str(root / "vault")
    os.environ["HAE_HEALTH_DIR"] = str(root / "hae")
    for m in ("wearable_merge", "hae_process", "fitbit_sync", "hae_daily_ingest", "google_health"):
        sys.modules.pop(m, None)
    sys.path.insert(0, str(HERE))
    import wearable_merge as WM
    return root, WM


# ── 1. the merge rules ────────────────────────────────────────────────────────
def test_merge_rules():
    print("M1. merge: steps max + both kept; sleep block from ONE source; RHR Air; hrv_ms never from the Air")
    _, WM = fresh_env()
    apple = {"sleep_total_h": "6.0", "sleep_core_h": "3.0", "sleep_deep_h": "1.0", "sleep_rem_h": "2.0",
             "sleep_in_bed_h": "6.5", "sleep_stages_valid": "true", "steps": "8800", "resting_hr": "61",
             "hrv_ms": "72", "active_kcal": "330", "exercise_min": "28", "respiratory_rate": "14.0"}
    air = {"steps": "6000", "sleep_total_h": "7.2", "sleep_light_h": "4.0", "sleep_deep_h": "1.2",
           "sleep_rem_h": "2.0", "sleep_awake_h": "0.4", "sleep_stages_valid": "true",
           "resting_hr": "58", "hrv_rmssd_ms": "79.6", "azm": "22", "total_kcal": "2100", "resp_rate": "15.2"}
    r = WM.merge_day(apple, air)
    check("steps = the larger device total", (r["steps"], r["steps_source"], r["steps_apple"], r["steps_air"]),
          ("8800", "apple", "8800", "6000"))
    check("sleep block entirely from the Air (no Apple in_bed left over)",
          (r["sleep_total_h"], r["sleep_core_h"], r.get("sleep_in_bed_h"), r["sleep_source"]),
          ("7.2", "4.0", None, "air"))
    check("resting HR from the Air", (r["resting_hr"], r["rhr_source"]), ("58", "air"))
    check("hrv_ms stays Apple SDNN; Air RMSSD in its own column", (r["hrv_ms"], r["hrv_rmssd_ms"]), ("72", "79.6"))
    check("Apple-meaning columns never filled from the Air", (r["active_kcal"], r["exercise_min"], r.get("total_kcal_air")),
          ("330", "28", "2100"))
    r2 = WM.merge_day(apple, {"steps": "9500"})
    check("no Air night -> Apple sleep kept, labelled", (r2["sleep_total_h"], r2["sleep_source"], r2["steps"], r2["steps_source"]),
          ("6.0", "apple", "9500", "air"))
    r3 = WM.merge_day({}, {"sleep_total_h": "7.0", "sleep_stages_valid": "false"})
    check("Air total without stages -> no stage columns invented", ("sleep_core_h" in r3, r3["sleep_stages_valid"]), (False, "false"))
    a, f = {"2026-10-01": apple}, {"2026-10-01": air, "2026-10-02": {"steps": "100"}}
    check("build is pure and covers the union of dates", (WM.build(a, f) == WM.build(dict(a), dict(f)), sorted(WM.build(a, f))),
          (True, ["2026-10-01", "2026-10-02"]))


# ── 2. apple.csv can never absorb Air values ─────────────────────────────────
def test_reseed_strips_air():
    print("M2. hae_process: re-seeding apple.csv from a MERGED metrics.csv strips every Air value")
    _, WM = fresh_env()
    import hae_process as P
    merged = WM.merge_day({"steps": "5000", "resting_hr": "62", "hrv_ms": "70"},
                          {"steps": "9000", "sleep_total_h": "7.0", "resting_hr": "58", "resp_rate": "15"})
    back = P._apple_only(merged)
    check("Air steps -> back to the Apple count", back.get("steps"), "5000")
    check("Air sleep / RHR / breathing rate removed", ("sleep_total_h" in back, "resting_hr" in back, "respiratory_rate" in back),
          (False, False, False))
    check("Air-only columns dropped", any(k in back for k in WM.AIR_COLUMNS), False)
    check("Apple HRV untouched", back.get("hrv_ms"), "70")


# ── 3. end to end in the VPS FLAT layout ─────────────────────────────────────
def test_flat_layout_end_to_end():
    print("M3. flat ~/.hermes/scripts layout: hae_process -> apple.csv -> metrics.csv; order-independent with fitbit.csv")
    root, _ = fresh_env()
    flat = root / "scripts"
    flat.mkdir()
    for n in ("hae_process.py", "wearable_merge.py", "fitbit_sync.py", "google_health.py", "hae_daily_ingest.py"):
        shutil.copy(HERE / n, flat / n)
    raw = root / "p.json"
    raw.write_text(json.dumps({"data": {"metrics": [
        {"name": "step_count", "data": [{"date": "2026-10-07 10:00:00 -0400", "qty": 9000}]},
        {"name": "heart_rate_variability", "data": [{"date": "2026-10-07 10:00:00 -0400", "qty": 70}]}]}}))
    metrics = root / "vault" / "07 - Health" / "Metrics"
    (metrics / "fitbit.csv").write_text("date,steps,hrv_rmssd_ms\n2026-10-07,6000,80\n")
    env = dict(os.environ)
    r = subprocess.run([sys.executable, str(flat / "hae_process.py"), str(raw)], capture_output=True, text=True, env=env)
    check("hae_process runs from the flat layout", r.returncode, 0)
    if r.returncode:
        print(r.stderr[-600:])
        return
    rows = {}
    import csv
    with (metrics / "metrics.csv").open() as fh:
        rows = {x["date"]: x for x in csv.DictReader(fh)}
    m = rows.get("2026-10-07", {})
    check("metrics.csv = merge of apple.csv + fitbit.csv", (m.get("steps"), m.get("steps_air"), m.get("hrv_ms"), m.get("hrv_rmssd_ms")),
          ("9000", "6000", "70", "80"))
    with (metrics / "apple.csv").open() as fh:
        a = {x["date"]: x for x in csv.DictReader(fh)}
    check("apple.csv holds Apple values only", ("steps_air" in a["2026-10-07"], a["2026-10-07"]["steps"]), (False, "9000"))
    r2 = subprocess.run([sys.executable, str(flat / "wearable_merge.py")], capture_output=True, text=True, env=env)
    with (metrics / "metrics.csv").open() as fh:
        again = {x["date"]: x for x in csv.DictReader(fh)}
    check("re-deriving gives the identical file (order-independent, idempotent)", (r2.returncode, again == rows), (0, True))


# ── 4. fitbit_sync: Air-only, per-type failure, blank-not-zero, worn gating ─
def test_fitbit_apply_and_parse():
    print("F1. fitbit_sync: non-Air points dropped; failed type keeps cells; empty success blanks; worn gating")
    fresh_env()
    import fitbit_sync as F
    hk = {"dataSource": {"platform": "HEALTH_KIT"}, "dailyRestingHeartRate": {"date": {"year": 2026, "month": 10, "day": 7}, "beatsPerMinute": "60"}}
    fb = {"dataSource": {"platform": "FITBIT"}, "dailyRestingHeartRate": {"date": {"year": 2026, "month": 10, "day": 8}, "beatsPerMinute": "57"}}
    check("Apple-Health points never counted as the Air", F.parse_daily("daily-resting-heart-rate", [hk, fb]),
          {"2026-10-08": {"resting_hr": "57"}})
    existing = {"2026-10-07": {"date": "2026-10-07", "steps": "5000", "resting_hr": "59", "total_kcal": "1800"},
                "2026-10-08": {"date": "2026-10-08", "steps": "4000", "resting_hr": "58"}}
    results = {"steps": {"2026-10-08": {"steps": "6100"}},                 # 10-07 now empty -> blanked
               "total-calories": {"2026-10-07": {"total_kcal": "1500"}, "2026-10-08": {"total_kcal": "1700"}}}
    rows = F.apply(existing, results, ["2026-10-07", "2026-10-08"])        # RHR group FAILED (absent)
    check("a FAILED type keeps its previous cells", (rows["2026-10-07"].get("resting_hr"), rows["2026-10-08"].get("resting_hr")), ("59", "58"))
    check("a successful EMPTY response blanks (not 0)", rows["2026-10-07"].get("steps"), None)
    check("calories only on days the Air was worn", (rows["2026-10-07"].get("total_kcal"), rows["2026-10-08"].get("total_kcal")), (None, "1700"))
    check("numbers are stored compactly", (F._fmt(29.0, 0), F._fmt(4.356, 2), F._fmt(9300.0)), ("29", "4.36", "9300"))
    tmp = {"dataSource": {"platform": "FITBIT"}, "dailySleepTemperatureDerivations": {
        "date": {"year": 2026, "month": 10, "day": 9}, "nightlyTemperatureCelsius": "NaN", "baselineTemperatureCelsius": 33.1}}
    check("NaN from the API is blank, never the text 'nan'", F.parse_daily("daily-sleep-temperature-derivations", [tmp]), {})


def test_sleep_parse():
    print("F2. sleep: Air only, processed non-nap, split night summed, wake-day by offset, minutes not clock maths")
    fresh_env()
    import fitbit_sync as F
    def sess(platform, start, end, asleep, awake, light, deep, rem, processed=True, nap=False):
        return {"dataSource": {"platform": platform}, "sleep": {
            "interval": {"startTime": start, "startUtcOffset": "-14400s", "endTime": end, "endUtcOffset": "-14400s"},
            "metadata": {"processed": processed, "nap": nap, "mainSleep": not nap},
            "summary": {"minutesAsleep": str(asleep), "minutesAwake": str(awake), "minutesInSleepPeriod": str(asleep + awake),
                        "stagesSummary": [{"type": "LIGHT", "minutes": str(light)}, {"type": "DEEP", "minutes": str(deep)},
                                          {"type": "REM", "minutes": str(rem)}, {"type": "AWAKE", "minutes": str(awake)}]}}}
    pts = [sess("FITBIT", "2026-10-09T03:30:00Z", "2026-10-09T08:00:00Z", 240, 30, 150, 40, 50),   # 23:30 -> 04:00 local
           sess("FITBIT", "2026-10-09T08:30:00Z", "2026-10-09T11:00:00Z", 140, 10, 90, 20, 30),    # back to sleep 04:30 -> 07:00
           sess("FITBIT", "2026-10-09T18:00:00Z", "2026-10-09T18:40:00Z", 35, 5, 35, 0, 0, nap=True),
           sess("FITBIT", "2026-10-08T03:00:00Z", "2026-10-08T10:00:00Z", 400, 20, 250, 70, 80, processed=False),
           sess("HEALTH_KIT", "2026-10-09T03:00:00Z", "2026-10-09T11:00:00Z", 450, 30, 300, 70, 80)]
    out = F.parse_sleep(pts)
    check("only the Air's processed, non-nap night counts; split night summed", sorted(out), ["2026-10-09"])
    n = out.get("2026-10-09", {})
    check("total from minutesAsleep (240+140 = 6.33 h)", n.get("sleep_total_h"), "6.33")
    check("stages summed", (n.get("sleep_light_h"), n.get("sleep_deep_h"), n.get("sleep_rem_h")), ("4", "1", "1.33"))
    utc_late = F._local_date("2026-11-01T03:30:00Z", "-14400s")
    check("wake day from the instant + its own UTC offset (DST-safe)", (utc_late, F._local_date("2026-11-02T05:30:00Z", "-18000s")),
          ("2026-10-31", "2026-11-02"))


# ── 5. the API client ─────────────────────────────────────────────────────────
class _Resp:
    def __init__(self, body): self.b = body.encode()
    def read(self): return self.b
    def __enter__(self): return self
    def __exit__(self, *a): return False


def _http_error(code, body):
    return urllib.error.HTTPError("u", code, "m", {}, io.BytesIO(body.encode()))


def test_client():
    print("C1. client: invalid_grant -> AuthExpired; 429 retried; Gmail-scope 403 -> AuthScope; token never printed")
    root, _ = fresh_env()
    gh = root / "gh"; gh.mkdir()
    (gh / "client.json").write_text(json.dumps({"client_id": "cid", "client_secret": "csec"}))
    (gh / "token.json").write_text(json.dumps({"refresh_token": "RT-SECRET-123"}))
    import google_health as GH
    GH.time.sleep = lambda s: None
    def dead(req, timeout=0):
        raise _http_error(400, '{"error": "invalid_grant"}')
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
        try:
            GH.Client(gh, opener=dead).call("GET", "/identity")
            got = "no error"
        except GH.AuthExpired as e:
            got = "AuthExpired"
            buf.write(str(e))
    check("invalid_grant is AuthExpired", got, "AuthExpired")
    check("the refresh token never appears in any output", "RT-SECRET-123" in buf.getvalue(), False)
    calls = []
    def flaky(req, timeout=0):
        calls.append(req.full_url)
        if "oauth2" in req.full_url:
            return _Resp('{"access_token": "AT", "expires_in": 3600}')
        if len([c for c in calls if "health" in c]) < 3:
            raise _http_error(429, "slow down")
        return _Resp('{"ok": 1}')
    check("429 is retried, then succeeds", GH.Client(gh, opener=flaky).call("GET", "/identity"), {"ok": 1})
    def gmail(req, timeout=0):
        if "oauth2" in req.full_url:
            return _Resp('{"access_token": "AT", "expires_in": 3600}')
        raise _http_error(403, '{"error": {"details": [{"reason": "DISALLOWED_OAUTH_SCOPES"}]}}')
    try:
        GH.Client(gh, opener=gmail).call("GET", "/identity"); got = "no error"
    except GH.AuthScope:
        got = "AuthScope"
    check("a Gmail-scoped token is reported as a scope problem", got, "AuthScope")
    check("token.json stays private (0600)", oct((gh / "token.json").stat().st_mode & 0o777), oct(0o600))
    src = (HERE / "google_health.py").read_text()
    check("never falls back to the Gmail-scoped google_token.json", "google_token.json" in src.split('"""', 2)[2], False)


def test_alerts():
    print("A1. alerts: once per problem kind, a recovery when it clears, silence otherwise")
    fresh_env()
    import fitbit_sync as F
    m1, s1 = F.decide_alerts({}, {"auth_expired": "x"})
    m2, s2 = F.decide_alerts(s1, {"auth_expired": "x"})
    m3, _ = F.decide_alerts(s2, {})
    check("first occurrence alerts", len(m1), 1)
    check("still broken -> silent", m2, [])
    check("cleared -> one recovery message", (len(m3), "recovered" in m3[0][1]), (1, True))
    m4, _ = F.decide_alerts({"open": {"band_not_syncing": "x"}}, {}, unknown={"band_not_syncing"})
    check("a check that could not run is NOT a recovery", m4, [])
    _, s5 = F.decide_alerts({}, {"repeated_failures": "errors in: steps"})
    m6, _ = F.decide_alerts(s5, {"repeated_failures": "errors in: sleep, steps"})
    check("a NEW failing group alerts again", len(m6), 1)


# ── 6. the ingest: Air beats /sleep, never a hand edit; state survives overlap ─
def test_ingest_sleep_priority():
    print("I1. ingest: Air night replaces a /sleep estimate (sleep_source: hermes) but never a hand edit")
    root, _ = fresh_env()
    import hae_daily_ingest as I
    note = root / "n.md"
    def write(fm):
        note.write_text("---\n" + fm + "---\n# day\n")
    upd = {"sleep_hours": "7.2", "sleep_deep_h": "1.2", "sleep_source": "air"}
    write("type: daily\nsleep_hours: 6\nsleep_source: hermes\nsleep_hermes_h: 6\n")
    I.update_frontmatter(note, dict(upd), {}, I.SLEEP_FIELDS, is_today=False)
    t = note.read_text()
    check("Hermes-logged sleep replaced by the Air night", ("sleep_hours: 7.2" in t, "sleep_source: air" in t), (True, True))
    write("type: daily\nsleep_hours: 8\nsleep_source: hermes\nsleep_hermes_h: 8\n")
    I.update_frontmatter(note, dict(upd), {"sleep_hours": "7.2", "sleep_source": "air"}, I.SLEEP_FIELDS, is_today=False)
    check("a /sleep AFTER the Air night is his correction and sticks", "sleep_hours: 8\n" in note.read_text(), True)
    write("type: daily\nsleep_hours: 6.5\nsleep_source: hermes\nsleep_hermes_h: 6\n")
    I.update_frontmatter(note, dict(upd), {}, I.SLEEP_FIELDS, is_today=False)
    check("a hand edit after /sleep is protected", "sleep_hours: 6.5\n" in note.read_text(), True)
    write("type: daily\nsleep_hours: 6\n")                   # typed by hand in Obsidian: no marker
    I.update_frontmatter(note, dict(upd), {}, I.SLEEP_FIELDS, is_today=False)
    t = note.read_text()
    check("a hand-edited night is never overwritten", ("sleep_hours: 6\n" in t, "sleep_deep_h" in t), (True, False))
    write("type: daily\nsleep_hours: 6\nsleep_source: hermes\nsleep_hermes_h: 6\n")
    I.update_frontmatter(note, {"sleep_hours": "5.5", "sleep_source": "apple"}, {}, I.SLEEP_FIELDS, is_today=False)
    check("an Apple night does NOT replace /sleep (only the Air does)", "sleep_hours: 6\n" in note.read_text(), True)
    # behavioural, not a substring (a docstring mentioning os.replace kept a text check green)
    swaps = []
    real_replace = I.os.replace
    I.os.replace = lambda a, b: (swaps.append((Path(a).name, Path(b).name)), real_replace(a, b))[1]
    try:
        I._save_state({"2026-10-08": {"steps": "1"}})
    finally:
        I.os.replace = real_replace
    check("ingest state is written to a temp file and swapped in", swaps, [("ingest_state.json.tmp", "ingest_state.json")])
    held = []
    import wearable_merge as WM
    real_locked = WM.locked
    @contextlib.contextmanager
    def spy():
        held.append(True)
        with real_locked():
            yield
    WM.locked = spy
    real_argv = sys.argv
    try:
        sys.argv = ["hae_daily_ingest.py", "2026-10-08"]
        with contextlib.redirect_stdout(io.StringIO()):
            I.main()
    finally:
        WM.locked, sys.argv = real_locked, real_argv
    check("the ingest takes the shared metrics lock", held, [True])


def test_vault_log_marks_sleep():
    print("I2. /sleep marks the note sleep_source: hermes (what lets the Air replace it)")
    src = (HERE.parent / "vault" / "vault_log.py").read_text()
    check("cmd_sleep writes the marker and the value it wrote",
          ('"sleep_source": "hermes"' in src, '"sleep_hermes_h"' in src), (True, True))


def test_first_run_keeps_apple_history():
    print("M4. first run after deploy: a FITBIT run before any Apple rebuild must not drop Apple history")
    root, WM = fresh_env()
    import hae_process as P
    metrics = root / "vault" / "07 - Health" / "Metrics"
    body = "".join(f"2026-08-{d:02d},5000,60,7.0\n" for d in range(1, 26))
    (metrics / "metrics.csv").write_text("date,steps,hrv_ms,sleep_total_h\n" + body +
                                         "2026-09-01,10000,70,7.5\n2026-10-07,9000,72,\n")
    (metrics / "fitbit.csv").write_text("date,steps\n2026-10-08,6000\n")
    with WM.locked():
        WM.rebuild(P.COLUMNS)                      # what fitbit_sync does — no apple.csv yet
    rows = WM.read_rows(metrics / "metrics.csv")
    check("old Apple days survive", (rows.get("2026-09-01", {}).get("steps"), rows.get("2026-09-01", {}).get("sleep_total_h")), ("10000", "7.5"))
    check("apple.csv was seeded from the Apple-only metrics.csv", (metrics / "apple.csv").exists(), True)
    check("the Air day is merged in", rows.get("2026-10-08", {}).get("steps"), "6000")
    root2, WM2 = fresh_env()
    import hae_process as P2
    m2 = root2 / "vault" / "07 - Health" / "Metrics"
    (m2 / "metrics.csv").write_text("date,steps\n2026-10-07,9000\n")      # truncated mid-write
    try:
        with WM2.locked():
            WM2.rebuild(P2.COLUMNS)
        got = "seeded"
    except RuntimeError:
        got = "refused"
    check("a suspiciously short metrics.csv is never used as the Apple history", (got, (m2 / "apple.csv").exists()), ("refused", False))


def test_coach_baseline_per_source():
    print("B1. coach: HRV/RHR compared only against the SAME source; 'baseline building' until 7 days")
    root, _ = fresh_env()
    sys.path.insert(0, str(HERE.parent / "fitness"))
    sys.modules.pop("coach_engine", None)
    import coach_engine as CE
    import datetime as _d
    csvp = root / "m.csv"
    lines = ["date,resting_hr,rhr_source,hrv_ms,hrv_rmssd_ms"]
    for i in range(25, 3, -1):                                   # a month of Apple days
        lines.append(f"{(_d.date(2026, 10, 9) - _d.timedelta(days=i)).isoformat()},62,apple,70,")
    for i in (3, 2, 1):                                         # the first Air days
        lines.append(f"{(_d.date(2026, 10, 9) - _d.timedelta(days=i)).isoformat()},57,air,,80")
    lines.append("2026-10-09,56,air,,82")
    csvp.write_text("\n".join(lines) + "\n")
    real_csv, real_now = CE.CSVP, CE.now
    CE.CSVP = csvp
    CE.now = lambda: _d.datetime(2026, 10, 9, 9, 0)
    try:
        b = CE.recovery_baseline()
    finally:
        CE.CSVP, CE.now = real_csv, real_now
    check("Air RMSSD is reported, not the Apple SDNN", (b.get("hrv_rmssd_ms"), "hrv_ms" in b), (82.0, False))
    check("no HRV % until 7 Air days exist", (b.get("hrv_vs_baseline_pct"), "building" in (b.get("hrv_baseline_note") or "")), (None, True))
    check("RHR not compared against the Apple month (no fake -6 bpm)",
          (b.get("rhr_source"), b.get("rhr_vs_baseline_bpm"), "building" in (b.get("rhr_baseline_note") or "")), ("air", None, True))


def test_brief_air_wording():
    print("R1. brief: Air sleep not synced -> 'open the Fitbit app' (no /sleep); HRV labelled; band gap asked")
    fresh_env()
    sys.modules.pop("health_morning_brief_gate", None)
    import health_morning_brief_gate as G
    msg = " ".join(G._sleep_lines({}, False, air=True))
    check("with the band: no request to type sleep in", ("Fitbit app" in msg, "/sleep" in msg), (True, False))
    check("without the band: the old Apple wording stays", "/sleep" in " ".join(G._sleep_lines({}, False)), True)
    lines = " ".join(G._sleep_lines({"sleep_total_h": 7.4, "hrv_rmssd_ms": 80.0, "hrv_ms": 70.0}, True, air=True))
    check("HRV shown with its measure; the SDNN one not shown beside it", ("80ms (RMSSD)" in lines, "SDNN" in lines), (True, False))
    rows = {"2026-10-08": {"steps": "8800", "steps_apple": "8800", "steps_air": "3000", "azm": "12"},
            "2026-10-09": {}}
    real = (G._row_for, G.gather_schedule, G.gather_tasks, G.gather_health_yesterday, G.gather_training,
            G.gather_inbox_checked, G.vault_sync, G.gather_board, G.gather_action_items)
    G._row_for = lambda d: rows.get(d, {})
    G.gather_schedule = G.gather_tasks = lambda d: []
    G.gather_health_yesterday = G.gather_training = lambda d: {}
    G.gather_inbox_checked = lambda d="": ([], "ok")
    G.vault_sync = lambda: {"state": "ok"}
    G.gather_board = lambda: {}
    G.gather_action_items = lambda d="": {"hard_deadlines": "", "this_week": ""}
    try:
        facts = G.gather_facts("2026-10-09", "2026-10-08")
    finally:
        (G._row_for, G.gather_schedule, G.gather_tasks, G.gather_health_yesterday, G.gather_training,
         G.gather_inbox_checked, G.vault_sync, G.gather_board, G.gather_action_items) = real
    a = facts["yesterday_activity"]
    check("activity line = steps + zone minutes on an Air day", (a.get("steps"), a.get("azm"), "active_kcal" in a), (8800, 12, False))
    check("band far below the phone -> the brief asks", a.get("band_gap"), {"band_steps": 3000, "phone_steps": 8800})
    check("air_in_use set", facts.get("air_in_use"), True)


def test_wrapper_and_run_lock():
    print("W1. cron wrapper: expected states exit 0 (no 'script failed' spam), crashes pass through; one run at a time")
    root, _ = fresh_env()
    home = root / "home"
    (home / ".hermes" / "scripts").mkdir(parents=True)
    wrapper = HERE.parent / "cron" / "fitbit_sync.sh"
    out = {}
    for rc in (0, 2, 3, 1):
        (home / ".hermes" / "scripts" / "fitbit_sync.py").write_text(f"import sys; sys.exit({rc})\n")
        r = subprocess.run(["bash", str(wrapper)], capture_output=True, text=True, env={"HOME": str(home), "PATH": "/usr/bin:/bin"})
        out[rc] = (r.returncode, '"wakeAgent": false' in r.stdout)
    check("rc 0/2/3 -> exit 0 and the agent is skipped; a crash (1) passes through",
          out, {0: (0, True), 2: (0, True), 3: (0, True), 1: (1, True)})
    import fitbit_sync as F, fcntl
    F.RUN_LOCK.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(str(F.RUN_LOCK), os.O_RDONLY | os.O_CREAT, 0o666)
    fcntl.flock(fd, fcntl.LOCK_EX)
    try:
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = F.main([])
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)
    check("a second concurrent run exits quietly", (rc, "in progress" in buf.getvalue()), (0, True))
    with contextlib.redirect_stderr(io.StringIO()):
        check("--days without a value is a usage error, not a crash", F._main(["--days"]), 1)


def test_wake_steps_signal():
    print("S1. a mid-night Fitbit sleep stub is not a finished night: band steps after its end decide (Oct 12-13: 3.5h/4.7h sent for 7.5h/8.1h)")
    _, WM = fresh_env()
    import fitbit_sync as F, datetime as _d
    def stp(platform, start, n, method="PASSIVELY_MEASURED"):
        return {"dataSource": {"platform": platform, "recordingMethod": method}, "steps": {"interval": {"startTime": start}, "count": str(n)}}
    end = "2026-10-13T11:26:00Z"
    pts = [stp("FITBIT", "2026-10-13T09:00:00Z", 9),           # asleep: before the end, ignored
           stp("FITBIT", "2026-10-13T11:26:00Z", 12),          # at the end counts
           stp("FITBIT", "2026-10-13T11:40:00Z", 20),
           stp("HEALTH_KIT", "2026-10-13T11:41:00Z", 500),     # the phone is not the band
           stp("FITBIT", "2026-10-13T11:42:00Z", 300, "MANUAL")]
    check("only band steps at/after the sleep end count; gap = minutes until they reach 25",
          F.wake_signal(pts, end), {"wake_steps": "32", "wake_gap_min": "14"})
    check("capped (a yes/no signal; no cell churn every run)", F.wake_signal([stp("FITBIT", end, 5000)], end)["wake_steps"], str(F.WAKE_STEPS_CAP))
    check("gap = when steps FIRST reach 25, not later walking",
          F.wake_signal([stp("FITBIT", "2026-10-13T11:40:00Z", 30), stp("FITBIT", "2026-10-13T13:40:00Z", 100)], end)["wake_gap_min"], "14")
    check("under 25 steps since the end -> no gap yet", F.wake_signal([stp("FITBIT", "2026-10-13T12:00:00Z", 16)], end), {"wake_steps": "16"})
    check("no/garbled sleep end -> 0 steps, never a crash", (F.wake_signal(pts, ""), F.wake_signal(pts, None)),
          ({"wake_steps": "0"}, {"wake_steps": "0"}))
    # THE review case: a stub ending 3:45 AM, then his morning walk at 7:40 -> gap ~235 min
    stub_end = "2026-10-13T07:45:00Z"
    check("morning steps after a STALE stub end give a long gap (the brief keeps waiting)",
          F.wake_signal([stp("FITBIT", "2026-10-13T08:30:00Z", 3), stp("FITBIT", "2026-10-13T11:40:00Z", 60)], stub_end),
          {"wake_steps": "63", "wake_gap_min": "235"})
    check("civil filter = Toronto local, 6 h early (lower bound; travel west safe)", F._civil_filter_time(end), "2026-10-13T01:26:00")

    class Stub:
        def __init__(self, sleep_pts, steps_pts=None, steps_err=None):
            self.sleep_pts, self.steps_pts, self.steps_err, self.calls = sleep_pts, steps_pts or [], steps_err, []
        def daily_rollup(self, *a, **k):
            return []
        def paired_devices(self):
            return []
        def list_points(self, t, filt, page_size=None):
            self.calls.append((t, filt))
            if t == "sleep":
                return self.sleep_pts
            if t == "steps":
                if self.steps_err:
                    raise self.steps_err
                return self.steps_pts
            return []
    night = {"dataSource": {"platform": "FITBIT"}, "sleep": {
        "interval": {"startTime": "2026-10-13T02:55:00Z", "startUtcOffset": "-14400s", "endTime": end, "endUtcOffset": "-14400s"},
        "metadata": {"processed": True}, "summary": {"minutesAsleep": "484", "minutesAwake": "27", "stagesSummary": []}}}
    day = _d.date(2026, 10, 13)
    st = Stub([night], pts)
    res, errs, _ = F.fetch(st, day - _d.timedelta(days=6), day)
    check("fetch stores today's wake signal", res.get("wake-steps"), {"2026-10-13": {"wake_steps": "32", "wake_gap_min": "14"}})
    check("the steps query is bounded below by the sleep end (minus the margin)",
          [c for c in st.calls if c[0] == "steps"], [("steps", 'steps.interval.civil_start_time >= "2026-10-13T01:26:00"')])
    res2, _, _ = F.fetch(Stub([]), day - _d.timedelta(days=6), day)
    check("no night today -> the cell is blanked (empty success)", res2.get("wake-steps"), {})
    res3, errs3, _ = F.fetch(Stub([night], steps_err=RuntimeError("HTTP 500")), day - _d.timedelta(days=6), day)
    check("a failed steps query keeps the old cell and COUNTS as failing (3 in a row alerts)",
          ("wake-steps" in res3, "wake-steps" in errs3, "wake-steps" in F.OPTIONAL_GROUPS), (False, True, False))
    rows = F.apply({"2026-10-13": {"date": "2026-10-13", "wake_steps": "40"}}, {}, ["2026-10-13"])
    check("failed group keeps its cell", rows["2026-10-13"].get("wake_steps"), "40")
    m = WM.merge_day({}, {"sleep_total_h": "8.07", "wake_steps": "32", "wake_gap_min": "14"})
    check("wake signal reaches metrics.csv", (m.get("wake_steps"), m.get("wake_gap_min"),
          all(c in WM.AIR_COLUMNS and c in WM.FITBIT_COLUMNS for c in ("wake_steps", "wake_gap_min"))), ("32", "14", True))


def _brief(rows, state, cutoff_passed, facts_extra=None):
    """Run the brief gate's main() once, offline. Returns (sent messages, saved state)."""
    import datetime as _d
    sys.modules.pop("health_morning_brief_gate", None)
    import health_morning_brief_gate as G
    root = Path(tempfile.mkdtemp(prefix="brief-"))
    _TMP.append(root)
    G.STATE = root / "brief_state.json"
    G.STATE.write_text(json.dumps(state))
    G.DRY_RUN = G.FORCE = False
    G.NO_LLM = True
    G._refresh = lambda: None
    sent = []
    G.send_message = lambda t: sent.append(t) or True
    G.WEEKDAY_CUTOFF = G.WEEKEND_CUTOFF = _d.time(0, 0) if cutoff_passed else _d.time(23, 59)
    G.gather_schedule = G.gather_tasks = lambda d: []
    G.gather_health_yesterday = G.gather_training = lambda d: {}
    G.gather_inbox_checked = lambda d="": ([], "ok")
    G.vault_sync = lambda: {"state": "ok"}
    G.gather_board = lambda: {}
    G.gather_action_items = lambda d="": {"hard_deadlines": "", "this_week": ""}
    now = _d.datetime.now(G.TZ)
    today, yday = now.strftime("%Y-%m-%d"), (now - _d.timedelta(days=1)).strftime("%Y-%m-%d")
    G._row_for = lambda d: dict(rows.get("T" if d == today else ("Y" if d == yday else ""), {}))
    with contextlib.redirect_stdout(io.StringIO()):
        G.main()
    return sent, json.loads(G.STATE.read_text()), today, G


def test_brief_waits_for_finished_night():
    print("S2. brief: waits for a finished Air night; at the cutoff says 'so far'; one correction when the full night lands")
    fresh_env()
    stub = {"sleep_total_h": "4.65", "sleep_source": "air", "sleep_start": "2026-10-13T02:55:00Z", "sleep_end": "2026-10-13T07:35:00Z"}
    full = {"sleep_total_h": "8.07", "sleep_source": "air", "sleep_start": "2026-10-13T02:55:00Z", "sleep_end": "2026-10-13T11:26:00Z",
            "sleep_deep_h": "0.47", "sleep_rem_h": "1.05", "sleep_core_h": "6.55", "resting_hr": "58", "hrv_rmssd_ms": "104.4",
            "spo2_avg": "97.2", "respiratory_rate": "16.5", "wake_steps": "40", "wake_gap_min": "20"}
    yday = {"steps": "8997", "steps_air": "8805", "steps_apple": "8997", "azm": "86", "distance_air_km": "6.05",
            "total_kcal_air": "2431", "exercise_air_count": "1", "exercise_air_min": "45"}
    sent, st, today, G = _brief({"T": dict(stub, wake_steps="0"), "Y": yday}, {}, cutoff_passed=False)
    check("stub night (no steps since it ended), before the cutoff -> no brief yet", (sent, st.get("last_brief_date")), ([], None))
    sent, st, _, _ = _brief({"T": dict(stub, wake_steps="63", wake_gap_min="235"), "Y": yday}, {}, cutoff_passed=False)
    check("he is up and walking but the stub has not grown yet (gap 235 min) -> still waits", sent, [])
    sent, st, today, G = _brief({"T": dict(stub, wake_steps="0"), "Y": yday}, {}, cutoff_passed=True)
    msg = sent[0] if sent else ""
    check("at the cutoff the brief goes, calling the stub 'so far' and not low sleep",
          ("4.7h so far" in msg, "Under your 7h floor" in msg, "Slept" in msg), (True, False, False))
    check("state remembers it lacked the finished night and what it showed",
          (st.get("brief_had_sleep"), st.get("brief_sleep_h")), (False, 4.65))
    sent, st, _, G = _brief({"T": full, "Y": yday}, {}, cutoff_passed=False)
    msg = sent[0] if sent else ""
    check("finished night -> brief fires before the cutoff with the real total + times",
          ("Slept *8.1h* · 10:55 PM → 7:26 AM" in msg, st.get("brief_had_sleep")), (True, True))
    check("yesterday's numbers always in the brief (his ask, Oct 13)",
          all(x in msg for x in ("*8,997* steps", "86 zone min", "6.0 km", "2,431 kcal burned", "1 workout (45 min)")), True)
    check("last night's extras shown", all(x in msg for x in ("deep 0.5h", "REM 1.1h", "light 6.5h", "RHR 58", "HRV 104ms (RMSSD)", "SpO2 97%", "breathing 16.5/min")), True)
    sent, st, _, _ = _brief({"T": full, "Y": yday}, {"last_brief_date": today, "brief_had_sleep": False, "brief_sleep_h": 4.65}, False)
    check("the full night later -> ONE correction naming what the brief had",
          (len(sent), "8.1h" in (sent or [""])[0], "had 4.7h" in (sent or [""])[0], st.get("sleep_followup_date")), (1, True, True, today))
    sent, _, _, _ = _brief({"T": full, "Y": yday}, {"last_brief_date": today, "brief_had_sleep": False, "brief_sleep_h": 7.9}, False)
    check("a 'so far' total within 0.5h of the final -> no extra message", sent, [])
    sent, _, _, _ = _brief({"T": full, "Y": yday}, {"last_brief_date": today, "brief_had_sleep": True, "brief_sleep_h": 7.8}, False)
    check("a finished total that refines by < 0.5h -> no extra message", sent, [])
    sent, _, _, _ = _brief({"T": full, "Y": yday}, {"last_brief_date": today, "brief_had_sleep": True, "brief_sleep_h": 7.4}, False)
    check("a finished total that moves by >= 0.5h -> one correction", len(sent), 1)
    sent, _, _, _ = _brief({"T": {k: v for k, v in full.items() if k != "wake_gap_min"}, "Y": yday},
                           {"last_brief_date": today, "brief_had_sleep": False, "brief_sleep_h": 4.65}, False)
    check("no correction while the night still looks unfinished", sent, [])
    sent, _, _, _ = _brief({"T": full, "Y": yday}, {"last_brief_date": today, "brief_had_sleep": False, "brief_sleep_h": 4.65,
                                                    "sleep_followup_date": today}, False)
    check("never a second correction the same day", sent, [])
    sent, _, _, _ = _brief({"T": {"sleep_total_h": "7.9", "sleep_source": "apple"}, "Y": {}},
                           {"last_brief_date": today, "brief_had_sleep": True, "brief_sleep_h": 6.0}, False)
    check("Apple night the brief already showed -> no new correction (old behaviour kept)", sent, [])
    sent, _, _, _ = _brief({"T": {"sleep_total_h": "7.2"}, "Y": {}}, {}, False)
    check("Apple / hand-logged nights are whole: brief fires as before", len(sent), 1)


TESTS = [test_merge_rules, test_reseed_strips_air, test_flat_layout_end_to_end, test_fitbit_apply_and_parse,
         test_sleep_parse, test_client, test_alerts, test_ingest_sleep_priority, test_vault_log_marks_sleep,
         test_first_run_keeps_apple_history, test_coach_baseline_per_source,
         test_brief_air_wording, test_wrapper_and_run_lock, test_wake_steps_signal,
         test_brief_waits_for_finished_night]

if __name__ == "__main__":
    for t in TESTS:
        try:
            t()
        except Exception as e:  # noqa: BLE001
            FAILS.append(f"{t.__name__} crashed: {type(e).__name__}: {e}")
            print(f"  ✗ {t.__name__} crashed: {type(e).__name__}: {e}")
    for d in _TMP:
        shutil.rmtree(d, ignore_errors=True)
    print(f"\n{PASSES} passed, {len(FAILS)} failed")
    sys.exit(1 if FAILS else 0)
