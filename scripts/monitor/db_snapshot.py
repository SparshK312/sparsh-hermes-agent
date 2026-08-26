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
import shutil
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

        with tmp_db.open("rb") as fin, gzip.open(final, "wb", compresslevel=6) as fout:
            shutil.copyfileobj(fin, fout)

        raw = tmp_db.stat().st_size
        gz = final.stat().st_size
        log(f"  ✓ {name}: {raw/1e6:.1f}MB -> {gz/1e6:.1f}MB gz (integrity ok)")
        return True
    except Exception as exc:
        log(f"  ✗ {name}: {type(exc).__name__}: {exc}")
        if final.exists():
            try:
                final.unlink()
            except OSError:
                pass
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
    """Keep the newest KEEP snapshots for this database."""
    files = sorted(DEST.glob(f"{name}.*.gz"), key=lambda p: p.stat().st_mtime, reverse=True)
    for old in files[KEEP:]:
        try:
            old.unlink()
            log(f"  · pruned {old.name}")
        except OSError as exc:
            log(f"  · prune failed for {old.name}: {exc}")


def main() -> int:
    DEST.mkdir(parents=True, exist_ok=True)
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

    total = sum(f.stat().st_size for f in DEST.glob("*.gz"))
    log(f"snapshot done: {ok} ok, {failed} failed, {missing} absent, "
        f"{total/1e6:.0f}MB retained, {time.monotonic()-started:.1f}s")

    # Non-zero exit drives the systemd OnFailure alert.
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
