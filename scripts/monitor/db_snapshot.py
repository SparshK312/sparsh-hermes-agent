#!/usr/bin/env python3
"""
db_snapshot.py — consistent daily snapshots of Hermes' SQLite databases.

WHY THIS EXISTS
---------------
`hermes doctor` (v0.20.5) reports that the linked SQLite 3.50.4 is exposed to
the WAL-reset corruption bug (https://sqlite.org/wal.html#walresetbug) across
five databases, the largest being state.db (~197 MB, 567 sessions, 10k
messages). The real repair is upgrading the interpreter's SQLite, which means
rebuilding the uv-managed Python runtime the gateway runs on — a deliberate,
riskier job. This does not fix the exposure; it bounds the CONSEQUENCE while
that repair waits.

WHY NOT `cp`
------------
These DBs are in WAL mode and are open by a live process. A plain file copy can
capture a torn page set or miss the WAL entirely, producing a backup that only
fails when you try to restore it. sqlite3's online backup API takes a
transactionally consistent snapshot of a database that is being written to.
Every snapshot is then re-opened and `PRAGMA integrity_check`ed before the old
ones are pruned, so a silently corrupt backup can never evict a good one.

Managed by https://github.com/SparshK312/sparsh-hermes-agent
Installed by scripts/monitor/install_db_snapshot.sh -> /usr/local/bin.
"""

import gzip
import os
import pathlib
import shutil
import signal
import sqlite3
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

HERMES = Path(os.environ.get("HERMES_HOME", "/home/hermes/.hermes"))
DEST = Path(os.environ.get("SNAPSHOT_DIR", HERMES / "backups" / "db-snapshots"))
KEEP = int(os.environ.get("SNAPSHOT_KEEP", "7"))
LOG = Path(os.environ.get("SNAPSHOT_LOG", str(HERMES / "logs" / "db-snapshot.log")))

DATABASES = [
    "state.db",
    "memory_store.db",
    "kanban.db",
    "cron/executions.db",
    "cron/notepad.db",          # new in v0.20.5; absent until a job uses it
    "verification_evidence.db",
]


def log(msg: str) -> None:
    line = f"{datetime.now().astimezone():%Y-%m-%d %H:%M:%S%z} {msg}"
    print(line)
    try:
        with LOG.open("a") as fh:
            fh.write(line + "\n")
    except OSError:
        pass  # never let logging failure abort a backup


def snapshot_one(src: Path, stamp: str) -> bool:
    """Online-backup one DB, verify it, gzip it. Returns True on success."""
    name = str(src.relative_to(HERMES)).replace("/", "_")
    final = DEST / f"{name}.{stamp}.gz"
    tmp_db = None
    try:
        # Snapshot to a temp file first: a half-written .gz next to the good
        # ones would look like a valid backup to anyone reading the directory.
        fd, tmp_path = tempfile.mkstemp(dir=DEST, suffix=".tmp")
        os.close(fd)
        tmp_db = Path(tmp_path)

        # mode=ro so a bug here can never write to the live database.
        source = sqlite3.connect(f"file:{src}?mode=ro", uri=True, timeout=60)
        try:
            target = sqlite3.connect(str(tmp_db))
            try:
                source.backup(target)          # consistent under concurrent writers
            finally:
                target.close()
        finally:
            source.close()

        # Verify BEFORE this snapshot is allowed to count toward retention.
        check = sqlite3.connect(f"file:{tmp_db}?mode=ro", uri=True)
        try:
            result = check.execute("PRAGMA integrity_check").fetchone()[0]
        finally:
            check.close()
        if result != "ok":
            log(f"  ✗ {name}: integrity_check returned {result!r} — DISCARDED")
            return False

        # Compress to a .part that the prune glob cannot see, then rename.
        # os.replace is atomic within a filesystem, so a reader never observes a
        # partial artifact and retention can never count one.
        part = final.with_suffix(final.suffix + ".part")
        with tmp_db.open("rb") as fin, gzip.open(part, "wb", compresslevel=6) as fout:
            shutil.copyfileobj(fin, fout)
        os.replace(part, final)

        raw = tmp_db.stat().st_size
        gz = final.stat().st_size
        log(f"  ✓ {name}: {raw/1e6:.1f}MB -> {gz/1e6:.1f}MB gz (integrity ok)")
        return True
    except BaseException as exc:
        # BaseException, not Exception: SIGTERM is delivered as KeyboardInterrupt
        # via the handler installed in main(), and Exception would not catch it —
        # leaving exactly the orphans this block exists to remove.
        log(f"  ✗ {name}: {type(exc).__name__}: {exc}")
        for stray in (final, final.with_suffix(final.suffix + ".part")):
            if stray.exists():
                try:
                    stray.unlink()
                except OSError:
                    pass
        if isinstance(exc, (KeyboardInterrupt, SystemExit)):
            raise
        return False
    finally:
        # The snapshot inherits journal_mode=wal from the source, so opening it
        # for the integrity check creates -wal and -shm sidecars. Removing only
        # the main file leaves those behind to accumulate on every run.
        if tmp_db is not None:
            for suffix in ("", "-wal", "-shm"):
                stray = Path(str(tmp_db) + suffix)
                if stray.exists():
                    try:
                        stray.unlink()
                    except OSError:
                        pass


