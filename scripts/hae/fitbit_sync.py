#!/usr/bin/env python3
"""
fitbit_sync.py — pull the Fitbit Air's daily data from the Google Health API into
<vault>/07 - Health/Metrics/fitbit.csv, then re-derive metrics.csv (wearable_merge).

    fitbit_sync.py              # trailing 7 days (Toronto dates), the normal cron run
    fitbit_sync.py --days 14    # backfill
    fitbit_sync.py --dry-run    # fetch + print what would change; write nothing

Design (plan reviewed adversarially, 2026-10-08):
  * AIR ONLY. His Google Health account also holds Apple Health data (every daily RHR / HRV /
    sleep / VO2 point before the Air was `platform: HEALTH_KIT`). Every list call keeps only
    `dataSource.platform == "FITBIT"`; rollups use the google-wearables source family.
  * Each data type succeeds or fails ON ITS OWN. A failed call keeps the cells it would have
    written; only a SUCCESSFUL empty response blanks them ("no data" is not "the call failed").
  * Missing is blank, never 0. Calories and VO2 max are kept only on days the band was worn
    (Fitbit reports a resting-burn estimate and a VO2 figure even for days without it).
  * A night belongs to the day he woke up (same as HAE). Durations come from the API's minute
    counts, never from clock-time subtraction (DST ends Nov 1).
  * Trailing 7 days, so days the band synced late are refetched.
  * Alerts (Telegram via `hermes send`) only on a state change: auth broken, band not synced
    for > 24 h, 3 runs in a row failing. Exit 0 ok · 2 partial · 3 auth/config.
"""
from __future__ import annotations

import datetime
import json
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import google_health as GH  # noqa: E402
import wearable_merge as WM  # noqa: E402

try:
    from zoneinfo import ZoneInfo
    TZ = ZoneInfo("America/Toronto")
except Exception:  # noqa: BLE001
    TZ = None

STATE = Path(os.environ.get("HAE_HEALTH_DIR", str(Path.home() / ".hermes" / "health" / "hae"))) / "fitbit_state.json"
HERMES = os.path.expanduser("~/.local/bin/hermes")
STALE_SYNC_H = 24
FAIL_STREAK_ALERT = 3
INGEST = Path(__file__).resolve().parent / "hae_daily_ingest.py"
# metrics columns whose change is worth rewriting the daily notes for right away (steps are
# left to the regular hae-sync ingest, so the note is not rewritten every hour — review M7)
NOTEWORTHY = {"sleep_total_h", "sleep_deep_h", "sleep_rem_h", "sleep_core_h", "sleep_awake_h",
              "resting_hr", "hrv_rmssd_ms", "azm", "spo2_avg"}

# group -> the fitbit.csv columns it owns (a failed group keeps exactly these)
GROUP_COLS = {
    "steps": ["steps"], "distance": ["distance_km"], "total-calories": ["total_kcal"],
    "active-minutes": ["active_min"], "active-zone-minutes": ["azm"],
    "daily-resting-heart-rate": ["resting_hr"], "daily-heart-rate-variability": ["hrv_rmssd_ms"],
    "daily-oxygen-saturation": ["spo2_avg"], "daily-respiratory-rate": ["resp_rate"],
    "daily-sleep-temperature-derivations": ["skin_temp_delta"], "daily-vo2-max": ["vo2_max"],
    "sleep": ["sleep_total_h", "sleep_light_h", "sleep_deep_h", "sleep_rem_h", "sleep_awake_h",
              "sleep_in_bed_h", "sleep_start", "sleep_end", "sleep_stages_valid"],
    "exercise": ["exercise_count", "exercise_min"],
}
WORN_GATED = {"total_kcal", "vo2_max"}


def today_local() -> datetime.date:
    return (datetime.datetime.now(TZ) if TZ else datetime.datetime.now()).date()


def _i(v):
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return None


