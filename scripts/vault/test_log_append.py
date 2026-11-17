#!/usr/bin/env python3
"""Invariants for the sharded vault log (log_append.py + log_shard.py). Pure, temp dirs only.

Run:  python3 test_log_append.py     (exit 0 = all pass)
"""
import hashlib
import multiprocessing as mp
import re
import subprocess
import sys
import tempfile
from collections import Counter
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import log_append as L  # noqa: E402

PASS = FAIL = 0


def check(label, got, want):
    global PASS, FAIL
    if got == want:
        PASS += 1
    else:
        FAIL += 1
        print(f"  ❌ {label}: got {got!r}, want {want!r}")


def _vault():
    v = Path(tempfile.mkdtemp())
    (v / "CLAUDE.md").write_text("x")
    (v / "Log.md").write_text(L.stub_text())
    return v


def _worker(args):
    v, i = args
    L.append_entry(Path(v), "2026-10-05",
                   L.format_entry("2026-10-05", "update", f"Scope {i}", f"entry {i}", "line\n" * 50))


def test_format_and_validation():
    print("LA1. header format is enforced")
    e = L.format_entry("2026-10-05", "update", "Curated Board", "did  a\n thing", "body")
    check("header shape", e.splitlines()[0], "## [2026-10-05] update | Curated Board — did a thing")
    for bad in (("2026-10-05", "updated", "s", "x"), ("10/05/2026", "update", "s", "x"),
                ("2026-10-05", "update", " ", "x")):
        try:
            L.format_entry(*bad)
            check(f"rejects {bad}", "accepted", "SystemExit")
        except SystemExit:
            check(f"rejects {bad}", "SystemExit", "SystemExit")


def test_day_file_and_frontmatter():
    print("LA2. entries land in the day file named by their header date, with frontmatter")
    v = _vault()
    p = L.append_entry(v, "2026-09-12", L.format_entry("2026-09-12", "lint", "Vault", "a"))
    check("path", p.relative_to(v).as_posix(), "Log/2026/2026-09/2026-09-12.md")
    t = p.read_text()
    check("frontmatter once", t.count("type: log"), 1)
    L.append_entry(v, "2026-09-12", L.format_entry("2026-09-12", "lint", "Vault", "b"))
    t = p.read_text()
    check("still one frontmatter after a second append", t.count("type: log"), 1)
    check("both entries, in order", re.findall(r"— (\w)$", t, re.M), ["a", "b"])


def test_parallel_appends_never_interleave():
    print("LA3. 40 parallel writers: every entry intact, none interleaved")
    v = _vault()
    with mp.Pool(8) as pool:
        pool.map(_worker, [(str(v), i) for i in range(40)])
    _, entries = L.split_entries(L.day_file(v, "2026-10-05").read_text())
    check("40 entries", len(entries), 40)
    check("frontmatter written exactly once despite the race to create the file",
          L.day_file(v, "2026-10-05").read_text().count("type: log"), 1)
    check("each entry is whole (header + 50 body lines)",
          all(e.count("line\n") == 50 for e in entries), True)
    check("all 40 distinct scopes present",
          sorted(int(re.search(r"Scope (\d+)", e).group(1)) for e in entries), list(range(40)))


def test_sweep_moves_habitual_appends_out_of_the_stub():
    print("LA4. an entry appended to the Log.md stub by habit is swept into its day file")
    v = _vault()
    with open(v / "Log.md", "a") as f:
        f.write("\n## [2026-10-05] update | Habit — appended to the old file\nbody text\n")
    check("sweep moved one", L.sweep(v), 1)
    check("stub restored exactly", (v / "Log.md").read_text(), L.stub_text())
    day = L.day_file(v, "2026-10-05").read_text()
    check("entry now in its day file", "appended to the old file" in day and "body text" in day, True)
    check("second sweep is a no-op", L.sweep(v), 0)
    check("stub's only header is the MOVED sentinel (grep '^## [' sees it, never silence)",
          [l for l in (v / "Log.md").read_text().splitlines() if l.startswith("## [")],
          [L.STUB_SENTINEL])


def test_cli_end_to_end():
    print("LA5. the CLI writes, and refuses a bad action")
    v = _vault()
    r = subprocess.run([sys.executable, str(HERE / "log_append.py"), "decision", "Strategy",
                        "a summary", "--body", "b", "--date", "2026-10-05", "--vault", str(v)],
                       capture_output=True, text=True)
    check("exit 0", r.returncode, 0)
    check("prints the day file", r.stdout.strip().endswith("Log/2026/2026-10/2026-10-05.md"), True)
    r = subprocess.run([sys.executable, str(HERE / "log_append.py"), "updated", "S", "x",
                        "--vault", str(v)], capture_output=True, text=True)
    check("bad action exits non-zero", r.returncode != 0, True)


def test_shard_round_trip_is_content_exact():
    print("LA6. log_shard: every entry preserved byte-for-byte, out-of-order + range headers")
    v = Path(tempfile.mkdtemp())
    (v / "CLAUDE.md").write_text("x")
    mono = ("---\ntype: log\n---\n\n# Vault Log\n\n## Events\n\n"
            "## [2026-09-01] update | A — first\nbody a\n\n"
            "## [2026-09-03] update | B — later\nbody b\n"
            "## [2026-09-02] lint | C — out of order\nbody c\n"
            "## [2026-10-02/03] update | D — range header\nbody d\n"
            "## [2026-09-01] schema | E — same day as A\n")
    (v / "Log.md").write_text(mono)
    _, src_entries = L.split_entries(mono)
    want = Counter(hashlib.sha1(e.rstrip("\n").encode()).hexdigest() for e in src_entries)
    r = subprocess.run([sys.executable, str(HERE / "log_shard.py"), "--vault", str(v)],
                       capture_output=True, text=True)
    check("shard exit 0", r.returncode, 0)
    got_entries = []
    for p in sorted((v / "Log").rglob("????-??-??.md")):
        got_entries += L.split_entries(p.read_text())[1]
    got = Counter(hashlib.sha1(e.rstrip("\n").encode()).hexdigest() for e in got_entries)
    check("multiset of entry hashes identical", got, want)
    d1 = L.split_entries(L.day_file(v, "2026-09-01").read_text())[1]
    check("same-day entries keep original order", [e.split("|")[1].split("—")[0].strip() for e in d1],
          ["A", "E"])
    check("range header goes to its first date", L.day_file(v, "2026-10-02").exists(), True)
    check("preamble preserved in README", "# Vault Log" in (v / "Log" / "README.md").read_text(), True)
    check("Log.md is now the stub", (v / "Log.md").read_text(), L.stub_text())
    r = subprocess.run([sys.executable, str(HERE / "log_shard.py"), "--vault", str(v)],
                       capture_output=True, text=True)
    check("a second run refuses (no entries left / Log/ exists)", r.returncode, 1)


if __name__ == "__main__":
    for fn in (test_format_and_validation, test_day_file_and_frontmatter,
               test_parallel_appends_never_interleave,
               test_sweep_moves_habitual_appends_out_of_the_stub, test_cli_end_to_end,
               test_shard_round_trip_is_content_exact):
        try:
            fn()
        except Exception as exc:  # noqa: BLE001
            FAIL += 1
            print(f"  ❌ {fn.__name__} RAISED {type(exc).__name__}: {exc}")

    print(f"\n{PASS} passed, {FAIL} failed")
    sys.exit(1 if FAIL else 0)
