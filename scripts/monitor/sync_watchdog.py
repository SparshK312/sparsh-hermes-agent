#!/usr/bin/env python3
"""
sync_watchdog.py — is the VPS vault actually in sync with the Mac? (no LLM, cron */30)

WHY (2026-10-04). Obsidian Sync was jammed for most of Sep 14 → Oct 5 ("vault limit
exceeded": Log.md's version history filled the 1 GiB quota) and NOTHING noticed: Hermes
nudged from a vault three days stale. The obvious watchdog would not have caught it
either — measured on the real log: the headless client never prints "limit exceeded"
(only `Sync error: {}`, 26,272 times), and it kept printing "Fully synced" on days it
was broken. So this checks what actually moves, not what the client says:

  1. PENDING UPLOADS — files changed on this box whose change has not reached the server
     (headless state.db: local hash != synchash). Older than PENDING_MAX_MIN → broken.
  2. ERROR RATE — `Sync error` lines in the last hour of sync.log.
  3. SERVICE — obsidian-sync.service must be active.
  4. THE MAC'S HEARTBEAT — `09 - Systems/Hermes/Sync Heartbeat.md`, rewritten hourly by a
     Mac launchd job. Its age is how long since the Mac's writes last arrived here (the
     Mac only syncs while it is awake with Obsidian open, so the threshold is generous).
  5. ECHO — the heartbeat's nonce is copied into `Sync Echo.md` (only when it changes),
     so the Mac's catchup.py can prove the full Mac → VPS → Mac round trip.

Alerts go to Telegram via `hermes send` ONLY on a state change (ok→bad, bad→ok) and every
REALERT_H hours while still bad — never every 30 minutes. It also keeps sync.log bounded
(the client never rotates it; it had reached 22 MB).
"""
from __future__ import annotations

import json
import os
import re
import sqlite3
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

VAULT = Path(os.environ.get("HERMES_VAULT", "/home/hermes/vault"))
SYNC_DIR = Path(os.environ.get("OB_SYNC_DIR", str(Path.home() / ".config/obsidian-headless/sync")))
STATE = Path(os.environ.get("SYNC_WATCHDOG_STATE", str(Path.home() / ".hermes/sync_watchdog_state.json")))
HERMES = os.path.expanduser("~/.local/bin/hermes")
HEARTBEAT = VAULT / "09 - Systems" / "Hermes" / "Sync Heartbeat.md"
ECHO = VAULT / "09 - Systems" / "Hermes" / "Sync Echo.md"

PENDING_MAX_MIN = 45
ERRORS_PER_HOUR_MAX = 10
HEARTBEAT_MAX_H = 26
REALERT_H = 12
LOG_MAX_BYTES = 5 * 1024 * 1024
LOG_KEEP_BYTES = 1024 * 1024
TS_RE = re.compile(r"^\[(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z)\]")


def _now() -> datetime:
    return datetime.now(timezone.utc)


def sync_dir() -> Path | None:
    dirs = [d for d in SYNC_DIR.glob("*") if (d / "state.db").exists()]
    return dirs[0] if len(dirs) == 1 else (max(dirs, key=lambda d: d.stat().st_mtime) if dirs else None)


def pending_uploads(db: Path, now: datetime) -> list[tuple[str, float]]:
    """[(path, age_minutes)] for files changed locally but not yet synced."""
    c = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        out = []
        for path, data in c.execute("select path, data from local_files"):
            d = json.loads(data)
            if d.get("folder") or not d.get("hash") or d.get("hash") == d.get("synchash"):
                continue
            mtime = (d.get("mtime") or 0) / 1000.0
            age = (now.timestamp() - mtime) / 60.0 if mtime else 1e9
            out.append((path, age))
        return out
    finally:
        c.close()


def errors_last_hour(log: Path, now: datetime) -> int:
    if not log.exists():
        return 0
    size = log.stat().st_size
    with open(log, "rb") as f:
        f.seek(max(0, size - 400_000))
        tail = f.read().decode("utf-8", "ignore").splitlines()
    cutoff = now - timedelta(hours=1)
    n = 0
    for ln in tail:
        m = TS_RE.match(ln)
        if m and "Sync error" in ln:
            ts = datetime.fromisoformat(m.group(1).replace("Z", "+00:00"))
            if ts >= cutoff:
                n += 1
    return n


def service_active() -> bool:
    r = subprocess.run(["systemctl", "is-active", "obsidian-sync"], capture_output=True, text=True)
    return r.stdout.strip() == "active"


def read_heartbeat(p: Path) -> tuple[datetime | None, str | None]:
    if not p.exists():
        return None, None
    t = p.read_text("utf-8", "ignore")
    ts = re.search(r"^ts:\s*(\S+)", t, re.M)
    nonce = re.search(r"^nonce:\s*(\S+)", t, re.M)
    try:
        when = datetime.fromisoformat(ts.group(1).replace("Z", "+00:00")) if ts else None
    except ValueError:
        when = None
    return when, (nonce.group(1) if nonce else None)


def write_echo(p: Path, nonce: str, now: datetime) -> bool:
    """Write-if-changed: a timestamp that changes every run would mint a Sync version
    every 30 minutes for nothing."""
    cur = p.read_text("utf-8", "ignore") if p.exists() else ""
    if f"nonce: {nonce}" in cur:
        return False
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(f"---\ntype: reference\nstatus: reference\ngenerated: true\n---\n"
                 f"nonce: {nonce}\nechoed_at: {now.isoformat(timespec='seconds')}\n\n"
                 f"Written by sync_watchdog.py on the VPS when it first sees a new Mac heartbeat "
                 f"nonce. The Mac's catchup.py compares this to its own heartbeat to prove the "
                 f"Mac → VPS → Mac round trip. Machine-owned; never edit.\n")
    return True


