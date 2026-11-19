---
name: log-food
description: 'THE meal-logging skill for Sparsh. Invoke this for ANY meal/snack/shake input — text descriptions ("had eggs and toast", "lunch was a chicken sandwich", "ate rice and fish"), photos of meals (with or without a caption like "had this for breakfast" / "lunch from the cafeteria"), keyboard taps ("🍽️ Log meal"), or slash commands ("/log_food <desc>", "log a meal", "logged dinner"). For PHOTO input: call vision_analyze first to identify the items, then proceed with template-match + nutrient lookup, log it, and show the breakdown (no confirmation gate). For ALL input types: write canonical record to 07 - Health/Food Log/<date>.md AND increment daily-note frontmatter totals (kcal, protein_g, carbs_g, fat_g). NEVER save a meal to just the ## Notes section of the daily note — that loses macro tracking. The vault is the single source of truth — do NOT use food-tracker''s log_food / get_daily_log / get_summary / set_goals / delete_entry tools (those write to a parallel SQLite DB that drifts from the vault).'
version: 1.5.1
platforms: [linux, macos]
metadata:
  hermes:
    tags: [health, food, nutrition, vault-write, mcp, vision]
    category: health
    requires_toolsets: [food-tracker]
---

# log-food

Log what he ate into the vault, fast and the same way every time. The vault is the only record: `vault_log.py food` (run through `terminal`) writes the Food Log file and the daily-note totals in one go. **If a reference file disagrees with this page (a confirmation step, a menu of buttons, extra searches, hand edits), this page wins.**

## The flow, in order

1. **Date.** The turn context gives it: `[Current date — America/New_York] Today = YYYY-MM-DD …` plus recent days. Use that string. Never run `date`, never guess.
2. **Items.** Split the message into meals, and each meal into items named `<amount> <food>` and nothing else: `"3 chicken leg pieces"`, `"0.5 chicken bowl"`, `"1 protein shake"`. No notes in the name (no `(office)`, `(half)`, `x3`, `~250g`); where it came from goes in `--source`. Meal type is `breakfast`, `lunch`, `dinner`, `snack` or `shake`: use his word, else the time of day (5–10 AM breakfast, 11 AM–2 PM lunch, 5–9 PM dinner, otherwise snack). If he taps "🍽️ Log meal" with nothing else, ask what he ate.
3. **Pantry first, ONE call for every item in the message:**
   ```
   /usr/bin/python3 /home/hermes/.hermes/scripts/vault/food_pantry.py resolve --item "2 eggs" --item "1 protein shake" --item "..."
   ```
   `hits` are his own confirmed numbers, already scaled: reuse them. Keep the unit he said (`200 g rice` and `1 cup rice` are stored separately). A `"match":"fuzzy"` hit is still used, but name it in the reply so he can catch a wrong match; a fuzzy hit whose matched name is a different place, brand or size from what he said is a miss, not a hit. `misses` go to step 4. `{"error": "pantry-unreadable"}`: treat everything as a miss, log normally, tell him in one line. Never read `pantry.json` by hand.
4. **Look up ONLY the pantry misses, at most one lookup per miss.** Anything the pantry resolved is settled, including restaurant dishes: do not search for it again.
   - A readable nutrition label in a photo beats any lookup: use its numbers.
   - A miss that is a named restaurant, chain or branded product: ONE `web_search` for its published numbers (`"<place> <dish> nutrition calories protein"`), use them, and say so. If the search gives only part of the numbers (calories only, a range, a protein figure), anchor your estimate to that figure; never rebuild the dish from generic ingredients (a bowl was once estimated from generic chicken-breast weights at almost four times the protein its restaurant lists).
   - Any other miss: the `mcp_food_tracker.search_food` tool with `query=<item>`. It is a tool, not a Python module: never import it or call it from `terminal`. For a composite dish with no clean match, look up its obvious components (the one exception to one lookup per miss; at most three).
   - If that lookup does not give numbers, estimate that item, mark it `estimated`, and say which part was estimated. Do not run more searches, open nutrition PDFs or read cached pages for the same item. Pantry, then one lookup per miss, then write.
5. **Write. One `terminal` call for the whole message.** One command per meal, chained with `&&` when the message holds several meals, with any supplement and water commands in the same chain:
   ```
   /usr/bin/python3 /home/hermes/.hermes/scripts/vault/vault_log.py food \
     --meal-type <breakfast|lunch|dinner|snack|shake> \
     --kcal <total> --protein <g> --carbs <g> --fat <g> \
     --item "<amount food>|<kcal>|<protein>|<carbs>|<fat>" \
     --item "<amount food>|<kcal>|<protein>|<carbs>|<fat>" \
     --source "<pantry | template:name | mcp:food-tracker | web:<place> | vision | estimated>" \
     --coach [--time "1:30 PM"] [--date YYYY-MM-DD]
   ```
   - Pass every item with its macros (that is what fills the pantry for next time). The totals you pass win over the items.
   - **Times.** One meal eaten just now: leave `--time` off. **When one message holds several meals, give each its own `--time`**: the time he said, else a typical time for that meal, and say in one line that the times are your guess. Never let two meals in one chain default to now: they would all be stamped the same minute, and two identical ones (two coffees, two shakes) would be treated as one.
   - `--date` defaults to today; pass it for any other day. Pass `--coach` for today's meals.
   - Supplements and water are not food; they have their own commands (include every supplement he named):
     ```
     /usr/bin/python3 /home/hermes/.hermes/scripts/vault/vault_log.py vitamins --supplements "Zinc, Magnesium" [--date YYYY-MM-DD]
     /usr/bin/python3 /home/hermes/.hermes/scripts/vault/vault_log.py water --ml 500 [--date YYYY-MM-DD]
     ```
     (`water` takes `--ml` or `--liters`. There is no `--amount`. A glass with no amount given = 500 ml.)
   - **If the chain stops partway** (an error, not a `↩︎` line), the commands before it are already written. Fix and re-run only the ones that did not print their `✓` / `💧` / `💊` line. Water is not de-duplicated, so never re-run a water command that already printed.
