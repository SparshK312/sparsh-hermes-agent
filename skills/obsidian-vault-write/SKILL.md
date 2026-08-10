---
name: obsidian-vault-write
description: Safe append/update patterns for Sparsh's UofT Obsidian vault. Knows the YAML frontmatter conventions, Dataview-friendly field values, and exact section/table structures used by Action Items, Internship Pipeline, Health & Fitness, Daily Notes, Perfecti Task Triage, and Shopify MOC. Use this skill any time an agent (Claude Code or Hermes) writes into the vault to prevent corruption of dashboards or breaking Dataview queries.
version: 1.0.0
platforms: [linux, macos]
metadata:
  hermes:
    tags: [obsidian, vault, markdown, frontmatter]
    category: productivity
---

# obsidian-vault-write

## When to Use

Any time an agent writes to or modifies a markdown file under `/Users/sparshk/Documents/School Vault - UofT/`. This includes:

- Pre-filling daily notes (`04 - Daily Notes/YYYY-MM-DD.md`)
- Appending rows to dashboards (Action Items, Internship Pipeline, Health & Fitness, Perfecti Task Triage)
- Updating frontmatter (`last_updated`, `status`)
- Creating new course or internship notes

**Do not use for read-only operations.** Reading the vault doesn't need this skill.

## Vault Path

`/Users/sparshk/Documents/School Vault - UofT/` — referenced as `$VAULT` below.

## Frontmatter Schema

Every note has YAML frontmatter. Field values are constrained — Dataview queries depend on them.

**`type`** — must be one of:
`lecture`, `assignment`, `reading`, `moc`, `daily`, `weekly-review`, `exam-prep`, `project`, `dashboard`, `action-items`, `study-schedule`, `grade-tracker`, `war-room`, `pipeline`, `prep-tracker`, `tracker`, `triage`, `degree-roadmap`, `life-context`

**`status`** — must be one of:
`not-started`, `in-progress`, `draft`, `complete`, `submitted`, `active`, `deferred-active`, `upcoming`, `completed`

**`date`** / **`last_updated`** / **`due_date`** — format `YYYY-MM-DD` always. Convert relative dates (`tomorrow`, `Monday`) to absolute before writing.

**`course`** — exact course code: `ECE311`, `ECE345`, `ECE361`, `ECE421`, `CSC384`, `ECE472` (no spaces, no `H1`, no section numbers).

**`tags`** — hierarchical: `course/ECE311`, `topic/Bode-plots`, `category/career`. Use forward slash, not nesting.

When updating a file, **bump `last_updated` to today's date** (Toronto local), but **never modify `created` or `date`**.

## Dashboard-Specific Rules

### Cross-file consistency

When a material state shift affects more than one dashboard, update the source of truth and the task surfaces together. Common pattern:

- If a build/setup goal is already complete, reflect it in the relevant master context note and remove or convert stale backlog items that still treat it as pending.
- If a priority changes materially, don't just add a new bullet — retitle the relevant section/stream to match the new urgency so the dashboard reads correctly at a glance.
- Always append the matching `Log.md` entry in the same edit pass.

### `00 - Dashboard/Action Items.md`

- **Five sections, in fixed order:** `## Hard Deadlines (next 7 days)`, `## This Week`, `## 2 Weeks Out`, `## Rolling/Background`, `## Completed (last 14 days)`.
- Append new items to the appropriate section based on urgency. **Never reorder sections.**
- Item format: `- [ ] **<Day Mon D>** — <action> [<context tag>]`
- Mark complete by changing `[ ]` → `[x]` AND moving to `## Completed (last 14 days)` with completion date.
- Tasks older than 14 days in Completed section get archived (deleted from this file). Do this once per week, not on every write.

### `00 - Dashboard/Internship Pipeline.md`

Two main tables. Identify which one to write to:

**Rotation Pipeline table** (status across the 4-rotation plan):
```
| Rotation | Period | Status |
```
Update the Status column when a rotation flips state (`🎯 Targeting` → `🟡 In progress` → `✅ Confirmed`). Don't add rows.

