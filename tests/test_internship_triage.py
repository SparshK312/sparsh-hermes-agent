"""Behavior tests for internship_triage.py — the frontier-triage replacement for
the old internship-watcher agent loop.

Pin the load-bearing logic:
  * rule_entry: builds a postings_seen entry (with the deterministic verdict) in the
    exact shape build_open_opportunities_section() renders.
  * frontier refine MERGES over the rule verdict (LLM wins when present + valid).
  * templated_digest: the offline fallback names the apply-now roles + cover lines.
  * main(): persists (append seen + rebuild pipeline) and sends a digest ONLY when
    something is actionable, and prints ONLY the wake-gate (never wakes an agent).

Imports work without bs4 (the scraper's bs4 import is lazy now) — these tests build
Posting objects directly and monkeypatch the network/file edges.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

INT_DIR = Path(__file__).resolve().parent.parent / "scripts" / "internship"


def _load():
    # internship_triage imports internship_scraper + internship_sources from its dir.
    import sys
    sys.path.insert(0, str(INT_DIR))
    spec = importlib.util.spec_from_file_location("internship_triage", INT_DIR / "internship_triage.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _posting(T, company="Stripe", title="Software Engineer Intern, Fall 2026",
             location="San Francisco, CA", url="https://stripe.com/jobs/x",
             posted="2026-06-14", age=1, terms="Fall 2026", source="simplify-offseason",
             cid=None):
    return T.S.Posting(company=company, title=title, location=location, url=url,
                       posted_date=posted, age_days=age, terms=terms, source=source,
                       canonical_id=cid or url, salary="")


def test_rule_entry_shape_matches_pipeline_renderer(tmp_path):
    T = _load()
    e = T.rule_entry(_posting(T))
    # the fields build_open_opportunities_section() reads:
    for k in ("id", "verdict", "company", "role", "location", "url", "source",
              "posted_date", "age_days"):
        assert k in e
    assert e["company"] == "Stripe" and e["role"].startswith("Software Engineer")
    assert e["verdict"] in T.VALID_VERDICTS


def test_templated_digest_lists_apply_now_with_cover_line(tmp_path):
    T = _load()
    entries = [
        {"verdict": "apply now", "company": "Stripe", "role": "SWE Intern, Fall 2026",
         "location": "SF", "cover_line": "I'm on PEY at Shopify…"},
        {"verdict": "wait", "company": "Ramp", "role": "SWE Intern", "location": "NYC"},
        {"verdict": "consider", "company": "Foo", "role": "ML Intern", "location": "Remote"},
    ]
    out = T.templated_digest(entries)
    assert "1 apply / 1 wait / 1 consider" in out
    assert "Stripe" in out and "I'm on PEY at Shopify" in out
    assert "Ramp" in out


def test_main_refines_persists_and_sends(tmp_path, capsys, monkeypatch):
    T = _load()
    p1 = _posting(T, company="Stripe", cid="stripe.com/x")
    p2 = _posting(T, company="RandoCorp", title="DevOps Intern", cid="rando.com/y")
    monkeypatch.setattr(T, "DRY", False)
    monkeypatch.setattr(T, "collect_new", lambda: ([p1, p2], []))
    # LLM refines: Stripe → apply now (+cover line); leaves p2 to its rule verdict
    monkeypatch.setattr(T, "frontier_triage",
                        lambda new: ({"stripe.com/x": {"verdict": "apply now",
                                                       "reason": "watchlist + recent",
                                                       "cover_line": "Shopify PEY hook"}},
                                     "💼 1 apply now: Stripe"))
    appended = {}
    monkeypatch.setattr(T.S, "append_seen_entries", lambda entries: appended.update({"n": len(entries), "rows": entries}))
    monkeypatch.setattr(T, "all_seen_entries", lambda: [])
    monkeypatch.setattr(T.S, "build_open_opportunities_section", lambda entries: "## section")
    wrote = {}
    monkeypatch.setattr(T.S, "write_open_opportunities_to_pipeline", lambda s: wrote.update({"s": s}))
    sent = []
    monkeypatch.setattr(T, "send_message", lambda t: sent.append(t) or True)

    rc = T.main()
    out = capsys.readouterr().out.strip()
    assert rc == 0
    assert out == T.WAKE_GATE                       # only the gate on stdout → no agent
    assert appended["n"] == 2                        # both new postings persisted
    stripe = next(r for r in appended["rows"] if r["id"] == "stripe.com/x")
    assert stripe["verdict"] == "apply now" and stripe["cover_line"] == "Shopify PEY hook"
    assert wrote.get("s") == "## section"            # pipeline rebuilt
    assert sent == ["💼 1 apply now: Stripe"]        # digest sent (LLM version)


def test_main_silent_when_no_new(tmp_path, capsys, monkeypatch):
    T = _load()
    monkeypatch.setattr(T, "DRY", False)
    monkeypatch.setattr(T, "collect_new", lambda: ([], [("simplify-offseason", "fetch: timeout")]))
    sent = []
    monkeypatch.setattr(T, "send_message", lambda t: sent.append(t) or True)

    rc = T.main()
    assert rc == 0
    assert capsys.readouterr().out.strip() == T.WAKE_GATE
    assert sent == []   # nothing new → no ping
