# Cron batch triage notes

## Use case
Daily Hermes internship watcher receives a batch of **already deduped** fresh postings as JSON lines. Each line includes `company`, `title`, `url`, `location`, `posted_date`, `age_days`, `terms`, `source`, and `canonical_id`.

## Practical rules
- Treat each JSON line independently.
- Use `canonical_id` exactly as provided for the dedupe key when present.
- If a canonical ID is absent, normalize the URL by stripping query params before checking `postings_seen.json`.
- Even when the batch was pre-deduped upstream, still append **every** posting to `postings_seen.json` after triage, including `skip` results.
- Only write to `00 - Dashboard/Internship Pipeline.md` for `apply now` and `wait` results.
- Add a short one-line cover sentence only for `apply now` results.

## Triage output shape
For batch use, keep the result compact and machine-readable:
- verdict
- company
- role
- url
- location
- posted_date
- age_days
- reason/notes for skip or wait
- whether the posting was already seen
