#!/usr/bin/env python3
"""
test_fall_watch.py — invariants for the Fall 2027 watcher and the Part C fixes that
shipped with it (2026-11-11). Plain asserts, no network, exits non-zero on any failure.
Wired into scripts/deploy.sh Step 0b, so a red run aborts the deploy.

Every assertion here was mutation-tested when written: break the code on purpose,
watch it go red, restore. A test that has never failed is a comment.

  F1  term matcher: the three real Amazon JD texts match (high / medium / low); the
      Databricks graduation line, "Winter 2028" and "Summer 2027" alone do not
  F2  location gate
  F3  state transitions: new key alerts, false->true flip alerts, a seen key is silent
  F4  source floors: SOURCE DOWN on the 2nd consecutive miss, not the 1st; non-zero exit
  F5  Amazon pager: pages with offset until hits are exhausted, normalized_country_code,
      loud on a failed page / a cap / a non-CAN row
  F6  runners propagate the Python exit code (run_fall_watch.sh, run_hot_watch.sh)
  F7  hot_watch seen-set keeps the NEWEST ids when trimmed; migration loses nothing
  F8  Google results parser
  F9  run(): seed sends nothing, dry-run writes nothing, heartbeat, quiet hours hold
  F5b the Amazon BOARD is pinned global (countries=[]) and sends no country param
  F10 an alert fits Telegram's 4,096-char cap (an oversize one failed forever)
"""
from __future__ import annotations

import asyncio
import io
import json
import os
import re
import subprocess
import sys
import tempfile
import traceback
from contextlib import redirect_stderr
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import ats_router as A  # noqa: E402
import fall_watch as F  # noqa: E402
import hot_watch as H  # noqa: E402

FAILURES: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    if cond:
        print(f"  ok   {name}")
    else:
        print(f"  FAIL {name} {detail}")
        FAILURES.append(name)


# Real JD fragments, verbatim from amazon.jobs search.json on 2026-11-11.
AMZ_10553947 = (
    "Please note that the majority of our openings are for Summer 2027 to meet business "
    "needs; however, there may be additional openings that include the following periods:"
    "<br/>1.Winter 2027 (Start January/February; End April/May)<br/>2.Fall 2027 (Start "
    "August/September; End November/December)<br/><br/>During the application process, "
    "you will be given the opportunity to select a preference")
AMZ_10553947_QUALS = (
    "- Work 40 hours/week minimum and commit to 12 week internship maximum<br/>- Expected "
    "degree conferral date between October 2027 – September 2029<br/>- Have a minimum of "
    "one quarter/semester/trimester remaining in their studies")
AMZ_10538066 = (
    "Internship Options<br/>- 12-16 month internship (starts May 2027)<br/>- 3-4 month "
    "internship (starts January 2027, May 2027, September 2027)<br/><br/>About the team<br/>"
    "Annapurna Labs designs the custom silicon at Amazon")
AMZ_10535280 = (
    "Please note that by applying to this role you would be considered for Software "
    "Development Engineer summer intern and spring/fall co-op roles on various Robotics "
    "teams. You will be able to provide your preference of location and start date during "
    "the application.")
DATABRICKS = "You will graduate in fall 2027 or spring 2028."


