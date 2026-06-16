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
  - Brand shorthand should resolve to the user's default unless they specify otherwise. Example: **"fairlife" = Core Power 26g** in this vault unless the user says 42g / Elite / another flavor.
- `meal_type` — one of `breakfast`, `lunch`, `dinner`, `snack`, `shake`. Infer from:
  - Time of day (5–10 AM = breakfast; 11 AM–2 PM = lunch; 5–9 PM = dinner; otherwise snack)
  - User's explicit word ("lunch was…" → lunch)
  - If ambiguous, ask in the confirmation step.

### 2. Resolve items against the PANTRY first (reuse — don't re-research)

**Before any web/MCP lookup, check what Sparsh has already logged.** The pantry caches every item he's confirmed (his own numbers), so a repeat is instant and consistent. Make ONE call with all the items:

```
/usr/bin/python3 /home/hermes/.hermes/scripts/vault/food_pantry.py resolve --item "2 eggs" --item "fairlife" --item "..."
```

It returns JSON `{"hits": [...], "misses": [...]}`:
- **hits** = already known. Reuse the returned macros directly (already scaled to the quantity you passed). No lookup. A `"match":"fuzzy"` hit (e.g. "fairlife" → "Fairlife Core Power") is still reused — but surface it in the confirm step so he can catch a wrong match.
- **misses** = genuinely new → only THESE go to the template check (2b) / MCP lookup (step 3).

Do NOT read `pantry.json` by hand — call the script. This one call replaces the dozen `search_food` lookups that used to run for a repeat meal.

### 2b. Try fuzzy-match against saved templates (for whole-meal repeats)

Read `07 - Health/Meal Templates/*.md`. For each, the frontmatter has `name`, `meal_type`, `items`, and macros.

Fuzzy-match strategy (in priority order):
1. **Exact name match.** User said "chicken bowl" and a template `chicken-bowl` exists → match.
2. **Item-set overlap.** User's items ≥ 60% overlap with a template's items → strong match.
3. **Semantic similarity.** User said "cafeteria breakfast" and a `cafeteria-breakfast-light` or `cafeteria-breakfast-heavy` template exists → present BOTH as candidates and let the user pick.

If high-confidence single match → use the template's macros, skip the MCP lookup. Set `source: template:<name>`.

If multiple plausible matches → ask user which one before proceeding.

If no match → proceed to step 3.

