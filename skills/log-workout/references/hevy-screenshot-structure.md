# Hevy Screenshot Structure

## What Hevy generates when a user shares a workout

### 1. Summary card (always first)
- Shows: workout title, date, duration, total volume (lbs), exercise names + set counts
- Does NOT show: per-set weights or reps
- Example: "Pull (back/biceps) · 1h 13m · 4,626 lbs · 6 exercises, 17 sets"

### 2. Per-exercise detail screens (1-2 additional images)
- Shows: each exercise with per-set weight × reps (e.g. "Set 1: 90 lb × 5")
- These are the images needed to populate `exercises[].sets[]` in the vault

## Cache-check pattern (2026-07-11 session confirmed)

When the user sends "this is my workout" + an image:
1. Note the image filename from the attachment path
2. Run `ls -t /home/hermes/.hermes/image_cache/img_*.jpg` to list all cached images
3. `vision_analyze` any that haven't been read yet in this conversation
4. The detail screens are typically the 2nd and 3rd most-recent images

In the 2026-07-11 session, three images were in the cache:
- `img_c5bfa0192dbe.jpg` — Hevy summary card (sent first, was the attached image)
- `img_488caf8cf7a5.jpg` — Hevy detail screen 1
- `img_e2a6ffd13a98.jpg` — Hevy detail screen 2
- `img_d997fe1c2383.jpg` — Hevy detail screen 3

The summary card alone was analyzed first, prompting a (wrong) clarify call. Analyzing all 3 detail screens yielded full per-set data.

## Implication
Never use `clarify` to ask for weights before checking the cache. The detail screens are almost always there.