**Active Companies table:**
```
| Company | Applied | OA Done | Phone Screen | Onsite/Loop | Offer | Status |
```
- Status emoji vocabulary: `✅` done/passed, `⏳` awaiting, `❌` rejected, `🎯` targeting, `—` not applicable yet.
- New row only when adding a brand-new company. Sort alphabetically within status tier.

**New Postings section** (for Hermes scraper output):
- Lives under `## New Postings (last 7 days)`. If section doesn't exist, create it just above `## Active Companies`.
- Format: `- **YYYY-MM-DD** [<Company>](<url>) — <role title> · <location> · <period>`
- Prune entries older than 7 days on each write to this section.

### `00 - Dashboard/Health & Fitness.md`

**Status:** `deferred-active` until `2026-05-11`, then `active`. Don't write daily logs before activation date.

Three append-only tables:

**Bodyweight Log:** `| Date | Weight | Notes |` — append one row per day. Weight in kg or lbs (be consistent — check existing rows).

**Workout Log:** `| Date | Session | Key lifts | Notes |` — `Key lifts` format: `Squat 3×5 @ 100kg, Bench 3×5 @ 70kg`.

**Daily Macros:** `| Date | kcal | Protein (g) | Notes |` — append one row per day.

If the same date already has a row in any table, **update the existing row, don't append a duplicate.**

### `00 - Dashboard/Perfecti Task Triage.md`

Hand-maintained priority sections. When updating:
- Add status flags (`✅ done`, `⏳ in-progress`, `❌ blocked`, `🟡 outsourced`) at the start of the task line.
- Don't reorder priority sections (#1, #2, etc.) — the priority order is human-set.
- New items go into existing appropriate section, not at the top.

### `04 - Daily Notes/YYYY-MM-DD.md`

Created from `Templates/Daily Note.md`. Schema:

```markdown
---
date: "YYYY-MM-DD"
type: daily
---
# <Day of week, Month D, YYYY>

## Schedule
| Time | Activity |
| --- | --- |
| | |

## Tasks
- [ ]

## Notes


## End of Day Reflection
**What went well?**
**What could improve?**
**Tomorrow's priorities:**
```

**Pre-fill rules:**
- **Schedule table:** populate from Google Calendar events for that day. One row per event. Time format: `9:00–10:30`. Sort chronologically.
- **Tasks checklist:** lift open `Hard Deadlines (next 7 days)` items from Action Items where the date matches today. Format: `- [ ] <action>`.
- **Notes section:** **leave empty.** Never pre-fill.
- **End of Day Reflection:** **leave the three prompts as-is, never pre-fill answers.**

End-of-day capture (6 PM cron):
- Append a `## Shipped Today` section after Tasks. Bullet list, agent-generated from session activity / git log / vault changes.
- Carry-over check: tasks still unchecked at 6 PM → ping Telegram so user can decide rollover vs drop.

### `06 - Internships/Shopify 2026 Summer/Shopify 2026 MOC.md`

**Key Facts table** is canonical reference — only update fields when verified (e.g., benefits status after onboarding). Don't speculate.

**Pre/Post-start TODOs** — flip checkboxes only after observed completion.

## Wikilink Conventions

- `[[Note Name]]` — link by exact filename (no `.md` extension).
- `[[Note Name|Display Text]]` — alias display.
- For internal sections: `[[Note Name#Section Header]]`.
- Don't escape spaces, Obsidian handles them.

## Math Notation

- Inline: `$x = 5$`
- Display: `$$\\sum_{i=1}^{n} x_i$$`
- Don't double-escape backslashes when writing from agent code.

## Pitfalls