def t_matcher():
    print("F1. term matcher")
    m = F.match_fall("Software Development Engineer Intern - Summer 2027 (CAN)",
                     A.clean_fragment(AMZ_10553947), A.clean_fragment(AMZ_10553947_QUALS))
    check("10553947 'Fall 2027 (Start August/September…)' -> high",
          m.fall and m.confidence == "high", str(m))
    check("10553947 seasons list Winter + Fall 2027",
          "Fall 2027" in m.seasons and "Winter 2027" in m.seasons, str(m.seasons))
    m = F.match_fall("ML Systems Software Development Engineer Intern, Annapurna Labs - 2027",
                     A.clean_fragment(AMZ_10538066))
    check("10538066 'starts January 2027, May 2027, September 2027' -> medium",
          m.fall and m.confidence == "medium", str(m))
    m = F.match_fall("Software Development Engineer Intern, ROBOTICS - 2027", AMZ_10535280)
    check("10535280 'spring/fall co-op roles' -> low", m.fall and m.confidence == "low", str(m))
    m = F.match_fall("Software Engineer Intern", DATABRICKS)
    check("Databricks 'graduate in fall 2027' rejected", not m.fall, str(m))
    m = F.match_fall("Software Engineer Intern", "Candidates who are Fall 2027 graduates.")
    check("'Fall 2027 graduates' rejected (graduation clause AFTER the season)", not m.fall, str(m))
    for t in ("Software Engineer Intern - Winter 2028", "Software Engineer Intern - Summer 2027"):
        m = F.match_fall(t, "12-week internship. Summer 2027 only.")
        check(f"{t!r} rejected", not m.fall, str(m))
    m = F.match_fall("Intern", "Internship runs September – December 2027.")
    check("'September – December 2027' -> high", m.confidence == "high", str(m))
    m = F.match_fall("Intern", "Internship starting May 2027 to September 2027.")
    check("'starting May 2027 to September 2027' (summer END) rejected", not m.fall, str(m))
    m = F.match_fall("Intern", terms=["Summer 2027", "Fall 2027"])
    check("Simplify terms ['Summer 2027','Fall 2027'] -> high", m.confidence == "high", str(m))
    m = F.match_fall("Intern", terms=["Summer 2027", "Fall 2026"])
    check("Simplify terms without Fall 2027 rejected", not m.fall, str(m))
    m = F.match_fall("Robotics - Software Development Engineer Intern - 2026 - Toronto",
                     AMZ_10535280)
    check("year-less boilerplate under a 2026 title rejected", not m.fall, str(m))
    m = F.match_fall("Intern", "Fall 2026 co-op. Returning fall term students welcome.")
    check("year-less 'fall term' beside an explicit Fall 2026 rejected", not m.fall, str(m))


def t_location():
    print("F2. location gate")
    cases = {"Toronto": True, "Vancouver, BC": True, "Remote": True,
             "Mountain View, CA": False, "": True, "Seattle, WA; Toronto, ON": True,
             "CA, ON, Toronto": True, "Toronto, Ontario, CAN": True,
             "Remote - US": False, "US Remote": False, "London, UK": False,
             "Montréal, QC, Canada": True}
    for loc, want in cases.items():
        check(f"location_ok({loc!r}) == {want}", F.location_ok(loc) is want)
    check("company_ok('Amazon') / 'Google' always",
          F.company_ok("Amazon") and F.company_ok("Google"))
    check("company_ok rejects a tier-C name", not F.company_ok("Georgian Partners Growth"))


def _r(key, fall=True, conf="high", src="amazon", company="Amazon", title="SDE Intern"):
    return {"key": key, "fall": fall, "confidence": conf if fall else None, "source": src,
            "company": company, "title": title, "location": "Toronto, ON", "url": "u",
            "evidence": "Fall 2027", "seasons": ["Fall 2027"] if fall else []}


