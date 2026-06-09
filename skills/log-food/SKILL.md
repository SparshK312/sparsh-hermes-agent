---
name: log-food
description: 'THE meal-logging skill for Sparsh. Invoke this for ANY meal/snack/shake input — text descriptions ("had eggs and toast", "lunch was a chicken sandwich", "ate rice and fish"), photos of meals (with or without a caption like "had this for breakfast" / "lunch from the cafeteria"), keyboard taps ("🍽️ Log meal"), or slash commands ("/log_food <desc>", "log a meal", "logged dinner"). For PHOTO input: call vision_analyze first to identify the items, then proceed with template-match + nutrient lookup + clarify confirmation. For ALL input types: write canonical record to 07 - Health/Food Log/<date>.md AND increment daily-note frontmatter totals (kcal, protein_g, carbs_g, fat_g). NEVER save a meal to just the ## Notes section of the daily note — that loses macro tracking. The vault is the single source of truth — do NOT use food-tracker''s log_food / get_daily_log / get_summary / set_goals / delete_entry tools (those write to a parallel SQLite DB that drifts from the vault).'
version: 1.2.0
platforms: [linux, macos]
metadata:
  hermes:
    tags: [health, food, nutrition, vault-write, mcp, vision]
    category: health
    requires_toolsets: [food-tracker]
---

# log-food

## When to Use

Any time the user describes, photographs, or otherwise reports a meal/snack/shake. **Photo-based meal input is first-class — do not route photos to obsidian-vault-write or "save as notes"; route them here.** Examples:

**Text input:**
- `/log_food 2 eggs, toast, and a banana`
- "lunch was the chicken bowl at the cafeteria"
- "had a protein shake — 25g whey in milk"
- "just finished dinner: salmon, rice, broccoli"
- "ate rice, fried fish, tortilla, nachos, guac" → multi-item meal
- "🍽️ Log meal" (keyboard button) → reply asking what they ate, then proceed

**Photo input** (single image or multiple — common at restaurants/cafeterias):
- Photo + "Had this for breakfast" → analyze image, log breakfast
- Photo of a plate, no caption → infer meal_type from time of day, ask "log this as <meal_type>?"
- Two photos: one of menu/sign + one of the plate → use both for identification (sign tells you item names, plate tells you portion)
- Cafeteria photo → likely a recurring meal; after 2 of these, prompt to save as a template (`shopify-cafeteria-breakfast-heavy` etc.)

**Combined input:**
- "Lunch from the cafeteria" + photo → photo identifies items, caption sets meal_type