def prune(name: str) -> None:
    """Keep the newest KEEP DAYS of snapshots for this database.

    Counting files meant every ad-hoc run consumed a retention slot: two manual
    runs on install day already held 2 of 7. Grouping by the YYYYMMDD in the
    stamp makes retention mean days, so testing cannot destroy history.
    """
    by_day: dict[str, list[pathlib.Path]] = {}
    for f in DEST.glob(f"{name}.*.gz"):
        parts = f.name.split(".")
        day = next((p[:8] for p in parts if len(p) >= 8 and p[:8].isdigit()), "")
        by_day.setdefault(day, []).append(f)
    keep_days = sorted(by_day, reverse=True)[:KEEP]
    files = [f for day, group in by_day.items() if day not in keep_days for f in group]
    for old in files:
        try:
            old.unlink()
            log(f"  · pruned {old.name}")
        except OSError as exc:
            log(f"  · prune failed for {old.name}: {exc}")


def main() -> int:
    # These artifacts contain full session history. 0600, not the login umask.
    os.umask(0o077)
    # Turn SIGTERM into an exception so `finally` blocks run and no truncated
    # artifact or 206MB orphan survives a timeout, reboot, or `systemctl stop`.
    def _die(signum, _frame):
        raise KeyboardInterrupt(f"signal {signum}")
    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            signal.signal(sig, _die)
        except (ValueError, OSError):
            pass
    DEST.mkdir(parents=True, exist_ok=True)
    # Sweep orphans from any previously-killed run before doing anything else.
    for stray in list(DEST.glob("*.tmp*")) + list(DEST.glob("*.part")):
        try:
            stray.unlink()
            log(f"  · swept orphan {stray.name}")
        except OSError:
            pass
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    log(f"snapshot start (keep={KEEP}, dest={DEST})")

    started = time.monotonic()
    ok = failed = missing = 0
    for rel in DATABASES:
        src = HERMES / rel
        if not src.exists():
            missing += 1
            continue
        if snapshot_one(src, stamp):
            ok += 1
            prune(str(rel).replace("/", "_"))
        else:
            failed += 1

    total = 0
    for f in DEST.glob("*.gz"):
        try:
            total += f.stat().st_size
        except OSError:
            pass  # pruned concurrently; not worth failing the run over
    log(f"snapshot done: {ok} ok, {failed} failed, {missing} absent, "
        f"{total/1e6:.0f}MB retained, {time.monotonic()-started:.1f}s")

    # Non-zero exit drives the systemd OnFailure alert. `ok == 0` must be a
    # failure too: with a wrong HERMES_HOME or renamed DB paths this reported
    # "0 ok, 0 failed, 6 absent" and exited 0 — a backup job that silently
    # backs up nothing is worse than one that crashes.
    if failed or ok == 0:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
