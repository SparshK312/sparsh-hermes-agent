# Meal-type correction flow

Use this when the user corrects the bucket after a meal was already logged:

- Examples: "that was lunch, not dinner", "snacks earlier were breakfast", "make it snack instead of dinner".
- Edit the existing food-log section header in place.
- Preserve the item list and macro totals unless the food composition also changed.
- Append a `Log.md` update noting the relabeling.
- Do not duplicate the meal as a second entry.
- If the user also changes the clock time, treat that as the same edit path.

This is separate from a food correction (different items / portions) and from a date correction (move the entry to a different day).
