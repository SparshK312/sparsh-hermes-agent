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


for fn in (test_review_status_never_fabricates, test_revive_gate_is_not_a_permanent_burial,
           test_shadowed_twins_needs_a_requisition_id, test_brand_tier_collisions,
           test_queue_sort):
    # A raised exception is a FAILURE, not a reason to stop: one crashing test used to
    # hide every test after it, which is how a suite reports "green" while blind.
    try:
        fn()
    except Exception as exc:            # noqa: BLE001
        FAIL += 1
        print(f"  ❌ {fn.__name__} RAISED {type(exc).__name__}: {exc}")

print(f"\n{PASS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
