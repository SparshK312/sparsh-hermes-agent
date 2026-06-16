# HAE sync reconciliation for daily health summaries

Use this when a user disputes sleep/steps numbers or says they should not have to report them manually.

## Canonical sources
- `04 - Daily Notes/YYYY-MM-DD.md` frontmatter is what `today-summary` should read.
- `07 - Health/Metrics/metrics.csv` is the HAE archive the daily-note ingest reads from.
- `~/.hermes/health/hae/sync.log` shows when the bridge last wrote a date.
- `~/.hermes/health/hae/raw/*.json` are the upstream payloads.

## Reconciliation rule
- Do **not** ask the user to hand-type sleep/steps first if the data looks wrong.
- Check whether the vault is merely stale versus the source feed actually missing the final totals.
- If `hae_synced`/`metrics.csv` still reflect an older payload, say the bridge has not received the final values yet.
- Only manually correct the daily note if the user explicitly supplies the correct numbers or another authoritative source is found.

## Session note
- On 2026-06-15, the final HAE archive row still topped out at `steps=3482` and `sleep_total_h=3.48`.
- The user-reported `steps=13794` and `sleep=7h 8m` were not present in the sync archive, so the issue was upstream, not the daily-note write.