# Unpublished-Menu Restaurant Lookup

When a restaurant/sandwich shop has NO official nutrition facts published (common for independent/local establishments), use component-level synthesis instead of guessing the whole meal as a single item.

## Pattern

1. **Identify visible/known ingredients** from the menu description or user description.
   - Example: Alfies Chicken Caesar = smoked chicken breast, focaccia roll, romaine, tomato, bacon, Caesar dressing.

2. **Estimate per-component quantity** based on:
   - Typical serving sizes for that food type.
   - User's cues ("half sandwich", "their sandwiches are big and heavy").
   - Visual reference (restaurant photos, social media posts, reviews mentioning portion size).
   - Portion standards (a slice of bacon ≈ 10–15g; a tbsp of dressing ≈ 15g; grilled chicken breast slice ≈ 30–40g).

3. **Look up each component** via MCP or USDA estimate (e.g., "grilled chicken breast 5 oz", "bacon 3 slices", "focaccia roll 100g").

4. **Sum component macros** and flag `source: estimated (menu sourcing)`.

## Example: Alfies Chicken Caesar (Half Sandwich)

**User input:** "Dinner was Alfies sandwich, the chicken Caesar sandwich. ... log half the sandwich cuz their sandwiches are big and heavy."

**Ingredients (from menu + user context):**
- Smoked chicken breast, grilled
- Focaccia or sub roll (half)
- Romaine lettuce + tomato
- Bacon (house-cured, 2–3 slices)
- Caesar dressing

**Component breakdown (half sandwich):**
| Component | Est. Quantity | Kcal | P (g) | C (g) | F (g) |
|-----------|---------------|------|-------|-------|-------|
| Chicken breast, grilled | 4–5 oz (120–140g) | 200–230 | 35–40 | 0 | 6–8 |
| Focaccia/roll (half) | 50g | 120 | 4 | 22 | 2 |
| Romaine + tomato | 2 cups + 2 slices | 20 | 1 | 4 | 0 |
| Bacon | 2–3 slices | 100 | 6 | 0 | 8 |
| Caesar dressing | 2 tbsp | 150 | 1 | 1 | 16 |
| **Total** | | **590** | **47** | **27** | **26** |

**Logged as:** "alfies chicken caesar sandwich (half) | 590 | 47 | 27 | 26" with `source: estimated (menu sourcing)`.

## When to Use This

- Independent/local restaurants with no published nutrition page.
- Custom-made sandwich/bowl shops.
- Cafeteria/office meals where the item is identifiable but macros are not standardized.
- User has strong context (knows it's half, knows the size).

## When NOT to Use

- Chains with official published macros (Chipotle, Subway, McDonald's, Shopify cafeteria) → use the official page first.
- Fully unknown dishes with no ingredient visibility → ask the user to describe components or use LLM estimate with lower confidence.
- If the user says "I don't know what was in it" → revert to a general category estimate (e.g., "sandwich" ≈ 500–700 kcal typical range).

## Confidence & Correction

Flag as `estimated (menu sourcing)` so the user knows it's component-based, not official. After logging, the user can correct if portions were off: "actually much larger", "only ate half of what I described", etc. Use undo + re-log.

## Related Patterns

- `references/branded-packaged-foods.md` — for products WITH labels/official pages (opposite situation).
- `references/mixed-meal-lookup.md` — for multi-component estimation in general.