**Branded / packaged items:** if an official product nutrition page exists and the brand/product is identifiable (Vector, Lay's, fairlife, restaurant label, etc.), prefer that exact serving macro over a generic USDA proxy. Scale from the stated serving size. If the user later corrects the portion, recompute from the same exact product page instead of swapping to a different database entry.

### 3. Look up macros — ONLY for the pantry misses

**Only look up items the pantry didn't already resolve.** Accuracy matters, so genuinely-new items DO get a real lookup — but do it efficiently: **one good source per item**, not five searches chasing the same thing.

For each MISS in `items[]`:
1. Call `mcp_food_tracker.search_food(query=item)` — returns USDA matches with kcal + macros. **This is the only MCP tool you may call. See the Forbidden tools section above.**
2. Use the best matching canonical entry. If the user gave a quantity (e.g. `2 eggs`), scale it.
3. For composite / restaurant / cafeteria dishes, try the dish name first, then obvious component queries when the dish itself is too fuzzy (e.g. `pad kra prow pork`, `ground pork`, `fried egg`, `thai chicken wing`, `vegetable spring roll`). Prefer partial canonical coverage over pretending the whole plate is one guessed item.
4. If a packaged snack/photo shows a readable nutrition label, use the label macros directly before doing any generic lookup. If the label is visible but blurry, prefer the brand/official product page next.
5. If a sub-item still has no clean match or the query is too ambiguous, fall back to LLM-estimate for that sub-item only, mark `source: estimated`, and flag exactly which part was estimated in the confirmation.
6. (Phase 2) `opennutrition.search_foods(query=item)` for the 300k DB — disabled in Phase 1.

Sum the macros across items.

See `references/mixed-meal-lookup.md` for the lookup pattern and confirmation wording for mixed meals and packaged snacks. See `references/mexican-restaurant-photo-logging.md` for restaurant-photo handling (enchilada-plate restaurants, chips/salsa exclusions, and photo-first timing). See `references/mexican-office-bowl-photo.md` for an office Mexican bowl photo pattern, including visible beverage-can exclusion and conservative mixed-plate portioning. See `references/office-breakfast-photo-pattern.md` for cafeteria/office breakfast trays with waffles, bacon, eggs, and fruit bowls. See `references/retrospective-day-summary.md` for whole-day “yesterday I had…” recaps that should be bucketed once and logged against the implied date. See `references/travel-weekend-recap.md` for camping / road-trip / off-grid recaps that span multiple days and need per-day bucketing. See `references/branded-packaged-foods.md` for branded/packaged or chain products with an official nutrition page — prefer the official page over generic USDA matches and scale to the stated portion. See `references/date-correction-flow.md` for how to handle explicit date corrections and blank out the mistakenly assigned day when needed. See `references/compound-health-bundles.md` for messages that combine food, supplements, and water in one turn; split the domains and persist each via the right log before replying.

### 3b. Duplicate / correction handling before confirmation

If the user sends a second meal message within a few minutes that materially matches a meal already logged today, do **not** assume it is a fresh meal. Compare the new description/photo against the latest food-log entry:
- If it looks like the same meal, ask whether it is a **correction** or an **addition**.
- If the user included a new photo plus the same caption/food description, treat it as a likely correction check, not a new meal by default.
- If the user explicitly says "log it again", "add another", or similar, proceed as a new entry.
- If the user re-sends the meal from a different angle or as a follow-up image, default to *same meal* unless they explicitly frame it as an addition.

This guard prevents duplicate counts when the user re-sends the same breakfast from a different angle or wants the estimate revised.

If the user answers with a terse correction like "Nah only 1 meal" / "same meal" / "just one", treat that as a correction signal and do not create a second log.

If the user corrects the *food itself* after a meal was already logged — e.g. "actually it was a grilled chicken sandwich, not a burger" — rewrite the existing meal entry in place, recompute the meal macros, and update daily totals from the delta. Do **not** append a second meal unless the user explicitly says it was an addition.

If the user gives a partial portion correction instead of a full rewrite — e.g. "less rice", "only 2 gyozas", "skip the sauce", "remove the salad" — keep the unchanged items and revise only the named components. Re-run the estimate and confirmation with the updated portions rather than asking them to restate the whole meal.

See `references/duplicate-correction-flow.md`, `references/photo-correction-confirmation.md`, `references/post-confirmation-correction.md`, `references/correction-after-estimate.md`, and `references/leftover-carry-over.md` for the session pattern.


### 4. Confirmation flow (MANDATORY — never skip)

**This step is non-negotiable.** Even if the meal seems unambiguous, you MUST surface the macros for confirmation before any vault write. The user wants a chance to catch quantity/item mistakes before they pollute the rolling totals.

If the user responds with a correction (e.g. "exclude turkey bacon", "actually it was X", "edit", "too much", "only one"), do **not** write yet. Rebuild the item list, recompute macros, and run the confirmation step again. A correction is *not* implicit approval to save the previous estimate.

See `references/photo-correction-confirmation.md` for the exact session pattern.

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

**This is the ONLY persistence step. The MCP DB does not count as "logged" — only the vault does.**

**Do the write with the deterministic vault writer — `vault_log.py`. Do NOT hand-edit the YAML** (no `patch` on frontmatter, no `python3 -c`, no heredocs, no `execute_code`): those trip Hermes' approval gate (so you'd nag Sparsh to "approve a command" for every meal), are hard-blocked in cron, and corrupt repeated `key:` lines. ONE command does BOTH writes below safely — it appends the Food Log section + re-sums its totals AND increments the daily-note macros:

```
/usr/bin/python3 /home/hermes/.hermes/scripts/vault/vault_log.py food \
  --meal-type <breakfast|lunch|dinner|snack|shake> \
  --kcal <total_kcal> --protein <g> --carbs <g> --fat <g> \
  --item "<item 1>|<kcal>|<protein>|<carbs>|<fat>" \
  --item "<item 2>|<kcal>|<protein>|<carbs>|<fat>" \
  --source "<pantry | template:name | mcp:food-tracker | estimated>" \
  --coach \
  [--time "1:30 PM"] [--date YYYY-MM-DD]
```

- `--kcal/--protein/--carbs/--fat` are the **confirmed meal totals** (they win over the items). The repeated `--item` flags give the per-item breakdown; **pass every item with its macros** — the script caches each one to the pantry, so next time it's an instant hit (this is how reuse builds up). Macros after the name are optional (`--item "guacamole"` is fine for a name-only line, but it won't cache).
- **`--coach`** appends a 1-line pace/protein nudge from the day's real totals — always pass it; that's his post-log feedback. Relay the whole output verbatim.
- `--time` defaults to now; `--date` defaults to today (Toronto) — pass it for retrospective / past-midnight logs.
- It prints `✓ Logged …` + the coach line — **send that as your reply; don't recompute or pad it.**

### 🚫 Speed rules (this skill used to take 60 tool calls for one meal — don't)
- **Never** `read_file` the daily note or the food log, **never** `search_files` for them, **never** `patch` them, **never** `write_file` them, **never** `execute_code`. `vault_log` owns all of that. Reading/searching/patching the vault by hand is the #1 source of bloat.
- **Never** use `execute_code` or `python3 -c` to add up macros — pass the `--item` numbers and let `vault_log` sum them, or add them yourself.
- Don't re-run `search_food` on an item the pantry already resolved, and don't chase one item across multiple web searches. Pantry → at most one lookup per miss → write. That's it.