1. **Breaking Dataview queries** — changing `type` or `status` values to ones outside the allowed set silently drops the note from dashboard tables. Always validate against the schema above.
2. **Duplicate `last_updated`** — if frontmatter already has the field, replace it; don't add a second.
3. **Trailing whitespace in tables** — Obsidian renders fine, but some Dataview filters break on inconsistent column widths. Run a quick column-align if you've inserted a wide cell.
4. **Date typos** — `2026-05-3` vs `2026-05-03`. Always pad to two digits.
5. **Daily note overwrites** — if a file at `04 - Daily Notes/YYYY-MM-DD.md` already exists with user-authored content (anything in Notes, Schedule rows the user added, or Reflection answers filled), **never blast the whole file**. Read first, identify section boundaries, append only to safe sections.
6. **Paginated reads before writes** — if you only inspected a file with an offset/limit slice, re-read the exact frontmatter block or the full file before overwriting fields. Partial reads are fine for inspection, but not as the sole source of truth for a write.
7. **Correcting earlier placeholders** — if the user says an earlier value was made up / wrong, treat the correction as authoritative and overwrite the same field in place; do not preserve the placeholder in the note.
8. **Toronto timezone** — vault dates are always Toronto local (America/Toronto). When agents run on UTC backends, convert before writing.
9. **Course code spaces** — `CSC 384` (with space) breaks `course/CSC384` tags. Always strip spaces in frontmatter / tags.

## References

- `references/daily-note-corrections.md` — concise pattern for authoritative user corrections to daily-note values.

## Verification

After any write:
1. Run `grep -c '^---$' <file>` — should return exactly 2 (one frontmatter delimiter pair).
2. For dashboards, run a quick visual diff (`git diff` if vault is git-tracked, otherwise `diff` against a backup) — confirm only the intended section/row changed.
3. Open the file in Obsidian and verify the note still renders (no broken frontmatter shown as plain text).
4. For files referenced by Dataview, open the dashboard and confirm the row appears as expected.

## Log the Change (Karpathy LLM Wiki convention)

After every successful vault write (excluding trivial edits — typo fixes, formatting-only, single-character changes), append a one-line entry to `$VAULT/Log.md`:

```
## [YYYY-MM-DD] <action> | <scope> — <one-line summary>
```

**Action vocabulary:**
- `ingest` — new file created in the wiki layer (dashboard, MOC, context doc, internship folder, archive doc)
- `update` — meaningful state change to an existing wiki page (grade confirmed, decision revised, plan updated)
- `decision` — decision recorded for the first time
- `archive` — file moved to `05 - Archive/`
- `lint` — health-check pass results
- `schema` — change to `CLAUDE.md`, `Index.md`, `Log.md`, or maintenance rules

**Scope** is the human-readable name of what changed: `Grade Tracker`, `Internship Pipeline`, `Shopify 2026 MOC`, `Action Items`, etc.

**Hermes-invoked context:** when this skill runs from a Hermes cron job (not an interactive Claude Code session), prefix the scope with the cron job name:

```
## [2026-05-12] ingest | hermes:daily-note-prefill — 2026-05-12 daily note seeded with calendar events
## [2026-05-12] update | hermes:internship-watcher — 3 new postings appended to Internship Pipeline
```

The cron job identifier matches the entry in `~/.hermes/cron/jobs.json` (e.g., `daily-note-prefill`, `internship-watcher`).

**Append, never edit prior entries.** If correcting a previous entry, write a new one that references the prior date. Order is chronological top-to-bottom; newest at bottom.

**When in doubt, log.** The cost of an extra log line is near zero; the cost of a missed state change is a vault that drifts from its own history.

**Verify:** `grep "^## \[" "$VAULT/Log.md" | tail -3` should show the entry you just appended.

When `Index.md` also needs updating (new file created, file archived to a new path), update both `Index.md` and `Log.md` in the same operation. See `$VAULT/CLAUDE.md` → Wiki Maintenance Rules.

## Append-Only Discipline

This skill enforces **append-only writes by default**. To replace or delete user-authored content, the agent must:
1. Show the user the diff first
2. Get explicit confirmation
3. Then execute

The exception is agent-authored content the agent itself wrote previously (Hermes pre-filled Schedule rows, scraper-appended New Postings). Those are owned by the agent and can be replaced.
