# Portion-Size Language & Home-Cooked Meal Estimation

## The Pattern

When Sparsh describes a meal with **portion-size keywords** ("full plate", "filling", "heaping", "loaded"), the estimate should bias **upward**, not conservative. These words signal an above-average serving.

### Example: Rice + Rajma (Thu 2026-07-16)

**User input:** "ate a plate of rice with rajma beans, it was a **full plate**"

**Wrong approach:** Treat "full plate" as baseline and estimate component-wise in isolation.
- Rice: 1 cup cooked ≈ 150 kcal
- Rajma: ½ cup ≈ 75 kcal
- Total: ~225 kcal (SEVERELY UNDERESTIMATED)

**Correct approach:** "Full plate" signals a substantial, restaurant-style serving size, not a minimal portion.
- Rice: 1.5 cups cooked (~300g) ≈ 390 kcal
- Rajma: 1 cup cooked (~200g) ≈ 254 kcal
- Total: ~644 kcal (realistic for a filling meal)

**Real outcome:** User corrected immediately after logging the wrong estimate. The vault was rewritten with accurate macros via `undo-last-meal` + re-log.

## Rules

1. **"Full plate" = portion-size signal.** If Sparsh says a meal was a full plate, filling, heaping, or loaded, do NOT estimate component-wise and assume standard serving sizes. Bump portions upward by 30–50% relative to baseline assumptions.

2. **Home-cooked Indian meals in particular.** Rice + lentil / bean combos (rajma, dal) are staple household sizes in Indian cooking. A "full plate" of rice + rajma is not a light side dish — it's a main course. Default to 1.5+ cups rice, 1 cup cooked legumes, unless the user says otherwise ("small plate", "half serving").

3. **Apply the same upward bias to other home-cooked meals.** Pasta, stews, casseroles, fried rice — if described as "a plate" / "full serving", assume restaurant-portion scale (300–400g) not a small bowl (150g).

4. **Restaurant vs. home distinction is real.** Restaurant chain meals (Chipotle, Osmow's) have published portions and caloric density that are predictably high. Home meals often feel deceptively large (rice expands during cooking, but the uncooked weight is still there). Ask for clarification only if the description is genuinely ambiguous ("had some rice"); if it's "full plate of rice", estimate high.

5. **Correction is reactive.** If the user says the estimate feels too large after logging ("that was less than I logged" / "I ate less"), undo and re-log with the correct reduction. Do not ask for clarification before logging — trust the portion-size language and log high, let them correct down if needed.

## Reference

- **Cooked rice:** 1 cup cooked basmati (~160g) ≈ 130 kcal. A "full plate" is typically 1.5–2 cups (~195–260g) ≈ 250–330 kcal.
- **Cooked rajma (kidney beans):** 1 cup cooked (~200g) ≈ 254 kcal (127 kcal/100g per USDA).
- **Cooked dal (lentils):** 1 cup (~240g) ≈ 230 kcal (though daal makhani adds cream/butter, so 350–420 kcal for the final dish).

**Source:** Session 2026-07-16. User corrected rice + rajma from 150 kcal to 644 kcal after logging the initial underestimate.
