# Chain Restaurant & Branded Food Lookup Pattern

When logging meals from chains (Taco Bell, McDonald's, Chipotle, Subway, Tim Hortons, etc.) or branded packaged foods (Fairlife, Lay's, Pepsi, etc.), **prefer the official brand/restaurant nutrition page over MCP + USDA.**

## Why Official > MCP

- **Accuracy:** Official nutrition pages are authoritative. MCP's USDA SR Legacy often has outdated entries, generic proxies, or no entry at all for variants.
- **Speed:** Direct link lookup is faster than MCP search + filtering. Fewer choices to disambiguate.
- **Variants:** Chains publish per-size, per-region, per-customization. MCP's single-entry model misses variants (e.g., "Taco Bell quesadilla" in MCP returned soft taco + burrito, not the grilled quesadilla).
- **Serve size clarity:** Official pages explicitly state the serving (1 quesadilla = 510 kcal, not per-100g guessing).

## Session Example: Taco Bell Combo (Jun 23, 2026)

**User input:** "chicken quesadilla combo from Taco Bell: quesadilla, fries, cheese dip, fire sauce, Baja Blast"

**Workflow:**
1. `web_search("Taco Bell chicken quesadilla calories")` → found tacobell.com official page + CalorieKing
2. `web_extract(tacobell.com/food/quesadillas/chicken-quesadilla)` → 500 cal (site says 500, CalorieKing says 510; use official 500 or note the discrepancy)
3. For each side item (fries, sauce, drink):
   - `web_search("Taco Bell seasoned fries")` → tacobell.com/food/deals-and-combos/nacho-fries → 290 cal
   - `web_search("Taco Bell nacho cheese sauce")` → tacobell.com/food/sauces/nacho-cheese-sauce → 60 cal
   - `web_search("Taco Bell Baja Blast large")` → tacobell.com/food/drinks/mtn-dew-baja-blast → 420 cal
4. For macros (if not on the main page), use CalorieKing or FatSecret (crowdsourced, often more detailed than the brand page itself):
   - CalorieKing: Chicken Quesadilla 510 kcal · 27g P · 38g C · 26g F
   - Estimate fries + sauce + drink ratios based on standard fast-food macro distribution
5. **Confirm total with user before logging.** Chain items are inherently high-sodium/high-processed, and portion clarity matters.
6. **Source tag:** `source: taco-bell-official` (not MCP)

**Result:** 1,290 kcal · 31g protein · 186g carbs · 45g fat. Confirmed + logged.

## Common Chain URLs

| Chain | Base URL | Format |
|-------|----------|--------|
| **Taco Bell** | `tacobell.com/food/` | Direct item links; nutrition in a sidebar |
| **McDonald's** | `mcdonalds.com/us/en-us/nutrition-facts-for-menu-items.html` | Master page lists all items + macros |
| **Chipotle** | `chipotle.com/menu` | Filter by item; nutrition shown inline |
| **Subway** | `subway.com/en-US/nutrition` | Master page; protein/macros per sub |
| **Tim Hortons** | `timhortons.com/menu` | Filter by item; calories shown inline |
| **Starbucks** | `starbucks.com/menu` | Nutrition behind a link (can require click) |
| **Fairlife** | `corepowerprotein.com` | Product pages show exact macros per serving size |

## Pitfalls

1. **Per-100g vs per-serving:** Official pages often show per-serving (1 quesadilla = 510 kcal). USDA is per-100g. Always convert USDA back to the food's natural serving before confirming.
2. **Regional variants:** McDonald's Egg McMuffin macros differ slightly between countries. If the user is in the US, use US.nutrition, not global.
3. **Customization math:** Taco Bell's site shows base nutrition + add-on deltas ("+30 Cal for cheese"). Build the total from the base + deltas. Do NOT guess compound modifications.
4. **Discrepancies (brand vs crowdsourced):** Sometimes the brand page and CalorieKing disagree by 10–20 cal. Use the brand page as primary; note the discrepancy in the confirmation if it's material (>50 kcal delta).
5. **Missing macros on brand page:** If the brand page shows calories but not full macros, backfill carbs/protein/fat from CalorieKing or FatSecret (both are typically accurate for branded items). Flag the mixed source in the confirmation.
6. **Large combo prices:** Chains sometimes don't break out individual items when selling a combo. If a "Quesadilla Combo" is sold as a bundle on the menu, look for the individual item pages to tally each part (quesadilla + fries + drink separately).

## Decision Tree

```
Is this a branded/chain item (Taco Bell, McDonald's, fairlife, Lay's, etc.)?
  ├─ YES → web_search + web_extract official brand page first
  │        If official page incomplete, backfill with CalorieKing/FatSecret
  │        Use official macros; mark source: <brand>-official
  │
  └─ NO → Is this a whole food or generic (eggs, rice, chicken breast)?
           ├─ YES → mcp_food_tracker.search_food()
           │        Use USDA entry + scale to quantity
           │
           └─ NO → Is this a composite/restaurant dish (pad thai, burrito bowl, salad)?
                    ├─ YES → Try dish name first in MCP
                    │        If fuzzy, break into components (rice, protein, veggies)
                    │        and estimate component-by-component
                    │
                    └─ NO → LLM estimate + flag source: estimated
```