def _f(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _fmt(v, nd=1):
    """Store numbers compactly and stably (no '9384.0' vs '9384' churn)."""
    if v is None:
        return None
    if nd == 0 or isinstance(v, int) or float(v).is_integer():
        return str(int(round(float(v))))
    return str(round(float(v), nd))


def _civil_date(cdt: dict) -> str | None:
    d = (cdt or {}).get("date") or {}
    try:
        return datetime.date(int(d["year"]), int(d["month"]), int(d["day"])).isoformat()
    except (KeyError, TypeError, ValueError):
        return None


def _local_date(ts: str, offset: str) -> str | None:
    """'2026-10-09T11:05:00Z' + '-14400s' -> local civil date of that instant."""
    try:
        t = datetime.datetime.fromisoformat(ts.replace("Z", "+00:00"))
        secs = int(float(str(offset).rstrip("s") or 0))
        return (t + datetime.timedelta(seconds=secs)).date().isoformat()
    except (TypeError, ValueError, AttributeError):
        return None


def is_air(point: dict) -> bool:
    """Recorded by the Fitbit device. Excludes entries typed into the Fitbit app by hand
    (recordingMethod MANUAL) — those are not band data (review L1)."""
    ds = point.get("dataSource") or {}
    return ds.get("platform") == "FITBIT" and str(ds.get("recordingMethod", "")).upper() != "MANUAL"


# ── per-group parsers (pure: API points -> {date: {col: value}}) ─────────────────────
def parse_rollup(group: str, points: list) -> dict:
    out = {}
    for p in points:
        d = _civil_date(p.get("civilStartTime"))
        if not d:
            continue
        if group == "steps":
            v = _i((p.get("steps") or {}).get("countSum"))
            vals = {"steps": _fmt(v)} if v is not None else {}
        elif group == "distance":
            mm = _f((p.get("distance") or {}).get("millimetersSum"))
            vals = {"distance_km": _fmt(mm / 1e6, 2)} if mm is not None else {}
        elif group == "total-calories":
            k = _f((p.get("totalCalories") or {}).get("kcalSum"))
            vals = {"total_kcal": _fmt(k, 0)} if k is not None else {}
        elif group == "active-minutes":
            lv = (p.get("activeMinutes") or {}).get("activeMinutesRollupByActivityLevel") or []
            tot = sum(_i(x.get("activeMinutesSum")) or 0 for x in lv)
            vals = {"active_min": _fmt(tot)} if lv else {}
        elif group == "active-zone-minutes":
            z = p.get("activeZoneMinutes") or {}
            parts = [_i(z.get(k)) for k in ("sumInFatBurnHeartZone", "sumInCardioHeartZone", "sumInPeakHeartZone")]
            # The rollup fields are sums of AZM, which already count a cardio/peak minute as 2
            # (discovery doc), so the plain sum should match the app (check once against it).
            vals = {"azm": _fmt(sum(x for x in parts if x is not None))} if any(x is not None for x in parts) else {}
        else:
            vals = {}
        if vals:
            out[d] = vals
    return out


DAILY_FIELDS = {
    "daily-resting-heart-rate": ("dailyRestingHeartRate", lambda x: {"resting_hr": _fmt(_i(x.get("beatsPerMinute")))}),
    "daily-heart-rate-variability": ("dailyHeartRateVariability",
                                     lambda x: {"hrv_rmssd_ms": _fmt(_f(x.get("averageHeartRateVariabilityMilliseconds")))}),
    "daily-oxygen-saturation": ("dailyOxygenSaturation", lambda x: {"spo2_avg": _fmt(_f(x.get("averagePercentage")))}),
    "daily-respiratory-rate": ("dailyRespiratoryRate", lambda x: {"resp_rate": _fmt(_f(x.get("breathsPerMinute")))}),
    "daily-sleep-temperature-derivations": ("dailySleepTemperatureDerivations",
        lambda x: {"skin_temp_delta": _fmt(_f(x.get("nightlyTemperatureCelsius")) - _f(x.get("baselineTemperatureCelsius")), 2)}
        if _f(x.get("nightlyTemperatureCelsius")) is not None and _f(x.get("baselineTemperatureCelsius")) is not None else {}),
    "daily-vo2-max": ("dailyVo2Max", lambda x: {"vo2_max": _fmt(_f(x.get("vo2Max")))}),
}


def parse_daily(group: str, points: list) -> dict:
    key, fn = DAILY_FIELDS[group]
    out = {}
    for p in points:
        if not is_air(p):
            continue
        x = p.get(key) or {}
        d = _civil_date({"date": x.get("date")})
        vals = {k: v for k, v in (fn(x) or {}).items() if v is not None}
        if d and vals and d not in out:
            out[d] = vals          # newest first from the API: the first one seen wins
    return out


def parse_sleep(points: list) -> dict:
    """Sum every processed, non-nap Air session that ENDS on a date (a split night counts
    whole). Totals from the API's minute counts; stages from stagesSummary."""
    acc = {}
    for p in points:
        if not is_air(p):
            continue
        s = p.get("sleep") or {}
        meta = s.get("metadata") or {}
        if not meta.get("processed") or meta.get("nap"):
            continue
        iv = s.get("interval") or {}
        d = _local_date(iv.get("endTime", ""), iv.get("endUtcOffset", "0s"))
        summ = s.get("summary") or {}
        asleep = _i(summ.get("minutesAsleep"))
        if not d or asleep is None:
            continue
        a = acc.setdefault(d, {"asleep": 0, "awake": 0, "period": 0, "LIGHT": 0, "DEEP": 0, "REM": 0,
                               "stages": False, "start": None, "end": None})
        a["asleep"] += asleep
        a["awake"] += _i(summ.get("minutesAwake")) or 0
        a["period"] += _i(summ.get("minutesInSleepPeriod")) or 0
        for st in summ.get("stagesSummary") or []:
            t = str(st.get("type", "")).upper()
            m = _i(st.get("minutes")) or 0
            if t in ("LIGHT", "DEEP", "REM") and m:
                a[t] += m
                a["stages"] = True
        st_, en_ = iv.get("startTime"), iv.get("endTime")
        if st_ and (a["start"] is None or st_ < a["start"]):
            a["start"] = st_
        if en_ and (a["end"] is None or en_ > a["end"]):
            a["end"] = en_
    out = {}
    for d, a in acc.items():
        row = {"sleep_total_h": _fmt(a["asleep"] / 60, 2), "sleep_awake_h": _fmt(a["awake"] / 60, 2),
               "sleep_start": a["start"], "sleep_end": a["end"],
               "sleep_stages_valid": "true" if a["stages"] else "false"}
        if a["period"]:
            row["sleep_in_bed_h"] = _fmt(a["period"] / 60, 2)
        if a["stages"]:
            row.update({"sleep_light_h": _fmt(a["LIGHT"] / 60, 2), "sleep_deep_h": _fmt(a["DEEP"] / 60, 2),
                        "sleep_rem_h": _fmt(a["REM"] / 60, 2)})
        out[d] = {k: v for k, v in row.items() if v is not None}
    return out


def parse_exercise(points: list) -> dict:
    out = {}
    for p in points:
        if not is_air(p):
            continue
        e = p.get("exercise") or {}
        iv = e.get("interval") or {}
        d = _local_date(iv.get("startTime", ""), iv.get("startUtcOffset", "0s"))
        secs = _f(str(e.get("activeDuration", "")).rstrip("s"))
        if not d:
            continue
        o = out.setdefault(d, {"n": 0, "min": 0.0})
        o["n"] += 1
        o["min"] += (secs or 0) / 60
    return {d: {"exercise_count": str(o["n"]), "exercise_min": _fmt(o["min"], 0)} for d, o in out.items()}


# ── fetch ─────────────────────────────────────────────────────────────────────
def fetch(client, start: datetime.date, end: datetime.date) -> tuple[dict, dict, str | None]:
    """Returns (results {group: {date: vals}} for groups that SUCCEEDED, errors {group: msg},
    last_sync). Auth/config/network errors propagate (they make every group fail the same
    way). A pairedDevices failure is reported as errs["pairedDevices"] but is OPTIONAL —
    main() keeps it out of the exit code and the failure streak."""
    res, errs = {}, {}
    end_x = end + datetime.timedelta(days=1)
    s_iso = start.isoformat()
    for g in ("steps", "distance", "total-calories", "active-minutes", "active-zone-minutes"):
        try:
            res[g] = parse_rollup(g, client.daily_rollup(g, start, end_x))
        except (GH.AuthExpired, GH.AuthScope, GH.AccountNotLinked, GH.Missing, GH.NetworkDown):
            raise
        except Exception as e:  # noqa: BLE001
            errs[g] = f"{type(e).__name__}: {str(e)[:120]}"
    for g in DAILY_FIELDS:
        snake = g.replace("-", "_")
        try:
            res[g] = parse_daily(g, client.list_points(g, f'{snake}.date >= "{s_iso}"'))
        except (GH.AuthExpired, GH.AccountNotLinked, GH.Missing, GH.NetworkDown):
            raise
        except Exception as e:  # noqa: BLE001 — incl. a scope error on one type
            errs[g] = f"{type(e).__name__}: {str(e)[:120]}"
    try:
        res["sleep"] = parse_sleep(client.list_points("sleep", f'sleep.interval.civil_end_time >= "{s_iso}"'))
    except (GH.AuthExpired, GH.AccountNotLinked, GH.Missing, GH.NetworkDown):
        raise
    except Exception as e:  # noqa: BLE001
        errs["sleep"] = f"{type(e).__name__}: {str(e)[:120]}"
    try:
        res["exercise"] = parse_exercise(client.list_points("exercise", f'exercise.interval.civil_start_time >= "{s_iso}"'))
    except (GH.AuthExpired, GH.AccountNotLinked, GH.Missing, GH.NetworkDown):
        raise
    except Exception as e:  # noqa: BLE001
        errs["exercise"] = f"{type(e).__name__}: {str(e)[:120]}"
    last_sync = None
    try:
        for dev in client.paired_devices():
            ts = dev.get("lastSyncTime")
            if ts and (last_sync is None or ts > last_sync):
                last_sync = ts
    except GH.NetworkDown:
        raise
    except Exception as e:  # noqa: BLE001 — optional (needs settings.readonly)
        errs["pairedDevices"] = f"{type(e).__name__}: {str(e)[:120]}"
    return res, errs, last_sync


def apply(existing: dict, results: dict, window: list) -> dict:
    """Pure: new fitbit.csv rows. For each SUCCESSFUL group, its columns on every date in the
    window take the fetched value or become blank; a FAILED group (absent from results) keeps
    its old cells. Worn-gated columns are kept only where the Air has steps that day."""
    rows = {d: dict(r) for d, r in existing.items()}
    for g, by_date in results.items():
        cols = GROUP_COLS[g]
        for d in window:
            r = rows.setdefault(d, {"date": d})
            for c in cols:
                r.pop(c, None)
            for c, v in (by_date.get(d) or {}).items():
                if c in cols and v not in (None, ""):
                    r[c] = v
    for d in window:
        r = rows.get(d)
        if r and not r.get("steps"):
            for c in WORN_GATED:
                r.pop(c, None)
    return {d: r for d, r in rows.items() if len([k for k in r if k != "date"]) > 0}


# ── alerts ────────────────────────────────────────────────────────────────────
def _send(subject: str, body: str) -> bool:
    try:
        subprocess.run([HERMES, "send", "-t", "telegram", "-q", "-s", subject, body], check=True, timeout=30)
        return True
    except Exception as e:  # noqa: BLE001
        print(f"send failed: {e}", file=sys.stderr)
        return False


def decide_alerts(prev: dict, problems: dict, unknown: set = frozenset()) -> tuple[list, dict]:
    """Pure: which alerts/recoveries to send. `problems` {kind: message}; `unknown` = kinds this
    run could NOT evaluate (e.g. band_not_syncing when pairedDevices failed) — their previous
    state is carried, so one failed check never sends a false "recovered" (review M1).
    A kind whose message changes (e.g. a NEW group starts failing) alerts again."""
    was = dict(prev.get("open", {}))
    now = dict(problems)
    for k in unknown:
        if k in was and k not in now:
            now[k] = was[k]
    msgs = []
    for k in sorted(now):
        if k not in was or (k == "repeated_failures" and now[k] != was[k]):
            msgs.append((k, "⚠️ Fitbit sync: " + k.replace("_", " "), now[k]))
    msgs += [(k, "✅ Fitbit sync recovered", f"{k.replace('_', ' ')} is resolved") for k in sorted(set(was) - set(now))]
    return msgs, {"open": now}


MAX_DAYS = 14          # the API caps some rollups (total-calories, active-minutes) at 14 days
RUN_LOCK = Path(os.environ.get("HAE_HEALTH_DIR", str(Path.home() / ".hermes" / "health" / "hae"))) / "fitbit_run.lock"


def main(argv: list) -> int:
    """One run at a time: the brief's refresh and the cron both call this; a second caller
    exits quietly instead of racing the first (review M2)."""
    import fcntl
    RUN_LOCK.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(str(RUN_LOCK), os.O_RDONLY | os.O_CREAT, 0o666)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print("[fitbit-sync] another run is in progress — skipping")
            return 0
        return _main(argv)
    finally:
        os.close(fd)


def _main(argv: list) -> int:
    days = 7
    if "--days" in argv:
        try:
            days = int(argv[argv.index("--days") + 1])
        except (IndexError, ValueError):
            print("usage: fitbit_sync.py [--days N (1-14)] [--dry-run]", file=sys.stderr)
            return 1
        days = max(1, min(MAX_DAYS, days))
    dry = "--dry-run" in argv
    end = today_local()
    start = end - datetime.timedelta(days=days - 1)
    window = [(start + datetime.timedelta(days=i)).isoformat() for i in range(days)]
    try:
        prev = json.loads(STATE.read_text())
    except (OSError, ValueError):
        prev = {}
    problems, code, note, unknown = {}, 0, "", set()
    try:
        client = GH.Client()
        results, errs, last_sync = fetch(client, start, end)
    except GH.NetworkDown as e:          # transient: counts toward the streak, no instant alert
        results, errs, last_sync, code = {}, {"network": str(e)}, None, 2
    except GH.GHError as e:
        problems[e.kind] = str(e)
        results, errs, last_sync, code = {}, {}, None, 3
    hard = {g: m for g, m in errs.items() if g != "pairedDevices"}     # pairedDevices is optional
    if hard:
        code = code or 2
        note = "; ".join(f"{g}: {m}" for g, m in sorted(hard.items()))
    streak = (prev.get("fail_streak", 0) + 1) if (code or hard) else 0
    if streak >= FAIL_STREAK_ALERT and code != 3:
        problems["repeated_failures"] = (f"{streak} runs in a row had errors in: "
                                         f"{', '.join(sorted(hard)) or 'auth'} — {note[:300]}")
    if not last_sync:
        unknown.add("band_not_syncing")           # could not check: keep the previous state
    if last_sync:
        try:
            age_h = (datetime.datetime.now(datetime.timezone.utc)
                     - datetime.datetime.fromisoformat(last_sync.replace("Z", "+00:00"))).total_seconds() / 3600
            if age_h > STALE_SYNC_H:
                problems["band_not_syncing"] = (f"the Air last synced {age_h:.0f} h ago ({last_sync}). "
                                                "Open the Fitbit app on your phone so it syncs.")
        except ValueError:
            pass

    changed = []
    if results:
        with WM.locked():
            existing = WM.read_rows(WM.FITBIT_CSV)
            rows = apply(existing, results, window)
            if last_sync:
                rows.setdefault(end.isoformat(), {"date": end.isoformat()})["fb_last_sync"] = last_sync
            for d in window:
                for c in set((rows.get(d) or {})) | set((existing.get(d) or {})):
                    if (rows.get(d) or {}).get(c) != (existing.get(d) or {}).get(c):
                        changed.append((d, c))
            if dry:
                print(f"[dry-run] would change {len(changed)} cell(s): {changed[:20]}")
            else:
                before = WM.read_rows(WM.METRICS_CSV)
                WM.write_rows(WM.FITBIT_CSV, WM.FITBIT_COLUMNS, rows)
                WM.rebuild()
                after = WM.read_rows(WM.METRICS_CSV)
        if not dry:
            moved = {(d, c) for d in window for c in NOTEWORTHY
                     if (before.get(d) or {}).get(c) != (after.get(d) or {}).get(c)}
            if moved:
                # outside the lock: the ingest takes the same lock itself
                try:
                    r = subprocess.run([sys.executable, str(INGEST), "--days", str(min(days, 7))],
                                       capture_output=True, text=True, timeout=120)
                    print(f"ingest ({len(moved)} noteworthy change(s)): exit {r.returncode}")
                except subprocess.TimeoutExpired:
                    print("ingest timed out after 120 s (the next run retries)")

    if not dry:
        msgs, st = decide_alerts(prev, problems, unknown)
        for kind, subj, body in msgs:
            if not _send(subj, body):
                # not delivered: restore the previous state for this kind so the next run retries
                if kind in (prev.get("open") or {}):
                    st["open"][kind] = prev["open"][kind]
                else:
                    st["open"].pop(kind, None)
        st.update({"fail_streak": streak, "last_run": datetime.datetime.now(datetime.timezone.utc).isoformat(),
                   "last_sync": last_sync or prev.get("last_sync"), "errors": errs})
        if code == 0:
            st["last_ok"] = st["last_run"]
        else:
            st["last_ok"] = prev.get("last_ok")
        STATE.parent.mkdir(parents=True, exist_ok=True)
        tmp = STATE.with_name(STATE.name + ".tmp")
        tmp.write_text(json.dumps(st))
        os.replace(str(tmp), str(STATE))
    print(f"[fitbit-sync] {window[0]}..{window[-1]} code={code} groups_ok={len(results)} "
          f"changed_cells={len(changed)} last_sync={last_sync} problems={sorted(problems)}"
          + (f" errors: {note}" if note else ""))
    return code


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
