#!/usr/bin/env python3
"""
test_fit_pass.py — the quality gate. Runs the labeled fit_eval_set.json through the
PRODUCTION score_batch() path on a candidate model and prints a PASS/FAIL scorecard.
The fit pass does not ship until this passes.

  python test_fit_pass.py                 # eval FIT_MODEL (default gpt-5.4-mini)
  python test_fit_pass.py --model gpt-5.5 # eval a specific model (escalation)
  python test_fit_pass.py --no-llm        # offline: validate fixture shape only

Acceptance (all must hold):
  1. disqualifier recall >= 8/9 AND citizenship+clearance subset recall == 1.0
  2. zero false-positive disqualifiers on the 10 clean cases (incl. the ITAR decoy)
  3. band sanity: every 'high' >= 70, every 'low' < 40 (no inversion)
  4. JSON validity 100% (every case returns a valid scored item)
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import fit_pass as F

FIXTURE = Path(__file__).resolve().parent / "fit_eval_set.json"
HARD_DQ = {"citizenship", "clearance"}   # missing one of these = wasted application


def main() -> int:
    model = F.FIT_MODEL
    if "--model" in sys.argv:
        model = sys.argv[sys.argv.index("--model") + 1]
    cases = json.loads(FIXTURE.read_text())

    if "--no-llm" in sys.argv:
        bad = [c["id"] for c in cases if len(c.get("jd", "")) < 150 or "expected" not in c]
        print(f"fixture: {len(cases)} cases, {'OK' if not bad else 'BAD: ' + str(bad)}")
        return 1 if bad else 0

    items = [{k: c[k] for k in ("id", "company", "title", "location", "cycle", "jd")} for c in cases]
    print(f"running eval on {model} ({len(items)} cases)…\n")
    results, usage = F.score_batch(items, model)

    print(f"{'id':12} {'exp-dq':14} {'got-dq':14} {'band':5} {'score':>5}  verdict")
    print("-" * 64)
    recall_hit = recall_tot = 0
    hard_miss = []
    false_pos = []
    band_fail = []
    missing = []
    for c in cases:
        cid = c["id"]
        exp_dq = c["expected"]["disqualifier"]
        band = c["expected"]["fit_band"]
        r = results.get(cid)
        if not r:
            missing.append(cid)
            print(f"{cid:12} {exp_dq:14} {'<MISSING>':14} {band:5} {'--':>5}  ✗ no result")
            continue
        got_dq = r["fit_disqualifier"]
        score = r["fit_score"]
        ok = True
        # recall (disqualifier cases)
        if exp_dq != "none":
            recall_tot += 1
            if got_dq != "none":
                recall_hit += 1
            else:
                ok = False
                if exp_dq in HARD_DQ:
                    hard_miss.append(cid)
        # false-positive (clean cases)
        if exp_dq == "none" and got_dq != "none":
            false_pos.append((cid, got_dq))
            ok = False
        # band sanity
        if band == "high" and score < 70:
            band_fail.append((cid, "high<70", score)); ok = False
        if band == "low" and score >= 40:
            band_fail.append((cid, "low>=40", score)); ok = False
        mark = "✓" if ok else "✗"
        note = "" if got_dq == exp_dq else f" (got {got_dq})"
        print(f"{cid:12} {exp_dq:14} {got_dq:14} {band:5} {score:>5}  {mark}{note}")

    # ── metrics ──
    recall = recall_hit / recall_tot if recall_tot else 0
    print("\n" + "=" * 64)
    m1 = recall >= 8 / 9 and not hard_miss
    m2 = not false_pos
    m3 = not band_fail
    m4 = not missing
    print(f"1. disqualifier recall      {recall_hit}/{recall_tot} ({recall:.2f})  "
          f"hard-gate miss={hard_miss or 'none'}   {'PASS' if m1 else 'FAIL'}")
    print(f"2. false-positive disqual.  {len(false_pos)} {false_pos or ''}   {'PASS' if m2 else 'FAIL'}")
    print(f"3. band sanity              {len(band_fail)} fails {band_fail or ''}   {'PASS' if m3 else 'FAIL'}")
    print(f"4. JSON validity            {len(cases)-len(missing)}/{len(cases)}   {'PASS' if m4 else 'FAIL'}")
    cost = F._cost(model, usage)
    print(f"\ncost: ~${cost:.4f}  ({usage.get('prompt_tokens',0)}+{usage.get('completion_tokens',0)} tok)")
    overall = m1 and m2 and m3 and m4
    print("=" * 64)
    if overall:
        print(f"✅ OVERALL PASS — {model} is adequate. Ship it.")
    else:
        print(f"❌ OVERALL FAIL — {model} insufficient. ESCALATE "
              f"(gpt-5.4-mini → gpt-5.5 → claude-haiku-4-5; re-run this on the next model).")
    return 0 if overall else 1


if __name__ == "__main__":
    sys.exit(main())