def t_state():
    print("F3. state transitions")
    st = {"reqs": {}, "sources": {}}
    due = F.update_reqs(st, [_r("amazon:1"), _r("amazon:2", fall=False)], "t0")
    check("new Fall key alerts", [r["key"] for r in due] == ["amazon:1"], str(due))
    check("new non-Fall key recorded, silent", st["reqs"]["amazon:2"]["fall_flag"] is False)
    F.mark_alerted(st, due, "t0")
    due = F.update_reqs(st, [_r("amazon:1"), _r("amazon:2", fall=False)], "t1")
    check("seen (alerted) key is silent", due == [], str(due))
    due = F.update_reqs(st, [_r("amazon:1"), _r("amazon:2", fall=True, conf="high")], "t2")
    check("false -> true flip alerts", [r["key"] for r in due] == ["amazon:2"], str(due))
    check("flip recorded in state", st["reqs"]["amazon:2"]["fall_flag"] is True)
    F.mark_alerted(st, due, "t2")
    st2 = {"reqs": {}, "sources": {}}
    F.mark_alerted(st2, F.update_reqs(st2, [_r("k", conf="low")], "t0"), "t0")
    due = F.update_reqs(st2, [_r("k", conf="high")], "t1")
    check("low -> high confidence upgrade re-alerts", len(due) == 1)
    # merge: one req seen by two sources is ONE row, best evidence wins
    res = {"amazon": F.SourceResult("amazon", rows=[_r("amazon:9", conf="medium")]),
           "simplify": F.SourceResult("simplify", rows=[_r("amazon:9", conf="high", src="simplify")])}
    rows = F.gate_and_merge(res)
    check("same req from two sources merges to one row, higher confidence kept",
          len(rows) == 1 and rows[0]["confidence"] == "high", str(rows))
    res = {"boards": F.SourceResult("boards", rows=[
        {**_r("x1"), "company": "Amazon", "location": "Mountain View, CA"},
        {**_r("x2"), "company": "Georgian Partners Growth"}])}
    check("gate drops a US-onsite row and a tier-C company", F.gate_and_merge(res) == [])


def t_floors():
    print("F4. source floors + SOURCE DOWN")
    st = {"reqs": {}, "sources": {}}
    bad = F.SourceResult("google", problems=["page 1 has 0 titles < floor 1"])
    m1 = F.source_health(st, bad, "t1")
    check("1st miss: no SOURCE DOWN", m1 is None, str(m1))
    m2 = F.source_health(st, bad, "t2")
    check("2nd miss: SOURCE DOWN", bool(m2) and m2.startswith("FALL-WATCH SOURCE DOWN: google"),
          str(m2))
    st["sources"]["google"]["down_alerted"] = True
    check("3rd miss after it was sent: silent", F.source_health(st, bad, "t3") is None)
    m4 = F.source_health(st, F.SourceResult("google"), "t4")
    check("recovery announced", bool(m4) and "RECOVERED" in m4, str(m4))
    # Simplify floor through the real source function (mocked HTTP)
    class Resp:
        status_code = 200

        def __init__(self, data):
            self._d = data

        def json(self):
            return self._d

    class Client:
        def __init__(self, data):
            self.data = data

        async def get(self, *a, **k):
            return Resp(self.data)
    res = asyncio.run(F.src_simplify(Client([{"active": False}] * 50)))
    check("Simplify below 10k rows is a failure", not res.ok, str(res.problems))
    res = asyncio.run(F.src_simplify(Client([{"active": False}] * F.FLOORS["simplify"])))
    check("Simplify at the floor is ok", res.ok, str(res.problems))


def _amazon_fake(total, country="CAN", fail_at=None, calls=None):
    async def fake(client, url, headers=None):
        off = int(re.search(r"offset=(\d+)", url).group(1))
        if calls is not None:
            calls.append(url)
        if fail_at is not None and off >= fail_at:
            return None, 503
        n = max(0, min(100, total - off))
        jobs = [{"id_icims": str(off + i), "title": f"SDE Intern {off + i}",
                 "country_code": country, "job_path": f"/en/jobs/{off + i}/x",
                 "description": "", "location": "CA, ON, Toronto"} for i in range(n)]
        return {"hits": total, "jobs": jobs, "error": None}, 200
    return fake


