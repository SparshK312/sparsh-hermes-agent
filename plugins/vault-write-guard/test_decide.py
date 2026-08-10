#!/usr/bin/env python3
"""Unit test for vault-write-guard.decide() — the block/allow decision logic.

The guard intercepts the live agent loop, so its decisions must be exactly right:
block the health-file hand-writes that corrupt logging, but NEVER block the morning
prefill (daily-note creation), mood/notes edits, terminal/vault_log, or reads.

  python test_decide.py     # exit 0 = all pass
"""
import importlib.util
import sys
from pathlib import Path

spec = importlib.util.spec_from_file_location("vwg", Path(__file__).resolve().parent / "__init__.py")
g = importlib.util.module_from_spec(spec)
spec.loader.exec_module(g)

CASES = [
    # (name, tool, args, expect_block)
    ("patch daily-note macro (the thrash)", "patch",
     {"file_path": "04 - Daily Notes/2026-06-23.md", "old_string": "kcal: 0", "new_string": "kcal: 500"}, True),
    ("patch daily-note abs path protein", "patch",
     {"file_path": "/home/hermes/vault/04 - Daily Notes/2026-06-23.md", "old_string": "protein_g: 0", "new_string": "protein_g: 40"}, True),
    ("write_file a food log", "write_file",
     {"file_path": "07 - Health/Food Log/2026-06-23.md", "content": "## meal"}, True),
    ("patch a workout file", "patch",
     {"file_path": "07 - Health/Workouts/2026-06-23.md", "old_string": "a", "new_string": "b"}, True),
    ("execute_code writing daily-note macro", "execute_code",
     {"code": "open('04 - Daily Notes/2026-06-23.md','a').write('kcal: 5')"}, True),
    # ---- MUST ALLOW ----
    ("PREFILL: write_file create daily note (empty health fields)", "write_file",
     {"file_path": "04 - Daily Notes/2026-06-24.md", "content": "---\ndate: x\nkcal: \nprotein_g: \nwater_l: \n---\n## Schedule\n## Notes"}, False),
    ("patch daily-note Notes body (no health key)", "patch",
     {"file_path": "04 - Daily Notes/2026-06-23.md", "old_string": "## Notes\nfoo", "new_string": "## Notes\nbar"}, False),
    ("patch daily-note mood (not a guarded key)", "patch",
     {"file_path": "04 - Daily Notes/2026-06-23.md", "old_string": "mood: ", "new_string": "mood: 7"}, False),
    ("PREFILL: patch Schedule section", "patch",
     {"file_path": "04 - Daily Notes/2026-06-24.md", "old_string": "## Schedule", "new_string": "## Schedule\n- 9am meeting"}, False),
    ("terminal running vault_log (the good path)", "terminal",
     {"command": "python3 /home/hermes/.hermes/scripts/vault/vault_log.py food --kcal 500"}, False),
    ("read_file a daily note", "read_file", {"file_path": "04 - Daily Notes/2026-06-23.md"}, False),
    ("write_file a non-health doc", "write_file",
     {"file_path": "00 - Dashboard/Notes.md", "content": "kcal: in prose"}, False),
    ("patch a random code file", "patch", {"file_path": "/tmp/foo.py", "old_string": "x", "new_string": "y"}, False),
]


# Tier 2 — needs_approval(): destructive rewrites of high-value docs must ASK first,
# additive edits must pass silently. (name, tool, args, expect_approval)
_BIG = "x" * 400          # a chunk of existing content being removed
_SMALL = "y" * 40