def rotate(log: Path) -> bool:
    if not log.exists() or log.stat().st_size <= LOG_MAX_BYTES:
        return False
    with open(log, "rb") as f:
        f.seek(-LOG_KEEP_BYTES, os.SEEK_END)
        keep = f.read()
    with open(log, "r+b") as f:          # copytruncate: the client keeps its fd open
        f.seek(0)
        f.write(keep)
        f.truncate(len(keep))
    return True


def evaluate(now: datetime) -> tuple[list[str], dict]:
    problems, info = [], {}
    d = sync_dir()
    if d is None:
        return ["no obsidian-headless sync state found"], info
    pend = pending_uploads(d / "state.db", now)
    stuck = [(p, a) for p, a in pend if a > PENDING_MAX_MIN]
    info["pending"] = len(pend)
    if stuck:
        worst = max(a for _, a in stuck)
        problems.append(f"{len(stuck)} file(s) changed here have not uploaded for up to "
                        f"{worst/60:.1f} h (e.g. {stuck[0][0]})")
    errs = errors_last_hour(d / "sync.log", now)
    info["errors_1h"] = errs
    if errs >= ERRORS_PER_HOUR_MAX:
        problems.append(f"{errs} 'Sync error' lines in the last hour")
    if not service_active():
        problems.append("obsidian-sync.service is not active")
    hb_when, hb_nonce = read_heartbeat(HEARTBEAT)
    if hb_when is None:
        problems.append("no Mac heartbeat yet (Sync Heartbeat.md missing or unreadable)")
    else:
        age_h = (now - hb_when).total_seconds() / 3600
        info["heartbeat_age_h"] = round(age_h, 1)
        if age_h > HEARTBEAT_MAX_H:
            problems.append(f"the Mac's last heartbeat arrived {age_h:.0f} h ago "
                            f"(Mac asleep / Obsidian closed, or Mac→VPS sync broken)")
        if hb_nonce:
            info["echoed"] = write_echo(ECHO, hb_nonce, now)
    info["rotated"] = rotate(d / "sync.log")
    return problems, info


def send(subject: str, body: str) -> bool:
    """True only if `hermes send` exited 0. A lost alert must not be recorded as sent."""
    try:
        subprocess.run([HERMES, "send", "-t", "telegram", "-q", "-s", subject, body],
                       check=True, timeout=30)
        return True
    except Exception as e:  # noqa: BLE001
        print(f"send failed: {e}", file=sys.stderr)
        return False


def decide(prev: dict, problems: list[str], now: datetime) -> str | None:
    """'alert' | 'realert' | 'recovered' | None — pure, so it is testable."""
    was_bad = bool(prev.get("bad"))
    if problems and not was_bad:
        return "alert"
    if problems and was_bad:
        last = prev.get("last_alert")
        if not last or now - datetime.fromisoformat(last) > timedelta(hours=REALERT_H):
            return "realert"
        return None
    if not problems and was_bad:
        return "recovered"
    return None


def next_state(prev: dict, action: str | None, problems: list[str], now: datetime,
               sent: bool) -> dict:
    """The state to persist — pure, so it is testable. (Audit 2026-10-07: a failed
    `hermes send` used to be saved as alerted, so the alert was lost and nothing retried
    for REALERT_H hours.) If the message did not go out, keep the PREVIOUS state so the
    next run decides the same action again and retries it."""
    st = dict(prev)
    st["checked"] = now.isoformat()
    if action is not None and not sent:
        return st
    if action in ("alert", "realert"):
        st["last_alert"] = now.isoformat()
    st["bad"] = bool(problems)
    return st


def run() -> int:
    now = _now()
    problems, info = evaluate(now)
    prev = json.loads(STATE.read_text()) if STATE.exists() else {}
    action = decide(prev, problems, now)
    sent = True
    if action in ("alert", "realert"):
        sent = send("⚠️ Vault sync problem",
                    "The VPS copy of your vault is not syncing properly, so Hermes may be "
                    "working from old data:\n- " + "\n- ".join(problems))
    elif action == "recovered":
        sent = send("✅ Vault sync recovered", "The VPS vault is syncing again.")
    STATE.parent.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps(next_state(prev, action, problems, now, sent)))
    print(f"[sync-watchdog] {now:%Y-%m-%dT%H:%M}Z {'BAD' if problems else 'ok'} {info} "
          f"{problems if problems else ''} action={action} sent={sent}")
    return 0 if sent else 1


def main() -> int:
    """A watchdog that dies silently is the failure it exists to catch (audit 2026-10-07:
    a crash went only to a log nobody reads while the cron still read `ok`)."""
    try:
        return run()
    except Exception as e:  # noqa: BLE001
        print(f"[sync-watchdog] CRASHED: {type(e).__name__}: {e}", file=sys.stderr)
        send("⚠️ Vault sync watchdog crashed",
             f"sync_watchdog.py raised {type(e).__name__}: {e}. Sync is UNMONITORED until "
             "this is fixed (log: ~/.hermes/health/sync_watchdog.log).")
        return 2


if __name__ == "__main__":
    sys.exit(main())
