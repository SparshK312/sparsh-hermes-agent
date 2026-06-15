# Follow-up ambiguity for repeat food mentions

When a user sends a follow-up photo or message for food that may already be logged in the same day or conversation:

- Default to **same item / same meal** if the user is clearly referring back to the prior food and does not explicitly say it is an additional serving.
- Treat explicit markers like **another**, **extra**, **added**, **plus**, or a new quantity as a new log entry.
- If the distinction changes the log, ask one short clarification only: `same one or another?`
- Keep the question terse; do not restate the whole meal back to the user unless needed for accuracy.
- If the user is just supplying more detail about the same meal, update the existing entry instead of duplicating it.