def t_amazon_pager():
    print("F5. Amazon pager + loud failures")
    orig = A._get_json
    try:
        calls: list[str] = []
        A._get_json = _amazon_fake(250, calls=calls)
        jobs, hits = asyncio.run(A.amazon_search(None, "intern", ("CAN",)))
        offs = [int(re.search(r"offset=(\d+)", u).group(1)) for u in calls]
        check("pages offsets 0,100,200 until 250 hits exhausted", offs == [0, 100, 200], str(offs))
        check("all 250 rows returned", len(jobs) == 250 and hits == 250, f"{len(jobs)}/{hits}")
        check("uses normalized_country_code[] (not country[])",
              all("normalized_country_code%5B%5D=CAN" in u and "&country%5B%5D" not in u
                  for u in calls), calls[0])
        calls.clear()
        err = io.StringIO()
        with redirect_stderr(err):
            jobs, hits = asyncio.run(A.amazon_search(None, "intern", ("CAN",), max_pages=2))
        check("page cap stops at 2 pages and SAYS how many were not seen",
              len(calls) == 2 and "NOT SEEN" in err.getvalue() and "50 NOT SEEN" in err.getvalue(),
              err.getvalue())
        A._get_json = _amazon_fake(250, fail_at=100)
        raised = False
        err = io.StringIO()
        with redirect_stderr(err):
            try:
                asyncio.run(A.amazon_search(None, "intern", ("CAN",)))
            except A.BoardFetchError:
                raised = True
        check("a failed page raises BoardFetchError and logs it (no silent continue)",
              raised and "FAILED" in err.getvalue(), err.getvalue())
        calls.clear()
        A._get_json = _amazon_fake(150, calls=calls)
        recs = asyncio.run(A._board_amazon(None, {"queries": ["intern"]}))
        check("_board_amazon pages too (150 rows) with USA+CAN normalized filter",
              len(recs) == 150 and all("normalized_country_code%5B%5D=USA" in u and
                                       "normalized_country_code%5B%5D=CAN" in u for u in calls),
              f"{len(recs)} {calls[:1]}")
        # fall_watch's Amazon source: non-CAN rows and short pages are failures
        A._get_json = _amazon_fake(30, country="USA")
        with redirect_stderr(io.StringIO()):
            res = asyncio.run(F.src_amazon(None))
        check("src_amazon: rows with country_code != CAN fail the source",
              any("not country_code CAN" in p for p in res.problems), str(res.problems))
        A._get_json = _amazon_fake(3)
        with redirect_stderr(io.StringIO()):
            res = asyncio.run(F.src_amazon(None))
        check("src_amazon: CAN 'intern' below floor 5 fails the source",
              any("floor" in p for p in res.problems), str(res.problems))
        A._get_json = _amazon_fake(30)
        with redirect_stderr(io.StringIO()):
            res = asyncio.run(F.src_amazon(None))
        check("src_amazon: 30 CAN rows, all CAN, is ok", res.ok and len(res.rows) == 30,
              f"{res.problems} {len(res.rows)}")

        async def short(client, url, headers=None):       # API stops short of hits
            off = int(re.search(r"offset=(\d+)", url).group(1))
            if off >= 100:
                return {"hits": 180, "jobs": [], "error": None}, 200
            return await _amazon_fake(180)(client, url, headers)
        A._get_json = short
        err = io.StringIO()
        with redirect_stderr(err):
            res = asyncio.run(F.src_amazon(None))
        check("src_amazon: hits > returned after paging is an ERROR + failure",
              any("hits but only" in p for p in res.problems) and "ERROR" in err.getvalue(),
              str(res.problems))
    finally:
        A._get_json = orig


def t_amazon_board_global():
    print("F5b. the Amazon BOARD fetches globally (countries=[]), the watcher filters CAN")
    import company_boards
    amz = [b for b in company_boards.BOARDS if b.get("ats_type") == "amazon"]
    check("exactly one Amazon board", len(amz) == 1, str(amz))
    board = amz[0] if amz else {}
    # [] on purpose: a USA+CAN filter would stop fetching ~48 UK/EU reqs, which would
    # then die by stale-strike (company_boards.py comment, 2026-11-11).
    check("Amazon board carries countries == [] (global, deliberate)",
          board.get("countries") == [], str(board))
    u = A.amazon_search_url("intern", ())
    check("countries=() URL has no country parameter at all",
          "country" not in u and "offset=0" in u and "result_limit=100" in u, u)
    orig = A._get_json
    try:
        calls: list[str] = []
        A._get_json = _amazon_fake(150, calls=calls)
        recs = asyncio.run(A._board_amazon(None, {**board, "queries": ["intern"]}))
        check("the production Amazon board config sends no country filter and still pages",
              len(recs) == 150 and len(calls) == 2 and all("country" not in c for c in calls),
              f"{len(recs)} {calls}")
    finally:
        A._get_json = orig


