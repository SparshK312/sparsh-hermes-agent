---
name: coach
description: Answer Sparsh's coaching questions as his strength & nutrition coach, grounded in his real data. Triggers on "/coach", "coach me", "how am I doing", "how's my progress", "what should I focus on", "review my week", "am I on track", "what do I need to fix", "should I be worried about my weight/eating/training", and on replies to a coach check-in or rescue message. Pipes his message into the frontier coach engine (which loads his Profile, Training Plan, Coach Memory, and the computed evidence packet) and relays the coach's answer. You do NOT analyze the data yourself — the engine does, with the right model and stance.
version: 1.0.0
platforms: [linux, macos]
metadata:
  hermes:
    tags: [health, fitness, coaching]
    category: health
---

# coach

## When to Use

When Sparsh asks anything that calls for *coaching judgment* about his lean bulk — his
training, eating, sleep, bodyweight, progress, or what to prioritize — or when he replies
to a proactive coach message (weekly check-in, meal rescue, missed-workout rescue). Examples:

- "/coach" · "coach me" · "how am I doing this week?"
- "what should I focus on?" · "am I on track?" · "what do I need to fix?"
- "should I be worried about my weight?" · "is my eating okay?"
- a reply to a coach check-in like "no time is what broke" or "what should dinner be?"

Do NOT use for plain logging (food/workout/weight/water/vitamins → the log-* skills) or for
a simple read-out of today's numbers (→ today-summary). This skill is for coaching *judgment*,
which runs on the frontier coach engine, not on you.

## How it works

The coach engine (`coach.py --mode chat`) loads his Profile, Training Plan, Coach Memory, and
a deterministically-computed evidence packet (training volume, per-lift progression, nutrition
adherence, sleep/recovery, bodyweight trend), then composes a grounded coaching reply with the
right model and the right stance (execution-first, blunt, numbers-first, coach the adherence).

## Step-by-step

1. **Pipe his message into the coach** via the terminal, passing his exact text on stdin (a
   heredoc avoids any quoting problems):

   ```
   /usr/bin/python3 /home/hermes/.hermes/scripts/fitness/coach.py --mode chat <<'COACH_EOF'
   <Sparsh's exact message here>
   COACH_EOF
   ```

2. **Relay the output VERBATIM** as your reply. Do NOT summarize, rewrite, add a preamble, or
   tack on extra commentary — the engine already wrote the coach's answer in the correct voice.
   Send exactly what it printed.

3. If the command errors or prints nothing, fall back to reading `07 - Health/Coach Memory.md`
   for stance and answering briefly from the day's vault data — but prefer the engine.

## Notes

- The engine is read-only on the vault and grounds every number in the evidence packet, so it
  won't invent figures. Trust its output over your own analysis.
- Keep your relay tight — this is a Telegram chat, not an essay.