**Corrections** ("less rice", "actually it was X", "wrong, only one serving"): don't hand-edit. Run `vault_log.py undo-last-meal` to remove the just-logged meal (it subtracts the macros too), then re-log the corrected version with the `food` command above. Two clean calls, no patching.

The file formats below are **reference for what the script produces** — you don't hand-write them.

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
6. **Whole-day retrospective recaps.** If the user gives a past-day meal dump in one message (e.g. breakfast + dinner + snack from yesterday), bucket everything first, confirm once, and log the whole day against the implied date. Do not turn the recap into multiple independent confirmations.

6a. **Mixed off-grid weekend dumps.** When the message also includes sleep, workouts, travel, weather, or "why I wasn't tracking" context, still keep food logging focused on the eaten items and treat the extra context as inputs for the other specialized skills. Parse the food first, then let workout/sleep logs catch up from the same narrative if needed. Don't ask the user to restate the whole weekend in separate messages.

6a. **Single retro meal / relative date phrasing.** If the user says "last night", "yesterday", "earlier today", or similar and the intended date is unambiguous from context, log against the implied local-date file rather than the current date. Preserve the user's relative phrasing in the meal header only when the exact clock time is unknown; otherwise convert to a normal time header for the food log and use the implied date for the daily-note update.

6b. **Leftover / half-portion carry-over.** If the user says they ate the *other half* or *remaining half* of a meal already logged earlier, treat the new intake as a continuation of that same plate, not a fresh composition.
- Reuse the earlier meal's item identity and scale the portion to the remaining fraction (usually 50%) unless the user says otherwise.
- If the earlier meal was already logged today, update today's running totals from the same item breakdown; if it was logged on a prior day, mirror that prior estimate and halve it.
- Do not ask the user to restate the full dish when the carry-over is explicit (e.g. "this is the second half").
7. **Sum errors.** Always re-sum the food-log file totals after each meal append. Don't trust an incremental counter that could drift.
8. **Duplicate / correction ambiguity.** If the user re-sends a meal they already logged today — especially with a new photo of the same plate or the same item list — stop and ask whether it is a correction or an addition. Do not double-count silently.
9. **AYCE buffet plate.** Treat as a meal with multiple items; ask user to describe roughly ("rice, paneer, salad, 2 rotis") rather than estimating from "cafeteria lunch" alone. Saved templates ("cafeteria-lunch-normal") are the way to make this fast.

10. **Wrong day / date rollover.** Always resolve against the live Toronto date before writing. If it is past midnight, log to the new date even when the conversation just referenced "today" or the prior meal.
10b. **Explicit date corrections beat the current day.** If the user says a meal belongs to yesterday / another date, move the entry to the corrected day in place. If the wrong-day placeholder already got written, restore that day to blank/zeroed totals rather than leaving a phantom entry behind. See `references/date-correction-flow.md`.
11. **Frontmatter integer vs float.** kcal should be int. protein_g/carbs_g/fat_g should be int (round to nearest gram). water_l is float (1 decimal).
12. **Time-only corrections are edits, not new meals.** If the user later says the logged meal happened at a different time (for example, "Lunch was at 2:30 pm"), update the existing food-log section header and append a correction line to Log.md. Do **not** create a second meal entry or recompute macros unless the food itself changed.
13. **Photo → notes-only anti-pattern (observed bug 2026-05-27).** When the user sends a photo with a brief caption ("Had this for breakfast"), the agent's lazy path is: call vision_analyze → save the description to the daily note's `## Notes` section → reply "Saved." → done. **THAT IS WRONG.** The macros never get written, week-summary stays empty, the failure-mode detection breaks. The correct path is: vision_analyze → synthesize items → run THIS skill end-to-end (template match → MCP search → clarify → write to BOTH Food Log file AND daily-note frontmatter `kcal`/`protein_g`/`carbs_g`/`fat_g`). The ## Notes section is for qualitative observations ("food felt heavy", "didn't like the dressing"), not the canonical macro record.
14. **Vision-identified meals get template suggestions.** If a photo-logged meal looks recurring (cafeteria breakfast, regular lunch spot), the template-creation prompt in step 6 should fire more eagerly than for text-logged meals — recurring photographable meals are the strongest template candidates because the user has clear visual context to validate the template once.
15. **Promised photo / incremental meal detail.** If the user says a photo is coming or keeps adding components to the same meal, keep the session open and consolidate into one estimate. Don't lock a partial text-only estimate unless they explicitly say to log the partial meal now.
16. **Shared chips/salsa/condiments.** If chips, salsa, or table condiments are visible in a restaurant photo, do not count them unless the user explicitly says they ate them.
18. **Conservative portion bias.** If the user says the estimate feels a little large but otherwise accurate, tighten the portion to the smaller plausible amount rather than re-anchoring at the midpoint. Keep the same food identity unless the user explicitly changes the item list.