def t_alert_length():
    print("F10. an alert always fits in one Telegram message (4,096 chars)")
    url = ("https://nvidia.wd5.myworkdayjobs.com/en-US/NVIDIAExternalCareerSite/job/"
           "Canada-Ontario-Toronto/Software-Engineering-Intern--Deep-Learning-Compiler-and-"
           "Inference-Libraries---Fall-2027_JR2{:05d}")
    rows = [{**_r(f"nv{i}", src="boards", company="NVIDIA",
                  title="Software Engineering Intern, Deep Learning Compiler and Inference "
                        "Libraries - Fall 2027"),
             "location": "Canada, Ontario, Toronto; Canada, Ontario, Waterloo; US, CA, Santa Clara",
             "url": url.format(i), "seasons": ["Fall 2027"],
             "evidence": "This internship runs Fall 2027 (September to December) at our "
                         "Toronto office with the compiler team"} for i in range(10)]
    text = F.format_alert(rows)
    shown = text.count("FALL 2027:")
    m = re.search(r"…and (\d+) more", text)
    check("10 long real-shaped rows: under Telegram's 4,096", len(text) <= 4096, str(len(text)))
    check("every row is shown or counted in '…and N more'",
          shown >= 1 and shown + (int(m.group(1)) if m else 0) == 10, f"{shown} {m and m.group(0)}")
    short = [_r(f"amazon:{i}") for i in range(3)]
    t2 = F.format_alert(short)
    check("three short rows: all shown, no overflow line",
          t2.count("FALL 2027:") == 3 and "more" not in t2, t2)


def t_runners():
    print("F6. runners propagate the exit code")
    with tempfile.TemporaryDirectory() as td:
        home = Path(td)
        pkg = home / ".hermes" / "scripts" / "internship"
        pkg.mkdir(parents=True)
        stub = "import os, sys\nsys.exit(int(os.environ.get('FAKE_RC', '0')))\n"
        (pkg / "fall_watch.py").write_text(stub)
        (pkg / "hot_watch.py").write_text(stub)
        vbin = home / ".hermes" / "hermes-agent" / "venv" / "bin"
        vbin.mkdir(parents=True)
        (vbin / "python").symlink_to(sys.executable)
        shim = home / "shim"                       # macOS has no `timeout`; the VPS does
        shim.mkdir()
        (shim / "timeout").write_text("#!/usr/bin/env bash\nshift\nexec \"$@\"\n")
        (shim / "timeout").chmod(0o755)
        env = {**os.environ, "HOME": str(home), "PATH": f"{shim}:{os.environ.get('PATH', '')}"}
        for runner in ("run_fall_watch.sh", "run_hot_watch.sh"):
            for rc in (0, 3):
                p = subprocess.run(["bash", str(HERE / runner)], env={**env, "FAKE_RC": str(rc)},
                                   capture_output=True, text=True)
                check(f"{runner}: python exit {rc} -> runner exit {rc}", p.returncode == rc,
                      f"got {p.returncode} {p.stderr[-200:]}")
        log = (home / ".hermes" / "health" / "fall_watch.log").read_text()
        check("run_fall_watch.sh still logs", "fall-watch" in log and "exit 3" in log, log[-200:])


