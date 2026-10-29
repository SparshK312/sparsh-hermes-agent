"""readme_analytics.py — the ANALYTICS block on the board's hand-kept READ ME tab.

WHY (2026-10-29, his ask): he wanted numbers and charts on the board (how many applied,
rejected, OAs, Winter vs Summer...) without opening a spreadsheet tool. The READ ME tab is
hand-kept: the refresh never writes it (it is not in board_tabs.TABS), so anything here
survives every refresh.

Everything below is a LIVE FORMULA over My Applications (and the queue tabs), so the
numbers move the moment a Status changes or a refresh lands. This script only lays the
block out; re-running it is safe (it clears its own rows and replaces its own charts, all
titled with the 📊 prefix, and never touches rows 1-26, his text).

    python3 readme_analytics.py            # (re)build the block
"""
from __future__ import annotations

import build_curated_gsheet as G

TAB = "READ ME"
APPS = "'My Applications'"
S = f"{APPS}!$B$2:$B$1000"      # Status
C = f"{APPS}!$H$2:$H$1000"      # Cycle
J = f"{APPS}!$J$2:$J$1000"      # Applied (ISO text)
TOP = 28                        # first row of the block (1-based); rows above are his text

WAIT = "^Applied$"
PROC = "^(Recruiter Outreach|OA - To Do|OA - Done|Phone Screen|Technical Interview|Onsite|Final Round - Sat|Networking|On Hold)$"
INTV = "^(Phone Screen|Technical Interview|Onsite|Final Round - Sat|Offer|Rejected after Interview)$"
PAST = "^(Recruiter Outreach|OA - To Do|OA - Done|Phone Screen|Technical Interview|Onsite|Final Round - Sat|Offer|Rejected after OA|Rejected after Interview)$"
REJ = "^(Rejected|Rejected after OA|Rejected after Interview)$"
OFFER = "^Offer$"
CLOSED = "^(Closed|Skip|Not a Fit)$"
WS = "Winter 2027|Spring 2027"


def n(rx: str, extra: str = "") -> str:
    return f'SUMPRODUCT(({S}<>"")*REGEXMATCH({S},"{rx}"){extra})'


TOTAL = f'COUNTIF({S},"?*")'
ACTIVE = f"({TOTAL}-{n(CLOSED)})"


def cyc(kind: str) -> str:
    if kind == "ws":
        return f'*REGEXMATCH({C},"{WS}")'
    if kind == "summer":
        return f'*REGEXMATCH({C},"Summer 2027")*NOT(REGEXMATCH({C},"{WS}"))'
    return f'*NOT(REGEXMATCH({C},"{WS}|Summer 2027"))'


def dates() -> str:
    return f"IFERROR(DATEVALUE({J}),0)"


STAGES = [
    ("Waiting to hear back", "^Applied$"),
    ("Recruiter outreach", "^Recruiter Outreach$"),
    ("OA to do", "^OA - To Do$"),
    ("OA done", "^OA - Done$"),
    ("Phone screen", "^Phone Screen$"),
    ("Technical interview", "^Technical Interview$"),
    ("Onsite / final booked", "^Onsite$"),
    ("Final round sat", "^Final Round - Sat$"),
    ("Offer", "^Offer$"),
    ("Rejected (no interview)", "^Rejected$"),
    ("Rejected after OA", "^Rejected after OA$"),
    ("Rejected after interview", "^Rejected after Interview$"),
    ("Closed / skipped", CLOSED),
]


