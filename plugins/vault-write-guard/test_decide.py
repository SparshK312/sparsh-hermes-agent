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


def main() -> int:
    ok = True
    for name, tool, args, exp in CASES:
        blocked = g.decide(tool, args) is not None
        status = "PASS" if blocked == exp else "FAIL"
        if blocked != exp:
            ok = False
        print(f"  [{status}] {'BLOCK' if blocked else 'allow'} (want {'BLOCK' if exp else 'allow'}) — {name}")
    print("\n=== ALL PASS ===" if ok else "\n=== SOME FAILED ===")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