def t_hot_watch_trim():
    print("F7. hot_watch seen-set trim keeps the newest")
    st = {"seen": ["z1", "z2", "z2"]}                      # old format: sorted, a dup
    lst = H._ordered_seen(st)
    check("migration keeps every old id, dedups", lst == ["z1", "z2"], str(lst))
    H._record_seen(lst, ["a3", "z1", "a4"])
    check("new ids appended in first-seen order", lst == ["z1", "z2", "a3", "a4"], str(lst))
    check("trim to 3 drops the OLDEST (z1), keeps a3/a4",
          H._trim(lst, 3) == ["z2", "a3", "a4"], str(H._trim(lst, 3)))
    # end to end through main(): poll + _save with a tiny cap
    orig = (H.STATE, H.SEEN_MAX, H.poll, H.send, H.datetime, sys.argv)
    with tempfile.TemporaryDirectory() as td:
        try:
            H.STATE = Path(td) / "hot.json"
            H.STATE.write_text(json.dumps({"seen": ["z1", "z2"], "seeded": True}))
            H.SEEN_MAX = 3

            async def fake_poll(tiers):
                return [{"canonical_id": c, "company": "X", "role": "SWE Intern"}
                        for c in ("a3", "a4")]

            class Noon(datetime):
                @classmethod
                def now(cls, tz=None):
                    return datetime(2026, 11, 11, 12, 0)
            H.poll, H.send, H.datetime = fake_poll, (lambda text: True), Noon
            sys.argv = ["hot_watch.py"]
            with redirect_stderr(io.StringIO()):
                rc = H.main()
            saved = json.loads(H.STATE.read_text())["seen"]
            check("main(): saved seen-set is the newest 3", rc == 0 and saved == ["z2", "a3", "a4"],
                  f"rc={rc} {saved}")
        finally:
            H.STATE, H.SEEN_MAX, H.poll, H.send, H.datetime, sys.argv = orig


GOOGLE_FIXTURE = """
<div class="rZt9ff lvEMZd"><span class="SWhIm">2</span>  jobs matched</div><ul class="spHGqe">
<li class="lLd3Je" ssk='18:111'><div><h3 class="QJPWVe">Student Researcher, BS/MS, Fall 2027</h3>
<div class="wVoYLb"><span class="r0wTof ">Waterloo, ON, Canada</span><span class="r0wTof p3oCrc">; Toronto, ON, Canada</span></div>
<div class="Xsxa1e"><h4>Minimum qualifications</h4><ul><li>Currently enrolled in a BS program.</li></ul></div>
<a href="jobs/results/111-student-researcher-bsms-fall-2027?x=1" aria-label="Learn more about Student Researcher, BS/MS, Fall 2027"></a>
<ul role="menu"><li>Share: Fall 2027 graduates</li></ul></div></li>
<li class="lLd3Je" ssk='18:222'><div><h3 class="QJPWVe">Software Developer Intern, PhD, Summer 2027</h3>
<div class="wVoYLb"><span class="r0wTof ">Montreal, QC, Canada</span></div>
<a href="jobs/results/222-software-developer-intern-phd-summer-2027" aria-label="Learn more about it"></a></div></li>
</ul></div><div>footer</div>
"""


def t_google():
    print("F8. Google results parser")
    cards, matched, n = F.parse_google(GOOGLE_FIXTURE)
    check("2 jobs matched, 2 titles, 2 cards", (matched, n, len(cards)) == (2, 2, 2),
          f"{matched} {n} {len(cards)}")
    c = {x["id"]: x for x in cards}
    check("card ids + titles + locations parsed",
          c.get("111", {}).get("title") == "Student Researcher, BS/MS, Fall 2027"
          and c["111"]["location"] == "Waterloo, ON, Canada; Toronto, ON, Canada"
          and c.get("222", {}).get("location") == "Montreal, QC, Canada", str(cards))
    check("Fall 2027 title -> high", F.match_fall(c["111"]["title"], c["111"]["text"]).confidence
          == "high")
    check("card text stops at 'Learn more' (share menu excluded)",
          "Share:" not in c["111"]["text"], c["111"]["text"])


class _GClient:
    def __init__(self, pages):
        self.pages = pages

    async def get(self, url, **k):
        m = re.search(r"[?&]page=(\d+)", url)
        body = self.pages.get(int(m.group(1)) if m else 1, "")

        class R:
            status_code = 200
            text = body
        return R()


