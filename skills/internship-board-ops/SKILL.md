---
name: internship-board-ops
description: Look up postings on Sparsh's internship board (did I apply to X? what's the status of Y? what should I apply to today?) and change a row's status. Every lookup goes through board.py show, which answers in ONE call for any number of fragments; every write goes through board.py applied/status/note, which refuses ambiguity and tells you the exact id to use next.
version: 2.0.0
platforms: [linux, macos]
metadata:
  hermes:
    tags: [internship, board, career, sheets]
    category: productivity
---

# Internship Board Operations

## Environment
- **VPS script root:** `~/.hermes/scripts/internship/`
- **Store (canonical, machine fields):** `~/.hermes/internship/curated_postings.json`
- **The Google Sheet owns the human fields** (status, applied date, notes, priority). board.py reads and writes the Sheet directly. **Never edit `curated_postings.json` to change a status** — the next render reads the Sheet back and overwrites it.
- **`composio` is NOT installed on the VPS.** Sheets access is board.py only.
- Run scripts as `python <name>.py …`. **Never `python -c "..."`** — the gateway's restart guard blocks it (it did, mid-loop, on 2026-09-21).

## Looking things up — `board.py show`, once, with every fragment

```bash
cd ~/.hermes/scripts/internship
python board.py show "Figma" "Waymo" "Robinhood" "Together AI" "Tesla People Products"
```

**One call, one Sheet fetch, every fragment answered.** For each fragment it prints one of:

| Output | Meaning |
|---|---|
| `→ N row(s) match:` | the fragment is literally in those rows |
| `→ N row(s) contain every word:` | every word you typed is in the row (punctuation/order ignored: "BS MS" finds "BS/MS") |
| `→ no exact match; N nearest row(s):` | no row has your wording; these share the company/team name. **This is the answer** — read the statuses. |
| `→ NOT ON THE BOARD` | no row on any tab mentions that company/team. Report it as not on the board. |

Each row line carries `[tab row N] Company — Role · Status=… Applied=… · id:<_id>`. A single hit prints the full block (Notes included).

**For "did I already apply to these?": pass the COMPANY NAMES, not the pasted titles.** `show "Figma"` returns every Figma row with its status; that is the whole answer. Pasted titles are retyped from memory and rarely match the row string — the matcher tolerates that, but the company name is the reliable handle.

🔴 **Do not reword and retry.** `show` never exits 1 for a miss or an ambiguity; whatever it printed IS the result. If a fragment came back `NOT ON THE BOARD`, that company is not on the board — say so. On 2026-09-21 a turn rewrote the same fragments 35 times over 15 minutes (41 model calls, $1.34) against the old tool, which refused ambiguity like a write; the rows it wanted were on the Sheet with their statuses the entire time. If two lookups in a row do not give you what you need, **stop and report what the tool printed.**

## Daily apply queue — `worklist.py`

```bash
python worklist.py
```
Ranked table: hotness, fit, tier, company, full role title, location, cycle, age, apply link. Filters fit ≥ 70, max 2 rows/company — the footer `Capped (…): X +N` means N more rows exist at that company; surface it. **Do not use `board.py list-live` for the queue** (truncated titles, no links).

Reading the table: `Hot` (brand tier × recency) is the priority signal across companies; `Fit` is brand-weighted, so same-company differences under ~10 points are noise; `Age` 0–2 d rows first.

## Changing a row — `board.py applied / status / note / priority`

```bash
python board.py applied  "<match>" --notes "..."                       # Applied + today's date
python board.py status   "<match>" "Rejected after OA" --notes "..."   # any status
python board.py status   "<match>" "OA - To Do" --due 2026-09-25       # a deadline he owes
python board.py note     "<match>" "<text>"                            # replaces Notes
python board.py priority "<match>" P0|P1|P2|P3|clear --notes "..."
```

**A write accepts exactly one row: a unique literal match, or a unique every-word match.** Anything else is refused with exit 1 and the candidates printed **with their `id:`** — copy the one he means and re-run with it:

```bash
python board.py status "id:boards.greenhouse.io/figma/jobs/6143238004" "OA - Done"
```

One refusal → one exact retry. Never a third attempt with a reworded fragment; if the id: list does not contain the row, it is not on the board.

**Status vocabulary** (the Sheet's dropdown; anything else renders broken):
`To Apply · Applied · OA - To Do · OA - Done · Phone Screen · Technical Interview · Onsite · Offer · Rejected · Rejected after OA · Rejected after Interview · Networking · On Hold · Skip · Not a Fit · Closed`
`Rejected` is cold; a rejection after an assessment or a live round gets its own status. The one source is `STATUS_OPTS` in `build_curated_xlsx.py`.

After a write the Sheet is updated immediately; the row re-routes between tabs on the next `curate.py` refresh (`Applied` moves it from Apply Now to My Applications). Do not move rows by hand and do not run the refresh to "check" — read the Sheet.

## Pitfalls
- `python -c` is blocked by the gateway guard — named scripts only.
- Fit scores are brand-weighted; eight Tesla roles scored 62–82 including one wanting embedded C. Hotness ranks across companies; fit does not rank within one.
- `worklist.py`'s 2-row/company cap — always surface the `+N` overflow.
- A row that is `Skip` / `Not a Fit` on Reviewed and `Applied` on My Applications is the same req picked up twice; the application row is the truth.