APPROVE_CASES = [
    # ---- MUST ASK (destructive) ----
    ("rewrite Action Items section (the Peru incident)", "patch",
     {"file_path": "00 - Dashboard/Action Items.md", "old_string": _BIG, "new_string": _SMALL}, True),
    ("overwrite a Perfecti draft (the David incident)", "patch",
     {"file_path": "/home/hermes/vault/06 - Internships/Perfecti/Engineering/David Wind-Down Message - Jul 18.md",
      "old_string": _BIG, "new_string": ""}, True),
    ("write_file over a dashboard doc", "write_file",
     {"file_path": "00 - Dashboard/Life Context.md", "content": "replaced"}, True),
    ("execute_code against internships dir", "execute_code",
     {"code": "open('06 - Internships/x.md','w').write('gone')"}, True),
    # ---- MUST PASS SILENTLY (additive / harmless) ----
    ("append a task to Action Items", "patch",
     {"file_path": "00 - Dashboard/Action Items.md", "old_string": "## Tasks", "new_string": "## Tasks\n- [ ] new thing"}, False),
    ("tick a checkbox (same-length swap)", "patch",
     {"file_path": "00 - Dashboard/Interview Prep.md", "old_string": "- [ ] rep 12", "new_string": "- [x] rep 12"}, False),
    ("small typo fix in a dashboard", "patch",
     {"file_path": "00 - Dashboard/Action Items.md", "old_string": "teh", "new_string": "the"}, False),
    # ---- MUST NOT ASK (outside the protected tier) ----
    ("big rewrite of course notes (not protected)", "patch",
     {"file_path": "01 - Courses/CSC384/notes.md", "old_string": _BIG, "new_string": ""}, False),
    ("daily-note rewrite stays out (prefill cron must not prompt)", "patch",
     {"file_path": "04 - Daily Notes/2026-07-22.md", "old_string": _BIG, "new_string": ""}, False),
    ("coach-memory rewrite stays out (refresh cron must not prompt)", "patch",
     {"file_path": "07 - Health/Coach Memory.md", "old_string": _BIG, "new_string": ""}, False),
    ("read_file never asks", "read_file",
     {"file_path": "00 - Dashboard/Action Items.md"}, False),
    ("terminal never asks", "terminal", {"command": "cat '00 - Dashboard/Action Items.md'"}, False),
    # ---- Tier 3: ADDITIVE outside-world claims (the Zero2Sudo incident) ----
    ("the real 2026-07-30 fabrication (3rd-party IG story -> pipeline)", "patch",
     {"file_path": "00 - Dashboard/Internship Pipeline.md",
      "old_string": "| **Google** | app submitted |",
      "new_string": "| **Google** | INTERVIEW REQUEST Jul 30 | expedited interview request, "
                    "45 min technical via Google Meet |"}, True),
    ("adds an offer claim to internships", "patch",
     {"file_path": "06 - Internships/Applications/Foo.md",
      "old_string": "status: applied", "new_string": "status: offer received"}, True),
    ("adds a rejection claim", "patch",
     {"file_path": "00 - Dashboard/Internship Pipeline.md",
      "old_string": "| Composio | onsite done |", "new_string": "| Composio | rejected Aug 10 |"}, True),
    ("ordinary append to the pipeline still passes", "patch",
     {"file_path": "00 - Dashboard/Internship Pipeline.md",
      "old_string": "| **Mercor** | App pending |",
      "new_string": "| **Mercor** | App pending | pinged the referrer |"}, False),
    ("editing a row whose claim ALREADY existed passes", "patch",
     {"file_path": "00 - Dashboard/Internship Pipeline.md",
      "old_string": "rejected May 28", "new_string": "rejected May 28 (no reason given)"}, False),
    ("same wording outside the career dirs passes", "patch",
     {"file_path": "04 - Daily Notes/2026-08-10.md",
      "old_string": "x", "new_string": "got an interview request today"}, False),
]


def main() -> int:
    ok = True
    print("--- Tier 1: decide() block/allow ---")
    for name, tool, args, exp in CASES:
        blocked = g.decide(tool, args) is not None
        status = "PASS" if blocked == exp else "FAIL"
        if blocked != exp:
            ok = False
        print(f"  [{status}] {'BLOCK' if blocked else 'allow'} (want {'BLOCK' if exp else 'allow'}) — {name}")

    print("\n--- Tier 2: needs_approval() ask/pass ---")
    for name, tool, args, exp in APPROVE_CASES:
        asks = g.needs_approval(tool, args) is not None
        status = "PASS" if asks == exp else "FAIL"
        if asks != exp:
            ok = False
        print(f"  [{status}] {'ASK' if asks else 'pass'} (want {'ASK' if exp else 'pass'}) — {name}")

    print("\n=== ALL PASS ===" if ok else "\n=== SOME FAILED ===")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