def t_google_source():
    print("F8b. Google source: floors and matched-vs-parsed")
    with redirect_stderr(io.StringIO()):
        res = asyncio.run(F.src_google(_GClient({1: GOOGLE_FIXTURE})))
    check("fixture page: ok, 2 rows, Fall row is google:111",
          res.ok and len(res.rows) == 2 and any(r["key"] == "google:111" and r["fall"]
                                                for r in res.rows), str(res.problems))
    short = GOOGLE_FIXTURE.replace('<span class="SWhIm">2</span>', '<span class="SWhIm">25</span>')
    with redirect_stderr(io.StringIO()):
        res = asyncio.run(F.src_google(_GClient({1: short})))
    check("25 matched but 2 parsed is a failure", any("only 2 parsed" in p for p in res.problems),
          str(res.problems))
    with redirect_stderr(io.StringIO()):
        res = asyncio.run(F.src_google(_GClient({1: "<html>redesigned page</html>"})))
    check("a page with no h3.QJPWVe titles fails the floor",
          any("floor" in p for p in res.problems), str(res.problems))


def _fake_collect(results):
    return lambda names: {k: v for k, v in results.items()}


def t_run():
    print("F9. run(): seed / dry-run / alert / SOURCE DOWN / heartbeat / quiet hours")
    noon = datetime(2026, 11, 11, 12, 0)
    night = datetime(2026, 11, 11, 23, 30)
    with tempfile.TemporaryDirectory() as td:
        os.environ["FALL_WATCH_STATE"] = str(Path(td) / "fw.json")
        path = Path(os.environ["FALL_WATCH_STATE"])
        sent: list[str] = []

        def send(t):
            sent.append(t)
            return True
        good = {"amazon": F.SourceResult("amazon", rows=[
            {**_r("amazon:10553947"), "amazon_id": "10553947"}]),
                "google": F.SourceResult("google", rows=[])}
        with redirect_stderr(io.StringIO()):
            rc = F.run(["--dry-run"], send=send, now=noon, collect_fn=_fake_collect(good))
        check("dry-run: nothing sent, no state file", rc == 0 and not sent and not path.exists(),
              f"rc={rc} sent={len(sent)} exists={path.exists()}")
        with redirect_stderr(io.StringIO()):
            rc = F.run(["--seed"], send=send, now=noon, collect_fn=_fake_collect(good))
        check("seed: nothing sent, state written", rc == 0 and not sent and path.exists())
        with redirect_stderr(io.StringIO()):
            F.run([], send=send, now=noon, collect_fn=_fake_collect(good))
        check("after seed, the seeded req is silent", not sent, str(sent))
        new = {**good, "amazon": F.SourceResult("amazon", rows=[
            {**_r("amazon:10553947"), "amazon_id": "10553947"},
            {**_r("amazon:777", title="SDE Intern Fall"), "amazon_id": "777"}])}
        with redirect_stderr(io.StringIO()):
            F.run([], send=send, now=night, collect_fn=_fake_collect(new))
        check("quiet hours: new req held, not sent", not sent, str(sent))
        with redirect_stderr(io.StringIO()):
            rc = F.run([], send=send, now=noon, collect_fn=_fake_collect(new))
        check("held req alerts at the next daytime run, with REFERRAL FIRST",
              rc == 0 and len(sent) == 1 and "FALL 2027: Amazon" in sent[0]
              and "REFERRAL FIRST: send your uncle job ID 777 before applying." in sent[0],
              str(sent))
        sent.clear()
        with redirect_stderr(io.StringIO()):
            F.run([], send=send, now=noon, collect_fn=_fake_collect(new))
        check("an alerted req is silent on the next run", not sent, str(sent))
        flaky = {"amazon": F.SourceResult("amazon", rows=[_r("amazon:888")])}
        with redirect_stderr(io.StringIO()):
            rc = F.run([], send=lambda t: False, now=noon, collect_fn=_fake_collect(flaky))
            rc_retry = F.run([], send=send, now=noon, collect_fn=_fake_collect(flaky))
        check("send failure: exit 1, nothing marked, retried next run",
              rc == 1 and rc_retry == 0 and len(sent) == 1 and "amazon:888" not in sent[0]
              and "SDE Intern" in sent[0], f"rc={rc} retry={rc_retry} {sent}")
        sent.clear()
        many = {"amazon": F.SourceResult("amazon", rows=[
            _r(f"amazon:{i}", company="Amazon") for i in range(1000, 1000 + F.MAX_ITEMS + 4)])}
        with redirect_stderr(io.StringIO()):
            F.run([], send=send, now=noon, collect_fn=_fake_collect(many))
        check("cap: one message, MAX_ITEMS items, '…and 4 more'",
              len(sent) == 1 and sent[0].count("FALL 2027:") == F.MAX_ITEMS
              and "…and 4 more" in sent[0], sent[0][-200:] if sent else "")
        sent.clear()
        down = {**good, "google": F.SourceResult("google", problems=["0 titles"])}
        with redirect_stderr(io.StringIO()):
            rc1 = F.run([], send=send, now=noon, collect_fn=_fake_collect(down))
            n1 = len(sent)
            rc2 = F.run([], send=send, now=noon, collect_fn=_fake_collect(down))
            n2 = len(sent)
            rc3 = F.run([], send=send, now=noon, collect_fn=_fake_collect(down))
        check("failing source: non-zero exit every run", (rc1, rc2, rc3) == (1, 1, 1),
              f"{rc1} {rc2} {rc3}")
        check("SOURCE DOWN sent on the 2nd miss only (not the 1st, not repeated)",
              n1 == 0 and n2 == 1 and len(sent) == 1 and "SOURCE DOWN: google" in sent[0],
              str(sent))
        sent.clear()
        with redirect_stderr(io.StringIO()):
            rc = F.run(["--heartbeat"], send=send, now=noon, collect_fn=_fake_collect(good))
        hb = [s for s in sent if s.startswith("fall-watch alive")]
        check("heartbeat: 'fall-watch alive: 2/2 sources ok, N Fall-tagged reqs tracked'",
              rc == 0 and hb and re.match(r"fall-watch alive: 2/2 sources ok, \d+ Fall-tagged", hb[0]),
              str(sent))
        check("recovery after SOURCE DOWN announced", any("RECOVERED: google" in s for s in sent),
              str(sent))
        sent.clear()
        monday = datetime(2026, 11, 16, 9, 15)
        with redirect_stderr(io.StringIO()):
            F.run([], send=send, now=datetime(2026, 11, 16, 8, 15), collect_fn=_fake_collect(good))
            n_early = len(sent)
            F.run([], send=send, now=monday, collect_fn=_fake_collect(good))
            n_first = len(sent)
            F.run([], send=send, now=datetime(2026, 11, 16, 10, 15), collect_fn=_fake_collect(good))
        check("weekly heartbeat: none before 09:00 Monday, one at the first run after, not repeated",
              (n_early, n_first, len(sent)) == (0, 1, 1)
              and sent[0].startswith("fall-watch alive"), f"{n_early} {n_first} {sent}")
        sent.clear()
        path.write_text("{not json")
        with redirect_stderr(io.StringIO()):
            rc = F.run([], send=send, now=noon, collect_fn=_fake_collect(good))
        check("corrupt state: refuses (exit 2), says so, does not overwrite",
              rc == 2 and path.read_text() == "{not json" and "CORRUPT" in "".join(sent))
        del os.environ["FALL_WATCH_STATE"]


def main() -> int:
    for t in (t_matcher, t_location, t_state, t_floors, t_amazon_pager, t_amazon_board_global,
              t_runners, t_hot_watch_trim, t_google, t_google_source, t_run, t_alert_length):
        try:
            t()
        except Exception:  # noqa: BLE001 - a crash is a failure, not a pass
            traceback.print_exc()
            FAILURES.append(f"{t.__name__} crashed")
    print()
    if FAILURES:
        print(f"FAILED: {len(FAILURES)} — {', '.join(FAILURES)}")
        return 1
    print("ALL FALL-WATCH INVARIANTS HOLD")
    return 0


if __name__ == "__main__":
    sys.exit(main())
