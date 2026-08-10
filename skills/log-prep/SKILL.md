---
name: log-prep
description: 'Log an interview-prep rep when Sparsh reports prep done — coding problems, mocks, system-design/ML reading, behavioral stories, debugging reps. Triggers on any prep-done report in text or voice. Examples: "did Two Sum and Valid Anagram", "knocked out 3Sum, took 35 min", "did a Pramp mock", "read the system design framework", "wrote my founder STAR story", "/prep Two Sum". Checks off the matching item(s) in the Prep Tracker (00 - Dashboard/Interview Prep.md), appends a Session-log row, and (for coding problems) adds a Redo-list row with a spaced re-solve date. The dashboard is NOT under the vault-write-guard, so edit it directly (patch / obsidian-vault-write). This is the accountability log that pairs with the prep-nudge cron.'
version: 1.0.0
platforms: [linux, macos]
metadata:
  hermes:
    tags: [career, interview-prep, vault-write, accountability]
    category: career
---

# log-prep

## When to use
Any time Sparsh reports interview-prep done — a LeetCode problem, a mock, reading a framework, drafting a STAR story, a debugging rep. Pairs with the **prep-nudge** cron (which reads this same tracker). Target context: he's prepping for **Mercor** + bigger-brand interviews; see [[Technical Interview Study Plan]].

Examples:
- "did Two Sum and Valid Anagram" → check both off, log, add both to redo (re-solve tomorrow)
- "knocked out 3Sum, ~35 min, sliding window finally clicked" → check off, log with time + note, add to redo
- "did a Pramp mock" → check a Mock item, log
- "read the system design framework" / "wrote my founder STAR story" → check the matching Phase item, log
- "re-solved Two Sum from memory" → it's a redo; mark that redo row done OR bump its re-solve date forward (1d → 3d → 7d)

Do NOT use for: planning ("gonna do leetcode tonight" — wait until done), or non-prep logging (health → the log-* health skills).

## The tracker
`00 - Dashboard/Interview Prep.md` → the **📋 Prep Tracker** section. It has:
- Checklist items as `- [ ] Name · [ ] Name2 · …` (multiple per line) across DS&A / Behavioral / System design / ML / Mocks / Debugging.
- A **🔁 Redo list** table: `| Problem | Pattern | First solved | Re-solve due | ✓ |`.
- A **📝 Session log** table: `| Date | What | ~Time | Notes |`.
Only touch the LIVE tracker (above the `## 🗄️ [ARCHIVED …]` marker), never the archived May plan.

## ⚡ How to log — follow exactly
1. **The date is GIVEN** in the turn context (`Today = YYYY-MM-DD`). Use it exactly — never run `date` or guess. Spaced re-solve dates: +1 day (first), then +3, then +7.
2. **Read** the tracker, find the item(s) he named (fuzzy-match the problem/topic name).
3. **Edit directly** (the dashboard is NOT guarded — use `patch` / obsidian-vault-write; do NOT use vault_log.py, that's health-only):
   - **Check off** each matching `[ ]` → `[x]` (match the specific item text in its line).
   - **Append a Session-log row:** `| <date> | <what he did> | <~time or blank> | <note or blank> |`.
   - **Coding problems only — add a Redo-list row:** `| <Problem> | <Pattern> | <date> | <date+1> | |` (first re-solve due tomorrow). If it's a *re-solve* of an existing redo row, instead bump that row's "Re-solve due" forward (1d→3d→7d) or mark ✓ if it's the 3rd clean re-solve.
4. **Log immediately — NO confirmation menu.** Write it, then show a terse readout.

## The readout (after logging)
Terse, markdown. Show: ✓ what you logged · new progress (X/N) · current streak · what's due for re-solve next · optionally the next unchecked item. Example:
> ✓ Logged: **Two Sum**, **Valid Anagram**. Progress 2/45 · streak 1 day 🔥 · re-solve both tomorrow. Next up: **Group Anagrams**.

Keep his voice in mind for any prose (plain, matter-of-fact). Encourage lightly, don't be a cheerleader.

## Edge cases
- **Item not found in the tracker** (he did something not on the list): still append a Session-log row + note it; suggest adding it to the plan if it recurs. Don't fabricate a checkbox.
- **Multiple problems in one session (same message or across messages):** check off all. Create ONE Session-log row with all problems listed (e.g., "Two Sum, Contains Duplicate, Valid Anagram"). Add a separate redo row for EACH problem — all with "First solved" = today, "Re-solve due" = today+1 initially. Example:
  - Session log: `| 2026-06-27 | Two Sum, Contains Duplicate, Valid Anagram | — | Batch session, Arrays/Hash pattern |`
  - Redo rows: one per problem, same date.
- **Ambiguous** ("did some leetcode"): ask one short question (which problems?) rather than guessing which to check off.
- Never check off more than he actually reported.