def build_rows() -> list[list]:
    rows: list[list] = []

    def put(r: int, vals: list):
        while len(rows) < r - TOP + 1:
            rows.append([])
        rows[r - TOP] = vals

    put(TOP, ["📊 ANALYTICS"])
    put(TOP + 1, ["Live from My Applications. Every number here updates by itself when a Status changes or a refresh lands."])
    put(TOP + 2, ['="As of "&TEXT(NOW(),"ddd mmm d, h:mm AM/PM")'])

    r = TOP + 4
    put(r, ["HEADLINE", "", ""]); r += 1
    head = [
        ("Applications (all time)", f"={TOTAL}", "every row on My Applications"),
        ("Applied in the last 7 days", f"=SUMPRODUCT(({dates()}>=TODAY()-6)*({dates()}<=TODAY()))", "by Applied date"),
        ("Still waiting to hear back", f"={n(WAIT)}", "status Applied"),
        ("In process right now", f"={n(PROC)}", "OA, screens, interviews, recruiter threads"),
        ("Offers", f"={n(OFFER)}", ""),
        ("Rejected", f"={n(REJ)}", "cold + after OA + after interview"),
        ("Closed / skipped", f"={n(CLOSED)}", "not counted in the rates below"),
        ("Heard back rate", f"=IFERROR(({ACTIVE}-{n(WAIT)})/{ACTIVE},0)", "any answer at all"),
        ("Got past the résumé screen", f"=IFERROR({n(PAST)}/{ACTIVE},0)", "OA, recruiter, interview or offer"),
        ("Reached an interview", f"=IFERROR({n(INTV)}/{ACTIVE},0)", "screen or later"),
        ("Left to apply: Winter queue", "=COUNTA('Apply - Winter'!$A$2:$A)", "rows on Apply - Winter"),
        ("Left to apply: Summer queue", "=COUNTA('Apply - Summer'!$A$2:$A)", "rows on Apply - Summer"),
    ]
    head_first = r
    for lab, f, note in head:
        put(r, [lab, f, note]); r += 1
    rates = [head_first + 7, head_first + 8, head_first + 9]

    r += 2
    stage_hdr = r
    put(r, ["BY STAGE", "Count"]); r += 1
    for lab, rx in STAGES:
        put(r, [lab, f"={n(rx)}"]); r += 1
    stage_last = r - 1

    r += 2
    term_hdr = r
    put(r, ["BY TERM", "Total", "Waiting", "In process", "Rejected", "Offer"]); r += 1
    for lab, k in (("Winter / Spring 2027", "ws"), ("Summer 2027", "summer"), ("Other / no term", "other")):
        x = cyc(k)
        put(r, [lab,
                f'=SUMPRODUCT(({S}<>""){x})',
                f"={n(WAIT, x)}", f"={n(PROC, x)}", f"={n(REJ, x)}", f"={n(OFFER, x)}"]); r += 1
    term_last = r - 1

    r += 2
    week_hdr = r
    put(r, ["APPLICATIONS PER WEEK", "Applied"]); r += 1
    for k in range(9, -1, -1):
        mon = f"TODAY()-WEEKDAY(TODAY(),3)-{7 * k}"
        put(r, [f'="Week of "&TEXT({mon},"mmm d")',
                f"=SUMPRODUCT(({dates()}>={mon})*({dates()}<{mon}+7))"]); r += 1
    week_last = r - 1

    r += 2
    co_hdr = r
    put(r, [f"=QUERY({APPS}!$D$2:$D$1000,\"select D, count(D) where D <> '' group by D order by count(D) desc limit 12 label D 'MOST APPLIED-TO COMPANIES', count(D) 'Applications'\",0)"])
    r += 14
    build_rows.layout = dict(head_first=head_first, rates=rates, stage_hdr=stage_hdr, stage_last=stage_last,
                             term_hdr=term_hdr, term_last=term_last, week_hdr=week_hdr, week_last=week_last,
                             co_hdr=co_hdr, last=r)
    return rows


def grid(sid: int, r0: int, r1: int, c0: int, c1: int) -> dict:
    """1-based inclusive rows, 0-based columns [c0, c1)."""
    return {"sheetId": sid, "startRowIndex": r0 - 1, "endRowIndex": r1,
            "startColumnIndex": c0, "endColumnIndex": c1}


def chart(sid, title, ctype, dom, series, row, stacked=False, legend="NO_LEGEND"):
    spec = {"title": title, "basicChart": {
        "chartType": ctype, "legendPosition": legend, "headerCount": 1,
        "domains": [{"domain": {"sourceRange": {"sources": [dom]}}}],
        "series": [{"series": {"sourceRange": {"sources": [s]}},
                    "targetAxis": "BOTTOM_AXIS" if ctype == "BAR" else "LEFT_AXIS"} for s in series],
    }}
    if stacked:
        spec["basicChart"]["stackedType"] = "STACKED"
    return {"addChart": {"chart": {"spec": spec, "position": {"overlayPosition": {
        "anchorCell": {"sheetId": sid, "rowIndex": row - 1, "columnIndex": 7},
        "widthPixels": 560, "heightPixels": 320}}}}}


