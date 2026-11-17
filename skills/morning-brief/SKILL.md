---
name: morning-brief
description: Refresh Sparsh's Apple-Watch data and send his morning brief (sleep & recovery + schedule + tasks + this-week + focus) to Telegram, on demand. Triggers on "brief", "my brief", "morning brief", "send my brief", "refresh my brief", "sync my sleep", "did my sleep sync", "what's my sleep", "pull my brief". Runs the brief gate with --force, which re-pulls the latest watch data and sends the brief itself. You do NOT compose the brief yourself.
version: 1.0.0
platforms: [linux, macos]
metadata:
  hermes:
    tags: [health, brief, sleep]
    category: health
---

# morning-brief

## When to Use

Sparsh wants his morning brief **now** — e.g. after his sleep finally syncs, or any time he wants the current sleep/recovery + schedule. Examples:
- "brief", "my brief", "send my brief", "refresh my brief", "pull my brief"
- "sync my sleep", "did my sleep sync", "what's my sleep"

## What to do

**This skill RE-PULLS the watch data and SENDS the brief itself. You do NOT read files or compose the brief.**

1. Run exactly this shell command. It refreshes the latest Apple-Watch data (HAE → metrics.csv → daily note), composes the brief, and sends it to Telegram directly:

   ```
   /usr/bin/python3 /home/hermes/.hermes/scripts/health_morning_brief_gate.py --force
   ```

2. It prints `[SILENT]` on success — that means **the brief was already sent**.

3. Reply with **one short line only**, e.g. *"Pulled your brief 🌅"*. Do **not** restate the brief.

4. If the output is not `[SILENT]` (an error or the raw brief text), tell Sparsh it didn't send cleanly and to check `~/.hermes/health/hae/sync.log`.

## Notes
- `--force` ignores the once-a-day fire lock, so it always re-sends with the freshest data.
- If his sleep still shows as "not synced," the data simply hasn't reached the VPS yet — that's an HAE-on-phone export-timing issue, not this skill.
