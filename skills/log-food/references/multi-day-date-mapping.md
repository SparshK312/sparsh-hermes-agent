# Multi-Day Date Mapping: Before You Log

## The Pitfall

When a user backfills meals across 3+ days without explicit date labels on each item (e.g. "Saturday I had X, then Y for lunch, then dinner was Z, and Sunday morning was a protein shake…"), it's dangerously easy to assume the wrong date or assign a meal to a date after you've already started writing to a different date.

**Real failure from 2026-06-23 session:**
1. User described meals from June 21 (Sunday), June 22 (Monday), and June 23 (Tuesday) in sequence.
2. Agent logged Monday's breakfast/snacks/dinner to a June 23 file.
3. Agent then mistakenly logged Sunday's sushi lunch to June 23, then realized it belonged to June 21.
4. Agent moved it to June 21, which already had other Sunday meals, causing a frontmatter total that included sushi twice.
5. User caught it, agent had to backtrack and clean the file.

The root cause: **Agent did not establish a date-map before starting nutrient lookups and writes.** Instead, it inferred dates from narrative cues mid-stream, and misaligned them.

## The Pattern: Date-Map Before Writing

When a user provides multiple meals from multiple days in one turn:

1. **Extract the meal list with narrative cues intact:**
   ```
   Meal 1: egg & sausage sandwich for breakfast
   Meal 2: roti with daal makhani, chicken, seekh kebab for lunch
   Meal 3: BeaverTail pastry as snack
   Meal 4: Alpha Shawarma chicken plate for dinner
   Meal 5: sushi bowl (rice, miso salmon, 6 gyoza, cucumber)
   Meal 6: Sunset Grill Eggs Sunset, half poutine, chicken kebab snacks, Medidari pasta + chicken lettuce wrap
   Meal 7: vanilla Fairlife shake
   ```

2. **Ask for explicit date clarification BEFORE starting nutrient lookups:**
   ```
   Got it. Let me make sure I map these correctly:
   
   • Breakfast (eggs & sausage), Roti lunch, BeaverTail snack, Alpha Shawarma dinner → which day?
   • Sushi bowl (rice, salmon, gyoza, cucumber) → which day?
   • Sunset Grill breakfast, poutine/chicken snacks, Medidari dinner → which day?
   • Fairlife shake → which day?
   ```
   
   User responds: "Sunday June 21 for the first four, sushi was also Sunday, breakfast and poutine was Monday June 22, dinner pasta was Monday, fairlife was today Tuesday June 23."

3. **Build a canonical date-map:**
   ```
   June 21 (Sunday):
     - Breakfast: egg & sausage sandwich
     - Lunch: roti with daal makhani, chicken, seekh kebab
     - Lunch: sushi bowl (rice, miso salmon, 6 gyoza, cucumber)
     - Snack: BeaverTail pastry
     - Dinner: Alpha Shawarma chicken plate
   
   June 22 (Monday):
     - Breakfast: Sunset Grill Eggs Sunset (partial)
     - Snack: half poutine
     - Snack: chicken seekh kebab & Lahori tikka (few pieces)
     - Dinner: Medidari pasta + chicken lettuce wrap
   
   June 23 (Tuesday):
     - Beverage: Fairlife vanilla shake
   ```

4. **NOW proceed with nutrient lookups and vault writes — one date at a time.** Process June 21 completely, then June 22, then June 23. No jumping between dates mid-stream.

5. **If a correction arrives mid-stream** (e.g. user says "actually the sushi was Monday, not Sunday"), **revert the already-written meal** via `vault_log.py undo-last-meal` and re-assign it to the correct date. Do NOT write a meal to two different dates by hand — use undo + re-log.

## Practical Implementation

**Trigger:** User sends a message with 4+ meals spanning more than one day, OR explicitly says "I'm logging meals from the past few days."

**Pre-flight check (before any `mcp_food_tracker.search_food` calls):**
- Extract the narrative structure and any explicit date/time cues.
- If ambiguous (e.g. user says "Saturday I had X, then Y, then the next day was Z" but doesn't label which days are which), ask for clarification.
- Confirmation reply should be bulletted, clear, and **ask the user to confirm the date-map back to you** before proceeding with nutrient lookups.

**Write order:**
- Process by date, oldest to newest.
- For each date, run steps 1–7 of the main log-food skill (template match → nutrient lookup → clarify → vault write).
- Only after all meals for a date are written and confirmed, move to the next date.

**Undo pattern (if a meal gets assigned to the wrong date):**
```bash
/usr/bin/python3 /home/hermes/.hermes/scripts/vault/vault_log.py undo-last-meal
# (re-reads the last food-log entry, backs out its macros from the daily note, removes it from the food log)
```
Then re-log the same meal against the correct date with `--date YYYY-MM-DD`.

## Signals That Warrant a Date-Map Check

- User sends 4+ meals in one turn.
- User explicitly says "backfill", "catch up", "this past weekend", "from the past few days".
- User uses relative date language ("yesterday", "a few days ago") without a clear anchor.
- Any message with meals from more than one calendar date.

## What NOT to Do

❌ Assume the date from a relative cue (e.g. "yesterday I had…") without confirming what "yesterday" is in calendar terms.
❌ Jump between dates mid-stream. Finish one date's meals before moving to the next.
❌ Assign a meal to a date after the user corrects it without using `undo-last-meal` first.
❌ Write a meal to the vault before asking the user to confirm the date-map.

## Session Reference (2026-06-23)

User provided meals from June 21, 22, 23 in narrative order without explicit date labels on each item. Agent inferred dates, made mistakes, and had to backtrack. The correction overhead would have been eliminated by a 30-second date-map clarification upfront.
