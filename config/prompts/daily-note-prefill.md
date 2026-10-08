<!-- Mirror of the live `daily-note-prefill` cron prompt (VPS ~/.hermes/cron/jobs.json, job c7f8a678e673).
     Not in config/cron_additions.json: that file's jobs_to_append requires a `script`, and the
     prefill has none. Apply changes with `hermes cron edit c7f8a678e673 --prompt "$(cat …)"` as the
     hermes user, after backing up jobs.json; never edit jobs.json by hand. -->

Pre-fill today's daily note. Use obsidian-vault-write skill (vault rules) and google-workspace skill (calendar read). This job is SILENT — it only sets up the note. Do NOT send any Telegram message.

(1) Compute the YYYY-MM-DD date in America/Toronto.
(2) DECIDE which of three states the note is in. NEVER overwrite a file that already has content.
  (2a) File does NOT exist -> copy Templates/Daily Note.md to that path, set the frontmatter date and the H1 (format: <Day of week, Month D, YYYY>), then continue.
  (2b) File EXISTS and already has a populated Schedule table, Tasks beyond the empty bullet, or non-empty Notes/Reflection -> a real day is already underway. SKIP everything below and output [SILENT].
  (2c) File EXISTS but is a STUB -> MERGE, do not overwrite and do not skip. A stub is any note missing the template's sections; in practice it is the Claude-session logger's output: frontmatter with only `type`/`date` plus a `## Claude sessions` section. PRESERVE every existing line (especially `## Claude sessions`), and ADD what's missing: the health frontmatter keys (weight, sleep_hours, sleep_quality, kcal, protein_g, carbs_g, fat_g, water_l, lifted, energy, mood — empty values), the H1, and the ## Schedule / ## Tasks / ## Health / ## Notes / ## End of Day Reflection sections from Templates/Daily Note.md. Append the template sections AFTER the existing content. Then continue with steps 4-6.
  The health frontmatter keys are load-bearing: vault_log.py and the morning brief both read them, so a day without them silently loses all health logging.
(4) Fetch the day's calendar events by running EXACTLY this ONE command in terminal, substituting the literal YYYY-MM-DD date. Do NOT invent flags (there is no --format), do NOT pipe it into anything (the security scanner blocks pipes), do NOT retry with variations:
  /home/hermes/.hermes/hermes-agent/venv/bin/python /home/hermes/.hermes/skills/productivity/google-workspace/scripts/google_api.py calendar list --start <DATE>T00:00:00-04:00 --end <DATE>T23:59:59-04:00
It prints a JSON array; each element has summary/start/end/location. An empty array [] means there are genuinely no events that day - that is a VALID result, so leave the Schedule table empty and move on. Append rows to the Schedule table chronologically: | <H:MM AM/PM> | <event title> |. Strip emoji only if they break markdown.
(5) TASKS. Run EXACTLY this ONE command in terminal and use its output:
  /home/hermes/.hermes/hermes-agent/venv/bin/python /home/hermes/.hermes/scripts/vault/today_actions.py
  It prints (short, capped) the OPEN rows of his THIS WEEK list — finished ✅/🗄️ rows are already removed; ⬜ marks a step that is still to do. ⚠️ DO NOT read_file '00 - Dashboard/Action Items.md' — it is ~100 KB.
  Each row is tagged [TODAY] (due or planned today) or [WEEK] (later this week). Add ONLY the [TODAY] rows, each as - [ ] <short action> under Tasks (when a row carries ⬜, the ⬜ part is the task). Never add [WEEK] rows as tasks. Skip any row marked ⚠️ STALE. If the output says UNKNOWN, add exactly one task "- [ ] ⚠️ Could not read the task list — check Action Items" and nothing invented.
(5b) INBOX. Read /home/hermes/.hermes/health/email_triage.md with read_file. It is written at 06:40 by the email-triage cron and holds the overnight inbox read: application outcomes, anything needing a reply, anything time-sensitive.
  - If the file exists and is not the "Nothing needing attention." placeholder, insert its ENTIRE contents as a section immediately BEFORE ## Notes. Copy it VERBATIM — do not re-summarise, re-rank or reword it; it was already written for him to read.
  - If it is missing, empty, or the placeholder, add nothing at all. Do NOT write "no updates" — an absent section is the signal.
  - If it contains a scan-failure warning, insert it as-is so the failure is visible rather than silent.
  - NEVER put inbox content in Tasks. If an item has a "▶️" action line, ALSO add that action as "- [ ] <action>" under ## Tasks so it becomes a real task.
  - An item marked "✅ already handled" is DONE (he replied, submitted, or the application is closed). It is NOT a task — never add it under ## Tasks.
(6) Leave Notes and End of Day Reflection empty.

After finishing (or skipping), output exactly: [SILENT]
Do NOT compose or send a morning brief. The hae-morning-brief hybrid cron sends the consolidated brief (schedule + tasks + sleep + health) later, when Sparsh actually wakes — this job just prepares the note so that brief has data to read.