6. **Reply** with the per-item breakdown, then relay `vault_log`'s own running-total and coach line as printed (for several meals, only the last one):
   ```
   🍽️ Logged <meal_type> · <H:MM AM/PM>
     • <item> — <kcal> kcal / <P>g P / <C>g C / <F>g F
   Total: <kcal> kcal · <P>g protein · <C>g carbs · <F>g fat  (<source>)
   ```
   For several meals, one combined reply. Keep your working short: do the arithmetic yourself, don't narrate it, and never use `execute_code` or `python3 -c` to add up macros.

## Rules

- **No confirmation step.** Log, then show the breakdown; he corrects afterwards if it is off. Ask ONE short plain question only when you cannot tell what the item is or roughly how much. Never a `clarify` menu. If he says a photo or more detail is coming, wait for it and log once.
- **Only log what he has eaten.** A plan, a question, or a photo of a menu or sign alone is not a meal. A photo of a plate or packaged food with no caption counts as eaten: log it with the meal type from the time of day and say so. "I ate half, going to eat the other half later" (or "gonna eat the other half as well") means log HALF now (`0.5 <dish>`) and tell him the rest is not logged yet: "Logged the half you ate. Tell me when you have the rest." Never log food that has not been eaten, and never invent a time for it. When he later says he ate the other half, log the same dish at the remaining fraction, reusing the earlier numbers.
- **The only way food reaches the vault is `vault_log.py`.** Do not `patch`, `write_file`, `execute_code` or `python3 -c` the daily note, the Food Log or the pantry; a guard rejects it. To see a day, use `vault_log.py meals --date <date>` (index, time, type, kcal, protein). Do not read or search the vault files otherwise, with one exception under Corrections.
- **Only one MCP tool: `search_food`.** Never `log_food`, `get_daily_log`, `get_summary`, `set_goals` or `delete_entry`: they use a separate database that drifts from the vault.
- **Corrections.** Last meal of that day: `vault_log.py undo-last-meal [--date <date>]`, then re-log it with its original `--time`. Any other meal: `vault_log.py meals --date <date>`, then `vault_log.py remove-meal --index N --date <date>`, then re-log just that meal with its original `--time`. A partial change ("less rice", "only 2") keeps the other items and recomputes from the same source. A time-only change re-logs the same macros with the new `--time`. Wrong day: remove it there and re-log with `--date`. **If the meal's items and macros are not in this conversation, read that day's Food Log file once (read-only) to get them before removing it; that is the one allowed read.**
- **Duplicates.** The writer refuses an identical meal within 20 minutes and prints `↩︎ Already logged…`: relay that, do not retry or tweak values. Use `--force`, or distinct `--time` values, only when he himself reported the repeat (two coffees, a real second serving). If he re-sends a meal from earlier today (same items, or a new photo of the same plate), ask whether it is a correction or an addition; "same meal" or "only one" means no second log.
- **Past days.** "Yesterday", "last night", a whole-day recap: bucket into meals and log each against the implied date with its own `--time`, one combined reply. Past midnight, "today" is the new date.
- **Estimating.**
  - "Had eggs" = 2. A brand name on its own means his usual product from that brand, and a drink he always takes the same way uses that default, unless he says otherwise.
  - A named restaurant platter with sauces: use the full platter, not the bare components, and lean high (a shawarma-chain chicken and rice platter is roughly 700–850 kcal, not 530). Mark it `estimated (full platter)`.
  - "Full plate", "loaded", "heaping", "filling": a large serving, about 30–50% above a standard one, home-cooked included. Do not ask first.
  - Shared chips, salsa or table condiments in a photo are not counted unless he says he ate them.
  - If he says an estimate feels a bit large, move to the smaller plausible amount; keep the same foods.
  - Food lookup tool down: estimate, mark `estimated (MCP down)`, do not block the log.
- **Numbers.** kcal, protein, carbs and fat are whole numbers; water is litres to one decimal.
- **No vault log entry per meal.** Only for a correction to a past day: `/usr/bin/python3 /home/hermes/.hermes/scripts/vault/log_append.py update "log-food" "<what was corrected, which date>"`.
- A threshold just crossed (protein reached 150 g): say so in one line.

## Read a reference only when it applies

Load with `skill_view("log-food", "references/<file>")`. They hold detail and history; where one asks for a confirmation step, a button menu, extra searches or a hand edit, follow this page.

| When | File |
|---|---|
| The message has a photo | `photo-input.md` (call `vision_analyze` on each image first, then this flow; never save a photo meal as notes only) |
| He names a meal, not its items ("the usual", "cafeteria breakfast", "my chicken bowl"), and the pantry misses it | `meal-templates.md` |
| A correction that the rules above do not settle | `pitfalls-full.md`, `duplicate-correction-flow.md`, `meal-type-correction-flow.md` |
| A recap covering several days or a trip | `travel-weekend-recap.md` |
| A branded or packaged product | `branded-packaged-foods.md` |
| Portion wording you are unsure how to size | `portion-size-language-and-home-meals.md` |
| What the files on disk look like | `file-formats.md` |

Do **not** use this skill for plain water (`log-water`), supplements with no food (`log-vitamins`), listing saved templates (the `meal-templates` skill), or notes about food he has not eaten (those go in the daily note's Notes via `obsidian-vault-write`).
