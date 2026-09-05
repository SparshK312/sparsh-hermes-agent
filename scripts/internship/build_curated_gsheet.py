#!/usr/bin/env python3
"""
build_curated_gsheet.py — the living board on Google Sheets (round-trip preserve).

Sibling of build_curated_xlsx.py. Same store, same routing, same ranking — it imports
them from the xlsx module so the two artifacts can never disagree about which posting
belongs on which tab. What differs is everything about *how a spreadsheet you can
sort behaves*:

  ── THE CRUX, and why this is not just the xlsx with a different writer ──
  The xlsx is regenerated from scratch every refresh, fully re-ranked. That is safe
  because nobody can reorder an xlsx between runs without Excel holding a lock.
  Google Sheets has no lock, and NOT having one is the whole reason it is preferred.

  Verified 2026-08-27 against the live sheet: issuing the sortRange that clicking a
  column header performs moved a pinned row from row 3 to row 32. Human columns
  travel with the row (good), but a positional machine write would then paste some
  other posting's company/role/link against those notes and that Applied status.
  Silent, unrecoverable corruption of the only table tracking real applications.

  So every write here is ORDER-AWARE: read column A (_id) first, lay the rows out in
  the order the sheet *currently* has, and append anything new at the bottom. His
  sort survives a refresh, and a refresh cannot misalign his data. Sorting the board
  however he likes is a supported operation, not a hazard.

  read_back_human(sheet_id) -> {canonical_id: {status, applied_date, notes, priority_override}}
  write_board(store, sheet_id, generated_at) -> counts
  ensure_format(sheet_id) -> idempotent formatting; run rarely, NOT per refresh

  ── Transport ──
  All HTTP goes through _sheets(), a single choke point, so swapping composio proxy
  for a GCP service account later is a change in one function. See TRANSPORT NOTE.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

from build_curated_xlsx import (  # single source of truth for routing + ranking
    APP_HEADERS,
    ID_HEADER,
    QUEUE_HEADERS,
    REVIEW_HEADERS,
    STATUS_RANK,
    _fit_cells,
    _queue_sort_key,
    _review_status,
    classify_row,
)

SCHEMA_VERSION = 1
SHEET_ID_DEFAULT = "1Kkle7QoKsBMXihoslWjIoMDqKFznwxxA4Y_OgiqJpWI"

TAB_QUEUE = "Apply Now"
TAB_APPS = "My Applications"
TAB_REVIEWED = "Reviewed"
TAB_META = "_meta"

# Header on row 1, data from row 2, row 1 frozen. Deliberately flatter than the xlsx
# (which spends rows 1-2 on a title block): this gets read on a phone, where two rows
# of decoration is a meaningful share of the screen.
HEADER_ROW = 1
FIRST_DATA_ROW = 2

_API = "https://sheets.googleapis.com/v4/spreadsheets"


class SheetsError(RuntimeError):
    """A Sheets API call failed. Raised for BOTH transport failures and API-level
    errors, because those are not distinguishable by exit code — see _sheets()."""


# ── transport ─────────────────────────────────────────────────────────────────
# TRANSPORT NOTE (2026-08-27): composio proxy is a curl-shaped CLI with no model in
# the path, so a scheduled LLM-free job can drive the full Sheets API through it.
# Verified working headless: `env -i HOME=… PATH=…`, stdin /dev/null, no TTY — the
# launchd shape. The credential is Sparsh's own personal Composio account.
#
# It is deliberately the ONLY place that knows how requests are authenticated. The
# VPS should NOT reuse this: that key can also select the gmail toolkit, so putting
# it on the always-on box would hand it read access to his email to render a
# spreadsheet. A GCP service account scoped to this one spreadsheet belongs there,
# and it plugs in here.
def _composio_bin() -> str:
    p = Path.home() / ".composio" / "composio"
    if p.is_file() and os.access(p, os.X_OK):
        return str(p)
    found = shutil.which("composio")
    if found:
        return found
    raise SheetsError(
        "composio CLI not found (looked in ~/.composio/composio and PATH). "
        "Install: curl -fsSL https://composio.dev/install | bash")


# ── native Google transport (the VPS) ────────────────────────────────────────
# The board moved to the VPS on 2026-09-04, and composio is a Mac-only install. The VPS
# does not need it: its `google_token.json` already carries the `spreadsheets` scope and
# the Sheets API is enabled, so it can talk to the API directly. Using the native path
# there also keeps the Gmail-capable Composio key OFF the always-on box.
#
#   VPS  -> google-api-python-client with the existing token
#   Mac  -> composio proxy (no Google credentials locally)
_GOOGLE_API_DIR = Path.home() / ".hermes/skills/productivity/google-workspace/scripts"


def _native_creds():
    """Credentials if this machine has them (the VPS), else None (the Mac)."""
    if not (Path.home() / ".hermes" / "google_token.json").is_file():
        return None
    try:
        import sys as _sys
        if str(_GOOGLE_API_DIR) not in _sys.path:
            _sys.path.insert(0, str(_GOOGLE_API_DIR))
        import google_api as _g
        return _g.get_credentials()
    except Exception:  # noqa: BLE001
        return None


def _sheets_native(method: str, url: str, body: dict | None, timeout: int) -> dict:
    """Same contract as _sheets(): parsed JSON, or raise SheetsError."""
    import google.auth.transport.requests as _gart
    creds = _native_creds()
    if creds is None:
        raise SheetsError("no native Google credentials")
    session = _gart.AuthorizedSession(creds)
    r = session.request(method.upper(), url, json=body, timeout=timeout)
    try:
        data = r.json()
    except Exception:  # noqa: BLE001
        raise SheetsError(f"{method} {url[-60:]} -> non-JSON (HTTP {r.status_code}): "
                          f"{r.text[:200]}") from None
    if isinstance(data, dict) and "error" in data:
        err = data["error"]
        if isinstance(err, dict):
            raise SheetsError(f"{method} -> Sheets API {err.get('code')} "
                              f"{err.get('status','')}: {err.get('message','')}".strip())
        raise SheetsError(f"{method} -> {err}")
    if r.status_code >= 400:
        raise SheetsError(f"{method} -> HTTP {r.status_code}: {r.text[:200]}")
    return data


def _sheets(method: str, path: str, body: dict | None = None, *,
            sheet_id: str, timeout: int = 120) -> dict:
    """Every Sheets HTTP call goes through here. Returns the parsed JSON body.

    🔴 THE TRAP THIS EXISTS TO CLOSE: `composio proxy` EXITS 0 ON HTTP ERRORS.
    Verified 2026-08-27 — a request for a nonexistent spreadsheet returned
    {"error":{"code":404,...}} with exit status 0. Only a malformed CLI invocation
    exits non-zero. So a wrapper trusting the exit code (or using check=True) would
    read 404 / 403 / 429 / quota-exceeded as a clean run and report success while
    writing nothing. That is the same silent-failure shape as a watchdog that scores
    healthy when its probe dies. We therefore inspect the BODY, always.
    """
    url = f"{_API}/{sheet_id}{path}"
    # Native first where credentials exist (the VPS); composio elsewhere (the Mac).
    if _native_creds() is not None:
        return _sheets_native(method, url, body, timeout)
    cmd = [_composio_bin(), "proxy", url, "--toolkit", "googlesheets"]
    if method.upper() != "GET":
        cmd += ["-X", method.upper()]
    payload = None
    if body is not None:
        # Body over stdin, never argv: a full-board render is ~272 KB and would
        # otherwise risk the argument-length limit.
        cmd += ["-H", "content-type: application/json", "-d", "-"]
        payload = json.dumps(body)

    # Pinned minimal environment so behaviour is identical under launchd and by hand.
    env = {"HOME": os.path.expanduser("~"),
           "PATH": "/usr/bin:/bin:/usr/sbin:/sbin"}
    try:
        # input="" (never None) so stdin is closed rather than INHERITED. With
        # input=None subprocess hands the parent's stdin to the child and a GET
        # blocks forever waiting on a terminal that is not there.
        proc = subprocess.run(cmd, input=payload if payload is not None else "",
                              capture_output=True, text=True,
                              timeout=timeout, env=env)
    except subprocess.TimeoutExpired as exc:
        raise SheetsError(f"{method} {path} timed out after {timeout}s") from exc

    out = (proc.stdout or "").strip()
    if not out:
        raise SheetsError(
            f"{method} {path} returned no output (exit {proc.returncode}). "
            f"stderr: {(proc.stderr or '').strip()[:400]}")
    try:
        data = json.loads(out)
    except json.JSONDecodeError as exc:
        # A CLI-level failure (bad toolkit, missing connection) prints help text.
        raise SheetsError(
            f"{method} {path} returned non-JSON (exit {proc.returncode}): "
            f"{out[:400]}") from exc

    if isinstance(data, dict):
        if "error" in data:
            err = data["error"]
            if isinstance(err, dict):
                raise SheetsError(
                    f"{method} {path} -> Sheets API {err.get('code')} "
                    f"{err.get('status', '')}: {err.get('message', '')}".strip())
            raise SheetsError(f"{method} {path} -> {err}")
        # composio's own envelope on a refused proxy call
        if data.get("successful") is False:
            raise SheetsError(f"{method} {path} -> composio: {data.get('error')}")
    return data


def _q(tab: str) -> str:
    """Quote a tab name for an A1 range. Sheets wants single quotes, doubled inside."""
    return "'" + tab.replace("'", "''") + "'"


def _enc(rng: str) -> str:
    from urllib.parse import quote
    return quote(rng, safe="")


def values_get(sheet_id: str, rng: str) -> list[list]:
    d = _sheets("GET", f"/values/{_enc(rng)}", sheet_id=sheet_id)
    return d.get("values", []) or []


def values_update(sheet_id: str, rng: str, values: list[list],
                  raw: bool = True) -> dict:
    opt = "RAW" if raw else "USER_ENTERED"
    return _sheets("PUT", f"/values/{_enc(rng)}?valueInputOption={opt}",
                   {"values": values}, sheet_id=sheet_id)


def values_clear(sheet_id: str, rng: str) -> dict:
    return _sheets("POST", f"/values/{_enc(rng)}:clear", {}, sheet_id=sheet_id)


def batch_update(sheet_id: str, requests: list[dict]) -> dict:
    return _sheets("POST", ":batchUpdate", {"requests": requests}, sheet_id=sheet_id)


def tab_map(sheet_id: str) -> dict[str, dict]:
    """{title: {sheetId, rowCount, columnCount, frozenRowCount}}"""
    d = _sheets("GET", "?fields=sheets.properties", sheet_id=sheet_id)
    out = {}
    for s in d.get("sheets", []):
        p = s.get("properties", {})
        g = p.get("gridProperties", {}) or {}
        out[p.get("title", "")] = {
            "sheetId": p.get("sheetId"),
            "rowCount": g.get("rowCount", 0),
            "columnCount": g.get("columnCount", 0),
            "frozenRowCount": g.get("frozenRowCount", 0),
        }
    return out


# ── column helpers ────────────────────────────────────────────────────────────
def _col_letter(n: int) -> str:
    s = ""
    while n > 0:
        n, r = divmod(n - 1, 26)
        s = chr(65 + r) + s
    return s


_HUMAN_BY_HEADER = {"status": "status", "priority": "priority_override",
                    "applied": "applied_date", "notes": "notes"}

TABS = (TAB_QUEUE, TAB_APPS, TAB_REVIEWED)
_HEADERS = {TAB_QUEUE: QUEUE_HEADERS, TAB_APPS: APP_HEADERS, TAB_REVIEWED: REVIEW_HEADERS}


# ── read-back ─────────────────────────────────────────────────────────────────
def read_back_human(sheet_id: str = SHEET_ID_DEFAULT) -> dict[str, dict]:
    """Pull his edits out of the sheet, keyed by canonical_id from the hidden _id
    column. Keys off _id per row and NEVER off row position, because he is expected
    to sort the sheet and sorting moves rows (proven: a pinned row went 3 -> 32)."""
    out: dict[str, dict] = {}
    present = tab_map(sheet_id)
    for tab in TABS:
        if tab not in present:
            continue
        headers = _HEADERS[tab]
        last = _col_letter(len(headers))
        rows = values_get(sheet_id, f"{_q(tab)}!A{HEADER_ROW}:{last}")
        if not rows:
            continue
        hdr = [str(c).strip() for c in rows[0]]
        if ID_HEADER not in hdr:
            continue  # not a board tab (e.g. an old snapshot with no _id)
        col_of = {h.lower(): i for i, h in enumerate(hdr)}
        i_id = col_of[ID_HEADER.lower()]
        for row in rows[1:]:
            if i_id >= len(row):
                continue
            cid = str(row[i_id]).strip()
            if not cid:
                continue
            human = out.setdefault(cid, {})
            for hname, field in _HUMAN_BY_HEADER.items():
                ci = col_of.get(hname)
                if ci is not None and ci < len(row):
                    val = str(row[ci]).strip()
                    if val:
                        human[field] = val
    return out


# ── row rendering ─────────────────────────────────────────────────────────────
def _rec(cid, entry):
    return {"_cid": cid, "machine": entry.get("machine", {}) or {},
            "human": entry.get("human", {}) or {}}


def _route(store: dict) -> dict[str, list]:
    buckets = {TAB_QUEUE: [], TAB_APPS: [], TAB_REVIEWED: []}
    dest_tab = {"queue": TAB_QUEUE, "application": TAB_APPS, "reviewed": TAB_REVIEWED}
    for cid, entry in store.items():
        rec = _rec(cid, entry)
        tab = dest_tab.get(classify_row(rec))
        if tab:
            buckets[tab].append(rec)
    buckets[TAB_QUEUE].sort(key=_queue_sort_key)
    buckets[TAB_QUEUE] = _diversify(buckets[TAB_QUEUE])
    buckets[TAB_APPS].sort(key=lambda r: (
        STATUS_RANK.get((r["human"].get("status") or "").strip(), 7),
        r["human"].get("applied_date") or "0"))
    buckets[TAB_REVIEWED].sort(key=lambda r: (
        0 if _review_status(r) in ("Skip", "Not a Fit") else 1,
        r["machine"].get("company", "")))
    return buckets


# ── company diversity ────────────────────────────────────────────────────────
# Hotness is brand-dominant by design, which is right — but it means one company
# posting 26 intern reqs owns the whole top of the board. Measured 2026-08-28 before
# this pass: 9 of the top 10 and 23 of the top 30 were Tesla, with just 4 distinct
# companies in the top 20. Sparsh's own framing: *"why do we need so many from the
# same company"* — a board he scrolls for five minutes should not show him one
# employer.
#
# Nothing is removed or down-ranked. Roles are INTERLEAVED: a company may take at
# most MAX_RUN consecutive slots and at most MAX_IN_WINDOW of any rolling WINDOW,
# after which its next-best role yields to another company's. Within those limits
# the original quality order is preserved exactly.
MAX_RUN = 2          # consecutive rows from one company
MAX_IN_WINDOW = 3    # rows from one company in any WINDOW-sized stretch
WINDOW = 12


def _diversify(rows: list[dict]) -> list[dict]:
    def co(r):
        return (r["machine"].get("company") or "?").strip().lower()

    remaining = list(rows)
    out: list[dict] = []
    while remaining:
        tail = [co(r) for r in out[-WINDOW:]]
        run = 0
        for c in reversed(tail):
            if out and c == co(out[-1]):
                run += 1
            else:
                break
        pick = None
        for i, r in enumerate(remaining):
            c = co(r)
            over_run = bool(out) and c == co(out[-1]) and run >= MAX_RUN
            over_win = tail.count(c) >= MAX_IN_WINDOW
            if not over_run and not over_win:
                pick = i
                break
        # Everything left is over quota (e.g. only one company remains) — take the
        # best one rather than looping forever.
        out.append(remaining.pop(pick if pick is not None else 0))
    return out


def _row_values(tab: str, rec: dict) -> list:
    m, h = rec["machine"], rec["human"]
    fit_disp, why_disp, _kind = _fit_cells(m)
    url = m.get("url", "") or ""
    common = {
        ID_HEADER: rec["_cid"],
        "Status": (h.get("status") or "") or ("To Apply" if tab == TAB_QUEUE else ""),
        "Company": m.get("company", ""), "Role": m.get("role", ""),
        "Lane": m.get("lane", ""), "Location": m.get("location", ""),
        "Cycle": m.get("cycle", ""), "Notes": h.get("notes", ""),
        "Apply": url,           # rewritten as a HYPERLINK below
        "Fit": fit_disp, "Why": why_disp,
    }
    if tab == TAB_QUEUE:
        common.update({
            "🔥": m.get("fresh", ""), "Hot": m.get("hotness", ""),
            "Tier": m.get("tier", ""), "Priority": h.get("priority_override", ""),
            "Posted": m.get("posted_date", ""), "Age": m.get("age_days", ""),
            "Source": m.get("source", ""),
        })
    elif tab == TAB_APPS:
        common.update({"Applied": h.get("applied_date", ""),
                       "Source / Referral": m.get("source", "")})
    elif tab == TAB_REVIEWED:
        common["Status"] = _review_status(rec)
    return [common.get(hname, "") for hname in _HEADERS[tab]]


# ── grid plumbing ─────────────────────────────────────────────────────────────
def _ensure_tabs(sheet_id: str, needed_rows: dict[str, int]) -> dict[str, dict]:
    """Create missing tabs and grow undersized grids. Idempotent."""
    present = tab_map(sheet_id)
    reqs = []
    for tab in (*TABS, TAB_META):
        want_rows = needed_rows.get(tab, 200) + 100     # headroom for appends
        want_cols = max(len(_HEADERS.get(tab, ["a", "b"])), 4)
        if tab not in present:
            reqs.append({"addSheet": {"properties": {
                "title": tab,
                "hidden": tab == TAB_META,
                "gridProperties": {"rowCount": want_rows, "columnCount": want_cols,
                                   "frozenRowCount": 1 if tab != TAB_META else 0}}}})
        else:
            info = present[tab]
            grow = {}
            if info["rowCount"] < want_rows:
                grow["rowCount"] = want_rows
            if info["columnCount"] < want_cols:
                grow["columnCount"] = want_cols
            if tab != TAB_META and info.get("frozenRowCount", 0) != 1:
                grow["frozenRowCount"] = 1
            if grow:
                fields = ",".join(f"gridProperties.{k}" for k in grow)
                reqs.append({"updateSheetProperties": {
                    "properties": {"sheetId": info["sheetId"],
                                   "gridProperties": grow},
                    "fields": fields}})
    if reqs:
        batch_update(sheet_id, reqs)
        present = tab_map(sheet_id)
    return present


def _sync_tab(sheet_id: str, tab: str, records: list[dict]) -> int:
    """Order-aware full-row write.

    Rows are laid out in the order the sheet ALREADY has (read from column A), with
    anything new appended at the bottom. Because each row is written whole — _id and
    his columns together — a row can never be split across two postings, which is the
    corruption a positional column-write causes after he sorts.
    """
    headers = _HEADERS[tab]
    last_col = _col_letter(len(headers))
    existing = values_get(sheet_id, f"{_q(tab)}!A{FIRST_DATA_ROW}:A")
    cur_ids = [str(r[0]).strip() if r else "" for r in existing]

    # ── ordering: CANONICAL RANK, not the sheet's existing order ────────────────
    # The first version preserved whatever order the sheet already had, to protect a
    # manual sort. That was the wrong trade and it broke the board: two disqualified
    # Netflix roles stayed pinned at #1 and #2 for a full day while their true rank was
    # #196 and #197, because the sheet had frozen at its very first ordering and no
    # refresh could ever re-rank it.
    #
    # 🔑 Order preservation was never what made this safe. SAFETY COMES FROM WRITING
    # WHOLE ROWS plus reading human edits back by `_id`: each row is written as a unit,
    # so `_id` and his Status/Notes always move together no matter where the row lands,
    # and read_back_human() matches on `_id` and never on position. Re-ranking is
    # therefore just as safe as preserving order — and a board whose whole purpose is
    # "best roles first" has to actually re-rank.
    #
    # He can still sort the sheet whenever he likes; the next refresh simply restores
    # machine rank, which is the behaviour the xlsx has always had.
    ordered, seen = [], set()
    for rec in records:
        if rec["_cid"] not in seen:
            ordered.append(rec)
            seen.add(rec["_cid"])
    _ = cur_ids            # still read, to size the tail-clear below

    grid = [headers] + [_row_values(tab, r) for r in ordered]
    end_row = HEADER_ROW + len(grid) - 1
    values_update(sheet_id, f"{_q(tab)}!A{HEADER_ROW}:{last_col}{end_row}", grid)

    # Clear whatever the previous, longer render left behind.
    prev_last = FIRST_DATA_ROW + len(cur_ids) - 1
    if prev_last > end_row:
        values_clear(sheet_id, f"{_q(tab)}!A{end_row + 1}:{last_col}{prev_last}")

    # Apply column as a compact link rather than a raw URL (phone-friendly).
    if "Apply" in headers:
        ci = headers.index("Apply") + 1
        col = _col_letter(ci)
        links = [[f'=HYPERLINK("{r["machine"].get("url", "")}","Apply ↗")'
                  if r["machine"].get("url") else ""] for r in ordered]
        if links:
            values_update(sheet_id, f"{_q(tab)}!{col}{FIRST_DATA_ROW}:{col}{end_row}",
                          links, raw=False)
    return len(ordered)


def write_board(store: dict, sheet_id: str = SHEET_ID_DEFAULT,
                generated_at: str = "") -> dict:
    """Render the board. Returns {'counts': {...}, 'human': {cid: {...}}}.

    Does NOT mutate `store`. The returned 'human' map is a LATE read-back — taken
    immediately before writing rather than at the top of the refresh — so the window
    in which an edit he makes on his phone could be overwritten is seconds, not the
    couple of minutes a harvest takes. Callers should merge it into the JSON store.
    """
    human = read_back_human(sheet_id)

    merged = {}
    for cid, entry in store.items():
        h = dict(entry.get("human", {}) or {})
        h.update(human.get(cid, {}))
        merged[cid] = {"machine": entry.get("machine", {}) or {}, "human": h}

    buckets = _route(merged)
    _ensure_tabs(sheet_id, {t: len(buckets[t]) for t in TABS})

    counts = {}
    for tab in TABS:
        counts[tab] = _sync_tab(sheet_id, tab, buckets[tab])

    values_update(sheet_id, f"{_q(TAB_META)}!A1:B6", [
        ["schema_version", SCHEMA_VERSION],
        ["generated_at", generated_at],
        ["queue", counts[TAB_QUEUE]],
        ["applications", counts[TAB_APPS]],
        ["reviewed", counts[TAB_REVIEWED]],
        ["store_total", len(store)],
    ])
    return {"counts": counts, "human": human}


# ── formatting ────────────────────────────────────────────────────────────────
# Idempotent, and deliberately NOT part of write_board: it is ~40 batchUpdate
# requests and none of it changes between runs. Call it when the layout changes.
from build_curated_xlsx import (  # noqa: E402  (palette lives with the xlsx board)
    PRIORITY_OPTS, STATUS_FILL, STATUS_OPTS, TIER_FILL,
)

_WIDTHS = {
    TAB_QUEUE: {"🔥": 34, "Hot": 46, "Fit": 44, "Why": 260, "Tier": 44, "Priority": 70,
                "Status": 104, "Company": 130, "Role": 300, "Lane": 62, "Location": 150,
                "Cycle": 92, "Posted": 88, "Age": 46, "Apply": 68, "Source": 90,
                "Notes": 240},
    TAB_APPS: {"Status": 104, "Company": 140, "Role": 300, "Lane": 62, "Location": 160,
               "Cycle": 92, "Apply": 68, "Applied": 92, "Source / Referral": 140,
               "Notes": 320},
    TAB_REVIEWED: {"Status": 96, "Company": 140, "Role": 290, "Lane": 62,
                   "Location": 150, "Cycle": 92, "Fit": 44, "Why": 250, "Apply": 68,
                   "Notes": 300},
}
_WRAP_COLS = {"Why", "Role", "Notes", "Location"}


def _rgb(hex6: str) -> dict:
    h = hex6.lstrip("#")
    return {"red": int(h[0:2], 16) / 255, "green": int(h[2:4], 16) / 255,
            "blue": int(h[4:6], 16) / 255}


def ensure_format(sheet_id: str = SHEET_ID_DEFAULT) -> int:
    """Header styling, frozen row, hidden _id, widths, wrapping, Status/Priority
    dropdowns, and per-status colour rules. Safe to re-run: existing conditional
    rules on the managed tabs are dropped first so they cannot accumulate."""
    info = _sheets("GET", "?fields=sheets.properties,sheets.conditionalFormats",
                   sheet_id=sheet_id)
    meta = {}
    for s in info.get("sheets", []):
        p = s["properties"]
        meta[p["title"]] = (p["sheetId"], len(s.get("conditionalFormats", []) or []))

    reqs: list[dict] = []
    for tab in TABS:
        if tab not in meta:
            continue
        sid, n_rules = meta[tab]
        headers = _HEADERS[tab]
        ncols = len(headers)

        for _ in range(n_rules):            # clear old rules (always index 0)
            reqs.append({"deleteConditionalFormatRule": {"sheetId": sid, "index": 0}})

        reqs.append({"repeatCell": {
            "range": {"sheetId": sid, "startRowIndex": 0, "endRowIndex": 1,
                      "startColumnIndex": 0, "endColumnIndex": ncols},
            "cell": {"userEnteredFormat": {
                "backgroundColor": _rgb("1F2937"),
                "textFormat": {"bold": True, "fontSize": 10,
                               "foregroundColor": _rgb("FFFFFF")},
                "verticalAlignment": "MIDDLE"}},
            "fields": "userEnteredFormat(backgroundColor,textFormat,verticalAlignment)"}})

        reqs.append({"updateDimensionProperties": {
            "range": {"sheetId": sid, "dimension": "COLUMNS",
                      "startIndex": 0, "endIndex": 1},
            "properties": {"hiddenByUser": True}, "fields": "hiddenByUser"}})

        for i, h in enumerate(headers):
            w = _WIDTHS.get(tab, {}).get(h)
            if w:
                reqs.append({"updateDimensionProperties": {
                    "range": {"sheetId": sid, "dimension": "COLUMNS",
                              "startIndex": i, "endIndex": i + 1},
                    "properties": {"pixelSize": w}, "fields": "pixelSize"}})
            if h in _WRAP_COLS:
                reqs.append({"repeatCell": {
                    "range": {"sheetId": sid, "startRowIndex": 1,
                              "startColumnIndex": i, "endColumnIndex": i + 1},
                    "cell": {"userEnteredFormat": {"wrapStrategy": "CLIP",
                                                   "verticalAlignment": "TOP"}},
                    "fields": "userEnteredFormat(wrapStrategy,verticalAlignment)"}})

        for hname, opts in (("Status", STATUS_OPTS), ("Priority", PRIORITY_OPTS)):
            if hname not in headers:
                continue
            i = headers.index(hname)
            reqs.append({"setDataValidation": {
                "range": {"sheetId": sid, "startRowIndex": 1,
                          "startColumnIndex": i, "endColumnIndex": i + 1},
                "rule": {"condition": {"type": "ONE_OF_LIST",
                                       "values": [{"userEnteredValue": o}
                                                  for o in opts if o]},
                         "showCustomUi": True, "strict": False}}})

        if "Status" in headers:
            i = headers.index("Status")
            col = _col_letter(i + 1)
            for label, (bg, fg) in STATUS_FILL.items():
                reqs.append({"addConditionalFormatRule": {"index": 0, "rule": {
                    "ranges": [{"sheetId": sid, "startRowIndex": 1,
                                "startColumnIndex": i, "endColumnIndex": i + 1}],
                    "booleanRule": {
                        "condition": {"type": "TEXT_EQ",
                                      "values": [{"userEnteredValue": label}]},
                        "format": {"backgroundColor": _rgb(bg),
                                   "textFormat": {"foregroundColor": _rgb(fg),
                                                  "bold": True}}}}}})
        if "Tier" in headers:
            i = headers.index("Tier")
            for label, (bg, fg) in TIER_FILL.items():
                reqs.append({"addConditionalFormatRule": {"index": 0, "rule": {
                    "ranges": [{"sheetId": sid, "startRowIndex": 1,
                                "startColumnIndex": i, "endColumnIndex": i + 1}],
                    "booleanRule": {
                        "condition": {"type": "TEXT_EQ",
                                      "values": [{"userEnteredValue": label}]},
                        "format": {"backgroundColor": _rgb(bg),
                                   "textFormat": {"foregroundColor": _rgb(fg),
                                                  "bold": True}}}}}})
        reqs.append({"setBasicFilter": {"filter": {"range": {
            "sheetId": sid, "startRowIndex": 0, "startColumnIndex": 0,
            "endColumnIndex": ncols}}}})

    if reqs:
        batch_update(sheet_id, reqs)
    return len(reqs)
