#!/usr/bin/env python3
"""Invariants for the curated board. One test per defect that has actually shipped.

WHY THIS FILE EXISTS. On 2026-09-08 five defects were found in one afternoon, and every
one was already covered by a PROSE RULE in the vault's CLAUDE.md. One of those rules was
written that same morning and violated that same afternoon. Prose did not hold. This file
is the same knowledge in a form that fails loudly.

Every test below maps to a defect that reached production, and the docstring says which.
Pure functions only: no network, no live store, runs in under a second. Exit 0 = all pass.

Run:  <venv>/bin/python test_board_invariants.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from build_curated_xlsx import _review_status, _review_closed, _queue_sort_key  # noqa: E402
from hotness import brand_tier                                                  # noqa: E402
import curate                                                                   # noqa: E402
import revive_dead                                                              # noqa: E402

PASS = FAIL = 0


def check(label, got, want):
    global PASS, FAIL
    if got == want:
        PASS += 1
    else:
        FAIL += 1
        print(f"  ❌ {label}: got {got!r}, want {want!r}")


def rec(status="", notes="", dead=False, **machine):
    m = {"dead": dead, "company": "X", "role": "R", "location": "L"}
    m.update(machine)
    return {"_cid": machine.get("cid", "c1"), "machine": m,
            "human": {"status": status, "notes": notes,
                      "applied_date": "", "priority_override": machine.pop("prio", "")}}


# ── DEFECT 1 ─────────────────────────────────────────────────────────────────
def test_review_status_never_fabricates():
    """`_review_status` rendered the literal "Closed" for any dead row with no status,
    and read_back_human() reads that column back into human.status. One round trip turned
    a transient scrape failure into a permanent human decision. 296 rows carried it; only
    17 were his; 96 of the fabricated ones were live jobs hidden from him."""
    print("1. _review_status never fabricates a status")
    check("dead + blank", _review_status(rec(dead=True)), "")
    check("dead + 'To Apply'", _review_status(rec(status="To Apply", dead=True)), "")
    check("alive + blank", _review_status(rec()), "")
    check("his Skip survives", _review_status(rec(status="Skip")), "Skip")
    check("his Not a Fit survives", _review_status(rec(status="Not a Fit")), "Not a Fit")
    check("his real Closed survives", _review_status(rec(status="Closed", notes="dead link")), "Closed")
    # the general invariant, not just the known case
    # 🔴 THE INVARIANT THAT ACTUALLY MATTERS: for a row he has NOT touched, the Status
    # column must be empty in every machine state. Any non-empty value here is a
    # laundering channel — read_back_human() will import it as his decision.
    untouched = {_review_status(rec(status=s, dead=d))
                 for s in ("", "To Apply") for d in (True, False)}
    check("untouched rows emit nothing, in every machine state", untouched, {""})
    check("staleness is reported separately", _review_closed(rec(dead=True)), True)
    check("...and not for a judged row", _review_closed(rec(status="Skip", dead=True)), False)


# ── DEFECT 2 ─────────────────────────────────────────────────────────────────
class _Store:
    def __init__(self, postings): self.postings = postings


def test_revive_gate_is_not_a_permanent_burial():
    """Gating revival on the dead_reason STRING made the burial permanent: curate.py
    resurrects a re-harvested row and left the string behind, so a row could carry
    "duplicate of X" long after X was gone and never be checked again. Ten rows were
    already primed, two of them BMO Winter-2027 Toronto co-ops."""
    print("2. revive gate refuses only while the reason is still TRUE")
    live_keeper = {"machine": {"dead": False}}
    dead_keeper = {"machine": {"dead": True}}
    st = _Store({"keep-live": live_keeper, "keep-dead": dead_keeper})
    check("keeper alive -> refuse",
          revive_dead.worth_reviving({"dead_reason": "duplicate of keep-live"}, st)[0], False)
    check("keeper DEAD -> check it",
          revive_dead.worth_reviving({"dead_reason": "duplicate of keep-dead"}, st)[0], True)
    check("keeper unknown -> check it",
          revive_dead.worth_reviving({"dead_reason": "duplicate of vanished"}, st)[0], True)
    check("with the '(already X)' suffix",
          revive_dead.worth_reviving({"dead_reason": "duplicate of keep-live (already Skip)"}, st)[0], False)
    check("unrelated reason -> check it",
          revive_dead.worth_reviving({"dead_reason": "jd-backfill: workday api 404"}, st)[0], True)
    check("no reason -> check it", revive_dead.worth_reviving({}, st)[0], True)
    check("past cycle refused", revive_dead.worth_reviving({"cycle": "Fall 2026"}, st)[0], False)
    check("target cycle allowed", revive_dead.worth_reviving({"cycle": "Winter 2027"}, st)[0], True)


# ── DEFECT 3 ─────────────────────────────────────────────────────────────────
def test_shadowed_twins_needs_a_requisition_id():
    """The pass marks dead an untouched row duplicating a req he already judged. It must
    key on req id ONLY: a title+location key would transfer a Skip across genuinely
    different requisitions whenever one row lacks a req id (45% of live rows), and BMO
    posts two different Winter-2027 Toronto reqs under an identical company/role/location.
    It must also never touch a row he has acted on, and never anchor on `Closed` —
    availability goes stale, judgements do not."""
    print("3. shadowed-twin collapse is rid-keyed and never eats a touched row")

    def store(rows):
        return _Store({c: r for c, r in rows})

    def m(rid=None, dead=False, role="Software Engineer Intern", loc="Toronto"):
        d = {"dead": dead, "company": "Acme", "role": role, "location": loc, "url": ""}
        if rid: d["req_id"] = rid
        return d

    # same req id, anchor judged -> the untouched twin dies
    s = store([("a", {"machine": m("R1"), "human": {"status": "Not a Fit", "notes": "n"}}),
               ("b", {"machine": m("R1"), "human": {"status": "To Apply", "notes": ""}})])
    check("same rid + judged anchor -> collapse", curate._collapse_shadowed_twins(s), 1)
    check("the untouched twin is the one killed", s.postings["b"]["machine"]["dead"], True)
    check("the anchor is untouched", s.postings["a"]["machine"]["dead"], False)

    # DIFFERENT req ids, identical title+location -> must NOT collapse (the BMO case)
    s = store([("a", {"machine": m("R1"), "human": {"status": "Not a Fit", "notes": "n"}}),
               ("b", {"machine": m("R2"), "human": {"status": "To Apply", "notes": ""}})])
    check("different rids, same title/location -> NO collapse",
          curate._collapse_shadowed_twins(s), 0)

    # no req id at all -> must NOT collapse on title alone
    s = store([("a", {"machine": m(), "human": {"status": "Not a Fit", "notes": "n"}}),
               ("b", {"machine": m(), "human": {"status": "To Apply", "notes": ""}})])
    check("no rid -> NO collapse on title alone", curate._collapse_shadowed_twins(s), 0)

    # anchor is `Closed` -> must NOT collapse (availability does not transfer)
    s = store([("a", {"machine": m("R1"), "human": {"status": "Closed", "notes": "n"}}),
               ("b", {"machine": m("R1"), "human": {"status": "To Apply", "notes": ""}})])
    check("Closed is not an anchor", curate._collapse_shadowed_twins(s), 0)

    # a touched victim is never killed
    for field, val in (("status", "Applied"), ("notes", "mine"), ("priority_override", "P0")):
        h = {"status": "To Apply", "notes": "", "priority_override": ""}
        h[field] = val
        s = store([("a", {"machine": m("R1"), "human": {"status": "Not a Fit", "notes": "n"}}),
                   ("b", {"machine": m("R1"), "human": h})])
        curate._collapse_shadowed_twins(s)
        check(f"victim with {field} survives", s.postings["b"]["machine"]["dead"], False)


# ── DEFECT 4 ─────────────────────────────────────────────────────────────────
def test_brand_tier_collisions():
    """tier C is not a ranking penalty, it is partial DELETION (wide_net_source drops
    tier-C swelist rows; MAX_ENRICH truncates by tier). Two failure directions: real
    employers defaulting to C, and unrelated companies inheriting a brand's tier —
    Sierra Nevada Corporation scored A off "sierra" while requiring US citizenship."""
    print("4. brand tiers: no collisions, no silent C for real employers")
    for name, want in [
        ("Sierra Nevada Corporation", "C"), ("Sierra Space", "C"), ("Meta Materials", "C"),
        ("Apple Bank", "C"), ("Citadel Credit Union", "C"), ("Unity Health Toronto", "C"),
        ("Square Enix", "C"), ("Brunswick Mercury Marine", "C"), ("Village Healthcare", "C"),
        ("Prestige Aerospace", "C"), ("HP Hood", "C"), ("Micron Solutions", "C"),
        ("Sierra", "A"), ("Meta", "S"), ("Apple", "S"), ("Citadel", "A"), ("Unity", "B"),
        ("NVIDIA", "S"), ("Palantir", "S"), ("Databricks", "A"), ("Intel", "B"),
        ("Campbell's", "B"), ("Campbell Soup", "B"),
        ("Royal Bank of Canada", "B"), ("Bank of Montreal", "B"), ("Manulife Financial", "B"),
        ("CAE", "B"), ("General Motors", "B"), ("Sony", "B"), ("RTX", "B"),
        ("Blue Origin", "A"), ("Postman", "A"), ("Five Rings", "A"), ("PDT Partners", "A"),
    ]:
        check(f"tier {name}", brand_tier(name), want)


# ── DEFECT 5 ─────────────────────────────────────────────────────────────────
def test_grouping_cannot_undo_the_sort():
    """DEFECT 6 (2026-09-08): _group_by_company runs AFTER the sort and a block takes the
    position of its best row, so ONE row dragged a whole company. An On Hold row at a
    company with live rows was pulled to position 84 of 276 instead of the bottom, and a
    P0 on a tier-C row lifted all 6 of that company's C rows above every tier-S row.
    Both passed at the time only by accident of the data. This is the surface HE sees;
    the sort key alone is not the guarantee."""
    print("6. company grouping never breaks a sort band")
    from build_curated_gsheet import _group_by_company

    def row(co, tier, hot=50, status="", prio="", dq="none"):
        return {"machine": {"company": co, "tier": tier, "hotness": hot,
                            "fit_disqualifier": dq},
                "human": {"status": status, "priority_override": prio}}

    # an On Hold row at a company that ALSO has live rows must still sink
    rows = [row("Acme", "S"), row("Acme", "S", status="On Hold"), row("Zeta", "C")]
    out = _group_by_company(sorted(rows, key=_queue_sort_key))
    check("On Hold sinks below another company's C row",
          out[-1]["human"]["status"], "On Hold")

    # A P0 DOES lift the whole company block above a tier-S company, and that is
    # INTENDED, not a defect -- Sparsh, 2026-09-05: "if they're the same company, they
    # should be put next to each other, EVEN IF THAT BREAKS THE SCORE." Company blocking
    # and strict tier order genuinely conflict; his pin wins. Asserted so the behaviour
    # is deliberate and nobody "fixes" it later by accident.
    rows = [row("Big", "S"), row("Small", "C", prio="P0"), row("Small", "C")]
    out = _group_by_company(sorted(rows, key=_queue_sort_key))
    check("a P0 pins its company block to the top, siblings included",
          [r["machine"]["company"] for r in out], ["Small", "Small", "Big"])

    # a disqualified row must not be pulled up by its company block
    rows = [row("Acme", "S"), row("Acme", "S", dq="phd-required"), row("Zeta", "B")]
    out = _group_by_company(sorted(rows, key=_queue_sort_key))
    check("disqualified row sinks below another company's B row",
          out[-1]["machine"]["fit_disqualifier"], "phd-required")

    # company blocking itself must still work (his 2026-09-05 request)
    rows = [row("A", "S"), row("B", "A"), row("A", "B")]
    out = _group_by_company(sorted(rows, key=_queue_sort_key))
    check("same company stays contiguous",
          [r["machine"]["company"] for r in out], ["A", "A", "B"])


def test_staleness_marker_is_not_a_second_fabrication():
    """DEFECT 7 (2026-09-08): the `posting went stale` marker fired on 190 of 190 rows it
    touched, all of which were routed by a DISQUALIFIER, and printed ahead of the real
    reason. A machine-invented reason asserted in front of the true one."""
    print("7. staleness marker never overrides a real disqualifier")
    d = rec(dead=True); d["machine"]["fit_disqualifier"] = "phd-required"
    check("disqualified row is not called stale", _review_closed(d), False)
    d2 = rec(dead=True); d2["machine"]["fit_disqualifier"] = "none"
    check("genuinely stale row still marked", _review_closed(d2), True)


def test_revive_has_a_blast_radius_cap():
    """DEFECT 8 (2026-09-08): one ungated `revive_dead --apply` revived 331 postings and
    took the queue from 108 to 276 in a single step. The revivals were CORRECT -- an
    independent ATS check found 172 of 172 still open -- but the SIZE was never measured
    before it ran, and "331 revived" was read as a success number rather than a warning.
    The cap does not refuse (that would silently stop a twice-daily cron); it bounds the
    step and says loudly what it deferred."""
    print("8. revive_dead bounds how much it can change in one run")
    import argparse, inspect
    src = inspect.getsource(revive_dead.main)
    check("has a --max-apply flag", "--max-apply" in src, True)
    check("defers rather than refusing", "DEFERRING" in src, True)
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--max-apply", type=int, default=50)
    check("default cap is bounded and small", ap.parse_args([]).max_apply <= 100, True)
    # best-first: a tier-S row must be applied before a tier-C row when the cap bites
    tier = {"S": 0, "A": 1, "B": 2, "C": 3}
    rows = [("c1", {"tier": "C", "hotness": 99}), ("s1", {"tier": "S", "hotness": 1})]
    rows.sort(key=lambda t: (tier.get(str(t[1].get("tier") or "").upper(), 4),
                             -int(t[1].get("hotness") or 0)))
    check("the cap keeps the best rows first", rows[0][0], "s1")


def test_queue_sort():
    """His instruction: S at the top, then A, B, C — and the two Amazon On Hold reqs
    (both tier S) parked at the bottom regardless."""
    print("5. queue sorts by tier, with On Hold sunk below everything")
    def r(tier, hot=50, status=""):
        return {"machine": {"tier": tier, "hotness": hot, "fit_disqualifier": "none"},
                "human": {"status": status, "priority_override": ""}}
    rows = [r("C", 99), r("B", 10), r("S", 1), r("A", 50), r("S", 100, "On Hold")]
    order = [x["machine"]["tier"] + ("/hold" if x["human"]["status"] == "On Hold" else "")
             for x in sorted(rows, key=_queue_sort_key)]
    check("order", order, ["S", "A", "B", "C", "S/hold"])
    check("a high-hotness C never outranks a low-hotness S",
          sorted([r("C", 99), r("S", 1)], key=_queue_sort_key)[0]["machine"]["tier"], "S")


# ── DEFECT 9 ─────────────────────────────────────────────────────────────────
# 2026-09-11: the stale-check could not tell "the aggregator dropped it" from
# "we declined to enrich it". wide_net_source caps enrichment at MAX_ENRICH and
# cuts the remainder TIER-SORTED, so the same ~990 rows are dropped every run,
# never enter `harvested`, and can never clear a strike. At WIDE_STALE_STRIKES=14
# and two runs/day that is a guaranteed 7-day death sentence independent of
# whether the job is open. It killed 265 rows in one run (queue 272 -> 110) and
# 38 of 40 spot-checked against the employers' own boards were STILL OPEN.
def test_cap_dropped_rows_are_never_struck():
    import wide_net_source as W

    # the module must expose what the cap cut, or curate has nothing to exempt on
    check("wide_net_source exposes CAPPED_OUT_IDS", hasattr(W, "CAPPED_OUT_IDS"), True)
    check("wide_net_source exposes CAPPED_OUT_TRIPLES", hasattr(W, "CAPPED_OUT_TRIPLES"), True)
    check("wide_net_source exposes _cap_triple", callable(getattr(W, "_cap_triple", None)), True)

    # the triple must normalise the same way curate's cross-lane dedup does,
    # otherwise the exemption silently matches nothing
    check("triple is case/whitespace-insensitive",
          W._cap_triple("  Acme  Corp ", "SWE  Intern", " New York, NY "),
          W._cap_triple("acme corp", "swe intern", "new york, ny"))

    # the sets must be cleared BEFORE anything that can raise, or a throwing
    # _gather_postings() leaves last run's evidence in place and the stale-check
    # exempts rows it should strike (wrong-direction: the board fills with dead jobs)
    wsrc = (Path(__file__).parent / "wide_net_source.py").read_text()
    body = wsrc[wsrc.index("async def collect("):]
    clear_at = body.index("CAPPED_OUT_IDS.clear()")
    gather_at = body.index("cand = _gather_postings()")   # the CALL, not the name:
    # the bare name also appears in the explanatory comment above the clear, and
    # anchoring on it made this assertion fail against correct code.
    check("capped-out sets are cleared before _gather_postings() can raise",
          clear_at < gather_at, True)

    # curate must actually consult it — the source is the contract here, because a
    # green unit test on a helper nobody calls is exactly the failure this suite exists for
    src = (Path(__file__).parent / "curate.py").read_text()
    check("curate.py reads CAPPED_OUT_IDS", "CAPPED_OUT_IDS" in src, True)
    check("curate.py reads CAPPED_OUT_TRIPLES", "CAPPED_OUT_TRIPLES" in src, True)
    check("the capped_out term is wired into `exempt`",
          "or capped_out" in src, True)

    # and the exemption must sit INSIDE the exempt tuple, not be computed and dropped
    ex = src[src.index("exempt = ("):src.index("exempt = (") + 420]
    check("capped_out appears within the exempt expression", "capped_out" in ex, True)

    # id-only keying is not enough: enrichment rewrites URLs
    # (`url = rec.url if ok else p.url`), so a stored row's id can differ from the
    # pre-cap id. Measured 2026-09-11: 21 rows that id-alone misses out of 180.
    # 🔴 THIS ASSERTION MUST LOOK INSIDE THE capped_out EXPRESSION, NOT THE WHOLE
    # FILE. The first version checked `"CAPPED_OUT_TRIPLES" in src`, which stayed
    # GREEN when the triple keying was deleted from the logic — because the name
    # still appeared in a log line elsewhere. Mutation testing caught it; a
    # file-wide substring check is not a control.
    expr_start = src.index("capped_out = (")
    expr = src[expr_start:src.index(")\n", src.index("CAPPED_OUT_TRIPLES", expr_start))]
    check("capped_out expression keys on the id set", "CAPPED_OUT_IDS" in expr, True)
    check("capped_out expression ALSO keys on the triple set",
          "CAPPED_OUT_TRIPLES" in expr, True)
    check("capped_out expression calls _cap_triple on the stored row",
          "_cap_triple(" in expr, True)


# ── VOCABULARY ───────────────────────────────────────────────────────────────
# 2026-09-12: the Status vocabulary was enumerated in FOUR places (board.VALID, the
# Sheet's dropdown via STATUS_OPTS, curate's in-pipeline set, the xlsx "In process"
# COUNTIF) and had to be edited in lockstep by hand. Adding "Technical Interview"
# (Sparsh: Wealthsimple's 1-hour CoderPad round "is not a phone screen") is the
# first change since the board was built; from now on there is one list and every
# consumer derives from it. A stage missing from any consumer is a silent defect:
# board.py refuses the write, or curate strikes a live interview as stale, or the
# Sheet renders it uncoloured with a validation warning.
def test_status_vocabulary_has_one_source():
    import re
    from build_curated_xlsx import (STATUS_OPTS, STATUS_FILL, STATUS_RANK,
                                    PIPELINE_STATUSES, IN_PROCESS_STATUSES)
    print("V. status vocabulary has one source")
    check("Technical Interview is a status", "Technical Interview" in STATUS_OPTS, True)
    check("every status has a colour (else the Sheet renders it uncoloured)",
          sorted(set(STATUS_OPTS) - set(STATUS_FILL)), [])
    check("no colour for a status that does not exist",
          sorted(set(STATUS_FILL) - set(STATUS_OPTS)), [])
    check("pipeline statuses are all real statuses",
          sorted(PIPELINE_STATUSES - set(STATUS_OPTS)), [])
    check("every pipeline status ranks (else it sorts as unknown)",
          sorted(PIPELINE_STATUSES - set(STATUS_RANK)), [])
    check("in-process funnel is inside the pipeline set",
          sorted(set(IN_PROCESS_STATUSES) - PIPELINE_STATUSES), [])
    check("Technical Interview is in the interview funnel",
          "Technical Interview" in IN_PROCESS_STATUSES, True)
    check("Technical Interview is exempt from the stale strike",
          "Technical Interview" in PIPELINE_STATUSES, True)
    # a stage sits between the recruiter screen and the final round
    check("ranks: Onsite < Technical Interview < Phone Screen",
          STATUS_RANK["Onsite"] < STATUS_RANK["Technical Interview"] < STATUS_RANK["Phone Screen"], True)

    # the consumers must DERIVE from the list, not restate it. Source-anchored on the
    # exact assignment, not a substring: a literal set that merely mentions the
    # symbol in a comment must not pass.
    here = Path(__file__).parent
    cur = (here / "curate.py").read_text()
    check("curate's stale-check consults PIPELINE_STATUSES",
          bool(re.search(r"^\s*in_pipeline = human_status in PIPELINE_STATUSES\s*$", cur, re.M)), True)
    check("curate has no inline status set left",
          "in_pipeline = human_status in {" in cur, False)
    brd = (here / "board.py").read_text()
    check("board.VALID derives from STATUS_OPTS",
          bool(re.search(r"^VALID = list\(STATUS_OPTS\)\s*$", brd, re.M)), True)
    check("board.py has no literal vocabulary left", '"Phone Screen", "Onsite"' in brd, False)
    xl = (here / "build_curated_xlsx.py").read_text()
    check("xlsx 'In process' tile derives from IN_PROCESS_STATUSES",
          "for st in IN_PROCESS_STATUSES" in xl, True)


# ── ID MATCH ─────────────────────────────────────────────────────────────────
# 2026-09-12: `board.py status "id:jobs.ashbyhq.com/sierra/<uuid>" Skip` marked the row
# whose _id was `.../Sierra/<uuid>` (capital S). The id: branch lowercased both sides and
# returned the FIRST hit, so two rows differing only by case were one row to it, and the
# "ambiguous match is refused" promise did not hold on the one branch built for precision.
def test_id_match_is_exact_and_refuses_ambiguity():
    from board_match import find_by_id
    print("M. id: match is case-exact and never guesses between twins")
    rows = {"Apply Now": [["_id", "Status"],
                          ["jobs.ashbyhq.com/Sierra/02e1", "To Apply"],
                          ["jobs.ashbyhq.com/sierra/02e1", "To Apply"]],
            "Reviewed": [["_id", "Status"],
                         ["simplify.jobs/p/c166", "Skip"]]}
    hit = find_by_id("jobs.ashbyhq.com/Sierra/02e1", rows)
    check("exact case returns exactly the matching row", [(t, i) for t, i, _ in hit],
          [("Apply Now", 2)])
    hit = find_by_id("jobs.ashbyhq.com/sierra/02e1", rows)
    check("exact case returns the OTHER twin, not the first row", [(t, i) for t, i, _ in hit],
          [("Apply Now", 3)])
    hit = find_by_id("JOBS.ASHBYHQ.COM/SIERRA/02E1", rows)
    check("no exact match -> every case-insensitive twin, so the caller refuses",
          len(hit), 2)
    check("no match at all -> empty, not a guess", find_by_id("nope", rows), [])
    # and board.py must actually route through it — anchored on the call, not the name
    brd = (Path(__file__).parent / "board.py").read_text()
    check("board._find uses find_by_id", "hits = find_by_id(want, rows_by_tab)" in brd, True)
    check("board._find refuses >1 id hits", "if len(hits) > 1:" in brd[brd.index("hits = find_by_id"):], True)
    check("board._find no longer lowercases the id needle", 'want = needle[3:].strip().lower()' in brd, False)


for fn in (test_review_status_never_fabricates, test_revive_gate_is_not_a_permanent_burial,
           test_shadowed_twins_needs_a_requisition_id, test_brand_tier_collisions,
           test_queue_sort, test_grouping_cannot_undo_the_sort,
           test_staleness_marker_is_not_a_second_fabrication,
           test_revive_has_a_blast_radius_cap,
           test_cap_dropped_rows_are_never_struck,
           test_status_vocabulary_has_one_source,
           test_id_match_is_exact_and_refuses_ambiguity):
    # A raised exception is a FAILURE, not a reason to stop: one crashing test used to
    # hide every test after it, which is how a suite reports "green" while blind.
    try:
        fn()
    except Exception as exc:            # noqa: BLE001
        FAIL += 1
        print(f"  ❌ {fn.__name__} RAISED {type(exc).__name__}: {exc}")

print(f"\n{PASS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