Do **not** use for:
- Plain water — that's `log-water`
- Supplements without food — that's `log-vitamins`
- Future / hypothetical meals — only log what was eaten
- Listing existing templates — that's `meal-templates` (this skill USES templates for matching; doesn't list them)
- Free-form journal entries about food preferences/cravings (those go to the daily note's ## Notes section via obsidian-vault-write — but ANY mention of having actually eaten something belongs here)

## 🛑 Forbidden tools (architecture-critical)

The vault is canonical. The food-tracker MCP exists ONLY for nutrient lookup. **Do NOT call these MCP tools — they write to / read from a parallel SQLite DB and will silently drift from the vault:**

- ❌ `mcp_food_tracker.log_food` — NEVER call. Writes to MCP's DB, not the vault.
- ❌ `mcp_food_tracker.get_daily_log` — NEVER call. Read the vault's daily note instead.
- ❌ `mcp_food_tracker.get_summary` — NEVER call. Aggregate the vault.
- ❌ `mcp_food_tracker.set_goals` — NEVER call. Targets live in this skill's prose.
- ❌ `mcp_food_tracker.delete_entry` — NEVER call. Edit the vault.

**Only allowed MCP call:** `mcp_food_tracker.search_food(query=...)` for nutrient resolution in step 3. That's it.

## Step-by-step

### 0. Photo input — vision pre-processing (skip if input is text-only)

If the user attached one or more images:

1. **Call `vision_analyze`** on each image with a prompt like:
   `"Identify every food/drink item visible. List items with estimated quantity (e.g. '2 sausage links', '1 cup orange juice'). Include any visible menu/sign text. Be specific about portions."`
2. **Combine** the analyzed items across all images:
   - A sign + a plate photo → use the sign for item names (e.g., "b.e.s.t sandwich: basil aioli, egg, sausage, roast tomato, English muffin") + the plate for additional items (sausage links, fruit bowl, OJ).
   - Multiple plate angles → de-duplicate the same items.
3. **Synthesize an `items[]` list** as if the user had described the meal in text. Now proceed to step 1 with the synthesized list.
4. **In the clarify step (step 4)**, set `Source: vision (gpt-5.4-mini)` and include a note like `📸 Identified from photo — verify items before logging.` so the user can correct mis-identifications.

**Critical:** Photo-input meals MUST still go through steps 1-7 (template match → nutrient lookup → clarify → vault write). Do NOT save photo descriptions to the daily note's ## Notes section and skip the macro write — that loses tracking.

### 1. Extract meal items + meal type

Parse the user's input (text OR vision-synthesized list from step 0) into a structured form:
- `items[]` — a list of strings, each one food item with quantity if specified
  - "2 eggs and toast" → `["2 eggs", "1 slice toast"]`
  - "chicken bowl at cafeteria" → `["chicken bowl"]` (single unknown item — template-match candidate)
- `meal_type` — one of `breakfast`, `lunch`, `dinner`, `snack`, `shake`. Infer from:
  - Time of day (5–10 AM = breakfast; 11 AM–2 PM = lunch; 5–9 PM = dinner; otherwise snack)
  - User's explicit word ("lunch was…" → lunch)
  - If ambiguous, ask in the confirmation step.

### 2. Try fuzzy-match against saved templates

Read `07 - Health/Meal Templates/*.md`. For each, the frontmatter has `name`, `meal_type`, `items`, and macros.

Fuzzy-match strategy (in priority order):
1. **Exact name match.** User said "chicken bowl" and a template `chicken-bowl` exists → match.
2. **Item-set overlap.** User's items ≥ 60% overlap with a template's items → strong match.
3. **Semantic similarity.** User said "cafeteria breakfast" and a `cafeteria-breakfast-light` or `cafeteria-breakfast-heavy` template exists → present BOTH as candidates and let the user pick.

If high-confidence single match → use the template's macros, skip the MCP lookup. Set `source: template:<name>`.

If multiple plausible matches → ask user which one before proceeding.

If no match → proceed to step 3.

### 3. Look up macros via MCP (no template match)

For each item in `items[]`:
1. Call `mcp_food_tracker.search_food(query=item)` — returns USDA matches with kcal + macros. **This is the only MCP tool you may call. See the Forbidden tools section above.**
2. Take the top match by relevance. If the user provided a quantity (e.g., "2 eggs"), multiply.
3. If `search_food` returns nothing useful or the query is ambiguous (e.g., "chicken" — chicken what?), fall back to LLM-estimate (use gpt-5.4-mini's training knowledge), mark `source: estimated` and flag in the confirmation.
4. (Phase 2) `opennutrition.search_foods(query=item)` for the 300k DB — disabled in Phase 1.

Sum the macros across items.

### 4. Confirmation flow (MANDATORY — never skip)

**This step is non-negotiable.** Even if the meal seems unambiguous, you MUST surface the macros for confirmation before any vault write. The user wants a chance to catch quantity/item mistakes before they pollute the rolling totals.

Use Hermes' `clarify` tool (inline keyboard) with this exact shape:

```
🍽️ <meal_type capitalized> · <H:MM AM/PM>

Items:
  • <item 1> — <X> kcal / <Y>g P
  • <item 2> — …

Total: <total kcal> kcal · <P>g protein · <C>g carbs · <F>g fat
Source: <template:name | mcp lookup | estimated>
```

Options for the `clarify` call:
- `✓ Log it` → proceed to step 5 (write to vault)
- `✎ Edit` → user replies with corrections in next message; re-run steps 1-3 with corrected items, re-confirm. Loop until `✓` or `✗`.
- `✗ Cancel` → reply "Cancelled. Nothing logged." and end (no vault write).

**If `clarify` isn't available / the agent loop fails, fall back to a plain-text confirmation:** post the macros block to the chat with "Reply ✓ to log, ✎ to edit, ✗ to cancel" and wait for the next user message. Either path, **no vault write before the user's affirmative.**

### 5. Write to vault (on confirmation)

**This is the ONLY persistence step. The MCP DB does not count as "logged" — only the vault does.** You write to TWO files:

**A. Append to today's food log:** `07 - Health/Food Log/<date>.md` (create if missing). Create the parent directory `07 - Health/Food Log/` first if it doesn't exist.

File frontmatter (on creation):
```yaml
---
type: food-log
date: YYYY-MM-DD
total_kcal: 0
total_protein_g: 0
total_carbs_g: 0
total_fat_g: 0
last_updated: YYYY-MM-DD
---

# Food Log — <Day Mon D, YYYY>
```

Append a section per meal:
```markdown

## <H:MM AM/PM> · <meal_type>

**Items:**
- <item 1>
- <item 2>

**Macros:** <kcal> kcal · <P>g protein · <C>g carbs · <F>g fat
**Source:** <template:name | mcp:food-tracker | mcp:opennutrition | estimated>
```

After appending, recompute the file-level frontmatter totals (`total_kcal`, etc.) by summing all sections.

**B. Update today's daily note frontmatter:** `04 - Daily Notes/<date>.md`.

Read the current values of `kcal`, `protein_g`, `carbs_g`, `fat_g`. Add the new meal's macros. Write back.

```yaml
# before
kcal: 1450
protein_g: 88
# after a 600 kcal / 35g P meal
kcal: 2050
protein_g: 123
```

If any field is null/empty, treat as 0 and replace with the new value.

### 6. Suggest template creation (optional)

If this meal was logged via `source: mcp:*` OR `source: estimated` (i.e., not from an existing template), and the items list looks like a recurring meal (e.g., user has logged a similar item-set ≥2 times in the past 14 days), prompt:

```
This looks like a recurring meal. Save as a template?
[ ✓ Save as <suggested-name> ]  [ ✎ Rename ]  [ ✗ Skip ]
```

If saved, create `07 - Health/Meal Templates/<slug>.md` with the items, macros, meal_type, and `use_count: 1`. Subsequent matches bump `use_count` and refresh `last_used`.

### 7. Reply

After successful log:
```
✓ Logged <meal_type>: <total kcal> kcal · <P>g protein.
Today so far: <new daily kcal>/2400 · <new daily protein>/140g.
```

If a target threshold was crossed (e.g., protein just hit 140g) → flag it: `🎯 Protein target hit.`

## Vault-write conventions (follow obsidian-vault-write skill)

- Date format `YYYY-MM-DD` always.
- Toronto local time.
- Read-modify-write on the daily note — preserve unrelated frontmatter and body content.
- Food log file: append-only sections. Never reorder or delete prior entries.
- Template files: created once, then incremented (`use_count`, `last_used`). Never overwrite the macros after creation without explicit user instruction.

## Log the change

Append a `Log.md` entry — meal logs are real state changes:
```
## [YYYY-MM-DD] update | log-food — <meal_type> logged, <kcal> kcal / <P>g protein (source: <template/mcp/estimated>)
```

Skip Log.md only if the log was cancelled.

## Pitfalls

1. **Phantom logs.** NEVER write to the vault until the user confirms via the clarify flow. The confirmation is the contract.
2. **MCP unavailable.** If `food-tracker` or `opennutrition` is down (timeout), fall back gracefully: estimate via LLM and clearly flag `source: estimated (MCP down)`. Do not block the log.
3. **Duplicate-meal-same-time.** If two log-food invocations land within 5 minutes for the same meal_type, ask the user if it's a correction or an addition. Don't double-count silently.
4. **Quantity ambiguity.** "Had eggs" — 1 egg? 2? 3? Default to 2 (typical breakfast portion) but show it in the confirmation so the user can edit.
5. **Restaurant / cafeteria items.** No exact macros. Use LLM-estimate, flag clearly. Encourage saving as a template after a couple of logs to lock in a personal-baseline estimate.
6. **Sum errors.** Always re-sum the food-log file totals after each meal append. Don't trust an incremental counter that could drift.
7. **AYCE buffet plate.** Treat as a meal with multiple items; ask user to describe roughly ("rice, paneer, salad, 2 rotis") rather than estimating from "cafeteria lunch" alone. Saved templates ("cafeteria-lunch-normal") are the way to make this fast.
8. **Liquids that are food.** Protein shakes, smoothies, coffee with milk — these are meals. Log them via log-food, not log-water.
9. **Wrong day.** If logging close to midnight Toronto, double-check the date. Past midnight referring to "today" might mean yesterday's date.
10. **Frontmatter integer vs float.** kcal should be int. protein_g/carbs_g/fat_g should be int (round to nearest gram). water_l is float (1 decimal).
11. **Photo → notes-only anti-pattern (observed bug 2026-05-27).** When the user sends a photo with a brief caption ("Had this for breakfast"), the agent's lazy path is: call vision_analyze → save the description to the daily note's `## Notes` section → reply "Saved." → done. **THAT IS WRONG.** The macros never get written, week-summary stays empty, the failure-mode detection breaks. The correct path is: vision_analyze → synthesize items → run THIS skill end-to-end (template match → MCP search → clarify → write to BOTH Food Log file AND daily-note frontmatter `kcal`/`protein_g`/`carbs_g`/`fat_g`). The ## Notes section is for qualitative observations ("food felt heavy", "didn't like the dressing"), not the canonical macro record.
12. **Vision-identified meals get template suggestions.** If a photo-logged meal looks recurring (cafeteria breakfast, regular lunch spot), the template-creation prompt in step 6 should fire more eagerly than for text-logged meals — recurring photographable meals are the strongest template candidates because the user has clear visual context to validate the template once.