def main() -> None:
    sid_ = G.SHEET_ID_DEFAULT
    meta = G._sheets("GET", "?fields=sheets(properties(sheetId,title,gridProperties),charts(chartId,spec.title))",
                     sheet_id=sid_)
    sheet = next(s for s in meta["sheets"] if s["properties"]["title"] == TAB)
    sid = sheet["properties"]["sheetId"]
    rows = build_rows()
    L = build_rows.layout

    reqs = [{"deleteEmbeddedObject": {"objectId": ch["chartId"]}}
            for ch in sheet.get("charts", []) if ch.get("spec", {}).get("title", "").startswith("📊")]
    need = L["last"] + 10
    if sheet["properties"]["gridProperties"]["rowCount"] < need:
        reqs.append({"updateSheetProperties": {"properties": {"sheetId": sid, "gridProperties": {"rowCount": need}},
                                               "fields": "gridProperties.rowCount"}})
    if reqs:
        G.batch_update(sid_, reqs)

    G.values_clear(sid_, f"{G._q(TAB)}!A{TOP}:Z{need}")
    G.values_update(sid_, f"{G._q(TAB)}!A{TOP}", rows, raw=False)

    bold = {"userEnteredFormat": {"textFormat": {"bold": True}}}
    section = {"userEnteredFormat": {"textFormat": {"bold": True, "foregroundColor": G._rgb("#5f6368")}}}
    fmt = [
        {"repeatCell": {"range": grid(sid, TOP, TOP, 0, 1), "cell": {"userEnteredFormat": {"textFormat": {"bold": True, "fontSize": 14}}},
                        "fields": "userEnteredFormat.textFormat"}},
        {"repeatCell": {"range": grid(sid, TOP + 1, TOP + 2, 0, 1), "cell": {"userEnteredFormat": {"textFormat": {"italic": True, "foregroundColor": G._rgb("#5f6368")}}},
                        "fields": "userEnteredFormat.textFormat"}},
        {"repeatCell": {"range": grid(sid, L["head_first"], L["head_first"] + 11, 1, 2), "cell": bold, "fields": "userEnteredFormat.textFormat"}},
        {"repeatCell": {"range": grid(sid, L["head_first"], L["head_first"] + 11, 2, 3),
                        "cell": {"userEnteredFormat": {"textFormat": {"italic": True, "foregroundColor": G._rgb("#80868b")}}},
                        "fields": "userEnteredFormat.textFormat"}},
    ]
    for rr in L["rates"]:
        fmt.append({"repeatCell": {"range": grid(sid, rr, rr, 1, 2),
                                   "cell": {"userEnteredFormat": {"numberFormat": {"type": "PERCENT", "pattern": "0%"}, "textFormat": {"bold": True}}},
                                   "fields": "userEnteredFormat(numberFormat,textFormat)"}})
    for hr, width in ((L["head_first"] - 1, 3), (L["stage_hdr"], 2), (L["term_hdr"], 6), (L["week_hdr"], 2), (L["co_hdr"], 2)):
        fmt.append({"repeatCell": {"range": grid(sid, hr, hr, 0, width), "cell": section, "fields": "userEnteredFormat.textFormat"}})
    fmt.append({"updateDimensionProperties": {"range": {"sheetId": sid, "dimension": "COLUMNS", "startIndex": 1, "endIndex": 6},
                                              "properties": {"pixelSize": 105}, "fields": "pixelSize"}})
    fmt.append({"updateDimensionProperties": {"range": {"sheetId": sid, "dimension": "COLUMNS", "startIndex": 0, "endIndex": 1},
                                              "properties": {"pixelSize": 250}, "fields": "pixelSize"}})
    charts = [
        chart(sid, "📊 Where every application stands", "BAR",
              grid(sid, L["stage_hdr"], L["stage_last"], 0, 1), [grid(sid, L["stage_hdr"], L["stage_last"], 1, 2)], L["head_first"] - 1),
        chart(sid, "📊 Outcomes by term", "COLUMN",
              grid(sid, L["term_hdr"], L["term_last"], 0, 1),
              [grid(sid, L["term_hdr"], L["term_last"], c, c + 1) for c in (2, 3, 4, 5)], L["stage_hdr"] + 4,
              stacked=True, legend="BOTTOM_LEGEND"),
        chart(sid, "📊 Applications per week", "COLUMN",
              grid(sid, L["week_hdr"], L["week_last"], 0, 1), [grid(sid, L["week_hdr"], L["week_last"], 1, 2)], L["term_hdr"] + 8),
    ]
    G.batch_update(sid_, fmt + charts)
    print(f"READ ME analytics written: rows {TOP}-{L['last']}, 3 charts")


if __name__ == "__main__":
    main()
