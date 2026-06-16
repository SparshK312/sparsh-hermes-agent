# Compound health bundles

Use this when a single user message mixes *multiple health domains* in one turn, especially:
- food + vitamins/supplements
- food + water
- food + vitamins + water
- "breakfast/lunch/dinner" plus supplement or hydration updates

## Pattern

1. **Parse each domain separately.**
   - Food items → `log-food`
   - Supplements/vitamins/pills → `log-vitamins`
   - Water → `log-water`
2. **Do not collapse domains into one note.** A meal report that also mentions creatine or a glass of water still needs the supplement/hydration writes.
3. **Persist all applicable logs before replying.** If one domain is ambiguous, log the others first and continue with the remaining confirmation flow.
4. **Keep the user’s wording intact where it matters.** Example: "Haven’t eaten lunch yet" should preserve a breakfast classification even if the message arrives after noon.

## Example from session

User: "Haven’t eaten lunch just yet. Had lucky charms bowl cereal with milk in the morning, drank a latte with sweetener and milk, ate a 'awake' chocolate. Also before coming office I had the same multivitamins I had before, also b-12, and also 5 grams of creatine with a full glass of water"

Recommended split:
- Food: Lucky Charms bowl, latte, Awake chocolate
- Supplements: multivitamin, B-12, creatine
- Water: 500 ml

## Pitfalls

- Don't wait for a perfect all-in-one estimate if one part is already clear.
- Don't count the water as part of the latte or food log.
- Separate pills stay separate even if one is already included in a multivitamin formula.
