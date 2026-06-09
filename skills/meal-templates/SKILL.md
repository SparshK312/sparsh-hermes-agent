---
name: meal-templates
description: List, search, or describe Sparsh's saved meal templates (recurring meals with pre-computed macros) stored at `07 - Health/Meal Templates/`. Triggers on "/templates", "list my meals", "what templates do I have", "show meals". Each template is a markdown file with YAML frontmatter holding kcal, protein, carbs, fat, items, and meal-type. Used by log-food for fuzzy matching incoming meal descriptions to skip the nutrient-DB lookup when a saved template fits.
version: 1.0.0
platforms: [linux, macos]
metadata:
  hermes:
    tags: [health, templates, food]
    category: health
---

# meal-templates

## When to Use

User wants to see or query saved meal templates. Examples:
- `/templates` → list all
- "what templates do I have"
- "show me my breakfast templates"
- "list my meals"

Read-only by default. (Template *creation* happens implicitly when `log-food` saves a new recurring meal — that's a different skill.)

## Step-by-step

1. **Determine the templates directory:** `07 - Health/Meal Templates/`. If it doesn't exist, reply: "No templates yet. The `log-food` skill creates one after you confirm a meal twice — or you can add markdown files manually to `07 - Health/Meal Templates/`."

2. **List all `.md` files in the directory.** For each:
   - Read YAML frontmatter.
   - Expected fields: `name`, `meal_type` (breakfast/lunch/dinner/snack/shake), `kcal`, `protein_g`, `carbs_g`, `fat_g`, `items` (list of strings).

3. **Filter by query** if the user specified a meal type or keyword (e.g., "breakfast templates" → filter `meal_type: breakfast`).

4. **Format reply for Telegram:**

```
🍽️ Meal templates  (<count>)
━━━━━━━━━━━
**Breakfast**
• <name>  —  <kcal> kcal / <protein>g P
• <name>  —  …

**Lunch**
• …

**Dinner**
• …

**Snacks / Shakes**
• …
```

Group by `meal_type`. Skip empty groups. If only a few templates exist, drop the groupings and just list them flat.

5. **For a specific template** (user asked "show me the chicken bowl template"):
   - Match by name (case-insensitive, fuzzy).
   - Reply with full detail:

```
🍽️ <name>
━━━━━━
type: <meal_type>
items:
  • <item 1>
  • <item 2>

kcal: <X>
protein: <X>g
carbs: <X>g
fat: <X>g
```

## Vault-write conventions

This skill is read-only. **Do not** create or modify template files. If a user asks to add a new template, route them to log-food (which builds templates from confirmed meals) or tell them to add a markdown file manually.

## Log the change

**Do not log.** Read-only.

## Template file format (for reference)

A template file at `07 - Health/Meal Templates/cafeteria-breakfast-light.md` looks like:

```markdown
---
name: cafeteria-breakfast-light
meal_type: breakfast
kcal: 420
protein_g: 28
carbs_g: 45
fat_g: 14
items:
  - 2 eggs scrambled
  - 1 slice multigrain toast
  - 1 small bowl yogurt + berries
created: 2026-05-26
last_used: 2026-05-26
use_count: 1
---

# Cafeteria Breakfast (light)

Shopify cafeteria, light end. Used when planning a heavy lunch.
```

## Pitfalls

1. **Templates dir doesn't exist.** Don't auto-create it. Tell the user how it gets created.
2. **Malformed frontmatter in a template.** Skip the template, list it as "<filename> (frontmatter error)" so the user knows to fix it.
3. **No `items:` field.** Show the template anyway — items are optional, macros are the load-bearing fields.
4. **Macros must be numeric.** Refuse to list a template where kcal isn't a number. Hard-fail loudly so the user can fix.
