# Self-Captured Observations

This directory is where Hermes writes patterns + learnings it observes from real usage.

## Purpose

These files are NOT operational — they don't change skill behavior on their own. Writing here is the **safe** alternative to silently editing skill source files (which causes drift between the canonical repo and the live VPS, bypasses CI, and removes the human review gate).

If you (the agent) notice that:
- A skill description should be more emphatic
- A new routing rule would prevent a recurring bug
- A user-correction pattern keeps coming up
- A meal or workout pattern recurs that's worth a template

→ write it here as a markdown file. **Do NOT edit `skills/<name>/SKILL.md` directly on the VPS.**

A human sweeps this directory periodically and promotes useful patterns into:
- `config/SOUL.md` if it's a routing or write-scope rule
- `skills/<skill>/SKILL.md` if it's a skill-specific pattern
- `docs/CHANGELOG.md` if it's a system-level learning

## Format

- One file per topic. Filename = kebab-case slug (`meal-correction-cases.md`, `workout-incremental-logging.md`).
- Append-only. Date each new observation: `### 2026-MM-DD`.
- Be concrete: include the user message that triggered it, your interpretation, and the rule you'd propose.

## Example shape

```markdown
# Meal correction cases

### 2026-05-27

**Trigger:** User said "I didn't eat turkey link, I ate chicken sausage, and no English muffin either, it was a B.E.S.T sandwich"

**Observation:** After a meal is logged, the user often refines the description. The MCP-searched macros are then stale.

**Proposed rule for log-food:** When the user corrects an ingredient/brand/portion after a meal is already logged, treat the correction as authoritative — edit the existing vault entry in place, recompute macros, update daily-note frontmatter. Do NOT append a second entry unless explicitly stated as an additional item.

**Promotion candidate:** log-food/SKILL.md step 3b.
```

## What lives here vs Hermes' built-in memory

- `~/.hermes/memories/MEMORY.md` and `USER.md` — built-in auto-curation by Hermes itself. Prose facts the agent re-reads. Lightweight.
- `~/.hermes/memories/observations/` (this dir) — your structured proposals for changes to operational behavior. Heavier; require human review.

Two different layers; both fine.
